"""SOLID-FILL FOREGROUND dialect + declaration-driven flat-table styling.

Truth-measured export dialect (validated against the Oracle-rendered PDFs):

* ``fillPattern="solid"`` paints the FOREGROUND color as the box fill —
  corpus-wide, every solid box declares only ``fillForegroundColor`` (row
  band frames, navy column-header frames, gray totals rows).
* A fillPattern attribute with any OTHER value (typically "transparent")
  keeps the established background rule: ``fillBackgroundColor`` paints.
* No fillPattern attribute -> the fill NEVER prints (template leftover).

Downstream (flat tabular builder): when the source declares caption text
objects, every header/detail style channel is declaration-driven — the
caption frame's own painted fill is the header band, caption ink/justify
are honored, a partial-width painted repeating frame bands exactly the
columns it contains, and ONLY declared borders paint (no invented grid).

Synthetic fixtures only — no client data.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402
from converter.parsers.oracle_xml import parse_oracle_xml  # noqa: E402


def _wrap(body_objects: str) -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<report name="WIDGET_LIST" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q_MAIN">
      <select canParse="no"><![CDATA[SELECT COL_A, COL_B, COL_C, COL_D FROM WIDGETS]]></select>
      <group name="G_MAIN">
        <dataItem name="COL_A" datatype="vchar2" columnOrder="1" defaultLabel="Alpha"/>
        <dataItem name="COL_B" datatype="vchar2" columnOrder="2" defaultLabel="Beta"/>
      </group>
      <group name="G_INNER">
        <dataItem name="COL_C" datatype="vchar2" columnOrder="3" defaultLabel="Gamma"/>
        <dataItem name="COL_D" datatype="vchar2" columnOrder="4" defaultLabel="Delta"/>
      </group>
    </dataSource>
  </data>
  <layout>
  <section name="main" width="11.00000" height="8.50000" orientation="landscape">
    <body width="10.42627" height="7.14587">
      <location x="0.29248" y="0.76038"/>
      {body_objects}
    </body>
  </section>
  </layout>
</report>""".encode()


# Header frame: solid fill (foreground channel) + white caption inks with
# declared solid hairlines. Data row: an INNER solid-fill repeating frame
# containing only the last two columns (the partial-row band idiom).
BANDED_TABLE_OBJECTS = """
      <repeatingFrame name="R_G_MAIN" source="G_MAIN" printDirection="down">
        <geometryInfo x="0.02075" y="0.44812" width="10.37512" height="0.20000"/>
        <visualSettings fillForegroundColor="r88g88b75"/>
        <field name="F_COL_A" source="COL_A"><font face="Arial" size="8"/>
          <geometryInfo x="0.02075" y="0.44812" width="2.50000" height="0.18750"/></field>
        <field name="F_COL_B" source="COL_B"><font face="Arial" size="8"/>
          <geometryInfo x="2.60000" y="0.44812" width="3.00000" height="0.18750"/></field>
        <repeatingFrame name="R_G_INNER" source="G_INNER">
          <geometryInfo x="5.80000" y="0.44812" width="4.50000" height="0.18750"/>
          <visualSettings fillPattern="solid" fillForegroundColor="r88g88b75"/>
          <field name="F_COL_C" source="COL_C"><font face="Arial" size="8"/>
            <geometryInfo x="5.80000" y="0.44812" width="2.00000" height="0.18750"/></field>
          <field name="F_COL_D" source="COL_D"><font face="Arial" size="8"/>
            <geometryInfo x="7.90000" y="0.44812" width="2.00000" height="0.18750"/></field>
        </repeatingFrame>
      </repeatingFrame>
      <frame name="M_G_MAIN_HDR">
        <geometryInfo x="0.02075" y="0.00000" width="10.37512" height="0.19263"/>
        <visualSettings fillPattern="solid" fillForegroundColor="darkblue" linePattern="solid"/>
        <text name="B_COL_A"><geometryInfo x="0.02075" y="0.0" width="2.5" height="0.18"/>
          <visualSettings linePattern="solid" lineForegroundColor="white"/>
          <textSegment><font face="Arial" size="9" textColor="white"/><string><![CDATA[Alpha]]></string></textSegment></text>
        <text name="B_COL_B"><geometryInfo x="2.60000" y="0.0" width="3.0" height="0.18"/>
          <visualSettings linePattern="solid" lineForegroundColor="white"/>
          <textSegment><font face="Arial" size="9" textColor="white"/><string><![CDATA[Beta]]></string></textSegment></text>
        <text name="B_COL_C"><textSettings justify="center"/><geometryInfo x="5.80000" y="0.0" width="2.0" height="0.18"/>
          <visualSettings linePattern="solid" lineForegroundColor="white"/>
          <textSegment><font face="Arial" size="9" textColor="white"/><string><![CDATA[Gamma]]></string></textSegment></text>
        <text name="B_COL_D"><geometryInfo x="7.90000" y="0.0" width="2.0" height="0.18"/>
          <visualSettings linePattern="solid" lineForegroundColor="white"/>
          <textSegment><font face="Arial" size="9" textColor="white"/><string><![CDATA[Delta]]></string></textSegment></text>
      </frame>
"""


