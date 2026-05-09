"""
Webcast - Scheduler Service
Handles scheduled stream starts, stops, and cleanup tasks.
"""
import asyncio
from datetime import datetime, timedelta, date
from typing import Optional, Callable
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
import logging

from config import (
    RECORDING_DELETE_HOUR, RECORDING_DELETE_MINUTE,
    STREAM_PRE_ROLL_MINUTES, STREAM_POST_ROLL_MINUTES, ATTENDANCE_MULTIPLIER
)
from database import (
    get_schedules_for_day, is_date_excepted, get_ward, get_ptz_preset,
    create_stream_session, update_stream_session, get_stream_session,
    get_sessions_pending_deletion, get_sessions_pending_email,
    get_pending_session_for_schedule, get_schedule, get_all_schedules,
    delete_expired_oneoff_schedules, get_setting, get_default_ptz_preset,
    get_active_stream_session, get_all_wards, delete_expired_schedule_exceptions
)
from services.youtube_api import (
    youtube_service, setup_youtube_stream, start_youtube_broadcast, 
    end_youtube_broadcast, get_peak_viewers, delete_youtube_broadcast,
    set_broadcast_thumbnail, delete_youtube_stream
)
from services.stream_manager import stream_manager
from services.camera import camera_service, absolute_move
from services.email import send_attendance_report, calculate_attendance

logger = logging.getLogger(__name__)


