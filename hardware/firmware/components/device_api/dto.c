#include "device_api.h"
#include "cJSON.h"

#include <string.h>

enum { JSON_DEPTH_MAX = 12, JSON_NODES_MAX = 2048, JSON_ARRAY_MAX = 128 };

static bool utf8(const unsigned char *s, size_t size)
{
    for (size_t i = 0; i < size;) {
        unsigned ch = s[i++];
        if (ch < 0x80) {
            if (!ch) { return false; }
            continue;
        }
        const unsigned bytes = ch >= 0xc2 && ch <= 0xdf ? 1 : ch >= 0xe0 && ch <= 0xef ? 2 :
                               ch >= 0xf0 && ch <= 0xf4 ? 3 : 0;
        if (!bytes || bytes > size - i || (ch == 0xe0 && s[i] < 0xa0) ||
            (ch == 0xed && s[i] >= 0xa0) || (ch == 0xf0 && s[i] < 0x90) ||
            (ch == 0xf4 && s[i] >= 0x90)) {
            return false;
        }
        for (unsigned j = 0; j < bytes; ++j) {
            if ((s[i++] & 0xc0) != 0x80) { return false; }
        }
    }
    return true;
}

static bool whitespace(char ch)
{
    return ch == ' ' || ch == '\n' || ch == '\r' || ch == '\t';
}

static bool digit(char ch)
{
    return ch >= '0' && ch <= '9';
}

/* cJSON stores numbers as doubles. Retain which source lexemes were positive,
 * exact decimal integers, so a rounded fraction cannot be mistaken for an ID. */
static const char *number_end(const char *p, const char *end, bool *exact)
{
    const bool negative = *p == '-';
    p += negative;
    if (p == end || !digit(*p) || (*p == '0' && p + 1 < end && digit(p[1]))) {
        return NULL;
    }
    uint64_t value = 0;
    bool in_range = true;
    do {
        unsigned d = (unsigned)(*p++ - '0');
        if (value > (DEVICE_MAX_ID - d) / 10) {
            in_range = false;
        } else {
            value = value * 10 + d;
        }
    } while (p < end && digit(*p));
    *exact = !negative && in_range;
    if (p < end && *p == '.') {
        *exact = false;
        ++p;
        if (p == end || !digit(*p)) { return NULL; }
        do { ++p; } while (p < end && digit(*p));
    }
    if (p < end && (*p == 'e' || *p == 'E')) {
        *exact = false;
        ++p;
        if (p < end && (*p == '+' || *p == '-')) { ++p; }
        if (p == end || !digit(*p)) { return NULL; }
        do { ++p; } while (p < end && digit(*p));
    }
    return p;
}

static bool scan(const char *p, const char *end, uint8_t *exact, size_t *numbers)
{
    unsigned depth = 0, nodes = 0;
    while (p < end) {
        if (++nodes > JSON_NODES_MAX) { return false; }
        const char ch = *p;
        if (whitespace(ch) || ch == ',' || ch == ':') {
            --nodes;
        } else if (ch == '{' || ch == '[') {
            if (++depth > JSON_DEPTH_MAX) { return false; }
        } else if (ch == '}' || ch == ']') {
            if (depth == 0) { return false; }
            --depth;
        } else if (ch == '"') {
            while (++p < end && *p != '"') {
                if ((unsigned char)*p < 0x20) { return false; }
                if (*p == '\\') {
                    if (++p == end) { return false; }
                    /* cJSON strings use C terminators. Embedded U+0000 is disallowed. */
                    if (*p == 'u') {
                        if (end - p < 5 || memcmp(p, "u0000", 5) == 0) { return false; }
                        for (unsigned i = 1; i <= 4; ++i) {
                            if (!digit(p[i]) && !(p[i] >= 'a' && p[i] <= 'f') &&
                                !(p[i] >= 'A' && p[i] <= 'F')) { return false; }
                        }
                        p += 4;
                    } else if (!strchr("\\\"/bfnrt", *p)) {
                        return false;
                    }
                }
            }
            if (p == end) { return false; }
        } else if (ch == '-' || digit(ch)) {
            bool valid;
            const char *after = number_end(p, end, &valid);
            if (!after) { return false; }
            exact[(*numbers)++] = valid;
            p = after;
            continue;
        } else if ((end - p >= 4 && (memcmp(p, "true", 4) == 0 || memcmp(p, "null", 4) == 0))) {
            p += 4;
            continue;
        } else if (end - p >= 5 && memcmp(p, "false", 5) == 0) {
            p += 5;
            continue;
        } else {
            return false;
        }
        ++p;
    }
    return depth == 0;
}

