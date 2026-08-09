"""PER-SECTION SHEET MARGINS, and NO EMPTY PAGE BANDS.

Oracle gives EVERY section its own sheet margin: ``<section><body><location>``
is where that section's body rectangle sits on the paper, and each section may
state a different one.  SSRS has exactly ONE page margin.  The two are
reconciled by declaration, not by picking a favourite:

  * the page margin is the SMALLEST declared section origin (an item above or
    left of the page margin cannot be expressed at all -- a body Top/Left is
    never negative), and

  * every other section carries the difference as a positive offset inside its
    own region, so each declared object still lands at its declared PAPER
    position.

A single-section report resolves to exactly the main section's origin with a
zero shift, which is what makes the rule inert for every report that declares
one body.

The second half of the file guards the page BANDS.  A PageHeader/PageFooter
that carries nothing is furniture the source never declared, and SSRS charges
its full height to every page.  Measured on the truth-paired corpus: an empty
0.25in header plus an empty 0.60in footer left 9.15in of printable height on
an 11in sheet for a source declaring a 10.60in body -- which is why records
paginated where the truth prints one page.  So an empty band is not emitted,
and the printable area reaches the declared body.

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

# --- declared geometry of the fixture, in one place ------------------------
HDR_ORIGIN = (0.25, 0.10)      # <section name="header"><body><location>
MAIN_ORIGIN = (0.50, 0.40)     # main omits x -> Oracle's default inset 0.50
TRL_ORIGIN = (0.375, 0.60)
MAIN_BODY_H = 9.00
PAGE_H = 11.00

HDR_LABEL_XY = (0.25, 0.30)    # B_CRITERIA_ONE inside the header body
REC_FIELD_Y = 1.00             # F_ACCT_NO inside the main body
TRL_TITLE_XY = (0.20, 0.10)    # B_VOUCHER_TITLE inside the trailer body

_HEADER_SECTION = b"""  <section name="header">
    <body width="8.00000" height="10.00000">
      <location x="0.25000" y="0.10000"/>
      <frame name="H_FORM">
        <geometryInfo x="0.00000" y="0.00000" width="8.00000" height="3.00000"/>
        <text name="B_CRITERIA_ONE">
          <geometryInfo x="0.25000" y="0.30000" width="1.60000" height="0.22000"/>
          <textSegment><font face="Arial" size="10"/>
          <string><![CDATA[Report:]]></string></textSegment></text>
        <field name="F_CRITERIA_ONE" source="ACCT_NO">
          <font face="Arial" size="10"/>
          <geometryInfo x="2.00000" y="0.30000" width="4.00000" height="0.22000"/></field>
        <text name="B_CRITERIA_TWO">
          <geometryInfo x="0.25000" y="0.80000" width="1.60000" height="0.22000"/>
          <textSegment><font face="Arial" size="10"/>
          <string><![CDATA[Run Date:]]></string></textSegment></text>
        <field name="F_CRITERIA_TWO" source="ACCT_NAME">
          <font face="Arial" size="10"/>
          <geometryInfo x="2.00000" y="0.80000" width="4.00000" height="0.22000"/></field>
        <text name="B_CRITERIA_THREE">
          <geometryInfo x="0.25000" y="1.30000" width="1.60000" height="0.22000"/>
          <textSegment><font face="Arial" size="10"/>
          <string><![CDATA[Sort Order:]]></string></textSegment></text>
        <field name="F_CRITERIA_THREE" source="SIGNER">
          <font face="Arial" size="10"/>
          <geometryInfo x="2.00000" y="1.30000" width="4.00000" height="0.22000"/></field>
      </frame>
    </body>
  </section>
"""

_TRAILER_SECTION = b"""  <section name="trailer" repeatOn="G_MAIN">
    <body width="7.60000" height="8.00000">
      <location x="0.37500" y="0.60000"/>
      <frame name="T_VOUCHER">
        <geometryInfo x="0.00000" y="0.00000" width="7.60000" height="3.00000"/>
        <text name="B_VOUCHER_TITLE">
          <geometryInfo x="0.20000" y="0.10000" width="6.00000" height="0.22000"/>
          <textSegment><font face="Arial" size="12" bold="yes"/>
          <string><![CDATA[Payment Voucher]]></string></textSegment></text>
        <field name="F_V_ACCT" source="ACCT_NO">
          <font face="Arial" size="10"/>
          <geometryInfo x="0.20000" y="1.00000" width="3.00000" height="0.20000"/></field>
      </frame>
    </body>
  </section>
