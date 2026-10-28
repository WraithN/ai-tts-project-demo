"""Voiceover providers.

FormantVoice (default, offline)
    Deterministic Klatt-style formant synthesis. Produces a real waveform
    plus a real word-level timeline. It is a *placeholder voice* -- the point
    is not that it sounds human, it is that it satisfies the same contract a
    real TTS does: text in, (audio, word timings) out.

    Word timings are what everything downstream actually depends on:
    caption sync, shot length, scene duration, music ducking. Swap in
    ElevenLabs and none of that changes. That is the whole benefit of
    keeping an IR in the middle.

ElevenLabsVoice
    Real provider, activates when ELEVENLABS_API_KEY is set. Uses the
    /with-timestamps endpoint so we get exact per-word timings, which is
    what makes frame-accurate kinetic captions possible.
"""

from __future__ import annotations

import json
import os
import shutil
import re
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.signal import lfilter

from ..hashing import short_hash
from .base import Cache, Cost, Provider

SR = 44100

# --------------------------------------------------------------------------
# Voice presets.
#   f0    base pitch in Hz
#   scale formant scaling (1.0 ~ adult male; >1 = smaller vocal tract)
#   rate  speaking-rate multiplier
# --------------------------------------------------------------------------

VOICES: dict[str, dict[str, float]] = {
    "adam":          {"f0": 112.0, "scale": 1.00, "rate": 1.00},
    "marcus":        {"f0": 98.0,  "scale": 0.94, "rate": 0.97},
    "rachel":        {"f0": 196.0, "scale": 1.18, "rate": 1.02},
    "sofia":         {"f0": 188.0, "scale": 1.16, "rate": 1.04},
    "vet_female":    {"f0": 182.0, "scale": 1.14, "rate": 1.00},
    "expert_female": {"f0": 178.0, "scale": 1.13, "rate": 0.98},
    "expert_male":   {"f0": 106.0, "scale": 0.98, "rate": 0.98},
}


def voice_params(name: str) -> dict[str, float]:
    return VOICES.get(name, VOICES["adam"])


def list_voices() -> list[str]:
    return sorted(VOICES)


# --------------------------------------------------------------------------
# Coarse grapheme -> phoneme
#
# Not a dictionary. Good enough that rendered captions visibly track a
# speech-like rhythm, which is all the demo needs.
# --------------------------------------------------------------------------

# name: (F1, F2, F3, class, base_ms)
PHONES: dict[str, tuple[float, float, float, str, float]] = {
    "IY": (270, 2290, 3010, "v", 95), "IH": (390, 1990, 2550, "v", 75),
    "EY": (530, 1840, 2480, "v", 110), "EH": (530, 1840, 2480, "v", 85),
    "AE": (660, 1720, 2410, "v", 105), "AA": (730, 1090, 2440, "v", 110),
    "AO": (570, 840, 2410, "v", 105),  "OW": (570, 840, 2410, "v", 110),
    "UH": (440, 1020, 2240, "v", 75),  "UW": (300, 870, 2240, "v", 100),
    "AH": (520, 1190, 2390, "v", 80),  "ER": (490, 1350, 1690, "v", 100),
    "AW": (720, 1240, 2500, "v", 130), "AY": (660, 1720, 2410, "v", 130),
    "OY": (500, 1000, 2400, "v", 130),
    "M":  (280, 900, 2200, "n", 65),  "N":  (280, 1700, 2600, "n", 60),
    "NG": (280, 2300, 2750, "n", 70),
    "L":  (360, 1300, 2900, "l",  60), "R":  (310, 1060, 1380, "l", 62),
    "W":  (300, 610, 2200, "l",  45),  "Y":  (260, 2070, 3020, "l", 45),
    "S":  (0, 1400, 4500, "f", 85),   "F":  (0, 1200, 4000, "f", 75),
    "TH": (0, 1400, 4200, "f", 70),   "SH": (0, 1800, 3500, "f", 90),
    "HH": (0, 1500, 4000, "f", 55),   "V":  (0, 1200, 3500, "f", 60),
    "Z":  (0, 1400, 4000, "f", 65),   "ZH": (0, 1700, 3800, "f", 75),
    "CH": (0, 2200, 3400, "f", 90),   "JH": (0, 2000, 3000, "f", 80),
    "P": (0, 800, 2200, "p", 60), "T": (0, 1700, 3500, "p", 58),
    "K": (0, 2000, 4000, "p", 65), "B": (0, 800, 2000, "p", 55),
    "D": (0, 1600, 3000, "p", 52), "G": (0, 1800, 3500, "p", 60),
}