# ---------------------------------------------------------------------------
# Parser dialect
# ---------------------------------------------------------------------------

def _group_by_name(rep, name):
    stack = list(rep.layout or [])
    while stack:
        g = stack.pop()
        if (getattr(g, "name", "") or "") == name:
            return g
        stack.extend(getattr(g, "children", None) or [])
    return None


def test_solid_fill_pattern_paints_foreground_color():
    rep = parse_oracle_xml(_wrap(BANDED_TABLE_OBJECTS))
    hdr = _group_by_name(rep, "M_G_MAIN_HDR")
    assert hdr is not None
    assert (hdr.background_color or "").upper() == "#000080", (
        "fillPattern='solid' must paint the FOREGROUND color as the fill "
        "(truth-measured: every solid box declares only fillForegroundColor)")
    inner = _group_by_name(rep, "R_G_INNER")
    assert inner is not None
    assert (inner.background_color or "").upper() == "#E0E0BF", (
        "solid inner repeating frame must paint its foreground fill")


def test_foreground_without_pattern_attr_does_not_paint():
    # Same fg color but NO fillPattern attribute -> unpainted (the outer
    # repeating frame in the fixture declares exactly this shape).
    rep = parse_oracle_xml(_wrap(BANDED_TABLE_OBJECTS))
    outer = _group_by_name(rep, "R_G_MAIN")
    assert outer is not None
    assert not (outer.background_color or ""), (
        "fillForegroundColor with NO fillPattern attribute must never paint")


def test_non_solid_pattern_keeps_background_rule():
    # transparent pattern + declared bg -> bg paints (the truth-counted
    # gray-band reports all declare this shape); the fg must NOT hijack it.
    objs = BANDED_TABLE_OBJECTS.replace(
        '<visualSettings fillPattern="solid" fillForegroundColor="darkblue" '
        'linePattern="solid"/>',
        '<visualSettings fillPattern="transparent" fillForegroundColor="red" '
        'fillBackgroundColor="gray16" linePattern="solid"/>')
    rep = parse_oracle_xml(_wrap(objs))
    hdr = _group_by_name(rep, "M_G_MAIN_HDR")
    assert (hdr.background_color or "").upper() == "#D6D6D6", (
        "a non-solid fillPattern must keep the fillBackgroundColor rule")


def test_solid_fill_on_a_field_paints_foreground_too():
    # The dialect applies at FIELD level as well (gray totals-value boxes
    # declare solid + foreground directly on the field).
    objs = BANDED_TABLE_OBJECTS.replace(
        '<field name="F_COL_A" source="COL_A"><font face="Arial" size="8"/>',
        '<field name="F_COL_A" source="COL_A">'
        '<visualSettings fillPattern="solid" fillForegroundColor="gray16"/>'
        '<font face="Arial" size="8"/>')
    rep = parse_oracle_xml(_wrap(objs))
    f = None
    stack = list(rep.layout or [])
    while stack:
        g = stack.pop()
        for lf in (getattr(g, "fields", None) or []):
            if (getattr(lf, "name", "") or "") == "F_COL_A":
                f = lf
        stack.extend(getattr(g, "children", None) or [])
    assert f is not None
    assert (f.background_color or "").upper() == "#D6D6D6", (
        "fillPattern='solid' on a field must paint its foreground fill")


