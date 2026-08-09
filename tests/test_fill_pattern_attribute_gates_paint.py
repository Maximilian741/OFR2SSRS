"""The fillPattern ATTRIBUTE gates the paint -- its VALUE does not.

MEASURED DIALECT
----------------
Oracle's export writes ``fillBackgroundColor`` on almost every object as a
design-time leftover. It PAINTS only when the same ``<visualSettings>``
also carries a ``fillPattern`` attribute; the attribute marks the fill as
author-touched. The attribute's VALUE selects WHICH colour channel paints
(``solid`` -> ``fillForegroundColor``, anything else -> the background),
never WHETHER it paints.

TRUTH MEASUREMENT (2026-08-08, per-declaration, every truth-paired report
in the corpus, ALL pages, filled BOXES only -- ``<image>``/``<line>`` are
not filled boxes, and objects inside format-trigger-gated variant frames
that this data run does not print are excluded):

    fillPattern="solid"        34 declared ->  34 painted   100%
    fillPattern="transparent"  73 declared ->  73 painted   100%
    no fillPattern attribute  165 declared ->   0 painted     0%

The 0% row is the decisive one and it is exact, not statistical: one
report declares the SAME gray on 44 objects -- 7 with a ``transparent``
fillPattern attribute, 37 with none -- and its Oracle-rendered truth PDF
contains exactly SEVEN non-white filled rectangles, whose widths and
heights account for the 7 attribute-carrying declarations and nothing
else. (Eleven of the 37 bare declarations happen to share a box size with
a painted one; that is dimension aliasing, not paint -- there is only one
rect of that size in the file and the attribute-carrying declaration
already accounts for it.)

A census of every ``fillPattern`` value in 158 real Oracle exports found
exactly two: ``transparent`` (626) and ``solid`` (48). There is no
"no-fill" value in this dialect for the absence to be spelled with -- the
absence IS the absent attribute.

WHAT THIS GUARDS
----------------
The band emitter carried a SECOND, independent gate that re-tested the
pattern VALUE and excluded ``transparent``, so a frame declaring
``fillPattern="transparent"`` + a real colour never got its one-rectangle
band even though the parser had correctly resolved the fill and the truth
paints it -- e.g. a header frame declaring transparent + gray16 over its
declared 7.57in prints one continuous #D6D6D6 band 545.4pt wide in the
Oracle render. Both gates must now agree with the measurement.

Synthetic fixture only -- no customer report, column or label name.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert                                   # noqa: E402
from converter.generators import rdl as R                       # noqa: E402
from converter.parsers.oracle_xml import parse_oracle_xml       # noqa: E402

# --- the declaration under test (inches) ---------------------------------
COLS = (("COL_A", 0.00000, 2.00000),
        ("COL_B", 2.50000, 2.00000),
        ("COL_C", 5.00000, 1.50000),
        ("COL_D", 7.00000, 1.50000))   # last child ends at 8.50000
BAND_X, BAND_W = 5.00000, 3.75000      # the fill frame -> ends at 8.75000
ROW_H = 0.18000

_SOLID = ('<visualSettings fillPattern="solid" '
          'fillForegroundColor="r88g88b75"/>')
# the three declaration shapes, and the colour each one is supposed to paint
_SHAPES = {
    "solid": (_SOLID, "#E0E0BF"),
    "transparent": ('<visualSettings fillPattern="transparent" '
                    'fillBackgroundColor="gray16"/>', "#D6D6D6"),
    "no-attribute": ('<visualSettings fillBackgroundColor="gray16"/>', None),
}


def _source(shape):
    """Four columns; the last two sit inside a fill frame declared WIDER
    than the fields it holds, so a band that tiles per cell is visibly
    different from one painted at the frame's declared extents."""
    vs = _SHAPES[shape][0]

    def _field(c, x, w):
        return (f'<field name="F_{c}" source="{c}" alignment="start">'
                f'<font face="Arial" size="8"/>'
                f'<geometryInfo x="{x:.5f}" y="0.30000" width="{w:.5f}"'
                f' height="{ROW_H:.5f}"/></field>')

    outer = "".join(_field(c, x, w) for c, x, w in COLS[:2])
    inner = "".join(_field(c, x, w) for c, x, w in COLS[2:])
    items = "".join(f'<dataItem name="{c}" datatype="vchar2"'
                    f' columnOrder="{i + 1}"/>'
                    for i, (c, _x, _w) in enumerate(COLS))
    return (
        '<?xml version="1.0"?>'
        '<report name="FILLGATE" DTDVersion="9.0.2.0.10"><data>'
        '<dataSource name="Q_1">'
        '<select><![CDATA[SELECT COL_A, COL_B, COL_C, COL_D FROM T]]></select>'
        f'<group name="G_ROW">{items}</group>'
        '</dataSource></data><layout>'
        '<section name="main" width="14.00000" height="8.50000"'
        ' orientation="landscape">'
        '<body width="12.50000" height="7.00000">'
        '<location x="0.50000" y="0.50000"/>'
        '<frame name="M_GRP">'
        '<geometryInfo x="0.00000" y="0.02000" width="9.00000"'
        ' height="0.55000"/>'
        '<repeatingFrame name="R_ROW" source="G_ROW" printDirection="down"'
        ' minWidowRecords="1" columnMode="no" vertSpaceBetweenFrames="0.0500">'
        f'<geometryInfo x="0.00000" y="0.30000" width="9.00000"'
        f' height="{ROW_H:.5f}"/>'
        f'{outer}'
        '<repeatingFrame name="R_INNER" source="G_ROW">'
        f'<geometryInfo x="{BAND_X:.5f}" y="0.30000" width="{BAND_W:.5f}"'
        f' height="{ROW_H:.5f}"/>{vs}{inner}</repeatingFrame>'
        '</repeatingFrame>'
        '</frame></body>'
        '</section></layout></report>'
    ).encode()


