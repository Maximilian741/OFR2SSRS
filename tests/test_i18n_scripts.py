# -*- coding: utf-8 -*-
"""Non-Latin-script fixtures: the converter must carry Greek, Cyrillic,
Arabic (RTL) and CJK content 1:1 from source to RDL.

The public harvest found ZERO non-Latin content anywhere, so the gap is
closed with SYNTHESIZED fixtures under tests/fixtures/i18n/ — real dialect
documents (data model + declared layout + margin chrome + format masks)
whose every human-readable string is invented native-script wording, one
per script and encoding:

    I18N_GREEK.xml     UTF-8
    I18N_CYRILLIC.xml  WINDOWS-1251   (actual cp1251 bytes)
    I18N_ARABIC.xml    WINDOWS-1256   (actual cp1256 bytes, RTL)
    I18N_CJK.xml       UTF-8          (Japanese: ideographs + kana)

The fixtures also ride the three fatal gates (no-prompt / upload /
generality) via the shared CORPORA roster, so every leg runs over them
forever. This file holds the script-specific contracts the gates don't
know about:

  1. LEGACY CODE PAGES — a correctly-declared WINDOWS-1251/1256 source
     must decode by its declaration on EVERY surface, including the
     raw_xml side-panel string (the old fallback chain tried
     utf-8→cp1252→latin-1 and mojibaked Cyrillic/Arabic bytes into
     Latin accents while the lxml parse read them correctly).
  2. LABEL ROUND-TRIP — native-script labels, boilerplate, and parameter
     defaults land in the RDL byte-exact (no mojibake, no escapes).
  3. FORMAT MASKS — fmMonth/RRRR masks and masks with embedded native
     literals (YYYY"年"MM"月"DD"日") translate to .NET format strings
     with the native literals preserved.
  4. CJK GLYPH METRICS — East-Asian Wide/Fullwidth code points advance a
     FULL em (~1000/1000 units, ~2x the 500-unit average the Latin
     estimate assumed), and combining marks advance ~0; every
     widen/exact-fit/wrap net shares this estimator.
  5. FONT COVERAGE — a synthesized textbox that carries wide-glyph label
     text must carry a FontFamily: the engine's default face (Microsoft
     Sans Serif in the ReportViewer PDF renderer) has NO East-Asian
     glyphs, and the band labels of a Japanese source render as notdef
     boxes (tofu) — render-measured on the real engine.

ENGINE MEASUREMENTS (ReportViewer PDF, render-verified 2026-08-28, PNGs
eyeballed + span/notdef counts):

  * Greek / Cyrillic / Arabic render correctly in the declared face (Arial
    subsets embedded); CJK renders correctly ONCE a FontFamily is present
    (contract 5) — notdef count 0 across all four PDFs afterwards.
  * RTL: the engine (GDI/Uniscribe) applies full contextual SHAPING and
    bidi run reordering to Arabic text — joined forms, correct RTL glyph
    order, mixed Arabic+Latin lines lay out as standard bidi with LTR base
    direction. The Oracle dialect declares no paragraph direction, so no
    Style/Direction is fabricated; textbox anchoring stays declared-x.
  * ENGINE LIMITATION (not a converter defect): the PDF writer embeds
    non-Latin font subsets WITHOUT a usable ToUnicode CMap, so pypdf/fitz
    text extraction yields glyph IDs, not code points — the native script
    does NOT round-trip through PDF TEXT EXTRACTION for ANY non-Latin
    run, even when the page prints perfectly. Glyph presence is therefore
    verified by render (notdef count = 0, embedded font names, page
    raster) rather than by extracted-string equality.

All wording in the fixtures is invented for this repo; no customer text.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402
from converter.generators.rdl import _afm_text_width  # noqa: E402
from converter.parsers.oracle_xml import parse_oracle_xml  # noqa: E402
from converter.validators.no_prompt_gate import audit_no_prompt  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "i18n"

# Per-fixture probes — the exact native strings the fixture declares.
# Byte-exact membership in the RDL is the round-trip contract.
# ``display_default`` is the initialValue of P_TITLE_NOTE, a NON-bind
# parameter printed by a margin field (the Oracle display-constant idiom):
# the converter honors THAT default verbatim, while query-bind parameters
# deliberately keep =Nothing (the load-bearing param-prompt bypass).
CASES = {
    "I18N_GREEK.xml": {
        "encoding": "utf-8",
        "title": "Μηνιαία Αναφορά Παρατηρήσεων",
        "labels": ["Σταθμός:", "Περιφέρεια:", "Ημερομηνία:", "Σελίδα"],
        "prompt": "Περιφέρεια",
        "display_default": "Τμήμα Περιβαλλοντικής Παρακολούθησης",
        "formats": ["MMMM d, yyyy"],
    },
    "I18N_CYRILLIC.xml": {
        "encoding": "cp1251",
        "title": "Ежемесячный отчёт наблюдений",
        "labels": ["Станция:", "Область:", "Дата:", "Страница"],
        "prompt": "Область",
        "display_default": "Отдел экологического мониторинга",
        "formats": ["MMMM d, yyyy"],
    },
    "I18N_ARABIC.xml": {
        "encoding": "cp1256",
        "title": "تقرير الرصد الشهري",
        "labels": ["المحطة:", "المنطقة:", "التاريخ:", "صفحة"],
        "prompt": "المنطقة",
        "display_default": "قسم الرصد البيئي",
        "formats": ["dd/MM/yyyy"],
    },
    "I18N_CJK.xml": {
        "encoding": "utf-8",
        "title": "月次観測報告書",
        "labels": ["観測所:", "地域:", "日付:", "ページ"],
        "prompt": "地域",
        "display_default": "環境監視管理課",
        "formats": ['yyyy"年"MM"月"dd"日"'],
    },
}

# cp1252-reading-of-UTF-8 signature pairs; any of these in a converted
# surface means a decode step guessed the wrong code page.
_MOJIBAKE = ("Ã", "Â", "Ð±", "Ñ")


def _fixture_bytes(name: str) -> bytes:
    p = FIXTURES / name
    assert p.is_file(), f"missing fixture {p}"
    return p.read_bytes()


@pytest.fixture(scope="module")
def converted():
    return {name: convert(_fixture_bytes(name), target_db="oracle")
            for name in CASES}


# ---------------------------------------------------------------------------
# 1. Legacy code pages decode by their declaration on EVERY surface
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(CASES))
def test_raw_xml_decodes_by_declaration(name):
    """raw_xml (the side-panel string, and the surface the trigger/label
    reconstruction scans read) must carry the native title — the legacy
    fallback chain turned cp1251/cp1256 bytes into Latin mojibake here
    while the byte-level parse read them correctly."""
    case = CASES[name]
    rep = parse_oracle_xml(_fixture_bytes(name))
    assert case["title"] in rep.raw_xml
    for sig in _MOJIBAKE:
        assert sig not in rep.raw_xml


# ---------------------------------------------------------------------------
# 2. Native labels / boilerplate / parameter defaults land in the RDL
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(CASES))
def test_native_labels_round_trip_into_rdl(name, converted):
    case = CASES[name]
    rdl = converted[name]["rdl_xml"]
    assert case["title"] in rdl, "native title lost"
    for lb in case["labels"]:
        assert lb in rdl, f"native label {lb!r} lost"
    for sig in _MOJIBAKE:
        assert sig not in rdl
    # The display-constant parameter's native initialValue must survive
    # verbatim into its DefaultValue (the Oracle title-subtitle idiom).
    m = re.search(r'<ReportParameter Name="P_TITLE_NOTE".*?</ReportParameter>',
                  rdl, re.S)
    assert m and case["display_default"] in m.group(0), \
        "native display-constant default lost"
    # A QUERY-BIND parameter keeps the =Nothing typed-NULL default (the
    # load-bearing prompt bypass) — but its native PROMPT must survive.
    m = re.search(r'<ReportParameter Name="P_REGION".*?</ReportParameter>',
                  rdl, re.S)
    assert m and "=Nothing" in m.group(0), "bind default policy changed"
    assert case["prompt"] in m.group(0), "native parameter prompt lost"


@pytest.mark.parametrize("name", sorted(CASES))
def test_i18n_fixture_passes_no_prompt_gate(name, converted):
    rdl = converted[name]["rdl_xml"]
    assert audit_no_prompt(rdl) == []


# ---------------------------------------------------------------------------
# 3. Format masks translate with native literals preserved
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(CASES))
def test_format_masks_translate(name, converted):
    rdl = converted[name]["rdl_xml"]
    for fmt in CASES[name]["formats"]:
        assert f"<Format>{fmt}</Format>" in rdl, \
            f"mask translation lost {fmt!r}"


def test_quoted_mask_literals_embed_vb_safely(converted):
    """A mask's embedded quoted literals (yyyy"年"MM"月"dd"日") are valid in
    a <Format> style element but must be DOUBLED inside a VB expression
    literal or =Format(x, "...") does not compile (BC32017 — caught by the
    upload gate's real-compiler leg). The margin run-date field is the
    always-on probe (the compile gate's sha-rotation only compiles this
    fixture every Nth day)."""
    rdl = converted["I18N_CJK.xml"]["rdl_xml"]
    assert '=Format(Globals!ExecutionTime, "yyyy""年""MM""月""dd""日""")' \
        in rdl, "embedded mask quotes are not VB-doubled"
    # and the STYLE surface keeps the raw (undoubled) .NET format string
    assert '<Format>yyyy"年"MM"月"dd"日"</Format>' in rdl


