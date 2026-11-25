#include "audio_hw.h"

#include "board_4_3c_io.h"
#include "board_audio.h"
#include "driver/i2s_std.h"
#include "esp_check.h"
#include "es7210_adc.h"
#include "es8311_codec.h"

#define ES8311_VOLUME_ZERO_CODE 191
#define ES8311_DEFAULT_VOLTAGE_COMP_CODES 7

#if CONFIG_CODEC_I2C_BACKWARD_COMPATIBLE
#error H07 requires the ESP-IDF I2C master bus API
#endif

static const char *const TAG = "audio_hw";

typedef struct {
    audio_codec_ctrl_if_t interface;
    i2c_master_dev_handle_t device;
    uint8_t address;
} codec_control_t;

static i2s_chan_handle_t receive;
static i2s_chan_handle_t transmit;
static codec_control_t adc_control;
static codec_control_t dac_control;
static const audio_codec_if_t *adc;
static const audio_codec_if_t *dac;
static bool receiving;
static bool transmitting;
static esp_err_t codec_io_error;

static esp_err_t codec_result(int result)
{
    const esp_err_t io_error = codec_io_error;
    codec_io_error = ESP_OK;
    if (io_error != ESP_OK) {
        return io_error;
    }
    return result == ESP_CODEC_DEV_OK ? ESP_OK : ESP_ERR_INVALID_RESPONSE;
}

static void keep_first_error(esp_err_t *result, esp_err_t next)
{
    if (*result == ESP_OK) {
        *result = next;
    }
}

static bool control_is_open(const audio_codec_ctrl_if_t *interface)
{
    return ((const codec_control_t *)interface)->device != NULL;
}

static int control_info(const audio_codec_ctrl_if_t *interface, audio_codec_ctrl_info_t *info)
{
    *info = (audio_codec_ctrl_info_t){
        .type = AUDIO_CODEC_CTRL_I2C,
        .i2c = {.port = I2C_NUM_0, .addr = ((const codec_control_t *)interface)->address},
    };
    return ESP_CODEC_DEV_OK;
}

static int control_read(const audio_codec_ctrl_if_t *interface, int reg, int reg_len,
                        void *data, int data_len)
{
    if (reg_len != 1 || data_len != 1 || !data) {
        keep_first_error(&codec_io_error, ESP_ERR_INVALID_ARG);
        return ESP_CODEC_DEV_INVALID_ARG;
    }
    const uint8_t address = (uint8_t)reg;
    const codec_control_t *control = (const codec_control_t *)interface;
    const esp_err_t err = i2c_master_transmit_receive(control->device, &address, 1, data, 1, 20);
    keep_first_error(&codec_io_error, err);
    return err == ESP_OK ? ESP_CODEC_DEV_OK : ESP_CODEC_DEV_READ_FAIL;
}

static int control_write(const audio_codec_ctrl_if_t *interface, int reg, int reg_len,
                         void *data, int data_len)
{
    if (reg_len != 1 || data_len != 1 || !data) {
        keep_first_error(&codec_io_error, ESP_ERR_INVALID_ARG);
        return ESP_CODEC_DEV_INVALID_ARG;
    }
    const uint8_t bytes[] = {(uint8_t)reg, *(const uint8_t *)data};
    const codec_control_t *control = (const codec_control_t *)interface;
    const esp_err_t err = i2c_master_transmit(control->device, bytes, sizeof(bytes), 20);
    keep_first_error(&codec_io_error, err);
    return err == ESP_OK ? ESP_CODEC_DEV_OK : ESP_CODEC_DEV_WRITE_FAIL;
}

static esp_err_t control_add(i2c_master_bus_handle_t bus, uint8_t address, codec_control_t *control)
{
    ESP_RETURN_ON_ERROR(i2c_master_probe(bus, address >> 1, 20), TAG, "Codec probe 0x%02x", address >> 1);
    const i2c_device_config_t config = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7, .device_address = address >> 1, .scl_speed_hz = 400000,
    };
    control->address = address;
    control->interface = (audio_codec_ctrl_if_t){
        .is_open = control_is_open, .get_info = control_info, .read_reg = control_read, .write_reg = control_write,
    };
    return i2c_master_bus_add_device(bus, &config, &control->device);
}

