"""
Item guards: trigger-body summary references, right-anchored width
preservation, static-note growth, and declared trailer wording.

* A format trigger gating on a report-scoped <summary> (:CS_X < 2) now
  translates — the summary resolves to its scoped aggregate
  re-implementation. Group-scoped summaries still decline (strictness).
* A right-justified box in a container clamped below its declared span
  keeps its declared WIDTH and slides left (Oracle anchors the right
  edge); left-aligned boxes keep the clamp.
* A static prose text whose AFM-estimated height exceeds its declared box
  gets CanGrow; fitting texts keep their fixed box.
* The report-level totals trailer pairs the source's declared literal
  labels verbatim (no invented colon) and prints IN FLOW below the last
  body item, not below the whole declared body height.
"""
import re
import xml.etree.ElementTree as ET

import pytest

from converter.models import LayoutField
from converter.generators import rdl as R
from converter.generators.rdl import generate_rdl
from converter.parsers.oracle_xml import parse_oracle_xml


# ---------------------------------------------------------------------------
# Trigger-body summary resolution
# ---------------------------------------------------------------------------

def _summary_report_xml(reset="report"):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<report name="TRIGSUM" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q1">
      <select><![CDATA[SELECT SRC, VAL FROM T]]></select>
      <group name="G_A">
        <dataItem name="SRC" datatype="vchar2" width="30" columnFlags="1"
         defaultLabel="Src">
          <dataDescriptor expression="SRC" order="1" width="30"/>
        </dataItem>
        <dataItem name="VAL" oracleDatatype="number" width="22"
         defaultLabel="Val">
          <dataDescriptor expression="VAL" order="2" width="22"/>
        </dataItem>
      </group>
    </dataSource>
    <summary name="CS_CNT" source="SRC" function="count" width="20"
     reset="{reset}" compute="report" columnFlags="8"/>
  </data>
  <layout>
  <section name="main">
    <body height="9.6">
      <repeatingFrame name="R_A" source="G_A" printDirection="down">
        <geometryInfo x="0.0" y="0.0" width="7.5" height="0.4"/>
        <field name="F_S" source="SRC">
          <font face="Arial" size="10"/>
          <geometryInfo x="0.1" y="0.1" width="3.0" height="0.19"/>
        </field>
      </repeatingFrame>
    </body>
  </section>
  </layout>
  <programUnits>
    <function name="f_gate_ft">
      <textSource><![CDATA[FUNCTION F_Gate_FT
RETURN BOOLEAN IS
BEGIN
  IF :CS_CNT < 2 THEN
    RETURN(TRUE) ;
  ELSE
    RETURN(FALSE) ;
  END IF ;
END ;]]></textSource>
    </function>
  </programUnits>