static bool mark(cJSON *node, const uint8_t *exact, size_t numbers, size_t *index)
{
    size_t children = 0;
    cJSON *item;
    cJSON_ArrayForEach(item, node) {
        if (++children > JSON_ARRAY_MAX || !mark(item, exact, numbers, index)) { return false; }
        if (cJSON_IsObject(node)) {
            for (cJSON *other = node->child; other != item; other = other->next) {
                if (strcmp(other->string, item->string) == 0) { return false; }
            }
        }
    }
    if (cJSON_IsNumber(node)) {
        if (*index >= numbers) { return false; }
        node->valueint = exact[(*index)++];
    }
    return true;
}

static cJSON *parse(const char *body, size_t size)
{
    uint8_t exact[JSON_NODES_MAX];
    size_t numbers = 0, index = 0;
    if (!body || !size || size > DEVICE_BODY_MAX || !utf8((const unsigned char *)body, size) ||
        !scan(body, body + size, exact, &numbers)) {
        return NULL;
    }
    const char *end = NULL;
    cJSON *root = cJSON_ParseWithLengthOpts(body, size, &end, false);
    if (root && end) {
        while (end < body + size && whitespace(*end)) { ++end; }
        if (end == body + size && cJSON_IsObject(root) && mark(root, exact, numbers, &index) && index == numbers) {
            return root;
        }
    }
    cJSON_Delete(root);
    return NULL;
}

static const cJSON *member(const cJSON *parent, const char *name)
{
    if (!cJSON_IsObject(parent)) { return NULL; }
    const cJSON *found = NULL, *item;
    cJSON_ArrayForEach(item, parent) {
        if (strcmp(item->string, name) == 0) {
            if (found) { return NULL; }
            found = item;
        }
    }
    return found;
}

static bool integer(const cJSON *parent, const char *key, uint64_t minimum, uint64_t max, uint64_t *out)
{
    const cJSON *item = member(parent, key);
    if (!cJSON_IsNumber(item) || item->valueint != 1 || item->valuedouble < (double)minimum ||
        item->valuedouble > (double)max) {
        return false;
    }
    *out = (uint64_t)item->valuedouble;
    return true;
}

static bool page_number(const cJSON *parent, const char *key, uint64_t *out, bool nullable)
{
    if (nullable && cJSON_IsNull(member(parent, key))) {
        *out = 0;
        return true;
    }
    return integer(parent, key, 1, DEVICE_MAX_ID, out);
}

static bool string(const cJSON *parent, const char *key, char *out, size_t capacity, bool nullable)
{
    const cJSON *item = member(parent, key);
    if (nullable && cJSON_IsNull(item)) {
        *out = 0;
        return true;
    }
    if (!cJSON_IsString(item) || strlen(item->valuestring) >= capacity) { return false; }
    strcpy(out, item->valuestring);
    return true;
}

static bool book(const cJSON *item, device_book_t *out)
{
    const cJSON *pages = member(item, "pages");
    out->page_count = cJSON_IsArray(pages) ? (uint64_t)cJSON_GetArraySize(pages) : 0;
    out->cover_ref[0] = 0;
    return integer(item, "storybookId", 1, DEVICE_MAX_ID, &out->book_id) &&
           integer(item, "storyVersionId", 1, DEVICE_MAX_ID, &out->version_id) &&
           integer(item, "versionNumber", 1, DEVICE_MAX_ID, &out->version_number) &&
           (!member(item, "totalPages") || page_number(item, "totalPages", &out->page_count, false)) &&
           string(item, "title", out->title, sizeof(out->title), false) && out->title[0] &&
           (!member(item, "coverRef") || string(item, "coverRef", out->cover_ref, sizeof(out->cover_ref), true));
}

