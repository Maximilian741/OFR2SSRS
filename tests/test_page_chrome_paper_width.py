"""DECLARED PAPER WIDTH: page chrome must not inflate the sheet.

Dialect (truth-measured against an Oracle-rendered PDF that measures
exactly 612x792pt — a Letter sheet):

* Objects authored in the section's ``<margin>`` band are PAGE FURNITURE
  in PAPER coordinates: their x already includes the sheet's own side
  margin (a "full width" footer rule on Letter runs 0.5in -> 8.01in).
* Body objects are BODY coordinates (0 -> 7.5in on that same sheet).

Adding page margins to a chrome span therefore double-counts the margin
and grows the paper past the sheet the source draws on. That is how a
plain portrait report ended up on an 8.61in-wide page while its body
content (7.5in) fit the printable area with room to spare — and a Report
Width at/over the printable width is the known cause of blank right-hand
pages.

Rules guarded here:
* The sheet is sized from BODY content; declared chrome never widens it.
* The body still goes through the printable-width clamp, so the
  blank-page invariant (body + margins <= PageWidth) holds.
* Genuinely wide BODY content still widens the sheet (landscape intact).
* The sheet must still CONTAIN declared chrome: the band emitter shifts
  each item one side-margin inward, so chrome_span - hmargin has to fit
  the printable width.

Synthetic fixtures only — no client data.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402

_HMARGIN_IN = 0.25  # Oracle's default side margin, for readability of sizes


def _report_xml(body_w: str = "7.50000", chrome_x: str = "0.51000",
                chrome_w: str = "7.50000") -> bytes:
    """A portrait master/detail report with a declared <margin> band.

    ``body_w`` widens the BODY content (landscape leg); ``chrome_x`` /
    ``chrome_w`` move the page furniture (containment leg).
    """
    chrome_right = float(chrome_x) + float(chrome_w)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<report name="LEDGER_HISTORY" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q_MAIN">
      <select canParse="no"><![CDATA[SELECT HOLDER, ADDR_LINE, TOTAL_AMT FROM LEDGER]]></select>
      <group name="G_MAIN">
        <dataItem name="HOLDER" datatype="vchar2" columnOrder="1" defaultLabel="Holder"/>
        <dataItem name="ADDR_LINE" datatype="vchar2" columnOrder="2" defaultLabel="Address"/>
        <dataItem name="TOTAL_AMT" datatype="number" columnOrder="3" defaultLabel="Total"/>
      </group>
    </dataSource>
  </data>
  <layout>
  <section name="main">
    <body height="8.94800">
      <location y="1.19788"/>
      <frame name="M_G_MAIN_GRPFR">
        <geometryInfo x="0.00000" y="0.00000" width="{body_w}" height="4.37500"/>
        <generalLayout verticalElasticity="variable"/>
        <repeatingFrame name="R_G_MAIN" source="G_MAIN" printDirection="down"
         maxRecordsPerPage="1" minWidowRecords="1" columnMode="no">
          <geometryInfo x="0.00000" y="0.00000" width="{body_w}" height="4.29968"/>
          <generalLayout verticalElasticity="variable"/>
          <field name="F_HOLDER" source="HOLDER" minWidowLines="1" alignment="start">
            <font face="Arial" size="10" bold="yes"/>
            <geometryInfo x="0.75000" y="0.00000" width="6.70000" height="0.18000"/></field>
          <text name="B_HOLDER_LBL"><geometryInfo x="0.00000" y="0.00000" width="0.70000" height="0.18000"/>
            <textSegment><font face="Arial" size="10"/><string><![CDATA[Holder]]></string></textSegment></text>
          <field name="F_ADDR_LINE" source="ADDR_LINE" minWidowLines="1" alignment="start">
            <font face="Arial" size="10"/>
            <geometryInfo x="0.75000" y="0.25000" width="6.70000" height="0.18000"/></field>
          <field name="F_TOTAL_AMT" source="TOTAL_AMT" minWidowLines="1" alignment="end">
            <font face="Arial" size="10"/>
            <geometryInfo x="5.50000" y="0.60000" width="2.00000" height="0.18000"/></field>
        </repeatingFrame>
      </frame>
    </body>
    <margin>
      <text name="B_TITLE" minWidowLines="1">
        <textSettings justify="center" spacing="0"/>
        <geometryInfo x="2.07000" y="0.48900" width="4.37500" height="0.25000"/>
        <textSegment><font face="Arial" size="14"/><string><![CDATA[Ledger History]]></string></textSegment>
      </text>
      <frame name="M_G_FOOTER">
        <geometryInfo x="{chrome_x}" y="10.25000" width="{chrome_w}" height="0.30000"/>
        <line name="B_FOOTER_LINE" arrow="none">
          <geometryInfo x="{chrome_x}" y="10.25000" width="{chrome_w}" height="0.00000"/>
          <visualSettings linePattern="solid"/>
          <points>
            <point x="{chrome_x}" y="10.25000"/>
            <point x="{chrome_right:.5f}" y="10.25000"/>
          </points>
        </line>
        <text name="B_PAGE_NUM" minWidowLines="1">
          <textSettings justify="center" spacing="0"/>
          <geometryInfo x="3.22000" y="10.36000" width="2.05000" height="0.17700"/>
          <textSegment><font face="Arial" size="8"/><string><![CDATA[ Page &PageNumber of &TotalPages]]></string></textSegment>
        </text>
        <field name="F_CURRENT_DATE" source="CurrentDate" minWidowLines="1"
         formatMask="MM/DD/RRRR" spacing="0" alignment="end">
          <font face="Arial" size="8"/>
          <geometryInfo x="{chrome_right - 0.80:.5f}" y="10.36000" width="0.80000" height="0.18700"/>
        </field>
      </frame>
    </margin>
  </section>
  </layout>
</report>""".encode()


