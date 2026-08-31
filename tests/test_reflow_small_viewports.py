"""Reflow: 320 CSS px, 200% zoom, and author text-spacing overrides.

WCAG 2.2 AA, three success criteria at once:

  1.4.10 Reflow          — no loss of content or function at 320 CSS px, and
                           no two-dimensional scrolling to read a line.
  1.4.4  Resize Text     — the same at 200% zoom (a 1280x864 desk screen
                           zoomed to 200% IS a 640x432 CSS-pixel viewport).
  1.4.12 Text Spacing    — none of it falls apart when the reader forces
                           line-height 1.5 / letter-spacing 0.12em /
                           word-spacing 0.16em / paragraph spacing 2em.

WHY THIS IS MEASURED IN A BROWSER AND NOT READ OUT OF THE CSS. Every defect
this file exists to prevent was invisible in the source and obvious on the
page. The three that were actually found, at 320px, with a real report
converted:

  * the preflight rule name `sql.lexical_where_dropped.P_WHERE_INV` rendered
    300px wide inside a 263px card whose overflow is hidden for its rounded
    corners — so the rest of the name was GONE, with no scrollbar to hint at
    it. Same for a connection string in the deploy checklist (408px).
  * `.burst-meta` is a paragraph of prose wearing the pill styling, which is
    display:inline-flex: max-content width, no wrapping, 676px inside a
    263px card, clipped at 320px AND at 200% zoom.
  * the generated T-SQL on the Live Data tab was a bare <pre> with
    overflow:visible — 2,948px wide, dragging the whole views region out to
    2,970px so that reading any ordinary sentence meant scrolling sideways
    first.

THE GATE HAS TO BE ABLE TO GO RED. Three mutation tests below break the
fixes on purpose (put back overflow-wrap:normal, put back overflow:visible
on <pre>, drop a 900px box into a card) and assert the probe reports it. A
detector that cannot fail certifies anything.
"""
from __future__ import annotations

import re
import socket
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

FRONTEND = ROOT / "frontend"
STYLE = FRONTEND / "static" / "css" / "style.css"
SCROLL_JS = FRONTEND / "static" / "js" / "scroll_regions.js"
INDEX = FRONTEND / "templates" / "index.html"

# 320x800  = WCAG 1.4.10's own yardstick.
# 640x432  = a 1280x864 screen at 200% browser zoom (1.4.4).
SMALL = {"width": 320, "height": 800}
ZOOMED = {"width": 640, "height": 432}

# The reader's own spacing, straight from SC 1.4.12. pre/code are excluded
# the way the criterion excludes them: their spacing is part of the content.
TEXT_SPACING_CSS = (
    "*:not(pre):not(code):not(kbd):not(samp){"
    "line-height:1.5 !important;letter-spacing:0.12em !important;"
    "word-spacing:0.16em !important;}"
    "p,li{margin-bottom:2em !important;}"
)


# ===================================================== layer 1: the source
def test_the_stylesheet_declares_the_reflow_layer():
    """The rules are load-bearing; a later edit that drops them should
    have to delete an explanation, not just a line."""
    css = STYLE.read_text(encoding="utf-8")
    assert "REFLOW LAYER" in css, "the reflow layer and its reasoning are gone"
    # wide preformatted content scrolls in its own box
    assert re.search(r"\.tab-panels\s+pre[^{]*\{[^}]*overflow-x\s*:\s*auto", css), (
        "no rule makes a <pre> inside the views scroll in its own box; without "
        "it one wide SQL listing pushes the whole page sideways")
    # long tokens wrap instead of being clipped away by a card
    assert re.search(r"\.tab-panels\s+code[^{]*\{[^}]*overflow-wrap\s*:\s*anywhere", css), (
        "no overflow-wrap on code in the views; long identifiers get clipped "
        "by the cards' overflow:hidden with no scrollbar to reveal them")


def test_the_scroll_region_script_is_actually_loaded():
    """A helper nobody includes helps nobody."""
    assert SCROLL_JS.exists(), "frontend/static/js/scroll_regions.js is missing"
    assert "scroll_regions.js" in INDEX.read_text(encoding="utf-8"), (
        "index.html does not load scroll_regions.js, so the scrolling boxes "
        "never become keyboard-reachable")


# ===================================================== layer 2: the page
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
        except Exception as exc:                      # no browser binary
            pytest.skip("chromium unavailable: %s" % exc)
        yield b
        b.close()


