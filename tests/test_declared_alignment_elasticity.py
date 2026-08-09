"""Declared field alignment vs. declared horizontal elasticity.

Oracle justifies a field's text inside the box it actually FORMATS, not
inside the design-time rectangle. When the object declares a fluid
horizontal elasticity (``variable``/``contract``) the box is sized to its
content and keeps its left edge at the declared x, so a declared
``end``/``right`` justification has nothing left to justify against and the
glyphs print flush at the declared x.

Truth-PDF measured on a real Oracle-produced page: a footer date declared
``alignment="right" horizontalElasticity="variable"`` at x=7.139in prints
its first glyph at exactly 7.139in -- NOT flush with the box's 7.95in right
edge -- while a fixed-width sibling declared ``end`` on the same line does
print flush right. Emitting TextAlign=Right for the fluid object pushed a
contact block's phone line to the right edge of its box, away from the email
line above it that shares its declared x.

The second half of the rule matters just as much: a stat/accounting section
defaults its value columns to right-aligned BY POSITION. That default may
only fill the gap the declaration leaves -- it must never overrule a column
that declares its own justification, and it must still apply where nothing
is declared. Both directions are asserted here so the fix can never be
"solved" by dropping right-alignment everywhere.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

from converter import convert
from converter.generators import rdl as R
from converter.parsers.oracle_xml import parse_oracle_xml

NS = ("{http://schemas.microsoft.com/sqlserver/reporting/"
      "2008/01/reportdefinition}")

# Every data column in the fixture below, and how it is declared:
#   COL_PLAIN  alignment="start"                     -> Left
#   COL_FIXED  alignment="end"                       -> Right
#   COL_FLUID  alignment="end" + fluid width         -> Left (the rule)
#   SIDE_TOP   alignment="start"                     -> Left
#   SIDE_LOW   alignment="end" + fluid width         -> Left (the rule)
#   SIDE_BARE  nothing declared                      -> emitter's own default
COLUMNS = ("COL_PLAIN", "COL_FIXED", "COL_FLUID",
           "SIDE_TOP", "SIDE_LOW", "SIDE_BARE")


def _source_xml(fluid_attr: str = 'horizontalElasticity="variable"') -> str:
    """Two stacked frames over two datasets -- the tabular column path plus a
    second section table, so the assertions cover more than one emitter."""
    gl = f"<generalLayout {fluid_attr}/>" if fluid_attr else ""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<report name="ALIGNCASE" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q_MAIN">
      <select><![CDATA[SELECT COL_PLAIN, COL_FIXED, COL_FLUID
       FROM T_MAIN]]></select>
      <group name="G_MAIN">
        <dataItem name="COL_PLAIN" datatype="vchar2" width="60"
         defaultLabel="Col Plain">
          <dataDescriptor expression="COL_PLAIN" order="1" width="60"/>
        </dataItem>
        <dataItem name="COL_FIXED" datatype="vchar2" width="40"
         defaultLabel="Col Fixed">
          <dataDescriptor expression="COL_FIXED" order="2" width="40"/>
        </dataItem>
        <dataItem name="COL_FLUID" datatype="vchar2" width="40"
         defaultLabel="Col Fluid">
          <dataDescriptor expression="COL_FLUID" order="3" width="40"/>
        </dataItem>
      </group>
    </dataSource>
    <dataSource name="Q_SIDE">
      <select><![CDATA[SELECT SIDE_TOP, SIDE_LOW, SIDE_BARE
       FROM T_SIDE]]></select>
      <group name="G_SIDE">
        <dataItem name="SIDE_TOP" datatype="vchar2" width="60"
         defaultLabel="Side Top">
          <dataDescriptor expression="SIDE_TOP" order="1" width="60"/>
        </dataItem>
        <dataItem name="SIDE_LOW" datatype="vchar2" width="60"
         defaultLabel="Side Low">
          <dataDescriptor expression="SIDE_LOW" order="2" width="60"/>
        </dataItem>
        <dataItem name="SIDE_BARE" datatype="vchar2" width="60"
         defaultLabel="Side Bare">
          <dataDescriptor expression="SIDE_BARE" order="3" width="60"/>
        </dataItem>
      </group>
    </dataSource>
  </data>
  <layout>
  <section name="main">
    <body height="9.6">
      <text name="B_H1" minWidowLines="1">
        <textSettings spacing="single"/>
        <geometryInfo x="0.30" y="0.10" width="3.00" height="0.19"/>
        <textSegment><font face="Arial" size="10" bold="yes"/>
        <string><![CDATA[Plain]]></string></textSegment>
      </text>
      <text name="B_H2" minWidowLines="1">
        <textSettings spacing="single"/>
        <geometryInfo x="3.50" y="0.10" width="1.80" height="0.19"/>
        <textSegment><font face="Arial" size="10" bold="yes"/>
        <string><![CDATA[Fixed]]></string></textSegment>
      </text>
      <text name="B_H3" minWidowLines="1">
        <textSettings spacing="single"/>
        <geometryInfo x="5.50" y="0.10" width="1.80" height="0.19"/>
        <textSegment><font face="Arial" size="10" bold="yes"/>
        <string><![CDATA[Fluid]]></string></textSegment>
      </text>
      <repeatingFrame name="R_MAIN" source="G_MAIN" printDirection="down">
        <geometryInfo x="0.30" y="0.40" width="7.00" height="0.20"/>
        <generalLayout verticalElasticity="variable"/>
        <field name="F_PLAIN" source="COL_PLAIN" alignment="start">
          <font face="Arial" size="10"/>
          <geometryInfo x="0.30" y="0.40" width="3.00" height="0.19"/>
        </field>
        <field name="F_FIXED" source="COL_FIXED" alignment="end">
          <font face="Arial" size="10"/>
          <geometryInfo x="3.50" y="0.40" width="1.80" height="0.19"/>
        </field>
        <field name="F_FLUID" source="COL_FLUID" alignment="end">
          <font face="Arial" size="10"/>
          <geometryInfo x="5.50" y="0.40" width="1.80" height="0.19"/>
          {gl}
        </field>
      </repeatingFrame>
      <repeatingFrame name="R_SIDE" source="G_SIDE" printDirection="down">
        <geometryInfo x="0.30" y="5.00" width="7.00" height="0.40"/>
        <generalLayout verticalElasticity="variable"/>
        <field name="F_SIDE_TOP" source="SIDE_TOP" alignment="start">
          <font face="Arial" size="9"/>
          <geometryInfo x="4.60" y="5.00" width="2.50" height="0.19"/>
        </field>
        <field name="F_SIDE_LOW" source="SIDE_LOW" alignment="end">
          <font face="Arial" size="10"/>
          <geometryInfo x="4.60" y="5.20" width="2.00" height="0.19"/>
          {gl}
        </field>
        <field name="F_SIDE_BARE" source="SIDE_BARE">
          <font face="Arial" size="10"/>
          <geometryInfo x="6.80" y="5.20" width="0.60" height="0.19"/>
        </field>
      </repeatingFrame>
    </body>
  </section>
  </layout>
</report>
"""


