"""
DECLARED box edges on synthesized record/card rectangles.

Oracle's ``linePattern`` is the paint gate for an object's box and
``hideXBorder`` names which of the four edges of that box actually
stroke.  A nested master-detail record rectangle stands for the record's
own repeating frame, so the ONLY border it may carry is the one that
frame declares.

TRUTH MEASUREMENT that drove these guards (nested master-detail exports,
engine-rendered, rule inventory by y/width/ink):

  * a record frame declaring
    ``linePattern="solid" hideLeftBorder="yes" hideRightBorder="yes"
    hideTopBorder="yes" hideBottomBorder="yes"`` still had a full-width
    rule painted under every record, because the emitter wrote a
    hardcoded ``<BottomBorder><Color>#777777</Color>`` (rendered gray
    119) regardless of the declaration;
  * a report whose repeating frames declare NO ``linePattern`` at all
    got the same invented full-width rule;
  * a frame declaring only ``linePattern="solid"`` (no ``lineWidth``,
    no ``lineForegroundColor``) must stroke Oracle's device hairline
    (~20% ink), not a house mid-gray and not solid black -- the
    ``border_width`` the parser defaults to 1.0 is a "this edge draws"
    flag, not a declared width.

So: a border colour or weight may never be a house literal.  It comes
from the declaration (``lineForegroundColor`` / edge colour +
``lineWidth``), or from the settled device-hairline rule, or the edge
does not paint at all.
"""
import re
import xml.etree.ElementTree as ET

import pytest

from converter.generators.rdl import _resolve_palette, generate_rdl
from converter.parsers.oracle_colors import (
    DEVICE_HAIRLINE_COLOR, resolve_color,
)
from converter.parsers.oracle_xml import parse_oracle_xml


_EDGE_TAGS = ("Border", "TopBorder", "BottomBorder",
              "LeftBorder", "RightBorder")

# The record frame's <visualSettings>, per case. Everything the emitter
# is allowed to paint has to be readable straight off these strings.
_REC_VS = {
    # nothing declared at all -> the source draws no box
    "none": "",
    # a box is named, then every one of its edges is hidden
    "all_hidden": (
        '<visualSettings fillPattern="transparent" linePattern="solid"'
        ' lineForegroundColor="gray4" hideLeftBorder="yes"'
        ' hideRightBorder="yes" hideTopBorder="yes"'
        ' hideBottomBorder="yes"/>'),
    # the per-row underline idiom: only the bottom edge survives
    "bottom_only": (
        '<visualSettings fillPattern="transparent" linePattern="solid"'
        ' lineForegroundColor="gray12" hideLeftBorder="yes"'
        ' hideRightBorder="yes" hideTopBorder="yes"/>'),
    # same, with a DECLARED stroke width and colour
    "bottom_2pt": (
        '<visualSettings fillPattern="transparent" linePattern="solid"'
        ' lineWidth="2" lineForegroundColor="darkblue"'
        ' hideLeftBorder="yes" hideRightBorder="yes"'
        ' hideTopBorder="yes"/>'),
    # a box that draws on three edges but hides exactly the one the
    # record rectangle would otherwise stroke
    "bottom_hidden": (
        '<visualSettings fillPattern="transparent" linePattern="solid"'
        ' lineForegroundColor="gray12" hideBottomBorder="yes"/>'),
    # a box that draws, with no colour and no width declared
    "undeclared_stroke": '<visualSettings linePattern="solid"/>',
}


def _xml(rec_vs="none"):
    """Two-level nested master->detail, with the record frame's declared
    box under test. Nothing report-specific: synthetic names only."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<report name="EDGE_DECL" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q_MAIN">
      <select><![CDATA[SELECT MVAL, ACOL, BCOL FROM T]]></select>
      <group name="G_MASTER">
        <dataItem name="MVAL" datatype="vchar2" width="30" columnFlags="1"
         defaultLabel="Mval">
          <dataDescriptor expression="MVAL" order="1" width="30"/>
        </dataItem>
      </group>
      <group name="G_DETAIL">
        <dataItem name="ACOL" datatype="vchar2" width="30" columnFlags="1"
         defaultLabel="Acol">
          <dataDescriptor expression="ACOL" order="2" width="30"/>
        </dataItem>
        <dataItem name="BCOL" datatype="vchar2" width="30"
         defaultLabel="Bcol">
          <dataDescriptor expression="BCOL" order="3" width="30"/>
        </dataItem>
      </group>
    </dataSource>
  </data>
  <layout>
  <section name="main">
    <body height="9.6">
      <repeatingFrame name="R_M" source="G_MASTER" printDirection="down">
        <geometryInfo x="0.0" y="0.0" width="7.49" height="1.70"/>
        <generalLayout verticalElasticity="variable"/>
        <field name="F_MVAL" source="MVAL" alignment="start">
          <font face="Arial" size="10" bold="yes"/>
          <geometryInfo x="0.01" y="0.052" width="3.0" height="0.19"/>
        </field>
        <repeatingFrame name="R_D" source="G_DETAIL" printDirection="down">
          <geometryInfo x="0.146" y="0.283" width="7.34" height="0.40"/>
          <generalLayout verticalElasticity="variable"/>
          {_REC_VS[rec_vs]}
          <field name="F_A" source="ACOL" alignment="start">
            <font face="Arial" size="10"/>
            <geometryInfo x="0.146" y="0.376" width="4.31" height="0.19"/>
          </field>
          <field name="F_B" source="BCOL" alignment="start">
            <font face="Arial" size="10"/>
            <geometryInfo x="4.552" y="0.376" width="2.80" height="0.19"/>
          </field>
        </repeatingFrame>
      </repeatingFrame>
    </body>
  </section>
  </layout>
</report>
"""


