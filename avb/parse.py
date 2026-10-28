"""Script -> SceneGraph IR.

Two paths:

  parse_script()        heuristic, deterministic, zero network. Always works.
  parse_script_llm()    OpenAI-compatible chat completion, used when
                        OPENAI_API_KEY / OPENAI_BASE_URL are present. Falls
                        back to the heuristic path on any failure.

Both return the same IR, so everything downstream is identical. That is the
point of having an IR at all: the creative-intent layer is swappable without
touching the asset or render layers.
"""

from __future__ import annotations

import os
import re
from dataclasses import replace

from .ir import Beat, CaptionStyle, Project, Scene, Sfx, Visual, Voiceover
from .styles import get_style, motion_for, visual_kind_for

# --------------------------------------------------------------------------
# Beat classification
# --------------------------------------------------------------------------

_BEAT_RULES: list[tuple[Beat, tuple[str, ...]]] = [
    (
        "mechanism",
        (
            "because", "works by", "how it works", "mechanism", "ingredient",
            "compound", "clinically", "molecule", "molecular", "cells", "cell",
            "enzyme", "receptor", "pathway", "metabolism", "metabolic",
            "absorption", "bioavailable", "bioavailability", "science",
            "scientific", "formula", "studies show", "the reason",
        ),
    ),
    (
        "proof",
        (
            "study", "studies", "trial", "trials", "research", "researchers",
            "patients", "participants", "results", "percent", "%", "weeks",
            "week", "days", "day", "published", "tested", "double-blind",
            "placebo", "clinical", "volunteers", "measured", "data",
        ),
    ),
    (
        "offer",
        (
            "today", "discount", "guarantee", "risk-free", "order", "only $",
            "save", "bundle", "free shipping", "limited", "special", "price",
            "bottle", "supply", "try it", "money back",
        ),
    ),
    (
        "cta",
        (
            "click", "tap", "below", "link", "get started", "buy now",
            "shop now", "get yours", "button", "right now", "start today",
        ),
    ),
    (
        "agitate",
        (
            "worse", "keeps", "cycle", "failed", "nothing works", "years",
            "doctor said", "every morning", "every night", "again", "still",
            "another", "wasted",
        ),
    ),
    (
        "problem",
        (
            "tired", "exhausted", "struggle", "struggling", "frustrated",
            "frustrating", "can't", "cannot", "won't", "tried everything",
            "stubborn", "bloated", "bloat", "sluggish", "crash", "fog",
        ),
    ),
]

_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "then", "that", "this",
    "these", "those", "is", "are", "was", "were", "be", "been", "being",
    "do", "does", "did", "have", "has", "had", "you", "your", "yours",
    "it", "its", "they", "them", "their", "we", "our", "us", "i", "me",
    "my", "to", "of", "in", "on", "at", "for", "with", "from", "by",
    "as", "so", "not", "no", "just", "about", "into", "over", "when",
    "what", "why", "how", "all", "any", "can", "will", "would", "could",
    "there", "here", "up", "out", "more", "most", "other", "some", "such",
    "only", "own", "same", "than", "too", "very", "get", "got", "make",
    "made", "take", "took", "know", "knew", "think", "want", "need",
    "one", "two", "three", "every", "each", "because", "been", "being",
}


def classify_beat(text: str, position: int, total: int) -> Beat:
    """Tag a line with a direct-response beat.

    Order matters: hook/cta are positional and win over keywords, because
    "click below" in the middle of a script is still just copy, not a CTA.
    """
    low = text.lower()

    if position == 0:
        return "hook"
    if position == total - 1:
        return "cta"

    for beat, keys in _BEAT_RULES:
        if any(k in low for k in keys):
            return beat

    if position < total * 0.35:
        return "problem"
    if position < total * 0.75:
        return "agitate"
    return "offer"


# --------------------------------------------------------------------------
# Timing
# --------------------------------------------------------------------------

_VOWELS = "aeiouy"


