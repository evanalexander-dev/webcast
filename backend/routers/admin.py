"""
Webcast - Admin Router
Handles ward management, schedule management, and system settings.
"""
from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks, UploadFile, File
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timedelta, date

import logging

from routers.auth import require_admin, require_specialist_or_admin
from database import (
    get_all_wards, get_ward, create_ward, update_ward, delete_ward,
    get_all_schedules, get_schedule, create_schedule, update_schedule, delete_schedule,
    get_schedule_exceptions, add_schedule_exception, delete_schedule_exception,
    get_setting, set_setting, get_all_ptz_presets,
    create_stream_session, update_stream_session, get_pending_session_for_schedule,
    get_stream_session
)
from services.email import send_test_email
from services.youtube_api import setup_youtube_stream, delete_youtube_broadcast, set_broadcast_thumbnail
from services.scheduler import scheduler_service
from config import STREAM_PRE_ROLL_MINUTES, STREAM_POST_ROLL_MINUTES

router = APIRouter(prefix="/admin", tags=["Admin"])
logger = logging.getLogger(__name__)


# =============================================================================
# Request Models
# =============================================================================

class WardCreate(BaseModel):
    name: str
    email_addresses: List[str] = []


class WardUpdate(BaseModel):
    name: Optional[str] = None
    email_addresses: Optional[List[str]] = None


class ScheduleCreate(BaseModel):
    ward_id: int
    day_of_week: int = 6  # Sunday (0=Monday, 6=Sunday)
    start_time: str  # HH:MM format (user-entered meeting time, e.g., "09:00")
    meeting_duration_minutes: int = 60  # Just the meeting length
    ptz_preset_id: Optional[int] = None
    is_recurring: bool = True
    one_off_date: Optional[str] = None  # YYYY-MM-DD for one-off events
    custom_title: Optional[str] = None  # Display title in UI for one-off events
    broadcast_title: Optional[str] = None  # Exact YouTube broadcast title
    active: bool = True


class ScheduleUpdate(BaseModel):
    day_of_week: Optional[int] = None
    start_time: Optional[str] = None
    meeting_duration_minutes: Optional[int] = None
    ptz_preset_id: Optional[int] = None
    active: Optional[bool] = None
    is_recurring: Optional[bool] = None
    one_off_date: Optional[str] = None
    custom_title: Optional[str] = None
    broadcast_title: Optional[str] = None


class ExceptionCreate(BaseModel):
    exception_date: str  # YYYY-MM-DD format
    reason: Optional[str] = None


class TestEmailRequest(BaseModel):
    to_address: str


# =============================================================================
# Ward Management
# =============================================================================

@router.get("/wards")
async def list_wards(user: dict = Depends(require_specialist_or_admin)):
    """Get all wards with their settings."""
    wards = get_all_wards()
    return {"wards": wards}


@router.get("/wards/{ward_id}")
async def get_ward_details(ward_id: int, user: dict = Depends(require_specialist_or_admin)):
    """Get detailed ward information."""
    ward = get_ward(ward_id)
    if not ward:
        raise HTTPException(status_code=404, detail="Ward not found")
    return {"ward": ward}


@router.post("/wards")
async def create_new_ward(request: WardCreate, admin: dict = Depends(require_admin)):
    """Create a new ward."""
    ward_id = create_ward(request.name, request.email_addresses)
    return {"success": True, "ward_id": ward_id}


