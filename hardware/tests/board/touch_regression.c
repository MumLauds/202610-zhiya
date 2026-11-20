#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "lvgl.h"

typedef int esp_err_t;
#define ESP_OK 0
#define ESP_ERR_INVALID_RESPONSE 0x108
#define ESP_ERR_NOT_FINISHED 0x10C
#define ESP_ERR_TIMEOUT 0x107
#define BOARD_4_3C_WIDTH 800
#define BOARD_4_3C_HEIGHT 480

/* PRODUCTION_CONSTANTS */

typedef struct {
    uint16_t x;
    uint16_t y;
    bool pressed;
} board_4_3c_touch_t;

static board_4_3c_touch_t touch;
static bool touch_wait_release;
static esp_err_t touch_last_error;
static uint8_t touch_track_id;
static esp_err_t flush_status;
static void *gt911;
static unsigned warnings;
static unsigned flush_errors;
static unsigned acknowledged;
static unsigned coordinate_reads;
static unsigned clicks;
static uint8_t status;
static uint8_t report[5];
static esp_err_t status_error;
static esp_err_t coordinate_error;
static esp_err_t acknowledge_error;
static esp_err_t draw_error;

#define ESP_LOGW(...) (++warnings)
#define ESP_LOGE(...) (++flush_errors)
static const char *esp_err_to_name(esp_err_t err) { (void)err; return "test"; }

static esp_err_t i2c_master_transmit_receive(void *device, const uint8_t *address,
                                            size_t address_length, uint8_t *data,
                                            size_t length, int timeout)
{
    (void)device;
    assert(address_length == 2 && timeout == I2C_TIMEOUT_MS);
    const unsigned reg = ((unsigned)address[0] << 8) | address[1];
    if (reg == GT911_STATUS_REG) {
        assert(length == 1);
        *data = status;
        return status_error;
    }
    assert(reg == GT911_FIRST_POINT_REG && length == sizeof(report));
    ++coordinate_reads;
    memcpy(data, report, length);
    return coordinate_error;
}

static esp_err_t i2c_master_transmit(void *device, const uint8_t *data,
                                    size_t length, int timeout)
{
    (void)device;
    assert(timeout == I2C_TIMEOUT_MS && length == 3);
    assert(data[0] == 0x81 && data[1] == 0x4E && data[2] == 0);
    ++acknowledged;
    return acknowledge_error;
}

static esp_err_t board_4_3c_panel_draw(const lv_area_t *area, const uint8_t *pixels)
{
    assert(area && pixels);
    return draw_error;
}

/* PRODUCTION_FUNCTIONS */

static lv_indev_t *input;
static lv_obj_t *button;

static void clicked(lv_event_t *event)
{
    (void)event;
    ++clicks;
}

static void sample(uint8_t value, uint16_t x, uint16_t y, uint8_t id)
{
    status = value;
    report[0] = id;
    report[1] = x & 255;
    report[2] = x >> 8;
    report[3] = y & 255;
    report[4] = y >> 8;
    lv_tick_inc(20);
    lv_indev_read(input);
}

static void fresh(void)
{
    lv_indev_reset(input, NULL);
    touch = (board_4_3c_touch_t){0};
    touch_wait_release = false;
    touch_last_error = ESP_OK;
    touch_track_id = 0;
    status_error = coordinate_error = acknowledge_error = ESP_OK;
    acknowledged = coordinate_reads = clicks = warnings = 0;
    sample(0x80, 0, 0, 0);
    assert(!lv_obj_has_state(button, LV_STATE_PRESSED));
    acknowledged = 0;
}

static void press(void)
{
    sample(0x81, 350, 250, 0);
    assert(touch.pressed);
    assert(lv_obj_has_state(button, LV_STATE_PRESSED));
}

static void canceled(void)
{
    assert(!touch.pressed);
    assert(!lv_obj_has_state(button, LV_STATE_PRESSED) && clicks == 0);
    status_error = coordinate_error = acknowledge_error = ESP_OK;
    sample(0x81, 350, 250, 0); /* Same contact after transport/multitouch fault. */
    sample(0x00, 0, 0, 0); /* No-data is not evidence of release. */
    sample(0x80, 0, 0, 0);
    assert(clicks == 0); /* The canceled contact must never become a new click. */
    assert(!touch.pressed && !touch_wait_release);
    press();
    sample(0x80, 0, 0, 0);
    assert(clicks == 1);
}

