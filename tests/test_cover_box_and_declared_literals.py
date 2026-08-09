"""DECLARED COVER GEOMETRY, FIXED-ELASTICITY CLIPPING, DECLARED LITERALS.

Four dialect rules, each measured against an Oracle-rendered truth page and
each previously wrong in a way that no other rail could see:

1. A section's ``<body><location x=>`` is that SECTION's own body origin on
   the paper. The RDL has ONE page origin (the main section's, emitted as
   LeftMargin), so a header/cover section whose origin differs prints that
   difference as extra Left inside the body. The export omits the axis whose
   origin is Oracle's default -- and Oracle's default is 0.5in, the same
   inset _page_left_margin_for already applies to an undeclared main body.
   Truth-measured on two reports whose MAIN bodies sit at different origins
   (0.25in and 0.85413in) and whose header bodies both omit ``x``: both
   render their header objects at declared_x + 0.5in.

2. A DECLARED cover frame is the frame: its declared height is the printed
   rectangle's height. Ending the rectangle at the last caption instead
   shortened a visible bordered box by 0.45in on the truth-paired report
   whose cover box declares 5.9375in of height but only fills ~5.2in of it.

3. Oracle's default vertical elasticity is FIXED: the box stays its declared
   height and what does not fit is CLIPPED there. Truth-measured on one
   report's two multi-line 11pt captions: three declared lines print as TWO
   in a 0.58337in box and as THREE in a 0.68750in box. In RDL that contract
   is declared Height + CanGrow=false; the converter does NOT drop declared
   lines itself, because Oracle's leading is font-dependent (see the note
   above _static_text_overflows_box) and guessing the cut point loses
   content.

4. A declared CDATA literal's INTERIOR whitespace is the author's ink.
   Collapsing every whitespace run (the old `" ".join(text.split())`) ate a
   declared double space that the Oracle page prints as a visibly wider gap.
   Only the exporter's pretty-print runs -- the ones carrying a newline, plus
   the leading/trailing indentation -- are normalized away.

Synthetic fixtures only -- no client data.
"""
from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402
from converter.generators.rdl import _declared_literal  # noqa: E402

# Declared geometry of the synthetic source, in inches.
MAIN_ORIGIN_X = 0.25000        # <section main><body><location x=>
COVER_X, COVER_Y = 0.68750, 0.06250
COVER_W, COVER_H = 6.18750, 5.93750
ORACLE_DEFAULT_ORIGIN = 0.5    # the origin the header body omits
TITLE_X, TITLE_W = 1.70837, 4.33337
FIXED_TITLE_H = 0.58337        # 11pt: room for 2 of the 3 declared lines
GROWN_TITLE_H = 0.58337        # same box, but declared elastic
TITLE_LINES = ("Regional Widget Stewardship Detail Sheet",
               "Bureau of Widget Standards",
               "Widget Stewardship Unit")
CARD_LABEL = "Docket  Number:"   # TWO declared spaces


