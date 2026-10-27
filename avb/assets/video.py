"""Motion footage providers.

Every provider here is a *hosted* model: Veo 3.1, Kling 3.0, Seedance,
Hailuo, Wan, CogVideoX, Jimeng. There is deliberately no offline stand-in --
a fake clip would produce a video that looks finished and isn't, which is
worse than failing loudly. When nothing is configured, `build_video_provider`
raises `ProviderUnavailable` naming the exact env vars to fill in.

All of them share the same call shape and are selected by env var. Note that
every one of them is *asynchronous*: POST -> job id -> poll -> download. The
orchestrator owns the queue; providers must stay pure.
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path

import requests

from ..hashing import short_hash
from .base import Cache, Cost, Provider

__all__ = [
    "VeoVideo", "KlingVideo",
    "BailianVideo", "MiniMaxVideo", "ZhipuVideo", "JimengVideo", "SeedanceVideo",
    "VideoResult", "build_video_provider", "ProviderUnavailable",
]


class ProviderUnavailable(RuntimeError):
    """Raised when no video provider has credentials.

    Carries `missing` so callers (CLI doctor, web settings) can render a
    precise "set these variables" list instead of a bare failure.
    """

    def __init__(self, message: str, missing: dict[str, tuple[str, ...]] | None = None):
        super().__init__(message)
        self.missing = missing or {}


@dataclass
class VideoResult:
    path: Path
    duration: float
    cached: bool
    cost: Cost


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
# Jimeng (ByteDance / Volcengine)
# --------------------------------------------------------------------------
#
# Two things make this adapter different from the Bearer-token family above:
#
# 1. Auth is the Volcengine HMAC-SHA256 request signature (AK/SK), not a
#    bearer token. It is AWS-SigV4-shaped: derive a signing key through
#    date/region/service, then sign a canonical request. Implemented below
#    because "use the official SDK" is not an option for a dependency-light
#    pipeline, and the algorithm is ~30 lines of hmac/hashlib.
# 2. The result URL expires in about an hour. The base class downloads
#    immediately after polling, which is the correct shape -- but if you ever
#    change generate() to return URLs instead of local files, you will lose
#    clips silently. Jimeng also caps concurrency at 1, so fan it out through
#    the orchestrator's queue rather than threads.

_REGION = "cn-north-1"
_SERVICE = "cv"


def _volc_sign(secret_key: str, date_short: str) -> bytes:
    """Derive the Volcengine signing key: date -> region -> service -> 'request'."""
    import hashlib
    import hmac

    def _h(key: bytes | str, msg: str) -> bytes:
        return hmac.new(key if isinstance(key, bytes) else key.encode(), msg.encode(), hashlib.sha256).digest()

    k_date = _h(secret_key, date_short)
    k_region = _h(k_date, _REGION)
    k_service = _h(k_region, _SERVICE)
    return _h(k_service, "request")


def _volc_authorization(
    method: str,
    query: dict[str, str],
    body: bytes,
    ak: str,
    sk: str,
    now: datetime | None = None,
) -> dict[str, str]:
    """Build the `Authorization` header and supporting headers for visual.volcengineapi.com."""
    import hashlib
    import hmac

    now = now or datetime.utcnow()
    x_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_short = now.strftime("%Y%m%d")

    payload_hash = hashlib.sha256(body).hexdigest()
    # Query string must be sorted by key, URI-encoded, exactly as sent.
    canonical_qs = "&".join(f"{_uri_encode(k)}={_uri_encode(v)}" for k, v in sorted(query.items()))
    headers = {
        "content-type": "application/json",
        "host": "visual.volcengineapi.com",
        "x-content-sha256": payload_hash,
        "x-date": x_date,
    }
    signed_headers = ";".join(sorted(headers))
    canonical_headers = "".join(f"{k}:{headers[k]}\n" for k in sorted(headers))
    canonical_request = "\n".join(
        [method, "/", canonical_qs, canonical_headers, signed_headers, payload_hash]
    )
    scope = f"{date_short}/{_REGION}/{_SERVICE}/request"
    string_to_sign = "\n".join(
        ["HMAC-SHA256", x_date, scope, hashlib.sha256(canonical_request.encode()).hexdigest()]
    )
    signing_key = _volc_sign(sk, date_short)
    signature = hmac.new(signing_key, string_to_sign.encode(), hashlib.sha256).hexdigest()

    headers["authorization"] = (
        f"HMAC-SHA256 Credential={ak}/{scope}, SignedHeaders={signed_headers}, Signature={signature}"
    )
    return headers


def _uri_encode(s: str) -> str:
    from urllib.parse import quote

    return quote(str(s), safe="-_.~")


class JimengVideo(_AsyncVideoBase):
    """ByteDance Jimeng (即梦) via the Volcengine visual API.

    Text-to-video and image-to-video from the same model family that powers
    the consumer Jimeng product. Two practical notes that bite people:

    - Auth is the Volcengine AK/SK signature, so this needs
      `VOLC_ACCESS_KEY` + `VOLC_SECRET_KEY` (not a single token).
    - Result URLs expire in ~1 hour and concurrency is capped at 1. The
      pipeline downloads the clip the moment polling succeeds, so expiry is
      not a problem in normal operation -- but do not cache URLs.

    `req_key` selects the model generation; `jimeng_vgfm_t2v_l20` is the
    S2.0 Pro text-to-video endpoint. Override via `JIMENG_REQ_KEY` when
    ByteDance moves the identifier (they version these aggressively).
    """

    name = "jimeng"
    price_per_sec = 0.05
    submit_url = "https://visual.volcengineapi.com"
    env_key = "VOLC_ACCESS_KEY"
    env_key_secret = "VOLC_SECRET_KEY"

    def __init__(self) -> None:
        self.api_key = os.environ.get(self.env_key, "")
        self.secret_key = os.environ.get(self.env_key_secret, "")
        self.req_key = os.environ.get("JIMENG_REQ_KEY", "jimeng_vgfm_t2v_l20")

    def available(self) -> bool:
        return bool(self.api_key and self.secret_key)

    def _call(self, action: str, payload: dict) -> dict:
        import json as _json

        body = _json.dumps(payload, separators=(",", ":")).encode()
        query = {"Action": action, "Version": "2022-08-31"}
        headers = _volc_authorization("POST", query, body, self.api_key, self.secret_key)
        # Sign over the canonical query; send it on the URL too.
        qs = "&".join(f"{k}={v}" for k, v in sorted(query.items()))
        r = requests.post(f"{self.submit_url}?{qs}", data=body, headers=headers, timeout=120)
        r.raise_for_status()
        return r.json()

    def _submit(self, prompt: str, seconds: float, seed: int) -> str:
        payload = {
            "req_key": self.req_key,
            "prompt": prompt[:150],  # S2.0 Pro documents a 150-char cap
            "seed": int(seed) if seed and seed > 0 else -1,
            "aspect_ratio": "9:16",
        }
        d = self._call("CVSync2AsyncSubmitTask", payload)
        code = d.get("code", d.get("status"))
        if code not in (10000, 0, "10000"):
            raise RuntimeError(f"Jimeng submit failed: {d.get('message', d)}")
        task_id = (d.get("data") or {}).get("task_id")
        if not task_id:
            raise RuntimeError(f"Jimeng returned no task_id: {d}")
        return task_id

    def _poll(self, job_id: str, timeout: float = 900.0) -> str:
        deadline = time.time() + timeout
        while time.time() < deadline:
            d = self._call("CVSync2AsyncGetResult", {"req_key": self.req_key, "task_id": job_id})
            data = d.get("data") or {}
            status = str(data.get("status", "")).lower()
            if status in ("done", "success", "succeeded", "10000"):
                url = data.get("video_url") or data.get("resp_data")
                if isinstance(url, dict):
                    url = url.get("video_url")
                if not url:
                    # Some generations return JSON-in-string under resp_data.
                    if isinstance(data.get("resp_data"), str) and "video_url" in data["resp_data"]:
                        import json as _json

                        url = _json.loads(data["resp_data"]).get("video_url")
                if not url:
                    raise RuntimeError(f"Jimeng done but no video_url: {data}")
                return url
            if status in ("not_found", "expired", "failed", "error", "cancelled"):
                raise RuntimeError(f"Jimeng job {job_id} ended as {status}: {data}")
            time.sleep(8)
        raise TimeoutError(f"Jimeng job {job_id} timed out")



# --------------------------------------------------------------------------
# Seedance (ByteDance / Volcengine Ark)
# --------------------------------------------------------------------------
#
# Seedance is the video-generation family behind Doubao (豆包) on Volcengine
# Ark. Two things make it different from the sibling Jimeng adapter:
#
# 1. Auth is a plain Bearer API key -- not the AK/SK HMAC-SHA256 signature
#    that Jimeng needs. Key comes from the Ark console. So this adapter is
#    ~15 lines of submit/poll and nothing else.
# 2. Billing is per-token (15 CNY/M tokens for pro, 4.2 for fast), not
#    per-second. `price_per_sec` below is a rough USD equivalent purely so
#    the shared cost ledger has something to report.
#
# Model tiers: "doubao-seedance-2-0-pro" (quality) and
# "doubao-seedance-2-0-mini" (fast/cheap). Override via SEEDANCE_MODEL --
# ByteDance revs these identifiers often and a stale id is the single most
# common failure mode here.
#
# Submit -> poll -> download, so it inherits _AsyncVideoBase unchanged.


class SeedanceVideo(_AsyncVideoBase):
    """ByteDance Seedance via the Volcengine Ark API.

    Text-to-video and image-to-video. Newer and cleaner than Jimeng: plain
    Bearer auth, JSON in/out, status strings 'queued' / 'running' /
    'succeeded' / 'failed'. The returned video URL is a pre-signed link valid
    for 24h, and the base class downloads it immediately, so expiry is not a
    concern in normal operation.
    """

    name = "seedance"
    env_key = "SEEDANCE_API_KEY"
    price_per_sec = 0.05  # rough USD equivalent of token billing
    submit_url = "https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks"

    def __init__(self) -> None:
        self.api_key = os.environ.get(self.env_key, "")
        self.model = os.environ.get("SEEDANCE_MODEL", "doubao-seedance-2-0-pro")

    def _submit(self, prompt: str, seconds: float, seed: int) -> str:
        payload = {
            "model": self.model,
            "content": [{"type": "text", "text": prompt[:600]}],
            "ratio": "9:16",  # vertical ad default
            "duration": int(max(2, min(12, seconds))),
            "watermark": False,
        }
        r = requests.post(
            self.submit_url,
            headers=self._headers(),
            json=payload,
            timeout=120,
        )
        r.raise_for_status()
        d = r.json()
        task_id = d.get("id")
        if not task_id:
            raise RuntimeError(f"Seedance returned no task id: {d}")
        return task_id

    def _poll(self, job_id: str, timeout: float = 900.0) -> str:
        url = f"{self.submit_url}/{job_id}"
        deadline = time.time() + timeout
        while time.time() < deadline:
            r = requests.get(url, headers=self._headers(), timeout=60)
            r.raise_for_status()
            d = r.json()
            status = str(d.get("status", "")).lower()
            if status == "succeeded":
                # Ark nests the link under content.video_url (or data.video_url
                # depending on the model revision); accept either.
                content = d.get("content") or d.get("data") or {}
                if isinstance(content, list):
                    content = content[0] if content else {}
                url_out = content.get("video_url") if isinstance(content, dict) else None
                if not url_out:
                    url_out = d.get("video_url")
                if not url_out:
                    raise RuntimeError(f"Seedance succeeded but no video_url: {d}")
                return url_out
            if status in ("failed", "cancelled", "expired"):
                raise RuntimeError(f"Seedance job {job_id} ended as {status}: {d}")
            time.sleep(5)
        raise TimeoutError(f"Seedance job {job_id} timed out")


# --------------------------------------------------------------------------
# Provider selection
# --------------------------------------------------------------------------

_REGISTRY: dict[str, type] = {
    "veo": VeoVideo,
    "kling": KlingVideo,
    "bailian": BailianVideo,
    "wan": BailianVideo,
    "minimax": MiniMaxVideo,
    "hailuo": MiniMaxVideo,
    "zhipu": ZhipuVideo,
    "cogvideox": ZhipuVideo,
    "jimeng": JimengVideo,
    "dreamina": JimengVideo,
    "seedance": SeedanceVideo,
}

# Probe order when the caller says `auto`. Roughly: cheapest-good first.
_DEFAULT_ORDER = ("seedance", "kling", "jimeng", "veo", "bailian", "minimax", "zhipu")


def required_envs(key: str) -> tuple[str, ...]:
    """Env vars a provider needs before it can be used."""
    cls = _REGISTRY.get(key)
    if cls is None:
        return ()
    out: list[str] = []
    for attr in ("env_key", "env_key_secret"):
        v = getattr(cls, attr, "")
        if v:
            out.append(v)
    return tuple(out)


def build_video_provider(prefer: str | None = None) -> Provider:
    """Pick the first provider that actually has credentials.

    `AVB_VIDEO_PROVIDER` (or `prefer`) forces a specific one; otherwise we
    walk `_DEFAULT_ORDER`. There is no offline fallback: if nothing is
    configured we raise `ProviderUnavailable` listing what to set, because
    silently producing fake footage would ship a broken ad.

    Falling back across *configured* vendors is still the point of the
    abstraction -- when a vendor kills an API (see: Sora 2, Sept 2026) the
    pipeline keeps running on the next one.
    """
    want = (prefer or os.environ.get("AVB_VIDEO_PROVIDER") or "auto").strip().lower()
    order: list[str] = [] if want in ("", "auto") else [want]
    order += [k for k in _DEFAULT_ORDER if k not in order]

    missing: dict[str, tuple[str, ...]] = {}
    for key in order:
        cls = _REGISTRY.get(key)
        if not cls:
            continue
        inst = cls()
        if inst.available():
            return inst
        missing[key] = required_envs(key)

    if want not in ("", "auto") and want in missing:
        env_list = ", ".join(missing[want]) or "(none)"
        known = ", ".join(sorted(_REGISTRY))
        raise ProviderUnavailable(
            f"video provider '{want}' is not configured. Set: {env_list}. "
            f"Known providers: {known}",
            missing,
        )

    env_list = "; ".join(f"{k}: {', '.join(v) or '(none)'}" for k, v in missing.items())
    raise ProviderUnavailable(
        f"No video provider configured -- set at least one of: {env_list}",
        missing,
    )
