"""The report-end trailer must print at its DECLARED horizontal geometry.

Oracle's report-end trailer idiom stacks label/value PAIRS in the right half of
the band: a caption box justified ``end`` and, beside it, the summary field's
own box aligned ``end``. The generator used to fold both halves into ONE
left-anchored 5.0in stack box at x=0.10in, so every such line printed flush
left -- the declared justification had no box left to justify against.

Each line now prints at the boxes the source declares: the caption at its own
x/width with its own justification, the value at its own x/width right-anchored
beside it, both on one row. Lines the declaration does NOT anchor (no explicit
end/centre justification) keep the stacked flush-left fallback, and the
declared y-ORDER of the stack is untouched.
"""
from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter.generators.rdl import generate_rdl, _q  # noqa: E402
from converter.parsers.oracle_xml import parse_oracle_xml  # noqa: E402


def _xml(anchor_pairs: bool = True) -> bytes:
    """A report whose trailer band declares a standalone caption plus two
    label/value pairs in the right half. ``anchor_pairs=False`` strips the
    declared justifications (nothing anchors the lines then)."""
    lj = ' justify="end"' if anchor_pairs else ""
    fa = ' alignment="end"' if anchor_pairs else ""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<report name="UNIT_LOG" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q_1">
      <select><![CDATA[SELECT REGION, UNITS, SPARES, CARRY FROM UNIT_LOG]]></select>
      <group name="G_1">
        <dataItem name="REGION" datatype="vchar2" width="30"
         defaultLabel="Region">
          <dataDescriptor expression="REGION" order="1" width="30"/>
        </dataItem>
        <dataItem name="UNITS" oracleDatatype="number" width="22"
         defaultLabel="Units">
          <dataDescriptor expression="UNITS" order="2" width="22"/>
        </dataItem>
        <dataItem name="SPARES" oracleDatatype="number" width="22"
         defaultLabel="Spares">
          <dataDescriptor expression="SPARES" order="3" width="22"/>
        </dataItem>
        <dataItem name="CARRY" oracleDatatype="number" width="22"
         defaultLabel="Carry">
          <dataDescriptor expression="CARRY" order="4" width="22"/>
        </dataItem>
      </group>
      <formula name="CF_NET" source="cf_net_f" datatype="number" width="20"
       columnFlags="16" defaultLabel="Cf Net"/>
    </dataSource>
    <summary name="CS_ALL_SPARES" source="SPARES" function="sum" width="20"
     reset="report" compute="report" columnFlags="8"/>
    <summary name="CS_ALL_NET" source="CF_NET" function="sum" width="20"
     reset="report" compute="report" columnFlags="8"/>
  </data>
  <layout>
  <section name="main">
    <body height="9.5">
      <repeatingFrame name="R_1" source="G_1" printDirection="down">
        <geometryInfo x="0.0" y="0.0" width="7.5" height="0.3"/>
        <field name="F_REGION" source="REGION">
          <font face="Arial" size="10"/>
          <geometryInfo x="0.1" y="0.05" width="3.0" height="0.19"/>
        </field>
        <field name="F_UNITS" source="UNITS">
          <font face="Arial" size="10"/>
          <geometryInfo x="4.0" y="0.05" width="1.0" height="0.19"/>
        </field>
      </repeatingFrame>
      <frame name="M_TRAILER">
        <geometryInfo x="0.0" y="1.90" width="7.5" height="0.9"/>
        <text name="B_BAND">
          <textSettings spacing="single"/>
          <geometryInfo x="0.0" y="1.95" width="5.0" height="0.19"/>
          <textSegment><font face="Arial" size="10" bold="yes"/>
            <string><![CDATA[ALL REGIONS COMBINED]]></string>
          </textSegment>
        </text>
        <text name="B_SPARES_LBL">
          <textSettings{lj} spacing="single"/>
          <geometryInfo x="4.30" y="2.20" width="2.15" height="0.19"/>
          <textSegment><font face="Arial" size="10"/>
            <string><![CDATA[= Total Spares]]></string>
          </textSegment>
        </text>
        <field name="F_ALL_SPARES" source="CS_ALL_SPARES"
         formatMask="NNN,NN0"{fa}>
          <font face="Arial" size="10" bold="yes"/>
          <geometryInfo x="6.60" y="2.20" width="0.90" height="0.19"/>
        </field>
        <text name="B_NET_LBL">
          <textSettings{lj} spacing="single"/>
          <geometryInfo x="5.25" y="2.45" width="1.25" height="0.19"/>
          <textSegment><font face="Arial" size="10"/>
            <string><![CDATA[- Total Carried]]></string>
          </textSegment>
        </text>
        <field name="F_ALL_NET" source="CS_ALL_NET" formatMask="NNN,NN0"{fa}>
          <font face="Arial" size="10" bold="yes"/>
          <geometryInfo x="6.60" y="2.45" width="0.90" height="0.19"/>
        </field>
      </frame>
    </body>
  </section>
  </layout>
  <programUnits>
    <function name="cf_net_f"><textSource><![CDATA[
FUNCTION CF_NET_F RETURN NUMBER IS BEGIN
  RETURN(:UNITS - :CARRY);
END;
]]></textSource></function>
  </programUnits>
