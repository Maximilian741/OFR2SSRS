"""Intra-record line pitch and caption-stack pitch follow the DECLARATION.

Oracle states, for every stacked line inside a record and for every caption
line inside a band, that line's own y. It also states the record's row PITCH
as the repeating frame's height plus ``vertSpaceBetweenFrames``. The Oracle
truth PDFs step their ink by exactly those numbers.

Four emitters were instead FLOWING those stacks at synthesized constants, or
rounding/flooring the declaration:

* the stacked-list detail cell stepped its lines at a fixed 0.20in
  (truth-measured 0.22192in -> printed 20% tight);
* the stacked-list caption band split its declared height evenly between the
  caption lines instead of using each line's declared y;
* the grouped-tabular detail row applied a 0.20in FLOOR, rounding a declared
  0.18994in pitch UP by 5.3% -- pagination drift over a long report;
* the report-end breakdown table dropped the declared inter-frame gutter and
  stacked its members at a fixed 0.20in;
* the positional frame emitter enforced a 0.02in minimum on each member's
  Top, which moved ONLY the member declared at its frame's top edge and so
  shortened the FIRST declared gap of a caption stack (measured 15.12pt
  printed against a declared and truth-measured 16.15pt).

Everything below is structural: synthetic fixtures, and every expected number
is computed from what the fixture DECLARES. No report, column or label from
any real source appears in this file.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402

# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _rdl(xml: str) -> str:
    res = convert(xml.encode("utf-8"), "pitchcase.xml")
    assert not res.get("conversion_error"), res.get("conversion_error")
    return res["rdl_xml"]


def _tops(rdl: str, name_prefix: str) -> list:
    """Sorted distinct <Top> values of every Textbox whose Name starts with
    ``name_prefix``."""
    out = set()
    for m in re.finditer(
            r'<Textbox Name="' + name_prefix + r'[^"]*">(.*?)</Textbox>',
            rdl, re.S):
        t = re.search(r"<Top>([\d.]+)in</Top>", m.group(1))
        if t:
            out.add(round(float(t.group(1)), 4))
    return sorted(out)


def _row_heights(rdl: str, rect_name: str) -> list:
    """Heights of every TablixRow whose cell holds ``rect_name``."""
    out = []
    for m in re.finditer(
            r"<TablixRow>\s*<Height>([\d.]+)in</Height>(.*?)</TablixRow>",
            rdl, re.S):
        if f'Rectangle Name="{rect_name}"' in m.group(2):
            out.append(float(m.group(1)))
    return out


# --------------------------------------------------------------------------
# (1) STACKED LIST -- a record spanning two column-aligned physical lines
# --------------------------------------------------------------------------

SL_BAND_Y = 0.44000        # record frame's declared y
SL_LINE1_Y = 0.44500       # declared y of the record's first line
SL_LINE_PITCH = 0.21810    # DECLARED intra-record line pitch
SL_LINE2_Y = SL_LINE1_Y + SL_LINE_PITCH
SL_HDR_H = 0.42000         # declared caption-frame height
SL_CAP1_Y = 0.00500
SL_CAP_PITCH = 0.24560     # DECLARED caption-stack pitch (!= SL_HDR_H / 2)
SL_CAP2_Y = SL_CAP1_Y + SL_CAP_PITCH

_SL_COLS = (("ALPHA", 0.02, 1.60), ("BETA", 1.81, 3.60),
            ("GAMMA", 5.55, 4.00), ("DELTA", 9.79, 0.60))


def _stacked_xml() -> str:
    items = "".join(
        f'<dataItem name="{c}" datatype="vchar2" width="30"'
        f' defaultLabel="{c.capitalize()}">'
        f'<dataDescriptor expression="{c}" order="{i + 1}" width="30"/>'
        f'</dataItem>' + (
            f'<dataItem name="{c}2" datatype="vchar2" width="30"'
            f' defaultLabel="{c.capitalize()} Two">'
            f'<dataDescriptor expression="{c}2" order="{i + 5}" width="30"/>'
            f'</dataItem>')
        for i, (c, _x, _w) in enumerate(_SL_COLS))
    line1 = "".join(
        f'<field name="F_{c}" source="{c}"><font face="Arial" size="10"/>'
        f'<geometryInfo x="{x:.5f}" y="{SL_LINE1_Y:.5f}" width="{w:.5f}"'
        f' height="0.18000"/></field>'
        for c, x, w in _SL_COLS)
    line2 = "".join(
        f'<field name="F_{c}2" source="{c}2"><font face="Arial" size="10"/>'
        f'<geometryInfo x="{x:.5f}" y="{SL_LINE2_Y:.5f}" width="{w:.5f}"'
        f' height="0.18000"/></field>'
        for c, x, w in _SL_COLS)
    caps = "".join(
        f'<text name="B_H{i}"><textSettings spacing="single"/>'
        f'<geometryInfo x="{x:.5f}" y="{SL_CAP1_Y:.5f}" width="{w:.5f}"'
        f' height="0.17000"/><textSegment>'
        f'<font face="Arial" size="10" bold="yes"/>'
        f'<string><![CDATA[Cap {i}]]></string></textSegment></text>'
        f'<text name="B_S{i}"><textSettings spacing="single"/>'
        f'<geometryInfo x="{x:.5f}" y="{SL_CAP2_Y:.5f}" width="{w:.5f}"'
        f' height="0.17000"/><textSegment>'
        f'<font face="Arial" size="10" bold="yes"/>'
        f'<string><![CDATA[Sub {i}]]></string></textSegment></text>'
        for i, (_c, x, w) in enumerate(_SL_COLS))
    cols_sql = ", ".join(c for c, _x, _w in _SL_COLS)
    cols2_sql = ", ".join(f"{c}2" for c, _x, _w in _SL_COLS)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<report name="PITCHSL" DTDVersion="9.0.2.0.10"><data>'
        '<dataSource name="Q_1"><select><![CDATA['
        f'SELECT {cols_sql}, {cols2_sql} FROM T]]></select>'
        f'<group name="G_ROW">{items}</group>'
        '</dataSource></data><layout>'
        '<section name="main" width="11.00000" height="8.50000"'
        ' orientation="landscape">'
        '<body width="10.42627" height="7.14587">'
        '<location x="0.29248" y="0.76038"/>'
        '<frame name="M_GRP">'
        '<geometryInfo x="0.02000" y="0.00000" width="10.37000"'
        ' height="1.00000"/>'
        '<repeatingFrame name="R_ROW" source="G_ROW" printDirection="down"'
        ' minWidowRecords="1" columnMode="no">'
        f'<geometryInfo x="0.02000" y="{SL_BAND_Y:.5f}" width="10.37000"'
        ' height="0.46000"/>'
        f'{line1}{line2}</repeatingFrame>'
        '<frame name="M_HDR">'
        f'<geometryInfo x="0.02000" y="0.00000" width="10.37000"'
        f' height="{SL_HDR_H:.5f}"/>'
        '<advancedLayout printObjectOnPage="allPage"'
        ' basePrintingOn="anchoringObject"/>'
        f'{caps}</frame></frame></body></section></layout></report>'
    )


def test_stacked_record_lines_step_the_declared_pitch():
    rdl = _rdl(_stacked_xml())
    assert '<Tablix Name="Tablix_StackedList">' in rdl, "not the stacked path"
    tops = _tops(rdl, "Tb_SLDet_")
    assert len(tops) == 2, tops
    assert abs((tops[1] - tops[0]) - SL_LINE_PITCH) < 0.002, tops


def test_stacked_caption_bands_step_the_declared_pitch():
    rdl = _rdl(_stacked_xml())
    tops = _tops(rdl, "Tb_SLHdr_")
    assert len(tops) == 2, tops
    assert abs((tops[1] - tops[0]) - SL_CAP_PITCH) < 0.002, tops
    # ...which is NOT the even split of the declared band the emitter used
    # to fall back on -- the assertion above must be able to tell them apart.
    assert abs(SL_CAP_PITCH - SL_HDR_H / 2) > 0.02


# --------------------------------------------------------------------------
# (2) GROUPED TABULAR BREAK -- detail row pitch + group-band caption stack
# --------------------------------------------------------------------------

GT_DET_H = 0.18994         # declared detail-frame height (BELOW the old floor)
GT_BAND1_Y = 0.40000
GT_BAND_PITCH = 0.24560    # DECLARED group-band caption pitch (!= 0.22)
GT_BAND2_Y = GT_BAND1_Y + GT_BAND_PITCH


def _grouped_xml(gutter: float = 0.0) -> str:
    _gap = f' vertSpaceBetweenFrames="{gutter:.5f}"' if gutter else ""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<report name="PITCHGT" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q_OUTER">
      <select><![CDATA[SELECT BREAK_KEY, G_CODE FROM T_BREAK]]></select>
      <group name="G_OUTER">
        <dataItem name="BREAK_KEY" datatype="vchar2" width="30"
         defaultLabel="Break Key">
          <dataDescriptor expression="BREAK_KEY" order="1" width="30"/>
        </dataItem>
        <dataItem name="G_CODE" datatype="vchar2" width="20"
         defaultLabel="G Code">
          <dataDescriptor expression="G_CODE" order="2" width="20"/>
        </dataItem>
      </group>
    </dataSource>
    <dataSource name="Q_DETAIL">
      <select><![CDATA[SELECT D_ONE, D_TWO, D_THREE, D_FOUR, CS_TALLY
       FROM T_DETAIL]]></select>
      <group name="G_DETAIL">
        <dataItem name="D_ONE" datatype="vchar2" width="30"
         defaultLabel="D One">
          <dataDescriptor expression="D_ONE" order="1" width="30"/></dataItem>
        <dataItem name="D_TWO" oracleDatatype="number" width="10"
         defaultLabel="D Two">
          <dataDescriptor expression="D_TWO" order="2" width="10"/></dataItem>
        <dataItem name="D_THREE" datatype="vchar2" width="10"
         defaultLabel="D Three">
          <dataDescriptor expression="D_THREE" order="3" width="10"/></dataItem>
        <dataItem name="D_FOUR" oracleDatatype="number" width="10"
         defaultLabel="D Four">
          <dataDescriptor expression="D_FOUR" order="4" width="10"/></dataItem>
        <dataItem name="CS_TALLY" oracleDatatype="number" width="10"
         defaultLabel="Cs Tally">
          <dataDescriptor expression="CS_TALLY" order="5" width="10"/></dataItem>
      </group>
    </dataSource>
    <summary name="CS_ALL_TALLY" source="CS_TALLY" function="sum" width="20"
     reset="report" compute="report" columnFlags="8"/>
  </data>
  <layout>
  <section name="main">
    <body height="9.6">
      <frame name="M_BODY">
        <geometryInfo x="0.00" y="0.00" width="7.50" height="2.00"/>
        <generalLayout verticalElasticity="variable"/>
      <repeatingFrame name="R_OUTER" source="G_OUTER" printDirection="down">
        <geometryInfo x="0.20" y="{GT_BAND1_Y:.5f}" width="7.20"
         height="1.60"/>
        <generalLayout verticalElasticity="variable"/>
        <field name="F_KEY" source="BREAK_KEY" alignment="start">
          <font face="Arial" size="10"/>
          <geometryInfo x="0.20" y="{GT_BAND1_Y:.5f}" width="3.00"
           height="0.19"/>
        </field>
        <field name="F_GCODE" source="G_CODE" alignment="end">
          <font face="Arial" size="10"/>
          <geometryInfo x="6.50" y="{GT_BAND2_Y:.5f}" width="0.70"
           height="0.19"/>
        </field>

        <text name="B_CAP_ONE" minWidowLines="1">
          <textSettings spacing="single"/>
          <geometryInfo x="0.20" y="0.80" width="1.40" height="0.19"/>
          <textSegment><font face="Arial" size="10"/>
          <string><![CDATA[Cap One]]></string></textSegment>
        </text>
        <text name="B_CAP_TWO" minWidowLines="1">
          <textSettings spacing="single"/>
          <geometryInfo x="1.80" y="0.80" width="1.20" height="0.19"/>
          <textSegment><font face="Arial" size="10"/>
          <string><![CDATA[Cap Two]]></string></textSegment>
        </text>
        <text name="B_CAP_THREE" minWidowLines="1">
          <textSettings spacing="single"/>
          <geometryInfo x="3.20" y="0.80" width="1.20" height="0.19"/>
          <textSegment><font face="Arial" size="10"/>
          <string><![CDATA[Cap Three]]></string></textSegment>
        </text>
        <text name="B_CAP_FOUR" minWidowLines="1">
          <textSettings spacing="single"/>
          <geometryInfo x="4.60" y="0.80" width="0.90" height="0.19"/>
          <textSegment><font face="Arial" size="10"/>
          <string><![CDATA[Cap Four]]></string></textSegment>
        </text>

        <repeatingFrame name="R_DETAIL" source="G_DETAIL"
         printDirection="down"{_gap}>
          <geometryInfo x="0.20" y="1.00" width="7.20"
           height="{GT_DET_H:.5f}"/>
          <generalLayout verticalElasticity="variable"/>
          <field name="F_D_ONE" source="D_ONE" alignment="start">
            <font face="Arial" size="10"/>
            <geometryInfo x="0.20" y="1.00" width="1.40" height="0.19"/>
          </field>
          <field name="F_D_TWO" source="D_TWO" alignment="end">
            <font face="Arial" size="10"/>
            <geometryInfo x="1.80" y="1.00" width="1.20" height="0.19"/>
          </field>
          <field name="F_D_THREE" source="D_THREE" alignment="center">
            <font face="Arial" size="10"/>
            <geometryInfo x="3.20" y="1.00" width="1.20" height="0.19"/>
          </field>
          <field name="F_D_FOUR" source="D_FOUR" alignment="end">
            <font face="Arial" size="10"/>
            <geometryInfo x="4.60" y="1.00" width="1.20" height="0.19"/>
          </field>
        </repeatingFrame>

        <frame name="M_FOOT">
          <geometryInfo x="0.20" y="1.40" width="7.20" height="0.30"/>
          <text name="B_FOOT" minWidowLines="1">
            <textSettings spacing="single"/>
            <geometryInfo x="4.60" y="1.45" width="1.00" height="0.19"/>
            <textSegment><font face="Arial" size="10"/>
            <string><![CDATA[Foot Line]]></string></textSegment>
          </text>
          <field name="F_TALLY" source="CS_TALLY" alignment="end">
            <font face="Arial" size="10"/>
            <geometryInfo x="5.90" y="1.45" width="1.30" height="0.19"/>
          </field>
        </frame>
      </repeatingFrame>
      </frame>
    </body>
  </section>
  </layout>
</report>
"""


