"""Brief -> multiple ad script variants.

This is the *top* of the creative funnel, above the parser. Where
``parse_script`` turns a single finished script into shots, this module turns
a product brief (name + selling points + style) into several *competing*
script concepts the user can pick between and edit.

Four frameworks are offered. They are intentionally different plays, not
four flavours of the same line:

  pain_solution   Problem-Agitate-Solution. The classic DR hook: name the
                  pain, make it worse, reveal the mechanism, drop the offer.
  grass_planting  Soft lifestyle recommendation. "I've been using this for
                  a month and..." — warm, never salesy, UGC energy.
  review_proof    Demonstration / before-after with proof and data. Builds
                  credibility for a claim-driven product.
  story_drama     A 15-30s narrative with a character and a payoff beat.

An LLM produces the four when configured; a deterministic template path
covers the offline / no-key case so the feature always works.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field

# --------------------------------------------------------------------------
# Frameworks
# --------------------------------------------------------------------------

SCRIPT_TYPES: list[dict] = [
    {
        "key": "pain_solution",
        "label": "Pain → Solution",
        "accent": "#FF3B30",
        "desc": "Open on a relatable pain point, agitate it, reveal the mechanism, close with the offer + CTA.",
    },
    {
        "key": "grass_planting",
        "label": "Grass-planting / Soft sell",
        "accent": "#A8FF60",
        "desc": "A warm 'I've been using this' recommendation. Lifestyle, never salesy, UGC energy.",
    },
    {
        "key": "review_proof",
        "label": "Review / Proof",
        "accent": "#4CC9F0",
        "desc": "A demonstration or before-after with proof and data — for claim-driven products.",
    },
    {
        "key": "story_drama",
        "label": "Story / Drama",
        "accent": "#FFD400",
        "desc": "A tiny 15-30s narrative with a character and a payoff moment.",
    },
]

SCRIPT_TYPE_KEYS = [t["key"] for t in SCRIPT_TYPES]


@dataclass
class ScriptDraft:
    """One generated ad script."""

    type: str
    title: str
    narration: str  # full, editable voiceover (one sentence per line)
    rationale: str = ""
    outline: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------
# Brief sanitisation
# --------------------------------------------------------------------------


def _clean_points(points: list[str] | str | None) -> list[str]:
    if not points:
        return []
    if isinstance(points, str):
        points = points.splitlines()
    out: list[str] = []
    for p in points:
        p = (p or "").strip().lstrip("•-*0123456789. ").strip()
        if p:
            out.append(p)
    return out[:8]


def _assemble_brief(product: str, points: list[str], style: str,
                    audience: str | None, tone: str | None) -> str:
    lines = [f"Product: {product or 'a DTC product'}"]
    if points:
        lines.append("Selling points:")
        lines += [f"  - {p}" for p in points]
    if style:
        lines.append(f"Visual style: {style}")
    if audience:
        lines.append(f"Target audience: {audience}")
    if tone:
        lines.append(f"Tone: {tone}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# LLM path
# --------------------------------------------------------------------------

_SYSTEM = """You are a senior short-form video ad copywriter for DTC brands.
Given a product brief, write FOUR complete ad scripts, one in each framework:

- pain_solution: open on a relatable pain point, agitate it, reveal the
  mechanism, close with the offer + CTA.
- grass_planting: a soft, lifestyle "I've been using this" recommendation;
  warm and relatable, never salesy, UGC energy.
- review_proof: a demonstration / before-after review with proof and data;
  build credibility.
- story_drama: a tiny 15-30s narrative with a character and a payoff moment.

Rules:
- Each script is a self-contained voiceover: 8-14 short sentences, ONE idea
  per line (separate lines with \\n).
- Match the language of the brief; default to English if ambiguous.
- Weave the product name and the listed selling points in naturally.
- Write FOR the target audience: open inside their real situation or pain,
  use the words they would use, and make the payoff matter to them. Do not
  mention the audience label verbatim if it reads unnatural.
- Hold the requested tone across every line (e.g. reassuring stays calm and
  confident; playful gets light, punchy phrasing) without breaking the
  framework's structure.
- Every line must be speakable in under ~3 seconds.
- Respect the requested visual style in tone (cinematic = restrained,
  dr_vsl = punchy, comic = playful).

