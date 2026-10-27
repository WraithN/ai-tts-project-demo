"""`avb` command-line interface.

    avb doctor                 check providers, keys, ffmpeg, GPU
    avb config get|set|list    edit avb.toml without touching a text editor
    avb config web             local HTML config page
    avb parse script.txt       script -> IR (JSON on stdout)
    avb render script.txt      script -> mp4
    avb edit proj.json "..."   conversational edit -> patched IR
    avb providers              what is wired up and what it costs
    avb tts "hello"            quick voice test

Everything the demo does is reachable from here; `demo/run_demo.py` is just
a scripted tour of these commands.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Allow `python -m avb.cli` and `python avb/cli.py` both to work.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "avb"


# --------------------------------------------------------------------------
# shared helpers
# --------------------------------------------------------------------------


def _load(args) -> "Config":
    from .config import load_config

    overrides = {}
    if getattr(args, "llm_model", None):
        overrides["llm.model"] = args.llm_model
    if getattr(args, "voice", None):
        overrides["voice.provider"] = args.voice
    if getattr(args, "video", None):
        overrides["video.provider"] = args.video
    return load_config(args.config, overrides=overrides or None)


def _print(obj) -> None:
    if isinstance(obj, (dict, list)):
        print(json.dumps(obj, indent=2, ensure_ascii=False))
    else:
        print(obj)


# --------------------------------------------------------------------------
# doctor
# --------------------------------------------------------------------------


def cmd_doctor(args) -> int:
    from .config import find_config_path
    from .assets.voice import build_voice_provider
    from .assets.video import build_video_provider
    from .assets.base import which

    cfg = _load(args)
    rows = []

    cfg_path = find_config_path()
    rows.append(("config file", str(cfg_path) if cfg_path else "not found (using defaults)"))

    # LLM
    key = cfg.llm.api_key
    rows.append(("llm provider", f"{cfg.llm.provider} / {cfg.llm.model}"))
    rows.append(("llm base_url", cfg.llm.base_url))
    rows.append(("llm api key", f"set (${cfg.llm.api_key_env}, {len(key)} chars)" if key else f"MISSING (${cfg.llm.api_key_env})"))
    if key:
        try:
            from .llm import LLMClient

            client = LLMClient(cfg.llm)
            resp = client.chat(
                [{"role": "user", "content": "Reply with the single word: ok"}],
                max_tokens=2000,
            )
            rows.append(("llm connectivity", f"OK ({resp.model}, {resp.latency_s}s)"))
        except Exception as e:
            rows.append(("llm connectivity", f"FAILED: {e}"))

    # voice
    v = build_voice_provider(cfg.voice.provider)
    rows.append(("voice provider", f"{v.name} ({'available' if v.available() else 'UNAVAILABLE'})"))
    rows.append(("voice preset", cfg.voice.voice))

    # video
    try:
        vid = build_video_provider(cfg.video.provider)
        rows.append(("video provider", f"{vid.name} (available)"))
    except Exception as e:
        rows.append(("video provider", f"NOT CONFIGURED -- {e}"))

    # render tools
    rows.append(("ffmpeg", which("ffmpeg") or "MISSING"))
    rows.append(("ffprobe", which("ffprobe") or "MISSING (mp3 duration falls back to word timings)"))

    # python deps
    for mod in ("numpy", "scipy", "PIL", "requests", "edge_tts", "tomllib"):
        try:
            __import__(mod)
            rows.append((f"py:{mod}", "ok"))
        except Exception:
            rows.append((f"py:{mod}", "MISSING"))

    # GPU (informational)
    try:
        import subprocess

        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10,
        )
        gpu = out.stdout.strip().splitlines()[0] if out.stdout.strip() else "none"
    except Exception:
        gpu = "none"
    rows.append(("gpu", gpu))

    width = max(len(k) for k, _ in rows) + 2
    print("avb doctor")
    print("-" * (width + 40))
    for k, val in rows:
        print(f"  {k.ljust(width)} {val}")

    missing = [k for k, v in rows if "MISSING" in str(v) and k.startswith(("py:", "ffmpeg"))]
    return 1 if missing else 0


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------


def cmd_config(args) -> int:
    from .config import (
        DEFAULT_CONFIG_NAME,
        config_set,
        find_config_path,
        load_config,
        save_config,
    )

    action = args.config_action

    if action == "init":
        dest = Path(args.config or DEFAULT_CONFIG_NAME)
        if dest.exists() and not args.force:
            print(f"{dest} already exists (use --force to overwrite)")
            return 1
        from .config import Config

        save_config(Config(), dest)
        print(f"wrote {dest}")
        env = dest.with_name(".env.example")
        env.write_text(
            "# Copy to .env and fill in. .env is gitignored.\n"
            "DEEPSEEK_API_KEY=\n"
            "ELEVENLABS_API_KEY=\n"
            "KLING_API_KEY=\n"
            "GOOGLE_API_KEY=\n",
            encoding="utf-8",
        )
        print(f"wrote {env} (copy to .env)")
        return 0

    if action == "path":
        p = find_config_path()
        print(p or "not found")
        return 0 if p else 1

    if action == "list":
        cfg = _load(args)
        _print(cfg.to_dict())
        return 0

    if action == "show":
        cfg = _load(args)
        print(cfg.describe())
        return 0

    if action == "get":
        cfg = _load(args)
        d = cfg.to_dict()
        cur = d
        for part in args.key.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                print(f"unknown key: {args.key}", file=sys.stderr)
                return 1
        print(cur)
        return 0

    if action == "set":
        path = Path(args.config or find_config_path() or DEFAULT_CONFIG_NAME)
        if not path.exists():
            from .config import Config, save_config

            save_config(Config(), path)
            print(f"created {path}")
        ok, msg = config_set(path, args.key, args.value)
        print(msg)
        return 0 if ok else 1

    if action == "web":
        from .webui import serve

        path = Path(args.config or find_config_path() or DEFAULT_CONFIG_NAME)
        serve(path, host=args.host, port=args.port, open_browser=not args.no_browser)
        return 0

    print(f"unknown config action: {action}", file=sys.stderr)
    return 1


# --------------------------------------------------------------------------
# parse / render / edit
# --------------------------------------------------------------------------


def _read_script(path_or_text: str) -> str:
    p = Path(path_or_text)
    if p.is_file():
        return p.read_text(encoding="utf-8")
    return path_or_text


def cmd_parse(args) -> int:
    from .parse import parse_script, parse_script_llm

    script = _read_script(args.script)
    if args.llm:
        project = parse_script_llm(script, name=args.name)
    else:
        project = parse_script(script, name=args.name)

    payload = {
        "id": project.id,
        "name": project.name,
        "style": project.style,
        "aspect": project.aspect,
        "scenes": [
            {
                "id": s.id,
                "beat": s.beat,
                "text": s.vo.text if s.vo else "",
                "visual": s.visual.prompt if s.visual else "",
                "kind": s.visual.kind if s.visual else "",
                "motion": s.visual.motion if s.visual else "",
            }
            for s in project.scenes
        ],
    }
    if args.out:
        Path(args.out).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"wrote {args.out} ({len(project.scenes)} scenes)")
    else:
        _print(payload)
    return 0


def cmd_render(args) -> int:
    from .parse import parse_script, parse_script_llm
    from .pipeline import Pipeline

    cfg = _load(args)
    script = _read_script(args.script)
    parse = parse_script_llm if args.llm else parse_script
    project = parse(script, name=args.name, style=args.style or cfg.style, aspect=args.aspect or cfg.aspect)

    pipe = Pipeline(
        workdir=args.workdir or cfg.workdir,
        draft=args.draft,
        workers=cfg.render.workers,
        config=cfg,
    )
    print(pipe.describe())
    print(f"scenes: {len(project.scenes)}  style: {project.style}  aspect: {project.aspect}")

    out_path = Path(args.out) if args.out else Path(cfg.workdir) / "out" / f"{project.id}.mp4"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    report = pipe.run(project, out_path, only=set(args.only.split(",")) if args.only else None)

    print(f"video: {report.video}")

    print(f"duration: {report.total_duration:.1f}s  wall: {report.wall_seconds:.1f}s")
    print(f"rendered: {len(report.rendered)}  reused: {len(report.reused)}")
    print(f"cost: ${report.cost_usd:.4f}  {report.cost_by_kind}")
    if report.qc:
        flag = "PASS" if report.qc.get("passed") else "FAIL"
        print(
            f"qc: {flag}  errors={report.qc.get('errors', 0)} "
            f"warnings={report.qc.get('warnings', 0)} "
            f"coverage={report.qc.get('coverage', 0)}"
        )
    return 0


def cmd_edit(args) -> int:
    from .edit import apply_plan, invalidate
    from .llm_edit import plan_edit_llm
    from .edit import plan_edit

    data = json.loads(Path(args.project).read_text(encoding="utf-8"))
    from .ir import Project

    project = Project.from_dict(data) if hasattr(Project, "from_dict") else None
    if project is None:
        print(
            "project JSON is a parse output (id/scenes list) -- render first to get a full IR, "
            "or use `avb render` + `avb edit` on the saved project in .avb/projects/",
            file=sys.stderr,
        )
        return 1

    if args.rules_only:
        plan = plan_edit(project, args.instruction)
    else:
        plan = plan_edit_llm(project, args.instruction)

    print(f"intent:    {plan.intent}")
    print(f"scenes:    {len(plan.scene_ids)} -> {', '.join(plan.scene_ids[:8])}")
    print(f"ops:       {', '.join(o.path for o in plan.ops)}")
    print(f"why:       {plan.explanation}")

    patched = apply_plan(project, plan)
    delta = invalidate(project, patched)
    print(f"stale:     {delta.get('stale_scenes')} scene(s) will re-render")

    if args.out:
        Path(args.out).write_text(json.dumps(patched.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"wrote {args.out}")
    return 0


# --------------------------------------------------------------------------
# providers / tts
# --------------------------------------------------------------------------


def cmd_providers(args) -> int:
    from .assets.voice import VOICE_REGISTRY
    from .assets.video import _REGISTRY as VIDEO_REGISTRY

    cfg = _load(args)
    print(f"config: {cfg.describe()}\n")

    print("VOICE")
    print(f"  {'name':<12} {'available':<10} notes")
    for key, cls in sorted(VOICE_REGISTRY.items()):
        try:
            inst = cls()
            avail = "yes" if inst.available() else "no"
            note = getattr(inst, "name", key)
        except Exception as e:
            avail, note = "error", str(e)[:40]
        marker = " <- selected" if key == cfg.voice.provider or (cfg.voice.provider == "auto" and key == "edge") else ""
        print(f"  {key:<12} {avail:<10} {note}{marker}")

    print("\nVIDEO")
    print(f"  {'name':<12} {'available':<10} notes")
    for key, cls in sorted(VIDEO_REGISTRY.items()):
        try:
            inst = cls()
            avail = "yes" if inst.available() else "no"
            note = getattr(inst, "name", key)
        except Exception as e:
            avail, note = "error", str(e)[:40]
        print(f"  {key:<12} {avail:<10} {note}")

    print("\nLLM")
    print(f"  provider  {cfg.llm.provider}")
    print(f"  model     {cfg.llm.model}")
    print(f"  base_url  {cfg.llm.base_url}")
    print(f"  api_key   {'set' if cfg.llm.api_key else 'MISSING ($' + cfg.llm.api_key_env + ')'}")
    return 0


def cmd_tts(args) -> int:
    from .assets.voice import build_voice_provider
    from .assets.base import Cache

    cfg = _load(args)
    provider = build_voice_provider(args.voice or cfg.voice.provider)
    print(f"provider: {provider.name}")

    cache = Cache(Path(cfg.workdir) / "cache")
    result = provider.synth(
        args.text,
        voice=args.preset or cfg.voice.voice,
        speed=cfg.voice.speed,
        energy=cfg.voice.energy,
        cache=cache,
    )
    print(f"audio:    {result.path}")
    print(f"duration: {result.duration:.2f}s")
    print(f"words:    {len(result.words)}")
    if result.words[:5]:
        for w in result.words[:5]:
            print(f"   {w['start']:6.2f}s  {w['word']}")
    print(f"cost:     ${result.cost.usd}")
    return 0


def cmd_web(args) -> int:
    """Launch the FastAPI web app (see docs/WEBAPP.md)."""
    try:
        import uvicorn  # noqa: F401
    except ImportError:
        print("web app needs extra deps:  pip install -e '.[web]'", file=sys.stderr)
        return 1

    from .webapp import run_server

    return run_server(host=args.host, port=args.port, open_browser=not args.no_browser)


# --------------------------------------------------------------------------
# entry
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="avb", description="AI video ad builder")
    p.add_argument("--config", help="path to avb.toml")
    p.add_argument("--llm-model", help="override [llm].model")
    p.add_argument("--voice", help="override [voice].provider")
    p.add_argument("--video", help="override [video].provider")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("doctor", help="check the environment")
    d.set_defaults(func=cmd_doctor)

    c = sub.add_parser("config", help="read and edit avb.toml")
    csub = c.add_subparsers(dest="config_action", required=True)
    ci = csub.add_parser("init", help="write a starter avb.toml + .env.example")
    ci.add_argument("--force", action="store_true")
    ci.set_defaults(func=cmd_config)
    csub.add_parser("path", help="print the resolved config path").set_defaults(func=cmd_config)
    csub.add_parser("list", help="dump effective config as JSON").set_defaults(func=cmd_config)
    csub.add_parser("show", help="one-line summary").set_defaults(func=cmd_config)
    cg = csub.add_parser("get", help="print one key")
    cg.add_argument("key", help="dotted, e.g. llm.model")
    cg.set_defaults(func=cmd_config)
    cs = csub.add_parser("set", help="set one key")
    cs.add_argument("key")
    cs.add_argument("value")
    cs.set_defaults(func=cmd_config)
    cw = csub.add_parser("web", help="local HTML config page")
    cw.add_argument("--host", default="127.0.0.1")
    cw.add_argument("--port", type=int, default=8787)
    cw.add_argument("--no-browser", action="store_true")
    cw.set_defaults(func=cmd_config)

    ps = sub.add_parser("parse", help="script -> IR JSON")
    ps.add_argument("script", help="path to a .txt script, or the script itself")
    ps.add_argument("--llm", action="store_true", help="use the LLM parser")
    ps.add_argument("--name")
    ps.add_argument("--out")
    ps.set_defaults(func=cmd_parse)

    r = sub.add_parser("render", help="script -> mp4")
    r.add_argument("script")
    r.add_argument("--llm", action="store_true")
    r.add_argument("--name")
    r.add_argument("--style")
    r.add_argument("--aspect")
    r.add_argument("--draft", action="store_true")
    r.add_argument("--workdir")
    r.add_argument("--out", help="copy the finished mp4 here")
    r.add_argument("--only", help="comma-separated scene ids to (re)render")
    r.set_defaults(func=cmd_render)

    e = sub.add_parser("edit", help="conversational edit")
    e.add_argument("project", help="path to project JSON")
    e.add_argument("instruction", help="natural language, e.g. 'make the hook punchier'")
    e.add_argument("--rules-only", action="store_true", help="skip the LLM router")
    e.add_argument("--out", help="write the patched IR here")
    e.set_defaults(func=cmd_edit)

    pr = sub.add_parser("providers", help="list providers and their status")
    pr.set_defaults(func=cmd_providers)

    t = sub.add_parser("tts", help="synthesize a test line")
    t.add_argument("text")
    t.add_argument("--preset", help="voice preset name or edge voice id")
    t.set_defaults(func=cmd_tts)

    w = sub.add_parser("web", help="launch the web app (browser UI + API)")
    w.add_argument("--host", default="127.0.0.1")
    w.add_argument("--port", type=int, default=8787)
    w.add_argument("--no-browser", action="store_true")
    w.set_defaults(func=cmd_web)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
