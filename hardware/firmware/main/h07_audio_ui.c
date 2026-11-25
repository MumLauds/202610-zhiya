#include "h07_audio_ui.h"

#include "board_audio.h"

LV_FONT_DECLARE(h02_title_font);
LV_FONT_DECLARE(h07_label_font);

typedef esp_err_t (*audio_action_t)(void);

static lv_obj_t *state_label;
static lv_obj_t *length_label;
static lv_obj_t *progress;
static lv_obj_t *level_label;
static lv_obj_t *level_meter;
static lv_obj_t *message;
static lv_obj_t *tone_button;
static lv_obj_t *record_button;
static lv_obj_t *stop_button;
static lv_obj_t *cancel_button;
static lv_obj_t *replay_button;
static lv_obj_t *clear_button;
static lv_obj_t *retry_button;
static lv_obj_t *mute_button;
static lv_obj_t *mute_label;
static lv_obj_t *volume_label;
static lv_obj_t *volume_slider;
static lv_obj_t *psram_button;
static lv_obj_t *slot_button;
static lv_obj_t *slot_label;
static esp_err_t request_error;

static lv_obj_t *label_at(lv_obj_t *parent, const char *text, int x, int y)
{
    lv_obj_t *label = lv_label_create(parent);
    if (label) {
        lv_label_set_text(label, text);
        lv_obj_set_pos(label, x, y);
    }
    return label;
}

static lv_obj_t *card(lv_obj_t *parent, int x)
{
    lv_obj_t *panel = lv_obj_create(parent);
    if (panel) {
        lv_obj_set_pos(panel, x, 86);
        lv_obj_set_size(panel, 370, 153);
        lv_obj_set_style_bg_color(panel, lv_color_white(), 0);
        lv_obj_set_style_radius(panel, 12, 0);
        lv_obj_remove_flag(panel, LV_OBJ_FLAG_SCROLLABLE);
    }
    return panel;
}

static void action_clicked(lv_event_t *event)
{
    const audio_action_t *action = lv_event_get_user_data(event);
    request_error = (*action)();
}

static esp_err_t toggle_mute(void)
{
    return board_audio_set_mute(!board_audio_status().muted);
}

static esp_err_t toggle_slot(void)
{
    return board_audio_set_input_slot(1U - board_audio_status().input_slot);
}

static lv_obj_t *button_at(lv_obj_t *parent, const char *text, int x, int y, audio_action_t action,
                           audio_action_t *storage)
{
    lv_obj_t *button = lv_button_create(parent);
    if (!button) {
        return NULL;
    }
    lv_obj_set_pos(button, x, y);
    lv_obj_set_size(button, 114, 48);
    lv_obj_t *label = lv_label_create(button);
    if (!label) {
        return NULL;
    }
    lv_label_set_text(label, text);
    lv_obj_set_style_text_font(label, &h07_label_font, 0);
    lv_obj_center(label);
    *storage = action;
    if (!lv_obj_add_event_cb(button, action_clicked, LV_EVENT_CLICKED, storage)) {
        return NULL;
    }
    return button;
}

static void set_enabled(lv_obj_t *button, bool enabled)
{
    if (enabled) {
        lv_obj_remove_state(button, LV_STATE_DISABLED);
    } else {
        lv_obj_add_state(button, LV_STATE_DISABLED);
    }
}

static void volume_changed(lv_event_t *event)
{
    request_error = board_audio_set_volume(lv_slider_get_value(lv_event_get_target(event)));
}

static void refresh(lv_timer_t *timer)
{
    (void)timer;
    const board_audio_status_t status = board_audio_status();
    static const char *const states[] = {
        "Starting", "Initializing codec", "Ready", "Recording", "Recording ready", "Playing", "Audio error",
    };
    const unsigned tenths = status.samples / (BOARD_AUDIO_RATE / 10);
    const bool idle = (status.state == BOARD_AUDIO_IDLE || status.state == BOARD_AUDIO_RECORDED) &&
                      !status.pending;
    const bool active = status.state == BOARD_AUDIO_RECORDING || status.state == BOARD_AUDIO_PLAYING;
    const bool recorded = idle && status.state == BOARD_AUDIO_RECORDED;
    lv_label_set_text(state_label, states[status.state]);
    lv_label_set_text_fmt(length_label, "%u.%u / 15 s   |   %u samples",
                          tenths / 10, tenths % 10, (unsigned)status.samples);
    lv_bar_set_value(progress, tenths, LV_ANIM_OFF);
    lv_bar_set_value(level_meter, status.peak, LV_ANIM_OFF);
    lv_label_set_text_fmt(level_label, "Slot 0: Peak %u / RMS %u\nSlot 1: Peak %u / RMS %u",
                          (unsigned)status.slot_peak[0], (unsigned)status.slot_rms[0],
                          (unsigned)status.slot_peak[1], (unsigned)status.slot_rms[1]);
    lv_label_set_text_fmt(slot_label, "Use slot %u", (unsigned)status.input_slot);
    lv_label_set_text_fmt(volume_label, "Volume %u%%", status.volume);
    lv_label_set_text(mute_label, status.muted ? "恢复" : "静音");
    const esp_err_t error = request_error != ESP_OK ? request_error : status.error;
    const bool too_short = error == ESP_ERR_INVALID_SIZE && status.state == BOARD_AUDIO_IDLE;
    lv_label_set_text(message, too_short ? "Recording too short (minimum 0.5 s)" :
                      error == ESP_OK ? "16 kHz PCM16 Mono   |   Local diagnostics" : esp_err_to_name(error));
    set_enabled(tone_button, idle);
    set_enabled(record_button, idle);
    set_enabled(replay_button, recorded);
    set_enabled(clear_button, recorded);
    set_enabled(stop_button, active);
    set_enabled(cancel_button, active);
    set_enabled(retry_button, status.state == BOARD_AUDIO_ERROR && !status.pending);
    set_enabled(mute_button, status.state != BOARD_AUDIO_OFFLINE);
    set_enabled(psram_button, idle && status.state == BOARD_AUDIO_IDLE);
    set_enabled(slot_button, idle);
}