Return ONLY JSON of the form:
{"scripts":[
  {"type":"pain_solution","title":"...","narration":"line1\\nline2\\n...",
   "rationale":"one sentence why this framework fits the product"},
  {"type":"grass_planting", ...},
  {"type":"review_proof", ...},
  {"type":"story_drama", ...}
]}"""


def generate_scripts(
    *,
    product: str = "",
    selling_points: list[str] | str | None = None,
    style: str = "",
    audience: str | None = None,
    tone: str | None = None,
    use_llm: bool = True,
) -> list[ScriptDraft]:
    """Return four script concepts (always 4, in SCRIPT_TYPE_KEYS order)."""
    points = _clean_points(selling_points)
    brief = _assemble_brief(product, points, style, audience, tone)

    if use_llm:
        try:
            drafts = _generate_llm(brief)
            if drafts:
                return _fill_order(drafts)
        except Exception:
            pass
    return _generate_heuristic(product, points, style, audience=audience, tone=tone)


def _generate_llm(brief: str) -> list[ScriptDraft]:
    from .llm import LLMError, get_client
    from .config import load_config

    cfg = load_config().llm
    client = get_client(cfg)
    if not client.available:
        raise LLMError("LLM not configured")

    data = client.chat_json(
        [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": brief},
        ],
        temperature=0.8,
        max_tokens=2400,
    )
    if not isinstance(data, dict):
        data = {"scripts": data}
    raw = data.get("scripts") or []
    if not isinstance(raw, list):
        raise ValueError("LLM did not return a scripts array")

    out: list[ScriptDraft] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        t = item.get("type", "")
        narration = str(item.get("narration", "")).strip()
        if not narration:
            continue
        out.append(
            ScriptDraft(
                type=t,
                title=str(item.get("title", "")).strip(),
                narration=narration,
                rationale=str(item.get("rationale", "")).strip(),
            )
        )
    if not out:
        raise ValueError("LLM returned no usable scripts")
    return out


def _fill_order(drafts: list[ScriptDraft]) -> list[ScriptDraft]:
    """Guarantee all four frameworks are present and in canonical order."""
    by_type = {d.type: d for d in drafts}
    ordered: list[ScriptDraft] = []
    for key in SCRIPT_TYPE_KEYS:
        if key in by_type:
            ordered.append(by_type[key])
        else:
            ordered.append(ScriptDraft(type=key, title=key, narration=""))
    return ordered


# --------------------------------------------------------------------------
# Heuristic path (no LLM, always works)
# --------------------------------------------------------------------------

def _bullet(points: list[str]) -> str:
    return "; ".join(points) if points else "its key benefit"


def _audience_line(audience: str | None) -> str:
    """A hook line that names who this is for, or "" when unspecified.

    Kept generic on purpose: it slots into any framework without assuming
    a product category.
    """
    a = (audience or "").strip()
    if not a:
        return ""
    return f"If you're {a}, this one's for you."


def _generate_heuristic(product: str, points: list[str], style: str,
                        audience: str | None = None, tone: str | None = None) -> list[ScriptDraft]:
    p = (product or "this product").strip()
    feats = _bullet(points)
    sp = points[0] if points else "the difference you can feel"
    # The offline templates are fixed English copy, so "tone" can only steer
    # small surface choices; the audience line is the one place a brief-only
    # path can visibly personalise the script.
    aud = _audience_line(audience)

    def _aud(suffix: str) -> str:
        return f"{aud}\n{suffix}" if aud else suffix

    return [
        ScriptDraft(
            type="pain_solution",
            title="Pain → Solution",
            narration=(
                _aud(
                f"Still dealing with the same problem every single day?\n"
                f"Most solutions just mask it — they never fix what's underneath.\n"
                f"That's exactly why {p} exists.\n"
                f"It works by {sp}.\n"
                f"No complicated routine, no wasted money.\n"
                f"Just {feats}.\n"
                f"Thousands have already made the switch.\n"
                f"Try {p} today — and feel the difference this week.\n"
                f"Tap below before the launch price disappears.")
            ),
            rationale="Names the pain, reveals the mechanism, closes with the offer.",
        ),
        ScriptDraft(
            type="grass_planting",
            title="Grass-planting / Soft sell",
            narration=(
                _aud(
                f"Okay, I have to tell you about {p}.\n"
                f"I've been using it for a while now and it's quietly become part of my routine.\n"
                f"What I love is {sp}.\n"
                f"It just makes life a little easier, you know?\n"
                f"And {feats} — without any fuss.\n"
                f"No, it's not magic, but it actually delivers.\n"
                f"If you've been on the fence, this is your sign.\n"
                f"Link's right there if you want to see for yourself.")
            ),
            rationale="Warm first-person recommendation, UGC energy, soft close.",
        ),
        ScriptDraft(
            type="review_proof",
            title="Review / Proof",
            narration=(
                _aud(
                f"So I put {p} to the test — here's what actually happened.\n"
                f"First, the claim: {sp}.\n"
                f"I compared it against what I was using before.\n"
                f"The difference was obvious within days.\n"
                f"The proof is in the results: {feats}.\n"
                f"I tracked it, and the numbers don't lie.\n"
                f"If you want something that's actually backed up, this is it.\n"
                f"Check the link to see the full breakdown.")
            ),
            rationale="Demonstration with proof and a before/after framing.",
        ),
        ScriptDraft(
            type="story_drama",
            title="Story / Drama",
            narration=(
                f"She almost gave up — until one small change.\n"
                f"Every morning felt like the same uphill climb.\n"
                f"Then a friend handed her {p}.\n"
                f"At first she didn't believe it: {sp}.\n"
                f"By the end of the week, everything felt different.\n"
                f"Now {feats} is just her normal.\n"
                f"Sometimes the fix is simpler than we think.\n"
                f"Find {p} through the link — and write your own ending."
            ),
            rationale="A tiny character arc with a payoff beat.",
        ),
    ]
