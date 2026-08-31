"""Semantic structure, keyboard operability and focus.

The person who runs this tool every day is deaf and works by keyboard and
by eye. Everything asserted here decides whether the app can be OPERATED at
all, so none of it is checked by reading source: it is measured on the page
the server actually sends, and on the DOM a real browser builds from it.

Three layers:

  1. SERVED MARKUP (always runs). Landmarks, exactly one h1, no skipped
     heading level, a programmatic label on every control, the ARIA tabs
     wiring, the skip link. These are facts about the bytes Flask sends.

  2. STYLESHEET CONTRACT (always runs). One focus-ring design, drawn with
     the theme's own token, and no rule anywhere that takes the outline
     away without putting a ring back.

  3. LIVE DOM (skipped when Playwright + Chromium are missing). Focus
     order, focus traps, Escape and focus restore on the dialog, arrow-key
     tab navigation and measured target sizes only exist once CSS and JS
     have run, so they are driven in a real browser against a real HTTP
     server -- with every external host blocked, which also proves the page
     needs nothing from the network.

The focus walker is itself mutation-proved: test_focus_walker_detects_a_real_trap
builds a page with a genuine keyboard trap and asserts the walker says so.
A walker that cannot fail would certify anything.
"""
from __future__ import annotations

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
STYLE = FRONTEND / "static" / "css" / "style.css"
APP_JS = FRONTEND / "static" / "js" / "app.js"
DEMO_JS = FRONTEND / "static" / "js" / "demo_mode.js"

# Anything the browser puts in the tab order by default, plus explicit
# tabindex. Written as XPath because lxml here has no cssselect.
FOCUSABLE_XPATH = (
    "//a[@href] | //button | //input | //select | //textarea"
    " | //*[@tabindex and @tabindex!='-1']"
)


# ============================================================ layer 1
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


def _text(node) -> str:
    return " ".join(("".join(node.itertext())).split())


def _tag(node) -> str:
    return "<%s id=%r class=%r>" % (node.tag, node.get("id"), node.get("class"))


def test_exactly_one_h1_names_the_application(dom):
    h1s = dom.xpath("//h1")
    assert len(h1s) == 1, (
        "the page needs exactly one h1; found %r" % [_text(h) for h in h1s])
    assert _text(h1s[0]), "the h1 must not be empty"


def test_heading_levels_never_skip(dom):
    """h1 -> h3 reads to a screen reader as a section that is not there."""
    levels = [(int(n.tag[1]), _text(n)) for n in dom.xpath(
        "//*[self::h1 or self::h2 or self::h3 or self::h4"
        " or self::h5 or self::h6]")]
    assert levels, "the page has no headings at all"
    assert levels[0][0] == 1, "the first heading must be the h1, got %r" % (levels[0],)
    prev = levels[0][0]
    for lvl, txt in levels[1:]:
        assert lvl <= prev + 1, (
            "heading level jumps h%d -> h%d at %r" % (prev, lvl, txt))
        prev = lvl


def test_page_uses_the_document_landmarks(dom):
    for tag in ("header", "nav", "main", "aside", "footer"):
        assert dom.xpath("//%s" % tag), "no <%s> landmark in the document" % tag
    mains = dom.xpath("//main")
    assert len(mains) == 1, "more than one <main>"
    assert mains[0].get("id") == "main", "the skip link needs #main to aim at"
    assert mains[0].get("tabindex") == "-1", (
        "without tabindex=-1 the skip link scrolls the page but leaves focus "
        "behind, so the next Tab drops back into the sidebar")


def _landmark_role(node):
    """The role a browser actually computes for this element (HTML-AAM).

    The catch this exists for: <header> and <footer> only become banner /
    contentinfo when they are NOT scoped inside a sectioning element. A
    <footer> tucked inside <aside> is just a generic box, so a page can
    look like it has a footer landmark while exposing none.
    """
    explicit = (node.get("role") or "").strip()
    if explicit:
        return explicit
    scoping = {"article", "aside", "main", "nav", "section"}
    if node.tag in ("header", "footer"):
        if any(a.tag in scoping for a in node.iterancestors()):
            return None
        return "banner" if node.tag == "header" else "contentinfo"
    return {"nav": "navigation", "main": "main", "aside": "complementary"}.get(
        node.tag)


def test_the_page_exposes_all_five_document_landmarks(dom):
    """Landmark navigation is how a screen-reader user moves between the
    regions of the page without walking every control in between."""
    found = {}
    for node in dom.xpath("//header | //footer | //nav | //main | //aside"
                          " | //*[@role]"):
        role = _landmark_role(node)
        if role in ("banner", "navigation", "main", "complementary",
                    "contentinfo"):
            found.setdefault(role, []).append(_tag(node))
    missing = [r for r in ("banner", "navigation", "main", "complementary",
                           "contentinfo") if r not in found]
    assert not missing, (
        "no %s landmark on the page (found %s). Remember that <header> and "
        "<footer> stop being landmarks once they sit inside <aside>, <main>, "
        "<nav>, <article> or <section>: they need an explicit role there."
        % (" or ".join(missing), sorted(found)))
    for solo in ("banner", "main", "contentinfo"):
        assert len(found[solo]) == 1, (
            "%d %s landmarks; a duplicate makes the landmark list ambiguous: "
            "%s" % (len(found[solo]), solo, found[solo]))


def test_a_nav_is_a_landmark_and_carries_a_name(dom):
    navs = dom.xpath("//nav")
    assert navs
    for nav in navs:
        assert nav.get("role") != "tablist", (
            "role=tablist on <nav> REPLACES the navigation landmark; put the "
            "tablist on a child element instead")
        assert nav.get("aria-label") or nav.get("aria-labelledby"), (
            "%s: a navigation landmark needs a name" % _tag(nav))
    for aside in dom.xpath("//aside"):
        assert aside.get("aria-label") or aside.get("aria-labelledby"), (
            "%s: the complementary landmark needs a name" % _tag(aside))


def test_skip_link_is_the_first_focusable_and_targets_main(dom):
    focusable = dom.xpath(FOCUSABLE_XPATH)
    assert focusable, "no focusable elements at all"
    first = focusable[0]
    assert first.tag == "a" and first.get("href") == "#main", (
        "the FIRST focusable element must be the skip link, got %s"
        % _tag(first))
    assert _text(first), "the skip link needs visible text once it is focused"


def test_every_control_has_a_programmatic_label(dom):
    """A placeholder is not a label: it vanishes the moment you type."""
    labelled = {lab.get("for") for lab in dom.xpath("//label[@for]")}
    problems = []
    for ctl in dom.xpath("//input | //select | //textarea"):
        if ctl.get("type") == "hidden" or ctl.get("hidden") is not None:
            continue
        named = (ctl.get("id") in labelled
                 or ctl.get("aria-label")
                 or ctl.get("aria-labelledby")
                 or any(a.tag == "label" for a in ctl.iterancestors()))
        if not named:
            problems.append(_tag(ctl))
    assert not problems, "controls with no label: %s" % problems


def test_every_interactive_element_has_an_accessible_name(dom):
    problems = []
    for node in dom.xpath("//a[@href] | //button | //*[@tabindex]"):
        if node.get("tabindex") == "-1":
            continue
        if (node.get("aria-label") or node.get("aria-labelledby")
                or _text(node) or node.get("title")):
            continue
        problems.append(_tag(node))
    assert not problems, "controls announced as nothing: %s" % problems


