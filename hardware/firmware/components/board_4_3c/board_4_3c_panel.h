#pragma once

#include "board_4_3c.h"

esp_err_t board_4_3c_panel_init(void);
void board_4_3c_panel_release(void);
esp_err_t board_4_3c_panel_draw(const lv_area_t *area, const uint8_t *pixels);
esp_err_t board_4_3c_panel_poll_touch(board_4_3c_touch_t *point);
esp_err_t board_4_3c_panel_backlight(bool enabled);