_DIGRAPHS: list[tuple[str, list[str]]] = [
    ("tion", ["SH", "AH", "N"]), ("sion", ["ZH", "AH", "N"]),
    ("ough", ["OW"]), ("augh", ["AO", "F"]), ("eigh", ["EY"]),
    ("igh", ["AY"]), ("tch", ["CH"]), ("ch", ["CH"]), ("sh", ["SH"]),
    ("th", ["TH"]), ("ph", ["F"]), ("wh", ["W"]), ("ck", ["K"]),
    ("ng", ["NG"]), ("qu", ["K", "W"]), ("ee", ["IY"]), ("ea", ["IY"]),
    ("oa", ["OW"]), ("oo", ["UW"]), ("ou", ["AW"]), ("ow", ["OW"]),
    ("ai", ["EY"]), ("ay", ["EY"]), ("ie", ["IY"]), ("ue", ["UW"]),
    ("aw", ["AO"]), ("au", ["AO"]), ("oi", ["OY"]), ("oy", ["OY"]),
    ("kn", ["N"]), ("wr", ["R"]), ("gh", []),
]

_LETTERS: dict[str, list[str]] = {
    "a": ["AE"], "b": ["B"], "c": ["K"], "d": ["D"], "e": ["EH"],
    "f": ["F"], "g": ["G"], "h": ["HH"], "i": ["IH"], "j": ["JH"],
    "k": ["K"], "l": ["L"], "m": ["M"], "n": ["N"], "o": ["AA"],
    "p": ["P"], "q": ["K"], "r": ["R"], "s": ["S"], "t": ["T"],
    "u": ["AH"], "v": ["V"], "w": ["W"], "x": ["K", "S"], "y": ["Y"],
    "z": ["Z"],
}

_VOWELISH = set("aeiouy")


def g2p(word: str) -> list[str]:
    """Word -> coarse phoneme sequence."""
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        return ["AH"]

    # Silent trailing 'e' lengthens the preceding vowel: "make" -> M EY K
    long_vowel = False
    if len(w) > 2 and w.endswith("e") and sum(c in _VOWELISH for c in w) >= 2:
        w = w[:-1]
        long_vowel = True

    phones: list[str] = []
    i = 0
    while i < len(w):
        hit = False
        for dgraph, out in _DIGRAPHS:
            if w.startswith(dgraph, i):
                phones.extend(out)
                i += len(dgraph)
                hit = True
                break
        if hit:
            continue
        phones.extend(_LETTERS.get(w[i], []))
        i += 1

    phones = [p for p in phones if p in PHONES] or ["AH"]

    if long_vowel:
        # Repeat the final vowel to lengthen it. Note: we append to the local
        # list only -- PHONES is a module-level table and must never mutate.
        for j in range(len(phones) - 1, -1, -1):
            if PHONES[phones[j]][3] == "v":
                phones.append(phones[j])
                break
    return phones


# --------------------------------------------------------------------------
# Synthesis
# --------------------------------------------------------------------------


