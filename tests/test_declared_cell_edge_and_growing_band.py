"""Cell-level declared edges, declared header slack, and a band that grows.

Three measurements taken off the Oracle-rendered PDF of a landscape
summary report (the engine PDF of our own RDL was diffed against it
drawing-by-drawing, in inches):

(a) STROKE WEIGHT OF A DECLARED CELL EDGE.  Each column-header cell
    declares ``<visualSettings linePattern="solid"
    lineForegroundColor="white"/>`` and NO ``lineWidth``.  The truth PDF
    strokes all ten of them at width **0.0** -- Oracle's device hairline --
    exactly as it strokes a no-lineWidth ``<line>``.  The emitter's house
    0.5pt printed those white separators as visibly thick gaps between the
    captions.  The settled dialect ("no lineWidth => device hairline") is a
    property of the DECLARATION, not of the object kind, so it applies to a
    cell edge as much as to a standalone rule.  SSRS has no zero-width
    border, so the hairline maps to its thinnest stroke; a DECLARED
    lineWidth still maps 1:1.

(b) DECLARED INTER-BAND SLACK.  The caption frame is declared at
    y=0.00525 height=0.19263 (bottom 0.19788) and the record band at
    y=0.20288, i.e. 0.005in of declared blank between them; with the body
    origin at y=0.5 the truth's first tinted fill starts at 0.703.
    Stacking the detail rows straight onto the header row dropped that
    slack and started the fill at 0.698, flush against the band.  Tablix
    rows abut, so the header row is the only place the slack can live.

(c) A BAND FILL FOLLOWS ITS ROW.  The truth paints the record's tint over
    the whole record.  Emitting the fill as a SIBLING of the cells freezes
    it at the declared height, so a record whose data wraps onto a second
    line kept a 0.1721in swatch inside a 0.375in row and left the bottom
    of every grown row unpainted (engine-measured).  An RDL rectangle's
    declared height is a MINIMUM and the engine grows a container around
    its contents -- so the cells must be the fill's CONTENTS.  The declared
    inter-record gutter rides along as an unpainted spacer below the fill
    so it survives the growth instead of being eaten by the taller row.

Synthetic and structural throughout: no report, column, label or colour
from any client source appears in this file.
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402

# --- the declaration under test (inches) ---------------------------------
BODY_Y = 0.50000        # <body><location y>
HDR_Y = 0.00525         # caption frame y   -> bottom 0.19788
HDR_H = 0.19263         # caption frame height
BAND_Y = 0.20288        # record band y     -> 0.005in of declared slack
BAND_H = 0.17212        # record band height
GUTTER = 0.05000        # vertSpaceBetweenFrames
SLACK = round(BAND_Y - (HDR_Y + HDR_H), 5)     # == 0.005
COLS = (("ALPHA", "Alpha", 0.00000, 3.00000),
        ("BETA", "Beta", 3.10000, 3.00000),
        ("GAMMA", "Gamma", 6.20000, 2.70000))
HAIRLINE = "0.25pt"     # the thinnest stroke RDL can express


def _src(cell_line_width: str = "", band_fill: bool = True) -> bytes:
    """A landscape summary: a solid caption frame whose cells each declare
    a solid white edge, over a record band declaring a tint fill and an
    inter-frame gutter, with declared slack between the two frames."""
    _lw = f' lineWidth="{cell_line_width}"' if cell_line_width else ""
    _fill = ('<visualSettings fillPattern="solid" '
             'fillForegroundColor="r88g88b75"/>') if band_fill else ""
    items = "".join(
        f'<dataItem name="{c}" datatype="vchar2" columnOrder="{i + 1}"'
        f' defaultLabel="{cap}"/>'
        for i, (c, cap, _x, _w) in enumerate(COLS))
    fields = "".join(
        f'<field name="F_{c}" source="{c}" alignment="start">'
        f'<font face="Arial" size="8"/>'
        f'<geometryInfo x="{x:.5f}" y="{BAND_Y:.5f}" width="{w:.5f}"'
        f' height="{BAND_H:.5f}"/></field>'
        for c, _cap, x, w in COLS)
    caps = "".join(
        f'<text name="B_{c}"><textSettings spacing="0"/>'
        f'<geometryInfo x="{x:.5f}" y="{HDR_Y:.5f}" width="{w:.5f}"'
        f' height="{HDR_H:.5f}"/>'
        f'<visualSettings linePattern="solid" lineForegroundColor="white"'
        f'{_lw}/>'
        f'<textSegment><font face="Arial" size="9" textColor="white"/>'
        f'<string><![CDATA[{cap}]]></string></textSegment></text>'
        for c, cap, x, w in COLS)
    return (
        '<?xml version="1.0"?>'
        '<report name="EDGEBAND" DTDVersion="9.0.2.0.10"><data>'
        '<dataSource name="Q_1">'
        '<select><![CDATA[SELECT ALPHA, BETA, GAMMA FROM T]]></select>'
        f'<group name="G_ROW">{items}</group>'
        '</dataSource></data><layout>'
        '<section name="main" width="11.00000" height="8.50000"'
        ' orientation="landscape">'
        '<body width="10.00000" height="7.00000">'
        f'<location x="0.50000" y="{BODY_Y:.5f}"/>'
        '<frame name="M_GRP">'
        '<geometryInfo x="0.00000" y="0.00525" width="9.00000"'
        ' height="0.38013"/>'
        '<repeatingFrame name="R_ROW" source="G_ROW" printDirection="down"'
        f' minWidowRecords="1" columnMode="no"'
        f' vertSpaceBetweenFrames="{GUTTER:.4f}">'
        f'<geometryInfo x="0.00000" y="{BAND_Y:.5f}" width="9.00000"'
        f' height="{BAND_H:.5f}"/>'
        f'{_fill}{fields}</repeatingFrame>'
        '<frame name="M_HDR">'
        f'<geometryInfo x="0.00000" y="{HDR_Y:.5f}" width="9.00000"'
        f' height="{HDR_H:.5f}"/>'
        '<advancedLayout printObjectOnPage="allPage"'
        ' basePrintingOn="anchoringObject"/>'
        '<visualSettings fillPattern="solid" fillForegroundColor="darkblue"/>'
        f'{caps}</frame></frame></body>'
        '</section></layout></report>'
    ).encode()


def _item(rdl: str, tag: str, name: str):
    for el in ET.fromstring(rdl).iter():
        if el.tag.rsplit("}", 1)[-1] == tag and el.get("Name") == name:
            return el
    raise AssertionError(f"missing {tag} {name}")


def _own(el, prop: str):
    """A property off the element ITSELF -- never a descendant's (a band
    rectangle now CONTAINS the cells it paints)."""
    for ch in el:
        if ch.tag.rsplit("}", 1)[-1] == prop:
            return (ch.text or "").strip()
    return None


def _border(tb):
    """``(Style, Color, Width)`` of a textbox's own Border."""
    for st in tb:
        if st.tag.rsplit("}", 1)[-1] != "Style":
            continue
        for bd in st:
            if bd.tag.rsplit("}", 1)[-1] != "Border":
                continue
            got = {c.tag.rsplit("}", 1)[-1]: (c.text or "").strip()
                   for c in bd}
            return got.get("Style"), got.get("Color"), got.get("Width")
    return None, None, None


