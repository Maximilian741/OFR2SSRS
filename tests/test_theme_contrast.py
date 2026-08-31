"""Measured contrast for every token pair the app actually paints, in BOTH
themes.

WHAT CHANGED, AND WHY IT HAD TO
-------------------------------
This file used to be a hand-written list of pairings. Two live defects
shipped underneath it while it reported 216 green in half a second:

  * the guided-tour focus ring measured 2.76:1 in the light theme. The tour
    tooltip is a card PINNED DARK in both themes (--tour-bg, declared in
    demo_mode.js), but the ring token flips with the theme, so in light mode
    a light-theme ring sat on a dark card. The list paired --focus-ring with
    the four PAGE surfaces and stopped there; the fifth surface was never in
    it, and nothing said so.
  * --ink-deco was on the "decorative, deliberately unmeasured" list while
    painting the three 13.5px arrow glyphs between the pipeline steps in the
    How-it-works dialog, at 2.58:1. The exemption named a reason that was
    simply not true.

Both are the same failure: a hand list cannot report the pairing it does not
contain. So the pairings are now checked against what the source actually
PAINTS -- every background, colour and outline in the stylesheet AND in the
stylesheets the loaded scripts inject (theme_palette.paints()), each one
carrying the selector that paints it. PAIRS below is still the readable
record, but it is no longer the whole gate:

  * SURFACES must name every token painted as a background anywhere, so a
    new card -- pinned or themed, CSS or JavaScript -- cannot arrive unnoticed;
  * a ring painted by a GLOBAL selector (`button:focus-visible` matches a
    button inside any card) must be measured against every surface that
    holds a control, unless that surface declares its own ring token AND the
    source proves the override exists;
  * every token painted as text must be measured as text somewhere -- there
    is no "decorative" escape for a `color:` declaration;
  * a text rule written inside a surface's own selector must be measured
    against that surface.

Thresholds are WCAG 2.2 level AA:
    text  4.5:1   body copy and anything under 18.66px/24px
    large 3.0:1   >=24px, or >=18.66px bold
    ui    3.0:1   1.4.11 Non-text Contrast -- control edges, focus rings

tests/test_theme_contrast_live.py measures the same thing the other way
round -- computed styles on the served page, in a real browser, in both
themes -- and is the authority for anything only the DOM knows.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from theme_palette import (                                    # noqa: E402
    FOCUSABLE_IN_SELECTOR, RING_PROPS, SURFACE_PROPS, TEXT_PROPS, canonical,
    is_colour, paints, palettes, ratio, resolve, selector_is_global,
    selectors,
)

THEMES = ("light", "dark_media", "dark_attr")

TEXT, LARGE, UI = 4.5, 3.0, 3.0

# --------------------------------------------------------------------------
# THE SURFACES. Every token this app paints as a background, what sits ON it,
# and -- where the answer is not the global focus ring -- which ring is drawn
# over it. Completeness is asserted against the paint scan below, so this
# table cannot silently fall behind the stylesheet.
#
#   holds:  "text"  something is written on this surface
#           "ring"  a focus indicator can be drawn over it
#   ring:   the token that paints that indicator. The default is the one
#           global ring; a surface that pins its own colours (a dark card in
#           a light theme) has to pin its ring too, and prove it.
# --------------------------------------------------------------------------
GLOBAL_RING = "--focus-ring"


def _s(where, holds=("text",), ring=None, why=""):
    return {"where": where, "holds": frozenset(holds), "ring": ring,
            "why": why}


SURFACES = {
    # ---- the four page surfaces: the depth model, both themes ----------
    "--paper": _s("html,body -- the page ground", ("text", "ring")),
    "--surface-0": _s("cards, top bar, inputs, .howto-panel", ("text", "ring")),
    "--surface-1": _s("sidebar, drop zone, pills, .howto-pipe-step",
                      ("text", "ring")),
    "--surface-2": _s("wells: .mockup-host, .render-host, .fidelity-bar",
                      ("text", "ring")),

    # ---- tinted strips that hold copy AND controls ---------------------
    "--accent-wash": _s(".mockup-cta-bar, .burst-callout, .o2s-ai-auto-bar,"
                        " .tab-badge, ::selection", ("text", "ring")),
    "--good-wash": _s(".preflight-banner.ok, .deploy-status.ds-ready,"
                      " .pill.good", ("text", "ring")),
    "--warn-wash": _s(".preflight-banner.warn, .cta-fix-first,"
                      " .mockup-fallback-note", ("text", "ring")),
    "--bad-wash": _s(".preflight-banner.bad, .deploy-status.ds-blocker,"
                     " .pill.bad", ("text", "ring")),

    # ---- filled controls: the ring is drawn OUTSIDE the fill (2px of
    #      offset shows the container), so a fill holds text only --------
    "--accent": _s(".btn-primary, .cta-btn, .ds-btn, .skip-link", ("text",),
                   why="a filled control: its focus ring is drawn on the "
                       "container behind it, never on the fill"),
    "--accent-hi": _s(".btn-primary:hover, .cta-btn:hover", ("text",),
                      why="hover fill of the same buttons"),
    "--good": _s(".toast.ok, .fidelity-bar > span", ("text",),
                 why="a filled status control"),
    "--warn": _s(".cta-fix-first .cta-btn", ("text",),
                 why="a filled status control"),
    "--warn-hi": _s(".cta-fix-first .cta-btn:hover", ("text",),
                    why="hover fill of the same button"),
    "--bad": _s(".toast.err", ("text",), why="a filled status control"),
    "--ink": _s('.toast, .mockup-mode-btn[aria-checked="true"]', ("text",),
                why="inverted chips; the mode button's ring is drawn on the "
                    "toolbar behind it, not on the chip"),

    # ---- surfaces pinned in BOTH themes --------------------------------
    "--code-bg": _s(".code-block, .issue-excerpt, .audit-snippet,"
                    " .activity-cmd", ("text", "ring"), ring="--code-focus",
                    why="a listing that scrolls sideways is a tab stop "
                        "(scroll_regions.js) and its ring is drawn INSIDE "
                        "the box, on the well itself -- which is dark in "
                        "BOTH themes, so the well pins its own ring just "
                        "like the tour card: the theme's light-theme ring "
                        "measured 3.03:1 there, a pass by 0.03"),
    "--doc-paper": _s(".mockup-host .o2s-page -- the simulated report sheet",
                      ("text",),
                      why="content sandbox: the mockup generator emits no "
                          "link, button or tab stop, so no focus ring is "
                          "drawn on the sheet. test_theme_contrast_live.py "
                          "converts a real report and re-checks this on the "
                          "rendered preview"),
    "--tour-bg": _s(".demo-tooltip (demo_mode.js) -- the guided-tour card",
                    ("text", "ring"), ring="--tour-focus",
                    why="the card is pinned dark in both themes, so the ring "
                        "inside it is pinned too: the theme's own ring is a "
                        "light-theme colour on a dark card (2.76:1)"),
    "--tour-cta": _s(".demo-tooltip .demo-next -- the tour's Next button",
                     ("text",),
                     why="a filled control; its ring is drawn on --tour-bg"),

    # ---- seams and dimmers: nothing is painted on these ----------------
    "--ink-3": _s(".topbar-pill::before dot, scrollbar thumb on hover", (),
                  why="a 6px dot and a scrollbar thumb -- no content sits "
                      "on either"),
    "--line": _s(".side-by-side -- the 1px seam between the two columns", (),
                 why="a gap the columns paint over; each column paints "
                     "--surface-0 for its own content"),
    "--line-strong": _s("::-webkit-scrollbar-thumb", (),
                        why="the thumb itself; nothing is drawn on it"),
    "--scrim": _s(".howto-backdrop -- the modal dimmer", (),
                  why="the dialog above it paints its own surface and moves "
                      "focus to the first control INSIDE itself, so no ring "
                      "is ever drawn on the dimmer (measured in Chromium: "
                      "opening with Enter focuses .howto-close)"),
}

# (foreground, background, threshold, "where this pairing is painted")
PAIRS = [
    # ---- the ink ramp on every surface it can land on -------------------
    ("--ink", "--surface-0", TEXT, "body copy in cards / topbar"),
    ("--ink", "--paper", TEXT, "body copy on the page ground"),
    ("--ink", "--surface-1", TEXT, "sidebar body copy"),
    ("--ink", "--surface-2", TEXT, "text in wells (.mockup-host, .render-host)"),
    ("--ink-2", "--surface-0", TEXT, ".muted-note, .deploy-body, .cta-text"),
    ("--ink-2", "--paper", TEXT, "secondary prose on the page ground"),
    ("--ink-2", "--surface-1", TEXT, ".batch-check, sidebar secondary prose"),
    ("--ink-2", "--surface-2", TEXT, ".deploy-num, .burst-steps counters"),
    ("--ink-3", "--surface-0", TEXT, ".panel-toolbar-right, .extras-table th"),
    ("--ink-3", "--paper", TEXT, ".brand-tag, meta labels on the ground"),
    ("--ink-3", "--surface-1", TEXT, ".side-heading, inactive .tab"),
    ("--ink-3", "--surface-2", TEXT, ".render-page-label over a well"),
    ("--ink-faint", "--surface-0", TEXT, "::placeholder, .burst-field-hint"),
    ("--ink-faint", "--paper", TEXT, ".tab-adv labels"),
    ("--ink-faint", "--surface-1", TEXT, "placeholder inside a filled field"),
    ("--ink-faint", "--surface-2", TEXT, "hint text over a well"),

    # ---- accent as text --------------------------------------------------
    ("--accent", "--surface-0", TEXT, "links, .issue-rule, .empty-glyph"),
    ("--accent", "--paper", TEXT, "links on the page ground"),
    ("--accent", "--surface-1", TEXT, ".drop-sub-alt a inside the drop zone"),
    ("--accent", "--surface-2", TEXT, "accent text over a well"),
    ("--accent", "--accent-wash", TEXT, ".tab-badge"),
    ("--accent-hi", "--surface-0", TEXT, "a:hover"),

    # ---- status colour as text on its own wash and on plain surfaces ----
    ("--good", "--good-wash", TEXT, ".preflight-banner.ok, .ds-ready"),
    ("--good", "--surface-0", TEXT, ".fidelity-score, .pill.good"),
    ("--warn", "--warn-wash", TEXT, ".preflight-banner.warn, .mockup-fallback-note"),
    ("--warn", "--surface-0", TEXT, ".summary-row.warn-row b"),
    ("--bad", "--bad-wash", TEXT, ".preflight-banner.bad, .ds-blocker"),
    ("--bad", "--surface-0", TEXT, ".pill.bad, .status-bad"),

    # ---- neutral text sitting ON a tinted wash --------------------------
    ("--ink", "--accent-wash", TEXT, ".cta-text b, ::selection"),
    ("--ink", "--good-wash", TEXT, ".ds-name on a READY strip"),
    ("--ink", "--warn-wash", TEXT, ".ds-name on an AMBER strip"),
    ("--ink", "--bad-wash", TEXT, ".ds-name on a BLOCKER strip"),
    ("--ink-2", "--accent-wash", TEXT, ".cta-text"),
    ("--ink-2", "--good-wash", TEXT, ".ds-steps on a READY strip"),
    ("--ink-2", "--warn-wash", TEXT, ".ds-steps on an AMBER strip"),
    ("--ink-2", "--bad-wash", TEXT, ".ds-steps on a BLOCKER strip"),

    # ---- text on a FILLED control ---------------------------------------
    ("--on-solid", "--accent", TEXT, ".btn-primary, .cta-btn, .ds-btn"),
    ("--on-solid", "--accent-hi", TEXT, ".btn-primary:hover"),
    ("--on-solid", "--good", TEXT, ".toast.ok"),
    ("--on-solid", "--warn", TEXT, ".cta-fix-first .cta-btn"),
    ("--on-solid", "--warn-hi", TEXT, ".cta-fix-first .cta-btn:hover"),
    ("--on-solid", "--bad", TEXT, ".toast.err"),

    # ---- inverted chips --------------------------------------------------
    ("--surface-0", "--ink", TEXT, '.mockup-mode-btn[aria-checked="true"]'),
    ("--paper", "--ink", TEXT, ".toast"),

    # ---- the connector glyphs in the How-it-works dialog -----------------
    # 13.5px arrows between the pipeline steps. They are TEXT: real glyphs in
    # a real text node, so they owe 4.5:1 like any other word on the card.
    # They used to be painted with a token exempted as "decorative" and
    # measured 2.58:1.
    ("--ink-faint", "--surface-0", TEXT, ".howto-pipe-arr on the dialog card"),

    # ---- code wells (dark in both themes) --------------------------------
    ("--code-ink", "--code-bg", TEXT, ".code-block, .issue-excerpt, .audit-snippet"),
    # the syntax palette is decoration over text that is already legible in
    # --code-ink, but it IS text, so it is measured as text. (The offline
    # rail measures the same set from the other end.)
    ("--syn-comment", "--code-bg", TEXT, "highlight.js comments, CDATA"),
    ("--syn-punct", "--code-bg", TEXT, "highlight.js brackets, commas"),
    ("--syn-tag", "--code-bg", TEXT, "highlight.js element names"),
    ("--syn-attr", "--code-bg", TEXT, "highlight.js attribute names"),
    ("--syn-string", "--code-bg", TEXT, "highlight.js strings"),
    ("--syn-number", "--code-bg", TEXT, "highlight.js numbers"),
    ("--syn-keyword", "--code-bg", TEXT, "highlight.js SQL keywords"),
    ("--syn-operator", "--code-bg", TEXT, "highlight.js operators, binds"),

    # ---- the report preview sheet ---------------------------------------
    # The sheet is a CONTENT SANDBOX, not chrome. Both of its tokens are
    # pinned in both themes, so converter output that does not declare its
    # own colour still lands as dark ink on white paper instead of
    # inheriting the chrome ink and disappearing in dark mode.
    ("--doc-ink", "--doc-paper", TEXT, ".mockup-host .o2s-page"),

    # ---- the guided-tour tooltip, a spotlight card pinned dark in both
    #      themes (same idea as the code well) --------------------------
    ("--tour-ink", "--tour-bg", TEXT, ".demo-tooltip body copy"),
    ("--tour-title", "--tour-bg", TEXT, ".demo-tooltip h4"),
    ("--tour-muted", "--tour-bg", TEXT, ".demo-tooltip .demo-skip"),
    ("--tour-cta-ink", "--tour-cta", TEXT, ".demo-tooltip .demo-next label"),
    ("--tour-cta", "--tour-bg", UI, ".demo-tooltip .demo-next against the card"),

    # ---- 1.4.11 non-text: control edges and rings ------------------------
    ("--field-border", "--surface-0", UI, "input / .btn border inside a card"),
    ("--field-border", "--paper", UI, "control edge against the page ground"),
    ("--field-border", "--surface-1", UI, ".drop-zone dashed edge, .theme-toggle"),
    ("--field-border", "--surface-2", UI, "control edge over a well"),
    ("--focus-ring", "--surface-0", UI, "focus ring on a card"),
    ("--focus-ring", "--paper", UI, "focus ring on the page ground"),
    ("--focus-ring", "--surface-1", UI, "focus ring in the sidebar / theme toggle"),
    ("--focus-ring", "--surface-2", UI, "focus ring over a well"),
    # a focusable control sits on each of these too -- .cta-btn on the CTA
    # bar, .ds-btn on a deploy strip, and a listing that scrolls sideways is
    # itself a tab stop whose ring is drawn INSIDE the well.
    ("--focus-ring", "--accent-wash", UI, "focus ring on .mockup-cta-bar / the AI bar"),
    ("--focus-ring", "--good-wash", UI, "focus ring on a READY deploy strip"),
    ("--focus-ring", "--warn-wash", UI, "focus ring on .cta-fix-first"),
    ("--focus-ring", "--bad-wash", UI, "focus ring on a BLOCKER strip"),
    ("--code-focus", "--code-bg", UI,
     "focus ring of a scrollable listing, drawn inside the well"),
    # An inset ring follows the box's rounded corner, where the well's own
    # fill has already been cut away and the card behind shows through.
    # test_theme_contrast_live.py measures the ring against exactly that
    # backdrop, so the model says so too.
    ("--code-focus", "--surface-0", UI,
     "the same ring where a rounded corner exposes the card behind the well"),
    # the tour card pins its own ring, because the theme's ring is a
    # light-theme colour on a card that is dark in both themes.
    ("--tour-focus", "--tour-bg", UI, ".demo-tooltip :focus-visible"),
    ("--tour-ring", "--surface-0", UI, ".demo-highlight over a card"),
    ("--tour-ring", "--paper", UI, ".demo-highlight over the page ground"),
    ("--tour-ring", "--surface-1", UI, ".demo-highlight over the sidebar"),
    ("--tour-ring", "--surface-2", UI, ".demo-highlight over a well"),
]

# Deliberately unmeasured: never a foreground, never the sole carrier of any
# information. Listed so the omission is a decision, not an oversight -- and
# every claim here is CHECKED below against what the source paints, because
# the last version of this list carried --ink-deco, which was painting text.
DECORATIVE = {
    "--line": "hairline dividers between rows -- border only",
    "--line-strong": "stronger dividers, .mockup-mode-toggle seam -- border only",
    "--accent-line": "tint border on .mockup-cta-bar / .burst-callout",
    "--good-line": "tint border on .preflight-banner.ok",
    "--warn-line": "tint border on .preflight-banner.warn",
    "--bad-line": "tint border on .preflight-banner.bad",
    "--tour-line": "1px edge of the guided-tour tooltip card",
    "--code-border": "seam above a code well",
    "--code-gutter": "unused reserve for a future line-number gutter",
}

# Properties a token may be painted with and still be exempt. `color` and
# `outline` are absent on purpose: text and focus indicators are never
# decorative, whatever the token is called.
DECORATIVE_PROPS = frozenset({
    "border", "border-color", "border-top", "border-right", "border-bottom",
    "border-left", "border-top-color", "border-right-color",
    "border-bottom-color", "border-left-color", "border-image",
    "column-rule", "column-rule-color", "background", "background-color",
    "box-shadow", "background-image",
})


@pytest.fixture(scope="module")
def pal():
    return palettes()


@pytest.fixture(scope="module")
def painted():
    """Every colour the app paints, with the selector that paints it."""
    return paints()


def _canon_paints(painted, light):
    """Paints, with alias tokens folded onto the token that holds the colour
    and non-colour tokens (shadows, fonts) dropped."""
    out = []
    for p in painted:
        tok = canonical(light, p.token)
        if tok in light and is_colour(light, tok):
            out.append((p.source, p.selector, p.prop, tok))
    return out


def _surface_selectors(cp):
    """surface token -> the selectors that paint it."""
    out = {}
    for _src, sel, prop, tok in cp:
        if prop in SURFACE_PROPS and sel:
            for one in sel.split(","):
                out.setdefault(tok, set()).add(one.strip())
    return out


def _own_background(cp):
    """selector -> the background token that same selector paints."""
    out = {}
    for _src, sel, prop, tok in cp:
        if prop in SURFACE_PROPS and sel:
            out[sel] = tok
    return out


# --------------------------------------------------------------------------
# 1. the measurement itself
# --------------------------------------------------------------------------
@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("fg,bg,need,where", PAIRS,
                         ids=[f"{a}_on_{b}" for a, b, _, _ in PAIRS])
def test_pair_meets_wcag_aa(pal, theme, fg, bg, need, where):
    got = ratio(pal[theme], fg, bg)
    assert got >= need, (
        "%s: %s (%s) on %s (%s) = %.2f:1, needs %.1f:1 -- %s"
        % (theme, fg, resolve(pal[theme], fg), bg, resolve(pal[theme], bg),
           got, need, where))


# A NON-TEXT INDICATOR THAT ONLY JUST PASSES IS A DEFECT WAITING FOR A
# ROUNDING. Two of them were sitting on the 3:1 line and passing this file:
# the focus ring of a scrollable listing, drawn inside the code well, at
# 3.03:1, and the edge of a control over a well at 3.09:1. Both were
# nominally compliant and neither was easy to SEE -- which is the whole
# point of 1.4.11. So the floor for an indicator is the standard's 3:1 and
# the BAR is higher: an indicator this app draws has to clear the line by a
# visible margin, not by two hundredths.
UI_HEADROOM = 3.5


@pytest.mark.parametrize("theme", THEMES)
def test_every_non_text_indicator_clears_the_floor_with_headroom(pal, theme):
    """SC 1.4.11 is the floor, not the target."""
    thin = []
    for fg, bg, need, where in PAIRS:
        if need != UI:
            continue
        got = ratio(pal[theme], fg, bg)
        if got < UI_HEADROOM:
            thin.append("%s on %s = %.2f:1 (%s)" % (fg, bg, got, where))
    assert not thin, (
        "%s theme: these focus rings / control edges pass 3:1 with almost "
        "nothing to spare, which is how both of the last two got shipped: "
        "%s" % (theme, thin))


# --------------------------------------------------------------------------
# 2. the surfaces table cannot fall behind the source
# --------------------------------------------------------------------------
def test_every_painted_background_is_named_in_the_surfaces_table(pal, painted):
    """A background nobody listed is a surface nobody measured.

    --tour-bg is why this exists: a card declared in demo_mode.js, pinned
    dark in both themes, that a stylesheet-only reading of the app could not
    see at all.
    """
    light = pal["light"]
    scanned = {tok for _s, _sel, prop, tok in _canon_paints(painted, light)
               if prop in SURFACE_PROPS}
    missing = sorted(scanned - set(SURFACES))
    assert not missing, (
        "these tokens are painted as a background but no SURFACES entry says "
        "what sits on them: %s. Add each one with what it holds -- text, a "
        "focus ring, or (with the reason) nothing." % ", ".join(missing))


def test_no_surface_in_the_table_is_a_fiction(pal, painted):
    light = pal["light"]
    scanned = {tok for _s, _sel, prop, tok in _canon_paints(painted, light)
               if prop in SURFACE_PROPS}
    stale = sorted(set(SURFACES) - scanned)
    assert not stale, (
        "SURFACES lists tokens nothing paints as a background any more: %s. "
        "A stale entry keeps a pairing alive that no longer exists."
        % ", ".join(stale))


@pytest.mark.parametrize("token", sorted(SURFACES))
def test_every_surface_holds_something_or_says_why_not(token):
    entry = SURFACES[token]
    assert entry["holds"] <= {"text", "ring"}
    if not entry["holds"]:
        assert entry["why"], (
            "%s holds neither text nor a ring -- that claim needs its reason "
            "on the record" % token)


@pytest.mark.parametrize("token", sorted(SURFACES))
def test_a_surface_that_holds_text_is_measured_with_text_on_it(token):
    if "text" not in SURFACES[token]["holds"]:
        return
    measured = [p for p in PAIRS if p[1] == token and p[2] >= TEXT]
    assert measured, (
        "%s holds text but no pair measures any ink on it" % token)


# --------------------------------------------------------------------------
# 3. rings: the class of defect that hid on the tour card
# --------------------------------------------------------------------------
def _global_ring_tokens(cp):
    return {tok for _s, sel, prop, tok in cp
            if prop in RING_PROPS and sel and selector_is_global(sel)}


def test_the_one_global_ring_is_the_token_the_table_expects(pal, painted):
    """`button:focus-visible` and friends paint ONE ring token. If a second
    one appears, every surface below has to be re-measured against it."""
    cp = _canon_paints(painted, pal["light"])
    got = _global_ring_tokens(cp)
    assert got == {GLOBAL_RING}, (
        "selectors that match anywhere paint these ring tokens: %s -- the "
        "model expects exactly %s. Any ring drawn by a selector with no "
        "class or id lands on EVERY surface that holds a control."
        % (sorted(got), GLOBAL_RING))


@pytest.mark.parametrize("token", sorted(
    t for t, e in SURFACES.items() if "ring" in e["holds"]))
def test_every_surface_that_holds_a_ring_measures_the_ring_drawn_on_it(token):
    """The blind spot, closed.

    A ring painted by a global selector is drawn over whatever surface the
    control happens to sit on -- a card, a well, or a tooltip pinned dark in
    both themes. Either the global ring is measured against this surface, or
    the surface declares its own ring token (and proves it, below).
    """
    expected = SURFACES[token]["ring"] or GLOBAL_RING
    measured = [p for p in PAIRS if p[0] == expected and p[1] == token]
    assert measured, (
        "%s holds a focus ring drawn with %s and that pairing is not "
        "measured. WCAG 2.2 SC 1.4.11 needs 3:1 against the surface the "
        "indicator is actually drawn on." % (token, expected))
    assert measured[0][2] >= UI


def test_a_surface_with_a_control_inside_it_is_declared_to_hold_a_ring(
        pal, painted):
    """Derived, so "holds" cannot be quietly narrowed.

    If the source styles a button, a link, an input or a :focus state INSIDE
    a surface, then a focus indicator is drawn on that surface and it owes
    3:1 there. `.demo-tooltip button` is the line that says so for the
    guided-tour card -- it paints no colour at all, so a scan that only
    looked at colour declarations could not see it.
    """
    cp = _canon_paints(painted, pal["light"])
    surf = _surface_selectors(cp)
    missing = []
    for src, sel in selectors():
        for one in sel.split(","):
            one = one.strip()
            for stok, sels in surf.items():
                for anc in sels:
                    if not (one.startswith(anc + " ")
                            or one.startswith(anc + ">")):
                        continue
                    rest = one[len(anc):]
                    if not FOCUSABLE_IN_SELECTOR.search(rest):
                        continue
                    if "ring" not in SURFACES.get(stok, {}).get(
                            "holds", frozenset()):
                        missing.append("%s holds a control (%s: %s) but is "
                                       "not declared to hold a ring"
                                       % (stok, src, sel))
    assert not missing, "\n".join(sorted(set(missing)))


@pytest.mark.parametrize("token", sorted(
    t for t, e in SURFACES.items() if e["ring"]))
def test_a_surface_that_pins_its_own_ring_proves_the_override(pal, painted,
                                                              token):
    """Declaring an override is not enough: a rule inside the surface has to
    actually paint it, or the global ring is still what the browser draws."""
    entry = SURFACES[token]
    cp = _canon_paints(painted, pal["light"])
    own = _surface_selectors(cp).get(token, set())
    assert own, "%s is not painted by any selector" % token
    proof = []
    for src, sel, prop, tok in cp:
        if tok != entry["ring"] or prop not in RING_PROPS or not sel:
            continue
        for one in sel.split(","):
            one = one.strip()
            for anc in own:
                if one == anc or one.startswith(anc + " ") \
                        or one.startswith(anc + ">") \
                        or one.startswith(anc + ":"):
                    proof.append("%s: %s { %s }" % (src, sel, prop))
    assert proof, (
        "%s says its focus ring is %s, but nothing scoped to %s paints that "
        "token as an outline. Without the rule the browser still draws %s "
        "there -- which is the defect this override exists to fix."
        % (token, entry["ring"], sorted(own), GLOBAL_RING))
    assert entry["why"], "%s pins its own ring without saying why" % token


# --------------------------------------------------------------------------
# 4. text: no exemptions, and scope-derived pairings
# --------------------------------------------------------------------------
def test_every_token_painted_as_text_is_measured_as_text(pal, painted):
    """`color:` is text. There is no decorative exemption for text.

    --ink-deco sat on the DECORATIVE list while painting the 13.5px arrows
    in the How-it-works dialog at 2.58:1. Naming a token "deco" does not
    make its glyphs stop being read.
    """
    light = pal["light"]
    cp = _canon_paints(painted, light)
    painted_as_text = {tok for _s, _sel, prop, tok in cp if prop in TEXT_PROPS}
    measured = {fg for fg, _bg, need, _w in PAIRS if need >= TEXT}
    missing = sorted(painted_as_text - measured)
    assert not missing, (
        "these tokens are painted with a text property and never measured "
        "against any surface at %.1f:1 -- %s"
        % (TEXT, ", ".join(missing)))


def test_a_decorative_exemption_is_never_painted_as_text_or_a_ring(pal, painted):
    light = pal["light"]
    cp = _canon_paints(painted, light)
    offenders = []
    for src, sel, prop, tok in cp:
        if tok not in DECORATIVE:
            continue
        if prop in DECORATIVE_PROPS:
            continue
        offenders.append("%s: %s { %s: var(%s) }" % (src, sel, prop, tok))
    assert not offenders, (
        "a token on the DECORATIVE list is painted with a property that "
        "carries meaning -- text or a focus indicator. Measure it instead of "
        "exempting it:\n" + "\n".join(sorted(set(offenders))))


@pytest.mark.parametrize("token", sorted(DECORATIVE))
def test_a_decorative_exemption_names_its_reason(token):
    reason = DECORATIVE[token]
    assert len(reason.split()) >= 3, (
        "%s is exempt with no real reason: %r" % (token, reason))


def test_text_written_inside_a_surface_is_measured_against_that_surface(
        pal, painted):
    """Derived, not listed: if a rule sets a colour and its selector sits
    inside a selector that paints a background, that is a pairing the app
    really renders, and it has to be in PAIRS."""
    light = pal["light"]
    cp = _canon_paints(painted, light)
    surf = _surface_selectors(cp)
    own = _own_background(cp)
    required = set()
    for _src, sel, prop, tok in cp:
        if prop not in TEXT_PROPS or not sel:
            continue
        if sel in own:                      # the rule paints its own fill
            required.add((tok, own[sel]))
            continue
        for one in sel.split(","):
            one = one.strip()
            for stok, sels in surf.items():
                for anc in sels:
                    if one.startswith(anc + " ") or one.startswith(anc + ">"):
                        required.add((tok, stok))
    have = {(fg, bg) for fg, bg, _n, _w in PAIRS}
    missing = sorted(required - have)
    assert not missing, (
        "the stylesheet writes these inks on these surfaces and nothing "
        "measures the pair: %s"
        % ", ".join("%s on %s" % p for p in missing))


# --------------------------------------------------------------------------
# 5. the instrument, and the token layer it reads
# --------------------------------------------------------------------------
@pytest.mark.parametrize("theme", THEMES)
def test_every_decorative_token_is_declared(pal, theme):
    """A token we chose not to measure still has to exist -- an undefined
    var() silently inherits and the element loses its colour entirely."""
    for token in DECORATIVE:
        assert token in pal[theme], "%s: %s is not declared" % (theme, token)


@pytest.mark.parametrize("theme", THEMES)
def test_no_pair_is_measured_against_an_undefined_token(pal, theme):
    for fg, bg, _, _ in PAIRS:
        resolve(pal[theme], fg)
        resolve(pal[theme], bg)


@pytest.mark.parametrize("theme", THEMES)
def test_every_pair_is_between_two_real_colours(pal, theme):
    for fg, bg, _, _ in PAIRS:
        for tok in (fg, bg):
            assert is_colour(pal[theme], tok), (
                "%s: %s resolves to %r, which contrast cannot be measured on"
                % (theme, tok, resolve(pal[theme], tok)))


def test_a_surface_pinned_in_one_theme_is_pinned_in_both(pal):
    """The tour card and the code well are the same idea: a surface that
    keeps its colours whatever the theme. If one of them starts flipping,
    every foreground pinned against it has to be re-measured."""
    for token in ("--tour-bg", "--tour-cta", "--doc-paper"):
        light = resolve(pal["light"], token)
        for dark in ("dark_media", "dark_attr"):
            assert resolve(pal[dark], token) == light, (
                "%s is pinned in the light palette but %s overrides it to "
                "%s -- the foregrounds measured against it assume one value"
                % (token, dark, resolve(pal[dark], token)))


def test_the_two_themes_are_actually_different(pal):
    """Guards the copy-paste failure mode: a 'dark' block that never
    changed any value would pass every contrast assertion above."""
    for dark in ("dark_media", "dark_attr"):
        changed = [t for t in pal["light"]
                   if pal[dark].get(t) != pal["light"][t]]
        assert len(changed) >= 20, (
            "%s only overrides %d tokens -- that is not a designed theme"
            % (dark, len(changed)))
        # and it must actually be darker
        from theme_palette import luminance
        assert luminance(resolve(pal[dark], "--paper")) < \
            luminance(resolve(pal["light"], "--paper"))
        assert luminance(resolve(pal[dark], "--ink")) > \
            luminance(resolve(pal["light"], "--ink"))


def test_contrast_maths_matches_known_values():
    """Mutation insurance for the measuring instrument itself. If this
    helper silently returned 21.0 for everything, every assertion above
    would pass while the app was unreadable."""
    from theme_palette import contrast
    assert round(contrast("#ffffff", "#000000"), 2) == 21.0
    assert round(contrast("#ffffff", "#ffffff"), 2) == 1.0
    # published reference value for #767676 on white
    assert 4.5 <= contrast("#767676", "#ffffff") <= 4.6
    # the exact defect the audit reported, so the number is on the record
    assert round(contrast("#8a8178", "#ffffff"), 2) == 3.82
    # and the two this round was opened for: the light-theme ring on the
    # dark tour card, and the old arrow ink on a card
    assert round(contrast("#b4452f", "#1d2733"), 2) == 2.76
    assert round(contrast("#a7a099", "#ffffff"), 2) == 2.58


def test_the_paint_scan_sees_the_stylesheets_the_scripts_inject(painted):
    """The scan is the whole basis of the completeness tests above. If it
    ever stopped reading demo_mode.js, every claim about the tour card would
    quietly become vacuous."""
    sources = {p.source for p in painted}
    assert "style.css" in sources
    assert "demo_mode.js" in sources, (
        "the guided-tour stylesheet is not being scanned -- that is exactly "
        "the blind spot this rail was widened to close")
    tour = [p for p in painted
            if p.source == "demo_mode.js" and p.token == "--tour-bg"]
    assert tour and any(p.prop in SURFACE_PROPS for p in tour), (
        "demo_mode.js is scanned but its card background was not found")