def test_grouped_detail_row_pitch_is_the_declaration_not_a_floor():
    """A declared pitch BELOW the emitter's old 0.20in floor must print at
    its declared value -- rounding it up drifts pagination."""
    rdl = _rdl(_grouped_xml())
    assert '<Tablix Name="Tablix_GroupedSubtotal">' in rdl, "not that path"
    rows = _row_heights(rdl, "GTS_Detail")
    assert len(rows) == 1, rows
    assert abs(rows[0] - GT_DET_H) < 0.002, rows
    assert GT_DET_H < 0.20, "fixture must exercise the sub-floor case"


def test_grouped_detail_row_pitch_adds_the_declared_gutter():
    """vertSpaceBetweenFrames is part of the pitch: same frame, declared
    gutter -> a taller row, by exactly the gutter."""
    gutter = 0.03500
    rdl = _rdl(_grouped_xml(gutter=gutter))
    rows = _row_heights(rdl, "GTS_Detail")
    assert len(rows) == 1, rows
    assert abs(rows[0] - (GT_DET_H + gutter)) < 0.002, rows


def test_grouped_band_caption_stack_steps_the_declared_pitch():
    rdl = _rdl(_grouped_xml())
    tops = _tops(rdl, "Tb_GH_")
    assert len(tops) >= 2, tops
    assert abs((tops[-1] - tops[0]) - GT_BAND_PITCH) < 0.002, tops


