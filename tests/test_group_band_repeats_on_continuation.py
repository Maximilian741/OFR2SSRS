"""A DECLARED page-repeating band REACHES the engine, and nothing else does.

Oracle scopes a page repeat per object (``<advancedLayout
printObjectOnPage="allPage">``); SSRS repeats a whole static tablix member.
Emitting ``RepeatOnNewPage`` is necessary but NOT sufficient -- the engine
resolves the flag against the member's siblings, and a run that disagrees is
silently dropped rather than rejected. Two truth-paired reports measured it:

TRUTH A -- a 37-page landscape summary. Its caption frame declares
``printObjectOnPage="allPage"``; the engine PDF prints that column band at
y=35.0pt on pages 1, 2, 3 ... 37, identical every time. Ours emitted no
``RepeatOnNewPage`` at all and printed it on page 1 only. This member is the
tablix's TOP-LEVEL static, and there the engine honours the flag on its own
(re-measured with and without a trailing grand-total static: band on 4/4
rendered pages either way).

TRUTH B -- a 70-page break report. Its group band (break caption, the status
pair beside it, the full-width rule at x=0.5067in w=7.4861in) prints on 70 of
70 pages; the column-header strip, whose frame declares NO print scope, prints
only where a group starts (pages 1 and 4 carry it, 2, 3, 69, 70 do not); the
group-subtotal block prints once, where the group closes. Ours emitted
``RepeatOnNewPage`` on the band member and the engine IGNORED it. Rendering
the emitted RDL through ReportViewer at 90 rows, one mutation at a time:

    [band(repeat), banddyn, footer(no repeat)]  band on 1/3 pages  <- shipped
    [band(repeat), banddyn]                     band on 1/3 pages
    [band(no repeat), banddyn, footer(repeat)]  band on 1/3 pages
    [band(repeat), banddyn, footer(repeat)]     band on 3/3 pages, but the
                                                footer printed TWICE
    [band(repeat), banddyn, anchor(repeat)]     band on 3/3 pages, strip on
      with the footer moved INSIDE banddyn      1/3, footer once  <- truth

So: a static NESTED inside a dynamic group keeps RepeatOnNewPage only when
every static beside it agrees AND the run is closed by a trailing
KeepWithGroup="Before" member. Anything that must not repeat has to sit one
level deeper, and a zero-height anchor closes the run instead.

Everything below is synthetic and structural: no report, column or label from
any real source appears in this file.
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert                       # noqa: E402

NS = ("{http://schemas.microsoft.com/sqlserver/reporting/"
      "2008/01/reportdefinition}")

_ALLPAGE = ('<advancedLayout printObjectOnPage="allPage"'
            ' basePrintingOn="enclosingObject"/>')


def _q(tag: str) -> str:
    return NS + tag


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
def _break_report(band_scope: str = _ALLPAGE) -> bytes:
    """A grouped break report: group band over a column strip over detail
    rows over a group-subtotal band. ``band_scope`` is what the band's own
    members declare about which pages they print on."""
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<report name="CONTBRK" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q_MAIN">
      <select><![CDATA[SELECT BKEY, BCAP, C_ONE, C_TWO, C_THREE
       FROM T_SRC]]></select>
      <group name="G_BREAK">
        <dataItem name="BKEY" datatype="vchar2" width="20">
          <dataDescriptor expression="BKEY" order="1" width="20"/>
        </dataItem>
        <dataItem name="BCAP" datatype="vchar2" width="40">
          <dataDescriptor expression="BCAP" order="2" width="40"/>
        </dataItem>
      </group>
      <group name="G_ROWS">
        <dataItem name="C_ONE" datatype="vchar2" width="10">
          <dataDescriptor expression="C_ONE" order="3" width="10"/>
        </dataItem>
        <dataItem name="C_TWO" datatype="vchar2" width="30">
          <dataDescriptor expression="C_TWO" order="4" width="30"/>
        </dataItem>
        <dataItem name="C_THREE" oracleDatatype="number" width="10">
          <dataDescriptor expression="C_THREE" order="5" width="10"/>
        </dataItem>
      </group>
    </dataSource>
    <summary name="CS_BRK" source="C_THREE" function="sum" width="12"
     reset="G_BREAK" compute="G_BREAK" columnFlags="8"/>
  </data>
  <layout>
  <section name="main">
    <body height="9.60">
      <frame name="M_ALL">
        <geometryInfo x="0.00000" y="0.00000" width="7.50000"
         height="2.40000"/>
        <generalLayout verticalElasticity="variable"/>
      <repeatingFrame name="R_BREAK" source="G_BREAK" printDirection="down">
        <geometryInfo x="0.00000" y="0.00000" width="7.50000"
         height="1.80000"/>
        <generalLayout verticalElasticity="variable"/>
        <field name="F_BCAP" source="BCAP" alignment="start">
          <font face="Arial" size="10" bold="yes"/>
          <geometryInfo x="0.00000" y="0.05000" width="3.00000"
           height="0.19000"/>
          {band_scope}
        </field>
        <text name="B_MARK"><textSettings spacing="single"/>
          <geometryInfo x="6.00000" y="0.05000" width="0.50000"
           height="0.19000"/>
          {band_scope}
          <textSegment><font face="Arial" size="10"/>
          <string><![CDATA[Phase:]]></string></textSegment></text>
        <line name="B_BAND_RULE" arrow="none">
          <geometryInfo x="0.00684" y="0.00684" width="7.48621"
           height="0.00000"/>
          {band_scope}
          <visualSettings lineWidth="1" linePattern="solid"/>
          <points><point x="0.00684" y="0.00684"/>
                  <point x="7.49304" y="0.00684"/></points>
        </line>
        <frame name="M_STRIP">
          <geometryInfo x="0.60000" y="0.30000" width="6.90000"
           height="0.19000"/>
          <text name="B_S1"><textSettings spacing="single"/>
            <geometryInfo x="0.65000" y="0.30000" width="0.50000"
             height="0.19000"/>
            <textSegment><font face="Arial" size="10" underline="yes"/>
            <string><![CDATA[Alpha]]></string></textSegment></text>
          <text name="B_S2"><textSettings spacing="single"/>
            <geometryInfo x="1.30000" y="0.30000" width="1.40000"
             height="0.19000"/>
            <textSegment><font face="Arial" size="10" underline="yes"/>
            <string><![CDATA[Beta]]></string></textSegment></text>
          <text name="B_S3"><textSettings spacing="single"/>
            <geometryInfo x="2.80000" y="0.30000" width="0.80000"
             height="0.19000"/>
            <textSegment><font face="Arial" size="10" underline="yes"/>
            <string><![CDATA[Gamma]]></string></textSegment></text>
        </frame>
        <repeatingFrame name="R_ROWS" source="G_ROWS" printDirection="down">
          <geometryInfo x="0.65000" y="0.56000" width="6.85000"
           height="0.19000"/>
          <field name="F_C1" source="C_ONE" alignment="start">
            <font face="Arial" size="10"/>
            <geometryInfo x="0.65000" y="0.56000" width="0.50000"
             height="0.19000"/></field>
          <field name="F_C2" source="C_TWO" alignment="start">
            <font face="Arial" size="10"/>
            <geometryInfo x="1.30000" y="0.56000" width="1.40000"
             height="0.19000"/></field>
          <field name="F_C3" source="C_THREE" alignment="end">
            <font face="Arial" size="10"/>
            <geometryInfo x="2.80000" y="0.56000" width="0.80000"
             height="0.19000"/></field>
        </repeatingFrame>
        <frame name="M_CLOSE">
          <geometryInfo x="0.00000" y="1.20000" width="7.50000"
           height="0.60000"/>
          <field name="F_BRK_TOT" source="CS_BRK" alignment="end">
            <font face="Arial" size="10" bold="yes"/>
            <geometryInfo x="6.60000" y="1.25000" width="0.90000"
             height="0.19000"/></field>
          <field name="F_CLOSE_CAP" source="BCAP" alignment="start">
            <font face="Arial" size="10" bold="yes"/>
            <geometryInfo x="0.00000" y="1.61000" width="3.00000"
             height="0.19000"/></field>
        </frame>
      </repeatingFrame>
      </frame>
    </body>
  </section>
  </layout>
</report>'''.encode()


_SUMMARY_COLS = (("S_ONE", 0.00000, 2.00000), ("S_TWO", 2.50000, 2.00000),
                 ("S_THREE", 5.00000, 1.50000), ("S_FOUR", 7.00000, 1.50000))


def _summary_report(hdr_scope: str = _ALLPAGE) -> bytes:
    """A flat columnar summary whose caption frame sits above the record
    band -- the shape whose header member is the tablix's TOP-LEVEL static."""
    def _field(c, x, w):
        return (f'<field name="F_{c}" source="{c}" alignment="start">'
                f'<font face="Arial" size="8"/>'
                f'<geometryInfo x="{x:.5f}" y="0.30000" width="{w:.5f}"'
                f' height="0.18000"/></field>')

    def _cap(i, x, w):
        return (f'<text name="B_CAP{i}"><textSettings spacing="0"/>'
                f'<geometryInfo x="{x:.5f}" y="0.02000" width="{w:.5f}"'
                f' height="0.19000"/><textSegment>'
                f'<font face="Arial" size="9"/>'
                f'<string><![CDATA[Head{i}]]></string>'
                f'</textSegment></text>')

    fields = "".join(_field(c, x, w) for c, x, w in _SUMMARY_COLS)
    caps = "".join(_cap(i, x, w)
                   for i, (_c, x, w) in enumerate(_SUMMARY_COLS))
    items = "".join(f'<dataItem name="{c}" datatype="vchar2"'
                    f' columnOrder="{i + 1}"/>'
                    for i, (c, _x, _w) in enumerate(_SUMMARY_COLS))
    cols = ", ".join(c for c, _x, _w in _SUMMARY_COLS)
    return (
        '<?xml version="1.0"?>'
        '<report name="CONTSUM" DTDVersion="9.0.2.0.10"><data>'
        '<dataSource name="Q_1">'
        f'<select><![CDATA[SELECT {cols} FROM T_SUM]]></select>'
        f'<group name="G_ROW">{items}</group>'
        '</dataSource></data><layout>'
        '<section name="main" width="14.00000" height="8.50000"'
        ' orientation="landscape">'
        '<body width="12.50000" height="7.00000">'
        '<location x="0.50000" y="0.50000"/>'
        '<frame name="M_GRP">'
        '<geometryInfo x="0.00000" y="0.02000" width="9.00000"'
        ' height="0.55000"/>'
        '<repeatingFrame name="R_ROW" source="G_ROW" printDirection="down">'
        '<geometryInfo x="0.00000" y="0.30000" width="9.00000"'
        ' height="0.18000"/>'
        f'{fields}'
        '</repeatingFrame>'
        '<frame name="M_HDR">'
        '<geometryInfo x="0.00000" y="0.02000" width="9.00000"'
        ' height="0.19000"/>'
        f'{hdr_scope}{caps}</frame></frame></body>'
        '</section></layout></report>'
    ).encode()


