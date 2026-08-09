"""DECLARED stroke pattern + DECLARED stroke weight = a rule's rendered ink.

Two halves of the same dialect, both truth-measured against the Oracle
exports and re-measured by rendering our own RDL through the engine and
rasterizing the PDF at 150 and 300 dpi.

1. ``<visualSettings dash="dot">`` is INK, not decoration.
   Two independent truth exports declare it. Each one's Oracle PDF strokes
   the rule with a real PDF dash array -- ``[ 1 ] 0`` -- at exactly the
   declared ``lineWidth`` (2.0pt on one, 1.0pt on the other), so the rule
   paints about HALF the ink of a continuous stroke. Rendered and
   rasterized (mean coverage across the rule, integrated over a window
   sized to the stroke):

       invoice rule, declared lineWidth="2" dash="dot"
           truth            0.01092in @150dpi   0.01096in @300dpi
           dash ignored     0.02241in           0.02242in    (2.05x truth)
           dash honoured    0.00892in           0.00902in    (0.82x truth)

       inspection rule, declared lineWidth="1" dash="dot"
                            peak row coverage @150 / @300
           truth            0.547 / 0.546
           dash ignored     1.000 / 1.000                    (1.83x truth)
           dash honoured    0.404 / 0.406                    (0.74x truth)

   A dash cannot be expressed by the filled bar a rule used to be emitted
   as, so a dashed rule must be a real ``<Line>`` stroke. The residual
   (0.74-0.82x) is SSRS's own dot duty cycle -- it strokes Dotted as
   ``[ 2 3 ]`` (40% on) where Oracle strokes ``[ 1 ] 0`` (50% on) -- and
   RDL exposes no way to parameterise it.

2. A DECLARED ``lineWidth`` maps 1:1 to points on a frame edge too.
   The same invoice declares ``lineWidth="2"`` on its outer frames and its
   Oracle PDF strokes them at exactly 2.0pt; every solid-linePattern frame
   that declares NO lineWidth is stroked at width 0.0 (the device
   hairline). The emitter used to write a flat house ``1pt`` on every
   bordered frame -- half the ink on the declared-2pt edges and ~4x the
   ink on the undeclared ones.
"""
import xml.etree.ElementTree as ET

from converter.generators import rdl as R
from converter.parsers.oracle_xml import parse_oracle_xml


# The stroke weight the emitter uses for an UNDECLARED width (Oracle's
# device hairline; SSRS has no zero-width border).
HAIRLINE = R._HAIRLINE_WEIGHT

_DASH_XML = """<?xml version="1.0" encoding="UTF-8"?>
<report name="DASHINK" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q1">
      <select><![CDATA[SELECT LBL FROM T]]></select>
      <group name="G_R">
        <dataItem name="LBL" datatype="vchar2" width="60" defaultLabel="Lbl">
          <dataDescriptor expression="LBL" order="1" width="60"/>
        </dataItem>
      </group>
    </dataSource>
  </data>
  <layout>
  <section name="main">
    <body height="9.6">
      <line name="B_DOTTED" arrow="none">
        <geometryInfo x="0.3" y="0.8" width="7.0" height="0.0"/>
        <visualSettings lineWidth="2" linePattern="solid" dash="dot"/>
        <points><point x="0.3" y="0.8"/><point x="7.3" y="0.8"/></points>
      </line>
      <line name="B_LONGDASH" arrow="none">
        <geometryInfo x="0.3" y="1.0" width="7.0" height="0.0"/>
        <visualSettings lineWidth="1" linePattern="solid" dash="longDash"/>
        <points><point x="0.3" y="1.0"/><point x="7.3" y="1.0"/></points>
      </line>
      <line name="B_PLAIN" arrow="none">
        <geometryInfo x="0.3" y="1.2" width="7.0" height="0.0"/>
        <visualSettings lineWidth="2" linePattern="solid"/>
        <points><point x="0.3" y="1.2"/><point x="7.3" y="1.2"/></points>
      </line>
      <frame name="T_HEAVY_G">
        <geometryInfo x="0.2" y="1.5" width="7.0" height="1.0"/>
        <visualSettings lineWidth="2" linePattern="solid"/>
      </frame>
      <frame name="T_HAIR_G">
        <geometryInfo x="0.2" y="2.6" width="7.0" height="1.0"/>
        <visualSettings linePattern="solid"/>
      </frame>
      <frame name="T_LEFTOVER_G">
        <geometryInfo x="0.2" y="3.7" width="7.0" height="1.0"/>
        <visualSettings lineWidth="4"/>
      </frame>
      <frame name="T_DOTTED_G">
        <geometryInfo x="0.2" y="4.8" width="7.0" height="1.0"/>
        <visualSettings lineWidth="2" linePattern="solid" dash="dot"/>
      </frame>
      <repeatingFrame name="R_R" source="G_R" printDirection="down">
        <geometryInfo x="0.26" y="6.0" width="7.0" height="0.25"/>
        <generalLayout verticalElasticity="variable"/>
        <field name="F_L" source="LBL" alignment="start">
          <font face="Arial" size="10"/>
          <geometryInfo x="0.3" y="6.0" width="4.0" height="0.19"/>
        </field>
      </repeatingFrame>
    </body>
  </section>
  </layout>
</report>
"""