def _text_aligns_by_source(rdl_xml: str) -> dict:
    """source column -> set of <TextAlign> values over every textbox that
    binds it. Emitter-agnostic on purpose: whichever builder drew the field,
    its declared justification has to survive."""
    root = ET.fromstring(rdl_xml)
    out: dict = {}
    for para in root.iter(f"{NS}Paragraph"):
        st = para.find(f"{NS}Style")
        el = st.find(f"{NS}TextAlign") if st is not None else None
        ta = el.text if el is not None else None
        for v in para.iter(f"{NS}Value"):
            for col in COLUMNS:
                if f"Fields!{col}.Value" in (v.text or ""):
                    out.setdefault(col, set()).add(ta)
    return out


def _convert(xml: str) -> str:
    res = convert(xml.encode("utf-8"), "aligncase.xml")
    assert not res.get("conversion_error"), res.get("conversion_error")
    return res["rdl_xml"]


# --------------------------------------------------------------------------
# parser


def test_parser_captures_horizontal_elasticity():
    rep = parse_oracle_xml(_source_xml().encode("utf-8"))
    seen = {}

    def walk(g):
        for f in (getattr(g, "fields", None) or []):
            seen[getattr(f, "name", "")] = getattr(
                f, "horizontal_elasticity", None)
        for c in (getattr(g, "children", None) or []):
            walk(c)

    for g in rep.layout:
        walk(g)
    assert seen.get("F_FLUID") == "variable"
    assert seen.get("F_SIDE_LOW") == "variable"
    # A field that declares no elasticity keeps the empty default -- the
    # attribute must never be invented.
    assert seen.get("F_FIXED") == ""
    assert seen.get("F_PLAIN") == ""


# --------------------------------------------------------------------------
# the rule itself


class _Stub:
    """A layout object as the emitter sees it.

    ``kind``/``text`` matter: a fluid box can only contract onto a width
    Oracle knows while it FORMATS the object, so what the object holds is
    part of the declaration (see _design_time_measurable). The default is a
    run-time-valued FIELD, which never contracts predictably.
    """

    def __init__(self, align="", he="", kind="field", text=""):
        self.align = align
        self.horizontal_elasticity = he
        self.kind = kind
        self.text = text


def _static_text(align, he):
    return _Stub(align, he, kind="text", text="WUTMB Control Program")


def _page_counter(align, he):
    return _Stub(align, he, kind="text",
                 text="Page &PageNumber of &TotalPages")


def test_fluid_width_cancels_end_and_right_justification():
    for a in ("end", "right"):
        for he in ("variable", "contract"):
            assert R._declared_align(_Stub(a, he)) == "start", (a, he)
            assert R._declared_align(_static_text(a, he)) == "start", (a, he)


