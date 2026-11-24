#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"
#include "freertos/FreeRTOS.h"
#include "lvgl.h"

#define BOARD_4_3C_WIDTH 800
#define BOARD_4_3C_HEIGHT 480

typedef struct {
    uint16_t x;
    uint16_t y;
    bool pressed;
} board_4_3c_touch_t;

typedef esp_err_t (*board_4_3c_create_ui_cb_t)(lv_display_t *display);

/* Call once from app_main. The callback runs before the LVGL task starts. */
esp_err_t board_4_3c_init(board_4_3c_create_ui_cb_t create_ui);

/* Hold this lock for all LVGL calls from tasks other than the LVGL task. */
bool board_4_3c_lock(TickType_t wait);
void board_4_3c_unlock(void);

/* Call with the LVGL lock held or from a callback running on the LVGL task. */
board_4_3c_touch_t board_4_3c_touch(void);

/* Serializes the expander update with touch polling. */
esp_err_t board_4_3c_set_backlight(bool enabled);
void board_4_3c_log_memory(void);
