"""Concurrency + failure-resilience of Pipeline.synth().

These exercise the ① fan-out video generation, ② voiceover synthesized off the
main thread, and ③ the account-halt degradation path. They run in the real
environment (numpy / PIL / requests installed); in a dependency-free sandbox
they are skipped by the import guard below.
"""

import time

import pytest

# The whole module needs numpy/PIL/requests (pulled in by avb.pipeline). Skip
# gracefully when those are absent so a thin sandbox does not pretend to pass.
try:
    import numpy  # noqa: F401
    from PIL import Image  # noqa: F401

    _HEAVY_DEPS = True
except ImportError:
    _HEAVY_DEPS = False

pytestmark = pytest.mark.skipif(not _HEAVY_DEPS, reason="needs numpy/PIL")


if _HEAVY_DEPS:
    from avb.assets.base import Cost
    from avb.assets.video import VideoGenError
    from avb.ir import Project, Scene, Visual, Voiceover
    from avb.pipeline import Pipeline


def _make_pipe(workdir, workers=4, draft=True):
    pipe = Pipeline(workdir, draft=draft, workers=workers)
    return pipe


def _project(n, with_duration=False):
    scenes = []
    for i in range(1, n + 1):
        scenes.append(Scene(
            id=f"s{i}", beat=f"b{i}",
            vo=Voiceover(text=f"Line {i}.", voice="adam"),
            visual=Visual(prompt=f"p{i}", seed=i),
            duration=(2.0 if with_duration else None),
        ))
    return Project(id="proj", name="proj", style="dr_vsl", scenes=scenes)


if _HEAVY_DEPS:

    class DelayVideo:
        """Provider whose generate() sleeps, so wall-time proves fan-out."""
        name = "delay-video"
        kind = "video"

        def __init__(self, delay=0.15):
            self.delay = delay

        def available(self):
            return True

        def price(self, **_):
            return 0.0

        def _report(self, **info):
            # Mirror _AsyncVideoBase._report: route through the module-level,
            # thread-local progress so concurrent scenes isolate their observers.
            from avb.assets.video import report_progress

            report_progress(provider=self.name, **info)

        def generate(self, prompt, style, seed, seconds, w, h, cache, **_):
            time.sleep(self.delay)
            # Report on the (thread-local) callback so the isolation test can
            # confirm each scene's generate() talks only to its own observer.
            self._report(scene_tag=seed)
            from PIL import Image
            d = cache.root / "delay"
            d.mkdir(parents=True, exist_ok=True)
            f = d / f"{seed}.png"
            Image.new("RGB", (w, h), (seed * 10 % 255, 0, 0)).save(f)
            return type("R", (), {
                "path": f, "duration": seconds, "cached": False,
                "cost": Cost(self.name, "video", 0.0),
            })()


def test_synth_fans_out_video_generation():
    """Six 0.15s scenes across 4 workers must finish well under serial time."""
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        pipe = _make_pipe(td, workers=4)
        pipe.video = DelayVideo(delay=0.15)
        proj = _project(6)

        t0 = time.perf_counter()
        out = pipe.synth(proj)
        elapsed = time.perf_counter() - t0

        # Serial would be 6 * 0.15 = 0.9s; 4-wide fan-out is ~0.3s.
        assert elapsed < 0.6, f"wall time too high: {elapsed:.2f}s (not concurrent?)"
        assert set(out) == {s.id for s in proj.scenes}


def test_progress_callbacks_are_thread_isolated():
    """A scene's provider reports must reach only that scene's observer.

    If the progress callback were a shared attribute, a scene on thread A would
    receive scene_tag values from scene B's generate(); the assertion that
    scene_id == f"s{scene_tag}" would then fail.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        pipe = _make_pipe(td, workers=6)
        pipe.video = DelayVideo(delay=0.02)
        proj = _project(6)

        records = []

        def _cb(info):
            records.append((info.get("scene_id"), info.get("scene_tag")))

        pipe.video_progress_cb = _cb
        pipe.synth(proj)

        assert records, "no progress was reported"
        for scene_id, scene_tag in records:
            assert scene_id is not None and scene_tag is not None
            assert scene_id == f"s{scene_tag}", (
                f"cross-talk: report for {scene_id} carried tag {scene_tag}"
            )


def test_account_halt_degrades_instead_of_aborting():
    """An account-scope failure must not abort the run.

    The failing scene and every scene after it degrade to a solid backdrop
    (skipped: True for the later ones), but the run still returns a full set of
    scene assets instead of raising and yielding nothing.
    """

    class HaltVideo:
        name = "halt-video"
        kind = "video"

        def available(self):
            return True

        def price(self, **_):
            return 0.0

        def generate(self, prompt, style, seed, seconds, w, h, cache, **_):
            if seed == 3:
                raise VideoGenError(
                    "insufficient balance", retryable=False, scope="account"
                )
            from PIL import Image
            d = cache.root / "halt"
            d.mkdir(parents=True, exist_ok=True)
            f = d / f"{seed}.png"
            Image.new("RGB", (w, h), (0, seed * 20 % 255, 0)).save(f)
            return type("R", (), {
                "path": f, "duration": seconds, "cached": False,
                "cost": Cost(self.name, "video", 0.0),
            })()

    import tempfile

    with tempfile.TemporaryDirectory() as td:
        pipe = _make_pipe(td, workers=4)
        pipe.video = HaltVideo()
        proj = _project(6)

        # Must NOT raise; returns the full set of assets.
        out = pipe.synth(proj)
        assert set(out) == {s.id for s in proj.scenes}

        # Warnings: scene 3 (the account failure) + scenes 4,5,6 (skipped).
        warns = pipe.scene_warnings
        by_index = {w["index"]: w for w in warns}
        assert 3 in by_index and not by_index[3]["skipped"]
        for i in (4, 5, 6):
            assert i in by_index and by_index[i]["skipped"]
            assert by_index[i]["scope"] == "account"
        # Scenes 1 and 2 succeeded: no warnings for them.
        assert 1 not in by_index and 2 not in by_index
