"""
Declared edge-rule dialect guards.

Oracle's hideXBorder attributes select WHICH edges of a linePattern="solid"
box actually paint (hideLeft/Right/Top = only the bottom rule — the
per-data-row underline idiom). The dialect was unparsed, so those declared
rules never reached the RDL. Also guards two stacked-list band fixes: the
header-band y-clustering tolerance (an offset second-line caption was
silently dropped) and the right-edge declared box keeping its span (the
width reserve clipped its final glyphs).
"""
import re
import xml.etree.ElementTree as ET

from converter.models import DataItem, DataQuery
from converter.generators import rdl as R
from converter.parsers.oracle_xml import parse_oracle_xml


def _stat_xml(with_hide=True, with_close_line=True, close_y=0.48):
    hide = ('linePattern="solid" hideLeftBorder="yes" '
            'hideRightBorder="yes" hideTopBorder="yes"'
            if with_hide else 'linePattern="solid"')
    close_line = f"""
          <line name="B_CLOSE" arrow="none">
            <geometryInfo x="0.3" y="{close_y}" width="7.0" height="0.0"/>
            <visualSettings linePattern="solid"/>
            <points><point x="0.3" y="{close_y}"/><point x="7.3" y="{close_y}"/></points>
          </line>
    """ if with_close_line else ""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<report name="EDGES" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q1">
      <select><![CDATA[SELECT LBL, CNT FROM T]]></select>
      <group name="G_SG">
        <dataItem name="SG" datatype="vchar2" width="30" columnFlags="1"
         defaultLabel="Sg">
          <dataDescriptor expression="SG" order="1" width="30"/>
        </dataItem>
      </group>
      <group name="G_SR">
        <dataItem name="LBL" datatype="vchar2" width="60" columnFlags="1"
         defaultLabel="Lbl">
          <dataDescriptor expression="LBL" order="2" width="60"/>
        </dataItem>
        <dataItem name="CNT" oracleDatatype="number" width="22"
         defaultLabel="Cnt">
          <dataDescriptor expression="CNT" order="3" width="22"/>
        </dataItem>
      </group>
    </dataSource>
  </data>
  <layout>
  <section name="main">
    <body height="9.6">
      <repeatingFrame name="R_SG" source="G_SG" printDirection="down">
        <geometryInfo x="0.26" y="0.0" width="7.0" height="0.6"/>
        <generalLayout verticalElasticity="variable"/>
        <repeatingFrame name="R_SR" source="G_SR" printDirection="down">
          <geometryInfo x="0.26" y="0.25" width="6.99" height="0.19"/>
          <generalLayout verticalElasticity="variable"/>
          <visualSettings fillBackgroundColor="r100g100b100" {hide}/>
          <field name="F_L" source="LBL" alignment="start">
            <font face="Arial" size="10"/>
            <geometryInfo x="0.3" y="0.25" width="4.0" height="0.19"/>
          </field>
          <field name="F_N" source="CNT" alignment="end">
            <font face="Arial" size="10"/>
            <geometryInfo x="5.0" y="0.25" width="1.0" height="0.19"/>
          </field>
        </repeatingFrame>
        {close_line}
      </repeatingFrame>
    </body>
  </section>
  </layout>
