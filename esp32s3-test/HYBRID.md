# Nhận diện hybrid: server khi online, ESP32 khi offline

ESP32 luôn chạy MSRMNP + MBF_S8_V1 để tạo embedding. Tác vụ hybrid nhận vector qua queue riêng,
không thực hiện HTTP trong tác vụ camera/AI. Viewer chỉ hiển thị bản tin `@MATCH` từ bo khi firmware hybrid hoạt động;
không lấy kết quả cosine trên máy tính để thay thế kết quả từ bo.

## Cấu hình và chạy

File riêng `edge_config.json` đã được tạo, không đưa vào Git. Điền:

```json
{
  "wifi_ssid": "TEN_WIFI_2.4_GHZ",
  "wifi_password": "MAT_KHAU_WIFI",
  "server_url": "http://IP_LAN_MAY_TINH:8766",
  "api_token": "GIU_TOKEN_NGAU_NHIEN_DA_TAO",
  "threshold": 0.5,
  "match_margin": 0.05
}
```

`server_url` không kết thúc bằng `/`. Dùng IP LAN mà ESP32 truy cập được, không dùng `localhost`/`127.0.0.1`.
Máy tính và bo cần cùng mạng, cổng TCP 8766 được phép qua firewall. Server mẫu dùng HTTP cho thử nghiệm LAN;
firmware cũng hỗ trợ URL HTTPS và kiểm tra chứng chỉ bằng certificate bundle.
Nếu dùng HTTPS, đồng hồ hệ thống cần có giờ đúng. ESP32 khởi động SNTP khi cấu hình Wi-Fi.

Server mẫu hiện dùng **FastAPI + Uvicorn**. Cài dependencies trước:

```sh
.venv/bin/python -m pip install -r requirements.txt
```

Chạy server và viewer ở hai terminal:

```sh
.venv/bin/python hybrid_server.py
.venv/bin/python camera_viewer.py --port /dev/cu.usbmodem2101
```

Sau khi thay Wi-Fi, URL, token hoặc cấu hình nhúng, **build và nạp lại** firmware. Trong ESP-IDF terminal:

```sh
idf.py build
idf.py -p /dev/cu.usbmodem2101 flash
```

Dừng viewer trước khi flash, chạy lại sau đó. Nếu bo ở lại chế độ nạp, dùng:

```sh
python -m esptool --chip esp32s3 --port /dev/cu.usbmodem2101 --after watchdog-reset chip-id
```

Bộ công cụ đang có trong phiên làm việc này nằm ở thư mục tạm. Có thể mở ESP-IDF terminal bằng:

```sh
export IDF_TOOLS_PATH=/private/tmp/esp32s3-idf-tools
source /private/tmp/esp32s3-idf-v6.1/export.sh
```

Nếu thư mục tạm đã bị hệ điều hành dọn, cần cài/kích hoạt lại ESP-IDF 6.1.

## Dữ liệu nhận diện

- `faces.json` vẫn là dữ liệu chính trên máy tính/server. Đăng ký bằng viewer như trước.
- Server đọc lại file ở mỗi request nên nhận mẫu mới mà không cần restart.
- Tối đa 50 người; online và edge đều dùng **5 mẫu gần nhất/người**. File JSON vẫn có thể giữ 20 mẫu/người.
- Lần build đầu tạo gallery nhị phân từ `faces.json` và nhúng vào firmware để offline hoạt động ngay,
  kể cả chưa từng kết nối server. Không nhúng mật khẩu vào JSON khuôn mặt.
- Có mạng, bo thử cập nhật gallery từ server mỗi 30 giây. Kiểm tra định dạng, số chiều, số người/mẫu,
  vector hữu hạn và chuẩn L2 trước khi commit vào phân vùng NVS `hybrid`.
- Chỉ dùng bản tải mới sau khi commit thành công. Sau reboot ưu tiên gallery đã cache trong flash;
  nếu chưa có cache hợp lệ thì dùng bản nhúng.
- Đăng ký mới khi bo đang offline **chưa cập nhật ngay vào bo**. Cần cho bo kết nối server một lần để tải gallery mới.
  Flash lại ứng dụng không xóa gallery cache đã có; server là nơi cập nhật bản chính.
- Model bắt buộc `MBF_S8_V1`, 512 chiều. Cosine dùng chuẩn hóa L2, điểm cao nhất theo từng danh tính,
  threshold và khoảng cách với danh tính đứng thứ hai như phiên bản viewer trước.

## Chuyển chế độ

