"""A nested frame's children keep the font their source DECLARES.

Oracle draws a multi-line list record as a band of columns plus one or more
FOLLOW-ON LINES held in a nested plain ``<frame>`` inside the record's
repeating frame. Every object in that record -- band line and nested line
alike -- carries its own ``<font face=... size=.../>``.

The band's declared styling used to be collected inside a FIXED +/-0.45in
window around the band's y. A nested follow-on line sits a whole line-height
below the band, so on a real report (band at y=0.448, nested row frame at
y=0.948 -- 0.50in away) every item in the nested frame fell outside that
window, lost its declared size, and was emitted at the synthesized default
while its band siblings emitted the declared size: one record printed in two
different sizes.

The window is now the RECORD's own DECLARED BOX (the repeating frame's
declared y..y+height), so the declaration wins wherever inside the record the
object lives. The fixed constant survives only as a floor, so no report's
window shrinks.

Nothing here keys on a report, field or label name -- the fixture is synthetic
and every assertion is driven by what the fixture DECLARES.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from converter import convert
from converter.generators import rdl as R
from converter.parsers.oracle_xml import parse_oracle_xml

NS = ("{http://schemas.microsoft.com/sqlserver/reporting/"
      "2008/01/reportdefinition}")

# The record's band line (y=0.45) and its NESTED follow-on line (y=0.95).
# 0.50in apart -- deliberately wider than the old fixed 0.45in window, which
# is the whole point: the nested line must be styled from the DECLARATION,
# not from its distance to the band.
BAND_SOURCES = ("COL_A", "COL_B", "COL_C")
NESTED_SOURCES = ("COL_D", "COL_E")
NESTED_LITERAL = "Nested Literal Line"


def _source_xml(band_size: int = 10, nested_size: int = 10) -> str:
    """A flat multi-line list: a repeating record whose second physical line
    lives in a nested plain frame."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<report name="NESTEDFONT" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q_REC">
      <select><![CDATA[SELECT COL_A, COL_B, COL_C, COL_D, COL_E
       FROM T_REC]]></select>
      <group name="G_REC">
        <dataItem name="COL_A" datatype="vchar2" width="30"
         defaultLabel="Col A">
          <dataDescriptor expression="COL_A" order="1" width="30"/>
        </dataItem>
        <dataItem name="COL_B" datatype="vchar2" width="30"
         defaultLabel="Col B">
          <dataDescriptor expression="COL_B" order="2" width="30"/>
        </dataItem>
        <dataItem name="COL_C" datatype="vchar2" width="30"
         defaultLabel="Col C">
          <dataDescriptor expression="COL_C" order="3" width="30"/>
        </dataItem>
        <dataItem name="COL_D" datatype="vchar2" width="30"
         defaultLabel="Col D">
          <dataDescriptor expression="COL_D" order="4" width="30"/>
        </dataItem>
        <dataItem name="COL_E" datatype="vchar2" width="30"
         defaultLabel="Col E">
          <dataDescriptor expression="COL_E" order="5" width="30"/>
        </dataItem>
      </group>
    </dataSource>
  </data>
  <layout>
  <section name="main">
    <body height="8.5" width="11.0">
      <repeatingFrame name="R_REC" source="G_REC" printDirection="down">
        <geometryInfo x="0.02" y="0.45" width="10.37" height="0.68"/>
        <generalLayout verticalElasticity="variable"/>
        <field name="F_A" source="COL_A" alignment="start">
          <font face="Arial" size="{band_size}"/>
          <geometryInfo x="0.02" y="0.45" width="1.64" height="0.19"/>
        </field>
        <field name="F_B" source="COL_B" alignment="start">
          <font face="Arial" size="{band_size}"/>
          <geometryInfo x="1.80" y="0.45" width="3.60" height="0.19"/>
        </field>
        <field name="F_C" source="COL_C" alignment="start">
          <font face="Arial" size="{band_size}"/>
          <geometryInfo x="5.55" y="0.45" width="4.04" height="0.19"/>
        </field>
        <frame name="M_LINE2">
          <geometryInfo x="0.02" y="0.94" width="10.37" height="0.18"/>
          <text name="B_LIT" minWidowLines="1">
            <textSettings spacing="single"/>
            <geometryInfo x="0.02" y="0.95" width="1.64" height="0.19"/>
            <textSegment><font face="Arial" size="{nested_size}"/>
            <string><![CDATA[{NESTED_LITERAL}]]></string></textSegment>
          </text>
          <field name="F_D" source="COL_D" alignment="start">
            <font face="Arial" size="{nested_size}"/>
            <geometryInfo x="1.80" y="0.95" width="3.60" height="0.19"/>
          </field>
          <field name="F_E" source="COL_E" alignment="start">
            <font face="Arial" size="{nested_size}"/>
            <geometryInfo x="5.55" y="0.95" width="4.04" height="0.19"/>
          </field>
        </frame>
      </repeatingFrame>
      <frame name="M_HDR">
        <geometryInfo x="0.02" y="0.00" width="10.37" height="0.38"/>
        <text name="B_H_A" minWidowLines="1">
          <textSettings spacing="single"/>
          <geometryInfo x="0.02" y="0.00" width="1.64" height="0.19"/>
          <textSegment><font face="Arial" size="10" bold="yes"/>
          <string><![CDATA[Head A]]></string></textSegment>
        </text>
        <text name="B_H_B" minWidowLines="1">
          <textSettings spacing="single"/>
          <geometryInfo x="1.80" y="0.00" width="3.60" height="0.19"/>
          <textSegment><font face="Arial" size="10" bold="yes"/>
          <string><![CDATA[Head B]]></string></textSegment>
        </text>
        <text name="B_H_C" minWidowLines="1">
          <textSettings spacing="single"/>
          <geometryInfo x="5.55" y="0.00" width="4.04" height="0.19"/>
          <textSegment><font face="Arial" size="10" bold="yes"/>
          <string><![CDATA[Head C]]></string></textSegment>
        </text>
      </frame>
    </body>
  </section>
  </layout>