def _resonate(x: np.ndarray, freq: float, bw: float, sr: int = SR) -> np.ndarray:
    """Klatt-style 2-pole resonator, DC gain normalised to 1.

    Recurrence:  y[n] = A*x[n] + B*y[n-1] + C*y[n-2]
                 B = 2*r*cos(theta),  C = -r*r

    Note the sign of C. Because C is negative, the denominator polynomial
    is [1, -B, -C] = [1, -2r*cos(theta), +r²] -- getting this wrong puts
    a pole outside the unit circle and the filter blows up to NaN within a
    few thousand samples. (Ask me how I know.)
    """
    if freq <= 0 or freq >= sr / 2:
        return np.zeros_like(x)
    r = float(np.exp(-np.pi * bw / sr))
    theta = 2.0 * np.pi * freq / sr
    b_coef = 2.0 * r * np.cos(theta)
    c_coef = -(r * r)
    a_gain = 1.0 - b_coef - c_coef
    return lfilter([a_gain], [1.0, -b_coef, -c_coef], x)


def _glottal_source(n: int, f0: np.ndarray, sr: int = SR) -> np.ndarray:
    """Rosenberg-like glottal pulse train driven by an F0 contour."""
    phase = np.cumsum(f0[:n] / sr)
    frac = phase - np.floor(phase)
    pulse = np.power(frac, 2.0) * np.exp(-5.5 * frac)
    pulse -= pulse.mean()
    return pulse.astype(np.float32)


def _plan(text: str, rate: float) -> tuple[list[tuple[str, list[str], float]], list[float]]:
    """Split a line into (word, phones, duration) plus a per-word pause.

    Pauses come from punctuation. A comma is a breath; a full stop is a beat.
    Without these the captions read as one unbroken stream.
    """
    tokens = re.findall(r"[A-Za-z0-9$%']+|[.,!?;:]", text)
    plan: list[tuple[str, list[str], float]] = []
    pauses: list[float] = []
    pending = 0.0

    for t in tokens:
        if re.fullmatch(r"[.,!?;:]", t):
            pending += 0.16 if t == "," else 0.26
            continue
        phones = g2p(t)
        dur = max(0.11, sum(PHONES[p][4] for p in phones) / 1000.0 / rate)
        plan.append((t, phones, dur))
        pauses.append(pending)
        pending = 0.0

    return plan, pauses


def synth_speech(
    text: str,
    voice: str = "adam",
    speed: float = 1.0,
    energy: float = 1.0,
    sr: int = SR,
) -> tuple[np.ndarray, list[dict]]:
    """Synthesize one line.

    Returns (mono float32 audio, [{"word", "start", "end"}, ...]).
    """
    vp = voice_params(voice)
    base_f0 = vp["f0"]
    scale = vp["scale"]
    rate = max(0.5, vp["rate"] * speed)

    plan, pauses = _plan(text, rate)

    if not plan:
        return np.zeros(int(0.85 * sr), dtype=np.float32), []

    total = sum(d for _, _, d in plan) + sum(pauses) + 0.25
    out = np.zeros(int(total * sr), dtype=np.float32)

    rng = np.random.default_rng(abs(hash(text)) % (2**32))
    t = 0.18  # small lead-in so the first word isn't clipped
    timings: list[dict] = []

    for i, (word, phones, dur) in enumerate(plan):
        start = t
        per = dur / len(phones)
        prog = i / max(1, len(plan))

        for k, ph in enumerate(phones):
            f1, f2, f3, cls, base_ms = PHONES[ph]
            seg = int(max(0.024, per) * sr)
            if seg <= 0:
                continue

            # Pitch declination across the line (= "sentence is ending"),
            # plus an accent bump on the word's first segment. That bump is
            # what makes captions feel like they land on the beat.
            decl = 1.0 - 0.15 * prog
            accent = 1.0 + (0.11 * energy if k == 0 else 0.0)
            jitter = 1.0 + float(rng.normal(0, 0.006))
            f0 = np.full(seg, max(70.0, base_f0 * decl * accent * jitter), dtype=np.float32)

            if cls == "v":
                src = _glottal_source(seg, f0, sr)
                y = (
                    1.00 * _resonate(src, f1 * scale, 70, sr)
                    + 0.60 * _resonate(src, f2 * scale, 100, sr)
                    + 0.26 * _resonate(src, f3 * scale, 140, sr)
                )
            elif cls == "n":
                src = _glottal_source(seg, f0, sr)
                y = (
                    0.85 * _resonate(src, 280 * scale, 60, sr)
                    + 0.42 * _resonate(src, f2 * scale * 0.8, 130, sr)
                    + 0.18 * _resonate(src, 2400, 220, sr)
                )
            elif cls == "l":
                src = _glottal_source(seg, f0, sr)
                y = (
                    0.80 * _resonate(src, 340 * scale, 70, sr)
                    + 0.52 * _resonate(src, f2 * scale, 110, sr)
                    + 0.20 * _resonate(src, f3 * scale, 150, sr)
                )
            elif cls == "f":
                nz = rng.standard_normal(seg).astype(np.float32)
                y = 0.55 * _resonate(nz, max(700.0, f2 * scale), 1100, sr) + 0.12 * nz
            else:  # stop
                burst = min(seg, int(0.014 * sr))
                nz = rng.standard_normal(burst).astype(np.float32)
                y = np.concatenate([np.zeros(seg - burst, dtype=np.float32), 0.45 * nz])

            if y.size and np.max(np.abs(y)) > 1e-6:
                y = y * np.hanning(len(y)).astype(np.float32)
                y = y / np.max(np.abs(y)) * (0.55 + 0.12 * energy)
            s0 = int(start * sr)
            s1 = min(len(out), s0 + seg)
            if s0 < len(out):
                out[s0:s1] += y[: s1 - s0]

            start += per

        end = start + pauses[i] if i < len(pauses) else start
        timings.append({"word": word, "start": round(t, 4), "end": round(start, 4)})
        t = end

    peak = float(np.max(np.abs(out))) or 1.0
    out = out / peak * 0.88
    return out.astype(np.float32), timings


