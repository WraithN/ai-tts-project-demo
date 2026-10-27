"""LLM-backed conversational edit router.

This is the piece the DTC brief marks "Extremely Important". The rule-based
router in `edit.py` is fast, deterministic and free -- and it collapses on
any phrasing its regexes did not anticipate ("give it more swagger", "the
middle drags", "make it feel like a Netflix trailer"). This module asks a
model to emit a structured `EditPlan` instead.

Design constraints, in priority order:

1. **Never make things worse.** If the LLM is unavailable, misbehaves, or
   returns a plan that would touch paths the IR does not have, we fall back
   to the rule-based plan. The user always ends up with *something* valid.
2. **The LLM proposes; the code disposes.** The model only picks from a
   closed vocabulary of intents, scene selectors and patch paths. It never
   emits code and never names arbitrary IR fields. Anything outside the
   whitelist is dropped before it reaches `apply_plan`.
3. **It must know the project.** The prompt carries the scene list (ids,
   beats, durations, current style) and the available edit vocabulary, so
   "make the proof section pop" can resolve to real scene ids rather than a
   guess.

Everything here is synchronous and side-effect free: it returns a plan.
`edit.apply_plan` is still the only thing that mutates the IR.
"""

from __future__ import annotations

import re
from typing import Any

from .edit import EditPlan, PatchOp, plan_edit
from .ir import Project
from .llm import LLMClient, LLMError, get_client

__all__ = ["plan_edit_llm", "EDIT_VOCABULARY", "EDIT_SYSTEM_PROMPT"]

# --------------------------------------------------------------------------
# Closed vocabulary -- the model may only pick from these
# --------------------------------------------------------------------------

INTENTS = (
    "pacing", "style", "cast", "replace", "interrupt",
    "music", "caption", "text", "voiceover", "transition", "unknown",
)

# Paths the model is allowed to patch. Anything else is stripped.
ALLOWED_PATHS = {
    "vo.speed", "vo.voice", "vo.text", "vo.energy",
    "visual.kind", "visual.style", "visual.motion", "visual.prompt",
    "visual.intensity",
    "caption.mode", "caption.scale_pop", "caption.color", "caption.position",
    "transition", "interrupt",
    "music.bpm", "music.volume", "music.mood",
}

# Closed value sets. The model is told these, but it drifts -- so we snap
# to the nearest allowed value (or drop) rather than letting "comic_book"
# leak into IR and blow up get_style() downstream.
ENUM_PATHS = {
    "visual.style": ("dr_vsl", "comic", "vector", "claymation", "talking_head",
                     "ugc", "kinetic", "cinematic", "song"),
    "caption.mode": ("kinetic", "karaoke", "static", "minimal", "bold"),
    "visual.motion": ("zoom_in", "zoom_out", "punch", "pan", "static", "dolly", "orbit"),
    "visual.kind": ("graphic", "figure", "product", "text", "pattern", "footage"),
    "transition": ("cut", "flash", "dissolve", "slide", "glitch", "whip"),
}

# Loose synonym map for the values models actually produce.
_ENUM_ALIASES = {
    "comic_book": "comic", "comicbook": "comic", "comic-book": "comic",
    "quick_cut": "cut", "quickcut": "cut", "hard_cut": "cut", "jump_cut": "cut",
    "pop": "kinetic", "popping": "kinetic", "pop_on": "kinetic",
    "premium_cinematic": "cinematic", "cinematic_premium": "cinematic",
    "premium": "cinematic", "luxury": "cinematic",
    "bold_pop": "bold", "highlight": "bold",
    "velociraptor": "kinetic",  # models are weird; keep the map honest
    "punch_zoom": "punch", "punchy": "punch",
    "slow_zoom": "zoom_in", "push_in": "zoom_in",
    "pull_back": "zoom_out", "slow_out": "zoom_out",
    "none": "cut", "straight": "cut", "hardcut": "cut",
}

# Scene selector strategies the model can name; resolved in Python.
SELECTORS = ("all", "beat", "time_range", "ids", "first_n", "last_n")

EDIT_VOCABULARY = {
    "intents": list(INTENTS),
    "paths": sorted(ALLOWED_PATHS),
    "selectors": list(SELECTORS),
    "beats": ["hook", "problem", "mechanism", "proof", "offer", "cta", "bridge", "payoff"],
    "styles": [
        "dr_vsl", "comic", "vector", "claymation",
        "talking_head", "ugc", "kinetic", "cinematic", "song",
    ],
    "caption_modes": ["kinetic", "karaoke", "static", "minimal", "bold"],
    "motions": ["zoom_in", "zoom_out", "punch", "pan", "static", "dolly", "orbit"],
    "transitions": ["cut", "flash", "dissolve", "slide", "glitch", "whip"],
    "visual_kinds": ["graphic", "figure", "product", "text", "pattern", "footage"],
}


