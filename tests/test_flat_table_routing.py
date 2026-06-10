"""Flat tabular reports must render as a column GRID, not a wallet card.

A single-group report with no detail sub-table and no linked detail query is an
ordinary table. Routing it through the grouped-card builder collapses every row
but the first via =First(Fields!X.Value) -- silent data loss. These guard the
routing gate (_is_grouped_card_report) and the rendered RDL.

Name-agnostic and synthetic -- no client data.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402
from converter.parsers.oracle_xml import parse_oracle_xml  # noqa: E402
from converter.generators.rdl import _is_grouped_card_report  # noqa: E402

# A FLAT table: one dataSource, one group, four independent columns, no break
# semantics, no ACTION_/STATUS_/... detail children, no <link> child query.
FLAT_TABLE_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<report name="PRODUCT_LIST" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q_PROD">
      <select canParse="no"><![CDATA[SELECT SKU, PRODUCT_NAME, QTY, PRICE FROM PRODUCTS]]></select>
      <group name="G_PROD">
        <dataItem name="SKU" oracleDatatype="number" columnOrder="1" defaultLabel="Sku">
          <dataDescriptor expression="SKU" oracleDatatype="number" precision="10"/>
        </dataItem>
        <dataItem name="PRODUCT_NAME" datatype="vchar2" columnOrder="2" defaultLabel="Product Name" breakOrder="none">
          <dataDescriptor expression="PRODUCT_NAME" width="64"/>
        </dataItem>
        <dataItem name="QTY" oracleDatatype="number" columnOrder="3" defaultLabel="Qty" breakOrder="none">
          <dataDescriptor expression="QTY" oracleDatatype="number" precision="10"/>
        </dataItem>
        <dataItem name="PRICE" oracleDatatype="number" columnOrder="4" defaultLabel="Price" breakOrder="none">
          <dataDescriptor expression="PRICE" oracleDatatype="number" precision="10" scale="2"/>
        </dataItem>
      </group>
    </dataSource>
  </data>
  <layout>
  <section name="main" width="11.00000" height="8.50000" orientation="landscape">
    <body width="10.42627" height="7.14587">
      <location x="0.29248" y="0.76038"/>
      <repeatingFrame name="R_G_PROD" source="G_PROD" printDirection="down">
        <geometryInfo x="0.02075" y="0.44812" width="10.37512" height="0.20000"/>
        <field name="F_SKU" source="SKU"><font face="Arial" size="10"/>
          <geometryInfo x="0.02075" y="0.44812" width="1.50000" height="0.18750"/></field>
        <field name="F_PRODUCT_NAME" source="PRODUCT_NAME"><font face="Arial" size="10"/>
          <geometryInfo x="1.60000" y="0.44812" width="4.00000" height="0.18750"/></field>
        <field name="F_QTY" source="QTY" alignment="end"><font face="Arial" size="10"/>
          <geometryInfo x="5.80000" y="0.44812" width="1.20000" height="0.18750"/></field>
        <field name="F_PRICE" source="PRICE" alignment="end"><font face="Arial" size="10"/>
          <geometryInfo x="7.20000" y="0.44812" width="1.20000" height="0.18750"/></field>
      </repeatingFrame>
      <frame name="M_G_PROD_HDR">
        <geometryInfo x="0.02075" y="0.00000" width="10.37512" height="0.38562"/>
        <visualSettings fillPattern="solid" fillForegroundColor="darkblue" lineForegroundColor="white"/>
        <text name="B_SKU"><geometryInfo x="0.02075" y="0.0" width="1.5" height="0.17"/>
          <textSegment><font face="Arial" size="10" bold="yes"/><string><![CDATA[SKU]]></string></textSegment></text>
        <text name="B_PROD"><geometryInfo x="1.60000" y="0.0" width="4.0" height="0.17"/>
          <textSegment><font face="Arial" size="10" bold="yes"/><string><![CDATA[Product Name]]></string></textSegment></text>
        <text name="B_QTY"><geometryInfo x="5.80000" y="0.0" width="1.2" height="0.17"/>
          <textSegment><font face="Arial" size="10" bold="yes"/><string><![CDATA[Qty]]></string></textSegment></text>
        <text name="B_PRICE"><geometryInfo x="7.20000" y="0.0" width="1.2" height="0.17"/>
          <textSegment><font face="Arial" size="10" bold="yes"/><string><![CDATA[Price]]></string></textSegment></text>
      </frame>
    </body>
    <margin>
      <text name="B_Title"><textSettings justify="center"/><geometryInfo x="3.0" y="0.25" width="4.9" height="0.2"/>
        <textSegment><font face="Arial" size="12" bold="yes"/><string><![CDATA[Product List Report]]></string></textSegment></text>
    </margin>
  </section>
  </layout>
</report>"""


def test_flat_table_is_not_classified_as_card():
    rep = parse_oracle_xml(FLAT_TABLE_XML)
    main = rep.queries[0]
    assert _is_grouped_card_report(main, rep) is False, (
        "a flat table (no detail sub-table, no linked detail query) must NOT "
        "be treated as a grouped-card report"
    )


def test_flat_table_renders_grid_without_first_collapse():
    rdl = convert(FLAT_TABLE_XML)["rdl_xml"]
    # A real grid has a column per field...
    assert len(re.findall(r"<TablixColumn>", rdl)) >= 3, "flat table did not render a multi-column grid"
    # ...and the repeating data columns are per-row Fields!, NEVER collapsed to
    # the first source row via =First() (which is the data-loss bug).
    for col in ("QTY", "PRICE", "PRODUCT_NAME"):
        assert re.search(rf"=Fields!{col}\.Value", rdl), f"{col} missing as a per-row field"
        assert not re.search(rf"=First\(Fields!{col}\.Value", rdl), (
            f"{col} was collapsed via =First() -- flat-table rows would be lost"
        )