@router.put("/wards/{ward_id}")
async def update_existing_ward(
    ward_id: int, request: WardUpdate, user: dict = Depends(require_specialist_or_admin)
):
    """Update ward settings. Specialists can only update email addresses for their ward."""
    ward = get_ward(ward_id)
    if not ward:
        raise HTTPException(status_code=404, detail="Ward not found")

    is_admin = user.get("is_admin")
    is_specialist = user.get("is_specialist")

    # Specialists can only update their assigned ward's email addresses
    if is_specialist and not is_admin:
        if user.get("specialist_ward_id") != ward_id:
            raise HTTPException(status_code=403, detail="You can only update your assigned ward")
        if request.name is not None:
            raise HTTPException(status_code=403, detail="Specialists cannot change ward names")

    updates = {}
    if request.name is not None and is_admin:
        updates['name'] = request.name
    if request.email_addresses is not None:
        updates['email_addresses'] = request.email_addresses

    if updates:
        update_ward(ward_id, **updates)

    return {"success": True}

@router.delete("/wards/{ward_id}")
async def delete_existing_ward(ward_id: int, admin: dict = Depends(require_admin)):
    """Delete a ward and all its schedules."""
    ward = get_ward(ward_id)
    if not ward:
        raise HTTPException(status_code=404, detail="Ward not found")
    
    delete_ward(ward_id)
    return {"success": True}


# =============================================================================
# Schedule Management
# =============================================================================

@router.get("/schedules")
async def list_schedules(user: dict = Depends(require_specialist_or_admin)):
    """Get all schedules with computed stream times and YouTube URLs."""
    schedules = get_all_schedules()
    
    # Add computed stream start/end times
    for s in schedules:
        s['pre_roll_minutes'] = STREAM_PRE_ROLL_MINUTES
        s['post_roll_minutes'] = STREAM_POST_ROLL_MINUTES
        # Compute actual stream time from meeting time
        if s.get('start_time'):
            hour, minute = map(int, s['start_time'].split(':'))
            # Stream starts PRE_ROLL minutes before
            stream_start_mins = hour * 60 + minute - STREAM_PRE_ROLL_MINUTES
            s['stream_start_time'] = f"{stream_start_mins // 60:02d}:{stream_start_mins % 60:02d}"
            # Stream ends POST_ROLL after meeting ends
            meeting_end_mins = hour * 60 + minute + s.get('meeting_duration_minutes', 60)
            stream_end_mins = meeting_end_mins + STREAM_POST_ROLL_MINUTES
            s['stream_end_time'] = f"{stream_end_mins // 60:02d}:{stream_end_mins % 60:02d}"
            # Total stream duration
            s['total_stream_minutes'] = STREAM_PRE_ROLL_MINUTES + s.get('meeting_duration_minutes', 60) + STREAM_POST_ROLL_MINUTES
        
        # Check for pre-created YouTube session
        pending_session = get_pending_session_for_schedule(s['id'])
        if pending_session:
            s['youtube_url'] = pending_session.get('youtube_watch_url')
    
    return {"schedules": schedules}


@router.get("/schedules/{schedule_id}")
async def get_schedule_details(schedule_id: int, user: dict = Depends(require_specialist_or_admin)):
    """Get detailed schedule information including YouTube URL if available."""
    schedule = get_schedule(schedule_id)
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")
    
    # Check for pre-created YouTube session
    pending_session = get_pending_session_for_schedule(schedule_id)
    if pending_session:
        schedule['youtube_url'] = pending_session.get('youtube_watch_url')
        schedule['youtube_session_id'] = pending_session.get('id')
    
    return {"schedule": schedule}


