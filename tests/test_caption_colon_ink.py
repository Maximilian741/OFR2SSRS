"""Caption literals reach ink: pairing decides PLACEMENT, never EXISTENCE.

Two wild-render-measured residual classes, both about 'Label:'-style
declared captions (synthetic fixtures only):

1. HEADER-POOL DROP: a colon-terminated header-band text with no field on
   its own line IS a declared column heading (the summary-count heading
   idiom). The caption/column pairing used to exclude every colon-suffixed
   text from the pool, which (a) dropped the declared literal from ink
   entirely and (b) handed that column the nearest OTHER caption, printing
   a neighbouring heading twice.

2. TRAILER DEDUP SWALLOW: a report-reset summary placed in a footer frame
   with a declared caption was skipped as "already displayed" because a
   per-record DETAIL-row cell carried the same dataset-scoped aggregate
   string (a Details member has a <Group> but no GroupExpression, so the
   ungrouped-tablix gate could not see that it repeats). The whole declared
   footer line -- caption AND value -- never reached ink.

Plus the exact-fit widen/padding-strip net must FIRE on every constant
caption emitter path (raw-literal Values, monospace + GDI-measured faces,
page-chrome containers) and may reclaim the neighbour-gap reserve only for
an ink-free left-anchored box.
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402
from converter.generators.rdl import (  # noqa: E402
    _widen_clipped_constant_labels,
)

RDL_NS = "http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition"


def _q(tag: str) -> str:
    return "{%s}%s" % (RDL_NS, tag)


# ---------------------------------------------------------------------------
# Fixture: a nested master-detail with a summary column whose header caption
# and report-footer caption are both 'Label:'-style literals. Mirrors the
# structural shape only -- all names and wording are synthetic.
# ---------------------------------------------------------------------------

MD_SUMMARY_XML = """<?xml version="1.0" encoding="UTF-8"?>
<report name="depot_ledger" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q_1">
      <select canParse="yes"><![CDATA[select depot_id, crate_id from depot_crates
]]></select>
      <group name="G_DEPOT">
        <dataItem name="DEPOT_ID" oracleDatatype="number" columnOrder="11"
         width="22" defaultWidth="80000" defaultHeight="10000"
         columnFlags="33" defaultLabel="Depot Id">
          <dataDescriptor expression="DEPOT.DEPOT_ID"
           descriptiveExpression="DEPOT_ID" order="1" width="22" precision="6"/>
        </dataItem>
        <dataItem name="CRATE_ID1" oracleDatatype="number" columnOrder="12"
         width="22" defaultWidth="80000" defaultHeight="10000"
         columnFlags="33" defaultLabel="Crate Id1">
          <dataDescriptor expression="DEPOT.CRATE_ID"
           descriptiveExpression="CRATE_ID" order="2" width="22" precision="6"/>
        </dataItem>
        <dataItem name="CRATE_ID" oracleDatatype="number" columnOrder="14"
         width="22" defaultWidth="80000" defaultHeight="10000"
         columnFlags="1" defaultLabel="Crate Id">
          <dataDescriptor expression="CRATE.CRATE_ID"
           descriptiveExpression="CRATE_ID" order="4" width="22" precision="6"/>
        </dataItem>
        <summary name="CountCRATE_IDPerDEPOT_ID" source="CRATE_ID"
         function="count" precision="8" reset="G_DEPOT" compute="report"
         defaultWidth="100000" defaultHeight="10000" columnFlags="552"
         defaultLabel="Tally:">
          <displayInfo x="0.00000" y="0.00000" width="0.00000" height="0.00000"/>
        </summary>
      </group>
      <group name="G_CRATE">
        <dataItem name="CRATE_NM" datatype="vchar2" columnOrder="13"
         width="510" defaultWidth="100000" defaultHeight="10000"
         columnFlags="33" defaultLabel="Crate Nm">
          <dataDescriptor expression="CRATE.CRATE_NM"
           descriptiveExpression="CRATE_NM" order="3" width="510"/>
        </dataItem>
      </group>
    </dataSource>
    <summary name="CountCRATE_IDPerReport" source="CRATE_ID" function="count"
     precision="8" reset="report" compute="report" defaultWidth="100000"
     defaultHeight="10000" columnFlags="552" defaultLabel="Tally:">
      <displayInfo x="0.00000" y="0.00000" width="0.00000" height="0.00000"/>
    </summary>
  </data>
  <layout>
  <section name="main">
    <body>
      <frame name="M_G_DEPOT_GRPFR">
        <geometryInfo x="0.00000" y="0.00000" width="6.87500" height="0.37500"/>
        <generalLayout verticalElasticity="variable"/>
        <visualSettings fillPattern="transparent"/>
        <repeatingFrame name="R_G_DEPOT" source="G_DEPOT"
         printDirection="down" minWidowRecords="1" columnMode="no">
          <geometryInfo x="0.00000" y="0.18750" width="6.87500" height="0.18750"/>
          <generalLayout verticalElasticity="variable"/>
          <visualSettings fillPattern="transparent"/>
          <field name="F_DEPOT_ID" source="DEPOT_ID" minWidowLines="1"
           spacing="0" alignment="start">
            <font face="helvetica" size="9"/>
            <geometryInfo x="0.00000" y="0.18750" width="1.12500" height="0.18750"/>
            <generalLayout verticalElasticity="expand"/>
          </field>
          <field name="F_CRATE_ID1" source="CRATE_ID1" minWidowLines="1"
           spacing="0" alignment="start">
            <font face="helvetica" size="9"/>
            <geometryInfo x="1.12500" y="0.18750" width="1.12500" height="0.18750"/>
            <generalLayout verticalElasticity="expand"/>
          </field>
          <field name="F_CountCRATE_IDPerDEPOT_ID"
           source="CountCRATE_IDPerDEPOT_ID" minWidowLines="1" spacing="0"
           alignment="start">
            <font face="helvetica" size="9"/>
            <geometryInfo x="2.25000" y="0.18750" width="1.37500" height="0.18750"/>
            <generalLayout verticalElasticity="expand"/>
          </field>
          <frame name="M_G_CRATE_GRPFR">
            <geometryInfo x="5.00000" y="0.18750" width="1.87500" height="0.18750"/>
            <generalLayout verticalElasticity="variable"/>
            <visualSettings fillPattern="transparent"/>
            <repeatingFrame name="R_G_CRATE" source="G_CRATE"
             printDirection="down" minWidowRecords="1" columnMode="no">
              <geometryInfo x="5.00000" y="0.18750" width="1.87500" height="0.18750"/>
              <generalLayout verticalElasticity="expand"/>
              <visualSettings fillPattern="transparent"/>
              <field name="F_CRATE_NM" source="CRATE_NM" minWidowLines="1"
               spacing="0" alignment="start">
                <font face="helvetica" size="9"/>
                <geometryInfo x="5.00000" y="0.18750" width="0.62500" height="0.18750"/>
                <generalLayout verticalElasticity="expand"/>
              </field>
            </repeatingFrame>
          </frame>
        </repeatingFrame>
        <frame name="M_G_DEPOT_HDR">
          <geometryInfo x="0.00000" y="0.00000" width="6.87500" height="0.18750"/>
          <text name="B_DEPOT_ID" minWidowLines="1">
            <textSettings spacing="0"/>
            <geometryInfo x="0.00000" y="0.00000" width="1.12500" height="0.18750"/>
            <textSegment><font face="helvetica" size="9" bold="yes"/>
              <string><![CDATA[Depot Id]]></string></textSegment>
          </text>
          <text name="B_CRATE_ID1" minWidowLines="1">
            <textSettings spacing="0"/>
            <geometryInfo x="1.12500" y="0.00000" width="1.12500" height="0.18750"/>
            <textSegment><font face="helvetica" size="9" bold="yes"/>
              <string><![CDATA[Crate Id1]]></string></textSegment>
          </text>
          <text name="B_CountCRATE_IDPerDEPOT_ID" minWidowLines="1">
            <textSettings spacing="0"/>
            <geometryInfo x="2.25000" y="0.00000" width="1.37500" height="0.18750"/>
            <textSegment><font face="helvetica" size="9" bold="yes"/>
              <string><![CDATA[Tally:]]></string></textSegment>
          </text>
          <text name="B_CRATE_NM" minWidowLines="1">
            <textSettings spacing="0"/>
            <geometryInfo x="5.00000" y="0.00000" width="0.62500" height="0.18750"/>
            <textSegment><font face="helvetica" size="9" bold="yes"/>
              <string><![CDATA[Crate Nm]]></string></textSegment>
          </text>
        </frame>
      </frame>
      <frame name="M_Plain_FTR">
        <geometryInfo x="0.00000" y="0.37500" width="3.87500" height="0.18750"/>
        <visualSettings fillPattern="transparent"/>
        <field name="F_CountCRATE_IDPerReport" source="CountCRATE_IDPerReport"
         minWidowLines="1" spacing="0" alignment="start">
          <font face="helvetica" size="10" bold="yes"/>
          <geometryInfo x="0.43750" y="0.37500" width="1.50000" height="0.18750"/>
          <generalLayout verticalElasticity="expand"/>
        </field>
        <text name="B_CountCRATE_IDPerReport" minWidowLines="1">
          <textSettings spacing="0"/>
          <geometryInfo x="0.00000" y="0.37500" width="0.43750" height="0.18750"/>
          <textSegment><font face="helvetica" size="10" bold="yes"/>
            <string><![CDATA[Tally:]]></string></textSegment>
        </text>
      </frame>
    </body>
    <margin>
    </margin>
  </section>
  </layout>
