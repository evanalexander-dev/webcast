"""
Webcast - Stream Manager Service
Manages FFmpeg processes for streaming to YouTube.
"""
import asyncio
import subprocess
import signal
import os
from datetime import datetime
from typing import Optional, Dict, Tuple
from enum import Enum
from pathlib import Path

from config import (
    GO2RTC_API, CAMERA_RTSP_HD,
    PAUSE_VIDEO_PATH, ASSETS_DIR
)


class StreamState(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    LIVE = "live"
    PAUSED = "paused"
    STOPPING = "stopping"
    ERROR = "error"


class StreamManager:
    """Manages FFmpeg streaming processes."""
    
    MAX_RESTART_ATTEMPTS = 3
    RESTART_DELAY_SECONDS = 5
    
    def __init__(self):
        self._process: Optional[subprocess.Popen] = None
        self._state = StreamState.STOPPED
        self._current_stream_key: Optional[str] = None
        self._current_rtmp_url: Optional[str] = None
        self._is_paused = False
        self._start_time: Optional[datetime] = None
        self._ward_id: Optional[int] = None
        self._session_id: Optional[int] = None
        self._lock = asyncio.Lock()
        self._restart_count = 0
        self._monitor_task: Optional[asyncio.Task] = None
        self._should_be_running = False
    
    @property
    def state(self) -> StreamState:
        return self._state
    
    @property
    def is_streaming(self) -> bool:
        return self._state in (StreamState.LIVE, StreamState.PAUSED)
    
    @property
    def is_paused(self) -> bool:
        return self._is_paused
    
    @property
    def current_info(self) -> Dict:
        return {
            "state": self._state.value,
            "is_paused": self._is_paused,
            "ward_id": self._ward_id,
            "session_id": self._session_id,
            "start_time": self._start_time.isoformat() if self._start_time else None,
            "stream_key": self._current_stream_key[:8] + "..." if self._current_stream_key else None,
            "restart_count": self._restart_count
        }
    
    def _build_ffmpeg_command(self, input_source: str, rtmp_url: str,
                               stream_key: str, is_loop: bool = False) -> list:
        """Build FFmpeg command for streaming."""
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning"]
        
        if is_loop:
            # Looping video file (pause screen)
            cmd.extend([
                "-stream_loop", "-1",  # Loop infinitely
                "-re",  # Read at native frame rate
                "-i", input_source
            ])
        else:
            # Live RTSP input via go2rtc
            # go2rtc provides the stream at /api/stream.mp4?src=chapel_hd
            go2rtc_stream = f"{GO2RTC_API}/api/stream.mp4?src=chapel_hd"
            cmd.extend([
                "-i", go2rtc_stream
            ])
        
        # Output settings - copy codecs (no transcoding)
        cmd.extend([
            "-c:v", "copy",
            "-c:a", "aac",  # Re-encode audio to ensure compatibility
            "-b:a", "128k",
            "-ar", "44100",
            "-f", "flv",
            f"{rtmp_url}/{stream_key}"
        ])
        
        return cmd
    
    async def start_stream(self, rtmp_url: str, stream_key: str,
                           ward_id: int, session_id: int) -> Tuple[bool, str]:
        """Start streaming from camera to YouTube."""
        async with self._lock:
            if self._process is not None:
                return False, "Stream already running"
            
            self._state = StreamState.STARTING
            self._current_rtmp_url = rtmp_url
            self._current_stream_key = stream_key
            self._ward_id = ward_id
            self._session_id = session_id
            self._is_paused = False
            self._should_be_running = True
            self._restart_count = 0
            
            try:
                cmd = self._build_ffmpeg_command(
                    CAMERA_RTSP_HD, rtmp_url, stream_key
                )
                
                self._process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    preexec_fn=os.setsid  # Create new process group
                )
                
                # Wait a moment to check if it started successfully
                await asyncio.sleep(2)
                
                if self._process.poll() is not None:
                    # Process ended unexpectedly
                    stderr = self._process.stderr.read().decode() if self._process.stderr else ""
                    self._state = StreamState.ERROR
                    self._process = None
                    return False, f"FFmpeg failed to start: {stderr[-500:]}"
                
                self._state = StreamState.LIVE
                self._start_time = datetime.now()
                
                # Start health monitor
                self._start_monitor()
                
                return True, "Stream started"
                
            except Exception as e:
                self._state = StreamState.ERROR
                self._process = None
                return False, str(e)
    
    def _start_monitor(self):
        """Start the health monitoring task."""
        if self._monitor_task is None or self._monitor_task.done():
            self._monitor_task = asyncio.create_task(self._monitor_health())
    
    async def _monitor_health(self):
        """Monitor stream health and auto-restart if needed."""
        import logging
        logger = logging.getLogger(__name__)
        
        while self._should_be_running:
            await asyncio.sleep(10)  # Check every 10 seconds
            
            if not self._should_be_running:
                break
            
            if self._process is None:
                continue
            
            poll = self._process.poll()
            if poll is not None:
                # Process died
                logger.warning(f"FFmpeg process died with exit code {poll}")
                
                if self._restart_count < self.MAX_RESTART_ATTEMPTS:
                    self._restart_count += 1
                    logger.info(f"Attempting restart {self._restart_count}/{self.MAX_RESTART_ATTEMPTS}")
                    
                    await asyncio.sleep(self.RESTART_DELAY_SECONDS)
                    
                    # Try to restart
                    if self._should_be_running and self._current_rtmp_url and self._current_stream_key:
                        try:
                            cmd = self._build_ffmpeg_command(
                                CAMERA_RTSP_HD if not self._is_paused else str(PAUSE_VIDEO_PATH),
                                self._current_rtmp_url,
                                self._current_stream_key,
                                is_loop=self._is_paused
                            )
                            
                            self._process = subprocess.Popen(
                                cmd,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                preexec_fn=os.setsid
                            )
                            
                            await asyncio.sleep(2)
                            
                            if self._process.poll() is None:
                                logger.info("Stream restarted successfully")
                                self._state = StreamState.PAUSED if self._is_paused else StreamState.LIVE
                            else:
                                logger.error("Restart failed")
                                self._state = StreamState.ERROR
                        except Exception as e:
                            logger.error(f"Restart error: {e}")
                            self._state = StreamState.ERROR
                else:
                    logger.error(f"Max restart attempts ({self.MAX_RESTART_ATTEMPTS}) reached")
                    self._state = StreamState.ERROR
                    self._should_be_running = False
    
    async def stop_stream(self) -> Tuple[bool, str]:
        """Stop the current stream."""
        async with self._lock:
            # Stop monitoring first
            self._should_be_running = False
            
            if self._process is None:
                self._state = StreamState.STOPPED
                return True, "No stream running"
            
            self._state = StreamState.STOPPING
            
            try:
                # Send SIGTERM to process group
                os.killpg(os.getpgid(self._process.pid), signal.SIGTERM)
                
                # Wait for graceful shutdown
                try:
                    self._process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    # Force kill if needed
                    os.killpg(os.getpgid(self._process.pid), signal.SIGKILL)
                    self._process.wait(timeout=2)
                
            except ProcessLookupError:
                pass  # Process already dead
            except Exception as e:
                return False, f"Error stopping stream: {e}"
            finally:
                self._process = None
                self._state = StreamState.STOPPED
                self._is_paused = False
                self._current_stream_key = None
                self._current_rtmp_url = None
                self._start_time = None
                self._restart_count = 0
            
            return True, "Stream stopped"
    
    async def pause_stream(self) -> Tuple[bool, str]:
        """
        Pause the stream by switching to the pause video.
        This stops the camera FFmpeg and starts the pause video FFmpeg.
        """
        async with self._lock:
            if self._state != StreamState.LIVE:
                return False, f"Cannot pause: stream is {self._state.value}"
            
            if self._is_paused:
                return False, "Already paused"
            
            if not PAUSE_VIDEO_PATH.exists():
                return False, "Pause video not found"
            
            # Kill current process
            if self._process:
                try:
                    os.killpg(os.getpgid(self._process.pid), signal.SIGTERM)
                    self._process.wait(timeout=3)
                except:
                    pass
                self._process = None
            
            # Start pause video stream
            try:
                cmd = self._build_ffmpeg_command(
                    str(PAUSE_VIDEO_PATH),
                    self._current_rtmp_url,
                    self._current_stream_key,
                    is_loop=True
                )
                
                self._process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    preexec_fn=os.setsid
                )
                
                await asyncio.sleep(1)
                
                if self._process.poll() is not None:
                    stderr = self._process.stderr.read().decode() if self._process.stderr else ""
                    return False, f"Failed to start pause video: {stderr[-200:]}"
                
                self._is_paused = True
                self._state = StreamState.PAUSED
                return True, "Stream paused"
                
            except Exception as e:
                return False, str(e)
    
    async def resume_stream(self) -> Tuple[bool, str]:
        """Resume from pause by switching back to camera."""
        async with self._lock:
            if self._state != StreamState.PAUSED:
                return False, f"Cannot resume: stream is {self._state.value}"
            
            if not self._is_paused:
                return False, "Not paused"
            
            # Kill pause video process
            if self._process:
                try:
                    os.killpg(os.getpgid(self._process.pid), signal.SIGTERM)
                    self._process.wait(timeout=3)
                except:
                    pass
                self._process = None
            
            # Start camera stream again
            try:
                cmd = self._build_ffmpeg_command(
                    CAMERA_RTSP_HD,
                    self._current_rtmp_url,
                    self._current_stream_key
                )
                
                self._process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    preexec_fn=os.setsid
                )
                
                await asyncio.sleep(2)
                
                if self._process.poll() is not None:
                    stderr = self._process.stderr.read().decode() if self._process.stderr else ""
                    return False, f"Failed to resume camera: {stderr[-200:]}"
                
                self._is_paused = False
                self._state = StreamState.LIVE
                return True, "Stream resumed"
                
            except Exception as e:
                return False, str(e)
    
    def check_health(self) -> Dict:
        """Check if the stream process is still running."""
        if self._process is None:
            return {"healthy": self._state == StreamState.STOPPED, "state": self._state.value}
        
        poll = self._process.poll()
        if poll is not None:
            # Process has ended
            self._state = StreamState.ERROR
            return {
                "healthy": False,
                "state": self._state.value,
                "exit_code": poll
            }
        
        return {
            "healthy": True,
            "state": self._state.value,
            "pid": self._process.pid
        }


