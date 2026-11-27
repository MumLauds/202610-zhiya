#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

enum {
    DEVICE_BODY_MAX = 32768, DEVICE_WRITE_MAX = 2048, DEVICE_BOOKS_MAX = 20,
    DEVICE_PAGES_MAX = 32, DEVICE_TITLE_MAX = 128, DEVICE_TEXT_MAX = 768,
    DEVICE_REF_MAX = 256, DEVICE_REQUEST_ID_MAX = 80, DEVICE_ERROR_MAX = 192,
};
#define DEVICE_MAX_ID UINT64_C(9007199254740991)

typedef enum {
    DEVICE_OK, DEVICE_INVALID, DEVICE_RESOURCE, DEVICE_NOT_AUTHORIZED, DEVICE_OFFLINE,
    DEVICE_CLOCK, DEVICE_DNS, DEVICE_CONNECT, DEVICE_TLS, DEVICE_READ, DEVICE_TIMEOUT,
    DEVICE_CANCELED, DEVICE_LIMIT, DEVICE_HTTP_PROTOCOL, DEVICE_CONTENT_TYPE, DEVICE_JSON,
    DEVICE_HTTP_STATUS, DEVICE_UNAUTHORIZED, DEVICE_FORBIDDEN, DEVICE_NOT_FOUND,
    DEVICE_CONFLICT, DEVICE_SESSION_CONFLICT, DEVICE_IDEMPOTENCY_CONFLICT,
    DEVICE_UNAVAILABLE, DEVICE_OUTCOME_UNKNOWN, DEVICE_REQUEST_IN_PROGRESS,
} device_status_t;

typedef enum { DEVICE_DEMO_HTTP, DEVICE_DEMO_HTTPS, DEVICE_FORMAL_HTTPS } device_mode_t;
typedef enum { DEVICE_GET, DEVICE_POST } device_method_t;
typedef enum { DEVICE_JSON_RESPONSE, DEVICE_STREAM_RESPONSE } device_response_kind_t;

typedef struct {
    uint64_t book_id, version_id, version_number, page_count;
    char title[DEVICE_TITLE_MAX + 1];
    char cover_ref[DEVICE_REF_MAX + 1];  /* Empty means JSON null. An opaque key, never a URL. */
} device_book_t;

typedef struct {
    size_t count;
    uint64_t page, page_size;
    device_book_t books[DEVICE_BOOKS_MAX];
} device_shelf_t;

typedef struct {
    uint64_t number, default_next;  /* Zero means JSON null. */
    char text[DEVICE_TEXT_MAX + 1];
    char image_ref[DEVICE_REF_MAX + 1];
} device_page_t;

typedef struct {
    device_book_t book;
    char summary[DEVICE_TEXT_MAX + 1];
    size_t page_count;
    device_page_t pages[DEVICE_PAGES_MAX];
} device_detail_t;

typedef struct {
    uint64_t session_id, version_id, revision, current_page;
    char status[32];
} device_session_t;

typedef struct {
    device_status_t status, cause;
    unsigned http_status, attempts;
    uint32_t elapsed_ms, retry_after_seconds;
    int sdk_error, tls_error, tls_flags;
    bool retryable, sent, has_retry_after;
    size_t body_size;
    char content_type[96];
    char request_id[DEVICE_REQUEST_ID_MAX + 1];
    char error_code[65];
    char message[DEVICE_ERROR_MAX + 1];
} device_result_t;

/* The caller owns the body buffer. A stream callback runs on the HTTP owner task,
 * must return promptly, and must discard incomplete streams when the result fails. */
typedef bool (*device_stream_cb)(const void *bytes, size_t size, void *context);
typedef bool (*device_cancel_cb)(void *context);

typedef struct {
    const char *url;
    device_mode_t mode;
    device_method_t method;
    device_response_kind_t kind;
    const char *body;
    size_t body_size;
    const char *mock_scenario;
    uint32_t timeout_ms;
    const unsigned char *demo_ca;
    size_t demo_ca_size;
    device_stream_cb stream;
    void *stream_context;
    size_t stream_limit;
} device_request_t;

/* There is one network owner. None of these functions may block the LVGL task.
 * buffer capacity excludes the final NUL byte. Stream mode uses no JSON buffer. */
device_result_t device_request(const device_request_t *request, char *buffer, size_t capacity,
                               device_cancel_cb cancel, void *context);
bool device_authorize_formal(void);  /* H09/H10 adapter seam. */

bool device_decode_envelope(const char *body, size_t size, bool success, device_result_t *result);
bool device_decode_shelf(const char *body, size_t size, device_shelf_t *shelf);
bool device_decode_detail(const char *body, size_t size, uint64_t expected_version, device_detail_t *detail);
bool device_decode_session(const char *body, size_t size, device_session_t *session);
const char *device_status_name(device_status_t status);