def _rows(rdl: str):
    out = []
    for row in ET.fromstring(rdl).iter():
        if row.tag.rsplit("}", 1)[-1] != "TablixRow":
            continue
        h = _own(row, "Height")
        if h:
            out.append(float(h.replace("in", "")))
    return out


# --------------------------------------------------------------------------
# (a) a DECLARED cell edge with no lineWidth is the device hairline
# --------------------------------------------------------------------------

def test_undeclared_cell_line_width_strokes_as_the_device_hairline():
    rdl = convert(_src())["rdl_xml"]
    for _c, _cap, _x, _w in COLS:
        style, color, width = _border(_item(rdl, "Textbox", f"Hdr_{_c}"))
        assert style == "Solid", (_c, style)
        assert (color or "").upper() == "#FFFFFF", (
            "the DECLARED lineForegroundColor is the edge ink", _c, color)
        assert width == HAIRLINE, (
            "a declared edge with NO lineWidth is Oracle's device hairline "
            "(the truth strokes all ten header cells at width 0.0); the "
            "house weight printed the white separators as thick gaps",
            _c, width)


def test_a_declared_cell_line_width_is_honored_one_to_one():
    """PROVE THE RULE READS THE SOURCE: the hairline is what an ABSENT
    lineWidth means, not a constant the emitter always writes."""
    rdl = convert(_src(cell_line_width="2"))["rdl_xml"]
    for _c, _cap, _x, _w in COLS:
        _style, _color, width = _border(_item(rdl, "Textbox", f"Hdr_{_c}"))
        assert width == "2pt", (
            "a DECLARED lineWidth maps 1:1 to points", _c, width)