# ---------------------------------------------------------------------------
# helpers over the emitted tablix
# ---------------------------------------------------------------------------
def _tablix(rdl: str, name: str):
    root = ET.fromstring(rdl.encode("utf-8"))
    return next(t for t in root.iter(_q("Tablix"))
                if (t.get("Name") or "") == name)


def _repeats(member) -> bool:
    return (member.findtext(_q("RepeatOnNewPage")) or "").strip().lower() \
        == "true"


def _kids(members_el):
    return members_el.findall(_q("TablixMember"))


def _is_static(m) -> bool:
    return m.find(_q("Group")) is None


def _leaf_count(members_el) -> int:
    n = 0
    for m in _kids(members_el):
        inner = m.find(_q("TablixMembers"))
        n += _leaf_count(inner) if inner is not None else 1
    return n


def _rows(tablix):
    return list(tablix.find(_q("TablixBody")).find(_q("TablixRows"))
                .findall(_q("TablixRow")))


def _row_names(row):
    return {el.get("Name") for el in row.iter() if el.get("Name")}


def _num(el, tag, default=None):
    c = el.find(_q(tag))
    if c is None or not (c.text or "").strip():
        return default
    try:
        return float(c.text.replace("in", "").strip())
    except ValueError:
        return default


def _every_members_collection(tablix):
    """Every TablixMembers collection of the ROW hierarchy, outermost first."""
    out = []

    def walk(ms):
        out.append(ms)
        for m in _kids(ms):
            inner = m.find(_q("TablixMembers"))
            if inner is not None:
                walk(inner)
    walk(tablix.find(_q("TablixRowHierarchy")).find(_q("TablixMembers")))
    return out