def _report_xml() -> bytes:
    """A report whose header section declares a bordered cover box and whose
    main section declares a per-record card carrying a double-spaced label."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<report name="WIDGET_STEWARDSHIP" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q_MAIN">
      <select canParse="no"><![CDATA[SELECT DOCKET_NO, HOLDER_NM, REGION_NM FROM WIDGET_DOCKET]]></select>
      <group name="G_REGION">
        <dataItem name="REGION_NM" datatype="vchar2" columnOrder="1" defaultLabel="Region"/>
        <group name="G_DOCKET">
          <dataItem name="DOCKET_NO" datatype="number" columnOrder="2" defaultLabel="Docket"/>
          <dataItem name="HOLDER_NM" datatype="vchar2" columnOrder="3" defaultLabel="Holder"/>
          <group name="G_ACTION">
            <dataItem name="ACTION_NM" datatype="vchar2" columnOrder="4" defaultLabel="Action"/>
            <dataItem name="ACTION_DT" datatype="date" columnOrder="5" defaultLabel="Action Date"/>
          </group>
        </group>
      </group>
    </dataSource>
  </data>
  <layout>
  <section name="header" orientation="portrait">
    <body>
      <roundedRectangle name="B_COVER">
        <geometryInfo x="{COVER_X:.5f}" y="{COVER_Y:.5f}"
         width="{COVER_W:.5f}" height="{COVER_H:.5f}"/>
        <visualSettings linePattern="solid"/>
        <points>
          <point x="{COVER_X:.5f}" y="{COVER_Y:.5f}"/>
          <point x="{COVER_W:.5f}" y="{COVER_H:.5f}"/>
          <point x="0.13794" y="0.13794"/>
        </points>
      </roundedRectangle>
      <text name="B_FIXED_TITLE" minWidowLines="1">
        <textSettings justify="center" spacing="0"/>
        <geometryInfo x="{TITLE_X:.5f}" y="0.58337"
         width="{TITLE_W:.5f}" height="{FIXED_TITLE_H:.5f}"/>
        <textSegment><font face="Verdana" size="11" bold="yes"/>
          <string><![CDATA[{TITLE_LINES[0]}
{TITLE_LINES[1]}
{TITLE_LINES[2]}]]></string></textSegment>
      </text>
      <text name="B_GROWN_TITLE" minWidowLines="1">
        <textSettings justify="center" spacing="0"/>
        <geometryInfo x="{TITLE_X:.5f}" y="1.60000"
         width="{TITLE_W:.5f}" height="{GROWN_TITLE_H:.5f}"/>
        <generalLayout verticalElasticity="variable"/>
        <textSegment><font face="Verdana" size="11" bold="yes"/>
          <string><![CDATA[{TITLE_LINES[0]}
{TITLE_LINES[1]}
{TITLE_LINES[2]}]]></string></textSegment>
      </text>
      <text name="B_CRITERIA_HDR" minWidowLines="1">
        <textSettings justify="center" spacing="0"/>
        <geometryInfo x="2.43750" y="2.50000" width="2.87500" height="0.25000"/>
        <textSegment><font face="Arial" size="12" bold="yes"/>
          <string><![CDATA[Report Parameters]]></string></textSegment>
      </text>
      <text name="B_REGION_LBL" minWidowLines="1">
        <textSettings justify="end" spacing="0"/>
        <geometryInfo x="2.87500" y="3.00000" width="0.68750" height="0.17712"/>
        <textSegment><font face="Arial" size="10" bold="yes"/>
          <string><![CDATA[Region:]]></string></textSegment>
      </text>
      <field name="F_REGION_ECHO" source="REGION_NM" minWidowLines="1"
       spacing="0" alignment="start">
        <font face="Arial" size="10" bold="yes"/>
        <geometryInfo x="3.58594" y="3.00000" width="3.10156" height="0.18750"/>
      </field>
    </body>
  </section>
  <section name="main">
    <body width="8.00000">
      <location x="{MAIN_ORIGIN_X:.5f}"/>
      <frame name="M_G_REGION_GRPFR">
        <geometryInfo x="0.00000" y="0.02136" width="7.93750" height="1.54114"/>
        <generalLayout verticalElasticity="variable"/>
        <repeatingFrame name="R_G_REGION" source="G_REGION"
         printDirection="down" minWidowRecords="1" columnMode="no">
          <geometryInfo x="0.00000" y="0.02136" width="7.93750" height="1.54114"/>
          <generalLayout verticalElasticity="variable"/>
          <field name="F_REGION_NM" source="REGION_NM" minWidowLines="1"
           spacing="0" alignment="start">
            <font face="Arial" size="11" bold="yes"/>
            <geometryInfo x="0.62500" y="0.02136" width="3.81250" height="0.16614"/>
          </field>
          <frame name="M_G_DOCKET_GRPFR">
            <geometryInfo x="0.00000" y="0.25000" width="7.87500" height="1.20000"/>
            <generalLayout verticalElasticity="variable"/>
            <repeatingFrame name="R_G_DOCKET" source="G_DOCKET"
             printDirection="down" minWidowRecords="1" columnMode="no">
              <geometryInfo x="0.00000" y="0.25000" width="7.87500" height="1.20000"/>
              <generalLayout verticalElasticity="variable"/>
              <text name="B_DOCKET_LBL" minWidowLines="1">
                <textSettings spacing="0"/>
                <geometryInfo x="0.06250" y="0.25000" width="1.06250" height="0.18750"/>
                <textSegment><font face="Arial" size="11" bold="yes"/>
                  <string><![CDATA[{CARD_LABEL}]]></string></textSegment>
              </text>
              <field name="F_DOCKET_NO" source="DOCKET_NO" minWidowLines="1"
               spacing="0" alignment="start">
                <font face="Arial" size="11" bold="yes"/>
                <geometryInfo x="1.18750" y="0.25000" width="1.00000" height="0.18750"/>
              </field>
              <text name="B_HOLDER_LBL" minWidowLines="1">
                <textSettings spacing="0"/>
                <geometryInfo x="0.06250" y="0.46875" width="1.06250" height="0.18750"/>
                <textSegment><font face="Arial" size="11" bold="yes"/>
                  <string><![CDATA[Holder:]]></string></textSegment>
              </text>
              <field name="F_HOLDER_NM" source="HOLDER_NM" minWidowLines="1"
               spacing="0" alignment="start">
                <font face="Arial" size="11"/>
                <geometryInfo x="1.18750" y="0.46875" width="3.00000" height="0.18750"/>
              </field>
              <frame name="M_G_ACTION_GRPFR">
                <geometryInfo x="0.00000" y="0.70000" width="7.75000" height="0.50000"/>
                <generalLayout verticalElasticity="variable"/>
                <text name="B_ACTION_HDR" minWidowLines="1">
                  <textSettings spacing="0"/>
                  <geometryInfo x="0.06250" y="0.70000" width="1.50000" height="0.18750"/>
                  <textSegment><font face="Arial" size="9" bold="yes"/>
                    <string><![CDATA[Action Type:]]></string></textSegment>
                </text>
                <text name="B_ACTION_DT_HDR" minWidowLines="1">
                  <textSettings spacing="0"/>
                  <geometryInfo x="4.50000" y="0.70000" width="1.50000" height="0.18750"/>
                  <textSegment><font face="Arial" size="9" bold="yes"/>
                    <string><![CDATA[Action Date:]]></string></textSegment>
                </text>
                <repeatingFrame name="R_G_ACTION" source="G_ACTION"
                 printDirection="down" minWidowRecords="1" columnMode="no">
                  <geometryInfo x="0.00000" y="0.90000" width="7.75000" height="0.25000"/>
                  <generalLayout verticalElasticity="variable"/>
                  <field name="F_ACTION_NM" source="ACTION_NM" minWidowLines="1"
                   spacing="0" alignment="start">
                    <font face="Arial" size="9"/>
                    <geometryInfo x="0.06250" y="0.90000" width="2.50000" height="0.16000"/>
                  </field>
                  <field name="F_ACTION_DT" source="ACTION_DT" minWidowLines="1"
                   formatMask="MM/DD/YYYY" spacing="0" alignment="start">
                    <font face="Arial" size="9"/>
                    <geometryInfo x="4.50000" y="0.90000" width="1.50000" height="0.16000"/>
                  </field>
                </repeatingFrame>
              </frame>
            </repeatingFrame>
          </frame>
        </repeatingFrame>
      </frame>
    </body>
  </section>
  </layout>
</report>""".encode()


