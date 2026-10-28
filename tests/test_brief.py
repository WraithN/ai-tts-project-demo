"""Tests for the brief -> scripts funnel and cinematography enrichment."""

from avb.brief import SCRIPT_TYPE_KEYS, generate_scripts
from avb.parse import parse_script
from avb.ir import Project


def test_heuristic_returns_four_ordered_scripts():
    drafts = generate_scripts(
        product="Lumo lamp",
        selling_points=["sleep faster", "warm wake"],
        style="cinematic",
        use_llm=False,
    )
    assert [d.type for d in drafts] == SCRIPT_TYPE_KEYS
    for d in drafts:
        assert d.narration.strip()
        assert "\n" in d.narration  # multiple lines


def test_parse_enriches_cinematography():
    p = parse_script(
        "This never works for me. Lumo fixes it fast. Try it today.",
        style="cinematic",
    )
    assert p.scenes
    for s in p.scenes:
        # Every scene must carry shot/angle/elements now, not just copy.
        assert s.visual.shot
        assert s.visual.angle
        assert s.visual.elements
        # The composed prompt weaves the cinematography in.
        assert s.visual.shot.split()[0].lower() in s.visual.prompt.lower() or s.visual.shot in s.visual.prompt


def test_ir_roundtrip_keeps_cinematography():
    p = parse_script("Hook line here. Body line explaining the benefit.", style="dr_vsl")
    p2 = Project.from_json(p.to_json())
    for a, b in zip(p.scenes, p2.scenes):
        assert a.visual.shot == b.visual.shot
        assert a.visual.angle == b.visual.angle
        assert (a.visual.elements or []) == (b.visual.elements or [])
