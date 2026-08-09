"""
Oracle Reports color token resolver.

Oracle Reports XML uses a mix of color formats in attributes like
fillBackgroundColor, fillForegroundColor, lineColor, edgeLineColor:

    - Named colors      : "white", "black", "red", "darkblue", ...
    - Grayscale shades  : "gray0".."gray100" (Oracle INK percent: 0=white,
                          8=light #EBEBEB, 100=black — truth-calibrated)
    - Hex passthroughs  : "#aabbcc" or bare "aabbcc"
    - RGB triplet form  : "r0g0b50" / "r100g0b0" (each channel 0-100, percent scale)
    - Specials          : "transparent", "no_fill"

`resolve_color(token)` returns a CSS-friendly value:
    - "#RRGGBB" for solid colors
    - "" (empty string) for transparent / no-fill / unknown / unparseable

The empty-string return for unknown values is intentional — downstream
mockup/RDL code can treat falsy as "no styling" without further checks.
"""
from __future__ import annotations

import re


# ---------------------------------------------------------------------------
# Named color table (case-insensitive). Values are #RRGGBB.
# ---------------------------------------------------------------------------

_NAMED_COLORS = {
    "white":     "#FFFFFF",
    "black":     "#000000",
    "red":       "#FF0000",
    "green":     "#008000",
    "blue":      "#0000FF",
    "yellow":    "#FFFF00",
    "cyan":      "#00FFFF",
    "magenta":   "#FF00FF",
    # Oracle's bare "gray" is 25% ink — the truth PDFs render it 0.75
    # gray (#BFBFBF), consistent with the grayN ink-percent family
    # (gray8 -> 0.92, gray16 -> 0.84). CSS's 50% "gray" painted the
    # activity-report section bands twice as dark as the Oracle output.
    "gray":      "#BFBFBF",
    "grey":      "#BFBFBF",
    # Oracle's "dark<primary>" family is the 50%-scale primary, NOT the
    # CSS name: truth-measured — a darkgreen band renders (0,128,0) where
    # CSS DarkGreen #006400 painted visibly darker, and darkblue titles
    # measure #000080 (navy) in the Oracle PDF output. Consistent with the
    # same exports' rXgYbZ percent scale (r0g0b50 -> (0,0,127)).
    "darkblue":  "#000080",
    "darkgreen": "#008000",
    "darkred":   "#800000",
    "darkgray":  "#A9A9A9",
    "darkgrey":  "#A9A9A9",
    "lightgray": "#D3D3D3",
    "lightgrey": "#D3D3D3",
    "silver":    "#C0C0C0",
    "navy":      "#000080",
    "maroon":    "#800000",
    "olive":     "#808000",
    "teal":      "#008080",
    "purple":    "#800080",
    "orange":    "#FFA500",
    "pink":      "#FFC0CB",
    "brown":     "#A52A2A",
}

_SPECIAL_EMPTY = {"transparent", "no_fill", "nofill", "none"}


# ---------------------------------------------------------------------------
# Stroke (rule / edge) ink defaults — truth-measured, see rule_color().
# ---------------------------------------------------------------------------

# A drawn rule that declares NO lineWidth is written to the export device as a
# ZERO-WIDTH stroke, and the device paints that at ~20% ink: the rule measures
# #CCCCCC in the exported PDFs regardless of rendering resolution.
#
# Measured on two truth exports, sampling the rule's pixel row at 72/150/300
# dpi (identical result on both, at every resolution):
#     summary export, footer rule  -> (204,204,204), or the pair
#                                     (221,221,221)+(238,238,238) = 51/255 ink
#                                     when the stroke straddles two pixel rows
#     history export, record rule  -> (204,204,204)
#     history export, footer rule  -> (204,204,204)
# The same exports stroke SOLID BLACK wherever a lineWidth IS declared
# (0.48pt / 0.96pt / 1pt / 2pt strokes), so the light ink is specifically the
# zero-width device hairline, not a global rule color.
#
# Renderers that only support a real stroke width cannot reproduce a
# zero-width stroke, so an undeclared-color hairline must carry this gray or
# it prints near-black (measured near-black at print resolution: a 0.25pt
# black rule reads (118,118,118) at 150dpi and a filled black bar (33,33,33)
# at 300dpi, where the truth holds at (204,204,204)).
#
# RENDER-CALIBRATED, not copied: this value is the ink whose RENDERED
# result deviates least from truth's over 150 and 300 dpi at the thinnest
# stroke SSRS will accept. The engine refuses any BorderWidth below
# 0.24985pt ("...must be between 0.24985pt and 20pt", raised by rendering a
# probe report of 0.1/0.05/0.01/0pt lines), and 0.25pt is 0.52 device px at
# 150 dpi against 1.04 at 300 -- so above the rasterizer's 0.2px coverage
# floor our ink necessarily DOUBLES with resolution while truth's
# device-space hairline holds at 0.200 ink-px at every dpi. No (colour,
# width) pair, and no thin filled Rectangle (resolution-independent but
# floored at 0.067 ink-px = grey 238), can match both. Full sweep and the
# per-report rasterized numbers: tests/test_hairline_ink_calibration.py.
DEVICE_HAIRLINE_COLOR = "#CCCCCC"

