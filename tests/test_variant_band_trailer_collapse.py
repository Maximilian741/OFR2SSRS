"""DYNAMIC VARIANT-BAND COLLAPSE visits per-record TRAILER sections too.

A record-bearing ``<section name="trailer">`` prints its OWN records after the
main section's (a voucher/invoice run that follows the letters). Its records
get the SAME rule as the main section's: when the emitted stack exceeds the
trailer section's OWN declared body but collapsing its conditional variant
bands would bring it back inside, the bands are rewritten as
hidden-collapsible tablix rows and the row is sized to the declared body —
one sheet per trailer record, as the Oracle truth prints (truth-measured: a
voucher section spread 2 sheets/record against the truth's 1 while the main
letter section printed 1:1).

Also guarded: DEEP bands. Conditional band rects buried at depth 3+ behind
INERT wrapper rectangles (pure ungated geometry: no trigger, no fill, no
painted border) are surfaced by dissolving the wrappers — an ancestor
Rectangle's declared height never shrinks at render (engine-measured), so
without the flatten a deep band can never propagate its reclaim to the
content below it. A LOAD-BEARING wrapper (painted border) must refuse the
flatten: reclaim honestly cannot propagate through it.

Synthetic fixtures only -- no client data.
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402

NS = ("{http://schemas.microsoft.com/sqlserver/reporting/"
      "2008/01/reportdefinition}")

_TRIGGER = """    <function name="{name}">
      <textSource>
      <![CDATA[FUNCTION {upper}
RETURN BOOLEAN IS
BEGIN
\tIF {cond} THEN
\t\tRETURN(TRUE) ;
\tELSE
\t\tRETURN(FALSE) ;
\tEND IF ;
END ; ]]>
      </textSource>
    </function>
"""


def _voucher_xml(trailer_frame_h=12.0, trailer_bottom_y=11.5,
                 wrapper_visual=""):
    """A main letter section whose record FITS its declared body, plus a
    record-bearing trailer section whose design stack EXCEEDS the trailer's
    declared 9in body. The trailer's two conditional bands (2.0in + 1.2in,
    different trigger columns so no static overlay proof exists) sit at
    depth 3 behind an ungated wrapper frame; collapsing them gives the
    overflow back.

    ``trailer_frame_h``/``trailer_bottom_y`` grow the unconditional tail to
    build the control fixture the gate must refuse. ``wrapper_visual``
    plants a painted border on the wrapper to make it LOAD-BEARING."""
    triggers = (_TRIGGER.format(name="f_pay_a_ft", upper="F_PAY_A_FT",
                                cond=":Pay_Kind = 'WIRE'")
                + _TRIGGER.format(name="f_pay_b_ft", upper="F_PAY_B_FT",
                                  cond=":Route_Cd = 'MAIL'"))
    return (f"""<?xml version="1.0" encoding="UTF-8"?>