def _rdl():
    out = convert(_report_xml())
    assert not out.get("conversion_error"), out.get("conversion_error")
    return out["rdl_xml"]


def _ns(root):
    return root.tag.split("}")[0][1:]


def _cover_rect(rdl: str):
    """(paper_left, top, width, height) of the emitted cover Rectangle."""
    root = ET.fromstring(rdl.encode())
    ns = _ns(root)

    def q(t):
        return f"{{{ns}}}{t}"

    lm = float((root.findtext(".//" + q("LeftMargin")) or "0in")
               .replace("in", ""))
    for rect in root.iter(q("Rectangle")):
        if (rect.get("Name") or "").startswith("Rect_CoverPage"):
            def g(t):
                return float((rect.findtext(q(t)) or "0in").replace("in", ""))
            return (lm + g("Left"), g("Top"), g("Width"), g("Height"))
    raise AssertionError("no cover Rectangle was emitted")


def _textbox_value(rdl: str, needle: str) -> str:
    """The first <Value> carrying ``needle``."""
    for m in re.finditer(r"<Value>(.*?)</Value>", rdl, re.S):
        if needle in m.group(1):
            return m.group(1)
    raise AssertionError(f"no emitted value carries {needle!r}")


# --------------------------------------------------------------------------
# 1 + 2: the declared cover box
# --------------------------------------------------------------------------

def test_cover_box_sits_at_the_header_sections_own_body_origin():
    """The header body omits its origin, so Oracle's 0.5in default applies —
    NOT the main section's 0.25in, which the RDL page margin already spends.
    """
    left, _top, _w, _h = _cover_rect(_rdl())
    want = ORACLE_DEFAULT_ORIGIN + COVER_X
    assert abs(left - want) <= 0.005, (
        f"cover box printed at paper x {left}in against a declared "
        f"{COVER_X}in inside a header body whose origin defaults to "
        f"{ORACLE_DEFAULT_ORIGIN}in (want {want}in). Placing it at the MAIN "
        f"section's origin ({MAIN_ORIGIN_X}in) puts the whole cover — box "
        "and every caption in it — "
        f"{want - MAIN_ORIGIN_X - COVER_X}in left of the Oracle page")


