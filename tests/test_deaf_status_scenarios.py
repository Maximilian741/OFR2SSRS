"""The status surface on the four scenarios that actually decide the job.

The operator who runs this converter every day is deaf and works on a
locked-down machine. The status architecture was reviewed and found sound in
the abstract -- and then broken on the four things that happen most:

  1. DROPPING A .rdf DESTROYED THE INSTRUCTION. The drop zone's own label
     invites ".xml or .rdf". The server answers a .rdf with the exact
     rwconverter command that fixes it. The UI wrote that command into the
     failure card and then, in the SAME tick, the setStatus() and toast()
     beside it overwrote the card with "There was no Oracle XML export...".
     Measured before the fix: the card carried no <code> block at all.

  2. RE-RENDER SHOWED NO WORKING STATE. #render-run called runRenderPreview()
     directly, and the only thing that changed was a disabled button. The
     toolbar kept its previous line -- "Done - Rendered by Microsoft's report
     engine - 4 page(s)" -- for the whole run. With no audio channel, an
     operation running under a message that says it already finished is
     indistinguishable from a hang.

  3. "DO THIS NEXT" WAS GUESSED FROM ENGLISH WORDS IN THE MESSAGE. Three of
     the most common failures -- a folder over the size cap, a .rdf, a drop
     with no report in it -- all matched nothing and were answered "Try the
     same step again", which fails identically every time. Same class of
     mistake as guessing an Oracle report's meaning from its name.

  4. THE PILL WAS A SECOND STATUS CHANNEL THAT DISAGREED WITH THE FIRST.
     Measured: converting the bundled sample left the card saying
     "Check this - Converted SAMPLE_FEE_NOTICE ... 1 thing may go wrong" while
     the corner pill said "Done - Converted" in green, for the same event.

Every check here is driven against the REAL served page, and every one of
them is mutation-proved: the probe is first pointed at a page carrying the
original defect and has to go red, because a check that cannot fail certifies
anything.
"""
from __future__ import annotations

import os
import re
import socket
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

APP_JS = ROOT / "frontend" / "static" / "js" / "app.js"
BACKEND_APP = ROOT / "backend" / "app.py"

# A minimal Oracle Reports BINARY. The point of a .rdf is that it is NOT
# XML -- the bytes never have to be real, and no client artifact is needed.
RDF_BYTES = b"\x00\x01ORACLE REPORTS COMPILED BINARY\x00" * 8

GENERIC_NEXT = "Try the same step again"


def _js() -> str:
    return APP_JS.read_text(encoding="utf-8")


def _fn_body(js: str, opener: str, closer: str) -> str:
    start = js.index(opener)
    return js[start:js.index(closer, start)]


# ====================================================== the kinds contract
def _frontend_kinds() -> set:
    """Every kind the frontend knows how to advise on."""
    js = _js()
    block = _fn_body(js, "const NEXT_ACTION_BY_KIND = {", "\n};")
    return set(re.findall(r"^  (\w+):", block, re.M))


def _backend_kinds() -> set:
    src = BACKEND_APP.read_text(encoding="utf-8")
    block = src[src.index("ERROR_KINDS = {"):]
    block = block[:block.index("\n}")]
    return set(re.findall(r'"(\w+)"', block))


def test_the_next_action_cannot_read_the_message():
    """The defect, structurally: the advice was chosen by looking for English
    words in the failure text. The lookup must be incapable of that."""
    js = _js()
    body = _fn_body(js, "function _nextActionFor(kind) {", "\n}")
    for banned in ("indexOf(", "toLowerCase(", ".match(", ".test(",
                   "includes(", "search("):
        assert banned not in body, (
            "_nextActionFor inspects the message again (%r). Prose is "
            "rewritten every few weeks; advice derived from it goes silently "
            "wrong. Look the step up from the kind." % banned)
    assert "NEXT_ACTION_BY_KIND" in body and "hasOwnProperty" in body, body


def test_no_failure_site_still_derives_advice_from_prose():
    """Generalised: nobody may call the lookup with a message again."""
    js = _js()
    kinds = _frontend_kinds()
    for call in re.findall(r"_nextActionFor\(([^)]*)\)", js):
        arg = call.strip()
        # A variable named for the kind, or a literal kind from the table.
        # Anything else -- a message, a headline, a concatenation -- is the
        # word-matching defect coming back.
        quoted = arg[:1] in ("'", '"') and arg[-1:] == arg[:1]
        literal = arg[1:-1] if quoted else None
        assert arg in ("kind", "opts.kind", "k") or literal in kinds, (
            "_nextActionFor is being handed %r. It takes a KIND -- passing a "
            "message re-creates the word-matching defect." % arg)


def test_the_status_mirror_does_not_recognise_category_words_either():
    """The other place a decision was made by matching English: the pill's
    words were promoted to a headline only if they matched a hard-coded list
    ("error", "upload too large", "nothing to convert", ...). Every copy
    rewrite silently took a phrase off that list."""
    js = _js()
    body = _fn_body(js, "function _mirrorStatus(text, kind, detail) {",
                    "\nfunction ")
    literals = re.findall(r"/\^?\([^)]*\|[^)]*\)\$?/[a-z]*", body)
    assert not literals, (
        "_mirrorStatus decides something by recognising wording again: %s"
        % literals)


