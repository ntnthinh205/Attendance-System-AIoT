#include "hybrid_recognition.h"
#include "hybrid_match.h"
#include "edge_settings.h"
#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <ctime>
#include <string>
#include "cJSON.h"
#include "esp_crt_bundle.h"
#include "esp_event.h"
#include "esp_heap_caps.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_netif.h"
#include "esp_random.h"
#include "esp_sntp.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "nvs.h"
#include "nvs_flash.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"

extern const uint8_t initial_gallery_start[] asm("_binary_edge_gallery_bin_start");
extern const uint8_t initial_gallery_end[] asm("_binary_edge_gallery_bin_end");
namespace {
constexpr const char *TAG = "hybrid";
using namespace hybrid_core;
QueueHandle_t queue;
std::atomic<uint32_t> quality_generation{0};
uint8_t *gallery = nullptr;
size_t gallery_size = 0;
nvs_handle_t storage;
bool storage_ready = false;
cJSON *events = nullptr;
bool wifi_ready = false;
portMUX_TYPE wifi_lock = portMUX_INITIALIZER_UNLOCKED;
std::string device_id, boot_id;
uint32_t event_counter = 0;

bool connected() { portENTER_CRITICAL(&wifi_lock); bool value=wifi_ready; portEXIT_CRITICAL(&wifi_lock); return value; }
void network_event(void *, esp_event_base_t base, int32_t id, void *) {
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) esp_wifi_connect();
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        portENTER_CRITICAL(&wifi_lock); wifi_ready=false; portEXIT_CRITICAL(&wifi_lock);
        esp_wifi_connect();
    }
    if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        portENTER_CRITICAL(&wifi_lock); wifi_ready=true; portEXIT_CRITICAL(&wifi_lock);
        ESP_LOGI(TAG, "Wi-Fi connected");
    }
}
void start_wifi() {
    if (!EDGE_WIFI_SSID[0]) { ESP_LOGI(TAG, "No Wi-Fi configured; edge mode"); return; }
    if (esp_netif_init()!=ESP_OK || esp_event_loop_create_default()!=ESP_OK) return;
    esp_netif_create_default_wifi_sta();
    wifi_init_config_t config = WIFI_INIT_CONFIG_DEFAULT();
    if (esp_wifi_init(&config)!=ESP_OK) return;
    esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, network_event, nullptr);
    esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, network_event, nullptr);
    wifi_config_t station = {};
    memcpy(station.sta.ssid, EDGE_WIFI_SSID, strlen(EDGE_WIFI_SSID));
    memcpy(station.sta.password, EDGE_WIFI_PASSWORD, strlen(EDGE_WIFI_PASSWORD));
    esp_wifi_set_storage(WIFI_STORAGE_RAM);
    esp_wifi_set_mode(WIFI_MODE_STA);
    esp_wifi_set_config(WIFI_IF_STA, &station);
    esp_wifi_start();
    esp_sntp_setoperatingmode(SNTP_OPMODE_POLL);
    esp_sntp_setservername(0, "pool.ntp.org");
    esp_sntp_init();
}