</report>
"""


def test_trigger_resolver_resolves_report_scoped_summary():
    rep = parse_oracle_xml(_summary_report_xml().encode("utf-8"))
    resolve = R._strict_trigger_resolve(rep)
    assert resolve("CS_CNT") == 'Count(Fields!SRC.Value, "Q1")'
    m = R._format_trigger_hidden_map(rep)
    assert "f_gate_ft" in m
    assert 'Count(Fields!SRC.Value, "Q1")' in m["f_gate_ft"]


def test_trigger_resolver_declines_group_scoped_summary():
    rep = parse_oracle_xml(_summary_report_xml(reset="G_A").encode("utf-8"))
    resolve = R._strict_trigger_resolve(rep)
    with pytest.raises(KeyError):
        resolve("CS_CNT")
    assert "f_gate_ft" not in R._format_trigger_hidden_map(rep)


# ---------------------------------------------------------------------------
# Right-anchored width preservation (_emit_field_textbox)
# ---------------------------------------------------------------------------

def _emit_one(align):
    rep = parse_oracle_xml(_summary_report_xml().encode("utf-8"))
    lf = LayoutField(name="B_T", kind="text", text="WIDE TITLE",
                     align=align, x=6.4, y=0.0, width=1.35, height=0.38,
                     font_size=22, bold=True)
    parent = ET.Element(R._q("ReportItems"))
    ok, _ = R._emit_field_textbox(parent, "TB_X", "", lf, 0.0, 0.0,
                                  7.57, 11.0, rep, [])
    assert ok
    x = ET.tostring(parent, encoding="unicode")
    left = float(re.search(r"<Left>([\d.]+)in</Left>", x).group(1))
    width = float(re.search(r"<Width>([\d.]+)in</Width>", x).group(1))
    return left, width


def test_right_aligned_box_keeps_declared_width():
    left, width = _emit_one("end")
    assert width >= 1.34            # declared span kept
    assert left < 6.4               # slid left to fit


def test_left_aligned_box_keeps_position_and_clamps():
    left, width = _emit_one("start")
    assert abs(left - 6.4) < 0.01
    assert width < 1.30             # clamped as before


# ---------------------------------------------------------------------------
# Static prose growth estimate
# ---------------------------------------------------------------------------

def test_long_static_note_overflowing_box_grows():
    long_txt = ("Billable amounts are computed from the recorded totals "
                "minus the base allowance. The first portion is included "
                "in the maintenance component. When both categories apply "
                "an applicable percent is credited to each category. "
                "Assessment details and the payment voucher are attached "
                "for reference and must accompany any remittance sent.")
    lf = LayoutField(name="B_N", kind="text", text=long_txt,
                     x=0.05, y=10.2, width=7.6, height=0.35, font_size=8)
    assert R._static_text_overflows_box(lf) is True


def test_fitting_static_text_keeps_fixed_box():
    lf = LayoutField(name="B_N", kind="text",
                     text="A short single line note that easily fits here.",
                     x=0.05, y=10.2, width=7.6, height=0.35, font_size=8)
    assert R._static_text_overflows_box(lf) is False


# ---------------------------------------------------------------------------
# Trailer declared wording + in-flow placement
# ---------------------------------------------------------------------------

def _trailer_xml(with_label=True):
    label = """
        <text name="B_REPORT_T">
          <textSettings justify="end" spacing="single"/>
          <geometryInfo x="5.2" y="2.2" width="1.3" height="0.19"/>
          <textSegment><font face="Arial" size="10"/>
            <string><![CDATA[- Total Things]]></string>
          </textSegment>
        </text>
    """ if with_label else ""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<report name="TRAILER" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q1">
      <select><![CDATA[SELECT MCOL, A, N FROM T]]></select>
      <group name="G_M">
        <dataItem name="MCOL" datatype="vchar2" width="30" columnFlags="1"
         defaultLabel="Mcol">
          <dataDescriptor expression="MCOL" order="1" width="30"/>
        </dataItem>
      </group>
      <group name="G_D">
        <dataItem name="A" datatype="vchar2" width="30" columnFlags="1"
         defaultLabel="A">
          <dataDescriptor expression="A" order="2" width="30"/>
        </dataItem>
        <dataItem name="N" oracleDatatype="number" width="22"
         defaultLabel="N">
          <dataDescriptor expression="N" order="3" width="22"/>
        </dataItem>
      </group>
    </dataSource>
    <summary name="CS_T" source="A" function="count" width="20"
     reset="report" compute="report" columnFlags="8"/>
  </data>
  <layout>
  <section name="main">
    <body height="9.6">
      <repeatingFrame name="R_M" source="G_M" printDirection="down">
        <geometryInfo x="0.0" y="0.0" width="7.5" height="1.6"/>
        <field name="F_M" source="MCOL">
          <font face="Arial" size="10" bold="yes"/>
          <geometryInfo x="0.05" y="0.05" width="3.0" height="0.19"/>
        </field>
        <repeatingFrame name="R_D" source="G_D" printDirection="down">
          <geometryInfo x="0.1" y="0.3" width="7.3" height="0.3"/>
          <field name="F_A" source="A">
            <font face="Arial" size="10"/>
            <geometryInfo x="0.1" y="0.35" width="3.0" height="0.19"/>
          </field>
          <field name="F_N" source="N">
            <font face="Arial" size="10"/>
            <geometryInfo x="4.0" y="0.35" width="1.0" height="0.19"/>
          </field>
        </repeatingFrame>
      </repeatingFrame>
      <frame name="M_TRAIL">
        <geometryInfo x="4.4" y="2.2" width="3.1" height="0.2"/>
        {label}
        <field name="F_CS_T" source="CS_T" alignment="end">
          <font face="Arial" size="10" bold="yes"/>
          <geometryInfo x="6.6" y="2.2" width="0.9" height="0.19"/>
        </field>
      </frame>
    </body>
  </section>
  </layout>
</report>
"""


def test_trailer_pairs_declared_literal_label_verbatim():
    rep = parse_oracle_xml(_trailer_xml().encode("utf-8"))
    rdl = generate_rdl(rep)
    m = re.search(r'<Textbox Name="Tb_GrandTotal_0">.*?<Value>([^<]*)</Value>',
                  rdl, re.S)
    assert m, "grand total missing"
    # Declared wording verbatim: no invented colon, declared dash kept.
    # A line whose declaration ANCHORS its boxes (justify/alignment=end) now
    # prints as the declared caption box + value box pair, so the caption box
    # carries the wording alone; an unanchored line keeps the fused
    # "<caption>  <value>" single box. Either shape must show the wording
    # exactly as declared.
    assert re.match(r'^="- Total Things\s*"', m.group(1)), m.group(1)


