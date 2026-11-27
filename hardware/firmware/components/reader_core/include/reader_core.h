#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define READER_MAX_HISTORY 128

typedef struct {
    uint32_t offset, size;
    uint16_t width, height;
} reader_asset_t;

typedef struct {
    uint32_t number, next;
    const char *text;
    reader_asset_t image;
} reader_page_t;

typedef struct {
    uint64_t book_id, version_id;
    uint32_t version_number;
    const char *title, *summary;
    uint8_t age_min, age_max;
    reader_asset_t thumb, cover;
    const reader_page_t *pages;
    size_t page_count;
} reader_book_t;

typedef enum {
    READER_CREATED, READER_IN_PROGRESS, READER_FINISHED, READER_INTERRUPTED,
} reader_status_t;

typedef struct {
    const reader_book_t *book;
    reader_status_t status;
    uint32_t history[READER_MAX_HISTORY];
    bool visited[READER_MAX_HISTORY];
    size_t length;
    uint64_t local_id;
} reader_session_t;

typedef struct {
    const reader_book_t *books;
    size_t count;
    uint64_t featured_version;
} reader_catalog_t;

const reader_book_t *reader_book(const reader_catalog_t *catalog, uint64_t id, uint64_t version);
const reader_page_t *reader_page(const reader_book_t *book, uint32_t number);
const reader_page_t *reader_current(const reader_session_t *session);
bool reader_start(reader_session_t *session, const reader_book_t *book, uint64_t local_id);
bool reader_next(reader_session_t *session);
bool reader_previous(reader_session_t *session);
bool reader_finish(reader_session_t *session);
void reader_reset(reader_session_t *session);
size_t reader_unique_pages(const reader_session_t *session);
