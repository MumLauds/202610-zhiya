idf_component_register(
    SRCS "board_4_3c.c" "board_4_3c_panel.c"
    INCLUDE_DIRS "include"
    PRIV_REQUIRES esp_driver_gpio esp_driver_i2c esp_lcd esp_psram esp_timer heap
)