# ---------------------------------------------------------------------------
# 4. CJK-aware glyph metrics in the shared AFM estimator
# ---------------------------------------------------------------------------

def test_afm_estimator_counts_cjk_full_width():
    """5 ideographs at 10pt advance ~5 em·10/72 = 0.694in in a real CJK
    face (MS Gothic advances 1000/1000 em per ideograph); the 500-unit
    Latin average under-reads them 2x and every widen/exact-fit net
    inherits the error."""
    w = _afm_text_width("月次観測報", 10.0, False, True)
    assert w == pytest.approx(5 * 1000 / 1000 * 10 / 72.0, rel=0.01), \
        f"CJK width {w:.3f}in — estimator is not counting full-width glyphs"


def test_afm_estimator_kana_and_fullwidth_forms_are_wide():
    # Hiragana / Katakana / fullwidth Latin are East-Asian W/F too.
    assert _afm_text_width("ページ", 10.0, False, True) \
        == pytest.approx(3 * 10 / 72.0, rel=0.01)
    assert _afm_text_width("Ｗ", 10.0, False, True) \
        == pytest.approx(10 / 72.0, rel=0.01)


def test_afm_estimator_combining_marks_are_zero_width():
    """Arabic harakat / combining accents advance ~0 — counting them as
    half-em average glyphs over-reads a vocalized Arabic label."""
    base = _afm_text_width("قرأ", 10.0, False, True)
    vocalized = _afm_text_width("قَرَأَ", 10.0, False, True)
    assert vocalized == pytest.approx(base, abs=1e-9)