def _band(rdl):
    """The band rectangle as an ELEMENT (None when the source declares no
    band).

    Element, not a text blob: the band CONTAINS the cells it paints -- the
    containment that lets the fill grow with a row whose data wraps -- so a
    blob would hand back a nested child's style/geometry instead of the
    band's own.
    """
    import xml.etree.ElementTree as ET
    for el in ET.fromstring(rdl).iter():
        if el.tag.rsplit("}", 1)[-1] == "Rectangle" \
                and el.get("Name") == "Band_0":
            return el
    return None


def _bg(band):
    """The band's OWN BackgroundColor (never a child's)."""
    if band is None:
        return ""
    for st in band:
        if st.tag.rsplit("}", 1)[-1] != "Style":
            continue
        for c in st:
            if c.tag.rsplit("}", 1)[-1] == "BackgroundColor":
                return (c.text or "").strip().upper()
    return ""


def _num(band, tag):
    """One geometry property off the band ITSELF."""
    if band is None:
        return None
    for c in band:
        if c.tag.rsplit("}", 1)[-1] == tag:
            return float((c.text or "").replace("in", ""))
    return None


# --------------------------------------------------------------------------
# the parser's gate
# --------------------------------------------------------------------------

@pytest.mark.parametrize("shape", ["solid", "transparent"])
def test_a_pattern_attribute_resolves_a_painting_fill(shape):
    _vs, want = _SHAPES[shape]
    rep = parse_oracle_xml(_source(shape))

    def find(g):
        if (getattr(g, "name", "") or "") == "R_INNER":
            return g
        for c in (getattr(g, "children", None) or []):
            r = find(c)
            if r is not None:
                return r
        return None

    frame = next(filter(None, (find(g) for g in rep.layout)), None)
    assert frame is not None, "fixture frame must parse"
    assert (frame.background_color or "").upper() == want, (
        f'fillPattern="{shape}" must resolve a painting fill')


def test_no_pattern_attribute_resolves_no_fill():
    rep = parse_oracle_xml(_source("no-attribute"))

    def find(g):
        if (getattr(g, "name", "") or "") == "R_INNER":
            return g
        for c in (getattr(g, "children", None) or []):
            r = find(c)
            if r is not None:
                return r
        return None

    frame = next(filter(None, (find(g) for g in rep.layout)), None)
    assert not (frame.background_color or ""), (
        "a fillBackgroundColor with NO fillPattern attribute is a design-time "
        "leftover: 0 of 165 such declarations paint in the truth corpus")


# --------------------------------------------------------------------------
# the BAND emitter's gate -- the one that disagreed
# --------------------------------------------------------------------------

@pytest.mark.parametrize("shape", ["solid", "transparent"])
def test_pattern_marked_frame_is_collected_as_a_band(shape):
    """``_declared_fill_frames`` must see BOTH pattern values."""
    rep = parse_oracle_xml(_source(shape))
    frames = R._declared_fill_frames(rep)
    want = _SHAPES[shape][1]
    assert any((f.get("bg") or "").upper() == want for f in frames), (
        f'a frame declaring fillPattern="{shape}" + a real colour is a '
        f"DECLARED band; collected instead: {[f.get('bg') for f in frames]}")


def test_unmarked_frame_is_not_collected_as_a_band():
    rep = parse_oracle_xml(_source("no-attribute"))
    assert not [f for f in R._declared_fill_frames(rep)
                if (f.get("bg") or "").upper() == "#D6D6D6"], (
        "no attribute -> no band")


@pytest.mark.parametrize("shape", ["solid", "transparent"])
def test_pattern_marked_frame_paints_one_band_at_its_declared_width(shape):
    """End-to-end: the RDL carries ONE band rectangle, in the declared
    colour, spanning the frame's DECLARED width -- identically for both
    pattern values."""
    rdl = convert(_source(shape))["rdl_xml"]
    band = _band(rdl)
    assert band is not None, (
        f'fillPattern="{shape}" + a real colour must emit the declared band')
    assert _bg(band) == _SHAPES[shape][1], (
        f"the band paints the DECLARED colour, got {_bg(band)!r}")
    assert abs(_num(band, "Width") - BAND_W) <= 0.01, (
        f"the band spans the frame's declared {BAND_W}in, "
        f"got {_num(band, 'Width')}in")


def test_the_two_pattern_values_place_the_band_identically():
    """The attribute's VALUE picks the colour channel and nothing else:
    geometry must be byte-identical between the two."""
    b_solid = _band(convert(_source("solid"))["rdl_xml"])
    b_trans = _band(convert(_source("transparent"))["rdl_xml"])
    assert b_solid is not None and b_trans is not None
    geo = lambda b: (_num(b, "Left"), _num(b, "Top"),  # noqa: E731
                     _num(b, "Width"), _num(b, "Height"))
    assert geo(b_solid) == geo(b_trans), (
        f"declared geometry must not depend on the pattern VALUE: "
        f"{geo(b_solid)} vs {geo(b_trans)}")


def test_unmarked_frame_paints_no_band_anywhere():
    """PROVE THE GATE: the same colour with the attribute removed must
    not reach the RDL at all -- not as a band, not tiled onto the cells."""
    rdl = convert(_source("no-attribute"))["rdl_xml"]
    assert _band(rdl) is None, "no attribute -> no band rectangle"
    assert "#D6D6D6" not in rdl, (
        "an unmarked fillBackgroundColor must not paint by any route "
        "(0 of 165 such declarations paint in the truth corpus)")
