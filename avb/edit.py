"""Conversational editing.

The user types an instruction. We do NOT regenerate the video. We:

    1. route the instruction to an intent (pacing / cast / style / replace / ...)
    2. resolve "the first 10 seconds" / "at 1:20" to concrete scene ids
    3. emit a JSON patch against the SceneGraph IR
    4. hash-diff old vs new IR to find which scenes are invalidated
    5. re-render only those scenes, then re-concat

Step 3 is the load-bearing one. Editing the IR (not pixels, not an mp4)
is what makes steps 4 and 5 possible, and it is what makes the edit
reversible, diffable, and cheap.

The router below is rule-based and deterministic. An LLM-backed router
drops in behind the same interface when OPENAI_API_KEY is present.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from typing import Any

from .ir import Project, Scene

# --------------------------------------------------------------------------
# Persona -> voice id
# --------------------------------------------------------------------------

_PERSONA_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"vet\w*|veterinar\w*", re.I), "vet_female"),
    (re.compile(r"\bdoctor\b|\bdr\.?\b|physician|expert|scientist", re.I), "expert_female"),
    (re.compile(r"female|woman|she|her|mother|mom", re.I), "rachel"),
    (re.compile(r"male|man\b|he\b|him|father|dad|guy", re.I), "marcus"),
]

_PERSONA_DESC = {
    "vet_female": "a middle-aged female veterinarian",
    "expert_female": "a female medical expert",
    "expert_male": "a male medical expert",
    "rachel": "a young female creator",
    "marcus": "a warm male creator",
    "sofia": "a female lifestyle creator",
    "adam": "a neutral presenter",
}


def resolve_voice(instruction: str) -> str | None:
    for pat, vid in _PERSONA_RULES:
        if pat.search(instruction):
            return vid
    return None


def persona_desc(voice: str) -> str:
    return _PERSONA_DESC.get(voice, "a presenter")


# --------------------------------------------------------------------------
# Time range resolution
# --------------------------------------------------------------------------


# Narrative beats, in the order a direct-response script uses them. Matching
# on these is how "make the mechanism section look like a comic book" resolves
# to four scenes instead of twenty -- in DR, editors talk in beats, not
# timestamps, so a router that only understands seconds mis-scopes the edit.
BEAT_WORDS: dict[str, tuple[str, ...]] = {
    "hook": ("hook", "opening", "open", "cold open", "first impression"),
    "problem": ("problem", "pain", "struggle", "issue"),
    "agitate": ("agitate", "agitation", "twist the knife", "emotional"),
    "mechanism": ("mechanism", "science", "how it works", "ingredient", "explain", "education"),
    "proof": ("proof", "evidence", "study", "trial", "clinical", "testimonial", "results", "stats"),
    "offer": ("offer", "deal", "price", "discount", "guarantee"),
    "cta": ("cta", "call to action", "close", "closer", "outro", "button"),
}


def resolve_beat(instruction: str, project: Project) -> list[str]:
    """Map a narrative-beat reference onto scene ids."""
    low = instruction.lower()
    # longest alias first so "how it works" beats a stray "works"
    ranked = sorted(
        ((beat, alias) for beat, aliases in BEAT_WORDS.items() for alias in aliases),
        key=lambda x: -len(x[1]),
    )
    for beat, alias in ranked:
        if re.search(r"\b" + re.escape(alias) + r"\b", low):
            ids = [s.id for s in project.scenes if s.beat == beat]
            if ids:
                return ids
    return []


def resolve_targets(project: Project, instruction: str, timings: list[Any]) -> list[str]:
    """Beat names beat prose; explicit timestamps beat both.

    Order matters. "Replace the footage at 1:20 ... explaining the ingredient"
    contains both a timestamp and a mechanism alias -- the timestamp is the
    more specific instruction, so it wins.
    """
    return (
        resolve_timerange(instruction, timings)
        or resolve_beat(instruction, project)
        or [s.id for s in project.scenes]
    )


def resolve_timerange(instruction: str, timings: list[Any]) -> list[str]:
    """Map natural-language time references onto scene ids."""
    if not timings:
        return []

    low = instruction.lower()
    ids: list[str] = []

    m = re.search(r"(?:first|opening|intro)\s+(\d+)\s*(?:sec|second)s?", low)
    if m:
        limit = float(m.group(1))
        ids = [t.scene_id for t in timings if t.start < limit]
        return ids

    m = re.search(r"(\d+):(\d{2})", low)
    if m:
        at = int(m.group(1)) * 60 + int(m.group(2))
        hit = [t for t in timings if t.start <= at < t.start + t.duration]
        if hit:
            return [hit[0].scene_id]
        return [min(timings, key=lambda t: abs(t.start - at)).scene_id]

    m = re.search(r"(?:at|around)\s+(\d+)\s*(?:sec|second)s?", low)
    if m:
        at = float(m.group(1))
        hit = [t for t in timings if t.start <= at < t.start + t.duration]
        return [hit[0].scene_id] if hit else []

    if re.search(r"\bintro\b|\bopening\b|\bbeginning\b|\bstart\b", low):
        return [t.scene_id for t in timings[: max(1, len(timings) // 5)]]
    if re.search(r"\boutro\b|\bending\b|\bfinal\b|\blast\b", low):
        return [t.scene_id for t in timings[-max(1, len(timings) // 5) :]]
    if re.search(r"\bthroughout\b|\bentire\b|\bwhole\b|\beverywhere\b|\ball over\b", low):
        return [t.scene_id for t in timings]

    return ids


# --------------------------------------------------------------------------
# Intent routing
# --------------------------------------------------------------------------

_STYLE_WORDS = {
    "comic": "comic",
    "comic book": "comic",
    "vector": "vector",
    "flat": "vector",
    "claymation": "claymation",
    "clay": "claymation",
    "stop motion": "claymation",
    "animation": "vector",
    "ugc": "ugc",
    "talking head": "talking_head",
    "presenter": "talking_head",
    "spokesperson": "talking_head",
    "vsl": "dr_vsl",
    "direct response": "dr_vsl",
}


def detect_style(instruction: str) -> str | None:
    low = instruction.lower()
    for word, sid in _STYLE_WORDS.items():
        if word in low:
            return sid
    return None


@dataclass
class PatchOp:
    path: str
    value: Any


@dataclass
class EditPlan:
    instruction: str
    intent: str
    scene_ids: list[str]
    ops: list[PatchOp] = field(default_factory=list)
    explanation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "instruction": self.instruction,
            "intent": self.intent,
            "scene_ids": self.scene_ids,
            "ops": [{"path": o.path, "value": o.value} for o in self.ops],
            "explanation": self.explanation,
        }


def plan_edit(project: Project, instruction: str, timings: list[Any] | None = None) -> EditPlan:
    """Turn natural language into a concrete IR patch."""
    timings = timings or []
    low = instruction.lower()

    # --- cast / spokesperson swap --------------------------------------
    if re.search(r"spokesperson|presenter|host|actor|creator|avatar|narrator", low):
        vid = resolve_voice(instruction)
        if vid:
            ids = resolve_targets(project, instruction, timings)
            return EditPlan(
                instruction=instruction,
                intent="cast",
                scene_ids=ids,
                ops=[
                    PatchOp("vo.voice", vid),
                    PatchOp("visual.kind", "figure"),
                    PatchOp("visual.prompt", f"presenter shot: {persona_desc(vid)}, direct to camera"),
                ],
                explanation=(
                    f"Recast {len(ids)} scene(s) as {persona_desc(vid)}. Swaps the voice model and "
                    "forces a presenter-shot visual; captions, music and timing are cache hits."
                ),
            )

    # --- pacing -----------------------------------------------------------
    m = re.search(
        r"(faster|quicker|punchier|tighter|more aggressive|more energy|"
        r"spice|spice up|more engaging|energetic|punch it|snappier)",
        low,
    )
    m2 = re.search(r"(slower|slow down|more relaxed|calmer|breathe)", low)
    if m or m2:
        factor = 1.0 / 1.28 if m else 1.25
        ids = resolve_targets(project, instruction, timings)
        return EditPlan(
            instruction=instruction,
            intent="pacing",
            scene_ids=ids,
            ops=[
                PatchOp("vo.speed", factor),
                PatchOp("caption.scale_pop", 1.35 if m else 0.8),
                PatchOp("visual.motion", "punch" if m else "zoom_out"),
                PatchOp("transition", "flash" if m else "dissolve"),
            ],
            explanation=(
                f"{'Tighten' if m else 'Loosen'} pacing on {len(ids)} scene(s): voiceover rate x{factor:.2f}, "
                "motion and transitions retuned. Voiceover re-synthesizes; visuals are reused."
            ),
        )

    # --- style swap -----------------------------------------------------
    sid = detect_style(instruction)
    if sid and re.search(r"(look|style|make|render|turn|as a|into)", low):
        ids = resolve_targets(project, instruction, timings)
        return EditPlan(
            instruction=instruction,
            intent="style",
            scene_ids=ids,
            ops=[PatchOp("visual.style", sid), PatchOp("caption.mode", get_style_defaults(sid))],
            explanation=f"Switch {len(ids)} scene(s) to the {sid} look. Visuals re-render; voiceover is reused.",
        )

    # --- replace footage -------------------------------------------------
    if re.search(r"replace|swap|change the footage|instead", low):
        ids = resolve_timerange(instruction, timings) or resolve_beat(instruction, project)
        if not ids:
            ids = [s.id for s in project.scenes]
        return EditPlan(
            instruction=instruction,
            intent="replace",
            scene_ids=ids,
            ops=[PatchOp("visual.kind", "graphic"), PatchOp("visual.motion", "zoom_in")],
            explanation=f"Swap visuals in {len(ids)} scene(s). Voiceover and captions unaffected.",
        )

    # --- pattern interrupts ----------------------------------------------
    if re.search(r"pattern interrupt|more cuts|punch it up|more dynamic|more chaos", low):
        ids = resolve_targets(project, instruction, timings)
        return EditPlan(
            instruction=instruction,
            intent="interrupt",
            scene_ids=ids,
            ops=[PatchOp("interrupt", 0.9), PatchOp("transition", "flash")],
            explanation=f"Raise interrupt strength across {len(ids)} scene(s). Pure parameter change -- no regeneration, seconds to re-render.",
        )

    # --- music / sound design --------------------------------------------
    # Project-level, not per-scene: the bed spans the whole timeline.
    if re.search(r"music|soundtrack|bed|beat|bpm|tempo", low):
        ops: list[PatchOp] = []
        if re.search(r"upbeat|energetic|hype|pump|faster|driving|intense", low):
            ops.append(PatchOp("music.bpm", 138))
            ops.append(PatchOp("music.volume", 0.34))
        elif re.search(r"calm|soft|mellow|ambient|relaxing|slow", low):
            ops.append(PatchOp("music.bpm", 82))
            ops.append(PatchOp("music.volume", 0.24))
        if re.search(r"quiet|lower|softer|duck|too loud", low):
            ops.append(PatchOp("music.volume", 0.16))
        elif re.search(r"louder|punchier|more present", low):
            ops.append(PatchOp("music.volume", 0.40))
        if ops:
            return EditPlan(
                instruction=instruction,
                intent="music",
                scene_ids=[],
                ops=ops,
                explanation=(
                    "Retune the music bed (project-level): "
                    + ", ".join(f"{o.path} -> {o.value}" for o in ops)
                    + ". The bed is re-synthesized once; no video re-render."
                ),
            )

    # --- caption styling ----------------------------------------------------
    if re.search(r"caption|subtitle|text|typography", low):
        ids = resolve_targets(project, instruction, timings)
        mode = "karaoke" if "karaoke" in low else ("static" if "static" in low else "kinetic")
        return EditPlan(
            instruction=instruction,
            intent="caption",
            scene_ids=ids,
            ops=[PatchOp("caption.mode", mode)],
            explanation=f"Caption mode -> {mode} on {len(ids)} scene(s). No asset regeneration at all.",
        )

    return EditPlan(
        instruction=instruction,
        intent="unknown",
        scene_ids=[],
        ops=[],
        explanation="No rule matched this instruction. Wire up an LLM router (OPENAI_API_KEY) to handle arbitrary phrasing.",
    )


def get_style_defaults(style_id: str) -> str:
    from .styles import get_style

    return get_style(style_id).caption_mode


# --------------------------------------------------------------------------
# Patch application
# --------------------------------------------------------------------------


def _set_path(scene: Scene, path: str, value: Any) -> None:
    if "." in path:
        head, tail = path.split(".", 1)
        obj = getattr(scene, head)
        setattr(obj, tail, value)
    else:
        setattr(scene, path, value)


def apply_plan(project: Project, plan: EditPlan) -> Project:
    """Return a NEW project with the patch applied.

    We never mutate in place -- the old version stays on disk so the edit
    is diffable and reversible. That is what makes a version tree cheap.
    """
    new = copy.deepcopy(project)

    # Project-level ops (music bed, brand) apply once, outside the scene loop.
    scene_ops = [op for op in plan.ops if not op.path.startswith("music.")]
    for op in plan.ops:
        if op.path.startswith("music."):
            setattr(new.music, op.path.split(".", 1)[1], op.value)

    targets = set(plan.scene_ids)
    for scene in new.scenes:
        if targets and scene.id not in targets:
            continue
        for op in scene_ops:
            if op.path == "vo.speed":
                scene.vo.speed = round(scene.vo.speed * float(op.value), 4)
            elif op.path == "caption.scale_pop":
                scene.caption.scale_pop = round(scene.caption.scale_pop * float(op.value), 4)
            elif op.path == "interrupt":
                scene.interrupt = round(min(1.0, max(scene.interrupt, float(op.value))), 4)
            elif op.path == "visual.style":
                from .styles import get_style, motion_for

                scene.visual.style = op.value
                spec = get_style(op.value)
                scene.visual.motion = spec.default_motion
                scene.visual.palette = spec.palette
                scene.caption.mode = spec.caption_mode
                scene.caption.position = spec.caption_position
                scene.caption.highlight = spec.highlight
                scene.caption.scale_pop = spec.font_scale * 2.2
                scene.transition = spec.transition
            elif op.path == "caption.mode":
                scene.caption.mode = op.value
            else:
                _set_path(scene, op.path, op.value)
    return new


# --------------------------------------------------------------------------
# Invalidation
# --------------------------------------------------------------------------


def invalidate(before: Project, after: Project) -> dict[str, Any]:
    """Which scenes must be rebuilt, and what that costs vs. a full rebuild."""
    return before.diff(after)