def test_field_font_textcolor_is_captured_as_ink():
    objs = BANDED_TABLE_OBJECTS.replace(
        '<field name="F_COL_A" source="COL_A"><font face="Arial" size="8"/>',
        '<field name="F_COL_A" source="COL_A">'
        '<font face="Arial" size="8" textColor="darkblue"/>')
    rep = parse_oracle_xml(_wrap(objs))
    f = None
    stack = list(rep.layout or [])
    while stack:
        g = stack.pop()
        for lf in (getattr(g, "fields", None) or []):
            if (getattr(lf, "name", "") or "") == "F_COL_A":
                f = lf
        stack.extend(getattr(g, "children", None) or [])
    assert f is not None
    assert (f.color or "").upper() == "#000080", (
        "a field's <font textColor=...> ink must be captured (a declared "
        "darkblue title must not fall back to black)")


# ---------------------------------------------------------------------------
# Flat-table builder: declaration-driven styling
# ---------------------------------------------------------------------------

def _textbox_xml(rdl: str, name: str) -> str:
    m = re.search(
        rf'<Textbox Name="{name}".*?</Textbox>', rdl, re.S)
    assert m, f"missing textbox {name}"
    return m.group(0)


def _rect_xml(rdl: str, name: str) -> str:
    m = re.search(rf'<Rectangle Name="{name}">.*?</Rectangle>', rdl, re.S)
    assert m, f"missing rectangle {name}"
    return m.group(0)


def _rect_geo(rdl: str, name: str) -> tuple:
    """``(Left, Width)`` off the named rectangle ITSELF.

    A band rectangle CONTAINS the cells it paints (that containment is what
    lets the fill grow with a row whose data wraps), so the first <Left> in
    the rectangle's text blob belongs to a nested child, not to the band.
    """
    import xml.etree.ElementTree as ET
    for el in ET.fromstring(rdl).iter():
        if el.tag.rsplit("}", 1)[-1] == "Rectangle" and el.get("Name") == name:
            geo = {}
            for c in el:
                tag = c.tag.rsplit("}", 1)[-1]
                if tag in ("Left", "Width"):
                    geo[tag] = float((c.text or "").replace("in", ""))
            assert "Left" in geo and "Width" in geo, (name, geo)
            return geo["Left"], geo["Width"]
    raise AssertionError(f"missing rectangle {name}")


def test_declared_header_band_ink_and_alignment():
    rdl = convert(_wrap(BANDED_TABLE_OBJECTS))["rdl_xml"]
    hdr = _textbox_xml(rdl, "Hdr_COL_A")
    # The caption FRAME declares the fill, so the band is ONE rectangle at
    # the frame's declared x, behind every caption -- not a per-caption
    # tile. (Truth-measured: the per-cell emission tiled a declared band
    # into ten cell fills that stopped 18.5pt short of the frame's edge.)
    assert rdl.count('<Rectangle Name="HdrBand_0">') == 1, (
        "the declared header band must be ONE rectangle, not per-cell tiles")
    _band = _rect_xml(rdl, "HdrBand_0")
    assert "<BackgroundColor>#000080</BackgroundColor>" in _band, (
        "the caption frame's own solid fill must be the header band color")
    _bl, _bw = _rect_geo(rdl, "HdrBand_0")
    assert abs(_bl - 0.02075) < 0.003, ("band starts at the frame's own "
                                        "declared x", _bl)
    assert _bl + _bw > 9.90 + 0.005, (
        "the band must reach past the LAST caption's declared right edge "
        "(9.90in) toward the frame's own (10.39587in)", _bl, _bw)
    assert "<ColSpan>4</ColSpan>" in rdl, (
        "the banded captions collapse into one spanned cell")
    for _c in ("COL_A", "COL_B", "COL_C", "COL_D"):
        assert "#000080" not in _textbox_xml(rdl, f"Hdr_{_c}"), (
            f"Hdr_{_c}: the FRAME paints the band, never the caption cell")
    assert re.search(r"<Color>#FFFFFF</Color>", hdr), (
        "declared caption ink (white) must be the header text color")
    assert "<TextAlign>Left</TextAlign>" in hdr, (
        "an unjustified caption starts at the cell start (Left), not Center")
    # declared center justify is honored per caption
    hdr_c = _textbox_xml(rdl, "Hdr_COL_C")
    assert "<TextAlign>Center</TextAlign>" in hdr_c
    # declared white hairline (linePattern solid + white stroke), not the
    # invented #a0a0a0 grid
    assert "#a0a0a0" not in hdr.lower()
    assert re.search(r"<Border>\s*<Style>Solid</Style>\s*<Color>#FFFFFF",
                     hdr), "declared caption hairline must paint as declared"


