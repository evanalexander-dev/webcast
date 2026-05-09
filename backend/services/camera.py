"""
Webcast - PTZ Camera Service
Controls the ClearTouch RL500 camera via HTTP CGI commands.
"""
import asyncio
import httpx
from typing import Optional, Tuple
from enum import Enum

from config import (
    CAMERA_IP, CAMERA_CGI_BASE,
    PTZ_PAN_SPEED, PTZ_TILT_SPEED, PTZ_ZOOM_SPEED
)


class PTZDirection(str, Enum):
    UP = "up"
    DOWN = "down"
    LEFT = "left"
    RIGHT = "right"
    UP_LEFT = "leftup"
    UP_RIGHT = "rightup"
    DOWN_LEFT = "leftdown"
    DOWN_RIGHT = "rightdown"
    STOP = "ptzstop"


class PTZZoom(str, Enum):
    IN = "zoomin"
    OUT = "zoomout"
    STOP = "zoomstop"


class PTZFocus(str, Enum):
    IN = "focusin"
    OUT = "focusout"
    STOP = "focusstop"


class CameraService:
    """Service for controlling the PTZ camera."""
    
    def __init__(self, camera_ip: str = CAMERA_IP):
        self.camera_ip = camera_ip
        self.base_url = f"http://{camera_ip}/cgi-bin"
        self.timeout = 5.0
    
    async def _send_command(self, endpoint: str, params: str = "") -> Tuple[bool, str]:
        """Send a command to the camera."""
        url = f"{self.base_url}/{endpoint}"
        if params:
            url = f"{url}?{params}"
        
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(url)
                return response.status_code == 200, response.text
        except httpx.TimeoutException:
            return False, "Camera timeout"
        except httpx.ConnectError:
            return False, "Camera connection failed"
        except Exception as e:
            return False, str(e)
    
    async def move(self, direction: PTZDirection, 
                   pan_speed: int = PTZ_PAN_SPEED, 
                   tilt_speed: int = PTZ_TILT_SPEED) -> Tuple[bool, str]:
        """Move the camera in a direction."""
        # Clamp speeds to valid ranges
        pan_speed = max(1, min(24, pan_speed))
        tilt_speed = max(1, min(20, tilt_speed))
        
        if direction == PTZDirection.STOP:
            params = f"ptzcmd&{direction.value}"
        else:
            params = f"ptzcmd&{direction.value}&{pan_speed}&{tilt_speed}"
        
        return await self._send_command("ptzctrl.cgi", params)
    
    async def stop(self) -> Tuple[bool, str]:
        """Stop camera movement."""
        return await self.move(PTZDirection.STOP)
    
    async def zoom(self, direction: PTZZoom, 
                   speed: int = PTZ_ZOOM_SPEED) -> Tuple[bool, str]:
        """Zoom the camera."""
        speed = max(1, min(7, speed))
        
        if direction == PTZZoom.STOP:
            params = f"ptzcmd&{direction.value}"
        else:
            params = f"ptzcmd&{direction.value}&{speed}"
        
        return await self._send_command("ptzctrl.cgi", params)
    
    async def zoom_stop(self) -> Tuple[bool, str]:
        """Stop zooming."""
        return await self.zoom(PTZZoom.STOP)
    
    async def focus(self, direction: PTZFocus,
                    speed: int = 4) -> Tuple[bool, str]:
        """Adjust camera focus."""
        speed = max(1, min(7, speed))
        
        if direction == PTZFocus.STOP:
            params = f"ptzcmd&{direction.value}"
        else:
            params = f"ptzcmd&{direction.value}&{speed}"
        
        return await self._send_command("ptzctrl.cgi", params)
    
    async def focus_stop(self) -> Tuple[bool, str]:
        """Stop focusing."""
        return await self.focus(PTZFocus.STOP)
    
    async def absolute_move(self, pan: int, tilt: int, zoom: int,
                            pan_speed: int = PTZ_PAN_SPEED,
                            tilt_speed: int = PTZ_TILT_SPEED,
                            zoom_speed: int = PTZ_ZOOM_SPEED) -> Tuple[bool, str]:
        """Move camera to an absolute pan/tilt/zoom position.

        Pan and tilt are integers stored in decimal; they are sent to the
        camera as zero-padded 4-digit hex values.
        Zoom uses the separate 'zoomto' command with its own speed parameter.

        Speed ranges: pan_speed 1-24, tilt_speed 1-20, zoom_speed 1-7.
        """
        pan_speed = max(1, min(24, pan_speed))
        tilt_speed = max(1, min(20, tilt_speed))
        zoom_speed = max(1, min(7, zoom_speed))

        pan_hex = format(pan & 0xFFFF, '04x')
        tilt_hex = format(tilt & 0xFFFF, '04x')
        zoom_hex = format(zoom & 0xFFFF, '04x')

        # Send pan/tilt move
        pt_params = f"ptzcmd&abs&{pan_speed}&{tilt_speed}&{pan_hex}&{tilt_hex}"
        ok, msg = await self._send_command("ptzctrl.cgi", pt_params)
        if not ok:
            return False, f"Pan/tilt failed: {msg}"

        # Send zoom move
        zoom_params = f"ptzcmd&zoomto&{zoom_speed}&{zoom_hex}"
        ok, msg = await self._send_command("ptzctrl.cgi", zoom_params)
        if not ok:
            return False, f"Zoom failed: {msg}"

        return True, "Move sent"

    async def go_home(self) -> Tuple[bool, str]:
        """Move camera to home position."""
        return await self._send_command("ptzctrl.cgi", "ptzcmd&home")
    
    async def get_device_info(self) -> Tuple[bool, dict]:
        """Get camera device information."""
        success, response = await self._send_command("param.cgi", "get_device_conf")
        if success:
            # Parse the response (typically key=value pairs)
            info = {}
            for line in response.strip().split('\n'):
                if '=' in line:
                    key, value = line.split('=', 1)
                    info[key.strip()] = value.strip()
            return True, info
        return False, {"error": response}
    
    async def check_connection(self) -> bool:
        """Check if camera is reachable."""
        success, _ = await self.get_device_info()
        return success
    
    async def take_snapshot(self) -> Tuple[bool, bytes]:
        """Take a JPEG snapshot from the camera."""
        url = f"http://{self.camera_ip}/snapshot.jpg"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(url)
                if response.status_code == 200:
                    return True, response.content
                return False, b""
        except Exception:
            return False, b""


