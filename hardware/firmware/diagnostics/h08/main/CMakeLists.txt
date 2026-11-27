get_filename_component(board_main "${CMAKE_CURRENT_LIST_DIR}/../../../main" ABSOLUTE)
idf_component_register(SRCS "diagnostics.c" "${board_main}/app_main.c" "${board_main}/h02_test_ui.c"
    "${board_main}/h02_title_font.c"
    PRIV_REQUIRES device_api board_4_3c esp_hw_support esp_psram esp_system log spi_flash
                  esp_driver_usb_serial_jtag heap mbedtls lwip)
# Reuse the verified baseline boot path without editing the shared app_main.c.
set_source_files_properties("${board_main}/app_main.c" PROPERTIES COMPILE_DEFINITIONS "app_main=h08_board_boot")

if(ZHIYA_H08_DEMO_CA)
    file(SIZE "${ZHIYA_H08_DEMO_CA}" ca_size)
    if(ca_size GREATER 8192)
        message(FATAL_ERROR "H08 demo CA must be at most 8192 bytes")
    endif()
    file(READ "${ZHIYA_H08_DEMO_CA}" pem)
    string(REGEX MATCHALL "-----BEGIN [A-Z ]+-----" pem_kinds "${pem}")
    if(NOT pem_kinds STREQUAL "-----BEGIN CERTIFICATE-----")
        message(FATAL_ERROR "Provide one public CA certificate PEM. Never embed a private key.")
    endif()
    set(demo_ca "${CMAKE_CURRENT_BINARY_DIR}/h08_demo_ca.pem")
    configure_file("${ZHIYA_H08_DEMO_CA}" "${demo_ca}" COPYONLY)
    target_add_binary_data(${COMPONENT_LIB} "${demo_ca}" TEXT)
    target_compile_definitions(${COMPONENT_LIB} PRIVATE ZHIYA_H08_HAVE_DEMO_CA=1)
endif()
