#!/bin/bash
# Webcast - Setup Script

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# Fixed install directories
INSTALL_DIR="/opt/webcast"
GO2RTC_DIR="/opt/go2rtc"

echo -e "${GREEN}Webcast Setup${NC}"
echo "=============="

# Install system dependencies
echo -e "${YELLOW}Installing system dependencies...${NC}"
sudo apt update
sudo apt install -y python3 python3-venv python3-pip ffmpeg curl vim

# Check if go2rtc is set up
if ! systemctl is-enabled go2rtc &>/dev/null; then
    echo -e "${RED}go2rtc is not set up.${NC}"
    echo ""
    echo "Run this first:"
    echo "  ./setup-go2rtc.sh <camera_ip>"
    echo ""
    echo "Example:"
    echo "  ./setup-go2rtc.sh 192.168.1.100"
    exit 1
fi

echo -e "${GREEN}go2rtc detected${NC}"

# Get the directory where this script is located (source files)
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Copy to /opt/webcast if not already there
if [ "$SOURCE_DIR" != "$INSTALL_DIR" ]; then
    echo -e "${YELLOW}Installing to $INSTALL_DIR...${NC}"
    sudo mkdir -p "$INSTALL_DIR"
    sudo cp -r "$SOURCE_DIR/backend" "$INSTALL_DIR/"
    sudo cp -r "$SOURCE_DIR/assets" "$INSTALL_DIR/"
    sudo mkdir -p "$INSTALL_DIR/data"
    sudo cp "$SOURCE_DIR/requirements.txt" "$INSTALL_DIR/"
    sudo cp "$SOURCE_DIR/run.sh" "$INSTALL_DIR/"
    sudo cp "$SOURCE_DIR/.env.example" "$INSTALL_DIR/"
    # Keep source .env if it exists
    [ -f "$SOURCE_DIR/.env" ] && sudo cp "$SOURCE_DIR/.env" "$INSTALL_DIR/"
    # Set ownership
    sudo chown -R 1000:1000 "$INSTALL_DIR"
else
    # Make sure data directory exists even when running from /opt/webcast
    sudo mkdir -p "$INSTALL_DIR/data"
    sudo chown -R 1000:1000 "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"

# Create virtual environment
if [ ! -d "venv" ]; then
    echo -e "${YELLOW}Creating virtual environment...${NC}"
    sudo python3 -m venv venv
    sudo chown -R 1000:1000 venv
fi

# Install dependencies
echo -e "${YELLOW}Installing Python dependencies...${NC}"
sudo ./venv/bin/pip install -r requirements.txt --quiet

# Create/update .env if needed
if [ -f ".env" ]; then
    echo ""
    echo -e "${GREEN}Found existing .env file - using current configuration${NC}"
    echo ""
else
    echo ""
    echo -e "${YELLOW}First-time setup - configuring environment${NC}"
    echo ""
    
    # Try to get camera IP from go2rtc config
    CAMERA_IP=""
    if [ -f "$GO2RTC_DIR/go2rtc.yaml" ]; then
        CAMERA_IP=$(grep -oP 'rtsp://\K[0-9.]+' "$GO2RTC_DIR/go2rtc.yaml" | head -1)
    fi
    
    if [ -n "$CAMERA_IP" ]; then
        echo -e "Camera IP (from go2rtc): ${GREEN}$CAMERA_IP${NC}"
    else
        read -p "Camera IP address: " CAMERA_IP
    fi
    
    echo ""
    echo "Google OAuth Configuration (from Google Cloud Console):"
    read -p "  Google Client ID: " GOOGLE_CLIENT_ID
    read -p "  Google Client Secret: " GOOGLE_CLIENT_SECRET
    
    echo ""
    echo "SMTP Configuration (for attendance email reports):"
    read -p "  SMTP Server: " SMTP_SERVER
    read -p "  SMTP Port [465]: " SMTP_PORT
    SMTP_PORT=${SMTP_PORT:-465}
    read -p "  SMTP Username: " SMTP_USERNAME
    read -sp "  SMTP Password: " SMTP_PASSWORD
    echo ""
    read -p "  Admin BCC Email (optional): " ADMIN_BCC_EMAIL
    
    # Generate session secret
    SESSION_SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    
    # Write .env file
    sudo tee .env > /dev/null << EOF
# Webcast Configuration

# Camera
CAMERA_IP=$CAMERA_IP

# Google OAuth (shared by all wards)
GOOGLE_CLIENT_ID=$GOOGLE_CLIENT_ID
GOOGLE_CLIENT_SECRET=$GOOGLE_CLIENT_SECRET

# SMTP
SMTP_SERVER=$SMTP_SERVER
SMTP_PORT=$SMTP_PORT
SMTP_USE_SSL=true
SMTP_USERNAME=$SMTP_USERNAME
SMTP_PASSWORD=$SMTP_PASSWORD

# Optional
ADMIN_BCC_EMAIL=$ADMIN_BCC_EMAIL
ATTENDANCE_MULTIPLIER=2.7