def count_syllables(word: str) -> int:
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        return 1
    n, prev = 0, False
    for ch in w:
        v = ch in _VOWELS
        if v and not prev:
            n += 1
        prev = v
    if w.endswith("e") and n > 1:
        n -= 1
    return max(1, n)


def estimate_duration(text: str, wpm: float = 168.0) -> float:
    """Rough VO duration in seconds.

    Syllable-weighted: 'metabolism' costs more time than 'it'. Good enough
    for shot planning -- the real TTS returns exact timings and we use those
    when available (see assets/voice.py).
    """
    words = re.findall(r"[A-Za-z0-9'$%]+", text)
    if not words:
        return 0.85
    syllables = sum(count_syllables(w) for w in words)
    # Each word pays a fixed onset cost; each syllable adds linear time.
    # Calibrated against the formant synth: ~5.3 s for a 16-word, 23-syllable
    # line at 168 wpm, which is the natural rate for DR voiceover.
    base = len(words) * 0.062 + syllables * 0.190
    pauses = text.count(",") * 0.16 + text.count(".") * 0.26 + text.count("?") * 0.26
    return round(max(0.85, (base + pauses) * (168.0 / wpm)), 3)


# --------------------------------------------------------------------------
# Visual prompt synthesis
# --------------------------------------------------------------------------

_BEAT_PROMPT: dict[Beat, str] = {
    "hook": "dramatic high-contrast close-up, shallow depth of field, {kw}",
    "problem": "moody documentary shot of a person looking tired, {kw}",
    "agitate": "restless handheld shot, frustrated person, {kw}",
    "mechanism": "clean 3D scientific animation diagram, glowing molecular structures, {kw}",
    "proof": "data visualisation, clinical trial results chart, {kw}",
    "offer": "premium product bottle on reflective surface with price badge, {kw}",
    "cta": "bold minimal product shot, {kw}",
}

# Cinematography tables. These are what turn a flat copy-split into an actual
# shot list: every beat gets a default camera move, vantage point, and the
# on-screen elements the renderer should put in frame.
_BEAT_SHOT: dict[Beat, str] = {
    "hook": "extreme close-up",
    "problem": "medium shot",
    "agitate": "handheld tracking shot",
    "mechanism": "macro insert",
    "proof": "graphic overlay",
    "offer": "product hero shot",
    "cta": "wide hero reveal",
}

_BEAT_ANGLE: dict[Beat, str] = {
    "hook": "eye-level, slight dutch tilt",
    "problem": "eye-level",
    "agitate": "low-angle handheld",
    "mechanism": "top-down macro",
    "proof": "straight-on",
    "offer": "slight low-angle",
    "cta": "eye-level",
}

_BEAT_SUBJECT: dict[Beat, str] = {
    "hook": "the product in hand",
    "problem": "a tired person at home",
    "agitate": "a frustrated person mid-routine",
    "mechanism": "the formula / active ingredient",
    "proof": "the results on screen",
    "offer": "the product on a clean surface",
    "cta": "the product, branded",
}

# Per-style look notes woven into every prompt so the model knows the
# rendering language even when the user just picked a style name.
_STYLE_NOTE: dict[str, str] = {
    "dr_vsl": "high-contrast commercial look, shallow depth of field",
    "cinematic": "cinematic lighting, film grain, anamorphic flare",
    "comic": "bold comic-book shading, halftone, thick outlines",
    "vector": "flat vector illustration, clean shapes, no shadows",
    "claymation": "claymation texture, soft pastel lighting",
    "ugc": "selfie camera, natural window light, slightly shaky",
    "talking_head": "studio portrait, soft key light, shallow bokeh",
}


def keywords(text: str, limit: int = 4) -> str:
    return ", ".join(keywords_list(text, limit)) if keywords_list(text, limit) else "abstract texture"