# ---------------------------------------------------------------------------
# THE ENGINE RULE -- an emitted repeat must be one the engine can honour
# ---------------------------------------------------------------------------
def _assert_repeat_runs_are_consistent(tablix):
    """Measured rule: a static member's RepeatOnNewPage survives only when
    every static SIBLING agrees with it. A mixed run is not rejected at
    publish -- it is silently ignored, which is why an RDL-only check that
    merely finds the attribute proves nothing."""
    for ms in _every_members_collection(tablix):
        flags = {_repeats(m) for m in _kids(ms) if _is_static(m)}
        assert len(flags) <= 1, (
            tablix.get("Name"),
            "statics sharing a TablixMembers collection must agree on "
            "RepeatOnNewPage or the engine drops the repeat", flags)


def _assert_rows_match_members(tablix):
    """Rows and leaf members move in lockstep -- an extra member without its
    row (or the reverse) shifts every row's content one member along."""
    hier = tablix.find(_q("TablixRowHierarchy")).find(_q("TablixMembers"))
    assert _leaf_count(hier) == len(_rows(tablix)), (
        tablix.get("Name"), _leaf_count(hier), len(_rows(tablix)))


# ---------------------------------------------------------------------------
# 1. the break report -- the band reaches the engine, nothing else does
# ---------------------------------------------------------------------------
def test_declared_band_repeats_and_its_run_is_consistent():
    tablix = _tablix(convert(_break_report())["rdl_xml"],
                     "Tablix_GroupedSubtotal")
    _assert_repeat_runs_are_consistent(tablix)
    _assert_rows_match_members(tablix)

    ginner = _kids(_tablix(convert(_break_report())["rdl_xml"],
                           "Tablix_GroupedSubtotal")
                   .find(_q("TablixRowHierarchy"))
                   .find(_q("TablixMembers")))[0].find(_q("TablixMembers"))
    band = _kids(ginner)[0]
    assert _is_static(band) and _repeats(band), (
        "the declared allPage group band must repeat on continuation pages")
    assert (band.findtext(_q("KeepWithGroup")) or "") == "After"

    # ...and the run it sits in is closed by a trailing repeat member, the
    # shape the engine actually honours (see the module docstring's table).
    tail = _kids(ginner)[-1]
    assert _is_static(tail) and _repeats(tail), (
        "the repeating run must be closed by a trailing static that repeats")
    assert (tail.findtext(_q("KeepWithGroup")) or "") == "Before"


