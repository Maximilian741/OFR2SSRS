"""DYNAMIC VARIANT-BAND COLLAPSE: a record whose declared section body fits
the declared paper must occupy ONE page.

An Oracle per-record design may declare EVERY format-trigger variant in one
vertical stack (an email letterhead band over a mail letterhead band over a
memo header), so the design-time record frame is TALLER than the declared
section body. At runtime Oracle prints one variant per record and reclaims
the space of the ones the trigger suppressed (implicit anchoring), so every
record still fits its declared body — the truth PDF prints one sheet per
record. SSRS reserves a hidden RECTANGLE's box, so emitting the stack
verbatim spreads every record onto a second sheet (measured on a truth-paired
invoice run: 2 sheets per record against the truth's 1, the second sheet
holding only the record's tail).

Engine-measured mechanics the fix is built on (ReportViewer renders):

* a hidden static Tablix ROW collapses, and the engine moves the items below
  it in the same container up by exactly the row height;
* the reflow never rides a moved item over a still-visible sibling (a visible
  band pins everything below it at its declared gap);
* an ancestor Rectangle's DECLARED height never shrinks — a 12.31in record
  frame rect inside a 10.70in row spilled a blank sheet per record until the
  frame rect was pulled back inside the row.

Guarded here:

* conditional band rects of an OVERFLOWING record are rewritten as one-row
  nested Tablixes ("Band_*") whose static TablixMember carries the <Hidden>
  (the rect itself loses it — the member is what collapses);
* the record row is sized to the DECLARED section body height, not to the
  design-time stack;
* every rect containing a collapsed band is pulled inside the clamped row;
* the rewrite is GATED: a record whose stack exceeds the declared body even
  with every conditional band collapsed keeps the grow/flow behavior (an
  honest oversize record spreads, it is not silently clamped).

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

VAR_A = "Electronic delivery banner"
VAR_B = "Postal routing banner"
BODY = "This determination applies to the account shown"

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


def _letter_xml(body_frame_h=12.0, body_bottom_y=11.5):
    """A per-record letter whose record frame stack EXCEEDS the declared
    section body (9in) but whose two conditional bands (2.0in + 1.2in,
    different trigger columns so no static overlay proof exists) can give
    the overflow back when they collapse.

    ``body_frame_h``/``body_bottom_y`` grow the unconditional tail to build
    the control fixture the gate must refuse."""
    triggers = (_TRIGGER.format(name="f_var_a_ft", upper="F_VAR_A_FT",
                                cond=":Doc_Type = 'EMAIL'")
                + _TRIGGER.format(name="f_var_b_ft", upper="F_VAR_B_FT",
                                  cond=":Other_Cd = 'ROUTE'"))
    return (f"""<?xml version="1.0" encoding="UTF-8"?>
<report name="NOTICE_BAND_STACK" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q_MAIN">
      <select canParse="no"><![CDATA[SELECT ACCT_NO, DOC_TYPE, OTHER_CD FROM ACCOUNTS]]></select>
      <group name="G_MAIN">
        <dataItem name="ACCT_NO" datatype="vchar2" columnOrder="1" defaultLabel="Account"/>
        <dataItem name="DOC_TYPE" datatype="vchar2" columnOrder="2" defaultLabel="Doc Type"/>
        <dataItem name="OTHER_CD" datatype="vchar2" columnOrder="3" defaultLabel="Other"/>
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
        <geometryInfo x="0.00000" y="0.00000" width="7.50000" height="{body_frame_h:.5f}"/>
        <generalLayout verticalElasticity="variable"/>
        <frame name="M_VAR_A">
          <geometryInfo x="0.00000" y="0.30000" width="7.50000" height="2.00000"/>
          <advancedLayout formatTrigger="f_var_a_ft"/>
          <text name="B_VAR_A">
            <geometryInfo x="0.20000" y="0.40000" width="6.00000" height="0.22000"/>
            <textSegment><font face="Arial" size="10"/>
            <string><![CDATA[{VAR_A}]]></string></textSegment></text>
        </frame>
        <frame name="M_VAR_B">
          <geometryInfo x="0.00000" y="2.50000" width="7.50000" height="1.20000"/>
          <advancedLayout formatTrigger="f_var_b_ft"/>
          <text name="B_VAR_B">
            <geometryInfo x="0.20000" y="2.60000" width="6.00000" height="0.22000"/>
            <textSegment><font face="Arial" size="10"/>
            <string><![CDATA[{VAR_B}]]></string></textSegment></text>
        </frame>
        <frame name="M_BODY">
          <geometryInfo x="0.00000" y="3.90000" width="7.50000" height="{body_bottom_y - 3.9:.5f}"/>
          <text name="B_PARA_ONE">
            <geometryInfo x="0.20000" y="4.00000" width="6.00000" height="0.40000"/>
            <textSegment><font face="Arial" size="10"/>
            <string><![CDATA[{BODY} above and remains payable on the stated schedule.]]></string></textSegment></text>
          <field name="F_ACCT_NO" source="ACCT_NO">
            <font face="Arial" size="10"/>
            <geometryInfo x="0.20000" y="{body_bottom_y - 0.4:.5f}" width="3.00000" height="0.20000"/></field>
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


