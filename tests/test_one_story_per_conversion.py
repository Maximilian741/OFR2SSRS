"""One conversion, one story -- on every surface that describes it.

THE DEFECT, measured on the real page with a real report (ASBST_ACTIVITY_FEE,
page defaults, nothing changed). Four surfaces described the same conversion
at the same moment:

    sidebar banner    "Ready to upload -- All 89 calculations in this report
                       compiled successfully. Nothing is standing in your way."
    deploy strip      "Ready -- Ready to upload"
    status card       "Do this next: download the .rdl file and upload it"
    deploy checklist  "Read the Oracle SQL the converter wrote (4 errors, 6
                       warnings)" ... "SSRS will fail to bind it."

Nothing was hidden. But three of the four decided "is this deployable?" from
the PREFLIGHT list, and the fourth from the VALIDATION list, and those two
lists disagree. A reader who cannot lean over and ask a colleague which one
to believe is left with a green light over four errors.

Two rails here, and both are mutation-proved:

  1. THE CHECKLIST vs THE FILE. tests/test_conversion_honesty.py's own
     contradiction reader, imported unmodified, over every report on this
     machine -- the bundled samples, the non-Latin fixtures, and the agency
     corpus when it is present. Measured before this session's fix: 93
     contradictions over the 34 agency reports. After: 0.

  2. THE SURFACES vs EACH OTHER. The real page, in a real browser, driven
     with the payloads the real server returns: the banner, the deploy
     strip, the status card and the download CTA are read as four claims
     about one conversion and held against the checklist beside them.

Layer 1 always runs. Layer 2 needs Playwright + Chromium and skips without
them.
"""
from __future__ import annotations

import io
import os
import re
import socket
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "tests"))

# The repo's own contradiction reader, imported unmodified. If this file
# grew its own copy, the copy is what it would be measuring.
from test_conversion_honesty import checklist_contradictions  # noqa: E402


# ---------------------------------------------------------------- corpora
# Absent directories simply contribute no cases; the samples and the i18n
# fixtures ship with the repo, so the gate always has something to measure.
CORPORA = {
    "samples": ROOT / "samples" / "oracle",
    "i18n": ROOT / "tests" / "fixtures" / "i18n",
    "agency": Path(os.environ.get(
        "O2S_AGENCY_CORPUS",
        "C:/Users/maxca/Downloads/OneDrive_2026-08-06/Artifact Folders")),
}


def _corpus_files():
    out = []
    for leg, root in CORPORA.items():
        if not root.is_dir():
            continue
        for p in sorted(root.rglob("*.xml")):
            if any(part.startswith("_") for part in p.relative_to(root).parts[:-1]):
                continue
            out.append((leg, p))
    return out


@pytest.fixture(scope="module")
def payloads():
    """Every corpus report converted ONCE, by the real endpoint, and shared
    by both rails: this pass is the expensive part of the file."""
    from app import app
    app.config["TESTING"] = True
    files = _corpus_files()
    if not files:
        pytest.skip("no corpus on this machine")
    out = {}
    with app.test_client() as c:
        for leg, p in files:
            r = c.post("/api/convert",
                       data={"file": (io.BytesIO(p.read_bytes()), p.name)},
                       content_type="multipart/form-data")
            if r.status_code != 200:
                out["%s/%s" % (leg, p.name)] = {"_http": r.status_code}
                continue
            out["%s/%s" % (leg, p.name)] = r.get_json()
    return out


# ============================== rail 1: the checklist against the file
def test_no_report_gets_a_checklist_that_describes_a_different_file(payloads):
    """The 34-report gate. Before this session's fix this reported 93
    contradictions; the four kinds it found are all fixed at the source:
    the T-SQL wording that leaked into Oracle checklists (backend/app.py's
    engine-word net), and three shapes the reader itself could not tell
    apart -- see test_conversion_honesty.checklist_contradictions."""
    bad = {}
    for name, payload in sorted(payloads.items()):
        if payload.get("_http") or payload.get("error"):
            continue
        problems = checklist_contradictions(
            payload.get("deployment_checklist") or [],
            payload.get("rdl_xml") or "")
        if problems:
            bad[name] = problems
    assert not bad, (
        "%d report(s) ship a checklist that describes a different file:\n%s"
        % (len(bad), "\n".join(
            "  %s\n    %s" % (n, "\n    ".join("%s: %s" % p for p in v))
            for n, v in sorted(bad.items()))))