<report name="NOTICE_WITH_VOUCHER" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q_MAIN">
      <select canParse="no"><![CDATA[SELECT ACCT_NO FROM ACCOUNTS]]></select>
      <group name="G_MAIN">
        <dataItem name="ACCT_NO" datatype="vchar2" columnOrder="1" defaultLabel="Account"/>
      </group>
    </dataSource>
    <dataSource name="Q_PAY">
      <select canParse="no"><![CDATA[SELECT PAY_REF, PAY_KIND, ROUTE_CD FROM PAYMENTS]]></select>
      <group name="G_PAY">
        <dataItem name="PAY_REF" datatype="vchar2" columnOrder="1" defaultLabel="Payment Ref"/>
        <dataItem name="PAY_KIND" datatype="vchar2" columnOrder="2" defaultLabel="Kind"/>
        <dataItem name="ROUTE_CD" datatype="vchar2" columnOrder="3" defaultLabel="Route"/>
      </group>
    </dataSource>
  </data>
  <programUnits>
{triggers}  </programUnits>
  <layout>
  <section name="main" repeatOn="G_MAIN">
    <body width="7.50000" height="9.00000">
      <location x="0.50000" y="0.50000"/>
      <frame name="M_DOC">
        <geometryInfo x="0.00000" y="0.00000" width="7.50000" height="8.00000"/>
        <generalLayout verticalElasticity="variable"/>
        <text name="B_LETTER">
          <geometryInfo x="0.20000" y="0.40000" width="6.00000" height="0.22000"/>
          <textSegment><font face="Arial" size="10"/>
          <string><![CDATA[This determination letter fits one declared sheet]]></string></textSegment></text>
        <field name="F_ACCT_NO" source="ACCT_NO">
          <font face="Arial" size="10"/>
          <geometryInfo x="0.20000" y="7.40000" width="3.00000" height="0.20000"/></field>
      </frame>
    </body>
  </section>
  <section name="trailer" repeatOn="G_PAY">
    <body width="7.50000" height="9.00000">
      <location x="0.50000" y="0.50000"/>
      <frame name="T_DOC">
        <geometryInfo x="0.00000" y="0.00000" width="7.50000" height="{trailer_frame_h:.5f}"/>
        <generalLayout verticalElasticity="variable"/>
        <frame name="T_WRAP">
          <geometryInfo x="0.00000" y="0.00000" width="7.50000" height="{trailer_frame_h - 0.1:.5f}"/>
          {wrapper_visual}
          <frame name="T_VAR_A">
            <geometryInfo x="0.00000" y="0.30000" width="7.50000" height="2.00000"/>
            <advancedLayout formatTrigger="f_pay_a_ft"/>
            <text name="B_PAY_A">
              <geometryInfo x="0.20000" y="0.40000" width="6.00000" height="0.22000"/>
              <textSegment><font face="Arial" size="10"/>
              <string><![CDATA[Wire remittance routing banner]]></string></textSegment></text>
          </frame>
          <frame name="T_VAR_B">
            <geometryInfo x="0.00000" y="2.50000" width="7.50000" height="1.20000"/>
            <advancedLayout formatTrigger="f_pay_b_ft"/>
            <text name="B_PAY_B">
              <geometryInfo x="0.20000" y="2.60000" width="6.00000" height="0.22000"/>
              <textSegment><font face="Arial" size="10"/>
              <string><![CDATA[Postal remittance routing banner]]></string></textSegment></text>
          </frame>
          <frame name="T_BODY">
            <geometryInfo x="0.00000" y="3.90000" width="7.50000" height="{trailer_bottom_y - 3.9:.5f}"/>
            <text name="B_PAY_BODY">
              <geometryInfo x="0.20000" y="4.00000" width="6.00000" height="0.40000"/>
              <textSegment><font face="Arial" size="10"/>
              <string><![CDATA[Return this voucher stub with the payment for proper credit]]></string></textSegment></text>
            <field name="F_PAY_REF" source="PAY_REF">
              <font face="Arial" size="10"/>
              <geometryInfo x="0.20000" y="{trailer_bottom_y - 0.4:.5f}" width="3.00000" height="0.20000"/></field>
          </frame>
        </frame>
      </frame>
    </body>
  </section>
  </layout>