def test_courtesy_copies_are_declared_as_courtesy_copies():
    """The .rdf defect in one line: setStatus() and toast() fire beside the
    real call, in the same tick, and used to overwrite its card. They may
    only ever CREATE a card, so both must say what they are."""
    js = _js()
    for fn, opener in (("_mirrorStatus",
                        "function _mirrorStatus(text, kind, detail) {"),
                       ("toast", "function toast(msg, kind) {")):
        body = _fn_body(js, opener, "\nfunction ")
        for call in re.findall(r"statusFail\((.*?)\);", body, re.S):
            assert "courtesy: true" in call, (
                "%s raises a failure card without declaring it a courtesy "
                "copy, so it can replace the card the real call wrote: %s"
                % (fn, " ".join(call.split())[:120]))


def test_every_kind_the_server_can_send_has_advice_on_the_client():
    missing = _backend_kinds() - _frontend_kinds()
    assert not missing, (
        "the server can answer with these kinds and the frontend has no next "
        "step for them, so the user gets the generic 'try again': %s"
        % sorted(missing))


def test_every_kind_the_client_sets_has_advice_too():
    js = _js()
    used = set(re.findall(r"""kind:\s*["'](\w+)["']""", js))
    used |= set(re.findall(r"""_nextActionFor\(["'](\w+)["']\)""", js))
    missing = used - _frontend_kinds()
    assert not missing, (
        "these kinds are raised but have no entry in NEXT_ACTION_BY_KIND, so "
        "they silently fall back to the generic advice: %s" % sorted(missing))


def test_an_unknown_kind_degrades_to_honest_not_to_wrong():
    js = _js()
    assert "NEXT_ACTION_UNKNOWN" in js
    unknown = _fn_body(js, "const NEXT_ACTION_UNKNOWN =", ";\n")
    assert GENERIC_NEXT in unknown, (
        "an unrecognised kind must fall back to admitting we do not know, "
        "never to a confident step that does not apply")


# ================================================ the pill is not a writer
def test_only_the_projection_writes_the_pill():
    """One truth, one surface: nothing but _syncPill may put words in the
    pill, and _syncPill takes them from the status card."""
    js = _js()
    sync = _fn_body(js, "function _syncPill() {", "\n}")
    assert "currentStatus()" in sync, (
        "_syncPill must render the pill FROM the status surface; anything "
        "else is a second channel that can disagree with the card")
    outside = js.replace(sync, "")
    for writer in ("pill.textContent", "pill.className",
                   'pill.setAttribute("data-state"'):
        assert writer not in outside, (
            "%s is written outside _syncPill, so the pill can say something "
            "the card does not" % writer)


# ===================================================== the server's shape
@pytest.fixture()
def client():
    from app import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_the_server_answers_failures_with_a_structural_kind(client):
    """Over real HTTP, not by reading the source."""
    seen = {}
    r = client.post("/api/convert", data={}, content_type="multipart/form-data")
    seen["no file"] = (r.status_code, r.get_json())
    r = client.post("/api/render-preview", data={},
                    content_type="multipart/form-data")
    seen["nothing converted"] = (r.status_code, r.get_json())
    r = client.post("/api/convert-bundle", data={},
                    content_type="multipart/form-data")
    seen["no files"] = (r.status_code, r.get_json())
    known = _frontend_kinds()
    for label, (status, body) in seen.items():
        assert status >= 400, (label, status)
        kind = (body or {}).get("error_kind")
        assert kind, (
            "%s answered with a sentence and no kind, so the client is back "
            "to guessing the next step from prose: %r" % (label, body))
        assert kind in known, (label, kind, sorted(known))


def test_a_dropped_rdf_is_reported_as_its_own_kind(client):
    import io
    r = client.post("/api/convert-bundle",
                    data={"files": (io.BytesIO(RDF_BYTES), "REPORT.rdf")},
                    content_type="multipart/form-data")
    body = r.get_json()
    assert body.get("error") == "no_convertible_artifacts", body
    assert body.get("error_kind") == "rdf_binary", (
        "a compiled .rdf and a folder with no report in it need completely "
        "different advice, so they cannot share a kind: %r" % body)
    assert "rwconverter" in (body.get("rdf_hint") or ""), body


# ========================================================= layer 3: live
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
    # The target machine may have no internet at all: nothing off this host
    # may load, or the page under test is not the page they run.
    pg.route("**/*", lambda route: route.continue_()
             if "127.0.0.1" in route.request.url else route.abort())
    pg.goto(live_url, wait_until="domcontentloaded")
    pg.wait_for_function("() => !!document.querySelector('.tab')")
    pg.wait_for_timeout(200)
    yield pg
    pg.close()