# Ink for a stroke that declares a WIDTH but no color: the same truth exports
# print those solid black.
DEFAULT_LINE_COLOR = "#000000"

# Ink for TEXT that declares no colour of its own.
#
# Oracle's device default for glyphs is pure black, and the truth exports
# confirm it: sampling every text span's fill colour across the truth PDFs,
# the reports that declare no <font textColor>/color anywhere print 100% of
# their glyphs at (0,0,0); the ones that DO declare a colour print exactly
# that colour and nothing else. There is no "softened" body ink in the
# dialect at all.
#
# Emitting a house tone instead (#111111 = 17/255 off black, #282828 = 40/255)
# renders visibly washed-out grey next to the truth, so no textbox / caption /
# label emitter may fall back to one: declared colour wins, otherwise BLACK.
DEFAULT_TEXT_COLOR = "#000000"


def text_color(declared) -> str:
    """Resolve the ink of a piece of text.

    ``declared`` is the object's own font colour token (or an already
    resolved ``#RRGGBB``); it always wins when it resolves. With nothing
    declared the ink is Oracle's device default for glyphs — solid black.

    Purely declaration-driven: no report-specific knowledge, and no house
    palette.
    """
    token = (declared or "").strip() if isinstance(declared, str) else ""
    if token:
        resolved = resolve_color(token)
        if resolved:
            return resolved
    return DEFAULT_TEXT_COLOR


def rule_color(declared, width_declared: bool = False) -> str:
    """Resolve the ink of a drawn rule / box edge.

    ``declared`` is the source's own stroke-color token (or an already
    resolved ``#RRGGBB``); it always wins when it resolves. With no declared
    color the ink comes from Oracle's device default, which depends on
    whether the source declared a stroke WIDTH:

        width declared    -> a real stroke, printed solid black
        width undeclared  -> the zero-width device hairline, printed at
                             ~20% ink (see DEVICE_HAIRLINE_COLOR)

    Purely declaration-driven: no report-specific knowledge.
    """
    token = (declared or "").strip() if isinstance(declared, str) else ""
    if token:
        # Already-resolved hex passes through resolve_color unchanged.
        resolved = resolve_color(token)
        if resolved:
            return resolved
    return DEFAULT_LINE_COLOR if width_declared else DEVICE_HAIRLINE_COLOR

# gray0 .. gray100 — Oracle grayscale percent (0 = black, 100 = white).
_GRAY_RE = re.compile(r"^gr[ae]y(\d{1,3})$")

# r{R}g{G}b{B} triplet where each value is 0-100 (Oracle percent scale).
_RGB_RE = re.compile(r"^r(\d{1,3})g(\d{1,3})b(\d{1,3})$")

# Bare hex (6 hex chars). 3-char form is also accepted (e.g. "abc" -> #aabbcc).
_HEX6_RE = re.compile(r"^#?([0-9a-f]{6})$")
_HEX3_RE = re.compile(r"^#?([0-9a-f]{3})$")


def _clamp(val: int, lo: int = 0, hi: int = 255) -> int:
    if val < lo:
        return lo
    if val > hi:
        return hi
    return val


def _pct_to_byte(pct: int) -> int:
    """Convert Oracle percent (0-100) to 0-255.

    Uses floor truncation so that r0g0b50 maps to rgb(0, 0, 127) — matching
    the documented Oracle Reports behavior in the converter spec.
    """
    pct = _clamp(pct, 0, 100)
    return int((pct * 255) // 100)


def resolve_color(token):
    """Resolve an Oracle color token to a CSS-friendly string.

    Returns "#RRGGBB" for solid colors, "" for transparent / no-fill /
    unknown / empty input.
    """
    if token is None:
        return ""
    if not isinstance(token, str):
        return ""
    t = token.strip().lower()
    if not t:
        return ""

    if t in _SPECIAL_EMPTY:
        return ""

    # Named?
    if t in _NAMED_COLORS:
        return _NAMED_COLORS[t]

    # Grayscale grayNN — Oracle's scale is INK percent (gray8 = 8% black =
    # a LIGHT #EBEBEB band, gray16 slightly darker). Truth-calibrated against
    # the real Oracle PDF output: the gray8 master band and gray16 column
    # strip of a banded receipts log are light grays; the previous
    # brightness-percent reading painted them near-BLACK.
    m = _GRAY_RE.match(t)
    if m:
        pct = _clamp(int(m.group(1)), 0, 100)
        byte = _pct_to_byte(100 - pct)
        return "#{0:02X}{0:02X}{0:02X}".format(byte)

    # RGB triplet r{R}g{G}b{B} with each channel 0-100
    m = _RGB_RE.match(t)
    if m:
        r = _pct_to_byte(int(m.group(1)))
        g = _pct_to_byte(int(m.group(2)))
        b = _pct_to_byte(int(m.group(3)))
        return "#{:02X}{:02X}{:02X}".format(r, g, b)

    # Hex passthrough (6-char)
    m = _HEX6_RE.match(t)
    if m:
        return "#" + m.group(1).upper()

    # 3-char hex shorthand
    m = _HEX3_RE.match(t)
    if m:
        s = m.group(1)
        return "#{0}{0}{1}{1}{2}{2}".format(s[0], s[1], s[2]).upper()

    # Unknown / unparseable
    return ""
