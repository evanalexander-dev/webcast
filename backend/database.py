"""
Webcast - Database Models and Setup
"""
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from contextlib import contextmanager
from typing import Optional, List, Dict, Any
import json
import hashlib
import secrets

from config import DATABASE_PATH, STREAM_PRE_ROLL_MINUTES, STREAM_POST_ROLL_MINUTES


def get_db_connection():
    """Get a database connection with row factory."""
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def get_db():
    """Context manager for database connections."""
    conn = get_db_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Initialize the database schema."""
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Users table (simple auth)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                is_admin BOOLEAN DEFAULT FALSE,
                is_specialist BOOLEAN DEFAULT FALSE,
                specialist_ward_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Wards table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS wards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                email_addresses TEXT DEFAULT '[]',
                youtube_channel_id TEXT,
                youtube_access_token TEXT,
                youtube_refresh_token TEXT,
                youtube_token_expiry TIMESTAMP,
                youtube_stream_id TEXT,
                youtube_stream_key TEXT,
                youtube_rtmp_url TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Schedules table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ward_id INTEGER NOT NULL,
                day_of_week INTEGER DEFAULT 6,
                start_time TEXT NOT NULL,
                meeting_duration_minutes INTEGER DEFAULT 60,
                ptz_preset_id INTEGER,
                is_recurring BOOLEAN DEFAULT TRUE,
                one_off_date DATE,
                custom_title TEXT,
                broadcast_title TEXT,
                active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (ward_id) REFERENCES wards(id) ON DELETE CASCADE,
                FOREIGN KEY (ptz_preset_id) REFERENCES ptz_presets(id) ON DELETE SET NULL
            )
        """)
        
        # Schedule exceptions (skip specific dates)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS schedule_exceptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                schedule_id INTEGER NOT NULL,
                exception_date DATE NOT NULL,
                reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (schedule_id) REFERENCES schedules(id) ON DELETE CASCADE,
                UNIQUE(schedule_id, exception_date)
            )
        """)
        
        # PTZ presets (absolute positioning)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ptz_presets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                pan INTEGER NOT NULL DEFAULT 0,
                tilt INTEGER NOT NULL DEFAULT 0,
                zoom INTEGER NOT NULL DEFAULT 0,
                pan_speed INTEGER NOT NULL DEFAULT 12,
                tilt_speed INTEGER NOT NULL DEFAULT 10,
                zoom_speed INTEGER NOT NULL DEFAULT 4,
                is_default BOOLEAN NOT NULL DEFAULT FALSE,
                sort_order INTEGER NOT NULL DEFAULT 0,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Stream sessions (active and historical)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS stream_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ward_id INTEGER NOT NULL,
                schedule_id INTEGER,
                broadcast_title TEXT,
                youtube_broadcast_id TEXT,
                youtube_stream_id TEXT,
                youtube_stream_key TEXT,
                youtube_watch_url TEXT,
                youtube_rtmp_url TEXT,
                status TEXT DEFAULT 'pending',
                scheduled_start TIMESTAMP,
                actual_start TIMESTAMP,
                actual_end TIMESTAMP,
                peak_viewers INTEGER,
                estimated_attendance INTEGER,
                email_sent BOOLEAN DEFAULT FALSE,
                deleted_from_youtube BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (ward_id) REFERENCES wards(id) ON DELETE CASCADE,
                FOREIGN KEY (schedule_id) REFERENCES schedules(id) ON DELETE SET NULL
            )
        """)
        
        # App settings (key-value store)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Sessions for web auth
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)

        # Migrate users table — add specialist columns if missing
        cursor.execute("PRAGMA table_info(users)")
        user_columns = {row[1] for row in cursor.fetchall()}
        for col, defn in [
            ('is_specialist', 'BOOLEAN DEFAULT FALSE'),
            ('specialist_ward_id', 'INTEGER'),
        ]:
            if col not in user_columns:
                cursor.execute(f"ALTER TABLE users ADD COLUMN {col} {defn}")

        # Migrate ptz_presets table if it has old schema (preset_number column)
        cursor.execute("PRAGMA table_info(ptz_presets)")
        ptz_columns = {row[1] for row in cursor.fetchall()}
        if 'preset_number' in ptz_columns:
            # Old schema — drop and recreate (presets need to be re-saved anyway)
            cursor.execute("DROP TABLE ptz_presets")
            cursor.execute("""
                CREATE TABLE ptz_presets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    pan INTEGER NOT NULL DEFAULT 0,
                    tilt INTEGER NOT NULL DEFAULT 0,
                    zoom INTEGER NOT NULL DEFAULT 0,
                    pan_speed INTEGER NOT NULL DEFAULT 12,
                    tilt_speed INTEGER NOT NULL DEFAULT 10,
                    zoom_speed INTEGER NOT NULL DEFAULT 4,
                    is_default BOOLEAN NOT NULL DEFAULT FALSE,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    description TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
        else:
            # New schema — add any missing columns for forward compatibility
            for col, defn in [
                ('pan_speed', 'INTEGER NOT NULL DEFAULT 12'),
                ('tilt_speed', 'INTEGER NOT NULL DEFAULT 10'),
                ('zoom_speed', 'INTEGER NOT NULL DEFAULT 4'),
                ('is_default', 'BOOLEAN NOT NULL DEFAULT FALSE'),
                ('sort_order', 'INTEGER NOT NULL DEFAULT 0'),
            ]:
                if col not in ptz_columns:
                    cursor.execute(f"ALTER TABLE ptz_presets ADD COLUMN {col} {defn}")


def hash_password(password: str) -> str:
    """Hash a password with a random salt."""
    salt = secrets.token_hex(16)
    hash_obj = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 100000)
    return f"{salt}:{hash_obj.hex()}"


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password against its hash."""
    try:
        salt, hash_hex = password_hash.split(':')
        hash_obj = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 100000)
        return hash_obj.hex() == hash_hex
    except ValueError:
        return False


