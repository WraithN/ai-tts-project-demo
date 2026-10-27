"""Motion footage providers.

MockVideo
    Offline. Returns a deterministic still; the composer supplies the motion
    (Ken Burns, punch-zoom, pan). Rendering motion at composite time rather
    than baking it into a clip is deliberate: it means changing "zoom_in" to
    "punch" costs zero generation.

Real adapters (Veo 3.1, Kling 3.0, Runway Gen-4.5, Seedance) all share the
same call shape and are selected by env var. Note that every one of them is
*asynchronous*: POST -> job id -> poll/webhook -> download. The orchestrator
owns the queue; providers must stay pure.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

import requests

from ..hashing import short_hash
from ..styles import get_style
from .base import Cache, Cost, Provider
from .image import background

__all__ = [
    "MockVideo", "VeoVideo", "KlingVideo",
    "BailianVideo", "MiniMaxVideo", "ZhipuVideo",
    "VideoResult", "build_video_provider",
]


@dataclass
class VideoResult:
    path: Path
    duration: float
    cached: bool
    cost: Cost


class MockVideo(Provider):
    """Offline still + composer-side motion."""

    name = "mock-video"
    kind = "video"
    price_per_sec = 0.0

    def available(self) -> bool:
        return True

    def price(self, seconds: float = 0.0, **_: object) -> float:
        return 0.0

    def generate(self, prompt: str, style: str, seed: int, seconds: float, w: int, h: int, cache: Cache) -> VideoResult:
        spec = get_style(style)
        key = short_hash({"prompt": prompt, "style": style, "seed": seed, "w": w, "h": h})
        hit = cache.get("video", key, ".png")
        if hit:
            return VideoResult(path=hit, duration=seconds, cached=True, cost=Cost(self.name, "video", 0.0))

        img = background(spec.bg_style, seed, int(w * 1.25), int(h * 1.25), spec.palette)
        tmp = cache.root / "video" / f"tmp_{os.getpid()}_{key}.png"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        img.save(tmp)
        final = cache.put("video", key, ".png", tmp)
        return VideoResult(path=final, duration=seconds, cached=False, cost=Cost(self.name, "video", 0.0, seconds))


class _AsyncVideoBase(Provider):
    """Shared plumbing for the asynchronous hosted video APIs.

    Every provider in this family is: submit -> poll -> download. There is no
    synchronous path that hands back an mp4, so don't design for one.
    """

    kind = "video"
    price_per_sec = 0.10
    submit_url = ""
    env_key = ""

    def __init__(self) -> None:
        self.api_key = os.environ.get(self.env_key, "")

    def available(self) -> bool:
        return bool(self.api_key)

    def price(self, seconds: float = 0.0, **_: object) -> float:
        return round(seconds * self.price_per_sec, 6)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def _submit(self, prompt: str, seconds: float, seed: int) -> str:
        raise NotImplementedError

    def _poll(self, job_id: str, timeout: float = 600.0) -> str:
        raise NotImplementedError

    def generate(self, prompt: str, style: str, seed: int, seconds: float, w: int, h: int, cache: Cache) -> VideoResult:
        key = short_hash({"p": self.name, "prompt": prompt, "style": style, "seed": seed, "sec": round(seconds, 2), "w": w, "h": h})
        hit = cache.get("video", key, ".mp4")
        if hit:
            return VideoResult(path=hit, duration=seconds, cached=True, cost=Cost(self.name, "video", 0.0))

        job = self._submit(prompt, seconds, seed)
        url = self._poll(job)
        tmp = cache.root / "video" / f"tmp_{os.getpid()}_{key}.mp4"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        with requests.get(url, stream=True, timeout=300) as r:
            r.raise_for_status()
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    if chunk:
                        f.write(chunk)
        final = cache.put("video", key, ".mp4", tmp)
        return VideoResult(path=final, duration=seconds, cached=False, cost=Cost(self.name, "video", self.price(seconds), seconds))


class VeoVideo(_AsyncVideoBase):
    """Google Veo 3.1. Native audio, longest clips, SynthID watermark
    (cannot be disabled -- plan your disclosure flow around it)."""

    name = "veo-3.1"
    env_key = "GOOGLE_API_KEY"
    price_per_sec = 0.40  # 1080p standard
    submit_url = "https://generativelanguage.googleapis.com/v1beta/models/veo-3.1-generate-001:predictLongRunning"

    def _submit(self, prompt: str, seconds: float, seed: int) -> str:
        payload = {
            "instances": [{"prompt": prompt}],
            "parameters": {
                "aspectRatio": "9:16",
                "durationSeconds": int(max(4, min(60, seconds))),
                "personGeneration": "allow_adult",
                "sampleCount": 1,
            },
        }
        r = requests.post(f"{self.submit_url}?key={self.api_key}", json=payload, timeout=120)
        r.raise_for_status()
        return r.json()["name"]

    def _poll(self, job_id: str, timeout: float = 900.0) -> str:
        url = f"https://generativelanguage.googleapis.com/v1beta/{job_id}?key={self.api_key}"
        deadline = time.time() + timeout
        while time.time() < deadline:
            r = requests.get(url, timeout=60)
            r.raise_for_status()
            d = r.json()
            if d.get("done"):
                return d["response"]["generatedVideos"][0]["video"]["uri"]
            time.sleep(5)
        raise TimeoutError(f"Veo job {job_id} timed out")


class KlingVideo(_AsyncVideoBase):
    """Kuaishou Kling 3.0. Best video-per-dollar; multi-shot generation."""

    name = "kling-3.0"
    env_key = "KLING_API_KEY"
    price_per_sec = 0.10
    submit_url = "https://api.klingai.com/v1/videos/text2video"

    def _submit(self, prompt: str, seconds: float, seed: int) -> str:
        payload = {
            "model_name": "kling-v3",
            "prompt": prompt,
            "duration": "5" if seconds <= 5 else "10",
            "aspect_ratio": "9:16",
            "seed": seed % 100000,
        }
        r = requests.post(self.submit_url, json=payload, headers=self._headers(), timeout=120)
        r.raise_for_status()
        return r.json()["data"]["task_id"]

    def _poll(self, job_id: str, timeout: float = 900.0) -> str:
        url = f"https://api.klingai.com/v1/videos/text2video/{job_id}"
        deadline = time.time() + timeout
        while time.time() < deadline:
            r = requests.get(url, headers=self._headers(), timeout=60)
            r.raise_for_status()
            d = r.json()["data"]
            if d["task_status"] == "succeed":
                return d["task_result"]["videos"][0]["url"]
            if d["task_status"] == "failed":
                raise RuntimeError(d.get("task_status_msg", "Kling generation failed"))
            time.sleep(5)
        raise TimeoutError(f"Kling job {job_id} timed out")




# --------------------------------------------------------------------------
# Domestic providers (Alibaba Bailian / MiniMax / Vidu / Zhipu)
# --------------------------------------------------------------------------
#
# All four are submit -> poll -> download, same shape as Veo/Kling, so they
# inherit _AsyncVideoBase. Auth differs per vendor and is normalised in
# `_headers`/`_submit` only. Prices below are list-rate USD/sec equivalents
# as of 2026-10 and are the *API* rates, not consumer membership credits --
# the two pools do not mix on any of these platforms.


class BailianVideo(_AsyncVideoBase):
    """Alibaba Cloud Bailian / Tongyi Wanxiang (Wan).

    The interesting one for a portfolio: Wan is the only major family in the
    domestic top-5 that open-sourced its weights (Apache 2.0, 1.3B and 14B).
    So this adapter covers both the hosted API and, in principle, a local
    Wan2.x checkpoint behind the same interface -- which is the whole point
    of the provider layer.
    """

    name = "bailian-wan"
    env_key = "BAILIAN_API_KEY"
    price_per_sec = 0.08  # 720p list-rate equivalent
    submit_url = "https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis"

    def _headers(self) -> dict[str, str]:
        # DashScope uses a plain API-Key header, not Bearer.
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json",
                "X-DashScope-Async": "enable"}

    def _submit(self, prompt: str, seconds: float, seed: int) -> str:
        payload = {
            "model": "wan2.2-t2v-plus",
            "input": {"prompt": prompt},
            "parameters": {
                "size": "720*1280",
                "duration": int(max(3, min(15, seconds))),
            },
        }
        r = requests.post(self.submit_url, json=payload, headers=self._headers(), timeout=120)
        r.raise_for_status()
        return r.json()["output"]["task_id"]

    def _poll(self, job_id: str, timeout: float = 900.0) -> str:
        url = f"https://dashscope.aliyuncs.com/api/v1/tasks/{job_id}"
        deadline = time.time() + timeout
        while time.time() < deadline:
            r = requests.get(url, headers=self._headers(), timeout=60)
            r.raise_for_status()
            d = r.json().get("output") or {}
            if d.get("task_status") == "SUCCEEDED":
                return d["video_url"]
            if d.get("task_status") in ("FAILED", "CANCELED"):
                raise RuntimeError(d.get("message", "Bailian generation failed"))
            time.sleep(6)
        raise TimeoutError(f"Bailian job {job_id} timed out")


class MiniMaxVideo(_AsyncVideoBase):
    """MiniMax Hailuo. Strongest filmic realism in the domestic set."""

    name = "minimax-hailuo"
    env_key = "MINIMAX_API_KEY"
    price_per_sec = 0.06
    submit_url = "https://api.minimaxi.com/v1/video_generation"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def _submit(self, prompt: str, seconds: float, seed: int) -> str:
        payload = {
            "model": "Hailuo-02",
            "prompt": prompt,
            "duration": int(max(3, min(10, seconds))),
            "resolution": "768P",
        }
        r = requests.post(self.submit_url, json=payload, headers=self._headers(), timeout=120)
        r.raise_for_status()
        return r.json()["base_resp"]["task_id"] if "base_resp" in r.json() else r.json()["task_id"]

    def _poll(self, job_id: str, timeout: float = 900.0) -> str:
        url = f"https://api.minimaxi.com/v1/query/video_generation?task_id={job_id}"
        deadline = time.time() + timeout
        while time.time() < deadline:
            r = requests.get(url, headers=self._headers(), timeout=60)
            r.raise_for_status()
            d = r.json()
            status = d.get("status")
            if status == "Success":
                return d.get("file_id") and f"https://api.minimaxi.com/v1/files/retrieve?file_id={d['file_id']}"
            if status == "Fail":
                raise RuntimeError(d.get("base_resp", {}).get("status_msg", "MiniMax generation failed"))
            time.sleep(6)
        raise TimeoutError(f"MiniMax job {job_id} timed out")


class ZhipuVideo(_AsyncVideoBase):
    """Zhipu CogVideoX (open weights) via the hosted BigModel API.

    CogVideoX-5B is one of the few video models you can actually self-host
    on a 24GB card. The hosted API is here so the pipeline works without
    owning a GPU; the local server path is the same interface.
    """

    name = "zhipu-cogvideox"
    env_key = "ZHIPU_API_KEY"
    price_per_sec = 0.07
    submit_url = "https://open.bigmodel.cn/api/paas/v4/videos/generations"

    def _submit(self, prompt: str, seconds: float, seed: int) -> str:
        payload = {"model": "cogvideox-2", "prompt": prompt}
        r = requests.post(self.submit_url, json=payload, headers=self._headers(), timeout=120)
        r.raise_for_status()
        return r.json()["id"]

    def _poll(self, job_id: str, timeout: float = 900.0) -> str:
        url = f"https://open.bigmodel.cn/api/paas/v4/async-result/{job_id}"
        deadline = time.time() + timeout
        while time.time() < deadline:
            r = requests.get(url, headers=self._headers(), timeout=60)
            r.raise_for_status()
            d = r.json()
            if d.get("task_status") == "SUCCESS":
                return d["video_result"][0]["url"]
            if d.get("task_status") == "FAIL":
                raise RuntimeError("CogVideoX generation failed")
            time.sleep(6)
        raise TimeoutError(f"CogVideoX job {job_id} timed out")


# --------------------------------------------------------------------------
# Provider selection
# --------------------------------------------------------------------------

_REGISTRY: dict[str, type] = {
    "mock": MockVideo,
    "veo": VeoVideo,
    "kling": KlingVideo,
    "bailian": BailianVideo,
    "wan": BailianVideo,
    "minimax": MiniMaxVideo,
    "hailuo": MiniMaxVideo,
    "zhipu": ZhipuVideo,
    "cogvideox": ZhipuVideo,
}


def build_video_provider(prefer: str | None = None) -> Provider:
    """Pick the first available provider.

    `AVB_VIDEO_PROVIDER` forces a specific one; otherwise we fall back down
    the list. Falling back is the entire point of the abstraction -- when a
    vendor kills an API (see: Sora 2, Sept 2026) the pipeline keeps running.
    """
    order = [prefer or os.environ.get("AVB_VIDEO_PROVIDER") or "mock"]
    order += [k for k in ("veo", "kling", "bailian", "minimax", "zhipu", "mock") if k not in order]
    for key in order:
        cls = _REGISTRY.get(key)
        if not cls:
            continue
        inst = cls()
        if inst.available():
            return inst
    return MockVideo()
