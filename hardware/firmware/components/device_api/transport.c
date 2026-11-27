#include "http.h"
#include "device_wifi.h"

#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <stdatomic.h>
#include <stdio.h>
#include <string.h>
#include "esp_crt_bundle.h"
#include "esp_timer.h"
#include "esp_tls.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "lwip/netdb.h"
#include "lwip/sockets.h"
#include "sdkconfig.h"

#if !CONFIG_MBEDTLS_HAVE_TIME_DATE || !CONFIG_MBEDTLS_CERTIFICATE_BUNDLE
#error "device_api requires X.509 time validation and the ESP-IDF CA bundle"
#endif

enum { CONNECT_MAX_MS = 2500, REQUEST_MAX_MS = 30000 };
typedef struct { uint32_t id; char host[128]; } dns_query_t;
typedef struct { uint32_t id; char address[INET_ADDRSTRLEN]; int error; } dns_answer_t;
typedef struct { int64_t deadline; device_cancel_cb cancel; void *context; } timing_t;

static QueueHandle_t dns_requests, dns_answers;
static atomic_bool dns_busy;
static uint32_t dns_id;

static int64_t now_ms(void) { return esp_timer_get_time() / 1000; }

static device_status_t check(const timing_t *timing)
{
    return device_gate(timing->cancel && timing->cancel(timing->context),
                       device_wifi_online(), now_ms(), timing->deadline);
}

static void pause_briefly(void) { vTaskDelay(pdMS_TO_TICKS(10) + 1); }

static void resolver(void *ignored)
{
    (void)ignored;
    const struct addrinfo hints = {.ai_family = AF_INET, .ai_socktype = SOCK_STREAM};
    for (;;) {
        dns_query_t query;
        xQueueReceive(dns_requests, &query, portMAX_DELAY);
        dns_answer_t answer = {.id = query.id};
        struct addrinfo *resolved = NULL;
        answer.error = getaddrinfo(query.host, NULL, &hints, &resolved);
        if (!answer.error && resolved) {
            const struct sockaddr_in *address = (const struct sockaddr_in *)resolved->ai_addr;
            if (!inet_ntop(AF_INET, &address->sin_addr, answer.address, sizeof(answer.address))) {
                answer.error = errno;
            }
        }
        if (resolved) { freeaddrinfo(resolved); }
        xQueueOverwrite(dns_answers, &answer);
        atomic_store(&dns_busy, false);
    }
}

static bool resolver_init(void)
{
    if (dns_requests) { return true; }
    dns_requests = xQueueCreate(1, sizeof(dns_query_t));
    dns_answers = xQueueCreate(1, sizeof(dns_answer_t));
    if (dns_requests && dns_answers &&
        xTaskCreate(resolver, "device_dns", 4096, NULL, 3, NULL) == pdPASS) {
        return true;
    }
    if (dns_requests) { vQueueDelete(dns_requests); }
    if (dns_answers) { vQueueDelete(dns_answers); }
    dns_requests = dns_answers = NULL;
    return false;
}

static device_status_t resolve(const device_url_t *url, const timing_t *timing, dns_answer_t *out)
{
    struct in_addr numeric;
    if (inet_pton(AF_INET, url->host, &numeric) == 1) {
        strcpy(out->address, url->host);
        return DEVICE_OK;
    }
    if (!resolver_init()) { return DEVICE_RESOURCE; }
    device_status_t status;
    while (atomic_load(&dns_busy)) {
        if ((status = check(timing)) != DEVICE_OK) { return status; }
        pause_briefly();
    }
    dns_query_t query = {.id = ++dns_id};
    strcpy(query.host, url->host);
    atomic_store(&dns_busy, true);
    xQueueOverwrite(dns_requests, &query);
    while ((status = check(timing)) == DEVICE_OK) {
        if (xQueueReceive(dns_answers, out, pdMS_TO_TICKS(10) + 1) == pdPASS && out->id == query.id) {
            return out->error || !out->address[0] ? DEVICE_DNS : DEVICE_OK;
        }
    }
    return status;
}

static bool lan_ipv4(const char *address)
{
    struct in_addr ip;
    if (inet_pton(AF_INET, address, &ip) != 1) { return false; }
    const uint32_t v = ntohl(ip.s_addr);
    return (v >> 24) == 10 || (v >> 20) == 0xac1 || (v >> 16) == 0xc0a8;
}

static void tls_error(esp_tls_t *tls, device_result_t *result)
{
    esp_tls_error_handle_t handle;
    if (esp_tls_get_error_handle(tls, &handle) == ESP_OK) {
        result->sdk_error = esp_tls_get_and_clear_last_error(handle, &result->tls_error, &result->tls_flags);
    }
}