# Singleton instance
camera_service = CameraService()


# Convenience functions
async def move_camera(direction: str, pan_speed: int = None, tilt_speed: int = None) -> Tuple[bool, str]:
    """Move camera in direction (convenience function)."""
    # Handle 'stop' as alias for 'ptzstop'
    if direction.lower() == 'stop':
        direction = 'ptzstop'
    
    try:
        dir_enum = PTZDirection(direction.lower())
    except ValueError:
        return False, f"Invalid direction: {direction}"
    
    kwargs = {}
    if pan_speed is not None:
        kwargs['pan_speed'] = pan_speed
    if tilt_speed is not None:
        kwargs['tilt_speed'] = tilt_speed
    
    return await camera_service.move(dir_enum, **kwargs)


async def stop_camera() -> Tuple[bool, str]:
    """Stop all camera movement."""
    return await camera_service.stop()


async def zoom_camera(direction: str, speed: int = None) -> Tuple[bool, str]:
    """Zoom camera (convenience function)."""
    # Handle 'stop' as alias for 'zoomstop'
    if direction.lower() == 'stop':
        direction = 'zoomstop'
    
    try:
        zoom_enum = PTZZoom(direction.lower())
    except ValueError:
        return False, f"Invalid zoom direction: {direction}"
    
    kwargs = {}
    if speed is not None:
        kwargs['speed'] = speed
    
    return await camera_service.zoom(zoom_enum, **kwargs)


async def absolute_move(pan: int, tilt: int, zoom: int,
                        pan_speed: int = None, tilt_speed: int = None,
                        zoom_speed: int = None) -> Tuple[bool, str]:
    """Move camera to absolute position (convenience function)."""
    kwargs = {}
    if pan_speed is not None:
        kwargs['pan_speed'] = pan_speed
    if tilt_speed is not None:
        kwargs['tilt_speed'] = tilt_speed
    if zoom_speed is not None:
        kwargs['zoom_speed'] = zoom_speed
    return await camera_service.absolute_move(pan, tilt, zoom, **kwargs)


if __name__ == "__main__":
    # Test camera connection
    async def test():
        print(f"Testing camera at {CAMERA_IP}...")
        
        # Check connection
        connected = await camera_service.check_connection()
        print(f"Connected: {connected}")
        
        if connected:
            # Get device info
            success, info = await camera_service.get_device_info()
            if success:
                print("Device info:")
                for k, v in info.items():
                    print(f"  {k}: {v}")
            
            # Test preset recall
            print("\nTesting preset 1...")
            success, msg = await camera_service.go_to_preset(1)
            print(f"Preset 1: {success} - {msg}")
    
    asyncio.run(test())
