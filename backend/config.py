"""
Webcast - Configuration
"""
import os
from pathlib import Path

# Base paths
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
ASSETS_DIR = BASE_DIR / "assets"
THUMBNAILS_DIR = DATA_DIR / "thumbnails"

# Ensure directories exist
DATA_DIR.mkdir(exist_ok=True)
THUMBNAILS_DIR.mkdir(exist_ok=True)

# Database
DATABASE_PATH = DATA_DIR / "webcast.db"

# Google OAuth (Device Flow) - shared by all wards
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_DEVICE_AUTH_URL = "https://oauth2.googleapis.com/device/code"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/youtube"
]

# Camera settings
CAMERA_IP = os.environ.get("CAMERA_IP", "")
CAMERA_RTSP_HD = f"rtsp://{CAMERA_IP}/1" if CAMERA_IP else ""
CAMERA_RTSP_SD = f"rtsp://{CAMERA_IP}/2" if CAMERA_IP else ""
CAMERA_CGI_BASE = f"http://{CAMERA_IP}/cgi-bin" if CAMERA_IP else ""

# PTZ speed defaults
PTZ_PAN_SPEED = 12  # 1-24
PTZ_TILT_SPEED = 10  # 1-20
PTZ_ZOOM_SPEED = 4   # 1-7

# go2rtc settings
GO2RTC_API = os.environ.get("GO2RTC_API", "http://127.0.0.1:1984")
GO2RTC_WEBRTC_PORT = 8555

# go2rtc stream names (internal convention)
GO2RTC_STREAM_HD = "chapel_hd"
GO2RTC_STREAM_SD = "chapel_sd"

# Streaming settings (applied automatically by frontend)
STREAM_PRE_ROLL_MINUTES = int(os.environ.get("STREAM_PRE_ROLL_MINUTES", "10"))
STREAM_POST_ROLL_MINUTES = int(os.environ.get("STREAM_POST_ROLL_MINUTES", "15"))

# Pause video
PAUSE_IMAGE_PATH = ASSETS_DIR / "pause.png"
PAUSE_VIDEO_PATH = ASSETS_DIR / "pause.mp4"
PAUSE_VIDEO_DURATION_SECONDS = 60  # Loop length

# SMTP settings
SMTP_SERVER = os.environ.get("SMTP_SERVER", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
SMTP_USE_SSL = os.environ.get("SMTP_USE_SSL", "true").lower() == "true"
SMTP_USERNAME = os.environ.get("SMTP_USERNAME", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
ADMIN_BCC_EMAIL = os.environ.get("ADMIN_BCC_EMAIL", "")

# Attendance calculation
ATTENDANCE_MULTIPLIER = float(os.environ.get("ATTENDANCE_MULTIPLIER", "2.7"))

# Session settings
SESSION_SECRET_KEY = os.environ.get("SESSION_SECRET_KEY", "change-me-in-production-use-random-string")
SESSION_EXPIRE_HOURS = 24

# Recording deletion time (1 AM next day)
RECORDING_DELETE_HOUR = 1
RECORDING_DELETE_MINUTE = 0

# Server settings
HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", "80"))