def test_afm_estimator_other_scripts_keep_average():
    # Greek/Cyrillic base letters keep the 500/1000-em average estimate.
    assert _afm_text_width("Σ", 10.0, False, True) \
        == pytest.approx(500 / 1000 * 10 / 72.0, rel=0.01)
    assert _afm_text_width("Ж", 10.0, False, True) \
        == pytest.approx(500 / 1000 * 10 / 72.0, rel=0.01)


# ---------------------------------------------------------------------------
# 5. Wide-glyph label text never rides a default-font textbox
# ---------------------------------------------------------------------------

def _textrun_literal(value_text: str) -> str:
    t = value_text or ""
    if t.lstrip().startswith("="):
        return " ".join(re.findall(r'"([^"]*)"', t))
    return t


def _has_wide(s: str) -> bool:
    import unicodedata
    return any(unicodedata.east_asian_width(c) in ("W", "F") for c in s)


def test_cjk_label_textruns_all_carry_a_font_family(converted):
    """Render-measured defect: synthesized band/card textboxes embed the
    source's label wording with NO FontFamily; the engine's default face
    has no East-Asian glyphs, so every such label prints as notdef boxes.
    Every TextRun whose literal text carries wide glyphs must therefore
    name a FontFamily (the face the source itself declared)."""
    import xml.etree.ElementTree as ET
    rdl = converted["I18N_CJK.xml"]["rdl_xml"]
    root = ET.fromstring(rdl)

    def local(tag):
        return tag.split("}", 1)[1] if "}" in tag else tag

    bare = []
    checked = 0
    for run in root.iter():
        if local(run.tag) != "TextRun":
            continue
        val = next((c for c in run if local(c.tag) == "Value"), None)
        lit = _textrun_literal(val.text if val is not None else "")
        if not _has_wide(lit):
            continue
        checked += 1
        style = next((c for c in run if local(c.tag) == "Style"), None)
        fam = None
        if style is not None:
            fam = next((c for c in style if local(c.tag) == "FontFamily"),
                       None)
        if fam is None or not (fam.text or "").strip():
            bare.append(lit[:40])
    assert checked, "fixture emitted no wide-glyph text runs to check"
    assert not bare, (
        f"{len(bare)} wide-glyph TextRun(s) have no FontFamily and would "
        f"render as tofu in the engine default face: {bare[:6]}")