def test_trailer_synthesized_without_declared_label():
    rep = parse_oracle_xml(_trailer_xml(with_label=False).encode("utf-8"))
    rdl = generate_rdl(rep)
    m = re.search(r'<Textbox Name="Tb_GrandTotal_0">.*?<Value>([^<]*)</Value>',
                  rdl, re.S)
    assert m
    assert m.group(1).startswith('="T:')  # name-derived fallback keeps colon


def _trailer_full_xml():
    """The full declared-trailer shape: a standalone all-caps band literal
    (with a &summary token), an FY-range line whose left-hand wording is a
    FORMULA field over min/max summaries, a literal-labeled base total,
    and a total whose source is a FORMULA column — all in one trailer
    frame, declared in y-order."""
    return """<?xml version="1.0" encoding="UTF-8"?>
<report name="TRAILER3" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q1">
      <select><![CDATA[SELECT MCOL, A, N, N2, YR FROM T]]></select>
      <group name="G_M">
        <dataItem name="MCOL" datatype="vchar2" width="30" columnFlags="1"
         defaultLabel="Mcol">
          <dataDescriptor expression="MCOL" order="1" width="30"/>
        </dataItem>
      </group>
      <group name="G_D">
        <dataItem name="A" datatype="vchar2" width="30" columnFlags="1"
         defaultLabel="A">
          <dataDescriptor expression="A" order="2" width="30"/>
        </dataItem>
        <dataItem name="N" oracleDatatype="number" width="22"
         defaultLabel="N">
          <dataDescriptor expression="N" order="3" width="22"/>
        </dataItem>
        <dataItem name="N2" oracleDatatype="number" width="22"
         defaultLabel="N2">
          <dataDescriptor expression="N2" order="4" width="22"/>
        </dataItem>
        <dataItem name="YR" oracleDatatype="number" width="22"
         defaultLabel="Yr">
          <dataDescriptor expression="YR" order="5" width="22"/>
        </dataItem>
      </group>
      <formula name="CF_NET" source="cf_net_f" datatype="number"
       width="20" columnFlags="16" defaultLabel="Cf Net"/>
      <formula name="CF_RANGE" source="cf_range_f" datatype="character"
       width="20" columnFlags="16" defaultLabel="Cf Range"/>
    </dataSource>
    <summary name="CS_T" source="N" function="sum" width="20"
     reset="report" compute="report" columnFlags="8"/>
    <summary name="CS_NET" source="CF_NET" function="sum" width="20"
     reset="report" compute="report" columnFlags="8"/>
    <summary name="CS_YMIN" source="YR" function="minimum" width="20"
     reset="report" compute="report" columnFlags="8"/>
    <summary name="CS_YMAX" source="YR" function="maximum" width="20"
     reset="report" compute="report" columnFlags="8"/>
    <summary name="CS_KIND" source="A" function="first" width="30"
     reset="report" compute="report" columnFlags="8"/>
  </data>
  <layout>
  <section name="main">
    <body height="9.6">
      <repeatingFrame name="R_M" source="G_M" printDirection="down">
        <geometryInfo x="0.0" y="0.0" width="7.5" height="1.6"/>
        <field name="F_M" source="MCOL">
          <font face="Arial" size="10" bold="yes"/>
          <geometryInfo x="0.05" y="0.05" width="3.0" height="0.19"/>
        </field>
        <repeatingFrame name="R_D" source="G_D" printDirection="down">
          <geometryInfo x="0.1" y="0.3" width="7.3" height="0.3"/>
          <field name="F_A" source="A">
            <font face="Arial" size="10"/>
            <geometryInfo x="0.1" y="0.35" width="3.0" height="0.19"/>
          </field>
          <field name="F_N" source="N">
            <font face="Arial" size="10"/>
            <geometryInfo x="4.0" y="0.35" width="1.0" height="0.19"/>
          </field>
        </repeatingFrame>
      </repeatingFrame>
      <frame name="M_TRAIL">
        <geometryInfo x="0.0" y="1.95" width="7.5" height="0.85"/>
        <text name="B_BAND">
          <textSettings spacing="single"/>
          <geometryInfo x="0.0" y="1.95" width="5.0" height="0.19"/>
          <textSegment><font face="Arial" size="10" bold="yes"/>
            <string><![CDATA[ALL THINGS COMBINED - &CS_KIND]]></string>
          </textSegment>
        </text>
        <field name="F_CF_RANGE" source="CF_RANGE">
          <font face="Arial" size="10"/>
          <geometryInfo x="5.2" y="2.0" width="1.2" height="0.19"/>
        </field>
        <field name="F_CS_T" source="CS_T" alignment="end">
          <font face="Arial" size="10" bold="yes"/>
          <geometryInfo x="6.6" y="2.0" width="0.9" height="0.19"/>
        </field>
        <text name="B_NET">
          <textSettings justify="end" spacing="single"/>
          <geometryInfo x="5.2" y="2.4" width="1.3" height="0.19"/>
          <textSegment><font face="Arial" size="10"/>
            <string><![CDATA[= Net Things]]></string>
          </textSegment>
        </text>
        <field name="F_CS_NET" source="CS_NET" alignment="end">
          <font face="Arial" size="10" bold="yes"/>
          <geometryInfo x="6.6" y="2.4" width="0.9" height="0.19"/>
        </field>
      </frame>
    </body>
  </section>
  </layout>
  <programUnits>
    <function name="cf_net_f"><textSource><![CDATA[
FUNCTION CF_NET_F RETURN NUMBER IS BEGIN
  RETURN(:N - :N2);
END;
]]></textSource></function>
    <function name="cf_range_f"><textSource><![CDATA[
FUNCTION CF_RANGE_F RETURN VARCHAR2 IS BEGIN
  IF :CS_YMIN = :CS_YMAX THEN
    RETURN('FY' || :CS_YMIN);
  ELSE
    RETURN('FY' || :CS_YMIN || ' - FY' || :CS_YMAX);
  END IF;
END;
]]></textSource></function>
  </programUnits>
</report>
"""