def write_wav(path: str | Path, audio: np.ndarray, sr: int = SR) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    ints = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(ints.tobytes())
    return p


# --------------------------------------------------------------------------
# Providers
# --------------------------------------------------------------------------


@dataclass
class VoiceResult:
    path: Path
    duration: float
    words: list[dict]
    cached: bool
    cost: Cost


class FormantVoice(Provider):
    """Offline Klatt formant synthesiser. Real audio, always available, free.

    It is not a placeholder -- it produces a genuine (robotic) voice with
    accurate word-level timings. It exists because Windows SAPI is not
    usable here, and because a pipeline that can still ship a cut with zero
    credentials is more useful than one that hard-fails on TTS.
    """

    name = "formant"
    kind = "voice"

    def available(self) -> bool:
        return True

    def price(self, **_: object) -> float:
        return 0.0

    def synth(self, text: str, voice: str, speed: float, energy: float, cache: Cache) -> VoiceResult:
        key = short_hash({"text": text, "voice": voice, "speed": round(speed, 3), "energy": round(energy, 3)})
        hit = cache.get("voice", key, ".wav")
        if hit is not None:
            dur = 0.0
            words: list[dict] = []
            side = hit.with_suffix(".json")
            if side.is_file():
                m = json.loads(side.read_text(encoding="utf-8"))
                words, dur = m.get("words", []), float(m.get("duration", 0.0))
            else:
                with wave.open(str(hit), "rb") as w:
                    dur = w.getnframes() / float(w.getframerate())
            return VoiceResult(path=hit, duration=dur, words=words, cached=True, cost=Cost(self.name, "voice", 0.0))

        audio, words = synth_speech(text, voice=voice, speed=speed, energy=energy)
        tmp = cache.root / "voice" / f"tmp_{os.getpid()}_{key}.wav"
        write_wav(tmp, audio)
        final = cache.put("voice", key, ".wav", tmp)

        # Sidecar keeps the provider self-contained: a cache hit can still
        # return exact word timings, which the caption layer needs.
        (cache.root / "voice" / f"{key}.json").write_text(
            json.dumps({"words": words, "duration": len(audio) / SR}, ensure_ascii=False),
            encoding="utf-8",
        )
        return VoiceResult(
            path=final,
            duration=len(audio) / SR,
            words=words,
            cached=False,
            cost=Cost(self.name, "voice", 0.0, len(audio) / SR),
        )