static esp_err_t control_remove(codec_control_t *control)
{
    if (!control->device) {
        return ESP_OK;
    }
    const esp_err_t err = i2c_master_bus_rm_device(control->device);
    if (err == ESP_OK) {
        control->device = NULL;
    }
    return err;
}

static esp_err_t configure_serial_format(const audio_codec_if_t *codec)
{
    esp_codec_dev_sample_info_t format = {
        .sample_rate = BOARD_AUDIO_RATE, .bits_per_sample = 16, .channel = 2, .mclk_multiple = 256,
    };
    return codec_result(codec->set_fs(codec, &format));
}

esp_err_t audio_hw_init(void)
{
    ESP_RETURN_ON_FALSE(!receive && !transmit && !adc && !dac && !adc_control.device && !dac_control.device,
                        ESP_ERR_INVALID_STATE, TAG, "Previous audio resources still owned");
    i2c_master_bus_handle_t bus = board_4_3c_borrow_i2c();
    ESP_RETURN_ON_FALSE(bus, ESP_ERR_INVALID_STATE, TAG, "Shared I2C unavailable");
    ESP_RETURN_ON_ERROR(board_4_3c_set_audio_amp(false), TAG, "PA disable");
    ESP_RETURN_ON_ERROR(control_add(bus, ES7210_CODEC_DEFAULT_ADDR, &adc_control), TAG, "ES7210 control");
    ESP_RETURN_ON_ERROR(control_add(bus, ES8311_CODEC_DEFAULT_ADDR, &dac_control), TAG, "ES8311 control");

    es7210_codec_cfg_t input = {
        .ctrl_if = &adc_control.interface, .mic_selected = ES7210_SEL_MIC1 | ES7210_SEL_MIC2,
        .mclk_src = ES7210_MCLK_FROM_PAD, .mclk_div = 256,
    };
    adc = es7210_codec_new(&input);
    ESP_RETURN_ON_ERROR(codec_result(adc ? ESP_CODEC_DEV_OK : ESP_CODEC_DEV_DRV_ERR),
                        TAG, "ES7210 configuration");
    es8311_codec_cfg_t output = {
        .ctrl_if = &dac_control.interface, .codec_mode = ESP_CODEC_DEV_WORK_MODE_DAC,
        .pa_pin = GPIO_NUM_NC, .use_mclk = true, .mclk_div = 256,
    };
    dac = es8311_codec_new(&output);
    ESP_RETURN_ON_ERROR(codec_result(dac ? ESP_CODEC_DEV_OK : ESP_CODEC_DEV_DRV_ERR),
                        TAG, "ES8311 configuration");

    ESP_RETURN_ON_ERROR(configure_serial_format(adc), TAG, "ES7210 format");
    ESP_RETURN_ON_ERROR(configure_serial_format(dac), TAG, "ES8311 format");
    ESP_RETURN_ON_ERROR(codec_result(dac->mute(dac, true)), TAG, "ES8311 mute");

    i2s_chan_config_t channel = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_1, I2S_ROLE_MASTER);
    channel.dma_desc_num = AUDIO_DMA_BLOCKS;
    channel.dma_frame_num = AUDIO_BLOCK_FRAMES;
    channel.auto_clear_after_cb = true;
    ESP_RETURN_ON_ERROR(i2s_new_channel(&channel, &transmit, &receive), TAG, "I2S channel allocation");
    const i2s_std_config_t i2s = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(BOARD_AUDIO_RATE),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_STEREO),
        .gpio_cfg = {.mclk = GPIO_NUM_6, .bclk = GPIO_NUM_44, .ws = GPIO_NUM_16,
                     .dout = GPIO_NUM_15, .din = GPIO_NUM_43},
    };
    ESP_RETURN_ON_ERROR(i2s_channel_init_std_mode(transmit, &i2s), TAG, "I2S TX format");
    ESP_RETURN_ON_ERROR(i2s_channel_init_std_mode(receive, &i2s), TAG, "I2S RX format");
    return ESP_OK;
}