</report>
"""


def _convert(xml: str) -> str:
    res = convert(xml.encode("utf-8"), "nestedfont.xml")
    assert not res.get("conversion_error"), res.get("conversion_error")
    return res["rdl_xml"]


def _font_sizes(rdl_xml: str) -> dict:
    """needle -> set of FontSize values over every textbox carrying it."""
    root = ET.fromstring(rdl_xml)
    out: dict = {}
    for tb in root.iter(f"{NS}Textbox"):
        size = None
        val = ""
        for para in tb.iter(f"{NS}Paragraph"):
            for run in para.iter(f"{NS}TextRun"):
                st = run.find(f"{NS}Style")
                el = st.find(f"{NS}FontSize") if st is not None else None
                if el is not None and size is None:
                    size = el.text
                v = run.find(f"{NS}Value")
                if v is not None and not val:
                    val = v.text or ""
        if not val:
            continue
        for needle in BAND_SOURCES + NESTED_SOURCES:
            if f"Fields!{needle}.Value" in val:
                out.setdefault(needle, set()).add(size)
        if NESTED_LITERAL in val:
            out.setdefault(NESTED_LITERAL, set()).add(size)
    return out


# --------------------------------------------------------------------------
# the window itself


def test_band_style_window_is_the_declared_record_box():
    """_detail_band_style must reach the nested frame's items -- they are
    0.50in from the band, beyond any fixed sub-record window."""
    rep = parse_oracle_xml(_source_xml().encode("utf-8"))
    _row, _wrap, row_y = R._detail_band_fields(rep)
    assert row_y is not None
    style = R._detail_band_style(rep, row_y)
    for src in BAND_SOURCES + NESTED_SOURCES:
        assert src in style["fonts"], (src, sorted(style["fonts"]))
        assert style["fonts"][src][1] == 10, (src, style["fonts"][src])
    assert style["caption_fonts"].get(NESTED_LITERAL, (None, None))[1] == 10


def test_the_nested_line_really_is_outside_the_old_fixed_window():
    """Guard the guard: if the fixture's nested line drifted back inside the
    0.45in window the assertions above would pass without the fix."""
    rep = parse_oracle_xml(_source_xml().encode("utf-8"))
    _row, _wrap, row_y = R._detail_band_fields(rep)
    nested_y = 0.95
    assert abs(nested_y - row_y) > 0.45, (nested_y, row_y)


# --------------------------------------------------------------------------
# end to end


def test_every_record_item_emits_its_declared_size():
    sizes = _font_sizes(_convert(_source_xml()))
    for needle in BAND_SOURCES + NESTED_SOURCES + (NESTED_LITERAL,):
        assert sizes.get(needle) == {"10pt"}, (needle, sizes)


def test_the_record_prints_in_ONE_size():
    """The defect's visible signature: a record split across two font sizes."""
    sizes = _font_sizes(_convert(_source_xml()))
    seen = {s for v in sizes.values() for s in v}
    assert seen == {"10pt"}, sizes


@pytest.mark.parametrize("nested_size", [9, 11, 12])
def test_mutation_anchor_the_nested_size_follows_its_declaration(nested_size):
    """The nested line's size is READ from the source, never assumed: change
    only the nested frame's declared size and the emitted size follows it
    while the band line keeps its own."""
    sizes = _font_sizes(_convert(_source_xml(nested_size=nested_size)))
    want = f"{nested_size}pt"
    for needle in NESTED_SOURCES + (NESTED_LITERAL,):
        assert sizes.get(needle) == {want}, (needle, want, sizes)
    for needle in BAND_SOURCES:
        assert sizes.get(needle) == {"10pt"}, (needle, sizes)
