"""
Per-report palette resolution from <visualSettings>.

The generator must read each report's own visualSettings (parser
populates LayoutGroup.background_color / foreground_color / etc.) and
synthesise a palette dict instead of using hardcoded global constants.

Tests are name-agnostic. They check the heuristic itself.
"""
from __future__ import annotations

import glob
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402
from converter.generators.rdl import _resolve_palette  # noqa: E402
from converter.models import LayoutField, LayoutGroup, ParsedReport  # noqa: E402
from converter.parsers.oracle_xml import parse_oracle_xml  # noqa: E402
from converter.preview.html_mockup import _normalize_color  # noqa: E402

UPLOAD_DIR = Path("/sessions/peaceful-optimistic-fermi/mnt/uploads")
REQUIRED_KEYS = {
    "band_bg", "band_fg", "subhdr_bg", "subhdr_fg",
    "card_bg", "ink", "ink_soft", "rule",
}
DEFAULTS = {
    "band_bg":   "#03047e",
    "band_fg":   "#fffe31",
    "subhdr_bg": "#d6d6d6",
    "subhdr_fg": "#03047e",
    "card_bg":   "#ffffff",
    "ink":       "#282828",
    "ink_soft":  "#282828",
    "rule":      "#777777",
}


def _hex_lum(c: str) -> float:
    c = c.lstrip("#")
    r, g, b = int(c[0:2], 16) / 255.0, int(c[2:4], 16) / 255.0, int(c[4:6], 16) / 255.0
    return 0.299 * r + 0.587 * g + 0.114 * b


# ---------------------------------------------------------------------------
# 1) Schema: every uploaded XML resolves to a complete palette dict.
# ---------------------------------------------------------------------------
def _corpus_xml():
    """$O2S_CORPUS_DIR if set, else the bundled samples + fixtures, so this
    suite runs for anyone cloning the repo (not just the author's sandbox)."""
    dirs = [os.environ.get("O2S_CORPUS_DIR"),
            str(ROOT / "samples" / "oracle"),
            str(ROOT / "tests" / "fixtures" / "source_of_truth")]
    out, seen = [], set()
    for d in dirs:
        if not d or not Path(d).is_dir():
            continue
        for p in sorted(Path(d).rglob("*.xml")):
            if p.name in seen:
                continue
            seen.add(p.name)
            out.append(str(p))
    return out


@pytest.mark.skipif(not _corpus_xml(), reason="no corpus XML found")
@pytest.mark.parametrize("xml_path", _corpus_xml())
def test_resolve_palette_returns_full_dict_for_uploads(xml_path):
    with open(xml_path, "rb") as fh:
        report = parse_oracle_xml(fh.read())
    pal = _resolve_palette(report)
    assert isinstance(pal, dict)
    assert set(pal.keys()) >= REQUIRED_KEYS, (
        f"missing keys for {os.path.basename(xml_path)}: "
        f"{REQUIRED_KEYS - set(pal.keys())}"
    )
    # All values must be CSS-friendly hex (#RRGGBB).
    for k in REQUIRED_KEYS:
        v = pal[k]
        assert isinstance(v, str) and v.startswith("#") and len(v) == 7, (
            f"bad color for {k} in {os.path.basename(xml_path)}: {v!r}"
        )


# ---------------------------------------------------------------------------
# 2) When the source XML has a non-default bg on a repeating frame,
#    band_bg reflects it (not the hardcoded default).
# ---------------------------------------------------------------------------
def test_band_bg_picks_up_repeating_frame_background():
    rep = ParsedReport(name="X")
    rep.layout = [
        LayoutGroup(
            name="M_main",
            kind="frame",
            children=[
                LayoutGroup(
                    name="R_rows",
                    kind="repeating_frame",
                    background_color="#123456",
                ),
            ],
        ),
    ]
    pal = _resolve_palette(rep)
    assert pal["band_bg"].upper() == "#123456"
    assert pal["band_bg"] != DEFAULTS["band_bg"]


