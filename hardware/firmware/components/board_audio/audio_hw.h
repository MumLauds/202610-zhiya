#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "esp_err.h"

#define AUDIO_BLOCK_FRAMES 160U
#define AUDIO_DMA_BLOCKS 4U

esp_err_t audio_hw_init(void);
esp_err_t audio_hw_release(void);
esp_err_t audio_hw_start_rx(void);
esp_err_t audio_hw_start_tx(void);
esp_err_t audio_hw_stop(void);
esp_err_t audio_hw_set_mute(bool mute);
esp_err_t audio_hw_read(int16_t *stereo, size_t *frames);
esp_err_t audio_hw_write(const int16_t *stereo, size_t *frames);
