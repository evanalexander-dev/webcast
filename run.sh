#!/bin/bash
# Webcast - Manual Run Script (for development/debugging)
# Normally the systemd service handles this

INSTALL_DIR="/opt/webcast"
cd "$INSTALL_DIR"

if [ -f ".env" ]; then
    export $(grep -v '^#' .env | grep -v '^$' | xargs)
fi

source venv/bin/activate
cd backend
exec python3 -m uvicorn main:app --host 0.0.0.0 --port ${PORT:-80}
