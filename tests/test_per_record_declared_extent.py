"""PER-RECORD DECLARED EXTENT: a record's objects print at their DECLARED y,
inside the record's DECLARED body height, with no synthesized slack between
frames.

Four inflation / content-loss classes are guarded here, all measured on the
truth-paired corpus:

1. OUT-OF-BOX MEMBERS. An export can declare a member well BELOW the box of
   the frame that owns it. The elasticity flow treated that as the frame
   having GROWN and opened a gap of the same size under it, shifting every
   sibling below by inches (measured +9.45in on one record, three sheets per
   record where the truth prints fewer). Only what is declared INSIDE a
   frame's own box may grow it.

2. DECLARED BODY HEIGHT. ``<section name="main"><body height="H">`` states how
   tall one record's drawing area is. The comfort pad the row carries below its
   last item may not push the row past H when the content itself fits.

3. SECTION PRINT ORDER. A content-bearing ``<section name="trailer">`` prints
   ALL of its own records AFTER the last record of the main section — it is
   never a band inside the per-record region. Truth measurement (a 307-page
   export whose source declares header + main + trailer, all record-bearing):
   page 1 is the header section, pages 2..128 are ALL main records, pages
   129..307 are ALL trailer records. Nesting the trailer inside the record
   loop interleaved one trailer page after every main record instead.
   So the trailer must be its OWN top-level data region, stacked below the
   record region, iterating its section's dataset, with a page break before
   its first record. (This supersedes the earlier "emitted below the record,
   inside the record cell" placement rule: staying inside the record cell
   cannot express the measured print order at all. The assertion below is
   stricter — no trailer geometry may reach the record cell whatsoever, so
   the paint-through failure the old rule guarded is impossible by
   construction, AND the ordering is now checked as well.)

4. ONE DECLARED FRAME = ONE REGION. Sibling repeating frames bound to the SAME
   Oracle group are two separate declarations at two separate places on the
   sheet (a signature block in the header band and a second one in the body).
   Folding them into a single region by ``source`` deleted the second frame's
   content outright and handed the first frame members declared inches below
   its own box — the exact shape class 1 then had to defend against.

Synthetic fixtures only — no client data.
"""
from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402

NS = ("{http://schemas.microsoft.com/sqlserver/reporting/"
      "2008/01/reportdefinition}")

