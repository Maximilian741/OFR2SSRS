"""A nested repeating region prints ONE INSTANCE PER CHILD ROW -- as a BLOCK.

Oracle declares the child frame's instances stacked at its own pitch (frame
height + vertSpaceBetweenFrames). RDL cannot manufacture rows inside a parent
scope -- a nested data region's <DataSetName> is IGNORED by the engine, which
keeps the containing scope (measured: a child-column reference inside such a
region fails publishing with "Report item expressions can only refer to fields
within the current dataset scope") -- so the instances arrive as the LINES of
the correlated LookupSet join.

Joining every member with a bare vbCrLf advanced each member ONE line per
child row. For a region whose members sit on two or more declared y-bands that
destroyed the block: the second instance of the upper band printed level with
the first instance of the band below it, and (because a Rectangle REFLOWS, a
growing box pushing everything under it down) the lower band's whole stack was
pushed below the upper band's -- engine-measured at 138pt for a declared
17.7pt offset. These tests pin the replacement model:

  * one box per DECLARED x-column, all anchored at the region's declared top;
  * declared band offsets survive as LINE positions inside that box;
  * each column advances the DECLARED pitch, measured on ITS OWN font (the
    engine ignores <LineHeight>, so a 9pt column and a 10pt column need
    different line counts to cover the same inches);
  * a SINGLE-band region is untouched -- one box per member, plain separator.
"""
import re
import xml.etree.ElementTree as ET

import pytest

from converter import convert

NS = {"r": "http://schemas.microsoft.com/sqlserver/reporting/"
            "2008/01/reportdefinition"}
# engine-measured single-spaced advance (10pt Arial -> 11.16pt/line)
LINE_FACTOR = 1.116 / 72.0


def _q(tag):
    return f"{{{NS['r']}}}{tag}"


def _fixture(members, region_h="0.60", gutter=""):
    """A Site record with a child Org region whose members are given as
    ``[(name, source, x, y, w, h, size), ...]`` -- declared geometry only."""
    body = "".join(
        f'<field name="{n}" source="{s}">'
        f'<font face="Arial" size="{sz}"/>'
        f'<geometryInfo x="{x}" y="{y}" width="{w}" height="{h}"/></field>'
        for (n, s, x, y, w, h, sz) in members)
    return (
        '<?xml version="1.0"?><report name="BR_T" DTDVersion="9.0.2.0.10">'
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
        'org_addr, org_mail from o where (:SITE_ID is null or '
        'o.site_id = :SITE_ID)]]></select>'
        '<group name="G_Org">'
        '<dataItem name="SITE_ID" datatype="number"/>'
        '<dataItem name="ORG_NM" datatype="vchar2"/>'
        '<dataItem name="ORG_ADDR" datatype="vchar2"/>'
        '<dataItem name="ORG_MAIL" datatype="vchar2"/>'
        '</group></dataSource>'
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
        f'<repeatingFrame name="R_Org" source="G_Org" printDirection="down"'
        f'{gutter}>'
        f'<geometryInfo x="0.2" y="1.05" width="7.2" height="{region_h}"/>'
        f'{body}'
        '</repeatingFrame></repeatingFrame></repeatingFrame>'
        '</body></section></layout></report>')


def _region_boxes(rdl):
    """{textbox name: (value, top, left, width)} for the nested region."""
    root = ET.fromstring(rdl)
    rect = next(r for r in root.iter(_q("Rectangle"))
                if r.get("Name") == "ND_Detail")
    out = {}
    for tb in rect.iter(_q("Textbox")):
        val = next((v.text or "" for v in tb.iter(_q("Value"))), "")
        if '"Q_Org"' not in val:
            continue

        def _n(tag, _tb=tb):
            return float((_tb.findtext(_q(tag)) or "0").replace("in", ""))
        out[tb.get("Name")] = (val, _n("Top"), _n("Left"), _n("Width"))
    return out


def _row_expr(val):
    m = re.match(r'=Join\(LookupSet\(.*?, .*?, (.*), "Q_Org"\), (.*)\)$', val)
    assert m, val
    return m.group(1), m.group(2)


def test_two_band_region_becomes_one_block_box_per_declared_column():
    """The defect shape: a name band and an address band 0.25in below it."""
    rdl = convert(_fixture([
        ("F_ORG", "ORG_NM", "1.30", "1.05", "3.0", "0.20", "10"),
        ("F_ADDR", "ORG_ADDR", "1.30", "1.30", "3.0", "0.35", "10"),
        ("F_MAIL", "ORG_MAIL", "4.90", "1.05", "2.5", "0.20", "10"),
    ]).encode())["rdl_xml"]
    boxes = _region_boxes(rdl)
    assert len(boxes) == 2, (
        f"two declared x-columns -> two boxes, got {sorted(boxes)}")
    tops = {round(t, 3) for _v, t, _l, _w in boxes.values()}
    assert tops == {0.70}, (
        f"every column anchors at the region's declared top, got {tops}")
    lefts = sorted(round(x, 2) for _v, _t, x, _w in boxes.values())
    assert lefts == [1.30, 4.90]
    left_col = next(v for v, _t, x, _w in boxes.values() if x == 1.30)
    row, sep = _row_expr(left_col)
    # 0.25in at 11.16pt/line = 2 lines -> exactly ONE blank line between them
    assert re.fullmatch(
        r'Fields!ORG_NM\.Value & vbCrLf & "" & vbCrLf & Fields!ORG_ADDR\.Value',
        row), row
    assert sep == "vbCrLf"


