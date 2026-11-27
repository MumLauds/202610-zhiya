#define HOST_TRANSPORT
#include "esp_host.h"
#include "transport.c"

struct esp_tls { size_t read_at; };
typedef enum { DNS_HOLD, DNS_OK, DNS_ERROR } dns_mode_t;
typedef enum { IO_OK, IO_TLS_FAIL, IO_HANDSHAKE_WAIT, IO_LINK_LOST } io_mode_t;

static int64_t clock_ms, cancel_at;
static unsigned dns_calls, tls_opens, plain_opens, socket_closes, writes, reads;
static int64_t read_delay;
static size_t piece;
static bool link_up, have_clock;
static dns_mode_t dns_mode;
static io_mode_t io_mode;
static const char *expected_host;
static const unsigned char *expected_ca;
static char wire[512];
static char body[DEVICE_BODY_MAX + 1];

int64_t esp_timer_get_time(void) { return clock_ms * 1000; }
bool device_wifi_online(void) { return link_up; }
bool device_wifi_clock_ready(void) { return link_up && have_clock; }

static void dns_reply(uint32_t id, int error)
{
    const dns_answer_t reply = {.id = id, .address = "192.168.2.5", .error = error};
    xQueueOverwrite(dns_answers, &reply);
    atomic_store(&dns_busy, false);
}

static void host_wait(TickType_t ticks)
{
    assert(ticks != portMAX_DELAY);
    clock_ms += ticks;
    if (dns_requests && dns_mode != DNS_HOLD) {
        dns_query_t query;
        if (xQueueReceive(dns_requests, &query, 0) == pdPASS) {
            ++dns_calls;
            dns_reply(query.id, dns_mode == DNS_ERROR ? 1 : 0);
        }
    }
}

int xTaskCreate(void (*function)(void *), const char *name, unsigned stack, void *arg, int priority, void *task)
{
    assert(function == resolver && strcmp(name, "device_dns") == 0 && stack == 4096 && !arg && priority == 3 && !task);
    return pdPASS;
}
int getaddrinfo(const char *host, const char *service, const struct addrinfo *hints, struct addrinfo **result)
{
    (void)host;
    (void)service;
    (void)hints;
    (void)result;
    assert(!"host DNS worker is simulated by host_wait");
    return -1;
}
void freeaddrinfo(struct addrinfo *value) { (void)value; }
uint32_t ntohl(uint32_t value) { return value; }
int inet_pton(int family, const char *input, void *out)
{
    unsigned octets[4];
    char extra;
    assert(family == AF_INET);
    if (sscanf(input, "%u.%u.%u.%u%c", &octets[0], &octets[1], &octets[2], &octets[3], &extra) != 4) { return 0; }
    for (size_t i = 0; i < 4; ++i) { if (octets[i] > 255) { return 0; } }
    ((struct in_addr *)out)->s_addr = octets[0] << 24 | octets[1] << 16 | octets[2] << 8 | octets[3];
    return 1;
}
const char *inet_ntop(int family, const void *address, char *buffer, size_t size)
{
    (void)family;
    (void)address;
    (void)buffer;
    (void)size;
    return NULL;
}
int fcntl(int fd, int cmd, ...) { assert(fd == 4 && cmd == F_SETFL); return 0; }

