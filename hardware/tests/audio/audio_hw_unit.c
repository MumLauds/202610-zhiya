#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "audio_hw.h"

#define ESP_RETURN_ON_ERROR(operation, ...) do { \
    const esp_err_t result = (operation); \
    if (result != ESP_OK) return result; \
} while (0)
#define ESP_RETURN_ON_FALSE(condition, error, ...) do { if (!(condition)) return error; } while (0)

enum event {
    AMP_OFF, PRELOAD, TX_ON, ADC_ON, RX_ON, DAC_ON, MUTE, FORMAT, GAIN, AMP_ON, RX_OFF,
    DAC_OFF, ADC_OFF, TX_OFF, TX_DELETE, RX_DELETE, DAC_DELETE, ADC_DELETE, DAC_REMOVE, ADC_REMOVE,
};
typedef enum event event_t;
typedef struct { int (*enable)(const void *, bool); int (*mute)(const void *, bool);
                 int (*set_vol)(const void *, float); } codec_t;
typedef struct { enum event remove_event; } control_t;

static event_t events[48];
static size_t event_count;
static event_t fail_at;
static void *transmit;
static void *receive;
static const codec_t *adc;
static const codec_t *dac;
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
    return record(codec == adc ? (enable ? ADC_ON : ADC_OFF) : (enable ? DAC_ON : DAC_OFF));
}

static int codec_mute(const void *codec, bool mute)
{
    assert(codec == dac && mute);
    return record(MUTE);
}

static int codec_volume(const void *codec, float db)
{
    assert(codec == dac && db == -18.0f);
    return record(GAIN);
}

static esp_err_t codec_result(int result) { return result; }
static esp_err_t configure_serial_format(const void *codec) { assert(codec == dac); return record(FORMAT); }
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
    static const codec_t speaker = {.enable = codec_enable, .mute = codec_mute, .set_vol = codec_volume};
    static const codec_t microphone = {.enable = codec_enable};
    transmit = (void *)1;
    receive = (void *)2;
    dac = &speaker;
    adc = &microphone;
    fail_at = RX_ON;
    assert(audio_hw_start_rx() == ESP_ERR_TIMEOUT);
    assert(transmitting && !receiving);
    const event_t rx_start[] = {AMP_OFF, PRELOAD, PRELOAD, PRELOAD, PRELOAD, TX_ON, ADC_ON, RX_ON};
    expect(rx_start, sizeof(rx_start) / sizeof(rx_start[0]));
    fail_at = RX_OFF;
    assert(audio_hw_stop() == ESP_OK);
    const event_t rx_cleanup[] = {MUTE, AMP_OFF, DAC_OFF, ADC_OFF, TX_OFF};
    expect(rx_cleanup, sizeof(rx_cleanup) / sizeof(rx_cleanup[0]));
    assert(audio_hw_start_rx() == ESP_OK && receiving);
    expect(rx_start, sizeof(rx_start) / sizeof(rx_start[0]));
    fail_at = TX_ON;
    assert(audio_hw_stop() == ESP_OK);
    const event_t both_cleanup[] = {MUTE, AMP_OFF, RX_OFF, DAC_OFF, ADC_OFF, TX_OFF};
    expect(both_cleanup, sizeof(both_cleanup) / sizeof(both_cleanup[0]));

    fail_at = AMP_ON;
    assert(audio_hw_start_tx() == ESP_ERR_TIMEOUT);
    const event_t tx_start[] = {
        AMP_OFF, PRELOAD, PRELOAD, PRELOAD, PRELOAD, TX_ON, DAC_ON, MUTE, FORMAT, GAIN, AMP_ON,
    };
    expect(tx_start, sizeof(tx_start) / sizeof(tx_start[0]));
    assert(audio_hw_release() == ESP_OK);
    const event_t release[] = {
        MUTE, AMP_OFF, DAC_OFF, ADC_OFF, TX_OFF, TX_DELETE, RX_DELETE,
        DAC_DELETE, ADC_DELETE, DAC_REMOVE, ADC_REMOVE,
    };
    expect(release, sizeof(release) / sizeof(release[0]));
    assert(!transmit && !receive && !dac && !adc);
    puts("PASS silent RX clock, muted PA start, fault shutdown and audio-only ownership");
    return 0;
}