def test_the_verdict_the_app_shows_is_not_softer_than_the_checklist(payloads):
    """Rail 1's other half, without a browser: for every report, the verdict
    the surfaces render (the same rule conversionVerdict applies in the
    frontend) may not call a file ready while its own deploy checklist is
    quoting errors out of it."""
    soft = []
    for name, payload in sorted(payloads.items()):
        if payload.get("_http") or payload.get("error"):
            continue
        v = _verdict_of(payload)
        errors = _checklist_errors(payload)
        if v == "ready" and errors:
            soft.append((name, errors))
    assert not soft, (
        "these reports are called ready while their checklist reports "
        "errors in the same file: %r" % soft)


# The frontend rule, restated once for the no-browser rail. Layer 2 checks
# that the page really applies it; this catches a divergence on machines
# with no Chromium at all.
def _verdict_of(payload) -> str:
    issues = (payload.get("preflight") or {}).get("issues") or []
    blockers = sum(1 for i in issues
                   if str(i.get("severity", "")).upper() == "BLOCKER")
    reds = sum(1 for i in issues
               if str(i.get("severity", "")).upper() == "RED")
    if blockers:
        return "blocker"
    return "runtime" if (reds or _checklist_errors(payload)) else "ready"


def _checklist_errors(payload) -> int:
    """The errors the deploy checklist's SQL step counts -- the same two
    lists backend/app.py's _deploy_step_sql adds up."""
    both = ((payload.get("validation_issues") or [])
            + (payload.get("rdl_issues") or []))
    return sum(1 for i in both
               if str(i.get("severity", "")).lower() == "error")


# ============================== rail 2: the surfaces against each other
def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def live_url():
    pytest.importorskip("playwright.sync_api")
    from werkzeug.serving import make_server
    from app import app

    port = _free_port()
    srv = make_server("127.0.0.1", port, app, threaded=True)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield "http://127.0.0.1:%d/" % port
    finally:
        srv.shutdown()
        t.join(timeout=5)


@pytest.fixture(scope="module")
def browser():
    sp = pytest.importorskip("playwright.sync_api")
    with sp.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:                       # noqa: BLE001
            pytest.skip("chromium unavailable: %s" % exc)
        yield b
        b.close()


@pytest.fixture()
def page(browser, live_url):
    pg = browser.new_page(viewport={"width": 1280, "height": 900})
    pg.route("**/*", lambda route: route.continue_()
             if "127.0.0.1" in route.request.url else route.abort())
    pg.goto(live_url, wait_until="domcontentloaded")
    pg.wait_for_function("() => !!document.querySelector('.tab')")
    pg.wait_for_timeout(200)
    yield pg
    pg.close()


# Render the four surfaces from ONE payload and read back what each of them
# now claims. Nothing here decides anything: the page does.
SURFACES = """(payload) => {
  renderPreflight(payload);
  renderDeployStatus(payload);
  reportConversionStatus(payload);
  showMockupCTA(payload);
  const flat = n => n ? (n.innerText || '').replace(/\\s+/g, ' ').trim() : '';
  const b = document.getElementById('preflight-banner');
  const d = document.getElementById('deploy-status');
  const cta = document.getElementById('mockup-cta');
  const card = document.querySelector('#activity-now .activity-card')
            || document.querySelector('#activity-alert .activity-card');
  return {
    banner: {verdict: b ? b.getAttribute('data-verdict') : null, text: flat(b)},
    strip: {verdict: d ? d.getAttribute('data-verdict') : null, text: flat(d)},
    cta: {fixFirst: cta ? cta.classList.contains('cta-fix-first') : null,
          text: flat(cta)},
    card: {state: card ? card.getAttribute('data-state') : null,
           text: flat(card)},
  };
}"""

