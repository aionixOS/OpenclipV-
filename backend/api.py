"""
api.py — FastAPI application for OpenClip.

� Seriously, are you going to read this full !

This is the main entry-point for the backend.  It wires together the
database, downloader, clipper, transcriber, LLM, and settings modules
behind a REST + WebSocket API that the Next.js frontend consumes.

Pipeline stages (v2.0 — AI-powered):
    1. **Download**     — yt-dlp fetches the YouTube video.
    2. **Transcribe**   — YouTube captions or local Whisper.
    3. **Analyze**      — LLM suggests 5-8 best clip timestamps.
    4. **Cut clips**    — FFmpeg stream-copy at LLM timestamps.
    5. **Save**         — clip records persisted to SQLite.

Architecture notes:
    • ``_ws_connections`` is a dict mapping ``project_id`` to a *set* of
      connected ``WebSocket`` objects.  The background task iterates
      over this set to broadcast progress.
    • All DB calls are ``await``-ed because ``database.py`` uses
      ``aiosqlite``.
    • The download, FFmpeg, and Whisper work is CPU/IO-bound, so it is
      run inside ``asyncio.to_thread`` to avoid blocking the event loop.
"""

import os
import re
import asyncio
import shutil
import logging
from typing import Optional

from fastapi import (  # type: ignore
    FastAPI,
    WebSocket,
    WebSocketDisconnect,
    HTTPException,
    BackgroundTasks,
    Header,
    UploadFile,
    File,
    Form,
)
from fastapi.staticfiles import StaticFiles  # type: ignore
from fastapi.middleware.cors import CORSMiddleware  # type: ignore
from pydantic import BaseModel  # type: ignore
from dotenv import load_dotenv  # type: ignore

load_dotenv()

import os
# Ensure ffmpeg is available to all subprocesses (Linux/Replit paths)
_extra_paths = os.path.join(os.path.dirname(__file__), "bin")
os.environ["PATH"] = _extra_paths + ":" + os.environ.get("PATH", "")

# Internal modules
import database  # type: ignore
import downloader  # type: ignore
import clipper  # type: ignore
import transcriber  # type: ignore
import llm  # type: ignore
import reframer # type: ignore
import captioner # type: ignore
import settings as settings_mod  # type: ignore

import logging

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler()
    ]
)
# Silence aiosqlite debug logs
logging.getLogger("aiosqlite").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(
    title="OpenClip API",
    description="Local-first video clipping engine by AIONIX",
    version="0.1.0",
)

# Allow any frontend origin since the frontend will be hosted externally
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Base directory for all temporary video / clip files
TMP_DIR = os.getenv("TMP_DIR", os.path.join(os.path.dirname(__file__), "..", "tmp"))
os.makedirs(TMP_DIR, exist_ok=True)

app.mount("/files", StaticFiles(directory=TMP_DIR), name="files")


# ---------------------------------------------------------------------------
# WebSocket connection registry
# ---------------------------------------------------------------------------

_ws_connections: dict[str, set[WebSocket]] = {}
_progress_state: dict[str, dict] = {}
pipeline_lock = asyncio.Lock()


async def _broadcast(project_id: str, stage: str, percent: float, message: str) -> None:
    """
    Send a progress update to every WebSocket client listening for
    the given project. Also update global state for HTTP polling clients.

    Args:
        project_id: UUID of the project.
        stage:      Pipeline stage name (download / clipping / done / error).
        percent:    Progress 0–100.
        message:    Human-readable status text.
    """
    payload = {"stage": stage, "percent": percent, "message": message}
    
    # Store globally for HTTP polling (Vercel bypass)
    _progress_state[project_id] = payload
    
    dead: list[WebSocket] = []
    for ws in _ws_connections.get(project_id, set()):
        try:
            await ws.send_json(payload)
        except Exception:
            dead.append(ws)
    # Clean up dead connections
    for ws in dead:
        _ws_connections.get(project_id, set()).discard(ws)


async def _cleanup_worker():
    """Background task to periodically clean up expired projects."""
    while True:
        try:
            # Wake up every 5 minutes
            await asyncio.sleep(300)
            
            expired_ids = await database.get_expired_projects(hours=2)
            for pid in expired_ids:
                logger.info(f"Auto-deleting expired project {pid}")
                
                # 1. Delete from database
                await database.delete_project(pid)
                
                # 2. Delete all files from disk
                project_dir = os.path.join(TMP_DIR, pid)
                if os.path.isdir(project_dir):
                    shutil.rmtree(project_dir, ignore_errors=True)
                    
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in cleanup worker: {e}")


