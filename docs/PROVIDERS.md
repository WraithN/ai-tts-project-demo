# Provider selection guide

Research notes behind the provider layer. Current as of **2026-10**. Prices
are list rates and move; verify before committing to volume.

---

## Part 1 — TTS

### What this pipeline actually needs

Three hard requirements, and most "best TTS" lists don't rank on them:

1. **Word-level timestamps.** Kinetic captions are frame-accurate or they
   are wrong. Everything downstream depends on this.
2. **Free or near-free at volume.** DTC ads are produced in batches of
   hundreds. Per-character pricing compounds.
3. **Runs without a GPU.** The reference environment is a laptop.

### Landscape (2026)

| Model | Params | License | Word timings | Chinese | VRAM | Notes |
|---|---|---|---|---|---|---|
| **edge-tts** | hosted | n/a (unofficial) | ✅ WordBoundary | ✅ native | 0 | free, no key, ~400 voices |
| **CosyVoice 2/3** | 0.5B | Apache 2.0 | via aligner | ✅ best-in-class, 18 dialects | ~4 GB | streaming 150ms first packet |
| **IndexTTS-2.5** | — | custom (contact) | ✅ duration control | ✅ pinyin correction | ~8 GB | 8-dim emotion vector; best for dubbing |
| **Fish Speech 1.5/S2** | ~1B | non-commercial (1.5) | partial | ✅ strong | ~4–6 GB | multi-speaker dialogue natively |
| **GPT-SoVITS** | — | MIT-ish | via aligner | ✅ strongest community | ~4 GB | 5s zero-shot / 1min finetune |
| **F5-TTS** | ~0.3B | code MIT / weights CC-BY-NC | via aligner | decent | ~3–12 GB | simplest install |
| **Spark-TTS** | 0.5B | Apache 2.0 | partial | ok | ~1–2 GB | lightest serious option |
| **Qwen3-TTS** | 0.6–1.7B | Apache 2.0 | ✅ | ✅ | ~2–6 GB | 97ms latency, voice design |
| **Kokoro** | 82M | Apache 2.0 | ❌ | weak (needs misaki[zh]) | CPU | edge/embedded |
| **ElevenLabs** | hosted | paid | ✅ exact (char-level) | ✅ | 0 | $0.18/1k chars; the safe default for production |

Sources: TTS Arena leaderboard, the 2026-04 Windows six-model comparison
(rarebuildsoftware.com), and the 35-model Chinese practical guide (2026).

### Verdict

**Ship `edge-tts` as the default.** It is the only thing that is free,
keyless, neural, and word-aligned at the same time. The unofficial-endpoint
risk is real but acceptable at portfolio scale and at volume where cost
dominates.

**Upgrade path, in order of when you need it:**

| Situation | Move to | Why |
|---|---|---|
| you need an SLA / a contract | ElevenLabs | managed, exact timings, stable |
| you need brand voice cloning | CosyVoice 2 or GPT-SoVITS | 3–30s reference clip |
| you need emotional dubbing | IndexTTS-2.5 | duration factor 0.5–2.0x is the killer feature for lip-sync |
| you need multi-speaker dialogue | Fish S2 | one pass, two voices |
| you need it offline on-prem | Qwen3-TTS or Spark-TTS | Apache 2.0, small |
| you need it on a phone | Kokoro | 82M, CPU realtime |

### License trap

Fish Speech 1.5 **cannot** be used commercially. F5-TTS weights are
CC-BY-NC. ChatTTS and GPT-SoVITS have murky commercial terms. If the client
is selling the output, stick to Apache 2.0 (CosyVoice, Qwen3-TTS, Spark,
VoxCPM2) or a paid API.

### Why not self-host by default

A single RTX 4090 runs CosyVoice/IndexTTS at roughly ¥520/month fully
loaded (hardware depreciation + power). That beats ElevenLabs past ~3M
characters/month — but it is a fixed cost and an ops burden. API is the
right call until you cross that line.

