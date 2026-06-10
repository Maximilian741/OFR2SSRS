"""Partial Oracle artifacts get an honest 'not a full report' verdict instead
of a scary RED + near-blank render (wild-corpus verified)."""
from __future__ import annotations

import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402

_CUSTOMIZE = b"""<?xml version="1.0" encoding="UTF-8"?>
<report name="X" DTDVersion="1.0">
  <layout><section name="main"><body width="7" height="9">
    <field name="F1"><geometryInfo x="0" y="0" width="2" height="0.2"/>
      <exception textColor="r100g0b0"/></field>
  </body></section></layout>
  <customize><object type="REP_REPORT"><property name="beforeReport"/></object></customize>
</report>"""

_DATA_ONLY = b"""<?xml version="1.0" encoding="UTF-8"?>
<report name="Y" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q1"><select><![CDATA[SELECT A, B FROM T]]></select>
      <group name="G1"><dataItem name="A" datatype="vchar2"/>
        <dataItem name="B" datatype="number"/></group>
    </dataSource>
  </data>
</report>"""

_FULL = b"""<?xml version="1.0" encoding="UTF-8"?>
<report name="Z" DTDVersion="9.0.2.0.10">
  <data><dataSource name="Q1"><select><![CDATA[SELECT A FROM T]]></select>
    <group name="G1"><dataItem name="A" datatype="vchar2"/></group>
  </dataSource></data>
  <layout><section name="main"><body width="7" height="9">
    <field name="F_A" source="A"><geometryInfo x="0" y="0" width="3" height="0.2"/></field>
  </body></section></layout>
</report>"""


def test_customization_overlay_flagged():
    pf = convert(_CUSTOMIZE)["preflight"]
    assert pf.get("source_kind") == "customization_overlay"
    assert "customization" in pf.get("source_kind_message", "").lower()


def test_data_model_only_flagged():
    pf = convert(_DATA_ONLY)["preflight"]
    assert pf.get("source_kind") == "data_model_only"
    assert "layout" in pf.get("source_kind_message", "").lower()


def test_full_report_not_flagged():
    pf = convert(_FULL)["preflight"]
    assert pf.get("source_kind") is None
    assert pf.get("verdict") == "READY"


def test_partial_verdict_flows_through_http():
    from app import app
    app.config["TESTING"] = True
    with app.test_client() as client:
        r = client.post("/api/convert",
                        data={"file": (io.BytesIO(_DATA_ONLY), "dm.xml")},
                        content_type="multipart/form-data")
        assert r.status_code == 200
        pf = r.get_json()["preflight"]
        assert pf["source_kind"] == "data_model_only"
        assert pf["source_kind_message"]