def _record_parts(rdl):
    """(record TablixRow height, record cell rect, [Band_* tablixes])."""
    root = ET.fromstring(rdl)
    for tab in root.iter(f"{NS}Tablix"):
        if tab.get("Name") != "Tablix_Record":
            continue
        row = next(iter(tab.iter(f"{NS}TablixRow")))
        row_h = _inches(row.findtext(f"{NS}Height"))
        cell = next(iter(tab.iter(f"{NS}CellContents")))
        bands = [t for t in cell.iter(f"{NS}Tablix")
                 if (t.get("Name") or "").startswith("Band_")]
        return row_h, cell, bands
    raise AssertionError("expected a per-record Tablix_Record")


def test_overflowing_record_bands_become_collapsible_rows():
    """The 12in design stack on a 9in declared body: bands are rewritten as
    hidden-collapsible rows and the row is sized to the DECLARED body."""
    rdl = convert(_letter_xml())["rdl_xml"]
    row_h, cell, bands = _record_parts(rdl)
    assert len(bands) == 2, (
        f"both conditional bands must be rewritten, got {len(bands)}")
    for band in bands:
        member = band.find(f"{NS}TablixRowHierarchy/{NS}TablixMembers/"
                           f"{NS}TablixMember")
        hid = member.findtext(f"{NS}Visibility/{NS}Hidden") if member is not None else None
        assert hid and hid.strip().startswith("="), (
            "the band's format trigger must ride the STATIC row member — "
            "that is the construct the engine collapses")
        for rect in band.iter(f"{NS}Rectangle"):
            assert rect.find(f"{NS}Visibility") is None, (
                "the band rect must NOT keep its own Hidden: a hidden "
                "rectangle reserves its box (engine-measured), the hidden "
                "row is what collapses")
    assert row_h <= 9.0 + 0.01, (
        f"record row is {row_h}in — a record whose declared 9in body fits "
        "the paper must be sized to the body, not the design stack")

    # Every rect holding a collapsed band sits inside the clamped row: a
    # container's declared height never shrinks at render (engine-measured),
    # so a taller ancestor would spill a blank sheet per record.
    def _walk(el):
        for ch in el:
            tag = ch.tag.split("}")[-1]
            if tag == "Rectangle":
                holds_band = any((t.get("Name") or "").startswith("Band_")
                                 for t in ch.iter(f"{NS}Tablix"))
                if holds_band:
                    bot = (_inches(ch.findtext(f"{NS}Top"))
                           + _inches(ch.findtext(f"{NS}Height")))
                    assert bot <= row_h + 0.01, (
                        f"band-holding rect bottoms at {bot}in, past the "
                        f"{row_h}in row — its declared box would re-spill "
                        "the record")
            _walk(ch)
    _walk(cell)


def test_record_that_cannot_fit_even_collapsed_keeps_grow_flow():
    """Gate: bands reclaim 3.2in, but this stack is 14.5in on a 9in body —
    collapse cannot bring it inside, so the record keeps today's grow/flow
    (honest oversize) and nothing is rewritten."""
    rdl = convert(_letter_xml(body_frame_h=14.5, body_bottom_y=14.3))["rdl_xml"]
    row_h, _cell, bands = _record_parts(rdl)
    assert not bands, "gate must refuse a record collapse cannot fit"
    assert row_h > 9.5, (
        f"row is {row_h}in — an oversize record must keep its extent, not "
        "be clamped to a body it cannot fit")
