"""A nested child block repeats once per DECLARED BREAK GROUP, not per row.

A child dataSource declares its ``<group>`` chain outermost-first. When the
nested repeating frame binds to a group that is NOT the finest one, its rows
are finer than the block: Oracle prints ONE block per break of the frame's own
group and stacks the deeper frame's rows inside it.

Rendering the block straight off the correlated ``LookupSet`` row order printed
one block per CHILD ROW instead, repeating every break-level column on each --
an organisation with four affiliation rows printed four identical
organisations (engine-measured on the real report: 8 child rows -> 8 blocks,
the same name at 147.5 / 203.3 / 259.1 / 314.9pt; after the fold, 3 blocks at
147.5 / 214.4 / 270.2pt, one per declared break).

Settled fact: a nested data region's ``<DataSetName>`` is IGNORED by the
engine, so RDL cannot manufacture child rows inside a parent region and a
Subreport would be a second artifact the user must deploy. The fold therefore
lives in the report's own ``<Code>`` block, over the correlated set.

Every positive test has a negative twin proving the fold is driven by the
DECLARATION, never switched on unconditionally.
"""
from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "tools" / "renderlab"))

from converter import convert                                  # noqa: E402
from converter.generators.rdl import _NDBREAK_CODE             # noqa: E402

NS = {"r": "http://schemas.microsoft.com/sqlserver/reporting/"
            "2008/01/reportdefinition"}
LINE_FACTOR = 1.116 / 72.0          # engine-measured single-space advance


def _q(tag):
    return f"{{{NS['r']}}}{tag}"


def _fixture(deeper=True, shared_col=False):
    """Site record -> Org region (child group G_Org) -> Affil list (G_Affil).

    ``deeper=False`` removes the deeper <group> AND its frame: the child rows
    are then exactly the region's own level, so nothing may fold.
    ``shared_col`` also declares the deeper group's column up at G_Org, the
    export shape that would key the break on a per-row column.
    """
    affil_group = ('<group name="G_Affil">'
                   '<dataItem name="AFFIL" datatype="vchar2"/>'
                   '</group>') if deeper else ""
    affil_frame = (
        '<repeatingFrame name="R_Affil" source="G_Affil" '
        'printDirection="down">'
        '<geometryInfo x="0.30" y="1.05" width="0.90" height="0.19"/>'
        '<field name="F_AFFIL" source="AFFIL">'
        '<geometryInfo x="0.30" y="1.05" width="0.85" height="0.19"/></field>'
        '<text name="B_SEP"><geometryInfo x="1.19" y="1.06" width="0.01" '
        'height="0.17"/><textSegment><string><![CDATA[,]]></string>'
        '</textSegment></text>'
        '</repeatingFrame>') if deeper else ""
    shared = ('<dataItem name="AFFIL" datatype="vchar2"/>'
              if shared_col else "")
    return (
        '<?xml version="1.0"?><report name="BG_T" DTDVersion="9.0.2.0.10">'
        '<data>'
        '<dataSource name="Q_Main"><select><![CDATA[select cnty, site_nm, '
        'site_id from s]]></select>'
        '<group name="G_County"><dataItem name="CNTY" datatype="vchar2"/>'
        '</group>'
        '<group name="G_Site"><dataItem name="SITE_NM" datatype="vchar2"/>'
        '<dataItem name="SITE_ID" datatype="number"/>'
        '<dataItem name="SITE_ADDR" datatype="vchar2"/>'
        '<dataItem name="PERMIT" datatype="vchar2"/>'
        '<dataItem name="STATUS" datatype="vchar2"/>'
        '</group></dataSource>'
        '<dataSource name="Q_Org"><select><![CDATA[select site_id, org_nm, '
        'org_addr, affil from o where (:SITE_ID is null or '
        'o.site_id = :SITE_ID)]]></select>'
        '<group name="G_Org">'
        '<dataItem name="SITE_ID" datatype="number"/>'
        '<dataItem name="ORG_NM" datatype="vchar2"/>'
        '<dataItem name="ORG_ADDR" datatype="vchar2"/>'
        f'{shared}'
        '</group>'
        f'{affil_group}'
        '</dataSource>'
        '<link parentGroup="G_Site" childQuery="Q_Org" condition="eq" '
        'sqlClause="where"/>'
        '</data>'
        '<layout><section name="main"><body height="9.6">'
        '<repeatingFrame name="R_County" source="G_County" '
        'printDirection="down">'
        '<geometryInfo x="0" y="0" width="7.5" height="1.7"/>'
        '<field name="F_CNTY" source="CNTY">'
        '<geometryInfo x="0.0" y="0.05" width="2.0" height="0.2"/></field>'
        '<repeatingFrame name="R_Site" source="G_Site" printDirection="down">'
        '<geometryInfo x="0.1" y="0.3" width="7.4" height="1.4"/>'
        '<field name="F_SITE" source="SITE_NM">'
        '<geometryInfo x="0.15" y="0.35" width="3.0" height="0.2"/></field>'
        '<field name="F_ADDR" source="SITE_ADDR">'
        '<geometryInfo x="0.15" y="0.6" width="3.0" height="0.2"/></field>'
        '<field name="F_PERMIT" source="PERMIT">'
        '<geometryInfo x="4.5" y="0.35" width="0.8" height="0.2"/></field>'
        '<field name="F_STATUS" source="STATUS">'
        '<geometryInfo x="5.5" y="0.35" width="1.9" height="0.2"/></field>'
        '<repeatingFrame name="R_Org" source="G_Org" printDirection="down" '
        'vertSpaceBetweenFrames="0.1500">'
        '<geometryInfo x="0.2" y="1.05" width="7.2" height="0.6"/>'
        '<field name="F_ORG" source="ORG_NM">'
        '<geometryInfo x="1.3" y="1.05" width="3.0" height="0.2"/></field>'
        '<field name="F_OADDR" source="ORG_ADDR">'
        '<geometryInfo x="1.3" y="1.3" width="3.0" height="0.35"/></field>'
        f'{affil_frame}'
        '</repeatingFrame></repeatingFrame></repeatingFrame>'
        '</body></section></layout></report>')