def test_tabs_are_wired_to_their_panels(dom):
    tabs = dom.xpath('//*[@role="tab"]')
    assert len(tabs) >= 2, "expected a tab strip"
    selected = 0
    for t in tabs:
        who = t.get("data-tab")
        assert t.tag == "button", "tab %r must be a real button" % who
        assert t.get("id"), "tab %r needs an id for the panel to point at" % who
        assert t.get("aria-selected") in ("true", "false"), (
            "tab %r has no aria-selected: a screen reader announces the strip "
            "with no current tab" % who)
        if t.get("aria-selected") == "true":
            selected += 1
        panel_id = t.get("aria-controls")
        assert panel_id, "tab %r has no aria-controls" % who
        panel = dom.get_element_by_id(panel_id, None)
        assert panel is not None, "aria-controls=%r matches nothing" % panel_id
        assert panel.get("role") == "tabpanel", (
            "%s is a tab's panel but has no tabpanel role" % _tag(panel))
        assert panel.get("aria-labelledby") == t.get("id"), (
            "panel %r must point back at tab %r" % (panel_id, t.get("id")))
        want = "0" if t.get("aria-selected") == "true" else "-1"
        assert t.get("tabindex") == want, (
            "tab %r has tabindex=%r, expected %r: ARIA tabs use a ROVING "
            "tabindex so the strip is one tab stop, not nine"
            % (who, t.get("tabindex"), want))
    assert selected == 1, "%d tabs marked selected, expected exactly 1" % selected


def test_tablist_contains_nothing_but_tabs(dom):
    lists = dom.xpath('//*[@role="tablist"]')
    assert lists, "no tablist in the document"
    for tl in lists:
        for child in tl:
            if not isinstance(child.tag, str):
                continue                      # comment / processing instruction
            if child.tag in ("script", "template"):
                continue
            assert child.get("role") == "tab", (
                "%s sits inside the tablist but is not a tab; assistive tech "
                "then announces one more tab than the strip has" % _tag(child))


def test_nothing_fakes_a_button(dom):
    fakes = [_tag(n) for n in dom.xpath('//*[@role="button"]') if n.tag != "button"]
    assert not fakes, "role=button on a non-button element: %s" % fakes
    js = APP_JS.read_text(encoding="utf-8")
    assert 'role: "button"' not in js and "role: 'button'" not in js, (
        "app.js builds a div that pretends to be a button. Build a real "
        "<button>: Enter/Space, the role and the focus ring then come from "
        "the platform and cannot drift out of sync with a hand-rolled "
        "keydown handler")


def test_no_interactive_element_nests_inside_another(dom):
    for outer in dom.xpath("//button | //a[@href]"):
        inner = outer.xpath(
            ".//a[@href] | .//button | .//input | .//select | .//textarea")
        assert not inner, (
            "%s contains another interactive element; the nested one is "
            "unreachable and the markup is invalid" % _tag(outer))


def test_a_file_input_behind_a_label_is_never_display_hidden():
    """`hidden` on the input takes the whole control out of the tab order,
    so the visible label-button it sits behind becomes mouse-only."""
    js = APP_JS.read_text(encoding="utf-8")
    blocks = re.findall(
        r'<label[^>]*class="[^"]*subreport-add[^"]*".{0,500}?</label>',
        js, re.S)
    assert blocks, (
        "could not find the label-wrapped file input in app.js -- this test "
        "was passing by finding nothing at all")
    for block in blocks:
        assert "file-input-hidden" in block, (
            "the wrapped file input must be CLIPPED, not hidden: %s"
            % block[:180])
        assert not re.search(r"<input[^>]*\shidden[\s>]", block), block[:180]


def _js_tables(js):
    """Each literal <table> ... </table> region the JS writes."""
    out = []
    for m in re.finditer(r"<table\b", js):
        end = js.find("</table>", m.start())
        assert end != -1, "unterminated <table> in app.js"
        out.append(js[m.start():end])
    return out


def test_data_tables_carry_scoped_headers_and_a_caption():
    js = APP_JS.read_text(encoding="utf-8")
    tables = _js_tables(js)
    assert tables, "expected the JS to build tables"
    for region in tables:
        head = region[:60].replace("\n", " ")
        assert "<caption" in region, (
            "table %r has no caption naming what it lists" % head)
        ths = re.findall(r"<th\b[^>]*>", region)
        assert ths, "table %r has no <th> at all: it is a layout table" % head
        unscoped = [t for t in ths if "scope=" not in t]
        assert not unscoped, (
            "table %r has headers with no scope, so a screen reader cannot "
            "pair a cell with its header: %s" % (head, unscoped))
    # The DOM-built results table takes the same contract.
    assert 'el("caption"' in js, "the query-results table has no caption"
    assert re.search(r'el\("th",\s*\{\s*scope:', js), (
        "the query-results table builds unscoped <th> elements")
    assert 'role="presentation"' not in js, "no layout-only tables"


# ============================================================ layer 2: CSS
@pytest.fixture(scope="module")
def css() -> str:
    return STYLE.read_text(encoding="utf-8")


def _blocks(css_text):
    for m in re.finditer(r"([^{}]*)\{([^{}]*)\}", css_text):
        yield m.group(1).strip(), m.group(2)


def test_focus_is_never_silently_removed(css):
    """outline:none is allowed in exactly one place: withdrawing the ring
    for POINTER focus, which the paired :focus-visible rule puts back."""
    offenders = []
    for selector, body in _blocks(css):
        if not re.search(r"outline\s*:\s*(none|0)\b", body):
            continue
        if ":not(:focus-visible)" in selector:
            continue                      # the legitimate pointer-focus case
        if "outline" in re.sub(r"outline\s*:\s*(none|0)\b", "", body):
            continue                      # sets a different outline too
        offenders.append(selector[:90])
    assert not offenders, (
        "these rules remove the focus indicator and put nothing back: %s"
        % offenders)


def test_stylesheet_draws_one_focus_ring_from_the_theme_token(css):
    rules = [(s, b) for s, b in _blocks(css) if ":focus-visible" in s]
    assert rules, "no :focus-visible rule anywhere in the stylesheet"
    ring = [(s, b) for s, b in rules
            if "outline" in b and "--focus-ring" in b]
    assert ring, (
        "the focus ring must be drawn with var(--focus-ring) -- that is the "
        "token the theme layer keeps at >=3:1 in BOTH themes")
    # It has to cover the ordinary controls, not just one bespoke widget.
    covered = " ".join(s for s, _ in ring)
    for need in ("button:focus-visible", "input:focus-visible",
                 "a:focus-visible", "[tabindex]:focus-visible"):
        assert need in covered, "the focus ring does not cover %s" % need


def _rules_for(css_text, cls):
    """Rules that style the class ITSELF -- not its descendants.

    Matching on `cls in selector` made this test blind: `.batch-check input
    { height:18px }` satisfied a check meant for `.batch-check`.
    """
    out = []
    for sel, body in _blocks(css_text):
        for part in sel.split(","):
            last = re.split(r"[\s>+~]+", part.strip())[-1]
            if last == cls or last.startswith(cls + ":"):
                out.append(body)
                break
    return out


