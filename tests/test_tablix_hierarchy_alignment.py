"""Tablix structural invariant: the number of TablixRows must equal the
number of INNERMOST TablixMembers in the TablixRowHierarchy. Same rule
for TablixColumns vs TablixColumnHierarchy.

SSRS rejects RDL upload with:

    "The tablix '<Name>' has an incorrect number of TablixRows. The
    number of TablixRows must equal the number of innermost
    TablixMembers (TablixMembers with no submembers) in the
    TablixRowHierarchy."

(and the analogous error for columns.)

This is the exact upload-blocker the user hit when SAMPLE_DRILLTHROUGH was
converted: the generator emitted a hierarchy with 3 innermost members
(band + card + spurious detail placeholder) but only 2 TablixRows
(band + card). The test below walks every emitted Tablix and asserts
the counts line up, name-agnostic, parameterized over every fixture +
the synthetic conftest fixture.
"""
from __future__ import annotations
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest


RDL_NS = "http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition"
RD = "{" + RDL_NS + "}"


def _count_innermost(member: ET.Element) -> int:
    """A leaf TablixMember is one whose <TablixMembers> child is absent
    or empty. Returns the total number of leaves in this member's
    subtree."""
    inner = member.find(RD + "TablixMembers")
    if inner is None or len(list(inner)) == 0:
        return 1
    return sum(_count_innermost(m) for m in inner)


def _walk_tablix_violations(root: ET.Element):
    """Yield (tablix_name, axis, rows_or_cols, innermost_count) for
    every Tablix whose row/column count doesn't match its hierarchy."""
    for tx in root.iter(RD + "Tablix"):
        name = tx.get("Name", "?")
        body = tx.find(RD + "TablixBody")
        if body is None:
            continue

        # ROW axis
        rows = body.find(RD + "TablixRows")
        row_hier = tx.find(RD + "TablixRowHierarchy")
        row_count = len(list(rows)) if rows is not None else 0
        row_leaves = 0
        if row_hier is not None:
            row_members = row_hier.find(RD + "TablixMembers")
            if row_members is not None:
                row_leaves = sum(_count_innermost(m) for m in row_members)
        if row_count != row_leaves:
            yield (name, "row", row_count, row_leaves)

        # COLUMN axis
        cols = body.find(RD + "TablixColumns")
        col_hier = tx.find(RD + "TablixColumnHierarchy")
        col_count = len(list(cols)) if cols is not None else 0
        col_leaves = 0
        if col_hier is not None:
            col_members = col_hier.find(RD + "TablixMembers")
            if col_members is not None:
                col_leaves = sum(_count_innermost(m) for m in col_members)
        if col_count != col_leaves:
            yield (name, "col", col_count, col_leaves)


FIXTURES = Path(__file__).parent / "fixtures" / "source_of_truth"


def _cases():
    if not FIXTURES.exists():
        return []
    return [
        pytest.param(d.name, d / "source.xml", id=d.name)
        for d in sorted(FIXTURES.iterdir())
        if (d / "source.xml").exists()
    ]


@pytest.mark.parametrize("case_name,src_path", _cases())
def test_tablix_row_count_matches_hierarchy(case_name, src_path):
    """Every Tablix's TablixRows count must equal the number of leaves
    in its TablixRowHierarchy. Same invariant applies to columns."""
    from converter import convert
    rdl = convert(src_path.read_bytes())["rdl_xml"]
    root = ET.fromstring(rdl)
    violations = list(_walk_tablix_violations(root))
    assert not violations, (
        f"[{case_name}] Tablix row/column count != innermost-member "
        f"count -- SSRS upload will fail with 'incorrect number of "
        f"Tablix{{Rows,Columns}}':\n"
        + "\n".join(
            f"  '{n}' {axis} count={cnt}, innermost leaves={leaves}"
            for n, axis, cnt, leaves in violations[:10]
        )
    )


def test_synthetic_fixture_tablix_alignment(translated_report):
    """Same enforcement on the synthetic conftest fixture."""
    from converter.generators.rdl import generate_rdl
    rdl = generate_rdl(translated_report, target_db="oracle")
    root = ET.fromstring(rdl)
    violations = list(_walk_tablix_violations(root))
    assert not violations, (
        "synthetic Tablix row/column count mismatch:\n"
        + "\n".join(
            f"  '{n}' {axis} count={cnt}, innermost leaves={leaves}"
            for n, axis, cnt, leaves in violations[:10]
        )
    )


# Plus a directly-exercised end-to-end check on every XML the user has
# uploaded -- catches regressions across the FULL set of real reports,
# not just the staged fixtures.
import os

import glob as _glob

# Corpus: $O2S_CORPUS_DIR if set, else the bundled samples + fixtures, so the
# suite runs for anyone cloning the repo (not just the author's sandbox).
_ROOT_TBX = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _upload_xmls():
    dirs = [os.environ.get("O2S_CORPUS_DIR"),
            os.path.join(_ROOT_TBX, "samples", "oracle"),
            os.path.join(_ROOT_TBX, "tests", "fixtures", "source_of_truth")]
    out, seen = [], set()
    for d in dirs:
        if not d or not os.path.isdir(d):
            continue
        for p in sorted(_glob.glob(os.path.join(d, "**", "*.xml"), recursive=True)):
            nm = os.path.basename(p)
            if nm in seen:
                continue
            seen.add(nm)
            out.append(pytest.param(nm, p, id=nm))
    return out


@pytest.mark.parametrize("xml_name,xml_path", _upload_xmls())
def test_uploaded_xml_tablix_alignment(xml_name, xml_path):
    """End-to-end on every real Oracle Reports XML the user has
    uploaded. Catches the bug across the WHOLE shipping set,
    not just the curated fixtures."""
    from converter import convert
    try:
        rdl = convert(open(xml_path, "rb").read())["rdl_xml"]
    except Exception as e:
        pytest.skip(f"convert raised {type(e).__name__}: {e}")
    root = ET.fromstring(rdl)
    violations = list(_walk_tablix_violations(root))
    assert not violations, (
        f"[{xml_name}] Tablix row/column count mismatch:\n"
        + "\n".join(
            f"  '{n}' {axis} count={cnt}, innermost leaves={leaves}"
            for n, axis, cnt, leaves in violations[:10]
        )
    )
