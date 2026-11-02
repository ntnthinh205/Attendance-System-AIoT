#pragma once
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <string>
#include <iterator>
namespace hybrid_core {
constexpr size_t MAX_RECORDS = 250;
#pragma pack(push, 1)
struct Header { char magic[8]; char revision[64]; float threshold; float margin; uint32_t count; };
struct Record { char id[64]; char name[320]; float vector[512]; };
#pragma pack(pop)
static_assert(sizeof(Header) == 84 && sizeof(Record) == 2432);
constexpr size_t MAX_GALLERY = sizeof(Header) + MAX_RECORDS * sizeof(Record);
struct Input { float vector[512]; bool valid; int64_t captured; uint32_t quality_generation; };
struct Result { std::string status="unavailable", id, name, revision, source="edge"; float score=0; };
inline bool valid_gallery(const uint8_t *data, size_t length) {
    if (length < sizeof(Header) || length > MAX_GALLERY) return false;
    const auto *h = reinterpret_cast<const Header *>(data);
    if (memcmp(h->magic,"MBF512V1",8) || h->count>MAX_RECORDS || length!=sizeof(Header)+h->count*sizeof(Record) ||
        !std::isfinite(h->threshold) || h->threshold < -1 || h->threshold > 1 ||
        !std::isfinite(h->margin) || h->margin<0 || h->margin>2) return false;
    for (char c : h->revision) if (!((c>='0' && c<='9') || (c>='a' && c<='f'))) return false;
    const auto *records = reinterpret_cast<const Record *>(data+sizeof(Header));
    unsigned people=0;
    for (uint32_t i=0;i<h->count;++i) {
        const auto &r=records[i];
        if (!r.id[0] || !r.name[0] || !memchr(r.id,0,sizeof(r.id)) || !memchr(r.name,0,sizeof(r.name))) return false;
        float norm=0;
        for (float v:r.vector) { if (!std::isfinite(v)) return false; norm+=v*v; }
        if (norm<.9f || norm>1.1f) return false;
        unsigned samples=1; bool seen=false;
        for (uint32_t j=0;j<i;++j) if (!strcmp(r.id,records[j].id)) { ++samples; seen=true; if (strcmp(r.name,records[j].name)) return false; }
        if (samples>5 || (!seen && ++people>50)) return false;
    }
    return true;
}
inline Result edge_match(const Input &input, const uint8_t *gallery) {
    Result result;
    if (!gallery) return result;
    const auto *h=reinterpret_cast<const Header *>(gallery);
    result.revision.assign(h->revision,64);
    if (!h->count) { result.status="empty"; return result; }
    const auto *records=reinterpret_cast<const Record *>(gallery+sizeof(Header));
    float best=-2,second=-2;
    const Record *winner=nullptr;
    for (uint32_t i=0;i<h->count;++i) {
        bool seen=false;
        for (uint32_t j=0;j<i;++j) if (!strcmp(records[i].id,records[j].id)) { seen=true; break; }
        if (seen) continue;
        float score=-2;
        for (uint32_t j=i;j<h->count;++j) if (!strcmp(records[i].id,records[j].id)) {
            float dot=0,norm=0; for (int k=0;k<512;++k) { dot+=input.vector[k]*records[j].vector[k]; norm+=records[j].vector[k]*records[j].vector[k]; }
            score=std::max(score,dot/std::sqrt(norm));
        }
        if (score>best) { second=best; best=score; winner=&records[i]; } else second=std::max(second,score);
    }
    result.score=std::clamp(best,-1.f,1.f);
    if (winner && best>=h->threshold && (second==-2 || best-second>=h->margin)) { result.status="matched"; result.id=winner->id; result.name=winner->name; }
    else result.status="unknown";
    return result;
}
}