esp_err_t esp_crt_bundle_attach(void *context) { (void)context; return ESP_OK; }
esp_tls_t *esp_tls_init(void) { return calloc(1, sizeof(esp_tls_t)); }
int esp_tls_conn_new_async(const char *address, int length, int port, const esp_tls_cfg_t *config, esp_tls_t *tls)
{
    assert(tls && !config->is_plain_tcp && config->non_block && config->addr_family == AF_INET);
    assert(config->timeout_ms > 0 && config->timeout_ms <= CONNECT_MAX_MS);
    assert(length == (int)strlen(address) && strcmp(address, "192.168.2.5") == 0 && port == 443);
    assert(strcmp(config->common_name, expected_host) == 0);
    assert(config->cacert_buf == expected_ca);
    assert(config->crt_bundle_attach == (expected_ca ? NULL : esp_crt_bundle_attach));
    ++tls_opens;
    if (io_mode == IO_TLS_FAIL) { return -1; }
    if (io_mode == IO_HANDSHAKE_WAIT) { return 0; }
    return 1;
}
int esp_tls_conn_new_sync(const char *address, int length, int port, const esp_tls_cfg_t *config, esp_tls_t *tls)
{
    assert(tls && length > 0 && address && port == 80 && config->is_plain_tcp && !config->non_block);
    ++plain_opens;
    return 1;
}
esp_err_t esp_tls_get_conn_state(esp_tls_t *tls, esp_tls_conn_state_t *state)
{
    assert(tls);
    *state = ESP_TLS_HANDSHAKE;
    return ESP_OK;
}
esp_err_t esp_tls_get_conn_sockfd(esp_tls_t *tls, int *fd) { assert(tls); *fd = 4; return ESP_OK; }
esp_err_t esp_tls_get_error_handle(esp_tls_t *tls, esp_tls_error_handle_t *handle) { *handle = tls; return ESP_OK; }
esp_err_t esp_tls_get_and_clear_last_error(esp_tls_error_handle_t handle, int *code, int *flags)
{
    assert(handle);
    *code = -9984;
    *flags = 8;
    return ESP_ERR_MBEDTLS_SSL_HANDSHAKE_FAILED;
}
int esp_tls_conn_destroy(esp_tls_t *tls) { ++socket_closes; free(tls); return 0; }
ssize_t esp_tls_conn_write(esp_tls_t *tls, const void *bytes, size_t size)
{
    assert(tls && bytes && size);
    ++writes;
    return (ssize_t)size;
}
ssize_t esp_tls_conn_read(esp_tls_t *tls, void *buffer, size_t capacity)
{
    ++reads;
    clock_ms += read_delay;
    if (io_mode == IO_LINK_LOST) { link_up = false; }
    size_t size = strlen(wire) - tls->read_at;
    if (size > piece) { size = piece; }
    if (size > capacity) { size = capacity; }
    memcpy(buffer, wire + tls->read_at, size);
    tls->read_at += size;
    return (ssize_t)size;
}

static bool canceled(void *context)
{
    (void)context;
    return cancel_at >= 0 && clock_ms >= cancel_at;
}

static void response(unsigned code, const char *json)
{
    const int size = snprintf(wire, sizeof(wire),
        "HTTP/1.1 %u Response\r\nContent-Type: application/json\r\nContent-Length: %u\r\n\r\n%s",
        code, (unsigned)strlen(json), json);
    assert(size > 0 && (size_t)size < sizeof(wire));
}

static void reset(void)
{
    clock_ms = 0;
    cancel_at = -1;
    dns_calls = tls_opens = plain_opens = socket_closes = writes = reads = 0;
    read_delay = 0;
    piece = sizeof(wire);
    link_up = have_clock = true;
    dns_mode = DNS_OK;
    io_mode = IO_OK;
    expected_host = "mock.test";
    expected_ca = NULL;
    response(200, "{\"data\":{},\"requestId\":\"transport\"}");
}

static device_result_t perform(const device_request_t *request)
{
    return device_request(request, body, DEVICE_BODY_MAX, canceled, NULL);
}

static void timeout_and_resolve(void)
{
    reset();
    device_request_t request = {.url = "https://mock.test/", .mode = DEVICE_DEMO_HTTPS, .timeout_ms = 50};
    dns_mode = DNS_HOLD;
    device_result_t result = perform(&request);
    assert(result.status == DEVICE_TIMEOUT && result.elapsed_ms >= 50 && result.elapsed_ms < 62 && !tls_opens);
    dns_query_t late;
    assert(xQueueReceive(dns_requests, &late, 0) == pdPASS);
    dns_reply(late.id, 0);

    cancel_at = clock_ms + 15;
    request.timeout_ms = 200;
    result = perform(&request);
    assert(result.status == DEVICE_CANCELED && !tls_opens && result.elapsed_ms < 30);
    assert(xQueueReceive(dns_requests, &late, 0) == pdPASS);
    dns_reply(late.id, 1);

    cancel_at = -1;
    dns_mode = DNS_OK;
    result = perform(&request);
    assert(result.status == DEVICE_OK && dns_calls == 1 && tls_opens == 1 && socket_closes == 1);
    request.timeout_ms = 10;
    result = perform(&request);
    assert(result.status == DEVICE_TIMEOUT && result.elapsed_ms == 10 && dns_calls == 2 && tls_opens == 1);
    puts("PASS DNS timeout and cancel are bounded, old success and error cannot become the next result");
}

