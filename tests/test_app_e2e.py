"""End-to-end tests through the REAL Flask app (full HTTP stack, in-process).

These are the "it actually generates" tests: not detection, not stubs —
every flow a user clicks is driven over HTTP and its OUTPUT is opened and
checked: sub-report build/download, burst pack zip, data source binding,
and the never-prompt parameter invariant on every produced artifact.
"""
from __future__ import annotations

import io
import re
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

FIXTURES = ROOT / "tests" / "fixtures"


@pytest.fixture()
def client():
    from app import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _letter_xml() -> bytes:
    p = FIXTURES / "subreports" / "SAMPLE_DRILLTHROUGH.xml"
    if not p.exists():
        pytest.skip("fixture missing")
    return p.read_bytes()


# A minimal synthetic Oracle report that trips the bursting detector
# (P_AS_PATH param) -- structural trigger, nothing client-specific.
_BURSTING_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<report name="SAMPLE_BURST" DTDVersion="9.0.2.0.10">
  <data>
    <userParameter name="P_AS_PATH" datatype="character"/>
    <userParameter name="P_DISTRIBUTE" datatype="character"/>
    <dataSource name="Q_MAIN">
      <select>
      <![CDATA[SELECT R.Recipient_Name, R.Email_Addr, R.Doc_No
FROM Recipients R WHERE R.Active = 'Y']]>
      </select>
      <group name="G_MAIN">
        <dataItem name="Recipient_Name" datatype="vchar2"/>
        <dataItem name="Email_Addr" datatype="vchar2"/>
        <dataItem name="Doc_No" datatype="number"/>
      </group>
    </dataSource>
  </data>
  <layout>
  <section name="main">
    <body width="8.0" height="10.0">
      <text name="B_TITLE" x="0.5" y="0.5" width="7.0" height="0.4">
        <textsettings justify="center"/>
        <font face="Arial" size="14" bold="yes"/>
        <contents>Sample Letter</contents>
      </text>
      <field name="F_NAME" x="0.5" y="1.2" width="7.0" height="0.3" source="Recipient_Name"/>
      <field name="F_DOC" x="0.5" y="1.6" width="7.0" height="0.3" source="Doc_No"/>
    </body>
  </section>
  </layout>