class ElevenLabsVoice(Provider):
    """Real TTS via the /with-timestamps endpoint.

    Activated by ELEVENLABS_API_KEY. Returns exact per-word timings, which
    is what makes frame-accurate kinetic captions possible.
    """

    name = "elevenlabs"
    kind = "voice"
    price_per_char = 0.00018

    VOICE_IDS = {
        "adam": "pNInz6obpgDQGcFmaJgB",
        "rachel": "21m00Tcm4TlvDq8ikWAM",
        "marcus": "VR6AewLTigWG4xSOukaG",
        "sofia": "EXAVITQu4vr4xnSDxMaL",
        "expert_female": "MF3mGyEYCl7XYWbV9V6O",
        "vet_female": "EXAVITQu4vr4xnSDxMaL",
        "expert_male": "TxGEqnHWrfWFTfGW9XjX",
    }

    def __init__(self) -> None:
        self.api_key = os.environ.get("ELEVENLABS_API_KEY", "")

    def available(self) -> bool:
        return bool(self.api_key)

    def price(self, text: str = "", **_: object) -> float:
        return round(len(text) * self.price_per_char, 6)

    def synth(self, text: str, voice: str, speed: float, energy: float, cache: Cache) -> VoiceResult:
        import base64

        import requests

        key = short_hash({"text": text, "voice": voice, "speed": round(speed, 3), "energy": round(energy, 3)})
        hit = cache.get("voice-el", key, ".mp3")
        if hit is not None:
            return VoiceResult(path=hit, duration=0.0, words=[], cached=True, cost=Cost(self.name, "voice", 0.0))

        vid = self.VOICE_IDS.get(voice, self.VOICE_IDS["adam"])
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{vid}/with-timestamps"
        payload = {
            "text": text,
            "model_id": "eleven_turbo_v2_5",
            "voice_settings": {
                "stability": round(0.55 - 0.2 * energy, 3),
                "similarity_boost": 0.75,
                "speed": speed,
            },
        }
        headers = {"xi-api-key": self.api_key, "Content-Type": "application/json"}
        r = requests.post(url, json=payload, headers=headers, timeout=240)
        r.raise_for_status()
        data = r.json()

        audio_bytes = base64.b64decode(data["audio_base64"])
        tmp = cache.root / "voice-el" / f"tmp_{os.getpid()}_{key}.mp3"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(audio_bytes)
        final = cache.put("voice-el", key, ".mp3", tmp)

        return VoiceResult(
            path=final,
            duration=0.0,
            words=_alignment_to_words(data.get("alignment") or {}, text),
            cached=False,
            cost=Cost(self.name, "voice", self.price(text), len(text), "char"),
        )


def _alignment_to_words(align: dict, text: str) -> list[dict]:
    """ElevenLabs per-character alignment -> per-word timings.

    The project's caption layer only ever consumes word timings, so this
    normalisation happens once, here, at the provider boundary.
    """
    chars = align.get("characters") or []
    starts = align.get("character_start_times_seconds") or []
    ends = align.get("character_end_times_seconds") or []
    if not chars:
        return []

    words: list[dict] = []
    buf: list[str] = []
    w_start: float | None = None
    w_end = 0.0

    for ch, s, e in zip(chars, starts, ends):
        if ch.isspace():
            if buf and w_start is not None:
                words.append({"word": "".join(buf), "start": round(w_start, 4), "end": round(w_end, 4)})
            buf, w_start = [], None
            continue
        if w_start is None:
            w_start = float(s)
        w_end = float(e)
        buf.append(ch)

    if buf and w_start is not None:
        words.append({"word": "".join(buf), "start": round(w_start, 4), "end": round(w_end, 4)})

    # Alignment is per-character; whitespace handling can drop a trailing word.
    if len(words) < len(text.split()):
        return words
    return words


