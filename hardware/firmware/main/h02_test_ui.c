#include "h02_test_ui.h"

#include <inttypes.h>

#include "esp_log.h"

LV_FONT_DECLARE(h02_title_font);

static const char *const TAG = "h02_test";
static uint32_t clicks;

static void button_clicked(lv_event_t *event)
{
    lv_obj_t *counter = lv_event_get_user_data(event);
    lv_label_set_text_fmt(counter, "Clicks: %" PRIu32, ++clicks);
    ESP_LOGI(TAG, "Touch click %" PRIu32, clicks);
    board_4_3c_log_memory();
}

static void update_coordinates(lv_timer_t *timer)
{
    static board_4_3c_touch_t last;
    const board_4_3c_touch_t point = board_4_3c_touch();
    if (point.x == last.x && point.y == last.y && point.pressed == last.pressed) {
        return;
    }
    last = point;
    lv_obj_t *label = lv_timer_get_user_data(timer);
    lv_label_set_text_fmt(label, "X: %u   Y: %u   %s",
                          (unsigned)point.x, (unsigned)point.y,
                          point.pressed ? "DOWN" : "UP");
}

esp_err_t h02_test_ui_create(lv_display_t *display)
{
    lv_obj_t *screen = lv_display_get_screen_active(display);
    if (!screen) {
        return ESP_ERR_NO_MEM;
    }
    lv_obj_clear_flag(screen, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_style_bg_color(screen, lv_color_hex(0xF4F7F2), 0);
    lv_obj_set_style_border_color(screen, lv_color_hex(0x164E39), 0);
    lv_obj_set_style_border_width(screen, 2, 0);
    lv_obj_set_style_border_post(screen, true, 0);
    lv_obj_set_style_pad_all(screen, 0, 0);

    lv_obj_t *title = lv_label_create(screen);
    lv_obj_t *coordinates = lv_label_create(screen);
    lv_obj_t *button = lv_button_create(screen);
    lv_obj_t *counter = lv_label_create(screen);
    if (!title || !coordinates || !button || !counter) {
        return ESP_ERR_NO_MEM;
    }
    lv_obj_set_style_text_font(title, &h02_title_font, 0);
    lv_obj_set_style_text_color(title, lv_color_hex(0x164E39), 0);
    lv_label_set_text(title, "知芽");
    lv_obj_align(title, LV_ALIGN_TOP_MID, 0, 28);

    static const uint32_t colors[] = {0xFF0000, 0x00FF00, 0x0000FF, 0xFFFFFF, 0x000000};
    for (unsigned i = 0; i < sizeof(colors) / sizeof(colors[0]); ++i) {
        lv_obj_t *tile = lv_obj_create(screen);
        if (!tile) {
            return ESP_ERR_NO_MEM;
        }
        lv_obj_remove_style_all(tile);
        lv_obj_clear_flag(tile, LV_OBJ_FLAG_SCROLLABLE | LV_OBJ_FLAG_CLICKABLE);
        lv_obj_set_size(tile, 136, 130);
        lv_obj_set_pos(tile, 33 + i * 148, 112);
        lv_obj_set_style_bg_color(tile, lv_color_hex(colors[i]), 0);
        lv_obj_set_style_bg_opa(tile, LV_OPA_COVER, 0);
    }

    lv_label_set_text(coordinates, "X: 0   Y: 0   UP");
    lv_obj_align(coordinates, LV_ALIGN_TOP_MID, 0, 270);
    lv_obj_set_size(button, 180, 56);
    lv_obj_align(button, LV_ALIGN_TOP_MID, 0, 310);
    lv_obj_t *button_label = lv_label_create(button);
    if (!button_label) {
        return ESP_ERR_NO_MEM;
    }
    lv_label_set_text(button_label, "Touch test");
    lv_obj_center(button_label);
    clicks = 0;
    lv_label_set_text(counter, "Clicks: 0");
    lv_obj_align(counter, LV_ALIGN_TOP_MID, 0, 388);
    lv_obj_add_event_cb(button, button_clicked, LV_EVENT_CLICKED, counter);
    if (!lv_timer_create(update_coordinates, 50, coordinates)) {
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}
