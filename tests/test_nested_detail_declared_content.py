"""
Nested master-detail DECLARED-content guards.

A master-detail list whose detail record spans multiple declared lines
(a key row plus follow-on lines holding several fields side by side)
must emit EVERY declared detail field -- the follow-on multi-field line
was silently dropped (only single-field wrap lines survived). The same
family of defects, all declaration-driven:

  * the outer band field declared with NO caption printed with an
    invented "Label: " prefix;
  * declared heavy solid rules (above the band / above each record /
    a section-level rule closing the group) never painted;
  * a margin-band title in the header-candidate window was re-emitted
    as a body column header (printed twice: chrome + body).

Each positive test has a negative twin proving the gate is driven by the
declaration, not unconditional new behavior.
"""
import re
import xml.etree.ElementTree as ET

from converter.generators.rdl import generate_rdl
from converter.parsers.oracle_xml import parse_oracle_xml


def _report_xml(with_lines=True, with_band_label=False, with_margin_title=True):
    lines_master = """
        <line name="B_TOP_RULE" arrow="none">
          <geometryInfo x="0.014" y="0.014" width="7.46" height="0.0"/>
          <visualSettings lineWidth="2" linePattern="solid"/>
        </line>
    """ if with_lines else ""
    line_detail = """
          <line name="B_ROW_RULE" arrow="none">
            <geometryInfo x="0.15" y="0.324" width="7.33" height="0.0"/>
            <visualSettings lineWidth="1" linePattern="solid"/>
          </line>
    """ if with_lines else ""
    line_section = """
      <line name="B_END_RULE" arrow="none">
        <geometryInfo x="0.014" y="1.748" width="7.46" height="0.0"/>
        <visualSettings lineWidth="2" linePattern="solid"/>
      </line>
    """ if with_lines else ""
    band_label = """
        <text name="B_MLBL">
          <geometryInfo x="0.01" y="0.05" width="0.9" height="0.18"/>
          <textSegment><font face="Arial" size="10" bold="yes"/>
            <string><![CDATA[Region]]></string>
          </textSegment>
        </text>
    """ if with_band_label else ""
    margin_title = """
      <text name="B_Title">
        <textSettings justify="center" spacing="single"/>
        <geometryInfo x="3.0" y="0.24" width="2.5" height="0.17"/>
        <textSegment><font face="Arial" size="12" bold="yes"/>
          <string><![CDATA[Synthetic Master List]]></string>
        </textSegment>
      </text>
    """ if with_margin_title else ""
    band_label_x = "0.95" if with_band_label else "0.01"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<report name="ND_DECL" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q_MAIN">
      <select><![CDATA[SELECT MVAL, ACOL, BCOL, CCOL, ADDR, NOTE FROM T]]></select>
      <group name="G_MASTER">
        <dataItem name="MVAL" datatype="vchar2" width="30" columnFlags="1"
         defaultLabel="Mval">
          <dataDescriptor expression="MVAL" order="1" width="30"/>
        </dataItem>
      </group>
      <group name="G_DETAIL">
        <dataItem name="ACOL" datatype="vchar2" width="30" columnFlags="1"
         defaultLabel="Acol">
          <dataDescriptor expression="ACOL" order="2" width="30"/>
        </dataItem>
        <dataItem name="BCOL" datatype="vchar2" width="30"
         defaultLabel="Bcol">
          <dataDescriptor expression="BCOL" order="3" width="30"/>
        </dataItem>
        <dataItem name="CCOL" datatype="vchar2" width="30"
         defaultLabel="Ccol">
          <dataDescriptor expression="CCOL" order="4" width="30"/>
        </dataItem>
        <dataItem name="ADDR" datatype="vchar2" width="200"
         defaultLabel="Addr">
          <dataDescriptor expression="ADDR" order="5" width="200"/>
        </dataItem>
        <dataItem name="NOTE" datatype="vchar2" width="200"
         defaultLabel="Note">
          <dataDescriptor expression="NOTE" order="6" width="200"/>
        </dataItem>
      </group>
    </dataSource>
  </data>
  <layout>
  <section name="main">
    <body height="9.6">
      <repeatingFrame name="R_M" source="G_MASTER" printDirection="downAcross">
        <geometryInfo x="0.0" y="0.0" width="7.49" height="1.70"/>
        <generalLayout verticalElasticity="variable"/>
        {lines_master}
        {band_label}
        <field name="F_MVAL" source="MVAL" alignment="start">
          <font face="Arial" size="10" bold="yes"/>
          <geometryInfo x="{band_label_x}" y="0.052" width="3.0" height="0.19"/>
        </field>
        <repeatingFrame name="R_D" source="G_DETAIL" printDirection="down">
          <geometryInfo x="0.146" y="0.283" width="7.34" height="1.30"/>
          <generalLayout verticalElasticity="variable"/>
          {line_detail}
          <field name="F_A" source="ACOL" alignment="start">
            <font face="Arial" size="10" bold="yes"/>
            <geometryInfo x="0.146" y="0.376" width="4.31" height="0.19"/>
          </field>
          <field name="F_B" source="BCOL" alignment="start">
            <font face="Arial" size="10" bold="yes"/>
            <geometryInfo x="4.552" y="0.375" width="0.80" height="0.19"/>
          </field>
          <field name="F_C" source="CCOL" alignment="start">
            <font face="Arial" size="10" bold="yes"/>
            <geometryInfo x="5.469" y="0.376" width="2.02" height="0.19"/>
          </field>
          <field name="F_ADDR" source="ADDR" alignment="start">
            <font face="Arial" size="10"/>
            <geometryInfo x="0.146" y="0.625" width="3.55" height="0.38"/>
            <generalLayout verticalElasticity="expand"/>
          </field>
          <field name="F_NOTE" source="NOTE" alignment="start">
            <font face="Arial" size="10"/>
            <geometryInfo x="3.793" y="0.625" width="3.69" height="0.19"/>
            <generalLayout verticalElasticity="variable"/>
          </field>
        </repeatingFrame>
      </repeatingFrame>
      {line_section}
    </body>
    <margin>
      {margin_title}
    </margin>
  </section>
  </layout>