def _selector_key(sel: dict[str, Any]) -> str:
    """Canonical string for cache/debug."""
    return ",".join(f"{k}={v}" for k, v in sorted(sel.items()))


def _resolve_selector(
    project: Project, sel: dict[str, Any], timings: list[Any]
) -> list[str]:
    """Turn a model-supplied selector into concrete scene ids."""
    from .edit import resolve_beat, resolve_timerange

    kind = (sel.get("kind") or "all").lower()
    scenes = project.scenes

    if kind == "ids":
        wanted = set(sel.get("ids") or [])
        return [s.id for s in scenes if s.id in wanted]
    if kind == "beat":
        beat = (sel.get("beat") or "").lower()
        return [s.id for s in scenes if s.beat == beat]
    if kind == "time_range":
        # reuse the rule-based resolver, which understands "first 10 seconds"
        phrase = sel.get("phrase") or ""
        if phrase:
            return resolve_timerange(phrase, timings)
        start = float(sel.get("start", 0.0))
        end = float(sel.get("end", 0.0))
        out = []
        for t in timings or []:
            mid = (getattr(t, "start", 0.0) + getattr(t, "duration", 0.0) / 2.0)
            if start <= mid <= end:
                out.append(getattr(t, "scene_id", "") or getattr(t, "id", ""))
        return [x for x in out if x]
    if kind == "first_n":
        return [s.id for s in scenes[: max(1, int(sel.get("n", 1)))]]
    if kind == "last_n":
        n = max(1, int(sel.get("n", 1)))
        return [s.id for s in scenes[-n:]]
    # "all" and anything unknown
    return [s.id for s in scenes]


def _sanitize_ops(raw_ops: list[Any]) -> list[PatchOp]:
    """Drop anything not on the whitelist; coerce values by path type."""
    out: list[PatchOp] = []
    for item in raw_ops or []:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        if path not in ALLOWED_PATHS:
            continue
        if "value" not in item:
            continue
        value = item["value"]
        # light coercion so a model saying "1.28" becomes a float
        if isinstance(value, str):
            if re.fullmatch(r"-?\d+", value):
                value = int(value)
            elif re.fullmatch(r"-?\d*\.\d+", value):
                value = float(value)
            elif value.lower() in {"true", "false"}:
                value = value.lower() == "true"
        if path == "vo.speed":
            try:
                value = max(0.5, min(2.0, float(value)))
            except (TypeError, ValueError):
                continue
        if path in {"music.bpm"}:
            try:
                value = max(60, min(200, int(value)))
            except (TypeError, ValueError):
                continue
        if path in {"music.volume", "caption.scale_pop", "visual.intensity", "interrupt"}:
            try:
                value = max(0.0, min(2.0, float(value)))
            except (TypeError, ValueError):
                continue
        if path in ENUM_PATHS:
            raw = str(value).strip().lower().replace(" ", "_").replace("-", "_")
            allowed = ENUM_PATHS[path]
            if raw not in allowed:
                raw = _ENUM_ALIASES.get(raw, raw)
            if raw not in allowed:
                # last resort: fuzzy containment ("comic_book_look" -> comic)
                hit = next((a for a in allowed if a in raw or raw in a), None)
                raw = hit
            if raw is None:
                continue  # drop rather than corrupt the IR
            value = raw
        if path == "vo.voice":
            value = str(value)[:64]
        if path == "vo.energy":
            # accept "high"/"low"/numeric
            if isinstance(value, str):
                lv = value.strip().lower()
                value = {"high": 1.0, "low": 0.7, "medium": 0.85, "normal": 0.9}.get(lv, value)
            try:
                value = max(0.3, min(1.5, float(value)))
            except (TypeError, ValueError):
                continue
        if path in {"visual.prompt", "vo.text"}:
            value = str(value)[:400]
        out.append(PatchOp(path, value))
    return out