# ---------------------------------------------------------------------------
# Startup event — initialise the database
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def on_startup() -> None:
    """Ensure the SQLite tables exist when the server starts."""
    await database.init_db()
    os.makedirs(TMP_DIR, exist_ok=True)
    logger.info("Database initialised, tmp dir ready at %s", TMP_DIR)
    
    # Start the background cleanup worker
    asyncio.create_task(_cleanup_worker())


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class CreateProjectRequest(BaseModel):
    """Payload for creating a new project."""
    youtube_url: str


class CreateProjectResponse(BaseModel):
    """Returned immediately after project creation."""
    project_id: str
    status: str


class ProjectListItem(BaseModel):
    """One item in the projects list."""
    id: str
    title: Optional[str] = None
    youtube_url: str
    status: str
    created_at: str
    clip_count: int


class ClipResponse(BaseModel):
    """Serialised clip record."""
    id: str
    project_id: str
    file_path: str
    start_time: float
    end_time: float
    duration: float
    reframed: bool
    captioned: bool
    title: Optional[str] = None
    reason: Optional[str] = None
    layout_mode: Optional[str] = None
    needs_user_confirm: bool = False
    hashtags: list[str] = []
    tags: list[str] = []
    created_at: str


class ProjectDetailResponse(BaseModel):
    """Full project detail including nested clips."""
    id: str
    title: Optional[str] = None
    youtube_url: str
    status: str
    created_at: str
    clips: list[ClipResponse] = []


# ---------------------------------------------------------------------------
# Background processing pipeline
# ---------------------------------------------------------------------------