# section_main declares a 9.00in body. Inside it:
#   R_SIGN_TOP  declared y=1.00 h=0.40  (bound to G_SIGN) and holding an
#               OUT-OF-BOX member, F_SIGN_OOB, declared at y=2.90 — 1.50in
#               below its own 1.00..1.40 box
#   B_HEADING   declared y=2.00
#   M_PARAS     declared y=3.00 h=4.00  (two body paragraphs)
#   R_SIGN_FOOT declared y=7.50 h=0.50  (bound to G_SIGN TOO — a SECOND
#               declaration at its own place on the sheet, which must stay a
#               region of its own)
LETTER_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<report name="NOTICE_LETTER" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q_MAIN">
      <select canParse="no"><![CDATA[SELECT ACCT_NO, ACCT_NAME, SIGNER FROM ACCOUNTS]]></select>
      <group name="G_MAIN">
        <dataItem name="ACCT_NO" datatype="vchar2" columnOrder="1" defaultLabel="Account"/>
        <dataItem name="ACCT_NAME" datatype="vchar2" columnOrder="2" defaultLabel="Name"/>
      </group>
      <group name="G_SIGN">
        <dataItem name="SIGNER" datatype="vchar2" columnOrder="3" defaultLabel="Signer"/>
      </group>
    </dataSource>
  </data>
  <layout>
  <section name="main" repeatOn="G_MAIN">
    <body width="7.50000" height="9.00000">
      <location x="0.50000" y="0.50000"/>
      <frame name="M_DOC">
        <geometryInfo x="0.00000" y="0.00000" width="7.50000" height="9.00000"/>
        <repeatingFrame name="R_SIGN_TOP" source="G_SIGN" printDirection="down">
          <geometryInfo x="0.00000" y="1.00000" width="7.50000" height="0.40000"/>
          <field name="F_SIGN_TOP" source="SIGNER">
            <font face="Arial" size="10"/>
            <geometryInfo x="0.20000" y="1.00000" width="3.00000" height="0.20000"/></field>
          <field name="F_SIGN_OOB" source="SIGNER">
            <font face="Arial" size="10"/>
            <geometryInfo x="0.20000" y="2.90000" width="3.00000" height="0.20000"/></field>
        </repeatingFrame>
        <text name="B_HEADING">
          <geometryInfo x="0.20000" y="2.00000" width="6.00000" height="0.22000"/>
          <textSegment><font face="Arial" size="12" bold="yes"/>
          <string><![CDATA[Statement of Account]]></string></textSegment></text>
        <frame name="M_PARAS">
          <geometryInfo x="0.00000" y="3.00000" width="7.50000" height="4.00000"/>
          <text name="B_PARA_ONE">
            <geometryInfo x="0.20000" y="3.10000" width="6.00000" height="0.40000"/>
            <textSegment><font face="Arial" size="10"/>
            <string><![CDATA[Balance summary for the period shown above, prepared from the
records held on file for this account on the statement date.]]></string></textSegment></text>
          <field name="F_ACCT_NO" source="ACCT_NO">
            <font face="Arial" size="10"/>
            <geometryInfo x="0.20000" y="4.00000" width="3.00000" height="0.20000"/></field>
          <text name="B_PARA_TWO">
            <geometryInfo x="0.20000" y="4.50000" width="6.00000" height="0.40000"/>
            <textSegment><font face="Arial" size="10"/>
            <string><![CDATA[Amounts remaining unpaid after the due date accrue interest at
the published rate. Please retain this statement for your records.]]></string></textSegment></text>
        </frame>
        <repeatingFrame name="R_SIGN_FOOT" source="G_SIGN" printDirection="down">
          <geometryInfo x="0.00000" y="7.50000" width="7.50000" height="0.50000"/>
          <field name="F_SIGN_FOOT" source="SIGNER">
            <font face="Arial" size="10"/>
            <geometryInfo x="0.20000" y="7.50000" width="3.00000" height="0.30000"/></field>
        </repeatingFrame>
      </frame>
    </body>
  </section>
  </layout>
</report>"""

TRAILER_SECTION = b"""  <section name="trailer" repeatOn="G_MAIN">
    <body width="7.50000" height="9.00000">
      <location x="0.50000" y="0.50000"/>
      <frame name="T_VOUCHER">
        <geometryInfo x="0.00000" y="0.00000" width="7.50000" height="3.00000"/>
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

LETTER_WITH_TRAILER_XML = LETTER_XML.replace(
    b'  <section name="main" repeatOn="G_MAIN">',
    TRAILER_SECTION + b'  <section name="main" repeatOn="G_MAIN">')

DECLARED_BODY_H = 9.00


def _inches(txt):
    try:
        return float((txt or "0in").replace("in", "").strip())
    except ValueError:
        return 0.0


def _record_cell(rdl):
    """The <CellContents> of the per-record Tablix row."""
    root = ET.fromstring(rdl)
    for tab in root.iter(f"{NS}Tablix"):
        if tab.get("Name") != "Tablix_Record":
            continue
        for cc in tab.iter(f"{NS}CellContents"):
            return cc
    return None


def _positions(cell):
    """{item name: (top, height)} for every positioned item in the record."""
    out = {}
    for el in cell.iter():
        nm = el.get("Name")
        if not nm:
            continue
        top = el.find(f"{NS}Top")
        if top is None:
            continue
        out[nm] = (_inches(top.text), _inches(el.findtext(f"{NS}Height")))
    return out


def _textbox_top_for(cell, literal):
    """Top (inches) of the textbox whose value carries ``literal``."""
    for tb in cell.iter(f"{NS}Textbox"):
        if literal in (tb.findtext(f".//{NS}Value") or ""):
            return _inches(tb.findtext(f"{NS}Top"))
    return None


