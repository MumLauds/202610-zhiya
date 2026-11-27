#include <inttypes.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "board_4_3c.h"
#include "device_api.h"
#include "device_wifi.h"
#include "driver/usb_serial_jtag.h"
#include "driver/usb_serial_jtag_vfs.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "lwip/sockets.h"
#include "mbedtls/platform_util.h"

void h08_board_boot(void);
#ifdef ZHIYA_H08_HAVE_DEMO_CA
extern const unsigned char ca_begin[] asm("_binary_h08_demo_ca_pem_start");
extern const unsigned char ca_end[] asm("_binary_h08_demo_ca_pem_end");
#endif

typedef enum { SHELF, DETAIL, SESSION, CREATE, START } operation_t;
typedef struct {
    uint32_t id;
    operation_t kind;
    uint64_t expected_version;
    char url[501], scenario[49], body[192];
} job_t;
typedef struct {
    uint32_t id, stack;
    operation_t kind;
    uint64_t first, second, third;
    device_result_t result;
} reply_t;

static QueueHandle_t jobs, replies;
static atomic_uint canceled;
static uint32_t generation, active;

static bool request_canceled(void *context)
{
    return atomic_load(&canceled) == *(const uint32_t *)context;
}

static void decode_reply(const job_t *job, char *body, reply_t *reply)
{
    const size_t size = reply->result.body_size;
    bool valid = false;
    if (job->kind == SHELF) {
        device_shelf_t *dto = heap_caps_malloc(sizeof(*dto), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        if (!dto) { reply->result.status = DEVICE_RESOURCE; return; }
        valid = device_decode_shelf(body, size, dto);
        if (valid) {
            reply->first = dto->count;
            reply->second = dto->count ? dto->books[0].version_id : 0;
            reply->third = dto->page;
        }
        free(dto);
    } else if (job->kind == DETAIL) {
        device_detail_t *dto = heap_caps_malloc(sizeof(*dto), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        if (!dto) { reply->result.status = DEVICE_RESOURCE; return; }
        valid = device_decode_detail(body, size, job->expected_version, dto);
        if (valid) {
            reply->first = dto->book.version_id;
            reply->second = dto->page_count;
            reply->third = 1;
        }
        free(dto);
    } else if (job->kind == SESSION || job->kind == CREATE) {
        device_session_t dto;
        valid = device_decode_session(body, size, &dto);
        if (valid) {
            reply->first = dto.session_id;
            reply->second = dto.revision;
            reply->third = dto.current_page;
        }
    } else {
        valid = true;  /* START response is only a protocol test in H08. */
    }
    if (!valid) {
        reply->result.cause = DEVICE_JSON;
        reply->result.status = job->kind == CREATE ? DEVICE_OUTCOME_UNKNOWN : DEVICE_JSON;
    }
}

static void network(void *ignored)
{
    (void)ignored;
    for (;;) {
        job_t job;
        xQueueReceive(jobs, &job, portMAX_DELAY);
        const bool secure = strncmp(job.url, "https://", 8) == 0;
        device_request_t request = {
            .url = job.url, .mode = secure ? DEVICE_DEMO_HTTPS : DEVICE_DEMO_HTTP,
            .method = job.body[0] ? DEVICE_POST : DEVICE_GET, .kind = DEVICE_JSON_RESPONSE,
            .body = job.body, .body_size = strlen(job.body),
            .mock_scenario = job.scenario[0] ? job.scenario : NULL, .timeout_ms = 8000,
        };
#ifdef ZHIYA_H08_HAVE_DEMO_CA
        if (secure) {
            request.demo_ca = ca_begin;
            request.demo_ca_size = (uintptr_t)ca_end - (uintptr_t)ca_begin;
        }
#endif
        char *body = heap_caps_malloc(DEVICE_BODY_MAX + 1, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        reply_t reply = {.id = job.id, .kind = job.kind, .result.status = DEVICE_RESOURCE};
        if (body) {
            reply.result = device_request(&request, body, DEVICE_BODY_MAX, request_canceled, &job.id);
            if (reply.result.status == DEVICE_OK && request_canceled(&job.id)) {
                reply.result.cause = DEVICE_CANCELED;
                reply.result.status = request.method == DEVICE_POST ? DEVICE_OUTCOME_UNKNOWN : DEVICE_CANCELED;
            }
            if (reply.result.status == DEVICE_OK) { decode_reply(&job, body, &reply); }
            mbedtls_platform_zeroize(body, DEVICE_BODY_MAX + 1);
            free(body);
        }
        reply.stack = uxTaskGetStackHighWaterMark(NULL);
        xQueueOverwrite(replies, &reply);
    }
}

static bool safe_token(const char *s, size_t max)
{
    if (!s || !*s || strlen(s) > max) { return false; }
    for (; *s; ++s) {
        if (!((*s >= 'a' && *s <= 'z') || (*s >= 'A' && *s <= 'Z') ||
              (*s >= '0' && *s <= '9') || *s == '-' || *s == '_' || *s == '.')) { return false; }
    }
    return true;
}

static void poll_diagnostics(void)
{
    device_wifi_poll();
    reply_t reply;
    if (xQueueReceive(replies, &reply, 0) != pdPASS || reply.id != active) { return; }
    active = 0;
    device_result_t *r = &reply.result;
    if (r->status == DEVICE_OK && request_canceled(&reply.id)) {
        r->cause = DEVICE_CANCELED;
        r->status = reply.kind == CREATE || reply.kind == START ? DEVICE_OUTCOME_UNKNOWN : DEVICE_CANCELED;
    }
    printf("[H08] req=%" PRIu32 " status=%s cause=%s http=%u bytes=%u elapsed_ms=%" PRIu32
           " attempts=%u sdk=%d tls=%d cert_flags=0x%x request_id=%s code=%s stack_free=%" PRIu32 "\n",
           reply.id, device_status_name(r->status), device_status_name(r->cause), r->http_status,
           (unsigned)r->body_size, r->elapsed_ms, r->attempts, r->sdk_error, r->tls_error, (unsigned)r->tls_flags,
           safe_token(r->request_id, DEVICE_REQUEST_ID_MAX) ? r->request_id : "-",
           safe_token(r->error_code, 64) ? r->error_code : "-", reply.stack);
    if (r->has_retry_after) {
        printf("[H08] retry_after_seconds=%" PRIu32 "\n", r->retry_after_seconds);
    }
    if (r->status == DEVICE_OK && reply.kind != START) {
        const char *labels = reply.kind == SHELF ? "books,first_version,page" :
                             reply.kind == DETAIL ? "version,pages,first_page" : "session,revision,page";
        printf("[H08] dto %s=%" PRIu64 ",%" PRIu64 ",%" PRIu64 "\n",
               labels, reply.first, reply.second, reply.third);
    }
    board_4_3c_log_memory();
}

static bool read_line(char *line, size_t capacity)
{
    static bool after_cr;
    size_t size = 0;
    bool too_long = false;
    for (;;) {
        poll_diagnostics();
        unsigned char c;
        if (usb_serial_jtag_read_bytes(&c, 1, pdMS_TO_TICKS(30) + 1) != 1) { continue; }
        if (c == '\n' && after_cr) { after_cr = false; continue; }
        after_cr = c == '\r';
        if (c == '\r' || c == '\n') {
            line[size] = 0;
            return !too_long;
        }
        if (c == 3) { line[0] = 0; return false; }
        if (c == 8 || c == 127) { if (size) { line[--size] = 0; } continue; }
        if (c < 0x20) { continue; }
        if (size + 1 < capacity) { line[size++] = (char)c; }
        else { too_long = true; }
    }
}

static void join(void)
{
    char ssid[33] = "", password[64] = "";
    fputs("SSID (hidden): ", stdout);
    if (read_line(ssid, sizeof(ssid))) {
        fputs("\nPassword (hidden, blank for open AP): ", stdout);
        if (read_line(password, sizeof(password))) {
            const esp_err_t err = device_wifi_join(ssid, password);
            printf("\n[H08] join=%s\n", esp_err_to_name(err));
        }
    }
    mbedtls_platform_zeroize(ssid, sizeof(ssid));
    mbedtls_platform_zeroize(password, sizeof(password));
}

static bool number(const char *text, uint64_t minimum, uint64_t *out)
{
    if (!text || !*text) { return false; }
    uint64_t value = 0;
    for (const char *p = text; *p; ++p) {
        if (*p < '0' || *p > '9' || value > (DEVICE_MAX_ID - (*p - '0')) / 10) { return false; }
        value = value * 10 + (unsigned)(*p - '0');
    }
    *out = value;
    return value >= minimum;
}

static bool submit(operation_t kind, const char *base, const char *id_arg, const char *rev_arg,
                   const char *key, const char *scenario)
{
    if (active) { puts("[H08] request busy"); return true; }
    const char *authority = base && strncmp(base, "http://", 7) == 0 ? base + 7 :
                            base && strncmp(base, "https://", 8) == 0 ? base + 8 : NULL;
    if (!authority || !*authority || strpbrk(authority, "/?#@") ||
        (scenario && !safe_token(scenario, 48))) { return false; }
    job_t job = {.kind = kind};
    uint64_t id = 0, rev = 0;
    if (kind != SHELF && !number(id_arg, 1, &id)) { return false; }
    if (kind == START && !number(rev_arg, 0, &rev)) { return false; }
    if ((kind == CREATE || kind == START) && !safe_token(key, 48)) { return false; }
    int length;
    switch (kind) {
    case SHELF:
        length = snprintf(job.url, sizeof(job.url), "%s/api/v1/storybooks?page=1&pageSize=20", base);
        break;
    case DETAIL:
        job.expected_version = id;
        length = snprintf(job.url, sizeof(job.url), "%s/api/v1/story-versions/%" PRIu64, base, id);
        break;
    case SESSION:
        length = snprintf(job.url, sizeof(job.url), "%s/api/v1/reading-sessions/%" PRIu64, base, id);
        break;
    case CREATE:
        length = snprintf(job.url, sizeof(job.url), "%s/api/v1/reading-sessions", base);
        snprintf(job.body, sizeof(job.body),
                 "{\"childProfileId\":1,\"storyVersionId\":%" PRIu64
                 ",\"mode\":\"PARENT_CHILD\",\"clientRequestId\":\"%s\"}", id, key);
        break;
    default:
        length = snprintf(job.url, sizeof(job.url), "%s/api/v1/reading-sessions/%" PRIu64 "/events", base, id);
        snprintf(job.body, sizeof(job.body),
                 "{\"eventType\":\"STARTED\",\"pageNumber\":1,\"expectedRevision\":%" PRIu64
                 ",\"clientEventId\":\"%s\"}", rev, key);
        break;
    }
    if (length < 0 || (size_t)length >= sizeof(job.url)) { return false; }
    if (scenario) { strcpy(job.scenario, scenario); }
    if (++generation == 0) { ++generation; }
    atomic_store(&canceled, 0);
    job.id = generation;
    if (xQueueSend(jobs, &job, 0) != pdPASS) { return false; }
    active = generation;
    printf("[H08] queued=%" PRIu32 "\n", active);
    board_4_3c_log_memory();
    return true;
}

static void command(char *line)
{
    char *save = NULL, *argv[7] = {0};
    unsigned argc = 0;
    for (char *word = strtok_r(line, " ", &save); word; word = strtok_r(NULL, " ", &save)) {
        if (argc == sizeof(argv) / sizeof(argv[0])) { puts("[H08] too many arguments"); return; }
        argv[argc++] = word;
    }
    if (!argc) { return; }
    const char *cmd = argv[0];
    if (argc == 1 && strcmp(cmd, "join") == 0) { join(); return; }
    if (argc == 1 && (strcmp(cmd, "leave") == 0 || strcmp(cmd, "cancel") == 0)) {
        atomic_store(&canceled, active);
        if (strcmp(cmd, "leave") == 0) { printf("[H08] leave=%s\n", esp_err_to_name(device_wifi_stop())); }
        else { printf("[H08] cancel=%" PRIu32 "\n", active); }
        return;
    }
    if (argc == 1 && strcmp(cmd, "status") == 0) {
        const device_wifi_info_t state = device_wifi_info();
        const struct in_addr ip = {.s_addr = state.ip};
        char address[INET_ADDRSTRLEN] = "-";
        inet_ntop(AF_INET, &ip, address, sizeof(address));
        printf("[H08] wifi=%s ip=%s RSSI=%d retries=%u reason=%u error=%s clock=%s\n",
               device_wifi_state_name(state.state), address, state.rssi, state.retries, state.reason,
               esp_err_to_name(state.error), device_wifi_clock_ready() ? "SYNCED" : "UNSYNCED");
        board_4_3c_log_memory();
        return;
    }
    if (argc >= 2 && argc <= 3 && strcmp(cmd, "shelf") == 0 && submit(SHELF, argv[1], NULL, NULL, NULL, argv[2])) { return; }
    if (argc >= 3 && argc <= 4 && strcmp(cmd, "detail") == 0 && submit(DETAIL, argv[1], argv[2], NULL, NULL, argv[3])) { return; }
    if (argc >= 3 && argc <= 4 && strcmp(cmd, "session") == 0 && submit(SESSION, argv[1], argv[2], NULL, NULL, argv[3])) { return; }
    if (argc >= 4 && argc <= 5 && strcmp(cmd, "create") == 0 && submit(CREATE, argv[1], argv[2], NULL, argv[3], argv[4])) { return; }
    if (argc >= 5 && argc <= 6 && strcmp(cmd, "start") == 0 && submit(START, argv[1], argv[2], argv[3], argv[4], argv[5])) { return; }
    puts("[H08 DEMO] join | status | cancel | leave");
    puts("shelf ORIGIN [FAULT] | detail ORIGIN VERSION [FAULT] | session ORIGIN ID [FAULT]");
    puts("create ORIGIN VERSION KEY [FAULT] | start ORIGIN ID REVISION KEY [FAULT]");
}

void app_main(void)
{
    h08_board_boot();
    usb_serial_jtag_driver_config_t serial = USB_SERIAL_JTAG_DRIVER_CONFIG_DEFAULT();
    esp_err_t err = usb_serial_jtag_driver_install(&serial);
    if (err == ESP_OK) {
        usb_serial_jtag_vfs_use_driver();
        setvbuf(stdout, NULL, _IONBF, 0);
        esp_log_level_set("wifi", ESP_LOG_WARN);
        err = device_wifi_init();
    }
    if (err != ESP_OK) {
        ESP_LOGE("h08", "Diagnostic initialization failed: %s", esp_err_to_name(err));
        return;
    }
    jobs = xQueueCreate(1, sizeof(job_t));
    replies = xQueueCreate(1, sizeof(reply_t));
    if (!jobs || !replies || xTaskCreate(network, "h08_http", 12288, NULL, 4, NULL) != pdPASS) {
        if (jobs) { vQueueDelete(jobs); }
        if (replies) { vQueueDelete(replies); }
        ESP_LOGE("h08", "Diagnostic task allocation failed");
        return;
    }
    puts("[H08 DEMO] Ready. Serial input is hidden. Type help for commands.");
    char line[550];
    while (true) {
        if (read_line(line, sizeof(line))) { command(line); }
        mbedtls_platform_zeroize(line, sizeof(line));
    }
}
