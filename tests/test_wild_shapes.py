"""Locks for the wild-corpus publish-safety nets (hunted-artifact verified).

1. Dangling-Fields! repair: a layout field whose source is really a
   PARAMETER (or a wrong-cased column) must not ship as a raw Fields! ref
   — SSRS rejects those at publish time.
2. Dataset hygiene: a nameless <dataItem> (Oracle's own docs ship one)
   must be skipped, never emitted as a field named "_"; an all-nameless
   dataset gets a PLACEHOLDER field so the engine can build a reader.
3. The repair net must NEVER rewrite refs already inside scoped
   aggregates/Lookups (no nested-aggregate publish rejections).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402

_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<report name="WILD_SHAPES" DTDVersion="9.0.2.0.10">
  <data>
    <userParameter name="P_REGION" datatype="character"/>
    <dataSource name="Q_MAIN">
      <select><![CDATA[SELECT Thing_Name, Qty FROM Things]]></select>
      <group name="G_MAIN">
        <dataItem name="Thing_Name" datatype="vchar2"/>
        <dataItem name="Qty" datatype="number"/>
        <dataItem datatype="vchar2"/>
      </group>
    </dataSource>
  </data>
  <layout>
  <section name="main">
    <body width="8.0" height="10.0">
      <repeatingFrame name="R_MAIN" source="G_MAIN" printDirection="down">
        <geometryInfo x="0.0" y="0.0" width="7.0" height="0.6"/>
        <field name="F_NAME" source="THING_NAME" minWidowLines="1">
          <geometryInfo x="0.0" y="0.0" width="3.0" height="0.2"/>
        </field>
        <field name="F_REGION" source="REGION" minWidowLines="1">
          <geometryInfo x="3.2" y="0.0" width="2.0" height="0.2"/>
        </field>
      </repeatingFrame>
    </body>
  </section>
  </layout>
</report>
"""


def test_dangling_refs_are_repaired_not_shipped():
    rdl = convert(_XML)["rdl_xml"]
    # REGION is not a column anywhere; P_REGION is a declared parameter ->
    # the P_-prefix repair must bind it.
    assert "Parameters!P_REGION.Value" in rdl
    assert "Fields!REGION.Value" not in rdl
    # THING_NAME (wrong case for Thing_Name) must resolve to the exact-cased
    # field, not ship verbatim.
    assert "Fields!THING_NAME.Value" not in rdl
    assert "Thing_Name" in rdl


def test_nameless_dataitem_skipped_and_no_cls_invalid_field():
    rdl = convert(_XML)["rdl_xml"]
    assert '<Field Name="_">' not in rdl
    names = re.findall(r'<Field Name="([^"]+)"', rdl)
    assert all(n.strip("_") for n in names), names


def test_all_nameless_dataset_gets_placeholder():
    xml = _XML.replace(b'name="Thing_Name" ', b"").replace(b'name="Qty" ', b"")
    rdl = convert(xml)["rdl_xml"]
    assert '<Field Name="PLACEHOLDER">' in rdl
    assert '<Field Name="_">' not in rdl


def test_no_nested_aggregates_anywhere_in_fixture_outputs():
    """The protected-span guard: across every synthetic fixture, the net
    must never produce First/aggregate nested inside another aggregate or
    Lookup (both are SSRS publish rejections)."""
    bad = re.compile(
        r"(CountDistinct|CountRows|Sum|Avg|Min|Max|Count|Lookup)\s*\("
        r"[^()]*\bFirst\s*\(")
    fixtures = list((ROOT / "tests" / "fixtures").rglob("source.xml")) + \
        list((ROOT / "tests" / "fixtures" / "subreports").glob("*.xml"))
    assert fixtures
    for f in fixtures:
        rdl = convert(f.read_bytes())["rdl_xml"]
        m = bad.search(rdl)
        assert not m, f"{f.name}: nested aggregate emitted: {m.group(0)[:80]}"
