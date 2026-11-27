#include "http.h"

#include <stdio.h>
#include <string.h>

enum { HEADER_BYTES_MAX = 8192 };

static unsigned lower_ascii(unsigned ch)
{
    return ch >= 'A' && ch <= 'Z' ? ch + ('a' - 'A') : ch;
}

static bool alnum_ascii(unsigned ch)
{
    return (ch >= '0' && ch <= '9') || (lower_ascii(ch) >= 'a' && lower_ascii(ch) <= 'z');
}

static bool same_ascii(const char *a, const char *b, size_t size)
{
    for (size_t i = 0; i < size; ++i) {
        if (lower_ascii((unsigned char)a[i]) != lower_ascii((unsigned char)b[i]) || !a[i]) {
            return false;
        }
    }
    return true;
}

bool device_header_token(const char *text, size_t limit)
{
    if (!text || !*text) {
        return false;
    }
    for (size_t i = 0; text[i]; ++i) {
        const unsigned char ch = text[i];
        if (i >= limit || (!alnum_ascii(ch) && ch != '.' && ch != '_' && ch != '-')) {
            return false;
        }
    }
    return true;
}

bool device_url(const char *url, device_mode_t mode, device_url_t *out)
{
    struct http_parser_url parsed;
    http_parser_url_init(&parsed);
    if (!url || !out || strlen(url) > 500 || http_parser_parse_url(url, strlen(url), 0, &parsed)) {
        return false;
    }
    const unsigned fields = parsed.field_set;
    if (!(fields & (1U << UF_SCHEMA)) || !(fields & (1U << UF_HOST)) ||
        (fields & ((1U << UF_USERINFO) | (1U << UF_FRAGMENT)))) {
        return false;
    }
    const char *scheme = url + parsed.field_data[UF_SCHEMA].off;
    const size_t scheme_size = parsed.field_data[UF_SCHEMA].len;
    const bool tls = scheme_size == 5 && memcmp(scheme, "https", 5) == 0;
    if ((mode == DEVICE_DEMO_HTTP && (scheme_size != 4 || memcmp(scheme, "http", 4))) ||
        (mode != DEVICE_DEMO_HTTP && !tls)) {
        return false;
    }
    *out = (device_url_t){.tls = tls};
    out->port = fields & (1U << UF_PORT) ? parsed.port : tls ? 443 : 80;
    const size_t host_size = parsed.field_data[UF_HOST].len;
    if (!out->port || !host_size || host_size >= sizeof(out->host)) {
        return false;
    }
    memcpy(out->host, url + parsed.field_data[UF_HOST].off, host_size);
    for (size_t i = 0; i < host_size; ++i) {
        const unsigned char ch = out->host[i];
        if (!alnum_ascii(ch) && ch != '-' && ch != '.') {
            return false;  /* This STA diagnostic uses IPv4 and DNS hostnames. */
        }
    }
    const char *path = fields & (1U << UF_PATH) ? url + parsed.field_data[UF_PATH].off : NULL;
    const char *query = fields & (1U << UF_QUERY) ? url + parsed.field_data[UF_QUERY].off : NULL;
    if ((path && *path != '/') || (path && strlen(path) >= sizeof(out->path))) {
        return false;
    }
    const int length = path ? snprintf(out->path, sizeof(out->path), "%s", path) :
        query ? snprintf(out->path, sizeof(out->path), "/?%s", query) : snprintf(out->path, sizeof(out->path), "/");
    if (length <= 0 || (size_t)length >= sizeof(out->path)) {
        return false;
    }
    for (int i = 0; i < length; ++i) {
        if ((unsigned char)out->path[i] < 0x21 || (unsigned char)out->path[i] >= 0x7f ||
            out->path[i] == '\\') {
            return false;
        }
    }
    return true;
}

static int fail(device_http_t *http, device_status_t status)
{
    http->failure = status;
    return -1;
}

