#include <assert.h>
#include <stdio.h>

#include "board_audio.c"

static audio_command_t queue[COMMAND_DEPTH];
static size_t queue_count;
static bool no_memory;
static int64_t now;
static bool rx_active;
static bool tx_active;
static unsigned releases;
static unsigned tx_blocks;
static int16_t first_left;
static esp_err_t read_result;
static esp_err_t write_result;
static esp_err_t init_result;
static bool short_write;

int xQueueSend(void *handle, const void *data, int wait)
{
    (void)handle;
    (void)wait;
    if (queue_count == COMMAND_DEPTH) {
        return 0;
    }
    queue[queue_count++] = *(const audio_command_t *)data;
    return pdTRUE;
}

int xQueueReceive(void *handle, void *data, int wait)
{
    (void)handle;
    (void)wait;
    if (!queue_count) {
        return 0;
    }
    *(audio_command_t *)data = queue[0];
    memmove(queue, queue + 1, --queue_count * sizeof(queue[0]));
    return pdTRUE;
}

int64_t esp_timer_get_time(void) { return now; }

void *heap_caps_malloc(size_t bytes, unsigned caps)
{
    assert(caps == (MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    return no_memory ? NULL : malloc(bytes);
}

esp_err_t audio_hw_init(void) { return init_result; }

esp_err_t audio_hw_release(void)
{
    ++releases;
    return audio_hw_stop();
}

esp_err_t audio_hw_start_rx(void)
{
    assert(!tx_active && !rx_active);
    rx_active = true;
    return ESP_OK;
}

esp_err_t audio_hw_start_tx(void)
{
    assert(!rx_active && !tx_active);
    tx_active = true;
    tx_blocks = 0;
    return ESP_OK;
}

esp_err_t audio_hw_stop(void)
{
    tx_active = false;
    rx_active = false;
    return ESP_OK;
}

esp_err_t audio_hw_set_mute(bool muted)
{
    (void)muted;
    assert(tx_active);
    return ESP_OK;
}

esp_err_t audio_hw_read(int16_t *stereo, size_t *frames)
{
    assert(rx_active && !tx_active && *frames <= AUDIO_BLOCK_FRAMES);
    for (size_t index = 0; index < *frames; ++index) {
        stereo[index * 2] = (int16_t)(-6000 + (int)index);
        stereo[index * 2 + 1] = 30000;
    }
    return read_result;
}

esp_err_t audio_hw_write(const int16_t *stereo, size_t *frames)
{
    assert(tx_active && !rx_active && *frames == AUDIO_BLOCK_FRAMES);
    for (size_t index = 0; index < *frames; ++index) {
        assert(stereo[index * 2] == stereo[index * 2 + 1]);
    }
    if (tx_blocks++ == 1) {
        first_left = stereo[0];
    }
    if (short_write) {
        --*frames;
    }
    return write_result;
}

#define EXPANDER_OUT_REG 3
#define EXPANDER_DIR_REG 2
static bool expander_ready = true;
static void *expander_lock = (void *)1;
static uint8_t directions;
static uint8_t outputs;
static esp_err_t expander_error;
static uint8_t last_reg;
static uint8_t last_value;

static esp_err_t expander_write(uint8_t reg, uint8_t value)
{
    last_reg = reg;
    last_value = value;
    return expander_error;
}

#include "expander_functions.h"

static void test_expander(void)
{
    outputs = directions = 0x16;
    assert(set_output(8, false, true) == ESP_OK);
    assert(last_reg == EXPANDER_DIR_REG && last_value == 0x1e);
    assert(set_output(8, true, false) == ESP_OK);
    assert(last_reg == EXPANDER_OUT_REG && last_value == 0x1e);
    expander_error = ESP_ERR_TIMEOUT;
    assert(set_output(8, false, true) == ESP_ERR_TIMEOUT && outputs == 0x1e);
    expander_error = ESP_OK;
    assert(set_output(8, false, true) == ESP_OK && outputs == 0x16 && directions == 0x1e);
}

static void test_wav(void)
{
    uint8_t wav[58] = {0};
    const uint8_t *pcm = NULL;
    size_t samples = 0;
    assert(board_audio_wav_header(wav, 2));
    assert(audio_wav_parse(wav, 48, &pcm, &samples) && pcm == wav + 44 && samples == 2);
    assert(!audio_wav_parse(wav, 47, &pcm, &samples));
    wav[22] = 2;
    assert(!audio_wav_parse(wav, 48, &pcm, &samples));
    wav[22] = 1;
    memmove(wav + 46, wav + 36, 12);
    memcpy(wav + 36, "JUNK\1\0\0\0x\0", 10);
    wav[4] += 10;
    assert(audio_wav_parse(wav, 58, &pcm, &samples) && pcm == wav + 54);
    wav[40] = 255;
    wav[41] = 255;
    wav[42] = 255;
    wav[43] = 255;
    assert(!audio_wav_parse(wav, 58, &pcm, &samples));
    assert(!board_audio_wav_header(wav, 0));
    assert(!board_audio_wav_header(wav, SIZE_MAX));
    assert(!audio_recording_valid(7999) && audio_recording_valid(8000));
    assert(audio_recording_valid(240000) && !audio_recording_valid(240001));
}

static void next(audio_worker_t *worker)
{
    audio_command_t command;
    assert(xQueueReceive(commands, &command, 0));
    handle_command(worker, &command);
    publish(worker, reserves_state(command.kind));
}

static void test_worker(void)
{
    assert(board_audio_init() == ESP_OK);
    audio_worker_t worker = {.status = {.volume = 20}};
    int16_t stereo[AUDIO_BLOCK_FRAMES * 2];
    init_result = ESP_ERR_TIMEOUT;
    initialize(&worker);
    publish(&worker, false);
    assert(board_audio_status().state == BOARD_AUDIO_ERROR && releases == 2);
    assert(board_audio_retry() == ESP_OK && board_audio_retry() == ESP_ERR_INVALID_STATE);
    init_result = ESP_OK;
    next(&worker);
    assert(board_audio_stop() == ESP_ERR_INVALID_STATE);
    assert(board_audio_set_volume(41) == ESP_ERR_INVALID_ARG);

    no_memory = true;
    assert(board_audio_record() == ESP_OK);
    next(&worker);
    assert(worker.status.state == BOARD_AUDIO_ERROR && !rx_active);
    no_memory = false;
    assert(board_audio_retry() == ESP_OK);
    next(&worker);

    assert(board_audio_record() == ESP_OK && board_audio_status().pending);
    assert(board_audio_test_tone() == ESP_ERR_INVALID_STATE);
    next(&worker);
    assert(rx_active && !tx_active);
    record_block(&worker, stereo);
    assert(board_audio_stop() == ESP_OK);
    next(&worker);
    assert(worker.status.state == BOARD_AUDIO_IDLE && worker.status.error == ESP_ERR_INVALID_SIZE);
    assert(!worker.recorded && !rx_active);

    assert(board_audio_record() == ESP_OK);
    next(&worker);
    while (worker.recorded < BOARD_AUDIO_MIN_SAMPLES) {
        record_block(&worker, stereo);
    }
    now = worker.deadline;
    record_block(&worker, stereo);
    publish(&worker, false);
    assert(worker.status.state == BOARD_AUDIO_RECORDED && worker.recorded == 8000);
    assert(worker.recording[0] == -6000 && worker.status.peak == 6000);
    assert(board_audio_replay() == ESP_OK);
    next(&worker);
    while (worker.status.state == BOARD_AUDIO_PLAYING) {
        play_block(&worker, stereo);
    }
    assert(first_left == -1200 && !tx_active && tx_blocks == 50 + AUDIO_DMA_BLOCKS);
    assert(worker.status.state == BOARD_AUDIO_RECORDED && worker.recorded == 8000);
    publish(&worker, false);

    assert(board_audio_replay() == ESP_OK);
    next(&worker);
    short_write = true;
    play_block(&worker, stereo);
    assert(worker.status.state == BOARD_AUDIO_ERROR && worker.recorded == 8000 && !tx_active);
    short_write = false;
    publish(&worker, false);
    assert(board_audio_retry() == ESP_OK);
    next(&worker);
    assert(worker.status.state == BOARD_AUDIO_RECORDED);

    assert(board_audio_record() == ESP_OK);
    next(&worker);
    record_block(&worker, stereo);
    assert(board_audio_cancel() == ESP_OK);
    next(&worker);
    assert(!worker.recorded && !rx_active && worker.recording[0] == 0);
    assert(board_audio_record() == ESP_OK);
    next(&worker);
    while (worker.status.state == BOARD_AUDIO_RECORDING) {
        record_block(&worker, stereo);
    }
    assert(worker.recorded == BOARD_AUDIO_MAX_SAMPLES && !rx_active);
    publish(&worker, false);
    assert(board_audio_record() == ESP_OK);
    next(&worker);
    read_result = ESP_ERR_TIMEOUT;
    record_block(&worker, stereo);
    assert(worker.status.state == BOARD_AUDIO_ERROR && !worker.recorded && !rx_active);
    free(worker.recording);
}

int main(void)
{
    test_expander();
    puts("PASS masked expander updates, write failure and safe retry");
    test_wav();
    puts("PASS PCM/WAV, padding, truncation, size bounds");
    test_worker();
    puts("PASS half-duplex worker, allocation and I/O failures, sample/time limits, real replay payload");
    return 0;
}