"""

# section_main omits <body width=> and the x axis of its origin, exactly as an
# Oracle export does for a body sitting at the default inset.
_MAIN_SECTION = b"""  <section name="main" repeatOn="G_MAIN">
    <body height="9.00000">
      <location y="0.40000"/>
      <frame name="M_DOC">
        <geometryInfo x="0.00000" y="0.00000" width="7.50000" height="8.00000"/>
        <text name="B_HEADING">
          <geometryInfo x="0.20000" y="0.20000" width="6.00000" height="0.22000"/>
          <textSegment><font face="Arial" size="12" bold="yes"/>
          <string><![CDATA[Statement of Account]]></string></textSegment></text>
        <field name="F_ACCT_NO" source="ACCT_NO">
          <font face="Arial" size="10"/>
          <geometryInfo x="0.20000" y="1.00000" width="3.00000" height="0.20000"/></field>
        <field name="F_ACCT_NAME" source="ACCT_NAME">
          <font face="Arial" size="10"/>
          <geometryInfo x="0.20000" y="1.50000" width="3.00000" height="0.20000"/></field>
      </frame>
    </body>
  </section>
"""

_PREAMBLE = b"""<?xml version="1.0" encoding="UTF-8"?>
<report name="ACCOUNT_NOTICE" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q_MAIN">
      <select canParse="no"><![CDATA[SELECT ACCT_NO, ACCT_NAME, SIGNER FROM ACCOUNTS]]></select>
      <group name="G_MAIN">
        <dataItem name="ACCT_NO" datatype="vchar2" columnOrder="1" defaultLabel="Account"/>
        <dataItem name="ACCT_NAME" datatype="vchar2" columnOrder="2" defaultLabel="Name"/>
        <dataItem name="SIGNER" datatype="vchar2" columnOrder="3" defaultLabel="Signer"/>
      </group>
    </dataSource>
  </data>
  <layout>
"""
_EPILOGUE = b"""  </layout>
</report>"""

THREE_SECTION_XML = (_PREAMBLE + _HEADER_SECTION + _TRAILER_SECTION
                     + _MAIN_SECTION + _EPILOGUE)
ONE_SECTION_XML = _PREAMBLE + _MAIN_SECTION + _EPILOGUE

# A plain columnar LISTING. It matters that this one is here: the per-record
# document path has its own declared-sheet reconciliation, so a listing is the
# fixture that isolates the page builder itself.
LIST_ORIGIN = (0.30, 0.20)
LIST_BODY_H = 9.50
LIST_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<report name="ACCOUNT_LIST" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q_MAIN">
      <select canParse="no"><![CDATA[SELECT ACCT_NO, ACCT_NAME FROM ACCOUNTS]]></select>
      <group name="G_ROW">
        <dataItem name="ACCT_NO" datatype="vchar2" columnOrder="1" defaultLabel="Account"/>
        <dataItem name="ACCT_NAME" datatype="vchar2" columnOrder="2" defaultLabel="Name"/>
      </group>
    </dataSource>
  </data>
  <layout>
  <section name="main" width="8.50000" height="11.00000">
    <body width="7.90000" height="9.50000">
      <location x="0.30000" y="0.20000"/>
      <repeatingFrame name="R_ROW" source="G_ROW" printDirection="down">
        <geometryInfo x="0.00000" y="0.40000" width="7.90000" height="0.25000"/>
        <field name="F_ACCT_NO" source="ACCT_NO">
          <font face="Arial" size="10"/>
          <geometryInfo x="0.00000" y="0.40000" width="2.00000" height="0.20000"/></field>
        <field name="F_ACCT_NAME" source="ACCT_NAME">
          <font face="Arial" size="10"/>
          <geometryInfo x="2.20000" y="0.40000" width="4.00000" height="0.20000"/></field>
      </repeatingFrame>
    </body>
  </section>
  </layout>
</report>"""


def _inches(txt) -> float:
    try:
        return float((txt or "0in").replace("in", "").strip())
    except ValueError:
        return 0.0


def _page(rdl):
    return ET.fromstring(rdl).find(NS + "Page")


def _band_height(page, tag) -> float:
    """Height a band actually costs the page (0 when it is not emitted)."""
    band = page.find(NS + tag)
    return 0.0 if band is None else _inches(band.findtext(NS + "Height"))


def _named(root, name):
    for el in root.iter():
        if el.get("Name") == name:
            return el
    return None


def _region(root, name):
    """A top-level body region by name, with its (Left, Top) in inches."""
    el = _named(root, name)
    assert el is not None, f"expected region {name} in the body"
    return el, _inches(el.findtext(NS + "Left")), _inches(el.findtext(NS + "Top"))