# --- the readers ----------------------------------------------------------
# What the failure card is telling the user, as a user could read it.
CARD = """() => {
  const host = document.getElementById('activity-alert');
  const card = host.querySelector('.activity-card');
  if (!card) return null;
  const cmd = card.querySelector('.activity-cmd');
  const cs = getComputedStyle(card), r = card.getBoundingClientRect();
  const lines = Array.from(card.querySelectorAll('.activity-line'))
                     .map(n => n.textContent.trim());
  return {
    visible: cs.display !== 'none' && cs.visibility !== 'hidden'
             && !!(r.width || r.height),
    state: card.getAttribute('data-state'),
    kind: card.getAttribute('data-kind') || '',
    headline: (card.querySelector('.activity-headline')||{}).textContent || '',
    lines: lines,
    next: (lines.find(t => t.indexOf('Do this next:') === 0) || ''),
    cmd: cmd ? cmd.textContent : null,
    text: card.innerText,
  };
}"""

# The pill and the card, side by side, as two claims about the same moment.
AGREEMENT = """() => {
  const pill = document.getElementById('status-pill');
  const card = document.querySelector('#activity-alert .activity-card')
            || document.querySelector('#activity-now .activity-card');
  if (!pill || !card) return null;
  const pillWord = (pill.querySelector('.pill-word')||{}).textContent || '';
  const pillGlyph = (pill.querySelector('.pill-glyph')||{}).textContent || '';
  const cardGlyph = (card.querySelector('.activity-glyph')||{}).textContent || '';
  const cardState = (card.querySelector('.activity-state')||{}).textContent || '';
  const head = (card.querySelector('.activity-headline')||{}).textContent || '';
  const trimmed = pillWord.replace(/\\u2026$/, '').trim();
  return {
    pillState: pill.getAttribute('data-state'),
    cardState: card.getAttribute('data-state'),
    pillGlyph: pillGlyph.trim(), cardGlyph: cardGlyph.trim(),
    cardWord: cardState.trim(),
    pillText: pillWord, headline: head,
    pillAria: pill.getAttribute('aria-label') || '',
    echoesHeadline: !!trimmed && head.indexOf(trimmed) === 0,
  };
}"""


def _disagreements(a):
    """Two status carriers, one event. Any difference is a contradiction the
    user has to resolve on their own."""
    if a is None:
        return ["no pill or no card to compare"]
    bad = []
    if a["pillState"] != a["cardState"]:
        bad.append("state %r vs %r" % (a["pillState"], a["cardState"]))
    if a["pillGlyph"] != a["cardGlyph"]:
        bad.append("glyph %r vs %r" % (a["pillGlyph"], a["cardGlyph"]))
    if not a["echoesHeadline"]:
        bad.append("pill says %r, card says %r" % (a["pillText"], a["headline"]))
    if a["cardWord"] and a["cardWord"].lower() not in a["pillAria"].lower():
        bad.append("pill's accessible name never says %r" % a["cardWord"])
    return bad


def _drop(page, files):
    page.set_input_files("#file-input-files", files)


def _wait_for_card(page, timeout=60000):
    page.wait_for_function(
        "() => !!document.querySelector('#activity-alert .activity-card')",
        timeout=timeout)


# --- 1. the .rdf instruction ---------------------------------------------
def test_the_card_reader_notices_when_an_instruction_is_destroyed(page):
    """Mutation proof for the test below. Rebuild the original defect -- a
    rich card written and then replaced, in the same tick, by a plain one --
    and confirm the reader reports the command as gone."""
    lost = page.evaluate("""() => {
      statusFail('Compiled .rdf', {subject: 'x', cmd: 'rwconverter userid=...',
                                   next: 'export it', why: 'binary'});
      const before = !!document.querySelector('#activity-alert .activity-cmd');
      // exactly what setStatus() used to do a microsecond later
      statusFail('There was no Oracle XML export', {});
      const after = !!document.querySelector('#activity-alert .activity-cmd');
      statusClearAlert();
      return {before: before, after: after};
    }""")
    assert lost["before"], "the reader cannot even see a command that IS there"
    assert not lost["after"], (
        "the reader says the command survived a card that was replaced by a "
        "plain one, so it cannot detect the very defect it exists for")


def test_dropping_an_rdf_keeps_the_export_command_on_screen(page):
    """The worst measured case: the one card that says how to fix it."""
    _drop(page, [{"name": "REPORT.rdf",
                  "mimeType": "application/octet-stream",
                  "buffer": RDF_BYTES}])
    _wait_for_card(page)
    page.wait_for_timeout(4200)          # outlive the toast
    assert page.evaluate("() => document.getElementById('toast').hidden")
    card = page.evaluate(CARD)
    assert card and card["visible"], "the failure card is not on screen at all"
    assert card["cmd"] and "rwconverter" in card["cmd"], (
        "the export command the server returned is not on screen. That is "
        "the only thing that unblocks this operator: %r" % card)
    assert card["kind"] == "rdf_binary", card
    assert ".rdf" in card["headline"], card["headline"]
    assert GENERIC_NEXT not in card["next"], (
        "a .rdf will fail identically every time it is dropped; 'try the "
        "same step again' is the one answer that is certainly wrong: %r"
        % card["next"])
    assert "Oracle Reports" in card["next"] or ".xml" in card["next"], card["next"]
    # and it is selectable text, not a console message
    assert page.evaluate(
        "() => getComputedStyle(document.querySelector("
        "'#activity-alert .activity-cmd')).userSelect !== 'none'")


