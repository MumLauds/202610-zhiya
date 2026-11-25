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
static unsigned applied_adc_gain;
static int applied_dac_gain;
static unsigned rx_blocks;
static audio_signal_t observed_tx;
static bool muted_at_dac;
static int16_t tone_reference[TONE_SAMPLES + AUDIO_DMA_BLOCKS * AUDIO_BLOCK_FRAMES];
static enum { TONE_UNCHECKED, TONE_SAVE, TONE_COMPARE } tone_check;
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

esp_err_t audio_hw_start_rx(unsigned gain_db)
{
    assert(!tx_active && !rx_active);
    rx_active = true;
    rx_blocks = 0;
    applied_adc_gain = gain_db;
    return ESP_OK;
}

esp_err_t audio_hw_start_tx(int gain_db)
{
    assert(!rx_active && !tx_active);
    tx_active = true;
    applied_dac_gain = gain_db;
    tx_blocks = 0;
    observed_tx = (audio_signal_t){0};
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
    assert(tx_active);
    muted_at_dac = muted;
    return ESP_OK;
}

esp_err_t audio_hw_read(int16_t *stereo, size_t *frames)
{
    assert(rx_active && !tx_active && *frames <= AUDIO_BLOCK_FRAMES);
    const bool quiet = rx_blocks++ < 12;
    for (size_t index = 0; index < *frames; ++index) {
        stereo[index * 2] = quiet ? (index % 2 ? 301 : 299) : (index % 2 ? 800 : -200);
        stereo[index * 2 + 1] = quiet ? (index % 2 ? 21 : 19) : (index % 2 ? 320 : -280);
    }
    return read_result;
}

