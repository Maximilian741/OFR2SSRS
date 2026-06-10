"""Tests for converter.parsers.oracle_colors.resolve_color."""
from __future__ import annotations

import pytest

from converter.parsers.oracle_colors import resolve_color


# ---------------------------------------------------------------------------
# Named colors
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("token,expected", [
    ("white",     "#FFFFFF"),
    ("black",     "#000000"),
    ("red",       "#FF0000"),
    ("green",     "#008000"),
    ("blue",      "#0000FF"),
    ("yellow",    "#FFFF00"),
    ("cyan",      "#00FFFF"),
    ("magenta",   "#FF00FF"),
    ("gray",      "#808080"),
    ("grey",      "#808080"),
    ("darkblue",  "#00008B"),
    ("darkgreen", "#006400"),
    ("darkred",   "#8B0000"),
    ("darkgray",  "#A9A9A9"),
    ("lightgray", "#D3D3D3"),
    ("silver",    "#C0C0C0"),
    ("navy",      "#000080"),
    ("maroon",    "#800000"),
    ("olive",     "#808000"),
    ("teal",      "#008080"),
    ("purple",    "#800080"),
    ("orange",    "#FFA500"),
    ("pink",      "#FFC0CB"),
    ("brown",     "#A52A2A"),
])
def test_named_colors(token, expected):
    assert resolve_color(token) == expected


def test_named_colors_are_case_insensitive():
    assert resolve_color("RED") == "#FF0000"
    assert resolve_color("DarkBlue") == "#00008B"
    assert resolve_color("  Gray  ") == "#808080"


# ---------------------------------------------------------------------------
# Grayscale shades
# ---------------------------------------------------------------------------

def test_gray0_is_black():
    assert resolve_color("gray0") == "#000000"


def test_gray100_is_white():
    assert resolve_color("gray100") == "#FFFFFF"


def test_gray4_matches_spec_example():
    # spec: gray4 == #0a0a0a
    assert resolve_color("gray4") == "#0A0A0A"


def test_gray16_is_dark_gray():
    # 16% of 255 = 40.8, floor -> 40 -> 0x28
    assert resolve_color("gray16") == "#282828"


def test_gray50_is_mid_gray():
    # 50% of 255 = 127.5, floor -> 127 -> 0x7F
    assert resolve_color("gray50") == "#7F7F7F"


def test_grey_spelling_also_works():
    assert resolve_color("grey50") == "#7F7F7F"


# ---------------------------------------------------------------------------
# RGB triplet r{R}g{G}b{B} (0-100 scale per channel)
# ---------------------------------------------------------------------------

def test_rgb_triplet_spec_example_r0g0b50():
    # spec: r0g0b50 == rgb(0, 0, 127)
    assert resolve_color("r0g0b50") == "#00007F"


def test_rgb_triplet_spec_example_r100g0b0():
    # spec: r100g0b0 == rgb(255, 0, 0)
    assert resolve_color("r100g0b0") == "#FF0000"


def test_rgb_triplet_all_zero():
    assert resolve_color("r0g0b0") == "#000000"


def test_rgb_triplet_all_full():
    assert resolve_color("r100g100b100") == "#FFFFFF"


def test_rgb_triplet_case_insensitive():
    assert resolve_color("R100G0B0") == "#FF0000"


# ---------------------------------------------------------------------------
# Hex passthrough
# ---------------------------------------------------------------------------

def test_hex_with_hash():
    assert resolve_color("#aabbcc") == "#AABBCC"


def test_hex_without_hash():
    assert resolve_color("aabbcc") == "#AABBCC"


def test_hex_uppercase_input():
    assert resolve_color("#FFEEDD") == "#FFEEDD"


def test_hex_short_form():
    assert resolve_color("#abc") == "#AABBCC"


# ---------------------------------------------------------------------------
# Specials
# ---------------------------------------------------------------------------

def test_transparent_returns_empty():
    assert resolve_color("transparent") == ""


def test_no_fill_returns_empty():
    assert resolve_color("no_fill") == ""


def test_none_returns_empty():
    assert resolve_color("none") == ""


# ---------------------------------------------------------------------------
# Unknown / unparseable / edge cases
# ---------------------------------------------------------------------------

def test_empty_string_returns_empty():
    assert resolve_color("") == ""


def test_whitespace_only_returns_empty():
    assert resolve_color("   ") == ""


def test_none_input_returns_empty():
    assert resolve_color(None) == ""


def test_unknown_name_returns_empty():
    assert resolve_color("chartreuse") == ""


def test_non_string_returns_empty():
    assert resolve_color(123) == ""
    assert resolve_color(["red"]) == ""