# --- 1b. ...in EVERY drop shape ------------------------------------------
#
# THE SECOND HALF OF THE SAME DEFECT, measured on the served page with a
# real .rdf. Dropped ALONE the export command is on screen (above). Dropped
# the way the drop zone invites -- the report's folder, which holds the .xml
# AND the .rdf beside it -- the conversion SUCCEEDS, and the instruction was
# gone: the ingest only wrote it on the "nothing convertible" answer, and the
# client only read it out of that same failure branch. The operator who does
# the everyday thing got no guidance at all, and no hint that a file had been
# skipped.
#
# It cannot come back as a failure card: that drop converted. It is a note in
# the panel that already says what each dropped file was taken to be, it
# NAMES the file it is about, and it carries the command as selectable text.
SAMPLE_XML = ROOT / "samples" / "oracle" / "SAMPLE_INSPECTION.xml"

RDF_GUIDANCE = """() => {
  const vis = n => { if (!n) return false;
    const cs = getComputedStyle(n), r = n.getBoundingClientRect();
    return cs.display !== 'none' && cs.visibility !== 'hidden'
           && !!(r.width || r.height); };
  const cmds = [];
  document.querySelectorAll('.activity-cmd').forEach(n => {
    if (!vis(n)) return;
    cmds.push({where: n.closest('#ingest-banner') ? 'panel'
                    : (n.closest('#activity-alert') ? 'card' : 'other'),
               text: n.textContent || ''});
  });
  const b = document.getElementById('ingest-banner');
  const card = document.querySelector('#activity-alert .activity-card');
  const flat = n => (n && vis(n))
    ? (n.innerText || '').replace(/\\s+/g, ' ').trim() : '';
  return {cmds: cmds, panel: flat(b), card: flat(card)};
}"""


def _instruction_for(seen, rdf_name):
    """Every on-screen copy of the export command that is ABOUT this file."""
    return [c for c in seen["cmds"]
            if "rwconverter" in c["text"] and rdf_name in c["text"]]


def _rdf_bytes_and_name():
    """A real compiled .rdf when the agency folder is on this machine; the
    synthetic one otherwise. Classification is by extension, so both drive
    the same path -- the real file is what the reviewer measured with."""
    corpus = Path(os.environ.get(
        "O2S_AGENCY_CORPUS",
        "C:/Users/maxca/Downloads/OneDrive_2026-08-06/Artifact Folders"))
    if corpus.is_dir():
        for p in sorted(corpus.rglob("*.rdf")):
            return p.name, p.read_bytes()
    return "REPORT.rdf", RDF_BYTES


def _write(tmp_path, name, blob):
    f = tmp_path / name
    f.write_bytes(blob)
    return str(f)


def test_the_mixed_drop_reader_can_fail(page, tmp_path):
    """Mutation proof for the two tests below. Disable the one renderer that
    puts the note on screen, replay the same payload, and confirm the reader
    reports the instruction as gone -- otherwise it certifies anything."""
    rdf_name, rdf_blob = _rdf_bytes_and_name()
    page.set_input_files("#file-input-files", [
        _write(tmp_path, rdf_name, rdf_blob),
        _write(tmp_path, "SAMPLE_INSPECTION.xml", SAMPLE_XML.read_bytes())])
    page.wait_for_function(
        "() => !!document.querySelector('#ingest-banner .activity-cmd')",
        timeout=45000)
    assert _instruction_for(page.evaluate(RDF_GUIDANCE), rdf_name), (
        "the reader cannot see an instruction that IS on screen")
    gone = page.evaluate("""() => {
      window.renderRdfGuidance = () => {};      // the pre-fix behaviour
      const b = document.getElementById('ingest-banner');
      b.innerHTML = ''; b.hidden = true;
      onConverted(state.data);
      return true;
    }""")
    assert gone
    assert not _instruction_for(page.evaluate(RDF_GUIDANCE), rdf_name), (
        "with the renderer disabled the instruction should be gone; the "
        "reader still claims it is on screen, so it proves nothing")


