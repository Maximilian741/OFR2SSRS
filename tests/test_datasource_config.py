"""Deployment data-source configuration.

The user supplies their report server's SHARED data source path once; every
generated artifact (main RDL, sub-report RDLs, burst pack) carries that
reference so SSRS binds the data source AT UPLOAD — no manual repointing,
no refresh dance, and (the load-bearing invariant) NEVER a parameter
prompt. An embedded connection string remains available as an opt-in.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter.rdl_postprocess import (  # noqa: E402
    inject_connection_string,
    set_datasource_reference,
)

_SAMPLE_RDL = (
    '<?xml version="1.0"?><Report>'
    "<DataSources><DataSource Name=\"SharedDataSource\">"
    "<DataSourceReference>SharedDataSource</DataSourceReference>"
    "</DataSource></DataSources>"
    "<Body/></Report>"
)


def test_set_datasource_reference_rewrites_target():
    out = set_datasource_reference(_SAMPLE_RDL, "/Data Sources/Oracle_Prod")
    assert "<DataSourceReference>/Data Sources/Oracle_Prod</DataSourceReference>" in out
    # The DataSource NAME (what datasets bind to) must stay untouched.
    assert 'DataSource Name="SharedDataSource"' in out


def test_set_datasource_reference_handles_all_occurrences_and_escaping():
    two = _SAMPLE_RDL + _SAMPLE_RDL
    out = set_datasource_reference(two, "A&B")
    assert out.count("<DataSourceReference>A&amp;B</DataSourceReference>") == 2


def test_set_datasource_reference_empty_is_noop():
    assert set_datasource_reference(_SAMPLE_RDL, "") == _SAMPLE_RDL
    assert set_datasource_reference(_SAMPLE_RDL, None) == _SAMPLE_RDL


def test_inject_connection_string_converts_reference_to_embedded():
    out = inject_connection_string(_SAMPLE_RDL, "Data Source=db;User Id=x")
    assert "<DataSourceReference>" not in out
    assert "<ConnectionProperties>" in out
    assert "<DataProvider>ORACLE</DataProvider>" in out
    assert "<ConnectString>Data Source=db;User Id=x</ConnectString>" in out


def test_inject_connection_string_provider_override():
    out = inject_connection_string(_SAMPLE_RDL, "Server=s;Database=d",
                                   provider="SQL")
    assert "<DataProvider>SQL</DataProvider>" in out


def test_inject_connection_string_legacy_swap_still_works():
    legacy = "<Report><ConnectString>OLD</ConnectString></Report>"
    out = inject_connection_string(legacy, "NEW")
    assert "<ConnectString>NEW</ConnectString>" in out


def _flask_client():
    from app import app
    app.config["TESTING"] = True
    return app.test_client()


def _first_sample_name():
    samples = sorted((ROOT / "samples" / "oracle").glob("*.xml"))
    if not samples:
        pytest.skip("no bundled samples")
    return samples[0].name


def test_convert_sample_applies_shared_ds_path_and_remembers_it():
    client = _flask_client()
    name = _first_sample_name()
    r = client.post(f"/api/convert-sample/{name}?shared_ds_path=/DS/MyOracle")
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    rdl = r.get_json()["rdl_xml"]
    assert "<DataSourceReference>/DS/MyOracle</DataSourceReference>" in rdl
    assert "<DataSourceReference>SharedDataSource</DataSourceReference>" not in rdl
    # Session memory: a follow-up convert WITHOUT the field still gets it.
    r2 = client.post(f"/api/convert-sample/{name}")
    rdl2 = r2.get_json()["rdl_xml"]
    assert "<DataSourceReference>/DS/MyOracle</DataSourceReference>" in rdl2


def test_convert_sample_embedded_connection_string():
    client = _flask_client()
    name = _first_sample_name()
    r = client.post(
        f"/api/convert-sample/{name}?connection_string=Data%20Source%3Ddb1")
    assert r.status_code == 200
    rdl = r.get_json()["rdl_xml"]
    assert "<ConnectionProperties>" in rdl
    assert "<ConnectString>Data Source=db1</ConnectString>" in rdl


def test_no_prompt_invariant_survives_datasource_rewrite():
    """The rewrite must not disturb parameter defaults — every param keeps
    its =Nothing default (the never-prompt invariant)."""
    client = _flask_client()
    name = _first_sample_name()
    r = client.post(f"/api/convert-sample/{name}?shared_ds_path=/DS/X")
    rdl = r.get_json()["rdl_xml"]
    for pname, block in re.findall(
            r'<ReportParameter Name="([^"]+)">(.*?)</ReportParameter>',
            rdl, re.S):
        assert "=Nothing" in block or "DataSetReference" in block, (
            f"param {pname} lost its default after datasource rewrite")