# Singleton instance
stream_manager = StreamManager()


# =============================================================================
# Pause video generation
# =============================================================================

def generate_pause_video(image_path: Path = None, output_path: Path = None,
                         duration: int = 60) -> Tuple[bool, str]:
    """
    Generate a looping pause video from a static image with silent audio.
    Uses 2-second keyframe interval for YouTube compatibility.
    """
    if image_path is None:
        image_path = ASSETS_DIR / "pause.png"
    if output_path is None:
        output_path = PAUSE_VIDEO_PATH
    
    if not image_path.exists():
        return False, f"Image not found: {image_path}"
    
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # FFmpeg command to create video from image with silent audio
    # -g 60 = keyframe every 60 frames (2 seconds at 30fps)
    # -keyint_min 60 = minimum keyframe interval
    cmd = [
        "ffmpeg", "-y",  # Overwrite output
        "-loop", "1",
        "-i", str(image_path),
        "-f", "lavfi",
        "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",  # Silent audio
        "-c:v", "libx264",
        "-preset", "medium",
        "-tune", "stillimage",
        "-crf", "23",
        "-r", "30",  # 30 fps
        "-g", "60",  # Keyframe every 60 frames (2 seconds)
        "-keyint_min", "60",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        "-t", str(duration),
        "-shortest",
        str(output_path)
    ]
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120
        )
        
        if result.returncode != 0:
            return False, f"FFmpeg error: {result.stderr[-500:]}"
        
        return True, f"Created pause video: {output_path}"
        
    except subprocess.TimeoutExpired:
        return False, "Video generation timed out"
    except Exception as e:
        return False, str(e)


