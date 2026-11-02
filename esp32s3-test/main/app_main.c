#include "camera_stream.h"
#include "hybrid_recognition.h"

void app_main(void)
{
    hybrid_recognition_start();
    camera_stream_run();
}
