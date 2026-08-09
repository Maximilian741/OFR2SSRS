"""Declared font attributes must reach every textbox the generator emits.

Oracle writes face, size, weight, slant AND underline on each layout object's
own ``<font>``. Three separate defects lived in the body emitters, all of the
same shape -- the emitter had an opinion where the source had a declaration:

1. ``underline="yes"`` was dropped everywhere in the body. The whole RDL
   carried ZERO ``<TextDecoration>`` elements while the source declared
   underlined column captions and the Oracle truth draws a rule under each
   one. (The cover builder did emit it, so the attribute survived parsing --
   only the body emitters ignored it.)
2. Bold was INVENTED: the column-header strip hardcoded ``bold=True`` and the
   band/totals emitters forced every literal text bold, so objects declaring a
   plain ``<font face="..." size="10"/>`` printed heavy.
3. The report-end trailer hardcoded its own 9pt house size over the declared
   size of the objects it prints.

The fix is one shared helper, ``_declared_font_style``, splatted into
``_build_textbox`` by each emitter, so a new emitter inherits the whole
dialect at once. Both directions are pinned here: a declaration that DOES
carry bold must still print bold, and an emitter with no declaration in hand
keeps its own house default.

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


# --------------------------------------------------------------------------
# the shared helper, in isolation


class _Font:
    def __init__(self, **kw):
        self.font_family = kw.get("face", "")
        self.font_size = kw.get("size", 0)
        self.bold = kw.get("bold", False)
        self.italic = kw.get("italic", False)
        self.underline = kw.get("underline", False)


def test_helper_reads_every_declared_attribute():
    st = R._declared_font_style(
        _Font(face="Courier New", size=12, bold=True, italic=True,
              underline=True), "9pt")
    assert st == {"font_family": "Courier New", "font_size": "12pt",
                  "bold": True, "italic": True, "underline": True}


def test_helper_invents_nothing_for_a_plain_declaration():
    """A declared object with no weight/slant/underline attribute is plain --
    even when the caller's own default for an UNdeclared box is bold."""
    st = R._declared_font_style(_Font(face="Arial", size=10), "9pt",
                                default_bold=True)
    assert st["bold"] is False
    assert st["underline"] is False
    assert st["italic"] is False
    assert st["font_size"] == "10pt"


def test_helper_falls_back_only_where_nothing_is_declared():
    """No layout object at all -> the caller's house style still stands, so
    the fix can never be "solved" by un-bolding everything."""
    st = R._declared_font_style(None, "9pt", default_bold=True)
    assert st == {"font_family": None, "font_size": "9pt", "bold": True,
                  "italic": False, "underline": False}
    # an object that declares no size falls back to the caller's size
    assert R._declared_font_style(_Font(face="Arial", size=0),
                                  "13pt")["font_size"] == "13pt"


# --------------------------------------------------------------------------
# end to end: the grouped-tabular break archetype + its report-end trailer
#
# Declared, and what each must emit:
#   caption  B_CAP_UND    size 10, underline="yes"   -> 10pt, plain, Underline
#            B_CAP_PLAIN  size 10, no weight         -> 10pt, plain, no rule
#            B_CAP_BOLD   size 10, bold="yes"        -> 10pt, Bold
#            B_CAP_WIDE   size 10, justify="end"     -> its DECLARED box width
#   band     B_BAND_PLAIN size 10, no weight         -> plain
#   footer   B_FOOT_PLAIN size 10, no weight         -> plain
#            B_FOOT_BOLD  size 10, bold="yes"        -> Bold
#   trailer  B_TR_LBL     size 12, no weight         -> 12pt, plain
#            F_TR_VAL     size 12, bold="yes"        -> 12pt, Bold

_CAP_UND = ' underline="yes"'