</report>
"""


def _q():
    return DataQuery(name="Q1", sql="SELECT 1",
                     items=[DataItem(name="LBL"), DataItem(name="CNT")])


def test_hidden_edges_parse_on_frames():
    rep = parse_oracle_xml(_stat_xml().encode("utf-8"))

    def _find(g, name):
        if getattr(g, "name", "") == name:
            return g
        for c in (getattr(g, "children", None) or []):
            r = _find(c, name)
            if r is not None:
                return r
        return None
    frame = next(f for f in (_find(lg, "R_SR")
                             for lg in rep.layout) if f is not None)
    assert frame.border_pattern.lower() == "solid"
    assert set(frame.hidden_edges.split(",")) == {"left", "right", "top"}


def test_section_row_edge_rule_selects_visible_edges():
    rep = parse_oracle_xml(_stat_xml().encode("utf-8"))
    rule = R._section_row_edge_rule(rep, _q(), ["LBL", "CNT"])
    assert rule is not None
    assert rule["edges"] == ["bottom"]
    assert rule["group_end_line"] is True


def test_section_row_edge_rule_none_without_hide_dialect():
    # a solid frame WITHOUT edge selection is a full box, not this idiom
    rep = parse_oracle_xml(_stat_xml(with_hide=False).encode("utf-8"))
    assert R._section_row_edge_rule(rep, _q(), ["LBL", "CNT"]) is None


def test_section_tablix_emits_bottom_rule_and_group_close():
    rep = parse_oracle_xml(_stat_xml().encode("utf-8"))
    palette = {"band_bg": "#ffffff", "band_fg": "#000000"}
    tx = R._build_section_tablix(rep, "S1", _q(), ["LBL", "CNT"],
                                 "Hdr", palette)
    x = ET.tostring(tx, encoding="unicode")
    # detail cells carry ONLY the bottom rule
    assert x.count("<BottomBorder>") >= 3  # 2 detail + 2 trailer cells
    assert "<TopBorder>" not in x and "<LeftBorder>" not in x


def test_group_close_line_emits_second_rule_at_declared_gap():
    # the close-out <line> is declared 0.04in BELOW the row frame's
    # bottom (0.25 + 0.19 = 0.44 vs 0.48) — the truth's heavy DOUBLE
    # rule: the second stroke is a REAL trailer row at the declared gap,
    # not a tablix BottomBorder hugging the last row's underline
    rep = parse_oracle_xml(_stat_xml().encode("utf-8"))
    palette = {"band_bg": "#ffffff", "band_fg": "#000000"}
    tx = R._build_section_tablix(rep, "S1", _q(), ["LBL", "CNT"],
                                 "Hdr", palette)
    x = ET.tostring(tx, encoding="unicode")
    m = re.search(r'<TablixRow>\s*<Height>0\.040in</Height>(.*?)'
                  r'</TablixRow>', x, re.S)
    assert m, "second-rule trailer row missing (declared 0.04in gap)"
    assert 'Name="S1_Rule2_0"' in m.group(1)
    assert "<BottomBorder>" in m.group(1)
    # the collapsed outer border is GONE (it rendered as one line)
    tail = re.search(r"</TablixRowHierarchy>.*$", x, re.S).group(0)
    assert "<BottomBorder>" not in tail


def test_flush_close_line_keeps_single_bottom_border():
    # negative twin: a close-out line declared FLUSH with the row frame
    # (no white gap) keeps the plain tablix BottomBorder — the trailer
    # row is geometry-driven, never unconditional
    rep = parse_oracle_xml(_stat_xml(close_y=0.44).encode("utf-8"))
    palette = {"band_bg": "#ffffff", "band_fg": "#000000"}
    tx = R._build_section_tablix(rep, "S1", _q(), ["LBL", "CNT"],
                                 "Hdr", palette)
    x = ET.tostring(tx, encoding="unicode")
    assert "Rule2" not in x
    tail = re.search(r"</TablixRowHierarchy>.*$", x, re.S).group(0)
    assert "<BottomBorder>" in tail


def test_section_tablix_no_rules_without_declaration():
    rep = parse_oracle_xml(_stat_xml(with_hide=False).encode("utf-8"))
    palette = {"band_bg": "#ffffff", "band_fg": "#000000"}
    tx = R._build_section_tablix(rep, "S1", _q(), ["LBL", "CNT"],
                                 "Hdr", palette)
    x = ET.tostring(tx, encoding="unicode")
    assert "<BottomBorder>" not in x


# ---------------------------------------------------------------------------
# Stacked-list band fixes (header clustering + right-edge span)
# ---------------------------------------------------------------------------

def _stacked_xml():
    return """<?xml version="1.0" encoding="UTF-8"?>
