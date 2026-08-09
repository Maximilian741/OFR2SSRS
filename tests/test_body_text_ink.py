"""BODY TEXT INK: the declared colour, or BLACK -- never a house palette.

Oracle's device default for glyphs is pure black. Measured over the truth
exports: a report that declares no font colour prints 100% of its glyph
spans at (0,0,0), and a report that DOES declare one prints exactly that
colour. There is no softened body ink anywhere in the dialect.

The converter used to default undeclared text to house tones (#111111,
#282828, #1a1a1a, #444444). Those are 17-68/255 off black and render as
visibly washed-out grey next to the truth -- four truth-paired reports
measured #111111/#282828 for the bulk of their glyphs where the truth
printed #000000.

This module locks the rule in three layers:

  1. the resolver (``text_color``): nothing declared -> black; declared ->
     that colour, resolved through the Oracle token dialect,
  2. the palette (``_resolve_palette``): both ink slots are pure black,
     themed report or plain one,
  3. the EMITTED RDL, corpus-wide: no ``<Color>`` on any text run may be a
     near-black grey that the SOURCE does not declare. That is the shape of
     the original defect (a washed-out house ink), and it fires on every
     textbox / caption / label emitter at once rather than on the one that
     happened to be found first.

Name-agnostic: layer 3 walks whatever XML is on disk (samples + fixtures,
plus $O2S_CORPUS_DIR when set) and reads the declared colours out of the
source itself. No report, field, or label name appears here.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402
from converter.generators.rdl import _resolve_palette  # noqa: E402
from converter.models import LayoutGroup, ParsedReport  # noqa: E402
from converter.parsers.oracle_colors import (  # noqa: E402
    DEFAULT_TEXT_COLOR, resolve_color, text_color,
)


# ---------------------------------------------------------------------------
# 1) The resolver
# ---------------------------------------------------------------------------
def test_default_text_color_is_pure_black():
    assert DEFAULT_TEXT_COLOR.upper() == "#000000"


@pytest.mark.parametrize("token", ["", None, "   ", "transparent", "no_fill"])
def test_undeclared_text_resolves_to_black(token):
    """Nothing declared -> Oracle's device default for glyphs: solid black."""
    assert text_color(token).upper() == "#000000"


@pytest.mark.parametrize("token,expected", [
    ("black", "#000000"),
    ("red", "#FF0000"),
    ("darkblue", "#000080"),
    ("#123456", "#123456"),
    ("123456", "#123456"),
    ("r100g0b0", "#FF0000"),
    ("gray100", "#000000"),
])
def test_declared_text_color_wins(token, expected):
    """A declared colour is emitted as declared -- black never overrides it."""
    assert text_color(token).upper() == expected.upper()


# ---------------------------------------------------------------------------
# 2) The palette
# ---------------------------------------------------------------------------
def _plain_report():
    rep = ParsedReport(name="X")
    rep.layout = [LayoutGroup(name="M", kind="frame"),
                  LayoutGroup(name="R", kind="repeating_frame")]
    return rep


def _themed_report():
    rep = ParsedReport(name="X")
    rep.layout = [LayoutGroup(name="R", kind="repeating_frame",
                              background_color="#123456")]
    return rep


@pytest.mark.parametrize("rep_factory", [_plain_report, _themed_report])
def test_palette_body_ink_is_black(rep_factory):
    """Both ink slots are pure black whether or not the report carries a
    band theme -- a themed header never licenses a washed-out body."""
    pal = _resolve_palette(rep_factory())
    for slot in ("ink", "ink_soft"):
        assert pal[slot].upper() == "#000000", (
            f"palette['{slot}'] = {pal[slot]!r}; body text with no declared "
            "colour must be pure black, not a house ink"
        )


# ---------------------------------------------------------------------------
# 3) The emitted RDL, corpus-wide
# ---------------------------------------------------------------------------
def _corpus_xml():
    """Every Oracle XML on disk: the bundled samples + fixtures (so this runs
    for anyone cloning the repo) plus $O2S_CORPUS_DIR when it is set."""
    dirs = [ROOT / "samples" / "oracle",
            ROOT / "tests" / "fixtures" / "source_of_truth"]
    extra = os.environ.get("O2S_CORPUS_DIR")
    if extra:
        dirs.append(Path(extra))
    out = []
    for d in dirs:
        if d and Path(d).is_dir():
            out.extend(sorted(Path(d).rglob("*.xml")))
    return out


# Every attribute Oracle can carry a colour in. Read straight off the raw
# source so the "declared" set is the SOURCE's, not the converter's opinion.
_COLOR_ATTR_RE = re.compile(
    r'(?:textColor|fillForegroundColor|fillBackgroundColor|lineColor'
    r'|lineForegroundColor|edgeLineColor|color|foreground)\s*=\s*"([^"]*)"',
    re.IGNORECASE)

_TEXTRUN_COLOR_RE = re.compile(
    r"<TextRun>.*?<Style>.*?<Color>([^<]+)</Color>.*?</Style>.*?</TextRun>",
    re.DOTALL)


def _declared_colors(raw: bytes) -> set:
    txt = raw.decode("utf-8", "replace")
    out = set()
    for tok in _COLOR_ATTR_RE.findall(txt):
        out.add((tok or "").strip().upper())
        hexed = resolve_color(tok)
        if hexed:
            out.add(hexed.upper())
    return out


def _is_near_black_grey(hexc: str) -> bool:
    """A dark neutral that is NOT black -- the washed-out-ink signature.

    Every channel within 0x60 of black, all three within 8 of each other
    (a grey, not a dark colour), and at least one channel non-zero.
    """
    m = re.fullmatch(r"#([0-9a-fA-F]{6})", (hexc or "").strip())
    if not m:
        return False
    v = m.group(1)
    r, g, b = int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)
    if r == g == b == 0:
        return False
    return max(r, g, b) <= 0x60 and (max(r, g, b) - min(r, g, b)) <= 8


@pytest.mark.parametrize(
    "src", _corpus_xml(), ids=lambda p: p.parent.name + "/" + p.name)
def test_no_undeclared_near_black_body_ink(src):
    """No text run may print in a near-black house grey.

    Undeclared body text is BLACK; declared body text is the declared
    colour. Anything in between (#111111, #1a1a1a, #282828, #444444 ...)
    that the source never declares is an invented house ink and reads as
    washed-out grey against the truth.
    """
    raw = src.read_bytes()
    try:
        rdl = convert(raw, src.name)["rdl_xml"]
    except Exception as exc:  # noqa: BLE001 -- a decline is not this test's job
        pytest.skip(f"source does not convert: {exc}")
    if not (rdl or "").strip():
        pytest.skip("source produced no RDL (honest decline)")
    declared = _declared_colors(raw)
    offenders = sorted({
        c.strip() for c in _TEXTRUN_COLOR_RE.findall(rdl)
        if _is_near_black_grey(c) and c.strip().upper() not in declared
    })
    assert not offenders, (
        f"{src.name}: text runs emit undeclared near-black house ink "
        f"{offenders} -- undeclared body text must be pure #000000"
    )
