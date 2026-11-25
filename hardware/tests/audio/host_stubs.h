#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>

typedef int esp_err_t;
enum {
    ESP_OK, ESP_ERR_INVALID_STATE, ESP_ERR_INVALID_SIZE, ESP_ERR_NO_MEM,
    ESP_ERR_TIMEOUT, ESP_ERR_INVALID_ARG, ESP_ERR_INVALID_RESPONSE,
};
typedef void *QueueHandle_t;
typedef void *SemaphoreHandle_t;
#define portMAX_DELAY 1000
#define pdTRUE 1
#define pdPASS 1
#define pdMS_TO_TICKS(value) (value)
#define MALLOC_CAP_SPIRAM 1
#define MALLOC_CAP_8BIT 2
static inline void host_log(const char *tag, const char *format, ...) { (void)tag; (void)format; }
static inline const char *esp_err_to_name(esp_err_t error) { (void)error; return ""; }
#define ESP_LOGI(tag, ...) host_log(tag, __VA_ARGS__)
#define ESP_LOGE(tag, ...) ((void)(tag))

static inline int xSemaphoreTake(void *lock, int wait) { return lock && wait; }
static inline void xSemaphoreGive(void *lock) { (void)lock; }
static inline void vSemaphoreDelete(void *lock) { (void)lock; }
static inline void *xSemaphoreCreateMutex(void) { return (void *)1; }
static inline void *xSemaphoreCreateBinary(void) { return (void *)1; }
static inline void *xQueueCreate(size_t count, size_t bytes) { return count && bytes ? (void *)1 : NULL; }
static inline void vQueueDelete(void *queue) { (void)queue; }
static inline void vTaskDelete(void *task) { (void)task; }
static inline void board_4_3c_log_memory(void) {}
static inline int xTaskCreate(void (*task)(void *), const char *name, unsigned stack,
                              void *argument, unsigned priority, void *handle)
{
    (void)argument;
    (void)handle;
    return task && name && stack && priority;
}

int xQueueSend(void *queue, const void *data, int wait);
int xQueueReceive(void *queue, void *data, int wait);
int64_t esp_timer_get_time(void);
void *heap_caps_malloc(size_t bytes, unsigned caps);
