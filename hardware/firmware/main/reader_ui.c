#include "reader_ui.h"

#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>

#include "esp_log.h"
#include "reader_core.h"
#include "reader_media.h"

LV_FONT_DECLARE(h06_reader_font);
extern const reader_catalog_t reader_demo;

enum { CREAM = 0xf8f3e8, PAPER = 0xfffdf7, FOREST = 0x214739,
       SAGE = 0xdde8d6, INK = 0x2b342c, PEACH = 0xeacb99, GOLD = 0xe4b94e };
typedef enum { WELCOME, HOME, SHELF, DETAIL, READING, CONFIRM, SUMMARY } scene_t;
typedef enum { NO_ACTION, ENTER, OPEN_SHELF, BACK, FIRST_BOOK, SECOND_BOOK, SHELF_PREV, SHELF_NEXT,
               START, READ_AGAIN, PREVIOUS, NEXT, TEXT_PREV, TEXT_NEXT, ASK_FINISH, KEEP_READING, FINISH } action_t;
enum {
    TURN_COOLDOWN_MS = 250, SWIPE_MAX_MS = 500, SWIPE_MIN_X = 60, SWIPE_MAX_Y = 40,
    TAP_MAX_MS = 600, TAP_MAX_MOVE = 8, TOUCH_SWIPE = 1 << 8,
    BODY_WIDTH = 240, BODY_HEIGHT = 288, BODY_LINE_HEIGHT = 36, BODY_SECTION_HEIGHT = 6 * BODY_LINE_HEIGHT,
};
typedef struct {
    lv_obj_t *frame;
    lv_obj_t *caption;
    lv_image_dsc_t image;
    uint16_t *pixels;
} picture_t;

static lv_obj_t *screen;
static scene_t scene;
static action_t pending;
static uint32_t generation;
static uint32_t render_tick;
static uint64_t session_id;
static size_t shelf_page;
static const reader_book_t *selected;
static reader_session_t session;
static reader_media_request_t request;
static picture_t pictures[READER_MEDIA_SLOTS];
static struct {
    lv_obj_t *target;
    lv_point_t start;
    uint32_t started_at;
    bool moved;
} touch;
static struct {
    lv_obj_t *label, *prev, *next;
    size_t section, count;
} body;
static uint32_t last_turn;
static bool turn_cooling;
static const char *const TAG = "reader_ui";

static lv_obj_t *label(lv_obj_t *parent, const char *text, int x, int y, int width, int height)
{
    lv_obj_t *object = lv_label_create(parent);
    if (object) {
        lv_obj_set_pos(object, x, y);
        lv_obj_set_size(object, width, height);
        lv_label_set_text(object, text);
    }
    return object;
}

static lv_obj_t *panel(int x, int y, int width, int height, unsigned bg)
{
    lv_obj_t *object = lv_obj_create(screen);
    if (object) {
        lv_obj_set_pos(object, x, y);
        lv_obj_set_size(object, width, height);
        lv_obj_remove_flag(object, LV_OBJ_FLAG_SCROLLABLE | LV_OBJ_FLAG_CLICKABLE);
        lv_obj_set_style_pad_all(object, 0, 0);
        lv_obj_set_style_border_width(object, 0, 0);
        lv_obj_set_style_radius(object, 18, 0);
        lv_obj_set_style_bg_color(object, lv_color_hex(bg), 0);
    }
    return object;
}

static bool is_page_turn(action_t action)
{
    return action == PREVIOUS || action == NEXT || action == SHELF_PREV || action == SHELF_NEXT;
}

static void queue_action(action_t action)
{
    if (action && !pending &&
        (!is_page_turn(action) || !turn_cooling || lv_tick_elaps(last_turn) >= TURN_COOLDOWN_MS)) {
        pending = action;
    }
}

