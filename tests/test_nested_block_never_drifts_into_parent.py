"""A nested block must never advance on a pitch of its own INSIDE its parent.

Two engine facts drive this file, both probe-measured on this ReportViewer
build (tools/renderlab, signed-DLL path):

  * A data region's REFLOW BOX is the SUM OF ITS ROW HEIGHTS, not its
    declared ``<Height>``. A tablix at Top 0.50in, ``<Height>0.50in``, rows
    0.30in + 0.25in never pushed a peer anchored at 1.00in or 1.02in (the
    peer stayed put while the detail rows grew straight through it) and
    pushed a peer anchored at 1.05in (= Top + row sum) by exactly the
    growth. So an appended block parked between the declared bottom and the
    true one is never reflowed: the parent's rows advance on the parent's
    declared pitch, the block's rows on its own, and the two DRIFT through
    each other -- measured on a nested-breakdown report at parent pitch
    0.271in vs block pitch 0.211in, the block's third row landing in the
    parent's second name cell (graze 8.2pt).

  * A stacked record's lines print at their OWN declared x. The column
    BUCKET is 0.6in wide -- it exists so a column's right edge can be found,
    not to place anything. Emitting the bucket x stamped a caption and the
    value declared 0.44in to its right onto one identical box (both
    Left 0.0625in Width 0.85in), so the value printed invisibly under the
    caption box's own opaque fill.

Every geometry below is DECLARED by the fixture; nothing is name-matched.
"""
import xml.etree.ElementTree as ET

import pytest

from converter import convert
from converter.generators.rdl import _region_reflow_height_in

NS = {"r": "http://schemas.microsoft.com/sqlserver/reporting/"
            "2008/01/reportdefinition"}


def _q(tag):
    return f"{{{NS['r']}}}{tag}"


def _in(el, tag, default=0.0):
    txt = el.findtext(_q(tag)) if el is not None else None
    try:
        return float((txt or "").replace("in", "").strip())
    except (AttributeError, ValueError):
        return default


def _row_sum(tablix):
    body = tablix.find(_q("TablixBody"))
    rows = body.find(_q("TablixRows")) if body is not None else None
    if rows is None:
        return 0.0
    return sum(_in(r, "Height") for r in rows.findall(_q("TablixRow")))


def _fld(n, s, x, y, w, h):
    return (f'<field name="{n}" source="{s}"><geometryInfo x="{x}" y="{y}" '
            f'width="{w}" height="{h}"/></field>')


def _txt(n, t, x, y, w, h):
    return (f'<text name="{n}"><geometryInfo x="{x}" y="{y}" width="{w}" '
            f'height="{h}"/><textSegment><string><![CDATA[{t}]]></string>'
            f'</textSegment></text>')


# --------------------------------------------------------------------------
# FIXTURE A -- a flat table plus a nested repeating frame on a SECOND dataset
# with no resolvable correlation, i.e. the report-end breakdown fallback.
# The declared caption band (0.30in) and record band (0.44in) sum to 0.74in
# of static tablix extent under the builder's 0.50in declared <Height>.
# --------------------------------------------------------------------------
BREAKDOWN_SRC = (
    '<?xml version="1.0"?><report name="BD_T" DTDVersion="9.0.2.0.10"><data>'
    '<dataSource name="Q_Main"><select><![CDATA[select code, nm, amt from t]]>'
    '</select><group name="G_A"><dataItem name="CODE" datatype="number"/>'
    '<dataItem name="NM" datatype="vchar2"/>'
    '<dataItem name="AMT" datatype="number"/></group></dataSource>'
    '<dataSource name="Q_Side"><select><![CDATA[select amt1 from u]]></select>'
    '<group name="G_B"><dataItem name="AMT1" datatype="number"/></group>'
    '</dataSource></data>'
    '<layout><section name="main"><body height="4.0">'
    '<frame name="M_A"><geometryInfo x="0.05" y="0.5" width="7.4" '
    'height="1.3"/>'
    '<repeatingFrame name="R_A" source="G_A" printDirection="down">'
    '<geometryInfo x="0.05" y="1.0" width="7.4" height="0.44"/>'
    + _fld("F_CODE", "CODE", 0.06, 1.02, 0.7, 0.40)
    + _fld("F_NM", "NM", 0.9, 1.02, 2.0, 0.40)
    + _fld("F_AMT", "AMT", 5.9, 1.02, 1.4, 0.40)
    + '<frame name="M_B"><geometryInfo x="3.1" y="1.02" width="1.2" '
      'height="0.21"/>'
      '<repeatingFrame name="R_B" source="G_B" printDirection="down">'
      '<geometryInfo x="3.1" y="1.02" width="1.2" height="0.21"/>'
    + _fld("F_AMT1", "AMT1", 3.1, 1.02, 1.15, 0.21)
    + '</repeatingFrame></frame></repeatingFrame></frame>'
    '<frame name="M_HDR"><geometryInfo x="0.05" y="0.5" width="7.4" '
    'height="0.5"/>'
    + _txt("B_CODE", "Code", 0.06, 0.55, 0.7, 0.4)
    + _txt("B_NM", "Name", 0.9, 0.55, 2.0, 0.4)
    + _txt("B_AMT", "Amount", 5.9, 0.55, 1.4, 0.4)
    + '</frame></body></section></layout></report>')