async def _run_pipeline(project_id: str, youtube_url: str, override_api_key: Optional[str] = None, user_id: Optional[str] = None) -> None:
    """
    Execute the full AI-powered pipeline in the background.

    Steps:
        1. Download the video using yt-dlp.
        2. Extract transcript (YouTube captions → Whisper fallback).
        3. Send transcript to LLM for clip suggestions.
        4. Cut clips at LLM-suggested timestamps.
        5. Save clip records to the database.
    """
    project_dir = os.path.join(TMP_DIR, project_id)
    clips_dir = os.path.join(project_dir, "clips")
    os.makedirs(clips_dir, exist_ok=True)

    if pipeline_lock.locked():
        await _broadcast(project_id, "queued", 0, "Waiting in queue for previous task to finish...")
        
    await pipeline_lock.acquire()
    try:
        project = await database.get_project(project_id, user_id)
        if project is None:
            return

        # Capture the running event loop for threadsafe callbacks
        loop = asyncio.get_running_loop()
        
        # ---- STEP 1: Download or setup local file ----
        if project.get("source_type") == "upload":
            file_path = project.get("local_file_path")
            title = project.get("title") or "Uploaded Video"
            
            # Use ffmpeg to get duration
            await _broadcast(project_id, "downloading", 25, "Processing uploaded file…")
            try:
                import subprocess
                cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", file_path]
                result_proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                video_duration = float(result_proc.stdout.strip())
            except Exception as e:
                logger.warning(f"Failed to get duration for uploaded file: {e}")
                video_duration = 0.0
                
            await database.update_project_status(project_id, "downloading")
        else:
            await database.update_project_status(project_id, "downloading")
            await _broadcast(project_id, "downloading", 5, "Starting download…")

            def download_progress(percent: float, msg: str):
                asyncio.run_coroutine_threadsafe(
                    _broadcast(project_id, "downloading", min(percent * 0.2 + 5, 25), msg),
                    loop,
                )

            result = await asyncio.to_thread(
                downloader.download_video,
                youtube_url,
                project_dir,
                download_progress,
            )

            file_path = result["file_path"]
            title = result["title"]
            video_duration = result["duration_seconds"]

        await database.update_project_title(project_id, title)
        await _broadcast(project_id, "downloading", 25, "Download complete")

        # ---- STEP 2: Extract Transcript ----
        await database.update_project_status(project_id, "transcribing")
        await _broadcast(project_id, "transcribing", 30, "Extracting transcript…")

        def transcript_progress(percent: float, msg: str):
            scaled = 30 + (percent / 100) * 20  # 30-50 range
            asyncio.run_coroutine_threadsafe(
                _broadcast(project_id, "transcribing", scaled, msg),
                loop,
            )

        transcript_segments = await transcriber.extract_captions(
            youtube_url,
            file_path,
            progress_callback=transcript_progress,
        )
        
        if not transcript_segments:
            await database.update_project_status(project_id, "error")
            await _broadcast(project_id, "error", 0, "Could not extract captions from this video.")
            return

        await _broadcast(project_id, "transcribing", 50, "Transcript ready")

        # ---- STEP 3: LLM Clip Suggestions ----
        await database.update_project_status(project_id, "analyzing")
        await _broadcast(project_id, "analyzing", 55, "AI analyzing video…")

        llm_api_key = override_api_key or await settings_mod.get_setting("llm_api_key", user_id=user_id)
        llm_provider = await settings_mod.get_setting("llm_provider", user_id=user_id) or "openai"
        llm_model = await settings_mod.get_setting("llm_model", user_id=user_id) or ("gpt-4o-mini" if llm_provider == "openai" else "gemini-2.0-flash")

        try:
            import logging
            
            class WsLogHandler(logging.Handler):
                def emit(self, record):
                    log_msg = self.format(record)
                    asyncio.run_coroutine_threadsafe(
                        _broadcast(project_id, "analyzing", 55, log_msg),
                        loop
                    )
                    
            ws_handler = WsLogHandler()
            ws_handler.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
            llm.logger.addHandler(ws_handler)

            def analyze_progress(stage: str, percent: float, msg: str):
                asyncio.run_coroutine_threadsafe(
                    _broadcast(project_id, stage, percent, msg),
                    loop,
                )

            suggestions: list[dict] = await llm.get_clip_suggestions(
                transcript_segments, llm_provider, llm_api_key, llm_model, video_duration,
                progress_callback=analyze_progress
            )

            llm.logger.removeHandler(ws_handler)

        except Exception as e:
            if 'ws_handler' in locals():
                llm.logger.removeHandler(ws_handler)
            msg = str(e).lower()
            if "nonretryableerror" in msg or "api error" in msg or "400" in msg or "401" in msg or "403" in msg or "404" in msg:
                err_msg = f"Configuration Error (User Fault): {e}"
            elif "429" in msg or "rate limit" in msg or "quota" in msg:
                err_msg = f"Quota/Rate Limit Error (User Fault): You have hit the rate limit for your API key. Details: {e}"
            else:
                err_msg = f"AI Service Error: {e}"
            
            # Extract the actual message if it's wrapped in NonRetryableError
            if "NonRetryableError: " in err_msg:
                err_msg = err_msg.replace("NonRetryableError: ", "")
            await database.update_project_status(project_id, "error")
            await _broadcast(project_id, "error", 0, err_msg)
            return

        if not suggestions:
            await database.update_project_status(project_id, "error")
            await _broadcast(project_id, "error", 0, "AI could not find clip moments. Try a different video or check your API key.")
            return

        await _broadcast(
            project_id, "analyzing", 75,
            f"Found {len(suggestions)} clip suggestions",
        )

        # ---- STEP 4: Cut Clips & Reframe ----
        await database.update_project_status(project_id, "processing")

        for i, suggestion in enumerate(suggestions):
            progress_pct = 75 + (i / len(suggestions) * 20)
            await _broadcast(project_id, "processing", progress_pct, 
                             f"Processing clip {i+1} of {len(suggestions)}...")
            
            clip = await reframer.process_clip(
                file_path, suggestion, clips_dir, project_id
            )
            
            # STEP 5 — BURN CAPTIONS
            caption_style = await settings_mod.get_setting("caption_style")
            if caption_style and caption_style != "none":
                progress_pct = 90 + (i / len(suggestions) * 8)
                await _broadcast(project_id, "processing", progress_pct, 
                                 f"Adding captions to clip {i+1}...")
                captioned_clip = await captioner.burn_captions(
                    clip['file_path'],
                    transcript_segments,           # full transcript from Step 2
                    suggestion['start'],  # clip start time in original video
                    suggestion['end'],    # clip end time
                    caption_style
                )
                # Replace clip file with captioned version
                os.replace(captioned_clip, clip['file_path'])
                clip["caption_style"] = caption_style

            filename = os.path.basename(clip["file_path"])
            file_url = f"/files/{project_id}/clips/{filename}"

            await database.save_clip(
                project_id=project_id,
                file_path=file_url,
                start_time=clip["start_time"],
                end_time=clip["end_time"],
                title=clip.get("title"),
                reason=clip.get("reason"),
                viral_score=clip.get("viral_score"),
                face_count=clip.get("face_count"),
                layout_mode=clip.get("layout_mode"),
                caption_style=clip.get("caption_style"),
                needs_user_confirm=clip.get("needs_user_confirm", False),
                reframed=clip.get("reframed", True),
                hashtags=suggestion.get("hashtags", []),
                tags=suggestion.get("tags", []),
            )

        # Aggressive cleanup: remove the large original downloaded video after successful processing
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except OSError:
                pass

        await database.update_project_status(project_id, "done")
        await _broadcast(project_id, "done", 100, "Complete")

    except Exception as exc:
        import traceback
        with open("error_trace.txt", "w") as f:
            f.write(traceback.format_exc())
            
        logger.exception("Pipeline failed for project %s", project_id)
        
        # Aggressive cleanup: remove the large original downloaded video after failure
        try:
            if 'file_path' in locals() and file_path and os.path.exists(file_path):
                os.remove(file_path)
        except OSError:
            pass

        await database.update_project_status(project_id, "error")
        await _broadcast(
            project_id, "error", 0, f"Pipeline error: {exc}"
        )
    finally:
        pipeline_lock.release()


