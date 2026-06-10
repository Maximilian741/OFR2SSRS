"""Regression tests for SSRS 2008/01 schema REQUIRED-child elements.

Catches the class of upload-blocker where a parent element is present
but missing a child that the SSRS deserializer flags as mandatory:

    "Deserialization failed: The report definition element 'X' is empty.
    It is missing a mandatory child element of type 'Y'."

This is the exact opposite of test_rdl_schema_children.py (which checks
INVALID children). Both must pass for an RDL to upload cleanly.

Name-agnostic: walks every fixture case + the synthetic fixture and
asserts that every emitted parent has its required children.
"""
from __future__ import annotations
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest


RDL_NS = "http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition"
RD = "{" + RDL_NS + "}"

# Minimum required children per parent, per SSRS 2008/01 schema.
# Only enforced when the parent element is actually emitted.
REQUIRED = {
    "Page":         {"PageHeight", "PageWidth", "LeftMargin", "RightMargin",
                     "TopMargin", "BottomMargin"},
    "PageHeader":   {"Height"},
    "PageFooter":   {"Height"},
    "Body":         {"Height", "ReportItems"},
    "Report":       {"Body", "Width", "Page"},
    "DataSet":      {"Query", "Fields"},
    "DataSource":   {"DataSourceReference"},
    "Query":        {"DataSourceName", "CommandText"},
    "Tablix":       {"TablixBody", "TablixColumnHierarchy",
                     "TablixRowHierarchy", "DataSetName"},
    "TablixBody":   {"TablixColumns", "TablixRows"},
    "TablixColumn": {"Width"},
    "TablixRow":    {"Height", "TablixCells"},
    "TablixCell":   {"CellContents"},
    "ReportParameter": {"DataType", "Prompt"},
    "QueryParameter":  {"Value"},
}


def _children_local_names(parent: ET.Element):
    out = set()
    for c in parent:
        tag = c.tag
        if isinstance(tag, str):
            out.add(tag.split("}", 1)[-1])
    return out


def _walk_required_violations(root: ET.Element):
    for parent in root.iter():
        ptag = parent.tag
        if not isinstance(ptag, str):
            continue
        plocal = ptag.split("}", 1)[-1]
        required = REQUIRED.get(plocal)
        if not required:
            continue
        have = _children_local_names(parent)
        missing = required - have
        if missing:
            yield plocal, parent.get("Name"), sorted(missing)


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
def test_no_missing_required_children(case_name, src_path):
    """Every emitted parent has every child the SSRS schema marks as
    mandatory. Catches "is empty / missing mandatory child" upload errors."""
    from converter import convert
    rdl = convert(src_path.read_bytes())["rdl_xml"]
    root = ET.fromstring(rdl)
    violations = list(_walk_required_violations(root))
    assert not violations, (
        f"[{case_name}] required-child violation(s) -- SSRS upload will "
        f"fail with 'X is empty / missing mandatory child':\n"
        + "\n".join(
            f"  <{p} Name={n!r}> missing {m}" for p, n, m in violations[:10]
        )
    )


def test_synthetic_fixture_has_no_missing_required_children(translated_report):
    """Same enforcement on the synthetic conftest fixture so the rule
    holds even when no real-world cases are staged."""
    from converter.generators.rdl import generate_rdl
    rdl = generate_rdl(translated_report, target_db="oracle")
    root = ET.fromstring(rdl)
    violations = list(_walk_required_violations(root))
    assert not violations, (
        "required-child violation(s) in synthetic fixture:\n"
        + "\n".join(
            f"  <{p} Name={n!r}> missing {m}" for p, n, m in violations[:10]
        )
    )
