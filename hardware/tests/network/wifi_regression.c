#define HOST_WIFI
#include "esp_host.h"
#include "wifi.c"
#include <stdio.h>

const esp_event_base_t WIFI_EVENT = "wifi";
const esp_event_base_t IP_EVENT = "ip";
static esp_event_handler_t wifi_handler, ip_handler, drain_handler;
static esp_netif_t default_station;
static int64_t clock_ms;
static uint32_t posted_drain, live_ip;
static unsigned post_failures, connects, stops, deinits;
static bool sdk_initialized, sdk_started, start_fails;
static esp_err_t stop_error, deinit_error;

static void host_wait(TickType_t ticks) { clock_ms += ticks; }
int64_t esp_timer_get_time(void) { return clock_ms * 1000; }
uint32_t esp_random(void) { return 0; }
esp_err_t nvs_flash_init(void) { return ESP_OK; }
esp_err_t esp_netif_init(void) { return ESP_OK; }
esp_err_t esp_event_loop_create_default(void) { return ESP_OK; }
esp_netif_t *esp_netif_get_handle_from_ifkey(const char *key) { (void)key; return NULL; }
esp_netif_t *esp_netif_create_default_wifi_sta(void) { return &default_station; }
void esp_netif_destroy_default_wifi(esp_netif_t *netif) { assert(netif == &default_station); }
esp_err_t esp_netif_get_ip_info(esp_netif_t *netif, esp_netif_ip_info_t *current)
{
    assert(netif == &default_station);
    current->ip.addr = live_ip;
    return ESP_OK;
}
esp_err_t esp_netif_sntp_init(const esp_sntp_config_t *config) { assert(config->server); return ESP_OK; }
void esp_netif_sntp_deinit(void) {}
esp_err_t esp_netif_sntp_sync_wait(TickType_t ticks) { assert(!ticks); return ESP_OK; }
void mbedtls_platform_zeroize(void *buffer, size_t size) { memset(buffer, 0, size); }

esp_err_t esp_event_handler_instance_register(esp_event_base_t base, int32_t id,
    esp_event_handler_t callback, void *arg, esp_event_handler_instance_t *instance)
{
    assert(!arg);
    if (base == WIFI_EVENT) { wifi_handler = callback; }
    else if (base == IP_EVENT) { ip_handler = callback; }
    else {
        assert(base == H08_EVENT && id == 0);
        drain_handler = callback;
    }
    *instance = &default_station;
    return ESP_OK;
}

esp_err_t esp_event_handler_instance_unregister(esp_event_base_t base, int32_t id,
                                                esp_event_handler_instance_t instance)
{
    (void)base;
    (void)id;
    (void)instance;
    return ESP_OK;
}

esp_err_t esp_event_post(esp_event_base_t base, int32_t id, const void *data, size_t size, TickType_t wait)
{
    assert(base == H08_EVENT && id == 0 && size == sizeof(posted_drain) && !wait);
    if (post_failures) { --post_failures; return ESP_ERR_TIMEOUT; }
    memcpy(&posted_drain, data, size);
    return ESP_OK;
}

esp_err_t esp_wifi_get_mode(wifi_mode_t *mode)
{
    *mode = WIFI_MODE_STA;
    return sdk_initialized ? ESP_OK : ESP_ERR_WIFI_NOT_INIT;
}

esp_err_t esp_wifi_init(const wifi_init_config_t *config)
{
    assert(!sdk_initialized && !config->nvs_enable);
    sdk_initialized = true;
    return ESP_OK;
}
esp_err_t esp_wifi_set_storage(int storage) { assert(storage == WIFI_STORAGE_RAM); return ESP_OK; }
esp_err_t esp_wifi_set_mode(wifi_mode_t mode) { assert(mode == WIFI_MODE_STA); return ESP_OK; }
esp_err_t esp_wifi_set_config(int interface, wifi_config_t *config)
{
    assert(interface == WIFI_IF_STA && config->sta.ssid[0]);
    return ESP_OK;
}
esp_err_t esp_wifi_start(void)
{
    assert(sdk_initialized && !sdk_started);
    sdk_started = true;
    wifi_handler(NULL, WIFI_EVENT, WIFI_EVENT_STA_START, NULL);
    return start_fails ? ESP_FAIL : ESP_OK;
}
esp_err_t esp_wifi_stop(void)
{
    ++stops;
    if (stop_error == ESP_ERR_WIFI_NOT_STARTED || stop_error == ESP_OK) {
        sdk_started = false;
        live_ip = 0;
    }
    return stop_error;  /* Deliberately never emits STA_STOP. */
}
esp_err_t esp_wifi_deinit(void)
{
    assert(!sdk_started && sdk_initialized);
    ++deinits;
    if (deinit_error == ESP_OK) { sdk_initialized = false; }
    return deinit_error;
}
esp_err_t esp_wifi_connect(void) { assert(sdk_started); ++connects; return ESP_OK; }
esp_err_t esp_wifi_disconnect(void) { live_ip = 0; return ESP_OK; }
esp_err_t esp_wifi_sta_get_ap_info(wifi_ap_record_t *ap) { ap->rssi = -42; return ESP_OK; }

static void got_ip(void)
{
    live_ip = UINT32_C(0x0101a8c0);
    wifi_handler(NULL, WIFI_EVENT, WIFI_EVENT_STA_CONNECTED, NULL);
    ip_event_got_ip_t ip = {.esp_netif = &default_station, .ip_info.ip.addr = live_ip};
    ip_handler(NULL, IP_EVENT, IP_EVENT_STA_GOT_IP, &ip);
    device_wifi_poll();
    assert(device_wifi_online() && device_wifi_info().state == DEVICE_WIFI_IP);
}

