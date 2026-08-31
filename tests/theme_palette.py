"""Read the app's colour tokens straight out of the shipped stylesheet.

Not a test by itself -- the helper the theme rails share. It parses
frontend/static/css/style.css into three palettes:

    light      -> the bare :root block
    dark_media -> @media (prefers-color-scheme: dark) :root:not([data-theme="light"])
    dark_attr  -> :root[data-theme="dark"]

and resolves var() aliases, so a test can ask for the value a browser would
actually compute rather than the literal text of a declaration.

Contrast maths is the WCAG 2.x relative-luminance formula, verbatim.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STYLE_CSS = ROOT / "frontend" / "static" / "css" / "style.css"

DARK_START = "/* O2S-DARK-TOKENS-START */"
DARK_END = "/* O2S-DARK-TOKENS-END */"


# --------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------
def _decls(block: str) -> dict:
    """--name:value pairs from a declaration block, comments stripped."""
    block = re.sub(r"/\*.*?\*/", " ", block, flags=re.S)
    out = {}
    for name, value in re.findall(r"(--[A-Za-z0-9_-]+)\s*:\s*([^;}]+)", block):
        out[name] = value.strip()
    return out


def _balanced_block(text: str, open_idx: int) -> str:
    """Return the body of the { } group whose opening brace is at open_idx."""
    depth = 0
    for i in range(open_idx, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[open_idx + 1:i]
    raise AssertionError("unbalanced braces in stylesheet")


def read_css(path: Path | None = None) -> str:
    return (path or STYLE_CSS).read_text(encoding="utf-8")


def light_block(css: str) -> str:
    """The bare `:root {` rule -- not :root:not(...) and not :root[...]."""
    m = re.search(r"(?<![\w\-\]\)]):root\s*\{", css)
    assert m, "no bare :root block in style.css"
    return _balanced_block(css, m.end() - 1)


def dark_blocks(css: str) -> dict:
    """Both dark declaration blocks, keyed by how the browser reaches them."""
    media = re.search(
        r"@media\s*\(\s*prefers-color-scheme\s*:\s*dark\s*\)\s*\{", css)
    assert media, "no @media (prefers-color-scheme: dark) block"
    media_body = _balanced_block(css, media.end() - 1)
    guard = re.search(r':root:not\(\[data-theme=["\']light["\']\]\)\s*\{',
                      media_body)
    assert guard, (
        "the dark media block must be guarded with "
        ':root:not([data-theme="light"]) so an explicit Light choice '
        "overrides a dark operating system")
    attr = re.search(r':root\[data-theme=["\']dark["\']\]\s*\{', css)
    assert attr, 'no :root[data-theme="dark"] block'
    return {
        "dark_media": _balanced_block(media_body, guard.end() - 1),
        "dark_attr": _balanced_block(css, attr.end() - 1),
    }


def palettes(css: str | None = None) -> dict:
    css = css if css is not None else read_css()
    light = _decls(light_block(css))
    dark_raw = dark_blocks(css)
    out = {"light": light}
    for key, body in dark_raw.items():
        merged = dict(light)          # dark redefines ONLY what it overrides
        merged.update(_decls(body))
        out[key] = merged
    return out


def dark_token_text(css: str | None = None) -> dict:
    """The raw text between the START/END markers in each dark block, so a
    test can prove the two copies did not drift apart."""
    css = css if css is not None else read_css()
    out = {}
    for key, body in dark_blocks(css).items():
        assert DARK_START in body and DARK_END in body, (
            "dark block %s lost its O2S-DARK-TOKENS markers" % key)
        chunk = body.split(DARK_START, 1)[1].split(DARK_END, 1)[0]
        out[key] = "\n".join(
            line.strip() for line in chunk.splitlines() if line.strip())
    return out


# --------------------------------------------------------------------------
# resolving + colour maths
# --------------------------------------------------------------------------
_VAR = re.compile(r"var\(\s*(--[A-Za-z0-9_-]+)\s*(?:,[^)]*)?\)")


def resolve(palette: dict, token: str, _seen: tuple = ()) -> str:
    """Follow var() aliases to the literal a browser would compute."""
    assert token not in _seen, "circular token alias: %s" % token
    assert token in palette, "undefined token %s" % token
    value = palette[token].strip()
    m = _VAR.fullmatch(value)
    if m:
        return resolve(palette, m.group(1), _seen + (token,))
    return value


def rgb(colour: str) -> tuple:
    c = colour.strip()
    m = re.fullmatch(r"#([0-9a-fA-F]{3})", c)
    if m:
        return tuple(int(ch * 2, 16) for ch in m.group(1))
    m = re.fullmatch(r"#([0-9a-fA-F]{6})", c)
    if m:
        h = m.group(1)
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    m = re.fullmatch(r"rgba?\(([^)]+)\)", c)
    if m:
        parts = [p for p in re.split(r"[\s,/]+", m.group(1)) if p]
        return tuple(float(p) for p in parts[:3])
    raise AssertionError("not a measurable colour: %r" % colour)


def luminance(colour: str) -> float:
    def chan(v):
        v = v / 255.0
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (chan(x) for x in rgb(colour))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def ratio(palette: dict, fg: str, bg: str) -> float:
    return contrast(resolve(palette, fg), resolve(palette, bg))


# --------------------------------------------------------------------------
# what the app actually PAINTS
#
# The contrast rail used to be a hand-written list of token pairs. A hand
# list has one failure mode and the audit found both halves of it: a pairing
# nobody thought of is simply absent, and nothing says so. Two live defects
# shipped underneath a green gate -- a focus ring measured only against the
# four page surfaces while the guided-tour card is a FIFTH surface (pinned
# dark, declared in JavaScript, invisible to a stylesheet-only scan), and a
# token exempted as "decorative" that was painting 13.5px text.
#
# So the pairings are now DERIVED from what the source paints: every
# `background`, every `color`, every `outline` in the stylesheet AND in the
# stylesheets the loaded scripts inject, each one carrying the selector that
# paints it. The tables in tests/test_theme_contrast.py are checked against
# this scan for completeness, which is what makes "we forgot one" fail
# rather than pass quietly.
# --------------------------------------------------------------------------
INDEX_HTML = ROOT / "frontend" / "templates" / "index.html"
JS_DIR = ROOT / "frontend" / "static" / "js"

# A background is a SURFACE. Everything else in these three groups is drawn
# ON one, and owes it a contrast ratio.
SURFACE_PROPS = frozenset({"background", "background-color"})
TEXT_PROPS = frozenset({"color", "fill", "stroke", "-webkit-text-fill-color"})
RING_PROPS = frozenset({"outline", "outline-color", "box-shadow"})
EDGE_PROPS = frozenset({
    "border", "border-color", "border-top", "border-right", "border-bottom",
    "border-left", "border-top-color", "border-right-color",
    "border-bottom-color", "border-left-color", "text-decoration-color",
    "caret-color", "accent-color", "column-rule", "column-rule-color",
    "outline-offset",
})

_LITERAL = re.compile(
    r'"((?:[^"\\\n]|\\.)*)"'
    r"|'((?:[^'\\\n]|\\.)*)'"
    r"|`((?:[^`\\]|\\.)*)`")
_CSSISH = re.compile(r"[{}]|[a-zA-Z-]+\s*:\s*[^;]+;")
_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_DECL = re.compile(r"(-?[a-zA-Z][a-zA-Z-]*)\s*:\s*([^;]+)")
_USES = re.compile(r"var\(\s*(--[A-Za-z0-9_-]+)")


class Paint(tuple):
    """One painted colour: (source, selector, property, token).

    `selector` is None for a declaration fragment with no rule around it --
    an inline style or an `el.style.cssText = "..."`, which can land on any
    element the script chooses.
    """
    __slots__ = ()

    def __new__(cls, source, selector, prop, token):
        return tuple.__new__(cls, (source, selector, prop, token))

    source = property(lambda s: s[0])
    selector = property(lambda s: s[1])
    prop = property(lambda s: s[2])
    token = property(lambda s: s[3])


def loaded_scripts(html: str | None = None) -> tuple:
    """The scripts index.html actually loads, in order.

    Derived, not listed: a script added to the page joins the scan the
    moment it is wired up, which is the whole point -- the tour stylesheet
    that hid the ring defect lives in one of these files.
    """
    html = html if html is not None else INDEX_HTML.read_text(encoding="utf-8")
    seen, out = set(), []
    for name in re.findall(r"js/([A-Za-z0-9_.-]+\.js)", html):
        if name not in seen:
            seen.add(name)
            out.append(name)
    assert out, "index.html loads no scripts -- the scan would be empty"
    return tuple(out)


def _strip_comments(text: str) -> str:
    return re.sub(r"/\*.*?\*/", " ", text, flags=re.S)


def css_chunks(js_src: str) -> list:
    """The CSS a script carries, reconstructed from its string literals.

    A script builds a stylesheet either as an array of lines it joins or as
    one template literal; both come back as a run of CSS-looking literals in
    source order. Runs are kept separate so an unrelated fragment two
    functions away is never glued onto the end of a rule.
    """
    js = re.sub(r"//[^\n]*", " ", _strip_comments(js_src))
    chunks, run = [], []
    for m in _LITERAL.finditer(js):
        lit = m.group(1) or m.group(2) or m.group(3) or ""
        if _CSSISH.search(lit):
            run.append(lit)
        elif run:
            chunks.append("\n".join(run))
            run = []
    if run:
        chunks.append("\n".join(run))
    return chunks


def _rules(css: str):
    for sel, body in _RULE.findall(css):
        sel = " ".join(sel.split())
        if not sel or sel.startswith("@") or sel.startswith(":root"):
            continue
        yield sel, body


def _paints_from_css(source: str, css: str, out: list) -> None:
    for sel, body in _rules(css):
        for prop, value in _DECL.findall(body):
            for tok in _USES.findall(value):
                out.append(Paint(source, sel, prop.lower(), tok))


def paints(css: str | None = None) -> tuple:
    """Every colour the app paints, from the stylesheet AND the scripts."""
    out: list = []
    _paints_from_css("style.css", _strip_comments(
        css if css is not None else read_css()), out)
    for name in loaded_scripts():
        path = JS_DIR / name
        if not path.exists():
            continue
        src = path.read_text(encoding="utf-8")
        for chunk in css_chunks(src):
            if "{" in chunk and "}" in chunk:
                _paints_from_css(name, chunk, out)
            else:
                for prop, value in _DECL.findall(chunk):
                    for tok in _USES.findall(value):
                        out.append(Paint(name, None, prop.lower(), tok))
        # el.style.someProperty = "var(--token)". `cssText` is skipped on
        # purpose: it carries a whole declaration list, which the chunk pass
        # above already read property by property -- reading it again here
        # would file every token in it under the property name "cssText".
        for m in re.finditer(
                r"\.style\.([A-Za-z]+)\s*=\s*[\"'][^\"']*var\(\s*(--[\w-]+)",
                src):
            if m.group(1) == "cssText":
                continue
            prop = re.sub(r"([A-Z])", lambda x: "-" + x.group(1).lower(),
                          m.group(1)).lower()
            out.append(Paint(name, None, prop, m.group(2)))
    return tuple(out)


def canonical(palette: dict, token: str, _seen: tuple = ()) -> str:
    """Follow var() aliases to the token that actually holds the colour.

    `--text-muted:var(--ink-2)` is --ink-2 wearing another name; measuring
    it twice under two names, or missing it because the alias was never
    listed, are the same bug.
    """
    value = palette.get(token, "").strip()
    m = _VAR.fullmatch(value)
    if m and m.group(1) not in _seen:
        return canonical(palette, m.group(1), _seen + (token,))
    return token


def is_colour(palette: dict, token: str) -> bool:
    """True when the token resolves to something contrast can be measured
    on. `--shadow-lg` (a whole box-shadow) and `--font-sans` do not."""
    try:
        rgb(resolve(palette, token))
        return True
    except AssertionError:
        return False


def selector_is_global(selector: str) -> bool:
    """True when the leftmost compound names no class and no id.

    `button:focus-visible` matches a button ANYWHERE -- including inside a
    card that is pinned dark and paints its own surface. That is exactly how
    the guided-tour focus ring came to be measured against the page and
    never against the card it is actually drawn on.
    """
    for one in selector.split(","):
        one = one.strip()
        if not one:
            continue
        head = re.split(r"[\s>+~]", one)[0]
        if not re.search(r"[.#]", head):
            return True
    return False


FOCUSABLE_IN_SELECTOR = re.compile(
    r"(?:^|[\s>+~])(?:a|button|input|select|textarea|summary|details)"
    r"(?![\w-])|:focus|\[tabindex")


def selectors(css: str | None = None) -> tuple:
    """(source, selector) for every rule, whether or not it paints a colour.

    paints() only sees declarations that use a token; a rule like
    `.demo-tooltip button { border:0 }` uses none, and it is the only thing
    in the source that says a BUTTON lives inside that card. Knowing where
    the controls are is how a surface's claim to hold a focus ring stops
    being a claim.
    """
    out = []
    for sel, _body in _rules(_strip_comments(
            css if css is not None else read_css())):
        out.append(("style.css", sel))
    for name in loaded_scripts():
        path = JS_DIR / name
        if not path.exists():
            continue
        for chunk in css_chunks(path.read_text(encoding="utf-8")):
            if "{" in chunk and "}" in chunk:
                for sel, _body in _rules(chunk):
                    out.append((name, sel))
    return tuple(out)
