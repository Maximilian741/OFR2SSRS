"""F4/F5: Oracle <summary> totals.

F4 -- the nested builder emitted only the OUTER group's FIRST summary; the rest
were silently dropped. F5 -- the card builder ignored declared summaries and
fabricated a record count. Both now render every declared summary, mapped via
the Oracle->SSRS aggregate function table.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402
from converter.generators.rdl import _summary_total_expr  # noqa: E402


def test_summary_total_expr_emits_all_and_maps_functions():
    class _I:
        def __init__(self, n): self.name = n
    class _Q:
        items = [_I("SALES"), _I("MARGIN")]
    summaries = [
        {"source": "SALES", "function": "sum", "label": "Region Total"},
        {"source": "MARGIN", "function": "average", "label": "Avg Margin"},
        {"source": "MISSING", "function": "sum", "label": "Dropped"},
    ]
    expr = _summary_total_expr(summaries, _Q(), {"SALES", "MARGIN"})
    assert "Sum(Fields!SALES.Value)" in expr
    assert "Avg(Fields!MARGIN.Value)" in expr   # 'average' -> Avg, not Sum
    assert "Region Total:" in expr and "Avg Margin:" in expr
    assert "MISSING" not in expr                 # unbindable source skipped


_NESTED_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<report name="REGION_SALES" DTDVersion="9.0.2.0.10">
  <data><dataSource name="Q">
    <select canParse="no"><![CDATA[SELECT REGION, PRODUCT, SALES, MARGIN FROM T]]></select>
    <group name="G_REGION">
      <dataItem name="REGION" datatype="vchar2" defaultLabel="Region"><dataDescriptor expression="REGION"/></dataItem>
      <summary name="CS_TOT" source="SALES" function="sum" defaultLabel="Region Total"/>
      <summary name="CS_AVG" source="MARGIN" function="average" defaultLabel="Avg Margin"/>
      <group name="G_DETAIL">
        <dataItem name="PRODUCT" datatype="vchar2" defaultLabel="Product" breakOrder="none"><dataDescriptor expression="PRODUCT"/></dataItem>
        <dataItem name="SALES" oracleDatatype="number" defaultLabel="Sales" breakOrder="none"><dataDescriptor expression="SALES" precision="10" scale="2"/></dataItem>
        <dataItem name="MARGIN" oracleDatatype="number" defaultLabel="Margin" breakOrder="none"><dataDescriptor expression="MARGIN" precision="5" scale="2"/></dataItem>
      </group>
    </group>
  </dataSource></data>
  <layout><section name="main" width="11" height="8.5"><body width="10" height="7"><location x="0.3" y="0.7"/>
    <repeatingFrame name="R_REGION" source="G_REGION" printDirection="down"><geometryInfo x="0" y="0" width="10" height="0.3"/>
      <field name="F_REGION" source="REGION"><font face="Arial" size="11" bold="yes"/><geometryInfo x="0" y="0" width="3" height="0.2"/></field>
      <repeatingFrame name="R_DETAIL" source="G_DETAIL" printDirection="down"><geometryInfo x="0.2" y="0.4" width="9" height="0.2"/>
        <field name="F_PRODUCT" source="PRODUCT"><font face="Arial" size="10"/><geometryInfo x="0.2" y="0.4" width="3" height="0.18"/></field>
        <field name="F_SALES" source="SALES" alignment="end"><font face="Arial" size="10"/><geometryInfo x="3.4" y="0.4" width="1.5" height="0.18"/></field>
        <field name="F_MARGIN" source="MARGIN" alignment="end"><font face="Arial" size="10"/><geometryInfo x="5.0" y="0.4" width="1.5" height="0.18"/></field>
      </repeatingFrame>
    </repeatingFrame>
  </body>
  <margin><text name="T"><geometryInfo x="3" y="0.25" width="4" height="0.2"/><textSegment><font face="Arial" size="12" bold="yes"/><string><![CDATA[Region Sales]]></string></textSegment></text></margin>
  </section></layout>
</report>"""


def test_nested_emits_all_outer_summaries():
    rdl = convert(_NESTED_XML)["rdl_xml"]
    assert "Sum(Fields!SALES.Value)" in rdl     # first summary
    assert "Avg(Fields!MARGIN.Value)" in rdl    # second summary (was dropped before)
    assert "Region Total:" in rdl and "Avg Margin:" in rdl


_CARD_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<report name="CARD_SUM" DTDVersion="9.0.2.0.10"><data><dataSource name="Q">
<select canParse="no"><![CDATA[SELECT CASE_ID, AMOUNT, ACTION_TYPE, ACTION_DT FROM T]]></select>
<group name="G">
 <dataItem name="CASE_ID" oracleDatatype="number" defaultLabel="Case"><dataDescriptor expression="CASE_ID"/></dataItem>
 <dataItem name="AMOUNT" oracleDatatype="number" defaultLabel="Amount" breakOrder="none"><dataDescriptor expression="AMOUNT" precision="10" scale="2"/></dataItem>
 <dataItem name="ACTION_TYPE" datatype="vchar2" defaultLabel="Action" breakOrder="none"><dataDescriptor expression="ACTION_TYPE"/></dataItem>
 <dataItem name="ACTION_DT" datatype="date" defaultLabel="Action Date" breakOrder="none"><dataDescriptor expression="ACTION_DT"/></dataItem>
 <summary name="CS_DUE" source="AMOUNT" function="sum" defaultLabel="Total Due"/>