# --------------------------------------------------------------------------
# FIXTURE B -- a multi-line stacked record whose third declared line carries
# a caption at x 0.0625 and its value at x 0.5: 0.44in apart, and inside ONE
# 0.6in column bucket.
# --------------------------------------------------------------------------
_SL_BODY = (
    _txt("B_REF", "Ref No:", 0.0625, 0, 0.9375, 0.375)
    + _fld("F_REF", "REF", 0.99988, 0, 1.06238, 0.1875)
    + _txt("B_OPEN", "Opened:", 2.24976, 0, 0.68774, 0.375)
    + _fld("F_OPEN", "OPENED", 2.99951, 0, 0.8125, 0.1875)
    + _txt("B_YR", "Year:", 4.0, 0, 0.37439, 0.1875)
    + _fld("F_YR", "YR", 4.49939, 0, 0.5625, 0.1875)
    + _txt("B_KIND", "Kind:", 5.125, 0, 0.99927, 0.1875)
    + _fld("F_KIND", "KIND", 6.18677, 0, 0.87488, 0.1875)
    + _txt("B_TYPE", "Type:", 7.24915, 0, 1.00085, 0.4375)
    + _fld("F_TYPE", "TYP", 8.1864, 0, 0.87488, 0.1875)
    + _txt("B_NOTE", "Note:", 0.0625, 0.5, 1.0, 0.1875)
    + _fld("F_NOTE", "NOTE", 1.12488, 0.5, 3.75012, 0.375)
    + _txt("B_CLOSED", "Closed:", 5.12463, 0.5, 0.68787, 0.375)
    + _fld("F_CLOSED", "CLOSED", 6.18701, 0.5, 0.81238, 0.1875)
    + _txt("B_STATE", "State:", 7.24939, 0.5, 0.81311, 0.25)
    + _fld("F_STATE", "STATE", 8.18677, 0.5, 0.87488, 0.1875)
    + '<frame name="M_P"><geometryInfo x="0.0625" y="0.9375" width="7.0" '
      'height="0.8125"/>'
      '<repeatingFrame name="R_P" source="G_P" printDirection="down">'
      '<geometryInfo x="0.0625" y="0.9375" width="7.0" height="0.8125"/>'
    + _txt("B_PER", "Period", 0.0625, 1.0, 0.3125, 0.1875)
    + _fld("F_PER", "PER", 0.5, 1.0, 0.37488, 0.1875)
    + '<frame name="M_D"><geometryInfo x="0.125" y="1.25" width="6.9375" '
      'height="0.5"/>'
      '<repeatingFrame name="R_D" source="G_D" printDirection="down">'
      '<geometryInfo x="0.125" y="1.5625" width="6.9375" height="0.1875"/>'
    + _fld("F_DAY", "DAY", 0.1875, 1.5625, 0.625, 0.1875)
    + _fld("F_CODE", "CODE", 1.24988, 1.5625, 1.31238, 0.1875)
    + _fld("F_DESCR", "DESCR", 3.0625, 1.5625, 2.25, 0.1875)
    + _fld("F_AMT", "AMT", 5.58984, 1.5625, 1.47266, 0.1875)
    + '</repeatingFrame></frame>'
      '<frame name="M_D_HDR"><geometryInfo x="0.125" y="1.3125" '
      'width="6.875" height="0.1875"/>'
    + _txt("B_DAY", "Day", 0.3125, 1.3125, 0.4375, 0.1875)
    + _txt("B_CODE", "Code", 1.31238, 1.3125, 1.31238, 0.1875)
    + _txt("B_DESCR", "Detail", 3.5, 1.3125, 1.125, 0.1875)
    + _txt("B_AMT", "Amount", 5.93726, 1.3125, 0.81238, 0.1875)
    + '</frame></repeatingFrame></frame>')

