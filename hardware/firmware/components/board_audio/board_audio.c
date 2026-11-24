#include "board_audio.h"

#include <math.h>
#include <stdlib.h>
#include <string.h>
#include "audio_hw.h"
#include "audio_pcm.h"
#include "board_4_3c.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

#define COMMAND_DEPTH 8U
#define WORKER_STACK_BYTES 6144U
#define TONE_SAMPLES 8000U
#define FADE_SAMPLES 160U
#define RECORD_US 15000000

typedef enum {
    COMMAND_RETRY, COMMAND_TONE, COMMAND_PCM, COMMAND_RECORD, COMMAND_REPLAY,
    COMMAND_STOP, COMMAND_CANCEL, COMMAND_CLEAR, COMMAND_VOLUME, COMMAND_MUTE, COMMAND_EXIT,
} command_kind_t;

typedef struct {
    command_kind_t kind;
    const uint8_t *pcm;
    size_t count;
    unsigned value;
} audio_command_t;

typedef struct {
    board_audio_status_t status;
    int16_t *recording;
    size_t recorded;
    const uint8_t *playback;
    size_t play_length;
    size_t played;
    unsigned drain_blocks;
    int16_t input_min;
    int16_t input_max;
    int64_t deadline;
    bool ready;
} audio_worker_t;

static const char *const TAG = "board_audio";
static QueueHandle_t commands;
static SemaphoreHandle_t snapshot_lock;
static SemaphoreHandle_t worker_done;
static board_audio_status_t snapshot;
static bool command_pending;
static esp_err_t release_result;

static void publish(const audio_worker_t *worker, bool clear_pending)
{
    xSemaphoreTake(snapshot_lock, portMAX_DELAY);
    snapshot = worker->status;
    if (clear_pending) {
        command_pending = false;
    }
    xSemaphoreGive(snapshot_lock);
}

static void set_state(audio_worker_t *worker, board_audio_state_t state, esp_err_t err)
{
    worker->status.state = state;
    worker->status.error = err;
    worker->status.samples = worker->recorded;
    ESP_LOGI(TAG, "State=%d samples=%zu error=%s", state, worker->recorded, esp_err_to_name(err));
}

static void clear_recording(audio_worker_t *worker)
{
    if (worker->recorded) {
        memset(worker->recording, 0, worker->recorded * sizeof(int16_t));
    }
    worker->recorded = 0;
    worker->status.peak = 0;
    worker->status.rms = 0;
    worker->status.constant_input = false;
    worker->status.clipped = false;
}

static void initialize(audio_worker_t *worker)
{
    set_state(worker, BOARD_AUDIO_INITIALIZING, ESP_OK);
    publish(worker, false);
    board_4_3c_log_memory();
    const esp_err_t cleanup = audio_hw_release();
    const esp_err_t result = cleanup == ESP_OK ? audio_hw_init() : cleanup;
    worker->ready = result == ESP_OK;
    if (!worker->ready) {
        const esp_err_t cleanup_error = audio_hw_release();
        if (cleanup_error != ESP_OK) {
            ESP_LOGE(TAG, "Retry cleanup failed: %s", esp_err_to_name(cleanup_error));
        }
    }
    set_state(worker, worker->ready ? BOARD_AUDIO_IDLE : BOARD_AUDIO_ERROR, result);
    board_4_3c_log_memory();
}

static void finish(audio_worker_t *worker, esp_err_t result, bool cancel)
{
    const bool recording = worker->status.state == BOARD_AUDIO_RECORDING;
    const bool io_failed = result != ESP_OK;
    const esp_err_t stopped = audio_hw_stop();
    if (stopped != ESP_OK) {
        ESP_LOGE(TAG, "Audio stop failed: %s", esp_err_to_name(stopped));
        if (result == ESP_OK) {
            result = stopped;
        }
    }
    if (recording && (result != ESP_OK || cancel || !audio_recording_valid(worker->recorded))) {
        if (result == ESP_OK && !cancel) {
            result = ESP_ERR_INVALID_SIZE;
        }
        clear_recording(worker);
    }
    worker->playback = NULL;
    if (stopped != ESP_OK || io_failed) {
        worker->ready = false;
        const esp_err_t cleanup = audio_hw_release();
        if (cleanup != ESP_OK) {
            ESP_LOGE(TAG, "Audio cleanup failed: %s", esp_err_to_name(cleanup));
        }
    }
    set_state(worker, !worker->ready ? BOARD_AUDIO_ERROR :
                      worker->recorded ? BOARD_AUDIO_RECORDED : BOARD_AUDIO_IDLE, result);
    board_4_3c_log_memory();
}

