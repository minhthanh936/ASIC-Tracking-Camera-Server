"""
🍓 Security Alert Server - Raspberry Pi + Webcam
Kiến trúc mới: Webcam USB → RPi YOLO → WebSocket → Web App

Xử lý ảnh liên tục từ webcam, detect object, phát cảnh báo WebSocket
"""

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
import os
# Giảm log OpenCV trước khi import cv2
os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")
import cv2
import asyncio
import threading
import json
import logging
from datetime import datetime
from typing import List, Set
from pathlib import Path
from ultralytics import YOLO
import numpy as np
import socket
import time
import urllib.request
import subprocess
import signal
import re

# ==================== LOGGING SETUP ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Giảm log nhiễu từ OpenCV khi probe camera index không hợp lệ
try:
    if hasattr(cv2, "setLogLevel") and hasattr(cv2, "LOG_LEVEL_ERROR"):
        cv2.setLogLevel(cv2.LOG_LEVEL_ERROR)
except Exception:
    pass

# ==================== CONFIG ====================
DETECTION_IMAGES_DIR = Path("detected_images")
DETECTION_IMAGES_DIR.mkdir(exist_ok=True)

CAMERA_INDEX = int(os.getenv("CAMERA_INDEX", "1"))  # Có thể đổi qua biến môi trường CAMERA_INDEX
CAMERA_DEVICE = os.getenv("CAMERA_DEVICE", "").strip()  # Ví dụ: /dev/video0
PREFERRED_CAMERA_DEVICES = [
    dev.strip() for dev in os.getenv("PREFERRED_CAMERA_DEVICES", "/dev/video0,/dev/video1").split(",")
    if dev.strip()
]
ALLOW_FULL_VIDEO_SCAN = os.getenv("ALLOW_FULL_VIDEO_SCAN", "0") == "1"
MAX_CAMERA_INDEX_TO_TRY = 4  # Thử thêm index khác nếu camera mặc định không mở được
ALLOW_CAMERA_ZERO_FALLBACK = os.getenv("ALLOW_CAMERA_ZERO_FALLBACK", "0") == "1"
FRAME_WIDTH = 640
FRAME_HEIGHT = 480
DETECTION_INTERVAL = float(os.getenv("DETECTION_INTERVAL", "0.15"))  # Detect gần realtime
IMAGE_SAVE_INTERVAL = float(os.getenv("IMAGE_SAVE_INTERVAL", "10.0"))  # Lưu ảnh cách nhau để giảm đầy dung lượng
OVERLAY_TTL_SECONDS = float(os.getenv("OVERLAY_TTL_SECONDS", "3.0"))  # Giữ overlay lâu hơn để dễ quan sát trên web app
MAX_SAVED_IMAGES = int(os.getenv("MAX_SAVED_IMAGES", "200"))  # Giới hạn số ảnh lưu để tránh đầy bộ nhớ
PRUNE_ON_STARTUP = os.getenv("PRUNE_ON_STARTUP", "0") == "1"  # Mặc định không dọn ảnh khi khởi động
CONFIDENCE_THRESHOLD = 0.5  # 50% confidence
DEFAULT_PORT = 8000
WEB_APP_PORT = int(os.getenv("WEB_APP_PORT", "8080"))

# ==================== GLOBAL STATE ====================
class DetectionState:
    def __init__(self):
        self.is_detecting = False
        self.current_frame = None
        self.last_detections = []
        self.last_detection_ts = 0.0
        self.last_detection_result = {}
        self.websocket_clients: Set[WebSocket] = set()
        self.camera = None
        self.model = None
        self.detection_thread = None
        self.lock = threading.Lock()

detection_state = DetectionState()

# ==================== FASTAPI APP ====================
app = FastAPI(
    title="Security Alert Server - Webcam Version",
    version="3.0-Webcam",
    description="Server detect từ webcam USB, xử lý YOLO, phát cảnh báo WebSocket"
)

# Enable CORS cho web app
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==================== CAMERA & MODEL INIT ====================
def _configure_camera(cap):
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
    cap.set(cv2.CAP_PROP_FPS, 30)

def _open_camera_source(source):
    """Mở camera từ index hoặc device path."""
    cap = None
    try:
        if hasattr(cv2, "CAP_V4L2"):
            cap = cv2.VideoCapture(source, cv2.CAP_V4L2)
            if not cap.isOpened():
                cap.release()
                cap = cv2.VideoCapture(source)
        else:
            cap = cv2.VideoCapture(source)

        if not cap.isOpened():
            return None

        _configure_camera(cap)
        ret, frame = cap.read()
        if not ret or frame is None:
            cap.release()
            return None
        return cap
    except Exception:
        if cap is not None:
            cap.release()
        return None

