"""The page must load, and work, with the network unplugged.

The operator this tool is built for runs it on a locked-down state-agency
workstation that may have no internet at all. A remote asset on that
machine does not degrade politely: it either fails (syntax highlighting
disappears, code listings become grey walls) or it hangs until a proxy
gives up, which the operator experiences as "the app doesn't load".

The page used to fetch SEVEN things from the internet -- a Prism
stylesheet and three Prism scripts from cdnjs, a webfont stylesheet from
fonts.googleapis.com and its font files from fonts.gstatic.com, plus the
preconnect to each. All of it is gone: highlighting is now
frontend/static/js/highlight.js plus the syntax tokens in style.css, and
the webfonts were dropped in favour of the stacks Windows already ships.

Four layers, because "I removed the <link>" is not the same claim as "the
page works offline":

  1. THE SERVED PAGE. The bytes Flask actually sends, parsed, with every
     resource-loading and navigation attribute inspected. Prose that
     mentions a URL (the ReportServer example) is not a fetch and is not
     flagged -- only positions a browser would actually request.

  2. TEMPLATES AND STATIC ASSETS ON DISK. Anything that could reintroduce
     a request: src/href/action attributes, CSS url() and @import, URLs in
     JavaScript code, and a hostname blocklist that fires even inside a
     comment. This is the layer that fails if a CDN tag comes back.

  3. A REAL BROWSER WITH THE NETWORK BLOCKED. Every request the page makes
     is intercepted; anything not addressed to the app's own loopback port
     is recorded as a violation and aborted. Then the page is checked to
     have actually rendered, and the highlighter is exercised on real
     generated RDL so "it renders" includes "it still colours code".

  4. THE HIGHLIGHTER ITSELF, in that same browser: it must colour XML and
     SQL, must not lose or alter a single character of the source, and
     must not let source text become live markup.

Layers 3 and 4 skip when Playwright or Chromium is missing. Layers 1 and 2
always run, so a CDN tag can never come back unnoticed.
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
sys.path.insert(0, str(Path(__file__).resolve().parent))   # theme_palette

FRONTEND = ROOT / "frontend"
TEMPLATES = FRONTEND / "templates"
STATIC = FRONTEND / "static"

# Ports we must never take: 5057 is the operator's own running server.
FORBIDDEN_PORTS = {5057}

# Hostnames that only ever appear because someone reached for a CDN. These
# fire even inside a comment: a commented-out CDN tag is one uncomment away
# from being a live one, and the reviewers found this class twice.
CDN_HOSTS = (
    "cdnjs.cloudflare.com", "cdn.jsdelivr.net", "unpkg.com",
    "fonts.googleapis.com", "fonts.gstatic.com", "ajax.googleapis.com",
    "code.jquery.com", "maxcdn.bootstrapcdn.com", "stackpath.bootstrapcdn.com",
    "cdn.skypack.dev", "esm.sh", "use.fontawesome.com", "kit.fontawesome.com",
    "raw.githubusercontent.com", "cdn.tailwindcss.com",
)

# A URL a browser would go and get: absolute, or protocol-relative.
REMOTE = r"(?:https?:)?//[A-Za-z0-9][A-Za-z0-9.\-]*"

# Attributes whose value the browser fetches or navigates to.
URL_ATTRS = (
    "src", "href", "srcset", "action", "formaction", "poster", "data",
    "codebase", "cite", "background", "manifest", "ping",
)

# In markup: one of those attributes pointing somewhere remote.
ATTR_REMOTE = re.compile(
    r"\b(%s)\s*=\s*[\"']?\s*(%s)" % ("|".join(URL_ATTRS), REMOTE), re.I)

# In stylesheets: url(...) and @import.
CSS_REMOTE = re.compile(
    r"(?:url\(\s*[\"']?|@import\s+(?:url\(\s*)?[\"']?)(%s)" % REMOTE, re.I)


def _lineno(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def _strip_block_comments(text: str) -> str:
    """Blank out /* ... */ but keep every character offset intact."""
    return re.sub(r"/\*.*?\*/", lambda m: " " * len(m.group(0)), text,
                  flags=re.S)


def _is_comment_line(line: str) -> bool:
    """A whole line given over to a comment.

    A documentation URL belongs on its own comment line; a URL that a
    browser would fetch lives in code. That is the line this draws, and it
    is drawn deliberately narrow -- a trailing `// see https://...` on a
    code line WILL be flagged, and the fix is to move the note to its own
    line rather than to loosen this rail.
    """
    s = line.strip()
    return s.startswith("//") or s.startswith("*") or s.startswith("/*")


# ==========================================================================
# layer 1 -- the page Flask actually serves
# ==========================================================================
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


def test_served_page_requests_nothing_from_the_internet(dom):
    """Every URL-bearing attribute in the rendered page is local."""
    offenders = []
    for el in dom.iter():
        for attr in URL_ATTRS:
            value = (el.get(attr) or "").strip()
            if not value:
                continue
            if re.match(r"^(?:https?:)?//", value, re.I):
                offenders.append("<%s %s=%r>" % (el.tag, attr, value[:110]))
    assert not offenders, (
        "the served page points at %d remote resource(s). The workstation "
        "this ships to may be offline, where each of these either fails or "
        "hangs the page load:\n  %s"
        % (len(offenders), "\n  ".join(offenders)))


def test_served_page_names_no_cdn_host(served_html):
    """Belt and braces: not even in a comment, a style block or a script."""
    found = sorted({h for h in CDN_HOSTS if h.lower() in served_html.lower()})
    assert not found, (
        "CDN hostnames are back in the served page: %s" % ", ".join(found))


def test_served_page_inline_css_fetches_nothing(dom, served_html):
    """<style> blocks and style="" attributes: no url() to a remote host."""
    chunks = [(s.text or "") for s in dom.iter("style")]
    chunks += [(el.get("style") or "") for el in dom.iter()]
    offenders = [m.group(1) for chunk in chunks
                 for m in CSS_REMOTE.finditer(chunk)]
    assert not offenders, (
        "inline CSS in the served page fetches from: %s"
        % ", ".join(sorted(set(offenders))))


def test_served_page_loads_the_local_highlighter(served_html):
    """The offline replacement is actually wired into the page.

    Without this, 'no remote URLs' could be satisfied by shipping a page
    with no syntax highlighting at all and nobody would notice.
    """
    assert re.search(r'src="[^"]*js/highlight\.js', served_html), (
        "index.html no longer loads static/js/highlight.js -- the local "
        "replacement for the CDN Prism bundle")
    scripts = re.findall(r'<script[^>]*src="([^"]+)"', served_html)
    hl = [s for s in scripts if "highlight.js" in s]
    app_js = [s for s in scripts if "/app.js" in s]
    assert hl and app_js and scripts.index(hl[0]) < scripts.index(app_js[0]), (
        "highlight.js must load before app.js: highlightPanel() calls into "
        "it while rendering the first tab")


# ==========================================================================
# layer 2 -- every template and every static asset on disk
# ==========================================================================
def _template_files():
    return sorted(TEMPLATES.rglob("*.html"))


def _static_text_files():
    out = []
    for p in sorted(STATIC.rglob("*")):
        if p.is_file() and p.suffix.lower() in (".js", ".css", ".html",
                                                ".svg", ".json"):
            out.append(p)
    return out


def test_there_are_templates_and_static_assets_to_scan():
    """A scanner that finds no files can never fail."""
    assert _template_files(), "no templates found -- layer 2 is scanning air"
    assert _static_text_files(), "no static assets found to scan"


@pytest.mark.parametrize(
    "path", _template_files(), ids=lambda p: p.name)
def test_template_has_no_remote_resource(path):
    text = path.read_text(encoding="utf-8")
    offenders = ["%s:%d  %s=%s" % (path.name, _lineno(text, m.start()),
                                   m.group(1), m.group(2))
                 for m in ATTR_REMOTE.finditer(text)]
    offenders += ["%s:%d  css %s" % (path.name, _lineno(text, m.start()),
                                     m.group(1))
                  for m in CSS_REMOTE.finditer(text)]
    assert not offenders, (
        "template pulls from the network -- this machine may be offline:\n  "
        + "\n  ".join(offenders))


@pytest.mark.parametrize(
    "path", _static_text_files(), ids=lambda p: p.name)
def test_static_asset_has_no_cdn_hostname(path):
    text = path.read_text(encoding="utf-8").lower()
    found = sorted({h for h in CDN_HOSTS if h in text})
    assert not found, "%s names a CDN host: %s" % (path.name, ", ".join(found))


@pytest.mark.parametrize(
    "path", [p for p in _static_text_files() if p.suffix.lower() == ".css"],
    ids=lambda p: p.name)
def test_stylesheet_fetches_nothing(path):
    text = _strip_block_comments(path.read_text(encoding="utf-8"))
    offenders = ["%s:%d  %s" % (path.name, _lineno(text, m.start()), m.group(0))
                 for m in re.finditer(REMOTE, text)]
    assert not offenders, (
        "stylesheet references a remote URL:\n  " + "\n  ".join(offenders))


@pytest.mark.parametrize(
    "path", [p for p in _static_text_files() if p.suffix.lower() == ".js"],
    ids=lambda p: p.name)
def test_javascript_requests_nothing_remote(path):
    """No remote URL in executable JavaScript.

    Comment LINES may mention a URL (documentation); code may not. The CDN
    hostname rail above covers comments as well, so a commented-out CDN
    fetch still fails the build.
    """
    text = _strip_block_comments(path.read_text(encoding="utf-8"))
    offenders = []
    for n, line in enumerate(text.splitlines(), start=1):
        if _is_comment_line(line):
            continue
        for m in re.finditer(r"https?://[A-Za-z0-9][A-Za-z0-9.\-]*", line):
            offenders.append("%s:%d  %s" % (path.name, n, m.group(0)))
    assert not offenders, (
        "JavaScript would fetch from the network:\n  " + "\n  ".join(offenders))


SYNTAX_TOKENS = ("--syn-comment", "--syn-punct", "--syn-tag", "--syn-attr",
                 "--syn-string", "--syn-number", "--syn-keyword",
                 "--syn-operator")


def test_the_local_syntax_palette_is_readable_in_both_themes():
    """The CDN stylesheet shipped its own colours; ours has to earn them.

    The code well is dark in both themes but not the SAME dark, so every
    syntax colour is measured against both wells at WCAG AA for body text.
    """
    from theme_palette import contrast, palettes, resolve
    pal = palettes()
    for theme in ("light", "dark_media", "dark_attr"):
        well = resolve(pal[theme], "--code-bg")
        for token in SYNTAX_TOKENS:
            ratio = contrast(resolve(pal[theme], token), well)
            assert ratio >= 4.5, (
                "%s is %.2f:1 on the %s code well (%s) -- needs 4.5:1"
                % (token, ratio, theme, well))


def test_every_syntax_token_is_actually_painted():
    """A token nothing references is a colour nobody sees."""
    css = (STATIC / "css" / "style.css").read_text(encoding="utf-8")
    unused = [t for t in SYNTAX_TOKENS if "var(%s)" % t not in css]
    assert not unused, (
        "syntax tokens declared but never used: %s" % ", ".join(unused))


def test_no_font_face_points_at_a_remote_file():
    """@font-face is the other way a webfont sneaks back in."""
    offenders = []
    for path in _static_text_files():
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r"@font-face\s*\{[^}]*\}", text, re.S):
            for u in CSS_REMOTE.finditer(m.group(0)):
                offenders.append("%s:%d  %s"
                                 % (path.name, _lineno(text, m.start()),
                                    u.group(1)))
    assert not offenders, (
        "@font-face pulls a font file over the network:\n  "
        + "\n  ".join(offenders))


def test_the_remote_url_scanners_can_actually_fail():
    """Mutation proof for layer 2.

    Every one of the patterns above is asserted to MATCH the exact tags
    this round removed. A scanner that cannot go red would have certified
    the CDN page it was written to catch.
    """
    prism_css = ('<link href="https://cdnjs.cloudflare.com/ajax/libs/prism/'
                 '1.29.0/themes/prism-tomorrow.min.css" rel="stylesheet">')
    prism_js = ('<script src="https://cdnjs.cloudflare.com/ajax/libs/prism/'
                '1.29.0/prism.min.js"></script>')
    fonts = ('<link href="https://fonts.googleapis.com/css2?family=Inter"'
             ' rel="stylesheet">')
    preconnect = '<link rel="preconnect" href="https://fonts.gstatic.com">'
    proto_rel = '<script src="//cdn.jsdelivr.net/npm/thing.js"></script>'
    for markup in (prism_css, prism_js, fonts, preconnect, proto_rel):
        assert ATTR_REMOTE.search(markup), \
            "the attribute scanner misses %r" % markup[:70]

    assert CSS_REMOTE.search('@import url("https://fonts.googleapis.com/x");')
    assert CSS_REMOTE.search("body{background:url(//cdn.jsdelivr.net/a.png)}")
    assert re.search(REMOTE, "src = 'https://unpkg.com/x'")

    # the CDN-host rail sees a commented-out tag too
    commented = "/* <link href='https://cdnjs.cloudflare.com/x'> */".lower()
    assert any(h in commented for h in CDN_HOSTS)

    # and the JS rail ignores a comment LINE but not a fetch
    assert _is_comment_line("  // see https://example.com/spec")
    assert not _is_comment_line('  fetch("https://example.com/x");')


# ==========================================================================
# layers 3 + 4 -- a real browser with the network blocked
# ==========================================================================
def _free_port() -> int:
    while True:
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        if port not in FORBIDDEN_PORTS:
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
        except Exception as exc:                       # no browser binary
            pytest.skip("chromium unavailable: %s" % exc)
        yield b
        b.close()


@pytest.fixture(scope="module")
def offline(browser, live_url):
    """The page, loaded with everything but the app's own port cut off.

    This is the machine the tool ships to. `blocked` collects every request
    the page tried to make off-box; it must stay empty.
    """
    pg = browser.new_page(viewport={"width": 1280, "height": 900})
    blocked = []
    errors = []

    def guard(route):
        url = route.request.url
        if url.startswith("http://127.0.0.1:") or url.startswith("data:") \
                or url.startswith("blob:") or url.startswith("about:"):
            route.continue_()
        else:
            blocked.append(url)
            route.abort()

    pg.route("**/*", guard)
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(live_url, wait_until="load")
    pg.wait_for_function("() => !!document.querySelector('.tab')")
    yield pg, blocked, errors
    pg.close()


def test_page_makes_no_off_box_request(offline):
    pg, blocked, _ = offline
    assert not blocked, (
        "with the network blocked the page still tried to fetch %d "
        "resource(s): %s" % (len(blocked), ", ".join(sorted(set(blocked)))))


def test_page_renders_offline(offline):
    pg, _, errors = offline
    assert pg.locator("h1").count() >= 1
    assert pg.locator(".tab").count() > 0
    assert pg.locator("#main").count() == 1
    assert not errors, "script errors on an offline load: %s" % errors


def test_highlighter_is_present_offline(offline):
    pg, _, _ = offline
    have = pg.evaluate(
        "() => !!(window.O2SHighlight && window.Prism && "
        "typeof window.Prism.highlightElement === 'function')")
    assert have, (
        "the syntax highlighter did not load offline -- code listings "
        "would render as unstyled text")


def test_highlighter_colours_xml_and_sql_offline(offline):
    """Not 'the file loaded' -- 'the colouring happens, and it is visible'."""
    pg, _, _ = offline
    result = pg.evaluate("""() => {
      const out = {};
      // Both samples deliberately START and END on plain text, so the
      // "text between tokens" and "tail after the last token" paths are
      // both exercised -- a highlighter that drops a character there must
      // be caught, not stepped over.
      for (const [lang, src] of [
        ['xml', '\\n<Report x="1"><!-- c --><Width>6.5in</Width></Report>\\n'],
        ['sql', "\\nSELECT a, b FROM t WHERE c = 'x' AND d > 12 -- note\\n"]
      ]) {
        const pre = document.createElement('pre');
        pre.className = 'code-block language-' + lang;
        const code = document.createElement('code');
        code.className = 'language-' + lang;
        code.textContent = src;
        pre.appendChild(code);
        document.body.appendChild(pre);
        window.Prism.highlightElement(code);
        const spans = [...code.querySelectorAll('span.token')];
        out[lang] = {
          source: src,
          text: code.textContent,
          tokens: spans.length,
          classes: [...new Set(spans.map(s => s.className))],
          colours: [...new Set(spans.map(s => getComputedStyle(s).color))],
          scripts: code.querySelectorAll('script').length
        };
        pre.remove();
      }
      return out;
    }""")

    for lang in ("xml", "sql"):
        got = result[lang]
        assert got["tokens"] >= 4, \
            "%s produced almost no tokens: %r" % (lang, got)
        # no character is lost, added or altered -- colouring must never
        # change what the operator copies out of the box
        assert got["text"] == got["source"], \
            "%s highlighting altered the text: %r" % (lang, got["text"])
        assert len(got["colours"]) >= 3, (
            "%s tokens all render the same colour %r -- the syntax palette "
            "is not reaching them" % (lang, got["colours"]))
        assert got["scripts"] == 0

    assert any("comment" in c for c in result["xml"]["classes"])
    assert any("tag" in c for c in result["xml"]["classes"])
    assert any("keyword" in c for c in result["sql"]["classes"])
    assert any("string" in c for c in result["sql"]["classes"])


def test_highlighter_never_turns_source_text_into_markup(offline):
    """Source text is data. An RDL full of angle brackets must not execute.

    The payload is carried INSIDE a token (a SQL string literal) as well as
    loose in the markup. That matters: a tag split across several spans
    cannot reassemble into an element by accident, so an XML-only payload
    would pass even with escaping switched off entirely -- measured. A
    payload inside a single token is the shape that actually goes into
    innerHTML in one piece.
    """
    pg, _, _ = offline
    bad = pg.evaluate("""() => {
      const payload = '<img src=x onerror="window.__o2s_xss=1">';
      const cases = {
        sql_string: ["SELECT '" + payload + "' FROM dual", 'sql'],
        sql_comment: ['-- ' + payload, 'sql'],
        xml_text: ['<Value>' + payload + '</Value>', 'xml'],
        xml_attr: ['<T x="' + payload + '"/>', 'xml']
      };
      const out = {};
      for (const key in cases) {
        const [src, lang] = cases[key];
        const html = window.O2SHighlight.highlight(src, lang);
        const host = document.createElement('div');
        host.innerHTML = html;
        document.body.appendChild(host);
        out[key] = {
          elements: host.querySelectorAll('img,script,iframe,object').length,
          // every '<' the SOURCE contained must have come out escaped
          raw_angles: (html.match(/<(?!\\/?span\\b)/g) || []).length,
          text: host.textContent
        };
        host.remove();
      }
      out.fired = !!window.__o2s_xss;
      return out;
    }""")
    assert bad.pop("fired") is False, "source text executed"
    for key, got in bad.items():
        assert got["elements"] == 0, \
            "%s: source text became %d live element(s)" % (key, got["elements"])
        assert got["raw_angles"] == 0, \
            "%s: %d unescaped '<' reached innerHTML" % (key, got["raw_angles"])
        assert "<img" in got["text"], \
            "%s: the payload should survive as visible TEXT, got %r" \
            % (key, got["text"])


def test_real_generated_rdl_is_highlighted_offline(browser, live_url):
    """End to end: convert a bundled sample with the network cut off and
    read the colours off the RDL the converter actually produced."""
    pg = browser.new_page(viewport={"width": 1280, "height": 900})
    blocked = []

    def guard(route):
        if route.request.url.startswith("http://127.0.0.1:"):
            route.continue_()
        else:
            blocked.append(route.request.url)
            route.abort()

    pg.route("**/*", guard)
    try:
        pg.goto(live_url, wait_until="load")
        pg.wait_for_function("() => !!document.querySelector('.tab')")
        if pg.locator(".sample-chip").count() == 0:
            pytest.skip("no bundled sample to convert")
        pg.click(".sample-chip")
        pg.wait_for_function(
            "() => document.querySelectorAll('#mockup-host *').length > 0",
            timeout=90000)
        tab = pg.locator('[data-tab="rdl"]')
        if not tab.count() or not tab.first.is_visible():
            pytest.skip("this sample produced no RDL tab")
        tab.first.click()
        pg.wait_for_timeout(600)
        seen = pg.evaluate("""() => {
          const code = document.querySelector(
            '#tab-rdl code[class*="language-"]');
          if (!code) return null;
          const spans = [...code.querySelectorAll('span.token')];
          return {
            chars: (code.textContent || '').length,
            tokens: spans.length,
            colours: [...new Set(spans.map(s => getComputedStyle(s).color))]
          };
        }""")
        assert seen, "the RDL tab has no code block to highlight"
        assert seen["chars"] > 200, "the RDL tab is empty: %r" % seen
        assert seen["tokens"] > 20, (
            "real generated RDL came out unhighlighted offline: %r" % seen)
        assert len(seen["colours"]) >= 3, \
            "the RDL is monochrome -- the syntax palette is missing: %r" % seen
        assert not blocked, "the conversion reached off-box: %s" % blocked
    finally:
        pg.close()
