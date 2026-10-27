"""Web application server for ai-video-builder.

FastAPI backend that wraps the avb pipeline into a web product:
script in -> video out -> conversational edit -> publish to TikTok.

Run:  python -m web.server   (or  uvicorn web.server:app --port 8787)
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# --- avb imports -----------------------------------------------------------
from avb.config import Config, load_config, load_dotenv
from avb.parse import parse_script, parse_script_llm
from avb.pipeline import Pipeline
from avb.edit import apply_plan
from avb.llm_edit import plan_edit_llm
from avb.edit import plan_edit

load_dotenv()

app = FastAPI(title="AI Video Builder", version="0.3.0")


@app.on_event("startup")
async def _capture_loop():
    """Remember the main loop so worker threads can schedule broadcasts."""
    global _main_loop
    _main_loop = asyncio.get_running_loop()

STATIC = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

# ---------------------------------------------------------------------------
# Job manager
# ---------------------------------------------------------------------------
_jobs: dict[str, dict] = {}
_ws_clients: dict[str, list[WebSocket]] = {}
# Live Project IR per job. Kept in memory rather than round-tripped through
# JSON: the IR is nested dataclasses with no from_dict(), and this server is
# a local single-process app, so keeping the object is both simpler and
# exact. If you ever need jobs to survive a restart, add from_dict() to
# avb/ir.py and persist to disk instead.
_projects: dict[str, object] = {}


class JobProgress(BaseModel):
    job_id: str
    stage: str
    progress: float  # 0.0 - 1.0
    message: str
    done: bool = False
    error: str | None = None
    video_url: str | None = None
    cost: float | None = None


# The pipeline runs in a worker thread (via run_in_executor), but WebSocket
# clients live on the main asyncio loop. Cross-thread notification therefore
# has to go through run_coroutine_threadsafe against the loop captured at
# startup -- calling asyncio.get_event_loop() inside the worker raises
# "There is no current event loop in thread", which used to kill every job
# at the very first progress update.
_main_loop: asyncio.AbstractEventLoop | None = None


async def _broadcast(job_id: str, data: dict):
    for ws in list(_ws_clients.get(job_id, [])):
        try:
            await ws.send_json(data)
        except Exception:
            pass


def _update_job(job_id: str, **kw):
    _jobs[job_id].update(kw)
    snapshot = dict(_jobs[job_id])
    if _main_loop is None or _main_loop.is_closed():
        return
    try:
        asyncio.run_coroutine_threadsafe(_broadcast(job_id, snapshot), _main_loop)
    except RuntimeError:
        pass  # loop gone; pollers still see the state via /api/jobs/{id}


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------
class GenerateRequest(BaseModel):
    script: str
    name: str = "untitled"
    style: str = "dr_vsl"
    aspect: str = "9:16"
    fps: int = 30
    draft: bool = False
    use_llm: bool = True
    video_provider: str | None = None  # override
    voice_provider: str | None = None
    out_name: str | None = None


class EditRequest(BaseModel):
    job_id: str | None = None
    project_json: str | None = None  # serialized IR
    instruction: str
    use_llm: bool = True


class ConfigUpdate(BaseModel):
    key: str  # dotted: "llm.model", "video.provider"
    value: str


class TikTokUploadRequest(BaseModel):
    video_path: str
    title: str = ""
    hashtags: list[str] = []
    privacy_level: str = "SELF_ONLY"  # SELF_ONLY | PUBLIC_TO_EVERYONE


# ---------------------------------------------------------------------------
# Routes: frontend
# ---------------------------------------------------------------------------
@app.get("/")
async def index():
    return FileResponse(str(STATIC / "index.html"))


# ---------------------------------------------------------------------------
# Routes: config
# ---------------------------------------------------------------------------
@app.get("/api/config")
async def get_config():
    cfg = load_config()
    return {
        "llm": {
            "provider": cfg.llm.provider,
            "model": cfg.llm.model,
            "base_url": cfg.llm.base_url,
            "api_key_set": bool(cfg.llm.api_key),
            "max_tokens": cfg.llm.max_tokens,
        },
        "voice": {
            "provider": cfg.voice.provider,
            "voice": cfg.voice.voice,
            "speed": cfg.voice.speed,
            "energy": cfg.voice.energy,
        },
        "video": {
            "provider": cfg.video.provider,
            "model": cfg.video.model,
            "max_usd_per_run": cfg.video.max_usd_per_run,
        },
        "render": {
            "width": cfg.render.width,
            "height": cfg.render.height,
            "fps": cfg.render.fps,
            "crf": cfg.render.crf,
        },
    }


@app.post("/api/config")
async def set_config(req: ConfigUpdate):
    """Write a key into avb.toml (persists across restarts)."""
    from avb.config import config_set, find_config_path
    path = find_config_path() or Path("avb.toml")
    try:
        changed, msg = config_set(path, req.key, req.value)
        if not changed:
            raise HTTPException(400, msg)
        return {"ok": True, "message": msg}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, str(e))


# ---------------------------------------------------------------------------
# Routes: providers
# ---------------------------------------------------------------------------
@app.get("/api/providers")
async def list_providers():
    from avb.assets.voice import build_voice_provider
    from avb.assets.video import build_video_provider, _REGISTRY as VREG

    voice = build_voice_provider()
    video = build_video_provider()

    # Probe video providers
    v_status = {}
    for name, cls in VREG.items():
        try:
            inst = cls()
            v_status[name] = {"available": inst.available(), "class": cls.__name__}
        except Exception:
            v_status[name] = {"available": False, "class": cls.__name__}

    return {
        "voice": {"active": voice.name, "available": voice.available()},
        "video": {"active": video.name, "providers": v_status},
    }


# ---------------------------------------------------------------------------
# Routes: generate
# ---------------------------------------------------------------------------
@app.post("/api/generate")
async def generate(req: GenerateRequest):
    job_id = uuid.uuid4().hex[:12]
    _jobs[job_id] = {
        "job_id": job_id,
        "stage": "queued",
        "progress": 0.0,
        "message": "Job queued",
        "done": False,
        "error": None,
        "video_url": None,
        "cost": None,
    }

    # Run in background thread (pipeline is sync)
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, _run_pipeline, job_id, req.model_dump())
    return {"job_id": job_id}


def _run_pipeline(job_id: str, params: dict):
    """Execute the full pipeline in a worker thread."""
    try:
        _update_job(job_id, stage="parsing", progress=0.05, message="Parsing script...")

        cfg = load_config()
        # Override providers if requested
        if params.get("video_provider"):
            cfg.video.provider = params["video_provider"]
        if params.get("voice_provider"):
            cfg.voice.provider = params["voice_provider"]

        script = params["script"]
        name = params.get("name", "untitled")

        # Parse
        if params.get("use_llm", True):
            project = parse_script_llm(script, name=name, style=params.get("style", "dr_vsl"))
        else:
            project = parse_script(script, name=name, style=params.get("style", "dr_vsl"))

        _update_job(job_id, stage="synth", progress=0.15,
                     message=f"Parsed {len(project.scenes)} scenes, synthesizing voiceover...")

        # Build pipeline
        out_dir = Path("demo/out/web")
        out_dir.mkdir(parents=True, exist_ok=True)
        out_name = params.get("out_name") or f"{name}_{job_id[:6]}.mp4"
        out_path = out_dir / out_name

        pipe = Pipeline(workdir=".avb_web", draft=params.get("draft", False), config=cfg)

        # Keep the IR alive so /api/edit can patch it without re-parsing.
        _projects[job_id] = project

        _update_job(job_id, stage="rendering", progress=0.3,
                     message="Rendering scenes...")

        report = pipe.run(project, out_path)

        # RunReport.scenes is a list of timings and rendered/reused are lists
        # of scene ids -- the frontend wants counts, so normalise here rather
        # than shipping whole scene arrays over the wire.
        def _count(v):
            return len(v) if isinstance(v, (list, tuple)) else (v or 0)

        _update_job(job_id, stage="done", progress=1.0, message="Complete",
                     done=True,
                     video_url=f"/api/video/{out_path.name}",
                     cost=getattr(report, "cost_usd", 0.0),
                     total_duration=getattr(report, "total_duration", 0.0),
                     scenes=_count(getattr(report, "scenes", 0)),
                     rendered=_count(getattr(report, "rendered", 0)),
                     reused=_count(getattr(report, "reused", 0)))

    except Exception as e:
        _update_job(job_id, stage="error", progress=0, done=True, error=str(e))


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    if job_id not in _jobs:
        raise HTTPException(404, "Job not found")
    return _jobs[job_id]


@app.websocket("/ws/jobs/{job_id}")
async def job_websocket(ws: WebSocket, job_id: str):
    await ws.accept()
    _ws_clients.setdefault(job_id, []).append(ws)
    try:
        # Send current state immediately
        if job_id in _jobs:
            await ws.send_json(_jobs[job_id])
        while True:
            await ws.receive_text()  # keep alive
    except WebSocketDisconnect:
        pass
    finally:
        if ws in _ws_clients.get(job_id, []):
            _ws_clients[job_id].remove(ws)


@app.get("/api/video/{filename}")
async def serve_video(filename: str):
    path = Path("demo/out/web") / filename
    if not path.exists():
        raise HTTPException(404, "Video not found")
    return FileResponse(str(path), media_type="video/mp4")


# ---------------------------------------------------------------------------
# Routes: conversational edit
# ---------------------------------------------------------------------------
@app.post("/api/edit")
async def edit(req: EditRequest):
    """Plan and optionally apply a conversational edit."""
    try:
        # The IR is held in memory per job (see _projects). Falling back to
        # a supplied project_json is not supported yet because avb.ir has
        # to_dict() but no from_dict().
        if req.job_id and req.job_id in _projects:
            project = _projects[req.job_id]
        else:
            raise HTTPException(400, "No project for this job (generate a video first)")

        # Build timings (simplified)
        from avb.pipeline import SceneTiming
        t = 0.0
        timings = []
        for s in project.scenes:
            dur = 3.0 if s.beat in ("hook", "cta") else 2.6
            timings.append(SceneTiming(s.id, s.index, s.beat, t, dur, t, s.vo.text))
            t += dur

        # Plan edit
        if req.use_llm:
            plan = plan_edit_llm(project, req.instruction, timings)
        else:
            plan = plan_edit(project, req.instruction, timings)

        return {
            "intent": plan.intent,
            "scene_ids": plan.scene_ids,
            "ops": [{"path": o.path, "value": o.value} for o in plan.ops],
            "explanation": plan.explanation,
        }
    except Exception as e:
        raise HTTPException(500, str(e))


# ---------------------------------------------------------------------------
# Routes: TikTok
# ---------------------------------------------------------------------------
@app.get("/api/tiktok/auth-url")
async def tiktok_auth_url():
    """Return the TikTok OAuth2 authorization URL."""
    from avb.tiktok import get_auth_url
    cfg = load_config()
    client_key = os.environ.get("TIKTOK_CLIENT_KEY", "")
    redirect_uri = os.environ.get("TIKTOK_REDIRECT_URI", "http://localhost:8787/api/tiktok/callback")
    if not client_key:
        raise HTTPException(400, "TIKTOK_CLIENT_KEY not set")
    return {"auth_url": get_auth_url(client_key, redirect_uri)}


@app.get("/api/tiktok/callback")
async def tiktok_callback(code: str, state: str = ""):
    """OAuth2 callback - exchange code for tokens."""
    from avb.tiktok import exchange_code
    cfg = load_config()
    client_key = os.environ.get("TIKTOK_CLIENT_KEY", "")
    client_secret = os.environ.get("TIKTOK_CLIENT_SECRET", "")
    redirect_uri = os.environ.get("TIKTOK_REDIRECT_URI", "http://localhost:8787/api/tiktok/callback")
    try:
        tokens = exchange_code(code, client_key, client_secret, redirect_uri)
        # Save tokens (in production, use proper storage)
        token_path = Path(".tiktok_tokens.json")
        token_path.write_text(json.dumps(tokens), encoding="utf-8")
        return HTMLResponse("<h2>TikTok authorized! You can close this tab.</h2>")
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post("/api/tiktok/upload")
async def tiktok_upload(req: TikTokUploadRequest):
    """Upload a video to TikTok via Content Posting API."""
    from avb.tiktok import upload_video

    video_path = Path(req.video_path)
    if not video_path.exists():
        raise HTTPException(404, f"Video not found: {req.video_path}")

    token_path = Path(".tiktok_tokens.json")
    if not token_path.exists():
        raise HTTPException(401, "Not authorized. Visit /api/tiktok/auth-url first.")

    tokens = json.loads(token_path.read_text(encoding="utf-8"))
    try:
        result = upload_video(
            video_path=video_path,
            access_token=tokens.get("access_token", ""),
            title=req.title,
            hashtags=req.hashtags,
            privacy_level=req.privacy_level,
        )
        return {"ok": True, "result": result}
    except Exception as e:
        raise HTTPException(500, str(e))


# ---------------------------------------------------------------------------
# Routes: quick TTS preview
# ---------------------------------------------------------------------------
@app.post("/api/tts-preview")
async def tts_preview(text: str, voice: str = ""):
    """Quick TTS preview - returns audio file."""
    import tempfile
    from avb.assets.voice import build_voice_provider
    from avb.assets.base import Cache

    cache = Cache(Path(".avb_web/cache"))
    provider = build_voice_provider()
    voice = voice or "en-US-AriaNeural"

    result = provider.synth(text, voice=voice, speed=1.0, energy=1.0, cache=cache)
    return FileResponse(str(result.path), media_type="audio/mpeg")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8787)