static int save_header(device_http_t *http)
{
    if (!http->writing_value) {
        return 0;
    }
    const char *value = http->value;
    size_t size = http->value_size;
    while (size && (*value == ' ' || *value == '\t')) {
        ++value;
        --size;
    }
    while (size && (value[size - 1] == ' ' || value[size - 1] == '\t')) {
        --size;
    }
    if (strcmp(http->field, "content-type") == 0) {
        if (http->seen_type || size >= sizeof(http->result->content_type)) {
            return fail(http, DEVICE_HTTP_PROTOCOL);
        }
        memcpy(http->result->content_type, value, size);
        http->result->content_type[size] = 0;
        http->seen_type = true;
    } else if (strcmp(http->field, "x-request-id") == 0) {
        if (http->seen_id || size >= sizeof(http->result->request_id)) {
            return fail(http, DEVICE_HTTP_PROTOCOL);
        }
        memcpy(http->result->request_id, value, size);
        http->result->request_id[size] = 0;
        http->seen_id = true;
    }
    http->field_size = http->value_size = 0;
    http->writing_value = false;
    return 0;
}

static int field_cb(http_parser *parser, const char *bytes, size_t size)
{
    device_http_t *http = parser->data;
    if (http->headers_done) {
        return 0;  /* Trailers never replace primary headers. */
    }
    if (save_header(http)) {
        return -1;
    }
    if (size >= sizeof(http->field) - http->field_size ||
        (http->head_size += size + 1) > HEADER_BYTES_MAX) {
        return fail(http, DEVICE_LIMIT);
    }
    for (size_t i = 0; i < size; ++i) {
        http->field[http->field_size++] = (char)lower_ascii((unsigned char)bytes[i]);
    }
    http->field[http->field_size] = 0;
    return 0;
}

static int value_cb(http_parser *parser, const char *bytes, size_t size)
{
    device_http_t *http = parser->data;
    if (http->headers_done) {
        return 0;
    }
    if (size >= sizeof(http->value) - http->value_size ||
        (http->head_size += size + 1) > HEADER_BYTES_MAX) {
        return fail(http, DEVICE_LIMIT);
    }
    memcpy(http->value + http->value_size, bytes, size);
    http->value_size += size;
    http->writing_value = true;
    return 0;
}

static bool json_type(const char *type)
{
    const char expected[] = "application/json";
    const size_t n = sizeof(expected) - 1;
    if (!same_ascii(type, expected, n)) { return false; }
    type += n;
    while (*type == ' ' || *type == '\t') { ++type; }
    return !*type || *type == ';';
}

static int headers_cb(http_parser *parser)
{
    device_http_t *http = parser->data;
    http->result->http_status = parser->status_code;
    if (save_header(http)) {
        return -1;
    }
    if (parser->status_code < 200 || parser->upgrade) {
        return fail(http, DEVICE_HTTP_PROTOCOL);
    }
    if (parser->nread > HEADER_BYTES_MAX) {
        return fail(http, DEVICE_LIMIT);
    }
    if (parser->content_length != UINT64_MAX && parser->content_length > http->limit) {
        return fail(http, DEVICE_LIMIT);
    }
    if (http->stream_mode && (parser->status_code < 200 || parser->status_code >= 300 ||
        json_type(http->result->content_type) || !http->result->content_type[0])) {
        return fail(http, parser->status_code >= 300 ? DEVICE_HTTP_STATUS : DEVICE_CONTENT_TYPE);
    }
    http->headers_done = true;
    return 0;
}

static int body_cb(http_parser *parser, const char *bytes, size_t size)
{
    device_http_t *http = parser->data;
    if (size > http->limit - http->result->body_size) {
        return fail(http, DEVICE_LIMIT);
    }
    if (http->stream_mode) {
        if (!http->stream(bytes, size, http->stream_context)) {
            return fail(http, DEVICE_CANCELED);
        }
    } else {
        memcpy(http->body + http->result->body_size, bytes, size);
    }
    http->result->body_size += size;
    return 0;
}

static int complete_cb(http_parser *parser)
{
    device_http_t *http = parser->data;
    http->complete = true;
    http_parser_pause(parser, 1);
    return 0;
}

void device_http_init(device_http_t *http, device_result_t *result, char *body, size_t capacity,
                      const device_request_t *request)
{
    *http = (device_http_t){.result = result, .body = body, .limit = capacity,
                            .stream_mode = request->kind == DEVICE_STREAM_RESPONSE,
                            .stream = request->stream, .stream_context = request->stream_context};
    if (http->stream_mode) {
        http->limit = request->stream_limit;
    }
    http_parser_init(&http->parser, HTTP_RESPONSE);
    http->parser.data = http;
}