@router.post("/schedules")
async def create_new_schedule(
    request: ScheduleCreate,
    background_tasks: BackgroundTasks,
    user: dict = Depends(require_specialist_or_admin)
):
    """
    Create a new schedule. Specialists can only create schedules for their assigned ward.
    
    For recurring schedules, set is_recurring=True and day_of_week.
    For one-off events (like funerals), set is_recurring=False and one_off_date.
    
    The start_time is the meeting start time (e.g., "09:00" for 9 AM).
    The system automatically adds pre-roll (10 min) and post-roll (15 min).
    
    If the ward has YouTube authorization, the broadcast is created immediately
    so the link can be shared before the event starts.
    """
    ward = get_ward(request.ward_id)
    if not ward:
        raise HTTPException(status_code=400, detail="Ward not found")

    if user.get("is_specialist") and not user.get("is_admin"):
        if user.get("specialist_ward_id") != request.ward_id:
            raise HTTPException(status_code=403, detail="You can only create schedules for your assigned ward")
    
    # Validate time format
    try:
        datetime.strptime(request.start_time, "%H:%M")
    except ValueError:
        raise HTTPException(status_code=400, detail="Start time must be HH:MM format")
    
    # Validate one-off date if provided
    if request.one_off_date:
        try:
            datetime.strptime(request.one_off_date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="Date must be YYYY-MM-DD format")
    
    schedule_id = create_schedule(
        ward_id=request.ward_id,
        start_time=request.start_time,
        day_of_week=request.day_of_week,
        meeting_duration_minutes=request.meeting_duration_minutes,
        ptz_preset_id=request.ptz_preset_id,
        is_recurring=request.is_recurring,
        one_off_date=request.one_off_date,
        custom_title=request.custom_title,
        broadcast_title=request.broadcast_title,
        active=request.active
    )
    
    youtube_url = None
    
    # Create YouTube broadcast immediately if ward has auth and broadcast title is set
    logger.info(f"Schedule created: id={schedule_id}, ward_id={request.ward_id}, broadcast_title={request.broadcast_title}")
    logger.info(f"Ward has refresh_token: {bool(ward.get('youtube_refresh_token'))}")
    
    if ward.get('youtube_refresh_token') and request.broadcast_title:
        hour, minute = map(int, request.start_time.split(':'))
        
        if request.one_off_date:
            # One-off event - use the specified date
            event_date = datetime.strptime(request.one_off_date, "%Y-%m-%d").date()
            logger.info(f"One-off event on {event_date}")
        else:
            # Recurring event - calculate next occurrence
            today = date.today()
            days_ahead = request.day_of_week - today.weekday()
            if days_ahead < 0:
                days_ahead += 7
            elif days_ahead == 0:
                # If it's today, check if time has passed
                now = datetime.now()
                if now.hour > hour or (now.hour == hour and now.minute >= minute):
                    days_ahead = 7  # Next week
            event_date = today + timedelta(days=days_ahead)
            logger.info(f"Recurring event, next occurrence: {event_date}")
        
        # Meeting time (what YouTube will display)
        meeting_start = datetime.combine(event_date, datetime.min.time().replace(hour=hour, minute=minute))
        # Stream start time (when FFmpeg actually starts, PRE_ROLL minutes before meeting)
        stream_start = meeting_start - timedelta(minutes=STREAM_PRE_ROLL_MINUTES)
        
        logger.info(f"Meeting start: {meeting_start}, Stream start: {stream_start}, Now: {datetime.now()}")
        
        # Only create if stream start is in the future
        if stream_start > datetime.now():
            logger.info(f"Adding background task to create broadcast")
            background_tasks.add_task(
                create_scheduled_broadcast,
                schedule_id,
                request.ward_id,
                request.broadcast_title,
                meeting_start,  # YouTube shows meeting time
                stream_start,   # Session stores stream start time
                bool(request.one_off_date)  # is_one_off
            )
        else:
            logger.warning(f"Stream start {stream_start} is in the past, not creating broadcast")
            youtube_url = "skipped:past"  # Signal to frontend
    else:
        logger.warning(f"Not creating broadcast: refresh_token={bool(ward.get('youtube_refresh_token'))}, broadcast_title={request.broadcast_title}")
    
    # Add to today's scheduler if applicable
    schedule = get_schedule(schedule_id)
    if schedule:
        scheduler_service.add_schedule_job(schedule)
    
    return {"success": True, "schedule_id": schedule_id, "youtube_url": youtube_url, "broadcast_skipped": youtube_url == "skipped:past"}


