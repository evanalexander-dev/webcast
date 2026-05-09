"""
Webcast - YouTube OAuth Router
Handles device flow authentication for YouTube channels.
"""
import asyncio
import logging
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional, Dict
from datetime import datetime, timedelta

from routers.auth import require_admin
from database import get_ward, update_ward, get_all_wards
from services.youtube_api import youtube_service, YouTubeAuthError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/youtube", tags=["YouTube"])

# Store pending device auth flows (in-memory, keyed by ward_id)
_pending_auth: Dict[int, dict] = {}


class StartAuthRequest(BaseModel):
    ward_id: int


class PollAuthRequest(BaseModel):
    ward_id: int


# =============================================================================
# Device Flow OAuth
# =============================================================================

@router.post("/auth/start")
async def start_youtube_auth(
    request: StartAuthRequest,
    admin: dict = Depends(require_admin)
):
    """
    Start the YouTube OAuth device flow for a ward.
    Returns the user code and verification URL.
    """
    ward = get_ward(request.ward_id)
    if not ward:
        raise HTTPException(status_code=404, detail="Ward not found")
    
    try:
        auth_data = await youtube_service.start_device_auth()
        
        # Store the device code for polling
        _pending_auth[request.ward_id] = {
            "device_code": auth_data["device_code"],
            "interval": auth_data.get("interval", 5),
            "expires_at": datetime.now() + timedelta(seconds=auth_data.get("expires_in", 1800)),
            "ward_name": ward["name"]
        }
        
        return {
            "user_code": auth_data["user_code"],
            "verification_url": auth_data["verification_url"],
            "expires_in": auth_data.get("expires_in", 1800),
            "interval": auth_data.get("interval", 5),
            "ward_name": ward["name"]
        }
    
    except YouTubeAuthError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/auth/poll")
async def poll_youtube_auth(
    request: PollAuthRequest,
    admin: dict = Depends(require_admin)
):
    """
    Poll for YouTube OAuth completion.
    Returns status: 'pending', 'success', or 'error'.
    """
    if request.ward_id not in _pending_auth:
        raise HTTPException(status_code=400, detail="No pending auth for this ward")
    
    pending = _pending_auth[request.ward_id]
    
    # Check expiration
    if datetime.now() > pending["expires_at"]:
        del _pending_auth[request.ward_id]
        return {"status": "error", "error": "Authorization expired. Please start again."}
    
    try:
        result = await youtube_service.poll_for_token(pending["device_code"])
        
        if result.get("pending"):
            return {
                "status": "pending",
                "slow_down": result.get("slow_down", False)
            }
        
        # Success! Save the tokens
        access_token = result["access_token"]
        refresh_token = result.get("refresh_token")
        expires_in = result.get("expires_in", 3600)
        
        # Get channel info
        channel_info = await youtube_service.get_channel_info(access_token)
        
        # Get or create a persistent/reusable stream for this ward
        ward = get_ward(request.ward_id)
        stream_data = await youtube_service.get_or_create_stream(access_token, "Webcast Stream")
        
        stream_id = stream_data["id"]
        cdn_info = stream_data["cdn"]["ingestionInfo"]
        rtmp_url = cdn_info["ingestionAddress"]
        stream_key = cdn_info["streamName"]
        
        # Update ward with tokens, channel ID, and stream info
        update_ward(
            request.ward_id,
            youtube_channel_id=channel_info["id"],
            youtube_access_token=access_token,
            youtube_refresh_token=refresh_token,
            youtube_token_expiry=(datetime.now() + timedelta(seconds=expires_in)).isoformat(),
            youtube_stream_id=stream_id,
            youtube_stream_key=stream_key,
            youtube_rtmp_url=rtmp_url
        )

        # Cancel any stale pending sessions for this ward — they have broken
        # broadcast IDs and stream keys from before the reconnect. The scheduler
        # will create fresh sessions when needed.
        from database import get_db
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE stream_sessions SET status='cancelled'
                WHERE ward_id=? AND status='scheduled'
            """, (request.ward_id,))
            cancelled_count = cursor.rowcount
        if cancelled_count:
            logger.info(f"Cancelled {cancelled_count} stale pending session(s) for ward {request.ward_id} after reconnect")

        # Clean up pending auth
        del _pending_auth[request.ward_id]
        
        return {
            "status": "success",
            "channel": {
                "id": channel_info["id"],
                "title": channel_info["title"],
                "thumbnail": channel_info.get("thumbnail")
            }
        }
    
    except YouTubeAuthError as e:
        if request.ward_id in _pending_auth:
            del _pending_auth[request.ward_id]
        return {"status": "error", "error": str(e)}


@router.post("/auth/cancel/{ward_id}")
async def cancel_youtube_auth(
    ward_id: int,
    admin: dict = Depends(require_admin)
):
    """Cancel a pending YouTube auth flow."""
    if ward_id in _pending_auth:
        del _pending_auth[ward_id]
    
    return {"success": True}


# =============================================================================
# Channel Status
# =============================================================================

@router.get("/status")
async def get_youtube_status(admin: dict = Depends(require_admin)):
    """Get YouTube authorization status for all wards."""
    wards = get_all_wards()
    
    status = []
    for ward in wards:
        ward_status = {
            "ward_id": ward["id"],
            "ward_name": ward["name"],
            "authorized": bool(ward.get("youtube_refresh_token")),
            "channel_id": ward.get("youtube_channel_id"),
            "pending_auth": ward["id"] in _pending_auth,
            "token_expired": False,
            "token_expiry": ward.get("youtube_token_expiry")
        }
        
        # If authorized, try to get a valid token to confirm it still works
        if ward.get("youtube_refresh_token"):
            try:
                token = await youtube_service.get_valid_token(ward["id"])
                if token:
                    channel = await youtube_service.get_channel_info(token)
                    ward_status["channel_title"] = channel.get("title")
                    ward_status["channel_thumbnail"] = channel.get("thumbnail")
                else:
                    # get_valid_token returned None — token was cleared due to expiry
                    ward_status["authorized"] = False
                    ward_status["token_expired"] = True
            except:
                ward_status["token_error"] = True
        
        status.append(ward_status)
    
    return {"wards": status}


@router.get("/channel/{ward_id}")
async def get_channel_info(
    ward_id: int,
    admin: dict = Depends(require_admin)
):
    """Get detailed channel info for a ward."""
    ward = get_ward(ward_id)
    if not ward:
        raise HTTPException(status_code=404, detail="Ward not found")
    
    if not ward.get("youtube_refresh_token"):
        raise HTTPException(status_code=400, detail="Ward not authorized with YouTube")
    
    try:
        token = await youtube_service.get_valid_token(ward_id)
        if not token:
            raise HTTPException(status_code=400, detail="Failed to get valid token")
        
        channel = await youtube_service.get_channel_info(token)
        return {"channel": channel}
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/auth/{ward_id}")
async def revoke_youtube_auth(
    ward_id: int,
    admin: dict = Depends(require_admin)
):
    """Remove YouTube authorization for a ward (allows reconnection)."""
    ward = get_ward(ward_id)
    if not ward:
        raise HTTPException(status_code=404, detail="Ward not found")
    
    # Clear tokens and stream info
    update_ward(
        ward_id,
        youtube_channel_id=None,
        youtube_access_token=None,
        youtube_refresh_token=None,
        youtube_token_expiry=None,
        youtube_stream_id=None,
        youtube_stream_key=None,
        youtube_rtmp_url=None
    )
    
    return {"success": True, "message": f"YouTube authorization removed for {ward['name']}"}
