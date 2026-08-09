"""The report-end trailer prints at its DECLARED y, not on a uniform stack.

Oracle re-flows the whole report-total block as a unit (it lands wherever the
data above it ends), but INSIDE the block every object keeps the y its own
declaration gives it. Measured on a truth PDF whose block declares three rules
and five lines: each object printed at its declared offset from the block's
first rule to within 0.0003in, and the block was exactly as tall as the
declaration makes it -- 0.9574in declared, 0.9577in printed.

The generator used to re-stack those lines on a synthesized uniform grid
(0.22in box + 0.04in gap = 0.26in a line). Three defects fell out of that:

  * the block ran 1.300in instead of 0.958in -- every gap wrong, and the
    closing rule 0.34in below where the source draws it;
  * a caption declared BESIDE a total (same declared y, different x) was
    pushed onto a line of its own, because the stack has one line per entry;
  * a caption whose declaration anchors it at Left 0.0in printed at the
    stack's synthesized 0.10in inset.

All three are asserted below against a synthetic trailer whose declared
geometry is the same shape. Nothing here comes from any real report.
"""
from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter.generators.rdl import generate_rdl, _q      # noqa: E402
from converter.parsers.oracle_xml import parse_oracle_xml  # noqa: E402


# --- declared geometry of the synthetic trailer block ----------------------
# (name, declared y, declared x, declared width)  -- the numbers the emitted
# block has to reproduce, expressed once so the test reads as a table.
RULE_TOP_Y = 1.90000
BAND_Y, BAND_X, BAND_W = 1.95000, 0.00000, 5.05000
SUB_Y, SUB_LBL_X, SUB_VAL_X = 1.94000, 5.25000, 6.60000
RULE_MID_Y = 2.18000
TOT_Y, TOT_LBL_X, TOT_VAL_X = 2.22534, 4.33000, 6.59400
CRU_Y, CRU_LBL_X, CRU_VAL_X = 2.43884, 5.24900, 6.60400
RULE_BOT_Y = 2.85559
BLOCK_H = RULE_BOT_Y - RULE_TOP_Y          # 0.95559in of declared block


def _xml() -> bytes:
    """A report whose report-total block declares, top to bottom: a full-width
    rule, a flush-left band caption sharing its line with a right-half
    label/value pair, a short rule, and two more label/value pairs, closed by a
    second full-width rule. The declared line pitch is deliberately UNEVEN
    (0.277in, 0.214in, 0.187in) so a uniform stack cannot fake it."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<report name="UNIT_ROLLUP" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q_1">
      <select><![CDATA[SELECT REGION, UNITS, SUBUNITS, SPARES, CARRY
                       FROM UNIT_ROLLUP]]></select>
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
        <dataItem name="SUBUNITS" oracleDatatype="number" width="22"
         defaultLabel="Subunits">
          <dataDescriptor expression="SUBUNITS" order="5" width="22"/>
        </dataItem>
      </group>
    </dataSource>
    <summary name="CS_SUB" source="SUBUNITS" function="sum" width="20"
     reset="report" compute="report" columnFlags="8"/>
    <summary name="CS_TOTAL" source="CARRY" function="sum" width="20"
     reset="report" compute="report" columnFlags="8"/>
    <summary name="CS_CRUSHED" source="SPARES" function="sum" width="20"
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
        <geometryInfo x="0.0" y="1.89" width="7.5" height="0.98"/>
        <line name="B_RULE_TOP" arrow="none">
          <geometryInfo x="0.00684" y="{RULE_TOP_Y}" width="7.48621"
           height="0.0"/>
          <visualSettings lineWidth="1" linePattern="solid"/>
          <points><point x="0.00684" y="{RULE_TOP_Y}"/>
            <point x="7.49304" y="{RULE_TOP_Y}"/></points>
        </line>
        <text name="B_BAND">
          <textSettings spacing="0"/>
          <geometryInfo x="{BAND_X}" y="{BAND_Y}" width="{BAND_W}"
           height="0.18689"/>
          <textSegment><font face="Arial" size="10" bold="yes"/>
            <string><![CDATA[ALL UNITS COMBINED]]></string>
          </textSegment>
        </text>
        <text name="B_SUB_LBL">
          <textSettings justify="end" spacing="single"/>
          <geometryInfo x="{SUB_LBL_X}" y="{SUB_Y}" width="1.24500"
           height="0.18652"/>
          <textSegment><font face="Arial" size="10"/>
            <string><![CDATA[Range Subtotal]]></string>
          </textSegment>
        </text>
        <field name="F_SUB_VAL" source="CS_SUB" formatMask="NNN,NN0"
         alignment="end">
          <font face="Arial" size="10" bold="yes"/>
          <geometryInfo x="{SUB_VAL_X}" y="{SUB_Y}" width="0.89978"
           height="0.18652"/>
        </field>
        <line name="B_RULE_MID" arrow="none">
          <geometryInfo x="6.61462" y="{RULE_MID_Y}" width="0.88538"
           height="0.0"/>
          <visualSettings linePattern="solid"/>
          <points><point x="6.61462" y="{RULE_MID_Y}"/>
            <point x="7.50000" y="{RULE_MID_Y}"/></points>
        </line>
        <text name="B_TOT_LBL">
          <textSettings justify="end" spacing="single"/>
          <geometryInfo x="{TOT_LBL_X}" y="{TOT_Y}" width="2.15527"
           height="0.19128"/>
          <textSegment><font face="Arial" size="10"/>
            <string><![CDATA[Total Units]]></string>
          </textSegment>
        </text>
        <field name="F_TOT_VAL" source="CS_TOTAL" formatMask="NNN,NN0"
         alignment="end">
          <font face="Arial" size="10" bold="yes"/>
          <geometryInfo x="{TOT_VAL_X}" y="{TOT_Y}" width="0.89551"
           height="0.21216"/>
        </field>
        <text name="B_CRU_LBL">
          <textSettings justify="end" spacing="single"/>
          <geometryInfo x="{CRU_LBL_X}" y="{CRU_Y}" width="1.25000"
           height="0.18628"/>
          <textSegment><font face="Arial" size="10"/>
            <string><![CDATA[- Total Spares]]></string>
          </textSegment>
        </text>
        <field name="F_CRU_VAL" source="CS_CRUSHED" formatMask="NNN,NN0"
         alignment="end">
          <font face="Arial" size="10" bold="yes"/>
          <geometryInfo x="{CRU_VAL_X}" y="{CRU_Y}" width="0.89551"
           height="0.18628"/>
        </field>
        <line name="B_RULE_BOT" arrow="none">
          <geometryInfo x="0.00684" y="{RULE_BOT_Y}" width="7.48621"
           height="0.0"/>
          <visualSettings lineWidth="1" linePattern="solid"/>
          <points><point x="0.00684" y="{RULE_BOT_Y}"/>
            <point x="7.49304" y="{RULE_BOT_Y}"/></points>
        </line>
      </frame>
    </body>
  </section>
  </layout>
</report>
""".encode("utf-8")