1. Có IP Wi-Fi và server phản hồi hợp lệ: dùng kết quả server, kể cả `unknown`/`empty`.
2. Mất Wi-Fi, server lỗi, response lỗi hoặc timeout: so khớp ngay với gallery trên ESP32.
3. Sau lỗi server, chờ 5 giây trước lần thử nhận diện online kế tiếp để không lặp timeout trên mọi vector.
4. HTTP có timeout 1.5 giây cho các thao tác I/O; kết quả quá 3 giây từ lúc nhận vector bị bỏ qua.
5. Queue nhận diện chỉ giữ vector gần nhất. Camera/embedding tiếp tục chạy khi mạng chậm.

Trang camera hiển thị `Server` hoặc `Edge / ESP32`, số sự kiện chờ đồng bộ và lỗi ghi sự kiện nếu có.
Trường `hybrid` trong `/status` xác định bo đã gửi bản tin hybrid; firmware cũ vẫn dùng đường so khớp Python cũ.

## Sự kiện chấm công và đồng bộ

- Hai kết quả liên tiếp cùng danh tính trong khoảng 4 giây mới đủ điều kiện ghi.
- Cùng người có thời gian nghỉ 60 giây giữa hai sự kiện trong một lần chạy bo.
  Đây là chống lặp sơ bộ, chưa phân loại ca làm/vào-ra; cooldown chưa giữ qua reboot.
- Cả online và offline đều lưu sự kiện xuống NVS trước. Chỉ báo `event_status=saved` khi commit thành công.
- Có mạng, gửi tối đa một sự kiện mỗi 2 giây, xóa khỏi hàng chờ sau khi server xác nhận đúng `event_id`
  và commit hàng chờ mới thành công. Request lỗi thì giữ nguyên để gửi lại, kể cả sau mất điện.
- `event_id` gồm MAC thiết bị, mã ngẫu nhiên của lần khởi động và số tăng dần. Retry dùng nguyên ID/payload.
- Server ghi SQLite (`attendance.sqlite3`), khóa chính `event_id`; cùng sự kiện gửi nhiều lần chỉ ghi một hàng.
  ID trùng với payload khác trả 409, không âm thầm ghi đè.
- Hàng chờ thử nghiệm tối đa **128 sự kiện**, blob JSON dưới 64 KB. Đầy hoặc ghi lỗi thì báo `storage_error`,
  không báo chấm công thành công và không xóa các sự kiện cũ.
- Phân vùng `hybrid` mới: offset `0x810000`, kích thước 2 MB, nằm sau app 8 MB; giữ nguyên offset NVS/app cũ.
  Không tự erase NVS khi lỗi khởi tạo để tránh mất sự kiện chưa gửi.

Sự kiện chứa ID người, thiết bị, nguồn (`edge`/`server`), điểm cosine, revision gallery, uptime và timestamp.
Khi chưa có giờ đáng tin sau boot, timestamp là `null`, `time_quality=unsynced`; server giữ nguyên trạng thái này
và ghi thời điểm nhận riêng. Không tự gán giờ server nhận thành giờ chấm công offline.
Muốn đúng giờ sau mất điện khi không có Internet cần RTC có pin (chưa triển khai trong phiên bản này).

## FastAPI và Swagger

Server chạy riêng cổng 8766; viewer vẫn ở 8765. Lệnh khởi động giữ nguyên:

```sh
.venv/bin/python hybrid_server.py --host 0.0.0.0 --port 8766
```

- `http://127.0.0.1:8766/docs`: Swagger UI, xem schema và thử API.
- `http://127.0.0.1:8766/redoc`: tài liệu ReDoc.
- `http://127.0.0.1:8766/openapi.json`: đặc tả OpenAPI.
- `http://127.0.0.1:8766/health`: kiểm tra tiến trình server, không chứng minh ESP32 đã kết nối.

Trong `/docs`, bấm **Authorize** và nhập giá trị `api_token` trong `edge_config.json`
(chỉ giá trị token, Swagger tự thêm `Bearer`). `GET /v1/gallery` có thể thử ngay.
`POST /v1/recognize` cần vector MBF 512 chiều, chuẩn hóa L2. Có thể lấy kết quả mới từ
`http://127.0.0.1:8765/face` khi có một mặt đạt điều kiện. `POST /v1/events` ghi sự kiện thật
vào SQLite, nên chỉ dùng dữ liệu thử trong database riêng nếu cần thử ghi.

Firmware không cần đổi endpoint hoặc định dạng request. Response gallery vẫn là binary có
`Content-Length`; response sự kiện vẫn `{"ack":"event_id"}`. Các request sai schema trả 422,
embedding không hợp lệ trả 400, sai token trả 401, dữ liệu khuôn mặt lỗi trả 503,
và event ID xung đột trả 409. Giới hạn request POST là 32 KB.

Ứng dụng được tạo bằng `create_app(...)`; SQLite khởi tạo qua FastAPI lifespan, kết nối được
mở/đóng theo thao tác để không chia sẻ một connection giữa các thread. Payload sự kiện giữ
nguyên JSON nhận được, tương thích retry của các sự kiện đã lưu bởi server Python cũ.

