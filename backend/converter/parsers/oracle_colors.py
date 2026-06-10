"""
Oracle Reports color token resolver.

Oracle Reports XML uses a mix of color formats in attributes like
fillBackgroundColor, fillForegroundColor, lineColor, edgeLineColor:

    - Named colors      : "white", "black", "red", "darkblue", ...
    - Grayscale shades  : "gray0".."gray100" (0=black, 100=white, percent scale)
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
    "gray":      "#808080",
    "grey":      "#808080",
    "darkblue":  "#00008B",
    "darkgreen": "#006400",
    "darkred":   "#8B0000",
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

    # Grayscale grayNN (must come before named, but named "gray" already handled)
    m = _GRAY_RE.match(t)
    if m:
        pct = _clamp(int(m.group(1)), 0, 100)
        byte = _pct_to_byte(pct)
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
