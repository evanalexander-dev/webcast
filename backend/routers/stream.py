"""
Webcast - Stream Control Router
Handles starting, stopping, pausing, and resuming streams.
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional

from routers.auth import get_current_user, require_admin
from database import get_active_stream_session, get_stream_session, get_all_wards
from services.stream_manager import stream_manager, StreamState
from services.scheduler import (
    manual_start_stream, manual_stop_stream, pause_stream, resume_stream,
    scheduler_service
)

router = APIRouter(prefix="/stream", tags=["Stream Control"])


class StartStreamRequest(BaseModel):
    ward_id: int
    title: Optional[str] = None  # Custom broadcast title
    privacy: Optional[str] = "unlisted"  # public, unlisted, private


# =============================================================================
# Stream Status (available to all authenticated users)
# =============================================================================

@router.get("/status")
async def get_stream_status(user: dict = Depends(get_current_user)):
    """Get current stream status."""
    # Get stream manager state
    manager_info = stream_manager.current_info
    
    # Get active session from database
    active_session = get_active_stream_session()
    
    youtube_url = None
    broadcast_title = None
    is_scheduled = False
    
    if active_session:
        if active_session.get("youtube_broadcast_id"):
            youtube_url = f"https://youtu.be/{active_session['youtube_broadcast_id']}"
        
        # Get broadcast title from schedule or use ward name
        broadcast_title = active_session.get('broadcast_title') or active_session.get('custom_title')
        
        # It's a scheduled stream if it has a schedule_id
        is_scheduled = active_session.get('schedule_id') is not None
    
    return {
        "stream_state": manager_info["state"],
        "is_paused": manager_info["is_paused"],
        "is_streaming": stream_manager.is_streaming,
        "session": {
            "id": active_session["id"] if active_session else None,
            "ward_name": active_session["ward_name"] if active_session else None,
            "status": active_session["status"] if active_session else None,
            "started_at": active_session["actual_start"] if active_session else None,
            "youtube_url": youtube_url,
            "broadcast_title": broadcast_title,
            "is_scheduled": is_scheduled
        } if active_session else None
    }


@router.get("/health")
async def get_stream_health(user: dict = Depends(get_current_user)):
    """Get stream health information."""
    health = stream_manager.check_health()
    return health


# =============================================================================
# Stream Control (available to all authenticated users)
# =============================================================================

@router.post("/pause")
async def pause_current_stream(user: dict = Depends(get_current_user)):
    """Pause the current stream (switch to pause screen)."""
    if not stream_manager.is_streaming:
        raise HTTPException(status_code=400, detail="No stream is currently running")
    
    success, message = await pause_stream()
    
    if not success:
        raise HTTPException(status_code=400, detail=message)
    
    return {"success": True, "message": message}


@router.post("/resume")
async def resume_current_stream(user: dict = Depends(get_current_user)):
    """Resume the stream from pause."""
    if not stream_manager.is_paused:
        raise HTTPException(status_code=400, detail="Stream is not paused")
    
    success, message = await resume_stream()
    
    if not success:
        raise HTTPException(status_code=400, detail=message)
    
    return {"success": True, "message": message}


# =============================================================================
# Manual Stream Control (admin only for start/stop)
# =============================================================================

@router.post("/start")
async def start_stream(
    request: StartStreamRequest,
    admin: dict = Depends(require_admin)
):
    """Manually start a stream for a ward."""
    if stream_manager.is_streaming:
        raise HTTPException(status_code=400, detail="A stream is already running")
    
    # Validate privacy
    privacy = request.privacy or "unlisted"
    if privacy not in ("public", "unlisted", "private"):
        raise HTTPException(status_code=400, detail="Privacy must be public, unlisted, or private")
    
    success, result = await manual_start_stream(
        request.ward_id, 
        title=request.title,
        privacy=privacy
    )
    
    if not success:
        raise HTTPException(status_code=400, detail=result.get("error", "Failed to start stream"))
    
    return {
        "success": True,
        "session_id": result.get("session_id"),
        "broadcast_id": result.get("broadcast_id"),
        "watch_url": result.get("watch_url"),
        "ward_name": result.get("ward_name")
    }


@router.post("/stop")
async def stop_stream(admin: dict = Depends(require_admin)):
    """Manually stop the current stream."""
    if not stream_manager.is_streaming:
        raise HTTPException(status_code=400, detail="No stream is currently running")
    
    success, message = await manual_stop_stream()
    
    if not success:
        raise HTTPException(status_code=400, detail=message)
    
    return {"success": True, "message": message}


# =============================================================================
# Available Wards for Streaming
# =============================================================================

@router.get("/wards")
async def get_streamable_wards(user: dict = Depends(get_current_user)):
    """Get list of wards available for streaming."""
    wards = get_all_wards()
    
    return {
        "wards": [
            {
                "id": ward["id"],
                "name": ward["name"],
                "has_youtube": bool(ward.get("youtube_refresh_token"))
            }
            for ward in wards
        ]
    }


# =============================================================================
# Scheduled Jobs Info
# =============================================================================

@router.get("/scheduled")
async def get_scheduled_streams(admin: dict = Depends(require_admin)):
    """Get upcoming scheduled stream jobs."""
    jobs = scheduler_service.get_upcoming_streams()
    
    # Filter to only stream-related jobs
    stream_jobs = [j for j in jobs if j["id"].startswith("stream_")]
    
    return {"scheduled_jobs": stream_jobs}


@router.delete("/scheduled/{job_id}")
async def cancel_scheduled_stream(
    job_id: str,
    admin: dict = Depends(require_admin)
):
    """Cancel a scheduled stream."""
    if not job_id.startswith("stream_"):
        raise HTTPException(status_code=400, detail="Invalid job ID")
    
    success = scheduler_service.cancel_stream(job_id)
    
    if not success:
        raise HTTPException(status_code=404, detail="Job not found")
    
    return {"success": True}