static esp_err_t start_silent_clock(void)
{
    const int16_t silence[AUDIO_BLOCK_FRAMES * 2] = {0};
    for (unsigned block = 0; block < AUDIO_DMA_BLOCKS; ++block) {
        size_t loaded = 0;
        ESP_RETURN_ON_ERROR(i2s_channel_preload_data(transmit, silence, sizeof(silence), &loaded),
                            TAG, "Preload I2S silence");
        ESP_RETURN_ON_FALSE(loaded == sizeof(silence), ESP_ERR_INVALID_SIZE, TAG, "DMA preload short");
    }
    ESP_RETURN_ON_ERROR(i2s_channel_enable(transmit), TAG, "Enable I2S TX");
    transmitting = true;
    return ESP_OK;
}

static esp_err_t read_codec_reg(const audio_codec_if_t *codec, int reg, int *value)
{
    return codec_result(codec->get_reg(codec, reg, value));
}

static esp_err_t configure_adc_gain(unsigned gain_db)
{
    int previous_left = 0;
    int previous_right = 0;
    int left = 0;
    int right = 0;
    int serial = 0;
    int mode = 0;
    ESP_RETURN_ON_ERROR(read_codec_reg(adc, 0x43, &previous_left), TAG, "Read ADC MIC1 gain after enable");
    ESP_RETURN_ON_ERROR(read_codec_reg(adc, 0x44, &previous_right), TAG, "Read ADC MIC2 gain after enable");
    const int driver_result = adc->set_mic_gain(adc, (float)gain_db);
    const esp_err_t result = codec_result(driver_result);
    ESP_LOGI(TAG, "ADC requested=+%u dB driver=%d status=%s previous_gain_code=%d/%d",
             gain_db, driver_result, esp_err_to_name(result), previous_left & 0x0f, previous_right & 0x0f);
    ESP_RETURN_ON_ERROR(result, TAG, "Set ES7210 gain after enable");
    ESP_RETURN_ON_ERROR(read_codec_reg(adc, 0x43, &left), TAG, "Verify ADC MIC1 gain");
    ESP_RETURN_ON_ERROR(read_codec_reg(adc, 0x44, &right), TAG, "Verify ADC MIC2 gain");
    ESP_RETURN_ON_ERROR(read_codec_reg(adc, 0x11, &serial), TAG, "Verify ADC serial format");
    ESP_RETURN_ON_ERROR(read_codec_reg(adc, 0x12, &mode), TAG, "Verify ADC slot mode");
    const int expected = 0x10 | (int)(gain_db / 3);
    const bool valid = (left & 0x1f) == expected && (right & 0x1f) == expected &&
                       (serial & 0xe3) == 0x60 && !(mode & 0x02);
    ESP_LOGI(TAG, "ADC MIC1=0x%02x MIC2=0x%02x format=0x%02x mode=0x%02x verified=%d",
             (unsigned)left, (unsigned)right, (unsigned)serial, (unsigned)mode, valid);
    return valid ? ESP_OK : ESP_ERR_INVALID_RESPONSE;
}

static esp_err_t configure_dac_gain(int gain_db)
{
    const int driver_result = dac->set_vol(dac, (float)gain_db);
    const esp_err_t result = codec_result(driver_result);
    ESP_LOGI(TAG, "DAC requested=%d dB driver=%d status=%s", gain_db, driver_result, esp_err_to_name(result));
    ESP_RETURN_ON_ERROR(result, TAG, "Set ES8311 gain");
    int actual = 0;
    ESP_RETURN_ON_ERROR(read_codec_reg(dac, 0x32, &actual), TAG, "Read ES8311 gain");
    const int expected = ES8311_VOLUME_ZERO_CODE + ES8311_DEFAULT_VOLTAGE_COMP_CODES + 2 * gain_db;
    ESP_LOGI(TAG, "DAC volume=0x%02x expected=0x%02x register_half_db=%d verified=%d",
             (unsigned)actual, (unsigned)expected, actual - ES8311_VOLUME_ZERO_CODE, actual == expected);
    return actual == expected ? ESP_OK : ESP_ERR_INVALID_RESPONSE;
}