def _page_geometry(rdl: str):
    """(PageWidth, LeftMargin, RightMargin, report Width) in inches."""
    def _one(tag):
        m = re.search(rf"<{tag}>([0-9.]+)in</{tag}>", rdl)
        return float(m.group(1)) if m else 0.0

    body_w = re.search(r"</Body>\s*<Width>([0-9.]+)in</Width>", rdl)
    return (_one("PageWidth"), _one("LeftMargin"), _one("RightMargin"),
            float(body_w.group(1)) if body_w else 0.0)


def test_declared_chrome_does_not_widen_the_sheet():
    """Body fits Letter; the paper-relative footer chrome must not grow it."""
    rdl = convert(_report_xml())["rdl_xml"]
    pw, left, right, width = _page_geometry(rdl)
    assert pw == 8.5, (
        f"PageWidth {pw}in: a portrait report whose BODY spans 7.5in prints "
        "on the declared Letter sheet — the <margin> band is paper-relative "
        "page furniture and must never inflate the paper")
    assert width + left + right <= pw + 1e-9, (
        f"report Width {width}in + margins overflows PageWidth {pw}in — "
        "the printable-width clamp is what keeps SSRS from emitting a blank "
        "right-hand page after every content page")


def test_wide_body_still_widens_the_sheet():
    """The chrome exclusion must not disable landscape sizing."""
    rdl = convert(_report_xml(body_w="9.60000"))["rdl_xml"]
    pw, _left, _right, _w = _page_geometry(rdl)
    assert pw > 10.0, (
        f"PageWidth {pw}in: BODY content of 9.6in genuinely needs a wide "
        "sheet — only page chrome is excluded from the sizing span")


def _emitted_chrome_right_edge(rdl: str) -> float:
    """Widest band-relative right edge (in) over the emitted page-chrome
    items — measured off the RDL instead of assumed, because the band
    emitter shifts each item by THIS report's own LeftMargin."""
    widest = 0.0
    for block in re.findall(r"<(?:Textbox|Image|Line|Rectangle) "
                            r"Name=\"MChrome_[^\"]*\"[^>]*>(.*?)"
                            r"</(?:Textbox|Image|Line|Rectangle)>",
                            rdl, re.S):
        left = re.search(r"<Left>([0-9.]+)in</Left>", block)
        width = re.search(r"<Width>([0-9.]+)in</Width>", block)
        if left and width:
            widest = max(widest, float(left.group(1)) + float(width.group(1)))
    return widest


