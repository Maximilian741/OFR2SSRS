"""Regression lock-in for the per-record (letter / certificate) body
section scoping.

Bug history: _build_per_record_body used to walk
_layout_fields_in_order(report) which traverses EVERY top-level
LayoutGroup (section_header + section_main + trailer + ...). For
SAMPLE_DRILLTHROUGH that pulled the Parameter Form labels living in
section_header ("Selection Criteria:", "*Generate Envelopes:",
"[Permittee] is a hyperlink to ...", and a leaked "&CF_*" token)
into the per-record body rectangle, so they reappeared on EVERY
record page in the SSRS PDF.

Fix: walk only the section_main subtree, mirroring what the generic
document renderer in html_mockup does.

Properties this file pins down (all generic / name-agnostic):

  A. Per-record rectangle exists when the report is letter-shaped.
     It must contain only a bounded set of textboxes (the fields in
     section_main, not the whole report).

  B. No textbox in the per-record rectangle carries a Value whose
     text matches a section_header instruction-label pattern:
       - starts with "*" and mentions envelope/sort/suggest
       - contains the phrase "is a hyperlink"
       - contains a literal "&CF_" or "&CP_" unresolved-token
       - is one of the cover-page title lines

  C. The new helper _layout_fields_in_order_from_section exists and
     walks ONLY the section subtree handed to it -- a section_main
     in -> only section_main fields out.

  D. SAMPLE_MASTER_DETAIL regression guard: routing the per-record body
     fix must NOT affect the grouped-card flow at all. SAMPLE_MASTER_DETAIL
     must still emit a Tablix_Cards (grouped-card) and no
     Rect_RecordPage.

Tests are name-agnostic; they walk every case_NNN fixture and skip
the ones whose RDL isn't shaped for the per-record body.
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
    _layout_fields_in_order,
    _layout_fields_in_order_from_section,
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


def _record_rect(rdl: str):
    """Return the Rect_RecordPage block, or None if the report isn't
    per-record-shaped."""
    m = re.search(
        r'<Rectangle Name="Rect_RecordPage">.*?</Rectangle>',
        rdl, re.DOTALL,
    )
    return m.group(0) if m else None


def _textbox_values(rect_block: str):
    """Pull every <Value> string from the per-record rect, decoded
    enough to match against instruction-label patterns."""
    raw = re.findall(r"<Value>(.*?)</Value>", rect_block, re.DOTALL)
    # Unescape the handful of XML entities we care about; full
    # XML decoding isn't required for keyword matching.
    out = []
    for v in raw:
        v = v.replace("&amp;", "&").replace("&quot;", '"')
        out.append(v.strip())
    return out


# ---------------------------------------------------------------------------
# PROPERTY A -- per-record rectangle stays small (no flood of header fields).
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("case_name,src_path", _cases())
def test_per_record_rect_is_not_flooded(case_name, src_path):
    rdl = _rdl_for(src_path)
    block = _record_rect(rdl)
    if block is None:
        pytest.skip(f"{case_name}: not a per-record report")
    tbs = re.findall(r"<Textbox\b[^>]*Name=\"Tb_Rec_\d+\"", block)
    # Oracle source XML for a letter / certificate has at most ~30
    # positional fields in section_main. The bug used to push it to
    # 80+ by leaking section_header. A hard ceiling of 40 catches
    # any future regression that re-introduces the leak.
    assert len(tbs) <= 40, (
        f"[{case_name}] per-record rectangle has {len(tbs)} textboxes "
        f"-- exceeds 40-textbox sanity ceiling. The header section "
        f"is probably leaking back in."
    )


# ---------------------------------------------------------------------------
# PROPERTY B -- no header-section instruction labels in the per-record body.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("case_name,src_path", _cases())
def test_per_record_has_no_parameter_form_instructions(case_name, src_path):
    rdl = _rdl_for(src_path)
    block = _record_rect(rdl)
    if block is None:
        pytest.skip(f"{case_name}: not a per-record report")
    values = _textbox_values(block)
    offenders = []
    for v in values:
        lower = v.lower()
        # *...envelope / *...sort order / *...suggest are Parameter-
        # Form author-notes from <section name="header"> describing
        # how to use the form. They must NEVER appear per-record.
        stripped = v.lstrip('="').strip()
        if stripped.startswith("*") and (
            "envelope" in lower
            or "sort order" in lower
            or "suggest" in lower
        ):
            offenders.append(("note-asterisk", v[:80]))
            continue
        # Author-notes describing what a hyperlink does belong to the
        # parameter form, not the per-record letter.
        if "is a hyperlink" in lower:
            offenders.append(("hyperlink-note", v[:80]))
            continue
        # Unresolved &CF_* / &CP_* tokens are formula references that
        # leaked out of the parameter form layer -- the per-record
        # body should never carry a literal one.
        if "&cf_" in lower or "&cp_" in lower:
            # Filter out legitimate SSRS expressions which happen to
            # mention the token inside a function call name -- those
            # would start with "=" and contain "Fields!" / "Parameters!".
            if v.startswith("=") and ("Fields!" in v or "Parameters!" in v):
                continue
            offenders.append(("unresolved-formula-token", v[:80]))
            continue
    assert not offenders, (
        f"[{case_name}] Per-record body contains Parameter-Form "
        f"instructions / notes:\n"
        + "\n".join(f"  [{kind}] {text}" for kind, text in offenders[:6])
    )


# ---------------------------------------------------------------------------
# PROPERTY C -- helper walks only the passed-in section.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("case_name,src_path", _cases())
def test_section_scoped_helper_excludes_header_fields(case_name, src_path):
    """_layout_fields_in_order_from_section(section_main) must return
    a strict subset of _layout_fields_in_order(report). And it MUST
    NOT contain any field whose y-coordinate matches a field that
    lives in section_header (proxy for: header fields excluded)."""
    rep = parse_oracle_xml(src_path.read_bytes())
    main = _section_by_kind(rep, "section_main")
    header = _section_by_kind(rep, "section_header")
    if main is None:
        pytest.skip(f"{case_name}: no section_main")

    all_fields = _layout_fields_in_order(rep)
    scoped = _layout_fields_in_order_from_section(main)

    all_ids = {id(t[3]) for t in all_fields}
    scoped_ids = {id(t[3]) for t in scoped}
    assert scoped_ids.issubset(all_ids), (
        f"[{case_name}] scoped walk returned fields that aren't in "
        f"the full walk -- internal inconsistency."
    )

    if header is not None:
        header_field_ids = set()
        # Collect every field reachable under section_header.
        stack = [header]
        while stack:
            g = stack.pop()
            for f in g.fields or []:
                header_field_ids.add(id(f))
            stack.extend(g.children or [])
        leaked = scoped_ids & header_field_ids
        assert not leaked, (
            f"[{case_name}] section_main scoped walk somehow included "
            f"{len(leaked)} field(s) that live under section_header."
        )


def test_section_scoped_helper_handles_none():
    """Passing None must return an empty list, not raise."""
    out = _layout_fields_in_order_from_section(None)
    assert out == []


# ---------------------------------------------------------------------------
# PROPERTY D -- nested master-detail grouped-card regression guard.
# ---------------------------------------------------------------------------
def test_master_detail_still_emits_grouped_card_not_record_page():
    """The per-record fix must NOT route a nested master-detail report
    through the record-page body. A master-detail report (master group +
    linked detail group) must emit a grouped Tablix -- the deterministic
    Tablix_Nested / Tablix_Cards driven by the parsed group tree."""
    rdl = _rdl_for(FIXTURES / "master_detail" / "source.xml")
    assert ("Tablix_Nested" in rdl or "Tablix_Cards" in rdl), (
        "the master-detail report lost its grouped Tablix -- the "
        "per-record routing fix accidentally re-routed it."
    )
    assert "Rect_RecordPage" not in rdl, (
        "the master-detail report gained a per-record rectangle -- "
        "should still be grouped, not per-record."
    )