def keywords_list(text: str, limit: int = 4) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z\-']+", text)
    seen: list[str] = []
    for w in words:
        lw = w.lower()
        if lw in _STOPWORDS or len(lw) < 4 or lw in seen:
            continue
        seen.append(lw)
        if len(seen) >= limit:
            break
    return seen


def build_visual_prompt(text: str, beat: Beat, style: str) -> str:
    return _BEAT_PROMPT.get(beat, "{kw}").format(kw=keywords(text))


def cinematography_for(beat: Beat, style: str, text: str) -> tuple[str, str, list[str]]:
    """Default shot / angle / elements for a beat, before any LLM override."""
    shot = _BEAT_SHOT.get(beat, "medium shot")
    angle = _BEAT_ANGLE.get(beat, "eye-level")
    kw = keywords_list(text, limit=3)
    elements = [_BEAT_SUBJECT.get(beat, "the product"), *kw]
    return shot, angle, elements


def compose_visual_prompt(
    text: str, beat: Beat, style: str, shot: str = "", angle: str = "", elements: list[str] | None = None
) -> str:
    """The full image/video-generation prompt.

    Composed from the cinematography fields so they are never lost, plus a
    per-style look note. When an LLM supplies a complete `visual` string we
    keep that instead (see _project_from_shots).
    """
    shot = shot or _BEAT_SHOT.get(beat, "medium shot")
    angle = angle or _BEAT_ANGLE.get(beat, "eye-level")
    el = ", ".join(elements) if elements else keywords(text)
    look = _STYLE_NOTE.get(style, "")
    kw = keywords(text)
    parts = [shot, angle, el]
    if look:
        parts.append(look)
    parts.append(kw)
    return ", ".join(p for p in parts if p)


# --------------------------------------------------------------------------
# Scene construction
# --------------------------------------------------------------------------

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")
_MAX_SHOT = 3.2


def split_sentences(script: str) -> list[str]:
    script = script.strip()
    if not script:
        return []
    parts: list[str] = []
    for block in script.split("\n"):
        block = block.strip()
        if not block:
            continue
        # Drop stage directions like [MUSIC SWELLS] or (beat)
        block = re.sub(r"[\[\(][^\])]*[\]\)]", "", block).strip()
        if not block:
            continue
        parts.extend(s.strip() for s in _SENT_SPLIT.split(block) if s.strip())
    return parts


def _pack_lines(lines: list[str], wpm: float) -> list[list[str]]:
    """Group consecutive lines into shots of roughly target length.

    Long sentences get split at commas so no single shot drags.
    """
    units: list[str] = []
    for ln in lines:
        if estimate_duration(ln, wpm) > _MAX_SHOT and "," in ln:
            units.extend(c.strip() for c in ln.split(",") if c.strip())
        else:
            units.append(ln)

    groups: list[list[str]] = []
    cur: list[str] = []
    cur_len = 0.0
    for u in units:
        d = estimate_duration(u, wpm)
        if cur and cur_len + d > _MAX_SHOT:
            groups.append(cur)
            cur, cur_len = [u], d
        else:
            cur.append(u)
            cur_len += d
    if cur:
        groups.append(cur)
    return groups


def _sfx_for(beat: Beat, interrupt: float) -> list[Sfx]:
    if interrupt >= 0.6:
        return [Sfx("impact", 0.0), Sfx("whoosh", 0.05)]
    if beat in ("mechanism", "proof"):
        return [Sfx("riser", 0.0)]
    if beat == "cta":
        return [Sfx("pop", 0.0)]
    return []


