"""Flask route smoke tests + per-session isolation (H4).

The 'most recent conversion' state is now keyed by a signed session cookie, so
two concurrent browsers must NOT see each other's report (the old single
process-global leaked one user's RDL to another on /api/download). Also covers
M2 -- the Flask layer previously had zero tests.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

import app as flask_app  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "source_of_truth" / "master_detail" / "source.xml"


@pytest.fixture
def client():
    flask_app.app.config["TESTING"] = True
    return flask_app.app.test_client()


def _convert(client, xml_bytes, name="r.xml"):
    return client.post(
        "/api/convert",
        data={"file": (io.BytesIO(xml_bytes), name)},
        content_type="multipart/form-data",
    )


def test_convert_happy_path(client):
    if not FIX.exists():
        pytest.skip("fixture missing")
    r = _convert(client, FIX.read_bytes())
    assert r.status_code == 200
    j = r.get_json()
    assert j.get("rdl_xml") and j.get("report")


def test_convert_no_file_returns_400(client):
    r = client.post("/api/convert", data={}, content_type="multipart/form-data")
    assert r.status_code == 400


def test_download_without_convert_returns_404(client):
    assert client.get("/api/download/rdl").status_code == 404


def test_session_isolation_between_clients():
    """Client A converts; a SECOND client (its own cookie jar) must NOT receive
    A's report on download -- it never converted, so it gets 404, not A's RDL."""
    if not FIX.exists():
        pytest.skip("fixture missing")
    a = flask_app.app.test_client()
    b = flask_app.app.test_client()
    assert _convert(a, FIX.read_bytes(), "A.xml").status_code == 200
    # A sees its own report.
    da = a.get("/api/download/rdl")
    assert da.status_code == 200
    # B never converted -> isolated session -> 404 (NOT A's report).
    assert b.get("/api/download/rdl").status_code == 404
