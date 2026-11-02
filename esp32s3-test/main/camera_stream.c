#include <stdbool.h>
#include "face_embedding.h"
#include "camera_stream.h"
#include <stdio.h>
#include <stdlib.h>
#include "mbedtls/base64.h"
#include "driver/usb_serial_jtag.h"
#include "driver/usb_serial_jtag_vfs.h"
#include "esp_camera.h"
#include "esp_log.h"
#include "esp_psram.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "camera_stream";

void camera_stream_run(void)
{
    usb_serial_jtag_driver_config_t usb_config = {
        .tx_buffer_size = 16384, .rx_buffer_size = 256,
    };
    if (!usb_serial_jtag_is_driver_installed()) {
        ESP_ERROR_CHECK(usb_serial_jtag_driver_install(&usb_config));
    }
    usb_serial_jtag_vfs_use_driver();
    // Freenove/S3-EYE pinout verified on the connected OV3660 board.
    // https://docs.freenove.com/projects/fnk0102/en/latest/fnk0102/codes/Main/Preface.html
    const camera_config_t config = {
        .pin_pwdn = -1, .pin_reset = -1,
        .pin_xclk = 15, .pin_sccb_sda = 4, .pin_sccb_scl = 5,
        .pin_d0 = 11, .pin_d1 = 9, .pin_d2 = 8, .pin_d3 = 10,
        .pin_d4 = 12, .pin_d5 = 18, .pin_d6 = 17, .pin_d7 = 16,
        .pin_vsync = 6, .pin_href = 7, .pin_pclk = 13,
        .xclk_freq_hz = 20000000,
        .ledc_timer = LEDC_TIMER_0, .ledc_channel = LEDC_CHANNEL_0,
        .pixel_format = PIXFORMAT_JPEG, .frame_size = FRAMESIZE_QVGA,
        .jpeg_quality = 12, .fb_count = 2,
        .fb_location = CAMERA_FB_IN_PSRAM,
        .grab_mode = CAMERA_GRAB_LATEST,
    };
    ESP_LOGI(TAG, "Initializing camera for face embedding");
    if (!esp_psram_is_initialized()) {
        ESP_LOGE(TAG, "PSRAM not initialized");
        return;
    }
    ESP_LOGI(TAG, "PSRAM: %u bytes", (unsigned)esp_psram_get_size());
    esp_err_t err = esp_camera_init(&config);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Camera init failed: %s. Check ribbon and board pinout.", esp_err_to_name(err));
        return;
    }
    // Discard initial frames while exposure settles.
    for (unsigned i = 0; i < 5; ++i) {
        camera_fb_t *fb = esp_camera_fb_get();
        if (fb) esp_camera_fb_return(fb);
        vTaskDelay(pdMS_TO_TICKS(100));
    }
    ESP_LOGI(TAG, "USB preview starting; open camera_viewer.py on computer");
    face_embedding_start();
    unsigned char *encoded = NULL;
    size_t allocated = 0;
    while (true) {
        camera_fb_t *fb = esp_camera_fb_get();
        if (fb) {
            face_embedding_submit(fb);
            size_t capacity = 4 * ((fb->len + 2) / 3) + 1;
            if (capacity > allocated) {
                unsigned char *larger = realloc(encoded, capacity);
                if (!larger) {
                    esp_camera_fb_return(fb);
                    vTaskDelay(pdMS_TO_TICKS(20));
                    continue;
                }
                encoded = larger;
                allocated = capacity;
            }
            size_t length = 0;
            if (encoded && mbedtls_base64_encode(encoded, capacity, &length,
                                                 fb->buf, fb->len) == 0) {
                printf("\n@CAMERA:%.*s\n", (int)length, (char *)encoded);
                fflush(stdout);
            }

            esp_camera_fb_return(fb);
        }
        vTaskDelay(1);
    }
}