def parse_script(
    script: str,
    *,
    project_id: str = "proj",
    name: str = "untitled",
    style: str = "dr_vsl",
    aspect: Aspect = "9:16",
    fps: int = 30,
    wpm: float = 168.0,
) -> Project:
    """Turn a plain-text ad script into a SceneGraph IR."""
    project = Project(id=project_id, name=name, script=script, style=style, aspect=aspect, fps=fps)

    lines = split_sentences(script)
    if not lines:
        return project

    groups = _pack_lines(lines, wpm)
    spec = get_style(style)

    scenes: list[Scene] = []
    for i, grp in enumerate(groups):
        text = " ".join(grp)
        beat = classify_beat(text, i, len(groups))

        # Hooks and proof points punch harder; explanations breathe.
        interrupt = min(1.0, spec.interrupt_bias + (0.2 if beat == "hook" else 0.0))
        energy = 1.0 if beat in ("hook", "cta", "offer") else 0.78
        shot, angle, elements = cinematography_for(beat, style, text)

        scenes.append(
            Scene(
                id=f"s{i+1:03d}",
                index=i,
                beat=beat,
                vo=Voiceover(text=text, voice="adam", speed=1.0, energy=energy),
                visual=Visual(
                    prompt=compose_visual_prompt(text, beat, style, shot, angle, elements),
                    kind=visual_kind_for(style, beat),
                    motion=motion_for(style, beat),
                    style=style,
                    seed=(hash(text) & 0xFFFF),
                    palette=spec.palette,
                    shot=shot,
                    angle=angle,
                    elements=elements,
                ),
                caption=CaptionStyle(
                    mode=spec.caption_mode,
                    position=spec.caption_position,
                    highlight=spec.highlight,
                    scale_pop=spec.font_scale * 2.2,
                ),
                sfx=_sfx_for(beat, interrupt),
                transition=spec.transition if interrupt >= 0.5 else "cut",
                interrupt=interrupt,
                duration=None,  # derived from VO by the pipeline
            )
        )

    project.scenes = scenes
    return project


# --------------------------------------------------------------------------
# LLM path
# --------------------------------------------------------------------------

_LLM_SYSTEM = """You are a direct-response video director. Convert an ad script into a shot list.

Return ONLY a JSON array. One object per shot:
  {"text": "<the exact spoken words for this shot, 4-18 words>",
   "beat": "hook|problem|agitate|mechanism|proof|offer|cta",
   "shot": "<camera framing, e.g. 'close-up', 'wide tracking', 'over-the-shoulder'>",
   "angle": "<camera vantage, e.g. 'eye-level', 'low-angle', 'top-down', 'dutch'>",
   "elements": ["<subject in frame>", "<key prop>", "..."],
   "visual": "<a concrete image/video generation prompt, 12-25 words, weaving shot+angle+elements>"}

Rules:
- Preserve the script's wording. Split only, never rewrite.
- A shot should be 1.5-3 seconds of speech. Never exceed 18 words of speech.
- Give every shot a distinct shot and angle, not the same framing each time.
- elements is a list of 2-4 concrete, filmable things that must appear in frame.
- The visual prompt must describe a specific, filmable image that an AI video model can render."""


def _llm_call(script: str, model: str, base_url: str, api_key: str) -> str:
    import json as _json

    import requests

    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": _LLM_SYSTEM},
            {"role": "user", "content": script[:8000]},
        ],
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    r = requests.post(url, json=payload, headers=headers, timeout=120)
    r.raise_for_status()
    data = r.json()
    return data["choices"][0]["message"]["content"]


def _extract_json_array(raw: str) -> list[dict]:
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?", "", raw).strip()
    raw = re.sub(r"```$", "", raw).strip()
    start = raw.find("[")
    end = raw.rfind("]")
    if start < 0 or end < 0:
        raise ValueError("no JSON array in LLM output")
    import json as _json

    return _json.loads(raw[start : end + 1])