# The download invitation, word for word from showMockupCTA and from
# reportConversionStatus. Matching on these is matching on a CLAIM, not on
# prose: they are the two places the app tells the operator to ship the file.
_INVITES = ("Download the .rdl file, then upload it",
            "Do this next: download the .rdl file and upload it")


def _slim(payload):
    """Only what the four surfaces read. The RDL and the mockup are hundreds
    of KB and none of these functions look at them."""
    return {k: payload.get(k) for k in
            ("report", "preflight", "validation_issues", "rdl_issues",
             "deployment_checklist", "fidelity_report", "subreport_links",
             "oracle_xml")}


def _disagreements(seen, payload):
    """What one surface says that another denies, about one conversion."""
    out = []
    verdict = seen["banner"]["verdict"]
    if verdict != seen["strip"]["verdict"]:
        out.append("banner says %r, the deploy strip says %r"
                   % (verdict, seen["strip"]["verdict"]))
    if seen["cta"]["fixFirst"] is not None:
        invites = not seen["cta"]["fixFirst"]
        if invites != (verdict == "ready"):
            out.append("banner verdict is %r but the preview bar %s"
                       % (verdict,
                          "invites a download" if invites
                          else "says fix this first"))
    said = seen["card"]["text"]
    if verdict != "ready" and any(w in said for w in _INVITES):
        out.append("the banner says %r and the status card still tells the "
                   "operator to download and upload it" % verdict)
    # ...and against the checklist beside them.
    errors = _checklist_errors(payload)
    if errors and verdict == "ready":
        out.append("the checklist reports %d error(s) in this file and the "
                   "banner calls it ready" % errors)
    unfinished = [str(s.get("title") or "").strip()
                  for s in (payload.get("deployment_checklist") or [])
                  if s.get("status") in ("todo", "caution")]
    if verdict == "ready":
        for title in unfinished:
            if title and title not in seen["banner"]["text"]:
                out.append("the checklist still needs a person for %r and "
                           "the banner does not say so" % title)
    return out


# The screen that was measured, reconstructed: preflight found nothing, the
# expression compiler passed 89 of 89, and the validator found four errors
# that the checklist is quoting.
MEASURED_CONTRADICTION = {
    "report": {"name": "ASBST_ACTIVITY_FEE", "queries": [{}, {}],
               "parameters": [{}, {}], "formulas": [{}]},
    "preflight": {"verdict": "READY", "issues": [],
                  "expr_verify": {"available": True,
                                  "summary": {"failed": 0, "total": 89}}},
    "validation_issues": [
        {"severity": "error", "rule": "report.param_unbound",
         "scope": "Q_Perm_Issue", "line": 29,
         "message": "Query references :P_END_DTGROUP but the report has no "
                    "matching parameter declaration; SSRS will fail to bind "
                    "it."}] * 4,
    "rdl_issues": [],
    "deployment_checklist": [
        {"step": 1, "status": "manual",
         "title": "Open the .rdl in SSRS Report Builder", "body_md": ""},
        {"step": 2, "status": "todo",
         "title": "Point the report at your data source", "body_md": ""},
        {"step": 3, "status": "caution",
         "title": "Read the Oracle SQL the converter wrote (4 errors, 6 "
                  "warnings)",
         "body_md": "SSRS will fail to bind it."},
    ],
}


