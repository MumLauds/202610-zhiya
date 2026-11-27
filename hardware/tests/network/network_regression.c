#include "http.h"

#include <assert.h>
#include <stdio.h>
#include <string.h>

static char body[DEVICE_BODY_MAX + 1];
static char wire[DEVICE_BODY_MAX + 8192];
static device_shelf_t shelf;
static device_detail_t detail;

static device_result_t parse_wire(const char *bytes, size_t size, bool eof, size_t piece, device_method_t method)
{
    const device_request_t request = {.kind = DEVICE_JSON_RESPONSE};
    device_result_t result = {.sent = method == DEVICE_POST};
    device_http_t http;
    device_http_init(&http, &result, body, DEVICE_BODY_MAX, &request);
    for (size_t at = 0; at < size && result.status == DEVICE_OK; at += piece) {
        size_t count = piece < size - at ? piece : size - at;
        result.status = device_http_feed(&http, bytes + at, count);
    }
    if (eof && !http.complete && result.status == DEVICE_OK) {
        result.status = device_http_feed(&http, "", 0);
    }
    if (result.status == DEVICE_OK) { result.status = device_http_result(&http); }
    return device_result_finish(result, method);
}

static device_result_t literal(const char *http)
{
    return parse_wire(http, strlen(http), true, 13, DEVICE_GET);
}

static void http_framing(void)
{
    const char chunked[] =
        "HTTP/1.1 200 OK\r\nContent-Type: Application/JSON; charset=UTF-8\r\n"
        "X-Request-Id: header-id\r\nTransfer-Encoding: chunked\r\n\r\n"
        "8\r\n{\"data\":\r\n"
        "13\r\n{},\"requestId\":\"r\"}\r\n0\r\n\r\n";
    const char json[] = "{\"data\":{},\"requestId\":\"r\"}";
    for (size_t piece = 1; piece <= sizeof(chunked); ++piece) {
        const device_result_t result = parse_wire(chunked, sizeof(chunked) - 1, true, piece, DEVICE_GET);
        assert(result.status == DEVICE_OK && strcmp(body, json) == 0 && strcmp(result.request_id, "r") == 0);
    }
    snprintf(wire, sizeof(wire), "HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n\r\n%s", json);
    assert(literal(wire).status == DEVICE_OK);
    snprintf(wire, sizeof(wire), "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: %u\r\n\r\n%s",
             (unsigned)strlen(json) + 1, json);
    assert(literal(wire).status == DEVICE_HTTP_PROTOCOL);
    assert(literal("HTTP/1.1 200 OK\r\nContent-Length: 32769\r\n\r\n").status == DEVICE_LIMIT);
    int prefix = snprintf(wire, sizeof(wire),
        "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\n\r\n8001\r\n");
    memset(wire + prefix, 'x', DEVICE_BODY_MAX + 1);
    assert(parse_wire(wire, (size_t)prefix + DEVICE_BODY_MAX + 1, false, 113, DEVICE_GET).status == DEVICE_LIMIT);
    assert(literal("HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: 3\r\n\r\nbad").status ==
           DEVICE_CONTENT_TYPE);
    assert(literal("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{}").status ==
           DEVICE_JSON);
    assert(literal("HTTP/1.1 302 Found\r\nLocation: https://other.test\r\nContent-Length: 0\r\n\r\n").status ==
           DEVICE_HTTP_STATUS);
    puts("PASS HTTP chunk fragmentation, close-delimited body, truncation, limits, MIME, and redirects");
}

