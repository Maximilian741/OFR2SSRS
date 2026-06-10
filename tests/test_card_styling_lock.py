"""Regression lock-in tests for the grouped-card layout styling
that ships with SAMPLE_MASTER_DETAIL (and every other report whose preview
detects as a grouped-card / tabular-details kind).

Three properties this file pins down. Every property is asserted
generically against every source.xml under tests/fixtures/source_of_truth
so the same guarantees keep holding for future card reports without
any per-report opt-in.

PROPERTY A -- subhdr_bg must be readable light grey.
    Oracle source XML routinely puts a text/foreground color (e.g.
    "#282828") into a <visualSettings> background slot. Without a
    luminance floor that color leaks into the SubHdr strip and renders
    as a near-black bar that the user can't read. _resolve_palette
    must clamp subhdr_bg to luminance >= 0.70 (or fall back to the
    default light grey).

PROPERTY B -- card-header grey extends through the whole card.
    Reference Oracle Reports cards render the SubHdr (Complaint ID)
    AND every label/value pair below it (Owner / Status / Location /
    City / Received / Bust / Contractor / ...) on ONE continuous
    light-grey panel, with the action sub-table starting only at the
    Action Type row. Therefore inside the Card_Header rectangle, the
    rectangle's own BackgroundColor must equal subhdr_bg AND every
    Textbox's BackgroundColor must equal subhdr_bg. No white blobs
    interrupting the grey.

PROPERTY C -- action sub-table has side borders.
    Act_Header and Act_Detail must each carry a LeftBorder and
    RightBorder with Style=Solid so the action rows visually belong
    to the card above them. Without these borders the action data
    floats loose under the grey card.

All tests are name-agnostic; they walk every case_NNN fixture and
skip fixtures whose RDL doesn't emit a grouped-card tablix (e.g. the
letter / certificate kind).
"""
from __future__ import annotations
import re
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402
from converter.generators.rdl import _resolve_palette  # noqa: E402
from converter.parsers.oracle_xml import parse_oracle_xml  # noqa: E402


FIXTURES = HERE / "fixtures" / "source_of_truth"
LUM_FLOOR = 0.70  # readable-grey luminance floor


def _hex_lum(c: str) -> float:
    """Rec 601 luminance for a #RRGGBB string."""
    s = (c or "").lstrip("#")
    if len(s) != 6:
        return 1.0
    try:
        r = int(s[0:2], 16) / 255.0
        g = int(s[2:4], 16) / 255.0
        b = int(s[4:6], 16) / 255.0
    except ValueError:
        return 1.0
    return 0.299 * r + 0.587 * g + 0.114 * b


def _cases():
    if not FIXTURES.exists():
        return []
    out = []
    for d in sorted(FIXTURES.iterdir()):
        src = d / "source.xml"
        if src.exists():
            out.append(pytest.param(d.name, src, id=d.name))
    return out


def _rdl_for(src_path: Path) -> str:
    return convert(src_path.read_bytes())["rdl_xml"]


def _palette_for(src_path: Path) -> dict:
    return _resolve_palette(parse_oracle_xml(src_path.read_bytes()))


def _find_rectangle(rdl: str, name: str):
    """Return the full <Rectangle Name="..."> ... </Rectangle> block
    matching `name`, or None if not present in this report's RDL."""
    m = re.search(
        rf'<Rectangle Name="{re.escape(name)}">.*?</Rectangle>',
        rdl, re.DOTALL,
    )
    return m.group(0) if m else None


# -----------------------------------------------------------------------------
# PROPERTY A -- subhdr_bg is readable light grey for every report.
# -----------------------------------------------------------------------------
@pytest.mark.parametrize("case_name,src_path", _cases())
def test_subhdr_bg_is_readable_light_grey(case_name, src_path):
    """The resolved subhdr_bg must be light enough to read dark text on."""
    pal = _palette_for(src_path)
    subhdr_bg = pal["subhdr_bg"]
    assert subhdr_bg.startswith("#") and len(subhdr_bg) == 7, (
        f"[{case_name}] subhdr_bg is not a #RRGGBB string: {subhdr_bg!r}"
    )
    lum = _hex_lum(subhdr_bg)
    assert lum >= LUM_FLOOR, (
        f"[{case_name}] subhdr_bg={subhdr_bg} has luminance {lum:.3f} "
        f"< floor {LUM_FLOOR}. Would render as a near-black SubHdr "
        f"strip and bury the Complaint-ID text."
    )


