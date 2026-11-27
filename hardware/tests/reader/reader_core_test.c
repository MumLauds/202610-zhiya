#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "reader_core.h"

extern const reader_catalog_t reader_demo;

static void test_demo(void)
{
    assert(reader_demo.count >= 1 && reader_demo.count <= 3);
    assert(reader_demo.books[0].version_id == reader_demo.featured_version);
    assert(!strcmp(reader_demo.books[0].title, "小狐狸的雨伞"));
    assert(reader_demo.books[0].page_count == 6);
    for (size_t index = 0; index < reader_demo.count; ++index) {
        const reader_book_t *book = &reader_demo.books[index];
        assert(reader_book(&reader_demo, book->book_id, book->version_id) == book);
        assert(!reader_book(&reader_demo, book->book_id, UINT64_MAX));
        assert(book->cover.size && book->thumb.size);
        reader_session_t session = {0};
        for (uint64_t round = 1; round <= 20; ++round) {
            assert(reader_start(&session, book, round));
            assert(!reader_start(&session, book, round + 1));
            assert(!reader_previous(&session));
            do {
                const reader_page_t *page = reader_current(&session);
                assert(page && *page->text && page->image.size);
            } while (reader_next(&session));
            assert(!reader_current(&session)->next);
            assert(!reader_next(&session));
            assert(reader_previous(&session) && reader_next(&session));
            assert(reader_unique_pages(&session) == book->page_count);
            assert(reader_finish(&session) && reader_finish(&session));
            assert(!reader_next(&session) && !reader_previous(&session));
            reader_reset(&session);
            assert(!reader_current(&session) && !reader_finish(&session));
        }
    }
}

static void test_edges(void)
{
    static const reader_page_t pages[] = {
        {.number = 1, .next = 9, .text = "A"},
        {.number = 4, .text = "B"},
        {.number = 9, .next = 4, .text = "C"},
    };
    static const reader_book_t books[] = {
        {.book_id = UINT64_C(9007199254740991), .version_id = UINT64_C(9007199254740002),
         .pages = pages, .page_count = 3},
    };
    const reader_catalog_t catalog = {.books = books, .count = 1};
    assert(reader_book(&catalog, UINT64_C(9007199254740991), UINT64_C(9007199254740002)) == books);
    assert(!reader_book(&catalog, UINT64_C(9007199254740991), 4));
    reader_session_t session = {0};
    assert(!reader_start(&session, NULL, 1));
    assert(reader_start(&session, books, 1));
    assert(reader_next(&session) && reader_current(&session)->number == 9);
    assert(reader_next(&session) && reader_current(&session)->number == 4);
    assert(!reader_next(&session));
    assert(reader_previous(&session) && reader_current(&session)->number == 9);
    assert(reader_previous(&session) && reader_current(&session)->number == 1);
    assert(!reader_previous(&session) && reader_unique_pages(&session) == 3);
    assert(!reader_page(books, 2) && !reader_page(books, 0));
}

int main(void)
{
    test_demo();
    test_edges();
    puts("PASS H06 reader core: canonical book, six pages, version/64-bit IDs, sparse links, history, finish, reset, 20 rounds/book");
    return 0;
}
