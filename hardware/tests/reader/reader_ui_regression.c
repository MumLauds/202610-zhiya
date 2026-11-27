#include <assert.h>
#include <string.h>

#include "reader_ui.c"

static lv_indev_t *input;
static lv_indev_data_t pointer;

void board_4_3c_log_memory(void) {}

bool reader_media_request(const reader_media_request_t *media)
{
    assert(media->generation == generation);
    return true;
}

bool reader_media_receive(reader_media_result_t *result)
{
    (void)result;
    return false;
}

static void read_pointer(lv_indev_t *indev, lv_indev_data_t *data)
{
    (void)indev;
    *data = pointer;
}

static void sample(lv_indev_state_t state, int x, int y, uint32_t delay)
{
    lv_tick_inc(delay);
    lv_obj_update_layout(screen);
    pointer = (lv_indev_data_t){.point = {x, y}, .state = state};
    lv_indev_read(input);
}

static void drag(int x, int y, int dx, int dy, uint32_t duration)
{
    sample(LV_INDEV_STATE_PRESSED, x, y, 0);
    sample(LV_INDEV_STATE_PRESSED, x + dx, y + dy, duration / 2);
    sample(LV_INDEV_STATE_RELEASED, x + dx, y + dy, duration - duration / 2);
    tick(NULL);
}

static void tap(int x, int y, uint32_t duration)
{
    drag(x, y, 0, 0, duration);
}

static void shelf_regression(void)
{
    tap(400, 385, 40);
    assert(scene == HOME);
    tap(150, 262, 40);
    assert(scene == SHELF && shelf_page == 0);

    drag(260, 150, 60, 0, 80);
    assert(scene == SHELF && shelf_page == 0 && !turn_cooling);
    drag(260, 150, -60, 0, 501);
    drag(260, 150, -59, 0, 100);
    drag(260, 150, -60, 41, 100);
    assert(scene == SHELF && shelf_page == 0);
    drag(260, 150, -60, 40, 500);
    assert(scene == SHELF && shelf_page == 1);
    tap(450, 440, 100);
    assert(shelf_page == 1);
    drag(180, 150, 90, 0, 150);
    assert(scene == SHELF && shelf_page == 0);

    /* A completed image overlays the interactive card without taking its taps. */
    const reader_asset_t asset = request.assets[0];
    reader_media_result_t result = {.generation = generation};
    result.pixels[0] = calloc((size_t)asset.width * asset.height, sizeof(uint16_t));
    assert(result.pixels[0]);
    accept_media(&result);
    tap(205, 160, 599);
    assert(scene == DETAIL && selected == &reader_demo.books[0]);
    tap(95, 440, 40);
    assert(scene == SHELF && shelf_page == 0);
    tap(205, 160, 600);
    assert(scene == SHELF);
    sample(LV_INDEV_STATE_PRESSED, 205, 160, 0);
    lv_indev_reset(input, NULL);
    sample(LV_INDEV_STATE_RELEASED, 205, 160, 50);
    tick(NULL);
    assert(scene == SHELF);

    tap(717, 440, 40);
    assert(scene == SHELF && shelf_page == 1);
    drag(210, 160, 80, 0, 100);
    assert(scene == SHELF && shelf_page == 1);
    tap(200, 160, 60);
    assert(scene == DETAIL && selected == &reader_demo.books[2]);
    tap(95, 440, 40);
    assert(scene == SHELF && shelf_page == 1);
    tap(450, 440, 60);
    assert(scene == SHELF && shelf_page == 0);
    tap(200, 160, 60);
    tap(670, 440, 60);
    assert(scene == READING && reader_current(&session)->number == 1);
    puts("PASS shelf cover tap, timed swipe, shared cooldown, bounds, and retained page");
}