@pytest.mark.parametrize("case_name,src_path", _cases())
def test_subhdr_fg_has_contrast_against_subhdr_bg(case_name, src_path):
    """subhdr_fg must NOT be light-on-light (label would be invisible)."""
    pal = _palette_for(src_path)
    bg_lum = _hex_lum(pal["subhdr_bg"])
    fg_lum = _hex_lum(pal["subhdr_fg"])
    # Both light -> bad. We require at least 0.30 luminance separation.
    assert abs(bg_lum - fg_lum) >= 0.30, (
        f"[{case_name}] subhdr_bg={pal['subhdr_bg']} (lum {bg_lum:.2f}) "
        f"and subhdr_fg={pal['subhdr_fg']} (lum {fg_lum:.2f}) have "
        f"insufficient contrast -- header text will be hard to read."
    )


# -----------------------------------------------------------------------------
# PROPERTY B -- grey extends through the whole Card_Header rectangle.
# -----------------------------------------------------------------------------
@pytest.mark.parametrize("case_name,src_path", _cases())
def test_card_header_rectangle_uses_subhdr_bg(case_name, src_path):
    """The Card_Header Rectangle (if this report emits a grouped card)
    must declare BackgroundColor=subhdr_bg as its outer style."""
    rdl = _rdl_for(src_path)
    block = _find_rectangle(rdl, "Card_Header")
    if block is None:
        pytest.skip(f"{case_name}: no Card_Header (not a grouped-card report)")
    pal = _palette_for(src_path)
    subhdr_bg = pal["subhdr_bg"].lower()

    # First BackgroundColor in the rectangle subtree is the rect's
    # own outer-Style bg (it is emitted before any nested ReportItems
    # / Textbox blocks, which carry their own BackgroundColors).
    bg = re.search(r"<BackgroundColor>([^<]+)</BackgroundColor>", block)
    assert bg, f"[{case_name}] Card_Header missing any BackgroundColor"
    assert bg.group(1).lower() == subhdr_bg, (
        f"[{case_name}] Card_Header rect bg = {bg.group(1)} but "
        f"subhdr_bg = {pal['subhdr_bg']}. The grey panel must match "
        f"the resolved palette."
    )


@pytest.mark.parametrize("case_name,src_path", _cases())
def test_card_header_textboxes_all_share_subhdr_bg(case_name, src_path):
    """Every Textbox inside the Card_Header rectangle must use
    subhdr_bg so the grey forms ONE continuous panel from the SubHdr
    strip down through the last header field (e.g. Contractor).

    No white blobs allowed -- a white textbox would punch a hole in
    the grey panel and reproduce the old 'only the top strip is
    coloured' look.
    """
    rdl = _rdl_for(src_path)
    block = _find_rectangle(rdl, "Card_Header")
    if block is None:
        pytest.skip(f"{case_name}: no Card_Header (not a grouped-card report)")
    pal = _palette_for(src_path)
    subhdr_bg = pal["subhdr_bg"].lower()

    # Every <Textbox>...</Textbox> nested inside the Card_Header rect.
    textbox_blocks = re.findall(
        r"<Textbox\b[^>]*>.*?</Textbox>", block, re.DOTALL
    )
    assert textbox_blocks, (
        f"[{case_name}] Card_Header contains zero Textboxes -- "
        f"expected at least the SubHdr strip plus header fields."
    )

    offenders = []
    for tb in textbox_blocks:
        bgs = re.findall(r"<BackgroundColor>([^<]+)</BackgroundColor>", tb)
        if not bgs:
            # A Textbox without an explicit bg inherits the rect bg --
            # that's fine, no offence.
            continue
        for bg in bgs:
            if bg.lower() != subhdr_bg:
                name_m = re.search(r'Name="([^"]+)"', tb[:80])
                offenders.append(f"{name_m.group(1) if name_m else '?'}={bg}")
                break
    assert not offenders, (
        f"[{case_name}] Textboxes in Card_Header use a bg other than "
        f"subhdr_bg={pal['subhdr_bg']}: {offenders[:6]} ... -- the grey "
        f"panel must be continuous, no white interruptions."
    )


@pytest.mark.parametrize("case_name,src_path", _cases())
def test_card_header_bg_color_is_uniform(case_name, src_path):
    """Across the whole Card_Header subtree exactly ONE BackgroundColor
    value should be in use (the subhdr_bg). Catches accidental mix of
    multiple greys / a stray white that would split the panel."""
    rdl = _rdl_for(src_path)
    block = _find_rectangle(rdl, "Card_Header")
    if block is None:
        pytest.skip(f"{case_name}: no Card_Header (not a grouped-card report)")
    bgs = {
        bg.lower()
        for bg in re.findall(r"<BackgroundColor>([^<]+)</BackgroundColor>", block)
    }
    assert len(bgs) == 1, (
        f"[{case_name}] Card_Header uses multiple background colors "
        f"{sorted(bgs)} -- panel should be ONE uniform grey."
    )


