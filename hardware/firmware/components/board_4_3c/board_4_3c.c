#include "board_4_3c.h"

#include <stdlib.h>

#include "board_4_3c_panel.h"
#include "esp_check.h"
#include "esp_heap_caps.h"
#include "esp_psram.h"
#include "esp_timer.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

#define DRAW_BUFFER_LINES 20
#define DRAW_BUFFER_BYTES (BOARD_4_3C_WIDTH * DRAW_BUFFER_LINES * sizeof(uint16_t))
#define LVGL_TASK_STACK_BYTES 6144

static const char *const TAG = "board_4_3c";
static SemaphoreHandle_t ui_mutex;
static board_4_3c_touch_t touch;
static bool touch_wait_release;
static esp_err_t touch_last_error;
static esp_err_t flush_status;

static uint32_t lvgl_tick_ms(void)
{
    return (uint32_t)(esp_timer_get_time() / 1000);
}

static void flush_display(lv_display_t *display, const lv_area_t *area, uint8_t *pixels)
{
    const esp_err_t err = board_4_3c_panel_draw(area, pixels);
    if (err != ESP_OK) {
        if (flush_status == ESP_OK) {
            ESP_LOGE(TAG, "RGB flush failed: %s", esp_err_to_name(err));
        }
        flush_status = err;
    }
    lv_display_flush_ready(display);
}

static void read_touch(lv_indev_t *indev, lv_indev_data_t *data)
{
    board_4_3c_touch_t next = touch;
    const esp_err_t err = board_4_3c_panel_poll_touch(&next);
    if (err == ESP_ERR_NOT_FINISHED) {
        next = touch;
    } else if (err != ESP_OK) {
        if (err != touch_last_error) {
            ESP_LOGW(TAG, "GT911 read failed: %s", esp_err_to_name(err));
        }
        touch_last_error = err;
        if (touch.pressed) {
            /* Cancel instead of turning a failed sample into a button click. */
            lv_indev_reset(indev, NULL);
        }
        touch_wait_release = true;
        next = touch;
        next.pressed = false;
    } else if (touch_wait_release) {
        /* Require a real zero-point report before accepting another contact. */
        touch_wait_release = next.pressed;
        next = touch;
        next.pressed = false;
    } else {
        touch_last_error = ESP_OK;
    }
    touch = next;
    data->point.x = touch.x;
    data->point.y = touch.y;
    data->state = touch.pressed ? LV_INDEV_STATE_PRESSED : LV_INDEV_STATE_RELEASED;
}

bool board_4_3c_lock(TickType_t wait)
{
    return ui_mutex && xSemaphoreTakeRecursive(ui_mutex, wait) == pdTRUE;
}

void board_4_3c_unlock(void)
{
    xSemaphoreGiveRecursive(ui_mutex);
}

board_4_3c_touch_t board_4_3c_touch(void)
{
    return touch;
}

esp_err_t board_4_3c_set_backlight(bool enabled)
{
    if (!ui_mutex) {
        return ESP_ERR_INVALID_STATE;
    }
    if (!board_4_3c_lock(pdMS_TO_TICKS(100))) {
        return ESP_ERR_TIMEOUT;
    }
    const esp_err_t err = board_4_3c_panel_backlight(enabled);
    board_4_3c_unlock();
    return err;
}

static void log_heap(const char *name, uint32_t caps)
{
    ESP_LOGI(TAG, "%s heap bytes: free=%zu min=%zu largest=%zu", name,
             heap_caps_get_free_size(caps), heap_caps_get_minimum_free_size(caps),
             heap_caps_get_largest_free_block(caps));
}

