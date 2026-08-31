"""The contrast rail from the other end: computed styles on the SERVED page.

tests/test_theme_contrast.py reasons about tokens. It is exact about what
the source paints, and blind to anything only the browser knows -- which
element ends up inside which card, what a translucent layer composites to,
whether a focus ring is drawn at all. That blindness is not theoretical: the
two defects this pair of rails was rebuilt for (a 2.76:1 focus ring inside
the guided-tour card, 2.58:1 arrow glyphs in the How-it-works dialog) were
both found by measuring the rendered page, not by reading the stylesheet.

So this module does what the audit did:

  * serve the real app,
  * open it in Chromium in the LIGHT theme and again in the DARK theme,
  * walk every visible element, take its computed colour and the effective
    background behind it (compositing translucent layers), and measure,
  * Tab through every focusable control and measure the ring the browser
    actually draws, against the surface it is actually drawn on,
  * do the same inside the How-it-works dialog and the guided tour, which
    only exist after JavaScript has built them.

It skips -- loudly, with the reason -- when Chromium is not available, so a
machine without the browser still runs the rest of the suite. It is a rail,
not a substitute: the token gate runs everywhere and runs in half a second.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

TEXT, LARGE, UI = 4.5, 3.0, 3.0

# Everything a measurement needs, in the page. Kept in one string so the
# element walk, the focus walk and the dialog walk all measure identically.
HELPERS = r"""
  const px = (v) => parseFloat(v) || 0;
  const parse = (c) => {
    const m = /rgba?\(([^)]+)\)/.exec(c || "");
    if (!m) return null;
    const p = m[1].split(/[\s,\/]+/).filter(Boolean).map(Number);
    return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 };
  };
  const over = (fg, bg) => ({
    r: fg.r * fg.a + bg.r * (1 - fg.a),
    g: fg.g * fg.a + bg.g * (1 - fg.a),
    b: fg.b * fg.a + bg.b * (1 - fg.a), a: 1 });
  const lum = (c) => {
    const f = (v) => { v /= 255; return v <= 0.04045 ? v / 12.92
                                    : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b);
  };
  const ratio = (a, b) => {
    const la = lum(a), lb = lum(b);
    return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
  };
  const hex = (c) => "#" + [c.r, c.g, c.b].map(
      v => Math.round(v).toString(16).padStart(2, "0")).join("");

  /* The surface a colour is really painted on: the first ancestor with an
     opaque background, with every translucent layer above it composited in
     order. `skipSelf` is for outlines, which are drawn OUTSIDE the border
     box and so land on the parent, not on the element's own fill. */
  const bgOf = (el, skipSelf) => {
    const stack = [];
    let n = skipSelf ? el.parentElement : el;
    while (n) {
      const c = parse(getComputedStyle(n).backgroundColor);
      if (c && c.a > 0) { stack.push(c); if (c.a >= 1) break; }
      n = n.parentElement;
    }
    let out = { r: 255, g: 255, b: 255, a: 1 };
    const html = parse(getComputedStyle(document.documentElement).backgroundColor);
    if (html && html.a >= 1) out = html;
    for (let i = stack.length - 1; i >= 0; i--) out = over(stack[i], out);
    return out;
  };
  const visible = (el) => {
    const s = getComputedStyle(el);
    if (s.display === "none" || s.visibility !== "visible") return false;
    if (px(s.opacity) < 0.05) return false;
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return false;
    /* inert / aria-hidden / hidden subtrees are presented to nobody */
    if (el.closest("[inert]") || el.closest('[aria-hidden="true"]')) return false;
    if (el.closest("[hidden]")) return false;
    return true;
  };
  const path = (el) => {
    const bits = []; let n = el;
    for (let i = 0; n && i < 4; i++, n = n.parentElement) {
      let s = n.tagName.toLowerCase();
      if (n.id) { s += "#" + n.id; bits.unshift(s); break; }
      if (n.className && typeof n.className === "string") {
        s += "." + n.className.trim().split(/\s+/).slice(0, 3).join(".");
      }
      bits.unshift(s);
    }
    return bits.join(" > ");
  };
  /* only the text this element writes itself -- a wrapper does not paint
     the words its children paint */
  const ownText = (el) => {
    let t = "";
    for (const n of el.childNodes) if (n.nodeType === 3) t += n.nodeValue;
    return t.replace(/\s+/g, " ").trim();
  };
  const measure = (el, out) => {
    if (!visible(el)) return;
    const s = getComputedStyle(el);
    const text = ownText(el);
    if (text) {
      const raw = parse(s.color);
      if (raw) {
        const bg = bgOf(el, false);
        const fg = raw.a < 1 ? over(raw, bg) : raw;
        const size = px(s.fontSize);
        const weight = parseInt(s.fontWeight, 10) || 400;
        const large = size >= 24 || (size >= 18.66 && weight >= 700);
        out.push({ kind: "text", sel: path(el), text: text.slice(0, 40),
                   fg: hex(fg), bg: hex(bg), size: size,
                   need: large ? 3.0 : 4.5, got: ratio(fg, bg) });
      }
    }
    if (s.outlineStyle !== "none" && px(s.outlineWidth) > 0) {
      const raw = parse(s.outlineColor);
      if (raw && raw.a > 0) {
        const bg = bgOf(el, true);
        const fg = raw.a < 1 ? over(raw, bg) : raw;
        out.push({ kind: "ring", sel: path(el),
                   text: (el.textContent || "").trim().slice(0, 40),
                   fg: hex(fg), bg: hex(bg), size: px(s.outlineWidth),
                   need: 3.0, got: ratio(fg, bg) });
      }
    }
  };