esp_err_t h07_audio_ui_create(lv_display_t *display)
{
    static audio_action_t actions[10];
    lv_obj_t *screen = lv_display_get_screen_active(display);
    lv_obj_remove_flag(screen, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_style_bg_color(screen, lv_color_hex(0xedf5f2), 0);
    lv_obj_t *brand = label_at(screen, "", 24, 23);
    lv_obj_t *left = card(screen, 22);
    lv_obj_t *right = card(screen, 408);
    if (!brand || !left || !right) {
        return ESP_ERR_NO_MEM;
    }
    lv_obj_t *title = label_at(screen, "音频诊断", 94, 29);
    lv_obj_t *meter_title = label_at(right, "麦克风信号", 0, 0);
    if (!title || !meter_title) {
        return ESP_ERR_NO_MEM;
    }
    lv_obj_set_style_text_font(title, &h07_label_font, 0);
    lv_obj_set_style_text_font(meter_title, &h07_label_font, 0);
    lv_obj_set_style_text_font(brand, &h02_title_font, 0);
    lv_label_set_text(brand, "知芽");
    state_label = label_at(left, "", 0, 0);
    length_label = label_at(left, "", 0, 31);
    progress = lv_bar_create(left);
    level_label = label_at(right, "", 0, 31);
    level_meter = lv_bar_create(right);
    tone_button = button_at(screen, "测试音", 22, 259, board_audio_test_tone, &actions[0]);
    record_button = button_at(screen, "录音", 150, 259, board_audio_record, &actions[1]);
    stop_button = button_at(screen, "停止", 278, 259, board_audio_stop, &actions[2]);
    cancel_button = button_at(screen, "取消", 406, 259, board_audio_cancel, &actions[3]);
    replay_button = button_at(screen, "回放", 534, 259, board_audio_replay, &actions[4]);
    clear_button = button_at(screen, "清除", 662, 259, board_audio_clear, &actions[5]);
    retry_button = button_at(screen, "重试", 662, 325, board_audio_retry, &actions[6]);
    mute_button = button_at(screen, "静音", 534, 325, toggle_mute, &actions[7]);
    slot_button = button_at(screen, "Use slot 0", 530, 21, toggle_slot, &actions[8]);
    psram_button = button_at(screen, "PSRAM tone", 658, 21, board_audio_psram_test_tone, &actions[9]);
    volume_label = label_at(screen, "", 22, 340);
    message = label_at(screen, "", 22, 412);
    volume_slider = lv_slider_create(screen);
    if (!state_label || !length_label || !progress || !level_label || !level_meter ||
        !tone_button || !record_button || !stop_button || !cancel_button || !replay_button ||
        !clear_button || !retry_button || !mute_button || !volume_label || !message || !volume_slider ||
        !slot_button || !psram_button) {
        return ESP_ERR_NO_MEM;
    }
    mute_label = lv_obj_get_child(mute_button, 0);
    slot_label = lv_obj_get_child(slot_button, 0);
    lv_obj_set_style_text_font(slot_label, LV_FONT_DEFAULT, 0);
    lv_obj_set_style_text_font(lv_obj_get_child(psram_button, 0), LV_FONT_DEFAULT, 0);
    lv_obj_set_pos(progress, 0, 73);
    lv_obj_set_size(progress, 326, 14);
    lv_bar_set_range(progress, 0, 150);
    lv_obj_set_pos(level_meter, 0, 73);
    lv_obj_set_size(level_meter, 326, 14);
    lv_bar_set_range(level_meter, 0, 32768);
    lv_obj_set_pos(volume_slider, 132, 344);
    lv_obj_set_size(volume_slider, 365, 12);
    lv_slider_set_range(volume_slider, 0, BOARD_AUDIO_MAX_VOLUME);
    lv_slider_set_value(volume_slider, 20, LV_ANIM_OFF);
    if (!lv_obj_add_event_cb(volume_slider, volume_changed, LV_EVENT_RELEASED, NULL)) {
        return ESP_ERR_NO_MEM;
    }
    refresh(NULL);
    return lv_timer_create(refresh, 100, NULL) ? ESP_OK : ESP_ERR_NO_MEM;
}
