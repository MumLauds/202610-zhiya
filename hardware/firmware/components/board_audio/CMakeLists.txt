idf_component_register(
    SRCS "board_audio.c" "audio_hw.c" "audio_pcm.c"
    INCLUDE_DIRS "include"
    PRIV_REQUIRES board_4_3c esp_driver_i2s esp_timer heap
)
