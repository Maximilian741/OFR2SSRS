"""Declared alignment on FIXED boxes, everywhere a layout object is drawn.

Companion to test_declared_alignment_elasticity.py, which pins the other half
of the same rule: a box declaring ``end``/``right`` together with a fluid
``horizontalElasticity`` is sized to its content and loses the right edge that
justification needed, so it anchors at its declared left edge -- while a
``center`` box contracts symmetrically about its declared centre line and so
still centres in its declared box. This module pins the FIXED half -- a box
that declares no elasticity keeps its declared width, so the declared
justification applies inside it:

    end -> Right      center -> Center      start -> Left

Both halves route through ONE mapping, ``_declared_text_align(lf, default)``,
so a new emitter cannot re-derive half the rule and silently drop the other.

Measured against Oracle-produced truth pages for the grouped-tabular break
archetype (an outer break frame -> column-header band -> detail rows -> group
totals): the numeric detail columns print with their glyph RIGHT edges lined up
and their left edges ragged (that is right-justification, not a coincidence of
equal-width values), their column captions print flush with the same right
edge, and a caption/value declaring ``start`` prints at its declared x even
when it sits in the right-hand half of the page. That emitter used to hardcode
Left for captions and detail cells and to guess the group-header/footer
justification from x position, so every declared end/center there was lost.

Nothing here keys on a report, field or label name: the fixture is synthetic
and every assertion is driven by what the fixture DECLARES.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from converter import convert
from converter.generators import rdl as R

NS = ("{http://schemas.microsoft.com/sqlserver/reporting/"
      "2008/01/reportdefinition}")


# --------------------------------------------------------------------------
# the single mapping, in isolation


class _Stub:
    def __init__(self, align="", he=""):
        self.align = align
        self.horizontal_elasticity = he


@pytest.mark.parametrize("declared,expected", [
    ("end", "Right"), ("right", "Right"),
    ("center", "Center"), ("centre", "Center"),
    ("start", "Left"), ("left", "Left"),
])
@pytest.mark.parametrize("he", ["", "fixed", "expand"])
def test_fixed_box_honors_its_declared_alignment(declared, expected, he):
    """A fixed (or expand-floored) box justifies inside its declared width."""
    assert R._declared_text_align(_Stub(declared, he), "SENTINEL") == expected


@pytest.mark.parametrize("declared", ["end", "right"])
@pytest.mark.parametrize("he", ["variable", "contract"])
def test_fluid_box_anchors_at_its_declared_edge(declared, he):
    """The other half of the rule, through the same mapping: a content-sized
    box loses the RIGHT edge an end/right justification needs, so it anchors
    at the declared left edge."""
    assert R._declared_text_align(_Stub(declared, he), "SENTINEL") == "Left"


@pytest.mark.parametrize("declared", ["center", "centre", "middle"])
@pytest.mark.parametrize("he", ["variable", "contract"])
def test_fluid_box_still_centres_a_centre_justified_object(declared, he):
    """Centre is NOT one-sided: contracting the box is symmetric about its
    declared centre line, so that centre survives and the object centres in
    its DECLARED box. Truth-PDF measured -- a centre-justified fluid footer
    declared x=3.4917in w=1.5137in (box 251.40..360.39pt, centre 305.90pt)
    prints at 293.52..317.98pt, centre 305.75pt: the declared box centre, not
    the declared left edge."""
    assert R._declared_text_align(_Stub(declared, he), "SENTINEL") == "Center"


def test_undeclared_alignment_falls_through_to_the_caller_default():
    """"Honor the declaration" must not become "never right-align": where the
    layout declares nothing, the caller's own default still stands."""
    assert R._declared_text_align(_Stub("", ""), "SENTINEL") == "SENTINEL"
    assert R._declared_text_align(_Stub("", "variable"), None) is None
    # Oracle's full-justify token has no RDL TextAlign equivalent -> default.
    assert R._declared_text_align(_Stub("flush", ""), "SENTINEL") == "SENTINEL"
    # A missing layout object at all (an emitter with no declaration in hand).
    assert R._declared_text_align(None, "SENTINEL") == "SENTINEL"


