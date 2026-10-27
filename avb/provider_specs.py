"""Machine-readable description of every provider and the keys it needs.

The web settings page and `avb providers` both render from this table, so
adding a vendor is one entry here plus one adapter class -- no UI work.

Secrets are never stored in avb.toml; this table only names the env var that
holds each one. The settings page writes them into `.env` (gitignored).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

__all__ = [
    "EnvSpec", "ProviderSpec",
    "VIDEO_SPECS", "VOICE_SPECS", "LLM_SPECS", "PUBLISH_SPECS",
    "KINDS", "spec_for", "specs_for_kind", "mask", "env_status", "describe_missing",
]


@dataclass
class EnvSpec:
    """One credential or setting the provider reads from the environment."""

    name: str
    label: str
    secret: bool = True
    required: bool = True
    hint: str = ""
    default: str = ""


@dataclass
class ProviderSpec:
    key: str                      # registry key: "seedance", "kling", ...
    label: str                    # human label shown in the UI
    kind: str                     # video | voice | llm | publish
    envs: tuple[EnvSpec, ...] = ()
    models: tuple[str, ...] = ()
    model_env: str = ""           # env var holding the model id
    console: str = ""             # where to get the key
    notes: str = ""
    aliases: tuple[str, ...] = field(default_factory=tuple)

    def ready(self) -> bool:
        return all(os.environ.get(e.name, "").strip() for e in self.envs if e.required)

    def missing(self) -> list[str]:
        return [e.name for e in self.envs if e.required and not os.environ.get(e.name, "").strip()]


KINDS = ("video", "voice", "llm", "publish")


VIDEO_SPECS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        key="seedance", label="Seedance（字节火山方舟）", kind="video",
        envs=(EnvSpec("SEEDANCE_API_KEY", "Ark API Key",
                      hint="火山方舟控制台 → API Key 管理；Bearer 单 key"),),
        models=("doubao-seedance-2-0-pro", "doubao-seedance-2-0-mini"),
        model_env="SEEDANCE_MODEL",
        console="https://console.volcengine.com/ark",
        notes="国内可用，中文口型与中文场景表现最好；pro 更稳，mini 更便宜。",
    ),
    ProviderSpec(
        key="kling", label="Kling 可灵（快手）", kind="video",
        envs=(EnvSpec("KLING_API_KEY", "API Key",
                      hint="可灵开放平台 → API 密钥（Bearer）"),),
        console="https://app.klingai.com",
        notes="运动幅度大、镜头语言强，适合 hook 段。",
    ),
    ProviderSpec(
        key="jimeng", label="即梦 Jimeng（火山视觉）", kind="video",
        envs=(
            EnvSpec("VOLC_ACCESS_KEY", "Access Key",
                    hint="火山引擎 → 密钥管理 → AccessKey ID"),
            EnvSpec("VOLC_SECRET_KEY", "Secret Key",
                    hint="与 Access Key 成对；签名用 HMAC-SHA256"),
            EnvSpec("JIMENG_REQ_KEY", "req_key", required=False,
                    default="jimeng_vgfm_t2v_l20",
                    hint="一般不用改；换模型版本时填"),
        ),
        console="https://console.volcengine.com/iam/keymanage",
        notes="HMAC 双 key 签名，与 Seedance 的 Bearer 单 key 不同。结果 URL 1 小时有效、并发 1。",
        aliases=("dreamina",),
    ),
    ProviderSpec(
        key="veo", label="Veo（Google）", kind="video",
        envs=(EnvSpec("GOOGLE_API_KEY", "API Key",
                      hint="Google AI Studio / Vertex；需开通 Veo 权限"),),
        console="https://aistudio.google.com/apikey",
        notes="海外首选，音画一致性最好，价格也最高。",
    ),
    ProviderSpec(
        key="bailian", label="Wan 通义万相（阿里百炼）", kind="video",
        envs=(EnvSpec("BAILIAN_API_KEY", "DashScope API Key",
                      hint="阿里云百炼控制台 → API-KEY"),),
        console="https://bailian.console.aliyun.com",
        notes="国内备选，便宜，速度一般。",
        aliases=("wan",),
    ),
    ProviderSpec(
        key="minimax", label="Hailuo 海螺（MiniMax）", kind="video",
        envs=(EnvSpec("MINIMAX_API_KEY", "API Key",
                      hint="MiniMax 开放平台 → 接口密钥"),),
        console="https://platform.minimaxi.com",
        notes="人物表现好，适合 talking_head。",
        aliases=("hailuo",),
    ),
    ProviderSpec(
        key="zhipu", label="CogVideoX（智谱）", kind="video",
        envs=(EnvSpec("ZHIPU_API_KEY", "API Key",
                      hint="智谱开放平台 → API Keys"),),
        console="https://open.bigmodel.cn",
        notes="开源可自部署，云端版便宜。",
        aliases=("cogvideox",),
    ),
)


VOICE_SPECS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        key="edge", label="Edge TTS（免费神经语音）", kind="voice",
        envs=(),
        notes="edge-tts，免费无需 key，带词级时间轴。默认选项。",
        aliases=("edge-tts",),
    ),
    ProviderSpec(
        key="elevenlabs", label="ElevenLabs（付费）", kind="voice",
        envs=(EnvSpec("ELEVENLABS_API_KEY", "API Key",
                      hint="ElevenLabs → Profile → API Key"),),
        console="https://elevenlabs.io/app/settings/api-keys",
        notes="/with-timestamps 端点，词级时间轴最准。",
    ),
    ProviderSpec(
        key="formant", label="Formant（离线共振峰）", kind="voice",
        envs=(),
        notes="离线 Klatt 合成器，真音频、有词级时间轴，机械音。无 key 时的兜底。",
    ),
)


LLM_SPECS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        key="deepseek", label="DeepSeek（默认）", kind="llm",
        envs=(EnvSpec("DEEPSEEK_API_KEY", "API Key",
                      hint="platform.deepseek.com → API Keys"),),
        models=("deepseek-flash", "deepseek-v4-pro", "deepseek-chat"),
        console="https://platform.deepseek.com/api_keys",
        notes="OpenAI 兼容端点，用于分镜解析与对话式改片路由。",
    ),
    ProviderSpec(
        key="openai", label="OpenAI 兼容端点", kind="llm",
        envs=(EnvSpec("OPENAI_API_KEY", "API Key",
                      hint="任意 OpenAI 兼容服务：OpenAI / Moonshot / vLLM / Ollama"),),
        console="https://platform.openai.com/api-keys",
        notes="改 base_url 即可接任何 OpenAI 兼容端点；免费额度可用 gpt-4o-mini。",
    ),
)


PUBLISH_SPECS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        key="tiktok", label="TikTok Direct Post", kind="publish",
        envs=(
            EnvSpec("TIKTOK_CLIENT_KEY", "Client Key",
                    hint="TikTok Developer Portal → App → Client Key"),
            EnvSpec("TIKTOK_CLIENT_SECRET", "Client Secret",
                    hint="同上，注意不要进 git"),
            EnvSpec("TIKTOK_REDIRECT_URI", "Redirect URI", required=False,
                    default="http://localhost:8787/api/tiktok/callback",
                    hint="必须与开发者后台配置的回调地址完全一致"),
        ),
        console="https://developers.tiktok.com",
        notes="未通过 Content Posting Audit 的应用只能发 SELF_ONLY（仅自己可见）。",
    ),
)


_ALL = {"video": VIDEO_SPECS, "voice": VOICE_SPECS, "llm": LLM_SPECS, "publish": PUBLISH_SPECS}


def specs_for_kind(kind: str) -> tuple[ProviderSpec, ...]:
    return _ALL.get(kind, ())


def spec_for(kind: str, key: str) -> ProviderSpec | None:
    key = (key or "").strip().lower()
    for s in specs_for_kind(kind):
        if s.key == key or key in s.aliases:
            return s
    return None


def mask(value: str) -> str:
    """Show just enough of a key to recognise it, never enough to copy it."""
    v = (value or "").strip()
    if not v:
        return ""
    if len(v) <= 8:
        return v[:2] + "*" * (len(v) - 2)
    return f"{v[:4]}{'*' * 6}{v[-4:]}"


def env_status(spec: ProviderSpec) -> dict:
    """Per-env-var status for the settings page."""
    return {
        e.name: {
            "label": e.label,
            "secret": e.secret,
            "required": e.required,
            "hint": e.hint,
            "default": e.default,
            "set": bool(os.environ.get(e.name, "").strip()),
            "masked": mask(os.environ.get(e.name, "")),
        }
        for e in spec.envs
    }


def describe_missing(kind: str) -> list[dict]:
    """Everything of `kind` that is not yet usable, for a first-run checklist."""
    out = []
    for s in specs_for_kind(kind):
        out.append({
            "key": s.key,
            "label": s.label,
            "ready": s.ready(),
            "missing": s.missing(),
            "console": s.console,
            "notes": s.notes,
        })
    return out
