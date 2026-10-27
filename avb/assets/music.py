"""Procedurally generated music beds and SFX.

Ships offline so the demo runs with zero credentials and zero licensing
risk. Every track is synthesised from a chord progression + BPM, which
means it is also *beat-aware*: the composer can align cuts to the grid,
which is most of what makes a direct-response edit feel intentional.

Real providers (Suno, stock libraries) drop into the same interface.
"""

from __future__ import annotations

import math
import os
from pathlib import Path

import numpy as np
from scipy.signal import lfilter

from ..hashing import short_hash
from .base import Cache, Cost, Provider

SR = 44100

# --------------------------------------------------------------------------
# Scales: semitone offsets from the root.
# --------------------------------------------------------------------------

MINOR = [0, 2, 3, 5, 7, 8, 10]
MAJOR = [0, 2, 4, 5, 7, 9, 11]

# Progressions as scale-degree indices (0-based) into the scale.
PROGRESSIONS: dict[str, list[list[int]]] = {
    "pulse_drive": [[0, 2, 4], [5, 0, 2], [3, 5, 0], [4, 6, 1]],
    "warm_hope": [[0, 2, 4], [3, 5, 0], [4, 6, 1], [0, 2, 4]],
    "tense": [[0, 2, 4], [1, 3, 5], [5, 0, 2], [4, 6, 1]],
    "uplift": [[0, 2, 4], [4, 6, 1], [3, 5, 0], [4, 6, 1]],
}

TRACK_META: dict[str, dict[str, object]] = {
    "pulse_drive": {"bpm": 128, "scale": MINOR, "root": 55.0, "drive": 1.0},
    "warm_hope": {"bpm": 96, "scale": MAJOR, "root": 65.4, "drive": 0.7},
    "tense": {"bpm": 140, "scale": MINOR, "root": 49.0, "drive": 1.15},
    "uplift": {"bpm": 112, "scale": MAJOR, "root": 58.3, "drive": 0.9},
}


def midi_to_hz(m: float) -> float:
    return 440.0 * (2.0 ** ((m - 69.0) / 12.0))


def _env(n: int, attack: float, decay: float, sr: int = SR) -> np.ndarray:
    a = max(1, int(attack * sr))
    e = np.ones(n, dtype=np.float32)
    if a < n:
        e[:a] = np.linspace(0.0, 1.0, a, dtype=np.float32)
    t = np.arange(n, dtype=np.float32) / sr
    e *= np.exp(-t / max(1e-4, decay)).astype(np.float32)
    return e


def _kick(sr: int = SR) -> np.ndarray:
    n = int(0.28 * sr)
    t = np.arange(n, dtype=np.float32) / sr
    f = 150.0 * np.exp(-t * 34.0) + 42.0
    phase = 2.0 * np.pi * np.cumsum(f) / sr
    y = np.sin(phase).astype(np.float32) * _env(n, 0.002, 0.11, sr)
    y += 0.35 * np.exp(-t * 60.0) * (np.random.default_rng(1).standard_normal(n) * 0.25)
    return (y / (np.max(np.abs(y)) + 1e-6) * 0.95).astype(np.float32)


def _snare(sr: int = SR) -> np.ndarray:
    n = int(0.20 * sr)
    rng = np.random.default_rng(2)
    nz = rng.standard_normal(n).astype(np.float32)
    y = lfilter([0.6, -0.2], [1.0, -0.35], nz)
    y = y * _env(n, 0.001, 0.055, sr)
    t = np.arange(n, dtype=np.float32) / sr
    y += 0.30 * np.sin(2 * np.pi * 190 * t) * np.exp(-t * 42.0)
    return (y / (np.max(np.abs(y)) + 1e-6) * 0.72).astype(np.float32)


def _hat(sr: int = SR, open_: bool = False) -> np.ndarray:
    n = int((0.16 if open_ else 0.055) * sr)
    rng = np.random.default_rng(3)
    nz = rng.standard_normal(n).astype(np.float32)
    y = lfilter([0.85, -0.85], [1.0, -0.6], nz)
    y = y * _env(n, 0.001, 0.02 if not open_ else 0.07, sr)
    return (y / (np.max(np.abs(y)) + 1e-6) * 0.30).astype(np.float32)


def _bass(note_hz: float, dur: float, sr: int = SR) -> np.ndarray:
    n = int(dur * sr)
    t = np.arange(n, dtype=np.float32) / sr
    phase = 2.0 * np.pi * note_hz * t
    y = np.sin(phase) + 0.42 * np.sin(2 * phase) + 0.18 * np.sin(3 * phase)
    y = y.astype(np.float32) * _env(n, 0.006, dur * 0.55, sr)
    return (y / (np.max(np.abs(y)) + 1e-6) * 0.55).astype(np.float32)


def _pad(notes: list[float], dur: float, sr: int = SR) -> np.ndarray:
    n = int(dur * sr)
    t = np.arange(n, dtype=np.float32) / sr
    y = np.zeros(n, dtype=np.float32)
    for k, hz in enumerate(notes):
        det = 1.0 + (k - 1) * 0.0016
        y += (0.5 / len(notes)) * np.sin(2 * np.pi * hz * det * t).astype(np.float32)
        y += (0.16 / len(notes)) * np.sin(2 * np.pi * hz * 2.0 * det * t).astype(np.float32)
    atk = int(0.22 * sr)
    env = np.ones(n, dtype=np.float32)
    if atk < n:
        env[:atk] = np.linspace(0.0, 1.0, atk, dtype=np.float32)
    env *= (0.68 + 0.32 * (1.0 + np.sin(2 * np.pi * 0.22 * t)) / 2.0).astype(np.float32)
    return (y * env * 0.34).astype(np.float32)