static void dto_fields(void)
{
    const char template[] = "{\"requestId\":\"ok\",\"data\":{\"page\":1,\"pageSize\":1,\"unknown\":0.25,\"items\":["
        "{\"storybookId\":%s,\"storyVersionId\":4294967297,\"versionNumber\":9007199254740991,"
        "\"title\":\"\xe7\x9f\xa5\xe8\x8a\xbd\",\"totalPages\":2,\"coverRef\":null}]}}";
    snprintf(body, sizeof(body), template, "9007199254740991");
    assert(device_decode_shelf(body, strlen(body), &shelf) && shelf.count == 1);
    assert(shelf.books[0].book_id == DEVICE_MAX_ID && shelf.books[0].version_id == UINT64_C(4294967297) &&
           shelf.books[0].version_number == DEVICE_MAX_ID && !shelf.books[0].cover_ref[0]);
    const char *const ids[] = {"9007199254740992", "9007199254740991.1", "01", "-1", "1e0", "null", "true", "\"1\""};
    for (size_t i = 0; i < sizeof(ids) / sizeof(ids[0]); ++i) {
        snprintf(body, sizeof(body), template, ids[i]);
        assert(!device_decode_shelf(body, strlen(body), &shelf));
    }
    const char *list_bad[] = {"{\"data\":[],\"requestId\":\"\\u0000\"}",
        "{\"data\":[],\"requestId\":\"\\uXYZW\"}", "{\"data\":[],\"requestId\":\"\xc0\xaf\"}",
        "{\"data\":[],\"requestId\":\"x\",\"requestId\":\"y\"}",
        "{\"data\":[[[[[[[[[[[[[0]]]]]]]]]]]]],\"requestId\":\"x\"}",
        "{\"data\":[],\"requestId\":\"x\"}false"};
    device_result_t result = {0};
    for (size_t i = 0; i < sizeof(list_bad) / sizeof(list_bad[0]); ++i) {
        assert(!device_decode_envelope(list_bad[i], strlen(list_bad[i]), true, &result));
    }
    size_t n = (size_t)snprintf(body, sizeof(body), "{\"data\":[0");
    for (size_t i = 0; i < 128; ++i) { n += (size_t)snprintf(body + n, sizeof(body) - n, ",0"); }
    n += (size_t)snprintf(body + n, sizeof(body) - n, "],\"requestId\":\"x\"}");
    assert(!device_decode_envelope(body, n, true, &result));
    puts("PASS JSON exact 53-bit numbers, UTF-8, null/unknown fields, depth, arrays, and malformed data");
}

static void detail_graph(void)
{
    const char page[] = "{\"pageNumber\":%d,\"defaultNextPageNumber\":%s,\"text\":\"text\",\"imageRef\":null}";
    const char prefix[] = "{\"data\":{\"storybookId\":1,\"storyVersionId\":9007199254740991,"
        "\"versionNumber\":2,\"title\":\"book\",\"pages\":[";
    char pages[3][112];
    for (unsigned scenario = 0; scenario < 5; ++scenario) {
        snprintf(pages[0], sizeof(pages[0]), page, 9, "null");
        snprintf(pages[1], sizeof(pages[1]), page, scenario == 2 ? 4 : 1,
                 scenario == 3 ? "8" : scenario == 4 ? "1" : "9");
        snprintf(pages[2], sizeof(pages[2]), page, scenario == 1 ? 9 : 11, "null");
        const int size = snprintf(body, sizeof(body),
            "%s%s,%s,%s],\"branches\":[{\"fromPageNumber\":1,\"nextPageNumber\":11}]},\"requestId\":\"r\"}",
            prefix, pages[0], pages[1], pages[2]);
        assert(size > 0);
        assert(device_decode_detail(body, (size_t)size, DEVICE_MAX_ID, &detail) == (scenario == 0));
        assert(detail.page_count == (scenario == 0 ? 3 : 0));
        assert(!device_decode_detail(body, (size_t)size, 2, &detail) && detail.page_count == 0);
    }
    puts("PASS detail version, unique pages, page 1, and valid successors with a branch-only page");
}

static void write_outcomes(void)
{
    const char pending[] = "{\"error\":{\"code\":\"REQUEST_IN_PROGRESS\",\"message\":\"busy\",\"retryable\":true},"
        "\"requestId\":\"pending-1\"}";
    const char *const headers[] = {"Retry-After: 2\r\n", "", "Retry-After: 4294967296\r\n",
                                   "Retry-After: 2\r\nRetry-After: 9\r\n"};
    const device_request_t post = {.method = DEVICE_POST, .kind = DEVICE_JSON_RESPONSE};
    for (size_t i = 0; i < sizeof(headers) / sizeof(headers[0]); ++i) {
        const int size = snprintf(wire, sizeof(wire),
            "HTTP/1.1 409 Conflict\r\nContent-Type: application/json\r\nContent-Length: %u\r\n%s\r\n%s",
            (unsigned)strlen(pending), headers[i], pending);
        const device_result_t result = parse_wire(wire, (size_t)size, true, 1, DEVICE_POST);
        assert(result.status == (i == 3 ? DEVICE_OUTCOME_UNKNOWN : DEVICE_REQUEST_IN_PROGRESS));
        assert(result.has_retry_after == (i == 0));
        if (result.has_retry_after) { assert(result.retry_after_seconds == 2); }
        assert(!device_retry_get(&result, &post, 0, 1000));
    }
    const char proxy[] = "HTTP/1.1 409 Conflict\r\nContent-Type: text/html\r\nContent-Length: 3\r\n\r\nbad";
    const device_result_t result = parse_wire(proxy, sizeof(proxy) - 1, true, 100, DEVICE_POST);
    assert(result.status == DEVICE_OUTCOME_UNKNOWN && result.cause == DEVICE_CONFLICT && result.http_status == 409);
    puts("PASS explicit pending, bounded Retry-After, proxy ambiguity, and no automatic write replay");
}