static void reading_regression(void)
{
    lv_tick_inc(TURN_COOLDOWN_MS);
    tick(NULL);
    drag(250, 200, 65, 0, 70);
    assert(reader_current(&session)->number == 1 && !turn_cooling);
    tap(190, 440, 40);
    assert(reader_current(&session)->number == 2);
    drag(250, 200, -70, 0, 200);
    tap(190, 440, 49);
    assert(reader_current(&session)->number == 2);
    tap(190, 440, 1);
    assert(reader_current(&session)->number == 3);
    drag(250, 200, 60, -40, 250);
    assert(reader_current(&session)->number == 2);
    tap(78, 440, 40);
    assert(reader_current(&session)->number == 2);
    drag(250, 200, 60, 0, 501);
    drag(226, 440, -68, 0, 100);
    assert(reader_current(&session)->number == 2);
    tap(190, 440, 50);
    assert(reader_current(&session)->number == 3);
    puts("PASS reader mixed-input cooldown at 249/250 ms, long/invalid gestures, and failed turn");

    tap(700, 440, 40);
    assert(scene == CONFIRM);
    tap(507, 270, 40);
    assert(scene == SUMMARY && reader_unique_pages(&session) == 3);
    const uint64_t first_session = session.local_id;
    tap(245, 422, 40);
    assert(scene == READING && session.local_id != first_session);
    assert(reader_current(&session)->number == 1 && reader_unique_pages(&session) == 1);
    puts("PASS reread starts a fresh session on page 1");
}

static void check_body(const char *text)
{
    lv_obj_clean(screen);
    body.label = body.prev = body.next = NULL;
    body.count = body.section = 0;
    reading_body(text);
    lv_obj_update_layout(screen);
    assert(body.label);
    assert(lv_label_get_long_mode(body.label) == LV_LABEL_LONG_WRAP);
    assert(lv_obj_get_style_text_font(body.label, LV_PART_MAIN) == &h06_reader_font);
    assert(lv_obj_get_style_text_line_space(body.label, LV_PART_MAIN) +
           lv_font_get_line_height(&h06_reader_font) == 36);
    assert(lv_obj_get_width(body.label) == BODY_WIDTH);
    assert(strcmp(lv_label_get_text(body.label), text) == 0);
}

static void text_regression(void)
{
    for (size_t book = 0; book < reader_demo.count; ++book) {
        for (size_t page = 0; page < reader_demo.books[book].page_count; ++page) {
            check_body(reader_demo.books[book].pages[page].text);
            assert(body.count == 0 && lv_obj_get_height(body.label) <= BODY_HEIGHT);
        }
    }

#define TEN "一一一一一一一一一一"
    check_body(TEN);
    assert(lv_obj_get_y(body.label) > 0);
    check_body(TEN TEN TEN TEN TEN TEN TEN TEN);
    assert(body.count == 0 && lv_obj_get_height(body.label) == 30 + 7 * 36);

    const char *long_text = TEN TEN TEN TEN TEN TEN TEN TEN TEN TEN TEN TEN TEN "一页";
    check_body(long_text);
    lv_obj_t *viewport = lv_obj_get_parent(body.label);
    assert(body.count == 3 && lv_obj_get_height(viewport) == BODY_SECTION_HEIGHT);
    assert(!lv_obj_has_flag(viewport, LV_OBJ_FLAG_SCROLLABLE));
    assert(!lv_obj_has_flag(viewport, LV_OBJ_FLAG_OVERFLOW_VISIBLE));
    assert(lv_obj_has_state(body.prev, LV_STATE_DISABLED));
    tap(690, 345, 45);
    tap(690, 345, 45);
    lv_obj_update_layout(screen);
    assert(body.section == 2 && lv_obj_get_y(body.label) == -2 * BODY_SECTION_HEIGHT);
    assert(lv_obj_has_state(body.next, LV_STATE_DISABLED));
    assert(lv_obj_get_height(body.label) - 2 * BODY_SECTION_HEIGHT <= BODY_SECTION_HEIGHT);
    assert(strcmp(lv_label_get_text(body.label), long_text) == 0);
    tap(575, 345, 45);
    assert(body.section == 1);
    puts("PASS all real body text, full 80-character wrap, 36 px line height, and long-text continuation");
}

int main(void)
{
    lv_init();
    lv_display_t *display = lv_display_create(800, 480);
    assert(display && reader_ui_create(display) == ESP_OK);
    input = lv_indev_create();
    assert(input);
    lv_indev_set_type(input, LV_INDEV_TYPE_POINTER);
    lv_indev_set_display(input, display);
    lv_indev_set_read_cb(input, read_pointer);
    lv_tick_inc(1000);

    shelf_regression();
    reading_regression();
    text_regression();
    return 0;
}
