#!/bin/bash
# Webcast - go2rtc Setup Script
# Usage: ./setup-go2rtc.sh <camera_ip>
# Example: ./setup-go2rtc.sh 192.168.1.100

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# Fixed install directory
GO2RTC_DIR="/opt/go2rtc"

# Require camera IP argument
if [ -z "$1" ]; then
    echo -e "${RED}Error: Camera IP address is required${NC}"
    echo ""
    echo "Usage: $0 <camera_ip>"
    echo "Example: $0 192.168.1.100"
    exit 1
fi

CAMERA_IP="$1"

# Validate IP format (basic check)
if ! [[ "$CAMERA_IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo -e "${RED}Error: Invalid IP address format: $CAMERA_IP${NC}"
    exit 1
fi

# Detect architecture
ARCH=$(uname -m)
case $ARCH in
    aarch64|arm64)
        GO2RTC_ARCH="arm64"
        ;;
    armv7l|armhf)
        GO2RTC_ARCH="arm"
        ;;
    x86_64|amd64)
        GO2RTC_ARCH="amd64"
        ;;
    i386|i686)
        GO2RTC_ARCH="i386"
        ;;
    *)
        echo -e "${RED}Unsupported architecture: $ARCH${NC}"
        exit 1
        ;;
esac

echo -e "${GREEN}go2rtc Setup for Webcast${NC}"
echo "========================="
echo "Camera IP: $CAMERA_IP"
echo "Architecture: $ARCH -> $GO2RTC_ARCH"
echo "Install directory: $GO2RTC_DIR"
echo ""

# Create directory
echo -e "${YELLOW}Creating go2rtc directory...${NC}"
sudo mkdir -p "$GO2RTC_DIR"
sudo chown 1000:1000 "$GO2RTC_DIR"

# Download latest go2rtc binary
echo -e "${YELLOW}Downloading go2rtc...${NC}"
DOWNLOAD_URL="https://github.com/AlexxIT/go2rtc/releases/latest/download/go2rtc_linux_${GO2RTC_ARCH}"

if command -v curl &> /dev/null; then
    sudo curl -L -o "$GO2RTC_DIR/go2rtc" "$DOWNLOAD_URL"
elif command -v wget &> /dev/null; then
    sudo wget -O "$GO2RTC_DIR/go2rtc" "$DOWNLOAD_URL"
else
    echo -e "${RED}Neither curl nor wget found. Please install one.${NC}"
    exit 1
fi

sudo chmod +x "$GO2RTC_DIR/go2rtc"
sudo chown 1000:1000 "$GO2RTC_DIR/go2rtc"
echo -e "${GREEN}Downloaded go2rtc binary${NC}"

# Create configuration file
echo -e "${YELLOW}Creating configuration...${NC}"
sudo tee "$GO2RTC_DIR/go2rtc.yaml" > /dev/null << EOF
# go2rtc configuration for Webcast
# Camera IP: $CAMERA_IP

api:
  listen: ":1984"

rtsp:
  listen: ":8554"

webrtc:
  listen: ":8555"
  candidates:
    - stun:stun.l.google.com:19302

streams:
  # HD stream (main)
  chapel_hd:
    - rtsp://${CAMERA_IP}/1
  
  # SD stream (preview - lower bandwidth)
  chapel_sd:
    - rtsp://${CAMERA_IP}/2

log:
  level: info
EOF

echo -e "${GREEN}Created go2rtc.yaml${NC}"

# Set ownership
sudo chown -R 1000:1000 "$GO2RTC_DIR"

# Create systemd service
echo -e "${YELLOW}Creating systemd service...${NC}"
SERVICE_FILE="/etc/systemd/system/go2rtc.service"

sudo tee "$SERVICE_FILE" > /dev/null << EOF
[Unit]
Description=go2rtc streaming server
After=network.target

[Service]
Type=simple
User=1000
Group=1000
WorkingDirectory=$GO2RTC_DIR
ExecStart=$GO2RTC_DIR/go2rtc -config $GO2RTC_DIR/go2rtc.yaml
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

echo -e "${GREEN}Created systemd service${NC}"

# Enable and start service
echo -e "${YELLOW}Enabling and starting go2rtc service...${NC}"
sudo systemctl daemon-reload
sudo systemctl enable go2rtc
sudo systemctl start go2rtc

# Check status
sleep 2
if sudo systemctl is-active --quiet go2rtc; then
    echo ""
    echo -e "${GREEN}go2rtc is running!${NC}"
    echo ""
    echo "Web UI:     http://$(hostname -I | awk '{print $1}'):1984"
    echo "WebRTC:     http://$(hostname -I | awk '{print $1}'):1984/stream.html?src=chapel_sd"
    echo "RTSP proxy: rtsp://$(hostname -I | awk '{print $1}'):8554/chapel_hd"
    echo ""
    echo "Useful commands:"
    echo "  sudo systemctl status go2rtc    # Check status"
    echo "  sudo systemctl restart go2rtc   # Restart"
    echo "  sudo systemctl stop go2rtc      # Stop"
    echo "  sudo journalctl -u go2rtc -f    # View logs"
    echo ""
    echo "Next step: Run ./setup.sh"
else
    echo -e "${RED}go2rtc failed to start. Check logs:${NC}"
    echo "  sudo journalctl -u go2rtc -n 50"
fi
