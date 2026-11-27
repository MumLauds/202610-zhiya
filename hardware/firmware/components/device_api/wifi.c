#include "device_wifi.h"

#include <stdatomic.h>
#include <string.h>
#include <time.h>
#include "esp_event.h"
#include "esp_netif.h"
#include "esp_netif_sntp.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "mbedtls/platform_util.h"
#include "nvs_flash.h"

enum { RETRIES_MAX = 4, CONNECT_MS = 12000, DHCP_MS = 12000, DRAIN_MS = 1000, DRAIN_POST_MS = 50 };
typedef enum { STA_START, STA_LINK, STA_DOWN, STA_STOP, STA_IP, STA_LOST } event_kind_t;
typedef struct { event_kind_t kind; uint32_t value; } event_t;
ESP_EVENT_DEFINE_BASE(H08_EVENT);

static QueueHandle_t events;
static esp_netif_t *station;
static device_wifi_info_t info;
static bool ready, driver, started, sntp;
static atomic_bool accepting, online, clock_ready, overflow;
static atomic_uint drained;
static uint32_t drain_id, drain_pending;
static bool drain_posted;
static int64_t drain_until, drain_post_at;
static int64_t due;

static int64_t now_ms(void) { return esp_timer_get_time() / 1000; }

static void send_event(event_kind_t kind, uint32_t value)
{
    if (!atomic_load(&accepting)) { return; }
    const event_t event = {.kind = kind, .value = value};
    if (kind == STA_DOWN || kind == STA_STOP || kind == STA_LOST) {
        atomic_store(&online, false);
        atomic_store(&clock_ready, false);
    }
    if (xQueueSend(events, &event, 0) != pdPASS) {
        atomic_store(&overflow, true);
    }
}

static void wifi_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    (void)arg;
    (void)base;
    switch (id) {
    case WIFI_EVENT_STA_START: send_event(STA_START, 0); break;
    case WIFI_EVENT_STA_CONNECTED: send_event(STA_LINK, 0); break;
    case WIFI_EVENT_STA_DISCONNECTED:
        send_event(STA_DOWN, ((const wifi_event_sta_disconnected_t *)data)->reason);
        break;
    case WIFI_EVENT_STA_STOP: send_event(STA_STOP, 0); break;
    default: break;
    }
}

static void ip_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    (void)arg;
    (void)base;
    if (id == IP_EVENT_STA_GOT_IP) {
        const ip_event_got_ip_t *event = data;
        if (event->esp_netif == station) {
            send_event(STA_IP, event->ip_info.ip.addr);
        }
    } else if (id == IP_EVENT_STA_LOST_IP && atomic_load(&accepting)) {
        esp_netif_ip_info_t current;
        if (esp_netif_get_ip_info(station, &current) == ESP_OK && !current.ip.addr) {
            send_event(STA_LOST, 0);
        }
    }
}

static void drain_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    (void)arg;
    (void)base;
    (void)id;
    atomic_store(&drained, *(const uint32_t *)data);
}

esp_err_t device_wifi_init(void)
{
    if (ready) { return ESP_OK; }
    esp_err_t err = nvs_flash_init();
    if (err != ESP_OK) { return err; }
    err = esp_netif_init();
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) { return err; }
    err = esp_event_loop_create_default();
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) { return err; }
    wifi_mode_t mode;
    if (esp_wifi_get_mode(&mode) != ESP_ERR_WIFI_NOT_INIT || esp_netif_get_handle_from_ifkey("WIFI_STA_DEF")) {
        return ESP_ERR_INVALID_STATE;
    }
    events = xQueueCreate(16, sizeof(event_t));
    if (!events) { return ESP_ERR_NO_MEM; }
    station = esp_netif_create_default_wifi_sta();
    if (!station) {
        vQueueDelete(events);
        return ESP_ERR_NO_MEM;
    }
    esp_event_handler_instance_t wifi_instance = NULL, ip_instance = NULL, drain_instance = NULL;
    err = esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID, wifi_event, NULL, &wifi_instance);
    if (err == ESP_OK) {
        err = esp_event_handler_instance_register(IP_EVENT, ESP_EVENT_ANY_ID, ip_event, NULL, &ip_instance);
    }
    if (err == ESP_OK) {
        err = esp_event_handler_instance_register(H08_EVENT, 0, drain_event, NULL, &drain_instance);
    }
    if (err != ESP_OK) {
        if (ip_instance) {
            esp_event_handler_instance_unregister(IP_EVENT, ESP_EVENT_ANY_ID, ip_instance);
        }
        if (wifi_instance) {
            esp_event_handler_instance_unregister(WIFI_EVENT, ESP_EVENT_ANY_ID, wifi_instance);
        }
        esp_netif_destroy_default_wifi(station);
        vQueueDelete(events);
        return err;
    }
    ready = true;
    return ESP_OK;
}