</report>""").encode("utf-8")


def _inches(txt):
    try:
        return float((txt or "0in").replace("in", "").strip())
    except ValueError:
        return 0.0


def _trailer_parts(rdl):
    """(trailer TablixRow height, trailer cell rect, [Band_* tablixes])."""
    root = ET.fromstring(rdl)
    for tab in root.iter(f"{NS}Tablix"):
        if tab.get("Name") != "Tablix_SectionTrailer":
            continue
        row = next(iter(tab.iter(f"{NS}TablixRow")))
        row_h = _inches(row.findtext(f"{NS}Height"))
        cell = next(iter(tab.iter(f"{NS}CellContents")))
        bands = [t for t in cell.iter(f"{NS}Tablix")
                 if (t.get("Name") or "").startswith("Band_")]
        return row_h, cell, bands
    raise AssertionError("expected a Tablix_SectionTrailer data region")


def test_overflowing_trailer_record_bands_become_collapsible_rows():
    """The 12in trailer design stack on a 9in declared trailer body: the
    DEEP bands (depth 3, behind an inert wrapper) are surfaced, rewritten
    as hidden-collapsible rows, and the trailer row is sized to the
    DECLARED trailer body — one sheet per voucher record."""
    rdl = convert(_voucher_xml())["rdl_xml"]
    row_h, cell, bands = _trailer_parts(rdl)
    assert len(bands) == 2, (
        f"both trailer conditional bands must be rewritten, got {len(bands)}")
    for band in bands:
        member = band.find(f"{NS}TablixRowHierarchy/{NS}TablixMembers/"
                           f"{NS}TablixMember")
        hid = (member.findtext(f"{NS}Visibility/{NS}Hidden")
               if member is not None else None)
        assert hid and hid.strip().startswith("="), (
            "the trailer band's format trigger must ride the STATIC row "
            "member — that is the construct the engine collapses")
        for rect in band.iter(f"{NS}Rectangle"):
            assert rect.find(f"{NS}Visibility") is None, (
                "the band rect must NOT keep its own Hidden: a hidden "
                "rectangle reserves its box (engine-measured)")
    assert row_h <= 9.0 + 0.01, (
        f"trailer row is {row_h}in — a voucher whose declared 9in body "
        "fits the paper must be sized to the body, not the design stack")

    # DEPTH: the inert wrapper must have been dissolved — each Band_*
    # tablix sits at native band depth (at most ONE Rectangle between the
    # trailer cell rect and the band tablix). The engine's shrink-reflow
    # is sibling-scoped and an ancestor rect's declared height never
    # shrinks, so a deeper band could never hand its space back.
    parent = {c: p for p in cell.iter() for c in p}
    for band in bands:
        rect_depth = 0
        cur = parent.get(band)
        while cur is not None and cur is not cell:
            if cur.tag == f"{NS}Rectangle":
                rect_depth += 1
            cur = parent.get(cur)
        # cell holds one Rectangle (the trailer page rect) that never
        # counts as a wrapper; band depth <= 2 means <= 2 rects above it.
        assert rect_depth <= 2, (
            f"band {band.get('Name')} still buried behind {rect_depth} "
            "rectangles — the inert wrapper was not flattened, reclaim "
            "cannot propagate")

    # Every rect holding a collapsed band sits inside the clamped row: a
    # container's declared height never shrinks at render.
    def _walk(el):
        for ch in el:
            if ch.tag == f"{NS}Rectangle":
                holds_band = any((t.get("Name") or "").startswith("Band_")
                                 for t in ch.iter(f"{NS}Tablix"))
                if holds_band:
                    bot = (_inches(ch.findtext(f"{NS}Top"))
                           + _inches(ch.findtext(f"{NS}Height")))
                    assert bot <= row_h + 0.01, (
                        f"band-holding rect bottoms at {bot}in, past the "
                        f"{row_h}in trailer row")
            _walk(ch)
    _walk(cell)


def test_trailer_record_that_cannot_fit_even_collapsed_keeps_grow_flow():
    """Gate: bands reclaim 3.2in, but this trailer stack is 14.5in on a 9in
    body — collapse cannot bring it inside, so the record keeps grow/flow
    (honest oversize) and nothing is rewritten."""
    rdl = convert(_voucher_xml(trailer_frame_h=14.5,
                               trailer_bottom_y=14.3))["rdl_xml"]
    row_h, _cell, bands = _trailer_parts(rdl)
    assert not bands, "gate must refuse a trailer record collapse cannot fit"
    assert row_h > 9.5, (
        f"trailer row is {row_h}in — an oversize record must keep its "
        "extent, not be clamped to a body it cannot fit")


def test_load_bearing_wrapper_refuses_the_flatten():
    """A wrapper that PAINTS (declared border) is load-bearing: dissolving
    it would erase declared ink, and reclaim honestly cannot propagate
    through it (its declared height never shrinks). The bands behind it
    must stay unreachable: no Band_* rewrite, row keeps the stack."""
    rdl = convert(_voucher_xml(
        wrapper_visual='<visualSettings lineWidth="1" linePattern="solid"/>'
    ))["rdl_xml"]
    row_h, _cell, bands = _trailer_parts(rdl)
    assert not bands, (
        "bands behind a PAINTED wrapper must not be rewritten — the "
        "wrapper cannot be dissolved and reclaim cannot pass through it")
    assert row_h > 9.5, (
        f"trailer row is {row_h}in — with the bands unreachable the "
        "record must keep its honest oversize extent")