def _rdl(rec_vs="none"):
    rdl = generate_rdl(parse_oracle_xml(_xml(rec_vs).encode("utf-8")))
    ET.fromstring(rdl)  # well-formed
    return rdl


def _record_style(rdl):
    """The record rectangle's own <Style> block (its border elements each
    nest a <Style>Solid</Style> of their own, so the block is taken up to
    the rectangle's <ReportItems> instead of the first close tag)."""
    m = re.search(r'<Rectangle Name="ND_Detail">(.*?)<ReportItems>',
                  rdl, re.S)
    assert m, "the nested-detail record rectangle is missing"
    return m.group(1)


def _edge(style_block, tag):
    """(color, width) of one declared edge in a Style block, or None."""
    m = re.search(r"<%s>(.*?)</%s>" % (tag, tag), style_block, re.S)
    if not m:
        return None
    body = m.group(1)
    st = re.search(r"<Style>([^<]*)</Style>", body)
    if not st or st.group(1).strip().lower() != "solid":
        return None
    col = re.search(r"<Color>([^<]*)</Color>", body)
    wid = re.search(r"<Width>([^<]*)</Width>", body)
    return (col.group(1) if col else None, wid.group(1) if wid else None)


# ---------------------------------------------------------------------------
# (a) hideXBorder="yes" SUPPRESSES that edge
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("case", ["all_hidden", "none"])
def test_record_rect_paints_no_edge_the_source_does_not_declare(case):
    """A frame that hides every edge -- and a frame that declares no box
    at all -- may not get ANY stroke on the record rectangle."""
    style = _record_style(_rdl(case))
    for tag in _EDGE_TAGS:
        assert f"<{tag}>" not in style, (
            f"[{case}] the record rectangle emitted a <{tag}> the "
            f"declaration does not ask for:\n{style}")


def test_a_box_that_draws_still_suppresses_its_one_hidden_edge():
    """hideBottomBorder="yes" on a frame whose other three edges DO draw:
    the record rectangle's bottom rule -- the only edge it emits -- must
    not paint. This is the per-edge gate, distinct from the whole-box
    gate above: the declaration says "box, but no bottom"."""
    style = _record_style(_rdl("bottom_hidden"))
    assert "<BottomBorder>" not in style, (
        f"hideBottomBorder=\"yes\" was ignored:\n{style}")


def test_hidden_edge_declaration_leaves_no_stroke_anywhere_in_the_document():
    """The suppressed edge must not reappear as a standalone Line either:
    an all-hidden record frame means the export draws nothing between
    records, full stop."""
    rdl = _rdl("all_hidden")
    body = re.search(r"<Body>.*?</Body>", rdl, re.S)
    assert body
    assert "<Line " not in body.group(0) and "<Line>" not in body.group(0)


# ---------------------------------------------------------------------------
# (b) the surviving edge takes its INK and WEIGHT from the declaration
# ---------------------------------------------------------------------------
def test_surviving_edge_uses_declared_ink_at_the_device_hairline():
    """hideLeft/Right/Top + a declared lineForegroundColor and NO
    lineWidth: the bottom rule paints, in the DECLARED colour, at
    Oracle's device hairline (settled: an undeclared width is the
    zero-width hairline)."""
    style = _record_style(_rdl("bottom_only"))
    bottom = _edge(style, "BottomBorder")
    assert bottom, f"the declared bottom edge must paint:\n{style}"
    assert bottom[0] == resolve_color("gray12"), bottom
    assert bottom[1] == "0.25pt", bottom
    for tag in ("Border", "TopBorder", "LeftBorder", "RightBorder"):
        assert f"<{tag}>" not in style, tag


def test_declared_width_and_colour_are_honoured_one_to_one():
    """Negative twin of the hairline rule: a DECLARED lineWidth strokes
    1:1 and a declared lineForegroundColor wins over every default."""
    style = _record_style(_rdl("bottom_2pt"))
    bottom = _edge(style, "BottomBorder")
    assert bottom, f"the declared bottom edge must paint:\n{style}"
    assert bottom[0] == resolve_color("darkblue"), bottom
    assert bottom[1] == "2pt", bottom


def test_box_without_declared_colour_or_width_is_the_device_hairline():
    """linePattern alone: the edge draws, but in the settled hairline ink
    -- not a house mid-gray, and not the solid black reserved for a
    stroke whose width IS declared."""
    style = _record_style(_rdl("undeclared_stroke"))
    bottom = _edge(style, "BottomBorder")
    assert bottom, f"a solid linePattern must still paint:\n{style}"
    assert bottom[0] == DEVICE_HAIRLINE_COLOR, bottom
    assert bottom[1] == "0.25pt", bottom


# ---------------------------------------------------------------------------
# (c) NO border colour is hardcoded: the house "rule" ink never strokes
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("case", sorted(_REC_VS))
def test_house_rule_ink_never_reaches_a_border(case):
    """The generator's palette carries a house "rule" tone for legacy
    callers. No emitted border edge may use it: a border colour comes
    from the declaration or from the device-hairline rule."""
    rdl = _rdl(case)
    house = (_resolve_palette(
        parse_oracle_xml(_xml(case).encode("utf-8"))).get("rule") or "")
    assert house, "palette must still expose its legacy rule slot"
    for m in re.finditer(
            r"<(%s)>(.*?)</\1>" % "|".join(_EDGE_TAGS), rdl, re.S):
        assert house.lower() not in m.group(2).lower(), (
            f"[{case}] a border edge painted the hardcoded house rule ink "
            f"{house}:\n{m.group(0)}")