def _list_video_devices() -> List[str]:
    devices = [str(p) for p in sorted(Path("/dev").glob("video*"))]
    if ALLOW_FULL_VIDEO_SCAN:
        return devices
    # Mặc định chỉ ưu tiên node webcam phổ biến để tránh quét codec/ISP node gây log nhiễu.
    return [dev for dev in devices if dev in PREFERRED_CAMERA_DEVICES]

def init_camera():
    """Khởi tạo webcam USB"""
    try:
        indices_to_try = [CAMERA_INDEX]
        indices_to_try.extend(
            idx
            for idx in range(MAX_CAMERA_INDEX_TO_TRY + 1)
            if idx != CAMERA_INDEX and (ALLOW_CAMERA_ZERO_FALLBACK or idx != 0)
        )
        device_paths = _list_video_devices()

        # Ưu tiên device path cụ thể nếu user chỉ định.
        candidates = []
        if CAMERA_DEVICE:
            candidates.append(CAMERA_DEVICE)
        for dev in PREFERRED_CAMERA_DEVICES:
            if dev not in candidates:
                candidates.append(dev)
        candidates.extend(indices_to_try)
        candidates.extend(path for path in device_paths if path != CAMERA_DEVICE)
        candidates = list(dict.fromkeys(candidates))

        logger.info(f"Trying camera candidates: {candidates}")
        for source in candidates:
            cap = _open_camera_source(source)
            if cap is not None:
                logger.info(f"✓ Camera initialized from source {source}: {FRAME_WIDTH}x{FRAME_HEIGHT}")
                return cap
            logger.warning(f"Camera source {source} not available")

        if not device_paths:
            logger.error("❌ Failed to open usable camera. No /dev/video* devices found")
        else:
            logger.error(f"❌ Failed to open usable camera. Detected devices: {device_paths}")
            logger.error("Try setting CAMERA_DEVICE explicitly, e.g. CAMERA_DEVICE=/dev/video0")
        return None
    except Exception as e:
        logger.error(f"❌ Camera init error: {e}")
        return None

def init_model():
    """Khởi tạo YOLO model"""
    try:
        logger.info("Loading YOLO model...")
        model = YOLO("best.pt")
        logger.info("✓ Model loaded successfully")
        return model
    except Exception as e:
        logger.error(f"❌ Failed to load model: {e}")
        return None

# ==================== DETECTION LOOP ====================
async def broadcast_detection(data: dict):
    """Phát cảnh báo tới tất cả WebSocket clients"""
    disconnected = set()
    
    for client in detection_state.websocket_clients:
        try:
            await client.send_json(data)
        except Exception as e:
            logger.warning(f"Client disconnect: {e}")
            disconnected.add(client)
    
    # Xóa clients bị disconnect
    for client in disconnected:
        detection_state.websocket_clients.discard(client)