"""

SWEEP_ALL = "() => {" + HELPERS + """
  const out = [];
  for (const el of document.querySelectorAll("*")) measure(el, out);
  return out;
}"""

SWEEP_FOCUSED = "() => {" + HELPERS + """
  const out = [];
  const el = document.activeElement;
  if (el && el !== document.body) measure(el, out);
  return out;
}"""

FOCUSABLE = ("a[href],button,input,select,textarea,summary,"
             "[tabindex]:not([tabindex='-1'])")


def _serve():
    from app import app
    from werkzeug.serving import make_server
    app.config["TESTING"] = True
    srv = make_server("127.0.0.1", 0, app, threaded=True)   # 0 = a free port
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d/" % srv.server_port


def _sweep_one_theme(browser, url, theme):
    from playwright.sync_api import TimeoutError as PWTimeout
    rows = []
    ctx = browser.new_context(viewport={"width": 1440, "height": 950},
                              color_scheme=theme)
    page = ctx.new_page()
    try:
        page.goto(url, wait_until="domcontentloaded")
        page.evaluate("t => localStorage.setItem('o2s-theme', t)", theme)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(600)

        def take(state, js=SWEEP_ALL):
            for r in page.evaluate(js):
                r["state"] = state
                rows.append(r)

        take("page")

        # Every control, reached the way a keyboard user reaches it, so the
        # browser really is in :focus-visible mode.
        n = page.evaluate("() => document.querySelectorAll(%r).length"
                          % FOCUSABLE)
        for _ in range(min(n + 4, 160)):
            page.keyboard.press("Tab")
            take("focus", SWEEP_FOCUSED)

        # The How-it-works dialog: built by app.js, so it exists only here.
        page.evaluate("() => { document.getElementById('howto-open').click();"
                      " return 1; }")
        page.wait_for_selector(".howto-panel", timeout=5000)
        page.wait_for_timeout(300)
        take("howto")
        for _ in range(10):
            page.keyboard.press("Tab")
            take("howto-focus", SWEEP_FOCUSED)
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)

        # A real conversion, so the surfaces that only exist with output on
        # them get measured too: the simulated report sheet (pinned white in
        # both themes), the code wells, and the rings of the listings that
        # scroll sideways -- those are drawn INSIDE the well.
        if page.evaluate("() => !!document.querySelector('#samples-list "
                         ".sample-chip')"):
            page.evaluate("() => { document.querySelector('#samples-list "
                          ".sample-chip').click(); return 1; }")
            try:
                page.wait_for_function(
                    "() => { const h = document.getElementById('mockup-host');"
                    " return h && h.children.length > 0; }", timeout=30000)
                page.wait_for_timeout(600)
                take("converted")
                for tab in ("mockup", "rdl", "side", "validate", "extras"):
                    if page.evaluate(
                            "t => { const el = document.querySelector("
                            "'.tab[data-tab=\"' + t + '\"]');"
                            " if (!el) return 0; el.click(); return 1; }", tab):
                        page.wait_for_timeout(350)
                        take("converted:" + tab)
                        for _ in range(8):
                            page.keyboard.press("Tab")
                            take("converted-focus:" + tab, SWEEP_FOCUSED)
            except PWTimeout:
                pass          # no conversion -> the skip below says so

        # The guided tour: a card pinned dark in both themes, with the one
        # button the tour asks the operator to press.
        page.evaluate("() => { window.o2sRunTour(); return 1; }")
        page.wait_for_selector(".demo-tooltip", timeout=10000)
        page.wait_for_timeout(400)
        take("tour")
        for _ in range(4):
            page.keyboard.press("Tab")
            page.wait_for_timeout(100)
            take("tour-focus", SWEEP_FOCUSED)
            take("tour", SWEEP_ALL)
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
    finally:
        ctx.close()
    return rows


@pytest.fixture(scope="module")
def measured():
    """{theme: [measurement, ...]} for the served page, or a skip."""
    pytest.importorskip("playwright.sync_api",
                        reason="playwright is not installed; the token gate "
                               "in test_theme_contrast.py still runs")
    from playwright.sync_api import Error as PWError
    from playwright.sync_api import sync_playwright

    srv, url = _serve()
    out = {}
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except PWError as exc:                     # no browser binary
                pytest.skip("Chromium is not available to playwright (%s). "
                            "Run `python -m playwright install chromium` to "
                            "turn this rail on." % str(exc).splitlines()[0])
            try:
                for theme in ("light", "dark"):
                    out[theme] = _sweep_one_theme(browser, url, theme)
            finally:
                browser.close()
    finally:
        srv.shutdown()
    return out


def _fmt(rows):
    seen = {}
    for r in rows:
        seen.setdefault((r["kind"], r["sel"], r["fg"], r["bg"]), r)
    return "\n".join(
        "  %-4s %5.2f:1 (needs %.1f)  %s on %s  %s  %r  [%s]"
        % (r["kind"], r["got"], r["need"], r["fg"], r["bg"], r["sel"],
           r["text"], r["state"])
        for r in sorted(seen.values(), key=lambda r: r["got"]))


@pytest.mark.parametrize("theme", ("light", "dark"))
def test_every_painted_text_meets_wcag_aa_on_the_served_page(measured, theme):
    rows = [r for r in measured[theme] if r["kind"] == "text"]
    assert rows, "the sweep measured no text at all -- it is not looking"
    bad = [r for r in rows if r["got"] < r["need"] - 0.005]
    assert not bad, (
        "%s theme: %d of %d painted texts are under their WCAG 2.2 AA "
        "threshold on the served page:\n%s" % (theme, len(bad), len(rows),
                                               _fmt(bad)))


@pytest.mark.parametrize("theme", ("light", "dark"))
def test_every_focus_ring_meets_1_4_11_on_the_served_page(measured, theme):
    """SC 1.4.11: a focus indicator owes 3:1 against what it is drawn on.

    Not against the page background -- against the surface underneath the
    ring itself, which for a control inside the guided-tour card is the
    card.
    """
    rows = [r for r in measured[theme] if r["kind"] == "ring"]
    assert rows, ("the sweep saw no focus ring at all -- either the walk "
                  "stopped focusing things or the ring rule is gone")
    bad = [r for r in rows if r["got"] < UI - 0.005]
    assert not bad, (
        "%s theme: %d of %d focus rings are under 3:1 against the surface "
        "they are drawn on:\n%s" % (theme, len(bad), len(rows), _fmt(bad)))


@pytest.mark.parametrize("theme", ("light", "dark"))
def test_the_sweep_actually_reached_the_dialog_and_the_tour(measured, theme):
    """The two states that only exist after JavaScript builds them are
    where both audited defects lived. A sweep that quietly stopped at the
    static page would pass everything above while measuring nothing."""
    states = {r["state"] for r in measured[theme]}
    for state in ("page", "focus", "howto", "tour"):
        assert state in states, (
            "%s theme: the sweep never reached the %r state" % (theme, state))
    tour_rings = [r for r in measured[theme]
                  if r["kind"] == "ring" and r["state"].startswith("tour")
                  and "demo-" in r["sel"]]
    assert tour_rings, (
        "%s theme: no focus ring was measured inside the guided-tour card -- "
        "that is the exact measurement this rail exists to keep" % theme)


@pytest.mark.parametrize("theme", ("light", "dark"))
def test_the_converted_report_surfaces_were_swept_too(measured, theme):
    """The report sheet and the code wells are pinned surfaces: they keep
    their colours in both themes, so a foreground that follows the theme can
    drift off them without any page-level measurement noticing.

    They only exist once something has been converted. If the conversion did
    not happen this SKIPS -- a skip says "not measured", which is the truth;
    a pass would say "measured and fine", which would not be.
    """
    states = {r["state"] for r in measured[theme]}
    if not any(s.startswith("converted") for s in states):
        pytest.skip("no conversion completed in the browser, so the preview "
                    "surfaces (--doc-paper, --code-bg) were not swept")
    sheet = [r for r in measured[theme] if r["bg"].lower() == "#ffffff"
             and "o2s-page" in r["sel"]]
    wells = [r for r in measured[theme]
             if r["state"].startswith("converted") and r["kind"] == "text"
             and r["bg"].lower() in ("#211e1b", "#131110")]
    assert sheet or wells, (
        "%s theme: a conversion ran but nothing was measured on the report "
        "sheet or in a code well" % theme)
