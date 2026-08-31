"""Structure-driven dashboard: every sidebar section / tab / CTA renders
only when the CONVERTED REPORT's own structure carries the signal for it.

Two layers, both guarded here:

1. SIGNAL CONTRACT (HTTP/JSON): the convert payload must carry a machine
   signal for each conditionally rendered element -- one fixture where the
   signal fires, one where it does not. If a signal ever vanishes from the
   payload, the frontend gate silently fails open/closed; these tests make
   that loud.

2. GATE PRESENCE (static frontend assertions): the load-bearing gating
   code must exist in the shipped JS/HTML. The suite cannot execute the
   browser, so the contract is pinned at the source level -- the same
   pattern as test_preview_frontend_is_the_real_render.

All fixtures are synthetic and structural -- no client report names.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

FRONTEND = ROOT / "frontend"


@pytest.fixture()
def client():
    from app import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _app_js() -> str:
    return (FRONTEND / "static" / "js" / "app.js").read_text(encoding="utf-8")


def _demo_js() -> str:
    return (FRONTEND / "static" / "js" / "demo_mode.js").read_text(
        encoding="utf-8")


def _index_html() -> str:
    return (FRONTEND / "templates" / "index.html").read_text(encoding="utf-8")


def _convert(client, xml_bytes: bytes) -> dict:
    r = client.post("/api/convert",
                    data={"file": (io.BytesIO(xml_bytes), "sample.xml")},
                    content_type="multipart/form-data")
    assert r.status_code == 200, r.get_data(as_text=True)[:400]
    return r.get_json()


# ---------------------------------------------------------------- fixtures
# A plain single-query report: no bursting, no drill-through, full layout.
_PLAIN_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<report name="SAMPLE_PLAIN" DTDVersion="9.0.2.0.10">
  <data><dataSource name="Q1"><select><![CDATA[SELECT A FROM T]]></select>
    <group name="G1"><dataItem name="A" datatype="vchar2"/></group>
  </dataSource></data>
  <layout><section name="main"><body width="7" height="9">
    <field name="F_A" source="A"><geometryInfo x="0" y="0" width="3" height="0.2"/></field>
  </body></section></layout>
</report>
"""

# Structural bursting trigger (distribution parameters) -- same class of
# synthetic fixture as test_app_e2e.
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
  <layout><section name="main"><body width="8.0" height="10.0">
    <field name="F_NAME" x="0.5" y="1.2" width="7.0" height="0.3" source="Recipient_Name"/>
  </body></section></layout>
</report>
"""

# Partial artifact: data model saved without its layout (wild-corpus shape).
_DATA_MODEL_ONLY_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<report name="SAMPLE_DM" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q1"><select><![CDATA[SELECT A, B FROM T]]></select>
      <group name="G1"><dataItem name="A" datatype="vchar2"/>
        <dataItem name="B" datatype="number"/></group>
    </dataSource>
  </data>
</report>
"""


def _drillthrough_xml() -> bytes:
    p = ROOT / "tests" / "fixtures" / "subreports" / "SAMPLE_DRILLTHROUGH.xml"
    if not p.exists():
        pytest.skip("drill-through fixture missing")
    return p.read_bytes()


# ------------------------------------------------- 1. signal contract (HTTP)

def test_signal_bursting_fires_and_stays_quiet(client):
    """bursting.is_bursting: True on a distributing report, False on plain."""
    burst = _convert(client, _BURSTING_XML)
    assert (burst.get("bursting") or {}).get("is_bursting") is True

    plain = _convert(client, _PLAIN_XML)
    assert not (plain.get("bursting") or {}).get("is_bursting")


def test_signal_subreport_links_fire_and_stay_quiet(client):
    """subreport_links: non-empty on a drill-through parent, empty on plain."""
    parent = _convert(client, _drillthrough_xml())
    assert len(parent.get("subreport_links") or []) > 0

    plain = _convert(client, _PLAIN_XML)
    assert len(plain.get("subreport_links") or []) == 0


def test_signal_partial_artifact_marks_source_kind(client):
    """preflight.source_kind is THE gate for fidelity display + the CTA:
    present on a partial export, absent on a full report."""
    partial = _convert(client, _DATA_MODEL_ONLY_XML)
    pf = partial.get("preflight") or {}
    assert pf.get("source_kind") == "data_model_only"
    assert pf.get("source_kind_message")
    # The raw fidelity number may well be 1.0 here -- which is exactly why
    # the frontend must show a dash instead: the signal must be available.
    assert "fidelity_report" in partial

    full = _convert(client, _PLAIN_XML)
    assert (full.get("preflight") or {}).get("source_kind") is None


# --------------------------------------------- 2. gate presence (frontend)

