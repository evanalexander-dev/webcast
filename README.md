# Webcast

A self-hosted live streaming control panel for church broadcasts, running on a Raspberry Pi. Webcast automates scheduling, starting, and monitoring YouTube Live streams, with a web-based camera control interface and attendance reporting.

![Version](https://img.shields.io/badge/version-1.4.7-blue)
![Python](https://img.shields.io/badge/python-3.11+-green)
![License](https://img.shields.io/badge/license-MIT-lightgrey)

---

## Features

- **Automated scheduling** — broadcasts start and stop automatically based on a weekly recurring schedule
- **YouTube Live integration** — creates broadcasts, manages stream keys, transitions broadcast states, and deletes recordings after processing
- **PTZ camera control** — web-based directional pad, zoom, and preset positions for a network-connected PTZ camera
- **Multi-ward support** — manages separate YouTube channels and schedules for multiple congregations
- **Attendance reporting** — fetches view counts after each broadcast and emails a summary to ward leaders
- **Role-based access** — Admin, Specialist (ward-level schedule management), and Viewer roles
- **Stream recovery** — detects and recovers interrupted streams on service restart
- **Live preview** — embedded RTSP-to-WebRTC preview via go2rtc

## Architecture

```
Raspberry Pi
├── Webcast (FastAPI + uvicorn on port 80)
│   ├── SQLite database
│   ├── APScheduler (cron jobs for stream start/stop/cleanup)
│   └── FFmpeg (RTSP → RTMP to YouTube)
├── go2rtc (RTSP → WebRTC for browser preview)
└── Nginx (optional reverse proxy)
```

## Requirements

### Hardware
- Raspberry Pi 4 (2GB+ RAM recommended) or any Linux system
- Network-connected PTZ camera with HTTP CGI control (tested with ClearTouch RL500)
- Stable internet connection (10+ Mbps upload for 1080p streaming)

### Software
- Raspberry Pi OS (Bookworm) or Ubuntu 22.04+
- Python 3.11+
- FFmpeg
- go2rtc

### Google / YouTube
- Google Cloud project with YouTube Data API v3 enabled
- OAuth 2.0 client credentials (Desktop app type)
- YouTube channel(s) with live streaming enabled

## Installation

```bash
git clone https://github.com/evanalexander-dev/webcast.git
cd webcast
./setup.sh
```

The setup script will:
1. Install Python dependencies
2. Install go2rtc if not present
3. Create the data directories
4. Prompt for initial configuration if `.env` doesn't exist
5. Configure and start the `webcast` systemd service

## Configuration

Copy `.env.example` to `/opt/webcast/.env` and fill in the values:

```env
# Google OAuth (Desktop app credentials from Google Cloud Console)
GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-client-secret

# Camera
CAMERA_IP=192.168.1.100

# Email (SMTP for attendance reports)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your@email.com
SMTP_PASSWORD=your-app-password
ADMIN_BCC_EMAIL=admin@email.com

# Attendance
ATTENDANCE_MULTIPLIER=2.2

# Stream timing (minutes)
STREAM_PRE_ROLL_MINUTES=10
STREAM_POST_ROLL_MINUTES=15

# Cleanup schedule (24-hour time)
RECORDING_DELETE_HOUR=1
RECORDING_DELETE_MINUTE=0
```

## Setup Guide

### 1. YouTube API credentials

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create a project (or use an existing one)
3. Enable the **YouTube Data API v3**
4. Create OAuth 2.0 credentials → **Desktop app** type
5. Add your Google account as a test user under OAuth consent screen
6. Copy the client ID and secret to `.env`

### 2. Connect YouTube channels

1. Open Webcast in your browser (`http://<pi-ip>/`)
2. Log in as admin (default: `admin` / `admin` — **change this immediately**)
3. Go to **Settings** → add your ward(s)
4. Go to **YouTube** → click **Connect** for each ward
5. Follow the device authorization flow (visit the URL shown, enter the code)

### 3. Configure schedules

Go to **Schedule** → add a recurring schedule for each ward with the day of week, meeting start time, duration, and ward assignment. Webcast will automatically create the next YouTube broadcast and start/stop the stream.

### 4. Camera presets

Go to **Control** → **Edit Presets** to add PTZ presets. Position the camera manually, enter the pan/tilt/zoom values. One preset can be marked as default — it fires automatically when a scheduled stream starts.

## Usage

### Service management

```bash
sudo systemctl status webcast
sudo systemctl restart webcast
sudo journalctl -u webcast -f
sudo journalctl -u webcast --since "08:00" --until "12:00"
```

### Daily operations

Webcast is fully automated for normal Sunday broadcasts. The manual controls (Control tab) are available for starting/stopping streams outside the schedule, switching camera presets during a broadcast, and pausing the stream (e.g., for sacrament).

### Cleanup

The 1 AM daily cleanup routine fetches view counts, deletes recordings, sends attendance emails, creates next week's broadcasts, and removes expired schedule exceptions. Trigger manually from **Settings → Run Cleanup**.

## Roles

| Feature | Viewer | Specialist | Admin |
|---------|--------|-----------|-------|
| View stream status | ✓ | ✓ | ✓ |
| Camera controls & presets | ✓ | ✓ | ✓ |
| Manage ward schedules | — | Own ward only | ✓ |
| Edit ward email addresses | — | Own ward only | ✓ |
| Edit presets | — | — | ✓ |
| Full admin access | — | — | ✓ |

## Token Migration

If migrating from the original webcast script, use the included migration tool to import existing OAuth tokens:

```bash
python3 /opt/webcast/migrate_tokens.py
```

## File Structure

```
webcast/
├── backend/
│   ├── main.py              # FastAPI application entry point
│   ├── config.py            # Configuration from environment
│   ├── database.py          # SQLite schema and data access
│   ├── routers/             # API route handlers
│   ├── services/            # Business logic and external integrations
│   └── static/              # Frontend (vanilla JS, no build step)
├── docs/                    # GitHub Pages (homepage, privacy policy)
├── setup.sh                 # Installation script
├── migrate_tokens.py        # Token migration from old app
└── .env.example             # Configuration template
```

## License

MIT — see [LICENSE](LICENSE)
