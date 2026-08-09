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