def test_small_controls_declare_the_24px_target_minimum(css):
    # .switch and .image-slot-input joined this list after being MEASURED
    # under the minimum on the live page: both live in states the first
    # pass never opened (a hidden tab panel, and a report that declares
    # seal/logo placeholders).
    for cls in (".btn-tiny", ".sample-chip", ".batch-file", ".batch-check",
                ".linkish", ".switch", ".image-slot-input"):
        rules = _rules_for(css, cls)
        assert rules, "no rule styles %s at all" % cls
        assert any(re.search(r"\bmin-height\s*:\s*(2[4-9]|[3-9]\d)", b)
                   for b in rules), (
            "%s declares no >=24px min-height; WCAG 2.2 SC 2.5.8 target size"
            % cls)


# ============================================================ layer 3: live
def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def live_url():
    """A real HTTP server, so a real browser can drive the real page."""
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


@pytest.fixture()
def page(browser, live_url):
    pg = browser.new_page(viewport={"width": 1280, "height": 900})
    # The machine this ships to is locked down and may be offline. Blocking
    # every host but the app keeps the test off the network AND proves the
    # page renders without a single external resource.
    pg.route("**/*", lambda route: route.continue_()
             if "127.0.0.1" in route.request.url else route.abort())
    pg.goto(live_url, wait_until="domcontentloaded")
    pg.wait_for_function("() => !!document.querySelector('.tab')")
    yield pg
    pg.close()


# --- the walker, and the proof it can fail --------------------------------
_DESCRIBE = """() => { const a = document.activeElement;
  if (!a) return 'NONE';
  return a.tagName + '#' + (a.id || '') + '.' + ((a.className || '') + '').split(' ')[0];
}"""


def walk_focus(pg, steps=80):
    """Press Tab `steps` times, recording where focus lands each time."""
    seen = []
    for _ in range(steps):
        pg.keyboard.press("Tab")
        seen.append(pg.evaluate(_DESCRIBE))
        if seen[-1].startswith("BODY"):
            break                    # focus left the document: no trap
    return seen


def find_trap(seen, run=6):
    """A trap is focus that stops moving: the same element `run` times."""
    for i in range(len(seen) - run + 1):
        window = seen[i:i + run]
        if len(set(window)) == 1:
            return window[0]
    return None


TRAPPED_PAGE = """
<!doctype html><title>trap</title>
<button id="a">one</button>
<button id="b">two</button>
<script>
  // A real keyboard trap: focus can get in and never gets out.
  document.getElementById('b').addEventListener('keydown', e => {
    if (e.key === 'Tab') { e.preventDefault(); document.getElementById('b').focus(); }
  });
</script>
"""


def test_focus_walker_detects_a_real_trap(browser):
    """Mutation proof. If the walker cannot see a trap it built itself, it
    proves nothing about the app -- so break it on purpose first."""
    pg = browser.new_page()
    try:
        pg.set_content(TRAPPED_PAGE)
        pg.eval_on_selector("#a", "n => n.focus()")
        seen = walk_focus(pg, steps=20)
        assert find_trap(seen) is not None, (
            "the walker walked straight past a deliberate keyboard trap: %s"
            % seen[:12])
    finally:
        pg.close()


def test_focus_order_reaches_every_control_and_never_traps(page):
    page.evaluate("() => document.body.focus()")
    seen = walk_focus(page, steps=80)
    assert find_trap(seen) is None, "keyboard trap at %r" % find_trap(seen)
    assert any(s.startswith("BODY") for s in seen), (
        "Tab never left the document in 80 presses: something is holding "
        "focus. Walk was: %s" % seen[:25])
    assert seen[0].endswith(".skip-link"), (
        "the skip link must be the first tab stop, got %r" % seen[0])

    # Every visible interactive element is either its own tab stop, or a
    # member of a composite widget (radio group / tablist) whose ACTIVE
    # member is -- that is what a roving tabindex is for.
    missed = page.evaluate("""(visited) => {
      const seen = new Set(visited);
      const key = n => n.tagName + '#' + (n.id||'') + '.' + ((n.className||'')+'').split(' ')[0];
      const out = [];
      document.querySelectorAll('a[href],button,input,select,textarea').forEach(n => {
        const r = n.getBoundingClientRect();
        if (!(r.width || r.height)) return;
        if (getComputedStyle(n).visibility === 'hidden') return;
        if (n.disabled) return;
        // composite widgets: one stop for the whole group
        if (n.type === 'radio' || n.getAttribute('role') === 'radio') {
          const grp = n.type === 'radio'
            ? document.getElementsByName(n.name)
            : n.closest('[role=radiogroup]').querySelectorAll('[role=radio]');
          if (Array.from(grp).some(m => seen.has(key(m)))) return;
        }
        if (n.getAttribute('role') === 'tab') {
          const strip = n.closest('[role=tablist]').querySelectorAll('[role=tab]');
          if (Array.from(strip).some(m => seen.has(key(m)))) return;
        }
        if (!seen.has(key(n))) out.push(key(n));
      });
      return out;
    }""", seen)
    assert not missed, "visible controls the keyboard never reaches: %s" % missed


def test_skip_link_actually_skips_the_sidebar(page):
    page.evaluate("() => document.body.focus()")
    page.keyboard.press("Tab")
    assert page.evaluate("() => document.activeElement.className") == "skip-link"
    # It has to become VISIBLE when focused, or a sighted keyboard user is
    # activating something they cannot see.
    page.wait_for_timeout(250)
    top = page.evaluate(
        "() => document.querySelector('.skip-link').getBoundingClientRect().top")
    assert top >= 0, "the skip link is still off-screen while focused"
    page.keyboard.press("Enter")
    page.wait_for_timeout(200)
    assert page.evaluate("() => document.activeElement.id") == "main", (
        "activating the skip link did not move focus into <main>")
    page.keyboard.press("Tab")
    landed = page.evaluate("() => document.activeElement")
    assert page.evaluate(
        "() => document.querySelector('.main').contains(document.activeElement)"), (
        "the tab after the skip link went back outside main, so nothing was "
        "skipped")
    assert landed is not None


def test_dialog_takes_focus_traps_tab_and_gives_focus_back(page):
    page.eval_on_selector("#howto-open", "n => n.focus()")
    page.click("#howto-open")
    page.wait_for_timeout(250)
    assert page.evaluate(
        "() => document.getElementById('howto-panel')"
        ".contains(document.activeElement)"), (
        "focus stayed on the button that opened the dialog, so Tab walks the "
        "page underneath it")
    assert page.evaluate(
        "() => document.querySelector('.layout').hasAttribute('inert')"), (
        "the page behind an aria-modal dialog must be inert")
    for _ in range(14):
        page.keyboard.press("Tab")
        assert page.evaluate(
            "() => document.getElementById('howto-panel')"
            ".contains(document.activeElement)"), "Tab escaped the dialog"
    page.keyboard.press("Escape")
    page.wait_for_timeout(200)
    assert page.evaluate("() => document.getElementById('howto-modal').hidden"), (
        "Escape did not close the dialog")
    assert page.evaluate("() => document.activeElement.id") == "howto-open", (
        "focus was not returned to the control that opened the dialog")
    assert not page.evaluate(
        "() => document.querySelector('.layout').hasAttribute('inert')")


