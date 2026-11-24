#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "esp_err.h"

#define BOARD_AUDIO_RATE 16000U
#define BOARD_AUDIO_MIN_SAMPLES 8000U
#define BOARD_AUDIO_MAX_SAMPLES 240000U
#define BOARD_AUDIO_MAX_VOLUME 40U
#define BOARD_AUDIO_WAV_HEADER_BYTES 44U

typedef enum {
    BOARD_AUDIO_OFFLINE,
    BOARD_AUDIO_INITIALIZING,
    BOARD_AUDIO_IDLE,
    BOARD_AUDIO_RECORDING,
    BOARD_AUDIO_RECORDED,
    BOARD_AUDIO_PLAYING,
    BOARD_AUDIO_ERROR,
} board_audio_state_t;

typedef struct {
    board_audio_state_t state;
    esp_err_t error;
    size_t samples;
    uint16_t peak;
    uint16_t rms;
    unsigned volume;
    bool muted;
    bool constant_input;
    bool clipped;
    bool pending;
} board_audio_status_t;

/* Serialize init/release with all clients. Release waits for the worker to exit. */
esp_err_t board_audio_init(void);
esp_err_t board_audio_release(void);
board_audio_status_t board_audio_status(void);
esp_err_t board_audio_retry(void);
esp_err_t board_audio_test_tone(void);
/* Keep input readable until the snapshot has neither pending nor playing. */
esp_err_t board_audio_play_pcm(const int16_t *samples, size_t count);
esp_err_t board_audio_play_wav(const void *buffer, size_t bytes);
esp_err_t board_audio_record(void);
esp_err_t board_audio_stop(void);
esp_err_t board_audio_cancel(void);
esp_err_t board_audio_replay(void);
esp_err_t board_audio_clear(void);
esp_err_t board_audio_set_volume(unsigned percent);
esp_err_t board_audio_set_mute(bool muted);
bool board_audio_wav_header(uint8_t header[BOARD_AUDIO_WAV_HEADER_BYTES], size_t samples);
