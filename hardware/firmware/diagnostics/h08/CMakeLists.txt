cmake_minimum_required(VERSION 3.16)

get_filename_component(ZHIYA_FIRMWARE "${CMAKE_CURRENT_LIST_DIR}/../.." ABSOLUTE)
set(EXTRA_COMPONENT_DIRS "${ZHIYA_FIRMWARE}/components/board_4_3c" "${ZHIYA_FIRMWARE}/components/device_api")
set(SDKCONFIG_DEFAULTS "${ZHIYA_FIRMWARE}/sdkconfig.defaults;${CMAKE_CURRENT_LIST_DIR}/sdkconfig.h08")
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
idf_build_set_property(MINIMAL_BUILD ON)
project(zhiya_h08)

idf_build_get_property(idf_version IDF_VER)
if(NOT IDF_TARGET STREQUAL "esp32s3" OR NOT idf_version STREQUAL "v5.5.5")
    message(FATAL_ERROR "H08 requires ESP32-S3 and ESP-IDF v5.5.5")
endif()
