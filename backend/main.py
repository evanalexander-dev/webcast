"""
Webcast - Main Application
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse, JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
import httpx

from config import HOST, PORT, GO2RTC_API
from database import init_db, seed_initial_data, cleanup_expired_sessions
from routers import auth, youtube, stream, ptz, admin
from routers.auth import get_current_user
from services.scheduler import scheduler_service
from services.stream_manager import write_go2rtc_config

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    logger.info("Starting Webcast...")
    init_db()
    seed_initial_data()
    cleanup_expired_sessions()
    write_go2rtc_config()
    scheduler_service.start()
    logger.info("Application started successfully")
    yield
    logger.info("Shutting down...")
    scheduler_service.stop()
    logger.info("Shutdown complete")


app = FastAPI(
    title="Webcast",
    description="Web-based control panel for church live streaming",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api")
app.include_router(youtube.router, prefix="/api")
app.include_router(stream.router, prefix="/api")
app.include_router(ptz.router, prefix="/api")
app.include_router(admin.router, prefix="/api")

static_path = Path(__file__).parent / "static"
if static_path.exists():
    app.mount("/static", StaticFiles(directory=static_path), name="static")


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    user = await get_current_user(request)
    if not user:
        return RedirectResponse(url="/login")
    index_path = static_path / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return HTMLResponse("<h1>Webcast</h1><p>Frontend not built.</p>")


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    user = await get_current_user(request)
    if user:
        return RedirectResponse(url="/")
    login_path = static_path / "login.html"
    if login_path.exists():
        return FileResponse(login_path)
    # Return inline login page (see full version in previous message)
    return FileResponse(static_path / "login.html") if (static_path / "login.html").exists() else HTMLResponse("Login page")


@app.get("/api/go2rtc/{path:path}")
async def go2rtc_proxy(path: str, request: Request):
    user = await get_current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"error": "Not authenticated"})
    async with httpx.AsyncClient() as client:
        try:
            url = f"{GO2RTC_API}/{path}"
            if request.query_params:
                url += f"?{request.query_params}"
            response = await client.get(url)
            return Response(content=response.content, status_code=response.status_code)
        except httpx.ConnectError:
            return JSONResponse(status_code=503, content={"error": "go2rtc not available"})


@app.get("/health")
async def health_check():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT)
