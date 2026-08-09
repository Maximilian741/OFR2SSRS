"""Declared band geometry: row pitch, tint swatch + gutter, band placement.

Oracle states the detail row's real geometry on the record's repeating
frame -- its own declared height plus ``vertSpaceBetweenFrames``, the blank
gutter it leaves between consecutive instances -- and states the caption
band's own y/height on the sibling header frame. Truth-PDF measured: the
painted swatch is exactly the frame height, the pitch is height+gutter, and
the caption band sits at ``body <location y> + its declared y``.

Synthesized values instead of the declaration inflated one report's rows 26%
(and its page count ~29%), shrank another's 10%, and printed a caption band
14pt low and 7.7pt tall. Everything here is structural: no report, column or
label from any real source appears in this file.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402

FRAME_H = 0.17212      # declared record-frame height
GAP = 0.0500           # declared vertSpaceBetweenFrames
HDR_Y = 0.00525        # declared caption-frame y (body coordinates)
HDR_H = 0.19263        # declared caption-frame height
BODY_Y = 0.50000       # declared <body><location y>
CHROME_Y = 0.04163     # declared margin-band top


def _band_xml(frame_h: float = FRAME_H, gap: float = GAP,
              fill: bool = True) -> bytes:
    """A flat 3-column list whose record frame declares a height and an
    inter-frame gutter, under a static caption frame."""
    _gap = f' vertSpaceBetweenFrames="{gap:.4f}"' if gap else ""
    _vs = ('<visualSettings fillPattern="solid" '
           'fillForegroundColor="r88g88b75"/>') if fill else ""
    cols = (("ALPHA", 0.0, 3.0), ("BETA", 3.10, 3.0), ("GAMMA", 6.20, 2.7))
    fields = "".join(
        f'<field name="F_{c}" source="{c}" alignment="start">'
        f'<font face="Arial" size="8"/>'
        f'<geometryInfo x="{x:.5f}" y="0.20288" width="{w:.5f}"'
        f' height="{frame_h:.5f}"/></field>'
        for c, x, w in cols)
    caps = "".join(
        f'<text name="B_{c}"><textSettings spacing="0"/>'
        f'<geometryInfo x="{x:.5f}" y="{HDR_Y:.5f}" width="{w:.5f}"'
        f' height="0.18000"/><textSegment>'
        f'<font face="Arial" size="9" textColor="white"/>'
        f'<string><![CDATA[{c.capitalize()}]]></string>'
        f'</textSegment></text>'
        for c, x, w in cols)
    return (
        '<?xml version="1.0"?>'
        '<report name="BANDGEO" DTDVersion="9.0.2.0.10"><data>'
        '<dataSource name="Q_1">'
        '<select><![CDATA[SELECT ALPHA, BETA, GAMMA FROM T]]></select>'
        '<group name="G_ROW">'
        '<dataItem name="ALPHA" datatype="vchar2"/>'
        '<dataItem name="BETA" datatype="vchar2"/>'
        '<dataItem name="GAMMA" datatype="vchar2"/>'
        '</group></dataSource></data><layout>'
        '<section name="main" width="11.00000" height="8.50000"'
        ' orientation="landscape">'
        '<body width="10.00000" height="7.00000">'
        f'<location x="0.50000" y="{BODY_Y:.5f}"/>'
        '<frame name="M_GRP">'
        '<geometryInfo x="0.00000" y="0.00525" width="9.00000"'
        ' height="0.38013"/>'
        f'<repeatingFrame name="R_ROW" source="G_ROW" printDirection="down"'
        f' minWidowRecords="1" columnMode="no"{_gap}>'
        f'<geometryInfo x="0.00000" y="0.20288" width="9.00000"'
        f' height="{frame_h:.5f}"/>'
        f'{_vs}{fields}</repeatingFrame>'
        '<frame name="M_HDR">'
        f'<geometryInfo x="0.00000" y="{HDR_Y:.5f}" width="9.00000"'
        f' height="{HDR_H:.5f}"/>'
        '<advancedLayout printObjectOnPage="allPage"'
        ' basePrintingOn="anchoringObject"/>'
        '<visualSettings fillPattern="solid" fillForegroundColor="darkblue"/>'
        f'{caps}</frame></frame></body><margin>'
        '<text name="B_TITLE" templateSection="main">'
        '<textSettings justify="center" spacing="0"/>'
        f'<geometryInfo x="3.00000" y="{CHROME_Y:.5f}" width="3.00000"'
        ' height="0.20813"/><textSegment>'
        '<font face="Arial" size="12" bold="yes"/>'
        '<string><![CDATA[Sample Band Geometry]]></string>'
        '</textSegment></text></margin></section></layout></report>'
    ).encode()


def _rows(rdl: str):
    return [float(h) for h in re.findall(
        r"<TablixRow>\s*<Height>([\d.]+)in", rdl)]


def _item(rdl: str, tag: str, name: str):
    """The named report item as an ELEMENT.

    Geometry must be read off the element ITSELF: a band rectangle CONTAINS
    the cells it paints (that containment is what lets the fill grow with a
    row whose data wraps), so a text-blob regex would return a nested
    child's <Top>/<Height>/<Left>/<Width>.
    """
    import xml.etree.ElementTree as ET
    for el in ET.fromstring(rdl).iter():
        if el.tag.rsplit("}", 1)[-1] == tag and el.get("Name") == name:
            return el
    raise AssertionError(f"missing {tag} {name}")


def _own(el, prop: str) -> float:
    for ch in el:
        if ch.tag.rsplit("}", 1)[-1] == prop:
            return float((ch.text or "").replace("in", ""))
    raise AssertionError(f"{el.get('Name')} declares no {prop}")


def _slack() -> float:
    """The DECLARED blank between the caption frame's bottom edge and the
    record band's top edge (the fixture states both absolutely)."""
    return round(0.20288 - (HDR_Y + HDR_H), 5)


