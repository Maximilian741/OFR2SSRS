"""
Drawn-rule INK guards (declared stroke color + Oracle's device default).

Oracle writes a drawn rule that declares no ``lineWidth`` to the export
device as a ZERO-WIDTH stroke, and the device paints that at ~20% ink: the
rule measures (204,204,204) in the exported PDFs at every rendering
resolution. Three truth exports were sampled at 72/150/300 dpi and all three
agree -- a summary export's footer rule, a history export's record-separator
and footer rules, and a ledger export's band-edge rules all hold at
(204,204,204) (or the pair (221,221,221)+(238,238,238) = the same 51/255 of
ink when the stroke straddles two pixel rows).

The same exports stroke SOLID BLACK wherever a ``lineWidth`` IS declared
(0.48 / 0.96 / 1 / 2 pt strokes), so the light ink belongs specifically to
the zero-width device hairline -- it is not a global rule color.

A renderer that only supports a real stroke width cannot reproduce a
zero-width stroke, so an undeclared-color hairline must carry that measured
gray. Emitting a flat dark default instead printed those rules near-black at
print resolution (measured: (118,118,118) at 150 dpi for a 0.25pt black
rule, (33,33,33) at 300 dpi for a filled black bar).
"""
import xml.etree.ElementTree as ET

from converter.generators import rdl as R
from converter.parsers.oracle_colors import (
    DEFAULT_LINE_COLOR,
    DEVICE_HAIRLINE_COLOR,
    rule_color,
)
from converter.parsers.oracle_xml import parse_oracle_xml


# The ink the truth exports measure for a zero-width device hairline.
TRUTH_HAIRLINE_RGB = (204, 204, 204)

# A declared stroke color used by the fixture, and what the resolver makes
# of it (Oracle's "darkblue" is the 50%-scale primary = navy).
DECLARED_TOKEN = "darkblue"
DECLARED_INK = "#000080"


def _rgb(hexc):
    h = (hexc or "").lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _is_near_black(hexc, threshold=96):
    """True when an ink would print as a heavy dark rule."""
    return max(_rgb(hexc)) < threshold


_RULE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<report name="RULEINK" DTDVersion="9.0.2.0.10">
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
      <line name="B_HAIR" arrow="none">
        <geometryInfo x="0.3" y="0.8" width="7.0" height="0.0"/>
        <visualSettings linePattern="solid"/>
        <points><point x="0.3" y="0.8"/><point x="7.3" y="0.8"/></points>
      </line>
      <line name="B_HEAVY" arrow="none">
        <geometryInfo x="0.3" y="1.0" width="7.0" height="0.0"/>
        <visualSettings lineWidth="2" linePattern="solid"/>
        <points><point x="0.3" y="1.0"/><point x="7.3" y="1.0"/></points>
      </line>
      <line name="B_TINTED" arrow="none">
        <geometryInfo x="0.3" y="1.2" width="7.0" height="0.0"/>
        <visualSettings linePattern="solid" lineForegroundColor="%s"/>
        <points><point x="0.3" y="1.2"/><point x="7.3" y="1.2"/></points>
      </line>
      <repeatingFrame name="R_R" source="G_R" printDirection="down">
        <geometryInfo x="0.26" y="1.5" width="7.0" height="0.25"/>
        <generalLayout verticalElasticity="variable"/>
        <field name="F_L" source="LBL" alignment="start">
          <font face="Arial" size="10"/>
          <geometryInfo x="0.3" y="1.5" width="4.0" height="0.19"/>
        </field>
      </repeatingFrame>
    </body>
  </section>
  </layout>
