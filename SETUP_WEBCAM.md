# 📹 Webcam Server Setup - Hướng Dẫn Toàn Bộ

## 🎯 Tổng Quan Kiến Trúc Mới

```
🎥 Webcam USB
    ↓ (cv2.VideoCapture)
RPi Server (Server_Webcam.py)
    ↓ (YOLO Detection - mỗi 1 giây)
Detected Objects
    ↓ (WebSocket)
🌐 Web App (web_app.html)
    ↓
🚨 Cảnh báo Real-time
```

**Không còn:**
- ❌ ESP32-CAM gửi HTTP POST
- ❌ Firebase notifications
- ❌ Android App

**Mới là:**
- ✅ Webcam USB trực tiếp trên RPi
- ✅ WebSocket real-time
- ✅ Web Dashboard (web_app.html)

---

## 📋 Yêu Cầu

### Hardware
- Raspberry Pi 4 (2GB+)
- Webcam USB (haul bất kỳ model nào)
- Thẻ nhớ SD 32GB

### Software
- Python 3.9+
- Raspberry Pi OS (Bullseye/Bookworm)
- YOLO model (best.pt)

---

## 🔧 Bước 1: Chuẩn Bị Webcam Trên RPi

### 1.1 Kết Nối Webcam USB
```bash
# Kết nối webcam vào port USB của RPi
# Kiểm tra webcam được nhận diện
lsusb

# Output sẽ có dòng tương tự:
# Bus 001 Device 003: ID 046d:082c Logitech, Inc. HD Webcam
```

### 1.2 Kiểm Tra Camera Index
```bash
# Test camera bằng OpenCV
python3 << 'EOF'
import cv2
for i in range(5):
    cap = cv2.VideoCapture(i)
    if cap.isOpened():
        print(f"Camera {i}: OK")
        cap.release()
    else:
        print(f"Camera {i}: Not found")
EOF
```

**Ghi chú:** Nếu webcam ở index 0, thì ok. Nếu ở index khác, update `CAMERA_INDEX` trong Server_Webcam.py

### 1.3 Phân Quyền Camera
```bash
# Thêm user pi vào group video
sudo usermod -a -G video pi

# Logout rồi login lại
# hoặc
sudo reboot
```

---

## 📦 Bước 2: Cập Nhật Dependencies

### 2.1 Update requirements.txt

Cạnh file Server_Webcam.py, requirements phải có:

```
fastapi==0.104.1
uvicorn==0.24.0
ultralytics==8.0.227
python-multipart==0.0.6
opencv-python==4.8.1.78
numpy==1.24.3
pillow==10.1.0
pydantic==2.5.0
requests==2.31.0
websockets==11.0.3
python-dotenv==1.0.0
```

### 2.2 Tạo/Cập Nhật requirements-webcam.txt

```bash
# Trên RPi
cat > requirements-webcam.txt << 'EOF'
fastapi==0.104.1
uvicorn==0.24.0
ultralytics==8.0.227
python-multipart==0.0.6
opencv-python==4.8.1.78
numpy==1.24.3
pillow==10.1.0
pydantic==2.5.0
requests==2.31.0
websockets==11.0.3
EOF
```

### 2.3 Cài Đặt
```bash
# SSH vào RPi
ssh pi@192.168.1.100

# Vào thư mục project
cd ~/security-alert-server

# Kích hoạt venv
source venv/bin/activate

# Cài packages
pip install -r requirements-webcam.txt

# Hoặc cài riêng lẻ
pip install fastapi uvicorn opencv-python ultralytics websockets
```

---

## 🎬 Bước 3: Chạy Server

### 3.1 Test Thủ Công Trước
```bash
# SSH vào RPi
ssh pi@192.168.1.100
cd ~/security-alert-server

# Kích hoạt venv
source venv/bin/activate

# Chạy server
python Server_Webcam.py
```