def _rdl():
    return generate_rdl(parse_oracle_xml(_xml()))


def _fin(el, tag):
    try:
        return float((el.findtext(_q(tag)) or "0").replace("in", ""))
    except ValueError:
        return 0.0


def _lines(rdl):
    """{name: (top, left, width, height, align, value)} for the trailer."""
    root = ET.fromstring(rdl)
    out = {}
    for tb in root.iter(_q("Textbox")):
        nm = tb.get("Name") or ""
        if not re.fullmatch(r"Tb_GrandTotal_\d+", nm):
            continue
        out[nm] = (_fin(tb, "Top"), _fin(tb, "Left"), _fin(tb, "Width"),
                   _fin(tb, "Height"),
                   next((a.text or "" for a in tb.iter(_q("TextAlign"))), ""),
                   next((v.text or "" for v in tb.iter(_q("Value"))), ""))
    return out


def _rules(rdl):
    """{declared line name: top} for every emitted trailer rule."""
    root = ET.fromstring(rdl)
    out = {}
    for ln in root.iter(_q("Line")):
        nm = (ln.get("Name") or "")
        if "_B_RULE_" in nm:
            out[nm.split("Rule_")[-1]] = _fin(ln, "Top")
    return out


def _find(lines, wording):
    hit = [v for v in lines.values() if wording in v[5]]
    assert hit, f"trailer line {wording!r} never emitted: {sorted(lines)}"
    return hit[0]


# ---------------------------------------------------------------------------
# 1. the block is exactly as tall as the declaration makes it
# ---------------------------------------------------------------------------

def test_block_height_is_the_declared_span_not_a_uniform_stack():
    rdl = _rdl()
    rules = _rules(rdl)
    assert {"B_RULE_TOP", "B_RULE_MID", "B_RULE_BOT"} <= set(rules), rules
    span = rules["B_RULE_BOT"] - rules["B_RULE_TOP"]
    assert abs(span - BLOCK_H) <= 0.01, (
        f"report-end block is {span:.3f}in tall, the declaration draws it "
        f"{BLOCK_H:.3f}in tall")
    # the middle rule keeps its own declared offset too (a stack would put
    # it wherever the running cursor happened to be)
    mid = rules["B_RULE_MID"] - rules["B_RULE_TOP"]
    assert abs(mid - (RULE_MID_Y - RULE_TOP_Y)) <= 0.01, mid


