#include "face_embedding.h"
#include "hybrid_recognition.h"
#include "face_quality_settings.h"
#include <cmath>
#include <cstdio>
#include <cstring>
#include "human_face_detect.hpp"
#include "human_face_recognition.hpp"
#include "img_converters.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"

namespace {
struct Frame { uint8_t *jpeg; size_t len; uint16_t width; uint16_t height; };
QueueHandle_t queue = nullptr;
void worker(void *)
{
    HumanFaceDetect detector(HumanFaceDetect::MSRMNP_S8_V1, false);
    HumanFaceFeat model(HumanFaceFeat::MBF_S8_V1, false);
    ESP_LOGI("face_ai", "MBF_S8_V1 READY dimensions=%d free_psram=%u", model.get_feat_len(),
             (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM));
    auto *rgb = static_cast<uint8_t *>(heap_caps_malloc(320 * 240 * 3, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    if (!rgb) {
        ESP_LOGE("face_ai", "RGB allocation failed");
        // Drain submitted frames so the preview can continue without leaks.
        Frame frame;
        while (true) { if (xQueueReceive(queue, &frame, portMAX_DELAY)) free(frame.jpeg); }
    }
    Frame frame;
    while (true) {
        if (!xQueueReceive(queue, &frame, portMAX_DELAY)) continue;
        const int64_t start = esp_timer_get_time();
        bool decoded = fmt2rgb888(frame.jpeg, frame.len, PIXFORMAT_JPEG, rgb);
        free(frame.jpeg);
        if (!decoded) { ESP_LOGW("face_ai", "JPEG decode failed"); continue; }
        dl::image::img_t img = {.data = rgb, .width = frame.width, .height = frame.height,
                               .pix_type = dl::image::DL_IMAGE_PIX_TYPE_RGB888};
        auto &faces = detector.run(img);
        const int64_t detected = esp_timer_get_time();
        // Require exactly one person to avoid associating an embedding with the wrong person.
        dl::TensorBase *feat = nullptr;
        face_quality::Result quality;
        if (faces.size() > 1) quality.status="multiple_faces";
        if (faces.size() == 1) {
            const auto &face=faces.front();
            quality=face_quality::evaluate(face.box.data(), face.box.size(), face.keypoint.data(), face.keypoint.size(),
                                           face.score, frame.width, frame.height, FACE_QUALITY_CONFIG);
            if (quality.accepted) feat=model.run(img, face.keypoint);
        }
        const int64_t finished = esp_timer_get_time();
        float norm2 = 0;
        if (feat) {
            const float *values = feat->get_element_ptr<float>();
            for (int i = 0; i < feat->get_size(); ++i) norm2 += values[i] * values[i];
            if (!std::isfinite(norm2) || norm2 < 0.9f || norm2 > 1.1f) feat = nullptr;
        }
        if (quality.accepted && !feat) { quality.accepted=false; quality.status="embedding_failed"; }
        hybrid_recognition_submit(feat ? feat->get_element_ptr<float>() : nullptr, feat ? feat->get_size() : 0);
        // Keep the JSON record atomic relative to camera JPEG records.
        flockfile(stdout);
        printf("\n@FACE:{\"model\":\"MBF_S8_V1\",\"faces\":%u,\"detect_ms\":%.1f,\"embedding_ms\":%.1f,\"dimensions\":%d,\"embedding\":[",
               (unsigned)faces.size(), (detected-start)/1000., (finished-detected)/1000., feat ? feat->get_size() : 0);
        if (feat) {
            const float *values = feat->get_element_ptr<float>();
            for (int i = 0; i < feat->get_size(); ++i) printf("%s%.6f", i ? "," : "", values[i]);
        }
        printf("],\"image_width\":%u,\"image_height\":%u,\"boxes\":[", frame.width, frame.height);
        bool first = true;
        for (const auto &face : faces) {
            if (face.box.size() < 4) continue;
            printf("%s{\"x1\":%d,\"y1\":%d,\"x2\":%d,\"y2\":%d,\"score\":%.3f}",
                   first ? "" : ",", face.box[0], face.box[1], face.box[2], face.box[3], face.score);
            first = false;
        }
        printf("],\"quality\":{\"accepted\":%s,\"status\":\"%s\",\"width_ratio\":%.3f,\"height_ratio\":%.3f,\"roll_degrees\":%.1f,\"yaw_ratio\":%.3f,\"nose_height_ratio\":%.3f,"
               "\"min_width_ratio\":%.3f,\"min_height_ratio\":%.3f,\"max_roll_degrees\":%.1f}}\n",
               quality.accepted ? "true":"false", quality.status, quality.width_ratio, quality.height_ratio,
               quality.roll_degrees, quality.yaw_ratio, quality.nose_height_ratio,
               FACE_QUALITY_CONFIG.min_width_ratio, FACE_QUALITY_CONFIG.min_height_ratio, FACE_QUALITY_CONFIG.max_roll_degrees);
        fflush(stdout);
        funlockfile(stdout);
    }
}
}
extern "C" void face_embedding_start(void)
{
    queue = xQueueCreate(1, sizeof(Frame));
    if (!queue) { ESP_LOGE("face_ai", "Queue allocation failed"); return; }
    if (xTaskCreatePinnedToCore(worker, "face_embedding", 12288, nullptr, 4, nullptr, 1) != pdPASS) {
        vQueueDelete(queue); queue = nullptr;
        ESP_LOGE("face_ai", "Task allocation failed");
    }
}
extern "C" void face_embedding_submit(const camera_fb_t *fb)
{
    static int64_t last = 0;
    const int64_t now = esp_timer_get_time();
    if (!queue || now - last < 1000000 || fb->width != 320 || fb->height != 240 ||
        fb->format != PIXFORMAT_JPEG || uxQueueMessagesWaiting(queue)) return;
    last = now;
    Frame frame{static_cast<uint8_t *>(heap_caps_malloc(fb->len, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT)),
                fb->len, (uint16_t)fb->width, (uint16_t)fb->height};
    if (!frame.jpeg) return;
    memcpy(frame.jpeg, fb->buf, fb->len);
    if (xQueueSend(queue, &frame, 0) != pdTRUE) free(frame.jpeg);
}
