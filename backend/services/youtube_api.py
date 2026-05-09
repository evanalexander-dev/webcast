"""
Webcast - YouTube API Service
Handles OAuth device flow and YouTube Live Streaming API.
"""
import asyncio
import httpx
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Tuple, List
import json

logger = logging.getLogger(__name__)

from config import (
    GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET,
    GOOGLE_DEVICE_AUTH_URL, GOOGLE_TOKEN_URL, GOOGLE_SCOPES
)
from database import get_ward, update_ward


class YouTubeAuthError(Exception):
    """YouTube authentication error."""
    pass


class YouTubeAPIError(Exception):
    """YouTube API error."""
    pass


class YouTubeService:
    """Service for YouTube Live Streaming API."""
    
    API_BASE = "https://www.googleapis.com/youtube/v3"
    
    def __init__(self):
        self.timeout = 30.0
    
    # =========================================================================
    # OAuth Device Flow
    # =========================================================================
    
    async def start_device_auth(self) -> Dict:
        """
        Start the OAuth device flow.
        Returns device_code, user_code, verification_url, and interval.
        """
        if not GOOGLE_CLIENT_ID:
            raise YouTubeAuthError("GOOGLE_CLIENT_ID not configured in .env")
            
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                GOOGLE_DEVICE_AUTH_URL,
                data={
                    "client_id": GOOGLE_CLIENT_ID,
                    "scope": " ".join(GOOGLE_SCOPES)
                }
            )
            
            if response.status_code != 200:
                raise YouTubeAuthError(f"Failed to start device auth: {response.text}")
            
            return response.json()
    
    async def poll_for_token(self, device_code: str) -> Dict:
        """
        Poll for the token after user has authorized.
        Returns access_token, refresh_token, expires_in.
        """
        if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
            raise YouTubeAuthError("Google credentials not configured in .env")
            
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "client_id": GOOGLE_CLIENT_ID,
                    "client_secret": GOOGLE_CLIENT_SECRET,
                    "device_code": device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code"
                }
            )
            
            data = response.json()
            
            if response.status_code == 200:
                return {
                    "access_token": data["access_token"],
                    "refresh_token": data.get("refresh_token"),
                    "expires_in": data.get("expires_in", 3600),
                    "token_type": data.get("token_type", "Bearer")
                }
            
            error = data.get("error")
            if error == "authorization_pending":
                return {"pending": True, "error": error}
            elif error == "slow_down":
                return {"pending": True, "error": error, "slow_down": True}
            elif error == "expired_token":
                raise YouTubeAuthError("Device code expired. Please start again.")
            elif error == "access_denied":
                raise YouTubeAuthError("User denied access.")
            else:
                raise YouTubeAuthError(f"Token error: {error} - {data.get('error_description', '')}")
    
    async def refresh_access_token(self, refresh_token: str) -> Dict:
        """Refresh an access token."""
        if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
            raise YouTubeAuthError("Google credentials not configured in .env")
            
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "client_id": GOOGLE_CLIENT_ID,
                    "client_secret": GOOGLE_CLIENT_SECRET,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token"
                }
            )
            
            if response.status_code != 200:
                raise YouTubeAuthError(f"Failed to refresh token: {response.text}")
            
            data = response.json()
            return {
                "access_token": data["access_token"],
                "expires_in": data.get("expires_in", 3600)
            }
    
    async def get_valid_token(self, ward_id: int) -> Optional[str]:
        """Get a valid access token for a ward, refreshing if needed."""
        ward = get_ward(ward_id)
        if not ward or not ward.get('youtube_refresh_token'):
            return None
        
        # Check if token is still valid (with 5 minute buffer)
        expiry = ward.get('youtube_token_expiry')
        if expiry:
            if isinstance(expiry, str):
                expiry = datetime.fromisoformat(expiry)
            if datetime.now() < expiry - timedelta(minutes=5):
                return ward.get('youtube_access_token')
        
        # Need to refresh
        try:
            token_data = await self.refresh_access_token(ward['youtube_refresh_token'])
            new_expiry = datetime.now() + timedelta(seconds=token_data['expires_in'])
            
            update_ward(
                ward_id,
                youtube_access_token=token_data['access_token'],
                youtube_token_expiry=new_expiry.isoformat()
            )
            
            return token_data['access_token']
        except YouTubeAuthError as e:
            err_str = str(e)
            # Only clear stored tokens if Google explicitly says the refresh token
            # is invalid or revoked — not for transient errors (network, rate limit, etc.)
            if "invalid_grant" in err_str:
                logger.warning(f"Refresh token for ward {ward_id} has been revoked or expired (invalid_grant) — clearing authorization")
                update_ward(
                    ward_id,
                    youtube_access_token=None,
                    youtube_refresh_token=None,
                    youtube_token_expiry=None
                )
            else:
                logger.warning(f"Transient token refresh failure for ward {ward_id}: {err_str} — keeping stored credentials")
            return None
    
    # =========================================================================
    # Channel Info
    # =========================================================================
    
    async def get_channel_info(self, access_token: str) -> Dict:
        """Get the authenticated user's channel info."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{self.API_BASE}/channels",
                params={
                    "part": "snippet,contentDetails,statistics",
                    "mine": "true"
                },
                headers={"Authorization": f"Bearer {access_token}"}
            )
            
            if response.status_code != 200:
                raise YouTubeAPIError(f"Failed to get channel info: {response.text}")
            
            data = response.json()
            items = data.get("items", [])
            
            if not items:
                raise YouTubeAPIError("No channel found for this account")
            
            channel = items[0]
            return {
                "id": channel["id"],
                "title": channel["snippet"]["title"],
                "description": channel["snippet"].get("description", ""),
                "thumbnail": channel["snippet"]["thumbnails"].get("default", {}).get("url"),
                "subscriberCount": channel["statistics"].get("subscriberCount"),
                "videoCount": channel["statistics"].get("videoCount")
            }
    
    # =========================================================================
    # Live Streaming
    # =========================================================================
    
    async def create_broadcast(
        self,
        access_token: str,
        title: str,
        description: str = "",
        scheduled_start: datetime = None,
        privacy: str = "unlisted"
    ) -> Dict:
        """Create a new live broadcast."""
        # YouTube requires scheduled start time to be in the future (UTC)
        if scheduled_start is None:
            # No time specified, use 1 minute from now (UTC)
            scheduled_start_utc = datetime.utcnow() + timedelta(minutes=1)
        else:
            # scheduled_start is in local time, convert to UTC
            # Get the local timezone offset and subtract it
            import time
            # Check if DST is in effect
            if time.daylight and time.localtime().tm_isdst > 0:
                utc_offset = timedelta(seconds=time.altzone)
            else:
                utc_offset = timedelta(seconds=time.timezone)
            scheduled_start_utc = scheduled_start + utc_offset
        
        # Format as ISO 8601 UTC timestamp
        scheduled_start_str = scheduled_start_utc.strftime("%Y-%m-%dT%H:%M:%S") + "Z"
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.API_BASE}/liveBroadcasts",
                params={"part": "snippet,status,contentDetails"},
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json"
                },
                json={
                    "snippet": {
                        "title": title,
                        "description": description,
                        "scheduledStartTime": scheduled_start_str
                    },
                    "status": {
                        "privacyStatus": privacy,
                        "selfDeclaredMadeForKids": True
                    },
                    "contentDetails": {
                        "enableAutoStart": True,  # Auto-go live when stream data arrives
                        "enableAutoStop": True,    # Auto-end when stream stops
                        "enableDvr": True,
                        "enableLiveChat": False,
                        "recordFromStart": True
                        # latencyPreference omitted — incompatible with made-for-kids content
                    }
                }
            )
            
            if response.status_code not in (200, 201):
                raise YouTubeAPIError(f"Failed to create broadcast: {response.text}")
            
            return response.json()
    
    async def list_streams(self, access_token: str) -> List[Dict]:
        """List existing live streams for the channel."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{self.API_BASE}/liveStreams",
                params={"part": "snippet,cdn,status", "mine": "true"},
                headers={"Authorization": f"Bearer {access_token}"}
            )
            
            if response.status_code != 200:
                return []
            
            data = response.json()
            return data.get("items", [])
    
    async def get_or_create_stream(self, access_token: str, title: str = "Webcast Stream") -> Dict:
        """Get an existing stream or create a new one."""
        # First, try to find an existing stream
        streams = await self.list_streams(access_token)
        
        for stream in streams:
            # Reuse any existing stream
            if stream.get("cdn", {}).get("ingestionInfo"):
                return stream
        
        # No existing stream found, create a new one
        return await self.create_stream(access_token, title)
    
    async def create_stream(self, access_token: str, title: str = "Webcast Stream") -> Dict:
        """Create a new live stream (the actual video stream endpoint)."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.API_BASE}/liveStreams",
                params={"part": "snippet,cdn,status"},
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json"
                },
                json={
                    "snippet": {
                        "title": title
                    },
                    "cdn": {
                        "frameRate": "30fps",
                        "ingestionType": "rtmp",
                        "resolution": "1080p"
                    }
                }
            )
            
            if response.status_code not in (200, 201):
                raise YouTubeAPIError(f"Failed to create stream: {response.text}")
            
            return response.json()
    
    async def bind_broadcast_to_stream(
        self,
        access_token: str,
        broadcast_id: str,
        stream_id: str
    ) -> Dict:
        """Bind a broadcast to a stream."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.API_BASE}/liveBroadcasts/bind",
                params={
                    "part": "id,snippet,contentDetails,status",
                    "id": broadcast_id,
                    "streamId": stream_id
                },
                headers={"Authorization": f"Bearer {access_token}"}
            )
            
            if response.status_code != 200:
                raise YouTubeAPIError(f"Failed to bind broadcast: {response.text}")
            
            return response.json()
    
    async def transition_broadcast(
        self,
        access_token: str,
        broadcast_id: str,
        status: str  # "testing", "live", "complete"
    ) -> Dict:
        """Transition a broadcast to a new status."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.API_BASE}/liveBroadcasts/transition",
                params={
                    "part": "id,snippet,contentDetails,status",
                    "id": broadcast_id,
                    "broadcastStatus": status
                },
                headers={"Authorization": f"Bearer {access_token}"}
            )
            
            if response.status_code != 200:
                raise YouTubeAPIError(f"Failed to transition broadcast: {response.text}")
            
            return response.json()
    
    async def get_broadcast_status(self, access_token: str, broadcast_id: str) -> Dict:
        """Get the current status of a broadcast."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{self.API_BASE}/liveBroadcasts",
                params={
                    "part": "id,snippet,contentDetails,status",
                    "id": broadcast_id
                },
                headers={"Authorization": f"Bearer {access_token}"}
            )
            
            if response.status_code != 200:
                raise YouTubeAPIError(f"Failed to get broadcast status: {response.text}")
            
            data = response.json()
            items = data.get("items", [])
            
            if not items:
                raise YouTubeAPIError("Broadcast not found")
            
            return items[0]
    
    async def get_stream_status(self, access_token: str, stream_id: str) -> Dict:
        """Get the current status of a stream."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{self.API_BASE}/liveStreams",
                params={
                    "part": "id,snippet,cdn,status",
                    "id": stream_id
                },
                headers={"Authorization": f"Bearer {access_token}"}
            )
            
            if response.status_code != 200:
                raise YouTubeAPIError(f"Failed to get stream status: {response.text}")
            
            data = response.json()
            items = data.get("items", [])
            
            if not items:
                raise YouTubeAPIError("Stream not found")
            
            return items[0]
    
    async def delete_broadcast(self, access_token: str, broadcast_id: str) -> bool:
        """Delete a broadcast and its recording.
        
        Uses videos.delete rather than liveBroadcasts.delete because completed
        broadcasts return 403 from the liveBroadcasts endpoint — videos.delete
        works on both live and completed broadcasts.
        A 404 is treated as success since the video is already gone.
        """
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.delete(
                f"{self.API_BASE}/videos",
                params={"id": broadcast_id},
                headers={"Authorization": f"Bearer {access_token}"}
            )
            
            return response.status_code in (204, 404)

    async def delete_stream(self, access_token: str, stream_id: str) -> bool:
        """Delete a live stream (ingestion point). 404 treated as success."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.delete(
                f"{self.API_BASE}/liveStreams",
                params={"id": stream_id},
                headers={"Authorization": f"Bearer {access_token}"}
            )
            return response.status_code in (204, 404)
    
    async def get_video_statistics(self, access_token: str, video_id: str) -> Dict:
        """Get statistics for a video (including view count)."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{self.API_BASE}/videos",
                params={
                    "part": "statistics,liveStreamingDetails",
                    "id": video_id
                },
                headers={"Authorization": f"Bearer {access_token}"}
            )
            
            if response.status_code != 200:
                raise YouTubeAPIError(f"Failed to get video stats: {response.text}")
            
            data = response.json()
            items = data.get("items", [])
            
            if not items:
                return {}
            
            video = items[0]
            stats = video.get("statistics", {})
            live_details = video.get("liveStreamingDetails", {})
            
            return {
                "viewCount": int(stats.get("viewCount", 0)),
                "likeCount": int(stats.get("likeCount", 0)),
                "concurrentViewers": int(live_details.get("concurrentViewers", 0))
            }


# Singleton instance
youtube_service = YouTubeService()


# =============================================================================
# Helper functions for scheduler
# =============================================================================

async def setup_youtube_stream(ward_id: int, title: str, description: str = "", privacy: str = "unlisted", scheduled_start: datetime = None, dedicated_stream: bool = False) -> Optional[Dict]:
    """Set up a YouTube live broadcast using the ward's persistent stream.
    
    Args:
        ward_id: The ward ID
        title: Broadcast title
        description: Broadcast description  
        privacy: Privacy setting (public, unlisted, private)
        scheduled_start: The time to show on YouTube (meeting start time, not stream start)
        dedicated_stream: If True, create a fresh stream instead of reusing the ward's
                          persistent one. Use for one-off events to avoid key conflicts
                          with already-scheduled recurring broadcasts.
    """
    ward = get_ward(ward_id)
    if not ward:
        return None
    
    token = await youtube_service.get_valid_token(ward_id)
    if not token:
        return None
    
    # Check if ward has a persistent stream
    stream_id = ward.get('youtube_stream_id')
    stream_key = ward.get('youtube_stream_key')
    rtmp_url = ward.get('youtube_rtmp_url')
    
    try:
        # Create broadcast (new for each event)
        broadcast = await youtube_service.create_broadcast(
            token, title, description, 
            scheduled_start=scheduled_start,
            privacy=privacy
        )
        broadcast_id = broadcast["id"]
        
        if dedicated_stream:
            # One-off event: create a temporary stream with its own unique key
            # so it doesn't conflict with the ward's persistent recurring stream
            stream = await youtube_service.create_stream(token, f"{title} Stream")
            stream_id = stream["id"]
            cdn = stream["cdn"]["ingestionInfo"]
            rtmp_url = cdn["ingestionAddress"]
            stream_key = cdn["streamName"]
            # Do NOT save back to ward — this stream is temporary
        elif not stream_id or not stream_key:
            # No persistent stream yet — create one and save it for future use
            stream = await youtube_service.get_or_create_stream(token, "Webcast Stream")
            stream_id = stream["id"]
            cdn = stream["cdn"]["ingestionInfo"]
            rtmp_url = cdn["ingestionAddress"]
            stream_key = cdn["streamName"]
            
            # Save for future use
            update_ward(
                ward_id,
                youtube_stream_id=stream_id,
                youtube_stream_key=stream_key,
                youtube_rtmp_url=rtmp_url
            )
        else:
            # Recurring event with existing stream — verify the key hasn't changed in YouTube Studio
            try:
                live_stream = await youtube_service.get_stream_status(token, stream_id)
                live_cdn = live_stream.get("cdn", {}).get("ingestionInfo", {})
                live_key = live_cdn.get("streamName")
                live_rtmp = live_cdn.get("ingestionAddress")

                if live_key and live_key != stream_key:
                    logger.warning(
                        f"Stream key mismatch for ward {ward_id} stream {stream_id} — "
                        f"stored key differs from YouTube. Updating ward record."
                    )
                    stream_key = live_key
                    rtmp_url = live_rtmp
                    update_ward(
                        ward_id,
                        youtube_stream_key=stream_key,
                        youtube_rtmp_url=rtmp_url
                    )
            except YouTubeAPIError as e:
                err_str = str(e).lower()
                if "not found" in err_str or "404" in err_str:
                    # Stream is unreachable — likely belongs to a different OAuth client
                    # or was deleted. Create a new persistent stream.
                    logger.warning(
                        f"Stored stream {stream_id} not found for ward {ward_id} — creating new persistent stream"
                    )
                    new_stream = await youtube_service.get_or_create_stream(token, "Webcast Stream")
                    stream_id = new_stream["id"]
                    cdn = new_stream["cdn"]["ingestionInfo"]
                    rtmp_url = cdn["ingestionAddress"]
                    stream_key = cdn["streamName"]
                    update_ward(
                        ward_id,
                        youtube_stream_id=stream_id,
                        youtube_stream_key=stream_key,
                        youtube_rtmp_url=rtmp_url
                    )
                else:
                    logger.warning(f"Could not verify stream key for ward {ward_id}: {e} — using stored key")
        
        # Bind broadcast to the stream
        await youtube_service.bind_broadcast_to_stream(token, broadcast_id, stream_id)
        
        return {
            "broadcast_id": broadcast_id,
            "stream_id": stream_id,
            "rtmp_url": rtmp_url,
            "stream_key": stream_key,
            "watch_url": f"https://youtu.be/{broadcast_id}"
        }
    
    except YouTubeAPIError as e:
        logger.error(f"setup_youtube_stream failed for ward {ward_id}: {e}")
        return None


async def start_youtube_broadcast(ward_id: int, broadcast_id: str) -> Tuple[bool, str]:
    """Transition a broadcast to live."""
    token = await youtube_service.get_valid_token(ward_id)
    if not token:
        return False, "No valid token"
    
    try:
        await youtube_service.transition_broadcast(token, broadcast_id, "live")
        return True, "live"
    except YouTubeAPIError as e:
        return False, str(e)


async def end_youtube_broadcast(ward_id: int, broadcast_id: str) -> Tuple[bool, str]:
    """End a broadcast by transitioning it to complete.

    Handles cases where the broadcast may be in testing/ready state
    (not yet live) by transitioning through live first if needed.
    """
    token = await youtube_service.get_valid_token(ward_id)
    if not token:
        return False, "No valid token"

    try:
        # Check current state first
        info = await youtube_service.get_broadcast_status(token, broadcast_id)
        current_status = info.get("status", {}).get("lifeCycleStatus", "")

        if current_status in ("complete", "revoked"):
            # Already ended
            return True, current_status

        if current_status in ("created", "ready", "testing"):
            # Not live yet — transition to live first, then complete
            try:
                await youtube_service.transition_broadcast(token, broadcast_id, "live")
                await asyncio.sleep(2)
            except YouTubeAPIError:
                pass  # May already be live or autoStart triggered it

        await youtube_service.transition_broadcast(token, broadcast_id, "complete")
        return True, "complete"
    except YouTubeAPIError as e:
        err = str(e)
        # redundantTransition means it's already complete
        if "redundantTransition" in err:
            return True, "complete"
        return False, err


async def get_peak_viewers(ward_id: int, video_id: str) -> int:
    """Get the total view count for a completed broadcast.

    Uses viewCount from the videos statistics endpoint, which matches the
    original monitor.py approach and remains accurate after the stream ends.
    concurrentViewers from liveStreamingDetails drops to 0 once complete.
    """
    token = await youtube_service.get_valid_token(ward_id)
    if not token:
        return 0

    try:
        stats = await youtube_service.get_video_statistics(token, video_id)
        return stats.get("viewCount", 0)
    except YouTubeAPIError:
        return 0


async def delete_youtube_broadcast(ward_id: int, broadcast_id: str) -> bool:
    """Delete a broadcast and its recording."""
    token = await youtube_service.get_valid_token(ward_id)
    if not token:
        return False
    
    try:
        return await youtube_service.delete_broadcast(token, broadcast_id)
    except YouTubeAPIError:
        return False


async def delete_youtube_stream(ward_id: int, stream_id: str) -> bool:
    """Delete a dedicated live stream (ingestion point) created for a one-off event."""
    token = await youtube_service.get_valid_token(ward_id)
    if not token:
        return False
    try:
        return await youtube_service.delete_stream(token, stream_id)
    except YouTubeAPIError:
        return False


async def set_broadcast_thumbnail(ward_id: int, broadcast_id: str, image_data: bytes, content_type: str) -> Tuple[bool, str]:
    """Upload a thumbnail image to a YouTube broadcast.
    
    Args:
        ward_id: The ward ID (for auth)
        broadcast_id: The YouTube broadcast/video ID
        image_data: Raw image bytes (JPEG or PNG)
        content_type: MIME type, e.g. 'image/jpeg' or 'image/png'
    
    Returns:
        (success, message)
    """
    token = await youtube_service.get_valid_token(ward_id)
    if not token:
        return False, "No valid YouTube token"
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://www.googleapis.com/upload/youtube/v3/thumbnails/set",
                params={"videoId": broadcast_id, "uploadType": "media"},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": content_type
                },
                content=image_data
            )
            if response.status_code in (200, 201):
                return True, "Thumbnail uploaded"
            else:
                return False, f"YouTube API error: {response.text}"
    except Exception as e:
        return False, str(e)