static void touch_event(lv_event_t *event)
{
    lv_obj_t *target = lv_event_get_current_target_obj(event);
    const lv_event_code_t code = lv_event_get_code(event);
    if (code == LV_EVENT_PRESS_LOST || code == LV_EVENT_INDEV_RESET ||
        lv_obj_has_state(target, LV_STATE_DISABLED)) {
        if (touch.target == target) {
            touch.target = NULL;
        }
        return;
    }
    lv_indev_t *input = lv_indev_active();
    if (!input) {
        return;
    }
    lv_point_t point;
    lv_indev_get_point(input, &point);
    if (code == LV_EVENT_PRESSED) {
        touch.target = target;
        touch.start = point;
        touch.started_at = lv_tick_get();
        touch.moved = false;
    } else if (touch.target == target) {
        const int dx = point.x - touch.start.x;
        const int dy = point.y - touch.start.y;
        touch.moved |= abs(dx) > TAP_MAX_MOVE || abs(dy) > TAP_MAX_MOVE;
        if (code != LV_EVENT_RELEASED) {
            return;
        }
        touch.target = NULL;
        const uintptr_t binding = (uintptr_t)lv_event_get_user_data(event);
        const uint32_t elapsed = lv_tick_elaps(touch.started_at);
        if ((binding & TOUCH_SWIPE) && elapsed <= SWIPE_MAX_MS &&
            abs(dx) >= SWIPE_MIN_X && abs(dy) <= SWIPE_MAX_Y) {
            if (scene == READING) {
                queue_action(dx < 0 ? NEXT : PREVIOUS);
            } else if (scene == SHELF) {
                queue_action(dx < 0 ? SHELF_NEXT : SHELF_PREV);
            }
        } else if (!touch.moved && elapsed < TAP_MAX_MS) {
            queue_action((action_t)(binding & ~TOUCH_SWIPE));
        }
    }
}

static void bind_touch(lv_obj_t *object, action_t action, bool allow_swipe)
{
    if (!object) {
        return;
    }
    static const lv_event_code_t events[] = {LV_EVENT_PRESSED, LV_EVENT_PRESSING, LV_EVENT_RELEASED,
                                             LV_EVENT_PRESS_LOST, LV_EVENT_INDEV_RESET};
    const uintptr_t binding = (uintptr_t)action | (allow_swipe ? TOUCH_SWIPE : 0);
    lv_obj_add_flag(object, LV_OBJ_FLAG_CLICKABLE | LV_OBJ_FLAG_PRESS_LOCK);
    for (size_t i = 0; i < sizeof(events) / sizeof(events[0]); ++i) {
        lv_obj_add_event_cb(object, touch_event, events[i], (void *)binding);
    }
}

static lv_obj_t *button(const char *text, int x, int y, int width, action_t action, bool enabled, bool primary)
{
    lv_obj_t *object = lv_button_create(screen);
    if (!object) {
        return NULL;
    }
    lv_obj_set_pos(object, x, y);
    lv_obj_set_size(object, width, 60);
    lv_obj_set_style_radius(object, 16, 0);
    lv_obj_set_style_bg_color(object, lv_color_hex(primary ? FOREST : PAPER), 0);
    lv_obj_set_style_text_color(object, lv_color_hex(primary ? PAPER : FOREST), 0);
    lv_obj_set_style_bg_color(object, lv_color_hex(SAGE), LV_STATE_DISABLED);
    lv_obj_set_style_text_color(object, lv_color_hex(0x596758), LV_STATE_DISABLED);
    lv_obj_t *caption = lv_label_create(object);
    if (caption) {
        lv_label_set_text(caption, text);
        lv_obj_center(caption);
    }
    if (action) {
        bind_touch(object, action, false);
    }
    if (!enabled) {
        lv_obj_add_state(object, LV_STATE_DISABLED);
    }
    return object;
}

static void status_bar(const char *title)
{
    lv_obj_t *bar = panel(0, 0, 800, 44, PAPER);
    if (bar) {
        lv_obj_set_style_radius(bar, 0, 0);
        label(bar, "知芽", 24, 7, 100, 32);
        lv_obj_t *heading = label(bar, title, 130, 7, 550, 32);
        if (heading) {
            lv_label_set_long_mode(heading, LV_LABEL_LONG_DOT);
        }
    }
    lv_obj_t *badge = panel(698, 4, 78, 36, PEACH);
    if (badge) {
        lv_obj_t *caption = lv_label_create(badge);
        if (caption) {
            lv_obj_set_style_text_font(caption, LV_FONT_DEFAULT, 0);
            lv_label_set_text(caption, "DEMO");
            lv_obj_center(caption);
        }
    }
}

static void picture(unsigned slot, reader_asset_t asset, int x, int y, int width, int height)
{
    picture_t *target = &pictures[slot];
    target->frame = panel(x, y, width, height, SAGE);
    if (!target->frame) {
        return;
    }
    if (scene == READING) {
        bind_touch(target->frame, NO_ACTION, true);
    }
    target->caption = lv_label_create(target->frame);
    if (target->caption) {
        lv_label_set_text(target->caption, asset.size ? "图片加载中" : "图片暂不可用");
        lv_obj_center(target->caption);
    }
    if (asset.width <= width && asset.height <= height) {
        request.assets[slot] = asset;
    }
}

