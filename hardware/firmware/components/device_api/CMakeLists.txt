idf_component_register(SRCS "http.c" "dto.c" "transport.c" "wifi.c"
    INCLUDE_DIRS "include"
    PRIV_REQUIRES http_parser json esp-tls mbedtls esp_netif esp_event esp_wifi nvs_flash esp_timer lwip)