# -----------------------------------------------------------------------------
# PROPERTY C -- action sub-table has visible side borders.
# -----------------------------------------------------------------------------
def _has_solid_border(rect_block: str, side: str) -> bool:
    """True iff the rect declares <side>...<Style>Solid</Style>...</side>.

    Looks directly for the side element inside the rectangle subtree.
    LeftBorder/RightBorder/TopBorder/BottomBorder only appear inside
    the rectangle's outer <Style>, so a direct search is unambiguous
    and avoids the nested-<Style> matching trap (each Border block
    contains its own <Style>Solid</Style> sub-element)."""
    side_m = re.search(rf"<{side}>(.*?)</{side}>", rect_block, re.DOTALL)
    if not side_m:
        return False
    sty = re.search(r"<Style>([^<]+)</Style>", side_m.group(1))
    return bool(sty and sty.group(1).strip().lower() == "solid")


@pytest.mark.parametrize("case_name,src_path", _cases())
def test_act_header_has_side_borders(case_name, src_path):
    rdl = _rdl_for(src_path)
    block = _find_rectangle(rdl, "Act_Header")
    if block is None:
        pytest.skip(f"{case_name}: no Act_Header (no action sub-table)")
    assert _has_solid_border(block, "LeftBorder"), (
        f"[{case_name}] Act_Header missing solid LeftBorder -- action "
        f"row will float loose under the card."
    )
    assert _has_solid_border(block, "RightBorder"), (
        f"[{case_name}] Act_Header missing solid RightBorder."
    )


@pytest.mark.parametrize("case_name,src_path", _cases())
def test_act_detail_has_side_and_bottom_borders(case_name, src_path):
    rdl = _rdl_for(src_path)
    block = _find_rectangle(rdl, "Act_Detail")
    if block is None:
        pytest.skip(f"{case_name}: no Act_Detail (no action sub-table)")
    assert _has_solid_border(block, "LeftBorder"), (
        f"[{case_name}] Act_Detail missing solid LeftBorder."
    )
    assert _has_solid_border(block, "RightBorder"), (
        f"[{case_name}] Act_Detail missing solid RightBorder."
    )
    assert _has_solid_border(block, "BottomBorder"), (
        f"[{case_name}] Act_Detail missing solid BottomBorder -- "
        f"action data should be closed off with a bottom rule."
    )


# -----------------------------------------------------------------------------
# Direct unit test of the _resolve_palette luminance clamp -- catches
# regressions to the heuristic itself without needing a real XML.
# -----------------------------------------------------------------------------
def test_resolve_palette_rejects_dark_subhdr_bg_candidate():
    """Synthesise a report whose only non-band background is a near-
    black '#282828' -- _resolve_palette must clamp it away."""
    from converter.models import LayoutGroup, ParsedReport
    rep = ParsedReport(name="synthetic", layout=[
        LayoutGroup(
            name="band",
            kind="repeating_frame",
            background_color="#00007F",  # band
            children=[
                LayoutGroup(
                    name="inner",
                    kind="frame",
                    background_color="#282828",  # leaked text color
                ),
            ],
        ),
    ])
    pal = _resolve_palette(rep)
    assert _hex_lum(pal["subhdr_bg"]) >= LUM_FLOOR, (
        f"_resolve_palette let a dark color (#282828) become "
        f"subhdr_bg = {pal['subhdr_bg']}"
    )


def test_resolve_palette_keeps_light_subhdr_bg_candidate():
    """A genuinely light non-band background (#e8e8e8) should be
    preferred over the default. Proves the clamp doesn't blanket-reject
    XML colors -- it only rejects dark ones."""
    from converter.models import LayoutGroup, ParsedReport
    rep = ParsedReport(name="synthetic", layout=[
        LayoutGroup(
            name="band",
            kind="repeating_frame",
            background_color="#00007F",
            children=[
                LayoutGroup(
                    name="inner",
                    kind="frame",
                    background_color="#e8e8e8",
                ),
            ],
        ),
    ])
    pal = _resolve_palette(rep)
    assert pal["subhdr_bg"].lower() == "#e8e8e8", (
        f"Light XML color was wrongly rejected: subhdr_bg = {pal['subhdr_bg']}"
    )