static void show_body_section(void)
{
    if (body.label && body.count > 1) {
        lv_obj_set_y(body.label, -(int32_t)body.section * BODY_SECTION_HEIGHT);
        if (body.prev) {
            lv_obj_set_state(body.prev, LV_STATE_DISABLED, body.section == 0);
        }
        if (body.next) {
            lv_obj_set_state(body.next, LV_STATE_DISABLED, body.section + 1 == body.count);
        }
    }
}

static void reading_body(const char *text)
{
    const int32_t line_space = BODY_LINE_HEIGHT - lv_font_get_line_height(&h06_reader_font);
    lv_point_t size;
    lv_text_get_size(&size, text, &h06_reader_font, 0, line_space, BODY_WIDTH, LV_TEXT_FLAG_NONE);
    const bool split = size.y > BODY_HEIGHT;
    lv_obj_t *viewport = panel(516, 76, BODY_WIDTH, split ? BODY_SECTION_HEIGHT : BODY_HEIGHT, PAPER);
    if (!viewport) {
        return;
    }
    lv_obj_set_style_radius(viewport, 0, 0);
    const int32_t y = !split && size.y < 3 * BODY_LINE_HEIGHT - line_space ?
                      (BODY_HEIGHT - size.y) / 2 : 0;
    body.label = label(viewport, text, 0, y, BODY_WIDTH, LV_SIZE_CONTENT);
    if (!body.label) {
        return;
    }
    lv_obj_set_style_text_font(body.label, &h06_reader_font, 0);
    lv_obj_set_style_text_letter_space(body.label, 0, 0);
    lv_obj_set_style_text_line_space(body.label, line_space, 0);
    lv_label_set_long_mode(body.label, LV_LABEL_LONG_WRAP);
    if (split) {
        body.count = ((size_t)size.y + BODY_SECTION_HEIGHT - 1) / BODY_SECTION_HEIGHT;
        body.prev = button("上一段", 516, 318, 114, TEXT_PREV, true, false);
        body.next = button("下一段", 642, 318, 114, TEXT_NEXT, true, false);
        show_body_section();
    }
}