# =============================================================================
# User functions
# =============================================================================

def create_user(username: str, password: str, is_admin: bool = False,
                is_specialist: bool = False, specialist_ward_id: int = None) -> Optional[int]:
    """Create a new user."""
    with get_db() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO users (username, password_hash, is_admin, is_specialist, specialist_ward_id) VALUES (?, ?, ?, ?, ?)",
                (username, hash_password(password), is_admin, is_specialist, specialist_ward_id)
            )
            return cursor.lastrowid
        except sqlite3.IntegrityError:
            return None


def get_user_by_username(username: str) -> Optional[Dict]:
    """Get user by username."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
        row = cursor.fetchone()
        return dict(row) if row else None


def authenticate_user(username: str, password: str) -> Optional[Dict]:
    """Authenticate a user and return user data if successful."""
    user = get_user_by_username(username)
    if user and verify_password(password, user['password_hash']):
        return user
    return None


# =============================================================================
# Session functions
# =============================================================================

def create_session(user_id: int, hours: int = 168) -> str:
    """Create a new session token."""
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now() + timedelta(hours=hours)
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO sessions (token, user_id, expires_at) VALUES (?, ?, ?)",
            (token, user_id, expires_at)
        )
    return token


def get_session(token: str) -> Optional[Dict]:
    """Get session and user data if valid."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT s.*, u.username, u.is_admin, u.is_specialist, u.specialist_ward_id
            FROM sessions s 
            JOIN users u ON s.user_id = u.id 
            WHERE s.token = ? AND s.expires_at > ?
        """, (token, datetime.now()))
        row = cursor.fetchone()
        return dict(row) if row else None


def delete_session(token: str):
    """Delete a session."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM sessions WHERE token = ?", (token,))


def cleanup_expired_sessions():
    """Remove expired sessions."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM sessions WHERE expires_at < ?", (datetime.now(),))


# =============================================================================
# Ward functions
# =============================================================================

def create_ward(name: str, email_addresses: List[str] = None) -> int:
    """Create a new ward."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO wards (name, email_addresses) VALUES (?, ?)",
            (name, json.dumps(email_addresses or []))
        )
        return cursor.lastrowid


