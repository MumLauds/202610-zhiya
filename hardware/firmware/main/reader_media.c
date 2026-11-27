#include "reader_media.h"

#include <inttypes.h>
#include <stdlib.h>
#include <string.h>

#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp32s3/rom/tjpgd.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"

extern const uint8_t media_begin[] asm("_binary_h06_media_bin_start");
extern const uint8_t media_end[] asm("_binary_h06_media_bin_end");
static const char *const TAG = "reader_media";
static QueueHandle_t requests;
static QueueHandle_t results;

typedef struct {
    const uint8_t *input;
    size_t remaining;
    uint16_t *pixels;
    unsigned width, height;
} decode_t;

static UINT jpeg_read(JDEC *decoder, BYTE *buffer, UINT size)
{
    decode_t *io = decoder->device;
    if (size > io->remaining) {
        size = io->remaining;
    }
    if (buffer) {
        memcpy(buffer, io->input, size);
    }
    io->input += size;
    io->remaining -= size;
    return size;
}

static UINT jpeg_output(JDEC *decoder, void *bitmap, JRECT *rect)
{
    decode_t *io = decoder->device;
    if (rect->left > rect->right || rect->top > rect->bottom ||
        rect->right >= io->width || rect->bottom >= io->height) {
        return 0;
    }
    const uint8_t *rgb = bitmap;
    for (unsigned row = rect->top; row <= rect->bottom; ++row) {
        uint16_t *pixel = &io->pixels[row * io->width + rect->left];
        for (unsigned col = rect->left; col <= rect->right; ++col) {
            *pixel++ = (uint16_t)(((rgb[0] & 0xf8) << 8) | ((rgb[1] & 0xfc) << 3) | (rgb[2] >> 3));
            rgb += 3;
        }
    }
    return 1;
}

static uint16_t *decode_asset(reader_asset_t asset, void *work, size_t work_size)
{
    const size_t bundle_size = (uintptr_t)media_end - (uintptr_t)media_begin;
    if (!asset.size || !asset.width || !asset.height || asset.width > 400 || asset.height > 300 ||
        asset.offset > bundle_size || asset.size > bundle_size - asset.offset || !work) {
        return NULL;
    }
    decode_t io = {.input = media_begin + asset.offset, .remaining = asset.size,
                   .width = asset.width, .height = asset.height};
    JDEC decoder = {0};
    const int64_t start = esp_timer_get_time();
    JRESULT status = jd_prepare(&decoder, jpeg_read, work, work_size, &io);
    if (status != JDR_OK || decoder.width != io.width || decoder.height != io.height) {
        ESP_LOGW(TAG, "JPEG header rejected offset=%" PRIu32 " status=%d dimensions=%ux%u",
                 asset.offset, status, decoder.width, decoder.height);
        return NULL;
    }
    io.pixels = heap_caps_malloc((size_t)io.width * io.height * sizeof(uint16_t),
                                 MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!io.pixels) {
        ESP_LOGW(TAG, "No PSRAM for %ux%u JPEG", io.width, io.height);
        return NULL;
    }
    status = jd_decomp(&decoder, jpeg_output, 0);
    if (status != JDR_OK) {
        ESP_LOGW(TAG, "JPEG decode rejected offset=%" PRIu32 " status=%d", asset.offset, status);
        free(io.pixels);
        return NULL;
    }
    ESP_LOGI(TAG, "JPEG %ux%u %u B decoded in %lld us", io.width, io.height,
             (unsigned)asset.size, (long long)(esp_timer_get_time() - start));
    return io.pixels;
}

static void media_task(void *argument)
{
    (void)argument;
    enum { WORK_BYTES = 8192 };
    void *work = malloc(WORK_BYTES);
    if (!work) {
        ESP_LOGW(TAG, "JPEG decoder work buffer unavailable");
    }
    for (;;) {
        reader_media_request_t request;
        xQueueReceive(requests, &request, portMAX_DELAY);
        reader_media_result_t result = {.generation = request.generation};
        for (unsigned slot = 0; slot < READER_MEDIA_SLOTS; ++slot) {
            result.pixels[slot] = decode_asset(request.assets[slot], work, WORK_BYTES);
        }
        xQueueSend(results, &result, portMAX_DELAY);
    }
}

esp_err_t reader_media_start(void)
{
    requests = xQueueCreate(1, sizeof(reader_media_request_t));
    results = xQueueCreate(1, sizeof(reader_media_result_t));
    if (requests && results && xTaskCreate(media_task, "zhiya_jpeg", 4096, NULL, 3, NULL) == pdPASS) {
        return ESP_OK;
    }
    if (requests) {
        vQueueDelete(requests);
    }
    if (results) {
        vQueueDelete(results);
    }
    requests = NULL;
    results = NULL;
    return ESP_ERR_NO_MEM;
}

bool reader_media_request(const reader_media_request_t *request)
{
    return requests && xQueueOverwrite(requests, request) == pdPASS;
}

bool reader_media_receive(reader_media_result_t *result)
{
    return results && xQueueReceive(results, result, 0) == pdTRUE;
}