# --- the probe, in one place ---------------------------------------------
# Every element that sticks out past the viewport is classified by what its
# ancestors do with the overflow:
#   scrolls-in-container -> a box of its own scrolls: the DESIGNED behaviour
#   clipped              -> an overflow:hidden ancestor: the content is LOST
#   pushes-region        -> nothing catches it: the page/region scrolls in 2D
PROBE = r"""
() => {
  const vw = window.innerWidth, de = document.documentElement;
  const sx = el => { const o = getComputedStyle(el).overflowX;
                     return o === 'auto' || o === 'scroll'; };
  const cx = el => { const o = getComputedStyle(el).overflowX;
                     return o === 'hidden' || o === 'clip'; };
  const out = [];
  document.querySelectorAll('.tab-panels *, .sidebar *, .topbar *').forEach(el => {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') return;
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) return;
    if (r.right <= vw + 1) return;
    let kind = 'pushes-region';
    for (let p = el.parentElement; p; p = p.parentElement) {
      if (sx(p)) { kind = 'scrolls-in-container'; break; }
      if (cx(p)) { kind = 'clipped'; break; }
    }
    if (kind === 'scrolls-in-container') return;
    out.push(kind + ' <' + el.tagName.toLowerCase() +
             ' class="' + (el.className || '').toString().slice(0, 40) + '"> ' +
             'right=' + Math.round(r.right) + 'px :: ' +
             (el.textContent || '').trim().slice(0, 48));
  });
  const panels = document.querySelector('.tab-panels');
  return {
    viewport: vw,
    bodyScrollsX: de.scrollWidth > de.clientWidth,
    panelsScrollsX: panels ? panels.scrollWidth > panels.clientWidth + 1 : false,
    lost: out.slice(0, 12),
    lostCount: out.length,
    marked: document.querySelectorAll('[data-scrollable="x"]').length
  };
}
"""


def _open(browser, live_url, viewport):
    pg = browser.new_page(viewport=dict(viewport))
    # Offline-proof: nothing but the app itself may load.
    pg.route("**/*", lambda route: route.continue_()
             if "127.0.0.1" in route.request.url else route.abort())
    pg.goto(live_url, wait_until="domcontentloaded")
    pg.wait_for_function("() => !!document.querySelector('.tab')")
    return pg


def _convert_sample(pg):
    """Fill the views with a real conversion (the bundled sample, so this
    test carries no report data of its own)."""
    chip = pg.query_selector("#samples-list .sample-chip")
    if chip is None:
        pytest.skip("no bundled sample to convert")
    chip.click()
    pg.wait_for_function(
        "() => (document.querySelector('#status-pill')||{}).textContent"
        "        && /converted/i.test(document.querySelector('#status-pill').textContent)",
        timeout=60000)
    # Advanced views hold the widest content (RDL XML, Live Data, Extras).
    pg.evaluate("""() => { const b = document.querySelector('#advanced-toggle');
        if (b && !document.body.classList.contains('show-advanced')) b.click(); }""")
    pg.wait_for_timeout(300)


def _walk_tabs(pg):
    """Measure every view, since each renders different content."""
    reports = []
    count = pg.evaluate("() => document.querySelectorAll('.tab').length")
    for i in range(count):
        pg.evaluate("(i) => document.querySelectorAll('.tab')[i].click()", i)
        pg.wait_for_timeout(250)
        name = pg.evaluate(
            "(i) => document.querySelectorAll('.tab')[i].textContent.trim().split('\\n')[0]", i)
        r = pg.evaluate(PROBE)
        r["tab"] = name
        reports.append(r)
    return reports


def _assert_reflows(reports, where):
    for r in reports:
        assert not r["bodyScrollsX"], (
            "%s: the PAGE scrolls sideways on the %s view — reading a line "
            "should never need two-dimensional scrolling (WCAG 1.4.10)"
            % (where, r["tab"]))
        assert not r["panelsScrollsX"], (
            "%s: the views region scrolls sideways on the %s view; wide "
            "content must scroll in its own box, not drag the region"
            % (where, r["tab"]))
        assert r["lostCount"] == 0, (
            "%s: content is clipped or pushed out of reach on the %s view:\n  %s"
            % (where, r["tab"], "\n  ".join(r["lost"])))


@pytest.fixture(scope="module")
def small_page(browser, live_url):
    pg = _open(browser, live_url, SMALL)
    _convert_sample(pg)
    yield pg
    pg.close()


def test_nothing_is_lost_at_320px(small_page):
    """SC 1.4.10 Reflow, at the criterion's own width, on every view."""
    _assert_reflows(_walk_tabs(small_page), "320x800")