def get_ward(ward_id: int) -> Optional[Dict]:
    """Get ward by ID."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM wards WHERE id = ?", (ward_id,))
        row = cursor.fetchone()
        if row:
            ward = dict(row)
            ward['email_addresses'] = json.loads(ward['email_addresses'])
            return ward
        return None


def get_all_wards() -> List[Dict]:
    """Get all wards."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM wards ORDER BY name")
        wards = []
        for row in cursor.fetchall():
            ward = dict(row)
            ward['email_addresses'] = json.loads(ward['email_addresses'])
            wards.append(ward)
        return wards


def update_ward(ward_id: int, **kwargs) -> bool:
    """Update ward fields."""
    allowed_fields = ['name', 'email_addresses', 'youtube_channel_id', 
                      'youtube_access_token', 'youtube_refresh_token', 'youtube_token_expiry',
                      'youtube_stream_id', 'youtube_stream_key', 'youtube_rtmp_url']
    updates = {k: v for k, v in kwargs.items() if k in allowed_fields}
    
    if 'email_addresses' in updates and isinstance(updates['email_addresses'], list):
        updates['email_addresses'] = json.dumps(updates['email_addresses'])
    
    if not updates:
        return False
    
    set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
    values = list(updates.values()) + [ward_id]
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(f"UPDATE wards SET {set_clause} WHERE id = ?", values)
        return cursor.rowcount > 0


def delete_ward(ward_id: int) -> bool:
    """Delete a ward."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM wards WHERE id = ?", (ward_id,))
        return cursor.rowcount > 0


# =============================================================================
# Schedule functions
# =============================================================================

def create_schedule(ward_id: int, start_time: str, day_of_week: int = 6,
                    meeting_duration_minutes: int = 60, ptz_preset_id: int = None,
                    is_recurring: bool = True, one_off_date: str = None,
                    custom_title: str = None, broadcast_title: str = None,
                    active: bool = True) -> int:
    """Create a new schedule."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO schedules (ward_id, day_of_week, start_time, meeting_duration_minutes, 
                                   ptz_preset_id, is_recurring, one_off_date, custom_title, 
                                   broadcast_title, active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (ward_id, day_of_week, start_time, meeting_duration_minutes, ptz_preset_id,
              is_recurring, one_off_date, custom_title, broadcast_title, active))
        return cursor.lastrowid


def get_schedule(schedule_id: int) -> Optional[Dict]:
    """Get schedule by ID."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT s.*, w.name as ward_name 
            FROM schedules s 
            JOIN wards w ON s.ward_id = w.id 
            WHERE s.id = ?
        """, (schedule_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_schedules_for_day(day_of_week: int, check_date: str = None) -> List[Dict]:
    """Get all active schedules for a given day of week, including one-off schedules for that date."""
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Get recurring schedules for this day of week
        cursor.execute("""
            SELECT s.*, w.name as ward_name, w.youtube_channel_id
            FROM schedules s 
            JOIN wards w ON s.ward_id = w.id 
            WHERE s.day_of_week = ? AND s.active = TRUE AND s.is_recurring = TRUE
            ORDER BY s.start_time
        """, (day_of_week,))
        schedules = [dict(row) for row in cursor.fetchall()]
        
        # Also get one-off schedules for today's date if provided
        if check_date:
            cursor.execute("""
                SELECT s.*, w.name as ward_name, w.youtube_channel_id
                FROM schedules s 
                JOIN wards w ON s.ward_id = w.id 
                WHERE s.one_off_date = ? AND s.active = TRUE AND s.is_recurring = FALSE
                ORDER BY s.start_time
            """, (check_date,))
            schedules.extend([dict(row) for row in cursor.fetchall()])
        
        return schedules


def get_all_schedules() -> List[Dict]:
    """Get all schedules with ward info."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT s.*, w.name as ward_name 
            FROM schedules s 
            JOIN wards w ON s.ward_id = w.id 
            ORDER BY s.day_of_week, s.start_time
        """)
        return [dict(row) for row in cursor.fetchall()]


def update_schedule(schedule_id: int, **kwargs) -> bool:
    """Update schedule fields."""
    allowed_fields = ['day_of_week', 'start_time', 'meeting_duration_minutes', 
                      'ptz_preset_id', 'active', 'is_recurring', 'one_off_date', 
                      'custom_title', 'broadcast_title']
    updates = {k: v for k, v in kwargs.items() if k in allowed_fields}
    
    if not updates:
        return False
    
    set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
    values = list(updates.values()) + [schedule_id]
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(f"UPDATE schedules SET {set_clause} WHERE id = ?", values)
        return cursor.rowcount > 0


