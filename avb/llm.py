"""OpenAI-compatible chat client.

Covers DeepSeek (V4 family, including the reasoning models), Moonshot,
Zhipu, local vLLM / llama.cpp `--jinja` servers, and OpenAI itself. The
contract is the standard `/chat/completions` shape, so swapping vendors is
a config change:

    [llm]
    provider  = "deepseek"
    model     = "deepseek-flash"
    base_url  = "https://api.deepseek.com/v1"
    api_key_env = "DEEPSEEK_API_KEY"

Two behaviours worth knowing about:

1. **Reasoning models.** DeepSeek V4 emits `reasoning_content` alongside
   `content` and bills for both. A short `max_tokens` silently truncates the
   answer and leaves `content` empty/None -- the failure looks like "the model
   returned nothing" rather than a budget error. This client raises the
   default budget and surfaces the reasoning length in the error message so
   the cause is obvious.

2. **JSON mode.** `chat_json()` strips fences, finds the outermost JSON
   object/array, and retries once at temperature 0 if parsing fails. It
   never raises for missing keys -- callers decide what to fall back to.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

import requests

from .config import LLMConfig

__all__ = ["LLMClient", "LLMError", "LLMResponse", "get_client", "set_client"]


class LLMError(RuntimeError):
    pass


@dataclass
class LLMResponse:
    content: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    cached_tokens: int = 0
    latency_s: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class LLMClient:
    """Thin, synchronous OpenAI-compatible chat wrapper."""

    def __init__(self, cfg: LLMConfig | None = None):
        self.cfg = cfg or LLMConfig()
        self.session = requests.Session()

    # -- plumbing ---------------------------------------------------------
    @property
    def available(self) -> bool:
        return bool(self.cfg.enabled and self.cfg.api_key)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.cfg.api_key}",
            "Content-Type": "application/json",
        }

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_mode: bool = False,
        extra: dict[str, Any] | None = None,
    ) -> LLMResponse:
        if not self.available:
            raise LLMError(
                f"LLM not configured: set ${self.cfg.api_key_env} "
                f"(provider={self.cfg.provider}, model={self.cfg.model})"
            )

        body: dict[str, Any] = {
            "model": self.cfg.model,
            "messages": messages,
            "temperature": self.cfg.temperature if temperature is None else temperature,
            "max_tokens": self.cfg.max_tokens if max_tokens is None else max_tokens,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        # DeepSeek V4 effort knob. Harmless on endpoints that reject unknown
        # fields only if we omit it -- so only send when explicitly set.
        if self.cfg.effort:
            body["effort"] = self.cfg.effort
        if extra:
            body.update(extra)

        url = self.cfg.base_url.rstrip("/") + "/chat/completions"
        t0 = time.time()
        try:
            r = self.session.post(url, headers=self._headers(), json=body, timeout=self.cfg.timeout)
        except requests.RequestException as e:
            raise LLMError(f"LLM request failed: {e}") from e
        latency = time.time() - t0

        if r.status_code == 401:
            raise LLMError(f"LLM auth failed (401) -- check ${self.cfg.api_key_env}")
        if r.status_code == 429:
            raise LLMError("LLM rate limited (429) -- back off and retry")
        if r.status_code >= 400:
            raise LLMError(f"LLM HTTP {r.status_code}: {r.text[:400]}")

        try:
            data = r.json()
        except Exception as e:
            raise LLMError(f"LLM returned non-JSON body: {r.text[:200]}") from e

        try:
            choice = data["choices"][0]
            msg = choice.get("message") or {}
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"LLM response missing choices: {str(data)[:300]}") from e

        content = msg.get("content") or ""
        reasoning = msg.get("reasoning_content") or ""
        usage = data.get("usage") or {}
        rdet = usage.get("completion_tokens_details") or {}
        pdet = usage.get("prompt_tokens_details") or {}

        if not content.strip():
            raise LLMError(
                "LLM returned empty content. On reasoning models this usually means "
                f"max_tokens={body['max_tokens']} was eaten by reasoning "
                f"(reasoning_tokens={rdet.get('reasoning_tokens', 0)}). "
                "Raise [llm].max_tokens or lower effort."
            )

        return LLMResponse(
            content=content,
            model=data.get("model", self.cfg.model),
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
            reasoning_tokens=int(rdet.get("reasoning_tokens", 0)),
            cached_tokens=int(pdet.get("cached_tokens", 0)),
            latency_s=round(latency, 3),
            raw=data,
        )

    # -- JSON convenience -------------------------------------------------
    def chat_json(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        retries: int = 1,
    ) -> Any:
        """Chat and parse a JSON object/array out of the reply.

        Falls back to fence-stripping + outermost-bracket extraction, then
        retries once at temperature 0. Raises LLMError only if all of that
        fails -- callers typically catch it and use the rule-based path.
        """
        last_err: Exception | None = None
        for attempt in range(retries + 1):
            temp = 0.0 if attempt else (self.cfg.temperature if temperature is None else temperature)
            resp = self.chat(messages, temperature=temp, max_tokens=max_tokens, json_mode=(attempt == 0))
            try:
                return _extract_json(resp.content)
            except Exception as e:
                last_err = e
        raise LLMError(f"could not parse JSON from LLM reply: {last_err}")


def _extract_json(raw: str) -> Any:
    text = raw.strip()
    # strip ```json ... ``` fences
    text = re.sub(r"^```(?:json|JSON)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # outermost object or array
    for open_c, close_c in (("{", "}"), ("[", "]")):
        start = text.find(open_c)
        end = text.rfind(close_c)
        if 0 <= start < end:
            return json.loads(text[start : end + 1])
    raise ValueError("no JSON found in reply")


# --------------------------------------------------------------------------
# Process-wide singleton (so the pipeline and CLI share one client)
# --------------------------------------------------------------------------

_client: LLMClient | None = None


def get_client(cfg: LLMConfig | None = None) -> LLMClient:
    """Process-wide client.

    With no argument this builds from the layered config (avb.toml + .env),
    NOT from bare LLMConfig() defaults -- otherwise the API key never gets
    loaded and every LLM call silently degrades to the rule-based fallback.
    """
    global _client
    if cfg is not None:
        _client = LLMClient(cfg)
        return _client
    if _client is None:
        from .config import load_config

        _client = LLMClient(load_config().llm)
    return _client


def set_client(client: LLMClient | None) -> None:
    global _client
    _client = client
