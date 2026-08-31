"""Status is visible, persistent, and never carried by colour alone.

The person who runs this converter every day is deaf. That removes a whole
channel other apps lean on without noticing, and it makes two ordinary UI
habits into blockers:

  * a message that expires -- the toast that said WHY a conversion failed was
    gone in three seconds, and the only thing left on screen afterwards was
    the word "Error" in an 11px pill, with the reason in a title= tooltip on
    a <span> that cannot be focused;
  * a message that is only a colour -- a BLOCKER verdict and a READY verdict
    painted the same, "Downloaded burst_pack.zip." and "Download failed:"
    were the same grey line, and the fidelity score printed 73% in exactly
    the ink it printed 100%.

So this file measures three things, on the page the server really sends and
in the DOM a real browser really builds:

  1. THE REGIONS EXIST, with the right politeness, as ON-SCREEN text. A
     polite region for progress, an assertive one for failures, and a
     durable log of outcomes. sr-only would satisfy a screen reader and fail
     the person this app is for.
  2. A FAILURE SURVIVES THE TOAST. The failure is simulated for real (the
     server is made unreachable mid-click), and the page is then read AFTER
     the toast has expired. The reader that does the checking is itself
     mutation-proved against a page where the reason lives only in a toast
     and a title=, so it cannot certify the very defect it exists to catch.
  3. NOTHING IS SAID WITH COLOUR ALONE. Every status carrier is read as
     TEXT: a glyph from the shared vocabulary plus words. The verdict
     surfaces are driven through all three verdicts and their glyph-and-word
     renderings compared, so "they only differ by tint" fails here.

Layers 1 and 2 always run. Layer 3 needs Playwright + Chromium and skips
without them.
"""
from __future__ import annotations

import json
import re
import socket
import sys
import threading
from pathlib import Path

import pytest
from lxml import html as lxml_html

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

FRONTEND = ROOT / "frontend"
APP_JS = FRONTEND / "static" / "js" / "app.js"
STYLE = FRONTEND / "static" / "css" / "style.css"

# The five states the app speaks in. Every one of them is a GLYPH plus a
# WORD; the tint is the third signal, never the first.
VOCAB_GLYPHS = {"○", "⟳", "✓", "⚠", "✕", "ℹ"}


def _js() -> str:
    return APP_JS.read_text(encoding="utf-8")


def _css() -> str:
    return STYLE.read_text(encoding="utf-8")


# ======================================================== layer 1: markup
@pytest.fixture(scope="module")
def served_html() -> str:
    from app import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        r = c.get("/")
        assert r.status_code == 200
        return r.get_data(as_text=True)


@pytest.fixture(scope="module")
def dom(served_html):
    return lxml_html.fromstring(served_html)


def _one(dom, xpath, what):
    found = dom.xpath(xpath)
    assert len(found) == 1, "expected exactly one %s, found %d" % (what, len(found))
    return found[0]


def test_progress_has_a_polite_live_region_on_screen(dom):
    node = _one(dom, "//*[@id='activity-now']", "#activity-now")
    assert node.get("role") == "status", (
        "progress must be announced; role=status is the polite one")
    assert node.get("aria-live") == "polite", node.get("aria-live")
    assert node.get("hidden") is None, (
        "a live region toggled with hidden is out of the accessibility tree "
        "when the change happens, so the change is announced to nobody")
    assert "sr-only" not in (node.get("class") or ""), (
        "the operator is DEAF: a screen-reader-only status region is not a "
        "status region for them. It has to be visible text.")


def test_failures_have_an_assertive_live_region_on_screen(dom):
    node = _one(dom, "//*[@id='activity-alert']", "#activity-alert")
    assert node.get("role") == "alert"
    assert node.get("aria-live") == "assertive", (
        "a failure interrupts; polite would queue it behind whatever else "
        "is being read")
    assert node.get("hidden") is None
    assert "sr-only" not in (node.get("class") or "")


def test_the_two_regions_are_distinct_and_live_in_the_main_column(dom):
    main = _one(dom, "//main", "<main>")
    ids = {n.get("id") for n in main.xpath(".//*[@id='activity-now']"
                                           " | .//*[@id='activity-alert']")}
    assert ids == {"activity-now", "activity-alert"}, (
        "both status regions must sit in the main column, next to the report "
        "views the user is looking at -- not in a corner of the top bar: %s"
        % ids)