def test_the_run_closer_is_a_zero_height_anchor_carrying_no_ink():
    """The trailing member exists to satisfy the engine, so it must cost the
    page nothing -- if it carried content or height it would print on every
    continuation page, which no declaration asks for."""
    tablix = _tablix(convert(_break_report())["rdl_xml"],
                     "Tablix_GroupedSubtotal")
    rows = _rows(tablix)
    anchor = rows[-1]
    assert _num(anchor, "Height") == 0.0, _num(anchor, "Height")
    assert not list(anchor.iter(_q("Textbox"))), (
        "the anchor row may not carry text")
    assert not list(anchor.iter(_q("Line"))), (
        "the anchor row may not carry a rule")
    assert not list(anchor.iter(_q("Image"))), (
        "the anchor row may not carry an image")
    # ...and the anchor is the ONLY thing that got added: the band, strip,
    # detail and footer rows are all still there, in declaration order.
    order = [next((n for n in ("GTS_Hdr", "GTS_ColHdr", "GTS_Detail",
                               "GTS_Footer", "GTS_RepeatAnchor")
                   if n in _row_names(r)), None) for r in rows]
    assert order == ["GTS_Hdr", "GTS_ColHdr", "GTS_Detail", "GTS_Footer",
                     "GTS_RepeatAnchor"], order


