idf_component_register(
    SRCS "app_main.c" "h02_test_ui.c" "h02_title_font.c" "h07_audio_ui.c" "h07_label_font.c"
    PRIV_REQUIRES board_4_3c board_audio esp_hw_support esp_psram esp_system log spi_flash
)