static void policy(void)
{
    device_url_t url;
    assert(device_url("http://192.168.0.1:8765/api/v1/storybooks?page=1", DEVICE_DEMO_HTTP, &url));
    assert(url.port == 8765 && !url.tls && strcmp(url.path, "/api/v1/storybooks?page=1") == 0);
    assert(device_url("https://zhiya.home.arpa", DEVICE_DEMO_HTTPS, &url) && url.port == 443 && url.tls);
    assert(!device_url("http://zhiya.home.arpa/", DEVICE_FORMAL_HTTPS, &url));
    assert(!device_url("https://user@zhiya.home.arpa/", DEVICE_DEMO_HTTPS, &url));
    assert(!device_url("https://zhiya.home.arpa/#fragment", DEVICE_DEMO_HTTPS, &url));
    assert(!device_header_token("x\r\nCookie: anything", 48));
    device_request_t request = {.method = DEVICE_GET, .kind = DEVICE_JSON_RESPONSE};
    device_result_t error = {.status = DEVICE_UNAVAILABLE, .retryable = true, .attempts = 1};
    assert(device_retry_get(&error, &request, 800, 1000));
    assert(!device_retry_get(&error, &request, 820, 1000));
    error.attempts = 2;
    assert(!device_retry_get(&error, &request, 0, 1000));
    request.method = DEVICE_POST;
    error.attempts = 1;
    assert(!device_retry_get(&error, &request, 0, 1000));
    assert(device_gate(false, true, 999, 1000) == DEVICE_OK);
    assert(device_gate(false, true, 1000, 1000) == DEVICE_TIMEOUT);
    assert(device_gate(true, true, 1000, 1000) == DEVICE_CANCELED);
    assert(device_gate(false, false, 0, 1000) == DEVICE_OFFLINE);
    device_result_t canceled = {.status = DEVICE_CANCELED};
    assert(device_result_finish(canceled, DEVICE_POST).status == DEVICE_CANCELED);
    canceled.sent = true;
    canceled = device_result_finish(canceled, DEVICE_POST);
    assert(canceled.status == DEVICE_OUTCOME_UNKNOWN && canceled.cause == DEVICE_CANCELED);
    device_result_t lost = {.status = DEVICE_HTTP_STATUS, .http_status = 504, .sent = true};
    assert(device_result_finish(lost, DEVICE_POST).status == DEVICE_OUTCOME_UNKNOWN);
    puts("PASS URL/scheme guard, absolute deadline, cancel precedence, retry cap, and unknown write outcome");
}

int main(int argc, char **argv)
{
    if (argc == 1) {
        http_framing();
        dto_fields();
        detail_graph();
        write_outcomes();
        policy();
        return 0;
    }
    assert(argc == 3);
    FILE *file = fopen(argv[2], "rb");
    assert(file);
    const size_t size = fread(wire, 1, sizeof(wire), file);
    assert(size < sizeof(wire) && !ferror(file));
    fclose(file);
    const bool post = strcmp(argv[1], "post") == 0 || strcmp(argv[1], "created") == 0;
    const device_result_t result = parse_wire(wire, size, true, 137, post ? DEVICE_POST : DEVICE_GET);
    printf("%s %u %s\n", device_status_name(result.status), result.http_status, result.error_code);
    if (result.status == DEVICE_OK && strcmp(argv[1], "shelf") == 0) {
        assert(device_decode_shelf(body, result.body_size, &shelf) && shelf.count == 20 && shelf.books[0].version_id == 2);
    } else if (result.status == DEVICE_OK && strcmp(argv[1], "detail") == 0) {
        assert(device_decode_detail(body, result.body_size, 2, &detail) && detail.page_count == 6 &&
               detail.book.version_id == 2 && detail.pages[0].number == 1 && !detail.pages[0].image_ref[0]);
    } else if (result.status == DEVICE_OK && (strcmp(argv[1], "session") == 0 || strcmp(argv[1], "created") == 0)) {
        device_session_t session;
        assert(device_decode_session(body, result.body_size, &session) && session.version_id == 2 && session.current_page == 1);
    }
    return 0;
}
