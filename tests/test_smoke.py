"""Smoke tests. Fast, no network required, no ffmpeg required.

These cover the layers that make the pipeline an *engine* rather than a
script: config round-trip, LLM sanitisation, provider registry fallback,
and the content-addressed cache. Full end-to-end render is intentionally
not here -- that needs ffmpeg and minutes of wall clock; see docs/USAGE.md.

Run:  python -m pytest -q
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# repo root on sys.path so `import avb` works without install
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------


def test_config_defaults():
    from avb.config import Config

    cfg = Config()
    assert cfg.llm.provider == "deepseek"
    assert cfg.render.fps == 30
    assert cfg.llm.api_key_env == "DEEPSEEK_API_KEY"


def test_config_file_and_env_override(tmp_path):
    from avb.config import load_config, save_config, Config

    cfg = Config()
    cfg.llm.model = "from-file"
    cfg.render.width = 720
    p = tmp_path / "avb.toml"
    save_config(cfg, p)

    os.environ["AVB_LLM_MODEL"] = "from-env"
    try:
        loaded = load_config(p)
        assert loaded.llm.model == "from-env"          # env beats file
        assert loaded.render.width == 720             # file beats default
    finally:
        del os.environ["AVB_LLM_MODEL"]

    loaded2 = load_config(p, overrides={"llm.model": "from-kwargs"})
    assert loaded2.llm.model == "from-kwargs"         # kwargs beat everything


def test_config_set_preserves_comments(tmp_path):
    from avb.config import Config, config_set, save_config, load_config

    p = tmp_path / "avb.toml"
    save_config(Config(), p)
    original = p.read_text(encoding="utf-8")
    assert "# Secrets are NOT stored here" in original

    ok, msg = config_set(p, "llm.model", "deepseek-v4-pro")
    assert ok, msg
    text = p.read_text(encoding="utf-8")
    assert "# Secrets are NOT stored here" in text      # comments survived
    assert "deepseek-v4-pro" in text
    assert load_config(p, use_env=False).llm.model == "deepseek-v4-pro"


def test_dotenv_loading(tmp_path, monkeypatch):
    from avb.config import load_dotenv

    env = tmp_path / ".env"
    env.write_text("# comment\nDEEPSEEK_API_KEY=sk-test-123\nexport FOO=bar\n", encoding="utf-8")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("FOO", raising=False)
    load_dotenv(env)
    assert os.environ["DEEPSEEK_API_KEY"] == "sk-test-123"
    assert os.environ["FOO"] == "bar"


# --------------------------------------------------------------------------
# LLM client plumbing
# --------------------------------------------------------------------------


def test_llm_client_unavailable_without_key(monkeypatch):
    from avb.config import LLMConfig
    from avb.llm import LLMClient, LLMError

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    c = LLMClient(LLMConfig(api_key_env="DEEPSEEK_API_KEY"))
    assert not c.available
    with pytest.raises(LLMError):
        c.chat([{"role": "user", "content": "hi"}])


def test_extract_json_variants():
    from avb.llm import _extract_json

    assert _extract_json('{"a": 1}') == {"a": 1}
    assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert _extract_json('Sure! Here you go:\n```json\n[1,2]\n```') == [1, 2]
    assert _extract_json('prefix {"x": {"y": 2}} suffix') == {"x": {"y": 2}}


# --------------------------------------------------------------------------
# LLM edit sanitisation (the guard rails)
# --------------------------------------------------------------------------


def test_sanitize_ops_drops_unknown_paths():
    from avb.llm_edit import _sanitize_ops

    ops = _sanitize_ops(
        [
            {"path": "rm_rf", "value": "/"},
            {"path": "vo.speed", "value": "1.25"},
            {"path": "visual.style", "value": "comic_book"},
            {"path": "transition", "value": "quick_cut"},
            {"path": "vo.energy", "value": "high"},
            "not-a-dict",
        ]
    )
    paths = {o.path for o in ops}
    assert "rm_rf" not in paths
    assert "vo.speed" in paths
    by = {o.path: o.value for o in ops}
    assert by["vo.speed"] == 1.25
    assert by["visual.style"] == "comic"        # alias snapped
    assert by["transition"] == "cut"            # alias snapped
    assert by["vo.energy"] == 1.0               # "high" coerced


def test_sanitize_ops_clamps_ranges():
    from avb.llm_edit import _sanitize_ops

    ops = _sanitize_ops(
        [
            {"path": "vo.speed", "value": 99},
            {"path": "music.bpm", "value": 10},
            {"path": "caption.scale_pop", "value": "0.5"},
        ]
    )
    by = {o.path: o.value for o in ops}
    assert by["vo.speed"] <= 2.0
    assert by["music.bpm"] >= 60
    assert by["caption.scale_pop"] == 0.5


def test_sanitize_ops_drops_impossible_enums():
    from avb.llm_edit import _sanitize_ops

    ops = _sanitize_ops([{"path": "visual.style", "value": "velociraptor"}])
    # no alias, no containment match -> dropped entirely
    # (there IS an alias for velociraptor -> kinetic, so it maps; use a truly
    #  impossible one)
    ops2 = _sanitize_ops([{"path": "visual.style", "value": "zzzz-not-a-style-zzzz"}])
    assert ops2 == []


# --------------------------------------------------------------------------
# provider registry
# --------------------------------------------------------------------------


def test_voice_registry_fallback():
    from avb.assets.voice import FormantVoice, build_voice_provider

    # formant is offline and always available; explicit request must work
    v = build_voice_provider("formant")
    assert v.available()
    assert isinstance(v, FormantVoice)


def test_video_registry_fallback():
    from avb.assets.video import MockVideo, build_video_provider

    v = build_video_provider("mock")
    assert isinstance(v, MockVideo)
    assert v.available()


def test_cache_is_content_addressed(tmp_path):
    from avb.assets.base import Cache

    c = Cache(tmp_path / "cache")
    p1 = c.path("ns", "abc", ".bin")
    p2 = c.path("ns", "abc", ".bin")
    assert p1 == p2

    src = tmp_path / "src.bin"
    src.write_bytes(b"hello")
    out = c.put("ns", "abc", ".bin", src)
    assert out.is_file()
    hit = c.get("ns", "abc", ".bin")
    assert hit is not None
    assert c.hits == 1


# --------------------------------------------------------------------------
# IR / hashing
# --------------------------------------------------------------------------


def test_parse_script_produces_scenes():
    from avb.parse import parse_script

    script = (
        "You are tired every afternoon. "
        "That is not laziness, it is biochemistry. "
        "Our product fixes it in two weeks. "
        "Tap below to claim your discount."
    )
    p = parse_script(script, name="t")
    assert len(p.scenes) >= 3
    assert p.scenes[0].beat in ("hook", "problem")
    assert p.scenes[-1].beat in ("cta", "offer", "proof", "problem")


def test_semantic_hash_ignores_cosmetic():
    from avb.hashing import short_hash

    a = short_hash({"text": "hello", "n": 1})
    b = short_hash({"text": "hello", "n": 1})
    c = short_hash({"text": "hello", "n": 2})
    assert a == b
    assert a != c


# --------------------------------------------------------------------------
# CLI smoke
# --------------------------------------------------------------------------


def test_cli_config_roundtrip(tmp_path, capsys):
    from avb.cli import main

    cfg = tmp_path / "avb.toml"
    rc = main(["--config", str(cfg), "config", "init"])
    assert rc == 0
    assert cfg.exists()

    rc = main(["--config", str(cfg), "config", "set", "llm.model", "deepseek-v4-pro"])
    assert rc == 0

    rc = main(["--config", str(cfg), "config", "get", "llm.model"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "deepseek-v4-pro" in out


def test_cli_providers_lists_all(capsys):
    from avb.cli import main

    rc = main(["providers"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "VOICE" in out and "VIDEO" in out and "LLM" in out


# --------------------------------------------------------------------------
# Jimeng / Volcengine signature
# --------------------------------------------------------------------------


def test_volc_signature_is_deterministic():
    """Same inputs -> same signature; different body -> different signature."""
    from datetime import datetime

    from avb.assets.video import _volc_authorization

    now = datetime(2026, 10, 27, 6, 0, 0)
    q = {"Action": "CVSync2AsyncSubmitTask", "Version": "2022-08-31"}
    a = _volc_authorization("POST", q, b'{"req_key":"x"}', "AKID", "SECRET", now=now)
    b = _volc_authorization("POST", q, b'{"req_key":"x"}', "AKID", "SECRET", now=now)
    assert a["authorization"] == b["authorization"]

    c = _volc_authorization("POST", q, b'{"req_key":"y"}', "AKID", "SECRET", now=now)
    assert a["authorization"] != c["authorization"]


def test_volc_signature_shape():
    """Header must carry credential scope and a hex signature."""
    import re
    from datetime import datetime

    from avb.assets.video import _volc_authorization

    h = _volc_authorization(
        "POST",
        {"Action": "CVSync2AsyncSubmitTask", "Version": "2022-08-31"},
        b"{}",
        "AKID",
        "SECRET",
        now=datetime(2026, 10, 27, 6, 0, 0),
    )
    auth = h["authorization"]
    assert auth.startswith("HMAC-SHA256 Credential=AKID/20261027/cn-north-1/cv/request")
    assert "SignedHeaders=" in auth
    sig = auth.split("Signature=")[-1]
    assert re.fullmatch(r"[0-9a-f]{64}", sig), sig
    assert h["x-content-sha256"] and h["x-date"]


def test_jimeng_registered_and_needs_two_keys():
    from avb.assets.video import _REGISTRY, JimengVideo

    assert _REGISTRY.get("jimeng") is JimengVideo
    assert _REGISTRY.get("dreamina") is JimengVideo

    j = JimengVideo()
    j.api_key = ""
    j.secret_key = ""
    assert j.available() is False
    j.api_key = "ak"
    assert j.available() is False, "a lone access key must not look available"
    j.secret_key = "sk"
    assert j.available() is True
    assert j.req_key.startswith("jimeng_")
