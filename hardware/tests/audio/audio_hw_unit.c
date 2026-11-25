#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "audio_hw.h"

#define ESP_RETURN_ON_ERROR(operation, ...) do { \
    const esp_err_t check_error = (operation); \
    if (check_error != ESP_OK) return check_error; \
} while (0)
#define ESP_RETURN_ON_FALSE(condition, error, ...) do { if (!(condition)) return error; } while (0)

enum event {
    AMP_OFF, PRELOAD, TX_ON, ADC_ON, ADC_GAIN, RX_ON, DAC_ON, MUTE, FORMAT, GAIN, AMP_ON, RX_OFF,
    DAC_OFF, ADC_OFF, TX_OFF, TX_DELETE, RX_DELETE, DAC_DELETE, ADC_DELETE, DAC_REMOVE, ADC_REMOVE,
};
typedef enum event event_t;
typedef struct { int (*enable)(const void *, bool); int (*mute)(const void *, bool);
                 int (*set_vol)(const void *, float); int (*set_mic_gain)(const void *, float);
                 int (*get_reg)(const void *, int, int *); } audio_codec_if_t;
typedef struct { enum event remove_event; } control_t;

static event_t events[48];
static size_t event_count;
static event_t fail_at;
static void *transmit;
static void *receive;
static const audio_codec_if_t *adc;
static const audio_codec_if_t *dac;
static const char *const TAG = "audio_hw";
static int adc_code;
static int dac_code;
static bool bad_adc_readback;
static bool bad_dac_readback;
static control_t adc_control = {ADC_REMOVE};
static control_t dac_control = {DAC_REMOVE};
static bool transmitting;
static bool receiving;

static esp_err_t record(event_t event)
{
    assert(event_count < sizeof(events) / sizeof(events[0]));
    events[event_count++] = event;
    return event == fail_at ? ESP_ERR_TIMEOUT : ESP_OK;
}

static int codec_enable(const void *codec, bool enable)
{
    if (codec == adc && enable) {
        adc_code = 0;
    }
    return record(codec == adc ? (enable ? ADC_ON : ADC_OFF) : (enable ? DAC_ON : DAC_OFF));
}

static int codec_mute(const void *codec, bool mute)
{
    assert(codec == dac && mute);
    return record(MUTE);
}

static int codec_volume(const void *codec, float db)
{
    assert(codec == dac);
    dac_code = 198 + 2 * (int)db;
    return record(GAIN);
}

static int codec_mic_gain(const void *codec, float db)
{
    assert(codec == adc && adc_code == 0 && !receiving);
    adc_code = (int)db / 3;
    return record(ADC_GAIN);
}

static int codec_read(const void *codec, int reg, int *value)
{
    if (codec == adc) {
        switch (reg) {
        case 0x43: *value = 0x10 | adc_code; return ESP_OK;
        case 0x44: *value = 0x10 | (bad_adc_readback ? 0 : adc_code); return ESP_OK;
        case 0x11: *value = 0x60; return ESP_OK;
        case 0x12: *value = 0; return ESP_OK;
        }
    }
    assert(codec == dac && reg == 0x32);
    *value = bad_dac_readback ? 0 : dac_code;
    return ESP_OK;
}

static esp_err_t codec_result(int result) { return result; }
static esp_err_t configure_serial_format(const void *codec)
{
    assert((codec == adc || codec == dac) && !receiving);
    return record(FORMAT);
}
static esp_err_t board_4_3c_set_audio_amp(bool enabled) { return record(enabled ? AMP_ON : AMP_OFF); }

static esp_err_t i2s_channel_preload_data(const void *channel, const int16_t *samples, size_t count, size_t *loaded)
{
    assert(channel == transmit && !transmitting && count == AUDIO_BLOCK_FRAMES * 4);
    for (size_t index = 0; index < count / sizeof(*samples); ++index) {
        assert(samples[index] == 0);
    }
    *loaded = count;
    return record(PRELOAD);
}

static esp_err_t i2s_channel_enable(const void *channel)
{
    if (channel == receive) {
        assert(transmitting);
    }
    return record(channel == transmit ? TX_ON : RX_ON);
}

static esp_err_t i2s_channel_disable(const void *channel)
{
    if (channel == transmit) {
        assert(!receiving);
    }
    return record(channel == transmit ? TX_OFF : RX_OFF);
}

