# ESP32-S3 Face Embedding

Pipeline OV3660 -> phát hiện mặt MSRMNP_S8_V1 -> căn chỉnh -> MBF_S8_V1.
Model chạy trên ESP32-S3; máy tính nhận hình, bounding box và embedding qua USB.
Hỗ trợ đăng ký danh tính vào JSON, nhận diện hybrid trên server/ESP32 và hàng chờ chấm công offline.
Hướng dẫn mới: [nhận diện hybrid và server mẫu](HYBRID.md).

## Mã nguồn

- `main/app_main.c`: điểm vào ứng dụng.
- `main/camera_stream.c`: khởi tạo camera/USB, lấy ảnh, gửi ảnh tới tác vụ AI và viewer.
- `main/face_embedding.cpp`: phát hiện mặt và tạo embedding MBF_S8_V1.
- `camera_viewer.py`: xem ảnh và khung khuôn mặt trên máy tính.
- `partitions.csv`: bố trí flash cho firmware chứa model.

## Build và chạy

Trong ESP-IDF terminal, tại thư mục này:

```sh
idf.py build
idf.py -p /dev/ttyACM0 flash
python camera_viewer.py --host 127.0.0.1 --no-browser
```

Đóng viewer bằng Ctrl+C trước khi nạp firmware. Cắm cổng USB-OTG/Serial-JTAG.
Mở http://127.0.0.1:8765. Endpoint `/face` chứa kết quả AI gần nhất;
kiểm tra `face_age` ở `/status` trước khi sử dụng vector.

Xem trên điện thoại cùng Wi-Fi: chạy với `--host IP_WIFI_CUA_MAY_TINH`.
Viewer in tên đăng nhập `camera` và mật khẩu ngẫu nhiên trong terminal.
Chế độ LAN khóa `/face`; chỉ chia sẻ hình và trạng thái xử lý cho người có mật khẩu.
Máy tính phải tiếp tục chạy và giữ kết nối USB với bo.

Chi tiết model, cấu hình và giới hạn: [MBF_S8_V1.md](MBF_S8_V1.md).

## Đăng ký khuôn mặt bằng viewer

Trên macOS, chạy viewer (cài `pyserial` trong môi trường Python trước):

```sh
.venv/bin/python camera_viewer.py --port /dev/cu.usbmodem2101
```

1. Mở http://127.0.0.1:8765, đưa đúng một khuôn mặt đủ sáng vào camera.
2. Nhập tên và bấm **Đăng ký khuôn mặt**, giữ mặt yên trong khi chờ kết quả.
   Viewer lấy embedding mới nhận sau khi bấm, không lấy vector đang lưu từ lần trước.
3. Khi báo thành công, vector được ghi vào `faces.json` cạnh source. File tự tạo ở lần đăng ký đầu tiên,
   tự đọc lại khi khởi động. Nhập lại cùng tên để bổ sung mẫu (không phân biệt hoa/thường, tối đa 20 mẫu/người).
4. Từ đó, mỗi kết quả AI mới được so với các mẫu đã lưu. Trang hiển thị tên nếu đủ điểm;
   ngược lại hiển thị **Chưa nhận diện**. Không có mặt, nhiều mặt, mất USB hoặc dữ liệu cũ sẽ không nhận diện/đăng ký.

ESP32 thực hiện phát hiện mặt, căn chỉnh và embedding. Firmware cũ dùng so khớp Python;
firmware hybrid dùng kết quả server hoặc ESP32. Đăng ký cập nhật JSON trên máy tính, sau đó server cấp gallery mới xuống bo.
Xem [HYBRID.md](HYBRID.md) để cấu hình Wi-Fi, build/nạp firmware và chạy server. Phải giữ viewer chạy và kết nối USB.
Đăng ký chỉ bật khi viewer bind địa chỉ loopback (mặc định `127.0.0.1`); chế độ LAN chỉ xem.

Điểm cosine là độ tương đồng, không phải xác suất nhận đúng. Mặc định `--threshold 0.5`
(theo mặc định recognizer trong thư viện Espressif đã cài), `--match-margin 0.05` yêu cầu
chênh lệch giữa hai người có điểm cao nhất. Điểm một người là cosine cao nhất trong các mẫu của người đó.
Cần thử với người đã đăng ký và người lạ trong điều kiện thực tế để hiệu chỉnh; tăng threshold
nếu nhận nhầm, giảm cẩn thận nếu bỏ sót. Ví dụ:

```sh
.venv/bin/python camera_viewer.py --port /dev/cu.usbmodem2101 --threshold 0.6 --database faces.json
```

`GET /status` thêm `recognition`, `people` (tên và số mẫu, không chứa vector), `enrollment_enabled`.
`POST /enroll` nhận JSON `{"name":"Tên"}`, header `Content-Type: application/json` và `X-Camera-Viewer: 1`;
đợi bản tin AI tiếp theo tối đa 8 giây rồi kiểm tra đúng một mặt và vector hợp lệ trước khi lưu.
JSON chứa phiên bản định dạng, model, số chiều và danh sách người với ID, tên, các mẫu embedding và thời gian UTC.
File được thay thế nguyên tử; ghi thất bại không cập nhật dữ liệu trong RAM.

Kiểm thử (dùng dữ liệu tổng hợp và file tạm, không đăng ký vào database thật):

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Viewer tự thử mở lại cổng USB mỗi giây sau lỗi/ngắt kết nối; dữ liệu ảnh và embedding cũ bị xóa khỏi RAM.
Cắm lại bo vào cùng cổng, không cần khởi động lại viewer. Nếu macOS đổi tên cổng USB, chạy lại với `--port` mới.
# Attendance-System-AIoT

## Server FastAPI

```sh
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python hybrid_server.py --port 8766
```

Mở http://127.0.0.1:8766/docs để thử API, nhập `api_token` qua nút Authorize.
Camera viewer vẫn ở http://127.0.0.1:8765. Xem [HYBRID.md](HYBRID.md) cho cấu hình ESP32 và API.