# --------------------------------------------------------------------------
# end to end: the grouped-tabular break archetype
#
# Declared, and what each must emit:
#   detail   D_START   alignment="start"                 -> Left
#            D_END     alignment="end"                   -> Right
#            D_MID     alignment="center"                -> Center
#            D_FLUID   alignment="end" + fluid width     -> Left
#   captions <text> justify="end"/"center"/none          -> Right/Center/Left
#   group    G_NOTE    alignment="start", right-hand x   -> Left
#            G_CODE    alignment="end"                   -> Right
#   footer   CS_TALLY  alignment="start", right-hand x   -> Left

_FLUID = 'horizontalElasticity="variable"'


def _source_xml(fluid_attr: str = _FLUID,
                caption_justify: str = "end",
                detail_end_align: str = "end") -> str:
    gl = f"<generalLayout {fluid_attr}/>" if fluid_attr else ""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<report name="BREAKCASE" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q_OUTER">
      <select><![CDATA[SELECT BREAK_KEY, G_CODE, G_NOTE
       FROM T_BREAK]]></select>
      <group name="G_OUTER">
        <dataItem name="BREAK_KEY" datatype="vchar2" width="30"
         defaultLabel="Break Key">
          <dataDescriptor expression="BREAK_KEY" order="1" width="30"/>
        </dataItem>
        <dataItem name="G_CODE" datatype="vchar2" width="20"
         defaultLabel="G Code">
          <dataDescriptor expression="G_CODE" order="2" width="20"/>
        </dataItem>
        <dataItem name="G_NOTE" datatype="vchar2" width="20"
         defaultLabel="G Note">
          <dataDescriptor expression="G_NOTE" order="3" width="20"/>
        </dataItem>
      </group>
    </dataSource>
    <dataSource name="Q_DETAIL">
      <select><![CDATA[SELECT D_START, D_END, D_MID, D_FLUID, CS_TALLY
       FROM T_DETAIL]]></select>
      <group name="G_DETAIL">
        <dataItem name="D_START" datatype="vchar2" width="30"
         defaultLabel="D Start">
          <dataDescriptor expression="D_START" order="1" width="30"/>
        </dataItem>
        <dataItem name="D_END" datatype="number" width="10"
         defaultLabel="D End">
          <dataDescriptor expression="D_END" order="2" width="10"/>
        </dataItem>
        <dataItem name="D_MID" datatype="vchar2" width="10"
         defaultLabel="D Mid">
          <dataDescriptor expression="D_MID" order="3" width="10"/>
        </dataItem>
        <dataItem name="D_FLUID" datatype="vchar2" width="20"
         defaultLabel="D Fluid">
          <dataDescriptor expression="D_FLUID" order="4" width="20"/>
        </dataItem>
        <dataItem name="CS_TALLY" datatype="number" width="10"
         defaultLabel="Cs Tally">
          <dataDescriptor expression="CS_TALLY" order="5" width="10"/>
        </dataItem>
      </group>
    </dataSource>
  </data>
  <layout>
  <section name="main">
    <body height="9.6">
      <frame name="M_BODY">
        <geometryInfo x="0.00" y="0.00" width="7.50" height="2.00"/>
        <generalLayout verticalElasticity="variable"/>
      <repeatingFrame name="R_OUTER" source="G_OUTER" printDirection="down">
        <geometryInfo x="0.20" y="0.40" width="7.20" height="1.60"/>
        <generalLayout verticalElasticity="variable"/>
        <field name="F_KEY" source="BREAK_KEY" alignment="start">
          <font face="Arial" size="10"/>
          <geometryInfo x="0.20" y="0.40" width="3.00" height="0.19"/>
        </field>
        <text name="B_NOTE" minWidowLines="1">
          <textSettings spacing="single"/>
          <geometryInfo x="5.10" y="0.40" width="0.60" height="0.19"/>
          <textSegment><font face="Arial" size="10"/>
          <string><![CDATA[Bare Band Label]]></string></textSegment>
        </text>
        <field name="F_GNOTE" source="G_NOTE" alignment="start">
          <font face="Arial" size="10"/>
          <geometryInfo x="5.80" y="0.40" width="0.60" height="0.19"/>
        </field>
        <field name="F_GCODE" source="G_CODE" alignment="end">
          <font face="Arial" size="10"/>
          <geometryInfo x="6.50" y="0.40" width="0.70" height="0.19"/>
        </field>

        <text name="B_CAP_START" minWidowLines="1">
          <textSettings spacing="single"/>
          <geometryInfo x="0.20" y="0.70" width="1.40" height="0.19"/>
          <textSegment><font face="Arial" size="9" bold="yes"/>
          <string><![CDATA[Cap Start]]></string></textSegment>
        </text>
        <text name="B_CAP_END" minWidowLines="1">
          <textSettings spacing="single" justify="{caption_justify}"/>
          <geometryInfo x="1.80" y="0.70" width="1.20" height="0.19"/>
          <textSegment><font face="Arial" size="9" bold="yes"/>
          <string><![CDATA[Cap End]]></string></textSegment>
        </text>
        <text name="B_CAP_MID" minWidowLines="1">
          <textSettings spacing="single" justify="center"/>
          <geometryInfo x="3.20" y="0.70" width="1.20" height="0.19"/>
          <textSegment><font face="Arial" size="9" bold="yes"/>
          <string><![CDATA[Cap Mid]]></string></textSegment>
        </text>
        <text name="B_CAP_FLUID" minWidowLines="1">
          <textSettings spacing="single"/>
          <geometryInfo x="4.60" y="0.70" width="1.20" height="0.19"/>
          <textSegment><font face="Arial" size="9" bold="yes"/>
          <string><![CDATA[Cap Fluid]]></string></textSegment>
        </text>

        <repeatingFrame name="R_DETAIL" source="G_DETAIL" printDirection="down">
          <geometryInfo x="0.20" y="1.00" width="7.20" height="0.20"/>
          <generalLayout verticalElasticity="variable"/>
          <field name="F_D_START" source="D_START" alignment="start">
            <font face="Arial" size="9"/>
            <geometryInfo x="0.20" y="1.00" width="1.40" height="0.19"/>
          </field>
          <field name="F_D_END" source="D_END" alignment="{detail_end_align}">
            <font face="Arial" size="9"/>
            <geometryInfo x="1.80" y="1.00" width="1.20" height="0.19"/>
          </field>
          <field name="F_D_MID" source="D_MID" alignment="center">
            <font face="Arial" size="9"/>
            <geometryInfo x="3.20" y="1.00" width="1.20" height="0.19"/>
          </field>
          <field name="F_D_FLUID" source="D_FLUID" alignment="end">
            <font face="Arial" size="9"/>
            <geometryInfo x="4.60" y="1.00" width="1.20" height="0.19"/>
            {gl}
          </field>
        </repeatingFrame>

        <frame name="M_FOOT">
          <geometryInfo x="0.20" y="1.40" width="7.20" height="0.30"/>
          <text name="B_FOOT_LBL" minWidowLines="1">
            <textSettings spacing="single"/>
            <geometryInfo x="4.60" y="1.45" width="1.00" height="0.19"/>
            <textSegment><font face="Arial" size="10" bold="yes"/>
            <string><![CDATA[Foot Label]]></string></textSegment>
          </text>
          <field name="F_TALLY" source="CS_TALLY" alignment="start">
            <font face="Arial" size="10"/>
            <geometryInfo x="5.90" y="1.45" width="1.30" height="0.19"/>
          </field>
        </frame>
      </repeatingFrame>
      </frame>
    </body>
  </section>
  </layout>