async def _apply_pending_thumbnail(schedule_id: int, ward_id: int, broadcast_id: str, keep_file: bool = False):
    """Apply a saved thumbnail file to a broadcast if one exists for this schedule.
    
    Set keep_file=True for recurring schedules so the thumbnail is reused each week.
    """
    from config import THUMBNAILS_DIR
    THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)
    for ext, content_type in [("jpg", "image/jpeg"), ("png", "image/png")]:
        thumb_path = THUMBNAILS_DIR / f"schedule_{schedule_id}.{ext}"
        if thumb_path.exists():
            image_data = thumb_path.read_bytes()
            success, message = await set_broadcast_thumbnail(ward_id, broadcast_id, image_data, content_type)
            if success:
                if not keep_file:
                    thumb_path.unlink()
                logger.info(f"Applied saved thumbnail to broadcast {broadcast_id} for schedule {schedule_id}")
            else:
                logger.warning(f"Failed to apply saved thumbnail for schedule {schedule_id}: {message}")
            return


async def create_scheduled_broadcast(schedule_id: int, ward_id: int, title: str, meeting_start: datetime, stream_start: datetime, is_one_off: bool = False):
    """Background task to create YouTube broadcast for a scheduled event.
    
    Args:
        schedule_id: The schedule this broadcast is for
        ward_id: The ward ID
        title: Broadcast title
        meeting_start: Meeting time (shown on YouTube)
        stream_start: When FFmpeg should actually start (PRE_ROLL before meeting)
    """
    try:
        logger.info(f"Creating broadcast for schedule {schedule_id}, ward {ward_id}, title: {title}")
        
        # Pass meeting_start to YouTube so it displays the correct time
        testing_mode = get_setting("testing_mode", "false") == "true"
        privacy = "unlisted" if testing_mode else "public"
        stream_info = await setup_youtube_stream(
            ward_id, title,
            privacy=privacy,
            scheduled_start=meeting_start,
            dedicated_stream=is_one_off
        )
        
        if stream_info:
            # Create a pre-scheduled session with stream_start (when FFmpeg starts)
            session_id = create_stream_session(
                ward_id=ward_id,
                schedule_id=schedule_id,
                scheduled_start=stream_start  # This is when FFmpeg will start
            )
            
            update_stream_session(
                session_id,
                broadcast_title=title,
                youtube_broadcast_id=stream_info['broadcast_id'],
                youtube_stream_id=stream_info['stream_id'],
                youtube_stream_key=stream_info['stream_key'],
                youtube_watch_url=stream_info['watch_url'],
                youtube_rtmp_url=stream_info['rtmp_url'],
                status='scheduled'
            )
            
            logger.info(f"Created scheduled broadcast for schedule {schedule_id}: {stream_info['watch_url']}")
            logger.info(f"  YouTube shows: {meeting_start}, FFmpeg starts: {stream_start}")

            # Apply any saved thumbnail — keep file for recurring so it reapplies each week
            await _apply_pending_thumbnail(schedule_id, ward_id, stream_info['broadcast_id'], keep_file=not is_one_off)
        else:
            logger.error(f"setup_youtube_stream returned None for schedule {schedule_id}")
    except Exception as e:
        import traceback
        logger.error(f"Failed to create scheduled broadcast: {e}")
        logger.error(traceback.format_exc())


