"""Remotion project emitter.

The offline compositor in `composer.py` is what this demo *runs*. Remotion is
what I'd *ship* for a client who needs 4K, real fonts, CSS motion, and
horizontal scale-out on Lambda.

This module projects the same SceneGraph IR into a Remotion project. The point
is not that the two renderers produce identical pixels -- it's that neither
one owns the creative intent. The IR does. Swapping renderers is a config
change, which is exactly the property that keeps you off a vendor's
deprecation schedule.

Usage:
    python -m avb.render.remotion <project.json> <outdir>
    cd <outdir> && npm install && npx remotion render Ad out/ad.mp4
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from ..ir import Project


_ROOT_TSX = """import {Composition} from 'remotion';
import {Ad} from './Ad';
import {project} from './project';

export const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="Ad"
      component={Ad}
      durationInFrames={project.totalFrames}
      fps={project.fps}
      width={project.width}
      height={project.height}
    />
  );
};
"""

_AD_TSX = r"""import React from 'react';
import {AbsoluteFill, Sequence, useCurrentFrame, useVideoConfig, Audio, staticFile} from 'remotion';
import {project, Scene as SceneIR} from './project';

const EaseOutCubic = (t: number) => 1 - Math.pow(1 - Math.min(1, Math.max(0, t)), 3);
const EaseOutBack = (t: number) => {
  const s = 1.9;
  const x = Math.min(1, Math.max(0, t));
  return 1 + (s + 1) * Math.pow(x - 1, 3) + s * Math.pow(x - 1, 2);
};

/**
 * Motion is applied at composite time, not baked into the asset.
 * Changing `motion` from "zoom_in" to "punch" therefore costs zero
 * generation -- it re-renders, it does not re-generate.
 */
const motionStyle = (motion: string, t: number, interrupt: number): React.CSSProperties => {
  const boost = 1 + 0.55 * interrupt;
  let scale = 1.02;
  let x = 0;
  switch (motion) {
    case 'zoom_in':
      scale = 1.02 + 0.16 * t * boost;
      break;
    case 'zoom_out':
      scale = 1.18 * boost - 0.16 * t;
      break;
    case 'pan_left':
      scale = 1.12 * boost;
      x = 18 * (0.5 - t);
      break;
    case 'pan_right':
      scale = 1.12 * boost;
      x = -18 * (0.5 - t);
      break;
    case 'punch':
      scale = 1.02 + 0.2 * boost * EaseOutCubic(Math.min(1, t / 0.3)) - 0.035 * Math.max(0, t - 0.3);
      break;
  }
  return {transform: `scale(${scale}) translateX(${x}%)`};
};