def test_the_strip_is_labelled_and_carries_a_durable_record(dom):
    section = _one(dom, "//*[@id='activity']", "the status strip")
    title = section.xpath(".//h2[@id='activity-title']")
    assert title and "".join(title[0].itertext()).strip(), (
        "the strip needs a visible heading, both to name the landmark and so "
        "a sighted user knows what they are looking at")
    assert section.get("aria-labelledby") == "activity-title"
    log = _one(dom, "//ol[@id='activity-log']", "#activity-log")
    assert log.get("aria-live") is None, (
        "the log is HISTORY. Making it live re-announces everything that "
        "already happened every time a line is added.")


def test_the_toast_is_not_the_only_carrier_in_the_markup(dom):
    toast = _one(dom, "//*[@id='toast']", "#toast")
    assert toast.get("role") is None and toast.get("aria-live") is None, (
        "the toast must not be the app's live region: it deletes its own "
        "content after three seconds, which is the defect this file exists "
        "for. Make it a courtesy copy of the status strip.")


# ======================================================== layer 2: source
def _states_from_source() -> dict:
    js = _js()
    m = re.search(r"const STATUS_STATES = \{(.*?)\n\};", js, re.S)
    assert m, "STATUS_STATES is the app's whole status vocabulary; it is gone"
    out = {}
    for name, glyph, word in re.findall(
            r'(\w+)\s*:\s*\{\s*glyph:\s*"([^"]*)"\s*,\s*word:\s*"([^"]*)"', m.group(1)):
        out[name] = (glyph, word)
    return out


def test_every_state_is_a_glyph_and_a_word_and_they_are_all_different():
    states = _states_from_source()
    assert len(states) >= 4, states
    for name, (glyph, word) in states.items():
        assert glyph.strip(), "%s has no glyph, so it can only be told apart by colour" % name
        assert word.strip(), "%s has no word, so it can only be told apart by a glyph" % name
    glyphs = [g for g, _ in states.values()]
    words = [w for _, w in states.values()]
    assert len(set(glyphs)) == len(glyphs), (
        "two states share a glyph, so colour is the only thing separating "
        "them: %s" % glyphs)
    assert len(set(words)) == len(words), words


def test_the_toast_writes_through_to_the_persistent_surface():
    js = _js()
    body = js[js.index("function toast(msg, kind) {"):]
    body = body[:body.index("\n}\n")]
    assert "statusFail" in body and "statusRecord" in body, (
        "toast() must copy what it says into the status strip; otherwise the "
        "three-second timer takes the information away with the toast")


def test_the_status_pill_writes_through_too():
    js = _js()
    body = js[js.index("function setStatus(text, kind, detail) {"):]
    body = body[:body.index("\nfunction _mirrorStatus")]
    assert "_mirrorStatus(" in body, (
        "the pill is 11px in a corner and its reason is a tooltip. It must "
        "not be the only place a reason lands.")
    assert "pill-glyph" in body, (
        "the pill state must carry a glyph, not just a coloured dot")


def test_no_user_instruction_is_left_in_the_browser_console():
    """The .rdf path used to print the exact export command to DevTools and
    tell the user to look there. A report writer cannot be asked to do that."""
    js = _js()
    assert "logged to the browser console" not in js, (
        "an actionable step that only exists in DevTools is not actionable")
    rdf = js[js.index("if (json.rdf_hint) {"):]
    rdf = rdf[:rdf.index("} else {")]
    assert "statusFail(" in rdf and "cmd:" in rdf, (
        "the rwconverter command must reach the screen as selectable text")


def test_every_upload_shows_real_progress_while_bytes_are_moving():
    js = _js()
    assert "function _xhrPostForm" in js, (
        "fetch() cannot report upload progress; the byte count is the ONE "
        "honest percentage this app has")
    for fn, url in (("async function uploadFile", "/api/convert"),
                    ("async function uploadBundle", "/api/convert-bundle"),
                    ("function wireBatch", "/api/batch")):
        body = js[js.index(fn):]
        body = body[:body.index("\n}\n")]
        assert "uploadProgressReporter(" in body, (
            "%s posts to %s without reporting progress, so a big upload is a "
            "frozen screen" % (fn, url))