</group></dataSource></data>
<layout><section name="main" width="11" height="8.5"><body width="10" height="7"><location x="0.3" y="0.7"/>
<repeatingFrame name="R_G" source="G" printDirection="down"><geometryInfo x="0" y="0" width="10" height="0.3"/>
 <field name="F_CASE" source="CASE_ID"><font face="Arial" size="11"/><geometryInfo x="0" y="0" width="2" height="0.2"/></field>
 <field name="F_AMT" source="AMOUNT" alignment="end"><font face="Arial" size="10"/><geometryInfo x="2.2" y="0" width="1.5" height="0.18"/></field>
 <field name="F_AT" source="ACTION_TYPE"><font face="Arial" size="10"/><geometryInfo x="0.2" y="0.4" width="2" height="0.18"/></field>
 <field name="F_AD" source="ACTION_DT"><font face="Arial" size="10"/><geometryInfo x="2.4" y="0.4" width="1.5" height="0.18"/></field>
</repeatingFrame></body>
<margin><text name="T"><geometryInfo x="3" y="0.25" width="4" height="0.2"/><textSegment><font face="Arial" size="12" bold="yes"/><string><![CDATA[Cards]]></string></textSegment></text></margin>
</section></layout></report>"""


def test_card_renders_declared_summary_not_fabricated_count():
    rdl = convert(_CARD_XML)["rdl_xml"]
    assert "Tablix_Cards" in rdl
    assert "Sum(Fields!AMOUNT.Value)" in rdl   # the declared SUM(AMOUNT)
    assert "Total Due:" in rdl                  # its label


# F4b: a 3-level nested report -- the MIDDLE group's <summary> must render as an
# inner-group subtotal in its card (only the outer's was emitted before).
_NESTED3_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<report name="REGION_DISTRICT_SALES" DTDVersion="9.0.2.0.10"><data><dataSource name="Q">
<select canParse="no"><![CDATA[SELECT REGION, DISTRICT, PRODUCT, SALES, MARGIN FROM T]]></select>
<group name="G_REGION">
 <dataItem name="REGION" datatype="vchar2" defaultLabel="Region"><dataDescriptor expression="REGION"/></dataItem>
 <summary name="CS_RTOT" source="SALES" function="sum" defaultLabel="Region Total"/>
 <group name="G_DISTRICT">
  <dataItem name="DISTRICT" datatype="vchar2" defaultLabel="District"><dataDescriptor expression="DISTRICT"/></dataItem>
  <summary name="CS_DAVG" source="MARGIN" function="average" defaultLabel="District Avg Margin"/>
  <group name="G_DETAIL">
   <dataItem name="PRODUCT" datatype="vchar2" defaultLabel="Product" breakOrder="none"><dataDescriptor expression="PRODUCT"/></dataItem>
   <dataItem name="SALES" oracleDatatype="number" defaultLabel="Sales" breakOrder="none"><dataDescriptor expression="SALES" precision="10" scale="2"/></dataItem>
   <dataItem name="MARGIN" oracleDatatype="number" defaultLabel="Margin" breakOrder="none"><dataDescriptor expression="MARGIN" precision="5" scale="2"/></dataItem>
  </group>
 </group>
</group></dataSource></data>
<layout><section name="main" width="11" height="8.5"><body width="10" height="7"><location x="0.3" y="0.7"/>
<repeatingFrame name="R_REGION" source="G_REGION" printDirection="down"><geometryInfo x="0" y="0" width="10" height="0.3"/>
 <field name="F_REGION" source="REGION"><font face="Arial" size="11" bold="yes"/><geometryInfo x="0" y="0" width="3" height="0.2"/></field>
 <repeatingFrame name="R_DISTRICT" source="G_DISTRICT" printDirection="down"><geometryInfo x="0.2" y="0.3" width="9" height="0.3"/>
  <field name="F_DISTRICT" source="DISTRICT"><font face="Arial" size="10" bold="yes"/><geometryInfo x="0.2" y="0.3" width="3" height="0.2"/></field>
  <repeatingFrame name="R_DETAIL" source="G_DETAIL" printDirection="down"><geometryInfo x="0.4" y="0.6" width="8" height="0.2"/>
   <field name="F_PRODUCT" source="PRODUCT"><font face="Arial" size="10"/><geometryInfo x="0.4" y="0.6" width="3" height="0.18"/></field>
   <field name="F_SALES" source="SALES" alignment="end"><font face="Arial" size="10"/><geometryInfo x="3.6" y="0.6" width="1.5" height="0.18"/></field>
   <field name="F_MARGIN" source="MARGIN" alignment="end"><font face="Arial" size="10"/><geometryInfo x="5.2" y="0.6" width="1.5" height="0.18"/></field>
  </repeatingFrame>
 </repeatingFrame>
</repeatingFrame></body></section></layout></report>"""


def test_inner_group_subtotal_renders_in_middle_card():
    rdl = convert(_NESTED3_XML)["rdl_xml"]
    assert "Sum(Fields!SALES.Value)" in rdl        # outer (Region) total on band
    assert "Avg(Fields!MARGIN.Value)" in rdl       # MIDDLE (District) subtotal -- F4b
    assert "Region Total:" in rdl and "District Avg Margin:" in rdl