def _box_with(el, literal):
    """(Left, Top) of the descendant textbox carrying ``literal``."""
    for tb in el.iter(NS + "Textbox"):
        if literal in (tb.findtext(f".//{NS}Value") or ""):
            return _inches(tb.findtext(NS + "Left")), _inches(tb.findtext(NS + "Top"))
    return None


# --------------------------------------------------------------------------
# per-section sheet margins
# --------------------------------------------------------------------------

def test_page_margin_is_the_smallest_declared_section_origin():
    """Three declared origins collapse to ONE page margin -- the smallest, so
    every section's shift is expressible."""
    page = _page(convert(THREE_SECTION_XML)["rdl_xml"])
    assert abs(_inches(page.findtext(NS + "LeftMargin"))
               - min(HDR_ORIGIN[0], MAIN_ORIGIN[0], TRL_ORIGIN[0])) < 0.001
    assert abs(_inches(page.findtext(NS + "TopMargin"))
               - min(HDR_ORIGIN[1], MAIN_ORIGIN[1], TRL_ORIGIN[1])) < 0.001


def test_every_section_lands_at_its_declared_paper_position():
    """Each region carries its own origin minus the page margin, so a declared
    object prints at (section origin + its declared offset) on the paper."""
    rdl = convert(THREE_SECTION_XML)["rdl_xml"]
    root = ET.fromstring(rdl)
    page = root.find(NS + "Page")
    lm = _inches(page.findtext(NS + "LeftMargin"))
    tm = _inches(page.findtext(NS + "TopMargin"))

    cover, c_left, c_top = _region(root, "Rect_CoverPage")
    box = _box_with(cover, "Report:")
    assert box is not None, "the declared cover label must be emitted"
    assert abs(lm + c_left + box[0] - (HDR_ORIGIN[0] + HDR_LABEL_XY[0])) < 0.02
    assert abs(tm + c_top + box[1] - (HDR_ORIGIN[1] + HDR_LABEL_XY[1])) < 0.02

    # The record region page-breaks, so its own body Top is not what places it
    # vertically -- the offset lives on the content inside the record cell.
    rec, r_left, _r_top = _region(root, "Tablix_Record")
    assert abs(lm + r_left - MAIN_ORIGIN[0]) < 0.001
    inner = _named(rec, "RecP_Rect_0")
    assert inner is not None, "expected the record's declared frame rectangle"
    assert abs(tm + _inches(inner.findtext(NS + "Top"))
               - MAIN_ORIGIN[1]) < 0.001

    trl, t_left, _t_top = _region(root, "Tablix_SectionTrailer")
    assert abs(lm + t_left - TRL_ORIGIN[0]) < 0.001
    t_inner = _named(trl, "SecT_Rect_0")
    assert t_inner is not None, "expected the trailer's declared frame rectangle"
    assert abs(tm + _inches(t_inner.findtext(NS + "Top"))
               - TRL_ORIGIN[1]) < 0.001


def test_single_section_report_keeps_the_main_origin_and_shifts_nothing():
    """The rule is inert where one body is declared: the page margin IS the
    main origin and no region is displaced."""
    root = ET.fromstring(convert(ONE_SECTION_XML)["rdl_xml"])
    page = root.find(NS + "Page")
    assert abs(_inches(page.findtext(NS + "LeftMargin")) - MAIN_ORIGIN[0]) < 0.001
    assert abs(_inches(page.findtext(NS + "TopMargin")) - MAIN_ORIGIN[1]) < 0.001
    _rec, r_left, _r_top = _region(root, "Tablix_Record")
    assert abs(r_left) < 0.001, "a lone section is never displaced"
    inner = _named(root, "RecP_Rect_0")
    assert inner is not None
    assert abs(_inches(inner.findtext(NS + "Top"))) < 0.001


# --------------------------------------------------------------------------
# no empty page bands / printable-height arithmetic
# --------------------------------------------------------------------------

def test_no_empty_page_band_is_emitted():
    """A band with nothing in it is never emitted -- on any archetype."""
    for xml in (THREE_SECTION_XML, ONE_SECTION_XML, LIST_XML):
        page = _page(convert(xml)["rdl_xml"])
        for tag in ("PageHeader", "PageFooter"):
            band = page.find(NS + tag)
            if band is None:
                continue
            items = band.find(NS + "ReportItems")
            assert items is not None and len(list(items)) > 0, (
                f"{tag} was emitted with no content -- it steals "
                f"{_inches(band.findtext(NS + 'Height'))}in from every page")