int main(void)
{
    lv_init();
    lv_display_t *display = lv_display_create(800, 480);
    assert(display);
    lv_obj_t *screen = lv_display_get_screen_active(display);
    lv_obj_remove_style_all(screen);
    button = lv_button_create(screen);
    lv_obj_set_pos(button, 300, 200);
    lv_obj_set_size(button, 200, 100);
    lv_obj_update_layout(screen);
    lv_obj_add_event_cb(button, clicked, LV_EVENT_CLICKED, NULL);
    input = lv_indev_create();
    assert(input);
    lv_indev_set_type(input, LV_INDEV_TYPE_POINTER);
    lv_indev_set_display(input, display);
    lv_indev_set_read_cb(input, read_touch);

    fresh();
    sample(0x01, 900, 500, 0); /* Stale low nibble ignored when buffer not ready. */
    assert(!touch.pressed && acknowledged == 0 && coordinate_reads == 0);
    puts("PASS idle/no-new-data does not read or acknowledge coordinates");

    fresh();
    press();
    for (unsigned i = 0; i < 200; ++i) {
        sample(0x00, 0, 0, 0);
        assert(touch.pressed && clicks == 0);
    }
    assert(acknowledged == 1 && coordinate_reads == 1);
    sample(0x81, 360, 250, 0);
    assert(touch.x == 360 && touch.pressed);
    sample(0x80, 0, 0, 0);
    assert(!touch.pressed && clicks == 1);
    sample(0x80, 0, 0, 0);
    assert(clicks == 1);
    puts("PASS press/hold/move/no-new-data/release produces one click");

    fresh(); press(); status_error = ESP_ERR_TIMEOUT;
    sample(0x00, 0, 0, 0);
    assert(acknowledged == 1);
    canceled();
    puts("PASS status-read failure cancels until acknowledged release");

    fresh(); press(); coordinate_error = ESP_ERR_TIMEOUT;
    sample(0x81, 350, 250, 0);
    assert(acknowledged == 2);
    canceled();
    puts("PASS coordinate-read failure cancels until acknowledged release");

    fresh(); press(); acknowledge_error = ESP_ERR_TIMEOUT;
    sample(0x81, 350, 250, 0);
    canceled();
    puts("PASS coordinate ACK failure cancels until acknowledged release");

    fresh(); press(); acknowledge_error = ESP_ERR_TIMEOUT;
    sample(0x80, 0, 0, 0);
    canceled();
    puts("PASS release ACK failure does not synthesize a click");

    const uint8_t bad_statuses[] = {0x82, 0x85, 0x86, 0xC1, 0x91, 0xC0};
    for (unsigned i = 0; i < sizeof(bad_statuses); ++i) {
        fresh(); press(); sample(bad_statuses[i], 350, 250, 0); canceled();
    }
    puts("PASS multi-point/invalid count/palm/key reports cancel");

    fresh(); press(); sample(0x81, 800, 250, 0); canceled();
    fresh(); press(); sample(0x81, 350, 480, 0); canceled();
    puts("PASS out-of-bounds coordinates cancel");

    fresh(); press(); sample(0x81, 350, 250, 1); canceled();
    fresh(); press(); sample(0x81, 350, 250, 32); canceled();
    puts("PASS contact-ID discontinuity/proximity report cancel");

    fresh();
    sample(0x81, 799, 479, 2);
    assert(touch.pressed && touch.x == 799 && touch.y == 479);
    sample(0x80, 0, 0, 0);
    assert(!touch.pressed && clicks == 0);
    puts("PASS inclusive screen edge maps without overflow");

    uint16_t pixels[4] = {0};
    const lv_area_t area = {0, 0, 1, 1};
    flush_status = ESP_OK;
    flush_errors = 0;
    draw_error = ESP_ERR_TIMEOUT;
    flush_display(display, &area, (uint8_t *)pixels);
    flush_display(display, &area, (uint8_t *)pixels);
    assert(flush_status == ESP_ERR_TIMEOUT && flush_errors == 1);
    draw_error = ESP_OK;
    flush_display(display, &area, (uint8_t *)pixels);
    assert(flush_status == ESP_ERR_TIMEOUT);
    puts("PASS flush failure latches/logs once and returns the LVGL buffer");

    lv_indev_delete(input);
    lv_display_delete(display);
    lv_deinit();
    puts("PASS LVGL 9.2.0 event regression (mocked I2C, host execution)");
    return 0;
}