def test_an_unknown_duration_is_admitted_rather_than_faked():
    js = _js()
    body = js[js.index("function _progressBlock(p) {"):]
    body = body[:body.index("\n}\n")]
    assert "aria-valuenow" in body
    assert "if (known) {" in body, (
        "aria-valuenow must be set ONLY when the fraction is known; that "
        "absence is how ARIA spells 'indeterminate'")
    assert "no percentage" in body, (
        "an indeterminate bar has to SAY it has no percentage, in words, "
        "beside itself")


def test_the_spinner_is_never_the_only_signal_and_stops_for_reduced_motion():
    js = _js()
    assert 'class="render-spinner" aria-hidden="true"' in js, (
        "a spinner with no accessible text is a bare spinner")
    assert "render-loading-text" in js, "the loading state needs words"

    css = _css()
    blocks = re.findall(r"@media \(prefers-reduced-motion: reduce\)\s*\{(.*?)\n\}",
                        css, re.S)
    assert blocks, "the stylesheet has no reduced-motion rules at all"
    joined = "\n".join(blocks)
    assert ".render-spinner" in joined and "animation:none" in joined, (
        "the render spinner runs animation-iteration-count:infinite; under "
        "prefers-reduced-motion it must stop and leave the words")


def test_no_infinite_animation_escapes_the_reduced_motion_guard():
    """Generalised: every looping animation in the stylesheet needs a rule
    that switches it off, or the next one added silently regresses this."""
    css = _css()
    guard = "\n".join(re.findall(
        r"@media \(prefers-reduced-motion: reduce\)\s*\{(.*?)\n\}", css, re.S))
    body = re.sub(r"@media \(prefers-reduced-motion: reduce\)\s*\{.*?\n\}",
                  "", css, flags=re.S)
    offenders = []
    for m in re.finditer(r"([^{}]+)\{([^{}]*animation:[^{};]*infinite[^{};]*;[^{}]*)\}",
                         body, re.S):
        selector = " ".join(m.group(1).split()).lstrip("}").strip()
        head = selector.split(",")[0].strip()
        key = re.split(r"[\s>:\[]", head)[0]
        if key and key not in guard:
            offenders.append(selector)
    assert not offenders, (
        "looping animations with no prefers-reduced-motion escape: %s"
        % offenders)


# ======================================================== layer 3: live DOM
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
        except Exception as exc:
            pytest.skip("chromium unavailable: %s" % exc)
        yield b
        b.close()


@pytest.fixture()
def page(browser, live_url):
    pg = browser.new_page(viewport={"width": 1280, "height": 900})
    # Locked-down target machine: nothing but the app itself may load.
    pg.route("**/*", lambda route: route.continue_()
             if "127.0.0.1" in route.request.url else route.abort())
    pg.goto(live_url, wait_until="domcontentloaded")
    pg.wait_for_function("() => !!document.querySelector('.tab')")
    pg.wait_for_timeout(250)
    yield pg
    pg.close()


# --- the reader, and the proof it can fail --------------------------------
# Returns every scrap of status text a user could still READ right now:
# on-screen, not the toast, not a tooltip. If a reason is not in here it did
# not survive.
PERSISTENT_TEXT = """() => {
  const bad = new Set(['toast']);
  const out = [];
  document.querySelectorAll(
    '#activity, [role=status], [role=alert], .inline-status, .activity-card'
  ).forEach(n => {
    if (bad.has(n.id)) return;
    if (n.closest('#toast')) return;
    const cs = getComputedStyle(n);
    if (cs.display === 'none' || cs.visibility === 'hidden') return;
    const r = n.getBoundingClientRect();
    if (!(r.width || r.height)) return;
    out.push(n.innerText || '');
  });
  return out.join('\\n');
}"""

TOAST_ONLY_PAGE = """
<!doctype html><title>toast only</title>
<style>.toast{position:fixed;bottom:20px}</style>
<span id="status-pill" title="Can't reach the converter"
      aria-label="Error: Can't reach the converter">Error</span>
<div id="toast" class="toast">Can't reach the converter</div>
"""


