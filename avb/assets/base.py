"""Provider abstraction + cost ledger.

Two reasons this layer exists:

1. Vendor risk. The Sora 2 API shut down on 2026-09-24. Any pipeline wired
   directly to one model dies with it. Every provider here implements the
   same interface and is selected by env var, so swapping is a config change.

2. Unit economics. High-volume DTC lives or dies on cost per finished video.
   Every provider reports what it spent, and the ledger rolls that up. If
   you cannot see the number, you cannot control it.

All providers must be async-safe-by-construction: they are pure functions of
(spec, cache_key) -> file path. The orchestrator handles concurrency.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = ["Provider", "Cost", "CostLedger", "Cache", "which"]


def which(prog: str) -> str | None:
    return shutil.which(prog)


# --------------------------------------------------------------------------
# Cache
# --------------------------------------------------------------------------


class Cache:
    """Content-addressed asset store on the local filesystem.

    Same inputs -> same path -> no regeneration. This is what turns
    "change one line" from a full rebuild into a 3-shot rebuild.
    """

    def __init__(self, root: str | Path = ".avb_cache"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.hits = 0
        self.misses = 0
        # The orchestrator fans segment rendering out across threads. The
        # counters and the mkstemp/rename dance both need serialising, or
        # concurrent puts leave stray *.tmp files behind and the hit-rate
        # arithmetic drifts.
        self._lock = threading.Lock()

    def path(self, namespace: str, key: str, ext: str) -> Path:
        d = self.root / namespace
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{key}{ext}"

    def get(self, namespace: str, key: str, ext: str) -> Path | None:
        p = self.path(namespace, key, ext)
        with self._lock:
            if p.is_file() and p.stat().st_size > 0:
                self.hits += 1
                return p
            self.misses += 1
            return None

    def put(self, namespace: str, key: str, ext: str, src: str | Path) -> Path:
        dst = self.path(namespace, key, ext)
        src = Path(src)
        with self._lock:
            if src.parent == dst.parent:
                # Same directory: a plain rename is atomic and costs no
                # copy. shutil.move() would copy then delete the source,
                # which is slower and -- under a parallel render -- enough
                # concurrent deletes to trip a filesystem guard.
                os.replace(str(src), str(dst))
                return dst

            # Cross-directory: stage in the destination so the final rename
            # is atomic and a reader never sees a half-written file.
            fd, tname = tempfile.mkstemp(dir=str(dst.parent), suffix=".tmp")
            os.close(fd)  # mkstemp hands back an open descriptor; close it
            tmp = Path(tname)
            try:
                shutil.copyfile(str(src), str(tmp))
                tmp.replace(dst)
            except Exception:
                tmp.unlink(missing_ok=True)
                raise
        return dst

    def stats(self) -> dict[str, Any]:
        total = self.hits + self.misses
        return {
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hits / total, 4) if total else 0.0,
        }


# --------------------------------------------------------------------------
# Cost
# --------------------------------------------------------------------------


@dataclass
class Cost:
    provider: str
    kind: str  # voice | video | image | music | render
    usd: float
    units: float = 0.0
    unit: str = "sec"


class CostLedger:
    """Accumulates spend across a project run."""

    def __init__(self) -> None:
        self.items: list[Cost] = []

    def add(self, provider: str, kind: str, usd: float, units: float = 0.0, unit: str = "sec") -> Cost:
        c = Cost(provider=provider, kind=kind, usd=round(float(usd), 6), units=round(float(units), 3), unit=unit)
        self.items.append(c)
        return c

    def total(self) -> float:
        return round(sum(i.usd for i in self.items), 4)

    def by_kind(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for i in self.items:
            out[i.kind] = round(out.get(i.kind, 0.0) + i.usd, 4)
        return out

    def by_provider(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for i in self.items:
            out[i.provider] = round(out.get(i.provider, 0.0) + i.usd, 4)
        return out

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(
            {
                "total_usd": self.total(),
                "by_kind": self.by_kind(),
                "by_provider": self.by_provider(),
                "items": [i.__dict__ for i in self.items],
            },
            indent=indent,
        )


# --------------------------------------------------------------------------
# Provider
# --------------------------------------------------------------------------


class Provider(ABC):
    """Base class for every generative asset provider.

    Contract:
      - `available()`   whether credentials/binaries exist right now
      - `price(...)`    USD cost for a hypothetical request (for estimates)
      - `generate(...)` returns a path to a file on disk; idempotent

    A provider must never raise for missing credentials -- it must return
    False from `available()` so the orchestrator can fall back.
    """

    name: str = "base"
    kind: str = "asset"

    @abstractmethod
    def available(self) -> bool:
        ...

    def price(self, **kwargs: Any) -> float:
        return 0.0

    def describe(self) -> str:
        return f"{self.name} ({'active' if self.available() else 'inactive'})"


# --------------------------------------------------------------------------
# Misc helpers
# --------------------------------------------------------------------------


def atomic_write_json(path: str | Path, obj: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tname = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
    os.close(fd)  # same fd-leak trap as Cache.put
    tmp = Path(tname)
    try:
        tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