@router.put("/schedules/{schedule_id}")
async def update_existing_schedule(
    schedule_id: int,
    request: ScheduleUpdate,
    background_tasks: BackgroundTasks,
    user: dict = Depends(require_specialist_or_admin)
):
    """Update schedule settings."""
    schedule = get_schedule(schedule_id)
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")

    if user.get("is_specialist") and not user.get("is_admin"):
        if user.get("specialist_ward_id") != schedule['ward_id']:
            raise HTTPException(status_code=403, detail="You can only edit schedules for your assigned ward")
    
    was_active = schedule.get('active', True)
    
    updates = {}
    if request.day_of_week is not None:
        updates['day_of_week'] = request.day_of_week
    if request.start_time is not None:
        try:
            datetime.strptime(request.start_time, "%H:%M")
        except ValueError:
            raise HTTPException(status_code=400, detail="Start time must be HH:MM format")
        updates['start_time'] = request.start_time
    if request.meeting_duration_minutes is not None:
        updates['meeting_duration_minutes'] = request.meeting_duration_minutes
    if request.ptz_preset_id is not None:
        updates['ptz_preset_id'] = request.ptz_preset_id
    if request.active is not None:
        updates['active'] = request.active
    if request.is_recurring is not None:
        updates['is_recurring'] = request.is_recurring
    if request.one_off_date is not None:
        updates['one_off_date'] = request.one_off_date
    
    if updates:
        update_schedule(schedule_id, **updates)
    
    # Handle enable/disable
    is_now_active = updates.get('active', was_active)
    
    if was_active and not is_now_active:
        # Disabling - remove from scheduler and delete YouTube broadcast
        logger.info(f"Schedule {schedule_id} disabled - removing from scheduler")
        scheduler_service.remove_schedule_job(schedule_id)
        
        # Delete pending YouTube broadcast
        pending_session = get_pending_session_for_schedule(schedule_id)
        if pending_session and pending_session.get('youtube_broadcast_id'):
            ward_id = schedule['ward_id']
            broadcast_id = pending_session['youtube_broadcast_id']
            logger.info(f"Deleting YouTube broadcast {broadcast_id}")
            background_tasks.add_task(delete_youtube_broadcast, ward_id, broadcast_id)
            update_stream_session(pending_session['id'], status='cancelled')
    
    elif not was_active and is_now_active:
        # Enabling - add to scheduler and create YouTube broadcast
        logger.info(f"Schedule {schedule_id} enabled - adding to scheduler")
        updated_schedule = get_schedule(schedule_id)
        scheduler_service.add_schedule_job(updated_schedule)
        
        # Create YouTube broadcast if needed
        ward = get_ward(schedule['ward_id'])
        if ward and ward.get('youtube_refresh_token') and schedule.get('broadcast_title'):
            hour, minute = map(int, updated_schedule['start_time'].split(':'))
            today = datetime.now()
            today_str = today.strftime('%Y-%m-%d')
            
            # Check if this schedule is for today
            is_today = False
            if updated_schedule.get('is_recurring'):
                if updated_schedule['day_of_week'] == today.weekday():
                    is_today = True
            else:
                if updated_schedule.get('one_off_date') == today_str:
                    is_today = True
            
            if is_today:
                event_date = today.date()
            elif updated_schedule.get('is_recurring'):
                days_ahead = updated_schedule['day_of_week'] - today.weekday()
                if days_ahead <= 0:
                    days_ahead += 7
                event_date = (today + timedelta(days=days_ahead)).date()
            else:
                event_date = datetime.strptime(updated_schedule['one_off_date'], "%Y-%m-%d").date()
            
            meeting_start = datetime.combine(event_date, datetime.min.time().replace(hour=hour, minute=minute))
            stream_start = meeting_start - timedelta(minutes=STREAM_PRE_ROLL_MINUTES)
            
            if stream_start > datetime.now():
                background_tasks.add_task(
                    create_scheduled_broadcast,
                    schedule_id,
                    schedule['ward_id'],
                    schedule['broadcast_title'],
                    meeting_start,
                    stream_start,
                    not updated_schedule.get('is_recurring', True)  # is_one_off
                )
    
    return {"success": True}


