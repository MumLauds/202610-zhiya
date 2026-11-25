#include "audio_pcm.h"

#include <math.h>
#include <string.h>
#include "board_audio.h"

#define REPLAY_TARGET_RMS 2200U
#define REPLAY_MAX_GAIN (16U * AUDIO_GAIN_UNITY)
#define REPLAY_FRAME_SAMPLES (BOARD_AUDIO_RATE / 50U)
#define REPLAY_MIN_VOICE_FRAMES 3U

static const uint16_t frame_limits[] = {
    1, 2, 4, 8, 12, 16, 24, 32, 48, 64, 96, 128, 192, 256, 384,
    512, 768, 1024, 1536, 2048, 3072, 4096, 6144, 8192, 12288, 16384, 24576, 32768,
};

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
        signal->peak_index = signal->count - 1;
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

audio_replay_gain_t audio_replay_gain(const audio_signal_t *signal, const uint8_t *pcm, int dac_db)
{
    audio_replay_gain_t gain = {
        .gain_q8 = AUDIO_GAIN_UNITY, .peak_limit = dac_db == -6 ? 2057U : dac_db == -12 ? 4105U : 8192U,
    };
    if (!signal->count) {
        return gain;
    }
    gain.dc = (int32_t)(signal->sum / signal->count);

    enum { BIN_COUNT = sizeof(frame_limits) / sizeof(frame_limits[0]) };
    unsigned counts[BIN_COUNT] = {0};
    unsigned totals[BIN_COUNT] = {0};
    unsigned clean_frames = 0;
    for (size_t offset = 0; offset + REPLAY_FRAME_SAMPLES <= signal->count; offset += REPLAY_FRAME_SAMPLES) {
        audio_signal_t frame = {0};
        for (size_t index = 0; index < REPLAY_FRAME_SAMPLES; ++index) {
            audio_signal_add(&frame, audio_pcm16_read(pcm + (offset + index) * 2));
        }
        gain.total_frames++;
        const unsigned rms = audio_signal_ac_rms(&frame);
        const int32_t mean = (int32_t)(frame.sum / frame.count);
        const int32_t positive = frame.max - mean;
        const int32_t negative = mean - frame.min;
        const unsigned peak = positive > negative ? positive : negative;
        if (peak >= 1024 && peak > 12 * rms) {
            gain.transient_frames++;
            continue;
        }
        unsigned bin = 0;
        while (rms > frame_limits[bin] && bin + 1 < BIN_COUNT) {
            ++bin;
        }
        counts[bin]++;
        totals[bin] += rms;
        clean_frames++;
    }
    if (!clean_frames) {
        return gain;
    }
    unsigned seen = 0;
    for (unsigned bin = 0; bin < BIN_COUNT; ++bin) {
        seen += counts[bin];
        if (seen > clean_frames / 5) {
            gain.noise_rms = frame_limits[bin];
            break;
        }
    }
    const unsigned threshold = gain.noise_rms < 2 ? 4 : gain.noise_rms * 2;
    unsigned voice_total = 0;
    for (unsigned bin = 1; bin < BIN_COUNT; ++bin) {
        if (frame_limits[bin - 1] >= threshold) {
            gain.voice_frames += counts[bin];
            voice_total += totals[bin];
        }
    }
    if (gain.voice_frames >= REPLAY_MIN_VOICE_FRAMES) {
        gain.voice_rms = voice_total / gain.voice_frames;
        const unsigned target_gain = REPLAY_TARGET_RMS * AUDIO_GAIN_UNITY / gain.voice_rms;
        if (target_gain > AUDIO_GAIN_UNITY) {
            gain.gain_q8 = target_gain < REPLAY_MAX_GAIN ? target_gain : REPLAY_MAX_GAIN;
        }
    }
    return gain;
}

int16_t audio_replay_apply(int16_t sample, audio_replay_gain_t gain)
{
    const int32_t scaled = ((int32_t)sample - gain.dc) * (int32_t)gain.gain_q8 / (int32_t)AUDIO_GAIN_UNITY;
    const unsigned magnitude = scaled < 0 ? -scaled : scaled;
    const unsigned knee = gain.peak_limit / 2;
    if (magnitude > knee) {
        const unsigned excess = magnitude - knee;
        const unsigned span = gain.peak_limit - knee;
        const int32_t limited = knee + (uint32_t)((uint64_t)span * excess / (span + excess));
        return (int16_t)(scaled < 0 ? -limited : limited);
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