---

## Part 2 — Video generation

### The uncomfortable truth

Every serious video model produces **clips, not films**. Single-shot output
is 5–15s (Wan 3.0 reaches 30s; Kling multi-round can stretch to ~2min). The
workflow is always:

```
generate clips → cut → voiceover → music → grade → export
```

This pipeline already assumes that shape, which is why the IR is shot-based.

### Domestic market, five routes (2026)

| | vendor | tech | max single | strength | commercial from |
|---|---|---|---|---|---|
| **Kling** | 快手 | 3D VAE + 3D DiT, Kling 4.0 | 15s (extendable) | physics, big motion, 4K native | Standard ≈ ¥66/mo |
| **Jimeng / Seedance** | 字节 | unified audio-video | 15s / 30s | multimodal `@` references, CapCut integration | ¥33/mo (first month) |
| **Hailuo** | MiniMax | Hailuo 2.3 / H3 | 10–12s | filmic realism, micro-expressions | intl $14.99/mo; CN free tier |
| **Vidu** | 生数 | UVM-DiT, Q3 | 16s | **multi-subject consistency as a reusable asset** | ¥48/mo |
| **Wan / Tongyi Wanxiang** | 阿里 | **open weights** (Apache 2.0) 1.3B/14B | 15–30s | only major open-source one; Bailian API ¥0.6–1/s | API pay-as-you-go |

One-line: 可灵拼画质与物理真实、即梦拼多模态可控、海螺拼写实质感、
Vidu 拼一致性、万相拼开源与自由度。

### API pricing (the pool that matters)

Consumer membership credits **do not** transfer to the API on any of these
platforms. API is billed separately, per second.

| Provider | 5s clip, 720p no-audio | 5s clip, 1080p | Entry package |
|---|---|---|---|
| Kling 3.0 Turbo | ~$0.42 | ~$0.56 | $9.80 trial / 100 units |
| Bailian Wan | ~$0.40 | ~$0.65 | pay-as-you-go, 90-day free quota |
| MiniMax Hailuo | ~$0.30 | — | welcome credits |
| Vidu | ~$0.20–1.50 | — | free tier (watermarked) |
| Zhipu CogVideoX | ~$0.35 | — | free tier |
| Google Veo 3.1 | ~$2.00 | ~$2.00 | $0.15–0.40/s list |

Domestic APIs are 3–6× cheaper than Veo at equivalent resolution.

### Open source: what can you actually run?

| Model | Weights | License | VRAM to run | Practical? |
|---|---|---|---|---|
| **Wan 2.1/2.2 (1.3B)** | ✅ full | Apache 2.0 | ~8–12 GB | **yes** — the reference open option |
| **Wan 2.2 (14B)** | ✅ full | Apache 2.0 | 24 GB+ | for a 4090, slowly |
| **CogVideoX-5B** | ✅ full | Apache 2.0 | 16–24 GB | yes |
| **HunyuanVideo** | ✅ full | custom (check) | 24 GB+ | heavy, 8.3B + SSTA |
| **LTX-Video** | ✅ full | check | ~8 GB | fastest, lower fidelity |
| **OpenSora** | partial | Apache 2.0 | 16 GB+ | research-grade |

**On this machine (RTX 5070 Laptop, 8 GB):** Wan-1.3B is the only serious
candidate, and it will be slow. Everything above 12 GB VRAM is out. For a
portfolio demo the hosted APIs are the better spend — $0.40 to prove the
pipeline works beats three hours of local inference.

### Adapter status in this repo