</report>
"""


def _rdl(**kw):
    rep = parse_oracle_xml(_report_xml(**kw).encode("utf-8"))
    rdl = generate_rdl(rep)
    ET.fromstring(rdl)  # well-formed
    return rdl


def test_multi_field_wrap_line_emits_all_declared_fields():
    rdl = _rdl()
    # the follow-on line's BOTH fields must reference their columns
    assert "Fields!ADDR.Value" in rdl
    assert "Fields!NOTE.Value" in rdl
    # ...at their declared side-by-side positions (same Top, distinct Left)
    m_addr = re.search(
        r'Textbox Name="Tb_NDWrap_\d+_ADDR".*?<Top>([\d.]+)in</Top>'
        r'\s*<Left>([\d.]+)in</Left>', rdl, re.S)
    m_note = re.search(
        r'Textbox Name="Tb_NDWrap_\d+_NOTE".*?<Top>([\d.]+)in</Top>'
        r'\s*<Left>([\d.]+)in</Left>', rdl, re.S)
    assert m_addr and m_note, "wrap textboxes missing"
    assert m_addr.group(1) == m_note.group(1)          # one physical line
    assert float(m_addr.group(2)) < float(m_note.group(2))
    # the left field's width must stop before its right-hand neighbour
    m_w = re.search(
        r'Textbox Name="Tb_NDWrap_\d+_ADDR".*?<Width>([\d.]+)in</Width>',
        rdl, re.S)
    assert m_w and float(m_w.group(1)) <= float(m_note.group(2))


def test_band_value_bare_when_layout_declares_no_caption():
    rdl = _rdl()
    assert "=Fields!MVAL.Value" in rdl
    assert "Mval: " not in rdl  # the invented prefix


def test_band_keeps_caption_when_layout_declares_a_label():
    # negative twin: a declared label beside the band field keeps the
    # caption'd band (the bare-value rule is declaration-driven).
    #
    # STRICTER than the old "one merged '=Label: ' & CStr(value)' run"
    # assertion, which this fixture's own declaration disproves: the layout
    # declares the caption and the value as TWO objects at two x positions
    # (0.01in and 0.95in here), and the truth render prints them there —
    # a single run pins the value immediately after the caption glyphs, so
    # it can never land at its declared x. Assert both boxes AND their
    # declared geometry.
    rdl = _rdl(with_band_label=True)
    lbl = re.search(r'<Textbox Name="Tb_ND_BandL">.*?</Textbox>', rdl, re.S)
    val = re.search(r'<Textbox Name="Tb_ND_BandV">.*?</Textbox>', rdl, re.S)
    assert lbl and val, "declared band caption and value need their own boxes"
    assert "Region" in lbl.group(0), "the declared caption must print"
    assert "=Fields!MVAL.Value" in val.group(0)
    lx = re.search(r"<Left>([\d.]+)in</Left>", lbl.group(0))
    vx = re.search(r"<Left>([\d.]+)in</Left>", val.group(0))
    assert lx and vx
    assert abs(float(lx.group(1)) - 0.01) < 0.005, lx.group(1)
    assert abs(float(vx.group(1)) - 0.95) < 0.005, vx.group(1)
    # ...and the caption box is the DECLARED width, not a stretched band
    lw = re.search(r"<Width>([\d.]+)in</Width>", lbl.group(0))
    assert lw and abs(float(lw.group(1)) - 0.9) < 0.005, lw.group(1)


def _rule_geom(rdl, name):
    """(left, width, stroke_pt) of an emitted <Line> rule, or None."""
    m = re.search(r'<Line Name="%s">(.*?)</Line>' % name, rdl, re.S)
    if not m:
        return None
    body = m.group(1)
    pt = re.search(r"<Border>.*?<Width>([\d.]+)pt</Width>", body, re.S)
    tail = body.split("</Style>")[-1]
    left = re.search(r"<Left>([\d.]+)in</Left>", tail)
    wid = re.search(r"<Width>([\d.]+)in</Width>", tail)
    if not (pt and left and wid):
        return None
    return float(left.group(1)), float(wid.group(1)), float(pt.group(1))


def test_declared_heavy_rules_emit():
    """Every declared body <line> prints as a REAL rule at its OWN declared
    endpoints. A band-wide Rectangle border was the old stand-in: it starts
    and ends at whatever the enclosing band measures, so the master rule and
    the record rule -- which the source insets differently -- both printed
    edge to edge (truth-measured: the record rule is inset from the master
    rule above it)."""
    rdl = _rdl()
    # band rect: declared 2pt master rule at x 0.014 .. 7.474
    band = _rule_geom(rdl, "Rule_B_TOP_RULE")
    assert band, "the declared master rule must emit as a real rule"
    assert abs(band[0] - 0.014) < 0.02 and abs(band[1] - 7.46) < 0.02, band
    assert band[2] == 2.0, band
    # detail rect: declared 1pt record rule, INSET (x 0.15 .. 7.48) --
    # replaces the invented gray hairline
    det = _rule_geom(rdl, "Rule_B_ROW_RULE")
    assert det, "the declared record rule must emit as a real rule"
    assert abs(det[0] - 0.15) < 0.02 and abs(det[1] - 7.33) < 0.02, det
    assert det[2] == 1.0, det
    assert det[0] > band[0], "the record rule is inset from the master rule"
    drect = re.search(r'<Rectangle Name="ND_Detail">.*?<ReportItems>',
                      rdl, re.S)
    assert drect and "0.25pt" not in drect.group(0), (
        "a declared record rule must suppress the invented hairline")
    # section-level closing rule -> group trailer row
    assert 'Rectangle Name="ND_GroupRule"' in rdl
    end = _rule_geom(rdl, "Rule_B_END_RULE")
    assert end and abs(end[0] - 0.014) < 0.02 and end[2] == 2.0, end


def test_no_rules_invented_without_declaration():
    rdl = _rdl(with_lines=False)
    band = re.search(r'<Rectangle Name="ND_Band">.*?</Rectangle>', rdl, re.S)
    assert band and "<TopBorder>" not in band.group(0)
    assert 'Rectangle Name="ND_GroupRule"' not in rdl
    # ...and no rule is invented in their place
    assert "<Line " not in rdl and "<Line>" not in rdl
    # STRICTER than the old "invented hairline stays as the default
    # separator" assertion, which this fixture's own declaration
    # disproves: with with_lines=False the record frame R_D carries NO
    # <visualSettings> at all, so it declares no box, and Oracle paints
    # nothing between records. The emitter used to write a hardcoded
    # <BottomBorder><Color>#777777</Color> here regardless -- measured on
    # real nested master-detail exports as a full-width stroke (rendered
    # gray 119) on frames that either declare no linePattern at all or
    # declare linePattern="solid" with hideLeft/Right/Top/BottomBorder
    # ="yes". So: not ONE of the four edges may appear, not merely the top.
    det = re.search(r'<Rectangle Name="ND_Detail">.*?<ReportItems>', rdl, re.S)
    assert det
    for _edge in ("Border", "TopBorder", "BottomBorder",
                  "LeftBorder", "RightBorder"):
        assert f"<{_edge}>" not in det.group(0), (
            f"an undeclared <{_edge}> was invented on the record "
            f"rectangle:\n{det.group(0)}")


def test_margin_title_not_reemitted_as_body_header():
    rdl = _rdl()
    root = ET.fromstring(rdl)
    ns = {"r": root.tag.split("}")[0].strip("{")}
    body = root.find(".//r:Body", ns)
    body_txt = ET.tostring(body, encoding="unicode")
    assert "Synthetic Master List" not in body_txt


# ---------------------------------------------------------------------------
# Three-level master -> record -> action shape (labeled per-record card, a
# record-scoped allPage action header, a declared band trailer label, and a
# declared header-section criteria cover).
# ---------------------------------------------------------------------------

def _md3_xml(hdr_depth="record", with_cover=True, band_decl=None,
             rec_fill=False):
    # DECLARED header-band styling variants (the fillPattern dialect):
    # "lightgray" = transparent-pattern frame with a declared paintable
    # background (labels keep their default black ink, declared italic);
    # "navy" = solid-pattern dark fill with declared white label ink.
    _hdr_vs = ""
    _hdr_ink = ""
    _hdr_em = ""
    if band_decl == "lightgray":
        _hdr_vs = ('<visualSettings fillPattern="transparent" '
                   'fillBackgroundColor="gray4"/>')
        _hdr_em = ' italic="yes"'
    elif band_decl == "navy":
        _hdr_vs = ('<visualSettings fillPattern="solid" '
                   'fillForegroundColor="darkblue"/>')
        _hdr_ink = ' textColor="white"'
    _rec_vs = ('<visualSettings fillPattern="transparent" '
               'fillBackgroundColor="gray16"/>') if rec_fill else ""
    cover = """
  <section name="header" orientation="portrait">
    <body>
      <text name="PB_HDR">
        <geometryInfo x="2.4" y="2.5" width="2.9" height="0.25"/>
        <textSegment><font face="Arial" size="12" bold="yes"/>
          <string><![CDATA[Report Parameters]]></string>
        </textSegment>
      </text>
      <text name="PB_L2">
        <geometryInfo x="2.0" y="2.85" width="1.4" height="0.18"/>
        <textSegment><font face="Arial" size="10" bold="yes"/>
          <string><![CDATA[Alpha Choice:]]></string>
        </textSegment>
      </text>
      <field name="PF_TWO" source="P_TWO">
        <font face="Arial" size="10" bold="yes"/>
        <geometryInfo x="3.6" y="2.85" width="3.1" height="0.19"/>
      </field>
      <text name="PB_L3">
        <geometryInfo x="2.0" y="3.09" width="1.4" height="0.18"/>
        <textSegment><font face="Arial" size="10" bold="yes"/>
          <string><![CDATA[Ref Number:]]></string>
        </textSegment>
      </text>
      <field name="PF_XID" source="P_XID">
        <font face="Arial" size="10" bold="yes"/>
        <geometryInfo x="3.6" y="3.09" width="3.1" height="0.19"/>
      </field>
      <text name="PB_L1">
        <geometryInfo x="2.0" y="3.33" width="1.4" height="0.18"/>
        <textSegment><font face="Arial" size="10" bold="yes"/>
          <string><![CDATA[Gamma Choice:]]></string>
        </textSegment>
      </text>
      <field name="PF_ONE" source="P_ONE">
        <font face="Arial" size="10" bold="yes"/>
        <geometryInfo x="3.6" y="3.33" width="3.1" height="0.19"/>
      </field>
    </body>
  </section>
