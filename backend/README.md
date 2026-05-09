# Backend

FastAPI application serving the Webcast control panel.

## Stack

- **FastAPI** + **uvicorn** — HTTP server on port 80
- **SQLite** — database at `/opt/webcast/data/webcast.db`
- **APScheduler** — cron-style jobs for stream automation
- **FFmpeg** — RTSP ingestion and RTMP output to YouTube
- **httpx** — async HTTP client for YouTube API and camera control

## Structure

```
backend/
├── main.py              # App factory, router registration, startup/shutdown
├── config.py            # All config read from environment via python-dotenv
├── database.py          # Schema definition, migrations, and all DB functions
├── routers/
│   ├── auth.py          # /auth — login, logout, session, user management
│   │                    #         auth dependencies (require_auth, require_admin,
│   │                    #         require_specialist_or_admin)
│   ├── admin.py         # /admin — wards, schedules, settings, thumbnails
│   ├── stream.py        # /stream — manual start/stop/pause/resume, status
│   ├── ptz.py           # /ptz — camera move/zoom/preset/home/status
│   └── youtube.py       # /youtube — OAuth device flow, ward status
└── services/
    ├── scheduler.py     # All APScheduler jobs:
    │                    #   - Daily schedule generation (00:01)
    │                    #   - Stream start/stop (per schedule)
    │                    #   - 1 AM cleanup (delete recordings, send emails,
    │                    #     create next broadcasts, purge expired data)
    │                    #   - 6 AM token health check
    │                    #   - Startup stream recovery
    ├── stream_manager.py # FFmpeg process lifecycle
    ├── youtube_api.py   # YouTube Data API v3 client (OAuth, broadcasts,
    │                    # streams, transitions, thumbnails, deletion)
    ├── camera.py        # PTZ camera HTTP CGI client
    └── email.py         # SMTP email (attendance reports, alerts)
```

## API Routes

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/auth/login` | — | Create session |
| POST | `/auth/logout` | User | Delete session |
| GET | `/auth/me` | User | Current user info |
| GET | `/auth/users` | Admin | List users |
| POST | `/auth/users` | Admin | Create user |
| DELETE | `/auth/users/{id}` | Admin | Delete user |
| GET | `/admin/wards` | Specialist+ | List wards |
| POST | `/admin/wards` | Admin | Create ward |
| PUT | `/admin/wards/{id}` | Specialist+ | Update ward |
| GET | `/admin/schedules` | Specialist+ | List schedules |
| POST | `/admin/schedules` | Specialist+ | Create schedule |
| PUT | `/admin/schedules/{id}` | Specialist+ | Update schedule |
| DELETE | `/admin/schedules/{id}` | Specialist+ | Delete schedule |
| POST | `/admin/schedules/{id}/thumbnail` | Admin | Upload thumbnail |
| DELETE | `/admin/schedules/{id}/thumbnail` | Admin | Remove thumbnail |
| POST | `/admin/run-cleanup` | Admin | Trigger 1 AM cleanup manually |
| GET | `/stream/status` | User | Current stream state |
| POST | `/stream/start` | Admin | Manual stream start |
| POST | `/stream/stop` | Admin | Manual stream stop |
| POST | `/stream/pause` | Admin | Pause (switch to pause image) |
| POST | `/stream/resume` | Admin | Resume from pause |
| GET | `/ptz/presets` | User | List presets |
| POST | `/ptz/presets/{id}/goto` | User | Go to preset |
| POST | `/ptz/move` | User | Directional move |
| POST | `/ptz/zoom` | User | Zoom in/out/stop |
| GET | `/youtube/status` | Admin | Ward authorization status |
| POST | `/youtube/auth/start` | Admin | Begin device auth flow |
| POST | `/youtube/auth/poll` | Admin | Poll for auth completion |
| POST | `/youtube/disconnect` | Admin | Remove ward authorization |

## Environment Variables

See `../.env.example` for all configuration options.

## Database Schema

Key tables:

- **users** — username, password_hash, is_admin, is_specialist, specialist_ward_id
- **sessions** — token, user_id, expires_at
- **wards** — name, email_addresses, youtube_* credentials and stream info
- **schedules** — ward_id, day_of_week, start_time, duration, is_recurring, one_off_date
- **schedule_exceptions** — schedule_id, exception_date (skip dates)
- **ptz_presets** — name, pan, tilt, zoom, speeds, is_default, sort_order
- **stream_sessions** — ward_id, schedule_id, status, youtube_broadcast_id, view counts
- **settings** — key/value store (testing_mode, etc.)
