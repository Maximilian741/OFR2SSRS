"""Regression lock-in for the layout-driven cover page used by
letter / certificate reports (the per-record body kind).

Round 1 (test_per_record_body_section_scope.py) stopped the
Parameter-Form labels from leaking into the per-record body.
Round 2 (this file) ensures the COVER PAGE itself uses the
report's own section_header layout fields ("Selection Criteria:",
"Permit Details:", "Hyperlinks in Permits:", etc.) instead of a
generic "Report Parameters" list of declared report parameters.

Properties pinned down here:

  A. _build_letter_cover_page exists and returns either an
     ET.Element (a layout-driven cover) or None.

  B. For a letter/certificate report whose section_header has
     form-shaped content, _build_per_record_body picks the
     LAYOUT-DRIVEN cover (textboxes named LcCov_*) -- NOT the
     generic one (Cov_ParmLbl_* / Cov_ParmVal_*).

  C. The layout-driven cover preserves the report's own labels
     in order. For SAMPLE_DRILLTHROUGH-shaped XML, the cover must include
     the labels from section_header (e.g. "Selection Criteria:",
     "Permit Details:", "Hyperlinks in Permits:").

  D. The over-aggressive `&CF_*` / `&CP_*` startswith filter is
     GONE. A text field whose content legitimately starts with
     "&CF_PERMITTEES" or similar must reach the per-record body
     so the two address cards keep rendering.

  E. SAMPLE_MASTER_DETAIL regression guard: the cover-page redirect must
     not affect grouped-card reports. SAMPLE_MASTER_DETAIL still emits
     its grouped-card Tablix and has neither LcCov_* nor a
     Rect_RecordPage rectangle.
"""
from __future__ import annotations
import re
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402
from converter.generators.rdl import (  # noqa: E402
    _build_letter_cover_page,
    _section_by_kind,
)
from converter.parsers.oracle_xml import parse_oracle_xml  # noqa: E402


FIXTURES = HERE / "fixtures" / "source_of_truth"


def _cases():
    if not FIXTURES.exists():
        return []
    out = []
    for d in sorted(FIXTURES.iterdir()):
        src = d / "source.xml"
        if src.exists():
            out.append(pytest.param(d.name, src, id=d.name))
    return out


def _rdl_for(src_path: Path) -> str:
    return convert(src_path.read_bytes())["rdl_xml"]


# ---------------------------------------------------------------------------
# PROPERTY A -- helper exists and is callable.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("case_name,src_path", _cases())
def test_letter_cover_helper_returns_element_or_none(case_name, src_path):
    rep = parse_oracle_xml(src_path.read_bytes())
    rect = _build_letter_cover_page(rep)
    # Either we got a rectangle Element, or None (caller falls back to
    # the generic _build_cover_page). Both shapes are valid -- the
    # helper is permitted to opt out when section_header is empty.
    assert rect is None or hasattr(rect, "tag"), (
        f"[{case_name}] _build_letter_cover_page returned unexpected "
        f"{type(rect).__name__}"
    )


# ---------------------------------------------------------------------------
# PROPERTY B -- letter reports actually use the layout-driven cover.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("case_name,src_path", _cases())
def test_letter_per_record_uses_layout_cover_when_available(case_name, src_path):
    rdl = _rdl_for(src_path)
    has_record = "Rect_RecordPage" in rdl
    if not has_record:
        pytest.skip(f"{case_name}: not a per-record report")

    rep = parse_oracle_xml(src_path.read_bytes())
    expected_layout_cover = _build_letter_cover_page(rep) is not None
    if not expected_layout_cover:
        pytest.skip(
            f"{case_name}: letter report but section_header isn't "
            f"form-shaped; falls back to generic cover -- not a test failure"
        )

    # Layout-driven cover textboxes are named LcCov_*.
    assert "LcCov_" in rdl, (
        f"[{case_name}] per-record report should emit the layout-"
        f"driven cover (LcCov_* textboxes) -- found none"
    )
    # And the generic cover's parameter rows (Cov_ParmLbl_*) should
    # NOT also appear -- only one cover per report.
    assert "Cov_ParmLbl_" not in rdl, (
        f"[{case_name}] both layout-driven and generic covers were "
        f"emitted -- pick one"
    )


