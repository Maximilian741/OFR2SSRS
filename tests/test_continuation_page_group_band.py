"""CONTINUATION PAGES REPEAT THE DECLARED GROUP BAND -- AND ONLY THAT.

Oracle scopes a page repeat PER OBJECT (``<advancedLayout
printObjectOnPage="allPage">``); SSRS repeats a WHOLE static tablix member.
Fusing a break report's two header bands -- the group band (break caption,
its status pair, the full-width rule) and the column-header strip -- into one
static member forces both to share a single answer, and the emitter answered
"repeat" for both.

TRUTH MEASUREMENT (a 70-page Oracle-rendered break report, engine PDF read
directly):

  * the band's full-width rule is declared at y=0.00684 with
    printObjectOnPage="allPage" and prints on 70 of 70 pages, at
    x=0.5067in w=7.4861in -- identical geometry on every one;
  * the group caption (declared "allPage") reprints at the top of every
    continuation page, with the declared "allButFirstPage" continuation
    marker beside it;
  * the column strip's frame declares NO print scope, and its underlines
    appear on 63 pages only -- always where a group starts, never at a
    continuation top.

Two more numbers measured on the same report, both synthesized slack the
declaration does not contain:

  * a group caption declared x=0.00000 prints at 0.500in (the sheet's left
    margin); ours emitted Left=0.02in and printed +0.020in right of truth;
  * the declared gap between the group-subtotal rule (y=1.19861) and the
    report-total rule (y=1.89819) is 0.69958in; ours printed 0.94in --
    0.16in of invented footer-band slack plus a flat 0.15in cushion above
    the report-end block where the declaration leaves 0.066in.

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


def _num(el, tag, default=None):
    c = el.find(_q(tag))
    if c is None or not (c.text or "").strip():
        return default
    try:
        return float(c.text.replace("in", "").strip())
    except ValueError:
        return default


# ---------------------------------------------------------------------------
# THE DECLARATION -- a two-band break report, numbers chosen so every
# assertion below is exact arithmetic on the declared geometry.
# ---------------------------------------------------------------------------
_BAND_Y = 0.05          # group caption / status pair
_RULE_Y = 0.00684       # the band's full-width rule ("allPage")
_CAP_Y = 0.30           # column-strip captions (no declared print scope)
_DET_Y = 0.56           # detail row
_SUBRULE_Y = 1.25       # group-subtotal rule
_FOOT_BOT = 1.83        # declared bottom of the group-footer band
_TOTRULE_Y = 1.95       # report-total rule
_DECL_RULE_GAP = _TOTRULE_Y - _SUBRULE_Y        # 0.700in, declared


def _break_report(col_hdr_scope: str = "") -> bytes:
    """A grouped break report. ``col_hdr_scope`` is the print scope the
    column-header frame declares (empty = Oracle's default)."""
    scope = (f'<advancedLayout printObjectOnPage="{col_hdr_scope}"'
             ' basePrintingOn="enclosingObject"/>') if col_hdr_scope else ""
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<report name="BREAKRPT" DTDVersion="9.0.2.0.10">
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
          <geometryInfo x="0.00000" y="{_BAND_Y:.5f}" width="3.00000"
           height="0.19000"/>
          <advancedLayout printObjectOnPage="allPage"
           basePrintingOn="enclosingObject"/>
        </field>
        <text name="B_STATE"><textSettings spacing="single"/>
          <geometryInfo x="6.00000" y="{_BAND_Y:.5f}" width="0.50000"
           height="0.19000"/>
          <advancedLayout printObjectOnPage="allPage"
           basePrintingOn="enclosingObject"/>
          <textSegment><font face="Arial" size="10"/>
          <string><![CDATA[Stage:]]></string></textSegment></text>
        <line name="B_BAND_RULE" arrow="none">
          <geometryInfo x="0.00684" y="{_RULE_Y:.5f}" width="7.48621"
           height="0.00000"/>
          <advancedLayout printObjectOnPage="allPage"
           basePrintingOn="enclosingObject"/>
          <visualSettings lineWidth="1" linePattern="solid"/>
          <points><point x="0.00684" y="{_RULE_Y:.5f}"/>
                  <point x="7.49304" y="{_RULE_Y:.5f}"/></points>
        </line>
        <frame name="M_COLHDR">
          <geometryInfo x="0.60000" y="{_CAP_Y:.5f}" width="6.90000"
           height="0.19000"/>
          {scope}
          <text name="B_C1"><textSettings spacing="single"/>
            <geometryInfo x="0.65000" y="{_CAP_Y:.5f}" width="0.50000"
             height="0.19000"/>
            <textSegment><font face="Arial" size="10" underline="yes"/>
            <string><![CDATA[Alpha]]></string></textSegment></text>
          <text name="B_C2"><textSettings spacing="single"/>
            <geometryInfo x="1.30000" y="{_CAP_Y:.5f}" width="1.40000"
             height="0.19000"/>
            <textSegment><font face="Arial" size="10" underline="yes"/>
            <string><![CDATA[Beta]]></string></textSegment></text>
          <text name="B_C3"><textSettings spacing="single"/>
            <geometryInfo x="2.80000" y="{_CAP_Y:.5f}" width="0.80000"
             height="0.19000"/>
            <textSegment><font face="Arial" size="10" underline="yes"/>
            <string><![CDATA[Gamma]]></string></textSegment></text>
        </frame>
        <repeatingFrame name="R_DET" source="G_DET" printDirection="down">
          <geometryInfo x="0.65000" y="{_DET_Y:.5f}" width="6.85000"
           height="0.19000"/>
          <field name="F_D1" source="D_ONE" alignment="start">
            <font face="Arial" size="10"/>
            <geometryInfo x="0.65000" y="{_DET_Y:.5f}" width="0.50000"
             height="0.19000"/></field>
          <field name="F_D2" source="D_TWO" alignment="start">
            <font face="Arial" size="10"/>
            <geometryInfo x="1.30000" y="{_DET_Y:.5f}" width="1.40000"
             height="0.19000"/></field>
          <field name="F_D3" source="D_THREE" alignment="end">
            <font face="Arial" size="10"/>
            <geometryInfo x="2.80000" y="{_DET_Y:.5f}" width="0.80000"
             height="0.19000"/></field>
        </repeatingFrame>
        <frame name="M_FOOT">
          <geometryInfo x="0.00000" y="1.20000" width="7.50000"
           height="0.63000"/>
          <line name="B_SUB_RULE" arrow="none">
            <geometryInfo x="6.60000" y="{_SUBRULE_Y:.5f}" width="0.90000"
             height="0.00000"/>
            <visualSettings linePattern="solid"/>
            <points><point x="6.60000" y="{_SUBRULE_Y:.5f}"/>
                    <point x="7.50000" y="{_SUBRULE_Y:.5f}"/></points>
          </line>
          <field name="F_SUB_TOT" source="CS_TOT" alignment="end">
            <font face="Arial" size="10" bold="yes"/>
            <geometryInfo x="6.60000" y="{_SUBRULE_Y:.5f}" width="0.90000"
             height="0.19000"/></field>
          <field name="F_CLOSE_CAP" source="CAPCOL" alignment="start">
            <font face="Arial" size="10" bold="yes"/>
            <geometryInfo x="0.00000" y="{_FOOT_BOT - 0.19:.5f}"
             width="3.00000" height="0.19000"/></field>
        </frame>
      </repeatingFrame>
      <frame name="M_TOTAL">
        <geometryInfo x="0.00000" y="1.94000" width="7.50000"
         height="0.50000"/>
        <line name="B_TOT_RULE" arrow="none">
          <geometryInfo x="0.00684" y="{_TOTRULE_Y:.5f}" width="7.48621"
           height="0.00000"/>
          <visualSettings lineWidth="1" linePattern="solid"/>
          <points><point x="0.00684" y="{_TOTRULE_Y:.5f}"/>
                  <point x="7.49304" y="{_TOTRULE_Y:.5f}"/></points>
        </line>
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
def plain_rdl():
    rdl = convert(_break_report())["rdl_xml"]
    assert "Tablix_GroupedSubtotal" in rdl, (
        "fixture must route through the grouped-tabular emitter")
    return rdl


# ---------------------------------------------------------------------------
# helpers over the emitted tablix
# ---------------------------------------------------------------------------
def _tablix(rdl: str):
    root = ET.fromstring(rdl.encode("utf-8"))
    return next(t for t in root.iter(_q("Tablix"))
                if (t.get("Name") or "") == "Tablix_GroupedSubtotal"), root


def _rows(tablix):
    body = tablix.find(_q("TablixBody"))
    return list(body.find(_q("TablixRows")).findall(_q("TablixRow")))


def _row_names(row):
    """Every named report item inside one tablix row."""
    return {el.get("Name") for el in row.iter()
            if el.get("Name") and el.tag in (_q("Textbox"), _q("Rectangle"),
                                             _q("Line"))}


def _row_texts(row):
    return {"".join(v.text or "" for v in tb.iter(_q("Value")))
            for tb in row.iter(_q("Textbox"))}


def _static_members(tablix):
    """Every static (non-Group) TablixMember of the row hierarchy, in
    document order, as (element, repeat-flag)."""
    rh = tablix.find(_q("TablixRowHierarchy"))
    out = []
    for m in rh.iter(_q("TablixMember")):
        if m.find(_q("Group")) is not None:
            continue
        out.append((m, (m.findtext(_q("RepeatOnNewPage")) or "")
                    .strip().lower() == "true"))
    return out


# ---------------------------------------------------------------------------
# 1. the band repeats, the strip does not
# ---------------------------------------------------------------------------
def test_group_band_and_column_strip_are_separate_rows(plain_rdl):
    """Fused in one row they can only share one repeat answer."""
    tablix, _ = _tablix(plain_rdl)
    rows = _rows(tablix)
    band_row = next((r for r in rows if "Tb_GH_0" in _row_names(r)), None)
    strip_row = next((r for r in rows if "GTS_ColBand" in _row_names(r)), None)
    assert band_row is not None and strip_row is not None
    assert band_row is not strip_row, (
        "the group band and the column strip must be separate tablix rows")
    assert rows.index(band_row) < rows.index(strip_row)
    # the strip's captions live in the strip row, not the band row
    assert {"Alpha", "Beta", "Gamma"} <= _row_texts(strip_row)
    assert not ({"Alpha", "Beta", "Gamma"} & _row_texts(band_row))
    # ...and the band's declared "allPage" rule rides with the band
    assert any(n.startswith("Rule_") for n in _row_names(band_row)), (
        "the declared allPage band rule belongs to the repeating band row")


def test_band_repeats_on_new_page_and_undeclared_strip_does_not(plain_rdl):
    tablix, _ = _tablix(plain_rdl)
    rows = _rows(tablix)
    band_i = next(i for i, r in enumerate(rows) if "Tb_GH_0" in _row_names(r))
    strip_i = next(i for i, r in enumerate(rows)
                   if "GTS_ColBand" in _row_names(r))
    members = _static_members(tablix)
    # static members appear in the same order as the rows they carry
    assert members[band_i][1] is True, (
        "the declared allPage group band must repeat on continuation pages")
    assert members[strip_i][1] is False, (
        "a column strip that declares no print scope must NOT repeat")


def test_column_strip_repeats_when_the_export_declares_allpage():
    """PROVE THE GATE: the repeat is read off the declaration, not assumed.
    Same layout, one attribute added, and the strip repeats too."""
    rdl = convert(_break_report("allPage"))["rdl_xml"]
    tablix, _ = _tablix(rdl)
    rows = _rows(tablix)
    strip_i = next(i for i, r in enumerate(rows)
                   if "GTS_ColBand" in _row_names(r))
    members = _static_members(tablix)
    assert members[strip_i][1] is True, (
        'printObjectOnPage="allPage" on the column-header frame must repeat '
        "the strip")


def test_header_rows_publish_a_legal_member_shape(plain_rdl):
    """SSRS rejects two statics on the SAME SIDE of a dynamic member that
    disagree about RepeatOnNewPage ("Expected Value: True; Actual Value:
    False" at publish). The strip therefore has to sit one level deeper --
    where Oracle declares it anyway."""
    tablix, _ = _tablix(plain_rdl)
    rh = tablix.find(_q("TablixRowHierarchy"))

    def check(members_el):
        seen, dyn = [], False
        for m in members_el.findall(_q("TablixMember")):
            if m.find(_q("Group")) is not None:
                if not dyn:
                    dyn, seen = True, []
                inner = m.find(_q("TablixMembers"))
                if inner is not None:
                    check(inner)
                continue
            seen.append((m.findtext(_q("RepeatOnNewPage")) or "")
                        .strip().lower() == "true")
            assert len(set(seen)) <= 1, (
                "statics on one side of a dynamic member must agree on "
                "RepeatOnNewPage")
    check(rh.find(_q("TablixMembers")))


# ---------------------------------------------------------------------------
# 2. a declared x=0 is Left=0, not Left=0.02
# ---------------------------------------------------------------------------
def test_declared_x_zero_carries_no_synthesized_inset(plain_rdl):
    root = ET.fromstring(plain_rdl.encode("utf-8"))
    boxes = {tb.get("Name"): tb for tb in root.iter(_q("Textbox"))
             if tb.get("Name")}
    # the group caption AND the footer's closing caption are both declared
    # at x=0.00000
    zeros = [n for n, tb in boxes.items()
             if n.startswith(("Tb_GH_", "Tb_F_"))
             and _num(tb, "Width", 0.0) == pytest.approx(3.0, abs=0.02)]
    assert zeros, "fixture must emit the x=0 captions"
    for n in zeros:
        assert _num(boxes[n], "Left") == pytest.approx(0.0, abs=1e-9), (
            n, "a declared x=0.00000 box carries no inset",
            _num(boxes[n], "Left"))


# ---------------------------------------------------------------------------
# 3. the declared gap between the two rules is the printed gap
# ---------------------------------------------------------------------------
def _rule_tops(rdl: str):
    """(group-subtotal rule, report-total rule) absolute tops in the body."""
    tablix, root = _tablix(rdl)
    rows = _rows(tablix)
    above, sub = 0.0, None
    for row in rows:
        for ln in row.iter(_q("Line")):
            if sub is None and (_num(ln, "Width", 0.0) or 0.0) < 1.0:
                sub = above + (_num(ln, "Top", 0.0) or 0.0)
        above += _num(row, "Height", 0.0) or 0.0
    body = root.find(_q("Body"))
    tot = None
    for ln in body.find(_q("ReportItems")).findall(_q("Line")):
        t = _num(ln, "Top")
        if t is not None and (tot is None or t < tot):
            tot = t
    return sub, tot


def test_declared_gap_between_group_and_report_rules_is_honoured(plain_rdl):
    sub, tot = _rule_tops(plain_rdl)
    assert sub is not None and tot is not None, (sub, tot)
    assert (tot - sub) == pytest.approx(_DECL_RULE_GAP, abs=0.01), (
        tot - sub, _DECL_RULE_GAP)
    # the trap: the synthesized footer slack (+0.34in) and the flat 0.15in
    # cushion together add ~0.24in, which is far outside that tolerance
    assert abs((_DECL_RULE_GAP + 0.24) - _DECL_RULE_GAP) > 0.05


# ---------------------------------------------------------------------------
# 4. a DRAWN rule declares its page-repeat scope like anything else
# ---------------------------------------------------------------------------
def test_drawn_rule_keeps_its_declared_print_scope():
    """The band's full-width rule is a <line>, and the scope that makes it
    reprint on continuation pages is declared ON IT. Drawn graphics used to
    be built without that attribute, so the declaration was invisible to
    every emitter downstream."""
    sys.path.insert(0, str(ROOT / "backend"))
    from converter.parsers.oracle_xml import parse_oracle_xml

    rep = parse_oracle_xml(_break_report())
    scopes = {}

    def walk(g):
        for f in (getattr(g, "fields", None) or []):
            scopes[f.name] = (getattr(f, "print_on_page", "") or "")
        for c in (getattr(g, "children", None) or []):
            walk(c)
    for lg in (rep.layout or []):
        walk(lg)
    assert scopes.get("B_BAND_RULE") == "allPage", (
        "a drawn rule must keep its declared printObjectOnPage",
        scopes.get("B_BAND_RULE"))
    # the control pair: a text/field in the same band, and a rule that
    # declares nothing
    assert scopes.get("B_STATE") == "allPage"
    assert scopes.get("B_SUB_RULE") == ""


def _footer_row(tablix):
    """The group-footer band row, BY IDENTITY (its own rectangle) rather
    than by position -- the hierarchy may close with a zero-height repeat
    anchor row, and 'the last row' would silently measure that instead."""
    return next(r for r in _rows(tablix) if "GTS_Footer" in _row_names(r))


def test_footer_band_is_as_tall_as_its_declaration(plain_rdl):
    """The band's bottom edge is the lowest declared (y + height) of its
    members -- no invented line of slack under the last one."""
    tablix, _ = _tablix(plain_rdl)
    ftr = _footer_row(tablix)
    assert _num(ftr, "Height") == pytest.approx(_FOOT_BOT - _SUBRULE_Y,
                                                abs=0.01), (
        _num(ftr, "Height"), _FOOT_BOT - _SUBRULE_Y)


def test_footer_members_fit_inside_the_declared_band(plain_rdl):
    """...and every member fits inside it. A member sized past the band's
    declared bottom makes the ROW grow at render time, which puts the slack
    straight back between the two rules -- a defect an RDL-only distance
    check cannot see (engine-measured: 0.7392in printed against a declared
    0.69958in while a member overhung the band by 0.03in)."""
    tablix, _ = _tablix(plain_rdl)
    ftr = _footer_row(tablix)
    band_h = _num(ftr, "Height", 0.0)
    boxes = [tb for tb in ftr.iter(_q("Textbox"))
             if (tb.get("Name") or "").startswith("Tb_F_")]
    assert boxes, "fixture must emit footer members"
    for tb in boxes:
        bot = (_num(tb, "Top", 0.0) or 0.0) + (_num(tb, "Height", 0.0) or 0.0)
        assert bot <= band_h + 1e-6, (
            tb.get("Name"), "a footer member may not overhang its declared "
            "band", bot, band_h)