static device_status_t open_connection(esp_tls_t *tls, const device_url_t *url, const char *address,
                                       const device_request_t *request, const timing_t *timing, device_result_t *result)
{
    device_status_t status = check(timing);
    if (status != DEVICE_OK) { return status; }
    const int64_t start = now_ms();
    int64_t budget = timing->deadline - start;
    if (budget <= 0) { return DEVICE_TIMEOUT; }
    if (budget > CONNECT_MAX_MS) { budget = CONNECT_MAX_MS; }
    esp_tls_cfg_t cfg = {
        .is_plain_tcp = !url->tls, .non_block = url->tls,
        .timeout_ms = (int)budget, .addr_family = ESP_TLS_AF_INET,
        .common_name = url->host,
        .cacert_buf = url->tls ? request->demo_ca : NULL,
        .cacert_bytes = url->tls ? request->demo_ca_size : 0,
        .crt_bundle_attach = url->tls && !request->demo_ca ? esp_crt_bundle_attach : NULL,
    };
    int connected;
    if (url->tls) {
        do {
            connected = esp_tls_conn_new_async(address, strlen(address), url->port, &cfg, tls);
            if (connected != 0) { break; }
            status = check(timing);
            if (status != DEVICE_OK) { return status; }
            esp_tls_conn_state_t state;
            if (esp_tls_get_conn_state(tls, &state) != ESP_OK) { return DEVICE_CONNECT; }
            /* The pinned SDK can retain select's cleared descriptor set after a TCP timeout.
             * Treat this attempt as expired instead of calling it again in CONNECTING. */
            if (state == ESP_TLS_CONNECTING || now_ms() - start >= budget) { return DEVICE_TIMEOUT; }
            pause_briefly();
        } while ((status = check(timing)) == DEVICE_OK);
        if (status != DEVICE_OK) { return status; }
    } else {
        connected = esp_tls_conn_new_sync(address, strlen(address), url->port, &cfg, tls);
    }
    if ((status = check(timing)) != DEVICE_OK) { return status; }
    if (connected != 1) {
        tls_error(tls, result);
        if (result->sdk_error == ESP_ERR_ESP_TLS_CONNECTION_TIMEOUT || now_ms() - start >= budget) {
            return DEVICE_TIMEOUT;
        }
        return url->tls && (result->tls_error || result->tls_flags ||
                            result->sdk_error == ESP_ERR_MBEDTLS_SSL_HANDSHAKE_FAILED) ? DEVICE_TLS : DEVICE_CONNECT;
    }
    int fd;
    if (esp_tls_get_conn_sockfd(tls, &fd) != ESP_OK || fcntl(fd, F_SETFL, O_NONBLOCK) < 0) {
        result->sdk_error = errno;
        return DEVICE_CONNECT;
    }
    return DEVICE_OK;
}

static bool would_block(ssize_t status, bool tls)
{
    return tls ? status == ESP_TLS_ERR_SSL_WANT_READ || status == ESP_TLS_ERR_SSL_WANT_WRITE :
                  errno == EAGAIN || errno == EWOULDBLOCK;
}

static device_status_t send_bytes(esp_tls_t *tls, const device_url_t *url, const timing_t *timing,
                                   const char *data, size_t size, device_result_t *result)
{
    while (size) {
        device_status_t status = check(timing);
        if (status != DEVICE_OK) { return status; }
        /* A TLS write can put bytes on the wire before returning an error. */
        result->sent = true;
        const ssize_t n = esp_tls_conn_write(tls, data, size);
        if (n > 0) {
            data += n;
            size -= (size_t)n;
        } else if (n < 0 && would_block(n, url->tls)) {
            pause_briefly();
        } else {
            if (url->tls) { tls_error(tls, result); }
            else { result->sdk_error = errno; }
            return DEVICE_READ;
        }
    }
    return DEVICE_OK;
}

