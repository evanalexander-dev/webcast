"""
Webcast - Authentication Router
Handles login, logout, and session management.
"""
from fastapi import APIRouter, Request, Response, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional

from database import (
    authenticate_user, create_session, get_session, delete_session,
    get_user_by_username, create_user, hash_password
)
from config import SESSION_EXPIRE_HOURS

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class CreateUserRequest(BaseModel):
    username: str
    password: str
    is_admin: bool = False
    is_specialist: bool = False
    specialist_ward_id: Optional[int] = None


def get_session_token(request: Request) -> Optional[str]:
    """Extract session token from cookie."""
    return request.cookies.get("session_token")


async def get_current_user(request: Request) -> Optional[dict]:
    """Get current user from session cookie."""
    token = get_session_token(request)
    if not token:
        return None
    return get_session(token)


async def require_auth(request: Request) -> dict:
    """Dependency that requires authentication."""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


async def require_admin(request: Request) -> dict:
    """Dependency that requires admin authentication."""
    user = await require_auth(request)
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


async def require_specialist_or_admin(request: Request) -> dict:
    """Dependency that requires specialist or admin authentication."""
    user = await require_auth(request)
    if not user.get("is_admin") and not user.get("is_specialist"):
        raise HTTPException(status_code=403, detail="Specialist or admin access required")
    return user


@router.post("/login")
async def login(request: LoginRequest, response: Response):
    """Log in with username and password."""
    user = authenticate_user(request.username, request.password)
    
    if not user:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    
    # Create session
    token = create_session(user["id"], hours=SESSION_EXPIRE_HOURS)
    
    # Set cookie
    response.set_cookie(
        key="session_token",
        value=token,
        httponly=True,
        max_age=SESSION_EXPIRE_HOURS * 3600,
        samesite="lax"
    )
    
    return {
        "success": True,
        "username": user["username"],
        "is_admin": user["is_admin"]
    }


@router.post("/logout")
async def logout(request: Request, response: Response):
    """Log out and clear session."""
    token = get_session_token(request)
    if token:
        delete_session(token)
    
    response.delete_cookie("session_token")
    return {"success": True}


@router.get("/me")
async def get_me(user: dict = Depends(require_auth)):
    """Get current user info."""
    return {
        "username": user["username"],
        "is_admin": user["is_admin"],
        "is_specialist": user.get("is_specialist", False),
        "specialist_ward_id": user.get("specialist_ward_id")
    }


@router.post("/change-password")
async def change_password(
    request: ChangePasswordRequest,
    user: dict = Depends(require_auth)
):
    """Change current user's password."""
    # Verify current password
    db_user = authenticate_user(user["username"], request.current_password)
    if not db_user:
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    
    # Update password
    from database import get_db
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (hash_password(request.new_password), user["user_id"])
        )
    
    return {"success": True, "message": "Password changed"}


@router.post("/users", dependencies=[Depends(require_admin)])
async def create_new_user(request: CreateUserRequest):
    """Create a new user (admin only)."""
    existing = get_user_by_username(request.username)
    if existing:
        raise HTTPException(status_code=400, detail="Username already exists")
    
    user_id = create_user(
        request.username, request.password,
        is_admin=request.is_admin,
        is_specialist=request.is_specialist,
        specialist_ward_id=request.specialist_ward_id
    )
    if not user_id:
        raise HTTPException(status_code=500, detail="Failed to create user")
    
    return {"success": True, "user_id": user_id}


@router.get("/users", dependencies=[Depends(require_admin)])
async def list_users():
    """List all users (admin only)."""
    from database import get_db
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT u.id, u.username, u.is_admin, u.is_specialist, u.specialist_ward_id,
                   w.name as specialist_ward_name, u.created_at
            FROM users u
            LEFT JOIN wards w ON u.specialist_ward_id = w.id
        """)
        users = [dict(row) for row in cursor.fetchall()]
    return {"users": users}


@router.delete("/users/{user_id}", dependencies=[Depends(require_admin)])
async def delete_user(user_id: int, user: dict = Depends(require_admin)):
    """Delete a user (admin only)."""
    if user_id == user["user_id"]:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")
    
    from database import get_db
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="User not found")
    
    return {"success": True}


class ResetPasswordRequest(BaseModel):
    new_password: str


@router.post("/users/{user_id}/reset-password", dependencies=[Depends(require_admin)])
async def reset_user_password(user_id: int, request: ResetPasswordRequest):
    """Reset a user's password (admin only)."""
    from database import get_db
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM users WHERE id = ?", (user_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="User not found")
        
        cursor.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (hash_password(request.new_password), user_id)
        )
    
    return {"success": True, "message": "Password reset"}