def test_nothing_is_lost_at_200_percent_zoom(browser, live_url):
    """SC 1.4.4 Resize Text: a 1280x864 desk screen zoomed to 200%."""
    pg = _open(browser, live_url, ZOOMED)
    try:
        _convert_sample(pg)
        _assert_reflows(_walk_tabs(pg), "640x432 (200% zoom)")
    finally:
        pg.close()


def test_nothing_is_lost_with_the_readers_own_text_spacing(browser, live_url):
    """SC 1.4.12: the reader widens the text and nothing disappears."""
    pg = _open(browser, live_url, SMALL)
    try:
        _convert_sample(pg)
        pg.add_style_tag(content=TEXT_SPACING_CSS)
        pg.wait_for_timeout(250)
        _assert_reflows(_walk_tabs(pg), "320x800 + 1.4.12 text spacing")
    finally:
        pg.close()


def test_the_empty_state_reflows_too(browser, live_url):
    """Before any conversion the page is all sidebar and empty state — the
    first thing the operator sees has to survive 320px as well."""
    pg = _open(browser, live_url, SMALL)
    try:
        r = pg.evaluate(PROBE)
        assert not r["bodyScrollsX"] and r["lostCount"] == 0, r["lost"]
    finally:
        pg.close()


def test_wide_content_scrolls_inside_its_own_box(small_page):
    """The RDL listing is 30,000px wide. It is allowed to be — inside a box
    that scrolls. What it may not do is widen the page."""
    small_page.evaluate("""() => { const t = Array.from(document.querySelectorAll('.tab'))
        .find(x => /RDL/i.test(x.textContent)); if (t) t.click(); }""")
    small_page.wait_for_timeout(400)
    box = small_page.evaluate("""() => {
        const pre = document.querySelector('.tab-panels pre.code-block');
        if (!pre) return null;
        const cs = getComputedStyle(pre);
        return { overflowX: cs.overflowX, sw: pre.scrollWidth, cw: pre.clientWidth,
                 marked: pre.getAttribute('data-scrollable'),
                 tabindex: pre.getAttribute('tabindex'),
                 label: pre.getAttribute('aria-label') || '' }; }""")
    assert box, "the RDL view has no code block to measure"
    assert box["overflowX"] in ("auto", "scroll"), (
        "the RDL listing does not scroll in its own box (overflow-x=%s)"
        % box["overflowX"])
    assert box["sw"] > box["cw"], "expected the RDL listing to be wider than its box"
    # ...and a box that scrolls has to be reachable without a mouse.
    assert box["marked"] == "x" and box["tabindex"] == "0", (
        "the scrolling listing is not a tab stop, so a keyboard user cannot "
        "move it (WCAG 2.1.1); got %r" % (box,))
    assert box["label"].strip(), "the scrolling box has no accessible name"


def test_a_scrolling_box_moves_with_the_keyboard(small_page):
    """Measured in Chromium: with focus ON the scroller, ArrowRight scrolls
    the DOCUMENT and leaves the box where it was. So the keys are handled by
    scroll_regions.js, and this is the proof they still are."""
    small_page.evaluate("""() => { const t = Array.from(document.querySelectorAll('.tab'))
        .find(x => /RDL/i.test(x.textContent)); if (t) t.click(); }""")
    small_page.wait_for_timeout(400)
    small_page.evaluate("""() => { const el = document.querySelector('[data-scrollable="x"]');
        el.scrollLeft = 0; el.focus(); window.scrollTo(0, 0); }""")
    before = small_page.evaluate(
        "() => ({ left: document.querySelector('[data-scrollable=\"x\"]').scrollLeft,"
        "         pageY: window.scrollY })")
    for _ in range(4):
        small_page.keyboard.press("ArrowRight")
    after = small_page.evaluate(
        "() => ({ left: document.querySelector('[data-scrollable=\"x\"]').scrollLeft,"
        "         pageY: window.scrollY })")
    assert after["left"] > before["left"], (
        "ArrowRight did not scroll the focused box (%r -> %r)" % (before, after))
    small_page.keyboard.press("End")
    end = small_page.evaluate(
        "() => { const el = document.querySelector('[data-scrollable=\"x\"]');"
        "        return { left: el.scrollLeft, max: el.scrollWidth - el.clientWidth }; }")
    assert end["left"] >= end["max"] - 24, "End did not reach the far edge: %r" % (end,)
    small_page.keyboard.press("Home")
    assert small_page.evaluate(
        "() => document.querySelector('[data-scrollable=\"x\"]').scrollLeft") == 0, (
        "Home did not return to the start of the line")


