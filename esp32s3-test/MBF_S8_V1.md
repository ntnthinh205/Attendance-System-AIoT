# MBF_S8_V1 trên ESP32-S3 + OV3660

Pipeline chạy trên ESP32, không chạy model trên máy tính:
JPEG 320x240 -> RGB888 -> MSRMNP_S8_V1 (phát hiện mặt và 5 điểm mốc)
-> căn chỉnh khuôn mặt trong HumanFaceFeat -> MBF_S8_V1 -> embedding float chuẩn hóa L2.

Model chính thức: espressif/human_face_recognition 0.3.3, dependency cụ thể được
khóa trong dependencies.lock. Chỉ MBF được đưa vào flash, MFN bị tắt.
Nguồn API/model: https://github.com/espressif/esp-dl/tree/master/models/human_face_recognition

Cấu hình: CPU 240 MHz, data cache 64 KB, Octal PSRAM 80 MHz, compiler performance.
partitions.csv giữ NVS tại 0x9000 và phy_init tại 0xf000, mở rộng app lên 8 MB
trên module flash 16 MB đã xác minh. Không có OTA trong bố trí này.
Model nhúng vào rodata của firmware; không cần SD card hoặc tải từ Internet khi chạy.

## Chạy

Trong ESP-IDF terminal tại thư mục dự án:

```sh
idf.py build
idf.py -p /dev/ttyACM0 flash
python camera_viewer.py --no-browser
```

Đóng viewer trước khi flash hoặc mở monitor. Kết nối cổng USB-OTG/Serial-JTAG.
Mở http://127.0.0.1:8765 và đưa một khuôn mặt đủ sáng vào khung hình.
Trang hiển thị số chiều embedding và thời gian detect/embedding khi có đúng một mặt.
Không có mặt hoặc nhiều mặt: không tạo embedding, tránh chọn nhầm người.

- `GET /status`: FPS camera, trạng thái AI và độ cũ của kết quả.
- `GET /face`: JSON kết quả AI gần nhất, gồm vector `embedding`.
- Viewer hỗ trợ đăng ký vào `faces.json` và nhận diện cosine; xem hướng dẫn trong README.md.
- Trước khi dùng /face để đăng ký, kiểm tra face_age ở /status, faces=1,
  dimensions>0 và số phần tử vector khớp dimensions. Không dùng kết quả cũ sau mất USB.

Tác vụ AI riêng nhận tối đa khoảng một ảnh mỗi giây, queue dài 1 để giới hạn bộ nhớ.
FPS preview và tốc độ embedding là hai chỉ số khác nhau; suy luận sẽ cạnh tranh CPU/PSRAM
với camera. Thời gian thực tế được đo ngay trên bo, không lấy benchmark làm cam kết.

## Phạm vi

ESP32 trích xuất embedding; viewer đăng ký nhiều mẫu theo tên/ID và so khớp cosine.
Đây chưa phải hệ thống điểm danh hoàn chỉnh: chưa lưu lịch sử, chưa có chống giả mạo
và cần hiệu chỉnh ngưỡng bằng dữ liệu thực tế. Hai tên khác nhau có thể đăng ký cùng một người;
viewer từ chối nhận diện khi hai danh tính có điểm quá gần nhau.
Không trộn vector MBF với vector MFN hoặc model khác.

## Khung khuôn mặt trên viewer

Firmware gửi `image_width`, `image_height` và `boxes` trong bản tin @FACE.
Mỗi box có x1,y1,x2,y2 theo pixel ảnh gốc và score của detector.
Trang camera vẽ khung xanh cho mọi mặt được phát hiện; điểm phần trăm là độ tin cậy
phát hiện mặt, không phải độ chắc chắn danh tính. Chỉ một mặt mới tạo embedding.
Overlay dùng kết quả AI gần nhất trên video live, không đồng bộ chính xác từng frame;
có thể lệch khi di chuyển nhanh vì AI lấy ảnh khoảng 1 lần/giây.
Khung bị xóa khi không có mặt, kết quả nhận quá 2 giây hoặc mất dữ liệu camera.

## Nhận diện hybrid

Firmware mới hỗ trợ server online, cosine trên ESP32 khi offline, cache gallery và hàng chờ sự kiện.
Xem [HYBRID.md](HYBRID.md) để biết cấu hình, API và các giới hạn chấm công thử nghiệm.