def delete_schedule(schedule_id: int) -> bool:
    """Delete a schedule."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))
        return cursor.rowcount > 0


def delete_expired_oneoff_schedules() -> int:
    """Delete one-off schedules where the date has passed. Returns count of deleted."""
    with get_db() as conn:
        cursor = conn.cursor()
        # Fetch IDs first so we can clean up thumbnail files
        cursor.execute("""
            SELECT id FROM schedules 
            WHERE is_recurring = FALSE 
            AND one_off_date < date('now')
        """)
        expired_ids = [row[0] for row in cursor.fetchall()]

        if not expired_ids:
            return 0

        # Delete thumbnail files for expired schedules
        try:
            from config import THUMBNAILS_DIR
            import logging
            _logger = logging.getLogger(__name__)
            for schedule_id in expired_ids:
                for ext in ("jpg", "png"):
                    thumb_path = THUMBNAILS_DIR / f"schedule_{schedule_id}.{ext}"
                    if thumb_path.exists():
                        thumb_path.unlink()
                        _logger.info(f"Removed thumbnail file for expired one-off schedule {schedule_id}")
        except Exception:
            pass

        cursor.execute("""
            DELETE FROM schedules 
            WHERE is_recurring = FALSE 
            AND one_off_date < date('now')
        """)
        return cursor.rowcount


# =============================================================================
# Schedule exception functions
# =============================================================================

def add_schedule_exception(schedule_id: int, exception_date: str, reason: str = None) -> Optional[int]:
    """Add a schedule exception (skip date)."""
    with get_db() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO schedule_exceptions (schedule_id, exception_date, reason) VALUES (?, ?, ?)",
                (schedule_id, exception_date, reason)
            )
            return cursor.lastrowid
        except sqlite3.IntegrityError:
            return None


def get_schedule_exceptions(schedule_id: int) -> List[Dict]:
    """Get all exceptions for a schedule."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM schedule_exceptions WHERE schedule_id = ? ORDER BY exception_date",
            (schedule_id,)
        )
        return [dict(row) for row in cursor.fetchall()]


def delete_schedule_exception(exception_id: int) -> bool:
    """Delete a schedule exception."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM schedule_exceptions WHERE id = ?", (exception_id,))
        return cursor.rowcount > 0


def delete_expired_schedule_exceptions() -> int:
    """Delete schedule exceptions whose date has passed. Returns count of deleted."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM schedule_exceptions WHERE exception_date < date('now')")
        return cursor.rowcount


def is_date_excepted(schedule_id: int, check_date: str) -> bool:
    """Check if a date is excepted for a schedule."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM schedule_exceptions WHERE schedule_id = ? AND exception_date = ?",
            (schedule_id, check_date)
        )
        return cursor.fetchone() is not None


# =============================================================================
# PTZ Preset functions
# =============================================================================

def create_ptz_preset(name: str, pan: int = 0, tilt: int = 0, zoom: int = 0,
                      pan_speed: int = 12, tilt_speed: int = 10, zoom_speed: int = 4,
                      is_default: bool = False, description: str = None) -> int:
    """Create a PTZ preset with absolute position."""
    with get_db() as conn:
        cursor = conn.cursor()
        if is_default:
            cursor.execute("UPDATE ptz_presets SET is_default = FALSE")
        cursor.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM ptz_presets")
        next_order = cursor.fetchone()[0]
        cursor.execute(
            """INSERT INTO ptz_presets (name, pan, tilt, zoom, pan_speed, tilt_speed, zoom_speed, is_default, sort_order, description)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (name, pan, tilt, zoom, pan_speed, tilt_speed, zoom_speed, is_default, next_order, description)
        )
        return cursor.lastrowid


