"""SMALL-PAPER class: a declared card/label stock prints at its declared
size.

Dialect (truth-measured): the main section's declared width/height IS the
paper — including when it is SMALLER than a Letter sheet (a 3.5x8.5in
card). The old geometry only honored declared paper when LARGER, so cards
rendered centered on a Letter page with content displaced sideways and the
page grown to 11in+.

Rules guarded here:
* PageWidth/PageHeight = the declared small stock.
* Margins scale down so the declared body still fits (blank-page rule:
  body width strictly < page width - margins).
* One record renders per stock page — no near-blank spill page from
  emission pad (the tail pad is clamped to the printable extent).
* A report whose CONTENT does not fit the declared width keeps the
  normal sheet (the small-paper honor requires a genuine fit).

Synthetic fixtures only — no client data.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402


def _card_xml(field_w: str = "3.20000", stock_h: str = "8.50000",
              foot_y: str = "8.20000") -> bytes:
    # A per-record card: single query, one repeating frame filling a
    # declared 3.5x8.5 main section. ``field_w`` widens a field past the
    # stock for the negative leg.
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<report name="WALLET_CARD" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q_CARD">
      <select canParse="no"><![CDATA[SELECT HOLDER_NAME, CRED_NO, EXP_DT FROM CARDS]]></select>
      <group name="G_CARD">
        <dataItem name="HOLDER_NAME" datatype="vchar2" columnOrder="1" defaultLabel="Holder"/>
        <dataItem name="CRED_NO" datatype="vchar2" columnOrder="2" defaultLabel="Credential"/>
        <dataItem name="EXP_DT" datatype="date" columnOrder="3" defaultLabel="Expires"/>
      </group>
    </dataSource>
  </data>
  <layout>
  <section name="main" width="3.50000" height="{stock_h}">
    <body width="3.49976" height="{stock_h}">
      <location x="0.00012" y="0.00000"/>
      <repeatingFrame name="R_G_CARD" source="G_CARD" printDirection="down">
        <geometryInfo x="0.00000" y="0.00000" width="3.49500" height="1.80000"/>
        <field name="F_HOLDER_NAME" source="HOLDER_NAME" alignment="center">
          <font face="Times" size="12" bold="yes"/>
          <geometryInfo x="0.05000" y="0.05000" width="{field_w}" height="0.25000"/></field>
        <text name="B_PROSE"><geometryInfo x="0.05000" y="0.40000" width="3.30000" height="0.60000"/>
          <textSegment><font face="Times" size="9"/><string><![CDATA[holds the credential shown below through the listed expiration date.]]></string></textSegment></text>
        <field name="F_CRED_NO" source="CRED_NO" alignment="center">
          <font face="Times" size="11" bold="yes"/>
          <geometryInfo x="0.05000" y="1.10000" width="3.30000" height="0.22000"/></field>
        <field name="F_EXP_DT" source="EXP_DT" alignment="end">
          <font face="Times" size="9"/>
          <geometryInfo x="2.00000" y="1.45000" width="1.40000" height="0.20000"/></field>
        <text name="B_FOOT"><geometryInfo x="0.05000" y="{foot_y}" width="3.30000" height="0.25000"/>
          <textSegment><font face="Times" size="8"/><string><![CDATA[Issued by the licensing office.]]></string></textSegment></text>
      </repeatingFrame>
    </body>
  </section>
  </layout>
</report>""".encode()


def test_declared_card_stock_is_the_page():
    rdl = convert(_card_xml())["rdl_xml"]
    assert re.search(r"<PageWidth>3\.50in</PageWidth>", rdl), (
        "a declared 3.5in-wide main section is real stock — the page must "
        "print at the declared width, not Letter")
    assert re.search(r"<PageHeight>8\.50in</PageHeight>", rdl), (
        "declared card height must be the page height (not grown to 11in+)")


def test_small_stock_margins_scale_to_fit_body():
    rdl = convert(_card_xml())["rdl_xml"]
    lm = float(re.search(r"<LeftMargin>([\d.]+)in</LeftMargin>", rdl).group(1))
    rm = float(re.search(r"<RightMargin>([\d.]+)in</RightMargin>", rdl).group(1))
    body_w = float(re.search(r"</Body>\s*<Width>([\d.]+)in</Width>",
                             rdl).group(1))
    # blank-page rule: body width strictly < page width - margins
    assert body_w + lm + rm < 3.50 + 1e-6, (
        f"body {body_w} + margins {lm}+{rm} must fit the 3.5in stock "
        "(otherwise the engine emits horizontal-overflow blank pages)")


def test_small_stock_record_row_fits_one_page():
    rdl = convert(_card_xml())["rdl_xml"]
    rows = [float(h) for h in re.findall(
        r"<TablixRow>\s*<Height>([\d.]+)in</Height>", rdl)]
    assert rows, "expected a record row"
    tm = float(re.search(r"<TopMargin>([\d.]+)in</TopMargin>", rdl).group(1))
    bm = float(re.search(r"<BottomMargin>([\d.]+)in</BottomMargin>", rdl).group(1))
    printable = 8.50 - tm - bm
    assert max(rows) <= printable + 1e-6, (
        f"record row {max(rows)} exceeds the printable stock {printable} — "
        "every record would emit a near-blank spill page")


def test_sub_letter_stock_height_is_honored():
    # A 3.5x5.0in label: below the 8in "paper band", so only the
    # small-paper rule can honor it.
    rdl = convert(_card_xml(stock_h="5.00000", foot_y="4.60000"))["rdl_xml"]
    assert re.search(r"<PageHeight>5\.00in</PageHeight>", rdl), (
        "a declared sub-8in stock height must be the page height")
    assert re.search(r"<PageWidth>3\.50in</PageWidth>", rdl)


def test_small_stock_keeps_declared_body_width():
    # The margins must scale down rather than the BODY being narrowed —
    # a body clamped below the declared 3.49976 displaces/clips content.
    rdl = convert(_card_xml())["rdl_xml"]
    body_w = float(re.search(r"</Body>\s*<Width>([\d.]+)in</Width>",
                             rdl).group(1))
    assert body_w >= 3.40, (
        f"body narrowed to {body_w}in — margins must scale for small "
        "stock so the declared 3.5in body keeps its width")


def test_content_wider_than_declared_stock_keeps_normal_sheet():
    # Negative leg: a field spanning 7in cannot fit 3.5in stock — the
    # small-paper honor must decline and keep the normal page.
    rdl = convert(_card_xml(field_w="7.00000"))["rdl_xml"]
    pw = float(re.search(r"<PageWidth>([\d.]+)in</PageWidth>", rdl).group(1))
    assert pw >= 8.5, (
        "content wider than the declared small stock must decline the "
        "small-paper honor (fit rule), keeping the normal sheet")