def test_mockup_cta_is_gated_on_verdict_and_source_kind():
    """The download CTA renders only for READY/AMBER full reports; anything
    else gets the fix-first variant pointing at Validation."""
    js = _app_js()
    assert "function showMockupCTA(data)" in js
    # The gate used to read preflight's letter grade directly
    # ('pf.verdict === "READY" || pf.verdict === "AMBER"'). That grade said
    # READY on a report whose deploy checklist was quoting four errors out
    # of the same file, so an invitation to download appeared beside them.
    # It now asks conversionVerdict(), the one place a verdict is decided
    # for every surface -- a strictly narrower gate: no BLOCKER, no RED and
    # no validation errors.
    _cta = js[js.index("function showMockupCTA(data)"):]
    _cta = _cta[:_cta.index("\nfunction ")]
    assert "conversionVerdict(data)" in _cta and 'key === "ready"' in _cta, (
        "the download CTA no longer derives its verdict from "
        "conversionVerdict, so it can invite a download the banner beside "
        "it calls unsafe")
    assert "pf.source_kind" in js
    # fix-first variant exists and routes to Validation
    assert "cta-fix-first" in js
    assert "cta-open-validation" in js
    assert 'activateTab("validate")' in js
    # the old unconditional show is gone
    assert "if (cta) cta.hidden = false;" not in js


def test_live_data_copy_is_honest_about_the_sample_db():
    """Neither the how-it-works modal nor the tour may claim that Live Data
    runs against the user's database -- /api/run-query executes against the
    bundled read-only sample DB with missing tables stubbed empty."""
    js = _app_js()
    demo = _demo_js()
    assert "real queries against your database" not in js
    assert "sample database" in js
    assert "REAL queries against your database" not in demo
    assert "sample database" in demo
    # both say what the connection string is actually for
    assert js.count("baked into the downloaded RDL") >= 1
    assert demo.count("baked into the downloaded RDL") >= 1


def test_bursting_tab_is_structure_gated():
    """Bursting starts in the Advanced group and is promoted to a main tab
    only when bursting.is_bursting is true for the converted report."""
    html = _index_html()
    js = _app_js()
    import re
    m = re.search(r'<button class="tab ([a-z-]+)" data-tab="burst"', html)
    assert m and m.group(1) == "tab-adv", "burst tab must default to Advanced"
    assert 'burstBtn.classList.toggle("tab-main", !!burst.is_bursting)' in js
    assert 'burstBtn.classList.toggle("tab-adv", !burst.is_bursting)' in js
    # demotion never strands the user on an invisible tab
    assert 'state.activeTab === "burst"' in js


def test_subreports_tab_and_sidebar_are_link_gated():
    """The Sub-Reports tab is a main tab only when drill-through links were
    detected; the sidebar dropzone section renders only for detected links,
    and the manual + Add affordance lives in the tab."""
    html = _index_html()
    js = _app_js()
    import re
    m = re.search(r'<button class="tab ([a-z-]+)" data-tab="subreports"', html)
    assert m and m.group(1) == "tab-adv", "sub-reports tab defaults to Advanced"
    assert 'tabBtn.classList.toggle("tab-main", nDetected > 0)' in js
    assert 'tabBtn.classList.toggle("tab-adv", nDetected === 0)' in js
    # sidebar section: no manual add button in the template ...
    sidebar = html.split('id="subreport-section"')[1].split("</section>")[0]
    assert "subreport-add-manual" not in sidebar
    # ... it is rendered inside the Sub-Reports tab instead
    assert 'id="subreport-add-manual"' in js
    # sidebar hides when nothing was detected
    assert "if (!state.data || detected === 0)" in js
    # the personalized guide only renders for detected links
    assert "detected > 0 ? _subDeployGuideHTML(children)" in js


def test_fidelity_display_is_not_applicable_for_partial_artifacts():
    """Sidebar row + Extras card must show a dash, not a percentage, when
    preflight.source_kind marks the source as a partial artifact."""
    js = _app_js()
    # sidebar summary row gate
    assert '(data.preflight || {}).source_kind' in js
    # extras card takes the whole preflight and bails to a n/a card
    assert "function renderFidelityCard(host, fid, preflight)" in js
    assert "if (pf.source_kind) {" in js
    assert "Not applicable." in js


def test_tour_adapts_to_structure_gated_tabs():
    """The guided tour must adapt (not break) when Bursting/Sub-Reports are
    demoted: it reads the converted payload's signals, reveals the Advanced
    group while touring it, and restores the user's view state after."""
    demo = _demo_js()
    assert "d.bursting && d.bursting.is_bursting" in demo
    assert "d.subreport_links" in demo
    assert "setAdvanced(true)" in demo
    assert "setAdvanced(advWasOpen)" in demo
    # the tour listens for the conversion payload rather than poking at
    # module-private state that was never actually visible on window
    assert 'addEventListener("o2s:converted"' in demo
    assert "window.state?.data" not in demo