""" if with_cover else ""
    act_hdr = f"""
                <frame name="M_ACT_HDR">
                  <geometryInfo x="0.0" y="0.9" width="7.9" height="0.19"/>
                  <advancedLayout printObjectOnPage="allPage"
                   basePrintingOn="enclosingObject"/>
                  {_hdr_vs}
                  <text name="B_AH1">
                    <geometryInfo x="0.06" y="0.9" width="2.1" height="0.19"/>
                    <textSegment><font face="Arial" size="10" bold="yes"{_hdr_em}{_hdr_ink}/>
                      <string><![CDATA[Kind:]]></string>
                    </textSegment>
                  </text>
                  <text name="B_AH2">
                    <geometryInfo x="2.5" y="0.9" width="1.7" height="0.19"/>
                    <textSegment><font face="Arial" size="10" bold="yes"{_hdr_em}{_hdr_ink}/>
                      <string><![CDATA[Notes:]]></string>
                    </textSegment>
                  </text>
                  <text name="B_AH3">
                    <geometryInfo x="6.7" y="0.9" width="1.1" height="0.19"/>
                    <textSegment><font face="Arial" size="10" bold="yes"{_hdr_em}{_hdr_ink}/>
                      <string><![CDATA[When:]]></string>
                    </textSegment>
                  </text>
                </frame>