# ---------------------------------------------------------------------------
# PROPERTY C -- section_header labels survive into the cover.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("case_name,src_path", _cases())
def test_layout_cover_carries_section_header_labels(case_name, src_path):
    rdl = _rdl_for(src_path)
    if "LcCov_" not in rdl:
        pytest.skip(f"{case_name}: no layout-driven cover for this report")

    rep = parse_oracle_xml(src_path.read_bytes())
    header_section = _section_by_kind(rep, "section_header")
    if header_section is None:
        pytest.skip(f"{case_name}: no section_header")

    # Gather text labels declared in section_header (top-level text
    # fields whose content looks like a label -- ends with ":").
    def _walk(g):
        yield g
        for ch in g.children or []:
            yield from _walk(ch)
    declared_labels = []
    for g in _walk(header_section):
        for f in g.fields or []:
            if (f.kind or "field") != "text":
                continue
            t = (f.text or "").strip()
            if t.endswith(":") and len(t) <= 60:
                declared_labels.append(t)

    if not declared_labels:
        pytest.skip(f"{case_name}: section_header has no label-shaped text fields")

    # Pull every LcCov_Lbl_* value and check at least one declared
    # label survived to the cover. (Some labels are filtered as
    # author-notes; the helper isn't expected to keep ALL of them.)
    cover_labels = re.findall(
        r'Name="LcCov_Lbl_\d+".*?<Value>([^<]+)</Value>',
        rdl, re.DOTALL,
    )
    cover_labels_norm = {l.strip().lower() for l in cover_labels}
    matches = [
        l for l in declared_labels
        if l.strip().lower() in cover_labels_norm
    ]
    assert matches, (
        f"[{case_name}] layout cover dropped EVERY declared label.\n"
        f"  declared: {declared_labels[:6]}\n"
        f"  cover   : {cover_labels[:6]}"
    )


# ---------------------------------------------------------------------------
# PROPERTY D -- over-aggressive `&CF_/&CP_ startswith` filter is gone.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("case_name,src_path", _cases())
def test_per_record_keeps_text_starting_with_formula_token(case_name, src_path):
    """A per-record text field whose source TEXT begins with "&CF_"
    or "&CP_" (e.g. the two address-card blocks) must NOT be
    filtered out. The textbox must be present so the card stays
    visible -- whether or not the token resolves to actual data."""
    rdl = _rdl_for(src_path)
    m = re.search(
        r'<Rectangle Name="Rect_RecordPage">.*?</Rectangle>',
        rdl, re.DOTALL,
    )
    if m is None:
        pytest.skip(f"{case_name}: not a per-record report")
    block = m.group(0)

    rep = parse_oracle_xml(src_path.read_bytes())
    main_section = _section_by_kind(rep, "section_main")
    if main_section is None:
        pytest.skip(f"{case_name}: no section_main")

    def _walk(g):
        yield g
        for ch in g.children or []:
            yield from _walk(ch)

    formula_text_count = 0
    for g in _walk(main_section):
        for f in g.fields or []:
            if (f.kind or "field") != "text":
                continue
            txt = (f.text or "").strip()
            if txt.startswith("&CF_") or txt.startswith("&CP_"):
                formula_text_count += 1
    if formula_text_count == 0:
        pytest.skip(f"{case_name}: no formula-token text fields in section_main")

    # The per-record rect must have AT LEAST as many textboxes as
    # there are formula-token text fields (each one survives).
    # Match both the legacy flat-positional name (Tb_Rec_N) and the
    # round-5 frame-based name (RecP_Tb_N).
    tbs = re.findall(r'Name="(?:Tb_Rec_|RecP_Tb_)\d+"', block)
    assert len(tbs) >= formula_text_count, (
        f"[{case_name}] section_main has {formula_text_count} formula-"
        f"token text fields but the per-record rect only has {len(tbs)} "
        f"textboxes total -- the &CF_/&CP_ filter is back."
    )


# ---------------------------------------------------------------------------
# PROPERTY E -- nested master-detail grouped-card regression guard.
# ---------------------------------------------------------------------------
def test_master_detail_stays_grouped_card_no_lccov():
    rdl = _rdl_for(FIXTURES / "master_detail" / "source.xml")
    # A nested master-detail report renders via the deterministic grouped
    # Tablix (group-tree driven). Grouped-card remains an acceptable shape.
    assert ("Tablix_Nested" in rdl or "Tablix_Cards" in rdl), (
        "the master-detail report lost its grouped Tablix"
    )
    assert "Rect_RecordPage" not in rdl, (
        "the master-detail report picked up a per-record rectangle -- routing leaked"
    )
    assert "LcCov_" not in rdl, (
        "the master-detail report picked up a letter-cover textbox -- only "
        "letter/certificate reports should get LcCov_*"
    )