def test_sheet_still_contains_oversize_declared_chrome():
    """Chrome wider than the default sheet still sizes the paper.

    A page band spans the PRINTABLE width (PageWidth - LeftMargin -
    RightMargin) whatever the report <Width> is — engine-measured: a header
    textbox at Left 8in/Width 1.5in printed its full span on a report whose
    Body Width was 5in. So the invariant is on the MARGINS: the printable
    band must reach the widest page-chrome item actually emitted, and the
    right margin may never close over it.
    """
    rdl = convert(_report_xml(chrome_x="0.50000",
                              chrome_w="9.50000"))["rdl_xml"]
    pw, left, right, _w = _page_geometry(rdl)
    chrome_right = _emitted_chrome_right_edge(rdl)
    assert chrome_right > 0.0, "no page chrome was emitted to measure"
    assert pw - left - right >= chrome_right - 1e-9, (
        f"PageWidth {pw}in with margins {left}/{right}: printable width "
        f"{pw - left - right}in does not reach the emitted page chrome's "
        f"{chrome_right}in right edge — the chrome is clipped out of the band")
    assert chrome_right >= 9.5 - 1e-9, (
        f"emitted chrome reaches only {chrome_right}in: a declared 9.5in-wide "
        "footer rule must keep its declared span")


# ---------------------------------------------------------------------------
# OVERSIZED LOGICAL SHEET: margin chrome authored past the physical cap
# ---------------------------------------------------------------------------
# Wild-corpus defect class (round 10): a section may declare a LOGICAL sheet
# far wider than any emittable physical page (PageWidth caps at 17in), and
# its <margin> band is authored in THAT sheet's coordinates -- a company-name
# title centred on a 50in logical page sits at x~23.5in, past every physical
# paper edge. Emitting it 1:1 put the string off-paper: zero ink in the
# render, while the synthesized mockup heads dropped every margin string
# except the one title candidate. The rule: a chrome box that overhangs the
# emitted paper keeps its PAPER AXIS (same fraction of the sheet), a box
# that already fits keeps its declared 1:1 position.


def _oversheet_xml() -> bytes:
    """Warehouse-style wide grid: 50in logical sheet, margin strings centred
    on it (x=23.5), plus a left-anchored label+date pair that DOES fit."""
    return """<?xml version="1.0" encoding="UTF-8"?>
<report name="STOCK_GRID" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q_MAIN">
      <select canParse="no"><![CDATA[SELECT ZONE_NAME, BIN_CODE, UNITS FROM STOCK]]></select>
      <group name="G_MAIN">
        <dataItem name="ZONE_NAME" datatype="vchar2" columnOrder="1" defaultLabel="Zone"/>
        <dataItem name="BIN_CODE" datatype="vchar2" columnOrder="2" defaultLabel="Bin"/>
        <dataItem name="UNITS" datatype="number" columnOrder="3" defaultLabel="Units"/>
      </group>
    </dataSource>
  </data>
  <layout>
  <section name="main" width="50.00000" height="163.00000" orientation="landscape">
    <body width="49.00000" height="161.00000">
      <repeatingFrame name="R_G_MAIN" source="G_MAIN" printDirection="down"
       minWidowRecords="1" columnMode="no">
        <geometryInfo x="0.00000" y="0.50000" width="48.90000" height="0.25000"/>
        <generalLayout verticalElasticity="expand"/>
        <field name="F_ZONE_NAME" source="ZONE_NAME" minWidowLines="1" alignment="start">
          <font face="Arial" size="10"/>
          <geometryInfo x="0.00000" y="0.50000" width="1.40000" height="0.25000"/></field>
        <field name="F_BIN_CODE" source="BIN_CODE" minWidowLines="1" alignment="start">
          <font face="Arial" size="10"/>
          <geometryInfo x="1.50000" y="0.50000" width="2.90000" height="0.25000"/></field>
        <field name="F_UNITS" source="UNITS" minWidowLines="1" alignment="end">
          <font face="Arial" size="10"/>
          <geometryInfo x="4.50000" y="0.50000" width="0.75000" height="0.25000"/></field>
      </repeatingFrame>
      <text name="B_ZONE_LBL" minWidowLines="1">
        <textSettings justify="center" spacing="0"/>
        <geometryInfo x="0.00000" y="0.25000" width="1.40000" height="0.25000"/>
        <textSegment><font face="Arial" size="10" bold="yes"/>
          <string><![CDATA[Zone]]></string></textSegment>
      </text>
    </body>
    <margin>
      <text name="B_CO" minWidowLines="1">
        <textSettings spacing="0"/>
        <geometryInfo x="23.50000" y="0.11000" width="3.00000" height="0.24000"/>
        <textSegment><font face="Tahoma" size="14" bold="yes"/>
          <string><![CDATA[Interstate Fixture Works]]></string></textSegment>
      </text>
      <text name="B_ADDR" minWidowLines="1">
        <textSettings spacing="0"/>
        <geometryInfo x="23.75000" y="0.40000" width="2.50000" height="0.14000"/>
        <textSegment><font face="Tahoma" size="8"/>
          <string><![CDATA[12 Depot Yard Road, Plainsboro]]></string></textSegment>
      </text>
      <text name="B_TILL" minWidowLines="1">
        <textSettings spacing="0"/>
        <geometryInfo x="0.47000" y="0.69000" width="0.50000" height="0.19000"/>
        <textSegment><font face="Tahoma" size="11"/>
          <string><![CDATA[As of :]]></string></textSegment>
      </text>
      <field name="F_DT" source="CurrentDate" minWidowLines="1"
       formatMask="MM/DD/RRRR" spacing="0" alignment="center">
        <font face="Tahoma" size="11"/>
        <geometryInfo x="1.01000" y="0.69000" width="1.35000" height="0.19000"/>
      </field>
    </margin>
  </section>
  </layout>
</report>""".encode()