def _region_boxes(rdl):
    """{name: (value, left)} for the nested region's own boxes."""
    root = ET.fromstring(rdl)
    rect = next(r for r in root.iter(_q("Rectangle"))
                if r.get("Name") == "ND_Detail")
    out = {}
    for tb in rect.iter(_q("Textbox")):
        val = next((v.text or "" for v in tb.iter(_q("Value"))), "")
        if "Q_Org" not in val:
            continue
        left = float((tb.findtext(_q("Left")) or "0").replace("in", ""))
        out[tb.get("Name")] = (val, left)
    return out


def _fold_args(val):
    """Code.NDBreakBlock's arguments, split at top-level commas."""
    m = re.match(r'=Code\.NDBreakBlock\((.*)\)$', val, re.S)
    assert m, f"expected a break fold, got {val!r}"
    depth, quoted, args, cur = 0, False, [], ""
    for ch in m.group(1):
        if ch == '"':
            quoted = not quoted
        if not quoted:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            if ch == "," and depth == 0:
                args.append(cur.strip())
                cur = ""
                continue
        cur += ch
    args.append(cur.strip())
    assert len(args) == 8, args
    return args


def _at(boxes, left):
    hit = [v for v in boxes.values() if abs(v[1] - left) <= 0.005]
    assert len(hit) == 1, f"one box at x={left}in, got {hit}"
    return hit[0][0]


# --------------------------------------------------------------- structure --
def test_declared_break_group_folds_the_block():
    """The fold fires, keyed on the region's own declared group level."""
    boxes = _region_boxes(convert(_fixture().encode())["rdl_xml"])
    assert len(boxes) == 2, sorted(boxes)
    org = _fold_args(_at(boxes, 1.30))
    affil = _fold_args(_at(boxes, 0.30))
    # SAME break key in every column, or the columns would break apart
    assert org[0] == affil[0], (org[0], affil[0])
    assert "Fields!ORG_NM.Value" in org[0] and "Fields!ORG_ADDR.Value" in org[0]
    assert "Fields!AFFIL.Value" not in org[0], org[0]
    # break-level members go over ONCE per block; per-row members repeat
    assert "Fields!ORG_NM.Value" in org[1] and org[2] == "Nothing"
    assert affil[1] == "Nothing" and "Fields!AFFIL.Value" in affil[2]
    # geometry travels in DECLARED inches: frame body / declared gutter /
    # deeper frame offset / deeper frame pitch, then each column's own line
    assert [float(x) for x in org[3:7]] == [0.60, 0.15, 0.0, 0.19], org
    assert [float(x) for x in affil[3:7]] == [0.60, 0.15, 0.0, 0.19], affil
    for a in (org, affil):
        assert float(a[7]) == pytest.approx(10 * LINE_FACTOR, abs=5e-4)