def detection_worker():
    """Thread xử lý detection liên tục"""
    logger.info("Detection worker started")
    last_detection_time = time.time()
    last_image_save_time = 0.0
    
    while detection_state.is_detecting:
        try:
            # Kiểm tra có cần detect lần này không
            current_time = time.time()
            if current_time - last_detection_time < DETECTION_INTERVAL:
                time.sleep(0.1)
                continue
            
            # Lấy frame hiện tại
            with detection_state.lock:
                if detection_state.current_frame is None:
                    time.sleep(0.1)
                    continue
                frame = detection_state.current_frame.copy()
            
            # Chạy YOLO detection
            results = detection_state.model(frame)
            
            detections = []
            alert_objects = []
            detection_image_path = None
            
            # Xử lý kết quả
            for r in results:
                for box in r.boxes:
                    class_id = int(box.cls[0])
                    label = detection_state.model.names[class_id]
                    confidence = float(box.conf[0])
                    
                    if confidence > CONFIDENCE_THRESHOLD:
                        detections.append({
                            "object": label,
                            "confidence": round(confidence, 2),
                            "class_id": class_id,
                            "bbox": box.xyxy[0].tolist()
                        })
                        alert_objects.append(label)

            if detections:
                with detection_state.lock:
                    detection_state.last_detections = detections
                    detection_state.last_detection_ts = current_time
            
            # Nếu có phát hiện
            if alert_objects:
                # WebSocket luôn phát realtime, nhưng chỉ lưu ảnh theo chu kỳ để tránh đầy dung lượng.
                image_id = None
                if current_time - last_image_save_time >= IMAGE_SAVE_INTERVAL:
                    unique_objects = list(set(alert_objects))
                    image_id, detection_image_path = save_detection_image(frame, results, unique_objects)
                    if image_id and detection_image_path:
                        last_image_save_time = current_time

                unique_objects = list(set(alert_objects))
                
                # Phát cảnh báo WebSocket
                alert_data = {
                    "type": "DETECTION_ALERT",
                    "timestamp": datetime.now().isoformat(),
                    "objects": unique_objects,
                    "confidence": [d["confidence"] for d in detections],
                    "detections": detections,
                    "image_id": image_id,
                    "image_url": detection_image_path,
                    "total_detections": len(detections),
                    "image_saved": detection_image_path is not None
                }

                detection_state.last_detection_result = alert_data
                
                logger.info(f"🚨 Detection: {', '.join(unique_objects)}")
                
                # Broadcast tới web app
                asyncio.run_coroutine_threadsafe(
                    broadcast_detection(alert_data),
                    loop
                )
            else:
                detection_state.last_detection_result = {
                    "type": "DETECTION_IDLE",
                    "timestamp": datetime.now().isoformat(),
                    "objects": [],
                    "confidence": [],
                    "detections": [],
                    "image_id": None,
                    "image_url": None,
                    "total_detections": 0,
                    "image_saved": False
                }
            
            last_detection_time = current_time
            
        except Exception as e:
            logger.error(f"Detection error: {e}")
            time.sleep(0.1)
    
    logger.info("Detection worker stopped")

def camera_capture_worker():
    """Thread capture ảnh từ webcam liên tục"""
    logger.info("Camera capture worker started")
    
    while detection_state.is_detecting:
        try:
            ret, frame = detection_state.camera.read()
            if not ret:
                logger.warning("Failed to read frame")
                time.sleep(0.1)
                continue
            
            # Resize frame
            frame = cv2.resize(frame, (FRAME_WIDTH, FRAME_HEIGHT))
            
            with detection_state.lock:
                detection_state.current_frame = frame
            
            time.sleep(0.03)  # ~30 FPS
            
        except Exception as e:
            logger.error(f"Camera capture error: {e}")
            time.sleep(0.1)
    
    logger.info("Camera capture worker stopped")