def test_declared_cover_box_keeps_its_declared_height():
    """The box's border is ink; ending it at the last caption shortens it."""
    _left, top, w, h = _cover_rect(_rdl())
    assert abs(top - COVER_Y) <= 0.005, f"cover top {top}in vs {COVER_Y}in"
    assert abs(w - COVER_W) <= 0.005, f"cover width {w}in vs {COVER_W}in"
    assert abs(h - COVER_H) <= 0.005, (
        f"cover box printed {h}in tall against a declared {COVER_H}in — a "
        "declared frame is the frame; the empty space its author left below "
        "the last caption is inside a bordered box the reader sees")


# --------------------------------------------------------------------------
# 3: fixed vertical elasticity CLIPS
# --------------------------------------------------------------------------

def _cover_textbox(rdl: str, needle: str):
    """(Height_in, CanGrow) of the cover Textbox whose value carries
    ``needle``, chosen by DECLARED height so the two same-text copies are
    distinguishable."""
    root = ET.fromstring(rdl.encode())
    ns = _ns(root)

    def q(t):
        return f"{{{ns}}}{t}"

    out = []
    for rect in root.iter(q("Rectangle")):
        if not (rect.get("Name") or "").startswith("Rect_CoverPage"):
            continue
        for tb in rect.iter(q("Textbox")):
            if any(needle in (v.text or "") for v in tb.iter(q("Value"))):
                out.append((float((tb.findtext(q("Height")) or "0in")
                                  .replace("in", "")),
                            (tb.findtext(q("CanGrow")) or "").lower()))
    assert out, f"no cover textbox carries {needle!r}"
    return out


def test_fixed_elasticity_cover_text_keeps_its_declared_box():
    """Oracle's default vertical elasticity is FIXED: the box stays exactly
    its declared height and what does not fit is clipped there.

    Expressed in RDL as declared Height + CanGrow=false. Growing it instead
    printed a line the Oracle page does not: truth-measured, three declared
    11pt lines in a 0.58337in cover box print TWO on the Oracle render.
    """
    boxes = _cover_textbox(_rdl(), TITLE_LINES[0])
    fixed = [b for b in boxes if b[1] == "false"]
    assert fixed, (
        "the cover title that declares NO vertical elasticity was emitted "
        "growable — Oracle's default is fixed, so the box may not outgrow "
        f"its declared {FIXED_TITLE_H}in and print a clipped line")
    assert abs(fixed[0][0] - FIXED_TITLE_H) <= 0.01, (
        f"fixed cover title emitted {fixed[0][0]}in tall against a declared "
        f"{FIXED_TITLE_H}in")


def test_elastic_cover_text_may_still_grow():
    """The no-grow rule is the DECLARATION's, not a blanket one: the same
    three lines in the same-size box declared variable stay growable."""
    boxes = _cover_textbox(_rdl(), TITLE_LINES[0])
    assert any(b[1] == "true" for b in boxes), (
        "the variable-elasticity copy of the title lost its growth — only a "
        "FIXED box is pinned to its declared height")


def test_both_cover_copies_keep_every_declared_line():
    """Neither copy DROPS a declared line here: Oracle's leading is
    font-dependent (1.124-1.196 em for Arial/Helvetica across four truth
    PDFs, 1.364 em ink-measured for Verdana), so no single factor can decide
    which line to cut. Cutting one would be a content-losing guess."""
    rdl = _rdl()
    for val in [m.group(1) for m in
                re.finditer(r"<Value>(.*?)</Value>", rdl, re.S)
                if TITLE_LINES[0] in m.group(1)]:
        assert TITLE_LINES[2] in val, (
            "a declared line was dropped from the emitted value — the "
            "converter does not model Oracle's per-font leading, so it must "
            "not guess a clip point")


# --------------------------------------------------------------------------
# 4: declared literals keep their interior whitespace
# --------------------------------------------------------------------------

def test_declared_double_space_survives_to_the_rdl():
    rdl = _rdl()
    assert CARD_LABEL in rdl, (
        f"the declared literal {CARD_LABEL!r} was emitted with its interior "
        "whitespace collapsed — Oracle prints the declared run verbatim (a "
        "truth screenshot measures the wider ink gap)")


def test_declared_literal_normalizes_only_the_exporters_whitespace():
    """Unit leg: pretty-print runs go, author runs stay."""
    assert _declared_literal("\n      Docket  Number:\n      ") \
        == "Docket  Number:"
    assert _declared_literal("Line one\n      Line two") == "Line one Line two"
    assert _declared_literal("  A  B  ") == "A  B"
    assert _declared_literal("") == ""
    assert _declared_literal(None) == ""