def test_the_persistence_reader_reports_nothing_when_the_reason_is_a_toast(browser):
    """Mutation proof. Rebuild the exact defect -- the reason living only in a
    toast and a title= -- and check the reader says the page has no
    persistent explanation. A reader that cannot fail proves nothing."""
    pg = browser.new_page()
    try:
        pg.set_content(TOAST_ONLY_PAGE)
        text = pg.evaluate(PERSISTENT_TEXT)
        assert "reach the converter" not in text, (
            "the reader counted a toast (or a title= tooltip) as a persistent "
            "explanation, so every assertion built on it is worthless. It "
            "returned: %r" % text)
    finally:
        pg.close()


def test_the_regions_are_really_on_screen_and_really_live(page):
    meta = page.evaluate("""() => ['activity-now','activity-alert'].map(id => {
      const n = document.getElementById(id), cs = getComputedStyle(n);
      return {id, role: n.getAttribute('role'), live: n.getAttribute('aria-live'),
              display: cs.display, visibility: cs.visibility,
              clipped: cs.clip !== 'auto' || cs.position === 'absolute'};
    })""")
    by = {m["id"]: m for m in meta}
    assert by["activity-now"]["role"] == "status"
    assert by["activity-now"]["live"] == "polite"
    assert by["activity-alert"]["role"] == "alert"
    assert by["activity-alert"]["live"] == "assertive"
    for m in meta:
        assert m["display"] != "none", (
            "%s is display:none, so it is not in the accessibility tree when "
            "its content changes and nothing gets announced" % m["id"])
        assert not m["clipped"], (
            "%s is visually hidden; the deaf operator reads this by eye" % m["id"])


def _break_the_network(page):
    """Simulate the real failure: the converter stops answering mid-click."""
    page.evaluate("""() => { window.fetch = () =>
        Promise.reject(new TypeError('Failed to fetch')); }""")


def test_a_failure_leaves_an_explanation_that_outlives_the_toast(page):
    if page.locator(".sample-chip").count() == 0:
        pytest.skip("no bundled sample to convert")
    _break_the_network(page)
    page.click(".sample-chip")
    page.wait_for_function(
        "() => (document.getElementById('activity-alert').textContent||'').length > 20",
        timeout=15000)
    # Past the toast's own three-second life, plus slack.
    page.wait_for_timeout(4200)
    assert page.evaluate("() => document.getElementById('toast').hidden"), (
        "the toast is meant to expire -- that is the whole point of the test")

    text = page.evaluate(PERSISTENT_TEXT)
    low = text.lower()
    assert "reach the converter" in low or "run.bat" in low, (
        "WHAT/WHY did not survive the toast. On screen: %r" % text[:400])
    assert "do this next" in low, (
        "the failure never says what to do about it. On screen: %r" % text[:400])
    assert page.evaluate(
        "() => document.getElementById('activity-alert')"
        ".querySelector('.activity-card') !== null"), (
        "the explanation must live in the assertive region, not somewhere a "
        "screen reader will never be told about")


def test_a_failure_says_the_report_on_screen_is_the_old_one(page):
    """The measured defect: after a failed conversion the main column still
    showed the PREVIOUS report, so the screen looked healthy."""
    if page.locator(".sample-chip").count() == 0:
        pytest.skip("no bundled sample to convert")
    page.click(".sample-chip")
    page.wait_for_function(
        "() => document.querySelectorAll('#mockup-host *').length > 0",
        timeout=60000)
    page.wait_for_timeout(800)
    name = page.evaluate("() => document.getElementById('sum-name').textContent")
    _break_the_network(page)
    page.click(".sample-chip")
    page.wait_for_function(
        "() => (document.getElementById('activity-alert').textContent||'').length > 20",
        timeout=15000)
    page.wait_for_timeout(300)
    text = page.evaluate(PERSISTENT_TEXT)
    assert name and name in text, (
        "the failure never says the views below are the earlier report %r; "
        "on screen: %r" % (name, text[:400]))
    assert "not updated" in text.lower() or "previous" in text.lower(), text[:400]
    assert page.evaluate("() => document.body.classList.contains('o2s-stale')"), (
        "nothing marks the stale content for the eye")