def save_detection_image(img, results, detected_objects):
    """Lưu ảnh với bounding box"""
    try:
        annotated_img = img.copy()
        
        for r in results:
            for box in r.boxes:
                x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                class_id = int(box.cls[0])
                label = detection_state.model.names[class_id]
                confidence = float(box.conf[0])
                
                if confidence > CONFIDENCE_THRESHOLD:
                    # Draw bounding box
                    cv2.rectangle(annotated_img, (x1, y1), (x2, y2), (0, 255, 0), 2)
                    
                    # Draw label
                    text = f"{label} {confidence:.2f}"
                    cv2.putText(
                        annotated_img, text, (x1, y1 - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2
                    )
        
        # Tạo tên file theo format: object_gio-phut_ngay-thang-nam
        if detected_objects:
            objects_text = "-".join(sorted(set(str(obj) for obj in detected_objects)))
        else:
            objects_text = "unknown"

        objects_text = re.sub(r"[^a-zA-Z0-9_-]+", "-", objects_text).strip("-")
        if not objects_text:
            objects_text = "unknown"

        timestamp = datetime.now().strftime("%H-%M_%d-%m-%Y")
        image_id = f"{objects_text}_{timestamp}"

        # Save image
        image_path = DETECTION_IMAGES_DIR / f"{image_id}.jpg"
        save_ok = cv2.imwrite(str(image_path), annotated_img)
        if not save_ok:
            logger.error(f"Failed to save image file: {image_path}")
            return None, None

        prune_detected_images(MAX_SAVED_IMAGES)
        
        logger.info(f"💾 Image saved: {image_id}")
        return image_id, f"/detected-images/{image_id}"
    except Exception as e:
        logger.error(f"Error saving image: {e}")
        return None, None

def prune_detected_images(max_saved_images: int):
    """Xóa ảnh cũ nhất để giữ số lượng ảnh không vượt ngưỡng."""
    if max_saved_images <= 0:
        return

    try:
        images = sorted(
            DETECTION_IMAGES_DIR.glob("*.jpg"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        if len(images) <= max_saved_images:
            return

        removed_count = 0
        for old_path in images[max_saved_images:]:
            try:
                old_path.unlink(missing_ok=True)
                removed_count += 1
            except Exception as e:
                logger.warning(f"Failed to remove old image {old_path.name}: {e}")

        if removed_count > 0:
            logger.info(
                f"🧹 Pruned {removed_count} old images (limit={max_saved_images}, current={max_saved_images})"
            )
    except Exception as e:
        logger.warning(f"Failed to prune detected images: {e}")

def draw_detection_overlay(frame, detections):
    """Vẽ bounding box + nhãn object lên frame realtime."""
    for det in detections:
        try:
            x1, y1, x2, y2 = map(int, det.get("bbox", [0, 0, 0, 0]))
            label = det.get("object", "object")
            confidence = float(det.get("confidence", 0.0))

            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
            text = f"{label} {confidence:.2f}"
            cv2.putText(
                frame,
                text,
                (x1, max(25, y1 - 10)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (0, 255, 0),
                2,
            )
        except Exception:
            continue

def generate_video_feed():
    """Stream frame realtime dạng MJPEG cho web app."""
    while True:
        try:
            with detection_state.lock:
                if detection_state.current_frame is None:
                    frame = None
                else:
                    frame = detection_state.current_frame.copy()
                latest_detections = list(detection_state.last_detections)
                latest_detection_ts = detection_state.last_detection_ts

            if frame is None:
                time.sleep(0.05)
                continue

            if latest_detections and (time.time() - latest_detection_ts) <= OVERLAY_TTL_SECONDS:
                draw_detection_overlay(frame, latest_detections)

            ok, buffer = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            if not ok:
                time.sleep(0.03)
                continue

            yield (
                b'--frame\r\n'
                b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n'
            )
            time.sleep(0.03)
        except Exception as e:
            logger.error(f"Video feed error: {e}")
            time.sleep(0.1)

# ==================== ENDPOINTS ====================

@app.get("/")
async def root():
    """Root endpoint - thông tin server"""
    return {
        "name": "Security Alert Server - Webcam",
        "version": "3.0-Webcam",
        "hostname": socket.gethostname(),
        "server_ip": get_server_ip(),
        "architecture": "Webcam (USB) → RPi → YOLO → WebSocket → Web App",
        "camera_status": "connected" if detection_state.camera else "disconnected",
        "detection_status": "running" if detection_state.is_detecting else "stopped",
        "endpoints": {
            "GET /": "Server info",
            "GET /status": "Detailed status",
            "POST /start-detection": "Start detection",
            "POST /stop-detection": "Stop detection",
            "WS /ws": "WebSocket for alerts",
            "GET /detected-images": "List detected images",
            "GET /detected-images/{image_id}": "Get image",
            "GET /last-detection": "Last detection result"
        }
    }

@app.get("/status")
async def get_status():
    """Trạng thái server chi tiết"""
    image_count = len(list(DETECTION_IMAGES_DIR.glob("*.jpg")))
    
    return {
        "status": "running",
        "server": "Raspberry Pi - Webcam",
        "hostname": socket.gethostname(),
        "ip_address": get_server_ip(),
        "timestamp": datetime.now().isoformat(),
        "camera": {
            "status": "connected" if detection_state.camera else "disconnected",
            "model": f"Index {CAMERA_INDEX}",
            "resolution": f"{FRAME_WIDTH}x{FRAME_HEIGHT}"
        },
        "detection": {
            "status": "running" if detection_state.is_detecting else "stopped",
            "interval": DETECTION_INTERVAL,
            "confidence_threshold": CONFIDENCE_THRESHOLD,
            "model": "YOLOv8",
            "model_loaded": detection_state.model is not None
        },
        "websocket_clients": len(detection_state.websocket_clients),
        "detected_images_count": image_count
    }

@app.get("/video-feed")
async def video_feed():
    """Luồng video realtime cho web app."""
    return StreamingResponse(
        generate_video_feed(),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )

@app.post("/start-detection")
async def start_detection():
    """Bắt đầu detection"""
    if detection_state.is_detecting:
        raise HTTPException(status_code=400, detail="Detection already running")
    
    if detection_state.model is None or detection_state.camera is None:
        raise HTTPException(status_code=500, detail="Model or camera not initialized")
    
    detection_state.is_detecting = True
    
    # Bắt đầu capture thread
    capture_thread = threading.Thread(
        target=camera_capture_worker,
        daemon=True
    )
    capture_thread.start()
    
    # Bắt đầu detection thread
    detection_thread = threading.Thread(
        target=detection_worker,
        daemon=True
    )
    detection_thread.start()
    
    logger.info("✓ Detection started")
    
    return {
        "status": "success",
        "message": "Detection started",
        "timestamp": datetime.now().isoformat()
    }

@app.post("/stop-detection")
async def stop_detection():
    """Dừng detection"""
    if not detection_state.is_detecting:
        raise HTTPException(status_code=400, detail="Detection not running")
    
    detection_state.is_detecting = False
    
    # Đóng camera
    if detection_state.camera:
        detection_state.camera.release()
        detection_state.camera = None
    
    logger.info("✓ Detection stopped")
    
    return {
        "status": "success",
        "message": "Detection stopped",
        "timestamp": datetime.now().isoformat()
    }

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint cho web app nhận cảnh báo"""
    await websocket.accept()
    detection_state.websocket_clients.add(websocket)
    
    logger.info(f"✓ WebSocket client connected (total: {len(detection_state.websocket_clients)})")
    
    try:
        # Gửi trạng thái ban đầu
        await websocket.send_json({
            "type": "CONNECTION_SUCCESS",
            "message": "Connected to security alert server",
            "detection_running": detection_state.is_detecting
        })
        
        # Giữ connection mở
        while True:
            data = await websocket.receive_text()
            
            # Có thể nhận ping từ client
            if data == "ping":
                await websocket.send_json({
                    "type": "PONG",
                    "timestamp": datetime.now().isoformat()
                })
    
    except WebSocketDisconnect:
        detection_state.websocket_clients.discard(websocket)
        logger.info(f"✓ WebSocket client disconnected (total: {len(detection_state.websocket_clients)})")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        detection_state.websocket_clients.discard(websocket)

@app.get("/last-detection")
async def get_last_detection():
    """Lấy kết quả detection cuối cùng"""
    if not detection_state.last_detection_result:
        return {
            "status": "no_detection",
            "message": "Chưa có phát hiện nào"
        }
    
    return detection_state.last_detection_result

@app.get("/detected-images")
async def get_detected_images():
    """Danh sách ảnh phát hiện"""
    try:
        images = []
        for img_path in DETECTION_IMAGES_DIR.glob("*.jpg"):
            images.append({
                "id": img_path.stem,
                "filename": img_path.name,
                "timestamp": img_path.stat().st_mtime,
                "url": f"/detected-images/{img_path.stem}",
                "size": img_path.stat().st_size
            })
        
        # Sắp xếp theo thời gian mới nhất
        images.sort(key=lambda x: x['timestamp'], reverse=True)
        
        return {
            "status": "success",
            "count": len(images),
            "images": images[:50]  # Giới hạn 50 ảnh mới nhất
        }
    except Exception as e:
        logger.error(f"Error getting images: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/detected-images/{image_id}")
async def get_detected_image(image_id: str):
    """Lấy ảnh chi tiết"""
    try:
        image_path = DETECTION_IMAGES_DIR / f"{image_id}.jpg"
        
        if not image_path.exists():
            raise HTTPException(status_code=404, detail="Image not found")
        
        return FileResponse(image_path, media_type="image/jpeg")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving image: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ==================== HELPER FUNCTIONS ====================
def get_server_ip():
    """Lấy IP của server"""
    ips = get_server_ips()
    return ips[0] if ips else "127.0.0.1"

def get_server_ips() -> List[str]:
    """Lấy danh sách IP đang hoạt động của server theo mạng hiện tại."""
    candidates = []

    # IP theo route mặc định (mạng đang dùng để ra internet/LAN)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith("127."):
            candidates.append(ip)
    except Exception:
        pass

    # IP theo hostname (có thể có nhiều interface)
    try:
        host_ips = socket.gethostbyname_ex(socket.gethostname())[2]
        for ip in host_ips:
            if ip and not ip.startswith("127."):
                candidates.append(ip)
    except Exception:
        pass

    unique_ips = list(dict.fromkeys(candidates))
    return unique_ips if unique_ips else ["127.0.0.1"]

def is_port_in_use(port: int) -> bool:
    """Kiểm tra port có đang được tiến trình khác sử dụng không."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0

def is_same_webcam_server_running(port: int) -> bool:
    """Kiểm tra service trên port có phải server webcam này không."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/status", timeout=1.5) as response:
            if response.status != 200:
                return False
            data = json.loads(response.read().decode("utf-8", errors="ignore"))
            return data.get("server") == "Raspberry Pi - Webcam" and data.get("status") == "running"
    except Exception:
        return False

def _extract_pids_from_text(text: str) -> List[int]:
    """Tách PID từ output command."""
    pids = set()
    for token in text.replace("\n", " ").split():
        if token.isdigit():
            pids.add(int(token))
    return sorted(pids)

def get_pids_using_port(port: int) -> List[int]:
    """Lấy danh sách PID đang listen trên port."""
    commands = [
        ["lsof", "-t", f"-i:{port}", "-sTCP:LISTEN"],
        ["fuser", f"{port}/tcp"],
    ]

    for cmd in commands:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
            output = (result.stdout or "") + " " + (result.stderr or "")
            pids = _extract_pids_from_text(output)
            if pids:
                return pids
        except FileNotFoundError:
            continue
        except Exception:
            continue

    return []

def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False

def force_free_port(port: int, wait_seconds: float = 1.5) -> bool:
    """Cưỡng ép giải phóng port bằng cách kill tiến trình đang chiếm."""
    self_pid = os.getpid()
    pids = [pid for pid in get_pids_using_port(port) if pid != self_pid]
    if not pids:
        return False

    logger.warning(f"Force freeing port {port}, target PIDs: {pids}")

    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except PermissionError:
            logger.error(f"No permission to terminate PID {pid} on port {port}")

    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        alive = [pid for pid in pids if _pid_exists(pid)]
        if not alive:
            return True
        time.sleep(0.2)

    alive = [pid for pid in pids if _pid_exists(pid)]
    for pid in alive:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError:
            logger.error(f"No permission to kill PID {pid} on port {port}")

    still_alive = [pid for pid in pids if _pid_exists(pid)]
    return len(still_alive) == 0

# ==================== MAIN ====================
if __name__ == "__main__":
    import uvicorn

    # Bắt đầu server
    server_ips = get_server_ips()
    server_ip = server_ips[0]
    server_port = DEFAULT_PORT

    force_free_port(9000)

    if is_port_in_use(server_port):
        if is_same_webcam_server_running(server_port):
            logger.warning(f"Port {server_port} is used by existing webcam server instance. Restarting it.")
        else:
            logger.warning(f"Port {server_port} is used by another process. Force reclaiming.")
        force_free_port(server_port)

    if is_port_in_use(server_port):
        logger.error(f"❌ Port {server_port} is still in use and could not be freed.")
        logger.error("Try running with permissions that allow terminating the process (e.g. sudo).")
        exit(1)
    
    # Khởi tạo camera và model
    logger.info("Initializing camera and model...")
    detection_state.camera = init_camera()
    detection_state.model = init_model()
    
    if detection_state.camera is None:
        logger.error("❌ Camera initialization failed. Exiting.")
        exit(1)
    
    if detection_state.model is None:
        logger.error("❌ Model initialization failed. Exiting.")
        exit(1)

    if PRUNE_ON_STARTUP:
        prune_detected_images(MAX_SAVED_IMAGES)
    
    # Lấy event loop cho asyncio
    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    logger.info("\n" + "="*70)
    logger.info("🍓 Security Alert Server - Webcam Version (v3.0)")
    logger.info("="*70)
    logger.info(f"Hostname: {socket.gethostname()}")
    logger.info(f"IP Address(es): {', '.join(server_ips)}")
    logger.info(f"Port: {server_port}")
    logger.info("")
    logger.info("📊 Architecture:")
    logger.info("  Webcam USB → RPi → YOLO Detection → WebSocket → Web App")
    logger.info("")
    logger.info("🔗 Access URLs:")
    logger.info(f"  Local:    http://127.0.0.1:{server_port}")
    for ip in server_ips:
        logger.info(f"  Network:  http://{ip}:{server_port}")
    logger.info(f"  Web App:  http://{server_ip}:{WEB_APP_PORT}/web_app.html")
    logger.info(f"  Docs:     http://{server_ip}:{server_port}/docs")
    logger.info(f"  ReDoc:    http://{server_ip}:{server_port}/redoc")
    logger.info("")
    logger.info("📝 Next steps:")
    logger.info("  1. POST /start-detection - Bắt đầu detect")
    logger.info(f"  2. WS ws://IP:{server_port}/ws - Kết nối WebSocket")
    logger.info("="*70 + "\n")
    
    try:
        uvicorn.run(app, host="0.0.0.0", port=server_port, log_level="info")
    except KeyboardInterrupt:
        logger.info("\n⏹️  Shutting down...")
        detection_state.is_detecting = False
        if detection_state.camera:
            detection_state.camera.release()