# ---------------------------------------------------------------------------
# REST routes — Projects
# ---------------------------------------------------------------------------

@app.post("/api/projects", response_model=CreateProjectResponse, status_code=201)
async def create_project(
    body: CreateProjectRequest,
    background_tasks: BackgroundTasks,
    x_openai_key: Optional[str] = Header(None),
    x_user_id: str = Header(...)
):
    """
    Create a new project from a YouTube URL.

    The project row is inserted immediately and the pipeline is kicked
    off in the background.  The response is returned without waiting
    for the download/clip work to finish.
    """
    if not re.match(r"^(https?\:\/\/)?(www\.youtube\.com|youtu\.be)\/.+$", body.youtube_url):
        raise HTTPException(status_code=400, detail="Invalid YouTube URL")

    result = await database.create_project(youtube_url=body.youtube_url, user_id=x_user_id)
    project_id = result["project_id"]

    # Fire-and-forget — runs after the response is sent
    background_tasks.add_task(_run_pipeline, project_id, body.youtube_url, x_openai_key, x_user_id)

    return {"project_id": project_id, "status": "pending"}

@app.post("/api/projects/upload", response_model=CreateProjectResponse, status_code=201)
async def upload_project(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    x_openai_key: Optional[str] = Header(None),
    x_user_id: str = Header(...)
):
    """
    Create a new project by uploading a local video file.
    """
    import uuid
    import shutil
    
    # Insert into DB first to get the official project_id
    result = await database.create_project(
        youtube_url="upload", 
        user_id=x_user_id, 
        source_type="upload", 
        local_file_path="pending"  # Will update after saving
    )
    actual_project_id = result["project_id"]
    actual_project_dir = os.path.join(TMP_DIR, actual_project_id)
    os.makedirs(actual_project_dir, exist_ok=True)
    
    # Save the uploaded file
    file_extension = os.path.splitext(file.filename)[1] if file.filename else ".mp4"
    final_local_file_path = os.path.join(actual_project_dir, f"source{file_extension}")
    
    with open(final_local_file_path, 'wb') as out_file:
        shutil.copyfileobj(file.file, out_file)
    
    # Update DB with the correct path
    conn = await database._get_connection()
    await conn.execute("UPDATE projects SET local_file_path = ?, title = ? WHERE id = ?", (final_local_file_path, file.filename or "Uploaded Video", actual_project_id))
    await conn.commit()
    await conn.close()
    
    # Fire-and-forget
    background_tasks.add_task(_run_pipeline, actual_project_id, "upload", x_openai_key, x_user_id)

    return {"project_id": actual_project_id, "status": "pending"}


