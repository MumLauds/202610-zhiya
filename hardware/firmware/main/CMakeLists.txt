idf_component_register(
    SRCS "app_main.c"
    PRIV_REQUIRES esp_hw_support esp_psram esp_system log spi_flash
)
