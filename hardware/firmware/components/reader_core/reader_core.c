#include "reader_core.h"

const reader_book_t *reader_book(const reader_catalog_t *catalog, uint64_t id, uint64_t version)
{
    for (size_t index = 0; index < catalog->count; ++index) {
        const reader_book_t *book = &catalog->books[index];
        if (book->book_id == id && book->version_id == version) {
            return book;
        }
    }
    return NULL;
}

const reader_page_t *reader_page(const reader_book_t *book, uint32_t number)
{
    if (book && number) {
        for (size_t index = 0; index < book->page_count; ++index) {
            if (book->pages[index].number == number) {
                return &book->pages[index];
            }
        }
    }
    return NULL;
}

const reader_page_t *reader_current(const reader_session_t *session)
{
    return session->length ? reader_page(session->book, session->history[session->length - 1]) : NULL;
}

bool reader_start(reader_session_t *session, const reader_book_t *book, uint64_t local_id)
{
    const reader_page_t *first = reader_page(book, 1);
    if (!local_id || session->status == READER_IN_PROGRESS || !first ||
        book->page_count > READER_MAX_HISTORY) {
        return false;
    }
    *session = (reader_session_t){
        .book = book, .local_id = local_id, .status = READER_IN_PROGRESS, .history = {1}, .length = 1,
    };
    session->visited[first - book->pages] = true;
    return true;
}

bool reader_next(reader_session_t *session)
{
    const reader_page_t *page = reader_current(session);
    const reader_page_t *next = page ? reader_page(session->book, page->next) : NULL;
    if (session->status != READER_IN_PROGRESS || !page || page->number == page->next ||
        session->length >= READER_MAX_HISTORY || !next) {
        return false;
    }
    session->history[session->length++] = page->next;
    session->visited[next - session->book->pages] = true;
    return true;
}

bool reader_previous(reader_session_t *session)
{
    if (session->status != READER_IN_PROGRESS || session->length < 2) {
        return false;
    }
    --session->length;
    return true;
}

bool reader_finish(reader_session_t *session)
{
    if (session->status == READER_FINISHED) {
        return true;
    }
    if (session->status != READER_IN_PROGRESS) {
        return false;
    }
    session->status = READER_FINISHED;
    return true;
}

void reader_reset(reader_session_t *session)
{
    *session = (reader_session_t){0};
}

size_t reader_unique_pages(const reader_session_t *session)
{
    size_t unique = 0;
    for (size_t index = 0; session->book && index < session->book->page_count; ++index) {
        unique += session->visited[index];
    }
    return unique;
}