def _source_xml(cap_underline: str = _CAP_UND,
                cap_bold: str = ' bold="yes"',
                trailer_size: str = "12") -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<report name="FONTCASE" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q_OUTER">
      <select><![CDATA[SELECT BREAK_KEY, G_CODE FROM T_BREAK]]></select>
      <group name="G_OUTER">
        <dataItem name="BREAK_KEY" datatype="vchar2" width="30"
         defaultLabel="Break Key">
          <dataDescriptor expression="BREAK_KEY" order="1" width="30"/>
        </dataItem>
        <dataItem name="G_CODE" datatype="vchar2" width="20"
         defaultLabel="G Code">
          <dataDescriptor expression="G_CODE" order="2" width="20"/>
        </dataItem>
      </group>
    </dataSource>
    <dataSource name="Q_DETAIL">
      <select><![CDATA[SELECT D_ONE, D_TWO, D_THREE, D_FOUR, CS_TALLY
       FROM T_DETAIL]]></select>
      <group name="G_DETAIL">
        <dataItem name="D_ONE" datatype="vchar2" width="30"
         defaultLabel="D One">
          <dataDescriptor expression="D_ONE" order="1" width="30"/>
        </dataItem>
        <dataItem name="D_TWO" oracleDatatype="number" width="10"
         defaultLabel="D Two">
          <dataDescriptor expression="D_TWO" order="2" width="10"/>
        </dataItem>
        <dataItem name="D_THREE" datatype="vchar2" width="10"
         defaultLabel="D Three">
          <dataDescriptor expression="D_THREE" order="3" width="10"/>
        </dataItem>
        <dataItem name="D_FOUR" oracleDatatype="number" width="10"
         defaultLabel="D Four">
          <dataDescriptor expression="D_FOUR" order="4" width="10"/>
        </dataItem>
        <dataItem name="CS_TALLY" oracleDatatype="number" width="10"
         defaultLabel="Cs Tally">
          <dataDescriptor expression="CS_TALLY" order="5" width="10"/>
        </dataItem>
      </group>
    </dataSource>
    <summary name="CS_ALL_TALLY" source="CS_TALLY" function="sum" width="20"
     reset="report" compute="report" columnFlags="8"/>
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
        <text name="B_BAND_PLAIN" minWidowLines="1">
          <textSettings spacing="single"/>
          <geometryInfo x="5.10" y="0.40" width="0.60" height="0.19"/>
          <textSegment><font face="Arial" size="10"/>
          <string><![CDATA[Band Plain]]></string></textSegment>
        </text>
        <field name="F_GCODE" source="G_CODE" alignment="end">
          <font face="Arial" size="10"/>
          <geometryInfo x="6.50" y="0.40" width="0.70" height="0.19"/>
        </field>

        <text name="B_CAP_UND" minWidowLines="1">
          <textSettings spacing="single"/>
          <geometryInfo x="0.20" y="0.70" width="1.40" height="0.19"/>
          <textSegment><font face="Arial" size="10"{cap_underline}/>
          <string><![CDATA[Cap Und]]></string></textSegment>
        </text>
        <text name="B_CAP_PLAIN" minWidowLines="1">
          <textSettings spacing="single"/>
          <geometryInfo x="1.80" y="0.70" width="1.20" height="0.19"/>
          <textSegment><font face="Arial" size="10"/>
          <string><![CDATA[Cap Plain]]></string></textSegment>
        </text>
        <text name="B_CAP_BOLD" minWidowLines="1">
          <textSettings spacing="single"/>
          <geometryInfo x="3.20" y="0.70" width="1.20" height="0.19"/>
          <textSegment><font face="Arial" size="10"{cap_bold}/>
          <string><![CDATA[Cap Bold]]></string></textSegment>
        </text>
        <text name="B_CAP_WIDE" minWidowLines="1">
          <textSettings spacing="single" justify="end"/>
          <geometryInfo x="4.60" y="0.70" width="0.90" height="0.19"/>
          <textSegment><font face="Arial" size="10"/>
          <string><![CDATA[Cap Wide]]></string></textSegment>
        </text>

        <repeatingFrame name="R_DETAIL" source="G_DETAIL" printDirection="down">
          <geometryInfo x="0.20" y="1.00" width="7.20" height="0.20"/>
          <generalLayout verticalElasticity="variable"/>
          <field name="F_D_ONE" source="D_ONE" alignment="start">
            <font face="Arial" size="10"/>
            <geometryInfo x="0.20" y="1.00" width="1.40" height="0.19"/>
          </field>
          <field name="F_D_TWO" source="D_TWO" alignment="end">
            <font face="Arial" size="10"/>
            <geometryInfo x="1.80" y="1.00" width="1.20" height="0.19"/>
          </field>
          <field name="F_D_THREE" source="D_THREE" alignment="center">
            <font face="Arial" size="10"/>
            <geometryInfo x="3.20" y="1.00" width="1.20" height="0.19"/>
          </field>
          <field name="F_D_FOUR" source="D_FOUR" alignment="end">
            <font face="Arial" size="10"/>
            <geometryInfo x="4.60" y="1.00" width="1.20" height="0.19"/>
          </field>
        </repeatingFrame>

        <frame name="M_FOOT">
          <geometryInfo x="0.20" y="1.40" width="7.20" height="0.30"/>
          <text name="B_FOOT_PLAIN" minWidowLines="1">
            <textSettings spacing="single"/>
            <geometryInfo x="4.60" y="1.45" width="1.00" height="0.19"/>
            <textSegment><font face="Arial" size="10"/>
            <string><![CDATA[Foot Plain]]></string></textSegment>
          </text>
          <text name="B_FOOT_BOLD" minWidowLines="1">
            <textSettings spacing="single"/>
            <geometryInfo x="4.60" y="1.70" width="1.00" height="0.19"/>
            <textSegment><font face="Arial" size="10" bold="yes"/>
            <string><![CDATA[Foot Bold]]></string></textSegment>
          </text>
          <field name="F_TALLY" source="CS_TALLY" alignment="end">
            <font face="Arial" size="10"/>
            <geometryInfo x="5.90" y="1.45" width="1.30" height="0.19"/>
          </field>
        </frame>
      </repeatingFrame>
      </frame>
      <frame name="M_REPORT_TOTAL">
        <geometryInfo x="0.00" y="2.40" width="7.50" height="0.40"/>
        <text name="B_TR_LBL" minWidowLines="1">
          <textSettings justify="end" spacing="single"/>
          <geometryInfo x="4.30" y="2.50" width="2.15" height="0.19"/>
          <textSegment><font face="Arial" size="{trailer_size}"/>
          <string><![CDATA[Trailer Caption]]></string></textSegment>
        </text>
        <field name="F_TR_VAL" source="CS_ALL_TALLY" alignment="end"
         formatMask="NNN,NN0">
          <font face="Arial" size="{trailer_size}" bold="yes"/>
          <geometryInfo x="6.60" y="2.50" width="0.90" height="0.19"/>
        </field>
      </frame>
    </body>
  </section>
  </layout>
