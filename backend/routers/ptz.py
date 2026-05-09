"""
Webcast - PTZ Camera Router
Handles camera movement and preset controls.
"""
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import Response
from pydantic import BaseModel
from typing import Optional

from routers.auth import require_auth, require_admin
from database import (
    get_all_ptz_presets, get_ptz_preset, create_ptz_preset,
    update_ptz_preset, delete_ptz_preset, get_default_ptz_preset,
    move_ptz_preset
)
from services.camera import (
    camera_service, move_camera, stop_camera, zoom_camera,
    absolute_move
)

router = APIRouter(prefix="/ptz", tags=["PTZ Camera"])


class MoveRequest(BaseModel):
    direction: str
    pan_speed: Optional[int] = None
    tilt_speed: Optional[int] = None


class ZoomRequest(BaseModel):
    direction: str
    speed: Optional[int] = None


class CreatePresetRequest(BaseModel):
    name: str
    pan: int = 0
    tilt: int = 0
    zoom: int = 0
    pan_speed: int = 12
    tilt_speed: int = 10
    zoom_speed: int = 4
    is_default: bool = False
    description: Optional[str] = None


class UpdatePresetRequest(BaseModel):
    name: Optional[str] = None
    pan: Optional[int] = None
    tilt: Optional[int] = None
    zoom: Optional[int] = None
    pan_speed: Optional[int] = None
    tilt_speed: Optional[int] = None
    zoom_speed: Optional[int] = None
    is_default: Optional[bool] = None
    description: Optional[str] = None


class AbsoluteMoveRequest(BaseModel):
    pan: int
    tilt: int
    zoom: int
    pan_speed: Optional[int] = None
    tilt_speed: Optional[int] = None
    zoom_speed: Optional[int] = None


@router.post("/move")
async def move(request: MoveRequest, user: dict = Depends(require_auth)):
    """Move the camera in a direction."""
    success, message = await move_camera(
        request.direction,
        request.pan_speed,
        request.tilt_speed
    )
    if not success:
        raise HTTPException(status_code=400, detail=message)
    return {"success": True}


@router.post("/stop")
async def stop(user: dict = Depends(require_auth)):
    """Stop camera movement."""
    success, message = await stop_camera()
    if not success:
        raise HTTPException(status_code=400, detail=message)
    return {"success": True}


@router.post("/zoom")
async def zoom(request: ZoomRequest, user: dict = Depends(require_auth)):
    """Zoom the camera."""
    success, message = await zoom_camera(request.direction, request.speed)
    if not success:
        raise HTTPException(status_code=400, detail=message)
    return {"success": True}


@router.post("/home")
async def home(user: dict = Depends(require_auth)):
    """Move camera to home position."""
    success, message = await camera_service.go_home()
    if not success:
        raise HTTPException(status_code=400, detail=message)
    return {"success": True}


@router.post("/absolute")
async def move_absolute(request: AbsoluteMoveRequest, user: dict = Depends(require_auth)):
    """Move camera to an absolute pan/tilt/zoom position."""
    success, message = await absolute_move(
        request.pan, request.tilt, request.zoom,
        request.pan_speed, request.tilt_speed, request.zoom_speed
    )
    if not success:
        raise HTTPException(status_code=400, detail=message)
    return {"success": True}


@router.get("/presets")
async def list_presets(user: dict = Depends(require_auth)):
    """Get all PTZ presets."""
    presets = get_all_ptz_presets()
    return {"presets": presets}


@router.post("/presets/{preset_id}/goto")
async def goto_preset(preset_id: int, user: dict = Depends(require_auth)):
    """Move camera to a saved preset position."""
    preset = get_ptz_preset(preset_id)
    if not preset:
        raise HTTPException(status_code=404, detail="Preset not found")
    success, message = await absolute_move(
        preset["pan"], preset["tilt"], preset["zoom"],
        preset["pan_speed"], preset["tilt_speed"], preset["zoom_speed"]
    )
    if not success:
        raise HTTPException(status_code=400, detail=message)
    return {"success": True, "preset_name": preset["name"]}


@router.post("/presets", dependencies=[Depends(require_admin)])
async def create_new_preset(request: CreatePresetRequest):
    """Create a new preset."""
    preset_id = create_ptz_preset(
        name=request.name,
        pan=request.pan,
        tilt=request.tilt,
        zoom=request.zoom,
        pan_speed=request.pan_speed,
        tilt_speed=request.tilt_speed,
        zoom_speed=request.zoom_speed,
        is_default=request.is_default,
        description=request.description
    )
    return {"success": True, "preset_id": preset_id}


@router.put("/presets/{preset_id}", dependencies=[Depends(require_admin)])
async def update_existing_preset(preset_id: int, request: UpdatePresetRequest):
    """Update a preset."""
    preset = get_ptz_preset(preset_id)
    if not preset:
        raise HTTPException(status_code=404, detail="Preset not found")
    updates = {k: v for k, v in request.dict().items() if v is not None}
    if updates:
        update_ptz_preset(preset_id, **updates)
    return {"success": True}


@router.post("/presets/{preset_id}/move/{direction}", dependencies=[Depends(require_admin)])
async def move_preset_order(preset_id: int, direction: str):
    """Move a preset up or down in display order."""
    if direction not in ("up", "down"):
        raise HTTPException(status_code=400, detail="Direction must be 'up' or 'down'")
    if not move_ptz_preset(preset_id, direction):
        raise HTTPException(status_code=400, detail="Cannot move preset in that direction")
    return {"success": True}


@router.delete("/presets/{preset_id}", dependencies=[Depends(require_admin)])
async def remove_preset(preset_id: int):
    """Delete a preset."""
    if not delete_ptz_preset(preset_id):
        raise HTTPException(status_code=404, detail="Preset not found")
    return {"success": True}


@router.get("/status")
async def camera_status(user: dict = Depends(require_auth)):
    """Check camera connection status."""
    connected = await camera_service.check_connection()
    result = {"connected": connected, "camera_ip": camera_service.camera_ip}
    if connected:
        success, info = await camera_service.get_device_info()
        if success:
            result["device_info"] = info
    return result


@router.get("/snapshot")
async def get_snapshot(user: dict = Depends(require_auth)):
    """Get a JPEG snapshot from the camera."""
    success, image_data = await camera_service.take_snapshot()
    if not success:
        raise HTTPException(status_code=500, detail="Failed to capture snapshot")
    return Response(content=image_data, media_type="image/jpeg")