static void lose_ap(void)
{
    live_ip = 0;
    wifi_event_sta_disconnected_t event = {.reason = 201};
    wifi_handler(NULL, WIFI_EVENT, WIFI_EVENT_STA_DISCONNECTED, &event);
    assert(!device_wifi_online());
    device_wifi_poll();
}

static void ack(uint32_t marker)
{
    drain_handler(NULL, H08_EVENT, 0, &marker);
    device_wifi_poll();
}

static void join_ap(void)
{
    const unsigned before = connects;
    assert(device_wifi_join("mock-ap", "") == ESP_OK);
    device_wifi_poll();
    assert(connects == before + 1 && !device_wifi_online());
    got_ip();
}

static void queue_overflow(void)
{
    join_ap();
    const unsigned before = connects;
    for (unsigned i = 0; i < 17; ++i) {
        wifi_handler(NULL, WIFI_EVENT, WIFI_EVENT_STA_START, NULL);
    }
    post_failures = 2;
    device_wifi_poll();
    assert(!sdk_initialized && !device_wifi_online() && device_wifi_info().error == ESP_ERR_NO_MEM);
    assert(device_wifi_join("mock-ap", "") == ESP_ERR_INVALID_STATE);
    host_wait(DRAIN_POST_MS);
    device_wifi_poll();
    host_wait(DRAIN_POST_MS);
    device_wifi_poll();
    assert(posted_drain && connects == before);
    wifi_handler(NULL, WIFI_EVENT, WIFI_EVENT_STA_START, NULL);
    wifi_handler(NULL, WIFI_EVENT, WIFI_EVENT_STA_STOP, NULL);
    ack(posted_drain);
    join_ap();
    assert(connects == before + 1);
    puts("PASS overflow, dropped STA_STOP, busy default loop, obsolete event queue drained and rejoin");
}

static void failed_cleanup(void)
{
    const unsigned before = connects;
    stop_error = ESP_FAIL;
    assert(device_wifi_stop() == ESP_FAIL && !device_wifi_online() && sdk_started);
    wifi_handler(NULL, WIFI_EVENT, WIFI_EVENT_STA_START, NULL);
    device_wifi_poll();
    assert(connects == before && device_wifi_join("mock-ap", "") == ESP_ERR_INVALID_STATE);
    stop_error = ESP_ERR_WIFI_NOT_STARTED;
    deinit_error = ESP_FAIL;
    assert(device_wifi_stop() == ESP_FAIL && sdk_initialized && !sdk_started);
    assert(device_wifi_join("mock-ap", "") == ESP_ERR_INVALID_STATE);
    const unsigned previous_stops = stops;
    deinit_error = stop_error = ESP_OK;
    assert(device_wifi_stop() == ESP_OK && stops == previous_stops && !sdk_initialized);
    ack(posted_drain);
    assert(device_wifi_info().state == DEVICE_WIFI_STOPPED);
    assert(device_wifi_stop() == ESP_OK && stops == previous_stops);
    join_ap();
    puts("PASS stop and deinit failures keep ownership, block reconnect, and permit cleanup retry");
}

static void late_marker_and_start_failure(void)
{
    assert(device_wifi_stop() == ESP_OK && device_wifi_info().state == DEVICE_WIFI_DRAINING);
    const uint32_t late = posted_drain;
    host_wait(DRAIN_MS);
    device_wifi_poll();
    assert(device_wifi_info().error == ESP_ERR_TIMEOUT);
    assert(device_wifi_join("mock-ap", "") == ESP_ERR_TIMEOUT);
    assert(posted_drain != late);
    ack(late);
    assert(device_wifi_join("mock-ap", "") == ESP_ERR_INVALID_STATE);
    ack(posted_drain);
    assert(device_wifi_info().state == DEVICE_WIFI_STOPPED);

    start_fails = true;
    const unsigned before = connects;
    assert(device_wifi_join("mock-ap", "") == ESP_FAIL);
    device_wifi_poll();
    assert(!sdk_started && !sdk_initialized && connects == before);
    ack(posted_drain);
    start_fails = false;
    join_ap();
    puts("PASS missing marker times out, fresh marker permits rejoin, partial start is cleaned");
}

static void reconnect(void)
{
    const unsigned before = connects;
    ip_handler(NULL, IP_EVENT, IP_EVENT_STA_LOST_IP, NULL);  /* Obsolete event, IPv4 is current. */
    device_wifi_poll();
    assert(device_wifi_online());
    lose_ap();
    assert(device_wifi_info().state == DEVICE_WIFI_BACKOFF && device_wifi_info().retries == 1);
    host_wait(499);
    device_wifi_poll();
    assert(connects == before);
    host_wait(1);
    device_wifi_poll();
    assert(connects == before + 1);
    got_ip();
    assert(device_wifi_info().retries == 0);
    for (unsigned i = 0; i < RETRIES_MAX; ++i) {
        lose_ap();
        host_wait(500U << i);
        device_wifi_poll();
    }
    lose_ap();
    host_wait(60000);
    device_wifi_poll();
    assert(!device_wifi_online() && device_wifi_info().state == DEVICE_WIFI_DISCONNECTED);
    assert(connects == before + 1 + RETRIES_MAX);
    puts("PASS stale lost-IP ignored, disconnect reconnects once after backoff, attempts stay bounded");
}

int main(void)
{
    assert(device_wifi_init() == ESP_OK);
    queue_overflow();
    failed_cleanup();
    late_marker_and_start_failure();
    reconnect();
    assert(deinits > 0 && device_wifi_stop() == ESP_OK);
    ack(posted_drain);
    vQueueDelete(events);
    return 0;
}
