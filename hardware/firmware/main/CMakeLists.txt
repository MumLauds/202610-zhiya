set(main_deps reader_core board_4_3c esp_hw_support esp_psram esp_system log spi_flash esp_timer heap esp_rom)
if(ZHIYA_H06)
    idf_build_get_property(python PYTHON)
    idf_build_get_property(build_dir BUILD_DIR)
    set(pack "${build_dir}/reader_pack")
    set(hardware "${CMAKE_CURRENT_LIST_DIR}/../..")
    set(packer "${hardware}/tools/pack_reader_demo.py")
    set(ZHIYA_H06_BOOKS 3 CACHE STRING "Number of books in the H06 DEMO")
    idf_component_register(SRCS "app_main.c" "reader_ui.c" "reader_media.c" "h06_reader_font.c"
        PRIV_REQUIRES ${main_deps})
    execute_process(COMMAND "${python}" "${packer}" --out "${pack}" --books "${ZHIYA_H06_BOOKS}"
                    COMMAND_ERROR_IS_FATAL ANY)
    file(STRINGS "${pack}/inputs.txt" reader_inputs ENCODING UTF-8)
    set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${packer}" ${reader_inputs}
        "${hardware}/mock/fixtures/storybooks.json"
        "${hardware}/mock/fixtures/story-details.json"
        "${hardware}/mock/fixtures/demo-session.json"
        "${hardware}/assets/generated/assets-manifest.json"
        "${hardware}/assets/generated-pages/pages-manifest.json")
    target_sources(${COMPONENT_LIB} PRIVATE "${pack}/reader_demo.c")
    target_compile_definitions(${COMPONENT_LIB} PRIVATE ZHIYA_H06=1)
    target_add_binary_data(${COMPONENT_LIB} "${pack}/h06_media.bin" BINARY)
else()
    idf_component_register(
        SRCS "app_main.c" "h02_test_ui.c" "h02_title_font.c"
        PRIV_REQUIRES ${main_deps}
    )
endif()