</report>
"""


def _convert_md():
    res = convert(MD_SUMMARY_XML.encode("utf-8"), "depot_ledger.xml")
    return res["rdl_xml"]


def test_colon_header_caption_reaches_header_ink():
    """The declared 'Tally:' heading captions the summary column -- and the
    neighbouring caption is never duplicated onto it."""
    rdl = _convert_md()
    root = ET.fromstring(rdl.encode("utf-8"))
    caps = {}
    for tb in root.iter(_q("Textbox")):
        nm = tb.get("Name") or ""
        if nm.startswith("Hdr_"):
            v = next((v.text for v in tb.iter(_q("Value"))
                      if (v.text or "").strip()), "")
            caps[nm] = v
    assert caps.get("Hdr_CountCRATE_IDPerDEPOT_ID") == "Tally:", caps
    # each declared heading appears exactly once across the header cells
    vals = list(caps.values())
    assert vals.count("Crate Id1") == 1, caps
    assert vals.count("Depot Id") == 1, caps


def test_footer_summary_caption_and_value_emit_despite_detail_row_dedup():
    """The report-reset summary line prints its DECLARED caption even though
    a per-record detail cell carries the identical aggregate string."""
    rdl = _convert_md()
    # the per-record detail cell exists (the identical aggregate string is
    # exactly what used to swallow the report-total line) ...
    assert rdl.count('=Count(Fields!CRATE_ID.Value, "Q_1")') >= 1, (
        "the per-record summary cell vanished -- fixture no longer probes "
        "the dedup shape")
    # ... and the trailer STILL pairs the declared caption with the aggregate
    assert '="Tally:  " &' in rdl, "declared footer caption never reached ink"
    # declared wording, never the humanized summary-name blob
    assert "Idperreport" not in rdl


# ---------------------------------------------------------------------------
# The widen/padding-strip net fires on every constant-caption emitter shape.
# ---------------------------------------------------------------------------


def _tree(container: str = "Body"):
    """Minimal report skeleton with one positioned-items container."""
    root = ET.Element(_q("Report"))
    ET.SubElement(root, _q("Width")).text = "8.5in"
    if container == "Body":
        cont = ET.SubElement(root, _q("Body"))
    elif container == "PageHeader":
        page = ET.SubElement(root, _q("Page"))
        cont = ET.SubElement(page, _q("PageHeader"))
    else:
        raise AssertionError(container)
    ri = ET.SubElement(cont, _q("ReportItems"))
    return root, ri


def _textbox(ri, name, value, left, top, width, height, *, family, size,
             bold=False, align="Left", bg=None, border=None, can_grow=False):
    tb = ET.SubElement(ri, _q("Textbox"))
    tb.set("Name", name)
    paras = ET.SubElement(tb, _q("Paragraphs"))
    para = ET.SubElement(paras, _q("Paragraph"))
    pst = ET.SubElement(para, _q("Style"))
    ET.SubElement(pst, _q("TextAlign")).text = align
    runs = ET.SubElement(para, _q("TextRuns"))
    run = ET.SubElement(runs, _q("TextRun"))
    ET.SubElement(run, _q("Value")).text = value
    rst = ET.SubElement(run, _q("Style"))
    ET.SubElement(rst, _q("FontFamily")).text = family
    ET.SubElement(rst, _q("FontSize")).text = size
    if bold:
        ET.SubElement(rst, _q("FontWeight")).text = "Bold"
    st = ET.SubElement(tb, _q("Style"))
    bd = ET.SubElement(st, _q("Border"))
    ET.SubElement(bd, _q("Style")).text = border or "None"
    if bg:
        ET.SubElement(st, _q("BackgroundColor")).text = bg
    ET.SubElement(st, _q("PaddingLeft")).text = "0pt"
    ET.SubElement(st, _q("PaddingRight")).text = "0pt"
    ET.SubElement(tb, _q("CanGrow")).text = "true" if can_grow else "false"
    ET.SubElement(tb, _q("Top")).text = f"{top:.4f}in"
    ET.SubElement(tb, _q("Left")).text = f"{left:.4f}in"
    ET.SubElement(tb, _q("Width")).text = f"{width:.4f}in"
    ET.SubElement(tb, _q("Height")).text = f"{height:.4f}in"
    return tb


def _w_of(tb) -> float:
    return float((tb.findtext(_q("Width")) or "0").replace("in", ""))


def test_net_widens_raw_literal_monospace_caption_into_gap_reserve():
    """RAW-literal Value + Courier metrics + the ink-free hairline reclaim:
    7 monospace glyphs at 8pt need 0.4667in; the declared 0.46in box clips
    the trailing colon, and the only free room lies inside the 0.04in
    neighbour-gap reserve."""
    root, ri = _tree()
    lbl = _textbox(ri, "Lbl", "Marker:", 1.75, 0.25, 0.46, 0.22,
                   family="Courier New", size="8pt", bold=True)
    _textbox(ri, "Val", "=Fields!X.Value", 2.25, 0.25, 1.0, 0.22,
             family="Courier New", size="8pt")
    _widen_clipped_constant_labels(root)
    w = _w_of(lbl)
    assert w >= 7 * 0.6 * 8 / 72.0 - 1e-6, f"still clips: {w}"
    assert 1.75 + w <= 2.25 - 0.01 + 1e-6, f"crossed the neighbour: {w}"


def test_net_leaves_painted_box_out_of_the_gap_reserve():
    """A box that paints its own background may NOT take the hairline
    reserve -- widening it would paint new ink against the neighbour."""
    root, ri = _tree()
    lbl = _textbox(ri, "Lbl", "Marker:", 1.75, 0.25, 0.46, 0.22,
                   family="Courier New", size="8pt", bold=True,
                   bg="#EEEEEE")
    _textbox(ri, "Val", "=Fields!X.Value", 2.25, 0.25, 1.0, 0.22,
             family="Courier New", size="8pt")
    _widen_clipped_constant_labels(root)
    assert abs(_w_of(lbl) - 0.46) < 1e-6, "painted box took the gap reserve"


def test_net_covers_page_chrome_containers_with_measured_face():
    """A raw-literal Tahoma caption sitting DIRECTLY under PageHeader is the
    same clip class as a body caption -- for a metric SLIVER deficit. The
    GDI-measured Tahoma advances price the literal wider than the Helvetica
    AFM proxy does, and free room exists to the right. A REAL overflow keeps
    the declared width (page chrome's settled bounded-growth contract)."""
    from converter.generators.rdl import _MEASURED_ADVANCES  # noqa: PLC0415
    text = "Depot up to :"
    tbl = _MEASURED_ADVANCES["tahoma"][0]
    need = sum(tbl[ord(c) - 32] for c in text) / 1000.0 * 11 / 72.0
    root, ri = _tree("PageHeader")
    lbl = _textbox(ri, "Chrome", text, 0.469, 0.57, need - 0.03, 0.19,
                   family="Tahoma", size="11pt")
    _widen_clipped_constant_labels(root)
    assert _w_of(lbl) >= need - 1e-6, "chrome caption still clips"
    # bounded: a REAL overflow (past the drift budget) keeps the declared box
    root2, ri2 = _tree("PageHeader")
    lbl2 = _textbox(ri2, "Chrome", text, 0.469, 0.57, need - 0.5, 0.19,
                    family="Tahoma", size="11pt")
    _widen_clipped_constant_labels(root2)
    assert abs(_w_of(lbl2) - (need - 0.5)) < 1e-3, (
        "page chrome must keep the declared width on a real overflow")


def test_net_expression_literal_times_path_unchanged():
    """The pre-existing ="..." Times shape keeps working (regression)."""
    root, ri = _tree()
    lbl = _textbox(ri, "Lbl", '="Geographic Detail"', 1.0, 0.25, 0.9, 0.22,
                   family="Times New Roman", size="12pt", bold=True)
    _widen_clipped_constant_labels(root)
    assert _w_of(lbl) > 0.9 + 1e-6, "expression-literal widen regressed"
