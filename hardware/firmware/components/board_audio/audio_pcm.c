#include "audio_pcm.h"

#include <math.h>
#include <string.h>
#include "board_audio.h"

#define REPLAY_TARGET_RMS 2200U
#define REPLAY_MIN_AC_RMS 128U
#define REPLAY_PEAK_LIMIT 8192
#define REPLAY_MAX_GAIN (4U * AUDIO_GAIN_UNITY)

void audio_signal_add(audio_signal_t *signal, int16_t sample)
{
    const int32_t value = sample;
    const unsigned magnitude = value < 0 ? -value : value;
    if (!signal->count) {
        signal->min = sample;
        signal->max = sample;
        signal->fingerprint = UINT32_C(2166136261);
    }
    signal->count++;
    signal->sum += value;
    signal->squares += (uint32_t)(value * value);
    signal->zeros += sample == 0;
    signal->clipped += magnitude >= 32760;
    signal->fingerprint = (signal->fingerprint ^ (uint16_t)sample) * UINT32_C(16777619);
    if (sample < signal->min) {
        signal->min = sample;
    }
    if (sample > signal->max) {
        signal->max = sample;
    }
    if (magnitude > signal->peak) {
        signal->peak = magnitude;
    }
}

uint16_t audio_signal_rms(const audio_signal_t *signal)
{
    return signal->count ? (uint16_t)sqrtf((float)signal->squares / signal->count) : 0;
}

uint16_t audio_signal_ac_rms(const audio_signal_t *signal)
{
    if (!signal->count || signal->min == signal->max) {
        return 0;
    }
    const double mean = (double)signal->sum / signal->count;
    const double variance = (double)signal->squares / signal->count - mean * mean;
    return variance > 0 ? (uint16_t)sqrt(variance) : 0;
}

audio_replay_gain_t audio_replay_gain(const audio_signal_t *signal)
{
    audio_replay_gain_t gain = {.gain_q8 = AUDIO_GAIN_UNITY};
    if (!signal->count) {
        return gain;
    }
    gain.dc = (int32_t)(signal->sum / signal->count);
    const unsigned ac_rms = audio_signal_ac_rms(signal);
    if (ac_rms >= REPLAY_MIN_AC_RMS && ac_rms < REPLAY_TARGET_RMS) {
        const unsigned target_gain = REPLAY_TARGET_RMS * AUDIO_GAIN_UNITY / ac_rms;
        gain.gain_q8 = target_gain < REPLAY_MAX_GAIN ? target_gain : REPLAY_MAX_GAIN;
    }
    const int32_t above_dc = signal->max - gain.dc;
    const int32_t below_dc = gain.dc - signal->min;
    const unsigned centered_peak = above_dc > below_dc ? above_dc : below_dc;
    if (centered_peak) {
        const unsigned peak_gain = REPLAY_PEAK_LIMIT * AUDIO_GAIN_UNITY / centered_peak;
        if (peak_gain < gain.gain_q8) {
            gain.gain_q8 = peak_gain;
        }
    }
    return gain;
}

int16_t audio_replay_apply(int16_t sample, audio_replay_gain_t gain)
{
    const int32_t scaled = ((int32_t)sample - gain.dc) * (int32_t)gain.gain_q8 / (int32_t)AUDIO_GAIN_UNITY;
    if (scaled > REPLAY_PEAK_LIMIT) {
        return REPLAY_PEAK_LIMIT;
    }
    if (scaled < -REPLAY_PEAK_LIMIT) {
        return -REPLAY_PEAK_LIMIT;
    }
    return (int16_t)scaled;
}

int16_t audio_pcm16_read(const uint8_t *bytes)
{
    const uint16_t word = (uint16_t)bytes[0] | (uint16_t)bytes[1] << 8;
    const int32_t signed_value = word <= INT16_MAX ? word : (int32_t)word - 65536;
    return (int16_t)signed_value;
}

void audio_pcm16_write(uint8_t *bytes, int16_t sample)
{
    const uint16_t word = (uint16_t)sample;
    bytes[0] = (uint8_t)word;
    bytes[1] = (uint8_t)(word >> 8);
}

static uint32_t read_le(const uint8_t *bytes, unsigned width)
{
    uint32_t value = 0;
    for (unsigned index = 0; index < width; ++index) {
        value |= (uint32_t)bytes[index] << (index * 8);
    }
    return value;
}

static void write_le(uint8_t *bytes, uint32_t value, unsigned width)
{
    for (unsigned index = 0; index < width; ++index) {
        bytes[index] = (uint8_t)(value >> (index * 8));
    }
}

bool audio_wav_parse(const void *buffer, size_t bytes, const uint8_t **pcm, size_t *samples)
{
    const uint8_t *input = buffer;
    if (!input || !pcm || !samples || bytes < 12 || memcmp(input, "RIFF", 4) ||
        memcmp(input + 8, "WAVE", 4) || read_le(input + 4, 4) != bytes - 8) {
        return false;
    }
    bool format_seen = false;
    const uint8_t *data = NULL;
    size_t count = 0;
    for (size_t offset = 12; offset < bytes;) {
        if (bytes - offset < 8) {
            return false;
        }
        const uint8_t *chunk = input + offset;
        const size_t size = read_le(chunk + 4, 4);
        offset += 8;
        if (size > bytes - offset || (size & 1U) > bytes - offset - size) {
            return false;
        }
        const uint8_t *payload = input + offset;
        if (!memcmp(chunk, "fmt ", 4)) {
            if (format_seen || size < 16 || read_le(payload, 2) != 1 ||
                read_le(payload + 2, 2) != 1 || read_le(payload + 4, 4) != BOARD_AUDIO_RATE ||
                read_le(payload + 8, 4) != BOARD_AUDIO_RATE * 2 ||
                read_le(payload + 12, 2) != 2 || read_le(payload + 14, 2) != 16) {
                return false;
            }
            format_seen = true;
        } else if (!memcmp(chunk, "data", 4)) {
            if (data || !size || size % 2 || size / 2 > BOARD_AUDIO_MAX_SAMPLES) {
                return false;
            }
            data = payload;
            count = size / 2;
        }
        offset += size + (size & 1U);
    }
    if (!format_seen || !data) {
        return false;
    }
    *pcm = data;
    *samples = count;
    return true;
}

bool board_audio_wav_header(uint8_t header[BOARD_AUDIO_WAV_HEADER_BYTES], size_t samples)
{
    if (!header || !samples || samples > BOARD_AUDIO_MAX_SAMPLES) {
        return false;
    }
    const uint32_t bytes = samples * 2;
    memcpy(header, "RIFF\0\0\0\0WAVEfmt \x10\0\0\0\1\0\1\0"
                   "\0\0\0\0\0\0\0\0\2\0\x10\0data\0\0\0\0", BOARD_AUDIO_WAV_HEADER_BYTES);
    write_le(header + 4, bytes + 36, 4);
    write_le(header + 24, BOARD_AUDIO_RATE, 4);
    write_le(header + 28, BOARD_AUDIO_RATE * 2, 4);
    write_le(header + 40, bytes, 4);
    return true;
}

bool audio_recording_valid(size_t samples)
{
    return samples >= BOARD_AUDIO_MIN_SAMPLES && samples <= BOARD_AUDIO_MAX_SAMPLES;
}
