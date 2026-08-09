"""Declared row-alternation (banded record) fill + no invented separator.

Oracle bands a repeating record frame by counting records into a
report-reset summary column and testing its parity in the frame's format
trigger::

    IF <counter> MOD 2 = 0 THEN
      SRW.SET_FOREGROUND_FILL_COLOR('<tint>'); SRW.SET_FILL_PATTERN('solid');
    ELSE ... END IF; RETURN(TRUE);

That trigger was dropped whole (neither the visibility nor the styling
translator matches an IF/ELSE pair of fill calls), so every record block
printed white while the truth export alternates a light tint down every
page.  It is now recognised STRUCTURALLY -- by the MOD-2 parity test
guarding SRW fill calls, never by a counter/column/report name -- and
translated to a BackgroundColor expression on the record's rect.

PARITY (truth-measured on an exported banded list): the header band is
tinted, then record 1 is unpainted and record 2 is tinted, alternating
from there.  Oracle's counter is a 1-based running count and so is
``RowNumber(Nothing)``, so ``MOD 2 = 0`` maps across unchanged.

The same truth export contains ZERO strokes anywhere on the page: the
tint alternation is the only separator between record blocks, so the
record rect may only carry a border the source actually declares.
"""
import re

from converter import convert
from converter.translators.plsql_formula import translate_row_alternation_fill


_BAND_TRIGGER = """FUNCTION R_REC_FT RETURN BOOLEAN IS
BEGIN
  IF :CS_COUNTER MOD 2 = 0 THEN
    SRW.Set_Foreground_Fill_Color('gray8') ;
    SRW.Set_Fill_Pattern('solid') ;
  ELSE
    SRW.Set_Foreground_Fill_Color('white') ;
    SRW.Set_Fill_Pattern('solid') ;
  END IF ;

  RETURN(TRUE) ;
END ;"""


def _band_xml(trigger_body=_BAND_TRIGGER, wire_trigger=True,
              edge_attrs=""):
    """A stacked-list record source; optionally wires a format trigger to
    the repeating record frame and/or declares an edge rule on it."""
    adv = ('<advancedLayout formatTrigger="r_rec_ft"/>'
           if wire_trigger else "")
    units = ""
    if trigger_body:
        units = (f'<programUnits><function name="r_rec_ft"><textSource>'
                 f'<![CDATA[{trigger_body}]]>'
                 f'</textSource></function></programUnits>')
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<report name="BANDED" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q_Rec">
      <select><![CDATA[SELECT A, B, C, V, A2, B2, C2 FROM T]]></select>
      <group name="G_R">
        <dataItem name="A" datatype="vchar2" width="30" defaultLabel="A">
          <dataDescriptor expression="A" order="1" width="30"/></dataItem>
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
      <repeatingFrame name="R_R" source="G_R" printDirection="down">
        <geometryInfo x="0.02" y="0.44" width="10.37" height="0.68"/>
        <generalLayout verticalElasticity="variable"/>
        {adv}
        <visualSettings {edge_attrs}/>
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
            <string><![CDATA[Sub Three]]></string></textSegment></text>
      </frame>
    </body>
  </section>
  </layout>
  {units}
</report>
"""


def _record_rect_style(rdl):
    """The record rect's own <Style> block (up to its ReportItems)."""
    m = re.search(r'<Rectangle Name="SL_Detail">\s*<Style>(.*?)</Style>\s*'
                  r'<ReportItems>', rdl, re.S)
    assert m, "stacked-list record rect missing"
    return m.group(1)


# --------------------------------------------------------------------------
# (a) the declared MOD-2 band reaches the RDL
# --------------------------------------------------------------------------

def test_declared_mod2_trigger_bands_alternating_records():
    rdl = convert(_band_xml().encode())["rdl_xml"]
    assert "Tablix_StackedList" in rdl, "fixture must route stacked"
    style = _record_rect_style(rdl)
    m = re.search(r"<BackgroundColor>(.*?)</BackgroundColor>", style, re.S)
    assert m, "record rect lost its BackgroundColor"
    expr = m.group(1)
    assert expr.startswith("="), (
        "a declared MOD-2 band must become a BackgroundColor EXPRESSION, "
        f"got {expr!r}")
    assert "RowNumber(Nothing) Mod 2 = 0" in expr, (
        f"band must key off the row parity, got {expr!r}")
    # PARITY: Oracle's 1-based counter tints the SECOND record, so the
    # MOD-2-zero branch carries the TINT and the other branch the base.
    lits = re.findall(r'"([^"]+)"', expr)
    assert len(lits) == 2, f"expected two branch colours, got {lits}"
    tint, base = lits
    assert tint.lower() not in ("#ffffff", "transparent", "white"), (
        f"the =0 branch must carry the declared tint, got {tint!r}")
    assert base.lower() in ("#ffffff", "transparent", "white"), (
        f"the other branch must stay the unpainted base, got {base!r}")


