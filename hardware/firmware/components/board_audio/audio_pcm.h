#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef struct {
    uint64_t squares;
    int64_t sum;
    uint32_t count;
    uint32_t zeros;
    uint32_t clipped;
    uint32_t fingerprint;
    uint32_t peak_index;
    int16_t min;
    int16_t max;
    uint16_t peak;
} audio_signal_t;

#define AUDIO_GAIN_UNITY 256U

typedef struct {
    int32_t dc;
    unsigned gain_q8;
    unsigned peak_limit;
    unsigned noise_rms;
    unsigned voice_rms;
    unsigned voice_frames;
    unsigned transient_frames;
    unsigned total_frames;
} audio_replay_gain_t;

void audio_signal_add(audio_signal_t *signal, int16_t sample);
uint16_t audio_signal_rms(const audio_signal_t *signal);
uint16_t audio_signal_ac_rms(const audio_signal_t *signal);
int16_t audio_pcm16_read(const uint8_t *bytes);
void audio_pcm16_write(uint8_t *bytes, int16_t sample);
audio_replay_gain_t audio_replay_gain(const audio_signal_t *signal, const uint8_t *pcm, int dac_db);
int16_t audio_replay_apply(int16_t sample, audio_replay_gain_t gain);
bool audio_wav_parse(const void *buffer, size_t bytes, const uint8_t **pcm, size_t *samples);
bool audio_recording_valid(size_t samples);