class SchedulerService:
    """Service for managing scheduled tasks."""
    
    def __init__(self):
        self.scheduler = AsyncIOScheduler()
        self._stream_end_job_id: Optional[str] = None
    
    def start(self):
        """Start the scheduler."""
        if not self.scheduler.running:
            # Daily job to schedule streams
            self.scheduler.add_job(
                self._schedule_todays_streams,
                CronTrigger(hour=0, minute=1),
                id="daily_schedule",
                replace_existing=True
            )
            
            # Cleanup job (1 AM daily) — also sends attendance emails after deletion
            self.scheduler.add_job(
                self._cleanup_old_streams,
                CronTrigger(hour=RECORDING_DELETE_HOUR, minute=RECORDING_DELETE_MINUTE),
                id="daily_cleanup",
                replace_existing=True
            )

            # Token health check (6 AM daily) — emails admin if any ward needs re-auth
            self.scheduler.add_job(
                self._check_youtube_token_health,
                CronTrigger(hour=6, minute=0),
                id="token_health_check",
                replace_existing=True
            )
            
            self.scheduler.start()
            logger.info("Scheduler started")
            
            # Schedule today's streams on startup
            asyncio.create_task(self._schedule_todays_streams())
            
            # Check for streams that should be running now (reboot recovery)
            asyncio.create_task(self._recover_missed_streams())
    
    def stop(self):
        """Stop the scheduler."""
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("Scheduler stopped")
    
    async def _recover_missed_streams(self):
        """Check for streams that should be running now but aren't (reboot recovery)."""
        await asyncio.sleep(5)  # Wait for system to stabilize

        logger.info("Checking for missed streams to recover...")

        # First check: is there a session the DB thinks is live/paused but FFmpeg isn't running?
        active_session = get_active_stream_session()
        if active_session and not stream_manager.is_streaming:
            # Check whether the session is still within its expected window before recovering
            now = datetime.now()
            schedule_id = active_session.get('schedule_id')
            stream_end = None
            if schedule_id:
                schedule = get_schedule(schedule_id)
                if schedule:
                    hour, minute = map(int, schedule['start_time'].split(':'))
                    meeting_start = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                    duration = schedule.get('meeting_duration_minutes', 60)
                    stream_end = meeting_start + timedelta(minutes=duration + STREAM_POST_ROLL_MINUTES)

            if stream_end is None:
                # No schedule — use session start time + 4 hours as a generous upper bound
                session_start = active_session.get('actual_start') or active_session.get('scheduled_start')
                if isinstance(session_start, str):
                    session_start = datetime.fromisoformat(session_start)
                if session_start:
                    stream_end = session_start + timedelta(hours=4)

            still_in_window = stream_end is None or now < stream_end

            if not still_in_window:
                # Stream window has passed — mark ended rather than restarting
                logger.warning(f"Session {active_session['id']} is marked live but its window has passed — marking ended")
                update_stream_session(active_session['id'], status='ended', actual_end=now)
            elif active_session.get('youtube_rtmp_url') and active_session.get('youtube_stream_key'):
                logger.warning(f"Session {active_session['id']} is marked live in DB but stream is not running — recovering")
                success, msg = await stream_manager.start_stream(
                    active_session['youtube_rtmp_url'],
                    active_session['youtube_stream_key'],
                    active_session['ward_id'],
                    active_session['id']
                )
                if success:
                    logger.info(f"Recovered live session {active_session['id']}")
                    update_stream_session(active_session['id'], status='live', actual_start=now)
                    if stream_end and stream_end > now:
                        end_job_id = f"end_{active_session['id']}"
                        self.scheduler.add_job(
                            self._end_scheduled_stream,
                            DateTrigger(run_date=stream_end),
                            args=[active_session['id'], active_session['ward_id'], active_session['youtube_broadcast_id']],
                            id=end_job_id,
                            replace_existing=True
                        )
                        self._stream_end_job_id = end_job_id
                        logger.info(f"Scheduled recovery stream end at {stream_end}")
                else:
                    logger.error(f"Failed to recover live session {active_session['id']}: {msg}")
            else:
                logger.warning(f"Live session {active_session['id']} has no RTMP credentials — marking ended")
                update_stream_session(active_session['id'], status='ended', actual_end=now)

        # Second check: is there a scheduled session whose window has started but FFmpeg never launched?
        today = datetime.now()
        day_of_week = today.weekday()
        today_str = today.strftime('%Y-%m-%d')
        schedules = get_schedules_for_day(day_of_week, today_str)

        for schedule in schedules:
            if not schedule.get('active', True):
                continue
            if schedule.get('is_recurring', True):
                if is_date_excepted(schedule['id'], today_str):
                    continue

            hour, minute = map(int, schedule['start_time'].split(':'))
            meeting_start = today.replace(hour=hour, minute=minute, second=0, microsecond=0)
            stream_start = meeting_start - timedelta(minutes=STREAM_PRE_ROLL_MINUTES)
            duration = schedule.get('meeting_duration_minutes', 60)
            stream_end = meeting_start + timedelta(minutes=duration + STREAM_POST_ROLL_MINUTES)
            now = datetime.now()

            if stream_start <= now <= stream_end:
                if not stream_manager.is_streaming:
                    logger.warning(f"Stream for {schedule['ward_name']} should be running — attempting recovery")
                    pending_session = get_pending_session_for_schedule(schedule['id'])
                    if pending_session and pending_session.get('youtube_rtmp_url') and pending_session.get('youtube_stream_key'):
                        success, msg = await stream_manager.start_stream(
                            pending_session['youtube_rtmp_url'],
                            pending_session['youtube_stream_key'],
                            schedule['ward_id'],
                            pending_session['id']
                        )
                        if success:
                            logger.info(f"Recovery stream started for {schedule['ward_name']}")
                            update_stream_session(pending_session['id'], status='live', actual_start=datetime.now())
                            remaining_minutes = (stream_end - now).total_seconds() / 60
                            if remaining_minutes > 1:
                                end_time = datetime.now() + timedelta(minutes=remaining_minutes)
                                end_job_id = f"end_{pending_session['id']}"
                                self.scheduler.add_job(
                                    self._end_scheduled_stream,
                                    DateTrigger(run_date=end_time),
                                    args=[pending_session['id'], schedule['ward_id'], pending_session['youtube_broadcast_id']],
                                    id=end_job_id,
                                    replace_existing=True
                                )
                                self._stream_end_job_id = end_job_id
                                logger.info(f"Scheduled recovery end at {end_time}")
                        else:
                            logger.error(f"Failed to start recovery stream: {msg}")
                    else:
                        logger.warning(f"No pending session with RTMP credentials for schedule {schedule['id']}")
                else:
                    logger.info(f"Stream for {schedule['ward_name']} is already running")

        logger.info("Missed stream recovery check complete")
    
    def add_schedule_job(self, schedule: dict):
        """Add a job for a schedule if it's for today and in the future."""
        today = datetime.now()
        today_str = today.strftime('%Y-%m-%d')
        
        # Check if this schedule is for today
        is_today = False
        if schedule.get('is_recurring'):
            if schedule['day_of_week'] == today.weekday():
                # Check for exceptions
                if not is_date_excepted(schedule['id'], today_str):
                    is_today = True
        else:
            # One-off event
            if schedule.get('one_off_date') == today_str:
                is_today = True
        
        if not is_today:
            logger.info(f"Schedule {schedule['id']} is not for today, not adding job")
            return False
        
        # Parse meeting start time
        hour, minute = map(int, schedule['start_time'].split(':'))
        meeting_start = today.replace(hour=hour, minute=minute, second=0, microsecond=0)
        
        # Stream starts PRE_ROLL minutes before meeting
        stream_start = meeting_start - timedelta(minutes=STREAM_PRE_ROLL_MINUTES)
        
        if stream_start < datetime.now():
            logger.info(f"Schedule {schedule['id']} start time already passed")
            return False
        
        job_id = f"stream_{schedule['id']}_{today_str.replace('-', '')}"
        
        try:
            self.scheduler.add_job(
                self._start_scheduled_stream,
                DateTrigger(run_date=stream_start),
                args=[schedule],
                id=job_id,
                replace_existing=True
            )
            logger.info(f"Added job {job_id} for {stream_start}")
            return True
        except Exception as e:
            logger.error(f"Failed to add job: {e}")
            return False
    
    def remove_schedule_job(self, schedule_id: int):
        """Remove a scheduled job for today."""
        today_str = datetime.now().strftime('%Y-%m-%d').replace('-', '')
        job_id = f"stream_{schedule_id}_{today_str}"
        
        try:
            self.scheduler.remove_job(job_id)
            logger.info(f"Removed job {job_id}")
            return True
        except Exception as e:
            logger.debug(f"Job {job_id} not found (may not exist): {e}")
            return False
    
    async def _schedule_todays_streams(self):
        """Schedule all streams for today."""
        today = datetime.now()
        day_of_week = today.weekday()
        today_str = today.strftime('%Y-%m-%d')
        
        logger.info(f"Scheduling streams for {today_str} (day {day_of_week})")
        
        schedules = get_schedules_for_day(day_of_week, today_str)
        logger.info(f"Found {len(schedules)} schedules for today")
        
        for schedule in schedules:
            logger.info(f"Processing schedule: {schedule['ward_name']} - {schedule.get('broadcast_title', 'No title')}")
            
            # Check for exceptions
            if schedule.get('is_recurring', True):
                if is_date_excepted(schedule['id'], today_str):
                    logger.info(f"Skipping {schedule['ward_name']} - exception date")
                    continue
            
            # Parse meeting start time
            hour, minute = map(int, schedule['start_time'].split(':'))
            meeting_start = today.replace(hour=hour, minute=minute, second=0, microsecond=0)
            
            # Stream starts PRE_ROLL minutes before meeting
            stream_start = meeting_start - timedelta(minutes=STREAM_PRE_ROLL_MINUTES)
            
            logger.info(f"  Meeting: {meeting_start}, Stream start: {stream_start}, Now: {datetime.now()}")
            
            if stream_start < datetime.now():
                logger.info(f"Skipping {schedule['ward_name']} - already past start time")
                continue
            
            job_id = f"stream_{schedule['id']}_{today_str.replace('-', '')}"
            
            self.scheduler.add_job(
                self._start_scheduled_stream,
                DateTrigger(run_date=stream_start),
                args=[schedule],
                id=job_id,
                replace_existing=True
            )
            
            logger.info(f"Scheduled {schedule['ward_name']} for {stream_start} (job_id: {job_id})")
    
    async def _start_scheduled_stream(self, schedule: dict, retry_count: int = 0):
        """Start a scheduled stream with retry logic."""
        RETRY_DELAY = 30  # seconds between retries
        
        # Calculate how long we should keep trying (entire broadcast window)
        meeting_duration = schedule.get('meeting_duration_minutes', 60)
        total_window = STREAM_PRE_ROLL_MINUTES + meeting_duration + STREAM_POST_ROLL_MINUTES
        max_retries = (total_window * 60) // RETRY_DELAY  # Retry for entire window
        
        logger.info(f"=== _start_scheduled_stream triggered for schedule {schedule['id']} (attempt {retry_count + 1}/{max_retries}) ===")
        
        ward_id = schedule['ward_id']
        ward = get_ward(ward_id)
        
        if not ward:
            logger.error(f"Ward {ward_id} not found")
            return
        
        if not ward.get('youtube_refresh_token'):
            logger.error(f"Ward {ward['name']} has no YouTube authorization")
            return
        
        logger.info(f"Starting scheduled stream for {ward['name']}")
        
        try:
            # Check if there's a pre-created session for this schedule
            pending_session = get_pending_session_for_schedule(schedule['id'])
            
            if pending_session and pending_session.get('youtube_rtmp_url') and pending_session.get('youtube_stream_key'):
                # Use the pre-created session
                session_id = pending_session['id']
                stream_info = {
                    'broadcast_id': pending_session['youtube_broadcast_id'],
                    'stream_id': pending_session['youtube_stream_id'],
                    'stream_key': pending_session['youtube_stream_key'],
                    'rtmp_url': pending_session['youtube_rtmp_url'],
                    'watch_url': pending_session['youtube_watch_url']
                }
                logger.info(f"Using pre-created session {session_id}")
            else:
                if pending_session:
                    logger.warning(f"Pending session {pending_session['id']} found but missing RTMP credentials — creating new broadcast")
                else:
                    logger.info(f"No pending session for schedule {schedule['id']} — creating new broadcast")
                # Create new session and YouTube broadcast
                session_id = create_stream_session(
                    ward_id=ward_id,
                    schedule_id=schedule['id'],
                    scheduled_start=datetime.now()
                )
                
                # Determine broadcast title - use exact title if specified
                if schedule.get('broadcast_title'):
                    title = schedule['broadcast_title']
                elif schedule.get('custom_title'):
                    title = schedule['custom_title']
                else:
                    title = f"{ward['name']} Sacrament Meeting"
                
                # Calculate meeting time (now + pre-roll since we're starting the stream now)
                meeting_start = datetime.now() + timedelta(minutes=STREAM_PRE_ROLL_MINUTES)
                
                stream_info = await setup_youtube_stream(
                    ward_id, title,
                    scheduled_start=meeting_start,
                    dedicated_stream=not schedule.get('is_recurring', True)
                )
                
                if not stream_info:
                    raise Exception("Failed to set up YouTube stream")
                
                # Update session with YouTube info
                update_stream_session(
                    session_id,
                    youtube_broadcast_id=stream_info['broadcast_id'],
                    youtube_stream_id=stream_info['stream_id'],
                    youtube_stream_key=stream_info['stream_key'],
                    youtube_watch_url=stream_info['watch_url'],
                    youtube_rtmp_url=stream_info['rtmp_url']
                )
            
            # Move to default PTZ preset before starting stream
            default_preset = get_default_ptz_preset()
            if default_preset:
                logger.info(f"Moving to default preset '{default_preset['name']}' before stream start")
                ok, msg = await absolute_move(
                    default_preset['pan'], default_preset['tilt'], default_preset['zoom'],
                    default_preset['pan_speed'], default_preset['tilt_speed'],
                    default_preset.get('zoom_speed', 4)
                )
                if not ok:
                    logger.warning(f"Failed to move to default preset: {msg}")
                else:
                    await asyncio.sleep(3)  # Give camera time to reach position

            # Start FFmpeg
            rtmp_url = stream_info['rtmp_url']
            stream_key = stream_info['stream_key']

            # For recurring events, verify the stream key hasn't been changed in YouTube Studio
            # since the broadcast was created (catches post-creation changes before stream start)
            stream_invalid = False
            if schedule.get('is_recurring') and stream_info.get('stream_id'):
                try:
                    token = await youtube_service.get_valid_token(ward_id)
                    if token:
                        live_stream = await youtube_service.get_stream_status(token, stream_info['stream_id'])
                        live_cdn = live_stream.get("cdn", {}).get("ingestionInfo", {})
                        live_key = live_cdn.get("streamName")
                        live_rtmp = live_cdn.get("ingestionAddress")
                        if live_key and live_key != stream_key:
                            logger.warning(
                                f"Stream key mismatch detected at start time for ward {ward['name']} — "
                                f"updating to current YouTube key"
                            )
                            stream_key = live_key
                            rtmp_url = live_rtmp
                            update_stream_session(session_id, youtube_stream_key=stream_key, youtube_rtmp_url=rtmp_url)
                            update_ward(ward_id, youtube_stream_key=stream_key, youtube_rtmp_url=rtmp_url)
                except Exception as e:
                    err_str = str(e)
                    if "not found" in err_str.lower() or "404" in err_str:
                        # Stream was deleted from YouTube — broadcast is invalid, recreate it
                        logger.warning(f"Stream not found at start time for ward {ward['name']} — recreating broadcast")
                        stream_invalid = True
                    else:
                        logger.warning(f"Could not verify stream key at start time for ward {ward['name']}: {e} — using stored key")

            # If verification revealed a missing stream, create a fresh broadcast immediately
            if stream_invalid:
                if schedule.get('broadcast_title'):
                    title = schedule['broadcast_title']
                elif schedule.get('custom_title'):
                    title = schedule['custom_title']
                else:
                    title = f"{ward['name']} Sacrament Meeting"
                meeting_start = datetime.now() + timedelta(minutes=STREAM_PRE_ROLL_MINUTES)

                fresh = await setup_youtube_stream(
                    ward_id, title,
                    scheduled_start=meeting_start,
                    dedicated_stream=not schedule.get('is_recurring', True)
                )
                if not fresh:
                    raise Exception("Failed to recreate YouTube broadcast after stream not found")

                stream_info = fresh
                rtmp_url = fresh['rtmp_url']
                stream_key = fresh['stream_key']
                update_stream_session(
                    session_id,
                    youtube_broadcast_id=fresh['broadcast_id'],
                    youtube_stream_id=fresh['stream_id'],
                    youtube_stream_key=fresh['stream_key'],
                    youtube_watch_url=fresh['watch_url'],
                    youtube_rtmp_url=fresh['rtmp_url']
                )
                logger.info(f"Recreated broadcast for {ward['name']}: {fresh['watch_url']}")

            success, msg = await stream_manager.start_stream(
                rtmp_url=rtmp_url,
                stream_key=stream_key,
                ward_id=ward_id,
                session_id=session_id
            )
            
            if not success:
                raise Exception(f"Failed to start FFmpeg: {msg}")
            
            # Wait for stream to stabilize
            # With enableAutoStart=True, YouTube will automatically go live when it receives data
            await asyncio.sleep(15)
            
            # Try manual transition as backup (in case autoStart didn't trigger)
            success, status = await start_youtube_broadcast(ward_id, stream_info['broadcast_id'])
            if success:
                logger.info(f"Stream transitioned to live for {ward['name']}")
            else:
                # This is expected if autoStart already triggered
                logger.info(f"Stream may already be live via autoStart: {status}")

            # Re-apply thumbnail after going live — YouTube can reset thumbnails on transition
            from routers.admin import _apply_pending_thumbnail
            is_one_off = not schedule.get('is_recurring', True)
            await _apply_pending_thumbnail(
                schedule['id'], ward_id, stream_info['broadcast_id'], keep_file=not is_one_off
            )

            update_stream_session(session_id, status='live', actual_start=datetime.now())
            logger.info(f"Stream live for {ward['name']}")
            
            # Schedule auto-end
            meeting_duration = schedule.get('meeting_duration_minutes', 60)
            total_duration = STREAM_PRE_ROLL_MINUTES + meeting_duration + STREAM_POST_ROLL_MINUTES
            end_time = datetime.now() + timedelta(minutes=total_duration)
            
            end_job_id = f"end_{session_id}"
            self.scheduler.add_job(
                self._end_scheduled_stream,
                DateTrigger(run_date=end_time),
                args=[session_id, ward_id, stream_info['broadcast_id']],
                id=end_job_id,
                replace_existing=True
            )
            self._stream_end_job_id = end_job_id
            
            logger.info(f"Scheduled stream end at {end_time}")
            
        except Exception as e:
            logger.error(f"Error starting stream for {ward['name']}: {e}")
            
            if retry_count < max_retries:
                logger.info(f"Will retry in {RETRY_DELAY} seconds (attempt {retry_count + 1}/{max_retries})...")
                await asyncio.sleep(RETRY_DELAY)
                await self._start_scheduled_stream(schedule, retry_count + 1)
            else:
                logger.error(f"Max retries reached for schedule {schedule['id']}")
                # Mark session as error if it exists
                if 'session_id' in locals():
                    update_stream_session(session_id, status='error')
    
    async def _end_scheduled_stream(self, session_id: int, ward_id: int, broadcast_id: str):
        """End a scheduled stream."""
        logger.info(f"Ending scheduled stream session {session_id}")
        
        # Stop FFmpeg
        await stream_manager.stop_stream()
        
        # End YouTube broadcast
        success, status = await end_youtube_broadcast(ward_id, broadcast_id)
        if not success:
            logger.warning(f"Failed to end YouTube broadcast: {status}")
        
        # Get peak viewers
        await asyncio.sleep(5)
        peak_viewers = await get_peak_viewers(ward_id, broadcast_id)
        
        estimated_attendance = None
        if peak_viewers:
            estimated_attendance = calculate_attendance(peak_viewers)
        
        # Update session
        update_stream_session(
            session_id,
            status='ended',
            actual_end=datetime.now(),
            peak_viewers=peak_viewers,
            estimated_attendance=estimated_attendance
        )
        
        self._stream_end_job_id = None
        logger.info(f"Stream ended. Peak viewers: {peak_viewers}")
    
    async def manual_end_stream(self, session_id: int):
        """Manually end a stream early."""
        session = get_stream_session(session_id)
        if not session:
            return False, "Session not found"
        
        # Cancel scheduled end job
        if self._stream_end_job_id:
            try:
                self.scheduler.remove_job(self._stream_end_job_id)
            except:
                pass
        
        await self._end_scheduled_stream(
            session_id,
            session['ward_id'],
            session['youtube_broadcast_id']
        )
        
        return True, "Stream ended"
    
    async def _cleanup_old_streams(self):
        """Delete old YouTube recordings, expired one-offs, and create next week's broadcasts (runs at 1 AM)."""
        logger.info("Running daily cleanup")
        
        # Delete expired one-off schedules
        deleted_count = delete_expired_oneoff_schedules()
        if deleted_count > 0:
            logger.info(f"Deleted {deleted_count} expired one-off schedule(s)")

        # Delete expired schedule exceptions (skip dates that have passed)
        expired_exceptions = delete_expired_schedule_exceptions()
        if expired_exceptions > 0:
            logger.info(f"Deleted {expired_exceptions} expired schedule exception(s)")
        
        sessions = get_sessions_pending_deletion()
        
        for session in sessions:
            if session.get('youtube_broadcast_id'):
                # Fetch final view count BEFORE deleting — video won't exist after
                final_viewers = await get_peak_viewers(session['ward_id'], session['youtube_broadcast_id'])
                if final_viewers is not None and final_viewers != session.get('peak_viewers'):
                    from services.email import calculate_attendance
                    update_stream_session(session['id'], peak_viewers=final_viewers,
                                         estimated_attendance=calculate_attendance(final_viewers))
                    session['peak_viewers'] = final_viewers  # Update local copy for email

                # Delete the old recording
                success = await delete_youtube_broadcast(
                    session['ward_id'],
                    session['youtube_broadcast_id']
                )
                if success:
                    update_stream_session(session['id'], deleted_from_youtube=True)
                    logger.info(f"Deleted recording for session {session['id']}")

                    # Delete the stream ingestion point if it was a dedicated one-off stream
                    # (i.e. stream_id doesn't match the ward's persistent stream)
                    session_stream_id = session.get('youtube_stream_id')
                    if session_stream_id:
                        ward = get_ward(session['ward_id'])
                        ward_stream_id = ward.get('youtube_stream_id') if ward else None
                        if session_stream_id != ward_stream_id:
                            stream_deleted = await delete_youtube_stream(session['ward_id'], session_stream_id)
                            if stream_deleted:
                                logger.info(f"Deleted dedicated stream {session_stream_id} for session {session['id']}")
                            else:
                                logger.warning(f"Failed to delete dedicated stream {session_stream_id} for session {session['id']}")

                    # Determine whether to send attendance email
                    testing_mode = get_setting("testing_mode", "false") == "true"
                    schedule_id = session.get('schedule_id')
                    if schedule_id:
                        schedule = get_schedule(schedule_id)
                        is_recurring_sunday = (
                            schedule and
                            schedule.get('is_recurring') and
                            schedule.get('day_of_week') == 6  # 6 = Sunday
                        )
                    else:
                        is_recurring_sunday = False
                        schedule = None

                    if testing_mode or is_recurring_sunday:
                        if testing_mode and not is_recurring_sunday:
                            logger.info(f"Testing mode: sending attendance email for non-Sunday session {session['id']}")
                        await self._send_attendance_email(session)
                    else:
                        logger.info(f"Skipping attendance email for session {session['id']} — not a recurring Sunday meeting")

                    # If this was from a recurring schedule, create next week's broadcast
                    if schedule_id:
                        if schedule and schedule.get('is_recurring') and schedule.get('active'):
                            await self._create_next_broadcast(schedule)
                else:
                    logger.warning(f"Failed to delete recording for session {session['id']}")
        
        # Also ensure all active recurring schedules have upcoming broadcasts
        await self._ensure_upcoming_broadcasts()
    
    async def _create_next_broadcast(self, schedule: dict):
        """Create a YouTube broadcast for the next occurrence of a recurring schedule."""
        ward_id = schedule['ward_id']
        ward = get_ward(ward_id)
        
        if not ward or not ward.get('youtube_refresh_token'):
            logger.warning(f"Cannot create broadcast for schedule {schedule['id']}: no YouTube auth")
            return
        
        # Check if there's already a pending session for this schedule
        existing = get_pending_session_for_schedule(schedule['id'])
        if existing:
            logger.info(f"Schedule {schedule['id']} already has pending broadcast")
            return
        
        # Calculate next occurrence, skipping excepted dates
        today = date.today()
        days_ahead = schedule['day_of_week'] - today.weekday()
        if days_ahead <= 0:
            days_ahead += 7  # Next week

        next_date = today + timedelta(days=days_ahead)

        # If next occurrence is excepted, look one week further
        if is_date_excepted(schedule['id'], next_date.strftime('%Y-%m-%d')):
            logger.info(f"Schedule {schedule['id']} has exception on {next_date} — skipping to following week")
            next_date += timedelta(weeks=1)
        hour, minute = map(int, schedule['start_time'].split(':'))
        
        # Meeting time (what YouTube will display)
        meeting_start = datetime.combine(next_date, datetime.min.time().replace(hour=hour, minute=minute))
        # Stream start time (when FFmpeg actually starts)
        stream_start = meeting_start - timedelta(minutes=STREAM_PRE_ROLL_MINUTES)
        
        # Determine title
        title = schedule.get('broadcast_title') or f"{ward['name']} Sacrament Meeting"
        
        try:
            testing_mode = get_setting("testing_mode", "false") == "true"
            privacy = "unlisted" if testing_mode else "public"
            # Pass meeting_start to YouTube so it displays the correct time
            stream_info = await setup_youtube_stream(
                ward_id, title, 
                privacy=privacy,
                scheduled_start=meeting_start
            )
            
            if stream_info:
                session_id = create_stream_session(
                    ward_id=ward_id,
                    schedule_id=schedule['id'],
                    scheduled_start=stream_start  # FFmpeg start time
                )
                
                update_stream_session(
                    session_id,
                    youtube_broadcast_id=stream_info['broadcast_id'],
                    youtube_stream_id=stream_info['stream_id'],
                    youtube_stream_key=stream_info['stream_key'],
                    youtube_watch_url=stream_info['watch_url'],
                    youtube_rtmp_url=stream_info['rtmp_url'],
                    status='scheduled'
                )
                
                logger.info(f"Created next broadcast for {ward['name']} on {next_date}: {stream_info['watch_url']}")
                logger.info(f"  YouTube shows: {meeting_start}, FFmpeg starts: {stream_start}")

                # Apply saved thumbnail if one exists — keep file so it reapplies each week
                from routers.admin import _apply_pending_thumbnail
                await _apply_pending_thumbnail(
                    schedule['id'], ward_id, stream_info['broadcast_id'], keep_file=True
                )
        except Exception as e:
            logger.error(f"Failed to create next broadcast for schedule {schedule['id']}: {e}")
    
    async def _ensure_upcoming_broadcasts(self):
        """Ensure all active recurring schedules have upcoming broadcasts."""
        schedules = get_all_schedules()
        
        for schedule in schedules:
            if not schedule.get('is_recurring') or not schedule.get('active'):
                continue
            
            # Check if there's already a pending session
            existing = get_pending_session_for_schedule(schedule['id'])
            if not existing:
                await self._create_next_broadcast(schedule)
    
    async def _send_attendance_email(self, session: dict):
        """Send attendance email for one session using the view count already on the session."""
        from database import get_ward
        from services.email import send_attendance_report

        ward = get_ward(session['ward_id'])
        if not ward or not ward.get('email_addresses'):
            return

        email_addresses = ward.get('email_addresses', [])
        if isinstance(email_addresses, str):
            import json
            email_addresses = json.loads(email_addresses)
        if not email_addresses:
            return

        peak_viewers = session.get('peak_viewers')

        stream_date = session.get('actual_start') or session.get('scheduled_start')
        if isinstance(stream_date, str):
            stream_date = datetime.fromisoformat(stream_date)

        success, msg = send_attendance_report(
            ward_name=session['ward_name'],
            stream_date=stream_date,
            peak_viewers=peak_viewers,
            to_addresses=email_addresses
        )

        if success:
            update_stream_session(session['id'], email_sent=True)
            logger.info(f"Sent attendance email for {session['ward_name']} (views: {peak_viewers})")
        else:
            logger.error(f"Failed to send attendance email for session {session['id']}: {msg}")

    async def _check_youtube_token_health(self):
        """Check YouTube token health and alert if any wards are missing authorization.

        Only checks whether a refresh token is stored — does NOT proactively call
        Google's token endpoint, which would cause unnecessary daily refreshes and
        risk triggering Google's token revocation policies.

        A missing refresh token means the ward needs to be reconnected.
        Transient refresh failures during actual stream setup are logged separately.
        """
        from services.email import send_email
        from config import ADMIN_BCC_EMAIL

        logger.info("Running YouTube token health check")
        failed_wards = []

        for ward in get_all_wards():
            if not ward.get('youtube_refresh_token'):
                # No refresh token stored — ward needs authorization
                if ward.get('youtube_channel_id'):
                    # Was previously connected but token was cleared
                    failed_wards.append(ward['name'])
                    logger.warning(f"YouTube token missing for ward '{ward['name']}' — reconnect required")

        if failed_wards:
            recipient = ADMIN_BCC_EMAIL
            if not recipient:
                logger.warning("Token health check found missing tokens but ADMIN_BCC_EMAIL is not set — cannot send alert")
                return

            ward_list = ", ".join(failed_wards)
            subject = "⚠️ Webcast: YouTube Re-authorization Required"
            body = f"""
            <html><body style="font-family:sans-serif;padding:20px;">
            <h2>YouTube Authorization Expired</h2>
            <p>The following ward(s) need to be re-authorized on YouTube before the next broadcast:</p>
            <p><strong>{ward_list}</strong></p>
            <p>Log in to Webcast, go to the <strong>YouTube tab</strong>, and reconnect the affected ward(s).</p>
            <p style="color:#999;font-size:0.85em;">This is an automated alert from Webcast.</p>
            </body></html>
            """
            success, msg = send_email([recipient], subject, body)
            if success:
                logger.info(f"Sent token expiry alert for: {ward_list}")
            else:
                logger.error(f"Failed to send token expiry alert: {msg}")
        else:
            logger.info("YouTube token health check passed — all wards have stored credentials")

    def get_upcoming_jobs(self) -> list:
        """Get list of upcoming scheduled jobs."""
        jobs = []
        for job in self.scheduler.get_jobs():
            jobs.append({
                "id": job.id,
                "name": job.name,
                "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
                "trigger": str(job.trigger)
            })
        return jobs
    
    # Aliases for router compatibility
    def get_upcoming_streams(self) -> list:
        """Alias for get_upcoming_jobs."""
        return self.get_upcoming_jobs()
    
    def cancel_stream(self, job_id: str) -> bool:
        """Cancel a scheduled stream job."""
        try:
            self.scheduler.remove_job(job_id)
            return True
        except:
            return False


