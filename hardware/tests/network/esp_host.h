#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>
#include <assert.h>

/* The firmware translation units are compiled unchanged. These definitions
 * stand in for the SDK APIs exercised by the host tests. Time is in ms. */
typedef int esp_err_t;
typedef unsigned TickType_t;
enum {
    ESP_OK, ESP_FAIL, ESP_ERR_INVALID_STATE, ESP_ERR_NO_MEM, ESP_ERR_INVALID_ARG, ESP_ERR_TIMEOUT,
    ESP_ERR_WIFI_NOT_INIT, ESP_ERR_WIFI_NOT_STARTED, ESP_ERR_ESP_TLS_CONNECTION_TIMEOUT,
    ESP_ERR_MBEDTLS_SSL_HANDSHAKE_FAILED,
};
#define pdPASS 1
#define portMAX_DELAY UINT32_MAX
#define pdMS_TO_TICKS(ms) (ms)

typedef struct {
    size_t capacity, item_size, count, head;
    unsigned char *items;
} host_queue_t;
typedef host_queue_t *QueueHandle_t;

static void host_wait(TickType_t ticks);

static inline QueueHandle_t xQueueCreate(size_t capacity, size_t item_size)
{
    QueueHandle_t queue = calloc(1, sizeof(*queue));
    assert(queue);
    queue->items = calloc(capacity, item_size);
    assert(queue->items);
    queue->capacity = capacity;
    queue->item_size = item_size;
    return queue;
}

static inline int xQueueReset(QueueHandle_t queue)
{
    queue->count = queue->head = 0;
    return pdPASS;
}

static inline int xQueueSend(QueueHandle_t queue, const void *item, TickType_t wait)
{
    assert(!wait);
    if (queue->count == queue->capacity) { return 0; }
    size_t index = (queue->head + queue->count++) % queue->capacity;
    memcpy(queue->items + index * queue->item_size, item, queue->item_size);
    return pdPASS;
}

static inline int xQueueOverwrite(QueueHandle_t queue, const void *item)
{
    assert(queue->capacity == 1);
    xQueueReset(queue);
    return xQueueSend(queue, item, 0);
}

static inline int xQueueReceive(QueueHandle_t queue, void *item, TickType_t wait)
{
    if (!queue->count && wait) { host_wait(wait); }
    if (!queue->count) { return 0; }
    memcpy(item, queue->items + queue->head * queue->item_size, queue->item_size);
    queue->head = (queue->head + 1) % queue->capacity;
    --queue->count;
    return pdPASS;
}

static inline void vQueueDelete(QueueHandle_t queue)
{
    free(queue->items);
    free(queue);
}

int64_t esp_timer_get_time(void);

#ifdef HOST_WIFI
typedef const char *esp_event_base_t;
typedef void *esp_event_handler_instance_t;
typedef void (*esp_event_handler_t)(void *, esp_event_base_t, int32_t, void *);
#define ESP_EVENT_DEFINE_BASE(name) static const esp_event_base_t name = #name
#define ESP_EVENT_ANY_ID (-1)
extern const esp_event_base_t WIFI_EVENT, IP_EVENT;
enum {
    WIFI_EVENT_STA_START, WIFI_EVENT_STA_CONNECTED, WIFI_EVENT_STA_DISCONNECTED, WIFI_EVENT_STA_STOP,
    IP_EVENT_STA_GOT_IP, IP_EVENT_STA_LOST_IP,
    WIFI_STORAGE_RAM, WIFI_MODE_STA, WIFI_IF_STA, WIFI_AUTH_WPA2_PSK, WIFI_AUTH_OPEN,
};
typedef struct { uint8_t reason; } wifi_event_sta_disconnected_t;
typedef struct { int unused; } esp_netif_t;
typedef struct { struct { uint32_t addr; } ip; } esp_netif_ip_info_t;
typedef struct { esp_netif_t *esp_netif; esp_netif_ip_info_t ip_info; } ip_event_got_ip_t;
typedef struct { bool nvs_enable; } wifi_init_config_t;
typedef struct { int rssi; } wifi_ap_record_t;
typedef int wifi_mode_t;
typedef union {
    unsigned unused;
    struct { uint8_t ssid[32], password[64]; struct { int authmode; } threshold; } sta;
} wifi_config_t;
typedef struct { const char *server; } esp_sntp_config_t;
#define WIFI_INIT_CONFIG_DEFAULT() { .nvs_enable = true }
#define ESP_NETIF_SNTP_DEFAULT_CONFIG(host) { .server = (host) }
esp_err_t nvs_flash_init(void);
esp_err_t esp_netif_init(void);
esp_err_t esp_event_loop_create_default(void);
esp_err_t esp_wifi_get_mode(wifi_mode_t *);
esp_netif_t *esp_netif_get_handle_from_ifkey(const char *);
esp_netif_t *esp_netif_create_default_wifi_sta(void);
void esp_netif_destroy_default_wifi(esp_netif_t *);
esp_err_t esp_netif_get_ip_info(esp_netif_t *, esp_netif_ip_info_t *);
esp_err_t esp_event_handler_instance_register(esp_event_base_t, int32_t, esp_event_handler_t, void *,
                                             esp_event_handler_instance_t *);