esp_err_t audio_hw_start_rx(unsigned gain_db)
{
    ESP_RETURN_ON_ERROR(board_4_3c_set_audio_amp(false), TAG, "PA disable before RX");
    ESP_RETURN_ON_ERROR(start_silent_clock(), TAG, "I2S clock for RX");
    ESP_RETURN_ON_ERROR(codec_result(adc->enable(adc, true)), TAG, "Enable ES7210");
    ESP_RETURN_ON_ERROR(configure_serial_format(adc), TAG, "Restore ES7210 format");
    ESP_RETURN_ON_ERROR(configure_adc_gain(gain_db), TAG, "Verify ES7210 active gain and format");
    const esp_err_t result = i2s_channel_enable(receive);
    receiving = result == ESP_OK;
    return result;
}

esp_err_t audio_hw_start_tx(int gain_db)
{
    ESP_RETURN_ON_ERROR(board_4_3c_set_audio_amp(false), TAG, "PA disable before TX");
    ESP_RETURN_ON_ERROR(start_silent_clock(), TAG, "I2S clock for TX");
    ESP_RETURN_ON_ERROR(codec_result(dac->enable(dac, true)), TAG, "Enable ES8311");
    ESP_RETURN_ON_ERROR(codec_result(dac->mute(dac, true)), TAG, "Mute ES8311 before PA");
    ESP_RETURN_ON_ERROR(configure_serial_format(dac), TAG, "Restore ES8311 format");
    ESP_RETURN_ON_ERROR(configure_dac_gain(gain_db), TAG, "Verify ES8311 active gain");
    ESP_RETURN_ON_ERROR(board_4_3c_set_audio_amp(true), TAG, "Enable PA");
    return ESP_OK;
}

esp_err_t audio_hw_set_mute(bool mute)
{
    return codec_result(dac->mute(dac, mute));
}

esp_err_t audio_hw_stop(void)
{
    esp_err_t result = ESP_OK;
    if (dac) {
        keep_first_error(&result, codec_result(dac->mute(dac, true)));
        keep_first_error(&result, board_4_3c_set_audio_amp(false));
    }
    if (receiving) {
        esp_err_t stopped = i2s_channel_disable(receive);
        receiving = stopped != ESP_OK;
        keep_first_error(&result, stopped);
    }
    if (dac) {
        keep_first_error(&result, codec_result(dac->enable(dac, false)));
    }
    if (adc) {
        keep_first_error(&result, codec_result(adc->enable(adc, false)));
    }
    if (transmitting) {
        esp_err_t stopped = i2s_channel_disable(transmit);
        transmitting = stopped != ESP_OK;
        keep_first_error(&result, stopped);
    }
    return result;
}

esp_err_t audio_hw_release(void)
{
    esp_err_t result = audio_hw_stop();
    if (transmit) {
        esp_err_t deleted = i2s_del_channel(transmit);
        keep_first_error(&result, deleted);
        if (deleted == ESP_OK) {
            transmit = NULL;
            transmitting = false;
        }
    }
    if (receive) {
        esp_err_t deleted = i2s_del_channel(receive);
        keep_first_error(&result, deleted);
        if (deleted == ESP_OK) {
            receive = NULL;
            receiving = false;
        }
    }
    if (dac) {
        keep_first_error(&result, codec_result(audio_codec_delete_codec_if(dac)));
        dac = NULL;
    }
    if (adc) {
        keep_first_error(&result, codec_result(audio_codec_delete_codec_if(adc)));
        adc = NULL;
    }
    keep_first_error(&result, control_remove(&dac_control));
    keep_first_error(&result, control_remove(&adc_control));
    return result;
}

esp_err_t audio_hw_read(int16_t *stereo, size_t *frames)
{
    size_t bytes = 0;
    esp_err_t result = i2s_channel_read(receive, stereo, *frames * 4, &bytes, 40);
    *frames = bytes / 4;
    return bytes % 4 ? ESP_ERR_INVALID_SIZE : result;
}

esp_err_t audio_hw_write(const int16_t *stereo, size_t *frames)
{
    size_t bytes = 0;
    esp_err_t result = i2s_channel_write(transmit, stereo, *frames * 4, &bytes, 40);
    *frames = bytes / 4;
    return bytes % 4 ? ESP_ERR_INVALID_SIZE : result;
}