def _tag(rdl: str, tag: str, default=None):
    m = re.search(rf"<{tag}>([\d.]+)in</{tag}>", rdl)
    return float(m.group(1)) if m else default


def _tablix_top(rdl: str) -> float:
    body = rdl.split("</Tablix>")[0]
    return float(re.findall(r"<Top>([\d.]+)in</Top>", body)[-1])


# --------------------------------------------------------------------------
# (a) row pitch = declared frame height + declared inter-frame space
# --------------------------------------------------------------------------

def test_row_pitch_is_declared_height_plus_inter_frame_space():
    rdl = convert(_band_xml())["rdl_xml"]
    rows = _rows(rdl)
    assert len(rows) >= 2, rows
    assert abs(rows[1] - (FRAME_H + GAP)) < 0.002, rows


def test_row_pitch_follows_a_different_declaration():
    """A taller declaration produces a taller pitch -- the rule reads the
    source, it is not a constant."""
    rdl = convert(_band_xml(frame_h=0.68018))["rdl_xml"]
    assert abs(_rows(rdl)[1] - (0.68018 + GAP)) < 0.002, _rows(rdl)


def test_no_declared_gutter_means_pitch_is_just_the_frame_height():
    rdl = convert(_band_xml(gap=0.0))["rdl_xml"]
    assert abs(_rows(rdl)[1] - FRAME_H) < 0.002, _rows(rdl)


# --------------------------------------------------------------------------
# (b) banded fill: the record's own height, gutter left unpainted
# --------------------------------------------------------------------------

def test_banded_fill_is_the_record_height_and_leaves_the_gutter_white():
    """The record frame DECLARES the fill, so ONE rectangle paints it over
    the frame's declared extents -- never one swatch per cell.

    Truth-measured (engine PDF vs the Oracle PDF of a landscape summary
    report): the per-cell emission tiled a declared 2.70483in band into
    FOUR swatches split by three 0.72pt white gutters, starting 8.77pt left
    of the declaration and stopping 18.5pt short of the frame's right edge.
    The truth PDF prints ONE 194.76pt band at 720.92 -> 915.68pt.
    """
    rdl = convert(_band_xml())["rdl_xml"]
    # ONE band rectangle for the whole record -- no per-cell tiles.
    assert rdl.count('<Rectangle Name="Band_0">') == 1, "band must be ONE rect"
    band = rdl.split('<Rectangle Name="Band_0">', 1)[1].split(
        "</Rectangle>", 1)[0]
    assert "<BackgroundColor>#E0E0BF</BackgroundColor>" in band, band[:400]
    _band = _item(rdl, "Rectangle", "Band_0")
    # It is exactly the DECLARED record height, not the full pitch, so the
    # declared gutter between records stays white...
    assert abs(_own(_band, "Height") - FRAME_H) < 0.002, band[:400]
    # ...it starts at the frame's declared x and spans the frame's declared
    # WIDTH (9.0in), i.e. past the last declared cell's right edge (8.90in)
    left = _own(_band, "Left")
    width = _own(_band, "Width")
    assert abs(left) < 0.002, band[:400]
    assert abs(width - 9.0) < 0.011, (left, width)
    assert left + width > 8.90 + 0.005, "band stops at the last cell's edge"
    # ...and it lives inside an UNPAINTED wrapper (the gutter stays white).
    gap = rdl.split('<Rectangle Name="Band_0_Wrap">', 1)[1].split(
        "<ReportItems>", 1)[0]
    assert not gap.strip(), gap
    # The declared height is a MINIMUM, not a cap: the cells the band paints
    # are its CONTENTS, so the engine grows the fill with a record whose data
    # wraps (a sibling fill keeps its authored height and leaves the bottom of
    # every grown row unpainted).
    _held = {c.get("Name") for c in _band.iter()
             if c.tag.rsplit("}", 1)[-1] == "Textbox"}
    for _c in ("ALPHA", "BETA", "GAMMA"):
        assert f"Cell_{_c}" in _held, (
            f"Cell_{_c} must be INSIDE the band rectangle", sorted(_held))
    # The declared gutter rides with the band as an unpainted spacer, so it
    # survives that growth instead of being swallowed by the taller row.
    _gut = _item(rdl, "Rectangle", "Band_0_Gutter")
    assert abs(_own(_gut, "Height") - GAP) < 0.002, _own(_gut, "Height")
    assert abs(_own(_gut, "Top") - FRAME_H) < 0.002, _own(_gut, "Top")
    assert not any(c.tag.rsplit("}", 1)[-1] == "BackgroundColor"
                   for c in _gut.iter()), "the gutter must stay UNPAINTED"
    # No cell keeps a tile of the band fill: the FRAME paints, not the cell.
    for _c in ("ALPHA", "BETA", "GAMMA"):
        cell = re.search(rf'<Textbox Name="Cell_{_c}">.*?</Textbox>',
                         rdl, re.S)
        assert cell and "#E0E0BF" not in cell.group(0), _c