def test_the_failure_stays_until_it_is_dismissed(page):
    if page.locator(".sample-chip").count() == 0:
        pytest.skip("no bundled sample to convert")
    _break_the_network(page)
    page.click(".sample-chip")
    page.wait_for_selector("#activity-alert .activity-dismiss", timeout=15000)
    page.wait_for_timeout(4200)
    assert page.locator("#activity-alert .activity-card").count() == 1, (
        "the failure card expired on its own; nothing here may be on a timer")
    page.click("#activity-alert .activity-dismiss")
    page.wait_for_timeout(200)
    assert page.locator("#activity-alert .activity-card").count() == 0
    # Dismissing acknowledges it; it must not erase the record.
    log = page.evaluate("() => document.getElementById('activity-log').innerText")
    assert "failed" in log.lower(), (
        "the durable record lost the failure the moment it was dismissed: %r"
        % log[:300])
    assert not page.evaluate("() => document.body.classList.contains('o2s-stale')")


def test_a_new_run_supersedes_the_old_failure(page):
    if page.locator(".sample-chip").count() == 0:
        pytest.skip("no bundled sample to convert")
    _break_the_network(page)
    page.click(".sample-chip")
    page.wait_for_selector("#activity-alert .activity-card", timeout=15000)
    page.reload(wait_until="domcontentloaded")     # restore a working fetch
    page.wait_for_function("() => !!document.querySelector('.tab')")
    page.click(".sample-chip")
    page.wait_for_function(
        "() => document.querySelectorAll('#mockup-host *').length > 0",
        timeout=60000)
    page.wait_for_timeout(600)
    assert page.locator("#activity-alert .activity-card").count() == 0, (
        "a successful run must clear the previous failure, or the screen "
        "shows two contradictory stories at once")
    now = page.evaluate("() => document.getElementById('activity-now').innerText")
    assert "Converted" in now, now[:200]
    assert "next" in now.lower(), (
        "a finished conversion has to say what to do with it: %r" % now[:300])


def test_progress_is_determinate_while_bytes_move_and_honest_afterwards(page):
    """Drive the app's OWN upload reporter -- the function every upload path
    hands to the transport -- and read what the user would see."""
    first = page.evaluate("""() => {
      const report = uploadProgressReporter('Converting', 1000);
      statusBegin('BIG_REPORT.xml', 'Converting');
      report(250, 1000);
      const bar = document.querySelector('#activity-now [role=progressbar]');
      return {det: bar.getAttribute('data-determinate'),
              now: bar.getAttribute('aria-valuenow'),
              vtext: bar.getAttribute('aria-valuetext'),
              width: bar.querySelector('.activity-bar-fill').style.width,
              label: document.querySelector('#activity-now .activity-progress-label').innerText};
    }""")
    assert first["det"] == "yes", first
    assert first["now"] == "25", first
    assert first["width"] == "25%", first
    assert "25%" in first["label"], (
        "the percentage must be in WORDS beside the bar, not only in the "
        "bar's geometry: %r" % first["label"])

    second = page.evaluate("""() => {
      uploadProgressReporter('Converting', 1000)(null, null);
      const bar = document.querySelector('#activity-now [role=progressbar]');
      return {det: bar.getAttribute('data-determinate'),
              now: bar.getAttribute('aria-valuenow'),
              label: document.querySelector('#activity-now .activity-progress-label').innerText};
    }""")
    assert second["det"] == "no", second
    assert second["now"] is None, (
        "once the bytes are gone nobody knows the fraction; publishing a "
        "number there is a lie the user cannot check")
    assert "no percentage" in second["label"].lower(), second["label"]