def _parsed():
    """(report, {rule name: LayoutField}, {frame name: LayoutGroup})."""
    rep = parse_oracle_xml(_DASH_XML.encode("utf-8"))
    rules, frames = {}, {}

    def _walk(g):
        nm = (getattr(g, "name", "") or "")
        if nm:
            frames[nm] = g
        for f in (getattr(g, "fields", None) or []):
            if (getattr(f, "kind", "") or "") == "line":
                rules[getattr(f, "name", "")] = f
        for c in (getattr(g, "children", None) or []):
            _walk(c)
    for lg in rep.layout:
        _walk(lg)
    return rep, rules, frames


# ---------------------------------------------------------------------------
# parser: the declaration must survive
# ---------------------------------------------------------------------------

def test_declared_dash_reaches_the_model():
    _rep, rules, frames = _parsed()
    assert rules["B_DOTTED"].line_dash == "dot"
    assert rules["B_LONGDASH"].line_dash == "longDash"
    # nothing declared == a continuous stroke, never an invented pattern
    assert rules["B_PLAIN"].line_dash == ""
    # frames carry the same declaration
    assert frames["T_DOTTED_G"].line_dash == "dot"
    assert frames["T_HEAVY_G"].line_dash == ""


# ---------------------------------------------------------------------------
# the token -> RDL BorderStyle mapping
# ---------------------------------------------------------------------------

def test_dash_token_maps_to_the_stroke_pattern():
    assert R._declared_dash_style("dot") == "Dotted"
    assert R._declared_dash_style("doubleDot") == "Dotted"
    # anything naming a dash strokes Dashed (dashDot is mostly dashes)
    assert R._declared_dash_style("dash") == "Dashed"
    assert R._declared_dash_style("longDash") == "Dashed"
    assert R._declared_dash_style("dashDot") == "Dashed"
    # an UNDECLARED / explicitly continuous stroke is never patterned
    for quiet in ("", None, "solid", "none", "transparent", 0):
        assert R._declared_dash_style(quiet) == "Solid"


# ---------------------------------------------------------------------------
# emitter: a dashed rule is a real stroke, at the declared weight
# ---------------------------------------------------------------------------

def _line_border(el):
    ln = el.find(R._q("Line"))
    assert ln is not None, "a dashed rule must be a real <Line> stroke"
    bd = ln.find(R._q("Style")).find(R._q("Border"))
    return (bd.find(R._q("Style")).text,
            bd.find(R._q("Width")).text,
            bd.find(R._q("Color")).text)


def test_declared_dash_rule_strokes_dotted_at_the_declared_weight():
    rep, rules, _frames = _parsed()
    items = ET.Element(R._q("ReportItems"))
    drawn, _ = R._emit_field_textbox(items, "G_Dot", "", rules["B_DOTTED"],
                                     0.0, 0.0, 7.5, 9.0, rep, set())
    assert drawn
    style, width, _ink = _line_border(items)
    # the DASH is the ink: a solid bar paints ~2x the truth's stroke
    assert style == "Dotted"
    # the DECLARED lineWidth maps 1:1 to points (truth strokes it at 2.0pt)
    assert width == "2pt"
    # and it must NOT also be painted as a filled bar (that would restore
    # the whole solid-ink error the dash removes)
    assert items.find(R._q("Rectangle")) is None


def test_declared_longdash_rule_strokes_dashed():
    rep, rules, _frames = _parsed()
    items = ET.Element(R._q("ReportItems"))
    R._emit_field_textbox(items, "G_LD", "", rules["B_LONGDASH"],
                          0.0, 0.0, 7.5, 9.0, rep, set())
    style, width, _ink = _line_border(items)
    assert style == "Dashed"
    assert width == "1pt"