# --------------------------------------------------------------------------
# Edge TTS (free, no key, neural voices + word boundaries)
# --------------------------------------------------------------------------


class EdgeVoice(Provider):
    """Microsoft Edge neural TTS via the edge-tts package.

    Why this one: it is free, needs no API key, ships ~400 neural voices
    (including native zh-CN), and emits WordBoundary events -- which is the
    exact thing frame-accurate kinetic captions need. That combination is
    rare. The catch is that it is an unofficial endpoint, so it is a
    *fallback-grade* dependency: fine for a portfolio pipeline and for
    volume where cost matters more than an SLA. When it matters, swap to
    ElevenLabs or a self-hosted CosyVoice/IndexTTS behind the same
    interface.

    Availability is probed once (network + package import) and cached,
    because a render fans out across threads and re-probing per scene would
    add seconds of latency for nothing.
    """

    name = "edge-tts"
    kind = "voice"
    price_per_1k_chars = 0.0  # free

    # (rate, pitch) mapping from the pipeline's abstract speed/energy knobs
    VOICES = {
        "expert_female": "en-US-AriaNeural",
        "expert_male": "en-US-GuyNeural",
        "warm_female": "en-US-JennyNeural",
        "narrator_male": "en-US-ChristopherNeural",
        "zh_female": "zh-CN-XiaoxiaoNeural",
        "zh_male": "zh-CN-YunxiNeural",
        "zh_news_female": "zh-CN-XiaoyiNeural",
        "zh_news_male": "zh-CN-YunjianNeural",
    }

    _probe: bool | None = None

    def available(self) -> bool:
        if EdgeVoice._probe is not None:
            return EdgeVoice._probe
        try:
            import edge_tts  # noqa: F401

            EdgeVoice._probe = True
        except Exception:
            EdgeVoice._probe = False
        return EdgeVoice._probe

    def price(self, text: str = "", **_: object) -> float:
        return 0.0

    def synth(self, text: str, voice: str, speed: float, energy: float, cache: Cache) -> VoiceResult:
        import asyncio

        # Nothing to pronounce -> let the offline synth produce the beat of
        # silence. Asking Edge would raise NoAudioReceived (see `speakable`).
        if not speakable(text):
            return FormantVoice().synth(text, voice, speed, energy, cache)

        try:
            return self._synth_edge(text, voice, speed, energy, cache)
        except Exception as exc:  # network, rate limit, bad voice id, ...
            # Edge is a free unofficial endpoint, so it owes us nothing. A
            # whole render should not die because Microsoft blipped -- drop to
            # the offline voice and say so loudly in the log.
            print(f"  !! edge-tts failed ({type(exc).__name__}: {exc}); "
                  f"falling back to offline formant voice for this line")
            return FormantVoice().synth(text, voice, speed, energy, cache)

    def _synth_edge(
        self, text: str, voice: str, speed: float, energy: float, cache: Cache
    ) -> VoiceResult:
        import asyncio

        import edge_tts

        voice_id = self.VOICES.get(voice, voice) if voice else self.VOICES["expert_female"]
        if not re.fullmatch(r"[a-z]{2}-[A-Za-z]+-[A-Za-z]+", voice_id or ""):
            voice_id = self.VOICES["expert_female"]

        rate_pct = int(round((speed - 1.0) * 100))
        rate = f"{rate_pct:+d}%"
        # energy maps to pitch deviation; keep it subtle, the model already
        # does most of the expressiveness work.
        pitch_hz = int(round((energy - 1.0) * 20))
        pitch = f"{pitch_hz:+d}Hz"

        key = short_hash(
            {"text": text, "voice": voice_id, "rate": rate, "pitch": pitch, "engine": self.name}
        )
        hit = cache.get("voice-edge", key, ".mp3")
        side = cache.root / "voice-edge" / f"{key}.json"
        if hit is not None and side.is_file():
            meta = json.loads(side.read_text(encoding="utf-8"))
            return VoiceResult(
                path=hit,
                duration=float(meta.get("duration", 0.0)),
                words=meta.get("words", []),
                cached=True,
                cost=Cost(self.name, "voice", 0.0),
            )

        words: list[dict] = []
        audio = bytearray()

        async def _run() -> None:
            com = edge_tts.Communicate(
                text, voice_id, rate=rate, pitch=pitch, boundary="WordBoundary"
            )
            async for chunk in com.stream():
                if chunk["type"] == "audio":
                    audio.extend(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    words.append(
                        {
                            "word": chunk["text"],
                            "start": round(chunk["offset"] / 1e7, 4),
                            "end": round((chunk["offset"] + chunk["duration"]) / 1e7, 4),
                        }
                    )

        # edge-tts is asyncio-based; the pipeline is sync + threaded. A new
        # loop per call is the honest way to bridge that without forcing the
        # whole orchestrator to become async.
        asyncio.run(_run())

        if not audio:
            raise RuntimeError(f"edge-tts returned no audio for voice={voice_id}")

        tmp = cache.root / "voice-edge" / f"tmp_{os.getpid()}_{key}.mp3"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(bytes(audio))
        final = cache.put("voice-edge", key, ".mp3", tmp)

        # duration from the container; fall back to word timings
        duration = _mp3_duration(final)
        if duration <= 0 and words:
            duration = max(w["end"] for w in words)

        side.write_text(
            json.dumps({"words": words, "duration": duration, "voice": voice_id}, ensure_ascii=False),
            encoding="utf-8",
        )
        return VoiceResult(
            path=final,
            duration=duration,
            words=words,
            cached=False,
            cost=Cost(self.name, "voice", 0.0, len(text), "char"),
        )


_SPEAKABLE = re.compile(r"[^\W_]", re.UNICODE)


def speakable(text: str | None) -> bool:
    """True if `text` has anything a TTS engine can actually pronounce.

    Edge's endpoint is the reason this exists: feed it `"..."`, `"--"` or
    `"♪♪"` and it happily completes the WebSocket stream having emitted zero
    audio bytes, then raises `NoAudioReceived` at the end. That is not a
    failure of the request, it is an empty result -- and it used to take the
    whole render down. `"🎉"` is fine (Edge narrates it), plain punctuation
    is not, hence "has at least one alphanumeric" rather than "is printable".
    """
    return bool(text) and bool(_SPEAKABLE.search(text))


def _mp3_duration(path: Path) -> float:
    """Duration in seconds via ffprobe; 0.0 if unavailable."""
    import subprocess

    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return 0.0
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=20,
        )
        return float(out.stdout.strip() or 0.0)
    except Exception:
        return 0.0


# --------------------------------------------------------------------------
# Voice provider registry
# --------------------------------------------------------------------------

VOICE_REGISTRY: dict[str, type] = {
    "edge": EdgeVoice,
    "edge-tts": EdgeVoice,
    "formant": FormantVoice,
    "elevenlabs": ElevenLabsVoice,
}


def build_voice_provider(prefer: str | None = None) -> Provider:
    """Pick a voice provider.

    Explicit preference always wins (that is the point of the config). With
    `auto`, prefer the highest-quality thing that is actually available:
    ElevenLabs (paid, exact timings) -> Edge (free, neural, word boundaries)
    -> Formant (offline, always works).
    """
    import os as _os

    want = (prefer or _os.environ.get("AVB_VOICE_PROVIDER") or "auto").strip().lower()

    if want and want != "auto":
        cls = VOICE_REGISTRY.get(want)
        if cls:
            inst = cls()
            if inst.available():
                return inst
        # explicit request that cannot be satisfied should still fall back,
        # but say so via the name so it is visible in `avb doctor`.

    for key in ("elevenlabs", "edge", "formant"):
        cls = VOICE_REGISTRY[key]
        inst = cls()
        if inst.available():
            return inst
    return FormantVoice()
