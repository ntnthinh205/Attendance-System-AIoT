#pragma once
#ifdef __cplusplus
extern "C" {
#endif
void hybrid_recognition_start(void);
void hybrid_recognition_submit(const float *embedding, int dimensions);
#ifdef __cplusplus
}
#endif
