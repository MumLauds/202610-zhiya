#include "audio_pcm.h"

#include <string.h>
#include "board_audio.h"

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
