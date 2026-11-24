#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

bool audio_wav_parse(const void *buffer, size_t bytes, const uint8_t **pcm, size_t *samples);
bool audio_recording_valid(size_t samples);