# --------------------------------------------------------------------------
# (3) REPORT-END BREAKDOWN -- pitch = declared height + declared gutter
# --------------------------------------------------------------------------

BD_H = 0.18994
BD_GAP = 0.05000


def _breakdown_xml(gutter: float = BD_GAP) -> str:
    _gap = f' vertSpaceBetweenFrames="{gutter:.5f}"' if gutter else ""
    return (
        '<?xml version="1.0"?><report name="PITCHBD" DTDVersion="9.0.2.0.10">'
        '<data>'
        '<dataSource name="Q_Main"><select><![CDATA[select main_a, main_b '
        'from t_main]]></select>'
        '<group name="G_Main"><dataItem name="MAIN_A" datatype="vchar2"/>'
        '<dataItem name="MAIN_B" datatype="date"/></group></dataSource>'
        '<dataSource name="Q_Side"><select><![CDATA[select side_key, side_cnt '
        'from t_side]]></select>'
        '<group name="G_Side"><dataItem name="SIDE_KEY" datatype="vchar2"/>'
        '<dataItem name="SIDE_CNT" datatype="number"/></group></dataSource>'
        '</data>'
        '<layout><section name="main">'
        '<frame name="M_Body"><geometryInfo x="0" y="0" width="8" height="3"/>'
        '<repeatingFrame name="R_Main" source="G_Main" printDirection="down">'
        '<geometryInfo x="0.2" y="0.2" width="7.5" height="0.2"/>'
        '<field name="F_A" source="MAIN_A"><geometryInfo x="0.2" y="0.2" '
        'width="3" height="0.2"/></field>'
        '<field name="F_B" source="MAIN_B"><geometryInfo x="3.4" y="0.2" '
        'width="1.4" height="0.2"/></field></repeatingFrame>'
        '<frame name="M_Totals"><geometryInfo x="0.2" y="1.2" width="7.5" '
        'height="0.8"/>'
        '<repeatingFrame name="R_Side" source="G_Side" '
        f'printDirection="down"{_gap}>'
        f'<geometryInfo x="0.3" y="1.3" width="4" height="{BD_H:.5f}"/>'
        '<text name="B_K"><geometryInfo x="0.3" y="1.3" width="2.4" '
        f'height="{BD_H:.5f}"/><textSegment><font face="Arial" size="8"/>'
        '<string><![CDATA[&SIDE_KEY Tally:]]></string>'
        '</textSegment></text>'
        '<field name="F_C" source="SIDE_CNT" alignment="end"><geometryInfo '
        f'x="2.9" y="1.3" width="0.9" height="{BD_H:.5f}"/></field>'
        '</repeatingFrame></frame></frame></section></layout></report>'
    )