## API server mẫu

Mọi endpoint yêu cầu `Authorization: Bearer <api_token>`; token server đọc từ `edge_config.json`.

| Endpoint | Dữ liệu | Kết quả |
| --- | --- | --- |
| `GET /v1/gallery` | — | Gallery nhị phân `MBF512V1`, revision SHA-256 nội dung và chính sách, vector float32 little endian |
| `POST /v1/recognize` | `model`, `faces=1`, `dimensions=512`, `embedding` | `status`, `id`, `name`, `similarity`, `source=server`, `gallery_revision` |
| `POST /v1/events` | Sự kiện đã lưu trên bo | `{"ack":"event_id"}` sau khi commit SQLite |

Không gửi ảnh tới server. Phần nhận diện là request chỉ đọc; ghi chấm công dùng endpoint riêng.
Vì vậy timeout ở request nhận diện không tạo sự kiện trùng trên server.

## Kiểm thử

```sh
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -v
```

Kiểm thử gồm thuật toán C++ edge chạy native với đúng binary gallery, dữ liệu không hợp lệ,
server online/unknown, token, cập nhật gallery sau đăng ký, retry sự kiện không ghi trùng,
USB reconnect và viewer ưu tiên kết quả hybrid. Dữ liệu giả lập chỉ dùng thư mục tạm.

Thử trên thiết bị sau khi cấu hình Wi-Fi: nhận diện online → dừng server (giữ Wi-Fi) → thấy Edge →
khởi động lại server → trở về Server; tiếp theo tắt Wi-Fi → nhận diện Edge → bật lại để gửi bù.
Kiểm tra `pending_events`, SQLite và reboot khi có sự kiện đang chờ để xác minh lưu trữ.

## Ngưỡng mặt đủ gần và nhìn thẳng

`face_quality.json` chứa các ngưỡng kiểm tra trước khi chạy model embedding. Chỉnh file rồi build/nạp lại firmware:

| Tham số | Mặc định | Ý nghĩa |
| --- | --- | --- |
| `min_width_ratio` | 0.28 | Chiều rộng mặt ít nhất 28% chiều rộng ảnh (~90 px ở 320 px) |
| `min_height_ratio` | 0.38 | Chiều cao mặt ít nhất 38% chiều cao ảnh (~92 px ở 240 px) |
| `border_ratio` | 0.02 | Bounding box cách mép ảnh ít nhất 2%, tránh mặt bị cắt |
| `min_detection_score` | 0.85 | Điểm phát hiện mặt tối thiểu, khác threshold cosine danh tính |
| `min_eye_distance_ratio` | 0.09 | Khoảng cách hai mắt tối thiểu ~29 px ở ảnh 320 px |
| `max_roll_degrees` | 12 | Đường nối hai mắt nghiêng tối đa 12 độ so với phương ngang |
| `max_yaw_ratio` | 0.18 | Độ lệch ngang mũi/tâm miệng so với trung điểm hai mắt, chia khoảng cách mắt |
| `min_nose_height_ratio` | 0.30 | Mũi nằm ít nhất 30% khoảng từ mắt đến miệng |
| `max_nose_height_ratio` | 0.70 | Mũi nằm không quá 70% khoảng từ mắt đến miệng |

Thứ tự landmark theo thư viện Espressif cài trong source là mắt trái, miệng trái, mũi, mắt phải, miệng phải
(`dl_feat_image_preprocessor.cpp`), không phải thứ tự hai mắt–mũi–hai miệng thường thấy ở thư viện khác.
Góc quay/cúi-ngửa chỉ là ước lượng từ hình học 2D, chưa phải phép đo tư thế 3D hay khoảng cách centimet.
Ngưỡng cần thử trên camera/góc lắp thực tế; mặt đủ lớn và nhìn thẳng vẫn có thể bị nhòe, ngược sáng hoặc che khuất.
Phiên bản này chưa đo độ nét/phơi sáng riêng.

Mặt quá nhỏ, bị cắt, nghiêng/quay nhiều, landmark lỗi hoặc có nhiều mặt sẽ không tạo embedding.
Firmware gửi `quality` trong `@FACE`; viewer hiện lý do, tỉ lệ kích thước và hướng dẫn điều chỉnh.
Khung cam = không đạt, xanh = đạt. Đăng ký từ chối chất lượng không đạt dù gói tin có vector.
Kết quả hybrid cũ bị ẩn khi bản tin mặt mới không đạt; tác vụ hybrid hủy kết quả đang chờ HTTP
nếu đã nhận tín hiệu mặt không hợp lệ trước khi ghi chấm công.
File JSON khuôn mặt và các mẫu đã đăng ký được giữ nguyên; bộ lọc áp dụng cho mẫu mới và các lần nhận diện tiếp theo.
