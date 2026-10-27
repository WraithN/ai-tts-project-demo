# ai-video-builder

**Paste an ad script. Pick a style. Get a finished, Meta-ready video. Then
revise it by talking to it.**

An end-to-end automated production pipeline: script → scene breakdown →
neural voiceover with word-level timings → visuals → kinetic captions →
music and SFX → rendered MP4. Conversational editing re-renders only what
actually changed.

---

## What is real, what is not

Straight answer, because the distinction matters:

| Layer | Status |
|---|---|
| **IR + content-addressed cache + conversational editing** | real — this is the deliverable |
| **LLM** (script parse, edit routing) | **real** — DeepSeek V4 by default, any OpenAI-compatible endpoint |
| **TTS** | **real** — edge-tts neural voices, free, word-level timestamps |
| **Music / SFX** | synthesised (chord progression + DSP), not generative |
| **Visuals / footage** | procedural stills + compositor motion; real video providers (Kling, Veo, Wan, Hailuo, CogVideoX) implemented and selected by config |

The offline formant voice and procedural video exist so the pipeline runs
with zero credentials and zero network. Everything real plugs into the same
`Provider` interface, so swapping is a config change, not a rewrite.

See **`docs/CASE_STUDY.md`** for the Upwork portfolio case study: answers
to the five application questions, a demo walk-through script, proposal
openers, and what is real vs stand-in.

See **`docs/PROVIDERS.md`** for the 2026 TTS and video-generation landscape
(pricing, licenses, what actually runs on a laptop GPU).

---

## Quick start

```bash
cd ai-video-builder
python -m pip install -e ".[all]"     # core + edge-tts
cp .env.example .env                  # then put your DEEPSEEK_API_KEY in it

python -m avb.cli doctor              # verify the environment
python -m avb.cli config web          # optional: configure at http://127.0.0.1:8787

python -m avb.cli render script.txt --draft --out out/draft.mp4
python -m avb.cli render script.txt --out out/final.mp4
```

Then edit by talking:

```bash
python -m avb.cli edit project.json "Spice up the hook and make the first 10 seconds punchier"
python -m avb.cli render script.txt --only s001,s002,s003,s004 --out out/v2.mp4
```

Requires Python 3.11+ and `ffmpeg` on `PATH`. Full guide: **`docs/USAGE.md`**.
Architecture: **`docs/ARCHITECTURE.md`**.

### The demo tour

```bash
python demo/run_demo.py               # 7-stage tour, full quality
python demo/run_demo.py --draft       # half res, for iteration
python demo/run_demo.py --stages 3,4  # just render + conversational edits
```

Outputs land in `demo/out/`. `--stages` splits a long run; later stages reuse
the on-disk segment cache from earlier ones, so the split is free. Stage ids:
`1` parse, `2` providers, `3` v1 render, `4` edit #1, `5` edit #2, `6` style
matrix, `7` scene map.

---

## Configure

Four-layer cascade (later wins): **defaults < `avb.toml` < env vars < CLI flags.**

Secrets never go in `avb.toml` — the file names the env var that holds each
key (`api_key_env = "DEEPSEEK_API_KEY"`), and `.env` (gitignored) feeds the
environment. So the config file is safe to commit and share.

Three equivalent front-ends:

```bash
# 1. CLI
python -m avb.cli config set llm.model deepseek-v4-pro
python -m avb.cli config set voice.voice zh-CN-XiaoxiaoNeural
python -m avb.cli config set video.provider kling

# 2. Local web UI (stdlib only, localhost)
python -m avb.cli config web

# 3. Hand-edit avb.toml — comments survive `config set`
```

| Section | Keys |
|---|---|
| `[llm]` | `provider` `model` `base_url` `api_key_env` `max_tokens` `effort` |
| `[voice]` | `provider` `voice` `rate` `speed` `energy` |
| `[video]` | `provider` `model` `clip_seconds` `max_usd_per_run` |
| `[render]` | `width` `height` `fps` `crf` `preset` `workers` |