def test_tab_strip_follows_the_aria_keyboard_pattern(page):
    page.click("#advanced-toggle")          # reveal the whole strip
    page.wait_for_timeout(150)
    assert page.get_attribute("#advanced-toggle", "aria-expanded") == "true", (
        "a disclosure must say whether it is open")
    page.eval_on_selector("#tab-btn-mockup", "n => n.focus()")
    moves = []
    for key in ("ArrowRight", "ArrowRight", "ArrowLeft", "End", "Home"):
        page.keyboard.press(key)
        page.wait_for_timeout(60)
        moves.append((key,
                      page.evaluate("() => document.activeElement.id"),
                      page.evaluate("() => document.activeElement"
                                    ".getAttribute('aria-selected')")))
    ids = [m[1] for m in moves]
    assert ids[0] != "tab-btn-mockup", "ArrowRight did not move the tab: %s" % moves
    assert ids[1] != ids[0], "ArrowRight did not keep moving: %s" % moves
    assert ids[2] == ids[0], "ArrowLeft did not step back: %s" % moves
    assert ids[3] != ids[2], "End did not jump to the last tab: %s" % moves
    assert ids[4] == "tab-btn-mockup", "Home did not jump to the first tab"
    assert all(m[2] == "true" for m in moves), (
        "the focused tab is not the selected one: %s" % moves)
    assert page.evaluate(
        "() => Array.from(document.querySelectorAll('[role=tab]'))"
        ".filter(t => t.tabIndex === 0).length") == 1, (
        "the strip must hold exactly ONE tab stop")


def test_collapsing_the_advanced_group_never_strands_the_tab_strip(page):
    """If the selected tab is hidden, the strip must keep a tab stop."""
    page.click("#advanced-toggle")
    page.wait_for_timeout(120)
    page.click('[data-tab="extras"]')       # an ADVANCED tab
    page.wait_for_timeout(120)
    page.click("#advanced-toggle")          # ...and hide the group again
    page.wait_for_timeout(150)
    stops = page.evaluate("""() => Array.from(document.querySelectorAll('[role=tab]'))
        .filter(t => t.offsetParent !== null && t.tabIndex === 0).length""")
    assert stops == 1, (
        "%d visible tab stops after collapsing the group: the strip is either "
        "unreachable by keyboard or has grown extra stops" % stops)


def test_every_target_is_at_least_24_css_px(page):
    small = page.evaluate("""() => {
      const out = [];
      document.querySelectorAll(
        'a[href],button,input,select,textarea,[tabindex]:not([tabindex="-1"])'
      ).forEach(n => {
        const r = n.getBoundingClientRect(), cs = getComputedStyle(n);
        if (!(r.width || r.height) || cs.visibility === 'hidden') return;
        if (n.classList.contains('skip-link')) return;   // off-screen until focused
        if (cs.opacity === '0') return;                  // visually-hidden proxy
        // A control wrapped in a label is targeted THROUGH the label.
        const lab = n.closest('label');
        const box = (lab && lab !== n) ? lab.getBoundingClientRect() : r;
        if (box.width < 24 || box.height < 24) {
          out.push({id: n.id || null, cls: ((n.className||'')+'').slice(0,26),
                    w: +box.width.toFixed(1), h: +box.height.toFixed(1)});
        }
      });
      return out;
    }""")
    assert not small, "targets under 24x24 CSS px: %s" % small


def test_the_checkbox_row_is_the_target_a_pointer_actually_hits(page):
    """The box is 18px; the LABEL is what a finger has to hit. Prove the
    whole row activates it rather than trusting the CSS."""
    page.locator("label.batch-check").scroll_into_view_if_needed()
    page.wait_for_timeout(150)
    box = page.evaluate("""() => {
      const r = document.querySelector('label.batch-check').getBoundingClientRect();
      return {x: r.x, y: r.y, w: r.width, h: r.height}; }""")
    assert box["h"] >= 24, "the checkbox row is only %.1fpx tall" % box["h"]
    before = page.is_checked("#batch-render")
    page.mouse.click(box["x"] + box["w"] - 14, box["y"] + box["h"] / 2)
    assert page.is_checked("#batch-render") != before, (
        "clicking the far end of the label did not toggle the checkbox, so "
        "the real target is only the 18px box")


def test_focus_ring_is_drawn_on_every_kind_of_control(page):
    for sel in ("#drop-zone", "#tab-btn-mockup", "#shared-ds-path",
                "#pick-files-link", "#recent-clear", "#batch-input",
                "#advanced-toggle"):
        page.eval_on_selector(sel, "n => n.focus()")
        ring = page.eval_on_selector(sel, """n => { const c = getComputedStyle(n);
          return {w: parseFloat(c.outlineWidth) || 0, style: c.outlineStyle}; }""")
        assert ring["w"] >= 2 and ring["style"] not in ("none", ""), (
            "%s has no visible focus ring: %s" % (sel, ring))


def test_the_guided_tour_is_a_dialog_you_can_leave(page):
    page.eval_on_selector("#howto-open", "n => n.focus()")
    page.evaluate("() => { window.o2sRunTour(); return 1; }")
    page.wait_for_selector(".demo-tooltip", timeout=15000)
    page.wait_for_timeout(300)
    meta = page.evaluate("""() => { const t = document.querySelector('.demo-tooltip');
      return {role: t.getAttribute('role'), modal: t.getAttribute('aria-modal'),
              name: t.getAttribute('aria-label')}; }""")
    assert meta["role"] == "dialog" and meta["modal"] == "true", meta
    assert meta["name"], "the tour step has no accessible name"
    assert page.evaluate(
        "() => document.querySelector('.demo-tooltip')"
        ".contains(document.activeElement)"), (
        "the tour does not take focus, so reaching 'Next' means tabbing "
        "through the whole page behind it")
    for _ in range(6):
        page.keyboard.press("Tab")
        assert page.evaluate(
            "() => document.querySelector('.demo-tooltip')"
            ".contains(document.activeElement)"), "Tab escaped the tour step"
    page.keyboard.press("Escape")
    page.wait_for_timeout(700)
    assert page.locator(".demo-tooltip").count() == 0, (
        "Escape did not end the tour")
    assert page.evaluate("() => document.activeElement.id") == "howto-open", (
        "the tour left focus on a node it deleted")
    assert page.locator(".demo-highlight").count() == 0


