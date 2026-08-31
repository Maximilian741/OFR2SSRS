"""STRIP-ONLY PAGE REPEAT: column strip declares allPage, band caption doesn't.

The banking-report shape from the wild-corpus render sweep: the ONLY
``printObjectOnPage="allPage"`` in the whole layout sits on the column-strip
frame; the group caption and the group footer declare nothing.  The emitter
used to answer that combination with a ginner-level static run
``[caption(no repeat), strip(repeat), detail rows, footer(no repeat)]`` --
statics disagreeing about RepeatOnNewPage -- which the engine REJECTS at
publish ("The tablix 'Tablix_GroupedSubtotal' has an invalid TablixMember.
The TablixMember must have the same value set for the RepeatOnNewPage
property as those following or preceding the dynamic TablixMember.
(Expected Value: \"False\"; Actual Value: \"True\")"): the report rendered
ZERO pages, a total loss.

The publish-legal AND reprint-honouring shape (both engine-measured on the
same report, MS ReportViewer PDF read back):

  ginner:  [caption(static), band-wrapper(dynamic), footer(static)]
  wrapper: [strip(static, RepeatOnNewPage), detail rows(dynamic),
            anchor(static, RepeatOnNewPage, KeepWithGroup=Before, 0in row)]

Two neighbouring shapes PUBLISH but silently never reprint the strip
(render-measured, page 2 of a 14-page render): the strip static left at
ginner between the caption and the detail wrapper, and the closed nested run
with a second dynamic member interposed between the detail rows and the
anchor.  The zero-height anchor ROW must also sit exactly where the anchor
member sits -- before the footer row -- because tablix rows pair with
hierarchy leaves in document order.

Everything below is synthetic and structural: no report, column or label
from any real source appears in this file.
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert                       # noqa: E402

NS = ("{http://schemas.microsoft.com/sqlserver/reporting/"
      "2008/01/reportdefinition}")


def _q(tag: str) -> str:
    return NS + tag


def _strip_only_report() -> bytes:
    """A grouped break report whose ONE declared page repeat is the
    column-header frame (``allPage``); the group band and footer declare
    none -- the exact wild combination that used to publish-fail."""
    return '''<?xml version="1.0" encoding="UTF-8"?>
<report name="STRIPRPT" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q_MAIN">
      <select><![CDATA[SELECT GKEY, CAPCOL, D_ONE, D_TWO, D_THREE
       FROM T_MAIN]]></select>
      <group name="G_OUT">
        <dataItem name="GKEY" datatype="vchar2" width="20">
          <dataDescriptor expression="GKEY" order="1" width="20"/>
        </dataItem>
        <dataItem name="CAPCOL" datatype="vchar2" width="40">
          <dataDescriptor expression="CAPCOL" order="2" width="40"/>
        </dataItem>
      </group>
      <group name="G_DET">
        <dataItem name="D_ONE" datatype="vchar2" width="10">
          <dataDescriptor expression="D_ONE" order="3" width="10"/>
        </dataItem>
        <dataItem name="D_TWO" datatype="vchar2" width="30">
          <dataDescriptor expression="D_TWO" order="4" width="30"/>
        </dataItem>
        <dataItem name="D_THREE" oracleDatatype="number" width="10">
          <dataDescriptor expression="D_THREE" order="5" width="10"/>
        </dataItem>
      </group>
    </dataSource>
    <summary name="CS_TOT" source="D_THREE" function="sum" width="12"
     reset="G_OUT" compute="G_OUT" columnFlags="8"/>
    <summary name="CS_ALL" source="D_THREE" function="sum" width="12"
     reset="report" compute="report" columnFlags="8"/>
  </data>
  <layout>
  <section name="main">
    <body height="9.60">
      <frame name="M_BODY">
        <geometryInfo x="0.00000" y="0.00000" width="7.50000"
         height="2.50000"/>
        <generalLayout verticalElasticity="variable"/>
      <repeatingFrame name="R_OUT" source="G_OUT" printDirection="down">
        <geometryInfo x="0.00000" y="0.00000" width="7.50000"
         height="1.83000"/>
        <generalLayout verticalElasticity="variable"/>
        <field name="F_CAP" source="CAPCOL" alignment="start">
          <font face="Arial" size="10" bold="yes"/>
          <geometryInfo x="0.00000" y="0.05000" width="3.00000"
           height="0.19000"/>
        </field>
        <frame name="M_COLHDR">
          <geometryInfo x="0.60000" y="0.30000" width="6.90000"
           height="0.19000"/>
          <advancedLayout printObjectOnPage="allPage"
           basePrintingOn="enclosingObject"/>
          <text name="B_C1"><textSettings spacing="single"/>
            <geometryInfo x="0.65000" y="0.30000" width="0.50000"
             height="0.19000"/>
            <textSegment><font face="Arial" size="10" underline="yes"/>
            <string><![CDATA[Alpha]]></string></textSegment></text>
          <text name="B_C2"><textSettings spacing="single"/>
            <geometryInfo x="1.30000" y="0.30000" width="1.40000"
             height="0.19000"/>
            <textSegment><font face="Arial" size="10" underline="yes"/>
            <string><![CDATA[Beta]]></string></textSegment></text>
          <text name="B_C3"><textSettings spacing="single"/>
            <geometryInfo x="2.80000" y="0.30000" width="0.80000"
             height="0.19000"/>
            <textSegment><font face="Arial" size="10" underline="yes"/>
            <string><![CDATA[Gamma]]></string></textSegment></text>
        </frame>
        <repeatingFrame name="R_DET" source="G_DET" printDirection="down">
          <geometryInfo x="0.65000" y="0.56000" width="6.85000"
           height="0.19000"/>
          <field name="F_D1" source="D_ONE" alignment="start">
            <font face="Arial" size="10"/>
            <geometryInfo x="0.65000" y="0.56000" width="0.50000"
             height="0.19000"/></field>
          <field name="F_D2" source="D_TWO" alignment="start">
            <font face="Arial" size="10"/>
            <geometryInfo x="1.30000" y="0.56000" width="1.40000"
             height="0.19000"/></field>
          <field name="F_D3" source="D_THREE" alignment="end">
            <font face="Arial" size="10"/>
            <geometryInfo x="2.80000" y="0.56000" width="0.80000"
             height="0.19000"/></field>
        </repeatingFrame>
        <frame name="M_FOOT">
          <geometryInfo x="0.00000" y="1.20000" width="7.50000"
           height="0.63000"/>
          <field name="F_SUB_TOT" source="CS_TOT" alignment="end">
            <font face="Arial" size="10" bold="yes"/>
            <geometryInfo x="6.60000" y="1.25000" width="0.90000"
             height="0.19000"/></field>
        </frame>
      </repeatingFrame>
      <frame name="M_TOTAL">
        <geometryInfo x="0.00000" y="1.94000" width="7.50000"
         height="0.50000"/>
        <field name="F_ALL_TOT" source="CS_ALL" alignment="end">
          <font face="Arial" size="10" bold="yes"/>
          <geometryInfo x="6.60000" y="2.05000" width="0.90000"
           height="0.19000"/></field>
      </frame>
      </frame>
    </body>
  </section>
  </layout>
</report>'''.encode()


@pytest.fixture(scope="module")
def strip_only_rdl():
    rdl = convert(_strip_only_report())["rdl_xml"]
    assert "Tablix_GroupedSubtotal" in rdl, (
        "fixture must route through the grouped-tabular emitter")
    return rdl


def _tablix(rdl: str):
    root = ET.fromstring(rdl.encode("utf-8"))
    return next(t for t in root.iter(_q("Tablix"))
                if (t.get("Name") or "") == "Tablix_GroupedSubtotal")


def _is_dynamic(m):
    return m.find(_q("Group")) is not None


def _ronp(m):
    return (m.findtext(_q("RepeatOnNewPage")) or "").strip().lower() == "true"


def test_strip_only_member_shape_is_publish_legal(strip_only_rdl):
    """The P0 publish rule: in EVERY TablixMembers collection, static members
    must agree about RepeatOnNewPage (the old emitter shipped
    [caption F, strip T, detail, footer F] -- engine-rejected, zero pages)."""
    tablix = _tablix(strip_only_rdl)
    rh = tablix.find(_q("TablixRowHierarchy"))

    def check(members_el):
        flags = []
        for m in members_el.findall(_q("TablixMember")):
            if _is_dynamic(m):
                inner = m.find(_q("TablixMembers"))
                if inner is not None:
                    check(inner)
                continue
            flags.append(_ronp(m))
        assert len(set(flags)) <= 1, (
            "static members in one collection must agree on RepeatOnNewPage",
            flags)
    check(rh.find(_q("TablixMembers")))


def test_strip_repeats_inside_wrapper_with_direct_anchor(strip_only_rdl):
    """The reprint-honouring run: [strip(RepeatOnNewPage), detail rows,
    anchor(RepeatOnNewPage, KeepWithGroup=Before)] nested one level deep, the
    anchor DIRECTLY after the detail member (an interposed dynamic member
    publishes but silently kills the reprint), the caption and footer statics
    OUTSIDE the wrapper with no repeat of their own."""
    tablix = _tablix(strip_only_rdl)
    rh = tablix.find(_q("TablixRowHierarchy"))
    gmem = rh.find(_q("TablixMembers")).find(_q("TablixMember"))
    ginner = gmem.find(_q("TablixMembers"))
    tops = ginner.findall(_q("TablixMember"))
    # ginner: caption static, dynamic wrapper, footer static — no repeats here
    assert [_is_dynamic(m) for m in tops] == [False, True, False], (
        "expected [caption, band wrapper, footer] at the group level")
    assert not _ronp(tops[0]) and not _ronp(tops[2]), (
        "the caption and footer never declared a page repeat")
    wrapper = tops[1].find(_q("TablixMembers"))
    run = wrapper.findall(_q("TablixMember"))
    assert [_is_dynamic(m) for m in run] == [False, True, False], (
        "wrapper run must be [strip, detail rows, anchor] with the anchor "
        "DIRECTLY after the dynamic member")
    strip, detail, anchor = run
    assert _ronp(strip), "the declared allPage strip must RepeatOnNewPage"
    assert detail.find(_q("Group")).get("Name") == "GTS_DetailRows"
    assert _ronp(anchor) and (
        anchor.findtext(_q("KeepWithGroup")) or "") == "Before", (
        "the run must close with a RepeatOnNewPage KeepWithGroup=Before "
        "anchor")


def test_anchor_row_sits_before_the_footer_row(strip_only_rdl):
    """Tablix rows pair with hierarchy LEAVES in document order; the anchor
    member precedes the ginner-level footer, so its zero-height row must be
    INSERTED before the footer row, not appended after it."""
    tablix = _tablix(strip_only_rdl)
    rows = tablix.find(_q("TablixBody")).find(_q("TablixRows")) \
                 .findall(_q("TablixRow"))
    names = [{el.get("Name") for el in r.iter() if el.get("Name")}
             for r in rows]
    anchor_i = next(i for i, ns in enumerate(names)
                    if "GTS_RepeatAnchor" in ns)
    footer_i = next(i for i, ns in enumerate(names) if "GTS_Footer" in ns)
    assert anchor_i == footer_i - 1, (
        "anchor row must directly precede the footer row", anchor_i, footer_i)
    assert (rows[anchor_i].findtext(_q("Height")) or "") == "0in"


def test_strip_only_report_renders_through_ms_engine():
    """THE gate that caught the defect: the engine PUBLISH.  Before the fix
    this exact member shape was rejected at publish (invalid TablixMember,
    zero pages rendered)."""
    sys.path.insert(0, str(ROOT / "tools" / "renderlab"))
    try:
        from render import render_rdl, lib_ready  # type: ignore
    except Exception:
        pytest.skip("renderlab not importable")
    if not lib_ready():
        pytest.skip("renderlab DLLs not fetched")
    import tempfile
    rdl = convert(_strip_only_report())["rdl_xml"]
    with tempfile.TemporaryDirectory() as td:
        rp = Path(td) / "s.rdl"
        rp.write_text(rdl, encoding="utf-8")
        res = render_rdl(rp, Path(td) / "s.pdf", rows=3)
        assert res["ok"], res.get("log", "")[-500:]