</report>
"""


def _convert(xml: str) -> str:
    res = convert(xml.encode("utf-8"), "breakcase.xml")
    assert not res.get("conversion_error"), res.get("conversion_error")
    return res["rdl_xml"]


def _aligns(rdl_xml: str) -> dict:
    """Textbox Name -> (TextAlign, first Value) for every emitted textbox."""
    root = ET.fromstring(rdl_xml)
    out: dict = {}
    for tb in root.iter(f"{NS}Textbox"):
        name = tb.get("Name") or ""
        ta = None
        val = ""
        for para in tb.iter(f"{NS}Paragraph"):
            st = para.find(f"{NS}Style")
            el = st.find(f"{NS}TextAlign") if st is not None else None
            if el is not None and ta is None:
                ta = el.text
            for v in para.iter(f"{NS}Value"):
                if not val:
                    val = v.text or ""
        out[name] = (ta, val)
    return out


def _align_of(aligns: dict, needle: str, prefix: str = ""):
    """The TextAlign of the single textbox whose value carries ``needle``
    (optionally restricted to one emitter's textbox-name prefix)."""
    hits = {n: a for n, (a, v) in aligns.items()
            if needle in (v or "") and n.startswith(prefix)}
    assert len(hits) == 1, (needle, prefix, hits)
    return next(iter(hits.values()))


def test_fixture_takes_the_grouped_tabular_break_path():
    """Guard the guard: if the fixture stopped matching the archetype the
    assertions below would silently test a different emitter."""
    from converter.parsers.oracle_xml import parse_oracle_xml
    rep = parse_oracle_xml(_source_xml().encode("utf-8"))
    assert R._grouped_tabular_spec(rep) is not None


def test_detail_columns_use_their_declared_justification():
    a = _aligns(_convert(_source_xml()))
    assert _align_of(a, "Fields!D_END.Value", "Tb_D_") == "Right"
    assert _align_of(a, "Fields!D_MID.Value", "Tb_D_") == "Center"
    assert _align_of(a, "Fields!D_START.Value", "Tb_D_") == "Left"


def test_detail_column_declaring_fluid_width_stays_left():
    a = _aligns(_convert(_source_xml()))
    assert _align_of(a, "Fields!D_FLUID.Value", "Tb_D_") == "Left"


def test_column_captions_use_their_declared_justification():
    a = _aligns(_convert(_source_xml()))
    assert _align_of(a, "Cap End") == "Right"
    assert _align_of(a, "Cap Mid") == "Center"
    assert _align_of(a, "Cap Start") == "Left"


def test_group_header_members_use_their_declared_justification():
    """The band's positional guess (right-hand half -> Right) may only fill the
    gap the declaration leaves: a right-hand member declaring start is Left."""
    a = _aligns(_convert(_source_xml()))
    assert _align_of(a, "Fields!G_CODE.Value", "Tb_GH_") == "Right"
    assert _align_of(a, "Fields!G_NOTE.Value", "Tb_GH_") == "Left"


def test_group_footer_members_use_their_declared_justification():
    """Same in the totals stack, whose default is Right."""
    a = _aligns(_convert(_source_xml()))
    tally = {n: ta for n, (ta, v) in a.items()
             if n.startswith("Tb_F_") and "CS_TALLY" in (v or "")}
    assert tally and set(tally.values()) == {"Left"}, tally


def test_undeclared_members_keep_the_emitter_default():
    """Nothing declared -> the emitter's own default still applies, so the fix
    can never be "solved" by left-aligning (or right-aligning) everything."""
    a = _aligns(_convert(_source_xml()))
    # A right-hand band label that declares nothing keeps the positional guess.
    assert _align_of(a, "Bare Band Label", "Tb_GH_") == "Right"
    # A totals-stack label that declares nothing keeps the stack's Right.
    assert _align_of(a, "Foot Label", "Tb_F_") == "Right"
    # A caption that declares nothing keeps Oracle's start-of-box default.
    assert _align_of(a, "Cap Fluid") == "Left"


def test_mutation_anchor_dropping_the_declarations_restores_the_defaults():
    """Strip ONLY the declarations and the very same objects fall back to the
    emitter defaults -- proof the alignment is read from the declaration and
    not from a name, a datatype or an x position."""
    a = _aligns(_convert(_source_xml(caption_justify="start",
                                     detail_end_align="start")))
    assert _align_of(a, "Cap End") == "Left"
    assert _align_of(a, "Fields!D_END.Value", "Tb_D_") == "Left"


def test_mutation_anchor_dropping_the_elasticity_restores_right():
    """And the fluid detail column goes Right the moment its elasticity
    declaration is gone -- the two declarations are read together."""
    a = _aligns(_convert(_source_xml(fluid_attr="")))
    assert _align_of(a, "Fields!D_FLUID.Value", "Tb_D_") == "Right"