@router.delete("/schedules/{schedule_id}")
async def delete_existing_schedule(
    schedule_id: int,
    background_tasks: BackgroundTasks,
    user: dict = Depends(require_specialist_or_admin)
):
    """Delete a schedule."""
    schedule = get_schedule(schedule_id)
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")

    if user.get("is_specialist") and not user.get("is_admin"):
        if user.get("specialist_ward_id") != schedule['ward_id']:
            raise HTTPException(status_code=403, detail="You can only delete schedules for your assigned ward")
    
    # Remove from scheduler
    scheduler_service.remove_schedule_job(schedule_id)
    
    # Delete pending YouTube broadcast
    pending_session = get_pending_session_for_schedule(schedule_id)
    if pending_session and pending_session.get('youtube_broadcast_id'):
        ward_id = schedule['ward_id']
        broadcast_id = pending_session['youtube_broadcast_id']
        logger.info(f"Deleting YouTube broadcast {broadcast_id} for deleted schedule")
        background_tasks.add_task(delete_youtube_broadcast, ward_id, broadcast_id)
        update_stream_session(pending_session['id'], status='cancelled')

    # Clean up any saved thumbnail file
    from config import THUMBNAILS_DIR
    for ext in ("jpg", "png"):
        thumb_path = THUMBNAILS_DIR / f"schedule_{schedule_id}.{ext}"
        if thumb_path.exists():
            thumb_path.unlink()
            logger.info(f"Removed thumbnail file for deleted schedule {schedule_id}")

    delete_schedule(schedule_id)
    return {"success": True}


@router.post("/schedules/{schedule_id}/create-broadcast")
async def create_schedule_broadcast(
    schedule_id: int, 
    background_tasks: BackgroundTasks,
    user: dict = Depends(require_specialist_or_admin)
):
    """
    Manually create a YouTube broadcast for a schedule.
    Useful when you've just created a recurring schedule and want the link immediately.
    """
    schedule = get_schedule(schedule_id)
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")
    
    # Check if there's already a pending session
    existing = get_pending_session_for_schedule(schedule_id)
    if existing:
        return {
            "success": True, 
            "message": "Broadcast already exists",
            "youtube_url": existing.get('youtube_watch_url')
        }
    
    ward = get_ward(schedule['ward_id'])
    if not ward or not ward.get('youtube_refresh_token'):
        raise HTTPException(status_code=400, detail="Ward has no YouTube authorization")
    
    # Calculate next occurrence for recurring, or use one_off_date
    if schedule['is_recurring']:
        today = date.today()
        # Convert day_of_week (0=Mon, 6=Sun) to match today.weekday() (0=Mon, 6=Sun)
        days_ahead = schedule['day_of_week'] - today.weekday()
        if days_ahead < 0:
            days_ahead += 7
        elif days_ahead == 0:
            # If it's today, check if time has passed
            hour, minute = map(int, schedule['start_time'].split(':'))
            now = datetime.now()
            if now.hour > hour or (now.hour == hour and now.minute >= minute):
                days_ahead = 7  # Next week
        
        next_date = today + timedelta(days=days_ahead)
    else:
        next_date = datetime.strptime(schedule['one_off_date'], "%Y-%m-%d").date()
    
    hour, minute = map(int, schedule['start_time'].split(':'))
    scheduled_start = datetime.combine(next_date, datetime.min.time().replace(hour=hour, minute=minute))
    stream_start = scheduled_start - timedelta(minutes=STREAM_PRE_ROLL_MINUTES)
    
    # Determine title
    title = schedule.get('broadcast_title') or schedule.get('custom_title') or f"{ward['name']} Sacrament Meeting"
    
    # Create in background
    background_tasks.add_task(
        create_scheduled_broadcast,
        schedule_id,
        schedule['ward_id'],
        title,
        scheduled_start,        # meeting_start (YouTube display time)
        stream_start,           # stream_start (FFmpeg start time)
        not schedule['is_recurring']  # is_one_off
    )
    
    return {"success": True, "message": "Creating broadcast in background..."}


