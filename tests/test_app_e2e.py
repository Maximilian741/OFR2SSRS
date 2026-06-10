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