@pytest.mark.parametrize("shape", ["alone", "with_xml", "folder"])
def test_the_rdf_export_command_survives_every_drop_shape(page, tmp_path,
                                                          shape):
    """The measured matrix, on the real page: a .rdf alone, a .rdf beside a
    valid .xml, and a folder holding both. The command has to be on screen
    in all three, and to name the file it applies to."""
    rdf_name, rdf_blob = _rdf_bytes_and_name()
    d = tmp_path / shape
    d.mkdir()
    rdf = _write(d, rdf_name, rdf_blob)
    xml = _write(d, "SAMPLE_INSPECTION.xml", SAMPLE_XML.read_bytes())
    if shape == "alone":
        page.set_input_files("#file-input-files", [rdf])
    elif shape == "with_xml":
        page.set_input_files("#file-input-files", [rdf, xml])
    else:
        # the webkitdirectory input -- what a real folder drop lands on
        try:
            page.set_input_files("#file-input", str(d))
        except Exception:                     # older Playwright: same handler
            page.set_input_files("#file-input", [rdf, xml])
    page.wait_for_function(
        "() => !!document.querySelector('#ingest-banner .activity-cmd')"
        " || !!document.querySelector('#activity-alert .activity-cmd')",
        timeout=45000)
    page.wait_for_timeout(4200)               # outlive the toast
    seen = page.evaluate(RDF_GUIDANCE)
    mine = _instruction_for(seen, rdf_name)
    assert mine, (
        "dropping a .rdf as %r left no export command on screen. That "
        "command is the only thing that unblocks this operator: %r"
        % (shape, seen))
    # attribution: with several files dropped, the note has to say WHICH
    # one it is about, not just print a command.
    if shape != "alone":
        assert rdf_name in seen["panel"], (
            "several files were dropped and the guidance never says which "
            "one it is about: %r" % seen["panel"])
        assert "rwconverter" in seen["panel"], seen["panel"]
    # and the command is selectable text wherever it landed
    assert page.evaluate(
        "() => Array.from(document.querySelectorAll('.activity-cmd'))"
        ".every(n => getComputedStyle(n).userSelect !== 'none')")


def test_a_successful_folder_drop_does_not_call_itself_a_failure(page,
                                                                 tmp_path):
    """The note may not undo the conversion it sits under: the .rdf was
    skipped, the report on screen is real, and the status must still say
    so. (Two verdicts for one drop is the other defect fixed this session.)"""
    rdf_name, rdf_blob = _rdf_bytes_and_name()
    page.set_input_files("#file-input-files", [
        _write(tmp_path, rdf_name, rdf_blob),
        _write(tmp_path, "SAMPLE_INSPECTION.xml", SAMPLE_XML.read_bytes())])
    page.wait_for_function(
        "() => !!document.querySelector('#ingest-banner .activity-cmd')",
        timeout=45000)
    state = page.evaluate("""() => {
      const card = document.querySelector('#activity-now .activity-card')
                || document.querySelector('#activity-alert .activity-card');
      return {state: card ? card.getAttribute('data-state') : null,
              head: card ? (card.querySelector('.activity-headline')||{})
                             .innerText || '' : ''};
    }""")
    assert state["state"] in ("done", "warn"), (
        "a folder drop that converted is being reported as a failure: %r"
        % state)
    assert "came across" in page.evaluate(
        "() => document.getElementById('activity').innerText"), (
        "the conversion that succeeded is not being reported at all")


# --- 2. re-render shows work happening -----------------------------------
RENDER_PROBE = """() => {
  const rs = document.getElementById('render-status');
  const btn = document.getElementById('render-run');
  const vis = n => { if (!n) return false;
    const cs = getComputedStyle(n), r = n.getBoundingClientRect();
    return cs.display !== 'none' && cs.visibility !== 'hidden'
           && !!(r.width || r.height); };
  const loading = document.querySelector('.render-loading');
  return {
    state: rs ? rs.getAttribute('data-state') : null,
    text: rs ? (rs.innerText || '').replace(/\\s+/g, ' ').trim() : '',
    visible: vis(rs),
    btnLabel: btn ? (btn.innerText || '').trim() : '',
    btnBusy: btn ? btn.getAttribute('aria-busy') : null,
    btnDisabled: btn ? btn.disabled : null,
    loadingVisible: vis(loading),
  };
}"""


def _says_working(p):
    """A working state a deaf operator can actually read: the state WORD, a
    sentence, and a control that says it is busy. A disabled button on its
    own is not a status."""
    return (p["state"] == "working"
            and "working" in p["text"].lower()
            and len(p["text"]) > 40
            and p["visible"]
            and (p["btnBusy"] == "true" or p["btnLabel"].lower() != "re-render"))


def _convert_sample(page):
    if page.locator(".sample-chip").count() == 0:
        pytest.skip("no bundled sample to convert")
    page.click(".sample-chip")
    page.wait_for_function(
        "() => document.querySelectorAll('#mockup-host *, #render-host *')"
        ".length > 0", timeout=45000)
    # Wait for the FIRST render to settle rather than trusting a fixed pause:
    # on a slower machine the engine is still working after one, and every
    # measurement below would then be reading a render nobody asked for.
    try:
        page.wait_for_function(
            "() => ['done','warn'].indexOf(("
            "document.getElementById('render-status')||{})"
            ".getAttribute('data-state')) !== -1", timeout=180000)
    except Exception:                       # engine wedged: say so, loudly
        pytest.skip("the first render never settled on this machine")
    page.wait_for_timeout(300)


def _slow_the_render(page, ms=2500):
    page.evaluate("""(ms) => {
      const real = window.fetch;
      window.fetch = (u, o) => (String(u).indexOf('/api/render-preview') !== -1)
        ? new Promise(res => setTimeout(() => res(real(u, o)), ms))
        : real(u, o);
    }""", ms)


