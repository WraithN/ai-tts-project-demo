"""FFmpeg wrapper.

Segments are encoded once and cached. Re-assembly is a concat with stream
copy, which is why a one-line edit costs one segment re-encode instead of a
full rebuild -- the whole point of the IR + content-hash design.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Iterable, Iterator

import numpy as np

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"

SR = 44100


class FFmpegError(RuntimeError):
    pass


def available() -> bool:
    return shutil.which(FFMPEG) is not None


def _run(args: list[str], stdin=None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", *args],
        input=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def probe_duration(path: str | Path) -> float:
    try:
        out = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        ).stdout
        return float(json.loads(out)["format"]["duration"])
    except Exception:
        return 0.0


def encode_segment(
    frames: Iterator[np.ndarray],
    w: int,
    h: int,
    fps: int,
    wav: Path | None,
    out: Path,
    *,
    crf: int = 18,
    preset: str = "veryfast",
) -> Path:
    """Encode a generator of RGB frames (optionally with VO) to mp4."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)

    cmd = ["-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(fps), "-i", "pipe:0"]
    if wav is not None and Path(wav).is_file():
        cmd += ["-i", str(wav), "-shortest"]
        cmd += ["-c:a", "aac", "-b:a", "160k", "-ar", str(SR), "-ac", "1"]
    cmd += ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p"]
    cmd += ["-movflags", "+faststart", str(out)]

    proc = subprocess.Popen([FFMPEG, "-y", "-hide_banner", "-loglevel", "error", *cmd], stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdin is not None
    write_err: str | None = None
    try:
        for f in frames:
            buf = np.ascontiguousarray(f, dtype=np.uint8)
            if buf.shape[:2] != (h, w):
                raise FFmpegError(
                    f"frame size {buf.shape[1]}x{buf.shape[0]} does not match "
                    f"declared {w}x{h}; ffmpeg would reject the stream"
                )
            try:
                proc.stdin.write(buf.tobytes())
            except BrokenPipeError:
                break
            except OSError:
                # Windows surfaces a write-to-closed-pipe as Errno 22 rather
                # than BrokenPipeError. Fall through and report ffmpeg's own
                # stderr, which is the actually useful message.
                break
    except FFmpegError:
        try:
            proc.stdin.close()
        except Exception:
            pass
        proc.kill()
        proc.wait()
        raise
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass

    err = proc.stderr.read().decode("utf-8", "ignore") if proc.stderr else ""
    rc = proc.wait()
    if rc != 0:
        write = write_err or ""
        raise FFmpegError(f"ffmpeg segment failed ({rc}): {err[-800:]}{write}")
    return out


def concat(segments: Iterable[Path], out: Path) -> Path:
    """Lossless concat. Safe because every segment is encoded identically."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    segs = list(segments)
    if not segs:
        raise FFmpegError("no segments to concat")

    # Absolute paths: ffmpeg resolves entries in a concat list relative to the
    # list file's directory, not the CWD. Writing a relative segment path here
    # silently relocates the lookup when the output mp4 lives in a different
    # directory from the segment cache -- which is exactly the case for
    # demo/out/ vs .avb/.
    list_file = out.with_suffix(".concat.txt")
    list_file.write_text(
        "".join(f"file '{Path(s).resolve().as_posix()}'\n" for s in segs),
        encoding="utf-8",
    )

    cmd = ["-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(out)]
    r = _run(cmd)
    if r.returncode != 0:
        raise FFmpegError(f"concat failed: {r.stderr.decode('utf-8','ignore')[-800:]}")
    return out


def mux_audio(video: Path, wav: Path, out: Path) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "-i", str(video), "-i", str(wav),
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-shortest", "-movflags", "+faststart", str(out),
    ]
    r = _run(cmd)
    if r.returncode != 0:
        raise FFmpegError(f"mux failed: {r.stderr.decode('utf-8','ignore')[-800:]}")
    return out


def decode_audio(path: Path, sr: int = SR) -> np.ndarray:
    """Decode any audio file to mono float32 via ffmpeg."""
    cmd = ["-i", str(path), "-f", "f32le", "-ac", "1", "-ar", str(sr), "pipe:1"]
    r = _run(cmd)
    if r.returncode != 0:
        raise FFmpegError(f"decode failed: {r.stderr.decode('utf-8','ignore')[-500:]}")
    return np.frombuffer(r.stdout, dtype="<f4").astype(np.float32)


def write_wav(path: Path, audio: np.ndarray, sr: int = SR) -> Path:
    """Write mono float32 as 16-bit PCM wav (raw, no container libs)."""
    import struct
    import wave

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    del wave, struct
    return path