def test_an_undeclared_edge_still_paints_nothing():
    """The hairline is for a DECLARED edge only -- no linePattern, no
    border (the dialect's draw gate is unchanged)."""
    src = _src().replace(
        b'<visualSettings linePattern="solid" lineForegroundColor="white"/>',
        b'<visualSettings lineForegroundColor="white"/>')
    rdl = convert(src)["rdl_xml"]
    for _c, _cap, _x, _w in COLS:
        style, _color, _width = _border(_item(rdl, "Textbox", f"Hdr_{_c}"))
        assert style == "None", (
            "no linePattern => no border at all", _c, style)


# --------------------------------------------------------------------------
# (b) the DECLARED slack between the caption band and the record band
# --------------------------------------------------------------------------

def test_header_row_carries_the_declared_slack_below_the_band():
    rdl = convert(_src())["rdl_xml"]
    assert SLACK > 0, "fixture must declare slack under the caption frame"
    rows = _rows(rdl)
    assert abs(rows[0] - (HDR_H + SLACK)) < 0.0015, (
        "the header row is the declared band height PLUS the declared slack "
        "below it -- Tablix rows abut, so the slack has nowhere else to live",
        rows, HDR_H + SLACK)
    # ...and the slack is UNPAINTED: the band keeps its own declared height.
    band_h = float(_own(_item(rdl, "Rectangle", "HdrBand_0"),
                        "Height").replace("in", ""))
    assert abs(band_h - HDR_H) < 0.0015, (
        "the painted caption band must not swallow the slack", band_h)


def test_band_top_keeps_its_declared_distance_from_the_caption_top():
    """The header row starts at the caption frame's declared y, so the top
    of the first detail row must sit the DECLARED top-to-top distance below
    it -- whatever the page chrome above the table happens to measure."""
    rdl = convert(_src())["rdl_xml"]
    assert abs(_rows(rdl)[0] - (BAND_Y - HDR_Y)) < 0.0015, (
        "caption top -> record top must be the declared distance",
        _rows(rdl)[0], BAND_Y - HDR_Y)


def test_no_declared_slack_means_none_is_invented():
    src = _src().replace(f'y="{BAND_Y:.5f}"'.encode(), b'y="0.19788"')
    rdl = convert(src)["rdl_xml"]
    assert abs(_rows(rdl)[0] - HDR_H) < 0.0015, _rows(rdl)


# --------------------------------------------------------------------------
# (c) the band fill follows its row; declared height is the MINIMUM
# --------------------------------------------------------------------------

def test_band_fill_contains_its_cells_so_it_grows_with_the_row():
    rdl = convert(_src())["rdl_xml"]
    band = _item(rdl, "Rectangle", "Band_0")
    held = {c.get("Name") for c in band.iter()
            if c.tag.rsplit("}", 1)[-1] == "Textbox"}
    for c, _cap, _x, _w in COLS:
        assert f"Cell_{c}" in held, (
            f"Cell_{c} must be a CHILD of the band rectangle: an RDL "
            "rectangle grows around its contents, so containment is what "
            "makes the fill cover a record whose data wraps -- a sibling "
            "fill keeps its authored height and leaves the bottom of every "
            "grown row unpainted", sorted(held))


def test_band_fill_keeps_the_declared_height_as_its_minimum():
    rdl = convert(_src())["rdl_xml"]
    h = float(_own(_item(rdl, "Rectangle", "Band_0"),
                   "Height").replace("in", ""))
    assert abs(h - BAND_H) < 0.0015, (
        "the DECLARED record height is the band's authored height (its "
        "minimum), so an unwrapped row paints exactly the declaration", h)


def test_declared_gutter_rides_with_the_band_and_stays_unpainted():
    rdl = convert(_src())["rdl_xml"]
    gut = _item(rdl, "Rectangle", "Band_0_Gutter")
    assert abs(float(_own(gut, "Height").replace("in", "")) - GUTTER) < 0.0015
    assert abs(float(_own(gut, "Top").replace("in", "")) - BAND_H) < 0.0015, (
        "the gutter sits BELOW the fill, so the engine pushes it down when "
        "the fill grows and the declared white gap survives")
    assert not any(c.tag.rsplit("}", 1)[-1] == "BackgroundColor"
                   for c in gut.iter()), "the gutter must stay UNPAINTED"


def test_no_declared_fill_means_no_band_and_no_gutter_spacer():
    rdl = convert(_src(band_fill=False))["rdl_xml"]
    assert '<Rectangle Name="Band_0">' not in rdl
    assert 'Name="Band_0_Gutter"' not in rdl
