"""Hidden breakdown members never paint; a record's own inline labels never
form a phantom caption strip; a two-line caption gets a two-line band.

Three engine-measured wild classes (one dug example each, no customer text):

* BREAKDOWN HIDDEN MEMBERS — Oracle stacks several ``visible="no"``
  computation-only fields on the exact same declared box as the one visible
  member (they feed boilerplate &tokens, they are never drawn). The
  report-end breakdown emitter copied every field member into the row, so
  three renditions painted on top of each other on every breakdown line
  (8 painted-over pairs per report, four wild reports).

* PHANTOM HEADER STRIP — the stacked-list header scan compared the ROUNDED
  primary band key against each label's RAW declared y, so a label declared
  a fraction of a point above the rounded key (i.e. on the record's own
  line) leaked in as a "header band". The strip duplicated the labels the
  detail cell already prints per record, and squeezed a declared two-line
  caption into a one-line band whose bottom rule cut through the second
  line's glyphs (2 rule-through-glyph pairs per report, two wild reports).

* TWO-LINE CAPTION BAND — with a genuine above-the-record caption band and
  no declared band height, the synthesized fallback gave every band ONE
  line; a caption authored with an embedded newline needs its line count.

Everything below is structural: synthetic fixtures, every expected number
computed from what the fixture declares. No report, column or label from
any real source appears in this file.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402


def _rdl(xml: str) -> str:
    res = convert(xml.encode("utf-8"), "hiddencase.xml")
    assert not res.get("conversion_error"), res.get("conversion_error")
    return res["rdl_xml"]


# --------------------------------------------------------------------------
# (1) BREAKDOWN — visible="no" members must not reach the row
# --------------------------------------------------------------------------

_BD_BOX = 'x="2.9" y="1.3" width="0.9" height="0.19"'


def _hidden_member_breakdown_xml() -> str:
    """A side dataset's breakdown frame with THREE fields sharing one
    declared box — two computation-only (visible="no"), one visible."""
    return (
        '<?xml version="1.0"?><report name="HIDEBD" DTDVersion="9.0.2.0.10">'
        '<data>'
        '<dataSource name="Q_Main"><select><![CDATA[select main_a, main_b, '
        'main_c, main_d from t_main]]></select>'
        '<group name="G_Main"><dataItem name="MAIN_A" datatype="vchar2"/>'
        '<dataItem name="MAIN_B" datatype="date"/>'
        '<dataItem name="MAIN_C" datatype="vchar2"/>'
        '<dataItem name="MAIN_D" datatype="vchar2"/></group></dataSource>'
        '<dataSource name="Q_Side"><select><![CDATA[select side_key, '
        'side_aux1, side_aux2, side_cnt from t_side]]></select>'
        '<group name="G_Side"><dataItem name="SIDE_KEY" datatype="vchar2"/>'
        '<dataItem name="SIDE_AUX1" datatype="number"/>'
        '<dataItem name="SIDE_AUX2" datatype="vchar2"/>'
        '<dataItem name="SIDE_CNT" datatype="number"/></group></dataSource>'
        '</data>'
        '<layout><section name="main">'
        '<frame name="M_Body"><geometryInfo x="0" y="0" width="8" height="3"/>'
        '<repeatingFrame name="R_Main" source="G_Main" printDirection="down">'
        '<geometryInfo x="0.2" y="0.2" width="7.5" height="0.2"/>'
        '<field name="F_A" source="MAIN_A"><geometryInfo x="0.2" y="0.2" '
        'width="3" height="0.2"/></field>'
        '<field name="F_B" source="MAIN_B"><geometryInfo x="3.4" y="0.2" '
        'width="1.4" height="0.2"/></field>'
        '<field name="F_MC" source="MAIN_C"><geometryInfo x="4.9" y="0.2" '
        'width="1.2" height="0.2"/></field>'
        '<field name="F_MD" source="MAIN_D"><geometryInfo x="6.2" y="0.2" '
        'width="1.2" height="0.2"/></field></repeatingFrame>'
        '<frame name="M_Totals"><geometryInfo x="0.2" y="1.2" width="7.5" '
        'height="0.8"/>'
        '<repeatingFrame name="R_Side" source="G_Side" printDirection="down">'
        '<geometryInfo x="0.3" y="1.3" width="4" height="0.19"/>'
        '<text name="B_K"><geometryInfo x="0.3" y="1.3" width="2.4" '
        'height="0.19"/><textSegment><font face="Arial" size="8"/>'
        '<string><![CDATA[&SIDE_KEY Tally:]]></string>'
        '</textSegment></text>'
        f'<field name="F_AUX1" source="SIDE_AUX1" visible="no">'
        f'<geometryInfo {_BD_BOX}/></field>'
        f'<field name="F_AUX2" source="SIDE_AUX2" visible="no">'
        f'<geometryInfo {_BD_BOX}/></field>'
        f'<field name="F_C" source="SIDE_CNT" alignment="end">'
        f'<geometryInfo {_BD_BOX}/></field>'
        '</repeatingFrame></frame></frame></section></layout></report>'
    )


def _breakdown_field_refs(rdl: str) -> list:
    """Field refs of the breakdown tablix bound to the SIDE dataset."""
    for m in re.finditer(r'<Tablix Name="Tablix_Breakdown_\d+"(.*?)</Tablix>',
                         rdl, re.S):
        if "<DataSetName>Q_Side</DataSetName>" in m.group(1):
            return re.findall(r"<Value>=Fields!(\w+)\.Value</Value>",
                              m.group(1))
    raise AssertionError("side-dataset breakdown table missing")


def test_hidden_breakdown_members_never_paint():
    """Only the VISIBLE field of the shared box reaches the breakdown row;
    the two visible="no" computation-only fields must not paint over it."""
    refs = _breakdown_field_refs(_rdl(_hidden_member_breakdown_xml()))
    assert refs == ["SIDE_CNT"], refs


def test_visible_breakdown_members_all_still_paint():
    """The filter must key on the DECLARED attribute — with every field
    visible the same frame emits every member (the gate can fail red)."""
    xml = _hidden_member_breakdown_xml().replace(' visible="no"', '')
    refs = _breakdown_field_refs(_rdl(xml))
    assert sorted(refs) == ["SIDE_AUX1", "SIDE_AUX2", "SIDE_CNT"], refs


# --------------------------------------------------------------------------
# (2) STACKED LIST — inline labels on the record's own line are NOT headers
# --------------------------------------------------------------------------

_SL_PAIRS = (("ALPHA", 0.00), ("BETA", 2.00), ("GAMMA", 4.00))
_SL_LBL_Y1 = 0.18750     # labels' declared y (rounds to the same 2dp band…)
_SL_FLD_Y1 = 0.19000     # …as the fields' declared y: ONE physical line
_SL_LBL_Y2 = 0.43750
_SL_FLD_Y2 = 0.44000


def _inline_label_stacked_xml() -> str:
    items = "".join(
        f'<dataItem name="{c}" datatype="vchar2" width="30">'
        f'<dataDescriptor expression="{c}" order="{i + 1}" width="30"/>'
        f'</dataItem>'
        f'<dataItem name="{c}2" datatype="vchar2" width="30">'
        f'<dataDescriptor expression="{c}2" order="{i + 4}" width="30"/>'
        f'</dataItem>'
        for i, (c, _x) in enumerate(_SL_PAIRS))
    line1 = "".join(
        f'<text name="B_{c}"><geometryInfo x="{x:.5f}" y="{_SL_LBL_Y1:.5f}"'
        f' width="0.85000" height="0.18750"/><textSegment>'
        f'<font face="Arial" size="9" bold="yes"/>'
        f'<string><![CDATA[Cap {c.capitalize()}:]]></string></textSegment>'
        f'</text>'
        f'<field name="F_{c}" source="{c}"><font face="Arial" size="9"/>'
        f'<geometryInfo x="{x + 0.9:.5f}" y="{_SL_FLD_Y1:.5f}"'
        f' width="1.00000" height="0.18750"/></field>'
        for c, x in _SL_PAIRS)
    line2 = "".join(
        f'<text name="B_{c}2"><geometryInfo x="{x:.5f}" y="{_SL_LBL_Y2:.5f}"'
        f' width="0.85000" height="0.18750"/><textSegment>'
        f'<font face="Arial" size="9" bold="yes"/>'
        f'<string><![CDATA[Sub {c.capitalize()}:]]></string></textSegment>'
        f'</text>'
        f'<field name="F_{c}2" source="{c}2"><font face="Arial" size="9"/>'
        f'<geometryInfo x="{x + 0.9:.5f}" y="{_SL_FLD_Y2:.5f}"'
        f' width="1.00000" height="0.18750"/></field>'
        for c, x in _SL_PAIRS)
    cols_sql = ", ".join(c for c, _x in _SL_PAIRS)
    cols2_sql = ", ".join(f"{c}2" for c, _x in _SL_PAIRS)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<report name="INLINESL" DTDVersion="9.0.2.0.10"><data>'
        '<dataSource name="Q_1"><select><![CDATA['
        f'SELECT {cols_sql}, {cols2_sql} FROM T]]></select>'
        f'<group name="G_ROW">{items}</group>'
        '</dataSource></data><layout>'
        '<section name="main" width="8.50000" height="11.00000">'
        '<body width="7.50000" height="9.50000">'
        '<location x="0.50000" y="0.75000"/>'
        '<frame name="M_GRP">'
        '<geometryInfo x="0.00000" y="0.00000" width="7.40000"'
        ' height="1.00000"/>'
        '<repeatingFrame name="R_ROW" source="G_ROW" printDirection="down"'
        ' minWidowRecords="1" columnMode="no">'
        '<geometryInfo x="0.00000" y="0.18750" width="7.40000"'
        ' height="0.50000"/>'
        f'{line1}{line2}</repeatingFrame>'
        '</frame></body></section></layout></report>'
    )


def test_inline_labels_do_not_form_a_header_strip():
    """Labels declared on the record's OWN line (their raw y a fraction of a
    point off the fields' — same 2dp band) must not be duplicated into a
    synthesized caption strip above the list."""
    rdl = _rdl(_inline_label_stacked_xml())
    assert '<Tablix Name="Tablix_StackedList">' in rdl, "not the stacked path"
    assert 'Tb_SLHdr_' not in rdl, "phantom header strip emitted"
    # ...and the labels still print, once per record, in the detail cell.
    dets = re.findall(r'<Textbox Name="Tb_SLDet_[^"]*">.*?<Value>([^<]*)'
                      r'</Value>', rdl, re.S)
    assert any("Cap Alpha:" in v for v in dets), dets


def test_headerless_stacked_list_has_no_empty_header_row():
    """With no caption strip the header ROW itself must go: an empty
    <ReportItems> is schema-invalid and an empty white band above the first
    record is ink the source never declares. Rows and row-hierarchy members
    must stay 1:1."""
    rdl = _rdl(_inline_label_stacked_xml())
    m = re.search(r'<Tablix Name="Tablix_StackedList">(.*?)\n    </Tablix>',
                  rdl, re.S) or \
        re.search(r'<Tablix Name="Tablix_StackedList">(.*?)</Tablix>',
                  rdl, re.S)
    body = m.group(1)
    assert 'SL_ColHdr' not in body, "empty header rectangle emitted"
    rows = body.count("<TablixRow>")
    members = len(re.findall(r"<TablixMember\b(?!s)", body))
    # one column member + one row member per row
    assert rows == 1, body[:400]
    assert members == rows + 1, (rows, members)


# --------------------------------------------------------------------------
# (3) STACKED LIST — a two-line caption gets a two-line synthesized band
# --------------------------------------------------------------------------

_H_LINE = 0.20   # the emitter's synthesized caption line height


def _twoline_caption_stacked_xml() -> str:
    caps = "".join(
        f'<text name="B_H{i}"><geometryInfo x="{x:.5f}" y="0.00000"'
        f' width="0.95000" height="0.17000"/><textSegment>'
        f'<font face="Arial" size="9" bold="yes"/>'
        f'<string><![CDATA[{t}]]></string></textSegment></text>'
        for i, (t, x) in enumerate(
            (("Head One", 0.00), ("Head\nTwo Lines", 2.00),
             ("Head Three", 4.00))))
    line1 = "".join(
        f'<field name="F_{c}" source="{c}"><font face="Arial" size="9"/>'
        f'<geometryInfo x="{x:.5f}" y="0.30000" width="1.00000"'
        f' height="0.18750"/></field>'
        for c, x in _SL_PAIRS)
    line2 = "".join(
        f'<field name="F_{c}2" source="{c}2"><font face="Arial" size="9"/>'
        f'<geometryInfo x="{x:.5f}" y="0.52000" width="1.00000"'
        f' height="0.18750"/></field>'
        for c, x in _SL_PAIRS)
    items = "".join(
        f'<dataItem name="{c}" datatype="vchar2" width="30">'
        f'<dataDescriptor expression="{c}" order="{i + 1}" width="30"/>'
        f'</dataItem>'
        f'<dataItem name="{c}2" datatype="vchar2" width="30">'
        f'<dataDescriptor expression="{c}2" order="{i + 4}" width="30"/>'
        f'</dataItem>'
        for i, (c, _x) in enumerate(_SL_PAIRS))
    cols_sql = ", ".join(c for c, _x in _SL_PAIRS)
    cols2_sql = ", ".join(f"{c}2" for c, _x in _SL_PAIRS)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<report name="TWOLNSL" DTDVersion="9.0.2.0.10"><data>'
        '<dataSource name="Q_1"><select><![CDATA['
        f'SELECT {cols_sql}, {cols2_sql} FROM T]]></select>'
        f'<group name="G_ROW">{items}</group>'
        '</dataSource></data><layout>'
        '<section name="main" width="8.50000" height="11.00000">'
        '<body width="7.50000" height="9.50000">'
        '<location x="0.50000" y="0.75000"/>'
        '<frame name="M_GRP">'
        '<geometryInfo x="0.00000" y="0.00000" width="7.40000"'
        ' height="1.20000"/>'
        f'{caps}'
        '<repeatingFrame name="R_ROW" source="G_ROW" printDirection="down"'
        ' minWidowRecords="1" columnMode="no">'
        '<geometryInfo x="0.00000" y="0.30000" width="7.40000"'
        ' height="0.42000"/>'
        f'{line1}{line2}</repeatingFrame>'
        '</frame></body></section></layout></report>'
    )


def test_two_line_caption_band_is_two_lines_tall():
    """A caption authored with an embedded newline needs its LINE COUNT of
    synthesized height — one line ran the second caption line through the
    band's bottom rule (engine-measured on two wild stacked lists)."""
    rdl = _rdl(_twoline_caption_stacked_xml())
    assert '<Tablix Name="Tablix_StackedList">' in rdl, "not the stacked path"
    hdr_boxes = re.findall(
        r'<Textbox Name="Tb_SLHdr_[^"]*">.*?<Height>([\d.]+)in</Height>',
        rdl, re.S)
    assert hdr_boxes, "caption strip missing"
    assert any(abs(float(h) - 2 * _H_LINE) < 0.002 for h in hdr_boxes), \
        hdr_boxes
    # the header ROW must hold the two-line band in full
    m = re.search(r"<TablixRow>\s*<Height>([\d.]+)in</Height>\s*"
                  r"<TablixCells>.*?SL_ColHdr", rdl, re.S)
    assert m, "header row missing"
    assert float(m.group(1)) >= 2 * _H_LINE + 0.02, m.group(1)
