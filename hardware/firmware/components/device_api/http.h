#pragma once

#include "device_api.h"
#include "http_parser.h"

typedef struct {
    char host[128], path[384];
    uint16_t port;
    bool tls;
} device_url_t;

typedef struct {
    http_parser parser;
    device_result_t *result;
    char *body;
    size_t limit, head_size;
    size_t field_size, value_size;
    char field[48], value[256];
    bool seen_type, seen_id, writing_value, headers_done, complete, stream_mode;
    device_status_t failure;
    device_stream_cb stream;
    void *stream_context;
} device_http_t;

enum { DEVICE_RETRY_DELAY_MS = 180 };

bool device_url(const char *url, device_mode_t mode, device_url_t *out);
bool device_header_token(const char *text, size_t limit);
void device_http_init(device_http_t *http, device_result_t *result, char *body, size_t capacity,
                      const device_request_t *request);
device_status_t device_http_feed(device_http_t *http, const char *data, size_t size);
device_status_t device_http_result(device_http_t *http);
device_result_t device_result_finish(device_result_t result, device_method_t method);
device_status_t device_gate(bool canceled, bool online, int64_t now, int64_t deadline);
bool device_retry_get(const device_result_t *result, const device_request_t *request,
                      int64_t now, int64_t deadline);