static device_status_t exchange(esp_tls_t *tls, const device_url_t *url, const device_request_t *request,
                                 const timing_t *timing, device_http_t *http)
{
    char headers[768];
    char content[96] = "", scenario[80] = "";
    if (request->method == DEVICE_POST) {
        snprintf(content, sizeof(content), "Content-Type: application/json\r\nContent-Length: %u\r\n",
                 (unsigned)request->body_size);
    }
    if (request->mock_scenario) {
        snprintf(scenario, sizeof(scenario), "X-Mock-Error: %s\r\n", request->mock_scenario);
    }
    const int bytes = snprintf(headers, sizeof(headers),
        "%s %s HTTP/1.1\r\nHost: %s:%u\r\nConnection: close\r\nAccept: %s\r\n%s%s\r\n",
        request->method == DEVICE_POST ? "POST" : "GET", url->path, url->host, url->port,
        http->stream_mode ? "*/*" : "application/json", content, scenario);
    if (bytes < 0 || (size_t)bytes >= sizeof(headers)) { return DEVICE_LIMIT; }
    device_status_t status = send_bytes(tls, url, timing, headers, (size_t)bytes, http->result);
    if (status == DEVICE_OK && request->method == DEVICE_POST) {
        status = send_bytes(tls, url, timing, request->body, request->body_size, http->result);
    }
    char block[1024];
    while (status == DEVICE_OK && !http->complete) {
        if ((status = check(timing)) != DEVICE_OK) { break; }
        const ssize_t n = esp_tls_conn_read(tls, block, sizeof(block));
        if (n >= 0) {
            status = device_http_feed(http, block, (size_t)n);
        } else if (would_block(n, url->tls)) {
            pause_briefly();
        } else {
            if (url->tls) { tls_error(tls, http->result); }
            else { http->result->sdk_error = errno; }
            status = DEVICE_READ;
        }
    }
    if (status == DEVICE_OK) { status = check(timing); }
    if (status != DEVICE_OK) { return status; }
    const device_status_t parsed = device_http_result(http);
    status = check(timing);
    return status == DEVICE_OK ? parsed : status;
}

bool device_authorize_formal(void)
{
    /* H10 must provide the approved scope-aware credential adapter first. */
    return false;
}

device_result_t device_request(const device_request_t *request, char *buffer, size_t capacity,
                               device_cancel_cb cancel, void *context)
{
    device_result_t result = {.status = DEVICE_INVALID};
    device_url_t url;
    if (!request || (unsigned)request->mode > DEVICE_FORMAL_HTTPS ||
        (unsigned)request->method > DEVICE_POST || (unsigned)request->kind > DEVICE_STREAM_RESPONSE ||
        !device_url(request->url, request->mode, &url) ||
        request->timeout_ms == 0 || request->timeout_ms > REQUEST_MAX_MS ||
        (request->kind == DEVICE_JSON_RESPONSE && (!buffer || !capacity || capacity > DEVICE_BODY_MAX)) ||
        (request->kind == DEVICE_STREAM_RESPONSE && (!request->stream || !request->stream_limit)) ||
        (request->method == DEVICE_POST && (!request->body || !request->body_size ||
                                           request->body_size > DEVICE_WRITE_MAX)) ||
        (request->mock_scenario && !device_header_token(request->mock_scenario, 48)) ||
        ((request->demo_ca != NULL) != (request->demo_ca_size != 0))) {
        return result;
    }
    if (request->mode == DEVICE_FORMAL_HTTPS &&
        (request->demo_ca || request->mock_scenario || !device_authorize_formal())) {
        result.status = DEVICE_NOT_AUTHORIZED;
        return result;
    }
    if (request->mode == DEVICE_DEMO_HTTP && request->demo_ca) { return result; }
    if (url.tls && !device_wifi_clock_ready()) {
        result.status = device_wifi_online() ? DEVICE_CLOCK : DEVICE_OFFLINE;
        return result;
    }
    const int64_t start = now_ms();
    const timing_t timing = {.deadline = start + request->timeout_ms, .cancel = cancel, .context = context};
    for (unsigned attempt = 1;; ++attempt) {
        result = (device_result_t){.attempts = attempt, .status = check(&timing)};
        dns_answer_t dns = {0};
        if (result.status == DEVICE_OK) { result.status = resolve(&url, &timing, &dns); }
        if (result.status == DEVICE_DNS) { result.sdk_error = dns.error; }
        if (result.status == DEVICE_OK && request->mode != DEVICE_FORMAL_HTTPS && !lan_ipv4(dns.address)) {
            result.status = DEVICE_INVALID;
        }
        if (result.status == DEVICE_OK) {
            esp_tls_t *tls = esp_tls_init();
            if (!tls) {
                result.status = DEVICE_RESOURCE;
            } else {
                result.status = open_connection(tls, &url, dns.address, request, &timing, &result);
                if (result.status == DEVICE_OK) {
                    device_http_t http;
                    device_http_init(&http, &result, buffer, capacity, request);
                    result.status = exchange(tls, &url, request, &timing, &http);
                }
                esp_tls_conn_destroy(tls);  /* Kept on the owning task for every path. */
            }
        }
        result = device_result_finish(result, request->method);
        if (!device_retry_get(&result, request, now_ms(), timing.deadline)) {
            break;
        }
        const int64_t retry_at = now_ms() + DEVICE_RETRY_DELAY_MS;
        while (now_ms() < retry_at && (result.status = check(&timing)) == DEVICE_OK) { pause_briefly(); }
        if (result.status != DEVICE_OK) { result.cause = result.status; break; }
    }
    result.elapsed_ms = (uint32_t)(now_ms() - start);
    return result;
}