Any OpenAI-compatible endpoint works — DeepSeek, Moonshot, Zhipu, local
vLLM / Ollama with `--jinja`.

---

## CLI

```
avb doctor                  environment and provider check
avb config init|get|set|web|list|show|path
avb parse  script.txt [--llm] [--out p.json]
avb render script.txt [--draft] [--llm] [--style X] [--only s001,s002]
avb edit   project.json "make it punchier" [--rules-only]
avb providers               what is wired up and whether it is live
avb tts "hello world"       quick voice test
```

---

## The one architectural decision that matters

Most script-to-video tools do this:

    script --> generate clips --> stitch --> mp4

That works for a demo and collapses in production, because **there is no
layer that can be edited**. Ask it to change the first 10 seconds and the
only available operation is "regenerate everything" — 8 minutes and $80 to
move one cut.

This pipeline puts a structured representation in the middle:

```
script
  |
  v
[LLM scene parser]  --->  SceneGraph IR (JSON)  <--- [conversational editor]
  |                            |
  |                            v
  |                    asset plan (per scene)
  |                            |
  |              +-------------+-------------+
  |              v             v             v
  |          video/image     voice        music/sfx
  |              |             |             |
  +--------------+-------------+-------------+
                             |
                             v
                  [compositor] -> segments -> concat -> mp4
```

Every scene carries a content hash:

```python
scene.content_hash() == sha1(canonical({
    "beat": ..., "vo": {...}, "visual": {...},
    "caption": {...}, "sfx": [...], "transition": ..., "interrupt": ...,
}))[:12]
```

Change one line, re-hash, diff. Only the scenes whose hash moved get
re-generated; everything else is a filesystem cache hit. **The IR is the
only thing ever edited** — never pixels, never the MP4. That single
constraint is what makes editing cheap, diffable, and reversible.

One trap worth naming, because it is invisible until a client asks why
nothing changed: the cache key must include the renderer version. A
compositor fix does not touch any scene's semantics, so without it the
pipeline cheerfully reuses every stale frame. `RENDER_VERSION` lives in the
segment key for exactly that reason.

---

## Conversational editing

This is the feature the brief marks *Extremely Important*. Two routers:

- **rules** (`avb/edit.py`) — fast, deterministic, free. Handles the common
  phrasings. Always available.
- **LLM** (`avb/llm_edit.py`) — handles arbitrary phrasing. Proposes a plan
  as structured JSON against a closed vocabulary of intents, selectors and
  patch paths; the code sanitises and applies it.

```
user > Make the first 10 seconds more engaging with faster cuts and
       more aggressive text animations.

  intent      : pacing
  scene_ids   : ['s001', 's002', 's003', 's004']
  ops         : vo.speed x1.08 | caption.scale_pop x1.35
               visual.motion -> punch | transition -> flash
  invalidated : 4 / 20 scenes
  reused      : 16 / 20 segments straight from cache
```

Scene targeting resolves in three tiers, most specific first:

```
explicit timestamp  >  narrative beat  >  whole video
"first 10 seconds"     "the hook"         "throughout the video"
```

Measured reuse on the bundled 20-scene demo:

| Instruction | Scenes re-rendered | Reuse |
|---|---|---|
| "first 10 seconds" (time range) | 3 / 20 | 85% |
| "mechanism section → comic" (style) | 1 / 20 | 95% |
| "throughout the video" (global) | 20 / 20 | 0% |

Style swaps are the cheapest edit class — they do not touch the voiceover,
and the voiceover is the slowest asset to produce.

### Safety rails

The LLM proposes; the code disposes. It can only pick from a closed list of
intents, selectors and patch paths. Enum values snap to known ones
(`comic_book` → `comic`). Anything that sanitises to zero ops falls back to
the rule-based plan. A bad model answer degrades to a dumber edit, never to
a corrupted IR.

---

## Performance

Segment rendering can fan out across processes (`--workers`, default auto).
Measured on the bundled 20-scene 9:16 draft, on this machine:

| Renderers | Wall clock | Speedup |
|---|---|---|
| 1 (serial) | 217 s | 1.0x |
| 6 (process pool) | 206 s | **1.05x** |

