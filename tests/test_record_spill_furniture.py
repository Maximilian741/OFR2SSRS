"""RECORD-SPILL class: in-section page-footer furniture must never inflate
the per-record body.

Root cause (truth-measured): Oracle sections carry their own footer band as
a frame parked at the page bottom ("<report>.rdf   Page &PageNumber of
&TotalPages   <date>"). Some exports write the page builtins WITHOUT the
bracket form ("&PageNumber", not "&<PageNumber>"); the furniture test
missed that dialect, the footer frame stayed in the BODY flow at its paper
y (~10.3in), the record row inflated past the printable height, and every
record emitted a near-blank spill page.

Also guarded: the declared margin <line> capping the footer band (the
solid rule the truth prints above the report-name/page/date chrome) must
emit into the PageFooter.

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
from converter.generators.rdl import _is_page_furniture_frame  # noqa: E402

HISTORY_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<report name="APP_HISTORY" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q_HIST">
      <select canParse="no"><![CDATA[SELECT PERSON_NAME, CASE_NO, DECISION FROM CASES]]></select>
      <group name="G_HIST">
        <dataItem name="PERSON_NAME" datatype="vchar2" columnOrder="1" defaultLabel="Person"/>
        <dataItem name="CASE_NO" datatype="vchar2" columnOrder="2" defaultLabel="Case"/>
        <dataItem name="DECISION" datatype="vchar2" columnOrder="3" defaultLabel="Decision"/>
      </group>
    </dataSource>
  </data>
  <layout>
  <section name="main" width="8.50000" height="11.00000">
    <body width="7.50000" height="8.94800">
      <location x="0.50000" y="1.06000"/>
      <repeatingFrame name="R_G_HIST" source="G_HIST" printDirection="down">
        <geometryInfo x="0.00000" y="0.00000" width="7.50000" height="4.30000"/>
        <field name="F_PERSON" source="PERSON_NAME">
          <font face="Arial" size="10" bold="yes"/>
          <geometryInfo x="0.75000" y="0.00000" width="6.70000" height="0.18000"/></field>
        <field name="F_CASE" source="CASE_NO">
          <font face="Arial" size="9"/>
          <geometryInfo x="0.75000" y="0.30000" width="4.50000" height="0.18000"/></field>
        <field name="F_DECISION" source="DECISION">
          <font face="Arial" size="9"/>
          <geometryInfo x="0.75000" y="0.60000" width="6.90000" height="0.18000"/></field>
      </repeatingFrame>
    </body>
    <margin>
      <text name="B_TITLE"><geometryInfo x="2.07000" y="0.48900" width="4.37000" height="0.25000"/>
        <textSegment><font face="Arial" size="14"/><string><![CDATA[Case Decision History]]></string></textSegment></text>
      <frame name="M_G_FOOTER">
        <geometryInfo x="0.49900" y="10.25000" width="7.51000" height="0.30000"/>
        <line name="B_FOOTER_LINE">
          <geometryInfo x="0.51000" y="10.25000" width="7.50000" height="0.00000"/>
          <visualSettings linePattern="solid"/>
        </line>
        <text name="B_Report_Name"><geometryInfo x="0.49900" y="10.37000" width="1.34000" height="0.17700"/>
          <textSegment><font face="Arial" size="8"/><string><![CDATA[APP_HISTORY.rdf]]></string></textSegment></text>
        <text name="B_Page_Num"><geometryInfo x="3.22000" y="10.36000" width="2.05000" height="0.17700"/>
          <textSegment><font face="Arial" size="8"/><string><![CDATA[Page &PageNumber of &TotalPages]]></string></textSegment></text>
        <field name="F_Current_Date" source="CurrentDate" formatMask="MM/DD/RRRR" alignment="end">
          <font face="Arial" size="8"/>
          <geometryInfo x="7.20000" y="10.36000" width="0.80000" height="0.18700"/></field>
      </frame>
    </margin>
  </section>
  </layout>
</report>"""


def _footer_frame(rep):
    def find(g, name):
        if (getattr(g, "name", "") or "") == name:
            return g
        for c in (getattr(g, "children", None) or []):
            r = find(c, name)
            if r is not None:
                return r
        return None
    for lg in (rep.layout or []):
        g = find(lg, "M_G_FOOTER")
        if g is not None:
            return g
    return None


def test_bare_ampersand_page_tokens_classify_as_furniture():
    rep = parse_oracle_xml(HISTORY_XML)
    g = _footer_frame(rep)
    assert g is not None
    assert _is_page_furniture_frame(g), (
        "a footer frame whose page-number text uses the BARE-ampersand "
        "dialect ('Page &PageNumber of &TotalPages') is page furniture — "
        "missing it leaks the frame into the body flow")


def test_footer_furniture_never_inflates_the_record_row():
    rdl = convert(HISTORY_XML)["rdl_xml"]
    rows = [float(h) for h in re.findall(
        r"<TablixRow>\s*<Height>([\d.]+)in</Height>", rdl)]
    assert rows, "expected a record row"
    # The record content extent is ~4.3in; a leaked footer frame at paper
    # y=10.25 inflates the row past 10in and spills a near-blank page per
    # record.
    assert max(rows) < 8.0, (
        f"record row {max(rows)}in — the in-section footer band leaked "
        "into the body flow (spill-page class)")


def test_declared_footer_rule_emits_in_page_footer():
    rdl = convert(HISTORY_XML)["rdl_xml"]
    assert "<PageFooter>" in rdl, "expected a page footer band"
    pf = rdl.split("<PageFooter>")[-1].split("</PageFooter>")[0]
    assert re.search(r"<Line Name=", pf), (
        "the declared margin <line> capping the footer band (linePattern="
        "solid) must emit as a rule in the PageFooter — the truth prints "
        "it above the report-name/page/date chrome")


def test_transparent_margin_line_does_not_emit():
    # Dialect gate: linePattern transparent/absent draws nothing.
    xml = HISTORY_XML.replace(b'<visualSettings linePattern="solid"/>',
                              b'<visualSettings linePattern="transparent"/>')
    rdl = convert(xml)["rdl_xml"]
    pf = rdl.split("<PageFooter>")[-1].split("</PageFooter>")[0] \
        if "<PageFooter>" in rdl else ""
    assert not re.search(r"<Line Name=", pf), (
        "a transparent-pattern margin line must not draw (dialect gate)")