def test_a_converted_report_keeps_the_structure_intact(page):
    """Most of the app only EXISTS after a conversion. Convert a sample and
    re-check the structural invariants on the generated panels."""
    if page.locator(".sample-chip").count() == 0:
        pytest.skip("no bundled sample to convert")
    page.click(".sample-chip")
    page.wait_for_function(
        "() => document.querySelectorAll('#mockup-host *').length > 0",
        timeout=60000)
    page.wait_for_timeout(1200)
    page.click("#advanced-toggle")
    page.wait_for_timeout(150)

    for tab in ("mockup", "rdl", "burst", "side", "live", "validate",
                "deploy", "extras"):
        page.click('[data-tab="%s"]' % tab)
        page.wait_for_timeout(400)
        found = page.evaluate("""(name) => {
          const p = document.getElementById('tab-' + name);
          const vis = n => { const r = n.getBoundingClientRect();
                             return r.width || r.height; };
          return {
            role: p.getAttribute('role'),
            fakeButtons: p.querySelectorAll('[role=button]:not(button)').length,
            unnamed: Array.from(p.querySelectorAll(
                'a[href],button,input,select,textarea'))
              .filter(vis)
              .filter(n => !(n.getAttribute('aria-label')
                             || n.getAttribute('aria-labelledby')
                             || (n.textContent || '').trim()
                             || (n.labels && n.labels.length) || n.title))
              .map(n => n.tagName + '.' + ((n.className||'')+'').slice(0,24)),
            badTables: Array.from(p.querySelectorAll('table'))
              .filter(t => t.querySelectorAll('th').length === 0
                        || t.querySelectorAll('th:not([scope])').length
                        || !t.querySelector('caption'))
              .map(t => (t.className || 'table')),
          };
        }""", tab)
        assert found["role"] == "tabpanel", (tab, found)
        assert not found["fakeButtons"], (tab, found)
        assert not found["unnamed"], "%s tab: unnamed controls %s" % (tab, found["unnamed"])
        assert not found["badTables"], (
            "%s tab: tables with no caption or unscoped headers %s"
            % (tab, found["badTables"]))

    order = page.evaluate("""() => {
      const hs = Array.from(document.querySelectorAll('h1,h2,h3,h4,h5,h6'))
                      .map(h => +h.tagName[1]);
      const bad = []; let prev = hs[0];
      hs.slice(1).forEach(l => { if (l > prev + 1) bad.push([prev, l]); prev = l; });
      return {h1: document.querySelectorAll('h1').length, first: hs[0], skips: bad};
    }""")
    assert order["h1"] == 1 and order["first"] == 1, order
    assert not order["skips"], "heading level skips after conversion: %s" % order["skips"]


# =================================================== layer 3b: every STATE
# The first pass at target size measured the page as it loads. Eight of the
# nine tab panels are `hidden` then, so every control inside them was
# invisible to the measurement -- which is exactly how a 79.8x19 "Sync
# scroll" switch survived it. These fixtures open each panel and measure
# what is actually on screen.

TABS = ("mockup", "rdl", "burst", "subreports", "side", "live", "validate",
        "deploy", "extras")


@pytest.fixture(scope="module")
def converted(browser, live_url):
    """One conversion of a bundled sample, reused by the state tests.

    Module-scoped on purpose: converting per test triples the runtime for
    no extra coverage. Each test re-opens the tab it cares about, so the
    order they run in does not matter.
    """
    pg = browser.new_page(viewport={"width": 1280, "height": 900})
    pg.route("**/*", lambda route: route.continue_()
             if "127.0.0.1" in route.request.url else route.abort())
    pg.goto(live_url, wait_until="domcontentloaded")
    pg.wait_for_function("() => !!document.querySelector('.tab')")
    if pg.locator(".sample-chip").count() == 0:
        pg.close()
        pytest.skip("no bundled sample to convert")
    pg.click(".sample-chip")
    pg.wait_for_function(
        "() => document.querySelectorAll('#mockup-host *').length > 0",
        timeout=90000)
    pg.wait_for_timeout(1200)
    if pg.get_attribute("#advanced-toggle", "aria-expanded") != "true":
        pg.click("#advanced-toggle")
        pg.wait_for_timeout(200)
    yield pg
    pg.close()


def _open_every_tab(pg):
    """Yield (name, panel-is-open) for every tab the report actually shows."""
    for tab in TABS:
        loc = pg.locator('[data-tab="%s"]' % tab)
        if not loc.count() or not loc.first.is_visible():
            continue
        loc.first.click()
        pg.wait_for_timeout(350)
        yield tab


# Everything a pointer or a finger has to hit, measured through the <label>
# when a label wraps it -- that is the real target. aria-hidden proxies are
# excluded: they are not controls, they are plumbing behind a real button.
_MEASURE_TARGETS = r"""() => {
  const key = n => n.tagName + '#' + (n.id||'') + '.' + ((n.className||'')+'').split(' ')[0];
  const out = [];
  document.querySelectorAll(
    'a[href],button,input,select,textarea,[tabindex]:not([tabindex="-1"])'
  ).forEach(n => {
    const cs = getComputedStyle(n), r = n.getBoundingClientRect();
    if (!(r.width || r.height) || cs.visibility === 'hidden') return;
    if (cs.opacity === '0') return;
    if (n.classList.contains('skip-link')) return;     // off-screen until focused
    if (n.closest('[aria-hidden="true"]') || n.closest('[inert]')) return;
    const lab = n.closest('label');
    const box = (lab && lab !== n) ? lab.getBoundingClientRect() : r;
    if (box.width < 24 || box.height < 24)
      out.push({k: key(n), w: +box.width.toFixed(1), h: +box.height.toFixed(1)});
  });
  return out;
}"""


def test_every_target_clears_24px_in_every_tab_panel(converted):
    """WCAG 2.2 SC 2.5.8, measured with each panel actually OPEN."""
    small = {}
    for tab in _open_every_tab(converted):
        found = converted.evaluate(_MEASURE_TARGETS)
        if found:
            small[tab] = found
    assert not small, (
        "targets under 24x24 CSS px once the panel is open: %s" % small)


def test_no_control_hides_what_it_does_in_a_hover_tooltip(converted):
    """A title= tooltip needs a mouse hovering over it. It never opens for a
    keyboard user, never appears on touch, is not dismissible, and is
    announced inconsistently. Anything worth saying goes on screen."""
    offenders = {}
    for tab in _open_every_tab(converted):
        found = converted.evaluate("""() => Array.from(
            document.querySelectorAll('[title]'))
          .filter(n => { const r = n.getBoundingClientRect();
                         return r.width || r.height; })
          .map(n => n.tagName + '#' + (n.id||'') + ' :: '
                    + n.getAttribute('title').slice(0, 60))""")
        if found:
            offenders[tab] = found
    assert not offenders, (
        "information parked in a hover-only title= tooltip: %s" % offenders)


def test_image_slot_rows_are_full_size_targets(converted):
    """Seal/logo upload rows only exist for reports that declare image
    placeholders, so no bundled sample renders them. Drive the real
    renderer with a synthetic slot list and measure what it builds."""
    built = converted.evaluate("""() => {
      if (typeof renderImageSlots !== 'function') return null;
      renderImageSlots({image_slots: [{name: 'AGENCY_SEAL', has_data: false},
                                      {name: 'LOGO', has_data: true}]});
      const rows = Array.from(document.querySelectorAll('#image-slot-list input'));
      return rows.map(n => { const r = n.getBoundingClientRect();
        return {name: n.getAttribute('aria-label'),
                w: +r.width.toFixed(1), h: +r.height.toFixed(1)}; });
    }""")
    assert built, "renderImageSlots is not reachable, or built no rows"
    for row in built:
        assert row["name"], "an image slot input with no accessible name: %s" % row
        assert row["h"] >= 24 and row["w"] >= 24, (
            "image slot target is %sx%s, under the 24px minimum: %s"
            % (row["w"], row["h"], row["name"]))
    # Leave the sidebar as the other state tests expect to find it.
    converted.evaluate("() => renderImageSlots({image_slots: []})")