# =============================================================================
# go2rtc configuration
# =============================================================================

def get_go2rtc_config() -> str:
    """Generate go2rtc configuration from environment."""
    from config import CAMERA_IP, GO2RTC_STREAM_HD, GO2RTC_STREAM_SD
    
    if not CAMERA_IP:
        return "# CAMERA_IP not configured in .env\n"
    
    return f"""# go2rtc configuration for Webcast
# Auto-generated - use setup-go2rtc.sh for permanent config

api:
  listen: ":1984"

rtsp:
  listen: ":8554"

webrtc:
  listen: ":8555"
  candidates:
    - stun:stun.l.google.com:19302

streams:
  {GO2RTC_STREAM_HD}:
    - rtsp://{CAMERA_IP}/1
  {GO2RTC_STREAM_SD}:
    - rtsp://{CAMERA_IP}/2
"""


def write_go2rtc_config(config_path: Path = None) -> Tuple[bool, str]:
    """Write go2rtc configuration file."""
    from config import BASE_DIR
    
    if config_path is None:
        config_path = BASE_DIR / "go2rtc" / "go2rtc.yaml"
    
    config_path.parent.mkdir(parents=True, exist_ok=True)
    
    try:
        config_path.write_text(get_go2rtc_config())
        return True, f"Wrote config to {config_path}"
    except Exception as e:
        return False, str(e)


if __name__ == "__main__":
    # Generate pause video if image exists
    image_path = ASSETS_DIR / "pause.png"
    
    if image_path.exists():
        print("Generating pause video...")
        success, msg = generate_pause_video()
        print(f"{'✓' if success else '✗'} {msg}")
    else:
        print(f"No pause image found at {image_path}")
        print("Please add your 1080p pause.png image to the assets folder")
    
    # Write go2rtc config
    print("\nWriting go2rtc config...")
    success, msg = write_go2rtc_config()
    print(f"{'✓' if success else '✗'} {msg}")