static void clear_ip(void)
{
    atomic_store(&online, false);
    atomic_store(&clock_ready, false);
    info.ip = 0;
    info.rssi = 0;
    if (sntp) {
        esp_netif_sntp_deinit();
        sntp = false;
    }
}

static void arm_drain(void)
{
    if (++drain_id == 0) { ++drain_id; }
    drain_pending = drain_id;
    drain_posted = false;
    drain_post_at = now_ms();
    drain_until = drain_post_at + DRAIN_MS;
}

static esp_err_t service_drain(void)
{
    if (!drain_pending) { return ESP_OK; }
    if (atomic_load(&drained) == drain_pending) {
        /* The default loop has dispatched the old events. A callback that had
         * already entered send_event before stop cannot refill this queue now. */
        xQueueReset(events);
        atomic_store(&overflow, false);
        drain_pending = 0;
        if (info.state == DEVICE_WIFI_DRAINING ||
            (info.state == DEVICE_WIFI_ERROR && info.error == ESP_ERR_TIMEOUT)) {
            info = (device_wifi_info_t){.state = DEVICE_WIFI_STOPPED};
        }
        return ESP_OK;
    }
    const int64_t now = now_ms();
    if (now >= drain_until) {
        info.state = DEVICE_WIFI_ERROR;
        info.error = ESP_ERR_TIMEOUT;
        return ESP_ERR_TIMEOUT;
    }
    if (!drain_posted && now >= drain_post_at) {
        /* This marker travels through the same default loop as Wi-Fi/IP events.
         * Its acknowledgement bypasses the bounded driver's event queue. */
        drain_posted = esp_event_post(H08_EVENT, 0, &drain_pending, sizeof(drain_pending), 0) == ESP_OK;
        drain_post_at = now + DRAIN_POST_MS;
    }
    return ESP_ERR_INVALID_STATE;
}

esp_err_t device_wifi_stop(void)
{
    if (!ready) { return ESP_ERR_INVALID_STATE; }
    atomic_store(&accepting, false);
    clear_ip();
    const bool owned_driver = started || driver;
    if (started) {
        const esp_err_t err = esp_wifi_stop();
        if (err != ESP_OK && err != ESP_ERR_WIFI_NOT_INIT && err != ESP_ERR_WIFI_NOT_STARTED) {
            info.state = DEVICE_WIFI_ERROR;
            return info.error = err;
        }
        started = false;
        if (err == ESP_ERR_WIFI_NOT_INIT) { driver = false; }
    }
    if (driver) {
        const esp_err_t err = esp_wifi_deinit();
        if (err != ESP_OK && err != ESP_ERR_WIFI_NOT_INIT) {
            info.state = DEVICE_WIFI_ERROR;
            return info.error = err;
        }
        driver = false;
    }
    if (owned_driver || (drain_pending && service_drain() == ESP_ERR_TIMEOUT)) {
        arm_drain();
    }
    info = (device_wifi_info_t){.state = drain_pending ? DEVICE_WIFI_DRAINING : DEVICE_WIFI_STOPPED};
    (void)service_drain();
    return ESP_OK;
}

esp_err_t device_wifi_join(const char *ssid, const char *password)
{
    if (!ready || started || driver) { return ESP_ERR_INVALID_STATE; }
    if (!ssid || !password || !*ssid) { return ESP_ERR_INVALID_ARG; }
    wifi_config_t config = {0};
    const size_t ssid_size = strlen(ssid), password_size = strlen(password);
    if (ssid_size > sizeof(config.sta.ssid) || password_size >= sizeof(config.sta.password) ||
        (password_size && password_size < 8)) {
        return ESP_ERR_INVALID_ARG;
    }
    const esp_err_t drained_status = service_drain();
    if (drained_status != ESP_OK) {
        if (drained_status == ESP_ERR_TIMEOUT) {
            arm_drain();
            (void)service_drain();
        }
        return drained_status;
    }
    xQueueReset(events);
    atomic_store(&overflow, false);
    memcpy(config.sta.ssid, ssid, ssid_size);
    memcpy(config.sta.password, password, password_size);
    config.sta.threshold.authmode = password_size ? WIFI_AUTH_WPA2_PSK : WIFI_AUTH_OPEN;
    const wifi_init_config_t defaults = WIFI_INIT_CONFIG_DEFAULT();
    wifi_init_config_t init = defaults;
    init.nvs_enable = false;
    esp_err_t err = esp_wifi_init(&init);
    if (err == ESP_OK) { driver = true; err = esp_wifi_set_storage(WIFI_STORAGE_RAM); }
    if (err == ESP_OK) { err = esp_wifi_set_mode(WIFI_MODE_STA); }
    if (err == ESP_OK) { err = esp_wifi_set_config(WIFI_IF_STA, &config); }
    mbedtls_platform_zeroize(&config, sizeof(config));
    if (err == ESP_OK) {
        started = true;
        atomic_store(&accepting, true);
        err = esp_wifi_start();
    }
    if (err != ESP_OK) {
        const esp_err_t cleanup = device_wifi_stop();
        info = (device_wifi_info_t){.state = DEVICE_WIFI_ERROR, .error = cleanup == ESP_OK ? err : cleanup};
        return err;
    }
    due = now_ms() + CONNECT_MS;
    info = (device_wifi_info_t){.state = DEVICE_WIFI_CONNECTING};
    return ESP_OK;
}

