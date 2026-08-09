"""PER-RECORD CARD LADDER: every declared child at its DECLARED y, and the
card/band as tall as the FRAME the source declares.

Root cause this guards (measured on a nested master-detail detail report):
the card row laid its members out on a SYNTHESIZED constant step in two
synthesized columns -- so a record whose declaration steps 0.1953 / 0.1822 /
0.3385 / 0.1822in (a deliberate extra gap before the fourth line) printed a
perfectly uniform ladder, and the row height came from a STEP COUNT
(``0.22 + rows * LINE_H + 0.10``) instead of the record frame's own declared
height.

Two declaration-driven rules restore it:

  * a card is DECLARED as soon as the frame positions its own value boxes --
    it no longer has to also carry standalone caption <text>s -- so every
    such card routes to the declared-geometry emitter and each member lands
    at its own declared x/y/width/height (4dp, per the declared-is-declared
    dialect);
  * the card/band row is as tall as the frame's own declared box (bounded by
    where the record's DETAIL frame is declared to start), with the members'
    extent only a floor. The blank a frame leaves under its last member is
    DECLARED slack, not padding to re-invent.

Negative twins prove both gates are driven by the declaration: a report that
declares no layout at all keeps the synthesized stack, and a frame that
declares no taller box keeps the members' extent.

Synthetic fixtures only -- no client data.
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter.models import (  # noqa: E402
    DataItem, DataQuery, ParsedReport, QueryGroup,
)
from converter.generators.rdl import generate_rdl  # noqa: E402
from converter.parsers.oracle_xml import parse_oracle_xml  # noqa: E402

NS = ("{http://schemas.microsoft.com/sqlserver/reporting/"
      "2008/01/reportdefinition}")

# The record's five declared line tops. The step is deliberately NOT uniform:
# 0.1953 / 0.1822 / 0.3385 / 0.1822 -- an extra 0.1563in gap before the
# fourth line, which a synthesized constant step erases.
CARD_Y0 = 1.2756
LINE_YS = [1.2756, 1.4709, 1.6531, 1.9916, 2.1738]
LINE_H = 0.1822
SRCS = ["OWNER", "LOCATION", "PHONE", "MAILED_TO", "MAIL_CITY"]
DETAIL_Y = 2.9000


def _md_xml(card_frame_h=1.0804):
    """3-level nested master-detail whose MIDDLE record frame declares the
    ladder above. ``card_frame_h`` is the frame's own declared height."""
    flds = "\n".join(
        f'            <field name="F_{s}" source="{s}">'
        f'<geometryInfo x="0.2500" y="{y:.4f}" width="3.2000" '
        f'height="{LINE_H:.4f}"/>'
        f'<font face="Arial" size="10"/></field>'
        for s, y in zip(SRCS, LINE_YS))
    cols = "\n".join(
        f'          <dataItem name="{s}" datatype="vchar2" '
        f'columnOrder="{i + 3}" defaultLabel="{s.title()}"/>'
        for i, s in enumerate(SRCS))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<report name="ND_CARD_LADDER" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q_1">
      <select canParse="no"><![CDATA[SELECT REGION_NM, CASE_NO, OWNER,
        LOCATION, PHONE, MAILED_TO, MAIL_CITY, ACT_KIND, NOTE FROM T]]></select>
      <group name="G_REGION">
        <dataItem name="REGION_NM" datatype="vchar2" columnOrder="1"
                  defaultLabel="Region"/>
        <group name="G_CASE">
          <dataItem name="CASE_NO" datatype="vchar2" columnOrder="2"
                    defaultLabel="Case"/>
{cols}
          <group name="G_ACT">
            <dataItem name="ACT_KIND" datatype="vchar2" columnOrder="9"
                      defaultLabel="Kind"/>
            <dataItem name="NOTE" datatype="vchar2" columnOrder="10"
                      defaultLabel="Note"/>
          </group>
        </group>
      </group>
    </dataSource>
  </data>
  <layout>
    <section name="main" width="8.50000" height="11.00000">
      <body width="7.50000" height="9.00000">
        <location x="0.50000" y="1.00000"/>
        <repeatingFrame name="R_REGION" source="G_REGION" printDirection="down">
          <geometryInfo x="0.00000" y="0.90000" width="7.50000" height="6.00000"/>
          <field name="F_REGION" source="REGION_NM">
            <geometryInfo x="0.0000" y="0.9000" width="3.0000" height="0.1700"/>
            <font face="Arial" size="11" bold="yes"/>
          </field>
          <repeatingFrame name="R_CASE" source="G_CASE" printDirection="down">
            <geometryInfo x="0.00000" y="{CARD_Y0:.4f}" width="7.50000"
                          height="{card_frame_h:.4f}"/>
{flds}
            <repeatingFrame name="R_ACT" source="G_ACT" printDirection="down">
              <geometryInfo x="0.25000" y="{DETAIL_Y:.4f}" width="7.00000"
                            height="0.2000"/>
              <field name="F_ACT" source="ACT_KIND">
                <geometryInfo x="0.2500" y="{DETAIL_Y:.4f}" width="2.0000"
                              height="0.1822"/>
                <font face="Arial" size="9"/>
              </field>
              <field name="F_NOTE" source="NOTE">
                <geometryInfo x="2.3000" y="{DETAIL_Y:.4f}" width="4.5000"
                              height="0.1822"/>
                <font face="Arial" size="9"/>
              </field>
            </repeatingFrame>
          </repeatingFrame>
        </repeatingFrame>
      </body>
    </section>
  </layout>