STACKED_SRC = (
    '<?xml version="1.0"?><report name="SL_T" DTDVersion="9.0.2.0.10"><data>'
    '<dataSource name="Q_Main"><select><![CDATA[select ref, opened, yr, kind, '
    'typ, note, closed, state, per, day, code, descr, amt from t]]></select>'
    '<group name="G_R"><dataItem name="REF" datatype="number"/>'
    '<dataItem name="OPENED" datatype="date"/>'
    '<dataItem name="YR" datatype="number"/>'
    '<dataItem name="KIND" datatype="vchar2"/>'
    '<dataItem name="TYP" datatype="vchar2"/>'
    '<dataItem name="NOTE" datatype="vchar2"/>'
    '<dataItem name="CLOSED" datatype="date"/>'
    '<dataItem name="STATE" datatype="vchar2"/></group>'
    '<group name="G_P"><dataItem name="PER" datatype="vchar2"/></group>'
    '<group name="G_D"><dataItem name="DAY" datatype="number"/>'
    '<dataItem name="CODE" datatype="vchar2"/>'
    '<dataItem name="DESCR" datatype="vchar2"/>'
    '<dataItem name="AMT" datatype="number"/></group></dataSource></data>'
    '<layout><section name="main"><body height="6.0">'
    '<frame name="M_R"><geometryInfo x="0.0625" y="0" width="9.125" '
    'height="1.9375"/>'
    '<repeatingFrame name="R_R" source="G_R" printDirection="down">'
    '<geometryInfo x="0.0625" y="0" width="9.125" height="1.9375"/>'
    + _SL_BODY +
    '</repeatingFrame></frame></body></section></layout></report>')


@pytest.fixture(scope="module")
def breakdown_root():
    return ET.fromstring(convert(BREAKDOWN_SRC.encode(), "bd.xml")["rdl_xml"])


@pytest.fixture(scope="module")
def stacked_root():
    return ET.fromstring(convert(STACKED_SRC.encode(), "sl.xml")["rdl_xml"])


# ---------------------------------------------------------------- fixture A

def test_breakdown_fixture_really_declares_a_short_tablix_height(
        breakdown_root):
    """Non-vacuity: the guard below only bites while the main tablix's rows
    sum PAST its declared <Height>. If a builder change ever makes the two
    agree, this fails and the guard must be re-aimed, not deleted."""
    main = next(t for t in breakdown_root.iter(_q("Tablix"))
                if t.get("Name") == "Tablix_Main")
    assert _row_sum(main) > _in(main, "Height") + 0.15, (
        "fixture no longer exercises the declared-vs-true height gap: "
        f"rows={_row_sum(main)} declared={_in(main, 'Height')}")


def test_appended_breakdown_clears_the_parent_regions_reflow_box(
        breakdown_root):
    """The report-end breakdown may not start inside a data region's row-sum
    box -- inside it the engine never reflows it and the two regions advance
    on their own separate declared pitches."""
    body = breakdown_root.find(_q("Body"))
    items = body.find(_q("ReportItems"))
    regions = [t for t in items if t.tag == _q("Tablix")
               and not (t.get("Name") or "").startswith("Tablix_Breakdown")]
    blocks = [t for t in items if t.tag == _q("Tablix")
              and (t.get("Name") or "").startswith("Tablix_Breakdown")]
    assert regions and blocks, "fixture must emit a main table AND a breakdown"
    for blk in blocks:
        for reg in regions:
            assert _in(blk, "Top") >= _in(reg, "Top") + _row_sum(reg), (
                f"{blk.get('Name')} Top={_in(blk, 'Top')} starts inside "
                f"{reg.get('Name')} reflow box "
                f"{_in(reg, 'Top')}..{_in(reg, 'Top') + _row_sum(reg)}")