static void retry(uint8_t reason, esp_err_t error)
{
    clear_ip();
    info.reason = reason;
    info.error = error;
    info.state = DEVICE_WIFI_DISCONNECTED;
    if (info.retries >= RETRIES_MAX) {
        return;
    }
    due = now_ms() + (500U << info.retries) + esp_random() % 251U;
    ++info.retries;
    info.state = DEVICE_WIFI_BACKOFF;
}

static void connect_once(void)
{
    due = now_ms() + CONNECT_MS;
    info.state = DEVICE_WIFI_CONNECTING;
    const esp_err_t err = esp_wifi_connect();
    if (err != ESP_OK) { retry(0, err); }
}

void device_wifi_poll(void)
{
    if (!ready) { return; }
    (void)service_drain();
    if (!atomic_load(&accepting)) { return; }
    if (atomic_exchange(&overflow, false)) {
        const esp_err_t err = device_wifi_stop();
        info = (device_wifi_info_t){.state = DEVICE_WIFI_ERROR, .error = err == ESP_OK ? ESP_ERR_NO_MEM : err};
        return;
    }
    event_t event;
    while (xQueueReceive(events, &event, 0) == pdPASS) {
        if (!started) { continue; }
        if (event.kind == STA_STOP) {
            const esp_err_t err = device_wifi_stop();
            info = (device_wifi_info_t){.state = DEVICE_WIFI_ERROR,
                                        .error = err == ESP_OK ? ESP_ERR_WIFI_NOT_STARTED : err};
            return;
        } else if (event.kind == STA_START && info.state == DEVICE_WIFI_CONNECTING) {
            connect_once();
        } else if (event.kind == STA_LINK && info.state == DEVICE_WIFI_CONNECTING) {
            info.state = DEVICE_WIFI_DHCP;
            due = now_ms() + DHCP_MS;
        } else if (event.kind == STA_IP && info.state == DEVICE_WIFI_DHCP) {
            info = (device_wifi_info_t){.state = DEVICE_WIFI_IP, .ip = event.value};
            atomic_store(&online, true);
            esp_sntp_config_t config = ESP_NETIF_SNTP_DEFAULT_CONFIG("pool.ntp.org");
            const esp_err_t err = esp_netif_sntp_init(&config);
            sntp = err == ESP_OK;
            info.error = err;
        } else if ((event.kind == STA_DOWN || event.kind == STA_LOST) &&
                   (info.state == DEVICE_WIFI_CONNECTING || info.state == DEVICE_WIFI_DHCP ||
                    info.state == DEVICE_WIFI_IP)) {
            if (event.kind == STA_LOST) { esp_wifi_disconnect(); }
            retry((uint8_t)event.value, ESP_FAIL);
        }
    }
    if (!started) { return; }
    if (info.state == DEVICE_WIFI_BACKOFF && now_ms() >= due) {
        connect_once();
    } else if ((info.state == DEVICE_WIFI_CONNECTING || info.state == DEVICE_WIFI_DHCP) && now_ms() >= due) {
        esp_wifi_disconnect();
        retry(0, ESP_ERR_TIMEOUT);
    }
    if (sntp && device_wifi_online() && !atomic_load(&clock_ready) &&
        esp_netif_sntp_sync_wait(0) == ESP_OK && time(NULL) >= 1735689600) {
        atomic_store(&clock_ready, true);
    }
}

bool device_wifi_online(void) { return atomic_load(&online); }
bool device_wifi_clock_ready(void) { return device_wifi_online() && atomic_load(&clock_ready); }

device_wifi_info_t device_wifi_info(void)
{
    wifi_ap_record_t ap;
    if (info.state == DEVICE_WIFI_IP && esp_wifi_sta_get_ap_info(&ap) == ESP_OK) { info.rssi = ap.rssi; }
    return info;
}

const char *device_wifi_state_name(device_wifi_state_t state)
{
    static const char *const names[] = {
        "UNCONFIGURED", "CONNECTING", "DHCP", "IP", "DISCONNECTED", "BACKOFF", "STOPPED", "ERROR", "DRAINING",
    };
    return (unsigned)state < sizeof(names) / sizeof(names[0]) ? names[state] : "UNKNOWN";
}