def test_a_closed_disclosure_keeps_its_fields_off_the_keyboard(converted):
    """The Extras tab hangs long editable prompts off <details>. Closed,
    their textareas must be out of the tab order (or Tab crawls through
    seventeen invisible text boxes); opening the summary must put them
    back. This is the contract that lets the focus walk finish at all."""
    converted.click('[data-tab="extras"]')
    converted.wait_for_timeout(400)
    state = converted.evaluate("""() => {
      const d = document.querySelector('.extras-prompt');
      if (!d) return null;
      const ta = d.querySelector('textarea');
      if (!ta) return null;
      const before = (() => { ta.focus(); return document.activeElement === ta; })();
      d.open = true;
      const after = (() => { ta.focus(); return document.activeElement === ta; })();
      d.open = false;
      return {closedFocusable: before, openFocusable: after};
    }""")
    if state is None:
        pytest.skip("this report produced no <details> prompt blocks")
    assert not state["closedFocusable"], (
        "a field inside a CLOSED disclosure took focus: the keyboard has to "
        "walk through content nobody can see")
    assert state["openFocusable"], (
        "opening the disclosure did not make its field focusable, so its "
        "content is unreachable by keyboard")


# ---------------------------------------------------------------- contrast
def _srgb(component):
    c = component / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _luminance(rgb):
    r, g, b = rgb
    return 0.2126 * _srgb(r) + 0.7152 * _srgb(g) + 0.0722 * _srgb(b)


def _contrast(a, b):
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _rgb(value):
    nums = re.findall(r"[\d.]+", value or "")
    if len(nums) < 3:
        return None
    return tuple(float(n) for n in nums[:3])


# The ring is drawn on whatever is BEHIND it: outside the control when the
# offset is positive, over the control's own fill when it is negative.
_RING_AND_BACKDROP = r"""(sel) => {
  const n = document.querySelector(sel);
  if (!n) return null;
  const cs = getComputedStyle(n);
  const opaque = (el) => {
    while (el) {
      const c = getComputedStyle(el).backgroundColor;
      const m = (c || '').match(/[\d.]+/g);
      if (m && (m.length < 4 || parseFloat(m[3]) > 0.9)) return c;
      el = el.parentElement;
    }
    return getComputedStyle(document.body).backgroundColor;
  };
  const inset = parseFloat(cs.outlineOffset) < 0;
  return {width: parseFloat(cs.outlineWidth) || 0, style: cs.outlineStyle,
          ring: cs.outlineColor, behind: opaque(inset ? n : n.parentElement)};
}"""

_RING_ON = ("#drop-zone", "#tab-btn-mockup", "#shared-ds-path",
            "#pick-files-link", "#recent-clear", "#advanced-toggle",
            "#howto-open", "#batch-run")


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_focus_ring_clears_3_to_1_against_what_is_behind_it(page, theme):
    """SC 1.4.11: a focus indicator you cannot see is not an indicator. The
    ratio is COMPUTED from the pixels the browser reports, in both themes --
    never eyeballed, and never assumed from the token name."""
    page.evaluate("(t) => document.documentElement.setAttribute('data-theme', t)",
                  theme)
    page.wait_for_timeout(150)
    weak = []
    for sel in _RING_ON:
        page.eval_on_selector(sel, "n => n.focus()")
        info = page.evaluate(_RING_AND_BACKDROP, sel)
        assert info, "%s is not on the page" % sel
        assert info["width"] >= 2 and info["style"] not in ("none", ""), (
            "%s (%s theme) draws no focus ring: %s" % (sel, theme, info))
        ring, behind = _rgb(info["ring"]), _rgb(info["behind"])
        assert ring and behind, (sel, info)
        ratio = _contrast(ring, behind)
        if ratio < 3.0:
            weak.append((sel, round(ratio, 2), info["ring"], info["behind"]))
    assert not weak, (
        "%s theme: focus rings under 3:1 against their own backdrop "
        "(selector, ratio, ring, behind): %s" % (theme, weak))


def test_reverse_tab_order_never_traps(converted):
    """Shift+Tab is half of keyboard navigation and it has its own bugs: a
    trap that only bites going backwards is still a trap."""
    converted.click('[data-tab="rdl"]')
    converted.wait_for_timeout(250)
    converted.eval_on_selector("#advanced-toggle", "n => n.focus()")
    seen = []
    for _ in range(120):
        converted.keyboard.press("Shift+Tab")
        seen.append(converted.evaluate(_DESCRIBE))
        if seen[-1].startswith("BODY"):
            break
    assert find_trap(seen) is None, (
        "keyboard trap walking BACKWARDS at %r" % find_trap(seen))
    assert any(s.startswith("BODY") for s in seen), (
        "Shift+Tab never left the document in 120 presses: %s" % seen[:25])


# A div with a click handler is a control the keyboard cannot press and a
# screen reader announces as nothing. Recording every addEventListener call
# BEFORE the app boots is the only way to see them all: they are invisible
# to the DOM and to any amount of reading.
_RECORD_CLICKS = """
window.__clickTargets = [];
const orig = EventTarget.prototype.addEventListener;
EventTarget.prototype.addEventListener = function (type, fn, opts) {
  if (type === 'click' && this instanceof Element) window.__clickTargets.push(this);
  return orig.call(this, type, fn, opts);
};
"""

_CLICKABLE_REPORT = """() => {
  const key = n => n.tagName + '#' + (n.id||'') + '.' + ((n.className||'')+'').split(' ')[0];
  const fine = 'a[href],button,input,select,textarea,summary,label,'
             + '[role=button],[role=tab],[role=radio],[role=link]';
  const handlers = [];
  (window.__clickTargets || []).forEach(n => {
    if (!n.isConnected || n === document.body || n === document.documentElement) return;
    if (n.matches(fine)) return;
    if (n.classList.contains('howto-backdrop')) return;  // see the test
    handlers.push(key(n));
  });
  const pointer = [];
  document.querySelectorAll('*').forEach(n => {
    if (getComputedStyle(n).cursor !== 'pointer') return;
    if (n.matches(fine) || n.matches('[tabindex]')) return;
    if (n.closest(fine) || n.closest('label')) return;
    const r = n.getBoundingClientRect();
    if (r.width || r.height) pointer.push(key(n));
  });
  return {handlers: Array.from(new Set(handlers)),
          pointerOnly: Array.from(new Set(pointer))};
}"""


def test_only_real_controls_carry_click_handlers(browser, live_url):
    """The modal BACKDROP is the one allowed exception: a click-outside
    target must not be a button (it would land in the tab order and be
    announced), and Escape plus a real close button already cover it."""
    pg = browser.new_page(viewport={"width": 1280, "height": 900})
    pg.route("**/*", lambda route: route.continue_()
             if "127.0.0.1" in route.request.url else route.abort())
    pg.add_init_script(_RECORD_CLICKS)
    try:
        pg.goto(live_url, wait_until="domcontentloaded")
        pg.wait_for_function("() => !!document.querySelector('.tab')")
        if pg.locator(".sample-chip").count():
            pg.click(".sample-chip")
            pg.wait_for_function(
                "() => document.querySelectorAll('#mockup-host *').length > 0",
                timeout=90000)
            pg.wait_for_timeout(1000)
            pg.click("#advanced-toggle")
            pg.wait_for_timeout(200)
            for tab in TABS:
                loc = pg.locator('[data-tab="%s"]' % tab)
                if loc.count() and loc.first.is_visible():
                    loc.first.click()
                    pg.wait_for_timeout(300)
        found = pg.evaluate(_CLICKABLE_REPORT)
    finally:
        pg.close()
    assert not found["handlers"], (
        "click handlers on elements that are not controls -- Enter and Space "
        "do nothing there and nothing announces them: %s" % found["handlers"])
    assert not found["pointerOnly"], (
        "these are PAINTED as clickable (cursor:pointer) but are not "
        "operable controls: %s" % found["pointerOnly"])