def test_no_deeper_declared_group_keeps_the_per_row_join():
    """NEGATIVE TWIN: no finer declared group -> the child rows ARE the
    region's own level, so the block must stay the plain per-row join."""
    rdl = convert(_fixture(deeper=False).encode())["rdl_xml"]
    assert "Code.NDBreakBlock" not in rdl, (
        "a region with no finer declared group must not fold")
    boxes = _region_boxes(rdl)
    assert boxes
    for val, _left in boxes.values():
        assert re.match(r'=Join\(LookupSet\(.*, "Q_Org"\), vbCrLf\)$',
                        val, re.S), val


def test_a_column_declared_at_both_levels_is_not_a_break_key():
    """NEGATIVE TWIN: a column the export declares in the deeper group too
    varies per row; keying on it would break on EVERY row and bring the
    per-child-row defect straight back."""
    boxes = _region_boxes(
        convert(_fixture(shared_col=True).encode())["rdl_xml"])
    key = _fold_args(_at(boxes, 1.30))[0]
    assert "Fields!AFFIL.Value" not in key, key
    assert "Fields!ORG_NM.Value" in key, key


def _move(src, frm, to):
    assert frm in src, frm
    return src.replace(frm, to)


def test_a_column_holding_both_levels_keeps_one_grid_line_per_declared_line():
    """A declared x-column carrying BOTH levels merges them in the reducer by
    LINE, so its break-level grid may not drop the wrap line a tall member
    covers -- dropping it shifts every per-row entry up by that many lines."""
    src = _move(_fixture(),
                '<geometryInfo x="0.30" y="1.05" width="0.90" height="0.19"/>',
                '<geometryInfo x="1.30" y="1.30" width="0.90" height="0.19"/>')
    src = _move(src,
                '<geometryInfo x="0.30" y="1.05" width="0.85" height="0.19"/>',
                '<geometryInfo x="1.30" y="1.30" width="0.85" height="0.19"/>')
    boxes = _region_boxes(convert(src.encode())["rdl_xml"])
    assert len(boxes) == 1, sorted(boxes)          # one declared x -> one box
    a = _fold_args(next(iter(boxes.values()))[0])
    assert "Fields!ORG_NM.Value" in a[1] and "Fields!AFFIL.Value" in a[2]
    # ORG_ADDR is declared 0.35in tall = 2 lines; the grid keeps BOTH, so the
    # grid has one entry per declared line of the block (0.60+0.15 = 5).
    assert a[1].count("vbCrLf") + 1 == round((0.60 + 0.15) / (10 * LINE_FACTOR))
    # the per-row entries start at the DEEPER frame's declared offset
    assert float(a[5]) == pytest.approx(0.25, abs=5e-4), a


def test_a_member_declared_past_its_frame_still_prints():
    """A deeper frame whose declared member reaches below its own declared
    box must not silently lose that member -- the per-row grid grows to hold
    it and the pitch handed to the reducer grows with it."""
    src = _move(_fixture(),
                '<field name="F_AFFIL" source="AFFIL">'
                '<geometryInfo x="0.30" y="1.05" width="0.85" height="0.19"/>',
                '<field name="F_AFFIL" source="AFFIL">'
                '<geometryInfo x="0.30" y="1.45" width="0.85" height="0.19"/>')
    a = _fold_args(_at(_region_boxes(convert(src.encode())["rdl_xml"]), 0.30))
    assert "Fields!AFFIL.Value" in a[2], a[2]
    lh = float(a[7])
    # 0.40in below the frame top = 3 lines, so the instance is 4 lines and the
    # pitch handed over is those 4 lines -- not the declared 0.19in stride,
    # which would stack every row on top of the one before it.
    assert a[2].count("vbCrLf") + 1 == 4, a[2]
    assert float(a[6]) == pytest.approx(4 * lh, abs=5e-4), a


def test_the_reducer_is_declared_in_the_report_code():
    """=Code.X the report never declares is #Error at run time."""
    root = ET.fromstring(convert(_fixture().encode())["rdl_xml"])
    code = root.find(_q("Code"))
    assert code is not None and "Function NDBreakBlock" in (code.text or "")