</report>
""".encode("utf-8")


def _boxes(rdl):
    """[(name, value, top, left, width, align, format)] in emitted order."""
    root = ET.fromstring(rdl)
    out = []
    for tb in root.iter(_q("Textbox")):
        nm = tb.get("Name") or ""
        if not re.fullmatch(r"Tb_GrandTotal_\d+", nm):
            continue

        def _f(tag, el=tb):
            try:
                return float((el.findtext(_q(tag)) or "0").replace("in", ""))
            except ValueError:
                return 0.0
        val = next((v.text or "" for v in tb.iter(_q("Value"))), "")
        align = next((a.text or "" for a in tb.iter(_q("TextAlign"))), "")
        fmt = next((f.text or "" for f in tb.iter(_q("Format"))), "")
        out.append((nm, val, _f("Top"), _f("Left"), _f("Width"), align, fmt))
    return out


def _line_with(boxes, wording):
    return [b for b in boxes if wording in b[1]]


def test_declared_pair_prints_at_its_declared_x_width_and_alignment():
    boxes = _boxes(generate_rdl(parse_oracle_xml(_xml())))
    assert boxes, "no report-end trailer emitted"
    lbl = _line_with(boxes, "= Total Spares")
    assert lbl, "declared trailer caption never reached the trailer stack"
    lbl = lbl[0]
    assert abs(lbl[3] - 4.30) < 0.02, \
        f"caption left is {lbl[3]}in, declared 4.30"
    assert abs(lbl[4] - 2.15) < 0.02, \
        f"caption width is {lbl[4]}in, declared 2.15"
    assert lbl[5] == "Right", f"declared justify=end dropped ({lbl[5]!r})"
    # the value half prints in its OWN declared box, right-anchored beside it
    val = [b for b in boxes
           if b[2] == lbl[2] and 'Sum(Fields!SPARES.Value' in b[1]]
    assert val, "the summary value never got its own declared box"
    val = val[0]
    assert abs(val[3] - 6.60) < 0.02, f"value left is {val[3]}in, declared 6.60"
    assert abs(val[4] - 0.90) < 0.02, f"value width is {val[4]}in, declared 0.90"
    assert val[5] == "Right", f"declared alignment=end dropped ({val[5]!r})"
    # the pair shares one row and its halves never overlap
    assert lbl[3] + lbl[4] <= val[3] + 1e-6


def test_declared_value_box_keeps_its_format_mask():
    """Standing alone the value is no longer inside a concatenation, so the
    inline-mask post-pass cannot reach it -- the declared formatMask has to
    ride on the textbox or the total prints its digits unseparated.

    Checked on the total whose source is a FORMULA column: its expression is a
    compiled aggregate, not a pure field reference, so the name-keyed <Format>
    post-pass cannot match it either."""
    boxes = _boxes(generate_rdl(parse_oracle_xml(_xml())))
    val = [b for b in boxes
           if b[1].startswith("=Sum(") and "Fields!CARRY.Value" in b[1]]
    assert val, "the formula-source total never emitted"
    assert val[0][6] == "###,##0", f"declared mask dropped ({val[0][6]!r})"


def test_declared_y_order_survives_the_pair_split():
    boxes = _boxes(generate_rdl(parse_oracle_xml(_xml())))
    band = _line_with(boxes, "ALL REGIONS COMBINED")
    spares = _line_with(boxes, "= Total Spares")
    carried = _line_with(boxes, "- Total Carried")
    assert band and spares and carried
    # declared ys: band 1.95 < spares line 2.20 < carried line 2.45
    assert band[0][2] < spares[0][2] < carried[0][2]
    # one row per declared line, and rows never collide
    tops = sorted({b[2] for b in boxes})
    assert len(tops) == 3, f"expected one row per declared line, got {tops}"


def test_unanchored_line_keeps_the_stacked_fallback():
    """No explicit justification declared = Oracle's own flush-left default,
    which the stacked fallback already reproduces. Re-anchoring those lines
    would be an invention, so they must stay put."""
    boxes = _boxes(generate_rdl(parse_oracle_xml(_xml(anchor_pairs=False))))
    spares = _line_with(boxes, "= Total Spares")
    assert spares, "declared trailer caption never reached the trailer stack"
    assert abs(spares[0][3] - 0.10) < 1e-6, "unanchored line left the stack"
    assert abs(spares[0][4] - 5.00) < 1e-6, "unanchored line changed width"
    # fused caption + value, exactly as before
    assert "Sum(Fields!SPARES.Value" in spares[0][1]