esp_err_t esp_event_handler_instance_unregister(esp_event_base_t, int32_t, esp_event_handler_instance_t);
esp_err_t esp_event_post(esp_event_base_t, int32_t, const void *, size_t, TickType_t);
esp_err_t esp_wifi_stop(void);
esp_err_t esp_wifi_deinit(void);
esp_err_t esp_wifi_init(const wifi_init_config_t *);
esp_err_t esp_wifi_set_storage(int);
esp_err_t esp_wifi_set_mode(wifi_mode_t);
esp_err_t esp_wifi_set_config(int, wifi_config_t *);
esp_err_t esp_wifi_start(void);
esp_err_t esp_wifi_connect(void);
esp_err_t esp_wifi_disconnect(void);
esp_err_t esp_wifi_sta_get_ap_info(wifi_ap_record_t *);
esp_err_t esp_netif_sntp_init(const esp_sntp_config_t *);
void esp_netif_sntp_deinit(void);
esp_err_t esp_netif_sntp_sync_wait(TickType_t);
uint32_t esp_random(void);
void mbedtls_platform_zeroize(void *, size_t);
#endif

#ifdef HOST_TRANSPORT
#define CONFIG_MBEDTLS_HAVE_TIME_DATE 1
#define CONFIG_MBEDTLS_CERTIFICATE_BUNDLE 1
#define ESP_TLS_ERR_SSL_WANT_READ (-2)
#define ESP_TLS_ERR_SSL_WANT_WRITE (-3)
#define AF_INET 2
#define SOCK_STREAM 1
#define INET_ADDRSTRLEN 16
#ifndef O_NONBLOCK
#define O_NONBLOCK 0x100
#endif
#ifndef F_SETFL
#define F_SETFL 4
#endif
struct in_addr { uint32_t s_addr; };
struct sockaddr_in { struct in_addr sin_addr; };
struct addrinfo { int ai_family, ai_socktype; void *ai_addr; };
int inet_pton(int, const char *, void *);
const char *inet_ntop(int, const void *, char *, size_t);
uint32_t ntohl(uint32_t);
int fcntl(int, int, ...);
int getaddrinfo(const char *, const char *, const struct addrinfo *, struct addrinfo **);
void freeaddrinfo(struct addrinfo *);
static inline void vTaskDelay(TickType_t ticks) { host_wait(ticks); }
int xTaskCreate(void (*)(void *), const char *, unsigned, void *, int, void *);

enum { ESP_TLS_AF_INET = AF_INET };
typedef enum { ESP_TLS_CONNECTING, ESP_TLS_HANDSHAKE } esp_tls_conn_state_t;
typedef struct esp_tls esp_tls_t;
typedef esp_tls_t *esp_tls_error_handle_t;
typedef struct {
    bool is_plain_tcp, non_block;
    int timeout_ms, addr_family;
    const char *common_name;
    const unsigned char *cacert_buf;
    size_t cacert_bytes;
    esp_err_t (*crt_bundle_attach)(void *);
} esp_tls_cfg_t;
esp_err_t esp_crt_bundle_attach(void *);
esp_tls_t *esp_tls_init(void);
int esp_tls_conn_new_async(const char *, int, int, const esp_tls_cfg_t *, esp_tls_t *);
int esp_tls_conn_new_sync(const char *, int, int, const esp_tls_cfg_t *, esp_tls_t *);
esp_err_t esp_tls_get_conn_state(esp_tls_t *, esp_tls_conn_state_t *);
esp_err_t esp_tls_get_conn_sockfd(esp_tls_t *, int *);
esp_err_t esp_tls_get_error_handle(esp_tls_t *, esp_tls_error_handle_t *);
esp_err_t esp_tls_get_and_clear_last_error(esp_tls_error_handle_t, int *, int *);
ssize_t esp_tls_conn_read(esp_tls_t *, void *, size_t);
ssize_t esp_tls_conn_write(esp_tls_t *, const void *, size_t);
int esp_tls_conn_destroy(esp_tls_t *);
#endif