**Output mong đợi:**
```
======================================================================
🍓 Security Alert Server - Webcam Version (v3.0)
======================================================================
Hostname: raspberrypi
IP Address: 192.168.1.100
Port: 8000

📊 Architecture:
  Webcam USB → RPi → YOLO Detection → WebSocket → Web App

🔗 Access URLs:
  Local:    http://127.0.0.1:8000
  Network:  http://192.168.1.100:8000
  Docs:     http://192.168.1.100:8000/docs
  ReDoc:    http://192.168.1.100:8000/redoc

📝 Next steps:
  1. POST /start-detection - Bắt đầu detect
  2. WS ws://IP:8000/ws - Kết nối WebSocket
======================================================================
```

### 3.2 Nếu Có Lỗi Camera

**Lỗi: Camera not found**
```
❌ Failed to open camera 0
```

**Fix:**
```python
# Kiểm tra camera index
python3 << 'EOF'
import cv2
for i in range(5):
    cap = cv2.VideoCapture(i)
    if cap.isOpened():
        print(f"Camera {i}: OK")
        cap.release()
EOF

# Update CAMERA_INDEX trong Server_Webcam.py
CAMERA_INDEX = 1  # Hoặc index tìm được
```

---

## 🌐 Bước 4: Chạy Web Dashboard

### 4.1 Copy web_app.html
```bash
# Copy file web_app.html từ máy tính
scp web_app.html pi@192.168.1.100:/home/pi/security-alert-server/

# Hoặc copy qua USB/shared folder
```

### 4.2 Mở Web App
```bash
# Từ máy tính bất kỳ trên cùng mạng
# Mở trình duyệt (Chrome, Firefox, Safari)
http://192.168.1.100:8000
```

**Ghi chú:** Cần update IP trong web_app.html nếu không auto-detect đúng

### 4.3 Sử Dụng Web App

1. **Kết nối**: Tự động kết nối WebSocket
2. **Bắt đầu**: Click nút "▶️ Start Detection"
3. **Xem cảnh báo**: Real-time từ WebSocket
4. **Xem ảnh**: Click ảnh để zoom

---

## 🔧 Bước 5: Setup Systemd Service (Autostart)

### 5.1 Tạo Service File
```bash
sudo nano /etc/systemd/system/security-alert-webcam.service
```

### 5.2 Dán Nội Dung
```ini
[Unit]
Description=Security Alert Server - Webcam
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/security-alert-server
Environment="PATH=/home/pi/security-alert-server/venv/bin"
ExecStart=/home/pi/security-alert-server/venv/bin/python /home/pi/security-alert-server/Server_Webcam.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

**Lưu: Ctrl+O, Enter, Ctrl+X**

### 5.3 Kích Hoạt
```bash
sudo systemctl daemon-reload
sudo systemctl enable security-alert-webcam.service
sudo systemctl start security-alert-webcam.service

# Kiểm tra status
sudo systemctl status security-alert-webcam.service
```

### 5.4 Xem Logs
```bash
# Real-time
sudo journalctl -u security-alert-webcam.service -f

# Lịch sử
sudo journalctl -u security-alert-webcam.service -n 100
```

---

## 🧪 Bước 6: Test Endpoints

### 6.1 Test /status
```bash
curl http://192.168.1.100:8000/status
```

**Output:**
```json
{
  "status": "running",
  "camera": {
    "status": "connected",
    "resolution": "640x480"
  },
  "detection": {
    "status": "stopped",
    "model": "YOLOv8"
  }
}
```

### 6.2 Test /start-detection
```bash
curl -X POST http://192.168.1.100:8000/start-detection
```

### 6.3 Test WebSocket
```bash
# Dùng wscat (install: npm install -g wscat)
wscat -c ws://192.168.1.100:8000/ws

# Hoặc dùng Python
python3 << 'EOF'
import asyncio
import websockets
import json

async def test_ws():
    async with websockets.connect('ws://192.168.1.100:8000/ws') as websocket:
        data = await websocket.recv()
        print("Received:", json.loads(data))

asyncio.run(test_ws())
EOF
```

### 6.4 Test /detected-images
```bash
# Liệt kê ảnh
curl http://192.168.1.100:8000/detected-images