"""
    record_children = f"""
          {act_hdr if hdr_depth == "record" else ""}
          <repeatingFrame name="R_A" source="G_ACT" printDirection="down">
            <geometryInfo x="0.0" y="1.1" width="7.9" height="0.19"/>
            <generalLayout verticalElasticity="expand"/>
            <field name="F_A" source="ACOL" alignment="start">
              <font face="Arial" size="10"/>
              <geometryInfo x="0.06" y="1.1" width="2.1" height="0.19"/>
            </field>
            <field name="F_AD" source="ADESC" alignment="start">
              <font face="Arial" size="10"/>
              <geometryInfo x="2.5" y="1.1" width="4.0" height="0.19"/>
            </field>
            <field name="F_AT" source="ADT" alignment="start">
              <font face="Arial" size="10"/>
              <geometryInfo x="6.7" y="1.1" width="1.1" height="0.19"/>
            </field>
          </repeatingFrame>
"""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<report name="ND_DECL3" DTDVersion="9.0.2.0.10">
  <data>
    <userParameter name="P_ONE" datatype="character" width="30"
     label="P One"/>
    <userParameter name="P_TWO" datatype="character" width="30"
     label="P Two"/>
    <userParameter name="P_XID" datatype="character" width="30"
     label="P Xid"/>
    <dataSource name="Q_MAIN">
      <select><![CDATA[SELECT MCOL, KCOL, REC_DT, ACOL, ADESC, ADT FROM T]]></select>
      <group name="G_MASTER">
        <dataItem name="MCOL" datatype="vchar2" width="30" columnFlags="1"
         defaultLabel="Mcol">
          <dataDescriptor expression="MCOL" order="1" width="30"/>
        </dataItem>
        <summary name="CountPerMaster" source="KCOL" function="count"
         width="20" reset="G_MASTER" defaultLabel="Count Per Master"/>
      </group>
      <group name="G_RECORD">
        <dataItem name="KCOL" datatype="vchar2" width="30" columnFlags="1"
         defaultLabel="Kcol">
          <dataDescriptor expression="KCOL" order="2" width="30"/>
        </dataItem>
        <dataItem name="REC_DT" datatype="date" width="20"
         defaultLabel="Rec Dt">
          <dataDescriptor expression="REC_DT" order="3" width="20"/>
        </dataItem>
      </group>
      <group name="G_ACT">
        <dataItem name="ACOL" datatype="vchar2" width="30" columnFlags="1"
         defaultLabel="Acol">
          <dataDescriptor expression="ACOL" order="4" width="30"/>
        </dataItem>
        <dataItem name="ADESC" datatype="vchar2" width="200"
         defaultLabel="Adesc">
          <dataDescriptor expression="ADESC" order="5" width="200"/>
        </dataItem>
        <dataItem name="ADT" datatype="date" width="20" defaultLabel="Adt">
          <dataDescriptor expression="ADT" order="6" width="20"/>
        </dataItem>
      </group>
    </dataSource>
  </data>
  <layout>
{cover}
  <section name="main">
    <body height="9.6">
      <repeatingFrame name="R_M" source="G_MASTER" printDirection="down">
        <geometryInfo x="0.0" y="0.02" width="7.9" height="1.5"/>
        <generalLayout verticalElasticity="variable"/>
        <text name="B_M">
          <geometryInfo x="0.0" y="0.02" width="0.6" height="0.17"/>
          <textSegment>
            <font face="Arial" size="11" bold="yes" italic="yes"
             textColor="yellow"/>
            <string><![CDATA[Zone:]]></string>
          </textSegment>
        </text>
        <field name="F_M" source="MCOL" alignment="start">
          <font face="Arial" size="11" bold="yes" italic="yes"
           textColor="yellow"/>
          <geometryInfo x="0.62" y="0.02" width="3.8" height="0.17"/>
        </field>
        <text name="B_SUM">
          <geometryInfo x="4.6" y="0.02" width="1.75" height="0.17"/>
          <textSegment>
            <font face="Arial" size="11" bold="yes" italic="yes"
             textColor="yellow"/>
            <string><![CDATA[Grand Tally:]]></string>
          </textSegment>
        </text>
        <field name="F_SUM" source="CountPerMaster" alignment="start">
          <font face="Arial" size="11" bold="yes" italic="yes"
           textColor="yellow"/>
          <geometryInfo x="6.35" y="0.02" width="1.5" height="0.17"/>
        </field>
        {act_hdr if hdr_depth == "outer" else ""}
        <repeatingFrame name="R_R" source="G_RECORD" printDirection="down">
          <geometryInfo x="0.0" y="0.25" width="7.9" height="1.3"/>
          <generalLayout verticalElasticity="variable"/>
          {_rec_vs}
          <text name="B_K">
            <geometryInfo x="0.06" y="0.25" width="1.0" height="0.19"/>
            <textSegment><font face="Arial" size="11" bold="yes"/>
              <string><![CDATA[Case ID:]]></string>
            </textSegment>
          </text>
          <field name="F_K" source="KCOL" alignment="start">
            <font face="Arial" size="11" bold="yes"/>
            <geometryInfo x="1.12" y="0.25" width="1.8" height="0.19"/>
          </field>
          <text name="B_RDT">
            <geometryInfo x="0.06" y="0.5" width="1.1" height="0.19"/>
            <textSegment><font face="Arial" size="11" bold="yes"/>
              <string><![CDATA[Opened Date:]]></string>
            </textSegment>
          </text>
          <field name="F_RDT" source="REC_DT" alignment="start">
            <font face="Arial" size="10"/>
            <geometryInfo x="1.25" y="0.5" width="1.6" height="0.19"/>
          </field>
          {record_children}
        </repeatingFrame>
      </repeatingFrame>
    </body>
  </section>
  </layout>
</report>
"""