def get_all_ptz_presets() -> List[Dict]:
    """Get all PTZ presets sorted by sort_order then name."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM ptz_presets ORDER BY sort_order, name")
        return [dict(row) for row in cursor.fetchall()]


def get_ptz_preset(preset_id: int) -> Optional[Dict]:
    """Get a specific PTZ preset."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM ptz_presets WHERE id = ?", (preset_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_default_ptz_preset() -> Optional[Dict]:
    """Get the default startup PTZ preset, if one is set."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM ptz_presets WHERE is_default = TRUE LIMIT 1")
        row = cursor.fetchone()
        return dict(row) if row else None


def update_ptz_preset(preset_id: int, **kwargs) -> bool:
    """Update PTZ preset."""
    allowed_fields = ['name', 'pan', 'tilt', 'zoom', 'pan_speed', 'tilt_speed', 'zoom_speed', 'is_default', 'sort_order', 'description']
    updates = {k: v for k, v in kwargs.items() if k in allowed_fields}

    if not updates:
        return False

    with get_db() as conn:
        cursor = conn.cursor()
        if updates.get('is_default'):
            cursor.execute("UPDATE ptz_presets SET is_default = FALSE WHERE id != ?", (preset_id,))
        set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
        values = list(updates.values()) + [preset_id]
        cursor.execute(f"UPDATE ptz_presets SET {set_clause} WHERE id = ?", values)
        return cursor.rowcount > 0


def move_ptz_preset(preset_id: int, direction: str) -> bool:
    """Move a preset up or down in sort order by swapping with its neighbour."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM ptz_presets ORDER BY sort_order, name")
        all_presets = [dict(r) for r in cursor.fetchall()]
        ids = [p['id'] for p in all_presets]
        if preset_id not in ids:
            return False
        idx = ids.index(preset_id)
        if direction == 'up' and idx == 0:
            return False
        if direction == 'down' and idx == len(ids) - 1:
            return False
        swap_idx = idx - 1 if direction == 'up' else idx + 1
        a, b = all_presets[idx], all_presets[swap_idx]
        cursor.execute("UPDATE ptz_presets SET sort_order = ? WHERE id = ?", (b['sort_order'], a['id']))
        cursor.execute("UPDATE ptz_presets SET sort_order = ? WHERE id = ?", (a['sort_order'], b['id']))
        # If sort_orders are equal (e.g. all 0 on first reorder), assign explicit values
        if a['sort_order'] == b['sort_order']:
            for i, p in enumerate(all_presets):
                cursor.execute("UPDATE ptz_presets SET sort_order = ? WHERE id = ?", (i, p['id']))
            new_order = list(range(len(all_presets)))
            new_order[idx], new_order[swap_idx] = new_order[swap_idx], new_order[idx]
            for i, p in enumerate(all_presets):
                cursor.execute("UPDATE ptz_presets SET sort_order = ? WHERE id = ?", (new_order[i], p['id']))
        return True