@router.post("/schedules/{schedule_id}/thumbnail")
async def upload_schedule_thumbnail(
    schedule_id: int,
    file: UploadFile = File(...),
    user: dict = Depends(require_specialist_or_admin)
):
    """Upload a thumbnail image for a scheduled broadcast.

    The image is saved to disk immediately so it can be retried if the
    broadcast hasn't been created yet or if the YouTube upload fails.
    Accepts JPEG or PNG images. YouTube recommends 1280x720 (16:9).
    """
    from config import THUMBNAILS_DIR

    # Determine content type — browser may send application/octet-stream for some file types
    raw_ct = file.content_type or ""
    if raw_ct in ("image/jpeg", "image/jpg"):
        content_type = "image/jpeg"
    elif raw_ct == "image/png":
        content_type = "image/png"
    else:
        # Fall back to filename extension
        fname = (file.filename or "").lower()
        if fname.endswith(".jpg") or fname.endswith(".jpeg"):
            content_type = "image/jpeg"
        elif fname.endswith(".png"):
            content_type = "image/png"
        else:
            raise HTTPException(status_code=400, detail=f"Image must be JPEG or PNG (got content-type '{raw_ct}', filename '{file.filename}')")

    schedule = get_schedule(schedule_id)
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")

    image_data = await file.read()
    if len(image_data) > 2 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Image must be under 2 MB")

    # Ensure directory exists (defensive — may not exist on older installs)
    THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)

    # Persist image to disk so it survives page navigation and can be retried
    ext = "jpg" if content_type == "image/jpeg" else "png"
    thumb_path = THUMBNAILS_DIR / f"schedule_{schedule_id}.{ext}"
    thumb_path.write_bytes(image_data)
    logger.info(f"Saved thumbnail for schedule {schedule_id} to {thumb_path}")

    # Try to upload to YouTube immediately if the broadcast already exists
    pending_session = get_pending_session_for_schedule(schedule_id)
    if not pending_session or not pending_session.get("youtube_broadcast_id"):
        return {
            "success": True,
            "message": "Thumbnail saved. It will be uploaded automatically when the YouTube broadcast is created.",
            "pending": True
        }

    success, message = await set_broadcast_thumbnail(
        pending_session["ward_id"],
        pending_session["youtube_broadcast_id"],
        image_data,
        content_type
    )

    if success:
        # Keep the file for recurring schedules so it reapplies each week
        # Only delete for one-off events where it won't be needed again
        if not schedule.get('is_recurring'):
            try:
                thumb_path.unlink()
            except Exception:
                pass
        return {"success": True, "message": "Thumbnail uploaded", "pending": False}
    else:
        # Leave the file on disk for retry by create_scheduled_broadcast
        logger.warning(f"Thumbnail YouTube upload failed for schedule {schedule_id}: {message} — will retry")
        return {"success": True, "message": f"Thumbnail saved but YouTube upload failed ({message}). Will retry automatically.", "pending": True}


@router.delete("/schedules/{schedule_id}/thumbnail")
async def delete_schedule_thumbnail(
    schedule_id: int,
    admin: dict = Depends(require_admin)
):
    """Remove the saved thumbnail file for a recurring schedule."""
    from config import THUMBNAILS_DIR
    deleted = False
    for ext in ("jpg", "png"):
        thumb_path = THUMBNAILS_DIR / f"schedule_{schedule_id}.{ext}"
        if thumb_path.exists():
            thumb_path.unlink()
            deleted = True
    if deleted:
        logger.info(f"Removed saved thumbnail for schedule {schedule_id}")
        return {"success": True, "message": "Thumbnail removed — YouTube will use an auto-generated image"}
    else:
        return {"success": True, "message": "No saved thumbnail found for this schedule"}


# =============================================================================
# Schedule Exceptions (Skip Dates)
# =============================================================================

@router.get("/schedules/{schedule_id}/exceptions")
async def list_schedule_exceptions(schedule_id: int, admin: dict = Depends(require_admin)):
    """Get all exception dates for a schedule."""
    schedule = get_schedule(schedule_id)
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")
    
    exceptions = get_schedule_exceptions(schedule_id)
    return {"exceptions": exceptions}