def test_no_fold_leaves_the_code_block_empty():
    """NEGATIVE TWIN: the reducer is emitted only when something calls it."""
    root = ET.fromstring(convert(_fixture(deeper=False).encode())["rdl_xml"])
    code = root.find(_q("Code"))
    assert "NDBreakBlock" not in ((code.text or "") if code is not None else "")


# --------------------------------------------------------------- behaviour --
_ORGS = [("A ORG", ["W", "X", "Y", "Z"]), ("B ORG", ["W", "X"]),
         ("C ORG", ["W", "X"])]


def _sim(once_tmpl):
    """A simulated multi-row child set: 3 break groups, 4/2/2 rows."""
    keys, once, each = [], [], []
    for nm, affs in _ORGS:
        for a in affs:
            keys.append(nm)
            once.append(once_tmpl.replace("@", nm))
            each.append(a + ",")
    return keys, once, each


def _run(calls):
    from vb_invoke import invoke
    res = invoke(_NDBREAK_CODE, calls)
    if not res.get("available"):
        pytest.skip(f"VB compiler unavailable: {res.get('reason')}")
    assert res.get("compiled"), res.get("errors")
    for r in res["results"]:
        assert r["ok"], r.get("error")
    return [r["value"] for r in res["results"]]


def test_the_reducer_prints_one_block_per_declared_break_group():
    """Simulated multi-row child set through the REAL compiled reducer."""
    args = _fold_args(_at(_region_boxes(
        convert(_fixture().encode())["rdl_xml"]), 1.30))
    body, gap, base, inner, lh = (float(x) for x in args[3:8])
    keys, once, each = _sim("@\r\n\r\nADDR OF @\r\n")
    org_txt, affil_txt = _run([
        ("NDBreakBlock", [keys, once, None, body, gap, base, inner, lh]),
        ("NDBreakBlock", [keys, None, each, body, gap, base, inner, lh]),
    ])
    org_lines = org_txt.split("\r\n")
    affil_lines = affil_txt.split("\r\n")
    # a break-level value prints ONCE per declared break group -- 3, not 8
    for nm, _affs in _ORGS:
        assert org_lines.count(nm) == 1, (nm, org_lines)
        assert org_lines.count(f"ADDR OF {nm}") == 1, (nm, org_lines)
    # ... while EVERY child row still prints, in dataset order, no loss
    assert [x for x in affil_lines if x] == [
        f"{a}," for _nm, affs in _ORGS for a in affs]
    # blocks are the declared height: max(frame body, rows * deeper pitch)
    # plus the declared gutter, counted in the column's own line
    want, at = [], 0
    for nm, affs in _ORGS:
        n = round((max(body, base + len(affs) * inner) + gap) / lh)
        assert org_lines[at] == nm, (nm, at, org_lines)
        assert affil_lines[at:at + len(affs)] == [f"{a}," for a in affs]
        want.append(n)
        at += n
    assert at == len(org_lines) == len(affil_lines), (want, org_lines)
    # the block GREW for the group with more rows than the frame reserves
    assert want[0] > want[1], want


def test_the_reducer_keeps_a_single_group_at_the_declared_pitch():
    """NEGATIVE TWIN: one break group with one row must not grow -- it is
    exactly the declared block, so nothing about the fold adds slack."""
    args = _fold_args(_at(_region_boxes(
        convert(_fixture().encode())["rdl_xml"]), 1.30))
    body, gap, base, inner, lh = (float(x) for x in args[3:8])
    txt, = _run([("NDBreakBlock",
                  [["K"], ["ONLY\r\n\r\nADDR\r\n"], None,
                   body, gap, base, inner, lh])])
    assert len(txt.split("\r\n")) == round((body + gap) / lh)
    assert txt.split("\r\n")[0] == "ONLY"


def test_the_reducer_survives_an_empty_correlated_set():
    """A parent with NO child rows prints nothing -- never an exception."""
    args = _fold_args(_at(_region_boxes(
        convert(_fixture().encode())["rdl_xml"]), 1.30))
    body, gap, base, inner, lh = (float(x) for x in args[3:8])
    empty, nothing = _run([
        ("NDBreakBlock", [[], None, None, body, gap, base, inner, lh]),
        ("NDBreakBlock", [None, None, None, body, gap, base, inner, lh]),
    ])
    assert empty == "" and nothing == ""