def test_a_real_upload_wires_its_progress_into_the_page(page):
    """Not the reporter in isolation: replace XMLHttpRequest, click the real
    control, and fire the progress event the browser would have fired."""
    page.evaluate("""() => {
      window.__xhr = null;
      class FakeXHR {
        constructor() {
          this.upload = { _h: {},
            addEventListener: (k, f) => { this.upload._h[k] = f; } };
          this._h = {};
          window.__xhr = this;
        }
        open() {} setRequestHeader() {}
        addEventListener(k, f) { this._h[k] = f; }
        send() {}
      }
      window.XMLHttpRequest = FakeXHR;
    }""")
    page.set_input_files("#batch-input", [{
        "name": "REPORT.xml", "mimeType": "text/xml",
        "buffer": b"<report/>" * 400}])
    page.click("#batch-run")
    page.wait_for_timeout(300)
    seen = page.evaluate("""() => {
      const h = window.__xhr && window.__xhr.upload._h.progress;
      if (!h) return {wired: false};
      h({lengthComputable: true, loaded: 600, total: 1200});
      const bar = document.querySelector('#activity-now [role=progressbar]');
      return {wired: true, det: bar.getAttribute('data-determinate'),
              now: bar.getAttribute('aria-valuenow'),
              label: document.querySelector('#activity-now .activity-progress-label').innerText};
    }""")
    assert seen["wired"], (
        "the real upload path registered no upload-progress listener, so a "
        "multi-megabyte folder drop shows nothing at all while it uploads")
    assert seen["det"] == "yes" and seen["now"] == "50", seen
    assert "50%" in seen["label"], seen["label"]


# --- colour is never the only signal --------------------------------------
CARRIERS = """() => {
  const sel = ['#status-pill', '#activity-now .activity-card',
               '#activity-alert .activity-card', '#preflight-banner',
               '#deploy-status', '#batch-status', '#bf-status',
               '#render-status', '#sum-fidelity', '.sev-chip', '.sev-head',
               '.pf-issue .pf-sev', '.inline-status'];
  const glyphs = ['\\u25cb','\\u27f3','\\u2713','\\u26a0','\\u2715','\\u2139'];
  const out = [];
  sel.forEach(s => document.querySelectorAll(s).forEach(n => {
    const cs = getComputedStyle(n), r = n.getBoundingClientRect();
    if (cs.display === 'none' || cs.visibility === 'hidden') return;
    if (!(r.width || r.height)) return;
    const flat = (n.innerText || n.textContent || '').replace(/\\s+/g, ' ').trim();
    if (!flat) return;                  // saying nothing yet is not a claim
    out.push({sel: s, text: flat.slice(0, 90),
              glyph: glyphs.some(g => flat.indexOf(g) !== -1),
              letters: /[A-Za-z]/.test(flat)});
  }));
  return out;
}"""


def _colour_free_offenders(carriers):
    """A carrier that SAYS something has to say it with a glyph AND with
    words. Anything else is leaning on its tint."""
    return [c for c in carriers if not c["glyph"] or not c["letters"]]


def test_the_colour_free_check_can_fail(page):
    """Mutation proof for the check below. Rebuild the original defect -- a
    status word carried by a tint and a coloured dot, with no glyph -- and
    confirm the check flags it."""
    page.evaluate("""() => { const p = document.getElementById('status-pill');
        p.textContent = 'Error'; p.className = 'topbar-pill err'; }""")
    offenders = _colour_free_offenders(page.evaluate(CARRIERS))
    assert any(o["sel"] == "#status-pill" for o in offenders), (
        "the check waved through a status carrier whose only state signal "
        "was its colour, so it cannot detect colour-only status: %s"
        % offenders)


def test_every_visible_status_carrier_reads_without_colour(page):
    if page.locator(".sample-chip").count() == 0:
        pytest.skip("no bundled sample to convert")
    page.click(".sample-chip")
    page.wait_for_function(
        "() => document.querySelectorAll('#mockup-host *').length > 0",
        timeout=60000)
    page.wait_for_timeout(900)
    page.click("#advanced-toggle")
    page.wait_for_timeout(150)
    for tab in ("validate", "burst", "deploy", "mockup"):
        page.click('[data-tab="%s"]' % tab)
        page.wait_for_timeout(250)
        offenders = _colour_free_offenders(page.evaluate(CARRIERS))
        assert not offenders, (
            "on the %s view these status carriers say nothing without their "
            "colour: %s" % (tab, json.dumps(offenders)))