void load_state() {
    // Never erase NVS on initialization failure: it may contain unsynced attendance.
    if (nvs_flash_init_partition("hybrid")==ESP_OK && nvs_open_from_partition("hybrid","state",NVS_READWRITE,&storage)==ESP_OK) storage_ready=true;
    size_t length=0;
    if (storage_ready && nvs_get_blob(storage,"gallery",nullptr,&length)==ESP_OK && length<=MAX_GALLERY) {
        auto *data=static_cast<uint8_t *>(heap_caps_malloc(length,MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT));
        if (data && nvs_get_blob(storage,"gallery",data,&length)==ESP_OK && valid_gallery(data,length)) { gallery=data; gallery_size=length; }
        else free(data);
    }
    if (!gallery) {
        length=initial_gallery_end-initial_gallery_start;
        if (valid_gallery(initial_gallery_start,length)) {
            gallery=static_cast<uint8_t *>(heap_caps_malloc(length,MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT));
            if (gallery) { memcpy(gallery,initial_gallery_start,length); gallery_size=length; }
        }
    }
    length=0;
    esp_err_t err=storage_ready ? nvs_get_str(storage,"pending",nullptr,&length) : ESP_ERR_NVS_NOT_FOUND;
    if (err==ESP_OK && length<=65536) {
        char *data=static_cast<char *>(malloc(length));
        if (data && nvs_get_str(storage,"pending",data,&length)==ESP_OK) events=cJSON_Parse(data);
        free(data);
        if (!cJSON_IsArray(events)) { cJSON_Delete(events); events=nullptr; }
    } else if (err==ESP_ERR_NVS_NOT_FOUND) events=cJSON_CreateArray();
    ESP_LOGI(TAG,"Gallery samples=%u; persistent storage=%s; pending=%d",gallery ? reinterpret_cast<Header *>(gallery)->count : 0,storage_ready ? "ready":"failed",events ? cJSON_GetArraySize(events):-1);
}
bool persist_events() {
    if (!storage_ready || !events) return false;
    char *text=cJSON_PrintUnformatted(events);
    if (!text) return false;
    bool ok=strlen(text)<65536 && nvs_set_str(storage,"pending",text)==ESP_OK && nvs_commit(storage)==ESP_OK;
    free(text); return ok;
}
bool request(const char *path, const char *payload, char **response, int *size, size_t limit) {
    if (!connected() || !EDGE_SERVER_URL[0] || !EDGE_API_TOKEN[0]) return false;
    std::string url=std::string(EDGE_SERVER_URL)+path;
    esp_http_client_config_t config={}; config.url=url.c_str(); config.timeout_ms=1500;
    config.crt_bundle_attach=esp_crt_bundle_attach; config.disable_auto_redirect=true;
    auto client=esp_http_client_init(&config);
    if (!client) return false;
    std::string authorization=std::string("Bearer ")+EDGE_API_TOKEN;
    esp_http_client_set_header(client,"Authorization",authorization.c_str());
    esp_http_client_set_header(client,"Content-Type","application/json");
    esp_http_client_set_method(client,payload ? HTTP_METHOD_POST:HTTP_METHOD_GET);
    bool ok=false;
    int length=payload ? strlen(payload):0;
    if (esp_http_client_open(client,length)==ESP_OK && (!length || esp_http_client_write(client,payload,length)==length)) {
        int64_t expected=esp_http_client_fetch_headers(client);
        if (esp_http_client_get_status_code(client)==200 && expected>=0 && static_cast<size_t>(expected)<=limit) {
            auto *buffer=static_cast<char *>(heap_caps_malloc(expected+1,MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT));
            if (buffer) {
                int received=esp_http_client_read_response(client,buffer,expected);
                if (received==expected) { buffer[received]=0; *response=buffer; *size=received; ok=true; }
                else free(buffer);
            }
        }
    }
    esp_http_client_close(client); esp_http_client_cleanup(client); return ok;
}
void sync_gallery() {
    char *data=nullptr; int length=0;
    if (!request("/v1/gallery",nullptr,&data,&length,MAX_GALLERY)) return;
    if (valid_gallery(reinterpret_cast<uint8_t *>(data),length) && (!gallery || memcmp(data+8,gallery+8,64))) {
        // Commit new snapshot first, retaining the previous one if storage fails.
        if (storage_ready && nvs_set_blob(storage,"gallery",data,length)==ESP_OK && nvs_commit(storage)==ESP_OK) {
            free(gallery); gallery=reinterpret_cast<uint8_t *>(data); gallery_size=length; data=nullptr;
            ESP_LOGI(TAG,"Gallery updated and persisted");
        }
    }
    free(data);
}
const char *string_field(cJSON *json,const char *key) { auto *v=cJSON_GetObjectItemCaseSensitive(json,key); return cJSON_IsString(v) ? v->valuestring:nullptr; }
bool server_match(const Input &input,Result &result) {
    cJSON *body=cJSON_CreateObject();
    cJSON_AddStringToObject(body,"model","MBF_S8_V1"); cJSON_AddNumberToObject(body,"faces",1); cJSON_AddNumberToObject(body,"dimensions",512);
    cJSON *values=cJSON_AddArrayToObject(body,"embedding"); for (float v:input.vector) cJSON_AddItemToArray(values,cJSON_CreateNumber(v));
    char *text=cJSON_PrintUnformatted(body); cJSON_Delete(body);
    char *data=nullptr; int length=0;
    bool ok=text && request("/v1/recognize",text,&data,&length,4096); free(text);
    if (!ok) return false;
    cJSON *response=cJSON_Parse(data); free(data);
    const char *status=string_field(response,"status"), *revision=string_field(response,"gallery_revision");
    const char *id=string_field(response,"id"), *name=string_field(response,"name");
    auto *score=cJSON_GetObjectItemCaseSensitive(response,"similarity");
    ok=status && revision && strlen(revision)==64 && (!strcmp(status,"matched") || !strcmp(status,"unknown") || !strcmp(status,"empty"));
    if (ok && !strcmp(status,"matched")) ok=id && *id && strlen(id)<64 && name && *name && strlen(name)<320 && cJSON_IsNumber(score) && std::isfinite(score->valuedouble) && score->valuedouble>=-1 && score->valuedouble<=1;
    if (ok) { result.status=status; result.revision=revision; result.id=id ? id:""; result.name=name ? name:""; result.source="server"; result.score=cJSON_IsNumber(score) ? score->valuedouble:0; }
    cJSON_Delete(response); return ok;
}
void sync_events() {
    if (!events || !cJSON_GetArraySize(events)) return;
    // One event per iteration bounds network work and drains backlog in the background.
    cJSON *event=cJSON_GetArrayItem(events,0); const char *id=string_field(event,"event_id");
    char *text=cJSON_PrintUnformatted(event), *response=nullptr; int length=0;
    bool ok=text && request("/v1/events",text,&response,&length,512); free(text);
    cJSON *ack=ok ? cJSON_Parse(response):nullptr; free(response);
    const char *ack_id=string_field(ack,"ack");
    if (id && ack_id && !strcmp(id,ack_id)) {
        cJSON *removed=cJSON_DetachItemFromArray(events,0);
        if (persist_events()) cJSON_Delete(removed); else cJSON_InsertItemInArray(events,0,removed);
    }
    cJSON_Delete(ack);
}
std::string save_event(const Result &r) {
    if (!events || cJSON_GetArraySize(events)>=128) return "";
    std::string id=device_id+"-"+boot_id+"-"+std::to_string(++event_counter);
    cJSON *event=cJSON_CreateObject();
    cJSON_AddStringToObject(event,"event_id",id.c_str()); cJSON_AddStringToObject(event,"device_id",device_id.c_str());
    cJSON_AddStringToObject(event,"employee_id",r.id.c_str()); cJSON_AddStringToObject(event,"source",r.source.c_str());
    cJSON_AddStringToObject(event,"gallery_revision",r.revision.c_str()); cJSON_AddNumberToObject(event,"similarity",r.score);
    time_t now=time(nullptr);
    if (now>1700000000) { cJSON_AddNumberToObject(event,"timestamp",now); cJSON_AddStringToObject(event,"time_quality","system_clock"); }
    else { cJSON_AddNullToObject(event,"timestamp"); cJSON_AddStringToObject(event,"time_quality","unsynced"); }
    cJSON_AddNumberToObject(event,"uptime_ms",esp_timer_get_time()/1000);
    cJSON_AddItemToArray(events,event);
    if (!persist_events()) { cJSON_DeleteItemFromArray(events,cJSON_GetArraySize(events)-1); return ""; }
    return id;
}
void worker(void *) {
    uint8_t mac[6]; esp_read_mac(mac,ESP_MAC_WIFI_STA);
    char buffer[64]; snprintf(buffer,sizeof(buffer),"%02x%02x%02x%02x%02x%02x",mac[0],mac[1],mac[2],mac[3],mac[4],mac[5]); device_id=buffer;
    snprintf(buffer,sizeof(buffer),"%08lx%08lx",(unsigned long)esp_random(),(unsigned long)esp_random()); boot_id=buffer;
    load_state(); start_wifi();
    struct Cooldown { std::string id; int64_t last=0; } cooldown[50];
    std::string stable_id; int stable_count=0; int64_t stable_at=0, retry_at=0, gallery_at=0, sync_at=0;
    while (true) {
        Input input={}; bool received=xQueueReceive(queue,&input,pdMS_TO_TICKS(100))==pdTRUE;
        int64_t now=esp_timer_get_time();
        if (received) {
            Result result;
            if (input.valid && now-input.captured<3000000) {
                if (now>=retry_at && server_match(input,result)) retry_at=0;
                else { result=edge_match(input,gallery); if (connected() && now>=retry_at) retry_at=now+5000000; }
            }
            std::string event_id, event_status="none";
            now=esp_timer_get_time();
            // Never act on a recognition that became stale during a slow HTTP operation.
            if (now-input.captured>3000000 || input.quality_generation!=quality_generation.load()) { result=Result(); }
            if (result.status=="matched") {
                stable_count=(stable_id==result.id && now-stable_at<4000000) ? stable_count+1:1;
                stable_id=result.id; stable_at=now;
                Cooldown *slot=nullptr;
                for (auto &entry:cooldown) if (entry.id==result.id) { slot=&entry; break; }
                if (!slot) for (auto &entry:cooldown) if (entry.id.empty()) { slot=&entry; break; }
                if (!slot) slot=&*std::min_element(std::begin(cooldown),std::end(cooldown),[](auto &a,auto &b){return a.last<b.last;});
                if (stable_count>=2 && (slot->id!=result.id || now-slot->last>=60000000)) {
                    event_id=save_event(result); event_status=event_id.empty() ? "storage_error":"saved";
                    if (!event_id.empty()) { slot->id=result.id; slot->last=now; }
                }
            } else { stable_id.clear(); stable_count=0; }
            cJSON *output=cJSON_CreateObject();
            cJSON_AddStringToObject(output,"status",result.status.c_str()); cJSON_AddStringToObject(output,"source",result.source.c_str());
            cJSON_AddStringToObject(output,"id",result.id.c_str()); cJSON_AddStringToObject(output,"name",result.name.c_str());
            cJSON_AddNumberToObject(output,"similarity",result.score); cJSON_AddStringToObject(output,"gallery_revision",result.revision.c_str());
            cJSON_AddBoolToObject(output,"storage_ready",storage_ready);
            cJSON_AddNumberToObject(output,"gallery_samples",gallery ? reinterpret_cast<Header *>(gallery)->count:0);
            cJSON_AddBoolToObject(output,"wifi",connected()); cJSON_AddNumberToObject(output,"pending_events",events ? cJSON_GetArraySize(events):0);
            cJSON_AddStringToObject(output,"event_status",event_status.c_str()); cJSON_AddStringToObject(output,"event_id",event_id.c_str());
            char *text=cJSON_PrintUnformatted(output); cJSON_Delete(output);
            if (text) { flockfile(stdout); printf("\n@MATCH:%s\n",text); fflush(stdout); funlockfile(stdout); free(text); }
        }
        now=esp_timer_get_time();
        if (connected() && now>=gallery_at) { gallery_at=now+30000000; sync_gallery(); }
        if (connected() && now>=sync_at) { sync_at=now+2000000; sync_events(); }
    }
}
}
extern "C" void hybrid_recognition_start(void) {
    esp_err_t err=nvs_flash_init();
    if (err!=ESP_OK) ESP_LOGE(TAG,"Default NVS init failed: %s",esp_err_to_name(err));
    queue=xQueueCreate(1,sizeof(Input));
    if (!queue || xTaskCreatePinnedToCore(worker,"hybrid",12288,nullptr,3,nullptr,0)!=pdPASS) { if(queue) vQueueDelete(queue); queue=nullptr; ESP_LOGE(TAG,"Unable to start hybrid task"); }
}
extern "C" void hybrid_recognition_submit(const float *embedding,int dimensions) {
    if (!queue) return;
    Input input={}; input.captured=esp_timer_get_time(); input.valid=embedding && dimensions==512;
    float norm=0;
    if (input.valid) { for (int i=0;i<512;++i) { float v=embedding[i]; input.vector[i]=v; norm+=v*v; if (!std::isfinite(v)) input.valid=false; }
        if (!std::isfinite(norm) || norm<.9f || norm>1.1f) input.valid=false;
        if (input.valid) { float scale=1.f/std::sqrt(norm); for (float &v:input.vector) v*=scale; }
    }
    if (!input.valid) ++quality_generation;
    input.quality_generation=quality_generation.load();
    xQueueOverwrite(queue,&input);
}