@router.post("/schedules/{schedule_id}/exceptions")
async def create_schedule_exception(
    schedule_id: int, request: ExceptionCreate, admin: dict = Depends(require_admin)
):
    """Add a skip date for a schedule."""
    schedule = get_schedule(schedule_id)
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")
    
    # Validate date
    try:
        datetime.strptime(request.exception_date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="Date must be YYYY-MM-DD format")
    
    exception_id = add_schedule_exception(
        schedule_id, request.exception_date, request.reason
    )
    
    if not exception_id:
        raise HTTPException(status_code=400, detail="Exception already exists for this date")
    
    return {"success": True, "exception_id": exception_id}


@router.delete("/exceptions/{exception_id}")
async def delete_existing_exception(exception_id: int, admin: dict = Depends(require_admin)):
    """Remove a schedule exception."""
    success = delete_schedule_exception(exception_id)
    if not success:
        raise HTTPException(status_code=404, detail="Exception not found")
    return {"success": True}


# =============================================================================
# System Info & Testing
# =============================================================================

@router.get("/config")
async def get_system_config(admin: dict = Depends(require_admin)):
    """Get current system configuration."""
    return {
        "stream_pre_roll_minutes": STREAM_PRE_ROLL_MINUTES,
        "stream_post_roll_minutes": STREAM_POST_ROLL_MINUTES,
        "presets": get_all_ptz_presets()
    }


@router.get("/settings")
async def get_system_settings(admin: dict = Depends(require_admin)):
    """Get system settings."""
    return {
        "testing_mode": get_setting("testing_mode", "false") == "true"
    }


@router.post("/settings")
async def update_system_settings(request: dict, admin: dict = Depends(require_admin)):
    """Update system settings."""
    if "testing_mode" in request:
        set_setting("testing_mode", "true" if request["testing_mode"] else "false")
        logger.info(f"Testing mode {'enabled' if request['testing_mode'] else 'disabled'} by {admin['username']}")
    return {"success": True}


@router.post("/test-email")
async def test_email_delivery(request: TestEmailRequest, admin: dict = Depends(require_admin)):
    """Send a test email to verify SMTP configuration."""
    success, message = send_test_email(request.to_address)
    
    if not success:
        raise HTTPException(status_code=500, detail=message)
    
    return {"success": True, "message": message}


@router.get("/dashboard")
async def get_dashboard_data(admin: dict = Depends(require_admin)):
    """Get dashboard summary data."""
    from database import get_db
    
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Ward count
        cursor.execute("SELECT COUNT(*) FROM wards")
        ward_count = cursor.fetchone()[0]
        
        # Authorized wards
        cursor.execute("SELECT COUNT(*) FROM wards WHERE youtube_refresh_token IS NOT NULL")
        authorized_count = cursor.fetchone()[0]
        
        # Active schedules
        cursor.execute("SELECT COUNT(*) FROM schedules WHERE active = TRUE")
        schedule_count = cursor.fetchone()[0]
        
        # Recent sessions
        cursor.execute("""
            SELECT ss.*, w.name as ward_name
            FROM stream_sessions ss
            JOIN wards w ON ss.ward_id = w.id
            ORDER BY ss.created_at DESC
            LIMIT 10
        """)
        recent_sessions = [dict(row) for row in cursor.fetchall()]
    
    return {
        "ward_count": ward_count,
        "authorized_count": authorized_count,
        "schedule_count": schedule_count,
        "recent_sessions": recent_sessions
    }


@router.post("/run-cleanup")
async def run_cleanup_now(admin: dict = Depends(require_admin)):
    """Manually trigger the 1 AM cleanup routine (fetch view counts, delete recordings, send emails, create next broadcasts)."""
    import asyncio
    asyncio.create_task(scheduler_service._cleanup_old_streams())
    return {"success": True, "message": "Cleanup started in background — check logs for progress"}