esp_err_t audio_hw_write(const int16_t *stereo, size_t *frames)
{
    assert(tx_active && !rx_active && *frames == AUDIO_BLOCK_FRAMES);
    for (size_t index = 0; index < *frames; ++index) {
        assert(stereo[index * 2] == stereo[index * 2 + 1]);
    }
    tx_blocks++;
    if (short_write) {
        --*frames;
    }
    for (size_t index = 0; index < *frames; ++index) {
        if (tone_check != TONE_UNCHECKED) {
            assert(observed_tx.count < sizeof(tone_reference) / sizeof(tone_reference[0]));
            if (tone_check == TONE_SAVE) {
                tone_reference[observed_tx.count] = stereo[index * 2];
            } else {
                assert(tone_reference[observed_tx.count] == stereo[index * 2]);
            }
        }
        audio_signal_add(&observed_tx, stereo[index * 2]);
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

static void drain_playback(audio_worker_t *worker, int16_t *stereo)
{
    assert(worker->status.state == BOARD_AUDIO_PLAYING);
    while (worker->status.state == BOARD_AUDIO_PLAYING) {
        play_block(worker, stereo);
    }
    assert(same_pcm(&observed_tx, &worker->tx[0]));
    assert(same_pcm(&observed_tx, &worker->tx[1]));
    publish(worker, false);
}

static audio_signal_t make_fixture(uint8_t *pcm, size_t samples, unsigned voice_amplitude,
                                   int16_t spike, size_t spike_index)
{
    audio_signal_t signal = {0};
    for (size_t index = 0; index < samples; ++index) {
        const int amplitude = index < samples / 4 ? 2 : (int)voice_amplitude;
        const int16_t sample = index == spike_index ? spike : (int16_t)(50 + (index % 2 ? amplitude : -amplitude));
        audio_pcm16_write(pcm + index * 2, sample);
        audio_signal_add(&signal, sample);
    }
    return signal;
}

static void test_gain(void)
{
    uint8_t bytes[2];
    audio_pcm16_write(bytes, INT16_MIN);
    assert(bytes[0] == 0 && bytes[1] == 128 && audio_pcm16_read(bytes) == INT16_MIN);
    audio_pcm16_write(bytes, INT16_MAX);
    assert(audio_pcm16_read(bytes) == INT16_MAX);
    enum { SAMPLES = 32000 };
    static uint8_t pcm[SAMPLES * 2];
    audio_signal_t signal = make_fixture(pcm, SAMPLES, 61, 0, SIZE_MAX);
    const audio_replay_gain_t weak = audio_replay_gain(&signal, pcm, -18);
    assert(weak.dc == 50 && weak.voice_frames == 75 && weak.gain_q8 == 16 * AUDIO_GAIN_UNITY);
    assert(audio_replay_apply(111, weak) == 976);
    signal = make_fixture(pcm, SAMPLES, 61, 16089, 5);
    const audio_replay_gain_t spike = audio_replay_gain(&signal, pcm, -18);
    assert(signal.peak == 16089 && signal.peak_index == 5 && spike.transient_frames == 1);
    assert(spike.gain_q8 == weak.gain_q8);
    assert(audio_replay_apply(16089, spike) < 8192 && audio_replay_apply(111, spike) >= 960);
    signal = make_fixture(pcm, SAMPLES, 226, 7853, 12000);
    const audio_replay_gain_t stronger = audio_replay_gain(&signal, pcm, -6);
    assert(signal.peak_index == 12000 && stronger.transient_frames == 1 &&
           stronger.gain_q8 > 8 * AUDIO_GAIN_UNITY);
    assert(audio_replay_apply(7853, stronger) < 2057);
    signal = make_fixture(pcm, SAMPLES, 2, 16089, 0);
    const audio_replay_gain_t transient_only = audio_replay_gain(&signal, pcm, -18);
    assert(transient_only.gain_q8 == AUDIO_GAIN_UNITY && !transient_only.voice_frames);
    memset(pcm, 0, sizeof(pcm));
    signal = scan_source(pcm, SAMPLES);
    const audio_replay_gain_t silence = audio_replay_gain(&signal, pcm, -6);
    assert(silence.gain_q8 == AUDIO_GAIN_UNITY && audio_replay_apply(0, silence) == 0);
    signal = make_fixture(pcm, SAMPLES, 120, INT16_MIN, 5);
    const audio_replay_gain_t limited = audio_replay_gain(&signal, pcm, -6);
    assert(signal.peak == 32768 && signal.clipped == 1);
    assert(audio_replay_apply(INT16_MIN, limited) >= -2057);
    assert(audio_replay_apply(INT16_MAX, limited) <= 2057);
}

static void test_worker(void)
{
    assert(board_audio_init() == ESP_OK);
    audio_worker_t worker = {.status = default_status(BOARD_AUDIO_OFFLINE, ESP_OK)};
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
    assert(board_audio_set_adc_gain(21) == ESP_ERR_INVALID_ARG);
    assert(board_audio_set_dac_gain(0) == ESP_ERR_INVALID_ARG);
    assert(board_audio_set_adc_gain(24) == ESP_OK);
    next(&worker);
    assert(board_audio_set_dac_gain(-6) == ESP_OK);
    next(&worker);

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
    assert(rx_active && !tx_active && applied_adc_gain == 24);
    assert(board_audio_set_adc_gain(27) == ESP_ERR_INVALID_STATE);
    assert(board_audio_set_dac_gain(-12) == ESP_ERR_INVALID_STATE);
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
    assert(audio_pcm16_read(worker.recording) == 299 && worker.status.peak == 800);
    assert(worker.capture[0].min == -200 && worker.capture[1].max == 320);
    assert(worker.status.slot_peak[0] == 800 && worker.status.slot_peak[1] == 320);
    assert(board_audio_psram_test_tone() == ESP_ERR_INVALID_STATE);

    assert(board_audio_test_tone() == ESP_OK);
    next(&worker);
    assert(applied_dac_gain == -18);
    tone_check = TONE_SAVE;
    drain_playback(&worker, stereo);
    tone_check = TONE_UNCHECKED;
    const audio_signal_t builtin_tone_tx = observed_tx;
    assert(builtin_tone_tx.peak && !muted_at_dac);
    assert(board_audio_replay() == ESP_OK);
    next(&worker);
    assert(applied_dac_gain == -6 && board_audio_set_dac_gain(-12) == ESP_ERR_INVALID_STATE);
    drain_playback(&worker, stereo);
    assert(!tx_active && tx_blocks == 50 + AUDIO_DMA_BLOCKS);
    assert(worker.status.state == BOARD_AUDIO_RECORDED && worker.recorded == 8000);
    assert(worker.compensated.peak > 1000 && worker.compensated.peak < 2057);
    assert(worker.scaled.peak > 200 && audio_pcm16_read(worker.recording) == 299);
    assert(board_audio_test_tone() == ESP_OK);
    next(&worker);
    drain_playback(&worker, stereo);
    assert(same_pcm(&builtin_tone_tx, &observed_tx));

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

    worker.recording[0] ^= 1;
    assert(board_audio_replay() == ESP_OK);
    next(&worker);
    assert(worker.status.error == ESP_ERR_INVALID_RESPONSE && !tx_active);
    worker.recording[0] ^= 1;
    assert(board_audio_retry() == ESP_OK);
    next(&worker);
    assert(board_audio_clear() == ESP_OK);
    next(&worker);
    uint8_t *original_buffer = worker.recording;
    assert(board_audio_psram_test_tone() == ESP_OK);
    next(&worker);
    assert(worker.playback == original_buffer && applied_dac_gain == -18);
    tone_check = TONE_COMPARE;
    drain_playback(&worker, stereo);
    tone_check = TONE_UNCHECKED;
    assert(same_pcm(&builtin_tone_tx, &observed_tx));
    assert(!worker.recorded && !worker.known_tone && worker.recording[0] == 0);

    assert(board_audio_set_input_slot(2) == ESP_ERR_INVALID_ARG);
    assert(board_audio_set_input_slot(1) == ESP_OK);
    next(&worker);
    assert(board_audio_record() == ESP_OK);
    next(&worker);
    while (worker.recorded < BOARD_AUDIO_MIN_SAMPLES) {
        record_block(&worker, stereo);
    }
    assert(worker.recorded_slot == 1 && audio_pcm16_read(worker.recording) == 19);
    assert(board_audio_stop() == ESP_OK);
    next(&worker);
    assert(board_audio_set_mute(true) == ESP_OK);
    next(&worker);
    assert(board_audio_replay() == ESP_OK);
    next(&worker);
    drain_playback(&worker, stereo);
    assert(muted_at_dac && !observed_tx.peak);
    assert(board_audio_set_mute(false) == ESP_OK);
    next(&worker);

    for (unsigned cycle = 0; cycle < 20; ++cycle) {
        assert(board_audio_record() == ESP_OK);
        next(&worker);
        while (worker.recorded < BOARD_AUDIO_MIN_SAMPLES) {
            record_block(&worker, stereo);
        }
        assert(board_audio_stop() == ESP_OK);
        next(&worker);
        assert(worker.recording == original_buffer && !rx_active);
        assert(board_audio_replay() == ESP_OK);
        next(&worker);
        drain_playback(&worker, stereo);
        assert(worker.status.state == BOARD_AUDIO_RECORDED && observed_tx.peak && !tx_active);
    }

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
    test_gain();
    puts("PASS weak speech, low noise, isolated 16089/7853 peaks, silence and DAC-aware soft limiter");
    test_worker();
    puts("PASS tone/replay/tone, PSRAM A/B, slot selection, corruption rejection, mute, 20 cycles and faults");
    return 0;
}
