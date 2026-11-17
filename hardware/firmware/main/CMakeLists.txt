idf_component_register(
    SRCS "app_main.c" "h02_test_ui.c" "h02_title_font.c"
    PRIV_REQUIRES board_4_3c esp_hw_support esp_psram esp_system log spi_flash
)