def test_the_focus_ring_on_a_scrolling_box_is_not_clipped_away(small_page):
    """Making these boxes focusable creates a new way to fail: they fill
    their card edge to edge, and the card clips for its rounded corners, so
    a ring drawn OUTSIDE the box is cut off on both sides and the keyboard
    user cannot see where they are (WCAG 2.4.7)."""
    small_page.evaluate("""() => { const t = Array.from(document.querySelectorAll('.tab'))
        .find(x => /RDL/i.test(x.textContent)); if (t) t.click(); }""")
    small_page.wait_for_timeout(400)
    rings = small_page.evaluate("""() => {
        const clips = el => { const o = getComputedStyle(el).overflowX;
                              return o === 'hidden' || o === 'clip'; };
        return Array.from(document.querySelectorAll('[data-scrollable="x"]'))
          .filter(el => el.getBoundingClientRect().width > 0)
          .map(el => {
            const cs = getComputedStyle(el);
            const off = parseFloat(cs.outlineOffset) || 0;
            const w = parseFloat(cs.outlineWidth) || 2;
            const r = el.getBoundingClientRect();
            let host = el.parentElement;
            while (host && !clips(host)) host = host.parentElement;
            if (!host) return { fits: true };          // nothing clips it
            const h = host.getBoundingClientRect();
            return { fits: (r.left - off - w) >= h.left - 0.5 &&
                           (r.right + off + w) <= h.right + 0.5,
                     offset: off, cls: (el.className || '').toString().slice(0, 30) };
          }); }""")
    assert rings, "no scrolling box to check"
    bad = [r for r in rings if not r["fits"]]
    assert not bad, (
        "the focus ring on a scrolling box is drawn outside a card that "
        "clips it, so it is invisible: %r" % (bad,))


def test_a_scrolling_table_keeps_its_table_semantics(small_page):
    """Making a box focusable must not cost more than it buys: role=group on
    a <table> would take the rows, columns and header associations away from
    a screen-reader user. The tab stop and the name go on; the role does
    not."""
    made = small_page.evaluate("""() => {
        const panel = Array.from(document.querySelectorAll('.tab-panel'))
                           .find(p => p.getBoundingClientRect().width > 0);
        const wrap = document.createElement('div');
        wrap.id = '__tbl_probe';
        let cells = '';
        for (let i = 0; i < 14; i++) cells += '<th scope="col">Column ' + i + '</th>';
        wrap.innerHTML = '<table style="overflow-x:auto; display:block;'
                       + ' white-space:nowrap"><caption>Probe</caption>'
                       + '<thead><tr>' + cells + '</tr></thead></table>';
        panel.appendChild(wrap);
        return true; }""")
    assert made
    small_page.wait_for_timeout(100)
    small_page.evaluate("() => window.o2sMarkScrollRegions()")
    got = small_page.evaluate("""() => {
        const t = document.querySelector('#__tbl_probe table');
        return { marked: t.getAttribute('data-scrollable'),
                 tabindex: t.getAttribute('tabindex'),
                 role: t.getAttribute('role'),
                 label: t.getAttribute('aria-label') || '' }; }""")
    small_page.evaluate("() => document.getElementById('__tbl_probe').remove()")
    assert got["marked"] == "x" and got["tabindex"] == "0", (
        "a table wide enough to scroll did not become a tab stop: %r" % (got,))
    assert got["role"] is None, (
        "a role was written onto a <table>, overriding its table semantics: %r"
        % (got,))
    assert got["label"].strip(), "the scrolling table has no accessible name"


def test_the_report_preview_sheet_is_keyboard_scrollable(small_page):
    """A report page is fixed-width by nature (1.4.10's own exception for
    content that needs a two-dimensional layout), so it scrolls — and the
    scrolling has to work without a mouse like everything else."""
    small_page.evaluate("""() => { document.querySelectorAll('.tab')[0].click();
        const b = document.querySelector('#mockup-mode-backend'); if (b) b.click(); }""")
    small_page.wait_for_timeout(1200)
    small_page.evaluate("() => window.o2sMarkScrollRegions && window.o2sMarkScrollRegions()")
    desk = small_page.evaluate("""() => {
        const d = document.querySelector('.mockup-host .o2s-desk');
        if (!d) return null;
        return { scrolls: d.scrollWidth > d.clientWidth + 2,
                 marked: d.getAttribute('data-scrollable'),
                 tabindex: d.getAttribute('tabindex'),
                 label: d.getAttribute('aria-label') || '' }; }""")
    if desk is None or not desk["scrolls"]:
        pytest.skip("this sample's mockup fits at 320px; nothing to scroll")
    assert desk["marked"] == "x" and desk["tabindex"] == "0", (
        "the simulated report sheet scrolls but is not a tab stop: %r" % (desk,))
    assert desk["label"].strip(), "the scrolling sheet has no accessible name"