| Provider | Class | Status |
|---|---|---|
| `mock` | `MockVideo` | ✅ used, offline, procedural |
| `kling` | `KlingVideo` | ✅ adapter implemented, needs `KLING_API_KEY` |
| `veo` | `VeoVideo` | ✅ adapter implemented, needs `GOOGLE_API_KEY` |
| `jimeng`/`dreamina` | `JimengVideo` | ✅ adapter implemented, needs `VOLC_ACCESS_KEY` + `VOLC_SECRET_KEY` |
| `bailian`/`wan` | `BailianVideo` | ✅ adapter implemented, needs `BAILIAN_API_KEY` |
| `minimax`/`hailuo` | `MiniMaxVideo` | ✅ adapter implemented, needs `MINIMAX_API_KEY` |
| `zhipu`/`cogvideox` | `ZhipuVideo` | ✅ adapter implemented, needs `ZHIPU_API_KEY` |

All six real adapters share `_AsyncVideoBase` (submit → poll → download)
and are content-addressed through the same cache. Swapping is:

```bash
python -m avb.cli config set video.provider kling
export KLING_API_KEY=...
```

### Jimeng (即梦) — the odd one out

Three things about the Volcengine path that differ from every other adapter
here, and all three bite people:

1. **Auth is AK/SK HMAC-SHA256, not a bearer token.** The signature is
   AWS-SigV4-shaped: derive a key through `date → cn-north-1 → cv →
   "request"`, then sign a canonical request. `avb/assets/video.py`
   implements it in ~40 lines so the pipeline does not need the official
   SDK. You need **both** `VOLC_ACCESS_KEY` and `VOLC_SECRET_KEY`; a single
   key is not enough and `available()` correctly reports false.
2. **Result URLs expire in about an hour.** The base class downloads the
   clip the instant polling returns `done`, which is the right shape. Never
   cache the URL instead of the bytes.
3. **Concurrency is capped at 1** on the Jimeng side. Fan out through the
   orchestrator's serial queue, not through threads — parallel submits get
   rejected or queued anyway.

`req_key` selects the model generation (`jimeng_vgfm_t2v_l20` = S2.0 Pro
text-to-video as of 2026-10). ByteDance versions these aggressively, so it
is overridable via `JIMENG_REQ_KEY` rather than hardcoded into the payload
logic. Statuses seen: `in_queue` → `generating` → `done` (or
`not_found`/`expired` on the failure path).

**Not yet live-tested against a billed endpoint** — no paid video key was
available in this environment. The request shapes follow each vendor's
public docs as of 2026-10. Before first billed call, run:

```bash
python -m avb.cli config set video.max_usd_per_run 1.00
```

which hard-caps spend on a run.

### What to use for a portfolio case

1. **mock** for the engineering story — it shows the IR/caching/edit loop,
   which is the actual deliverable.
2. **one keyed provider** (Kling or Bailian) for a 2–3 shot hook sequence,
   to prove the adapter is real. ~$1–2.
3. Leave the rest on mock, and be explicit about which shots are generated.

Honesty beats a fully fake reel. The architecture is what gets you the
interview; a single real Kling clip is what stops the interviewer from
asking "but does it actually call a model?"

---

## Part 3 — LLM

| Provider | Model | Notes |
|---|---|---|
| **DeepSeek** | `deepseek-flash` / `deepseek-v4-pro` | default here. V4 is a reasoning family — budget `max_tokens` accordingly and prefer `effort = "low"` for edit routing |
| OpenAI | `gpt-4o-mini` / `gpt-4.1` | the safe fallback everyone knows |
| Moonshot | `moonshot-v1-8k` | good Chinese |
| Zhipu | `glm-4-plus` | domestic |
| Ollama/vLLM | anything | fully local, `--jinja` for JSON |

Cost for the workloads here (script parse + edit routing): fractions of a
cent per call on DeepSeek. Not a factor. Latency and JSON reliability are,
which is why `chat_json()` retries once and why the rule-based fallback
exists.

---

## Change log

| Date | Change |
|---|---|
| 2026-10 | initial survey; edge-tts adopted as default voice; domestic video adapters written (Kling / Bailian / MiniMax / Zhipu) alongside Veo |
| 2026-10 | Jimeng (即梦) adapter added — Volcengine AK/SK HMAC-SHA256 signing implemented inline, `req_key` overridable |