static void render(void)
{
    render_tick = lv_tick_get();
    touch.target = NULL;
    lv_obj_clean(screen);
    body.label = body.prev = body.next = NULL;
    body.count = 0;
    for (unsigned slot = 0; slot < READER_MEDIA_SLOTS; ++slot) {
        if (pictures[slot].pixels) {
            lv_image_cache_drop(&pictures[slot].image);
            free(pictures[slot].pixels);
        }
        pictures[slot] = (picture_t){0};
    }
    if (++generation == 0) {
        ++generation;
    }
    request = (reader_media_request_t){.generation = generation};
    char line[96];
    switch (scene) {
    case WELCOME:
        status_bar("绘本共读");
        panel(164, 112, 472, 220, PAPER);
        panel(188, 137, 10, 112, GOLD);
        label(screen, "知芽绘本共读", 224, 154, 336, 40);
        label(screen, "离线演示", 224, 214, 330, 36);
        button("进入本地演示", 285, 355, 230, ENTER, true, true);
        break;
    case HOME:
        status_bar("首页");
        panel(24, 96, 752, 274, PAPER);
        label(screen, "今天想看哪一本故事？", 64, 142, 680, 45);
        button("进入书架", 64, 232, 200, OPEN_SHELF, true, true);
        button("绑定未开放", 296, 232, 192, BACK, false, false);
        button("设置未开放", 514, 232, 192, BACK, false, false);
        break;
    case SHELF: {
        status_bar("书架");
        bind_touch(panel(24, 59, 752, 334, CREAM), NO_ACTION, true);
        button("返回", 24, 410, 102, BACK, true, false);
        const size_t start = shelf_page * 2;
        if (!reader_demo.count) {
            label(screen, "本机暂无可读绘本", 256, 175, 320, 42);
        }
        for (unsigned slot = 0; slot < 2 && start + slot < reader_demo.count; ++slot) {
            const reader_book_t *book = &reader_demo.books[start + slot];
            const int x = 24 + slot * 384;
            const action_t open_book = slot ? SECOND_BOOK : FIRST_BOOK;
            bind_touch(panel(x, 59, 368, 334, PAPER), open_book, true);
            picture(slot, book->thumb, x + 24, 76, 320, 166);
            label(screen, book->title, x + 24, 254, 320, 36);
            snprintf(line, sizeof(line), "共 %u 页", (unsigned)book->page_count);
            label(screen, line, x + 24, 281, 320, 30);
            button("绘本详情", x + 24, 316, 320, open_book, true, true);
        }
        button("上一页", 386, 410, 116, SHELF_PREV, shelf_page > 0, false);
        snprintf(line, sizeof(line), "%u / %u", (unsigned)shelf_page + 1,
                 (unsigned)((reader_demo.count + 1) / 2));
        label(screen, line, 528, 423, 100, 34);
        button("下一页", 658, 410, 118, SHELF_NEXT, start + 2 < reader_demo.count, false);
        break;
    }
    case DETAIL:
        status_bar("绘本详情");
        picture(0, selected->cover, 24, 56, 456, 342);
        panel(496, 56, 280, 342, PAPER);
        label(screen, selected->title, 514, 82, 244, 34);
        snprintf(line, sizeof(line), "适合 %u–%u 岁", selected->age_min, selected->age_max);
        label(screen, line, 514, 127, 244, 32);
        if (*selected->summary) {
            lv_obj_t *summary = label(screen, selected->summary, 514, 180, 244, 135);
            if (summary) {
                lv_label_set_long_mode(summary, LV_LABEL_LONG_DOT);
            }
        }
        snprintf(line, sizeof(line), "共 %u 页", (unsigned)selected->page_count);
        label(screen, line, 514, 331, 244, 34);
        button("返回书架", 24, 410, 160, BACK, true, false);
        button("开始阅读", 564, 410, 212, START, true, true);
        break;
    case READING: {
        const reader_page_t *page = reader_current(&session);
        status_bar(selected->title);
        picture(0, page->image, 24, 56, 456, 342);
        panel(496, 56, 280, 342, PAPER);
        reading_body(page->text);
        button("上一页", 24, 410, 106, PREVIOUS, session.length > 1, false);
        button("下一页", 140, 410, 106, NEXT, page->next != 0 && session.length < READER_MAX_HISTORY, true);
        button("朗读", 256, 410, 84, BACK, false, false);
        button("提问", 350, 410, 84, BACK, false, false);
        snprintf(line, sizeof(line), "第 %" PRIu32 " 页 / %u", page->number, (unsigned)selected->page_count);
        label(screen, line, 454, 424, 173, 32);
        button("结束", 638, 410, 138, ASK_FINISH, true, false);
        ESP_LOGI(TAG, "Local session=%" PRIu64 " version=%" PRIu64 " page=%" PRIu32,
                 session.local_id, selected->version_id, page->number);
        break;
    }
    case CONFIRM:
        status_bar(selected->title);
        panel(128, 114, 544, 234, PAPER);
        label(screen, "要结束本次阅读吗？", 204, 157, 408, 45);
        button("继续阅读", 194, 242, 180, KEEP_READING, true, true);
        button("结束阅读", 426, 242, 180, FINISH, true, false);
        break;
    case SUMMARY:
        status_bar("演示记录");
        panel(124, 94, 552, 280, PAPER);
        label(screen, "阅读完成", 174, 132, 452, 42);
        label(screen, selected->title, 174, 195, 452, 38);
        snprintf(line, sizeof(line), "已读 %u 页 / 共 %u 页", (unsigned)reader_unique_pages(&session),
                 (unsigned)selected->page_count);
        label(screen, line, 174, 249, 452, 38);
        button("再读一遍", 152, 396, 228, READ_AGAIN, true, true);
        button("返回书架", 420, 396, 228, BACK, true, false);
        break;
    }
    if (request.assets[0].size || request.assets[1].size) {
        if (!reader_media_request(&request)) {
            for (unsigned slot = 0; slot < READER_MEDIA_SLOTS; ++slot) {
                if (pictures[slot].caption) {
                    lv_label_set_text(pictures[slot].caption, "图片暂不可用");
                }
            }
        }
    }
}