def test_the_working_state_probe_can_report_nothing(page):
    """Mutation proof: disable the one function that raises the working
    state -- the pre-fix behaviour, where pressing Re-render only greyed the
    button -- and confirm the probe says nothing is happening."""
    _convert_sample(page)
    _slow_the_render(page)
    page.evaluate("() => { window._renderBusy = function () {}; }")
    page.click("#render-run")
    page.wait_for_timeout(600)
    p = page.evaluate(RENDER_PROBE)
    assert not _says_working(p), (
        "the probe reported a working state on a page where nothing raises "
        "one, so it cannot detect the defect it exists for: %r" % p)


def test_re_render_shows_a_working_state_and_then_clears_it(page):
    _convert_sample(page)
    before = page.evaluate(RENDER_PROBE)
    assert before["state"] in ("done", "warn"), before
    _slow_the_render(page)
    page.click("#render-run")
    page.wait_for_timeout(500)
    during = page.evaluate(RENDER_PROBE)
    assert _says_working(during), (
        "pressing Re-render started a multi-second operation with no visible "
        "working state. For an operator with no audio channel that is a "
        "hang: %r" % during)
    assert "done" not in during["text"].lower(), (
        "the toolbar is still showing the PREVIOUS render's success line "
        "while a new render runs: %r" % during["text"])
    assert during["loadingVisible"], (
        "the pages on screen were discarded but nothing replaced them, so "
        "the user is reading stale output: %r" % during)
    # ...and it must not stay that way.
    page.wait_for_function(
        "() => document.getElementById('render-status')"
        ".getAttribute('data-state') !== 'working'", timeout=45000)
    after = page.evaluate(RENDER_PROBE)
    assert after["state"] in ("done", "warn"), after
    assert after["btnLabel"].lower() == "re-render" and not after["btnDisabled"], (
        "the button never came back, so the operation looks like it is still "
        "running: %r" % after)
    assert after["btnBusy"] is None, after


def test_a_later_failure_is_never_swallowed_by_the_courtesy_rule(page):
    """The other half of the rule. A restatement fired beside the real call
    is declined; a failure that happens LATER is a different event and must
    always get its own card, or the guard would hide real problems."""
    same_tick = page.evaluate("""() => {
      statusFail('Compiled .rdf', {cmd: 'rwconverter ...', next: 'export it',
                                   why: 'binary', kind: 'rdf_binary'});
      const first = document.querySelector('#activity-alert .activity-headline').textContent;
      toast('something shorter', 'err');       // the courtesy pair
      return {first: first,
              after: document.querySelector('#activity-alert .activity-headline').textContent,
              cmd: !!document.querySelector('#activity-alert .activity-cmd')};
    }""")
    assert same_tick["after"] == same_tick["first"] and same_tick["cmd"], same_tick
    page.wait_for_timeout(1600)                # past the pairing window
    later = page.evaluate("""() => {
      toast('The download could not be saved', 'err');
      const card = document.querySelector('#activity-alert .activity-card');
      return {headline: card.querySelector('.activity-headline').textContent,
              lines: Array.from(card.querySelectorAll('.activity-line'))
                       .map(n => n.textContent).join(' | ')};
    }""")
    assert "download" in later["headline"].lower(), (
        "a failure that happened later never reached the screen -- the guard "
        "is hiding real problems, not duplicates: %r" % later)
    assert "Do this next" in later["lines"], later


def test_the_render_fallback_says_the_rdl_is_still_fine(page):
    """When the engine cannot run here, the notice has to say what that
    means: the preview is degraded, the .rdl is not."""
    _convert_sample(page)
    note = page.evaluate("""() => {
      // what the engine-failure path really leaves behind: no pages, and a
      // reason
      state.renderedPages = null; state.renderedPdf = null;
      state._renderFailed = 'ReportViewer could not start on this machine';
      renderMockupTab(state.data);
      const n = document.querySelector('.mockup-fallback-note');
      return n ? n.innerText : null;
    }""")
    assert note, "no notice at all when the engine render fails"
    assert "Do this next" in note, note
    assert ".rdl" in note and "report server" in note, (
        "the notice never says the .rdl itself is unaffected, so a degraded "
        "preview reads as a failed conversion: %r" % note)


# --- 3. the everyday failures get the right next step --------------------
def test_a_drop_over_the_size_cap_is_told_what_to_remove(page):
    """The everyday failure on a real artifact folder: it holds a 34 MB
    rendered PDF beside a 55 KB XML export."""
    page.evaluate("() => { state._maxUploadBytes = 20000; }")
    _drop(page, [
        {"name": "REPORT.xml", "mimeType": "text/xml",
         "buffer": b"<report/>" * 20},
        {"name": "TRUTH.pdf", "mimeType": "application/pdf",
         "buffer": b"%PDF-" + b"x" * 60000},
    ])
    _wait_for_card(page)
    card = page.evaluate(CARD)
    assert card["kind"] == "upload_too_large", card
    assert GENERIC_NEXT not in card["next"], (
        "the same drop fails identically every time; telling the operator to "
        "try it again is the one answer that cannot work: %r" % card["next"])
    low = card["next"].lower()
    assert "pdf" in low and (".xml" in low or "xml export" in low), (
        "the next step has to name what to leave out: %r" % card["next"])