static esp_err_t i2s_del_channel(const void *channel) { return record(channel == transmit ? TX_DELETE : RX_DELETE); }
static esp_err_t audio_codec_delete_codec_if(const void *codec) { return record(codec == dac ? DAC_DELETE : ADC_DELETE); }
static esp_err_t control_remove(const control_t *control) { return record(control->remove_event); }

#include "hardware_functions.h"

static void expect(const event_t *expected, size_t count)
{
    assert(event_count == count && memcmp(events, expected, count * sizeof(*expected)) == 0);
    event_count = 0;
}

int main(void)
{
    static const audio_codec_if_t speaker = {
        .enable = codec_enable, .mute = codec_mute, .set_vol = codec_volume, .get_reg = codec_read,
    };
    static const audio_codec_if_t microphone = {
        .enable = codec_enable, .set_mic_gain = codec_mic_gain, .get_reg = codec_read,
    };
    transmit = (void *)1;
    receive = (void *)2;
    dac = &speaker;
    adc = &microphone;
    fail_at = RX_ON;
    assert(audio_hw_start_rx(18) == ESP_ERR_TIMEOUT && adc_code == 6);
    assert(transmitting && !receiving);
    const event_t rx_start[] = {AMP_OFF, PRELOAD, PRELOAD, PRELOAD, PRELOAD, TX_ON, ADC_ON, FORMAT, ADC_GAIN, RX_ON};
    expect(rx_start, sizeof(rx_start) / sizeof(rx_start[0]));
    fail_at = RX_OFF;
    assert(audio_hw_stop() == ESP_OK);
    const event_t rx_cleanup[] = {MUTE, AMP_OFF, DAC_OFF, ADC_OFF, TX_OFF};
    expect(rx_cleanup, sizeof(rx_cleanup) / sizeof(rx_cleanup[0]));
    bad_adc_readback = true;
    assert(audio_hw_start_rx(24) == ESP_ERR_INVALID_RESPONSE && !receiving);
    expect(rx_start, sizeof(rx_start) / sizeof(rx_start[0]) - 1);
    assert(audio_hw_stop() == ESP_OK);
    expect(rx_cleanup, sizeof(rx_cleanup) / sizeof(rx_cleanup[0]));
    bad_adc_readback = false;
    assert(audio_hw_start_rx(27) == ESP_OK && receiving && adc_code == 9);
    expect(rx_start, sizeof(rx_start) / sizeof(rx_start[0]));
    fail_at = TX_ON;
    assert(audio_hw_stop() == ESP_OK);
    const event_t both_cleanup[] = {MUTE, AMP_OFF, RX_OFF, DAC_OFF, ADC_OFF, TX_OFF};
    expect(both_cleanup, sizeof(both_cleanup) / sizeof(both_cleanup[0]));

    const event_t tx_start[] = {
        AMP_OFF, PRELOAD, PRELOAD, PRELOAD, PRELOAD, TX_ON, DAC_ON, MUTE, FORMAT, GAIN, AMP_ON,
    };
    fail_at = RX_OFF;
    bad_dac_readback = true;
    assert(audio_hw_start_tx(-6) == ESP_ERR_INVALID_RESPONSE);
    expect(tx_start, sizeof(tx_start) / sizeof(tx_start[0]) - 1);
    assert(audio_hw_stop() == ESP_OK);
    expect(rx_cleanup, sizeof(rx_cleanup) / sizeof(rx_cleanup[0]));
    bad_dac_readback = false;
    fail_at = AMP_ON;
    assert(audio_hw_start_tx(-12) == ESP_ERR_TIMEOUT && dac_code == 174);
    expect(tx_start, sizeof(tx_start) / sizeof(tx_start[0]));
    assert(audio_hw_release() == ESP_OK);
    const event_t release[] = {
        MUTE, AMP_OFF, DAC_OFF, ADC_OFF, TX_OFF, TX_DELETE, RX_DELETE,
        DAC_DELETE, ADC_DELETE, DAC_REMOVE, ADC_REMOVE,
    };
    expect(release, sizeof(release) / sizeof(release[0]));
    assert(!transmit && !receive && !dac && !adc);
    puts("PASS post-enable ADC gain, DAC readback, mute/PA sequencing and gain fault cleanup");
    return 0;
}
