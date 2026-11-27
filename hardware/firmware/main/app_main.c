#include <inttypes.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "board_4_3c.h"
#include "esp_chip_info.h"
#include "esp_err.h"
#include "esp_flash.h"
#include "esp_log.h"
#include "esp_psram.h"
#include "esp_system.h"
#ifdef ZHIYA_H06
#include "reader_media.h"
#include "reader_ui.h"
#else
#include "h02_test_ui.h"
#endif

#define EXPECTED_FLASH_BYTES (16U * 1024U * 1024U)
#define EXPECTED_PSRAM_BYTES (8U * 1024U * 1024U)

static const char *const TAG = "zhiya_boot";

static void log_reset_reason(void)
{
    const esp_reset_reason_t reason = esp_reset_reason();

    switch (reason) {
    case ESP_RST_POWERON:
    case ESP_RST_EXT:
    case ESP_RST_SW:
    case ESP_RST_USB:
    case ESP_RST_JTAG:
        ESP_LOGI(TAG, "Reset reason: %d (esp_reset_reason_t)", (int)reason);
        break;
    default:
        ESP_LOGW(TAG, "Unexpected reset reason: %d (esp_reset_reason_t)", (int)reason);
        break;
    }
}

static bool check_flash(void)
{
    uint32_t flash_bytes = 0;
    const esp_err_t result = esp_flash_get_physical_size(NULL, &flash_bytes);
    if (result != ESP_OK) {
        ESP_LOGE(TAG, "Flash size query failed: %s", esp_err_to_name(result));
        return false;
    }

    ESP_LOGI(TAG, "Flash: %" PRIu32 " bytes (physical)", flash_bytes);
    if (flash_bytes != EXPECTED_FLASH_BYTES) {
        ESP_LOGE(TAG, "Expected 16 MiB Flash for N16R8");
        return false;
    }

    return true;
}

static bool check_psram(void)
{
    if (!esp_psram_is_initialized()) {
        ESP_LOGE(TAG, "PSRAM initialization failed: unavailable (0 bytes)");
        return false;
    }

    const size_t psram_bytes = esp_psram_get_size();
    ESP_LOGI(TAG, "PSRAM initialized: %zu bytes", psram_bytes);
    if (psram_bytes != EXPECTED_PSRAM_BYTES) {
        ESP_LOGE(TAG, "Expected 8 MiB PSRAM for N16R8");
        return false;
    }

    return true;
}

void app_main(void)
{
    esp_chip_info_t chip;
    esp_chip_info(&chip);

    ESP_LOGI(TAG, "Zhiya hardware boot (H01)");
    ESP_LOGI(TAG, "ESP-IDF: %s", esp_get_idf_version());
    ESP_LOGI(TAG, "Chip: %s, %u cores, revision v%u.%u",
             chip.model == CHIP_ESP32S3 ? "ESP32-S3" : "unknown",
             (unsigned)chip.cores, (unsigned)(chip.revision / 100),
             (unsigned)(chip.revision % 100));
    log_reset_reason();

    const bool flash_ok = check_flash();
    const bool psram_ok = check_psram();
    if (flash_ok && psram_ok) {
        ESP_LOGI(TAG, "H01 startup checks passed");
    } else {
        ESP_LOGE(TAG, "H01 startup checks failed");
        return;
    }

    ESP_LOGI(TAG, "Starting H02 display and touch");
#ifdef ZHIYA_H06
    const esp_err_t err = board_4_3c_init(reader_ui_create);
    if (err == ESP_OK && board_4_3c_lock(portMAX_DELAY)) {
        const esp_err_t media = reader_media_start();
        board_4_3c_unlock();
        if (media != ESP_OK) {
            ESP_LOGW(TAG, "H06 media unavailable: %s", esp_err_to_name(media));
        }
    }
#else
    const esp_err_t err = board_4_3c_init(h02_test_ui_create);
#endif
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "H02 board initialization failed: %s. Check the bring-up guide.",
                 esp_err_to_name(err));
    }
}