def test_a_drop_with_no_report_in_it_is_told_what_to_add(page):
    _drop(page, [{"name": "notes.txt", "mimeType": "text/plain",
                  "buffer": b"a few notes about the report"}])
    _wait_for_card(page)
    card = page.evaluate(CARD)
    assert card["kind"] == "no_report_in_drop", card
    assert GENERIC_NEXT not in card["next"], card["next"]
    assert "xml" in card["next"].lower(), card["next"]


def test_an_unreachable_server_is_still_told_to_restart_it(page):
    """The one case the old word-matching got right; it must survive the
    rewrite that removed the word-matching."""
    if page.locator(".sample-chip").count() == 0:
        pytest.skip("no bundled sample to convert")
    page.evaluate("""() => { window.fetch = () =>
        Promise.reject(new TypeError('Failed to fetch')); }""")
    page.click(".sample-chip")
    _wait_for_card(page, timeout=30000)
    card = page.evaluate(CARD)
    assert card["kind"] == "server_unreachable", card
    assert "run.bat" in card["next"], card["next"]


# --- 4. one truth, one surface -------------------------------------------
def test_the_agreement_check_can_fail(page):
    """Mutation proof: repaint the pill by hand with the words and the tint
    it used to write for itself -- the measured defect, a green 'Converted'
    beside an amber 'Check this' card -- and confirm the check flags it."""
    _convert_sample(page)
    page.evaluate("""() => {
      const p = document.getElementById('status-pill');
      p.className = 'topbar-pill ok has-glyph';
      p.setAttribute('data-state', 'done');
      p.innerHTML = '<span class="pill-glyph">\\u2713</span>'
                  + '<span class="pill-word">Converted</span>';
      p.setAttribute('aria-label', 'Converted');
    }""")
    bad = _disagreements(page.evaluate(AGREEMENT))
    assert bad, (
        "the check waved through a pill saying Done beside a card saying "
        "Check this, so it cannot detect two contradicting status channels")


def test_the_pill_and_the_card_agree_after_a_conversion(page):
    """The measured case: a conversion with a preflight finding. The card
    says 'Check this'; the pill used to say 'Done' in green."""
    _convert_sample(page)
    a = page.evaluate(AGREEMENT)
    assert a and a["cardState"] in ("done", "warn"), a
    assert not _disagreements(a), a


def test_the_pill_and_the_card_agree_on_a_failure(page):
    _drop(page, [{"name": "REPORT.rdf",
                  "mimeType": "application/octet-stream",
                  "buffer": RDF_BYTES}])
    _wait_for_card(page)
    page.wait_for_timeout(4200)          # the toast has been and gone
    a = page.evaluate(AGREEMENT)
    assert a and a["cardState"] == "failed", a
    assert not _disagreements(a), a


def test_the_pill_never_contradicts_the_card_through_a_whole_session(page):
    """Every state the app can be in, in one run, checked after each step.
    A pill that is a projection cannot drift; this is what proves it."""
    steps = []
    if page.locator(".sample-chip").count() == 0:
        pytest.skip("no bundled sample to convert")

    page.click(".sample-chip")
    page.wait_for_timeout(400)
    steps.append(("mid-conversion", page.evaluate(AGREEMENT)))
    page.wait_for_function(
        "() => document.querySelectorAll('#mockup-host *, #render-host *')"
        ".length > 0", timeout=45000)
    page.wait_for_timeout(800)
    steps.append(("converted", page.evaluate(AGREEMENT)))

    _drop(page, [{"name": "REPORT.rdf", "mimeType": "application/octet-stream",
                  "buffer": RDF_BYTES}])
    _wait_for_card(page)
    page.wait_for_timeout(300)
    steps.append(("rdf failure", page.evaluate(AGREEMENT)))

    page.click("#activity-alert .activity-dismiss")
    page.wait_for_timeout(200)
    steps.append(("dismissed", page.evaluate(AGREEMENT)))

    for label, a in steps:
        assert a is not None, "%s: nothing to compare" % label
        assert not _disagreements(a), "%s: %s" % (label, _disagreements(a))