def test_banded_record_fields_do_not_punch_out_the_band():
    """An opaque white field box paints over the record's band. When the
    source declares a band the field boxes must carry no background."""
    rdl = convert(_band_xml().encode())["rdl_xml"]
    m = re.search(r'<Rectangle Name="SL_Detail">.*?</Rectangle>', rdl, re.S)
    assert m
    body = m.group(0)
    for tb in re.finditer(r'<Textbox Name="Tb_SLDet_[^"]*">(.*?)</Textbox>',
                          body, re.S):
        assert "<BackgroundColor>#ffffff</BackgroundColor>" \
            not in tb.group(1), "field box repaints white over the band"


def test_no_band_without_a_declared_alternation_trigger():
    """Same layout, trigger never wired to the frame -> plain white rows.
    (Reinforces test_no_invented_zebra_striping_anywhere for this path.)"""
    rdl = convert(_band_xml(wire_trigger=False).encode())["rdl_xml"]
    assert "Tablix_StackedList" in rdl
    assert "RowNumber(Nothing) Mod 2" not in rdl, (
        "no source declaration -> no striping anywhere in the RDL")
    assert "<BackgroundColor>#ffffff</BackgroundColor>" \
        in _record_rect_style(rdl)


def test_alternation_translator_declines_non_band_triggers():
    """Structural detector: anything outside the MOD-2-guarded fill shape
    declines rather than guessing a band."""
    T = translate_row_alternation_fill
    assert T(_BAND_TRIGGER) == {"even": "gray8", "odd": "white"}
    # parity written the other way round: the =1 branch is the ODD one
    flipped = _BAND_TRIGGER.replace("MOD 2 = 0", "MOD 2 = 1")
    assert T(flipped) == {"even": "white", "odd": "gray8"}
    # not a parity test
    assert T(_BAND_TRIGGER.replace("MOD 2 = 0", "MOD 3 = 0")) is None
    assert T(_BAND_TRIGGER.replace(":CS_COUNTER MOD 2 = 0", ":X = 0")) is None
    # a visibility trigger is not a band
    assert T("FUNCTION F RETURN BOOLEAN IS BEGIN IF :X MOD 2 = 0 THEN "
             "RETURN(FALSE); END IF; RETURN(TRUE); END;") is None
    # the branch does something other than fill -> decline whole
    assert T(_BAND_TRIGGER.replace(
        "SRW.Set_Foreground_Fill_Color('gray8') ;",
        "SRW.Set_Foreground_Fill_Color('gray8') ;"
        " SRW.Set_Font_Weight(SRW.BOLD_WEIGHT) ;")) is None
    # DIALECT: the fill PATTERN gates the paint. With none declared in the
    # branches there is no paint decision to translate...
    assert T(re.sub(r"(?i)\s*SRW\.Set_Fill_Pattern\([^)]*\)\s*;", "",
                    _BAND_TRIGGER)) is None
    # ...and an explicitly transparent pattern paints neither branch, so no
    # band reaches the RDL.
    both_clear = _BAND_TRIGGER.replace("'solid'", "'transparent'")
    assert T(both_clear) == {"even": "", "odd": ""}
    rdl = convert(_band_xml(trigger_body=both_clear).encode())["rdl_xml"]
    assert "RowNumber(Nothing) Mod 2" not in rdl, (
        "two transparent branches are not a band")
    assert T("") is None


# --------------------------------------------------------------------------
# (b) no separator the source never declared
# --------------------------------------------------------------------------

def test_record_block_carries_no_undeclared_separator():
    """The truth export draws NO stroke between record blocks."""
    for wired in (True, False):
        style = _record_rect_style(
            convert(_band_xml(wire_trigger=wired).encode())["rdl_xml"])
        assert "Border" not in style, (
            "record rect invented a separator the source never declared: "
            f"{style!r}")


def test_declared_record_edge_rule_still_paints():
    """The declaration-driven path is untouched: a solid-linePattern frame
    with hidden left/right/top edges keeps its bottom rule."""
    edge = ('lineWidth="1" linePattern="solid" hideLeftBorder="yes" '
            'hideRightBorder="yes" hideTopBorder="yes"')
    style = _record_rect_style(
        convert(_band_xml(edge_attrs=edge).encode())["rdl_xml"])
    assert "<BottomBorder>" in style, (
        "a DECLARED per-row edge rule must still paint")
