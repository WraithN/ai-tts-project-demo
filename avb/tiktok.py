"""TikTok Content Posting API integration.

Implements the Direct Post flow:
  1. OAuth2 authorize -> code -> access_token (24h) + refresh_token (365d)
  2. POST /v2/post/publish/video/init/ -> upload_url
  3. PUT chunks to upload_url (Content-Range)
  4. POST /v2/post/publish/status/fetch/ -> poll until PUBLISHED

Docs: https://developers.tiktok.com/doc/content-posting-api-get-started

Requires TIKTOK_CLIENT_KEY + TIKTOK_CLIENT_SECRET (register at
developers.tiktok.com and request the video.publish scope).

Note: without TikTok's Content Posting Audit (2-6 weeks), all posts are
forced to SELF_ONLY (private). Plan for the audit if you need public posts.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import requests

BASE = "https://open.tiktokapis.com"
UPLOAD_BASE = "https://open-upload.tiktokapis.com"

SCOPES = ["video.publish", "user.info.basic"]
CHUNK_SIZE = 10 * 1024 * 1024  # 10 MB


def get_auth_url(client_key: str, redirect_uri: str, state: str = "avb") -> str:
    """Build the TikTok OAuth2 authorization URL."""
    from urllib.parse import urlencode

    params = {
        "client_key": client_key,
        "scope": ",".join(SCOPES),
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "state": state,
    }
    return f"https://www.tiktok.com/v2/auth/authorize/?{urlencode(params)}"


def exchange_code(code: str, client_key: str, client_secret: str, redirect_uri: str) -> dict:
    """Exchange OAuth2 code for access + refresh tokens."""
    r = requests.post(
        f"{BASE}/v2/oauth/token/",
        data={
            "client_key": client_key,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        },
        timeout=30,
    )
    r.raise_for_status()
    d = r.json()
    if "access_token" not in d:
        raise RuntimeError(f"TikTok token exchange failed: {d}")
    return {
        "access_token": d["access_token"],
        "refresh_token": d.get("refresh_token", ""),
        "expires_in": d.get("expires_in", 86400),
        "open_id": d.get("open_id", ""),
        "obtained_at": time.time(),
    }


def refresh_token(refresh_tok: str, client_key: str, client_secret: str) -> dict:
    """Refresh an expired access token."""
    r = requests.post(
        f"{BASE}/v2/oauth/token/",
        data={
            "client_key": client_key,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_tok,
        },
        timeout=30,
    )
    r.raise_for_status()
    d = r.json()
    return {
        "access_token": d["access_token"],
        "refresh_token": d.get("refresh_token", refresh_tok),
        "expires_in": d.get("expires_in", 86400),
        "obtained_at": time.time(),
    }


def query_creator_info(access_token: str) -> dict:
    """Get the creator's available settings (privacy levels, etc.)."""
    r = requests.post(
        f"{BASE}/v2/post/publish/creator_info/query/",
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        json={"max_video_count_to_post": 1},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def upload_video(
    video_path: Path,
    access_token: str,
    title: str = "",
    hashtags: list[str] | None = None,
    privacy_level: str = "SELF_ONLY",
) -> dict:
    """Upload and publish a video via TikTok Direct Post.

    Flow: init -> chunked PUT upload -> poll status.
    """
    hashtags = hashtags or []
    file_size = video_path.stat().st_size
    chunk_size = min(CHUNK_SIZE, file_size)
    total_chunks = max(1, (file_size + chunk_size - 1) // chunk_size)

    # 1. Init
    caption = title
    if hashtags:
        caption += " " + " ".join(f"#{h.lstrip('#')}" for h in hashtags)

    init_payload = {
        "post_info": {
            "title": caption[:2200],  # TikTok caption limit
            "privacy_level": privacy_level,
            "disable_duet": False,
            "disable_comment": False,
            "disable_stitch": False,
        },
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": file_size,
            "chunk_size": chunk_size,
            "total_chunk_count": total_chunks,
        },
    }

    r = requests.post(
        f"{BASE}/v2/post/publish/video/init/",
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        json=init_payload,
        timeout=30,
    )
    r.raise_for_status()
    init_data = r.json()
    publish_id = init_data.get("publish_id", "")
    upload_url = init_data.get("upload_url", "")

    if not upload_url:
        raise RuntimeError(f"TikTok init failed: {init_data}")

    # 2. Chunked upload
    with open(video_path, "rb") as f:
        for i in range(total_chunks):
            chunk = f.read(chunk_size)
            start = i * chunk_size
            end = start + len(chunk) - 1
            r = requests.put(
                upload_url,
                headers={
                    "Content-Range": f"bytes {start}-{end}/{file_size}",
                    "Content-Type": "video/mp4",
                },
                data=chunk,
                timeout=300,
            )
            if r.status_code not in (200, 201, 206):
                raise RuntimeError(f"Chunk {i} upload failed: {r.status_code} {r.text[:200]}")

    # 3. Poll status (brief -- publish is async on TikTok's side)
    status = "IN_PROGRESS"
    for _ in range(12):
        time.sleep(5)
        r = requests.post(
            f"{BASE}/v2/post/publish/status/fetch/",
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            json={"publish_id": publish_id},
            timeout=30,
        )
        d = r.json().get("data", {})
        status = d.get("status", "UNKNOWN")
        if status in ("PUBLISH_COMPLETE", "PUBLISH_FAILED", "FAILED"):
            break

    return {
        "publish_id": publish_id,
        "status": status,
        "privacy_level": privacy_level,
        "caption": caption,
    }


def ensure_token(
    token_path: Path = Path(".tiktok_tokens.json"),
) -> str | None:
    """Load a valid access token, refreshing if expired. Returns None if not authorized."""
    if not token_path.exists():
        return None
    data = json.loads(token_path.read_text(encoding="utf-8"))
    obtained = data.get("obtained_at", 0)
    expires_in = data.get("expires_in", 86400)

    if time.time() - obtained < expires_in - 60:
        return data.get("access_token")

    # Expired -> refresh
    client_key = os.environ.get("TIKTOK_CLIENT_KEY", "")
    client_secret = os.environ.get("TIKTOK_CLIENT_SECRET", "")
    refresh_tok = data.get("refresh_token", "")
    if not (client_key and client_secret and refresh_tok):
        return None

    try:
        new_tokens = refresh_token(refresh_tok, client_key, client_secret)
        token_path.write_text(json.dumps(new_tokens), encoding="utf-8")
        return new_tokens.get("access_token")
    except Exception:
        return None