def test_printable_height_reaches_the_declared_body():
    """PageHeight - margins - bands must cover the body the source declares,
    or every record paginates on page setup alone."""
    for xml, body_h in ((THREE_SECTION_XML, MAIN_BODY_H),
                        (ONE_SECTION_XML, MAIN_BODY_H),
                        (LIST_XML, LIST_BODY_H)):
        page = _page(convert(xml)["rdl_xml"])
        printable = (_inches(page.findtext(NS + "PageHeight"))
                     - _inches(page.findtext(NS + "TopMargin"))
                     - _inches(page.findtext(NS + "BottomMargin"))
                     - _band_height(page, "PageHeader")
                     - _band_height(page, "PageFooter"))
        assert printable >= body_h - 0.001, (
            f"printable {printable:.3f}in < declared body {body_h}in")


def test_listing_page_origin_is_its_declared_body_origin():
    """The page builder's own answer, isolated from the per-record path: the
    margins ARE the section's declared body origin."""
    page = _page(convert(LIST_XML)["rdl_xml"])
    assert abs(_inches(page.findtext(NS + "LeftMargin")) - LIST_ORIGIN[0]) < 0.001
    assert abs(_inches(page.findtext(NS + "TopMargin")) - LIST_ORIGIN[1]) < 0.001


#: a listing that declares page chrome AND a body filling almost the sheet.
#: chrome top 0.20 + band 0.25 = body top 0.45, body 10.30 -> 0.25in of paper
#: is all that is left underneath, and the synthesized half inch does not fit.
CHROME_BODY_H = 10.30
CHROME_LIST_XML = LIST_XML.replace(
    b'    <body width="7.90000" height="9.50000">\n'
    b'      <location x="0.30000" y="0.20000"/>',
    b'    <margin>\n'
    b'      <text name="B_PAGE_TITLE">\n'
    b'        <geometryInfo x="0.50000" y="0.20000" width="6.00000" height="0.25000"/>\n'
    b'        <textSegment><font face="Arial" size="12" bold="yes"/>\n'
    b'        <string><![CDATA[Account Listing]]></string></textSegment></text>\n'
    b'    </margin>\n'
    b'    <body width="7.90000" height="10.30000">\n'
    b'      <location x="0.30000" y="0.45000"/>')


def test_declared_body_height_reclaims_the_bottom_margin_under_chrome():
    """With declared chrome the band fixes where the body BEGINS; the declared
    <body height=> fixes how far it runs. The paper under it is the bottom
    margin -- a synthesized half inch there is height the declaration already
    spent, and the content paginates by exactly the difference."""
    root = ET.fromstring(convert(CHROME_LIST_XML)["rdl_xml"])
    page = root.find(NS + "Page")
    hdr = page.find(NS + "PageHeader")
    assert hdr is not None, "declared chrome must get its band"
    printable = (_inches(page.findtext(NS + "PageHeight"))
                 - _inches(page.findtext(NS + "TopMargin"))
                 - _inches(page.findtext(NS + "BottomMargin"))
                 - _band_height(page, "PageHeader")
                 - _band_height(page, "PageFooter"))
    assert printable >= CHROME_BODY_H - 0.001, (
        f"printable {printable:.3f}in < declared body {CHROME_BODY_H}in")


def test_declared_chrome_band_is_still_emitted_and_still_carries_its_items():
    """The empty-band rule removes EMPTY bands only: a source that declares
    <margin> chrome keeps its band, its items and its declared band top."""
    xml = THREE_SECTION_XML.replace(
        b'  <section name="main" repeatOn="G_MAIN">\n    <body height="9.00000">',
        b'  <section name="main" repeatOn="G_MAIN">\n'
        b'    <margin>\n'
        b'      <text name="B_PAGE_TITLE">\n'
        b'        <geometryInfo x="0.50000" y="0.20000" width="6.00000" height="0.25000"/>\n'
        b'        <textSegment><font face="Arial" size="12" bold="yes"/>\n'
        b'        <string><![CDATA[Account Notice]]></string></textSegment></text>\n'
        b'    </margin>\n'
        b'    <body height="9.00000">')
    page = _page(convert(xml)["rdl_xml"])
    hdr = page.find(NS + "PageHeader")
    assert hdr is not None, "declared chrome must still get its band"
    items = hdr.find(NS + "ReportItems")
    assert items is not None and len(list(items)) > 0
    # the band's own top offset IS the page TopMargin (declared chrome rule)
    assert abs(_inches(page.findtext(NS + "TopMargin")) - 0.20) < 0.001