def test_each_column_advances_the_declared_pitch_on_its_own_font():
    """<LineHeight> is ignored by the engine, so a smaller-font column
    needs MORE lines than a 10pt one to cover the same declared pitch."""
    rdl = convert(_fixture([
        ("F_ORG", "ORG_NM", "1.30", "1.05", "3.0", "0.20", "10"),
        ("F_ADDR", "ORG_ADDR", "1.30", "1.30", "3.0", "0.35", "10"),
        # 0.15in is ONE 8pt line, so this column's slot count is decided by
        # the font alone -- read at 10pt it would emit one slot fewer.
        ("F_MAIL", "ORG_MAIL", "4.90", "1.05", "2.5", "0.15", "8"),
    ], region_h="0.60", gutter=' vertSpaceBetweenFrames="0.1500"').encode()
    )["rdl_xml"]
    boxes = _region_boxes(rdl)
    pitch = 0.60 + 0.15
    declared_h = {"ORG_NM": 0.20, "ORG_ADDR": 0.35, "ORG_MAIL": 0.15}
    for _n, (val, _t, left, _w) in boxes.items():
        row, sep = _row_expr(val)
        size = 8.0 if round(left, 2) == 4.90 else 10.0
        lh = size * LINE_FACTOR
        want = round(pitch / lh)
        # the row expression's SLOTS: one per line the block does not already
        # get from a declared box that spans more than one line
        spans = sum(max(1, round(h / lh)) - 1
                    for col, h in declared_h.items()
                    if f"Fields!{col}.Value" in row)
        slots = row.count("vbCrLf") + 1
        assert slots == want - spans, (
            f"column at {left}in must advance {want} lines of {size}pt to "
            f"cover the declared {pitch}in pitch, got {slots + spans}: {row}")
        assert sep == "vbCrLf"
    # the small-font column really does need MORE lines than the 10pt one
    assert {len(_row_expr(v[0])[0].split("vbCrLf")) for v in boxes.values()} \
        == {4, 6}, {n: _row_expr(v[0])[0] for n, v in boxes.items()}
    # and the two columns land within HALF A LINE of each other per instance
    adv = []
    for _n, (val, _t, left, _w) in boxes.items():
        size = 8.0 if round(left, 2) == 4.90 else 10.0
        adv.append(round(pitch / (size * LINE_FACTOR)) * size * LINE_FACTOR)
    assert abs(adv[0] - adv[1]) <= 0.5 * 10.0 * LINE_FACTOR, adv


def test_single_band_region_keeps_one_box_per_member():
    """PROVE THE PASS IS SCOPED: a region whose members share one declared
    band cannot interleave, so it must come out exactly as before -- one box
    per member at its own declared x, joined by a bare vbCrLf."""
    rdl = convert(_fixture([
        ("F_ORG", "ORG_NM", "1.30", "1.05", "3.0", "0.20", "10"),
        ("F_MAIL", "ORG_MAIL", "4.90", "1.05", "2.5", "0.20", "10"),
    ]).encode())["rdl_xml"]
    boxes = _region_boxes(rdl)
    assert len(boxes) == 2
    for val, top, _l, _w in boxes.values():
        row, sep = _row_expr(val)
        assert "vbCrLf" not in row, (
            f"a single-band region prints ONE line per child row: {row}")
        assert sep == "vbCrLf"
        assert top == pytest.approx(0.70, abs=0.005)


def test_block_box_is_tall_enough_for_one_declared_instance():
    """The box must reserve a whole declared instance; anything shorter
    clipped the block's lower bands before CanGrow could take over."""
    rdl = convert(_fixture([
        ("F_ORG", "ORG_NM", "1.30", "1.05", "3.0", "0.20", "10"),
        ("F_ADDR", "ORG_ADDR", "1.30", "1.30", "3.0", "0.35", "10"),
    ], region_h="0.60").encode())["rdl_xml"]
    root = ET.fromstring(rdl)
    rect = next(r for r in root.iter(_q("Rectangle"))
                if r.get("Name") == "ND_Detail")
    for tb in rect.iter(_q("Textbox")):
        val = next((v.text or "" for v in tb.iter(_q("Value"))), "")
        if '"Q_Org"' not in val:
            continue
        h = float((tb.findtext(_q("Height")) or "0").replace("in", ""))
        assert h >= round(0.60 / (10 * LINE_FACTOR)) * 10 * LINE_FACTOR - 0.005
        assert (tb.findtext(_q("CanGrow")) or "").lower() == "true"
