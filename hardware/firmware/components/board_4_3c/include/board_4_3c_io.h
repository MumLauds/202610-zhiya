#pragma once

#include "driver/i2c_master.h"

/* Borrow only after display init succeeds. The caller owns its devices, not the bus. */
i2c_master_bus_handle_t board_4_3c_borrow_i2c(void);
esp_err_t board_4_3c_set_audio_amp(bool enabled);
