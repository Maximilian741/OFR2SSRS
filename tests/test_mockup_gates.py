"""MUTATION-PROOF THE PREVIEW GATES.

A gate that has never been shown to fail on a known-broken input is not
evidence -- it is decoration, and a clean score from it means nothing.
That is not a theoretical concern here: a previous overlap gate selected
elements by their text content, which excluded images entirely, so an
emblem painting straight across a card's wording scored ZERO defects
while the preview was visibly broken to anyone who looked at it.

So every defect class in ``mockup_audit.DEFECT_CLASSES`` gets a
deliberate corruption below, and the gate must report it. The final test
asserts the mapping is total -- add a defect class without a corruption
and the suite fails, because an unproven gate is worse than no gate: it
manufactures false confidence.
"""
import pathlib
import re
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "backend"))

from converter.validators.mockup_audit import (  # noqa: E402
    DEFECT_CLASSES, defect_counts, mockup_defects,
)

_SHEET = 'box-shadow:0 4px 14px'


def _page(inner, width=816):
    return (f'<div class="o2s-desk"><div class="o2s-page" '
            f'style="max-width:8.50in;{_SHEET}">'
            f'<div style="position:relative;width:{width}px;">'
            f'{inner}</div></div></div>')


def _text(x, y, w, text, h=16, z=2, extra=""):
    return (f'<div style="position:absolute;z-index:{z};left:{x}px;'
            f'top:{y}px;width:{w}px;height:{h}px;font-size:11px;{extra}">'
            f'{text}</div>')


def _image(x, y, w, h, z=0):
    return (f'<img src="data:image/png;base64,AA" '
            f'style="position:absolute;z-index:{z};left:{x}px;top:{y}px;'
            f'width:{w}px;height:{h}px;">')


# a clean baseline every corruption is derived from -- if this is not
# clean, the corruptions below prove nothing
CLEAN = _page(
    _image(400, 40, 120, 120)
    + _text(20, 40, 300, "Permit holder")
    + _text(20, 70, 300, "Issued under state law")
    + _text(20, 100, 300, "Valid through the listed date")
)


def test_the_clean_baseline_is_actually_clean():
    assert mockup_defects(CLEAN) == []


def _corrupt_overlap():
    return _page(_text(20, 40, 300, "Permit holder")
                 + _text(120, 44, 300, "Issued under state law"))


def _corrupt_buried_text():
    """The emblem wins the stacking contest and eats the wording.

    This is the exact defect a text-only metric reported as clean.
    """
    return _page(_image(60, 40, 240, 120, z=5)
                 + _text(80, 60, 200, "State of issue"))


def _corrupt_out_of_bounds():
    return _page(_text(700, 40, 400, "Runs off the right edge"))


def _corrupt_unprotected_cell():
    return _page('<div style="position:absolute;left:0px;top:0px;'
                 'width:40%;">Unguarded cell</div>')


def _corrupt_multi_root():
    return CLEAN + '<div style="color:red">stray second root</div>'


def _corrupt_page_width():
    two = CLEAN.replace('</div></div>', '</div></div>', 1)
    return two[:-6] + ('<div class="o2s-page" style="max-width:11.00in;'
                       f'{_SHEET}"><div style="position:relative;'
                       'width:1056px;">' + _text(20, 40, 300, "Second sheet")
                       + '</div></div></div>')


def _corrupt_empty():
    return _page("")


CORRUPTIONS = {
    "overlap": _corrupt_overlap,
    "buried_text": _corrupt_buried_text,
    "out_of_bounds": _corrupt_out_of_bounds,
    "unprotected_cell": _corrupt_unprotected_cell,
    "multi_root": _corrupt_multi_root,
    "page_width": _corrupt_page_width,
    "empty": _corrupt_empty,
}


@pytest.mark.parametrize("kind", sorted(CORRUPTIONS))
def test_each_gate_detects_its_own_defect(kind):
    """Break one thing; the gate for that thing must report it."""
    counts = defect_counts(CORRUPTIONS[kind]())
    assert counts[kind] > 0, (
        f"gate {kind!r} did not fire on a deliberately broken preview -- "
        f"it cannot certify anything. saw: "
        f"{ {k: v for k, v in counts.items() if v} }")


def test_every_defect_class_has_a_corruption():
    """No unproven gates. Adding a class means proving it can fail."""
    missing = set(DEFECT_CLASSES) - set(CORRUPTIONS)
    assert not missing, (
        f"defect classes with no proof they can detect anything: {missing}")


def test_text_over_an_image_is_fine_when_the_text_is_on_top():
    """Overlap is not the defect -- losing the stacking contest is.

    Oracle legitimately lays wording across an emblem, so a geometry-only
    rule would flag every correct certificate. The gate must accept the
    same geometry when z-order keeps the words readable.
    """
    same_geometry_but_readable = _page(
        _image(60, 40, 240, 120, z=0) + _text(80, 60, 200, "State of issue"))
    assert defect_counts(same_geometry_but_readable)["buried_text"] == 0


def test_pages_are_scored_independently():
    """Sheets restart at y=0, so boxes on different pages never collide.

    Scoring the document as one plane reported pairs that print inches
    apart on separate sheets as overlapping.
    """
    two_sheets = (
        '<div class="o2s-desk">'
        f'<div class="o2s-page" style="max-width:8.50in;{_SHEET}">'
        '<div style="position:relative;width:816px;">'
        + _text(20, 40, 300, "First sheet line") + '</div></div>'
        f'<div class="o2s-page" style="max-width:8.50in;{_SHEET}">'
        '<div style="position:relative;width:816px;">'
        + _text(20, 40, 300, "Second sheet line") + '</div></div></div>')
    assert defect_counts(two_sheets)["overlap"] == 0


def test_a_clipped_box_is_measured_by_its_glyphs_not_its_width():
    """A short value in a wide nowrap box cannot paint across the page."""
    clipped = _page(
        _text(20, 40, 400, "12", extra="overflow:hidden;white-space:nowrap;"
                                       "text-overflow:ellipsis;")
        + _text(200, 40, 300, "Neighbouring label"))
    assert defect_counts(clipped)["overlap"] == 0


def test_real_converted_previews_pass_every_gate():
    """The gates run against genuine pipeline output, not just fixtures."""
    from converter import convert

    fixtures = sorted(
        (pathlib.Path(__file__).resolve().parents[1] / "samples" / "oracle")
        .glob("*.xml"))
    if not fixtures:
        pytest.skip("no sample reports available")
    checked = 0
    for f in fixtures:
        try:
            html = convert(f.read_bytes())["mockup_html"]
        except Exception:  # noqa: BLE001 - conversion is covered elsewhere
            continue
        if not html.strip():
            continue
        checked += 1
        found = mockup_defects(html)
        assert not found, (
            f"{f.name} preview has visual defects: "
            f"{[d['detail'] for d in found][:4]}")
    assert checked, "no sample report produced a preview to audit"


def test_gate_output_names_only_known_classes():
    for d in mockup_defects(_corrupt_overlap()):
        assert d["kind"] in DEFECT_CLASSES
        assert d["detail"] and not re.search(r"\bNone\b", d["kind"])