# ---------------------------------------------------------------------------
# 3) When the source XML has NO color signals, band_bg falls back to
#    the historical navy default.
# ---------------------------------------------------------------------------
def test_no_color_signals_falls_back_to_defaults():
    rep = ParsedReport(name="X")
    rep.layout = [
        LayoutGroup(name="M_main", kind="frame"),
        LayoutGroup(name="R_rows", kind="repeating_frame"),
    ]
    pal = _resolve_palette(rep)
    assert pal["band_bg"] == DEFAULTS["band_bg"]
    assert pal["band_fg"] == DEFAULTS["band_fg"]
    assert pal["subhdr_bg"] == DEFAULTS["subhdr_bg"]
    assert pal["subhdr_fg"] == DEFAULTS["subhdr_fg"]
    assert pal["card_bg"] == DEFAULTS["card_bg"]
    assert pal["ink"] == DEFAULTS["ink"]


# ---------------------------------------------------------------------------
# 4) Dark-band heuristic.
# ---------------------------------------------------------------------------
def test_band_fg_is_black_on_light_band():
    rep = ParsedReport(name="X")
    rep.layout = [
        LayoutGroup(
            name="R_rows",
            kind="repeating_frame",
            background_color="#FFEEAA",  # light cream, lum > 0.5
        ),
    ]
    pal = _resolve_palette(rep)
    assert pal["band_fg"] == "#000000"
    assert _hex_lum(pal["band_bg"]) >= 0.5


def test_band_fg_is_light_on_dark_band():
    rep = ParsedReport(name="X")
    rep.layout = [
        LayoutGroup(
            name="R_rows",
            kind="repeating_frame",
            background_color="#100070",  # dark navy, lum < 0.5
        ),
    ]
    pal = _resolve_palette(rep)
    # Either a light fg from the XML (none here) or the default yellow.
    assert _hex_lum(pal["band_fg"]) >= 0.5
    assert _hex_lum(pal["band_bg"]) < 0.5


# ---------------------------------------------------------------------------
# 5) Preview pipeline behavioral contract (replaces a former source-file SHA
#    pin, which asserted nothing behavioral and churned on every legit edit).
#    These assert what the preview must actually DO.
# ---------------------------------------------------------------------------
def test_normalize_color_rejects_xss_payloads():
    """_normalize_color feeds inline style="..." attributes, so a '#'-prefixed
    value must be a well-formed hex literal or fall back -- never a verbatim
    string that could break out of the attribute and inject markup (XSS)."""
    assert _normalize_color("#abc", "#000000") == "#abc"
    assert _normalize_color("#A1B2C3", "#000000") == "#A1B2C3"
    for hostile in (
        '#000"></div><img src=x onerror=alert(1)>',
        "#abc;</style><script>alert(1)</script>",
        "#zzzzzz", "#", "#12", "#1234567",
    ):
        out = _normalize_color(hostile, "#000000")
        assert out == "#000000", f"unsafe color echoed verbatim: {out!r}"
        assert "<" not in out and '"' not in out and ">" not in out


def test_preview_renders_structural_html_for_real_report():
    """A real report must convert to non-empty, page-structured preview HTML
    (the substantive contract the old file-hash pin failed to express)."""
    fixture = (ROOT / "tests" / "fixtures" / "source_of_truth"
               / "master_detail" / "source.xml")
    if not fixture.exists():
        pytest.skip("master_detail fixture not present")
    res = convert(fixture.read_bytes())
    html = res.get("mockup_html") or ""
    assert len(html) > 500, "preview HTML is empty/too small"
    assert "Page" in html, "preview is missing page structure"
    assert res.get("conversion_error") in (None, ""), "fixture should convert cleanly"


# ---------------------------------------------------------------------------
# 6) Most-common-bg fallback when no repeating frame has a bg.
# ---------------------------------------------------------------------------
def test_band_bg_falls_back_to_most_common_when_no_repeating_frame_bg():
    rep = ParsedReport(name="X")
    rep.layout = [
        LayoutGroup(name="M_a", kind="frame", background_color="#AAAAAA"),
        LayoutGroup(name="M_b", kind="frame", background_color="#AAAAAA"),
        LayoutGroup(name="M_c", kind="frame", background_color="#BBBBBB"),
        # no repeating_frame at all
    ]
    pal = _resolve_palette(rep)
    assert pal["band_bg"].upper() == "#AAAAAA"