@app.get("/api/projects", response_model=list[ProjectListItem])
async def list_projects(x_user_id: str = Header(...)):
    """Return all projects, newest first, with clip counts."""
    return await database.get_all_projects(user_id=x_user_id)


@app.get("/api/projects/{project_id}", response_model=ProjectDetailResponse)
async def get_project(project_id: str, x_user_id: str = Header(...)):
    """Get a single project by ID, including its clips."""
    project = await database.get_project(project_id, user_id=x_user_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@app.delete("/api/projects/{project_id}")
async def delete_project(project_id: str, x_user_id: str = Header(...)):
    """
    Delete a project and all of its clips (both DB rows and files).
    """
    project = await database.get_project(project_id, user_id=x_user_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    await database.delete_project(project_id)

    # Clean up files on disk
    project_dir = os.path.join(TMP_DIR, project_id)
    if os.path.isdir(project_dir):
        shutil.rmtree(project_dir, ignore_errors=True)

    return {"success": True}


@app.get("/api/caption-styles")
async def list_caption_styles():
    def ass_to_hex(ass_color):
        if not ass_color: return None
        if len(ass_color) >= 10:
            return f"#{ass_color[8:10]}{ass_color[6:8]}{ass_color[4:6]}"
        return None

    styles = []
    for key, style in captioner.CAPTION_STYLES.items():
        styles.append({
            "key": key,
            "name": style["name"],
            "animation": style["animation"],
            "preview_colors": {
                "text": ass_to_hex(style.get("primary_color")),
                "highlight": ass_to_hex(style.get("highlight_color")),
                "background": ass_to_hex(style.get("bg_color")) if style.get("background") else None
            }
        })
    return styles

# ---------------------------------------------------------------------------
# REST routes — Settings
# ---------------------------------------------------------------------------

class UpdateSettingsRequest(BaseModel):
    """Payload for updating settings."""
    llm_provider: Optional[str] = None
    llm_api_key: Optional[str] = None
    llm_model: Optional[str] = None
    whisper_model: Optional[str] = None
    caption_style: Optional[str] = None


@app.get("/api/settings")
async def get_settings(x_user_id: str = Header(...)):
    """
    Return all settings.  API keys are **never** returned — only a
    boolean ``has_api_key`` flag.
    """
    return await settings_mod.get_all_settings(user_id=x_user_id)


@app.post("/api/settings")
async def update_settings(body: UpdateSettingsRequest, x_user_id: str = Header(...)):
    """
    Create or update one or more settings.
    """
    pairs = {
        "llm_provider": body.llm_provider,
        "llm_api_key": body.llm_api_key,
        "llm_model": body.llm_model,
        "whisper_model": body.whisper_model,
        "caption_style": body.caption_style,
    }
    for key, value in pairs.items():
        if value is not None:
            await settings_mod.set_setting(key, value, user_id=x_user_id)
    return {"success": True}


# ---------------------------------------------------------------------------
# WebSocket — real-time progress
# ---------------------------------------------------------------------------

@app.get("/api/progress/{project_id}")
async def get_progress(project_id: str):
    """HTTP Polling endpoint for progress updates (bypasses Vercel WS limits)."""
    return _progress_state.get(project_id, {"stage": "Initializing...", "percent": 0, "message": ""})


@app.websocket("/ws/progress/{project_id}")
async def websocket_progress(websocket: WebSocket, project_id: str):
    """
    WebSocket endpoint that streams processing-progress updates to
    the frontend for a given project.

    Message format (JSON sent from server):
        {
          "stage":   "download" | "clipping" | "done" | "error",
          "percent": 0.0 – 100.0,
          "message": "human-readable status"
        }

    The connection stays open until the client disconnects.
    """
    await websocket.accept()

    # Register this socket
    if project_id not in _ws_connections:
        _ws_connections[project_id] = set()
    _ws_connections[project_id].add(websocket)

    # Immediately send the last known state so UI doesn't hang at 0%
    last_state = _progress_state.get(project_id)
    if last_state:
        try:
            await websocket.send_json(last_state)
        except Exception:
            pass

    try:
        # Stay alive — wait for the client to close or send pings
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _ws_connections.get(project_id, set()).discard(websocket)
        if project_id in _ws_connections and not _ws_connections[project_id]:
            _ws_connections.pop(project_id, None)


# ---------------------------------------------------------------------------
# Entrypoint (for development)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn  # type: ignore
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
