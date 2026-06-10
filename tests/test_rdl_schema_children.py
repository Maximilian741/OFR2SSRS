"""Regression tests for SSRS 2008/01 schema-compliant child elements.

Catches the class of bug where the generator emits a child element
that the SSRS deserializer rejects with:

    "Deserialization failed: The element 'X' in namespace
    'http://schemas.microsoft.com/sqlserver/reporting/2008/01/
    reportdefinition' has invalid child element 'Y' in namespace ..."

The user hit this with <Body><Width/></Body> -- Body's allowed children
per the SSRS 2008/01 RDL schema are only {ReportItems, Height, Style},
so any <Width> there blocks the upload.

This test enforces the allowed-children sets for the elements we
actually emit. The allowed sets come straight from the SSRS 2008/01
ReportDefinition.xsd documented on Microsoft Learn. Name-agnostic --
walks every discovered fixture case + the synthetic fixture.
"""
from __future__ import annotations
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest


RDL_NS = "http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition"
RD = "{" + RDL_NS + "}"

# Subset of SSRS 2008/01 allowed-children. Each entry: parent local name ->
# set of locally-named children the schema permits. We only enforce
# elements the generator can produce; unknown parents are skipped.
ALLOWED = {
    "Report": {
        "Description", "Author", "AutoRefresh", "DataSources", "DataSets",
        "ReportParameters", "ReportParametersLayout", "EmbeddedImages",
        "Body", "ReportSections", "Page", "Width", "Code", "Variables",
        "Language", "CodeModules", "Classes", "CustomProperties",
        "DataElementName", "DataElementStyle", "DataSchema",
        "DataTransform", "ConsumeContainerWhitespace",
        "InitialPageName", "Subject", "InitialPageNumber",
    },
    "Body": {"ReportItems", "Height", "Style"},
    "Page": {
        "PageHeader", "PageFooter", "PageHeight", "PageWidth",
        "LeftMargin", "RightMargin", "TopMargin", "BottomMargin",
        "Columns", "ColumnSpacing", "InteractiveHeight",
        "InteractiveWidth", "Style",
    },
    "PageHeader": {"Height", "PrintOnFirstPage", "PrintOnLastPage",
                   "ReportItems", "Style"},
    "PageFooter": {"Height", "PrintOnFirstPage", "PrintOnLastPage",
                   "ReportItems", "Style"},
    "CellContents": {"Line", "Rectangle", "Textbox", "Image", "Subreport",
                     "Tablix", "Chart", "GaugePanel", "CustomReportItem",
                     "ColSpan", "RowSpan"},
    "ReportItems": {"Line", "Rectangle", "Textbox", "Image", "Subreport",
                    "Tablix", "Chart", "GaugePanel", "CustomReportItem"},
    "DataSources": {"DataSource"},
    "DataSets": {"DataSet"},
    "ReportParameters": {"ReportParameter"},
    "ReportParameter": {
        "DataType", "Nullable", "DefaultValue", "AllowBlank", "Prompt",
        "Hidden", "MultiValue", "ValidValues", "UsedInQuery",
        "DataProvider", "DataField",
    },
    "Query": {
        "DataSourceName", "CommandText", "CommandType", "QueryParameters",
        "Timeout", "DataSourceReference",
    },
    "QueryParameters": {"QueryParameter"},
    "QueryParameter": {"Value", "OmitFromQuery", "MultiValue"},
}


def _fixtures():
    fx = Path(__file__).parent / "fixtures" / "source_of_truth"
    if not fx.exists():
        return []
    out = []
    for d in sorted(fx.iterdir()):
        src = d / "source.xml"
        if src.exists():
            out.append(pytest.param(d.name, src, id=d.name))
    return out


def _walk_schema_violations(root: ET.Element):
    """Yield (parent_local, child_local) for every parent->child edge
    that violates the allowed-children map.

    Only children in the RDL namespace are enforced. Children in the
    rd: design-time namespace (DrawGrid, GridSpacing, etc.) are
    permitted on any parent -- SSRS treats them as opaque designer
    hints and ignores them at deserialization."""
    for parent in root.iter():
        ptag = parent.tag
        if not isinstance(ptag, str):
            continue
        plocal = ptag.split("}", 1)[-1]
        if plocal not in ALLOWED:
            continue
        allowed = ALLOWED[plocal]
        for child in parent:
            ctag = child.tag
            if not isinstance(ctag, str):
                continue
            # Skip non-RDL-namespace children (rd: designer hints, etc.)
            if not ctag.startswith("{" + RDL_NS + "}"):
                continue
            clocal = ctag.split("}", 1)[-1]
            if clocal not in allowed:
                yield plocal, clocal


@pytest.mark.parametrize("case_name,src_path", _fixtures())
def test_no_invalid_schema_children(case_name, src_path):
    """Every parent->child edge in the emitted RDL must be schema-legal."""
    from converter import convert
    rdl = convert(src_path.read_bytes())["rdl_xml"]
    root = ET.fromstring(rdl)
    violations = list(_walk_schema_violations(root))
    assert not violations, (
        f"[{case_name}] schema-illegal child element(s) -- SSRS will "
        f"reject upload with 'invalid child element X':\n"
        + "\n".join(f"  <{p}><{c}/> not allowed" for p, c in violations[:10])
    )


def test_synthetic_fixture_has_no_invalid_children(translated_report):
    """Same enforcement on the synthetic conftest fixture so the rule
    holds even when no real-world cases are staged."""
    from converter.generators.rdl import generate_rdl
    rdl = generate_rdl(translated_report, target_db="oracle")
    root = ET.fromstring(rdl)
    violations = list(_walk_schema_violations(root))
    assert not violations, (
        f"schema-illegal child element(s) in synthetic fixture:\n"
        + "\n".join(f"  <{p}><{c}/> not allowed" for p, c in violations[:10])
    )