def test_the_three_deploy_verdicts_differ_without_colour(page):
    """READY, RED and BLOCKER painted with three tints and ONE glyph ("!")
    for two of them. Render all three and compare the text."""
    seen = page.evaluate("""() => {
      const mk = sev => ({report: {name: 'R'},
        preflight: {issues: sev ? [{severity: sev, rule: 'r', message: 'm'}] : []}});
      const out = {};
      ['BLOCKER', 'RED', null].forEach(sev => {
        renderDeployStatus(mk(sev));
        const h = document.getElementById('deploy-status');
        out[sev || 'READY'] = {
          glyph: h.querySelector('.ds-glyph').textContent.trim(),
          word: (h.querySelector('.ds-state') || {}).textContent || '',
          verdict: h.querySelector('.ds-verdict').textContent.trim(),
          attr: h.getAttribute('data-verdict'),
        };
      });
      return out;
    }""")
    glyphs = [v["glyph"] for v in seen.values()]
    words = [v["word"].strip().lower() for v in seen.values()]
    assert len(set(glyphs)) == 3, (
        "the deploy verdicts share glyphs, so only the tint tells them "
        "apart: %s" % json.dumps(seen))
    assert len(set(words)) == 3 and all(words), (
        "each verdict needs its own word: %s" % json.dumps(seen))
    assert len({v["attr"] for v in seen.values()}) == 3, seen


def test_the_three_preflight_verdicts_differ_without_colour(page):
    seen = page.evaluate("""() => {
      const mk = sev => ({report: {name: 'R'}, validation_issues: [],
        preflight: {issues: sev ? [{severity: sev, rule: 'r', message: 'm'}] : []}});
      const out = {};
      ['BLOCKER', 'RED', null].forEach(sev => {
        renderPreflight(mk(sev));
        const b = document.getElementById('preflight-banner');
        out[sev || 'READY'] = {label: b.querySelector('b').textContent.trim(),
                               attr: b.getAttribute('data-verdict')};
      });
      return out;
    }""")
    labels = [v["label"] for v in seen.values()]
    assert len(set(labels)) == 3, seen
    glyphs = [l[0] for l in labels]
    assert len(set(glyphs)) == 3, (
        "the verdict banners open with the same glyph: %s" % json.dumps(seen))
    assert len({v["attr"] for v in seen.values()}) == 3, (
        "the stylesheet paints off data-verdict; without three distinct "
        "values a BLOCKER banner paints like a READY one: %s" % seen)


def test_success_and_failure_never_look_the_same_on_an_inline_line(page):
    """#batch-status wrote both outcomes as the same grey sentence."""
    seen = page.evaluate("""() => {
      const n = document.getElementById('batch-status');
      const read = () => ({text: n.innerText.replace(/\\s+/g,' ').trim(),
                           state: n.getAttribute('data-state')});
      setInlineStatus(n, 'done', 'Downloaded burst_pack.zip.');
      const ok = read();
      setInlineStatus(n, 'failed', 'Download failed: the server said no.');
      const bad = read();
      return {ok, bad, live: n.getAttribute('aria-live')};
    }""")
    assert seen["ok"]["state"] != seen["bad"]["state"], seen
    assert seen["ok"]["text"].split()[0] != seen["bad"]["text"].split()[0], (
        "success and failure start with the same characters, so at a glance "
        "they are the same line: %s" % json.dumps(seen))
    assert seen["live"] == "polite", (
        "an inline result that changes on its own has to be announced")


def test_the_fidelity_score_says_which_state_it_is_in(page):
    """73% and 100% used to be printed in identical ink."""
    if page.locator(".sample-chip").count() == 0:
        pytest.skip("no bundled sample to convert")
    seen = page.evaluate("""() => {
      const mk = score => ({report: {name: 'R'}, preflight: {issues: []},
        fidelity_report: {score: score, summary: 's',
          categories: {layout_fields: {display_coverage: score}}}});
      const out = {};
      [1, 0.73].forEach(s => {
        renderSummary(mk(s));
        const v = document.getElementById('sum-fidelity');
        out[String(s)] = {text: v.innerText.replace(/\\s+/g,' ').trim(),
                          state: v.getAttribute('data-state')};
      });
      return out;
    }""")
    full, part = seen["1"], seen["0.73"]
    assert full["state"] != part["state"], seen
    assert full["text"] != part["text"], seen
    assert any(g in full["text"] for g in ("✓", "⚠")), full
    assert any(g in part["text"] for g in ("✓", "⚠")), part
