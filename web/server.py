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
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# --- avb imports -----------------------------------------------------------
from avb.config import (
    Config, load_config, load_dotenv, config_set, env_path, find_config_path,
    write_env, save_config,
)
from avb.parse import parse_script, parse_script_llm
from avb.brief import generate_scripts
from avb.pipeline import Pipeline
from avb.edit import apply_plan
from avb.llm_edit import plan_edit_llm
from avb.edit import plan_edit
from avb.provider_specs import (
    VIDEO_SPECS, VOICE_SPECS, LLM_SPECS, PUBLISH_SPECS,
    spec_for, env_status, mask,
)

load_dotenv()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Remember the main loop so worker threads can schedule broadcasts."""
    global _main_loop
    _main_loop = asyncio.get_running_loop()
    yield


app = FastAPI(title="AI Video Builder", version="0.3.0", lifespan=lifespan)

STATIC = Path(__file__).parent / "static"
# React build output (web/frontend -> web/static/dist). If it is missing we
# fall back to the hand-written legacy page, so the server always boots.
DIST = STATIC / "dist"
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
if DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=str(DIST / "assets")), name="assets")

# ---------------------------------------------------------------------------
# Product reference images (three-view uploads)
# ---------------------------------------------------------------------------
# Uploaded product photos that steer video generation (MiniMax H3 v2
# reference-to-video keeps the product identity across scenes). Saved under
# .avb_web/uploads and served back for thumbnail previews.
UPLOAD_DIR = Path(".avb_web/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/api/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")

# Vendor limits: MiniMax H3 v2 allows <= 9 reference images, each <= 30 MB,
# body <= 64 MB total. 10 MB / 9 images keeps us far inside all three.
MAX_UPLOADS = 9
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

# job_id -> resolved reference image paths, so edit re-renders inherit them.
_ref_images_by_job: dict[str, list[str]] = {}

# job_id -> render flags (voice/captions), so conversational edit re-renders
# inherit the same audio/text layers the user last picked.
_render_flags_by_job: dict[str, dict] = {}

_ALLOWED_IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
    (b"BM", ".bmp"),
)


def _upload_ext(raw: bytes, fallback: str) -> str:
    """Extension from magic bytes first, filename second (bg.png taught us
    vendors and users alike hand over video bytes wearing an image name)."""
    for magic, ext in _MAGIC:
        if raw.startswith(magic):
            return ext
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return ".webp"
    return fallback if fallback in _ALLOWED_IMG_EXT else ""


@app.post("/api/upload")
async def upload_images(files: list[UploadFile] = File(...)):
    """Store product reference images; returns {path, name, url} per file.

    `path` is what the render request sends back; `url` is for the <img>
    thumbnail. Magic-byte validated: anything that is not a real image
    (e.g. a video renamed .png) is rejected here rather than detonating
    three layers downstream.
    """
    if len(files) > MAX_UPLOADS:
        raise HTTPException(400, f"一次最多上传 {MAX_UPLOADS} 张参考图")
    out = []
    for f in files:
        raw = await f.read()
        if len(raw) > MAX_UPLOAD_BYTES:
            raise HTTPException(400, f"{f.filename or 'image'} 超过 10MB 限制")
        ext = _upload_ext(raw, (Path(f.filename or "").suffix or "").lower())
        if not ext:
            raise HTTPException(
                400,
                f"{f.filename or 'image'} 不是可识别的图片（支持 PNG/JPG/WEBP/GIF/BMP）",
            )
        name = f"{uuid.uuid4().hex[:12]}{ext}"
        (UPLOAD_DIR / name).write_bytes(raw)
        out.append({
            "path": str(UPLOAD_DIR / name),
            "name": f.filename or name,
            "url": f"/api/uploads/{name}",
            "size": len(raw),
        })
    return {"files": out}


def _resolve_reference_images(paths: list[str]) -> list[str]:
    """Keep only real image files that live inside UPLOAD_DIR.

    The containment check matters: a forged reference path would otherwise
    get base64-encoded straight into an outbound vendor request.
    """
    root = UPLOAD_DIR.resolve()
    out = []
    for p in paths or []:
        try:
            rp = Path(p).resolve()
        except (OSError, ValueError):
            continue
        if rp.parent == root and rp.is_file() and rp.suffix.lower() in _ALLOWED_IMG_EXT:
            out.append(str(rp))
    return out

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
# Background generation jobs (scripts / plan)
#
# LLM script-writing and scene planning are single blocking calls that take
# 10-60s. Handing the user a spinner with no feedback felt frozen; these wrap
# the work in the same job machinery as renders, with a *smoothed* progress
# ticker: real progress is unknowable inside one LLM call, so we creep toward
# 92% and let the completing worker set the truth (100% or error). The
# "fake" numbers only ever move forward and never claim completion early --
# they exist to answer "is it still working?", not to mislead.
# ---------------------------------------------------------------------------


def _new_job(kind: str, message: str = "Job queued") -> str:
    job_id = uuid.uuid4().hex[:12]
    _jobs[job_id] = {
        "job_id": job_id,
        "kind": kind,
        "stage": "queued",
        "progress": 0.02,
        "message": message,
        "done": False,
        "error": None,
        "video_url": None,
        "cost": None,
        "stats": None,
        "result": None,
    }
    return job_id


def _ticker(job_id: str, stop: threading.Event, phases: list[tuple[str, str]], cap: float = 0.92):
    """Smooth progress + rotating phase messages while real work runs."""
    t0 = time.time()
    progress = 0.03
    i = 0
    while not stop.wait(2.0):
        progress = min(cap, progress + (cap - progress) * 0.10 + 0.008)
        phase, fallback = phases[min(i // 3, len(phases) - 1)]
        _update_job(
            job_id,
            stage=phase,
            progress=round(progress, 3),
            message=fallback,
            stats={"phase": phase, "elapsed": round(time.time() - t0, 1)},
        )
        i += 1


def _finish_job(job_id: str, result: dict):
    stop = _tickers.pop(job_id, None)
    if stop:
        stop.set()
    _update_job(job_id, stage="done", progress=1.0, message="Complete",
                done=True, result=result)


def _fail_job(job_id: str, err: str):
    stop = _tickers.pop(job_id, None)
    if stop:
        stop.set()
    _update_job(job_id, stage="error", progress=0, done=True, error=err)


_tickers: dict[str, threading.Event] = {}


def _start_ticker(job_id: str, phases: list[tuple[str, str]]):
    stop = threading.Event()
    _tickers[job_id] = stop
    threading.Thread(target=_ticker, args=(job_id, stop, phases), daemon=True).start()


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
    voice_model: str | None = None
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
    "moonshot": {"base_url": "https://api.moonshot.cn/v1", "model": "moonshot-v1-8k",
                 "api_key_env": "MOONSHOT_API_KEY"},
    "zhipu": {"base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4.6",
               "api_key_env": "ZHIPU_API_KEY"},
    "qwen": {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus",
             "api_key_env": "BAILIAN_API_KEY"},
    # "compat" deliberately has no preset: the whole point is that the user
    # types the base URL, so auto-filling it would defeat the field.
}


def _spec_payload(specs, selected: str | None = None, model_values: dict[str, str] | None = None):
    """Serialize specs for the settings page.

    `model_values` overrides the current model per spec key, for values that
    live somewhere other than the spec's env var (the LLM model lives in
    avb.toml, for instance).
    """
    out = []
    for s in specs:
        if model_values and s.key in model_values:
            current = model_values[s.key] or ""
        else:
            current = os.environ.get(s.model_env, "") if s.model_env else ""
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
            "model_value": current,
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
            "specs": _spec_payload(
                VOICE_SPECS, cfg.voice.provider,
                # The chosen voice is a config value (voice.voice), not an
                # env var, so hand it to the UI explicitly.
                {s.key: cfg.voice.voice for s in VOICE_SPECS if s.model_env},
            ),
        },
        "llm": {
            "selected": cfg.llm.provider,
            "model": cfg.llm.model,
            "base_url": cfg.llm.base_url,
            "api_key_env": cfg.llm.api_key_env,
            "api_key_set": bool(cfg.llm.api_key),
            "api_key_masked": mask(cfg.llm.api_key),
            "specs": _spec_payload(
                LLM_SPECS, cfg.llm.provider, {s.key: cfg.llm.model for s in LLM_SPECS}
            ),
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
        # per-provider model override, e.g. SEEDANCE_MODEL / MINIMAX_MODEL
        sel = (req.video_provider or load_config().video.provider or "").lower()
        spec = spec_for("video", sel)
        if spec and spec.model_env:
            secrets[spec.model_env] = req.video_model.strip()

    if req.voice_model:
        # AVB_VOICE is the one env var the voice adapters read for the voice
        # id; it also mirrors [voice].voice in avb.toml.
        sel = (req.voice_provider or load_config().voice.provider or "").lower()
        spec = spec_for("voice", sel)
        env_name = (spec.model_env if spec and spec.model_env else "AVB_VOICE")
        if req.voice_model.strip():
            secrets[env_name] = req.voice_model.strip()

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


class ScriptsRequest(BaseModel):
    """Product brief -> four competing ad-script concepts."""

    product: str = ""
    selling_points: list[str] | str | None = None
    style: str = "dr_vsl"
    audience: str | None = None
    tone: str | None = None
    use_llm: bool = True


@app.post("/api/scripts")
async def create_scripts(req: ScriptsRequest):
    """Brief -> up to four ad-script variants. Free; no generation yet.

    Runs as a background job: LLM writing takes tens of seconds and the UI
    wants a live progress panel, not a dead spinner.
    """
    product = (req.product or "").strip()
    if not product and not req.selling_points:
        raise HTTPException(400, "请至少填写产品名称或卖点")
    params = req.model_dump()
    job_id = _new_job("scripts")
    _start_ticker(job_id, [
        ("brief", "Reading product & selling points"),
        ("drafting", "Drafting 4 script frameworks"),
        ("polishing", "Polishing tone & hooks"),
    ])

    def _run():
        try:
            drafts = generate_scripts(
                product=params.get("product", ""),
                selling_points=params.get("selling_points"),
                style=params.get("style", "dr_vsl"),
                audience=params.get("audience"),
                tone=params.get("tone"),
                use_llm=params.get("use_llm", True),
            )
            _finish_job(job_id, {
                "brief": {"product": product, "style": params.get("style", "dr_vsl")},
                "scripts": [d.to_dict() for d in drafts],
            })
        except Exception as e:  # LLM path falls back internally; surface plainly
            _fail_job(job_id, f"脚本生成失败：{e}")

    asyncio.get_event_loop().run_in_executor(None, _run)
    return {"job_id": job_id}


class PlanScene(BaseModel):
    """A user-confirmed (or user-edited) scene, sent back on render."""

    id: str
    vo: str | None = None
    prompt: str | None = None
    shot: str | None = None
    angle: str | None = None
    elements: list[str] | None = None


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
    # Target total length in seconds. Scenes are scaled proportionally after
    # voice synthesis; VO/caption sync is untouched.
    target_seconds: float | None = None
    # Product reference images (three-view set) uploaded via /api/upload.
    # Steer video generation so every scene keeps the same product identity.
    reference_images: list[str] = []
    # Optional audio/text layers. Off by default would be unusual, but the
    # UI lets the user skip the voiceover (silent clips) and/or the kinetic
    # captions (burned-in subtitles) per render.
    with_voice: bool = True
    with_captions: bool = True


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
        "shot": scene.visual.shot or "",
        "angle": scene.visual.angle or "",
        "elements": scene.visual.elements or [],
    }


def _apply_scene_overrides(project: object, overrides: list[dict]) -> int:
    """Write confirmed copy back onto the IR. Returns the number changed."""
    by_id = {s.id: s for s in project.scenes}
    changed = 0
    for ov in overrides or []:
        scene = by_id.get(getattr(ov, "id", None) or (ov or {}).get("id"))
        if scene is None:
            continue
        ovd = ov if isinstance(ov, dict) else {
            "vo": getattr(ov, "vo", None),
            "prompt": getattr(ov, "prompt", None),
            "shot": getattr(ov, "shot", None),
            "angle": getattr(ov, "angle", None),
            "elements": getattr(ov, "elements", None),
        }
        vo = ovd.get("vo")
        prompt = ovd.get("prompt")
        shot = ovd.get("shot")
        angle = ovd.get("angle")
        elements = ovd.get("elements")
        if vo is not None and vo != scene.vo.text:
            scene.vo.text = vo
            changed += 1
        if prompt is not None and prompt != scene.visual.prompt:
            scene.visual.prompt = prompt
            changed += 1
        if shot is not None and shot != scene.visual.shot:
            scene.visual.shot = shot
            changed += 1
        if angle is not None and angle != scene.visual.angle:
            scene.visual.angle = angle
            changed += 1
        if elements is not None:
            el = list(elements) if isinstance(elements, (list, tuple)) else None
            if el != (scene.visual.elements or []):
                scene.visual.elements = el
                changed += 1
    return changed


@app.post("/api/plan")
async def create_plan(req: PlanRequest):
    """Script -> scenes. No TTS, no video, no spend. Background job (see /api/scripts)."""
    script = (req.script or "").strip()
    if not script:
        raise HTTPException(400, "请先粘贴广告脚本")
    params = req.model_dump()
    job_id = _new_job("plan")
    _start_ticker(job_id, [
        ("parsing", "Splitting the script into scenes"),
        ("cinema", "Assigning shot / angle / elements"),
        ("polishing", "Polishing visual prompts"),
    ])

    def _run():
        try:
            if params.get("use_llm", True):
                project = parse_script_llm(script, name=params["name"], style=params["style"])
            else:
                project = parse_script(script, name=params["name"], style=params["style"])
            if not getattr(project, "scenes", None):
                _fail_job(job_id, "没能从脚本里解析出任何场景，请换一段文案试试")
                return
            plan_id = uuid.uuid4().hex[:12]
            _plans[plan_id] = {
                "project": project,
                "script": script,
                "style": params["style"],
                "name": params["name"],
            }
            # Also register under _projects so /api/edit can patch a plan that
            # has been rendered under the same id (see /api/render).
            _projects[plan_id] = project
            _finish_job(job_id, {
                "plan_id": plan_id,
                "name": params["name"],
                "style": params["style"],
                "scenes": [_scene_view(s) for s in project.scenes],
            })
        except Exception as e:  # LLM/rule parse both fall back; surface it plainly
            _fail_job(job_id, f"脚本解析失败：{e}")

    asyncio.get_event_loop().run_in_executor(None, _run)
    return {"job_id": job_id}


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

    job_id = _new_job("render")
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

        # Visual style is chosen at render time (confirm step), not in the
        # brief: an explicit pick overrides the project default AND every
        # per-scene style, because the user's choice is a whole-film intent.
        # An empty/absent style keeps whatever the parser assigned.
        chosen_style = (params.get("style") or "").strip()
        if chosen_style:
            project.style = chosen_style
            for s in getattr(project, "scenes", []):
                s.visual.style = chosen_style

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
        raw_target = params.get("target_seconds")
        try:
            target = float(raw_target) if raw_target else None
        except (TypeError, ValueError):
            target = None
        if target is not None:
            target = min(60.0, max(5.0, target))

        # Live video progress: the pipeline stamps scene context onto every
        # provider report (submit chars, vendor task id, poll status, received
        # MB). Progress maps scene_index into the 0.30-0.95 band; scene_done
        # events land exactly on their fraction so the bar only moves forward.
        n_total = len(project.scenes)

        def _video_progress(info: dict):
            idx = int(info.get("scene_index") or 0)
            phase = info.get("phase", "")
            if phase == "scene_done":
                frac = idx / max(1, n_total)
                _update_job(job_id, stage="rendering",
                            progress=round(0.30 + 0.65 * frac, 3),
                            message=f"Scene {idx}/{n_total} done",
                            stats={"phase": "scene_done", "scene_index": idx,
                                   "scene_total": n_total, "cached": info.get("cached")})
                return
            # In-scene: stay at the scene's lower bound; the detail lives in
            # stats for the UI to format.
            frac = (idx - 1) / max(1, n_total) if idx > 1 else 0.0
            stats = {"phase": phase}
            for k in ("prompt_chars", "task_id", "status", "elapsed", "received_mb",
                      "provider", "scene_index", "scene_total", "scene_id", "model"):
                if info.get(k) is not None:
                    stats[k] = info.get(k)
            _update_job(job_id, stage="rendering",
                        progress=round(0.30 + 0.65 * frac, 3),
                        stats=stats)

        pipe.video_progress_cb = _video_progress
        ref_images = _resolve_reference_images(params.get("reference_images") or [])
        # Remember them so /api/edit re-renders keep steering generation with
        # the same product images.
        _ref_images_by_job[job_id] = ref_images
        # Captions are only meaningful with a voiceover; guard against a
        # client sending captions-on + voice-off.
        want_voice = bool(params.get("with_voice", True))
        want_captions = bool(params.get("with_captions", True)) and want_voice
        _render_flags_by_job[job_id] = {
            "with_voice": want_voice,
            "with_captions": want_captions,
        }
        report = pipe.run(
            project, out_path,
            target_seconds=target,
            reference_images=ref_images or None,
            with_voice=want_voice,
            with_captions=want_captions,
        )

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
                     reused=_count(getattr(report, "reused", 0)),
                     warnings=list(getattr(report, "warnings", []) or []))
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
                     reused=_count(getattr(report, "reused", 0)),
                     warnings=list(getattr(report, "warnings", []) or []))

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
        ref_images = _ref_images_by_job.get(src_job_id, [])
        _ref_images_by_job[job_id] = ref_images
        # Inherit the voiceover/caption choice from the render this edit
        # branched off of, so a no-voice edit doesn't suddenly grow a VO.
        flags = _render_flags_by_job.get(src_job_id, {})
        want_voice = bool(flags.get("with_voice", True))
        want_captions = bool(flags.get("with_captions", True)) and want_voice
        _render_flags_by_job[job_id] = {
            "with_voice": want_voice,
            "with_captions": want_captions,
        }
        report = pipe.run(
            project, path, only=only, reference_images=ref_images or None,
            with_voice=want_voice, with_captions=want_captions,
        )

        def _count(v):
            return len(v) if isinstance(v, (list, tuple)) else (v or 0)

        _update_job(job_id, stage="done", progress=1.0, message="Complete", done=True,
                     video_url=f"/api/video/{path.name}",
                     video_path=str(path),
                     cost=getattr(report, "cost_usd", 0.0),
                     total_duration=getattr(report, "total_duration", 0.0),
                     scenes=_count(getattr(report, "scenes", 0)),
                     rendered=_count(getattr(report, "rendered", 0)),
                     reused=_count(getattr(report, "reused", 0)),
                     warnings=list(getattr(report, "warnings", []) or []))
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