# The three shapes that were measured failing on a real report. Injected
# rather than converted, so the check needs no report data of its own and
# holds whatever the operator happens to open.
HOSTILE = """() => {
  // It has to go somewhere VISIBLE: a hidden panel measures 0x0 and the
  // probe skips it, which would make this guard pass while testing nothing.
  const visible = el => el && el.getBoundingClientRect().width > 0;
  const panel = Array.from(document.querySelectorAll('.tab-panel'))
                     .find(p => visible(p));
  if (!panel) return { injected: 0 };
  const card = Array.from(panel.querySelectorAll('.panel-card')).find(visible)
            || panel;
  const add = (host, html) => { const d = document.createElement('div');
                                d.className = '__hostile'; d.innerHTML = html;
                                host.appendChild(d); };
  // 1. a preflight rule name and a connection string: long, unbreakable,
  //    inside a card that clips for its rounded corners.
  add(card, '<p><code>sql.lexical_where_dropped.P_WHERE_INV_LONG_NAME</code></p>');
  add(card, '<p><code>Data Source=YOUR_SQL_SERVER;Initial Catalog=AppDb;'
          + 'Integrated Security=SSPI;</code></p>');
  // 2. prose wearing the pill class (the .burst-meta shape). The emphasis
  //    tags matter: under display:inline-flex each one is a FLEX ITEM, and
  //    flex items do not wrap onto a second line — that is precisely how a
  //    paragraph ends up 493px wide inside a 263px card.
  add(card, '<div class="burst-meta">Your <b>main report</b> (<code>REPORT'
          + '_NAME</code>) lists many records. A link on each row opens the '
          + '<i>child</i> report; build it in the <b>Sub-Reports</b> view '
          + '<b>before</b> this one, <i>in the same order</i>, or '
          + '<b>generate all</b> will not find it.</div>');
  // 3. a wide preformatted listing with no card around it.
  const pre = document.createElement('pre');
  pre.className = '__hostile';
  pre.textContent = 'SELECT ' + 'A.SOME_COLUMN_NAME, '.repeat(60) + 'FROM DUAL';
  panel.appendChild(pre);
  // Report back what was actually PLACED and MEASURED, so a test can refuse
  // to pass on an injection that never rendered.
  const shown = Array.from(document.querySelectorAll('.__hostile'))
                     .filter(e => e.getBoundingClientRect().width > 0);
  return { injected: shown.length, cardClips:
           getComputedStyle(card).overflowX === 'hidden' };
}"""


def test_the_shapes_that_broke_it_before_stay_fixed(small_page):
    """The regression guard proper. Every one of these was measured LOST at
    320px before the reflow layer existed; none of them may be again."""
    placed = small_page.evaluate(HOSTILE)
    small_page.wait_for_timeout(250)
    assert placed["injected"] >= 4, (
        "the hostile shapes did not render, so this guard would pass without "
        "testing anything: %r" % (placed,))
    r = small_page.evaluate(PROBE)
    small_page.evaluate("() => document.querySelectorAll('.__hostile')"
                        ".forEach(e => e.remove())")
    assert r["lostCount"] == 0 and not r["panelsScrollsX"] and not r["bodyScrollsX"], (
        "a known-bad shape is clipped or widens the page again:\n  %s"
        % "\n  ".join(r["lost"] or ["(region or page scrolls sideways)"]))


# ============================================ the proof the gate can fail
# Each of these BREAKS the page on purpose, so each gets a page of its own:
# a mutation that leaked into the shared one would turn the tests above into
# theatre.
@pytest.fixture()
def broken_page(browser, live_url):
    pg = _open(browser, live_url, SMALL)
    _convert_sample(pg)
    yield pg
    pg.close()