def test_out_of_box_member_does_not_push_declared_siblings():
    """A member declared BELOW a frame's own box is not that frame growing:
    the siblings declared under the frame keep their declared y."""
    cell = _record_cell(convert(LETTER_XML)["rdl_xml"])
    assert cell is not None, "expected a per-record Tablix"
    heading_top = _textbox_top_for(cell, "Statement of Account")
    assert heading_top is not None, "the heading must be emitted"
    assert abs(heading_top - 2.00) < 0.05, (
        f"heading emitted at {heading_top}in, declared 2.00in — the "
        "out-of-box member above it opened a synthesized gap")
    paras = [(nm, tp) for nm, (tp, _h) in _positions(cell).items()
             if nm.startswith("RecP_Rect_")]
    tops = sorted(tp for _nm, tp in paras)
    assert any(abs(tp - 3.00) < 0.05 for tp in tops), (
        f"no frame rect at the declared 3.00in (rect tops {tops}) — the "
        "paragraph frame was pushed off its declaration")
    assert any(abs(tp - 7.50) < 0.05 for tp in tops), (
        f"no frame rect at the declared 7.50in (rect tops {tops}) — the "
        "bottom signature region was pushed off its declaration")


def test_sibling_repeating_frames_over_one_group_stay_distinct_regions():
    """Two <repeatingFrame> declarations bound to the SAME Oracle group are
    TWO regions at TWO declared places. Folding them into one (keyed by
    ``source``) deletes the second declaration's content and hands the first
    one members declared inches below its own box."""
    cell = _record_cell(convert(LETTER_XML)["rdl_xml"])
    assert cell is not None, "expected a per-record Tablix"
    tops = sorted(tp for nm, (tp, _h) in _positions(cell).items()
                  if nm.startswith("RecP_Rect_"))
    for declared in (1.00, 7.50):
        assert any(abs(tp - declared) < 0.05 for tp in tops), (
            f"no region at the declared {declared:.2f}in (rect tops {tops})"
            " — the two frames over one group were merged into one")
    # ...and neither region may swell to swallow the other's declared band.
    boxes = [(tp, h) for nm, (tp, h) in _positions(cell).items()
             if nm.startswith("RecP_Rect_")]
    top_box = min((b for b in boxes if abs(b[0] - 1.00) < 0.05),
                  key=lambda b: b[1], default=None)
    assert top_box is not None
    assert top_box[0] + top_box[1] <= 7.50 + 0.01, (
        f"the 0.40in region at 1.00in emitted {top_box[1]}in tall — it "
        "absorbed the second declaration's members")


def test_record_row_stays_within_declared_body_height():
    """<body height="H"> is the record's drawing area; the comfort pad may
    not push the row past it when the content fits."""
    rdl = convert(LETTER_XML)["rdl_xml"]
    rows = [float(h) for h in re.findall(
        r"<TablixRow>\s*<Height>([\d.]+)in</Height>", rdl)]
    assert rows, "expected a record row"
    assert max(rows) <= DECLARED_BODY_H + 0.01, (
        f"record row {max(rows)}in exceeds the declared body height "
        f"{DECLARED_BODY_H}in — synthesized slack inflated the record")


def test_declared_record_extent_matches_the_declaration():
    """The record's own frame rect is its declared height, not a summed one."""
    cell = _record_cell(convert(LETTER_XML)["rdl_xml"])
    pos = _positions(cell)
    assert "RecP_Rect_0" in pos, "expected the record's outer frame rect"
    _top, height = pos["RecP_Rect_0"]
    assert height <= DECLARED_BODY_H + 0.01, (
        f"the record frame emitted {height}in for a declared "
        f"{DECLARED_BODY_H}in box")


TRAILER_LITERAL = "Payment Voucher"


def _body_items(rdl):
    """{item name: (top, height)} for the BODY's own top-level items."""
    root = ET.fromstring(rdl)
    body = root.find(f"{NS}Body")
    items = body.find(f"{NS}ReportItems") if body is not None else None
    out = {}
    for el in list(items if items is not None else []):
        nm = el.get("Name") or ""
        out[nm] = (_inches(el.findtext(f"{NS}Top")),
                   _inches(el.findtext(f"{NS}Height")))
    return out