def test_focus_walker_detects_a_trap_going_backwards(browser):
    """Mutation proof for the reverse walk, same bargain as the forward one."""
    pg = browser.new_page()
    try:
        pg.set_content("""
        <!doctype html><title>trap</title>
        <button id="a">one</button><button id="b">two</button>
        <script>
          document.getElementById('a').addEventListener('keydown', e => {
            if (e.key === 'Tab' && e.shiftKey) {
              e.preventDefault(); document.getElementById('a').focus(); }
          });
        </script>""")
        pg.eval_on_selector("#b", "n => n.focus()")
        seen = []
        for _ in range(20):
            pg.keyboard.press("Shift+Tab")
            seen.append(pg.evaluate(_DESCRIBE))
        assert find_trap(seen) is not None, (
            "the reverse walker walked past a deliberate backwards trap: %s"
            % seen[:12])
    finally:
        pg.close()


# ================================================ the rendered page images
# WHY THIS SECTION EXISTS, measured on the real page: every page of the
# engine render was <img alt="Rendered page 3">. Those images are not
# decoration -- they are the deliverable, the only place the operator sees
# what SSRS will print -- and an alt that carries none of the image's
# information fails SC 1.1.1 as surely as no alt at all. It was also the one
# view with no text equivalent, so a reader who could not use the picture
# had nowhere else to go.
#
# The contract lives in one function, used by the live checks AND by the
# mutation proof below, because a checker that cannot fail proves nothing:
#
#   * the alt is not empty, and is not the old "Rendered page N";
#   * it says WHICH report and WHICH page of how many;
#   * when the server could read the page's own opening line, the alt
#     carries it -- that is the description, not a label;
#   * it names the text route, so the picture is never a dead end.
_ALT_GENERIC = re.compile(r"^\s*(rendered\s+)?page\s*\d+\s*$", re.I)


def _alt_defects(pages):
    """Everything wrong with these rendered-page alts. Empty list = fine."""
    bad = []
    for p in pages:
        alt = (p.get("alt") or "").strip()
        where = "page %s" % p.get("n")
        if not alt:
            bad.append("%s: no alt at all" % where)
            continue
        if _ALT_GENERIC.match(alt):
            bad.append("%s: alt %r identifies the image and describes "
                       "nothing in it" % (where, alt))
        if p.get("report") and p["report"] not in alt:
            bad.append("%s: alt does not say which report this is a page of "
                       "(%r)" % (where, alt))
        if ("of %s" % p.get("total")) not in alt:
            bad.append("%s: alt does not place the page in the report (%r)"
                       % (where, alt))
        if p.get("heading") and p["heading"] not in alt:
            bad.append("%s: the page opens %r and the alt does not say so"
                       % (where, p["heading"]))
        if "RDL" not in alt:
            bad.append("%s: alt does not point at the text of the report, so "
                       "the image is a dead end (%r)" % (where, alt))
    return bad


def test_the_alt_text_checker_fails_the_alt_this_section_replaced():
    """Mutation proof, first: the contract has to reject exactly what
    shipped -- alt="Rendered page 1" -- and accept a real description."""
    old = [{"n": 1, "total": 4, "report": "ACME_FEE_NOTICE",
            "heading": "NOTICE OF ANNUAL FEE", "alt": "Rendered page 1"}]
    defects = _alt_defects(old)
    assert defects, "the alt-text contract passes the defect it exists for"
    assert any("describes nothing" in d for d in defects), defects
    good = [{"n": 1, "total": 4, "report": "ACME_FEE_NOTICE",
             "heading": "NOTICE OF ANNUAL FEE",
             "alt": "Page 1 of 4 of the report ACME_FEE_NOTICE. Begins "
                    "“NOTICE OF ANNUAL FEE”. 38 lines of text. The "
                    "same report as text is in the RDL XML view."}]
    assert not _alt_defects(good), _alt_defects(good)


# A 1x1 PNG: this is a test about the alt text, not about the pixels.
_PIXEL = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfF"
          "cSJAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=")

# Drive the REAL production path (_showRenderedPages) with a known render
# state, so the contract is measured on the DOM the app builds -- and
# without requiring Microsoft's engine on the machine running the tests.
_DRIVE_RENDER = """(png) => {
  state.data = {report: {name: 'ACME_FEE_NOTICE'}};
  state.renderedPdf = null;
  state.renderedPages = [png, png];
  state.renderedNotes = [{heading: 'NOTICE OF ANNUAL FEE', lines: 38,
                          words: 312},
                         {heading: '', lines: 12, words: 61}];
  state.renderedRows = 3;
  state.mockupMode = 'frontend';
  activateTab('mockup');
  _showRenderedPages();
  const imgs = Array.from(document.querySelectorAll('#render-host img'));
  const labels = Array.from(
    document.querySelectorAll('#render-host .render-page-label'));
  return {
    pages: imgs.map((n, i) => ({n: i + 1, total: imgs.length, alt: n.alt,
                                report: 'ACME_FEE_NOTICE',
                                heading: state.renderedNotes[i].heading,
                                label: (labels[i] || {}).textContent || ''})),
    routes: Array.from(document.querySelectorAll('#render-host button'))
      .map(b => ({id: b.id, text: (b.textContent || '').trim(),
                  w: b.getBoundingClientRect().width,
                  h: b.getBoundingClientRect().height})),
  };
}"""


def test_every_rendered_page_describes_itself(page):
    built = page.evaluate(_DRIVE_RENDER, _PIXEL)
    assert len(built["pages"]) == 2, built
    assert not _alt_defects(built["pages"]), _alt_defects(built["pages"])
    # The page that HAS an opening line puts it on screen as well: a stack
    # of grey thumbnails is navigated by reading, not by squinting.
    assert "NOTICE OF ANNUAL FEE" in built["pages"][0]["label"], built["pages"][0]
    assert built["pages"][1]["label"].strip() == "Page 2 of 2", (
        "a page whose text could not be read must not invent a caption: %r"
        % built["pages"][1]["label"])


def test_the_render_view_offers_the_report_as_text(page):
    """The other half of SC 1.1.1 here: pictures of pages need a route to
    the same content in words. Both routes already existed -- the Backend
    preview (every label and field) and the RDL XML view -- so the render
    view links them instead of leaving the operator to know they are there."""
    built = page.evaluate(_DRIVE_RENDER, _PIXEL)
    ids = {b["id"] for b in built["routes"]}
    assert {"render-as-labels", "render-as-rdl"} <= ids, (
        "the render view offers no way to read the report as text: %s"
        % built["routes"])
    for b in built["routes"]:
        assert b["text"], "a text-route button with no accessible name: %s" % b
        assert b["h"] >= 24 and b["w"] >= 24, (
            "%s is %sx%s, under the 24px target minimum"
            % (b["id"], b["w"], b["h"]))
    # ...and they actually go there.
    page.click("#render-as-rdl")
    page.wait_for_timeout(300)
    assert page.evaluate("() => !document.getElementById('tab-rdl').hidden"), (
        "the RDL XML button did not open the RDL view")
    assert page.evaluate("() => document.activeElement.id") == "tab-btn-rdl", (
        "focus stayed in the view the operator just left")
    page.evaluate("() => activateTab('mockup')")
    page.evaluate(_DRIVE_RENDER, _PIXEL)
    page.click("#render-as-labels")
    page.wait_for_timeout(300)
    assert page.evaluate(
        "() => document.getElementById('mockup-mode-backend')"
        ".getAttribute('aria-checked')") == "true", (
        "the Backend-view button did not switch the preview to labels")
    # Measured before this line existed: the switch hides the host this
    # button lives in, and focus fell to <body> -- the keyboard user was
    # dropped at the top of the document, in a view they had just left.
    assert page.evaluate(
        "() => document.activeElement.id") == "mockup-mode-backend", (
        "switching to the Backend view threw focus away: it landed on %r"
        % page.evaluate("() => document.activeElement.id"
                        " || document.activeElement.tagName"))