</report>
"""


def _convert(xml: str) -> str:
    res = convert(xml.encode("utf-8"), "fontcase.xml")
    assert not res.get("conversion_error"), res.get("conversion_error")
    return res["rdl_xml"]


def _styles(rdl_xml: str) -> dict:
    """Textbox Name -> {value, size, weight, decoration, left, width}."""
    root = ET.fromstring(rdl_xml)
    out: dict = {}

    def _f(tb, tag):
        t = tb.findtext(f"{NS}{tag}")
        try:
            return float((t or "0").replace("in", ""))
        except ValueError:
            return 0.0

    for tb in root.iter(f"{NS}Textbox"):
        run = next(iter(tb.iter(f"{NS}TextRun")), None)
        if run is None:
            continue
        st = run.find(f"{NS}Style")
        out[tb.get("Name") or ""] = {
            "value": run.findtext(f"{NS}Value") or "",
            "size": st.findtext(f"{NS}FontSize") if st is not None else None,
            "weight": st.findtext(f"{NS}FontWeight") if st is not None else None,
            "decoration": (st.findtext(f"{NS}TextDecoration")
                           if st is not None else None),
            "left": _f(tb, "Left"), "width": _f(tb, "Width"),
        }
    return out


def _box(styles: dict, needle: str, prefix: str = ""):
    hits = [s for n, s in styles.items()
            if needle in (s["value"] or "") and n.startswith(prefix)]
    assert len(hits) == 1, (needle, prefix, [h["value"] for h in hits])
    return hits[0]


def test_fixture_takes_the_grouped_tabular_break_path():
    """Guard the guard: if the fixture stopped matching the archetype the
    assertions below would silently test a different emitter."""
    rep = parse_oracle_xml(_source_xml().encode("utf-8"))
    assert R._grouped_tabular_spec(rep) is not None


# -- (a) declared underline ------------------------------------------------

def test_declared_underline_reaches_the_body():
    rdl = _convert(_source_xml())
    assert "<TextDecoration>Underline</TextDecoration>" in rdl, (
        "the body emitters dropped every declared underline")
    s = _styles(rdl)
    assert _box(s, "Cap Und", "Tb_CH_")["decoration"] == "Underline"


def test_undeclared_underline_draws_no_rule():
    """Prove-the-gate in the other direction: the fix must not underline
    everything -- a caption that declares no underline gets no rule."""
    s = _styles(_convert(_source_xml()))
    assert _box(s, "Cap Plain", "Tb_CH_")["decoration"] is None
    assert _box(s, "Cap Bold", "Tb_CH_")["decoration"] is None
    # and with the attribute stripped from the source, the rule disappears
    s2 = _styles(_convert(_source_xml(cap_underline="")))
    assert _box(s2, "Cap Und", "Tb_CH_")["decoration"] is None


# -- (b) bold only where declared -----------------------------------------

def test_plain_declarations_are_never_bolded():
    """Column captions, band literals and totals-stack literals that declare
    no weight print plain -- the emitters used to force all three bold."""
    s = _styles(_convert(_source_xml()))
    assert _box(s, "Cap Und", "Tb_CH_")["weight"] is None
    assert _box(s, "Cap Plain", "Tb_CH_")["weight"] is None
    assert _box(s, "Band Plain", "Tb_GH_")["weight"] is None
    assert _box(s, "Foot Plain", "Tb_F_")["weight"] is None


def test_declared_bold_still_prints_bold():
    """The other direction: a source that DOES declare bold keeps it."""
    s = _styles(_convert(_source_xml()))
    assert _box(s, "Cap Bold", "Tb_CH_")["weight"] == "Bold"
    assert _box(s, "Foot Bold", "Tb_F_")["weight"] == "Bold"
    # and stripping the declaration removes it -> the weight tracks the source
    s2 = _styles(_convert(_source_xml(cap_bold="")))
    assert _box(s2, "Cap Bold", "Tb_CH_")["weight"] is None


def test_declared_caption_size_survives():
    """The column strip's 9pt house size may only fill in for a caption with
    no declared size of its own."""
    s = _styles(_convert(_source_xml()))
    for wording in ("Cap Und", "Cap Plain", "Cap Bold"):
        assert _box(s, wording, "Tb_CH_")["size"] == "10pt", wording


# -- (c) declared size in the report-end trailer ---------------------------

@pytest.mark.parametrize("declared", ["12", "14"])
def test_trailer_prints_at_its_declared_size(declared):
    s = _styles(_convert(_source_xml(trailer_size=declared)))
    lbl = _box(s, "Trailer Caption", "Tb_GrandTotal_")
    assert lbl["size"] == f"{declared}pt", (
        "the trailer overrode the declared size with its house size")
    assert lbl["weight"] is None, "the trailer caption declares no weight"
    val = _box(s, "Sum(Fields!CS_TALLY", "Tb_GrandTotal_")
    assert val["size"] == f"{declared}pt"
    assert val["weight"] == "Bold", "the declared bold value must stay bold"


# -- the knock-on: a right-justified caption keeps its declared box --------

def test_right_justified_caption_keeps_its_declared_box_width():
    """Oracle right-justifies inside the caption's OWN box. The synthesized
    column span runs to the next column's start, which is wider, so anchoring
    to it slid the caption past its declared right edge into the neighbouring
    band (engine-render measured)."""
    s = _styles(_convert(_source_xml()))
    cap = _box(s, "Cap Wide", "Tb_CH_")
    assert cap["left"] + cap["width"] <= 4.60 + 0.90 + 0.03, (
        f"caption box runs to {cap['left'] + cap['width']}in, "
        f"declared right edge is 5.50in")
    # a caption that declares no justification still spans its column
    assert _box(s, "Cap Plain", "Tb_CH_")["width"] > 1.20
