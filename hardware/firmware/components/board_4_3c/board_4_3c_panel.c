#include "board_4_3c_panel.h"

#include <string.h>

#include "driver/gpio.h"
#include "driver/i2c_master.h"
#include "esp_check.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_panel_rgb.h"
#include "freertos/task.h"

/* LCD timing, pins and IO protocol from Waveshare's 4.3C examples.
 * Reference and schematic: hardware/docs/board-pin-map.md.
 */
#define I2C_CLOCK_HZ 400000
#define I2C_TIMEOUT_MS 20
#define TOUCH_INT_GPIO GPIO_NUM_4
#define EXPANDER_ADDR 0x24
#define EXPANDER_DIR_REG 0x02
#define EXPANDER_OUT_REG 0x03
#define TOUCH_RESET_BIT (1U << 1)
#define BACKLIGHT_BIT (1U << 2)

#define GT911_ADDR 0x5D
#define GT911_ALT_ADDR 0x14
#define GT911_PRODUCT_ID_REG 0x8140
#define GT911_STATUS_REG 0x814E
#define GT911_FIRST_POINT_REG 0x814F
#define GT911_DATA_READY 0x80

static const char *const TAG = "board_4_3c_panel";
static i2c_master_bus_handle_t bus;
static i2c_master_dev_handle_t expander;
static i2c_master_dev_handle_t gt911;
static esp_lcd_panel_handle_t panel;
static bool expander_ready;
static bool touch_gpio_configured;
static uint8_t outputs;

static esp_err_t add_i2c_device(uint8_t address, i2c_master_dev_handle_t *device)
{
    const i2c_device_config_t config = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = address,
        .scl_speed_hz = I2C_CLOCK_HZ,
    };
    return i2c_master_bus_add_device(bus, &config, device);
}

static esp_err_t expander_write(uint8_t reg, uint8_t value)
{
    const uint8_t data[] = {reg, value};
    return i2c_master_transmit(expander, data, sizeof(data), I2C_TIMEOUT_MS);
}

static esp_err_t set_outputs(uint8_t value)
{
    const esp_err_t err = expander_write(EXPANDER_OUT_REG, value);
    if (err == ESP_OK) {
        outputs = value;
    }
    return err;
}

static esp_err_t touch_read(uint16_t reg, uint8_t *data, size_t length)
{
    const uint8_t address[] = {(uint8_t)(reg >> 8), (uint8_t)reg};
    return i2c_master_transmit_receive(gt911, address, sizeof(address), data, length,
                                       I2C_TIMEOUT_MS);
}

static esp_err_t touch_acknowledge(void)
{
    const uint8_t data[] = {GT911_STATUS_REG >> 8, GT911_STATUS_REG & 0xFF, 0};
    return i2c_master_transmit(gt911, data, sizeof(data), I2C_TIMEOUT_MS);
}

static esp_err_t init_touch(void)
{
    /* EXIO1 is already low. Drive INT low during reset release to select 0x5D. */
    ESP_RETURN_ON_ERROR(gpio_set_level(TOUCH_INT_GPIO, 0), TAG, "GT911 INT level");
    ESP_RETURN_ON_ERROR(gpio_set_direction(TOUCH_INT_GPIO, GPIO_MODE_OUTPUT),
                        TAG, "GT911 INT output");
    touch_gpio_configured = true;
    vTaskDelay(pdMS_TO_TICKS(100));
    ESP_RETURN_ON_ERROR(set_outputs(TOUCH_RESET_BIT), TAG, "GT911 reset release");
    vTaskDelay(pdMS_TO_TICKS(200));
    ESP_RETURN_ON_ERROR(gpio_set_direction(TOUCH_INT_GPIO, GPIO_MODE_INPUT),
                        TAG, "GT911 INT input");

    uint8_t address = GT911_ADDR;
    if (i2c_master_probe(bus, address, I2C_TIMEOUT_MS) != ESP_OK) {
        address = GT911_ALT_ADDR;
        ESP_RETURN_ON_ERROR(i2c_master_probe(bus, address, I2C_TIMEOUT_MS),
                            TAG, "GT911 probe failed at 0x5D and 0x14");
    }
    ESP_RETURN_ON_ERROR(add_i2c_device(address, &gt911), TAG, "GT911 I2C device");

    uint8_t product[3];
    ESP_RETURN_ON_ERROR(touch_read(GT911_PRODUCT_ID_REG, product, sizeof(product)),
                        TAG, "GT911 product ID read");
    ESP_RETURN_ON_FALSE(memcmp(product, "911", sizeof(product)) == 0,
                        ESP_ERR_INVALID_RESPONSE, TAG, "Unexpected GT911 product ID");
    ESP_RETURN_ON_ERROR(touch_acknowledge(), TAG, "GT911 clear initial status");
    ESP_LOGI(TAG, "GT911 detected at 0x%02X; native landscape", (unsigned)address);
    return ESP_OK;
}