</report>
"""


def _convert(client, xml_bytes: bytes, extra: dict | None = None):
    data = {"file": (io.BytesIO(xml_bytes), "sample.xml")}
    data.update(extra or {})
    r = client.post("/api/convert", data=data,
                    content_type="multipart/form-data")
    assert r.status_code == 200, r.get_data(as_text=True)[:400]
    j = r.get_json()
    assert not j.get("error"), j.get("error")
    return j


def _assert_no_prompt_params(rdl: str, ctx: str):
    """THE invariant: no parameter may ever prompt on upload/refresh/run."""
    for pname, block in re.findall(
            r'<ReportParameter Name="([^"]+)">(.*?)</ReportParameter>',
            rdl, re.S):
        assert "=Nothing" in block or "DataSetReference" in block, (
            f"[{ctx}] parameter {pname} has no default -> WOULD PROMPT")


# ---------------------------------------------------------------------------
# Main convert + data source binding
# ---------------------------------------------------------------------------

def test_no_empty_query_parameter_values(client):
    """An empty <Value/> on a QueryParameter is a 'Define Query Parameters'
    prompt trigger. Undeclared binds must bind =Nothing (silent NULL)."""
    for xml in (_letter_xml(), _BURSTING_XML):
        rdl = _convert(client, xml)["rdl_xml"]
        for block in re.findall(r"<QueryParameters>.*?</QueryParameters>",
                                rdl, re.S):
            assert "<Value />" not in block and "<Value/>" not in block \
                and "<Value></Value>" not in block, (
                    "empty QueryParameter Value found -> WOULD PROMPT:\n"
                    + block[:400])


def test_convert_binds_shared_datasource_path(client):
    j = _convert(client, _letter_xml(), {"shared_ds_path": "/Data Sources/Oracle"})
    rdl = j["rdl_xml"]
    assert "<DataSourceReference>/Data Sources/Oracle</DataSourceReference>" in rdl
    assert "<DataSourceReference>SharedDataSource</DataSourceReference>" not in rdl
    _assert_no_prompt_params(rdl, "main convert")


def test_download_rdl_carries_datasource(client):
    _convert(client, _letter_xml(), {"shared_ds_path": "/DS/Bound"})
    r = client.get("/api/download/rdl")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "<DataSourceReference>/DS/Bound</DataSourceReference>" in body


# ---------------------------------------------------------------------------
# Sub-reports: upload artifact -> build -> download (must ACTUALLY generate)
# ---------------------------------------------------------------------------

def test_subreport_build_and_download_e2e(client):
    _convert(client, _letter_xml(), {"shared_ds_path": "/DS/Oracle"})
    child_sql = (
        b"SELECT O.Org_Name, O.Org_Addr FROM Orgs O\n"
        b"WHERE O.Org_Id = :P_ORG_ID AND O.Site_Id = :P_SITE_ID\n"
    )
    up = client.post(
        "/api/subreport/CHILD_ENV/upload",
        data={"artifact": (io.BytesIO(child_sql), "child.sql")},
        content_type="multipart/form-data",
    )
    assert up.status_code == 200, up.get_data(as_text=True)[:300]
    b = client.post("/api/subreport/CHILD_ENV/build", json={
        "shared_ds_path": "/DS/Oracle"})
    assert b.status_code == 200, b.get_data(as_text=True)[:400]
    bj = b.get_json()
    rdl = bj.get("rdl_xml") or ""
    assert "<Report" in rdl, "build returned no RDL"
    assert bj.get("source") in ("sql", "oracle_xml", "rdl"), bj.get("source")
    # Child binds declared as params, never-prompt defaults, ds path applied.
    assert "P_ORG_ID" in rdl and "P_SITE_ID" in rdl
    _assert_no_prompt_params(rdl, "subreport build")
    assert "<DataSourceReference>/DS/Oracle</DataSourceReference>" in rdl
    # Download streams the SAME artifact.
    d = client.get("/api/subreport/CHILD_ENV/download")
    assert d.status_code == 200
    dl = d.get_data(as_text=True)
    assert "<DataSourceReference>/DS/Oracle</DataSourceReference>" in dl
    # Cleanup for repeatability.
    client.post("/api/subreport/CHILD_ENV/clear")


# ---------------------------------------------------------------------------
# Bursting: preview + pack must ACTUALLY produce artifacts
# ---------------------------------------------------------------------------

def test_bursting_detected_and_pack_generates(client):
    j = _convert(client, _BURSTING_XML, {"shared_ds_path": "/DS/Oracle"})
    assert j["bursting"].get("is_bursting") is True, j["bursting"]
    # Preview endpoint rebuilds the 4 blocks.
    p = client.post("/api/burst-preview", json={})
    assert p.status_code == 200, p.get_data(as_text=True)[:400]
    pj = p.get_json()
    for key in ("email_burst_query", "email_powershell_script",
                "email_config_template", "service_account_checklist"):
        val = pj.get(key)
        # Blocks may be a string (SQL/script) or a structured list
        # (checklist steps) — both must be present and non-empty.
        if isinstance(val, (list, dict)):
            assert val, f"burst preview block {key} empty"
        else:
            assert (val or "").strip(), f"burst preview block {key} empty"
    # The pack: a real zip with every artifact inside.
    z = client.post("/api/download/burst-pack", json={})
    assert z.status_code == 200, z.get_data(as_text=True)[:400]
    zf = zipfile.ZipFile(io.BytesIO(z.data))
    names = set(zf.namelist())
    assert "Send-Reports.ps1" in names
    assert "burst.config.json" in names
    assert "README.md" in names
    assert "service-account-setup.md" in names
    rdl_names = [n for n in names if n.endswith(".rdl")]
    assert rdl_names, "burst pack missing the .rdl"
    rdl = zf.read(rdl_names[0]).decode("utf-8")
    # The packed RDL inherits the session's data source binding + invariant.
    assert "<DataSourceReference>/DS/Oracle</DataSourceReference>" in rdl
    _assert_no_prompt_params(rdl, "burst pack rdl")
    ps = zf.read("Send-Reports.ps1").decode("utf-8", "replace")
    assert "__BURST_SQL__" not in ps, "PS template placeholder not substituted"
    assert "__REPORT_NAME__" not in ps, "PS template placeholder not substituted"


def test_oracle_xml_subreport_declares_forwarded_drillthrough_params():
    """An Oracle-XML sub-report (the highest-fidelity path) must DECLARE the
    parent's forwarded drill-through params, else the parent's <Drillthrough>
    errors "parameter not declared" the instant it is clicked. Regression:
    that path used to silently drop them and surface 0 fields."""
    from converter.subreports import build_subreport
    src = ROOT / "tests" / "fixtures" / "source_of_truth" / "master_detail" / "source.xml"
    if not src.exists():
        import pytest
        pytest.skip("fixture missing")
    res = build_subreport("Child", [str(src)],
                          drillthrough_params=["P_DRILL_KEY", "P_ORG_ID"])
    assert res["source"] == "oracle_xml"
    rdl = res["rdl_xml"]
    for p in ("P_DRILL_KEY", "P_ORG_ID"):
        assert f'<ReportParameter Name="{p}">' in rdl, f"{p} not declared"
        assert p in res["forwarded_params"]
    # fields are now surfaced (was hardcoded [])
    assert res["fields"], "expected dataset field names to be surfaced"


def test_rdl_as_is_subreport_injects_forwarded_drillthrough_params():
    """The RDL-as-is sub-report path (user uploads a built .rdl) must ALSO
    declare the parent's forwarded drill-through params -- injecting a hidden
    ReportParameter if absent -- and the result must stay XSD-valid. Same gap
    the Oracle-XML path had; covers both 'no params yet' and 'append'."""
    import tempfile, os
    from converter import convert
    from converter.subreports import build_subreport, _inject_report_parameters
    src = ROOT / "tests" / "fixtures" / "source_of_truth" / "master_detail" / "source.xml"
    if not src.exists():
        pytest.skip("fixture missing")
    rdl = convert(src.read_bytes())["rdl_xml"]
    with tempfile.TemporaryDirectory() as td:
        rp = os.path.join(td, "child.rdl")
        with open(rp, "w", encoding="utf-8") as fh:
            fh.write(rdl)
        res = build_subreport("Child", [rp],
                              drillthrough_params=["P_DRILL_KEY", "P_ORG_ID"])
    assert res["source"] == "rdl"
    for p in ("P_DRILL_KEY", "P_ORG_ID"):
        assert f'Name="{p}"' in res["rdl_xml"], f"{p} not declared"
        assert p in res["forwarded_params"]
    # unit: a param already present is NOT duplicated
    once = _inject_report_parameters(res["rdl_xml"], ["P_DRILL_KEY"])
    assert once.count('Name="P_DRILL_KEY"') == 1


def test_stub_subreport_path_declares_forwarded_drillthrough_params():
    """The STUB fallback path (no parseable artifact) must also declare the
    parent's forwarded drill-through params. Completes the sweep: all four
    sub-report input paths (oracle_xml, rdl, sql, stub) declare them, so a
    parent drill-through never errors regardless of child source."""
    import tempfile, os
    from converter.subreports import build_subreport
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "notes.txt")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("just notes, no query here")
        res = build_subreport("Child", [p],
                              drillthrough_params=["P_DRILL_KEY", "P_ORG_ID"])
    assert res["source"] == "stub"
    for p in ("P_DRILL_KEY", "P_ORG_ID"):
        assert f'Name="{p}"' in res["rdl_xml"], f"{p} not declared in stub"


def test_endpoints_never_500_on_bad_input(client):
    """Security/robustness: no endpoint may return a 500 (which can leak a
    stack trace) on missing/garbage input. Bad input -> a clean 4xx, or a
    graceful 200 fallback -- never an unhandled server error."""
    import io as _io
    probes = [
        ("post", "/api/convert", {}),
        ("post", "/api/convert", {"json": {}}),
        ("post", "/api/batch", {}),
        ("post", "/api/run-query", {"json": {}}),
        ("post", "/api/auto-fix", {"json": {}}),
        ("post", "/api/apply-fix", {"json": {}}),
        ("post", "/api/report-images/upload", {}),
        ("post", "/api/burst-preview", {"json": {}}),
        ("post", "/api/subreport/Child/build", {"json": {}}),
        ("get", "/api/download/rdl", {}),
        ("get", "/api/download/bundle", {}),
        ("get", "/api/download/batch-pack", {}),
        ("get", "/api/mockup/bogus", {}),
    ]
    for method, path, kw in probes:
        resp = getattr(client, method)(path, **kw)
        assert resp.status_code < 500, f"{method.upper()} {path} -> {resp.status_code}"
    # garbage XML upload must convert to a fallback, not crash
    g = client.post("/api/convert",
                    data={"file": (_io.BytesIO(b"not xml at all"), "x.xml")},
                    content_type="multipart/form-data")
    assert g.status_code < 500
