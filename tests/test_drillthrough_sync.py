"""Drill-through chicken-and-egg fixes.

1. The parent's <Drillthrough><ReportName> must end up matching the child
   report that is ACTUALLY built — when artifacts produce a different
   name, the cached parent is re-synced in place (build order no longer
   matters; the next parent download is the completed RDL).
2. A layout field whose SOURCE is a link's URL-builder formula must get a
   Drillthrough action too (the Oracle cover page's clickable URL line),
   not just fields carrying <webSettings hyperlink>.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

FIXTURE = ROOT / "tests" / "fixtures" / "subreports" / "SAMPLE_DRILLTHROUGH.xml"


def _child_xml(name: str) -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<report name="{name}" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q_ENV">
      <select><![CDATA[SELECT Site_Name FROM Sites]]></select>
      <group name="G_ENV">
        <dataItem name="Site_Name" datatype="vchar2"/>
      </group>
    </dataSource>
  </data>
  <layout>
  <section name="main">
    <body width="8.0" height="10.0">
      <field name="F_SITE" x="0.5" y="0.5" width="7.0" height="0.3"
             source="Site_Name"/>
    </body>
  </section>
  </layout>
</report>
""".encode()


def _convert_parent(client):
    with FIXTURE.open("rb") as fh:
        r = client.post("/api/convert",
                        data={"file": (fh, FIXTURE.name)},
                        content_type="multipart/form-data")
    assert r.status_code == 200
    return r.get_json()


def test_parent_resyncs_when_child_builds_under_different_name():
    from app import app
    app.config["TESTING"] = True
    with app.test_client() as client:
        _convert_parent(client)
        parent = client.get("/api/download/rdl").get_data(as_text=True)
        assert "<ReportName>CHILD_REPORT</ReportName>" in parent

        up = client.post(
            "/api/subreport/CHILD_REPORT/upload",
            data={"artifact": (io.BytesIO(_child_xml("ENVELOPE_FINAL")),
                               "ENVELOPE_FINAL.xml")},
            content_type="multipart/form-data")
        assert up.status_code == 200
        b = client.post("/api/subreport/CHILD_REPORT/build", json={})
        j = b.get_json()
        assert b.status_code == 200, j
        assert j["report_name"] == "ENVELOPE_FINAL"
        assert j["parent_synced"] is True
        assert any("RE-SYNCED" in i for i in j["issues"]), j["issues"]
        assert "<ReportName>ENVELOPE_FINAL</ReportName>" in j["parent_rdl_xml"]

        # The next parent download IS the completed RDL.
        parent2 = client.get("/api/download/rdl").get_data(as_text=True)
        assert "<ReportName>ENVELOPE_FINAL</ReportName>" in parent2
        assert "<ReportName>CHILD_REPORT</ReportName>" not in parent2
        client.post("/api/subreport/CHILD_REPORT/clear")


def test_link_verified_note_when_names_already_match():
    from app import app
    app.config["TESTING"] = True
    with app.test_client() as client:
        _convert_parent(client)
        client.post(
            "/api/subreport/CHILD_REPORT/upload",
            data={"artifact": (io.BytesIO(_child_xml("CHILD_REPORT")),
                               "CHILD_REPORT.xml")},
            content_type="multipart/form-data")
        j = client.post("/api/subreport/CHILD_REPORT/build", json={}).get_json()
        assert j["parent_synced"] is False
        assert any("VERIFIED" in i for i in j["issues"]), j["issues"]
        client.post("/api/subreport/CHILD_REPORT/clear")


def test_url_source_field_matches_drillthrough():
    """A field whose SOURCE is the link's URL formula (the cover's visible
    URL text) must resolve to the same Drillthrough as a hyperlink field."""
    from converter.parsers.oracle_xml import parse_oracle_xml
    from converter.generators.rdl import _drillthrough_for

    report = parse_oracle_xml(FIXTURE.read_bytes())

    class _LF:  # minimal stand-in for a LayoutField
        hyperlink = ""
        source = "CP_CHILD_ENVELOPE"

    dt = _drillthrough_for(report, _LF())
    assert dt and dt["report_name"] == "CHILD_REPORT"

    class _NoMatch:
        hyperlink = ""
        source = "SOME_OTHER_COLUMN"

    assert _drillthrough_for(report, _NoMatch()) is None
