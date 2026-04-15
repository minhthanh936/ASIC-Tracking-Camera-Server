#!/bin/bash

# 🍓 Quick Setup Script - Webcam Server on Raspberry Pi
# Chạy script này để setup nhanh

set -e

echo "========================================"
echo "🍓 Webcam Server Quick Setup"
echo "========================================"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Check if running on RPi
if ! grep -q "Raspberry" /proc/cpuinfo; then
    echo -e "${YELLOW}⚠️  Not running on Raspberry Pi${NC}"
    echo "This script should run on RPi"
    read -p "Continue anyway? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Step 1: Create project directory
echo ""
echo -e "${GREEN}Step 1: Creating project directory${NC}"
PROJECT_DIR="$HOME/security-alert-server"
mkdir -p "$PROJECT_DIR"
cd "$PROJECT_DIR"
echo "✓ Directory: $PROJECT_DIR"

# Step 2: Create virtual environment
echo ""
echo -e "${GREEN}Step 2: Creating virtual environment${NC}"
if [ -d "venv" ]; then
    echo "⚠️  venv already exists, skipping..."
else
    python3 -m venv venv
    echo "✓ Virtual environment created"
fi

# Step 3: Activate venv and upgrade pip
echo ""
echo -e "${GREEN}Step 3: Upgrading pip${NC}"
source venv/bin/activate
pip install --upgrade pip setuptools wheel > /dev/null 2>&1
echo "✓ pip upgraded"

# Step 4: Create requirements
echo ""
echo -e "${GREEN}Step 4: Creating requirements${NC}"
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
echo "✓ requirements-webcam.txt created"

# Step 5: Install dependencies
echo ""
echo -e "${GREEN}Step 5: Installing dependencies (this may take 10-15 min)${NC}"
pip install -q -r requirements-webcam.txt
echo "✓ Dependencies installed"

# Step 6: Check if files exist
echo ""
echo -e "${GREEN}Step 6: Checking required files${NC}"
FILES=("Server_Webcam.py" "best.pt" "web_app.html")
MISSING=0

for file in "${FILES[@]}"; do
    if [ -f "$file" ]; then
        echo "✓ $file found"
    else
        echo "⚠️  $file NOT found - copy it to $PROJECT_DIR"
        MISSING=$((MISSING + 1))
    fi
done

# Step 7: Create detected_images directory
echo ""
echo -e "${GREEN}Step 7: Creating detected_images directory${NC}"
mkdir -p detected_images
chmod 755 detected_images
echo "✓ detected_images/ created"

# Step 8: Test camera
echo ""
echo -e "${GREEN}Step 8: Testing camera${NC}"
python3 << 'PYEOF'
import cv2
print("Testing camera...")
for i in range(3):
    cap = cv2.VideoCapture(i)
    if cap.isOpened():
        ret, frame = cap.read()
        if ret:
            print(f"✓ Camera {i}: OK (resolution: {frame.shape[1]}x{frame.shape[0]})")
        cap.release()
        break
else:
    print("⚠️  No camera found - check USB connection")
PYEOF

# Step 9: Create systemd service
echo ""
echo -e "${GREEN}Step 9: Creating systemd service${NC}"
sudo bash << SUDOEOF
cat > /etc/systemd/system/security-alert-webcam.service << 'EOF'
[Unit]
Description=Security Alert Server - Webcam
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=$PROJECT_DIR
Environment="PATH=$PROJECT_DIR/venv/bin"
ExecStart=$PROJECT_DIR/venv/bin/python $PROJECT_DIR/Server_Webcam.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable security-alert-webcam.service
echo "✓ Systemd service created and enabled"
SUDOEOF

# Step 10: Show next steps
echo ""
echo "========================================"
echo -e "${GREEN}✓ Setup Complete!${NC}"
echo "========================================"
echo ""
echo "📋 Next Steps:"
echo ""
echo "1. Start the server:"
echo -e "   ${YELLOW}sudo systemctl start security-alert-webcam.service${NC}"
echo ""
echo "2. Check status:"
echo -e "   ${YELLOW}sudo systemctl status security-alert-webcam.service${NC}"
echo ""
echo "3. View logs:"
echo -e "   ${YELLOW}sudo journalctl -u security-alert-webcam.service -f${NC}"
echo ""
echo "4. Open web app (from any browser on same network):"
echo -e "   ${YELLOW}http://$(hostname -I | awk '{print $1}'):8000${NC}"
echo ""
echo "5. Test API:"
echo -e "   ${YELLOW}curl http://$(hostname -I | awk '{print $1}'):8000/status${NC}"
echo ""
echo "📚 For detailed guide, see: SETUP_WEBCAM.md"
echo ""
echo "========================================"
