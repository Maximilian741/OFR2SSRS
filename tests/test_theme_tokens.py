"""The colour layer is a SYSTEM, and these are the rules that keep it one.

Five things can quietly destroy a two-theme token system, and there is a
test here for each:

  1. someone drops a literal colour into a component rule -- it then ignores
     the theme completely (this is how the app got a white-on-salmon button
     at 2.66:1 in dark mode);
  2. a rule references a token nobody declared -- var() with no fallback
     silently inherits, so the element loses its colour and no error is
     raised anywhere;
  3. the two dark blocks drift apart, so the toggle and the system
     preference render different applications;
  4. the dark media block loses its :not([data-theme="light"]) guard, so
     choosing Light on a dark machine stops working;
  5. the no-flash boot script gets deferred, moved to the end of <body>, or
     dropped, and the operator sees a white flash on every load.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from theme_palette import (                                    # noqa: E402
    STYLE_CSS, dark_blocks, dark_token_text, light_block, loaded_scripts,
    palettes, read_css, resolve,
)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
FRONTEND = ROOT / "frontend"
INDEX = FRONTEND / "templates" / "index.html"
THEME_JS = FRONTEND / "static" / "js" / "theme.js"

# JS that index.html actually loads. A colour literal in any of these is a
# component rule too -- it just skipped the stylesheet on the way in.
#
# READ FROM index.html, not typed here: the hand-typed version of this list
# was two files short (highlight.js and scroll_regions.js), so a literal in
# either of them was invisible to the rule. A list of files is exactly the
# kind of thing that falls behind the page it describes.
LOADED_JS = list(loaded_scripts())

HEX = re.compile(r"(?<![&\w])#[0-9a-fA-F]{3,8}\b")
FUNC = re.compile(r"\b(?:rgba?|hsla?)\s*\(")
NAMED = re.compile(
    r"\b(white|black|red|green|blue|gray|grey|silver|navy|orange|yellow"
    r"|purple|pink|teal|lime|maroon|olive|aqua|fuchsia|crimson|gold)\b(?!-)",
    re.I)


@pytest.fixture(scope="module")
def css():
    return read_css()


@pytest.fixture(scope="module")
def pal():
    return palettes()


def _strip_comments(text: str) -> str:
    """Blank out comments but keep every character offset intact."""
    return re.sub(r"/\*.*?\*/", lambda m: " " * len(m.group(0)), text,
                  flags=re.S)


def _token_spans(css: str):
    """(start, end) offsets of the three declaration blocks that are ALLOWED
    to contain colour literals."""
    spans = []
    light = light_block(css)
    spans.append((css.index(light), css.index(light) + len(light)))
    for body in dark_blocks(css).values():
        spans.append((css.index(body), css.index(body) + len(body)))
    return spans


# --------------------------------------------------------------------------
# 1. no literal colour outside the token layer
# --------------------------------------------------------------------------
def test_no_component_rule_hardcodes_a_colour(css):
    body = _strip_comments(css)
    spans = _token_spans(css)
    inside = lambda i: any(a <= i < b for a, b in spans)   # noqa: E731

    offenders = []
    for rx in (HEX, FUNC, NAMED):
        for m in rx.finditer(body):
            if inside(m.start()):
                continue
            line = body.count("\n", 0, m.start()) + 1
            offenders.append("style.css:%d  %s" % (line, m.group(0)))
    assert not offenders, (
        "colour literals outside the token layer -- these ignore the theme:\n"
        + "\n".join(offenders))


@pytest.mark.parametrize("name", LOADED_JS)
def test_loaded_javascript_paints_with_tokens_only(name):
    src = (FRONTEND / "static" / "js" / name).read_text(encoding="utf-8")
    src = re.sub(r"//[^\n]*", " ", src)          # line comments
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    hits = [m.group(0) for m in HEX.finditer(src)]
    # a var() fallback is allowed: var(--good, #2a7d2a) still themes
    fallbacks = set(re.findall(r"var\(\s*--[\w-]+\s*,\s*(#[0-9a-fA-F]{3,8})",
                               src))
    hits = [h for h in hits if h not in fallbacks]
    assert not hits, (
        "%s injects literal colours (%s) -- inline styles are component "
        "rules and must use var(--token) so they follow the theme"
        % (name, ", ".join(sorted(set(hits)))))


# --------------------------------------------------------------------------
# 2. every var() the stylesheet uses is actually declared
# --------------------------------------------------------------------------
def test_every_referenced_token_is_declared(css, pal):
    declared = set(pal["light"])
    used = set(re.findall(r"var\(\s*(--[A-Za-z0-9_-]+)", _strip_comments(css)))
    missing = sorted(used - declared)
    assert not missing, (
        "style.css references tokens that :root never declares: %s. "
        "An undefined var() with no fallback makes the property inherit, "
        "so the element silently loses its colour." % ", ".join(missing))


def test_every_declared_token_resolves(pal):
    """Aliases must terminate at a literal, in every theme."""
    for theme, tokens in pal.items():
        for name in tokens:
            resolve(tokens, name)          # raises on a cycle or a gap


# --------------------------------------------------------------------------
# 3 + 4. the two dark blocks, and the guard that makes the toggle win
# --------------------------------------------------------------------------
def test_the_two_dark_blocks_are_identical(css):
    text = dark_token_text(css)
    assert text["dark_media"] == text["dark_attr"], (
        "the media-query dark block and the [data-theme=dark] block have "
        "drifted apart -- the toggle and the system preference would render "
        "two different applications")


def test_dark_overrides_only_tokens_that_light_declares(pal):
    light = set(pal["light"])
    for dark in ("dark_media", "dark_attr"):
        extra = sorted(set(pal[dark]) - light)
        assert not extra, (
            "%s declares tokens the light palette does not: %s. The complete "
            "palette belongs on bare :root; dark only redefines."
            % (dark, ", ".join(extra)))


def test_dark_media_block_is_guarded_so_an_explicit_light_choice_wins(css):
    # dark_blocks() asserts the :not([data-theme="light"]) guard exists;
    # this pins the reason it has to.
    dark_blocks(css)
    assert ':root:not([data-theme="light"])' in css


def test_color_scheme_is_declared_for_both_themes(css, pal):
    assert re.search(r":root\s*\{[^}]*color-scheme\s*:\s*light", css, re.S), \
        "bare :root must declare color-scheme:light"
    for body in dark_blocks(css).values():
        assert re.search(r"color-scheme\s*:\s*dark", body), \
            "each dark block must declare color-scheme:dark so native " \
            "scrollbars and form controls follow the theme"


def test_dark_is_a_designed_palette_not_an_inversion(pal):
    """Every surface must be darker AND every ink lighter -- an inverted
    filter or a half-finished copy fails this."""
    from theme_palette import luminance
    for dark in ("dark_media", "dark_attr"):
        for surf in ("--surface-0", "--paper", "--surface-1", "--surface-2"):
            assert luminance(resolve(pal[dark], surf)) < 0.10, \
                "%s %s is not a dark surface" % (dark, surf)
        for ink in ("--ink", "--ink-2", "--ink-3", "--ink-faint"):
            assert luminance(resolve(pal[dark], ink)) > \
                luminance(resolve(pal["light"], ink))
        # the depth model is preserved: cards above the page, wells below it
        lum = lambda t: luminance(resolve(pal[dark], t))       # noqa: E731
        assert lum("--surface-2") < lum("--surface-1") < \
            lum("--paper") < lum("--surface-0")


# --------------------------------------------------------------------------
# 5. the toggle, and the boot script that stops the flash
# --------------------------------------------------------------------------
@pytest.fixture()
def served_html():
    from app import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        res = c.get("/")
        assert res.status_code == 200
        return res.get_data(as_text=True)


def test_theme_boot_script_is_synchronous_and_in_the_head(served_html):
    head = served_html.split("</head>", 1)[0]
    m = re.search(r"<script[^>]*js/theme\.js[^>]*>", head)
    assert m, ("theme.js must be loaded from <head>. Anywhere else and the "
               "stored theme is applied after the first paint, which is the "
               "flash of the wrong theme.")
    tag = m.group(0)
    assert "defer" not in tag and "async" not in tag, (
        "theme.js must block parsing: %s" % tag)


def test_toggle_offers_system_light_and_dark_as_real_radios(served_html):
    assert 'id="theme-toggle"' in served_html
    assert "<fieldset" in served_html and "<legend" in served_html
    for value in ("system", "light", "dark"):
        assert re.search(
            r'<input[^>]*name="o2s-theme"[^>]*value="%s"' % value,
            served_html), "no radio for the %s theme" % value
        assert 'for="theme-%s"' % value in served_html, (
            "the %s option has no <label for> -- a placeholder or a bare "
            "span is not an accessible name" % value)


def test_toggle_is_not_hidden_from_the_keyboard():
    """display:none would drop the radios out of the tab order entirely."""
    css = read_css()
    m = re.search(r"\.theme-radio\s*\{([^}]*)\}", css)
    assert m, ".theme-radio has no rule"
    rule = m.group(1)
    assert "display:none" not in rule.replace(" ", "")
    assert "visibility:hidden" not in rule.replace(" ", "")


def test_focus_ring_is_declared_for_the_toggle():
    css = read_css()
    assert ".theme-radio:focus-visible + .theme-option" in css
    assert re.search(
        r"\.theme-radio:focus-visible \+ \.theme-option\{[^}]*outline:2px "
        r"solid var\(--focus-ring\)", css.replace("\n", ""))


def test_theme_js_persists_the_choice_and_clears_the_attribute_for_system():
    js = THEME_JS.read_text(encoding="utf-8")
    assert '"o2s-theme"' in js, "the localStorage key must be stable"
    assert "localStorage.setItem" in js and "localStorage.getItem" in js
    assert 'removeAttribute("data-theme")' in js, (
        "choosing System must REMOVE data-theme, otherwise the "
        "prefers-color-scheme media query can never take effect again")
    assert 'setAttribute("data-theme", pref)' in js
    assert "prefers-color-scheme: dark" in js, (
        "System mode has to know which way it resolved in order to say so")
    # Storage can be switched off by policy; neither accessor may throw.
    for fn in ("readPref", "savePref"):
        body = js.split("function %s(" % fn, 1)[1]
        head = body[:body.index("\n  }")]
        assert "try" in head and "catch" in head, (
            "%s must survive localStorage being unavailable" % fn)


def test_option_targets_clear_the_wcag_22_minimum():
    css = read_css()
    m = re.search(r"\.theme-option\s*\{([^}]*)\}", css)
    assert m, ".theme-option has no rule"
    rule = m.group(1).replace(" ", "")
    for prop in ("min-height:", "min-width:"):
        got = re.search(re.escape(prop) + r"(\d+)px", rule)
        assert got and int(got.group(1)) >= 24, (
            "%s must be at least 24px (WCAG 2.2 Target Size)" % prop)
