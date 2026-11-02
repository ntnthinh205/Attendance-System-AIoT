#pragma once
#include "esp_camera.h"
#ifdef __cplusplus
extern "C" {
#endif
void face_embedding_start(void);
void face_embedding_submit(const camera_fb_t *fb);
#ifdef __cplusplus
}
#endif