That is an honest number and it is the interesting one. Parallelising the
compositor barely helps, because **the compositor is not the bottleneck** —
voice synthesis and music rendering are, and both are inherently serial
(audio ducking needs the final voice envelope before the bed can be laid
down). Frame work is also GIL-bound, so a thread pool is worse still
(measured 1.1x).

The process pool is still worth having: it is the right shape for the real
bottleneck the moment you swap the offline voice mock for a hosted TTS API
(where each segment is an HTTP round-trip and latency, not CPU, dominates).
On Windows it needs an `if __name__ == "__main__"` guard in the caller, so
the pool falls back to serial rendering rather than dying if it does not
get one.

If you do chase the actual bottleneck later: synthesis is content-addressed
and cached, so the second run of an unchanged script is mostly concat and
mux.

---

## Unit economics

High-volume DTC lives or dies on cost per finished video. Every provider
reports what it spent and `CostLedger` rolls it up per run.

| Component | Offline default | Real provider |
|---|---|---|
| voice (per 60s ad) | $0 (formant/edge) | ~$0.10 (ElevenLabs) |
| visuals | $0 (procedural) | $0.40–2.00/clip (Kling–Veo) |
| music | $0 (synth) | $0 (synth) |
| LLM (parse + edits) | $0 (rules) | ~$0.001–0.01 (DeepSeek) |
| **60s ad, all-real voice + 8 clips** | — | **~$4–16** |
| **60s ad, edge-tts + mock visuals** | **~$0.001** | — |

A style change touches ~1 scene's visual and nothing else. A pacing change
touches voiceover but reuses every visual. The cache turns "change one line"
from a full rebuild into a diff.

---

## Tests

```bash
python -m pytest -q
```

16 smoke tests, no network, no ffmpeg. They cover the config cascade,
comment-preserving writes, `.env` loading, JSON extraction, edit-plan
sanitisation (the guard rails), provider fallback, and the content-addressed
cache.

---

## Known limits

- **edge-tts is an unofficial endpoint.** Fine for a portfolio and for
  cost-sensitive volume; use ElevenLabs or self-hosted CosyVoice/IndexTTS
  for anything with an SLA.
- **The formant voice is a synthesiser, not a model.** It exists so the
  pipeline runs with zero credentials. It sounds like 1998.
- **Video mocks are procedural stills.** Motion is applied at composite time
  (Ken Burns, punch-zoom) — deliberately, so changing `zoom_in` to `punch`
  costs zero generation. Real footage needs a keyed provider; the adapters
  for Kling / Veo / Wan / Hailuo / CogVideoX are implemented but not yet
  live-tested against a billed endpoint.
- **Compliance is your job.** FTC endorsement guides and Meta ad policy
  apply to what you ship. This pipeline does not audit claims.
- **Parallelism is ~1.05×** on the current bottleneck mix. See Performance
  above.

---

## Layout

```
ai-video-builder/
├── avb.toml              # config (secrets live in .env, not here)
├── .env                  # gitignored, holds DEEPSEEK_API_KEY etc.
├── pyproject.toml        # package metadata, `avb` entry point
├── avb/
│   ├── config.py         # layered config + .env loader
│   ├── llm.py            # OpenAI-compatible chat client
│   ├── llm_edit.py       # LLM conversational edit router
│   ├── parse.py          # script -> IR (heuristic + LLM)
│   ├── edit.py           # rule-based edit router + apply/invalidate
│   ├── ir.py             # SceneGraph IR
│   ├── pipeline.py       # orchestrator, content-addressed segments
│   ├── qc.py             # quality control
│   ├── cli.py            # `avb` command
│   ├── webui.py          # local HTML config page
│   ├── assets/           # providers: voice / video / image / music
│   └── render/           # compositor, ffmpeg, remotion
├── docs/
│   ├── ARCHITECTURE.md
│   ├── USAGE.md
│   └── PROVIDERS.md      # TTS + video generation survey
├── tests/test_smoke.py
└── demo/                 # scripted tour + sample script
```