def test_fluid_width_left_anchors_a_static_centred_boilerplate():
    """A CENTRED fluid box contracts too -- onto the static string it holds --
    so the centre line it would justify against collapses to the declared
    LEFT edge.

    Truth-PDF measured: a landscape list title declared justify="center"
    horizontalElasticity="variable" at x=3.31970in w=4.37061in -- declared
    box 239.02..553.70pt, centre 396.36pt -- prints its 272.03pt of glyphs
    at 239.00..511.03pt, i.e. flush with the declared LEFT edge (dLeft =
    -0.02pt). Centring it in the declared box put the first glyph at
    260.35pt, 21.35pt right of where Oracle prints it (engine-render
    measured against that report's truth PDF).
    """
    for he in ("variable", "contract"):
        for a in ("center", "centre", "middle"):
            assert R._declared_align(_static_text(a, he)) == "start", (a, he)
        assert R._declared_text_align(
            _static_text("center", he), "X") == "Left"


def test_fluid_width_does_not_cancel_center_on_a_page_counter():
    """The contraction needs a width Oracle can measure while it lays the
    object out. A page built-in is substituted AFTER pagination, so the box
    keeps its declared design width and its declared centre line survives.

    Truth-PDF measured (two separate reports): a footer page counter declared
    justify="center" horizontalElasticity="variable" at x=3.4917in w=1.5137in
    -- declared box 251.40..360.39pt, centre 305.90pt -- prints its glyphs at
    293.52..317.98pt, i.e. centred on 305.75pt. Re-anchoring it to "start"
    would have put the first glyph at 251.40pt, 42pt to the left of where
    Oracle prints it.
    """
    for he in ("variable", "contract"):
        for a in ("center", "centre", "middle"):
            assert R._declared_align(_page_counter(a, he)) == a, (a, he)
            # the &<PageNumber> spelling of the same built-in
            assert R._declared_align(
                _Stub(a, he, kind="text",
                      text="Page &<PageNumber> of &<TotalPages>")) == a
        assert R._declared_text_align(
            _page_counter("center", he), "X") == "Center"


def test_fluid_width_does_not_cancel_center_on_a_runtime_valued_field():
    """A data/parameter-bound field carries a run-time value, so its
    formatted width is unknown at layout time and the declared box (and its
    centre) stands. No truth PDF in the corpus declares center+fluid on a
    field, so this half stays at the conservative declared-box reading.
    """
    for he in ("variable", "contract"):
        for a in ("center", "centre", "middle"):
            assert R._declared_align(_Stub(a, he)) == a, (a, he)
        assert R._declared_text_align(_Stub("center", he), "X") == "Center"


def test_fixed_and_expand_widths_keep_their_declared_justification():
    # "expand" keeps the declared width as a floor, so the declared
    # justification still has a box to justify against.
    for he in ("", "fixed", "expand"):
        assert R._declared_align(_Stub("end", he)) == "end", he
        assert R._declared_align(_Stub("center", he)) == "center", he


def test_undeclared_alignment_stays_undeclared():
    # No declaration -> "" so callers keep their own default (SSRS General
    # right-aligns numbers, which is Oracle's datatype default too).
    for he in ("", "variable", "contract", "expand"):
        assert R._declared_align(_Stub("", he)) == ""
    assert R._ssrs_text_align(R._declared_align(_Stub("", "variable"))) is None


# --------------------------------------------------------------------------
# end to end through the emitters


def test_fluid_width_fields_are_not_right_aligned_in_the_rdl():
    aligns = _text_aligns_by_source(_convert(_source_xml()))
    for col in ("COL_FLUID", "SIDE_LOW"):
        assert col in aligns, aligns
        assert "Right" not in aligns[col], (col, aligns)


def test_declared_end_on_a_fixed_width_field_still_right_aligns():
    aligns = _text_aligns_by_source(_convert(_source_xml()))
    assert aligns.get("COL_FIXED") == {"Right"}, aligns


def test_declared_start_stays_left():
    aligns = _text_aligns_by_source(_convert(_source_xml()))
    assert aligns.get("COL_PLAIN") == {"Left"}, aligns
    assert aligns.get("SIDE_TOP") == {"Left"}, aligns


def test_undeclared_column_keeps_the_emitter_numeric_default():
    """The positional right-align default is only allowed to fill the gap the
    declaration leaves -- it must still apply to a column that declares
    nothing, or "honor the declaration" would silently become "never right
    align"."""
    aligns = _text_aligns_by_source(_convert(_source_xml()))
    assert aligns.get("SIDE_BARE") == {"Right"}, aligns


def test_same_fields_right_align_when_the_fluid_declaration_is_absent():
    """Mutation anchor: strip ONLY the horizontalElasticity declaration and
    the very same fields go back to Right -- proving the guard keys off the
    declaration, not off a name or a position."""
    aligns = _text_aligns_by_source(_convert(_source_xml(fluid_attr="")))
    assert aligns.get("COL_FLUID") == {"Right"}, aligns
    assert aligns.get("SIDE_LOW") == {"Right"}, aligns