def _breakdown_row_height(rdl: str) -> float:
    m = re.search(r'<Tablix Name="Tablix_Breakdown_0"(.*?)</Tablix>', rdl,
                  re.S)
    assert m, "breakdown table missing"
    h = re.search(r"<TablixRow>\s*<Height>([\d.]+)in", m.group(1))
    assert h, m.group(1)[:300]
    return float(h.group(1))


def test_breakdown_row_pitch_is_declared_height_plus_gutter():
    assert abs(_breakdown_row_height(_rdl(_breakdown_xml()))
               - (BD_H + BD_GAP)) < 0.002


def test_breakdown_row_pitch_drops_to_the_height_with_no_gutter():
    """The rule reads the source: no declared gutter -> pitch is the frame
    height alone, not a synthesized constant."""
    assert abs(_breakdown_row_height(_rdl(_breakdown_xml(gutter=0.0)))
               - BD_H) < 0.002


# --------------------------------------------------------------------------
# (4) POSITIONAL FRAME -- a declared caption stack keeps EVERY declared gap
# --------------------------------------------------------------------------

PF_FRAME_Y = 7.15686
PF_CAPS = (7.16382, 7.38818, 7.59229, 7.79944)   # declared caption stack
PF_GAPS = [round(PF_CAPS[i + 1] - PF_CAPS[i], 5)
           for i in range(len(PF_CAPS) - 1)]