static void start_recording(audio_worker_t *worker)
{
    if (!worker->recording) {
        worker->recording = heap_caps_malloc(BOARD_AUDIO_MAX_SAMPLES * sizeof(int16_t),
                                             MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    }
    clear_recording(worker);
    set_state(worker, BOARD_AUDIO_RECORDING, ESP_OK);
    if (!worker->recording) {
        finish(worker, ESP_ERR_NO_MEM, true);
        return;
    }
    worker->input_min = INT16_MAX;
    worker->input_max = INT16_MIN;
    esp_err_t err = audio_hw_start_rx();
    worker->deadline = esp_timer_get_time() + RECORD_US;
    if (err != ESP_OK) {
        finish(worker, err, true);
    } else {
        board_4_3c_log_memory();
    }
}

static void record_block(audio_worker_t *worker, int16_t *stereo)
{
    size_t frames = BOARD_AUDIO_MAX_SAMPLES - worker->recorded;
    if (frames > AUDIO_BLOCK_FRAMES) {
        frames = AUDIO_BLOCK_FRAMES;
    }
    if (esp_timer_get_time() >= worker->deadline || !frames) {
        finish(worker, ESP_OK, false);
        return;
    }
    const esp_err_t err = audio_hw_read(stereo, &frames);
    if (err != ESP_OK || !frames) {
        finish(worker, err == ESP_OK ? ESP_ERR_TIMEOUT : err, true);
        return;
    }
    uint64_t squares = 0;
    uint16_t peak = 0;
    for (size_t index = 0; index < frames; ++index) {
        const int sample = stereo[index * 2];
        const unsigned level = abs(sample);
        worker->recording[worker->recorded++] = (int16_t)sample;
        squares += (uint32_t)(sample * sample);
        if (level > peak) {
            peak = level;
        }
        if (sample < worker->input_min) {
            worker->input_min = sample;
        }
        if (sample > worker->input_max) {
            worker->input_max = sample;
        }
    }
    worker->status.samples = worker->recorded;
    worker->status.peak = peak;
    worker->status.rms = (uint16_t)sqrtf((float)squares / frames);
    worker->status.constant_input = worker->input_min == worker->input_max;
    worker->status.clipped |= peak >= 32760;
    if (worker->recorded == BOARD_AUDIO_MAX_SAMPLES) {
        finish(worker, ESP_OK, false);
    }
}

static void start_playback(audio_worker_t *worker, const audio_command_t *command)
{
    worker->played = 0;
    worker->drain_blocks = 0;
    worker->playback = command->pcm;
    worker->play_length = command->count;
    if (command->kind == COMMAND_TONE) {
        worker->play_length = TONE_SAMPLES;
    } else if (command->kind == COMMAND_REPLAY) {
        worker->playback = (const uint8_t *)worker->recording;
        worker->play_length = worker->recorded;
    }
    set_state(worker, BOARD_AUDIO_PLAYING, ESP_OK);
    esp_err_t result = audio_hw_start_tx();
    if (result == ESP_OK) {
        result = audio_hw_set_mute(worker->status.muted || !worker->status.volume);
    }
    if (result != ESP_OK) {
        finish(worker, result, false);
    }
}

static int16_t playback_sample(const audio_worker_t *worker, size_t index)
{
    int sample;
    if (worker->playback) {
        const uint8_t *bytes = worker->playback + index * 2;
        sample = (int16_t)((uint16_t)bytes[0] | (uint16_t)bytes[1] << 8);
    } else {
        static const int16_t tone[] = {0, 1254, 2317, 3027, 3277, 3027, 2317, 1254,
                                      0, -1254, -2317, -3027, -3277, -3027, -2317, -1254};
        sample = tone[index % (sizeof(tone) / sizeof(tone[0]))];
    }
    size_t fade = worker->play_length - index - 1;
    if (fade > index) {
        fade = index;
    }
    if (fade < FADE_SAMPLES) {
        sample = sample * (int)fade / (int)FADE_SAMPLES;
    }
    return worker->status.muted ? 0 : (int16_t)(sample * (int)worker->status.volume / 100);
}

static void play_block(audio_worker_t *worker, int16_t *stereo)
{
    const size_t remaining = worker->play_length - worker->played;
    const size_t payload = remaining < AUDIO_BLOCK_FRAMES ? remaining : AUDIO_BLOCK_FRAMES;
    for (size_t index = 0; index < AUDIO_BLOCK_FRAMES; ++index) {
        const int16_t sample = index < payload ? playback_sample(worker, worker->played + index) : 0;
        stereo[index * 2] = sample;
        stereo[index * 2 + 1] = sample;
    }
    size_t frames = AUDIO_BLOCK_FRAMES;
    const esp_err_t err = audio_hw_write(stereo, &frames);
    if (err != ESP_OK || frames != AUDIO_BLOCK_FRAMES) {
        finish(worker, err == ESP_OK ? ESP_ERR_INVALID_SIZE : err, false);
        return;
    }
    worker->played += payload;
    if (!payload && ++worker->drain_blocks >= AUDIO_DMA_BLOCKS) {
        finish(worker, ESP_OK, false);
    }
}

static bool is_running(board_audio_state_t state)
{
    return state == BOARD_AUDIO_RECORDING || state == BOARD_AUDIO_PLAYING;
}

static void handle_command(audio_worker_t *worker, const audio_command_t *command)
{
    switch (command->kind) {
    case COMMAND_RETRY:
        initialize(worker);
        if (worker->ready && worker->recorded) {
            set_state(worker, BOARD_AUDIO_RECORDED, ESP_OK);
        }
        break;
    case COMMAND_RECORD:
        start_recording(worker);
        break;
    case COMMAND_TONE:
    case COMMAND_PCM:
    case COMMAND_REPLAY:
        start_playback(worker, command);
        break;
    case COMMAND_CANCEL:
    case COMMAND_STOP:
        if (is_running(worker->status.state)) {
            finish(worker, ESP_OK, command->kind == COMMAND_CANCEL);
        }
        break;
    case COMMAND_CLEAR:
        clear_recording(worker);
        set_state(worker, BOARD_AUDIO_IDLE, ESP_OK);
        break;
    case COMMAND_VOLUME:
    case COMMAND_MUTE:
        if (command->kind == COMMAND_VOLUME) {
            worker->status.volume = command->value;
        } else {
            worker->status.muted = command->value != 0;
        }
        if (worker->status.state == BOARD_AUDIO_PLAYING) {
            const esp_err_t err = audio_hw_set_mute(worker->status.muted || !worker->status.volume);
            if (err != ESP_OK) {
                finish(worker, err, false);
            }
        }
        break;
    case COMMAND_EXIT:
        break;
    }
}

static bool reserves_state(command_kind_t kind)
{
    return kind <= COMMAND_REPLAY || kind == COMMAND_CLEAR;
}

static void audio_task(void *argument)
{
    (void)argument;
    audio_worker_t worker = {.status = {.volume = 20}};
    int16_t stereo[AUDIO_BLOCK_FRAMES * 2];
    initialize(&worker);
    publish(&worker, false);

    while (true) {
        audio_command_t command;
        if (xQueueReceive(commands, &command, is_running(worker.status.state) ? 0 : portMAX_DELAY)) {
            if (command.kind == COMMAND_EXIT) {
                break;
            }
            handle_command(&worker, &command);
            publish(&worker, reserves_state(command.kind));
        }
        if (worker.status.state == BOARD_AUDIO_RECORDING) {
            record_block(&worker, stereo);
            publish(&worker, false);
        } else if (worker.status.state == BOARD_AUDIO_PLAYING) {
            play_block(&worker, stereo);
            publish(&worker, false);
        }
    }
    release_result = audio_hw_release();
    clear_recording(&worker);
    free(worker.recording);
    board_4_3c_log_memory();
    xSemaphoreGive(worker_done);
    vTaskDelete(NULL);
}

esp_err_t board_audio_init(void)
{
    if (commands) {
        return ESP_ERR_INVALID_STATE;
    }
    commands = xQueueCreate(COMMAND_DEPTH, sizeof(audio_command_t));
    snapshot_lock = xSemaphoreCreateMutex();
    worker_done = xSemaphoreCreateBinary();
    snapshot = (board_audio_status_t){.state = BOARD_AUDIO_INITIALIZING, .volume = 20};
    command_pending = false;
    if (commands && snapshot_lock && worker_done &&
        xTaskCreate(audio_task, "zhiya_audio", WORKER_STACK_BYTES, NULL, 4, NULL) == pdPASS) {
        return ESP_OK;
    }
    if (commands) {
        vQueueDelete(commands);
    }
    if (snapshot_lock) {
        vSemaphoreDelete(snapshot_lock);
    }
    if (worker_done) {
        vSemaphoreDelete(worker_done);
    }
    commands = NULL;
    snapshot_lock = NULL;
    worker_done = NULL;
    snapshot = (board_audio_status_t){.state = BOARD_AUDIO_ERROR, .error = ESP_ERR_NO_MEM, .volume = 20};
    return ESP_ERR_NO_MEM;
}

esp_err_t board_audio_release(void)
{
    if (!commands) {
        return ESP_OK;
    }
    const audio_command_t command = {.kind = COMMAND_EXIT};
    xQueueSend(commands, &command, portMAX_DELAY);
    xSemaphoreTake(worker_done, portMAX_DELAY);
    vQueueDelete(commands);
    vSemaphoreDelete(worker_done);
    vSemaphoreDelete(snapshot_lock);
    commands = NULL;
    worker_done = NULL;
    snapshot_lock = NULL;
    snapshot = (board_audio_status_t){.state = BOARD_AUDIO_OFFLINE};
    return release_result;
}

board_audio_status_t board_audio_status(void)
{
    if (!snapshot_lock) {
        return snapshot;
    }
    xSemaphoreTake(snapshot_lock, portMAX_DELAY);
    board_audio_status_t status = snapshot;
    status.pending = command_pending;
    xSemaphoreGive(snapshot_lock);
    return status;
}

static esp_err_t submit(audio_command_t command)
{
    if (!commands) {
        return ESP_ERR_INVALID_STATE;
    }
    xSemaphoreTake(snapshot_lock, portMAX_DELAY);
    const board_audio_state_t state = snapshot.state;
    const bool idle = (state == BOARD_AUDIO_IDLE || state == BOARD_AUDIO_RECORDED) && !command_pending;
    bool allowed = false;
    switch (command.kind) {
    case COMMAND_RECORD:
    case COMMAND_TONE:
    case COMMAND_PCM:
        allowed = idle;
        break;
    case COMMAND_REPLAY:
    case COMMAND_CLEAR:
        allowed = idle && state == BOARD_AUDIO_RECORDED;
        break;
    case COMMAND_STOP:
    case COMMAND_CANCEL:
        allowed = is_running(state);
        break;
    case COMMAND_RETRY:
        allowed = state == BOARD_AUDIO_ERROR && !command_pending;
        break;
    case COMMAND_VOLUME:
    case COMMAND_MUTE:
        allowed = true;
        break;
    case COMMAND_EXIT:
        break;
    }
    esp_err_t result = ESP_ERR_INVALID_STATE;
    if (allowed) {
        if (xQueueSend(commands, &command, 0) == pdTRUE) {
            command_pending |= reserves_state(command.kind);
            result = ESP_OK;
        } else {
            result = ESP_ERR_TIMEOUT;
        }
    }
    xSemaphoreGive(snapshot_lock);
    return result;
}

esp_err_t board_audio_retry(void)
{
    return commands ? submit((audio_command_t){.kind = COMMAND_RETRY}) : board_audio_init();
}
esp_err_t board_audio_test_tone(void) { return submit((audio_command_t){.kind = COMMAND_TONE}); }
esp_err_t board_audio_record(void) { return submit((audio_command_t){.kind = COMMAND_RECORD}); }
esp_err_t board_audio_replay(void) { return submit((audio_command_t){.kind = COMMAND_REPLAY}); }
esp_err_t board_audio_stop(void) { return submit((audio_command_t){.kind = COMMAND_STOP}); }
esp_err_t board_audio_cancel(void) { return submit((audio_command_t){.kind = COMMAND_CANCEL}); }
esp_err_t board_audio_clear(void) { return submit((audio_command_t){.kind = COMMAND_CLEAR}); }

esp_err_t board_audio_play_pcm(const int16_t *samples, size_t count)
{
    if (!samples || !count || count > BOARD_AUDIO_MAX_SAMPLES) {
        return ESP_ERR_INVALID_ARG;
    }
    return submit((audio_command_t){.kind = COMMAND_PCM, .pcm = (const uint8_t *)samples, .count = count});
}

esp_err_t board_audio_play_wav(const void *buffer, size_t bytes)
{
    audio_command_t command = {.kind = COMMAND_PCM};
    if (!audio_wav_parse(buffer, bytes, &command.pcm, &command.count)) {
        return ESP_ERR_INVALID_ARG;
    }
    return submit(command);
}

esp_err_t board_audio_set_volume(unsigned percent)
{
    if (percent > BOARD_AUDIO_MAX_VOLUME) {
        return ESP_ERR_INVALID_ARG;
    }
    return submit((audio_command_t){.kind = COMMAND_VOLUME, .value = percent});
}

esp_err_t board_audio_set_mute(bool muted)
{
    return submit((audio_command_t){.kind = COMMAND_MUTE, .value = muted});
}