def parse_script_llm(
    script: str,
    *,
    model: str | None = None,
    base_url: str | None = None,
    **kwargs,
) -> Project:
    """LLM-backed parser. Falls back to the heuristic parser on any failure.

    Uses the shared OpenAI-compatible client (DeepSeek by default -- see
    `[llm]` in avb.toml). The old OPENAI_API_KEY-only path is still honoured
    when no config is present, so existing env setups keep working.
    """
    from .llm import LLMError, get_client
    from .config import load_config

    # Build from the layered config so .env / avb.toml are honoured; allow
    # per-call overrides on top.
    llm_cfg = load_config().llm
    if model:
        llm_cfg.model = model
    if base_url:
        llm_cfg.base_url = base_url
    client = get_client(llm_cfg)

    # Legacy fallback: bare OPENAI_API_KEY with no config file.
    if not client.available and os.environ.get("OPENAI_API_KEY"):
        try:
            raw = _llm_call(
                script,
                model or os.environ.get("AVB_LLM_MODEL", "gpt-4o-mini"),
                base_url or os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
                os.environ["OPENAI_API_KEY"],
            )
            shots = _extract_json_array(raw)
        except Exception:
            return parse_script(script, **kwargs)
        return _project_from_shots(script, shots, **kwargs)

    if not client.available:
        return parse_script(script, **kwargs)

    try:
        data = client.chat_json(
            [
                {"role": "system", "content": _LLM_SYSTEM},
                {"role": "user", "content": script[:8000]},
            ]
        )
        shots = data if isinstance(data, list) else data.get("shots") or data.get("scenes") or []
        if not isinstance(shots, list):
            raise ValueError("LLM did not return a shot list")
    except (LLMError, ValueError, TypeError):
        return parse_script(script, **kwargs)

    return _project_from_shots(script, shots, **kwargs)


def _project_from_shots(script: str, shots: list, **kwargs) -> Project:
    """Assemble a Project from a list of shot dicts (LLM or heuristic)."""
    kwargs.setdefault("style", "dr_vsl")
    project = Project(
        id=kwargs.get("project_id", "proj"),
        name=kwargs.get("name", "untitled"),
        script=script,
        style=kwargs["style"],
        aspect=kwargs.get("aspect", "9:16"),
        fps=kwargs.get("fps", 30),
    )
    spec = get_style(kwargs["style"])

    scenes: list[Scene] = []
    for i, sh in enumerate(shots):
        if not isinstance(sh, dict):
            continue
        text = str(sh.get("text", "")).strip()
        if not text:
            continue
        beat = sh.get("beat", "problem")
        if beat not in ("hook", "problem", "agitate", "mechanism", "proof", "offer", "cta"):
            beat = "problem"
        prompt = str(sh.get("visual") or "").strip() or build_visual_prompt(text, beat, kwargs["style"])
        shot = str(sh.get("shot") or "").strip() or _BEAT_SHOT.get(beat, "medium shot")
        angle = str(sh.get("angle") or "").strip() or _BEAT_ANGLE.get(beat, "eye-level")
        raw_elements = sh.get("elements") or []
        elements = [str(e).strip() for e in raw_elements if str(e).strip()] or None
        if not prompt or prompt == build_visual_prompt(text, beat, kwargs["style"]):
            prompt = compose_visual_prompt(text, beat, kwargs["style"], shot, angle, elements)
        interrupt = min(1.0, spec.interrupt_bias + (0.2 if beat == "hook" else 0.0))
        scenes.append(
            Scene(
                id=f"s{i+1:03d}",
                index=len(scenes),
                beat=beat,
                vo=Voiceover(text=text, voice="adam", speed=1.0, energy=1.0 if beat in ("hook", "cta", "offer") else 0.78),
                visual=Visual(
                    prompt=prompt,
                    kind=visual_kind_for(kwargs["style"], beat),
                    motion=motion_for(kwargs["style"], beat),
                    style=kwargs["style"],
                    seed=(hash(text) & 0xFFFF),
                    palette=spec.palette,
                    shot=shot,
                    angle=angle,
                    elements=elements,
                ),
                caption=CaptionStyle(
                    mode=spec.caption_mode,
                    position=spec.caption_position,
                    highlight=spec.highlight,
                    scale_pop=spec.font_scale * 2.2,
                ),
                sfx=_sfx_for(beat, interrupt),
                transition=spec.transition if interrupt >= 0.5 else "cut",
                interrupt=interrupt,
            )
        )
    project.scenes = scenes
    return project