def test_every_line_prints_at_its_declared_offset():
    """The per-line table: emitted offset from the block anchor == declared
    offset, for every declared object in the block."""
    rdl = _rdl()
    anchor = _rules(rdl)["B_RULE_TOP"]
    lines = _lines(rdl)
    for wording, declared_y in (("ALL UNITS COMBINED", BAND_Y),
                                ("Range Subtotal", SUB_Y),
                                ("Total Units", TOT_Y),
                                ("- Total Spares", CRU_Y)):
        top = _find(lines, wording)[0]
        got, want = top - anchor, declared_y - RULE_TOP_Y
        assert abs(got - want) <= 0.01, (
            f"{wording!r} prints {got:.3f}in below the block anchor, "
            f"declared {want:.3f}in")


def test_declared_pitch_is_uneven_and_stays_uneven():
    """Negative twin of the stack: consecutive declared lines are 0.277in,
    0.214in and 0.187in apart, so any single fixed pitch is provably wrong."""
    rdl = _rdl()
    lines = _lines(rdl)
    tops = [_find(lines, w)[0] for w in
            ("Range Subtotal", "Total Units", "- Total Spares")]
    gaps = [round(b - a, 3) for a, b in zip(tops, tops[1:])]
    want = [round(TOT_Y - SUB_Y, 3), round(CRU_Y - TOT_Y, 3)]
    assert all(abs(g - w) <= 0.01 for g, w in zip(gaps, want)), (gaps, want)
    assert len(set(gaps)) == len(gaps), \
        f"the declared pitch collapsed onto one uniform value: {gaps}"


# ---------------------------------------------------------------------------
# 2. objects declared at the same y print on ONE line
# ---------------------------------------------------------------------------

def test_caption_declared_beside_a_total_shares_its_line():
    rdl = _rdl()
    lines = _lines(rdl)
    band = _find(lines, "ALL UNITS COMBINED")
    pair = _find(lines, "Range Subtotal")
    assert abs(band[0] - pair[0]) <= 0.02, (
        f"the band caption prints at {band[0]:.3f}in and the total declared "
        f"beside it at {pair[0]:.3f}in -- the declaration puts both on one "
        f"line ({BAND_Y} vs {SUB_Y})")
    # sharing a line is only legal because the boxes do not collide
    assert band[1] + band[2] <= pair[1] + 1e-6


# ---------------------------------------------------------------------------
# 3. a solo declared box prints at its DECLARED Left
# ---------------------------------------------------------------------------

def test_solo_declared_caption_keeps_its_declared_left_and_width():
    rdl = _rdl()
    band = _find(_lines(rdl), "ALL UNITS COMBINED")
    assert abs(band[1] - BAND_X) <= 0.005, (
        f"the band caption prints at Left {band[1]}in; the declaration puts "
        f"it at {BAND_X}in (0.10in is the synthesized stack inset)")
    assert abs(band[2] - BAND_W) <= 0.02, (
        f"the band caption is {band[2]}in wide, declared {BAND_W}in")


def test_declared_pairs_still_print_in_their_own_boxes():
    """The earlier contract (label box + value box at their declared
    x/alignment) must survive the vertical rework."""
    lines = _lines(_rdl())
    lbl = _find(lines, "- Total Spares")
    assert abs(lbl[1] - CRU_LBL_X) <= 0.005 and lbl[4] == "Right", lbl
    val = [v for v in lines.values()
           if v[5].startswith("=Sum(") and "Fields!SPARES.Value" in v[5]]
    assert val, "the paired total value never emitted"
    assert abs(val[0][1] - CRU_VAL_X) <= 0.005 and val[0][4] == "Right", val[0]
    assert abs(val[0][0] - lbl[0]) <= 0.005, "the pair left its own line"


def test_no_trailer_line_overlaps_the_line_below_it():
    """Declared y is honoured, never at the price of burying a line: rows
    that do not share a declared line must stay clear of each other."""
    lines = sorted(_lines(_rdl()).values())
    rows: dict = {}
    for top, _l, _w, h, _a, _v in lines:
        key = round(top, 2)
        rows[key] = max(rows.get(key, 0.0), h)
    keys = sorted(rows)
    for a, b in zip(keys, keys[1:]):
        if b - a <= 0.02:          # same printed line
            continue
        assert a + rows[a] <= b + 1e-6, (
            f"row at {a}in (+{rows[a]}in) collides with the row at {b}in")