def test_the_probe_reports_a_box_that_really_overflows(broken_page):
    """Mutation proof of the DETECTOR. Drop a 900px box into a card that
    clips, and the probe has to say so — otherwise every green run above
    means nothing."""
    clean = broken_page.evaluate(PROBE)
    assert clean["lostCount"] == 0, "start from a clean page: %r" % (clean["lost"],)
    broken_page.evaluate("""() => {
        const card = document.querySelector('.tab-panels .panel-card')
                  || document.querySelector('.tab-panels > *');
        const d = document.createElement('div');
        d.id = '__overflow_probe'; d.style.width = '900px'; d.style.height = '20px';
        d.textContent = 'x'; card.appendChild(d); }""")
    broken_page.wait_for_timeout(150)
    dirty = broken_page.evaluate(PROBE)
    assert dirty["lostCount"] > 0, (
        "the probe did not notice a 900px box inside a 263px card — it is "
        "blind to exactly the defect it exists to catch")


def test_putting_back_overflow_wrap_normal_reintroduces_the_clipping(broken_page):
    """Mutation proof of FIX 1. Undo the wrap rule, hand a card the kind of
    identifier the converter really prints (a package function, a path, a
    connection string), and it is clipped away again with no scrollbar."""
    long_token = "Pkg_Haz_Util.F_Get_Task_Corresp_Type_And_More_Besides_Xyz"
    inject = """(tok) => {
        const card = document.querySelector('.tab-panels .panel-card')
                  || document.querySelector('.tab-panels > *');
        const p = document.createElement('p');
        p.id = '__wrap_probe';
        p.innerHTML = '<code>' + tok + '</code>';
        card.appendChild(p); }"""
    broken_page.evaluate(inject, long_token)
    broken_page.wait_for_timeout(150)
    assert broken_page.evaluate(PROBE)["lostCount"] == 0, (
        "a long identifier is ALREADY being clipped with the fix in place")
    broken_page.add_style_tag(content=(
        ".tab-panels, .tab-panels *{ overflow-wrap:normal !important;"
        " word-break:normal !important; }"))
    broken_page.wait_for_timeout(200)
    broken = broken_page.evaluate(PROBE)
    assert broken["lostCount"] > 0, (
        "with overflow-wrap back to normal the long identifier was not "
        "reported as clipped — the probe is blind to the original defect")


def test_putting_the_pill_display_back_on_burst_meta_clips_the_prose(broken_page):
    """Mutation proof of FIX 3. .burst-meta is a paragraph of prose that
    shares the pill styling; as inline-flex it takes its max-content width
    and the card cuts it off."""
    placed = broken_page.evaluate(HOSTILE)
    broken_page.wait_for_timeout(200)
    assert placed["injected"] >= 4 and placed["cardClips"], (
        "this proof needs the prose inside a card that really clips: %r"
        % (placed,))
    assert broken_page.evaluate(PROBE)["lostCount"] == 0
    broken_page.add_style_tag(content=".burst-meta{ display:inline-flex !important; }")
    broken_page.wait_for_timeout(200)
    broken = broken_page.evaluate(PROBE)
    assert any("burst-meta" in line for line in broken["lost"]), (
        "prose back on the pill display was not reported as clipped: %r"
        % (broken["lost"],))


def test_putting_back_overflow_visible_on_pre_drags_the_region_sideways(broken_page):
    """Mutation proof of FIX 2, on the shape the defect actually had: a
    <pre> sitting straight in a view (like the generated T-SQL on Live
    Data), with no card around it to clip anything. With the rule it
    scrolls in its own box; without it, it drags the whole region."""
    broken_page.evaluate("""() => {
        const panel = document.querySelector('.tab-panel:not([hidden])');
        const pre = document.createElement('pre');
        pre.id = '__wide_pre';
        pre.textContent = 'SELECT ' + 'A.COLUMN_NAME, '.repeat(80) + 'FROM DUAL';
        panel.appendChild(pre); }""")
    broken_page.wait_for_timeout(200)
    fixed = broken_page.evaluate(PROBE)
    assert fixed["panelsScrollsX"] is False and fixed["lostCount"] == 0, (
        "a wide <pre> dropped into a view already widens the region: %r"
        % (fixed["lost"],))
    # Both axes: with overflow-y left at auto, CSS computes overflow-x back
    # to auto and the mutation would quietly not happen.
    broken_page.add_style_tag(content=".tab-panels pre{ overflow:visible !important; }")
    broken_page.wait_for_timeout(200)
    broken = broken_page.evaluate(PROBE)
    assert broken["panelsScrollsX"] is True, (
        "a wide listing with overflow:visible did not widen the region — "
        "the region measurement is not measuring anything")
