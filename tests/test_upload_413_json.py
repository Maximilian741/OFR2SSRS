"""Oversized uploads must fail as JSON, never as a raw HTML error page.

A request body over MAX_CONTENT_LENGTH used to come back as Werkzeug's stock
HTML 413 page. Every frontend caller does ``res.json()`` on the response, so
that page made the parse throw and the user saw only a generic failure toast.
The app now registers an ``errorhandler(413)`` that answers in the standard
``{"error": ...}`` JSON shape with an honest message: the configured cap, the
attempted upload size, and that truth PDFs/screenshots need not be uploaded
for conversion.

These tests drive the REAL Flask app through the full request cycle
(dispatch -> Werkzeug body-limit enforcement -> error handler -> response)
with a synthetic oversized multipart upload. The cap is shrunk to 1 MB for
the test so no test allocates hundreds of MB; enforcement and the handler
read the same config either way.
"""
from __future__ import annotations

import io
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

import app as flask_app  # noqa: E402

# 1 MB cap for the test -- same enforcement + handler path as any cap value.
_TEST_LIMIT = 1 * 1024 * 1024


@pytest.fixture()
def client():
    app = flask_app.app
    saved = app.config.get("MAX_CONTENT_LENGTH")
    app.config["MAX_CONTENT_LENGTH"] = _TEST_LIMIT
    app.config["TESTING"] = True
    try:
        with app.test_client() as c:
            yield c
    finally:
        app.config["MAX_CONTENT_LENGTH"] = saved


def _post_oversized(client, url: str, field: str, as_list: bool = False):
    """POST a synthetic multipart body twice the configured cap."""
    blob = b"x" * (2 * _TEST_LIMIT)
    part = (io.BytesIO(blob), "huge_synthetic.xml")
    data = {field: [part] if as_list else part}
    return client.post(url, data=data, content_type="multipart/form-data")


def _assert_json_413(r):
    assert r.status_code == 413
    assert r.mimetype == "application/json", (
        "413 must be JSON, got %r" % r.mimetype)
    body = r.get_data(as_text=True)
    assert "<html" not in body.lower(), "413 leaked an HTML error page"
    j = r.get_json()
    assert isinstance(j, dict) and j.get("error"), (
        "413 body must use the app's {'error': ...} shape")
    msg = j["error"]
    # Honest message: names the configured cap ...
    assert re.search(r"\b1 MB\b", msg), msg
    # ... reports the size that was actually sent ...
    assert re.search(r"upload was \d+(\.\d+)? MB", msg), msg
    # ... and says truth PDFs/screenshots aren't needed for conversion.
    assert "XML" in msg and ("PDF" in msg or "screenshot" in msg), msg
    return msg


def test_oversized_bundle_upload_returns_json_413(client):
    r = _post_oversized(client, "/api/convert-bundle", "files", as_list=True)
    _assert_json_413(r)


def test_oversized_single_convert_returns_json_413(client):
    r = _post_oversized(client, "/api/convert", "file")
    _assert_json_413(r)


def test_reported_size_matches_what_was_sent(client):
    """The '(this upload was N MB)' figure must be the real request size,
    not a canned number: a ~2 MB body must report ~2 MB."""
    r = _post_oversized(client, "/api/convert-bundle", "files", as_list=True)
    msg = _assert_json_413(r)
    m = re.search(r"upload was (\d+(?:\.\d+)?) MB", msg)
    assert m, msg
    reported = float(m.group(1))
    # 2 MB payload + multipart framing overhead; well under 3 MB.
    assert 1.9 <= reported <= 3.0, reported


def test_within_limit_upload_is_not_rejected_by_the_cap(client):
    """Control: a small (invalid-content) upload must NOT trip the 413 path --
    proves the handler only fires on genuinely oversized requests."""
    part = (io.BytesIO(b"<not-a-report/>"), "small.xml")
    r = client.post("/api/convert", data={"file": part},
                    content_type="multipart/form-data")
    assert r.status_code != 413
