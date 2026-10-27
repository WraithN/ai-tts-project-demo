"""Procedural imagery for the offline path.

Backgrounds are deterministic stills (cached by content hash) that the
composer pushes through affine motion. Graphics are drawn per frame so they
can animate -- charts grow, diagram nodes light up, badges punch in.

Real providers (FLUX, SDXL, Runway) satisfy the same interface; the mock
exists so the pipeline is runnable and testable without credentials.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from ..hashing import short_hash
from .base import Cache, Cost, Provider

# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def hex2rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def mix(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    t = max(0.0, min(1.0, t))
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))  # type: ignore[return-value]


def clamp01(x: float) -> float:
    return 0.0 if x < 0 else (1.0 if x > 1 else x)


def ease_out_cubic(t: float) -> float:
    t = clamp01(t)
    return 1.0 - (1.0 - t) ** 3


def ease_out_back(t: float, s: float = 1.7) -> float:
    t = clamp01(t)
    return 1.0 + (s + 1.0) * (t - 1.0) ** 3 + s * (t - 1.0) ** 2


def ease_in_out(t: float) -> float:
    t = clamp01(t)
    return t * t * (3.0 - 2.0 * t)


def _rng(seed: int) -> np.random.Generator:
    return np.random.default_rng(seed & 0xFFFFFFFF)


def _grid(h: int, w: int) -> tuple[np.ndarray, np.ndarray]:
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    return xx / max(1, w - 1), yy / max(1, h - 1)


# --------------------------------------------------------------------------
# Backgrounds
# --------------------------------------------------------------------------


def _vgrad(w: int, h: int, top, bottom) -> Image.Image:
    t = np.linspace(0.0, 1.0, h, dtype=np.float32)[:, None]
    arr = np.zeros((h, w, 3), dtype=np.float32)
    for c in range(3):
        arr[:, :, c] = top[c] + (bottom[c] - top[c]) * t
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")


def _vignette(img: Image.Image, strength: float = 0.55) -> Image.Image:
    w, h = img.size
    x, y = _grid(h, w)
    d = np.sqrt((x - 0.5) ** 2 + (y - 0.5) ** 2) / 0.7071
    m = np.clip(1.0 - strength * (d**1.8), 0, 1).astype(np.float32)[:, :, None]
    a = np.asarray(img).astype(np.float32) * m
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), "RGB")


def _grain(img: Image.Image, seed: int, amount: float = 6.0) -> Image.Image:
    r = _rng(seed)
    n = r.normal(0, amount, (img.size[1], img.size[0], 1)).astype(np.float32)
    a = np.asarray(img).astype(np.float32) + n
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), "RGB")


def _bokeh(img: Image.Image, seed: int, palette, count: int = 14) -> Image.Image:
    """Soft out-of-focus highlights.

    Drawn at 1/8 scale then upscaled with BICUBIC, which produces the blur
    essentially for free. A real GaussianBlur at the equivalent radius (80+px
    at 1350px wide) was by far the most expensive operation in the pipeline:
    it turned a 40-second render into a 3-minute one.
    """
    w, h = img.size
    r = _rng(seed)
    s = 8
    lw, lh = max(8, w // s), max(8, h // s)

    layer = Image.new("RGB", (lw, lh), (0, 0, 0))
    d = ImageDraw.Draw(layer)
    for _ in range(count):
        rad = max(1, int(r.uniform(lw * 0.05, lw * 0.30)))
        cx = int(r.uniform(0, lw))
        cy = int(r.uniform(0, lh))
        col = hex2rgb(palette[int(r.integers(0, len(palette)))])
        d.ellipse([cx - rad, cy - rad, cx + rad, cy + rad], fill=col)

    layer = layer.resize((w, h), Image.BICUBIC)
    layer = layer.filter(ImageFilter.GaussianBlur(radius=max(1.0, w * 0.006)))
    return Image.blend(img, layer, 0.42)


def _dots(img: Image.Image, seed: int, color: str, spacing: int = 22, rmax: float = 5.0) -> Image.Image:
    """Halftone dot screen -- the comic-book signature."""
    w, h = img.size
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    rgb = hex2rgb(color)
    for y in range(0, h, spacing):
        for x in range(0, w, spacing):
            r = rmax * (0.45 + 0.55 * (((x // spacing) + (y // spacing)) % 3) / 2.0)
            d.ellipse([x - r, y - r, x + r, y + r], fill=rgb + (46,))
    return Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")


def _shapes(img: Image.Image, seed: int, palette, soft: bool = False) -> Image.Image:
    """Large geometric masses -- the abstract subject of a B-roll frame."""
    w, h = img.size
    r = _rng(seed)
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for i in range(5):
        col = hex2rgb(palette[int(r.integers(0, len(palette)))])
        a = int(r.uniform(60, 140))
        cx, cy = int(r.uniform(0.1, 0.9) * w), int(r.uniform(0.15, 0.85) * h)
        rad = int(r.uniform(0.08, 0.30) * min(w, h))
        if i % 2 == 0:
            d.ellipse([cx - rad, cy - rad, cx + rad, cy + rad], fill=col + (a,))
        else:
            d.rounded_rectangle(
                [cx - rad, cy - rad * 0.7, cx + rad, cy + rad * 0.7],
                radius=int(rad * 0.22),
                fill=col + (a,),
            )
    if soft:
        layer = layer.filter(ImageFilter.GaussianBlur(radius=max(1.0, min(w, h) * 0.012)))
    return Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")


def _figure(img: Image.Image, seed: int, palette) -> Image.Image:
    """A soft human silhouette -- stands in for a spokesperson shot."""
    w, h = img.size
    r = _rng(seed)
    cx = w * (0.5 + r.uniform(-0.06, 0.06))
    head_r = w * 0.16
    head_cy = h * 0.34
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    ink = hex2rgb(palette[3]) + (190,)
    d.ellipse([cx - head_r, head_cy - head_r, cx + head_r, head_cy + head_r], fill=ink)
    d.ellipse(
        [cx - w * 0.42, head_cy + head_r * 0.85, cx + w * 0.42, head_cy + head_r * 0.85 + h * 0.42],
        fill=ink,
    )
    layer = layer.filter(ImageFilter.GaussianBlur(radius=max(1.0, w * 0.02)))
    return Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")


def background(style: str, seed: int, w: int, h: int, palette: list[str]) -> Image.Image:
    """Render a deterministic still background for a given style."""
    pal = palette or ["#FFFFFF", "#FF3B30", "#14213D", "#0B0B0F"]
    r = _rng(seed)

    if style == "halftone":
        bg = _vgrad(w, h, hex2rgb(pal[0]), mix(hex2rgb(pal[0]), hex2rgb("#000000"), 0.12))
        bg = _shapes(bg, seed, pal)
        bg = _dots(bg, seed, pal[3])
    elif style == "flat":
        top = mix(hex2rgb("#FFFFFF"), hex2rgb(pal[1]), 0.10)
        bg = _vgrad(w, h, top, mix(top, hex2rgb(pal[2]), 0.20))
        bg = _shapes(bg, seed, pal)
    elif style == "clay":
        bg = _vgrad(w, h, hex2rgb(pal[0]), mix(hex2rgb(pal[0]), hex2rgb(pal[3]), 0.35))
        bg = _shapes(bg, seed, pal, soft=True)
        bg = _grain(bg, seed, 9.0)
    elif style == "selfie":
        bg = _vgrad(w, h, mix(hex2rgb("#FFFFFF"), hex2rgb(pal[1]), 0.16), mix(hex2rgb(pal[2]), hex2rgb(pal[3]), 0.55))
        bg = _figure(bg, seed, pal)
        bg = _grain(bg, seed, 5.0)
    elif style == "studio":
        bg = _vgrad(w, h, mix(hex2rgb(pal[0]), hex2rgb("#000000"), 0.25), mix(hex2rgb(pal[0]), hex2rgb("#8FD3FF"), 0.18))
        bg = _figure(bg, seed, pal)
        bg = _vignette(bg, 0.62)
    else:  # photo / cinematic B-roll
        c1 = hex2rgb(pal[2]) if len(pal) > 2 else hex2rgb("#14213D")
        c2 = hex2rgb(pal[0]) if len(pal) > 0 else hex2rgb("#FFFFFF")
        bg = _vgrad(w, h, mix(c1, hex2rgb("#000000"), 0.35), mix(c1, c2, 0.45))
        bg = _bokeh(bg, seed, pal, count=int(10 + abs(seed) % 10))
        bg = _vignette(bg, 0.58)

    return bg.resize((w, h), Image.LANCZOS)


# --------------------------------------------------------------------------
# Graphic overlays (animated)
# --------------------------------------------------------------------------


def _stage(t: float, start: float, dur: float) -> float:
    """Element progress: 0 before `start`, eased 0->1 across `dur`."""
    return ease_out_cubic((t - start) / max(1e-6, dur))


def graphic_mechanism(d: ImageDraw.ImageDraw, w: int, h: int, t: float, pal: list[str], labels: list[str]) -> None:
    accent = hex2rgb(pal[1] if len(pal) > 1 else "#FF7A00")
    ink = hex2rgb(pal[2] if len(pal) > 2 else "#14213D")
    cx, cy = w * 0.5, h * 0.42
    r = min(w, h) * 0.19
    pts = [(cx + r * math.cos(a), cy + r * math.sin(a)) for a in (-math.pi / 2, 0, math.pi / 2, math.pi)]
    for i in range(4):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % 4]
        p = _stage(t, 0.05 * i, 0.22)
        if p <= 0:
            continue
        d.line([x1, y1, x1 + (x2 - x1) * p, y1 + (y2 - y1) * p], fill=accent, width=int(w * 0.010))
    for i, (px, py) in enumerate(pts):
        p = _stage(t, 0.05 * i, 0.20)
        if p <= 0:
            continue
        rad = max(2, int(min(w, h) * 0.035 * ease_out_back(p)))
        d.ellipse([px - rad, py - rad, px + rad, py + rad], fill=ink)
        lab = labels[i] if i < len(labels) else ""
        if lab and p > 0.55:
            d.text((px, py + rad * 1.5), lab[:14], fill=ink, anchor="mm")


def graphic_proof(d: ImageDraw.ImageDraw, w: int, h: int, t: float, pal: list[str], labels: list[str]) -> None:
    accent = hex2rgb(pal[1] if len(pal) > 1 else "#FF7A00")
    ink = hex2rgb(pal[2] if len(pal) > 2 else "#14213D")
    n = 5
    vals = [0.28, 0.42, 0.55, 0.74, 0.96]
    bw = w * 0.11
    gap = w * 0.055
    total = n * bw + (n - 1) * gap
    x0 = (w - total) / 2
    base = h * 0.60
    d.line([w * 0.12, base, w * 0.88, base], fill=ink, width=int(w * 0.006))
    for i in range(n):
        p = _stage(t, 0.06 * i, 0.30)
        if p <= 0:
            continue
        bh = h * 0.30 * vals[i] * p
        x = x0 + i * (bw + gap)
        d.rounded_rectangle([x, base - bh, x + bw, base], radius=int(bw * 0.16), fill=accent if i == n - 1 else mix(accent, ink, 0.45))


def graphic_offer(d: ImageDraw.ImageDraw, w: int, h: int, t: float, pal: list[str], labels: list[str]) -> None:
    accent = hex2rgb(pal[1] if len(pal) > 1 else "#FF7A00")
    ink = hex2rgb(pal[2] if len(pal) > 2 else "#14213D")
    p = ease_out_back(t, 1.4)
    bw, bh = w * 0.20 * p, h * 0.26 * p
    cx, cy = w * 0.40, h * 0.45
    d.rounded_rectangle([cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2], radius=int(bw * 0.22), fill=ink)
    d.rounded_rectangle([cx - bw * 0.22, cy - bh * 0.62, cx + bw * 0.22, cy - bh / 2], radius=int(bw * 0.08), fill=mix(ink, (255, 255, 255), 0.35))
    badge = (labels[0][:12] if labels else "SAVE 40%")
    r0 = w * 0.085 * p
    bx, by = w * 0.66, h * 0.52
    d.ellipse([bx - r0, by - r0, bx + r0, by + r0], fill=accent)
    d.text((bx, by), badge, fill=hex2rgb(pal[0] if pal else "#FFFFFF"), anchor="mm")


def graphic_cta(d: ImageDraw.ImageDraw, w: int, h: int, t: float, pal: list[str], labels: list[str]) -> None:
    accent = hex2rgb(pal[1] if len(pal) > 1 else "#FF7A00")
    ink = hex2rgb(pal[2] if len(pal) > 2 else "#14213D")
    # pulsing button
    pulse = 1.0 + 0.035 * math.sin(t * math.pi * 6)
    p = ease_out_cubic(t / 0.25)
    bw, bh = w * 0.62 * p * pulse, h * 0.085 * p * pulse
    cx, cy = w * 0.5, h * 0.52
    d.rounded_rectangle([cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2], radius=int(bh * 0.5), fill=accent)
    d.rectangle([cx - bw / 2, cy + bh / 2, cx + bw / 2, cy + bh / 2 + h * 0.008], fill=mix(accent, ink, 0.35))
    lab = (labels[0][:22] if labels else "GET STARTED")
    d.text((cx, cy), lab, fill=hex2rgb(pal[0] if pal else "#FFFFFF"), anchor="mm")


_GRAPHICS = {
    "mechanism": graphic_mechanism,
    "proof": graphic_proof,
    "offer": graphic_offer,
    "cta": graphic_cta,
}


def graphic_layer(beat: str, labels: list[str]) -> object | None:
    return _GRAPHICS.get(beat)
