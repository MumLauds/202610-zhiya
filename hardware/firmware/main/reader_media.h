#pragma once

#include "esp_err.h"
#include "reader_core.h"

#define READER_MEDIA_SLOTS 2

typedef struct {
    uint32_t generation;
    reader_asset_t assets[READER_MEDIA_SLOTS];
} reader_media_request_t;

typedef struct {
    uint32_t generation;
    uint16_t *pixels[READER_MEDIA_SLOTS];
} reader_media_result_t;

esp_err_t reader_media_start(void);
bool reader_media_request(const reader_media_request_t *request);
bool reader_media_receive(reader_media_result_t *result);