<report name="SLIST" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q_Permit">
      <select><![CDATA[SELECT A, B, C, V, A2, B2, C2 FROM T]]></select>
      <group name="G_P">
        <dataItem name="A" datatype="vchar2" width="30" columnFlags="1"
         defaultLabel="A"><dataDescriptor expression="A" order="1" width="30"/></dataItem>
        <dataItem name="B" datatype="vchar2" width="30" defaultLabel="B">
          <dataDescriptor expression="B" order="2" width="30"/></dataItem>
        <dataItem name="C" datatype="vchar2" width="30" defaultLabel="C">
          <dataDescriptor expression="C" order="3" width="30"/></dataItem>
        <dataItem name="V" datatype="vchar2" width="3" defaultLabel="V">
          <dataDescriptor expression="V" order="4" width="3"/></dataItem>
        <dataItem name="A2" datatype="vchar2" width="30" defaultLabel="A2">
          <dataDescriptor expression="A2" order="5" width="30"/></dataItem>
        <dataItem name="B2" datatype="vchar2" width="30" defaultLabel="B2">
          <dataDescriptor expression="B2" order="6" width="30"/></dataItem>
        <dataItem name="C2" datatype="vchar2" width="30" defaultLabel="C2">
          <dataDescriptor expression="C2" order="7" width="30"/></dataItem>
      </group>
    </dataSource>
  </data>
  <layout>
  <section name="main" width="11.00000" height="8.50000"
   orientation="landscape">
    <body width="10.42627" height="7.14587">
      <location x="0.29248" y="0.76038"/>
      <repeatingFrame name="R_P" source="G_P" printDirection="down">
        <geometryInfo x="0.02" y="0.44" width="10.37" height="0.68"/>
        <generalLayout verticalElasticity="variable"/>
        <field name="F_A" source="A"><font face="Arial" size="10"/>
          <geometryInfo x="0.02" y="0.45" width="1.6" height="0.18"/></field>
        <field name="F_B" source="B"><font face="Arial" size="10"/>
          <geometryInfo x="1.81" y="0.45" width="3.6" height="0.18"/></field>
        <field name="F_C" source="C"><font face="Arial" size="10"/>
          <geometryInfo x="5.55" y="0.45" width="4.0" height="0.18"/></field>
        <field name="F_V" source="V"><font face="Arial" size="10"/>
          <geometryInfo x="9.79" y="0.45" width="0.60" height="0.18"/></field>
        <field name="F_A2" source="A2"><font face="Arial" size="10"/>
          <geometryInfo x="0.02" y="0.64" width="1.6" height="0.18"/></field>
        <field name="F_B2" source="B2"><font face="Arial" size="10"/>
          <geometryInfo x="1.81" y="0.64" width="3.6" height="0.18"/></field>
        <field name="F_C2" source="C2"><font face="Arial" size="10"/>
          <geometryInfo x="5.56" y="0.64" width="4.0" height="0.18"/></field>
      </repeatingFrame>
      <frame name="M_HDR">
        <geometryInfo x="0.02" y="0.0" width="10.37" height="0.38"/>
        <text name="B_H1"><geometryInfo x="0.02" y="0.0" width="1.6" height="0.17"/>
          <textSegment><font face="Arial" size="10" bold="yes"/>
            <string><![CDATA[Col One]]></string></textSegment></text>
        <text name="B_H2"><geometryInfo x="1.81" y="0.0" width="3.6" height="0.17"/>
          <textSegment><font face="Arial" size="10" bold="yes"/>
            <string><![CDATA[Col Two]]></string></textSegment></text>
        <text name="B_H3"><geometryInfo x="5.55" y="0.0" width="4.0" height="0.17"/>
          <textSegment><font face="Arial" size="10" bold="yes"/>
            <string><![CDATA[Col Three]]></string></textSegment></text>
        <text name="B_H4"><geometryInfo x="9.79" y="0.0" width="0.604" height="0.17"/>
          <textSegment><font face="Arial" size="10" bold="yes"/>
            <string><![CDATA[EdgeCap]]></string></textSegment></text>
        <text name="B_S1"><geometryInfo x="0.02" y="0.198" width="1.6" height="0.17"/>
          <textSegment><font face="Arial" size="10" bold="yes"/>
            <string><![CDATA[Sub One]]></string></textSegment></text>
        <text name="B_S2"><geometryInfo x="1.81" y="0.198" width="3.6" height="0.17"/>
          <textSegment><font face="Arial" size="10" bold="yes"/>
            <string><![CDATA[Sub Two]]></string></textSegment></text>
        <text name="B_S3"><geometryInfo x="5.56" y="0.208" width="4.0" height="0.17"/>
          <textSegment><font face="Arial" size="10" bold="yes"/>
            <string><![CDATA[Sub Offset]]></string></textSegment></text>
      </frame>
    </body>
  </section>
  </layout>
</report>
"""


def test_stacked_header_band_clusters_offset_labels():
    rep = parse_oracle_xml(_stacked_xml().encode("utf-8"))
    sl = R._stacked_list_columns(rep)
    assert sl is not None
    all_lbls = {t for band in sl["headers"] for _x, t in band}
    # the 0.01in-offset second-line caption survives band clustering
    assert "Sub Offset" in all_lbls
    assert "Sub One" in all_lbls and "Sub Two" in all_lbls


def test_right_edge_declared_box_keeps_span():
    rep = parse_oracle_xml(_stacked_xml().encode("utf-8"))
    q = DataQuery(name="Q_Permit", sql="SELECT 1",
                  items=[DataItem(name=n) for n in
                         ["A", "B", "C", "V", "A2", "B2", "C2"]])
    tx = R._build_stacked_list_tablix(rep, q)
    x = ET.tostring(tx, encoding="unicode")
    m = re.search(
        r'Name="Tb_SLHdr_0_3">.*?<Left>([\d.]+)in</Left>\s*'
        r'<Width>([\d.]+)in</Width>', x, re.S)
    assert m, "right-edge header missing"
    left, width = float(m.group(1)), float(m.group(2))
    assert width >= 0.55, f"right-edge caption clipped to {width}in"
    # the tablix indent shrank so the span still fits the printable width
    mt = re.search(r"</TablixRowHierarchy>.*?<Left>([\d.]+)in</Left>", x, re.S)
    mw = re.search(r"</TablixRowHierarchy>.*?<Width>([\d.]+)in</Width>", x, re.S)
    page_printable = 11.0 - 0.5
    assert float(mt.group(1)) + float(mw.group(1)) <= page_printable + 0.02
