"""Style adapters.

The client asked for nine styles. They are NOT nine separate pipelines --
they are nine parameter bundles consumed by the same renderer. Each style
decides: what font, how captions animate, how hard the pattern interrupts
hit, how long a shot lasts, what the background looks like, and what kind
of visual each beat prefers.

Because styles are per-scene (`Scene.visual.style`), a single video can
mix them: hook in DR VSL, mechanism in vector, offer in comic.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal

from .ir import Aspect, Beat, Motion, VisualKind

# --------------------------------------------------------------------------
# Font resolution
# --------------------------------------------------------------------------

_FONT_DIRS = [
    os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
    "/usr/share/fonts/truetype/dejavu",
    "/Library/Fonts",
]

_FONT_FILES = {
    "ariblk": "ariblk.ttf",
    "arialbd": "arialbd.ttf",
    "arial": "arial.ttf",
    "impact": "impact.ttf",
    "segoeuib": "segoeuib.ttf",
    "segoeui": "segoeui.ttf",
    "calibrib": "calibrib.ttf",
    "georgiab": "georgiab.ttf",
    "consolab": "consolab.ttf",
}


def resolve_font(key: str) -> str | None:
    """Return an absolute path to a system font, or None if unavailable."""
    fname = _FONT_FILES.get(key)
    if not fname:
        return None
    for d in _FONT_DIRS:
        p = os.path.join(d, fname)
        if os.path.isfile(p):
            return p
    return None


def any_bold_font() -> str | None:
    for key in ("ariblk", "arialbd", "impact", "segoeuib", "calibrib"):
        p = resolve_font(key)
        if p:
            return p
    return None


# --------------------------------------------------------------------------
# Style spec
# --------------------------------------------------------------------------


@dataclass
class StyleSpec:
    name: str
    label: str
    font_key: str
    font_scale: float
    uppercase: bool
    caption_mode: Literal["kinetic", "karaoke", "static", "none"]
    caption_position: Literal["bottom", "center", "top"]
    text_color: str
    stroke_color: str
    stroke_width: int
    highlight: str
    line_gap: int
    default_motion: Motion
    shot_len: tuple[float, float]  # target seconds per shot
    interrupt_bias: float
    transition: str
    bg_style: str  # how the offline mock renders this style
    overlays: list[str] = field(default_factory=list)
    palette: list[str] = field(default_factory=list)


STYLES: dict[str, StyleSpec] = {
    "dr_vsl": StyleSpec(
        name="dr_vsl",
        label="Direct-response VSL",
        font_key="ariblk",
        font_scale=0.082,
        uppercase=True,
        caption_mode="kinetic",
        caption_position="bottom",
        text_color="#FFFFFF",
        stroke_color="#000000",
        stroke_width=14,
        highlight="#FFE500",
        line_gap=18,
        default_motion="punch",
        shot_len=(1.4, 2.6),
        interrupt_bias=0.75,
        transition="flash",
        bg_style="photo",
        overlays=["vignette", "scanlines"],
        palette=["#FF3B30", "#FFE500", "#0B0B0F", "#FFFFFF"],
    ),
    "comic": StyleSpec(
        name="comic",
        label="Comic book animation",
        font_key="impact",
        font_scale=0.090,
        uppercase=True,
        caption_mode="kinetic",
        caption_position="bottom",
        text_color="#FFFFFF",
        stroke_color="#111111",
        stroke_width=16,
        highlight="#FFD400",
        line_gap=14,
        default_motion="zoom_in",
        shot_len=(1.8, 3.0),
        interrupt_bias=0.55,
        transition="whip",
        bg_style="halftone",
        overlays=["panel_border", "halftone_dots"],
        palette=["#F5F1E6", "#E8402A", "#1B4B8F", "#111111"],
    ),
    "vector": StyleSpec(
        name="vector",
        label="Vector animation",
        font_key="segoeuib",
        font_scale=0.062,
        uppercase=False,
        caption_mode="karaoke",
        caption_position="bottom",
        text_color="#14213D",
        stroke_color="#FFFFFF",
        stroke_width=8,
        highlight="#FF7A00",
        line_gap=22,
        default_motion="zoom_out",
        shot_len=(2.4, 3.8),
        interrupt_bias=0.20,
        transition="dissolve",
        bg_style="flat",
        overlays=[],
        palette=["#FFFFFF", "#FF7A00", "#14213D", "#8AC926"],
    ),
    "claymation": StyleSpec(
        name="claymation",
        label="Claymation",
        font_key="arialbd",
        font_scale=0.070,
        uppercase=False,
        caption_mode="kinetic",
        caption_position="bottom",
        text_color="#3A2A20",
        stroke_color="#FFF3E0",
        stroke_width=11,
        highlight="#F2A65A",
        line_gap=18,
        default_motion="zoom_in",
        shot_len=(2.2, 3.4),
        interrupt_bias=0.30,
        transition="dissolve",
        bg_style="clay",
        overlays=["grain"],
        palette=["#F4E3C8", "#E08A5B", "#7A9E7E", "#5C4033"],
    ),
    "ugc": StyleSpec(
        name="ugc",
        label="AI UGC creator",
        font_key="segoeuib",
        font_scale=0.068,
        uppercase=False,
        caption_mode="kinetic",
        caption_position="center",
        text_color="#FFFFFF",
        stroke_color="#000000",
        stroke_width=12,
        highlight="#A8FF60",
        line_gap=16,
        default_motion="zoom_in",
        shot_len=(1.6, 3.0),
        interrupt_bias=0.45,
        transition="cut",
        bg_style="selfie",
        overlays=["subtle_grain"],
        palette=["#FFFFFF", "#A8FF60", "#111111", "#FF5C8A"],
    ),
    "talking_head": StyleSpec(
        name="talking_head",
        label="AI presenter / expert",
        font_key="segoeuib",
        font_scale=0.064,
        uppercase=False,
        caption_mode="karaoke",
        caption_position="bottom",
        text_color="#FFFFFF",
        stroke_color="#0B1B2B",
        stroke_width=11,
        highlight="#4CC9F0",
        line_gap=20,
        default_motion="static",
        shot_len=(3.0, 5.0),
        interrupt_bias=0.15,
        transition="dissolve",
        bg_style="studio",
        overlays=["lower_third"],
        palette=["#0B1B2B", "#4CC9F0", "#FFFFFF", "#F0F4F8"],
    ),
}

DEFAULT_STYLE = "dr_vsl"


def get_style(name: str) -> StyleSpec:
    return STYLES.get(name, STYLES[DEFAULT_STYLE])


# --------------------------------------------------------------------------
# Beat -> visual kind
#
# Different styles prefer different media for the same narrative beat.
# This table is the "determine appropriate visuals for each section" step.
# --------------------------------------------------------------------------

_BEAT_VISUAL: dict[str, dict[Beat, VisualKind]] = {
    "dr_vsl": {
        "hook": "ai_clip",
        "problem": "stock",
        "agitate": "stock",
        "mechanism": "graphic",
        "proof": "still",
        "offer": "graphic",
        "cta": "graphic",
    },
    "comic": {
        "hook": "still",
        "problem": "still",
        "agitate": "still",
        "mechanism": "graphic",
        "proof": "graphic",
        "offer": "graphic",
        "cta": "graphic",
    },
    "vector": {
        "hook": "graphic",
        "problem": "graphic",
        "agitate": "graphic",
        "mechanism": "graphic",
        "proof": "graphic",
        "offer": "graphic",
        "cta": "graphic",
    },
    "claymation": {
        "hook": "still",
        "problem": "still",
        "agitate": "still",
        "mechanism": "still",
        "proof": "still",
        "offer": "graphic",
        "cta": "graphic",
    },
    "ugc": {
        "hook": "ai_clip",
        "problem": "ai_clip",
        "agitate": "ai_clip",
        "mechanism": "graphic",
        "proof": "ai_clip",
        "offer": "graphic",
        "cta": "ai_clip",
    },
    "talking_head": {
        "hook": "ai_clip",
        "problem": "ai_clip",
        "agitate": "ai_clip",
        "mechanism": "graphic",
        "proof": "graphic",
        "offer": "graphic",
        "cta": "ai_clip",
    },
}


def visual_kind_for(style: str, beat: Beat) -> VisualKind:
    table = _BEAT_VISUAL.get(style, _BEAT_VISUAL[DEFAULT_STYLE])
    return table.get(beat, "stock")


def motion_for(style: str, beat: Beat) -> Motion:
    spec = get_style(style)
    if beat in ("mechanism", "proof"):
        return "zoom_in"
    return spec.default_motion


# --------------------------------------------------------------------------
# Typography helpers shared by every renderer
# --------------------------------------------------------------------------


def scale_for_canvas(spec: StyleSpec, aspect: Aspect, width: int, height: int) -> int:
    """Caption font size in px, proportional to the short edge."""
    return max(28, int(min(width, height) * spec.font_scale))