# Download ảnh
curl http://192.168.1.100:8000/detected-images/[image-id] > image.jpg
```

---

## 📊 Configuration Files

### Tuỳ Chỉnh Camera Quality

**Trong Server_Webcam.py:**
```python
FRAME_WIDTH = 640        # Độ rộng frame (càng cao càng chậm)
FRAME_HEIGHT = 480       # Độ cao frame
DETECTION_INTERVAL = 1.0 # Detect mỗi 1 giây (tăng để chậm hơn)
CONFIDENCE_THRESHOLD = 0.5  # 50% confidence (tăng để strict hơn)
CAMERA_INDEX = 0         # Index webcam (0, 1, 2, etc.)
```

**Ví dụ: Performance Optimize**
```python
# Chậm hơn nhưng chi phí xử lý ít
FRAME_WIDTH = 480
FRAME_HEIGHT = 360
DETECTION_INTERVAL = 2.0  # Detect mỗi 2 giây
```

---

## 🔄 Migration từ Architecture Cũ

| Cũ (ESP32) | Mới (Webcam) |
|-----------|-------------|
| ESP32-CAM → HTTP POST | Webcam USB → cv2.VideoCapture |
| Server nhận ảnh từ endpoint | Server capture ảnh liên tục |
| /detect, /detect-raw endpoints | /ws WebSocket |
| Firebase notifications | Web Dashboard |
| Android App | web_app.html |

**Endpoints Bị Xóa:**
- ❌ POST /register
- ❌ POST /detect
- ❌ POST /detect-raw

**Endpoints Mới:**
- ✅ POST /start-detection
- ✅ POST /stop-detection
- ✅ WS /ws
- ✅ GET /status (cập nhật)
- ✅ GET /detected-images
- ✅ GET /detected-images/{id}

---

## 🆘 Troubleshooting

### Webcam Không Nhận Diện

```bash
# Kiểm tra USB
lsusb

# Kiểm tra /dev/video*
ls /dev/video*

# Test camera bằng ffmpeg
ffplay /dev/video0
```

### Server Không Detect

```bash
# Kiểm tra YOLO model
python3 << 'EOF'
from ultralytics import YOLO
model = YOLO("best.pt")
print(model.info())
EOF
```

### WebSocket Connection Refused

```bash
# Kiểm tra firewall
sudo ufw status
sudo ufw allow 8000/tcp

# Kiểm tra port
netstat -tulpn | grep 8000
```

### Hiệu Suất Chậm

- Giảm resolution (480x360)
- Tăng DETECTION_INTERVAL (2-5 giây)
- Sử dụng model nhẹ hơn
- Tăng swap: `sudo nano /etc/dphys-swapfile`

---

## ✅ Checklist Cuối Cùng

- [ ] Webcam USB kết nối & nhận diện
- [ ] Camera index đúng (test bằng Python)
- [ ] Dependencies cài xong
- [ ] Server chạy thủ conhecido (Ctrl+C)
- [ ] Systemd service autostart work
- [ ] POST /start-detection trả 200
- [ ] WS /ws connection work
- [ ] web_app.html mở được
- [ ] Detected images được lưu
- [ ] Cảnh báo hiển thị real-time trên web

---

## 📝 Ghi Chú Quan Trọng

1. **Webcam Permission**: RPi user phải ở group `video`
2. **Camera Module**: Nếu dùng Raspberry Pi Camera Module, khác config (CSI)
3. **Resolution**: RPi yếu, không nên quá cao (640x480 ok)
4. **Detection Speed**: YOLO trên RPi ~0.5-1s/frame (CPU only)
5. **WebSocket**: Real-time, mất khoảng 50-100ms latency

---

## 🎉 Bước Tiếp Theo

- ✅ Server webcam chạy ổn định
- ✅ Web dashboard work real-time
- ✅ Tự động autostart trên boot
- ✅ Có log để debugging

**Hệ thống detection hoàn toàn hoạt động! 🍓**