def _rdl3(**kw):
    rep = parse_oracle_xml(_md3_xml(**kw).encode("utf-8"))
    rdl = generate_rdl(rep)
    ET.fromstring(rdl)
    return rdl


def test_declared_record_card_emits_frame_fields_and_labels():
    rdl = _rdl3()
    # the record frame's date field + BOTH declared labels survive (the
    # synthesized card dropped _DT fields and every label text)
    assert "Fields!REC_DT.Value" in rdl
    assert "Case ID:" in rdl and "Opened Date:" in rdl


def test_record_scoped_allpage_header_not_hoisted_to_page_top():
    rdl = _rdl3(hdr_depth="record")
    root = ET.fromstring(rdl)
    ns = {"r": root.tag.split("}")[0].strip("{")}
    tbx = next(t for t in root.iter(f"{{{ns['r']}}}Tablix")
               if t.get("Name") == "Tablix_Nested")
    rows = tbx.findall(f".//{{{ns['r']}}}TablixRow")
    first_row = ET.tostring(rows[0], encoding="unicode")
    # band first; the action header repeats per record, not at page top
    assert "ND_Band" in first_row and "ND_ColHdr" not in first_row
    assert "Kind:" in rdl  # the header row itself still emits


def test_outer_allpage_header_still_hoists():
    # negative twin: the same header declared at the OUTER frame level is
    # genuine page chrome and keeps the page-top hoist
    rdl = _rdl3(hdr_depth="outer")
    root = ET.fromstring(rdl)
    ns = {"r": root.tag.split("}")[0].strip("{")}
    tbx = next(t for t in root.iter(f"{{{ns['r']}}}Tablix")
               if t.get("Name") == "Tablix_Nested")
    rows = tbx.findall(f".//{{{ns['r']}}}TablixRow")
    assert "ND_ColHdr" in ET.tostring(rows[0], encoding="unicode")