device_status_t device_http_feed(device_http_t *http, const char *data, size_t size)
{
    const http_parser_settings settings = {
        .on_header_field = field_cb, .on_header_value = value_cb, .on_headers_complete = headers_cb,
        .on_body = body_cb, .on_message_complete = complete_cb,
    };
    const size_t consumed = http_parser_execute(&http->parser, &settings, data, size);
    if (http->failure) {
        return http->failure;
    }
    if (consumed != size || (!http->complete && HTTP_PARSER_ERRNO(&http->parser) != HPE_OK) ||
        (!size && !http->complete)) {
        return DEVICE_HTTP_PROTOCOL;
    }
    return DEVICE_OK;
}

device_status_t device_http_result(device_http_t *http)
{
    if (!http->complete) {
        return DEVICE_HTTP_PROTOCOL;
    }
    const unsigned status = http->result->http_status;
    const bool success = status >= 200 && status < 300;
    if (http->stream_mode) {
        return success ? DEVICE_OK : DEVICE_HTTP_STATUS;
    }
    http->body[http->result->body_size] = 0;
    if (!json_type(http->result->content_type)) {
        return success ? DEVICE_CONTENT_TYPE : DEVICE_HTTP_STATUS;
    }
    if (!device_decode_envelope(http->body, http->result->body_size, success, http->result)) {
        return success ? DEVICE_JSON : DEVICE_HTTP_STATUS;
    }
    return success ? DEVICE_OK : DEVICE_HTTP_STATUS;
}

device_result_t device_result_finish(device_result_t result, device_method_t method)
{
    switch (result.status == DEVICE_HTTP_STATUS ? result.http_status : 0) {
    case 401: result.status = DEVICE_UNAUTHORIZED; break;
    case 403: result.status = DEVICE_FORBIDDEN; break;
    case 404: result.status = DEVICE_NOT_FOUND; break;
    case 409:
        result.status = strcmp(result.error_code, "SESSION_CONFLICT") == 0 ? DEVICE_SESSION_CONFLICT :
            strcmp(result.error_code, "IDEMPOTENCY_CONFLICT") == 0 ? DEVICE_IDEMPOTENCY_CONFLICT : DEVICE_CONFLICT;
        break;
    case 503: result.status = DEVICE_UNAVAILABLE; break;
    case 504: result.status = DEVICE_OUTCOME_UNKNOWN; break;
    default: break;
    }
    result.cause = result.status;
    if (method == DEVICE_POST && result.sent &&
        (result.http_status >= 500 || strcmp(result.error_code, "REQUEST_OUTCOME_UNKNOWN") == 0 ||
         (!result.error_code[0] && result.status != DEVICE_OK))) {
        result.status = DEVICE_OUTCOME_UNKNOWN;
    }
    return result;
}

device_status_t device_gate(bool canceled, bool online, int64_t now, int64_t deadline)
{
    if (canceled) { return DEVICE_CANCELED; }
    if (!online) { return DEVICE_OFFLINE; }
    return now >= deadline ? DEVICE_TIMEOUT : DEVICE_OK;
}

bool device_retry_get(const device_result_t *result, const device_request_t *request,
                      int64_t now, int64_t deadline)
{
    return request->method == DEVICE_GET && request->kind == DEVICE_JSON_RESPONSE && result->attempts < 2 &&
           now + DEVICE_RETRY_DELAY_MS < deadline &&
           (result->status == DEVICE_DNS || result->status == DEVICE_CONNECT ||
            result->status == DEVICE_READ || (result->status == DEVICE_UNAVAILABLE && result->retryable));
}

const char *device_status_name(device_status_t status)
{
    static const char *const names[] = {
        "OK", "INVALID", "RESOURCE", "NOT_AUTHORIZED", "OFFLINE", "CLOCK", "DNS", "CONNECT",
        "TLS", "READ", "TIMEOUT", "CANCELED", "LIMIT", "HTTP_PROTOCOL", "CONTENT_TYPE", "JSON",
        "HTTP_STATUS", "UNAUTHORIZED", "FORBIDDEN", "NOT_FOUND", "CONFLICT", "SESSION_CONFLICT",
        "IDEMPOTENCY_CONFLICT", "UNAVAILABLE", "REQUEST_OUTCOME_UNKNOWN",
    };
    return (unsigned)status < sizeof(names) / sizeof(names[0]) ? names[status] : "UNKNOWN";
}
