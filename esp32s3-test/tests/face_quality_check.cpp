#include "face_quality.h"
#include <array>
#include <string>
#include <cassert>
#include <cmath>
int main() {
    face_quality::Config config;
    std::array<int,4> box={80,40,240,220};
    std::array<int,10> frontal={120,90,130,160,160,125,200,90,190,160};
    auto check=[&](const auto &b,const auto &p,float score=.99f) { return face_quality::evaluate(b.data(),b.size(),p.data(),p.size(),score,320,240,config); };
    auto accepted=check(box,frontal);
    assert(accepted.accepted && std::string(accepted.status)=="ok");
    assert(std::fabs(accepted.nose_height_ratio-.5f)<.001f);
    std::array<int,4> small={130,90,190,150};
    assert(std::string(check(small,frontal).status)=="too_far");
    auto crop=box; crop[0]=0;
    assert(std::string(check(crop,frontal).status)=="cropped");
    auto turned=frontal; turned[4]=185;
    assert(std::string(check(box,turned).status)=="turned");
    auto pitch=frontal; pitch[5]=95;
    assert(std::string(check(box,pitch).status)=="pitch");
    pitch[5]=155;
    assert(std::string(check(box,pitch).status)=="pitch");
    auto tilt=frontal;
    for (size_t i=0;i<tilt.size();i+=2) {
        float x=frontal[i]-160,y=frontal[i+1]-125,angle=20*3.14159265/180;
        tilt[i]=std::lround(160+x*std::cos(angle)-y*std::sin(angle));
        tilt[i+1]=std::lround(125+x*std::sin(angle)+y*std::cos(angle));
    }
    assert(std::string(check(box,tilt).status)=="tilted");
    assert(std::string(check(box,frontal,.5f).status)=="low_confidence");
    auto bad=frontal; bad[0]=-10;
    assert(std::string(check(box,bad).status)=="invalid_landmarks");
    bad=frontal; bad[6]=bad[0];
    assert(!check(box,bad).accepted);
    assert(!face_quality::evaluate(box.data(),box.size(),frontal.data(),8,.99f,320,240,config).accepted);
    config.min_width_ratio=.55f;
    assert(std::string(check(box,frontal).status)=="too_far");
    return 0;
}