def test_undashed_rule_keeps_its_solid_emission():
    """No declaration, no pattern -- the dash path must not capture rules
    that declare none (they stay the continuous bar Oracle prints)."""
    rep, rules, _frames = _parsed()
    items = ET.Element(R._q("ReportItems"))
    R._emit_field_textbox(items, "G_Plain", "", rules["B_PLAIN"],
                          0.0, 0.0, 7.5, 9.0, rep, set())
    assert items.find(R._q("Line")) is None
    rect = items.find(R._q("Rectangle"))
    assert rect is not None
    bstyle = rect.find(R._q("Style")).find(R._q("Border")).find(R._q("Style"))
    assert bstyle.text == "None"


def test_declared_rule_emitter_carries_the_dash_through():
    """The page-band / declared-rule path shares the dialect."""
    _rep, rules, _frames = _parsed()
    items = ET.Element(R._q("ReportItems"))
    R._emit_declared_rule(items, rules["B_DOTTED"])
    style, width, _ink = _line_border(items)
    assert style == "Dotted"
    assert width == "2pt"


# ---------------------------------------------------------------------------
# emitter: a bordered FRAME strokes its DECLARED weight, never a house 1pt
# ---------------------------------------------------------------------------

def _frame_border(rep, group):
    items = ET.Element(R._q("ReportItems"))
    R._emit_frame_rect(items, group, 0.0, 0.0, 7.5, rep, set(), "T", [0])
    rect = items.find(R._q("Rectangle"))
    assert rect is not None
    bd = rect.find(R._q("Style")).find(R._q("Border"))
    return bd.find(R._q("Style")).text, bd.find(R._q("Width")).text


def test_frame_edge_strokes_the_declared_line_width():
    rep, _rules, frames = _parsed()
    # DECLARED lineWidth -> 1:1 points (the truth PDF strokes these at 2.0pt;
    # the old flat house weight painted exactly half that ink)
    style, width = _frame_border(rep, frames["T_HEAVY_G"])
    assert width == "2pt", "a declared lineWidth must stroke 1:1"
    assert style == "Solid"


def test_frame_edge_without_a_declared_width_is_the_device_hairline():
    rep, _rules, frames = _parsed()
    _style, width = _frame_border(rep, frames["T_HAIR_G"])
    # the truth strokes a solid-linePattern frame with no lineWidth at
    # width 0.0 -- the thinnest stroke RDL can express, never 1pt
    assert width == HAIRLINE
    assert float(width[:-2]) < 1.0


def test_frame_line_width_without_a_pattern_is_not_a_weight():
    """A lineWidth with no linePattern draws no stroke at all in the truth
    exports (an invoice declares lineWidth="4" on two pattern-less frames
    and its PDF holds no 4.0pt stroke anywhere), so it must never be
    promoted into a heavy edge."""
    rep, _rules, frames = _parsed()
    _style, width = _frame_border(rep, frames["T_LEFTOVER_G"])
    assert width == HAIRLINE
    assert float(width[:-2]) <= 1.0


def test_frame_edge_carries_a_declared_dash():
    rep, _rules, frames = _parsed()
    style, width = _frame_border(rep, frames["T_DOTTED_G"])
    assert style == "Dotted"
    assert width == "2pt"


# ---------------------------------------------------------------------------
# whole-pipeline contract: no emitted stroke may invent a house weight
# ---------------------------------------------------------------------------

def test_no_declared_edge_is_emitted_at_an_undeclared_house_weight():
    """Every border weight in the RDL must be either a DECLARED lineWidth
    or the device hairline -- the flat 1pt that used to be stamped on every
    bordered frame is not a declaration and must not reappear."""
    from converter import convert
    rdl = convert(_DASH_XML.encode("utf-8"))["rdl_xml"]
    root = ET.fromstring(rdl)
    declared = {"2pt", "1pt", HAIRLINE}
    frame_widths = set()
    for rect in root.iter(R._q("Rectangle")):
        st = rect.find(R._q("Style"))
        if st is None:
            continue
        bd = st.find(R._q("Border"))
        if bd is None:
            continue
        w = bd.find(R._q("Width"))
        if w is not None and w.text:
            frame_widths.add(w.text)
    assert frame_widths <= declared, (
        f"undeclared stroke weights emitted: {frame_widths - declared}")
    # the fixture's heavy frame really is in there at its declared weight
    assert "2pt" in frame_widths