def delete_ptz_preset(preset_id: int) -> bool:
    """Delete a PTZ preset."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM ptz_presets WHERE id = ?", (preset_id,))
        return cursor.rowcount > 0


# =============================================================================
# Stream session functions
# =============================================================================

def create_stream_session(ward_id: int, schedule_id: int = None, 
                          scheduled_start: datetime = None) -> int:
    """Create a new stream session."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO stream_sessions (ward_id, schedule_id, scheduled_start, status)
            VALUES (?, ?, ?, 'pending')
        """, (ward_id, schedule_id, scheduled_start))
        return cursor.lastrowid


def get_stream_session(session_id: int) -> Optional[Dict]:
    """Get stream session by ID."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT ss.*, w.name as ward_name 
            FROM stream_sessions ss 
            JOIN wards w ON ss.ward_id = w.id 
            WHERE ss.id = ?
        """, (session_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_active_stream_session() -> Optional[Dict]:
    """Get currently active stream session with schedule info."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT ss.*, w.name as ward_name,
                   s.broadcast_title as schedule_broadcast_title, s.custom_title, s.is_recurring
            FROM stream_sessions ss 
            JOIN wards w ON ss.ward_id = w.id 
            LEFT JOIN schedules s ON ss.schedule_id = s.id
            WHERE ss.status IN ('live', 'paused')
            ORDER BY ss.created_at DESC LIMIT 1
        """)
        row = cursor.fetchone()
        if row:
            result = dict(row)
            # Use session broadcast_title if set, otherwise fall back to schedule
            if not result.get('broadcast_title'):
                result['broadcast_title'] = result.get('schedule_broadcast_title')
            return result
        return None


def update_stream_session(session_id: int, **kwargs) -> bool:
    """Update stream session."""
    allowed_fields = ['broadcast_title', 'youtube_broadcast_id', 'youtube_stream_id', 'youtube_stream_key',
                      'youtube_watch_url', 'youtube_rtmp_url',
                      'status', 'actual_start', 'actual_end', 'peak_viewers', 
                      'estimated_attendance', 'email_sent', 'deleted_from_youtube']
    updates = {k: v for k, v in kwargs.items() if k in allowed_fields}
    
    if not updates:
        return False
    
    set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
    values = list(updates.values()) + [session_id]
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(f"UPDATE stream_sessions SET {set_clause} WHERE id = ?", values)
        return cursor.rowcount > 0


def get_sessions_pending_deletion() -> List[Dict]:
    """Get sessions that need YouTube recording deletion."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT ss.*, w.name as ward_name 
            FROM stream_sessions ss 
            JOIN wards w ON ss.ward_id = w.id 
            WHERE ss.status = 'ended' 
            AND ss.deleted_from_youtube = FALSE
            AND ss.youtube_broadcast_id IS NOT NULL
            AND ss.actual_end < datetime('now', '-1 hour')
        """)
        return [dict(row) for row in cursor.fetchall()]


def get_pending_session_for_schedule(schedule_id: int) -> Optional[Dict]:
    """Get a pending (pre-created) session for a schedule."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT ss.*, w.name as ward_name 
            FROM stream_sessions ss 
            JOIN wards w ON ss.ward_id = w.id 
            WHERE ss.schedule_id = ? 
            AND ss.status = 'scheduled'
            AND ss.youtube_broadcast_id IS NOT NULL
            ORDER BY ss.scheduled_start DESC
            LIMIT 1
        """, (schedule_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_sessions_pending_email() -> List[Dict]:
    """Get sessions that need attendance email sent (deleted from YouTube but not yet emailed)."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT ss.*, w.name as ward_name, w.email_addresses
            FROM stream_sessions ss 
            JOIN wards w ON ss.ward_id = w.id 
            WHERE ss.status = 'ended' 
            AND ss.email_sent = FALSE
            AND ss.deleted_from_youtube = TRUE
        """)
        sessions = []
        for row in cursor.fetchall():
            session = dict(row)
            session['email_addresses'] = json.loads(session['email_addresses'])
            sessions.append(session)
        return sessions


# =============================================================================
# Settings functions
# =============================================================================

def get_setting(key: str, default: str = None) -> Optional[str]:
    """Get a setting value."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = cursor.fetchone()
        return row['value'] if row else default


def set_setting(key: str, value: str):
    """Set a setting value."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO settings (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET value = ?, updated_at = CURRENT_TIMESTAMP
        """, (key, value, value))


def delete_setting(key: str) -> bool:
    """Delete a setting."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM settings WHERE key = ?", (key,))
        return cursor.rowcount > 0


# =============================================================================
# Seed initial data
# =============================================================================

def seed_initial_data():
    """Seed the database with default admin user only. Wards and presets managed via UI."""
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Check if users already exist
        cursor.execute("SELECT COUNT(*) FROM users")
        if cursor.fetchone()[0] > 0:
            return  # Already seeded
        
        # Create default admin user (password: admin - CHANGE THIS!)
        cursor.execute(
            "INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, ?)",
            ("admin", hash_password("admin"), True)
        )
        
        # Create default viewer user (password: alma3738)
        cursor.execute(
            "INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, ?)",
            ("webcast", hash_password("alma3738"), False)
        )


if __name__ == "__main__":
    print("Initializing database...")
    init_db()
    print("Seeding initial data...")
    seed_initial_data()
    print("Done!")
    
    # Print summary
    print("\nDefault users created:")
    print("  - admin / admin (CHANGE THIS PASSWORD!)")
    print("  - webcast / alma3738")
    print("\nWards and presets can be added via the web UI.")