</report>
"""


def _card_rect(rdl, name="ND_Card0"):
    root = ET.fromstring(rdl)
    for rect in root.iter(NS + "Rectangle"):
        if (rect.get("Name") or "") == name:
            return rect
    return None


def _card_row_height(rdl, name="ND_Card0"):
    root = ET.fromstring(rdl)
    for row in root.iter(NS + "TablixRow"):
        if any((r.get("Name") or "") == name
               for r in row.iter(NS + "Rectangle")):
            return float((row.findtext(NS + "Height") or "0in")
                         .replace("in", ""))
    return None


def _tops(rect):
    out = []
    for tb in rect.iter(NS + "Textbox"):
        t = tb.findtext(NS + "Top")
        if t is not None:
            out.append(float(t.replace("in", "")))
    return sorted(out)


def _rdl(**kw):
    rep = parse_oracle_xml(_md_xml(**kw).encode("utf-8"))
    rdl = generate_rdl(rep)
    ET.fromstring(rdl)          # well-formed
    return rdl


def test_card_members_land_on_their_declared_ladder():
    """Each declared line prints at its declared offset -- the NON-uniform
    declared step survives verbatim."""
    rect = _card_rect(_rdl())
    assert rect is not None, "declared card row missing"
    tops = _tops(rect)
    assert len(tops) == len(LINE_YS)
    declared = [round(y - CARD_Y0, 4) for y in LINE_YS]
    emitted = [round(t - tops[0], 4) for t in tops]
    assert emitted == declared, (declared, emitted)
    # ...and the extra gap before the 4th line is really there
    steps = [round(emitted[i + 1] - emitted[i], 4)
             for i in range(len(emitted) - 1)]
    assert steps == [0.1953, 0.1822, 0.3385, 0.1822], steps
    assert max(steps) - min(steps) > 0.1, "ladder flattened to a constant step"


def test_card_members_keep_their_declared_x_and_width():
    """The declared column, not a synthesized two-column split."""
    rect = _card_rect(_rdl())
    lefts = {tb.findtext(NS + "Left") for tb in rect.iter(NS + "Textbox")}
    widths = {tb.findtext(NS + "Width") for tb in rect.iter(NS + "Textbox")}
    assert lefts == {"0.2500in"}, lefts
    assert widths == {"3.2000in"}, widths


def test_card_row_height_is_the_declared_frame_box():
    """A frame declared TALLER than its members sizes the row: the slack it
    leaves under the last line is declared, not padding."""
    tall = 1.6000          # > members' extent (1.0804) + slack
    h = _card_row_height(_rdl(card_frame_h=tall))
    assert h is not None
    assert abs(h - tall) < 0.0005, h


def test_declared_frame_box_is_bounded_by_the_detail_frame():
    """A frame declared to wrap its own DETAIL cannot claim the detail's
    space: the card ends where the detail frame is declared to start."""
    h = _card_row_height(_rdl(card_frame_h=4.0000))
    assert h is not None
    assert abs(h - (DETAIL_Y - CARD_Y0)) < 0.0005, h


def test_members_extent_is_the_floor_when_frame_declares_less():
    """Negative twin: a frame declared SHORTER than its members keeps the
    members' extent -- the declared box only ever raises the floor."""
    h = _card_row_height(_rdl(card_frame_h=0.4000))
    extent = (LINE_YS[-1] + LINE_H) - CARD_Y0
    assert h is not None
    assert abs(h - (extent + 0.06)) < 0.0005, h


def test_no_declared_layout_keeps_the_synthesized_stack():
    """Negative twin: with NO layout to read there is nothing declared, so
    the synthesized two-column card still stands in."""
    inner = QueryGroup(name="G_ACT", break_col="ACT_KIND",
                       items=[DataItem(name="ACT_KIND"),
                              DataItem(name="NOTE")])
    mid = QueryGroup(name="G_CASE", break_col="CASE_NO",
                     items=[DataItem(name="CASE_NO"),
                            DataItem(name="OWNER"),
                            DataItem(name="LOCATION")],
                     children=[inner])
    outer = QueryGroup(name="G_REGION", break_col="REGION_NM",
                       items=[DataItem(name="REGION_NM")], children=[mid])
    q = DataQuery(name="Q_1", sql="SELECT 1",
                  items=[DataItem(name=n) for n in
                         ("REGION_NM", "CASE_NO", "OWNER", "LOCATION",
                          "ACT_KIND", "NOTE")],
                  groups=[outer])
    rdl = generate_rdl(ParsedReport(name="NDX", dtd_version="9.0",
                                    queries=[q]))
    rect = _card_rect(rdl)
    assert rect is not None
    # synthesized ladder: the constant-step stack, two columns
    lefts = {tb.findtext(NS + "Left") for tb in rect.iter(NS + "Textbox")}
    assert lefts <= {"0.10in", "3.85in"}, lefts


def test_declared_card_still_carries_its_group_subtotal():
    """The declared-geometry card must not lose the group's own declared
    <summary> line (the synthesized card printed it)."""
    xml = _md_xml().replace(
        '<dataItem name="CASE_NO" datatype="vchar2" columnOrder="2"\n'
        '                    defaultLabel="Case"/>',
        '<dataItem name="CASE_NO" datatype="vchar2" columnOrder="2"\n'
        '                    defaultLabel="Case"/>\n'
        '          <summary name="CS_CNT" source="PHONE" function="count"'
        ' defaultLabel="Lines"/>')
    assert "CS_CNT" in xml, "fixture patch did not apply"
    rdl = generate_rdl(parse_oracle_xml(xml.encode("utf-8")))
    assert "Count(Fields!PHONE.Value)" in rdl
    assert "Lines:" in rdl