esp_err_t board_4_3c_panel_init(void)
{
    const i2c_master_bus_config_t i2c_config = {
        .i2c_port = I2C_NUM_0,
        .sda_io_num = GPIO_NUM_8,
        .scl_io_num = GPIO_NUM_9,
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = 7,
    };
    ESP_RETURN_ON_ERROR(i2c_new_master_bus(&i2c_config, &bus), TAG, "I2C0 init");
    ESP_RETURN_ON_ERROR(i2c_master_bus_reset(bus), TAG, "I2C0 bus recovery");
    ESP_RETURN_ON_ERROR(i2c_master_probe(bus, EXPANDER_ADDR, I2C_TIMEOUT_MS),
                        TAG, "4.3C I/O expander not responding at 0x24");
    ESP_RETURN_ON_ERROR(add_i2c_device(EXPANDER_ADDR, &expander), TAG, "I/O expander device");

    /* Load both low before enabling the two board outputs. Other bits stay inputs. */
    ESP_RETURN_ON_ERROR(set_outputs(0), TAG, "I/O expander output latch");
    ESP_RETURN_ON_ERROR(expander_write(EXPANDER_DIR_REG, TOUCH_RESET_BIT | BACKLIGHT_BIT),
                        TAG, "I/O expander output directions");
    expander_ready = true;
    ESP_RETURN_ON_ERROR(init_touch(), TAG, "GT911 init");

    const esp_lcd_rgb_panel_config_t lcd_config = {
        .clk_src = LCD_CLK_SRC_DEFAULT,
        .timings = {
            .pclk_hz = 16000000,
            .h_res = BOARD_4_3C_WIDTH,
            .v_res = BOARD_4_3C_HEIGHT,
            .hsync_pulse_width = 4,
            .hsync_back_porch = 8,
            .hsync_front_porch = 8,
            .vsync_pulse_width = 4,
            .vsync_back_porch = 8,
            .vsync_front_porch = 8,
            .flags.pclk_active_neg = true,
        },
        .data_width = 16,
        .bits_per_pixel = 16,
        .num_fbs = 1,
        .bounce_buffer_size_px = BOARD_4_3C_WIDTH * 10,
        .dma_burst_size = 64,
        .hsync_gpio_num = GPIO_NUM_46,
        .vsync_gpio_num = GPIO_NUM_3,
        .de_gpio_num = GPIO_NUM_5,
        .pclk_gpio_num = GPIO_NUM_7,
        .disp_gpio_num = GPIO_NUM_NC,
        .data_gpio_nums = {
            14, 38, 18, 17, 10,        /* B3-B7 */
            39, 0, 45, 48, 47, 21,    /* G2-G7 */
            1, 2, 42, 41, 40,          /* R3-R7 */
        },
        .flags.fb_in_psram = true,
    };
    ESP_RETURN_ON_ERROR(esp_lcd_new_rgb_panel(&lcd_config, &panel), TAG, "RGB565 panel allocation");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_init(panel), TAG, "RGB565 panel start");
    return ESP_OK;
}

esp_err_t board_4_3c_panel_backlight(bool enabled)
{
    if (!expander_ready) {
        return ESP_ERR_INVALID_STATE;
    }
    return set_outputs(enabled ? outputs | BACKLIGHT_BIT : outputs & ~BACKLIGHT_BIT);
}

esp_err_t board_4_3c_panel_draw(const lv_area_t *area, const uint8_t *pixels)
{
    /* ESP-IDF copies this partial draw buffer into its own RGB framebuffer. */
    return esp_lcd_panel_draw_bitmap(panel, area->x1, area->y1, area->x2 + 1, area->y2 + 1, pixels);
}

esp_err_t board_4_3c_panel_poll_touch(board_4_3c_touch_t *point)
{
    uint8_t status;
    const esp_err_t status_err = touch_read(GT911_STATUS_REG, &status, 1);
    if (status_err != ESP_OK) {
        return status_err;
    }
    if (!(status & GT911_DATA_READY)) {
        return ESP_OK;
    }

    const uint8_t count = status & 0x0F;
    uint8_t data[5]; /* Track ID, X low/high, Y low/high. */
    esp_err_t result = ESP_OK;
    if (count == 1) {
        result = touch_read(GT911_FIRST_POINT_REG, data, sizeof(data));
    }
    const esp_err_t ack = touch_acknowledge();
    if (result != ESP_OK || ack != ESP_OK) {
        return result != ESP_OK ? result : ack;
    }
    if (count != 1) {
        point->pressed = false;
        return count > 1 ? ESP_ERR_INVALID_RESPONSE : ESP_OK;
    }

    /* The reference has no XY swap or mirroring in the 800x480 orientation. */
    const uint16_t x = (uint16_t)data[1] | ((uint16_t)data[2] << 8);
    const uint16_t y = (uint16_t)data[3] | ((uint16_t)data[4] << 8);
    if (x >= BOARD_4_3C_WIDTH || y >= BOARD_4_3C_HEIGHT) {
        return ESP_ERR_INVALID_RESPONSE;
    }
    *point = (board_4_3c_touch_t){.x = x, .y = y, .pressed = true};
    return ESP_OK;
}

void board_4_3c_panel_release(void)
{
    if (expander_ready) {
        ESP_ERROR_CHECK_WITHOUT_ABORT(board_4_3c_panel_backlight(false));
        expander_ready = false;
    }
    if (panel) {
        ESP_ERROR_CHECK_WITHOUT_ABORT(esp_lcd_panel_del(panel));
        panel = NULL;
    }
    if (touch_gpio_configured) {
        ESP_ERROR_CHECK_WITHOUT_ABORT(gpio_reset_pin(TOUCH_INT_GPIO));
        touch_gpio_configured = false;
    }
    if (gt911) {
        ESP_ERROR_CHECK_WITHOUT_ABORT(i2c_master_bus_rm_device(gt911));
        gt911 = NULL;
    }
    if (expander) {
        ESP_ERROR_CHECK_WITHOUT_ABORT(i2c_master_bus_rm_device(expander));
        expander = NULL;
    }
    if (bus) {
        ESP_ERROR_CHECK_WITHOUT_ABORT(i2c_del_master_bus(bus));
        bus = NULL;
    }
}