def test_breakdown_rows_still_carry_their_own_declared_pitch(breakdown_root):
    """Clearing the parent box must not restyle the block: its row height is
    still the child frame's own declared height + gutter (0.21in)."""
    blk = next(t for t in breakdown_root.iter(_q("Tablix"))
               if (t.get("Name") or "").startswith("Tablix_Breakdown"))
    assert _row_sum(blk) == pytest.approx(0.21, abs=0.005)


def test_region_reflow_height_is_the_row_sum_not_the_declared_height():
    """Unit: the reflow box of a data region is its rows, and a plain item
    keeps its declared height."""
    tablix = ET.fromstring(
        '<Tablix xmlns="{0}"><TablixBody><TablixRows>'
        '<TablixRow><Height>0.30in</Height></TablixRow>'
        '<TablixRow><Height>0.25in</Height></TablixRow>'
        '</TablixRows></TablixBody><Height>0.50in</Height></Tablix>'
        .format(NS["r"]))
    assert _region_reflow_height_in(tablix) == pytest.approx(0.55)
    box = ET.fromstring(
        '<Textbox xmlns="{0}"><Height>0.50in</Height></Textbox>'
        .format(NS["r"]))
    assert _region_reflow_height_in(box) == pytest.approx(0.50)


# ---------------------------------------------------------------- fixture B

def _sl_detail_boxes(root):
    """{name: (top, left, width)} of the stacked record's own line boxes."""
    rect = next(r for r in root.iter(_q("Rectangle"))
                if r.get("Name") == "SL_Detail")
    out = {}
    for tb in rect.find(_q("ReportItems")):
        out[tb.get("Name")] = (_in(tb, "Top"), _in(tb, "Left"),
                               _in(tb, "Width"))
    return out


def test_stacked_line_prints_at_its_own_declared_x(stacked_root):
    """The caption declared at 0.0625 and the value declared at 0.5 share a
    line and a column bucket; each must land on its OWN declared x."""
    boxes = _sl_detail_boxes(stacked_root)
    cap = next(v for k, v in boxes.items() if k.endswith("_Period"))
    val = next(v for k, v in boxes.items() if k.endswith("_PER"))
    assert cap[0] == val[0], "the two are declared on one line"
    assert cap[1] == pytest.approx(0.0625, abs=0.0005)
    assert val[1] == pytest.approx(0.5, abs=0.0005)


def test_stacked_lines_sharing_a_declared_line_never_overlap(stacked_root):
    """No two boxes on one line of the record may share horizontal space --
    the later one's opaque fill buries the earlier one's text."""
    boxes = _sl_detail_boxes(stacked_root)
    by_top = {}
    for name, (top, left, width) in boxes.items():
        by_top.setdefault(round(top, 4), []).append((left, left + width, name))
    for top, row in by_top.items():
        row.sort()
        for (l1, r1, n1), (l2, r2, n2) in zip(row, row[1:]):
            assert r1 <= l2 + 1e-9, (
                f"line {top}in: {n1} spans {l1}..{r1} into {n2} at {l2}")


def test_stacked_line_widths_still_reach_their_column_edge(stacked_root):
    """The width rule is unchanged where nothing is declared beside a line:
    a line with no line-mate to its right keeps the column's right edge."""
    boxes = _sl_detail_boxes(stacked_root)
    note = next(v for k, v in boxes.items() if k.endswith("_NOTE"))
    # declared at x 1.12488 on a line whose next declared member is at
    # 5.12463, so the COLUMN edge (not the line-mate) still bounds it
    assert note[1] == pytest.approx(1.12488, abs=0.0005)
    assert note[1] + note[2] < 5.12463
    assert note[2] > 0.4