static void tls_and_deadline(void)
{
    reset();
    const unsigned char fake_ca[] = "test certificate pointer";
    device_request_t request = {.url = "https://mock.test/", .mode = DEVICE_DEMO_HTTPS, .timeout_ms = 800,
                                .demo_ca = fake_ca, .demo_ca_size = sizeof(fake_ca)};
    expected_ca = fake_ca;
    io_mode = IO_TLS_FAIL;
    device_result_t result = perform(&request);
    assert(result.status == DEVICE_TLS && result.tls_flags == 8 && result.tls_error == -9984);
    assert(tls_opens == 1 && socket_closes == 1 && !writes && !plain_opens && result.attempts == 1);

    request.demo_ca = NULL;
    request.demo_ca_size = 0;
    expected_ca = NULL;
    io_mode = IO_HANDSHAKE_WAIT;
    request.timeout_ms = 60;
    result = perform(&request);
    assert(result.status == DEVICE_TIMEOUT && socket_closes == 2 && !writes && !plain_opens);
    io_mode = IO_OK;
    piece = 1;
    read_delay = 5;
    result = perform(&request);
    assert(result.status == DEVICE_TIMEOUT && result.elapsed_ms >= 60 && result.elapsed_ms < 65);
    assert(reads > 1 && socket_closes == 3 && result.attempts == 1 && !plain_opens);

    have_clock = false;
    result = perform(&request);
    assert(result.status == DEVICE_CLOCK && socket_closes == 3);
    request.mode = DEVICE_FORMAL_HTTPS;
    result = perform(&request);
    assert(result.status == DEVICE_NOT_AUTHORIZED && socket_closes == 3);
    puts("PASS TLS failure, trust, hostname, clock and authorization gates, trickle deadline and no fallback");
}

static void disconnect_reconnect_and_retries(void)
{
    reset();
    device_request_t request = {.url = "http://192.168.2.5/", .mode = DEVICE_DEMO_HTTP, .method = DEVICE_POST,
                                .body = "{}", .body_size = 2, .timeout_ms = 1500};
    io_mode = IO_LINK_LOST;
    device_result_t result = perform(&request);
    assert(result.status == DEVICE_OUTCOME_UNKNOWN && result.cause == DEVICE_OFFLINE && result.attempts == 1);
    assert(result.sent && plain_opens == 1 && socket_closes == 1 && writes == 2);
    result = perform(&request);
    assert(result.status == DEVICE_OFFLINE && !result.sent && plain_opens == 1);

    link_up = true;
    io_mode = IO_OK;
    request.method = DEVICE_GET;
    result = perform(&request);
    assert(result.status == DEVICE_OK && plain_opens == 2 && socket_closes == 2);
    cancel_at = clock_ms + 10;
    read_delay = 10;
    result = perform(&request);
    assert(result.status == DEVICE_CANCELED && result.body_size == 0 && socket_closes == 3);

    cancel_at = -1;
    read_delay = 0;
    response(503, "{\"error\":{\"code\":\"DEPENDENCY_UNAVAILABLE\",\"message\":\"down\",\"retryable\":true},\"requestId\":\"r\"}");
    result = perform(&request);
    assert(result.status == DEVICE_UNAVAILABLE && result.attempts == 2 && plain_opens == 5 && socket_closes == 5);
    request.method = DEVICE_POST;
    result = perform(&request);
    assert(result.status == DEVICE_OUTCOME_UNKNOWN && result.cause == DEVICE_UNAVAILABLE && result.attempts == 1);
    assert(plain_opens == 6 && socket_closes == 6);
    puts("PASS disconnect preserves write ambiguity, recovered link serves new reads, late cancel discards data, reads retry once");
}

int main(void)
{
    timeout_and_resolve();
    tls_and_deadline();
    disconnect_reconnect_and_retries();
    vQueueDelete(dns_requests);
    vQueueDelete(dns_answers);
    return 0;
}
