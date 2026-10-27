"""Content addressing: every derived asset is keyed by a hash of the
*semantic* inputs that produced it.

This is what makes conversational editing cheap. When a user says
"make the intro punchier", we patch the IR, re-hash every scene, and
diff. Only the scenes whose hash changed need new assets -- everything
else is a cache hit and costs nothing.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

__all__ = ["canonical", "sha1", "short_hash"]


def canonical(obj: Any) -> str:
    """Deterministic JSON encoding.

    sort_keys + no whitespace means the same logical IR always yields
    the same bytes, which is required for a stable cache key.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def short_hash(obj: Any, length: int = 12) -> str:
    """Stable short digest of any JSON-able object."""
    return sha1(canonical(obj))[:length]