def test_band_trailer_uses_declared_label_wording():
    rdl = _rdl3()
    assert "Grand Tally:" in rdl
    assert "Count Per Master" not in rdl  # the data-model defaultLabel


def _tb_geom(rdl, name):
    """(left, top, width, height) of a named Textbox, or None."""
    m = re.search(r'<Textbox Name="%s">.*?</Textbox>' % name, rdl, re.S)
    if not m:
        return None
    b = m.group(0)

    def _n(tag):
        g = re.search(r"<%s>([\d.]+)in</%s>" % (tag, tag), b)
        return float(g.group(1)) if g else None
    return _n("Left"), _n("Top"), _n("Width"), _n("Height")


def test_band_caption_value_and_trailer_keep_declared_geometry():
    """A group band's caption, its value, the trailer label and the trailer
    total are FOUR declared layout objects at four declared x positions.

    Merging caption+value (and label+total) into single stretched textboxes
    pinned each value immediately after its caption glyphs — truth-measured
    on a detail report, the total landed ~1.1in right of its declared column
    — and the band row itself was a flat 0.34in stand-in against a declared
    extent of 0.17in (~45% tall in the engine render). Everything here comes
    from the declaration: the row is the declared extent + the same 0.06in
    slack the declared record-card row uses, and every box keeps its own
    declared Left/Width."""
    rdl = _rdl3()
    lbl = _tb_geom(rdl, "Tb_ND_BandL")
    val = _tb_geom(rdl, "Tb_ND_BandV")
    tlbl = _tb_geom(rdl, "Tb_ND_BandTotalL")
    tval = _tb_geom(rdl, "Tb_ND_BandTotal")
    assert lbl and val and tlbl and tval, (
        "band caption, value, trailer label and trailer total each need "
        "their own textbox")
    # declared x: 0.0 / 0.62 / 4.6 / 6.35   (widths 0.6 / 3.8 / 1.75 / 1.5)
    for got, x, w in ((lbl, 0.0, 0.6), (val, 0.62, 3.8),
                      (tlbl, 4.6, 1.75), (tval, 6.35, 1.5)):
        assert abs(got[0] - x) < 0.005, (got, x)
        assert abs(got[2] - w) < 0.005, (got, w)
    # the trailer total prints the aggregate ALONE (its label is its own box)
    tot = re.search(r'<Textbox Name="Tb_ND_BandTotal">.*?</Textbox>',
                    rdl, re.S).group(0)
    assert "Grand Tally" not in tot, (
        "the trailer label must be its own declared box, not a prefix run")
    assert "Count(Fields!" in tot
    # declared band row height = declared extent (0.17in) + 0.06in slack
    row = re.search(r"<TablixRow>\s*<Height>([\d.]+)in</Height>"
                    r"(?:(?!</TablixRow>).)*?ND_Band", rdl, re.S)
    assert row, "band row not found"
    assert abs(float(row.group(1)) - 0.23) < 0.005, row.group(1)
    # declared 11pt survives on every band box
    for nm in ("Tb_ND_BandL", "Tb_ND_BandV", "Tb_ND_BandTotalL",
               "Tb_ND_BandTotal"):
        blk = re.search(r'<Textbox Name="%s">.*?</Textbox>' % nm,
                        rdl, re.S).group(0)
        assert "<FontSize>11pt</FontSize>" in blk, nm


