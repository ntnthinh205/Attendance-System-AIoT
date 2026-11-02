#pragma once
#include <algorithm>
#include <cmath>
#include <cstddef>

namespace face_quality {
struct Config {
    float min_width_ratio=.28f, min_height_ratio=.38f, border_ratio=.02f;
    float min_detection_score=.85f, min_eye_distance_ratio=.09f;
    float max_roll_degrees=12.f, max_yaw_ratio=.18f;
    float min_nose_height_ratio=.30f, max_nose_height_ratio=.70f;
};
struct Result {
    const char *status="no_face";
    bool accepted=false;
    float width_ratio=0, height_ratio=0, roll_degrees=0, yaw_ratio=0, nose_height_ratio=0;
};
// Espressif landmark order: left eye, left mouth, nose, right eye, right mouth.
// Frontalness is a 2-D landmark heuristic, not a calibrated 3-D pose/distance estimate.
inline Result evaluate(const int *box, size_t box_count, const int *landmarks, size_t point_count,
                       float detection_score, int width, int height, const Config &config) {
    Result out;
    out.status="invalid_landmarks";
    if (!box || box_count!=4 || !landmarks || point_count!=10 || width<=0 || height<=0 ||
        box[2]<=box[0] || box[3]<=box[1]) return out;
    out.width_ratio=float(box[2]-box[0])/width;
    out.height_ratio=float(box[3]-box[1])/height;
    if (!std::isfinite(detection_score) || detection_score<config.min_detection_score) { out.status="low_confidence"; return out; }
    if (out.width_ratio<config.min_width_ratio || out.height_ratio<config.min_height_ratio) { out.status="too_far"; return out; }
    const float bx=width*config.border_ratio, by=height*config.border_ratio;
    if (box[0]<bx || box[1]<by || box[2]>width-bx || box[3]>height-by) { out.status="cropped"; return out; }
    for (size_t i=0;i<10;i+=2) if (landmarks[i]<box[0] || landmarks[i]>box[2] || landmarks[i+1]<box[1] || landmarks[i+1]>box[3]) return out;
    const float ex=landmarks[6]-landmarks[0], ey=landmarks[7]-landmarks[1];
    const float distance=std::hypot(ex,ey);
    if (ex<=0 || distance<width*config.min_eye_distance_ratio) { out.status="too_far"; return out; }
    out.roll_degrees=std::atan2(ey,ex)*180.f/3.14159265f;
    const float ux=ex/distance, uy=ey/distance;
    const float eye_x=(landmarks[0]+landmarks[6])*.5f, eye_y=(landmarks[1]+landmarks[7])*.5f;
    const float nose_x=landmarks[4]-eye_x, nose_y=landmarks[5]-eye_y;
    const float mouth_x=(landmarks[2]+landmarks[8])*.5f-eye_x, mouth_y=(landmarks[3]+landmarks[9])*.5f-eye_y;
    const float mouth_down=-uy*mouth_x+ux*mouth_y;
    if (mouth_down<distance*.4f || mouth_down>distance*2.f || landmarks[8]<=landmarks[2]) return out;
    // Lateral offset of both nose and mouth from the eye midpoint, relative to eye spacing.
    out.yaw_ratio=std::max(std::fabs(nose_x*ux+nose_y*uy),std::fabs(mouth_x*ux+mouth_y*uy))/distance;
    out.nose_height_ratio=(-uy*nose_x+ux*nose_y)/mouth_down;
    if (std::fabs(out.roll_degrees)>config.max_roll_degrees) { out.status="tilted"; return out; }
    if (out.yaw_ratio>config.max_yaw_ratio) { out.status="turned"; return out; }
    if (out.nose_height_ratio<config.min_nose_height_ratio || out.nose_height_ratio>config.max_nose_height_ratio) { out.status="pitch"; return out; }
    out.status="ok"; out.accepted=true;
    return out;
}
}