def _grand_totals(rdl):
    return [m.group(1) for m in re.finditer(
        r'<Textbox Name="Tb_GrandTotal_\d+">.*?<Value>([^<]*)</Value>',
        rdl, re.S)]


def test_trailer_formula_source_total_emits_inline_aggregate():
    # CS_NET sums a FORMULA column: the grand total compiles the formula
    # inline and aggregates it (the summary vanished before)
    rep = parse_oracle_xml(_trailer_full_xml().encode("utf-8"))
    rdl = generate_rdl(rep)
    vals = _grand_totals(rdl)
    i = next((k for k, v in enumerate(vals)
              if re.match(r'^="= Net Things\s*"', v)), None)
    assert i is not None, "formula-source total line missing"
    # the aggregate prints on that LINE — fused into the caption's box, or in
    # the value box the declaration anchors beside it
    net = " ".join(vals[i:i + 2])
    assert "Sum(" in net and "Fields!N2.Value" in net and '"Q1"' in net


def test_trailer_formula_field_is_the_line_wording():
    # the FY-range line's left-hand "label" is a FORMULA field over
    # min/max summaries: its compiled expression IS the wording (not the
    # synthesized "T:  " name-derived label), with the full-word
    # minimum/maximum functions mapped to Min/Max (not the Sum fallback)
    rep = parse_oracle_xml(_trailer_full_xml().encode("utf-8"))
    rdl = generate_rdl(rep)
    fy = next((v for v in _grand_totals(rdl) if "IIf" in v), None)
    assert fy, "formula-field-labelled line missing"
    assert 'Min(Fields!YR.Value, "Q1")' in fy
    assert 'Max(Fields!YR.Value, "Q1")' in fy
    assert "Sum(Fields!YR.Value" not in fy


def test_trailer_standalone_literal_emits_in_declared_y_order():
    # the all-caps band caption pairs with NO summary — it still prints,
    # as its own line, and all trailer lines keep declared y-order
    rep = parse_oracle_xml(_trailer_full_xml().encode("utf-8"))
    rdl = generate_rdl(rep)
    vals = _grand_totals(rdl)
    band_i = next((i for i, v in enumerate(vals)
                   if v.startswith('="ALL THINGS COMBINED - "')), None)
    assert band_i is not None, "standalone trailer literal missing"
    # the &summary token resolved body-scope-safe
    assert "First(Fields!A.Value" in vals[band_i]
    fy_i = next(i for i, v in enumerate(vals) if "IIf" in v)
    net_i = next(i for i, v in enumerate(vals)
                 if v.startswith('="= Net Things'))
    # declared ys: band 1.95 < range line 2.0 < net line 2.4
    assert band_i < fy_i < net_i


def test_trailer_prints_in_flow_below_last_item():
    rep = parse_oracle_xml(_trailer_xml().encode("utf-8"))
    rdl = generate_rdl(rep)
    root = ET.fromstring(rdl)
    ns = root.tag.split("}")[0].strip("{")
    q = lambda t: f"{{{ns}}}{t}"
    body = root.find(q("Body"))
    ri = body.find(q("ReportItems"))
    tops = {}
    bottoms = []
    for it in ri:
        nm = it.get("Name") or ""
        try:
            top = float((it.findtext(q("Top")) or "0").replace("in", ""))
            h = float((it.findtext(q("Height")) or "0").replace("in", ""))
        except ValueError:
            continue
        if nm.startswith("Tb_GrandTotal"):
            tops[nm] = top
        else:
            bottoms.append(top + h)
    assert tops, "no grand totals emitted"
    # in flow: within 1in of the last real content bottom, NOT parked below
    # the whole declared 9.6in body
    assert min(tops.values()) <= max(bottoms) + 1.0