def test_the_agreement_reader_can_fail(page):
    """Mutation proof. Put the OLD rule back -- every surface deciding from
    the preflight list alone -- replay the measured payload, and confirm the
    reader reports the contradiction. A reader that cannot see this one is
    not measuring anything."""
    seen = page.evaluate(
        """(payload) => {
             window.conversionVerdict = (d) => {      // the pre-fix rule
               const iss = ((d.preflight || {}).issues) || [];
               const up = s => String(s || '').toUpperCase();
               const b = iss.filter(i => up(i.severity) === 'BLOCKER').length;
               const r = iss.filter(i => up(i.severity) === 'RED').length;
               return {key: b ? 'blocker' : (r ? 'runtime' : 'ready'),
                       blockers: b, reds: r, sqlErrors: 0, runtime: r,
                       needsHuman: []};
             };
             return (%s)(payload);
           }""" % SURFACES, MEASURED_CONTRADICTION)
    bad = _disagreements(seen, MEASURED_CONTRADICTION)
    assert bad, (
        "with the pre-fix rule restored the reader saw no disagreement, so "
        "it cannot detect the defect it exists for: %r" % seen)
    assert any("calls it ready" in b for b in bad), bad
    page.reload()          # leave the page's own rule in place for the rest


def test_every_surface_tells_one_story_on_the_measured_report(page):
    """The same payload through the page as it ships now."""
    seen = page.evaluate("(payload) => (%s)(payload)" % SURFACES,
                         MEASURED_CONTRADICTION)
    assert not _disagreements(seen, MEASURED_CONTRADICTION), seen
    assert seen["banner"]["verdict"] == "runtime", (
        "four errors that say SSRS will fail to bind, and the banner still "
        "reads %r: %r" % (seen["banner"]["verdict"], seen["banner"]["text"]))


def test_a_clean_conversion_is_still_called_ready(page):
    """The rule may not cry wolf: with nothing wrong and nothing unfinished,
    every surface still says so."""
    clean = {"report": {"name": "R", "queries": [{}], "parameters": [{}],
                        "formulas": [{}]},
             "preflight": {"verdict": "READY", "issues": []},
             "validation_issues": [], "rdl_issues": [],
             "deployment_checklist": [
                 {"step": 1, "status": "auto", "title": "Data source - "
                  "already pointed at /Data Sources/X", "body_md": ""}]}
    seen = page.evaluate("(payload) => (%s)(payload)" % SURFACES, clean)
    assert not _disagreements(seen, clean), seen
    assert seen["banner"]["verdict"] == "ready", seen
    assert seen["cta"]["fixFirst"] is False, seen


def test_every_surface_tells_one_story_on_every_corpus_report(page, payloads):
    """The whole roster, through the real page, one payload at a time."""
    bad = {}
    for name, payload in sorted(payloads.items()):
        if payload.get("_http") or payload.get("error"):
            continue
        slim = _slim(payload)
        seen = page.evaluate("(payload) => (%s)(payload)" % SURFACES, slim)
        problems = _disagreements(seen, slim)
        if problems:
            bad[name] = {"problems": problems, "seen": seen}
    assert not bad, (
        "%d report(s) describe themselves two different ways at once:\n%s"
        % (len(bad), "\n".join("  %s: %s" % (n, v["problems"])
                               for n, v in sorted(bad.items()))))


def test_the_verdict_is_decided_in_exactly_one_place():
    """Structural. Three renderers used to count BLOCKER/RED for
    themselves; the fourth read preflight's letter grade. Whichever way the
    wording moves next, they have to keep asking the same function."""
    js = (ROOT / "frontend" / "static" / "js" / "app.js").read_text(
        encoding="utf-8")
    for fn in ("renderPreflight", "renderDeployStatus",
               "reportConversionStatus", "showMockupCTA"):
        start = js.index("function %s(" % fn)
        body = js[start:js.index("\nfunction ", start + 10)]
        assert "conversionVerdict(" in body, (
            "%s does not ask conversionVerdict() for the verdict it renders"
            % fn)
        # ...and does not decide one for itself.
        counted = re.findall(r'===\s*"(?:BLOCKER|RED)"', body)
        assert not counted, (
            "%s counts preflight severities itself again (%r), so it can "
            "drift away from every other surface" % (fn, counted))
    assert 'pf.verdict === "READY"' not in js, (
        "a surface is reading preflight's letter grade directly again; that "
        "grade said READY on a report whose checklist quoted four errors")
