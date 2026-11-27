#pragma once

#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"

typedef enum {
    DEVICE_WIFI_UNCONFIGURED, DEVICE_WIFI_CONNECTING, DEVICE_WIFI_DHCP, DEVICE_WIFI_IP,
    DEVICE_WIFI_DISCONNECTED, DEVICE_WIFI_BACKOFF, DEVICE_WIFI_STOPPED, DEVICE_WIFI_ERROR,
} device_wifi_state_t;

typedef struct {
    device_wifi_state_t state;
    esp_err_t error;
    uint8_t reason;
    unsigned retries;
    uint32_t ip;
    int rssi;
} device_wifi_info_t;

/* Control and poll share one owner. Online state is readable by the HTTP worker. */
esp_err_t device_wifi_init(void);
esp_err_t device_wifi_join(const char *ssid, const char *password);
esp_err_t device_wifi_stop(void);
void device_wifi_poll(void);
bool device_wifi_online(void);
bool device_wifi_clock_ready(void);
device_wifi_info_t device_wifi_info(void);
const char *device_wifi_state_name(device_wifi_state_t state);