def test_cover_uses_declared_criteria_labels_in_declared_order():
    rdl = _rdl3()
    a = rdl.find("Alpha Choice:")
    x = rdl.find("Ref Number:")
    g = rdl.find("Gamma Choice:")
    assert a != -1 and x != -1 and g != -1
    assert a < x < g  # declared y-order, not parameter declaration order
    # an ID-suffixed parameter the declared form prints must NOT be dropped
    assert "Parameters!P_XID.Value" in rdl


def test_cover_fallback_keeps_humanized_labels_without_declared_form():
    rdl = _rdl3(with_cover=False)
    assert "Alpha Choice:" not in rdl


def test_cover_gets_its_own_page_when_body_follows():
    rdl = _rdl3()
    root = ET.fromstring(rdl)
    ns = {"r": root.tag.split("}")[0].strip("{")}
    grp = next((g for g in root.iter(f"{{{ns['r']}}}Group")
                if g.get("Name") == "PageSep_Cover"), None)
    assert grp is not None
    pb = grp.find(f"{{{ns['r']}}}PageBreak/{{{ns['r']}}}BreakLocation")
    assert pb is not None and pb.text == "Start"


def test_no_cover_break_without_a_cover():
    rdl = _rdl3(with_cover=False)
    assert "PageSep_Cover" not in rdl