# Session (auto-generated)
SESSION_SECRET_KEY=$SESSION_SECRET_KEY

# Server
PORT=80
GO2RTC_API=http://127.0.0.1:1984
EOF
    
    echo ""
    echo -e "${GREEN}Configuration saved to $INSTALL_DIR/.env${NC}"
    
    # Set ownership on new .env
    sudo chown 1000:1000 "$INSTALL_DIR/.env"
fi

# Load environment
export $(grep -v '^#' .env | grep -v '^$' | xargs)

# Validate required settings
MISSING=""
[ -z "$CAMERA_IP" ] && MISSING="$MISSING CAMERA_IP"
[ -z "$GOOGLE_CLIENT_ID" ] && MISSING="$MISSING GOOGLE_CLIENT_ID"
[ -z "$GOOGLE_CLIENT_SECRET" ] && MISSING="$MISSING GOOGLE_CLIENT_SECRET"
[ -z "$SMTP_SERVER" ] && MISSING="$MISSING SMTP_SERVER"
[ -z "$SESSION_SECRET_KEY" ] && MISSING="$MISSING SESSION_SECRET_KEY"

if [ -n "$MISSING" ]; then
    echo -e "${RED}Missing required settings in .env:${NC}"
    echo "$MISSING"
    echo ""
    echo "Edit $INSTALL_DIR/.env and run setup.sh again."
    exit 1
fi

# Check for pause image
if [ ! -f "assets/pause.png" ]; then
    echo -e "${YELLOW}Note: No pause.png in $INSTALL_DIR/assets/${NC}"
    echo "Add a 1080p image to show during sacrament pause."
else
    # Generate pause video if needed
    if [ ! -f "assets/pause.mp4" ]; then
        echo -e "${YELLOW}Generating pause video...${NC}"
        ffmpeg -y -loop 1 -i assets/pause.png \
            -f lavfi -i anullsrc=channel_layout=stereo:sample_rate=44100 \
            -c:v libx264 -preset medium -tune stillimage -crf 23 -pix_fmt yuv420p \
            -c:a aac -b:a 128k -t 60 -shortest \
            assets/pause.mp4 2>/dev/null
        echo -e "${GREEN}Generated pause.mp4${NC}"
    fi
fi

# Ensure data directory exists with proper permissions
sudo mkdir -p "$INSTALL_DIR/data"
sudo chown 1000:1000 "$INSTALL_DIR/data"

# Initialize database
echo -e "${YELLOW}Initializing database...${NC}"
cd "$INSTALL_DIR/backend"
sudo -u \#1000 ../venv/bin/python -c "from database import init_db, seed_initial_data; init_db(); seed_initial_data()"
cd ..

# Ensure database has correct ownership
sudo chown 1000:1000 "$INSTALL_DIR/data/webcast.db" 2>/dev/null || true

# Create systemd service
echo -e "${YELLOW}Creating systemd service...${NC}"
SERVICE_FILE="/etc/systemd/system/webcast.service"

sudo tee "$SERVICE_FILE" > /dev/null << EOF
[Unit]
Description=Webcast
After=network.target go2rtc.service
Wants=go2rtc.service

[Service]
Type=simple
User=1000
Group=1000
WorkingDirectory=$INSTALL_DIR
EnvironmentFile=$INSTALL_DIR/.env
ExecStart=$INSTALL_DIR/venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port ${PORT:-80} --app-dir $INSTALL_DIR/backend
Restart=always
RestartSec=5
AmbientCapabilities=CAP_NET_BIND_SERVICE

[Install]
WantedBy=multi-user.target
EOF

# Enable and restart service
echo -e "${YELLOW}Enabling and restarting webcast service...${NC}"
sudo systemctl daemon-reload
sudo systemctl enable webcast
sudo systemctl restart webcast

# Check status
sleep 2
if sudo systemctl is-active --quiet webcast; then
    IP=$(hostname -I | awk '{print $1}')
    echo ""
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}Webcast is running!${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo ""
    echo "Web UI: http://$IP/"
    echo ""
    echo "Default credentials:"
    echo -e "  Admin:  admin / admin  ${RED}(CHANGE THIS!)${NC}"
    echo "  Viewer: webcast / alma3738"
    echo ""
    echo "Next steps:"
    echo "  1. Login as admin"
    echo "  2. Change admin password"
    echo "  3. Add wards (with Google OAuth credentials)"
    echo "  4. Add PTZ presets"
    echo "  5. Create schedules"
    echo ""
    echo "Service commands:"
    echo "  sudo systemctl status webcast"
    echo "  sudo systemctl restart webcast"
    echo "  sudo journalctl -u webcast -f"
    echo ""
    echo "Config: $INSTALL_DIR/.env"
    echo "Data:   $INSTALL_DIR/data/"
else
    echo -e "${RED}Webcast failed to start. Check logs:${NC}"
    echo "  sudo journalctl -u webcast -n 50"
fi
