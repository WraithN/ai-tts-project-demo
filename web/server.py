"""Web application server for ai-video-builder.

FastAPI backend that wraps the avb pipeline into a web product:
script in -> video out -> conversational edit -> publish to TikTok.

Run:  python -m web.server   (or  uvicorn web.server:app --port 8787)
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# --- avb imports -----------------------------------------------------------
from avb.config import (
    Config, load_config, load_dotenv, config_set, env_path, find_config_path,
    write_env, save_config,
)
from avb.parse import parse_script, parse_script_llm
from avb.pipeline import Pipeline
from avb.edit import apply_plan
from avb.llm_edit import plan_edit_llm
from avb.edit import plan_edit
from avb.provider_specs import (
    VIDEO_SPECS, VOICE_SPECS, LLM_SPECS, PUBLISH_SPECS,
    spec_for, env_status, mask,
)

load_dotenv()

app = FastAPI(title="AI Video Builder", version="0.3.0")


@app.on_event("startup")
async def _capture_loop():
    """Remember the main loop so worker threads can schedule broadcasts."""
    global _main_loop
    _main_loop = asyncio.get_running_loop()

STATIC = Path(__file__).parent / "static"
# React build output (web/frontend -> web/static/dist). If it is missing we
# fall back to the hand-written legacy page, so the server always boots.
DIST = STATIC / "dist"
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
if DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=str(DIST / "assets")), name="assets")

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
    # False -> only report the plan (preview what would change).
    # True  -> apply it, invalidate, and re-render just the stale scenes.
    apply: bool = False
    draft: bool = False
    out_name: str | None = None


class ConfigUpdate(BaseModel):
    key: str  # dotted: "llm.model", "video.provider"
    value: str


class TikTokUploadRequest(BaseModel):
    video_path: str
    title: str = ""
    hashtags: list[str] = []
    privacy_level: str = "SELF_ONLY"  # SELF_ONLY | PUBLIC_TO_EVERYONE


class SettingsUpdate(BaseModel):
    """Provider selection + credentials from the settings page.

    `keys` is env-var -> value (secrets). Empty values are ignored, so the
    form can be submitted without retyping (and without wiping) a key that
    is already set.
    """
    video_provider: str | None = None
    voice_provider: str | None = None
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_base_url: str | None = None
    video_model: str | None = None
    keys: dict[str, str] = {}


# ---------------------------------------------------------------------------
# Routes: frontend
# ---------------------------------------------------------------------------
@app.api_route("/", methods=["GET", "HEAD"])
async def index():
    # Starlette does not answer HEAD for a GET-only route, and the IDE's
    # preview panel probes with HEAD -- without this it concluded nothing was
    # listening on a perfectly healthy server.
    if DIST.is_dir():
        return FileResponse(str(DIST / "index.html"))
    return FileResponse(str(STATIC / "index.html"))


@app.get("/legacy")
async def legacy():
    """The pre-React single-file UI, kept as a fallback."""
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
# Routes: provider settings (the page that was missing)
# ---------------------------------------------------------------------------
# Non-secret choices go to avb.toml; secrets go to .env. Both are written
# line-by-line so existing comments survive, and os.environ is updated in
# process so a save takes effect without restarting the server.
_LLM_PRESETS = {
    "deepseek": {"base_url": "https://api.deepseek.com/v1", "model": "deepseek-flash",
                 "api_key_env": "DEEPSEEK_API_KEY"},
    "openai": {"base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini",
               "api_key_env": "OPENAI_API_KEY"},
}


def _spec_payload(specs, selected: str | None = None):
    out = []
    for s in specs:
        out.append({
            "key": s.key,
            "label": s.label,
            "kind": s.kind,
            "ready": s.ready(),
            "missing": s.missing(),
            "console": s.console,
            "notes": s.notes,
            "models": list(s.models),
            "model_env": s.model_env,
            "model_value": os.environ.get(s.model_env, "") if s.model_env else "",
            "envs": env_status(s),
            "selected": selected == s.key,
        })
    return out


@app.get("/api/settings")
async def get_settings():
    """Everything the settings page needs: what's available, what's missing."""
    cfg = load_config()
    from avb.assets.voice import build_voice_provider
    from avb.assets.video import build_video_provider

    try:
        video = build_video_provider(cfg.video.provider)
        video_active, video_ready = video.name, True
    except Exception as e:  # ProviderUnavailable
        video_active, video_ready = None, False
        video_error = str(e)
    else:
        video_error = ""

    voice = build_voice_provider(cfg.voice.provider)

    # `auto` means "whatever is configured"; resolve it for display so the
    # dropdown does not show a mode the user cannot reason about.
    def _resolve(kind, want):
        if want and want != "auto":
            return want
        for s in (VIDEO_SPECS if kind == "video" else VOICE_SPECS):
            if s.ready():
                return s.key
        return "auto"

    return {
        "video": {
            "selected": cfg.video.provider,
            "resolved": _resolve("video", cfg.video.provider),
            "active": video_active,
            "ready": video_ready,
            "error": video_error,
            "specs": _spec_payload(VIDEO_SPECS, _resolve("video", cfg.video.provider)),
        },
        "voice": {
            "selected": cfg.voice.provider,
            "resolved": voice.name.replace("-tts", "") if voice else "auto",
            "active": voice.name,
            "ready": voice.available(),
            "specs": _spec_payload(VOICE_SPECS, cfg.voice.provider),
        },
        "llm": {
            "selected": cfg.llm.provider,
            "model": cfg.llm.model,
            "base_url": cfg.llm.base_url,
            "api_key_env": cfg.llm.api_key_env,
            "api_key_set": bool(cfg.llm.api_key),
            "api_key_masked": mask(cfg.llm.api_key),
            "specs": _spec_payload(LLM_SPECS, cfg.llm.provider),
        },
        "publish": {"specs": _spec_payload(PUBLISH_SPECS, "tiktok")},
        "env_file": str(env_path()),
        "toml_file": str(find_config_path() or Path("avb.toml")),
    }


@app.post("/api/settings")
async def update_settings(req: SettingsUpdate):
    """Persist provider choice + credentials, then re-probe.

    Returns the same shape as GET so the UI can just re-render.
    """
    cfg_path = find_config_path() or Path("avb.toml")
    if not cfg_path.is_file():
        save_config(load_config(), cfg_path)
    written_toml: list[str] = []
    written_env: list[str] = []

    def _toml(dotted: str, value: str):
        try:
            changed, _ = config_set(cfg_path, dotted, value)
            if changed:
                written_toml.append(dotted)
        except Exception:
            pass

    if req.video_provider:
        _toml("video.provider", req.video_provider)
    if req.voice_provider:
        _toml("voice.provider", req.voice_provider)
    if req.llm_provider:
        _toml("llm.provider", req.llm_provider)
        preset = _LLM_PRESETS.get(req.llm_provider.strip().lower())
        if preset and not req.llm_base_url:
            _toml("llm.base_url", preset["base_url"])
        if preset and not req.llm_model:
            _toml("llm.model", preset["model"])
        if preset:
            _toml("llm.api_key_env", preset["api_key_env"])
    if req.llm_model:
        _toml("llm.model", req.llm_model)
    if req.llm_base_url:
        _toml("llm.base_url", req.llm_base_url)

    # Secrets -> .env, and into os.environ so they apply immediately.
    secrets: dict[str, str] = {}
    for k, v in (req.keys or {}).items():
        v = (v or "").strip()
        if not v or not re.fullmatch(r"[A-Z][A-Z0-9_]*", k or ""):
            continue  # blank = leave as-is; non-env-shaped = ignore
        secrets[k] = v

    if req.video_model:
        # per-provider model override, e.g. SEEDANCE_MODEL
        sel = (req.video_provider or load_config().video.provider or "").lower()
        spec = spec_for("video", sel)
        if spec and spec.model_env:
            secrets[spec.model_env] = req.video_model.strip()

    if secrets:
        write_env(secrets)
        os.environ.update(secrets)
        written_env = sorted(secrets)

    # Provider objects cache their key at construction time, so drop any
    # memoised client that would otherwise keep the old credential.
    try:
        from avb import llm as _llm
        _llm.get_client(load_config())
    except Exception:
        pass

    fresh = await get_settings()
    fresh["written"] = {"toml": written_toml, "env": written_env}
    return fresh


# ---------------------------------------------------------------------------
# Routes: providers
# ---------------------------------------------------------------------------
@app.get("/api/providers")
async def list_providers():
    from avb.assets.voice import build_voice_provider
    from avb.assets.video import build_video_provider, _REGISTRY as VREG

    voice = build_voice_provider()

    v_status = {}
    for name, cls in VREG.items():
        try:
            inst = cls()
            v_status[name] = {"available": inst.available(), "class": cls.__name__}
        except Exception:
            v_status[name] = {"available": False, "class": cls.__name__}

    try:
        video = build_video_provider()
        active, err = video.name, None
    except Exception as e:
        active, err = None, str(e)

    return {
        "voice": {"active": voice.name, "available": voice.available()},
        "video": {"active": active, "error": err, "providers": v_status},
    }


# ---------------------------------------------------------------------------
# Routes: plan -> confirm -> render
#
# Generation is deliberately split in two. `/api/plan` turns a script into
# scenes (copy + visual prompts) and stops there; `/api/render` is what
# actually spends money on TTS and video. Putting a confirmation step between
# them is the whole point: the model's first guess at scene copy is a draft,
# and regenerating eight clips because a line read wrong costs real dollars.
# ---------------------------------------------------------------------------
# A plan is a *proposal* the user has not approved yet, so it is kept apart
# from _projects (which holds IRs already promoted into a render job).
_plans: dict[str, dict] = {}


class PlanRequest(BaseModel):
    script: str
    name: str = "untitled"
    style: str = "dr_vsl"
    use_llm: bool = True


class PlanScene(BaseModel):
    """A user-confirmed (or user-edited) scene, sent back on render."""

    id: str
    vo: str | None = None
    prompt: str | None = None


class RenderRequest(BaseModel):
    plan_id: str
    scenes: list[PlanScene] = []
    name: str = "untitled"
    style: str = "dr_vsl"
    aspect: str = "9:16"
    fps: int = 30
    draft: bool = False
    video_provider: str | None = None
    voice_provider: str | None = None
    out_name: str | None = None


def _scene_view(scene: object) -> dict:
    """Flatten one Scene for the confirmation UI.

    `caption` is not a separate field on purpose: kinetic captions are driven
    by the voiceover's word-level timings, so there is exactly one piece of
    text per scene and editing it moves the caption too. Exposing them as two
    independent inputs would be a lie the renderer could not honour.
    """
    return {
        "id": scene.id,
        "index": scene.index,
        "beat": scene.beat,
        "vo": scene.vo.text,
        "prompt": scene.visual.prompt,
        "motion": scene.visual.motion,
        "style": scene.visual.style or "",
    }


def _apply_scene_overrides(project: object, overrides: list[dict]) -> int:
    """Write confirmed copy back onto the IR. Returns the number changed."""
    by_id = {s.id: s for s in project.scenes}
    changed = 0
    for ov in overrides or []:
        scene = by_id.get(getattr(ov, "id", None) or (ov or {}).get("id"))
        if scene is None:
            continue
        vo = getattr(ov, "vo", None) if hasattr(ov, "vo") else (ov or {}).get("vo")
        prompt = getattr(ov, "prompt", None) if hasattr(ov, "prompt") else (ov or {}).get("prompt")
        if vo is not None and vo != scene.vo.text:
            scene.vo.text = vo
            changed += 1
        if prompt is not None and prompt != scene.visual.prompt:
            scene.visual.prompt = prompt
            changed += 1
    return changed


@app.post("/api/plan")
async def create_plan(req: PlanRequest):
    """Script -> scenes. No TTS, no video, no spend."""
    script = (req.script or "").strip()
    if not script:
        raise HTTPException(400, "请先粘贴广告脚本")
    try:
        if req.use_llm:
            project = parse_script_llm(script, name=req.name, style=req.style)
        else:
            project = parse_script(script, name=req.name, style=req.style)
    except Exception as e:  # LLM/rule parse both fall back; surface it plainly
        raise HTTPException(500, f"脚本解析失败：{e}") from e

    if not getattr(project, "scenes", None):
        raise HTTPException(400, "没能从脚本里解析出任何场景，请换一段文案试试")

    plan_id = uuid.uuid4().hex[:12]
    _plans[plan_id] = {
        "project": project,
        "script": script,
        "style": req.style,
        "name": req.name,
    }
    # Also register under _projects so /api/edit can patch a plan that has
    # been rendered under the same id (see /api/render).
    _projects[plan_id] = project
    return {
        "plan_id": plan_id,
        "name": req.name,
        "style": req.style,
        "scenes": [_scene_view(s) for s in project.scenes],
    }


@app.get("/api/plan/{plan_id}")
async def get_plan(plan_id: str):
    if plan_id not in _plans:
        raise HTTPException(404, "Plan not found or expired")
    p = _plans[plan_id]
    return {
        "plan_id": plan_id,
        "name": p["name"],
        "style": p["style"],
        "scenes": [_scene_view(s) for s in p["project"].scenes],
    }


@app.post("/api/render")
async def render(req: RenderRequest):
    """Render a confirmed plan. This is the step that costs money."""
    from avb.assets.video import build_video_provider

    cfg0 = load_config()
    try:
        build_video_provider(req.video_provider or cfg0.video.provider)
    except Exception as e:
        raise HTTPException(
            400,
            "尚未配置可用的视频 Provider。请到「设置」页选择一家并填入 API Key。",
        ) from e

    if req.plan_id not in _plans:
        raise HTTPException(404, "Plan not found or expired, please re-run the script step")

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
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, _run_render, job_id, req.model_dump())
    return {"job_id": job_id}


def _run_render(job_id: str, params: dict):
    """Render a confirmed plan in a worker thread."""
    try:
        entry = _plans.get(params.get("plan_id", ""))
        if entry is None:
            raise RuntimeError("Plan expired before rendering started")
        project = entry["project"]

        _update_job(job_id, stage="confirming", progress=0.05,
                     message="Applying confirmed copy...")

        changed = _apply_scene_overrides(project, params.get("scenes") or [])
        if changed:
            _update_job(job_id, stage="confirming", progress=0.08,
                         message=f"Applied {changed} edit(s) to the confirmed copy")

        cfg = load_config()
        if params.get("video_provider"):
            cfg.video.provider = params["video_provider"]
        if params.get("voice_provider"):
            cfg.voice.provider = params["voice_provider"]

        _update_job(job_id, stage="synth", progress=0.15,
                     message=f"Rendering {len(project.scenes)} scenes...")

        out_dir = Path("demo/out/web")
        out_dir.mkdir(parents=True, exist_ok=True)
        name = params.get("name") or entry["name"] or "untitled"
        out_name = params.get("out_name") or f"{name}_{job_id[:6]}.mp4"
        out_path = out_dir / out_name

        pipe = Pipeline(workdir=".avb_web", draft=params.get("draft", False), config=cfg)

        # Keep the IR alive so /api/edit can patch it without re-parsing, and
        # so a follow-up render of the same plan reuses the cache.
        _projects[job_id] = project
        _projects[params["plan_id"]] = project

        _update_job(job_id, stage="rendering", progress=0.3, message="Rendering scenes...")
        report = pipe.run(project, out_path)

        def _count(v):
            return len(v) if isinstance(v, (list, tuple)) else (v or 0)

        _update_job(job_id, stage="done", progress=1.0, message="Complete",
                     done=True,
                     video_url=f"/api/video/{out_path.name}",
                     video_path=str(out_path),
                     cost=getattr(report, "cost_usd", 0.0),
                     total_duration=getattr(report, "total_duration", 0.0),
                     scenes=_count(getattr(report, "scenes", 0)),
                     rendered=_count(getattr(report, "rendered", 0)),
                     reused=_count(getattr(report, "reused", 0)))
    except Exception as e:
        _update_job(job_id, stage="error", progress=0, done=True, error=str(e))


@app.post("/api/generate")
async def generate(req: GenerateRequest):
    # Fail fast with an actionable message rather than burning a job on a
    # pipeline that cannot produce footage: there is no offline video path.
    from avb.assets.video import build_video_provider
    cfg0 = load_config()
    try:
        build_video_provider(req.video_provider or cfg0.video.provider)
    except Exception as e:
        raise HTTPException(
            400,
            "尚未配置可用的视频 Provider。请在左侧「Provider 配置」里选择一家并填入 API Key。",
        ) from e

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

        out = {
            "intent": plan.intent,
            "scene_ids": plan.scene_ids,
            "ops": [{"path": o.path, "value": o.value} for o in plan.ops],
            "explanation": plan.explanation,
        }

        if not req.apply:
            return out

        # --- apply and re-render -----------------------------------------
        # apply_plan returns a NEW project, so the pre-edit version is still
        # intact: invalidate() diffs the two and we only re-render scenes
        # whose content hash actually moved. That is the feature the whole
        # architecture exists for.
        new_project = apply_plan(project, plan)
        diff = invalidate(project, new_project)  # Project.diff -> dict
        stale = set(diff.get("invalidated", []) or diff.get("added", []) or [])

        _projects[req.job_id] = new_project

        job_id = uuid.uuid4().hex[:12]
        _jobs[job_id] = {
            "job_id": job_id,
            "stage": "queued",
            "progress": 0.0,
            "message": "Re-rendering edited scenes",
            "done": False,
            "error": None,
            "video_url": None,
            "cost": None,
        }
        asyncio.get_event_loop().run_in_executor(
            None, _run_rerender, job_id, req.job_id, new_project, sorted(stale),
            req.out_name, req.draft,
        )
        out["job_id"] = job_id
        out["rebuilt"] = sorted(stale)
        return out
    except Exception as e:
        raise HTTPException(500, str(e))


def _run_rerender(job_id: str, src_job_id: str | None, project: object,
                  stale: list[str], out_name: str | None, draft: bool):
    """Re-render a project after an edit, rebuilding only the stale scenes."""
    try:
        cfg = load_config()
        out_dir = Path("demo/out/web")
        out_dir.mkdir(parents=True, exist_ok=True)
        name = getattr(project, "name", "untitled") or "untitled"
        path = out_dir / (out_name or f"{name}_edit_{job_id[:6]}.mp4")

        only = set(stale) or None
        _update_job(job_id, stage="rendering", progress=0.2,
                     message=f"Rebuilding {len(only) if only else len(project.scenes)} scene(s)...")

        pipe = Pipeline(workdir=".avb_web", draft=draft, config=cfg)
        _projects[job_id] = project
        report = pipe.run(project, path, only=only)

        def _count(v):
            return len(v) if isinstance(v, (list, tuple)) else (v or 0)

        _update_job(job_id, stage="done", progress=1.0, message="Complete", done=True,
                     video_url=f"/api/video/{path.name}",
                     video_path=str(path),
                     cost=getattr(report, "cost_usd", 0.0),
                     total_duration=getattr(report, "total_duration", 0.0),
                     scenes=_count(getattr(report, "scenes", 0)),
                     rendered=_count(getattr(report, "rendered", 0)),
                     reused=_count(getattr(report, "reused", 0)))
    except Exception as e:
        _update_job(job_id, stage="error", progress=0, done=True, error=str(e))


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