static void handle(action_t action)
{
    switch (action) {
    case ENTER: if (scene == WELCOME) { scene = HOME; break; } return;
    case OPEN_SHELF: if (scene == HOME) { scene = SHELF; break; } return;
    case BACK:
        if (scene == SHELF) {
            scene = HOME;
        } else if (scene == DETAIL || scene == SUMMARY) {
            reader_reset(&session);
            selected = NULL;
            scene = SHELF;
        } else {
            return;
        }
        break;
    case FIRST_BOOK:
    case SECOND_BOOK: {
        const size_t index = shelf_page * 2 + (action == SECOND_BOOK);
        if (scene != SHELF || index >= reader_demo.count) { return; }
        selected = &reader_demo.books[index];
        scene = DETAIL;
        break;
    }
    case SHELF_PREV: if (scene != SHELF || !shelf_page) { return; } --shelf_page; break;
    case SHELF_NEXT: if (scene != SHELF || (shelf_page + 1) * 2 >= reader_demo.count) { return; } ++shelf_page; break;
    case START:
    case READ_AGAIN:
        if ((action == START ? scene != DETAIL : scene != SUMMARY) ||
            !reader_start(&session, selected, session_id + 1)) { return; }
        ++session_id;
        body.section = 0;
        scene = READING;
        break;
    case PREVIOUS:
    case NEXT:
        if (scene != READING || !(action == PREVIOUS ? reader_previous(&session) : reader_next(&session))) {
            return;
        }
        body.section = 0;
        break;
    case TEXT_PREV:
    case TEXT_NEXT:
        if (scene == READING && body.count > 1) {
            if (action == TEXT_PREV && body.section > 0) {
                --body.section;
            } else if (action == TEXT_NEXT && body.section + 1 < body.count) {
                ++body.section;
            }
            show_body_section();
        }
        return;
    case ASK_FINISH: if (scene != READING) { return; } scene = CONFIRM; break;
    case KEEP_READING: if (scene != CONFIRM) { return; } scene = READING; break;
    case FINISH:
        if (scene != CONFIRM || !reader_finish(&session)) { return; }
        scene = SUMMARY;
        break;
    default: return;
    }
    if (is_page_turn(action)) {
        last_turn = lv_tick_get();
        turn_cooling = true;
    }
    render();
    if (scene == SUMMARY) {
        board_4_3c_log_memory();
    }
}

static void accept_media(reader_media_result_t *result)
{
    for (unsigned slot = 0; slot < READER_MEDIA_SLOTS; ++slot) {
        picture_t *target = &pictures[slot];
        const reader_asset_t asset = request.assets[slot];
        uint16_t *pixels = result->pixels[slot];
        if (result->generation != generation || !target->frame || !asset.size) {
            free(pixels);
            continue;
        }
        lv_obj_t *image = pixels ? lv_image_create(target->frame) : NULL;
        if (!image) {
            free(pixels);
            if (target->caption) {
                lv_label_set_text(target->caption, "图片暂不可用");
                lv_obj_center(target->caption);
            }
            continue;
        }
        target->pixels = pixels;
        target->image = (lv_image_dsc_t){
            .header = {.magic = LV_IMAGE_HEADER_MAGIC, .cf = LV_COLOR_FORMAT_RGB565,
                       .w = asset.width, .h = asset.height, .stride = asset.width * 2},
            .data_size = (uint32_t)asset.width * asset.height * 2, .data = (const uint8_t *)pixels,
        };
        lv_image_set_src(image, &target->image);
        lv_obj_center(image);
        if (target->caption) {
            lv_obj_add_flag(target->caption, LV_OBJ_FLAG_HIDDEN);
        }
        if (scene == READING) {
            ESP_LOGI(TAG, "Page %" PRIu32 " image ready in %" PRIu32 " ms",
                     reader_current(&session)->number, lv_tick_elaps(render_tick));
        }
    }
}

static void tick(lv_timer_t *timer)
{
    (void)timer;
    if (turn_cooling && lv_tick_elaps(last_turn) >= TURN_COOLDOWN_MS) {
        turn_cooling = false;
    }
    const action_t action = pending;
    pending = NO_ACTION;
    if (action) {
        handle(action);
    }
    reader_media_result_t result;
    if (reader_media_receive(&result)) {
        accept_media(&result);
    }
}

esp_err_t reader_ui_create(lv_display_t *display)
{
    screen = lv_display_get_screen_active(display);
    lv_obj_remove_flag(screen, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_style_bg_color(screen, lv_color_hex(CREAM), 0);
    lv_obj_set_style_text_color(screen, lv_color_hex(INK), 0);
    lv_obj_set_style_text_font(screen, &h06_reader_font, 0);
    render();
    return lv_timer_create(tick, 25, NULL) ? ESP_OK : ESP_ERR_NO_MEM;
}