# ============================== advice that points at something on screen
# THE DEFECT THIS RAIL EXISTS FOR, measured with the real artifact folder:
# dropping a folder whose only sizeable file is the 34.9 MB rendered report
# PDF fails in the BROWSER, before anything is sent -- and was answered with
#
#   "Add the report's Oracle XML export (the .xml file) to the drop, then
#    try again. The Ingest Summary below lists what each file you dropped
#    was taken to be."
#
# There is no Ingest Summary below. Nothing was sent, so the server never
# said what it made of each file and that panel was never drawn. The
# operator is told to go and read a panel that is not on the screen -- and
# the person who runs this tool cannot lean over and ask a colleague what
# actually happened.
#
# The rule, stated generally: advice may name a panel only on a code path
# that draws it. A SIBLING branch of the same function drawing it proves
# nothing about this path, so the function body as a whole does not count.
def _next_actions() -> dict:
    """kind -> its advice text, string concatenation resolved."""
    block = _fn_body(_js(), "const NEXT_ACTION_BY_KIND = {", "\n};")
    out = {}
    for m in re.finditer(r"^  (\w+):((?:.|\n)*?)(?=^  \w+:|\Z)", block, re.M):
        out[m.group(1)] = "".join(
            re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(2)))
    return out


def _brace_map(src: str) -> dict:
    """'{' offset -> matching '}' offset, skipping strings and comments."""
    out, stack = {}, []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c in "\"'`":
            quote = c
            i += 1
            while i < n:
                if src[i] == "\\":
                    i += 2
                    continue
                if src[i] == quote:
                    break
                i += 1
        elif c == "/" and i + 1 < n and src[i + 1] == "/":
            nl = src.find("\n", i)
            if nl < 0:
                break
            i = nl
        elif c == "/" and i + 1 < n and src[i + 1] == "*":
            i = src.find("*/", i) + 1
        elif c == "{":
            stack.append(i)
        elif c == "}":
            if stack:
                out[stack.pop()] = i
        i += 1
    return out


_STMT_BLOCK = re.compile(r"(\)|else|try|do)\s*$")
_FUNC_BLOCK = re.compile(r"function\s*\w*\s*\([^()]*\)\s*$")


def _branch_blocks(js: str, bm: dict, index: int):
    """The statement blocks around `index`, innermost first, WITHOUT the
    function body: an `if` in another branch is not this code path."""
    out = []
    for a, b in sorted((ab for ab in bm.items() if ab[0] < index < ab[1]),
                       key=lambda ab: -ab[0]):
        before = js[max(0, a - 200):a]
        if not _STMT_BLOCK.search(before):
            continue                      # an object literal, not a branch
        if _FUNC_BLOCK.search(before):
            break                         # reached the function itself
        out.append((a, b))
    return out


def _panel_advice_offenders(js: str) -> list:
    """Every place a kind whose advice names the Ingest Summary is raised on
    a path that never draws one."""
    named = [k for k, txt in _next_actions().items()
             if "ingest summary" in txt.lower()]
    bm = _brace_map(js)
    bad = []
    for kind in named:
        for m in re.finditer(r"""["']%s["']""" % kind, js):
            blocks = _branch_blocks(js, bm, m.start())
            if any("renderIngestSummary" in js[a:b] for a, b in blocks):
                continue
            bad.append("line %d raises %r, whose advice sends the operator "
                       "to the Ingest Summary, on a path that never draws "
                       "one" % (js[:m.start()].count("\n") + 1, kind))
    return bad


def test_advice_naming_a_panel_is_only_given_where_that_panel_is_drawn():
    assert [k for k, txt in _next_actions().items()
            if "ingest summary" in txt.lower()], (
        "no advice names the Ingest Summary any more -- point this rail at "
        "whatever panel the advice now names, or delete it")
    assert not _panel_advice_offenders(_js()), (
        "\n".join(_panel_advice_offenders(_js())))


def test_the_panel_rail_catches_the_call_site_it_was_written_for():
    """Mutation proof: put the original defect back and the rail has to go
    red. A rail that cannot fail certifies anything."""
    js = _js()
    broken = js.replace('kind: "reference_files_only" });',
                        'kind: "no_report_in_drop" });', 1)
    assert broken != js, (
        "the pre-flight drop failure no longer raises reference_files_only, "
        "so this proof is pointed at nothing")
    found = _panel_advice_offenders(broken)
    assert found, "the rail walked straight past the defect it exists for"
    assert any("no_report_in_drop" in f for f in found), found


def test_the_drop_that_never_left_the_browser_says_what_to_drop_instead():
    """Not only "stop pointing at the missing panel": the operator still has
    to be told the one thing that does work."""
    actions = _next_actions()
    js = _js()
    for fn, opener, closer in (
            ("uploadBundle", "async function uploadBundle(list) {",
             "\n  // LAYER 2"),
            ("postEnrichmentBundle",
             "function postEnrichmentBundle(newFiles) {",
             "\n  var enrichTooBig")):
        body = _fn_body(js, opener, closer)
        kinds = re.findall(r"""kind:\s*["'](\w+)["']""", body)
        assert kinds, "%s no longer declares a kind for its pre-flight" % fn
        for kind in kinds:
            advice = actions.get(kind, "")
            assert advice, "%s raises %r, which has no advice" % (fn, kind)
            assert "ingest summary" not in advice.lower(), (
                "%s fails before anything is sent, so no Ingest Summary is "
                "drawn -- and its advice sends the operator to read one" % fn)
            assert ".xml" in advice, (
                "%s says what will not work and never names the file that "
                "does: %r" % (fn, advice))
