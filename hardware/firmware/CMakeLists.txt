cmake_minimum_required(VERSION 3.16)

include($ENV{IDF_PATH}/tools/cmake/project.cmake)

idf_build_set_property(MINIMAL_BUILD ON)
project(zhiya_hardware)

if(NOT IDF_TARGET STREQUAL "esp32s3")
    message(FATAL_ERROR "Zhiya H01 requires esp32s3. Run: idf.py set-target esp32s3")
endif()

idf_build_get_property(idf_version IDF_VER)
if(NOT idf_version STREQUAL "v5.5.5")
    message(FATAL_ERROR "Zhiya H01 requires ESP-IDF v5.5.5 (found ${idf_version}).")
endif()
