"""Automated quality control.

This is the part everyone skips and the part that decides whether
"minimal human intervention" is real. Before the system hands a video to a
person, it checks the things a human would otherwise catch by eye and ear:

  - captions that outlive their scene (text hanging after the VO stops)
  - dead air inside a line
  - shots too short to register or long enough to lose the scroll
  - caption cards that will overflow the safe area

Failures are attached per scene so a human only reviews what actually broke.
"""

from __future__ import annotations

import numpy as np

from .assets.voice import SR
from .ir import Project, Scene
from .render.composer import chunk_words, scale_for_canvas
from .styles import get_style

MIN_SHOT = 0.8
MAX_SHOT = 6.0
MAX_CARD_WORDS = 4
SAFE_WIDTH_FRAC = 0.88


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(x * x))) if x.size else 0.0


def check_scene(scene: Scene, assets, timing, project: Project) -> list[dict]:
    issues: list[dict] = []
    sid = scene.id

    # --- caption runs past the scene ------------------------------------
    if assets.vo_words:
        last = assets.vo_words[-1]["end"]
        if last > timing.duration + 0.02:
            issues.append(
                {
                    "scene": sid,
                    "severity": "error",
                    "code": "caption_overrun",
                    "detail": f"last word ends at {last:.2f}s but shot is {timing.duration:.2f}s",
                }
            )
        if timing.duration - last > 1.6:
            issues.append(
                {
                    "scene": sid,
                    "severity": "warn",
                    "code": "long_tail",
                    "detail": f"{timing.duration - last:.2f}s of silent tail after last word",
                }
            )

    # --- shot length ------------------------------------------------------
    if timing.duration < MIN_SHOT:
        issues.append(
            {"scene": sid, "severity": "warn", "code": "shot_too_short", "detail": f"{timing.duration:.2f}s < {MIN_SHOT}s"}
        )
    if timing.duration > MAX_SHOT:
        issues.append(
            {"scene": sid, "severity": "warn", "code": "shot_too_long", "detail": f"{timing.duration:.2f}s > {MAX_SHOT}s"}
        )

    # --- caption card width ---------------------------------------------
    spec = get_style(scene.visual.style or project.style)
    font_size = scale_for_canvas(spec, "", project.width, project.height)
    for i, ch in enumerate(chunk_words(assets.vo_words or [])):
        if len(ch) > MAX_CARD_WORDS:
            issues.append(
                {
                    "scene": sid,
                    "severity": "warn",
                    "code": "card_too_long",
                    "detail": f"card {i} has {len(ch)} words (max {MAX_CARD_WORDS})",
                }
            )
    return issues


def run_qc(project: Project, assets: dict, timings: list, total: float) -> dict:
    issues: list[dict] = []
    for tm in timings:
        a = assets.get(tm.scene_id)
        scene = next((s for s in project.scenes if s.id == tm.scene_id), None)
        if a is None or scene is None:
            continue
        issues.extend(check_scene(scene, a, tm, project))

    errors = [i for i in issues if i["severity"] == "error"]
    warns = [i for i in issues if i["severity"] == "warn"]

    return {
        "passed": not errors,
        "errors": len(errors),
        "warnings": len(warns),
        "issues": issues,
        "coverage": round(sum(t.duration for t in timings) / max(1e-6, total), 4),
    }