def test_unbanded_report_keeps_a_plain_cell():
    """No declared fill -> no swatch rectangle is invented."""
    rdl = convert(_band_xml(fill=False))["rdl_xml"]
    assert "<Rectangle Name=\"Band_" not in rdl


# --------------------------------------------------------------------------
# (c) band placement: declared y, declared height
# --------------------------------------------------------------------------

def test_caption_band_height_is_declared():
    """The PAINTED caption band is exactly the declared frame height, and
    the header ROW is that height PLUS the declared slack below it.

    Oracle states the caption frame and the record band absolutely, so the
    blank between them (here 0.20288 - 0.00525 - 0.19263 = 0.005in) is a
    declaration too. Tablix rows stack edge to edge, so the header row is
    the only place that slack can live -- truth-PDF measured on a landscape
    summary: the navy band ends at 0.6978 and the truth's first record fill
    starts at 0.703, while a header row sized to the band alone started it
    at 0.698, flush against the band.
    """
    rdl = convert(_band_xml())["rdl_xml"]
    slack = _slack()
    assert slack > 0, "fixture must declare slack under the caption frame"
    assert abs(_rows(rdl)[0] - (HDR_H + slack)) < 0.002, (_rows(rdl), slack)
    # ...and the slack is UNPAINTED: the band rectangle keeps its own
    # declared height, so the extra sliver stays white.
    assert abs(_own(_item(rdl, "Rectangle", "HdrBand_0"), "Height")
               - HDR_H) < 0.002, "the painted band grew with the row"


def test_first_record_band_lands_at_its_declared_y():
    """The declared slack under the caption band puts the first detail row
    on its declared y instead of flush against the header."""
    rdl = convert(_band_xml())["rdl_xml"]
    top_margin = _tag(rdl, "TopMargin", 0.0)
    ph = rdl.split("<PageHeader>", 1)[1].split("</PageHeader>", 1)[0]
    ph_h = float(re.search(r"<Height>([\d.]+)in</Height>", ph).group(1))
    first_detail_y = (top_margin + ph_h + _tablix_top(rdl) + _rows(rdl)[0])
    assert abs(first_detail_y - (BODY_Y + 0.20288)) < 0.011, (
        "the first record band must start at body y + its declared y",
        first_detail_y, BODY_Y + 0.20288)


def test_abutting_caption_frame_declares_no_slack():
    """No declared gap -> nothing invented: the header row is the band."""
    src = _band_xml().replace(b'y="0.20288"', b'y="0.19788"')
    rdl = convert(src)["rdl_xml"]
    assert abs(_rows(rdl)[0] - HDR_H) < 0.002, _rows(rdl)


def test_caption_band_lands_at_its_declared_paper_y():
    """TopMargin + PageHeader height + the table's Top must add up to the
    declared body origin plus the caption frame's own declared y."""
    rdl = convert(_band_xml())["rdl_xml"]
    top_margin = _tag(rdl, "TopMargin", 0.0)
    ph = rdl.split("<PageHeader>", 1)[1].split("</PageHeader>", 1)[0]
    ph_h = float(re.search(r"<Height>([\d.]+)in</Height>", ph).group(1))
    paper_y = top_margin + ph_h + _tablix_top(rdl)
    assert abs(paper_y - (BODY_Y + HDR_Y)) < 0.011, (
        top_margin, ph_h, _tablix_top(rdl), paper_y)