def render_track(track: str = "pulse_drive", seconds: float = 60.0, sr: int = SR) -> np.ndarray:
    """Synthesise a looping music bed."""
    meta = TRACK_META.get(track, TRACK_META["pulse_drive"])
    bpm = int(meta["bpm"])
    scale: list[int] = list(meta["scale"])  # type: ignore[arg-type]
    root: float = float(meta["root"])
    drive: float = float(meta["drive"])

    beat = 60.0 / bpm
    bar = beat * 4
    n = int(seconds * sr) + int(1.5 * sr)
    out = np.zeros(n, dtype=np.float32)

    kick, snare, hat_c, hat_o = _kick(sr), _snare(sr), _hat(sr), _hat(sr, True)
    prog = PROGRESSIONS.get(track, PROGRESSIONS["pulse_drive"])

    bars = int(seconds / bar) + 2
    for b in range(bars):
        t0 = b * bar
        chord_idx = prog[b % len(prog)]
        # Root note for the bass, in Hz, from the scale degree.
        root_deg = chord_idx[0]
        octave, deg = divmod(root_deg, len(scale))
        bass_hz = root * (2.0 ** (octave + scale[deg] / 12.0))
        pad_notes = [
            root * (2.0 ** (divmod(d, len(scale))[0] + scale[divmod(d, len(scale))[1]] / 12.0)) * 2.0
            for d in chord_idx
        ]

        for i in range(4):
            s = int((t0 + i * beat) * sr)
            if s + len(kick) <= n:
                out[s : s + len(kick)] += kick
            if i in (1, 3):
                s = int((t0 + i * beat) * sr)
                if s + len(snare) <= n:
                    out[s : s + len(snare)] += snare
            for sub in (0.0, 0.5):
                s = int((t0 + (i + sub) * beat) * sr)
                if s + len(hat_c) <= n:
                    out[s : s + len(hat_c)] += hat_c * (0.45 if sub else 1.0)
            if i == 3:
                s = int((t0 + i * beat + beat * 0.5) * sr)
                if s + len(hat_o) <= n:
                    out[s : s + len(hat_o)] += hat_o

        s = int(t0 * sr)
        bs = _bass(bass_hz, bar)
        if s + len(bs) <= n:
            out[s : s + len(bs)] += bs
        pd = _pad(pad_notes, bar)
        if s + len(pd) <= n:
            out[s : s + len(pd)] += pd

    out = np.tanh(out * 1.1).astype(np.float32) * 0.80
    return out


# --------------------------------------------------------------------------
# SFX
# --------------------------------------------------------------------------


def sfx(name: str, sr: int = SR) -> np.ndarray:
    rng = np.random.default_rng(abs(hash(name)) % (2**32))
    if name == "whoosh":
        n = int(0.45 * sr)
        t = np.arange(n, dtype=np.float32) / sr
        nz = rng.standard_normal(n).astype(np.float32)
        f = 300 + 2600 * (t / (n / sr))
        y = np.sin(2 * np.pi * np.cumsum(f) / sr).astype(np.float32) * 0
        y = lfilter([0.7, -0.6], [1.0, -0.55], nz)
        env = np.sin(np.pi * (t / (n / sr))) ** 1.6
        return (y * env * 0.42).astype(np.float32)
    if name == "impact":
        n = int(0.7 * sr)
        t = np.arange(n, dtype=np.float32) / sr
        f = 180 * np.exp(-t * 22) + 45
        y = np.sin(2 * np.pi * np.cumsum(f) / sr).astype(np.float32)
        y += 0.5 * rng.standard_normal(n).astype(np.float32) * np.exp(-t * 30)
        return (y * np.exp(-t * 6.5) * 0.62).astype(np.float32)
    if name == "riser":
        n = int(1.1 * sr)
        t = np.arange(n, dtype=np.float32) / sr
        nz = rng.standard_normal(n).astype(np.float32)
        f = 200 + 3400 * (t / (n / sr)) ** 2
        y = np.sin(2 * np.pi * np.cumsum(f) / sr).astype(np.float32) * 0.35
        y += lfilter([0.5, -0.45], [1.0, -0.6], nz) * 0.5
        return (y * (t / (n / sr)) ** 1.4 * 0.40).astype(np.float32)
    if name == "pop":
        n = int(0.14 * sr)
        t = np.arange(n, dtype=np.float32) / sr
        y = np.sin(2 * np.pi * (900 * np.exp(-t * 28) + 300) * t)
        return (y * np.exp(-t * 34) * 0.5).astype(np.float32)
    return np.zeros(int(0.2 * sr), dtype=np.float32)


# --------------------------------------------------------------------------
# Provider
# --------------------------------------------------------------------------


class SynthMusic(Provider):
    """Offline music + SFX bed."""

    name = "synth-mock"
    kind = "music"
    price_per_sec = 0.0

    def available(self) -> bool:
        return True

    def price(self, seconds: float = 0.0, **_: object) -> float:
        return 0.0

    def bed(self, track: str, seconds: float, cache: Cache) -> Path:
        key = short_hash({"track": track, "sec": round(seconds, 2)})
        hit = cache.get("music", key, ".wav")
        if hit:
            return hit
        from .voice import write_wav

        audio = render_track(track, seconds)
        tmp = cache.root / "music" / f"tmp_{os.getpid()}_{key}.wav"
        write_wav(tmp, audio)
        return cache.put("music", key, ".wav", tmp)