</report>
""" % DECLARED_TOKEN


def _parsed_rules():
    """{name: LayoutField} for the three declared <line> objects."""
    rep = parse_oracle_xml(_RULE_XML.encode("utf-8"))
    out = {}

    def _walk(g):
        for f in (getattr(g, "fields", None) or []):
            if (getattr(f, "kind", "") or "") == "line":
                out[getattr(f, "name", "")] = f
        for c in (getattr(g, "children", None) or []):
            _walk(c)
    for lg in rep.layout:
        _walk(lg)
    return rep, out


# ---------------------------------------------------------------------------
# resolver contract
# ---------------------------------------------------------------------------

def test_device_hairline_ink_matches_the_measured_truth():
    assert _rgb(DEVICE_HAIRLINE_COLOR) == TRUTH_HAIRLINE_RGB
    assert not _is_near_black(DEVICE_HAIRLINE_COLOR)


def test_rule_color_declared_token_always_wins():
    # a declared color is honoured whatever the width declaration says
    assert rule_color(DECLARED_TOKEN, width_declared=False) == DECLARED_INK
    assert rule_color(DECLARED_TOKEN, width_declared=True) == DECLARED_INK
    # an already-resolved hex passes through
    assert rule_color("#123456", width_declared=False) == "#123456"


def test_rule_color_default_follows_the_width_declaration():
    # undeclared width == the zero-width device hairline -> measured gray
    assert rule_color("", width_declared=False) == DEVICE_HAIRLINE_COLOR
    assert rule_color(None, width_declared=False) == DEVICE_HAIRLINE_COLOR
    # a DECLARED width is a real stroke -> the truth prints those black
    assert rule_color("", width_declared=True) == DEFAULT_LINE_COLOR
    # unresolvable tokens fall back the same way (never silently dark)
    assert rule_color("transparent", width_declared=False) == \
        DEVICE_HAIRLINE_COLOR


# ---------------------------------------------------------------------------
# parser contract
# ---------------------------------------------------------------------------

def test_parsed_rule_ink_follows_declarations():
    _rep, rules = _parsed_rules()
    assert set(rules) == {"B_HAIR", "B_HEAVY", "B_TINTED"}
    # no color, no width -> the device hairline the truth measures
    assert rules["B_HAIR"].border_color == DEVICE_HAIRLINE_COLOR
    assert not _is_near_black(rules["B_HAIR"].border_color)
    # declared width, no color -> the solid black the truth prints
    assert rules["B_HEAVY"].border_color == DEFAULT_LINE_COLOR
    # declared color wins outright
    assert rules["B_TINTED"].border_color == DECLARED_INK


# ---------------------------------------------------------------------------
# emitter contract
# ---------------------------------------------------------------------------

def _color_of(el):
    return "".join(c.text or "" for c in el.iter(R._q("Color")))


def test_emit_rule_line_defaults_to_the_hairline_ink():
    parent = ET.Element(R._q("ReportItems"))
    R._emit_rule_line(parent, "Rule_NoColor", 1.0, 0.5, 7.0, "")
    assert _color_of(parent) == DEVICE_HAIRLINE_COLOR
    assert not _is_near_black(_color_of(parent))
    # an explicit color is never overridden
    parent2 = ET.Element(R._q("ReportItems"))
    R._emit_rule_line(parent2, "Rule_Navy", 1.0, 0.5, 7.0, DECLARED_INK)
    assert _color_of(parent2) == DECLARED_INK


def test_page_chrome_rules_carry_declared_and_default_ink():
    rep, rules = _parsed_rules()
    items = ET.Element(R._q("ReportItems"))
    R._emit_margin_chrome(items, list(rules.values()), rep, 0.0, "Ftr")
    inks = [_color_of(ln) for ln in items.iter(R._q("Line"))]
    assert len(inks) == 3, "every declared margin rule must be drawn"
    assert DEVICE_HAIRLINE_COLOR in inks, (
        "an undeclared-color hairline must print the measured device gray")
    assert DECLARED_INK in inks, "a declared stroke color must survive"
    assert DEFAULT_LINE_COLOR in inks, (
        "a declared-width stroke keeps the solid black the truth prints")


def _uncolored(lf, width_pt):
    """Copy of a parsed rule with its ink stripped -- the state an emitter
    sees whenever a stroke color is absent or fails to resolve."""
    import copy
    out = copy.copy(lf)
    out.border_color = ""
    out.border_width = width_pt
    return out


def test_chrome_emitter_supplies_the_default_ink_itself():
    # defence in depth: the emitters must not fall back to a dark ink even
    # when an uncolored rule reaches them (an unresolvable stroke token
    # resolves to "" and would otherwise print near-black).
    rep, rules = _parsed_rules()
    base = rules["B_HAIR"]
    items = ET.Element(R._q("ReportItems"))
    R._emit_margin_chrome(items, [_uncolored(base, 0.0)], rep, 0.0, "F1")
    assert _color_of(items) == DEVICE_HAIRLINE_COLOR
    assert not _is_near_black(_color_of(items))
    heavy = ET.Element(R._q("ReportItems"))
    R._emit_margin_chrome(heavy, [_uncolored(base, 2.0)], rep, 0.0, "F2")
    assert _color_of(heavy) == DEFAULT_LINE_COLOR


def test_body_emitter_supplies_the_default_ink_itself():
    rep, rules = _parsed_rules()
    base = rules["B_HAIR"]
    for width_pt, expect in ((0.0, DEVICE_HAIRLINE_COLOR),
                             (2.0, DEFAULT_LINE_COLOR)):
        parent = ET.Element(R._q("ReportItems"))
        drawn, _ = R._emit_field_textbox(
            parent, f"G_Bare_{width_pt:g}", "", _uncolored(base, width_pt),
            0.0, 0.0, 7.5, 9.0, rep, set())
        assert drawn
        bg = "".join(c.text or ""
                     for c in parent.iter(R._q("BackgroundColor")))
        assert bg == expect
    assert not _is_near_black(DEVICE_HAIRLINE_COLOR)


def test_bordered_frame_uses_its_declared_edge_color():
    # a frame that declares a stroke color must paint THAT color; with none
    # declared its 1pt border keeps the solid black the truth prints.
    boxed = _RULE_XML.replace(
        '<generalLayout verticalElasticity="variable"/>',
        '<generalLayout verticalElasticity="variable"/>'
        f'<visualSettings linePattern="solid" '
        f'lineForegroundColor="{DECLARED_TOKEN}"/>', 1)
    rep = parse_oracle_xml(boxed.encode("utf-8"))
    frame = None

    def _walk(g):
        nonlocal frame
        if (getattr(g, "border_width", 0) or 0) > 0 and frame is None:
            frame = g
        for c in (getattr(g, "children", None) or []):
            _walk(c)
    for lg in rep.layout:
        _walk(lg)
    assert frame is not None and frame.border_color == DECLARED_INK

    def _frame_border_ink(parent):
        rect = parent.find(R._q("Rectangle"))
        border = rect.find(R._q("Style")).find(R._q("Border"))
        return border.find(R._q("Color")).text

    items = ET.Element(R._q("ReportItems"))
    R._emit_frame_rect(items, frame, 0.0, 0.0, 7.5, rep, set(), "T", [0])
    assert _frame_border_ink(items) == DECLARED_INK, (
        "a bordered frame must stroke in its DECLARED color")

    frame.border_color = ""
    plain = ET.Element(R._q("ReportItems"))
    R._emit_frame_rect(plain, frame, 0.0, 0.0, 7.5, rep, set(), "T", [0])
    assert _frame_border_ink(plain) == DEFAULT_LINE_COLOR


def test_body_rule_graphics_carry_declared_and_default_ink():
    rep, rules = _parsed_rules()
    inks = {}
    for nm, lf in rules.items():
        parent = ET.Element(R._q("ReportItems"))
        drawn, _ = R._emit_field_textbox(parent, f"G_{nm}", "", lf,
                                         0.0, 0.0, 7.5, 9.0, rep, set())
        assert drawn, "a declared rule must be drawn in the body"
        bg = "".join(c.text or ""
                     for c in parent.iter(R._q("BackgroundColor")))
        inks[nm] = bg
    assert inks["B_HAIR"] == DEVICE_HAIRLINE_COLOR
    assert not _is_near_black(inks["B_HAIR"])
    assert inks["B_HEAVY"] == DEFAULT_LINE_COLOR
    assert inks["B_TINTED"] == DECLARED_INK
