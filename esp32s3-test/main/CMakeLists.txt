set(HYBRID_GENERATED "${CMAKE_CURRENT_BINARY_DIR}/hybrid_generated")
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
    "${PROJECT_DIR}/faces.json" "${PROJECT_DIR}/edge_config.json"
    "${PROJECT_DIR}/tools/prepare_hybrid.py" "${PROJECT_DIR}/hybrid_gallery.py" "${PROJECT_DIR}/face_quality.json")
execute_process(COMMAND "${PYTHON}" "${PROJECT_DIR}/tools/prepare_hybrid.py"
    --database "${PROJECT_DIR}/faces.json" --config "${PROJECT_DIR}/edge_config.json" --quality "${PROJECT_DIR}/face_quality.json" --output "${HYBRID_GENERATED}"
    RESULT_VARIABLE HYBRID_RESULT)
if(NOT HYBRID_RESULT EQUAL 0)
    message(FATAL_ERROR "Cannot prepare hybrid gallery/configuration")
endif()
idf_component_register(
    SRCS "app_main.c" "camera_stream.c" "face_embedding.cpp" "hybrid_recognition.cpp"
    PRIV_REQUIRES esp_timer esp_driver_usb_serial_jtag mbedtls esp_psram vfs
                  esp_wifi esp_event esp_netif esp_http_client nvs_flash espressif__cjson lwip esp_hw_support
    INCLUDE_DIRS "." "${HYBRID_GENERATED}"
    EMBED_FILES "${HYBRID_GENERATED}/edge_gallery.bin"
)