EDIT_SYSTEM_PROMPT = """\
You are the edit planner inside an automated video-ad production pipeline.

You receive a project description (scene list with ids, beats, durations, and
current settings) and a natural-language edit instruction from a marketer.
You must respond with ONE JSON object describing the edit plan.

SCHEMA (all fields required):
{
  "intent": one of INTENTS,
  "selector": {"kind": one of SELECTORS, ...},
  "ops": [{"path": <allowed path>, "value": <scalar>}],
  "explanation": "one or two sentences, marketer-facing, no jargon"
}

SELECTOR shapes:
  {"kind":"all"}
  {"kind":"beat","beat":"hook"|"problem"|"mechanism"|"proof"|"offer"|"cta"|"bridge"|"payoff"}
  {"kind":"ids","ids":["scene_03","scene_04"]}
  {"kind":"first_n","n":3}  {"kind":"last_n","n":2}
  {"kind":"time_range","start":0.0,"end":10.5}   # seconds on the timeline

RULES:
- Only use paths from ALLOWED_PATHS. Never invent fields.
- Prefer the SMALLEST edit that satisfies the request. If the user asked to
  change the hook, do not touch scene 12.
- "throughout the video" / "everywhere" / "the whole thing" -> selector kind "all".
- Ambiguous target -> prefer "all" over guessing ids.
- Style changes: set visual.style and usually caption.mode together.
- Pacing changes: vo.speed plus visual.motion and/or transition.
- Music is project-level: use selector {"kind":"all"} with ops on music.* and
  an empty scene list is fine -- just set intent "music".
- Never output code, markdown fences, or commentary outside the JSON object.
"""


def _describe_project(project: Project, timings: list[Any] | None) -> str:
    lines = [
        f"PROJECT: id={project.id} style={project.style} aspect={project.aspect}",
        f"SCENES ({len(project.scenes)}):",
    ]
    tmap = {}
    for t in timings or []:
        sid = getattr(t, "scene_id", "") or getattr(t, "id", "")
        tmap[sid] = (getattr(t, "start", 0.0), getattr(t, "duration", 0.0))

    for s in project.scenes:
        start, dur = tmap.get(s.id, (0.0, 0.0))
        text = (s.vo.text if s.vo else "")[:80]
        style = getattr(s.visual, "style", "") if s.visual else ""
        motion = getattr(s.visual, "motion", "") if s.visual else ""
        lines.append(
            f"  - {s.id} | beat={s.beat} | t={start:.1f}s+{dur:.1f}s | "
            f"style={style} motion={motion} | {text!r}"
        )
    lines.append("")
    lines.append("ALLOWED_PATHS: " + ", ".join(sorted(ALLOWED_PATHS)))
    lines.append("INTENTS: " + ", ".join(INTENTS))
    lines.append("SELECTORS: " + ", ".join(SELECTORS))
    return "\n".join(lines)


def plan_edit_llm(
    project: Project,
    instruction: str,
    timings: list[Any] | None = None,
    *,
    client: LLMClient | None = None,
    prefer_llm: bool = True,
) -> EditPlan:
    """Plan an edit using the LLM, falling back to rules on any failure.

    Returns a validated EditPlan. Never raises for LLM problems -- the
    rule-based plan is the safety net, and a slightly dumber plan beats a
    crashed pipeline.
    """
    timings = timings or []

    if not prefer_llm:
        return plan_edit(project, instruction, timings)

    c = client or get_client()
    if not c.available:
        return plan_edit(project, instruction, timings)

    user = (
        f"{_describe_project(project, timings)}\n\n"
        f"INSTRUCTION: {instruction}\n\n"
        "Respond with the JSON edit plan only."
    )
    try:
        data = c.chat_json(
            [
                {"role": "system", "content": EDIT_SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ]
        )
    except (LLMError, ValueError, TypeError):
        return plan_edit(project, instruction, timings)

    if not isinstance(data, dict):
        return plan_edit(project, instruction, timings)

    intent = str(data.get("intent") or "unknown").lower()
    if intent not in INTENTS:
        intent = "unknown"

    selector = data.get("selector") or {"kind": "all"}
    if not isinstance(selector, dict):
        selector = {"kind": "all"}
    if (selector.get("kind") or "all") not in SELECTORS:
        selector = {"kind": "all"}

    scene_ids = _resolve_selector(project, selector, timings)
    ops = _sanitize_ops(data.get("ops") or [])

    # An LLM plan with no effect is worse than a rule plan that does
    # something sensible -- fall back.
    if not ops:
        return plan_edit(project, instruction, timings)

    explanation = str(data.get("explanation") or "").strip()
    if not explanation:
        explanation = f"LLM planned a {intent} edit across {len(scene_ids)} scene(s)."

    # Flag that this plan came from the model so reports can show the split.
    explanation = f"[llm:{c.cfg.model}] {explanation}"

    return EditPlan(
        instruction=instruction,
        intent=intent,
        scene_ids=scene_ids,
        ops=ops,
        explanation=explanation,
    )