def test_group_footer_does_not_ride_the_band_repeat():
    """Truth prints the group-subtotal block once, where the group closes.
    Making IT repeat is the other way to satisfy the engine, and it printed
    the block twice in a 3-page render -- so the footer sits one level
    deeper instead, under the band's own dynamic member."""
    tablix = _tablix(convert(_break_report())["rdl_xml"],
                     "Tablix_GroupedSubtotal")
    ftr_row = next(r for r in _rows(tablix) if "GTS_Footer" in _row_names(r))
    ftr_i = _rows(tablix).index(ftr_row)

    # locate the member that owns that row by walking leaves in order
    leaves = []

    def walk(ms):
        for m in _kids(ms):
            inner = m.find(_q("TablixMembers"))
            if inner is not None:
                walk(inner)
            else:
                leaves.append(m)
    walk(tablix.find(_q("TablixRowHierarchy")).find(_q("TablixMembers")))
    fmem = leaves[ftr_i]
    assert _is_static(fmem) and not _repeats(fmem), (
        "the group footer must not repeat on continuation pages")

    ginner = _kids(tablix.find(_q("TablixRowHierarchy"))
                   .find(_q("TablixMembers")))[0].find(_q("TablixMembers"))
    assert fmem not in _kids(ginner), (
        "a non-repeating footer beside the repeating band makes the engine "
        "drop the band's repeat -- it has to sit one level deeper")


def test_column_strip_still_does_not_repeat():
    """The strip declares no print scope, and truth prints it only where a
    group starts. The band's new machinery must not drag it along."""
    tablix = _tablix(convert(_break_report())["rdl_xml"],
                     "Tablix_GroupedSubtotal")
    rows = _rows(tablix)
    strip_i = next(i for i, r in enumerate(rows)
                   if "GTS_ColHdr" in _row_names(r))
    leaves = []

    def walk(ms):
        for m in _kids(ms):
            inner = m.find(_q("TablixMembers"))
            if inner is not None:
                walk(inner)
            else:
                leaves.append(m)
    walk(tablix.find(_q("TablixRowHierarchy")).find(_q("TablixMembers")))
    assert not _repeats(leaves[strip_i])


# ---------------------------------------------------------------------------
# 2. PROVE THE GATE -- the repeat is read off the declaration
# ---------------------------------------------------------------------------
def test_undeclared_band_neither_repeats_nor_grows_an_anchor():
    rdl = convert(_break_report(band_scope=""))["rdl_xml"]
    tablix = _tablix(rdl, "Tablix_GroupedSubtotal")
    _assert_repeat_runs_are_consistent(tablix)
    _assert_rows_match_members(tablix)
    assert not any(_repeats(m)
                   for ms in _every_members_collection(tablix)
                   for m in _kids(ms)), (
        "no declared page scope -> nothing repeats")
    assert not any("GTS_RepeatAnchor" in _row_names(r)
                   for r in _rows(tablix)), (
        "the anchor exists only to carry a DECLARED repeat")


# ---------------------------------------------------------------------------
# 3. the top-level static -- honoured on its own, still declaration-gated
# ---------------------------------------------------------------------------
def test_declared_caption_frame_repeats_at_the_top_level():
    tablix = _tablix(convert(_summary_report())["rdl_xml"], "Tablix_Main")
    top = _kids(tablix.find(_q("TablixRowHierarchy"))
                .find(_q("TablixMembers")))
    assert _is_static(top[0]) and _repeats(top[0]), (
        'a caption frame declaring printObjectOnPage="allPage" must reprint '
        "at the top of every page")
    assert top[1].find(_q("Group")) is not None, (
        "the repeating header must precede the dynamic detail member")
    _assert_repeat_runs_are_consistent(tablix)
    _assert_rows_match_members(tablix)


def test_undeclared_caption_frame_does_not_repeat():
    tablix = _tablix(convert(_summary_report(hdr_scope=""))["rdl_xml"],
                     "Tablix_Main")
    top = _kids(tablix.find(_q("TablixRowHierarchy"))
                .find(_q("TablixMembers")))
    assert _is_static(top[0]) and not _repeats(top[0]), (
        "an undeclared caption frame prints where the flow puts it, once")