def _tablix_named(rdl, name):
    root = ET.fromstring(rdl)
    for tab in root.iter(f"{NS}Tablix"):
        if tab.get("Name") == name:
            return tab
    return None


def _carries(el, literal):
    """True when any textbox under ``el`` prints ``literal``."""
    return any(literal in (tb.findtext(f".//{NS}Value") or "")
               for tb in el.iter(f"{NS}Textbox"))


def test_trailer_section_never_reaches_the_per_record_region():
    """SECTION PRINT ORDER, half 1: the trailer section owns its own records,
    so NOTHING of it may be emitted inside the per-record region. Nesting it
    there interleaves a trailer page after every main record (the truth
    export prints all main records first, then all trailer records) — and it
    is what let the trailer paint through the record content."""
    rdl = convert(LETTER_WITH_TRAILER_XML)["rdl_xml"]
    cell = _record_cell(rdl)
    assert cell is not None, "expected a per-record Tablix"
    pos = _positions(cell)
    assert any(nm.startswith("RecP_Rect_") for nm in pos), (
        f"the record's own frames stopped emitting: {sorted(pos)}")
    assert not _carries(cell, TRAILER_LITERAL), (
        "the trailer section printed INSIDE the per-record region — its "
        "records would interleave with the main section's")


def test_trailer_section_is_its_own_region_after_every_main_record():
    """SECTION PRINT ORDER, half 2: the trailer section is a separate
    top-level data region, stacked strictly BELOW the record region, bound
    to a declared dataset, whose detail group breaks the page — so every
    main record prints before the first trailer record."""
    rdl = convert(LETTER_WITH_TRAILER_XML)["rdl_xml"]
    items = _body_items(rdl)
    rec = _tablix_named(rdl, "Tablix_Record")
    trl = _tablix_named(rdl, "Tablix_SectionTrailer")
    assert rec is not None, "expected the per-record data region"
    assert trl is not None, (
        f"expected a separate trailer data region, body items: {sorted(items)}")
    assert _carries(trl, TRAILER_LITERAL), (
        "the trailer region does not print the trailer section's content")
    r_top, r_h = items["Tablix_Record"]
    t_top, t_h = items["Tablix_SectionTrailer"]
    assert t_top >= r_top + r_h - 0.01, (
        f"the trailer region starts at {t_top}in, inside the record region "
        f"({r_top}in + {r_h}in) — the two would overlap on the sheet")
    # ...bound to a dataset the report actually declares.
    ds = trl.findtext(f"{NS}DataSetName") or ""
    declared = {d.get("Name") for d in ET.fromstring(rdl).iter(f"{NS}DataSet")}
    assert ds in declared, (
        f"the trailer region binds {ds!r}, not one of {sorted(declared)}")
    # ...and its detail group breaks the page, so the first trailer record
    # leaves the last main record's sheet.
    breaks = [g.findtext(f"{NS}PageBreak/{NS}BreakLocation")
              for g in trl.iter(f"{NS}Group")
              if g.get("Name", "").startswith("Details_")]
    assert breaks and breaks[0] == "Start", (
        f"the trailer detail group carries page breaks {breaks} — without a "
        "Start break its first record shares the last main record's page")
    # The body must actually reserve the region's inches.
    body_h = _inches(ET.fromstring(rdl).find(f"{NS}Body")
                     .findtext(f"{NS}Height"))
    assert body_h >= t_top + t_h - 0.01, (
        f"body {body_h}in ends above the trailer region "
        f"({t_top}in + {t_h}in) — the region would be clipped away")


def test_single_section_report_grows_no_trailer_region():
    """No trailer section declared -> nothing extra is emitted. The fix may
    not invent a second data region for the single-section family."""
    rdl = convert(LETTER_XML)["rdl_xml"]
    assert _tablix_named(rdl, "Tablix_SectionTrailer") is None, (
        "a report with no trailer section grew a trailer data region")