const Captions: React.FC<{words: {word: string; start: number; end: number}[]; frame: number; fps: number; style: any}> = ({words, frame, fps, style}) => {
  const t = frame / fps;
  const active = words.find((w) => t >= w.start && t < w.end) || words[words.length - 1];
  const card = words.filter((w) => Math.abs(w.start - (active?.start ?? 0)) < 0.001);
  const list = card.length ? card : [active];
  const pop = 1 + style.scale_pop * EaseOutBack(Math.min(1, ((t - (active?.start ?? 0)) / Math.max(0.001, (active?.end ?? 1) - (active?.start ?? 0))) * 1.2);

  return (
    <AbsoluteFill
      style={{
        justifyContent: style.position === 'center' ? 'center' : style.position === 'top' ? 'flex-start' : 'flex-end',
        alignItems: 'center',
        paddingBottom: style.position === 'bottom' ? 140 : 0,
        paddingTop: style.position === 'top' ? 160 : 0,
      }}
    >
      <div style={{display: 'flex', gap: '0.28em', flexWrap: 'wrap', justifyContent: 'center'}}>
        {list.map((w: any, i: number) => (
          <span
            key={i}
            style={{
              fontFamily: style.fontFamily,
              fontSize: style.fontSize,
              fontWeight: 800,
              letterSpacing: '-0.02em',
              color: w === active ? style.highlight : style.textColor,
              transform: w === active ? `scale(${pop})` : 'none',
              textShadow: `0 0 ${style.strokeWidth}px ${style.strokeColor}, 0 0 ${style.strokeWidth}px ${style.strokeColor}`,
              textTransform: style.uppercase ? 'uppercase' : 'none',
            }}
          >
            {w.word}
          </span>
        ))}
      </div>
    </AbsoluteFill>
  );
};

export const Ad: React.FC = () => {
  const {fps} = useVideoConfig();
  const frame = useCurrentFrame();

  let cursor = 0;
  return (
    <AbsoluteFill style={{backgroundColor: '#000'}}>
      {project.scenes.map((s: SceneIR) => {
        const start = cursor;
        cursor += s.frames;
        return (
          <Sequence key={s.id} from={start} durationInFrames={s.frames}>
            <AbsoluteFill>
              {s.visual && <img src={staticFile(s.visual)} style={{width: '100%', height: '100%', objectFit: 'cover', ...motionStyle(s.motion, (frame - start) / Math.max(1, s.frames), s.interrupt)}} />}
              {s.vo && <Audio src={staticFile(s.vo)} />}
              <Captions words={s.words} frame={frame - start} fps={fps} style={project.caption} />
            </AbsoluteFill>
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};
"""

_PKG_JSON = """{
  "name": "avb-remotion",
  "version": "0.1.0",
  "private": true,
  "scripts": {
    "start": "remotion studio",
    "build": "remotion render Ad out/ad.mp4"
  },
  "dependencies": {
    "@remotion/cli": "4.x",
    "remotion": "4.x",
    "react": "18.x",
    "react-dom": "18.x"
  }
}
"""


def emit(project: "Project", outdir: str | Path, public_dir: str | Path | None = None) -> Path:
    """Write a runnable Remotion project for `project`.

    Note: assets are referenced via `staticFile()`, so the project's asset
    directory must be copied into `<outdir>/public` (or `public_dir`).
    """
    outdir = Path(outdir)
    (outdir / "src").mkdir(parents=True, exist_ok=True)

    fps = project.fps
    scenes_payload = []
    for s in project.scenes:
        dur = s.duration if s.duration is not None else 2.0
        frames = max(1, int(round(dur * fps)))
        scenes_payload.append(
            {
                "id": s.id,
                "beat": s.beat,
                "frames": frames,
                "motion": s.visual.motion,
                "interrupt": s.interrupt,
                "transition": s.transition,
            }
        )

    total_frames = sum(s["frames"] for s in scenes_payload)

    (outdir / "src" / "project.ts").write_text(
        "export const project = "
        + json.dumps(
            {
                "fps": fps,
                "width": project.width,
                "height": project.height,
                "style": project.style,
                "totalFrames": total_frames,
                "scenes": scenes_payload,
            },
            indent=2,
        )
        + ";\n",
        encoding="utf-8",
    )
    (outdir / "src" / "Ad.tsx").write_text(_AD_TSX, encoding="utf-8")
    (outdir / "src" / "Root.tsx").write_text(_ROOT_TSX, encoding="utf-8")
    (outdir / "package.json").write_text(_PKG_JSON, encoding="utf-8")
    (outdir / "remotion.config.ts").write_text(
        "import {Config} from '@remotion/cli/config';\n"
        "Config.setVideoImageFormat('jpeg');\n"
        "Config.setEntryPoint('src/Root.tsx');\n",
        encoding="utf-8",
    )
    (outdir / "tsconfig.json").write_text(
        json.dumps(
            {
                "compilerOptions": {
                    "target": "ES2018",
                    "module": "ESNext",
                    "jsx": "react-jsx",
                    "strict": True,
                    "moduleResolution": "bundler",
                    "noEmit": True,
                }
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return outdir


def emit_json(project: "Project", outdir: str | Path) -> Path:
    """Persist the IR so it can be diffed, re-rendered, or unit tested."""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    p = outdir / "project.json"
    p.write_text(project.to_json(), encoding="utf-8")
    return p


if __name__ == "__main__":
    import sys

    from ..ir import Project as _P

    src, dst = sys.argv[1], sys.argv[2]
    proj = _P.from_json(Path(src).read_text(encoding="utf-8"))
    emit(proj, dst)
    emit_json(proj, dst)
    print(f"wrote remotion project to {dst}")