void board_4_3c_log_memory(void)
{
    log_heap("PSRAM", MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    log_heap("internal", MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    log_heap("internal DMA", MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA);
}

static void lvgl_task(void *unused)
{
    (void)unused;
    for (;;) {
        board_4_3c_lock(portMAX_DELAY);
        const uint32_t next_ms = lv_timer_handler();
        board_4_3c_unlock();
        const uint32_t sleep_ms = next_ms < 10 ? 10 : (next_ms > 20 ? 20 : next_ms);
        const TickType_t ticks = pdMS_TO_TICKS(sleep_ms);
        vTaskDelay(ticks ? ticks : 1);
    }
}

esp_err_t board_4_3c_init(board_4_3c_create_ui_cb_t create_ui)
{
    ESP_RETURN_ON_FALSE(create_ui, ESP_ERR_INVALID_ARG, TAG, "UI callback missing");
    ESP_RETURN_ON_FALSE(!ui_mutex, ESP_ERR_INVALID_STATE, TAG, "Board already initialized");
    ESP_RETURN_ON_FALSE(esp_psram_is_initialized(), ESP_ERR_INVALID_STATE, TAG, "PSRAM unavailable");

    ESP_LOGI(TAG, "Before H02 allocation");
    board_4_3c_log_memory();

    void *draw_buffer = NULL;
    lv_display_t *display = NULL;
    lv_indev_t *indev = NULL;
    bool lvgl_initialized = false;
    esp_err_t ret;
    ui_mutex = xSemaphoreCreateRecursiveMutex();
    ESP_GOTO_ON_FALSE(ui_mutex, ESP_ERR_NO_MEM, fail, TAG, "LVGL recursive mutex");
    ESP_GOTO_ON_ERROR(board_4_3c_panel_init(), fail, TAG, "RGB/GT911 hardware");

    draw_buffer = heap_caps_aligned_alloc(64, DRAW_BUFFER_BYTES, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    ESP_GOTO_ON_FALSE(draw_buffer, ESP_ERR_NO_MEM, fail, TAG, "LVGL partial buffer (%u bytes)",
                      (unsigned)DRAW_BUFFER_BYTES);

    lv_init();
    lvgl_initialized = true;
    lv_tick_set_cb(lvgl_tick_ms);
    /* The LCD copy API expects tightly packed rows even for odd-width regions. */
    ESP_GOTO_ON_FALSE(lv_draw_buf_width_to_stride(1, LV_COLOR_FORMAT_RGB565) == sizeof(uint16_t),
                      ESP_ERR_NOT_SUPPORTED, fail, TAG, "LVGL RGB565 row alignment");
    display = lv_display_create(BOARD_4_3C_WIDTH, BOARD_4_3C_HEIGHT);
    ESP_GOTO_ON_FALSE(display, ESP_ERR_NO_MEM, fail, TAG, "LVGL display");
    lv_display_set_color_format(display, LV_COLOR_FORMAT_RGB565);
    lv_display_set_buffers(display, draw_buffer, NULL, DRAW_BUFFER_BYTES, LV_DISPLAY_RENDER_MODE_PARTIAL);
    lv_display_set_flush_cb(display, flush_display);

    indev = lv_indev_create();
    ESP_GOTO_ON_FALSE(indev, ESP_ERR_NO_MEM, fail, TAG, "LVGL pointer");
    lv_indev_set_type(indev, LV_INDEV_TYPE_POINTER);
    lv_indev_set_display(indev, display);
    lv_indev_set_read_cb(indev, read_touch);

    touch = (board_4_3c_touch_t){0};
    touch_wait_release = false;
    touch_last_error = ESP_OK;
    flush_status = ESP_OK;
    ESP_GOTO_ON_ERROR(create_ui(display), fail, TAG, "Initial UI");
    lv_refr_now(display);
    ESP_GOTO_ON_ERROR(flush_status, fail, TAG, "Initial RGB refresh");
    ESP_GOTO_ON_ERROR(board_4_3c_panel_backlight(true), fail, TAG, "LCD backlight");
    ESP_GOTO_ON_FALSE(xTaskCreate(lvgl_task, "zhiya_lvgl", LVGL_TASK_STACK_BYTES, NULL, 4, NULL) == pdPASS,
                      ESP_ERR_NO_MEM, fail, TAG, "LVGL task");

    ESP_LOGI(TAG, "H02 initialized: 800x480 RGB565, backlight on, LVGL %d.%d.%d",
             LVGL_VERSION_MAJOR, LVGL_VERSION_MINOR, LVGL_VERSION_PATCH);
    board_4_3c_log_memory();
    return ESP_OK;

fail:
    if (indev) {
        lv_indev_delete(indev);
    }
    if (display) {
        lv_display_delete(display);
    }
    if (lvgl_initialized) {
        lv_deinit();
    }
    free(draw_buffer);
    board_4_3c_panel_release();
    if (ui_mutex) {
        vSemaphoreDelete(ui_mutex);
        ui_mutex = NULL;
    }
    board_4_3c_log_memory();
    return ret;
}