def test_header_text_never_inherits_the_fill_channel_as_ink():
    # The row-band frame declares a khaki FILL foreground; the caption ink
    # is white. The fill channel must never become header text color.
    rdl = convert(_wrap(BANDED_TABLE_OBJECTS))["rdl_xml"]
    hdr = _textbox_xml(rdl, "Hdr_COL_B")
    assert "<Color>#E0E0BF</Color>" not in hdr, (
        "fillForegroundColor is a FILL channel — it must never paint "
        "caption text")
    # Harder leg: even when the captions declare NO ink of their own, the
    # main frame's fill-foreground must not become the header text color
    # (this is exactly the misroute the truth PDF exposed).
    objs = BANDED_TABLE_OBJECTS.replace(' textColor="white"', "")
    rdl2 = convert(_wrap(objs))["rdl_xml"]
    hdr2 = _textbox_xml(rdl2, "Hdr_COL_B")
    assert "<Color>#E0E0BF</Color>" not in hdr2, (
        "with no declared caption ink, the row-band FILL color must still "
        "never leak into header text")


def test_partial_row_band_fills_only_covered_columns():
    """A partial-width solid frame bands exactly the columns it contains --
    as ONE rectangle at the frame's declared extents, not per-cell tiles.

    The inner frame declares x=5.8 width=4.5 while its two fields end at
    9.90in: the band must start at 5.8 and run PAST 9.90 toward 10.3. The
    per-cell emission instead painted two swatches that started at the
    column boundary and stopped at the last field's right edge, separated
    by a white gutter (truth-measured: 0.72pt gutters, 18.5pt short)."""
    rdl = convert(_wrap(BANDED_TABLE_OBJECTS))["rdl_xml"]
    assert rdl.count('<Rectangle Name="Band_0">') == 1, (
        "the declared row band must be ONE rectangle")
    band = _rect_xml(rdl, "Band_0")
    assert "<BackgroundColor>#E0E0BF</BackgroundColor>" in band, band
    # Offsets are relative to the spanned cell, and a tablix cell starts at
    # the running SUM of the column widths before it -- so read that sum
    # from the emitted columns instead of assuming the boxes pack edge to
    # edge (they do not: the declared inter-column gutters are part of the
    # column pitch, and assuming otherwise is exactly how a table drifts
    # left of its declaration).
    cols = [float(w) for w in re.findall(
        r"<TablixColumn>\s*<Width>([\d.]+)in", rdl)]
    assert len(cols) == 4, cols
    org = sum(cols[:2])
    # STRICTER than the old hard-coded 5.5: the two uncovered columns must
    # themselves span from the first field's declared x (0.02075) to the
    # inner frame's own (5.8), i.e. carry the declared gutters.
    assert abs(org - (5.80000 - 0.02075)) < 0.003, (
        "the uncovered columns must reach the covered run's declared x", org)
    left, width = _rect_geo(rdl, "Band_0")
    assert abs((org + left) - 5.80000) < 0.003, (
        "the band starts at the inner frame's own declared x", org + left)
    assert org + left + width > 9.90 + 0.005, (
        "the band must reach past the last covered field's declared right "
        "edge (9.90in) toward the frame's own (10.3in)", org + left + width)
    assert "<ColSpan>2</ColSpan>" in rdl, (
        "the two covered columns collapse into one spanned cell")
    # NO column keeps a per-cell tile of the frame's fill -- covered or not.
    for col in ("COL_A", "COL_B", "COL_C", "COL_D"):
        cell = _textbox_xml(rdl, f"Cell_{col}")
        assert "#E0E0BF" not in cell, (
            f"Cell_{col}: the FRAME paints the band, never the cell")
    # ...and the UNCOVERED columns keep their own plain cells.
    assert '<Rectangle Name="Band_COL_A">' not in rdl
    assert '<Rectangle Name="Band_COL_B">' not in rdl


def test_no_invented_grid_when_layout_is_declared():
    rdl = convert(_wrap(BANDED_TABLE_OBJECTS))["rdl_xml"]
    for col in ("COL_A", "COL_B", "COL_C", "COL_D"):
        cell = _textbox_xml(rdl, f"Cell_{col}")
        assert "#d0d0d0" not in cell.lower(), (
            "data cells with no declared linePattern must not carry the "
            "invented gray grid")
        assert re.search(r"<Border>\s*<Style>None</Style>", cell), (
            "undeclared borders must emit Style None")
