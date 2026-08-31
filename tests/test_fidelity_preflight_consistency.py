"""One story across surfaces: fidelity headline vs the preflight surface.

A report could convert upload-READY while the fidelity card showed a sub-1.0
headline (e.g. display_coverage 67%) — the validation badge said "0 findings"
and the CTA invited download, while the fidelity card said attention was
needed. The contract locked here: whenever the fidelity score OR the
display-coverage axis is below the fidelity module's OWN needs-attention
threshold, the convert payload's preflight issues carry a visible
INFORMATIONAL finding (rule ``fidelity.needs_attention``) naming the measured
numbers — and the frontend counts it in the Validation badge. Severity is
never manufactured: the finding is INFO and the verdict is untouched.

Verified at the level the dashboard consumes: real HTTP through the Flask
app. Structural / general — the low-fidelity case is induced by stubbing the
fidelity measurement (so the test outlives converter improvements), never by
naming any customer report.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

import converter  # noqa: E402
from converter import convert  # noqa: E402
from converter.fidelity import ATTENTION_THRESHOLD  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "source_of_truth" / "master_detail" / "source.xml"


@pytest.fixture()
def client():
    from app import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _fixture_bytes() -> bytes:
    if not FIXTURE.exists():
        pytest.skip("fixture missing")
    return FIXTURE.read_bytes()


def _post_convert(client, xml: bytes):
    r = client.post(
        "/api/convert",
        data={"file": (io.BytesIO(xml), "source.xml")},
        content_type="multipart/form-data")
    assert r.status_code == 200, r.status_code
    return r.get_json()


def _info_findings(payload: dict):
    pf = payload.get("preflight") or {}
    return [i for i in (pf.get("issues") or [])
            if (i.get("severity") or "").upper() == "INFO"
            and (i.get("rule") or "").startswith("fidelity.")]


def _stub_low_display(parsed, rdl_xml):
    # Shape mirrors build_fidelity_report; the display axis is below the bar.
    return {"score": 1.0, "summary": "", "needs_attention": [],
            "categories": {"layout_fields": {"bound": 2, "total": 3,
                                             "display_coverage": 0.67}}}


def test_low_fidelity_http_response_carries_informational_finding(
        client, monkeypatch):
    """HTTP-level: a conversion whose fidelity headline is below the
    needs-attention threshold must ship an INFO preflight finding that names
    the coverage number — and must NOT degrade the verdict (informational,
    not manufactured severity)."""
    monkeypatch.setattr(converter, "build_fidelity_report", _stub_low_display)
    payload = _post_convert(client, _fixture_bytes())
    infos = _info_findings(payload)
    assert len(infos) == 1, (payload.get("preflight") or {}).get("issues")
    f = infos[0]
    assert f["rule"] == "fidelity.needs_attention"
    assert f["severity"] == "INFO"
    assert "67%" in f["message"]          # names the measured number
    # Verdict untouched: the same fixture converts READY without the stub,
    # and the informational finding must not change that.
    assert (payload.get("preflight") or {}).get("verdict") == "READY"


def test_low_binding_score_also_discloses(client, monkeypatch):
    """The OTHER axis (binding score) below the bar triggers the same
    disclosure — the finding names which axis is low."""
    def _stub(parsed, rdl_xml):
        return {"score": 0.5, "summary": "", "needs_attention": [],
                "categories": {"layout_fields": {"display_coverage": 1.0}}}
    monkeypatch.setattr(converter, "build_fidelity_report", _stub)
    payload = _post_convert(client, _fixture_bytes())
    infos = _info_findings(payload)
    assert len(infos) == 1
    assert "50%" in infos[0]["message"]


def test_full_coverage_report_carries_no_fidelity_finding(client):
    """HTTP-level, no stubbing: a genuinely full-coverage conversion ships
    ZERO fidelity findings — the badge stays honest at 0."""
    payload = _post_convert(client, _fixture_bytes())
    fr = payload.get("fidelity_report") or {}
    lf = (fr.get("categories") or {}).get("layout_fields") or {}
    assert fr.get("score") == 1.0
    assert lf.get("display_coverage") == 1.0
    assert _info_findings(payload) == []


def test_convert_level_disclosure_matches_fidelity_own_threshold(monkeypatch):
    """The converter judges against the fidelity module's OWN constant, not a
    duplicated magic number, and only full coverage is finding-free."""
    assert ATTENTION_THRESHOLD == 1.0
    # exactly AT the threshold -> no finding (strictly-below semantics)
    def _stub_at(parsed, rdl_xml):
        return {"score": ATTENTION_THRESHOLD, "summary": "",
                "needs_attention": [],
                "categories": {"layout_fields":
                               {"display_coverage": ATTENTION_THRESHOLD}}}
    monkeypatch.setattr(converter, "build_fidelity_report", _stub_at)
    out = convert(_fixture_bytes())
    assert _info_findings(out) == []


def test_frontend_badge_counts_the_informational_finding():
    """The Validation badge and the preflight detail list must actually
    COUNT and RENDER INFO findings — otherwise the backend disclosure is
    invisible and the two surfaces still disagree."""
    js = (ROOT / "frontend" / "static" / "js" / "app.js").read_text(
        encoding="utf-8")
    # the count exists
    assert 'const infos = count("INFO");' in js
    # the badge includes it
    assert 'setBadge("badge-validate", blockers + reds + vSerious + infos);' in js
    # the Validation detail list renders INFO rows
    assert '["BLOCKER", "RED", "AMBER", "INFO"]' in js
