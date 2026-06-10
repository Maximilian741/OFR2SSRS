"""F1: Oracle formatMask -> SSRS <Format>.

Oracle display masks (currency, dates, thousands) were dropped at parse time so
the RDL emitted no <Format> -- numbers/dates rendered in raw DB form. These
cover the mask translator and the central emission pass.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402
from converter.generators.rdl import _oracle_mask_to_net  # noqa: E402


@pytest.mark.parametrize("mask,want", [
    ("$NNN,NN0.00", "$###,##0.00"),
    ("999,999", "###,###"),
    ("0.0000", "0.0000"),
    ("999G999D99", "###,###.##"),
    ("DD-MON-YYYY", "dd-MMM-yyyy"),
    ("MM/DD/YYYY", "MM/dd/yyyy"),
    ("MONTH DD, YYYY", "MMMM dd, yyyy"),
    ("HH24:MI:SS", "HH:mm:ss"),
    ("MM/DD/YYYY HH24:MI", "MM/dd/yyyy HH:mm"),
    # Oracle FM/FX fill-mode modifiers are stripped; A.M./P.M. -> tt
    ("FMMONTH dd, yyyy", "MMMM dd, yyyy"),
    ("FMMONTH dd, yyyy hh:FMMI P.M.", "MMMM dd, yyyy hh:mm tt"),
    ("FM999G990D00", "###,##0.00"),
])
def test_oracle_mask_to_net(mask, want):
    assert _oracle_mask_to_net(mask) == want


def test_unrecognized_mask_returns_empty():
    assert _oracle_mask_to_net("") == ""
    assert _oracle_mask_to_net("garbage") == ""


_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<report name="PRICED_LIST" DTDVersion="9.0.2.0.10">
  <data><dataSource name="Q">
    <select canParse="no"><![CDATA[SELECT SKU, PRICE, ORDER_DT FROM T]]></select>
    <group name="G">
      <dataItem name="SKU" oracleDatatype="number" defaultLabel="Sku" breakOrder="none"><dataDescriptor expression="SKU"/></dataItem>
      <dataItem name="PRICE" oracleDatatype="number" defaultLabel="Price" breakOrder="none"><dataDescriptor expression="PRICE" precision="10" scale="2"/></dataItem>
      <dataItem name="ORDER_DT" datatype="date" defaultLabel="Order Date" breakOrder="none"><dataDescriptor expression="ORDER_DT"/></dataItem>
    </group>
  </dataSource></data>
  <layout><section name="main" width="11" height="8.5">
    <body width="10" height="7"><location x="0.3" y="0.7"/>
      <repeatingFrame name="R_G" source="G" printDirection="down">
        <geometryInfo x="0" y="0.4" width="10" height="0.2"/>
        <field name="F_SKU" source="SKU"><font face="Arial" size="10"/><geometryInfo x="0" y="0.4" width="1.5" height="0.18"/></field>
        <field name="F_PRICE" source="PRICE" formatMask="$NNN,NN0.00" alignment="end"><font face="Arial" size="10"/><geometryInfo x="1.6" y="0.4" width="1.5" height="0.18"/></field>
        <field name="F_DT" source="ORDER_DT" formatMask="MM/DD/YYYY"><font face="Arial" size="10"/><geometryInfo x="3.2" y="0.4" width="1.5" height="0.18"/></field>
      </repeatingFrame>
      <frame name="HDR"><geometryInfo x="0" y="0" width="10" height="0.38"/>
        <visualSettings fillPattern="solid" fillForegroundColor="darkblue" lineForegroundColor="white"/>
        <text name="B_SKU"><geometryInfo x="0" y="0" width="1.5" height="0.17"/><textSegment><font face="Arial" size="10" bold="yes"/><string><![CDATA[SKU]]></string></textSegment></text>
        <text name="B_PRICE"><geometryInfo x="1.6" y="0" width="1.5" height="0.17"/><textSegment><font face="Arial" size="10" bold="yes"/><string><![CDATA[Price]]></string></textSegment></text>
        <text name="B_DT"><geometryInfo x="3.2" y="0" width="1.5" height="0.17"/><textSegment><font face="Arial" size="10" bold="yes"/><string><![CDATA[Order Date]]></string></textSegment></text>
      </frame>
    </body>
    <margin><text name="T"><textSettings justify="center"/><geometryInfo x="3" y="0.25" width="4" height="0.2"/><textSegment><font face="Arial" size="12" bold="yes"/><string><![CDATA[Priced List]]></string></textSegment></text></margin>
  </section></layout>
</report>"""


def test_format_emitted_on_masked_field_values():
    rdl = convert(_XML)["rdl_xml"]
    # currency + date masks land as <Format> next to their field value
    assert re.search(r"Fields!PRICE\.Value.*?<Format>\$###,##0\.00</Format>", rdl, re.DOTALL)
    assert re.search(r"Fields!ORDER_DT\.Value.*?<Format>MM/dd/yyyy</Format>", rdl, re.DOTALL)


def test_no_format_on_unmasked_field():
    rdl = convert(_XML)["rdl_xml"]
    # SKU has no formatMask -> exactly two <Format> elements total (PRICE, DT)
    assert len(re.findall(r"<Format>", rdl)) == 2