bool device_decode_envelope(const char *body, size_t size, bool success, device_result_t *result)
{
    cJSON *root = parse(body, size);
    char request_id[DEVICE_REQUEST_ID_MAX + 1];
    bool valid = string(root, "requestId", request_id, sizeof(request_id), false) && request_id[0];
    if (valid && success) {
        const cJSON *data = member(root, "data");
        valid = data && !cJSON_IsNull(data);
    } else if (valid) {
        const cJSON *error = member(root, "error");
        const cJSON *retryable = member(error, "retryable");
        valid = cJSON_IsBool(retryable) &&
                string(error, "code", result->error_code, sizeof(result->error_code), false) &&
                result->error_code[0] &&
                string(error, "message", result->message, sizeof(result->message), false);
        result->retryable = valid && cJSON_IsTrue(retryable);
        if (!valid) {
            result->error_code[0] = result->message[0] = 0;
        }
    }
    if (valid) { strcpy(result->request_id, request_id); }
    cJSON_Delete(root);
    return valid;
}

bool device_decode_shelf(const char *body, size_t size, device_shelf_t *out)
{
    out->count = 0;
    cJSON *root = parse(body, size);
    const cJSON *data = member(root, "data");
    const cJSON *items = member(data, "items");
    const size_t count = (size_t)cJSON_GetArraySize(items);
    bool valid = cJSON_IsArray(items) && count <= DEVICE_BOOKS_MAX &&
                 integer(data, "page", 1, DEVICE_MAX_ID, &out->page) &&
                 integer(data, "pageSize", 1, DEVICE_MAX_ID, &out->page_size) &&
                 count <= out->page_size;
    const cJSON *item;
    size_t index = 0;
    cJSON_ArrayForEach(item, items) {
        if (!valid || !book(item, &out->books[index++])) {
            valid = false;
            break;
        }
    }
    if (valid) { out->count = count; }
    cJSON_Delete(root);
    return valid;
}

bool device_decode_detail(const char *body, size_t size, device_detail_t *out)
{
    out->page_count = 0;
    cJSON *root = parse(body, size);
    const cJSON *data = member(root, "data");
    const cJSON *pages = member(data, "pages");
    const size_t count = (size_t)cJSON_GetArraySize(pages);
    out->summary[0] = 0;
    bool valid = cJSON_IsArray(pages) && count && count <= DEVICE_PAGES_MAX && book(data, &out->book) &&
                 count == out->book.page_count &&
                 (!member(data, "summary") || string(data, "summary", out->summary, sizeof(out->summary), true));
    const cJSON *item;
    size_t index = 0;
    cJSON_ArrayForEach(item, pages) {
        if (!valid) { break; }
        device_page_t *page = &out->pages[index++];
        valid = page_number(item, "pageNumber", &page->number, false) &&
                page_number(item, "defaultNextPageNumber", &page->default_next, true) &&
                string(item, "text", page->text, sizeof(page->text), false) &&
                string(item, "imageRef", page->image_ref, sizeof(page->image_ref), true);
    }
    if (valid) { out->page_count = count; }
    cJSON_Delete(root);
    return valid;
}

bool device_decode_session(const char *body, size_t size, device_session_t *out)
{
    cJSON *root = parse(body, size);
    const cJSON *data = member(root, "data");
    const cJSON *session = member(data, "session");
    if (!session) { session = data; }
    const bool valid = integer(session, "readingSessionId", 1, DEVICE_MAX_ID, &out->session_id) &&
                       integer(session, "storyVersionId", 1, DEVICE_MAX_ID, &out->version_id) &&
                       integer(session, "revision", 0, DEVICE_MAX_ID, &out->revision) &&
                       page_number(session, "currentPage", &out->current_page, false) &&
                       string(session, "status", out->status, sizeof(out->status), false) && out->status[0];
    cJSON_Delete(root);
    return valid;
}