def _positional_xml() -> str:
    caps = "".join(
        f'<text name="B_C{i}" minWidowLines="1">'
        f'<textSettings spacing="single"/>'
        f'<geometryInfo x="4.46484" y="{y:.5f}" width="1.20000"'
        f' height="0.21000"/><textSegment>'
        f'<font face="Arial" size="8"/>'
        f'<string><![CDATA[Caption {i}]]></string></textSegment></text>'
        for i, y in enumerate(PF_CAPS))
    return (
        '<?xml version="1.0"?><report name="PITCHPF"'
        ' DTDVersion="9.0.2.0.10"><data>'
        '<dataSource name="Q_Rec"><select><![CDATA[select rec_key, rec_note '
        'from t_rec]]></select>'
        '<group name="G_Rec"><dataItem name="REC_KEY" datatype="vchar2"/>'
        '<dataItem name="REC_NOTE" datatype="vchar2"/></group>'
        '</dataSource></data>'
        '<layout><section name="main" width="8.50000" height="11.00000">'
        '<body width="7.50000" height="10.00000">'
        '<location x="0.50000" y="0.50000"/>'
        '<repeatingFrame name="R_Rec" source="G_Rec" printDirection="down"'
        ' maxRecordsPerPage="1">'
        '<geometryInfo x="0.00000" y="0.00000" width="7.50000"'
        ' height="9.60000"/>'
        '<field name="F_KEY" source="REC_KEY"><font face="Arial" size="10"/>'
        '<geometryInfo x="0.10000" y="0.20000" width="3.00000"'
        ' height="0.20000"/></field>'
        '<field name="F_NOTE" source="REC_NOTE"><font face="Arial" size="10"/>'
        '<geometryInfo x="0.10000" y="0.60000" width="3.00000"'
        ' height="0.20000"/></field>'
        '<frame name="M_VOUCHER">'
        f'<geometryInfo x="4.40000" y="{PF_FRAME_Y:.5f}" width="3.00000"'
        ' height="1.80000"/>'
        f'{caps}</frame></repeatingFrame>'
        '</body></section></layout></report>'
    )


def test_positional_caption_stack_keeps_every_declared_gap():
    """The topmost caption is declared 0.00696in below its frame's own top --
    inside the emitter's 0.02in minimum. Nudging THAT box alone shortened the
    first declared gap; the whole stack must shift rigidly instead, so every
    declared gap survives."""
    rdl = _rdl(_positional_xml())
    # the caption stack's own boxes, identified by the text they carry
    stack = sorted(
        round(float(re.search(r"<Top>([\d.]+)in</Top>", b).group(1)), 5)
        for b in re.findall(r"<Textbox Name=\"[^\"]+\">(.*?)</Textbox>",
                            rdl, re.S)
        if "Caption " in b and re.search(r"<Top>([\d.]+)in</Top>", b))
    assert len(stack) == len(PF_CAPS), stack
    gaps = [round(stack[i + 1] - stack[i], 5) for i in range(len(stack) - 1)]
    for got, want in zip(gaps, PF_GAPS):
        assert abs(got - want) < 0.002, (gaps, PF_GAPS)
    # the declaration is deliberately NON-uniform, so a stack flowed at any
    # constant cannot pass the loop above
    assert max(PF_GAPS) - min(PF_GAPS) > 0.015, PF_GAPS
