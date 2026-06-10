"""Batch migration + Migration Assessment + licensing seam."""
from __future__ import annotations

import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter.batch import (  # noqa: E402
    batch_convert, build_assessment_html, build_batch_zip)

FIXTURES = ROOT / "tests" / "fixtures"


def _items():
    out = []
    for p in [FIXTURES / "source_of_truth" / "letter" / "source.xml",
              FIXTURES / "subreports" / "SAMPLE_DRILLTHROUGH.xml"]:
        if p.exists():
            out.append((p.name, p.read_bytes()))
    if len(out) < 2:
        pytest.skip("fixtures missing")
    return out


def test_batch_converts_and_scores_each_report():
    batch = batch_convert(_items())
    rs = batch["results"]
    assert len(rs) == 2
    for r in rs:
        assert r["ok"], r.get("error")
        assert r["effort"] in ("automatic", "light-touch", "assisted", "manual")
        assert r["verdict"] == "READY"
        assert "<Report" in r["rdl_xml"]
        # the never-prompt invariant holds in batch mode too
        assert "<Value /></DefaultValue>" not in r["rdl_xml"]


def test_assessment_html_and_zip():
    batch = batch_convert(_items())
    html = build_assessment_html(batch)
    assert "Migration Assessment" in html
    for r in batch["results"]:
        assert r["name"] in html
    blob = build_batch_zip(batch)
    zf = zipfile.ZipFile(io.BytesIO(blob))
    names = set(zf.namelist())
    assert "ASSESSMENT.html" in names
    assert "assessment.json" in names
    rdls = [n for n in names if n.startswith("rdl/") and n.endswith(".rdl")]
    assert len(rdls) == 2
    meta = json.loads(zf.read("assessment.json"))
    assert len(meta["results"]) == 2
    assert all("rdl_xml" not in r for r in meta["results"])


def test_batch_limit_locks_overflow(monkeypatch):
    monkeypatch.setenv("O2S_BATCH_LIMIT", "1")
    batch = batch_convert(_items())
    assert len(batch["results"]) == 1
    assert len(batch["locked"]) == 1
    html = build_assessment_html(batch)
    assert "batch limit" in html


def test_batch_endpoint_e2e(monkeypatch):
    monkeypatch.delenv("O2S_BATCH_LIMIT", raising=False)
    from app import app
    app.config["TESTING"] = True
    with app.test_client() as client:
        data = {"files": [(io.BytesIO(b), n) for n, b in _items()],
                "shared_ds_path": "/DS/Batch"}
        r = client.post("/api/batch", data=data,
                        content_type="multipart/form-data")
        assert r.status_code == 200, r.get_data(as_text=True)[:300]
        j = r.get_json()
        assert len(j["results"]) == 2
        assert all("rdl_xml" not in row for row in j["results"])
        d = client.get("/api/download/batch-pack")
        assert d.status_code == 200
        zf = zipfile.ZipFile(io.BytesIO(d.data))
        rdl_name = next(n for n in zf.namelist() if n.endswith(".rdl"))
        rdl = zf.read(rdl_name).decode("utf-8")
        # session data source binding applied to batch artifacts too
        assert "<DataSourceReference>/DS/Batch</DataSourceReference>" in rdl


def test_rdf_only_bundle_gets_rwconverter_hint():
    from converter.ingest import convert_bundle
    out = convert_bundle([("LEGACY.rdf", b"\x00\x01binarygarbage")])
    assert out.get("error") == "no_convertible_artifacts"
    assert "rwconverter" in (out.get("rdf_hint") or "")