def test_the_engine_render_reaches_the_alt_text_end_to_end(page):
    """The drive above proves the contract; this proves the WIRE: a real
    conversion, Microsoft's real engine, the server's own reading of the PDF
    it produced, arriving in the alt attribute of the real page. Skipped --
    never faked -- on a machine where the engine cannot run."""
    if page.locator(".sample-chip").count() == 0:
        pytest.skip("no bundled sample to convert")
    page.click(".sample-chip")
    page.wait_for_function(
        "() => document.querySelectorAll('#mockup-host *').length > 0",
        timeout=90000)
    try:
        page.wait_for_function(
            "() => document.querySelectorAll('#render-host img').length > 0",
            timeout=120000)
    except Exception:
        pytest.skip("the report engine did not render pages on this machine")
    got = page.evaluate("""() => {
      const imgs = Array.from(document.querySelectorAll('#render-host img'));
      return {name: (state.data.report || {}).name,
              alts: imgs.map(n => n.alt),
              notes: state.renderedNotes || []};
    }""")
    notes = got["notes"] or []
    pages = [{"n": i + 1, "total": len(got["alts"]), "alt": a,
              "report": got["name"],
              "heading": (notes[i] or {}).get("heading") if i < len(notes) else ""}
             for i, a in enumerate(got["alts"])]
    assert pages, "the engine rendered no pages"
    assert not _alt_defects(pages), _alt_defects(pages)
    # The server read the pages it rendered: the description in the alt came
    # off the PDF, not out of the client's own state.
    assert any((n or {}).get("lines") for n in notes), (
        "no page description came back from the render at all: %s" % notes)


# ---- the server's reading of the rendered PDF ---------------------------
def test_the_page_reader_describes_a_page_and_refuses_garbled_text(tmp_path):
    """_rendered_page_notes reads the engine's own PDF. Two things it must
    get right, both measured here: a page it CAN read is described by its
    own opening line, and text it cannot trust is not described at all --
    ReportViewer embeds non-Latin font subsets with no usable ToUnicode, so
    extraction hands back glyph ids that have a length and print as tofu. A
    wrong description is worse than none."""
    fitz = pytest.importorskip("fitz")
    import app as backend_app
    doc = fitz.open()
    first = doc.new_page()
    first.insert_text((72, 96), "NOTICE OF ANNUAL FEE")
    first.insert_text((72, 120), "Account 4471   Amount due 250.00")
    second = doc.new_page()
    second.insert_text((72, 96), "Page two of the notice")
    out = tmp_path / "probe.pdf"
    doc.save(str(out))
    doc.close()
    notes = backend_app._rendered_page_notes(out, 2)
    assert len(notes) == 2, notes
    assert notes[0]["heading"] == "NOTICE OF ANNUAL FEE", notes[0]
    assert notes[0]["lines"] == 2 and notes[0]["words"] >= 6, notes[0]
    assert notes[1]["heading"] == "Page two of the notice", notes[1]
    # the guard itself, both ways
    assert backend_app._line_is_readable("NOTICE OF ANNUAL FEE")
    assert backend_app._line_is_readable("Fee due: 250.00 EUR")
    assert not backend_app._line_is_readable("\x01\x02\x03\x04\x05"), (
        "control characters are what a subset with no ToUnicode extracts as")
    pua = "".join(chr(c) for c in range(0xE000, 0xE004))
    assert not backend_app._line_is_readable(pua), (
        "private-use code points are glyph ids, not words")
    assert not backend_app._line_is_readable("---"), "a rule is not a title"
    assert not backend_app._line_is_readable(
        "Ειδοποίηση "
        "τέλους"), (
        "a whole non-Latin run cannot be trusted to say what it appears to "
        "say, so the page is described structurally instead")


def test_no_rendered_page_is_left_without_a_description_by_the_wiring(page):
    """The failure mode that would sneak past everything above: the server
    sends no notes at all (an old engine, no PyMuPDF, a locked PDF). The
    alt must still identify the report and the page and point at the text."""
    built = page.evaluate("""(png) => {
      state.data = {report: {name: 'ACME_FEE_NOTICE'}};
      state.renderedPdf = null;
      state.renderedPages = [png];
      state.renderedNotes = null;
      state.mockupMode = 'frontend';
      activateTab('mockup');
      _showRenderedPages();
      const n = document.querySelector('#render-host img');
      return [{n: 1, total: 1, alt: n.alt, report: 'ACME_FEE_NOTICE',
               heading: ''}];
    }""", _PIXEL)
    assert not _alt_defects(built), _alt_defects(built)


# =========================================== every view names itself
def test_every_tab_panel_carries_a_heading_of_its_own(dom):
    """Heading navigation is how a screen-reader user skims a page. The
    Preview panel -- the view the operator spends the day in -- was the one
    panel with no heading, so that skim jumped straight over it."""
    panels = dom.xpath("//*[@role='tabpanel']")
    assert len(panels) >= 8, (
        "expected the full set of views, found %d" % len(panels))
    missing, wrong = [], []
    for p in panels:
        heads = [h for h in p.xpath(".//h1|.//h2|.//h3|.//h4|.//h5|.//h6")
                 if _text(h)]
        if not heads:
            missing.append(p.get("id"))
            continue
        if heads[0].tag != "h2":
            wrong.append((p.get("id"), heads[0].tag, _text(heads[0])))
    assert not missing, (
        "these views have no heading of their own, so heading navigation "
        "skips them: %s" % missing)
    assert not wrong, (
        "a view's own heading is an h2, one rung under the page title; these "
        "open at another level: %s" % wrong)


def test_the_preview_panel_heading_is_really_on_screen(page):
    """A heading can exist in the markup and still be hidden by a
    stylesheet. This one is meant to be read, not merely announced."""
    page.evaluate("() => { state.data = {report: {name: 'X'}};"
                  " activateTab('mockup'); }")
    page.wait_for_timeout(200)
    box = page.evaluate("""() => {
      const h = document.querySelector('#tab-mockup h2');
      if (!h) return null;
      const r = h.getBoundingClientRect();
      const cs = getComputedStyle(h);
      return {text: (h.textContent || '').trim(), w: r.width, h: r.height,
              display: cs.display, visibility: cs.visibility};
    }""")
    assert box, "the Preview panel has no heading in the live DOM"
    assert box["text"], "the Preview heading is empty"
    assert box["display"] != "none" and box["visibility"] != "hidden", box
    assert box["w"] > 40 and box["h"] > 8, (
        "the Preview heading occupies no space on screen: %s" % box)