def _mchrome_box(rdl: str, value_marker: str):
    """(Left, Width) of the MChrome textbox whose Value carries the marker."""
    for m in re.finditer(r'<Textbox Name="MChrome_[^"]*">', rdl):
        block_end = rdl.find("</Textbox>", m.start())
        block = rdl[m.start():block_end]
        if value_marker in block:
            left = re.search(r"<Left>([0-9.]+)in</Left>", block)
            width = re.search(r"<Width>([0-9.]+)in</Width>", block)
            if left and width:
                return float(left.group(1)), float(width.group(1))
    return None


def test_oversheet_chrome_keeps_its_paper_axis_on_the_capped_page():
    """Chrome authored past the physical cap must still ink: it lands at the
    SAME FRACTION of the emitted sheet it held on the declared one (a centred
    company line stays centred), and the containment invariant holds."""
    rdl = convert(_oversheet_xml())["rdl_xml"]
    pw, left, right, _w = _page_geometry(rdl)
    assert pw <= 17.0 + 1e-9, f"physical cap not engaged (PageWidth {pw}in)"
    chrome_right = _emitted_chrome_right_edge(rdl)
    assert chrome_right > 0.0, "no page chrome was emitted to measure"
    assert pw - left - right >= chrome_right - 1e-9, (
        f"printable width {pw - left - right}in does not reach the emitted "
        f"chrome's {chrome_right}in right edge -- the margin strings are "
        "off-paper and never ink (the round-10 warehouse-grid class)")
    co = _mchrome_box(rdl, "Interstate Fixture Works")
    assert co is not None, "the margin company string was not emitted at all"
    co_center = left + co[0] + co[1] / 2.0
    # declared centre = (23.5 + 1.5) / 50 = exactly half the logical sheet
    assert abs(co_center - pw / 2.0) <= 0.05, (
        f"company line centre {co_center:.3f}in on a {pw}in sheet: a box "
        "centred on the declared logical sheet must stay on that axis")
    addr = _mchrome_box(rdl, "12 Depot Yard Road, Plainsboro")
    assert addr is not None, "the margin address string was not emitted"
    assert addr[0] + addr[1] <= pw - left - right + 1e-9, (
        "address line clipped off the printable band")


def test_oversheet_fitting_chrome_stays_declared_one_to_one():
    """The axis remap is for OVERHANGING boxes only: a left-anchored label
    that already fits the emitted paper keeps its declared position (sliding
    it collided the label with its companion date box)."""
    rdl = convert(_oversheet_xml())["rdl_xml"]
    _pw, left, _right, _w = _page_geometry(rdl)
    till = _mchrome_box(rdl, "As of :")
    assert till is not None, "the fitting margin label was not emitted"
    assert abs((left + till[0]) - 0.47) <= 0.02, (
        f"fitting chrome moved: declared paper x 0.47in, emitted "
        f"{left + till[0]:.3f}in -- boxes that fit must stay 1:1")


def test_oversheet_margin_strings_reach_the_mockup_head_once():
    """Mockup arm of the same class: the synthesized heads printed only the
    ONE title candidate, so the margin address line never reached ink. Every
    top-band margin static text must ink exactly once (no round-10
    duplicate emission either)."""
    res = convert(_oversheet_xml())
    html = res["mockup_html"]
    n_co = html.count("Interstate Fixture Works")
    n_addr = html.count("12 Depot Yard Road, Plainsboro")
    assert n_co == 1, (
        f"margin company string ink count {n_co} (want exactly 1)")
    assert n_addr == 1, (
        f"margin address string ink count {n_addr} (want exactly 1)")