# Singleton instance
scheduler_service = SchedulerService()


# =============================================================================
# Manual stream control functions (used by routers)
# =============================================================================

async def manual_start_stream(ward_id: int, title: str = None, privacy: str = "unlisted") -> tuple:
    """Start a stream manually."""
    ward = get_ward(ward_id)
    if not ward:
        return False, {"error": "Ward not found"}
    
    if not ward.get('youtube_refresh_token'):
        return False, {"error": "Ward has no YouTube authorization"}
    
    if stream_manager.is_streaming:
        return False, {"error": "A stream is already running"}
    
    # Create session
    session_id = create_stream_session(
        ward_id=ward_id,
        scheduled_start=datetime.now()
    )
    
    # Set up YouTube with custom title or default
    broadcast_title = title if title else f"{ward['name']} Broadcast"
    stream_info = await setup_youtube_stream(ward_id, broadcast_title, privacy=privacy)
    
    if not stream_info:
        update_stream_session(session_id, status='error')
        return False, {"error": "Failed to set up YouTube stream"}
    
    # Update session with YouTube info and title
    update_stream_session(
        session_id,
        broadcast_title=broadcast_title,
        youtube_broadcast_id=stream_info['broadcast_id'],
        youtube_stream_id=stream_info['stream_id'],
        youtube_stream_key=stream_info['stream_key'],
        youtube_watch_url=stream_info['watch_url'],
        youtube_rtmp_url=stream_info['rtmp_url']
    )
    
    # Start FFmpeg
    success, msg = await stream_manager.start_stream(
        rtmp_url=stream_info['rtmp_url'],
        stream_key=stream_info['stream_key'],
        ward_id=ward_id,
        session_id=session_id
    )
    
    if not success:
        update_stream_session(session_id, status='error')
        return False, {"error": msg}
    
    # Wait for stream to stabilize
    # With enableAutoStart=True, YouTube will automatically go live when it receives data
    await asyncio.sleep(15)
    
    # Try manual transition as backup
    await start_youtube_broadcast(ward_id, stream_info['broadcast_id'])
    
    update_stream_session(session_id, status='live', actual_start=datetime.now())
    
    return True, {
        "session_id": session_id,
        "broadcast_id": stream_info['broadcast_id'],
        "watch_url": stream_info['watch_url'],
        "ward_name": ward['name']
    }


async def manual_stop_stream() -> tuple:
    """Stop the current stream."""
    if not stream_manager.is_streaming:
        return False, "No stream running"
    
    info = stream_manager.current_info
    session_id = info.get('session_id')
    
    if session_id:
        session = get_stream_session(session_id)
        if session:
            return await scheduler_service.manual_end_stream(session_id)
    
    # Fallback: just stop FFmpeg
    return await stream_manager.stop_stream()


async def pause_stream() -> tuple:
    """Pause the current stream."""
    success, msg = await stream_manager.pause_stream()
    
    if success:
        info = stream_manager.current_info
        if info.get('session_id'):
            update_stream_session(info['session_id'], status='paused')
    
    return success, msg


async def resume_stream() -> tuple:
    """Resume from pause."""
    success, msg = await stream_manager.resume_stream()
    
    if success:
        info = stream_manager.current_info
        if info.get('session_id'):
            update_stream_session(info['session_id'], status='live')
    
    return success, msg