# ---------------------------------------------------------------------------
# Declaration-driven column-header band + record-card fills (the
# fillPattern dialect): the frame OWNING the header labels paints the
# band with its pattern-gated fill and the labels print their declared
# ink/em; the record card paints its frame's declared fill. No
# declaration -> plain band / white card, never an invented navy.
# ---------------------------------------------------------------------------

def _colhdr_block(rdl):
    m = re.search(r'<Rectangle Name="ND_ColHdr">.*?</Rectangle>', rdl, re.S)
    assert m, "ND_ColHdr rectangle missing"
    return m.group(0)


def test_header_band_uses_declared_frame_fill_and_black_ink():
    rdl = _rdl3(band_decl="lightgray")
    hdr = _colhdr_block(rdl)
    # transparent-pattern frame with declared background paints THAT fill
    assert "<BackgroundColor>#F4F4F4</BackgroundColor>" in hdr
    # never the invented navy
    assert "#00008B" not in hdr and "#000080" not in hdr
    lbl = re.search(r'Textbox Name="Tb_NDHdr_0".*?</Textbox>', rdl, re.S)
    assert lbl and "<Color>#000000</Color>" in lbl.group(0)
    # DECLARED em carries through -- EXCEPT the slant, which the Oracle
    # export dialect never paints. TRUTH (2026-08-08, whole truth corpus):
    # 16 Oracle-driver PDFs / 142,831 non-blank spans carry ZERO italic-
    # flagged spans and reference no *-Oblique font resource, while 32,604
    # spans are bold -- weight is honoured, slant is dropped. Every
    # declared-italic object locatable in a truth PDF (16 of them, e.g. an
    # italic+bold total caption) prints upright Helvetica / Helvetica-Bold.
    # STRICTER than the old "italic carries through": the rest of the
    # declared em must still carry AND no slant may appear ANYWHERE in the
    # document, not merely in this one textbox.
    assert "<FontWeight>Bold</FontWeight>" in lbl.group(0)
    assert "<FontSize>10pt</FontSize>" in lbl.group(0)
    assert "<FontStyle>Italic</FontStyle>" not in rdl
    assert "Italic" not in rdl


def test_header_band_keeps_declared_navy_and_white_ink():
    # negative twin: a solid dark declared fill keeps the dark band and
    # its declared white label ink (band styling is declaration-driven)
    rdl = _rdl3(band_decl="navy")
    hdr = _colhdr_block(rdl)
    assert "<BackgroundColor>#000080</BackgroundColor>" in hdr
    lbl = re.search(r'Textbox Name="Tb_NDHdr_0".*?</Textbox>', rdl, re.S)
    assert lbl and "<Color>#FFFFFF</Color>" in lbl.group(0)


def test_record_card_paints_declared_frame_fill():
    rdl = _rdl3(rec_fill=True)
    m = re.search(r'<Rectangle Name="ND_Card0">.*?<ReportItems>', rdl, re.S)
    assert m, "ND_Card0 rectangle missing"
    assert "<BackgroundColor>#D6D6D6</BackgroundColor>" in m.group(0)


def test_record_card_stays_white_without_declared_fill():
    rdl = _rdl3()
    m = re.search(r'<Rectangle Name="ND_Card0">.*?<ReportItems>', rdl, re.S)
    assert m, "ND_Card0 rectangle missing"
    assert "<BackgroundColor>#ffffff</BackgroundColor>" in m.group(0)
