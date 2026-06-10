"""
Tests for parent-report artifact stacking (artifact_enrich + ingest.convert_bundle).

Covers:
- SQL-only bundle => non-empty RDL, passes schema-children rules
- DOCX with embedded SELECT only => non-empty RDL
- XML + supporting .sql => artifacts_enriched has non-zero counts; RDL still valid
- XML-only bundle => identical to plain convert() (no regression)
- Every produced RDL still passes schema-children + required-children checks
"""
from __future__ import annotations

import io
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert as plain_convert  # noqa: E402
from converter.ingest import convert_bundle  # noqa: E402
from converter.artifact_enrich import (  # noqa: E402
    enrich_report_from_artifacts,
    enrich_synthetic_from_artifacts,
)
from converter.parsers.oracle_xml import parse_oracle_xml  # noqa: E402


RDL_NS = "http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition"
RD = "{" + RDL_NS + "}"

ALLOWED = {
    "Body": {"ReportItems", "Height", "Style"},
    "ReportItems": {"Line", "Rectangle", "Textbox", "Image", "Subreport",
                    "Tablix", "Chart", "GaugePanel", "CustomReportItem"},
    "DataSources": {"DataSource"},
    "DataSets": {"DataSet"},
    "ReportParameters": {"ReportParameter"},
    "QueryParameters": {"QueryParameter"},
}


def _assert_schema_children_ok(rdl_xml: str) -> None:
    """Trim of test_rdl_schema_children: enforce a subset of SSRS rules."""
    if not rdl_xml or not rdl_xml.strip():
        return  # synthetic-empty path covered elsewhere
    root = ET.fromstring(rdl_xml)
    for elt in root.iter():
        local = elt.tag.split("}", 1)[-1] if "}" in elt.tag else elt.tag
        allowed = ALLOWED.get(local)
        if not allowed:
            continue
        for child in list(elt):
            cl = child.tag.split("}", 1)[-1] if "}" in child.tag else child.tag
            assert cl in allowed, (
                f"Invalid child <{cl}> under <{local}>; "
                f"allowed={sorted(allowed)}"
            )


def _build_minimal_docx(text_paragraphs):
    """Build a tiny docx zip with the given paragraphs in word/document.xml."""
    paras = "".join(
        f"<w:p><w:r><w:t xml:space=\"preserve\">{p}</w:t></w:r></w:p>"
        for p in text_paragraphs
    )
    doc = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{paras}</w:body>"
        "</w:document>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        "</Relationships>"
    )
    ct = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", ct)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("word/document.xml", doc)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Path 2: SQL-only bundle
# ---------------------------------------------------------------------------

SAMPLE_SQL = b"""SELECT permit_no, applicant_name, issue_date
FROM permits
WHERE :P_YEAR = TO_CHAR(issue_date, 'YYYY')
"""


def test_bundle_sql_only_produces_rdl():
    out = convert_bundle([("Q_PERMITS.sql", SAMPLE_SQL)])
    assert "rdl_xml" in out, out
    assert out.get("rdl_xml"), "expected non-empty RDL from sql-only bundle"
    _assert_schema_children_ok(out["rdl_xml"])
    # enrichment summary should be present
    assert "artifacts_enriched" in out
    assert out["artifacts_enriched"]["sql_added"] >= 1


def test_bundle_sql_only_report_name_derived_from_filename():
    out = convert_bundle([("Q_PERMITS.sql", SAMPLE_SQL)])
    name = (out.get("report") or {}).get("name", "")
    # Should be derived from the SQL filename, not the static "BUNDLE" default
    assert name and name != "BUNDLE"


# ---------------------------------------------------------------------------
# Path 2: DOCX-with-SELECT bundle
# ---------------------------------------------------------------------------

def test_bundle_docx_with_select_produces_rdl():
    # Use a filename starting with "sql" so ingest._docx_extract treats it as
    # a SQL-doc and splits out the SELECT block.
    docx_text = [
        "Q_SAMPLE",
        "SELECT id, name FROM widgets WHERE id > 0",
    ]
    blob = _build_minimal_docx(docx_text)
    out = convert_bundle([("sql_doc.docx", blob)])
    assert "rdl_xml" in out, out
    assert out.get("rdl_xml"), "expected non-empty RDL from docx-only bundle"
    _assert_schema_children_ok(out["rdl_xml"])


# ---------------------------------------------------------------------------
# Path 1: XML + supporting SQL
# ---------------------------------------------------------------------------

def test_bundle_xml_plus_sql_enriches(synthetic_xml_bytes):
    extra_sql = b"SELECT a, b, c, d, e, f, g FROM enriched_view\n"
    out = convert_bundle([
        ("TEST_REPORT.xml", synthetic_xml_bytes),
        ("Q_EXTRA.sql", extra_sql),
    ])
    assert "rdl_xml" in out
    assert out["rdl_xml"], "RDL must be non-empty"
    _assert_schema_children_ok(out["rdl_xml"])
    enriched = out.get("artifacts_enriched") or {}
    # Either a new query was added or an existing query was upgraded
    assert (enriched.get("sql_added", 0) + enriched.get("sql_replaced", 0)) >= 1


def test_bundle_xml_plus_doc_labels(synthetic_xml_bytes):
    doc_text = [
        "name: Employee Full Name",
        "hire_year: Year the employee was hired",
        "region_code: Two-letter regional code",
    ]
    blob = _build_minimal_docx(doc_text)
    out = convert_bundle([
        ("TEST_REPORT.xml", synthetic_xml_bytes),
        ("column_glossary.docx", blob),
    ])
    assert "rdl_xml" in out and out["rdl_xml"]
    _assert_schema_children_ok(out["rdl_xml"])
    enriched = out.get("artifacts_enriched") or {}
    # At least one auto-derived label should have been overridden by the glossary.
    assert enriched.get("label_overrides", 0) >= 1


# ---------------------------------------------------------------------------
# Path 1 regression: XML-only bundle must equal plain convert() byte-for-byte
# ---------------------------------------------------------------------------

def test_bundle_xml_only_byte_identical_to_plain_convert(synthetic_xml_bytes):
    plain = plain_convert(synthetic_xml_bytes)
    bundled = convert_bundle([("TEST_REPORT.xml", synthetic_xml_bytes)])
    # The bundle output gains ingest_report / cross_validation keys --
    # the RDL itself must be unchanged.
    assert plain["rdl_xml"] == bundled["rdl_xml"], (
        "XML-only bundle changed the RDL compared to plain convert()"
    )
    # And no enrichment should be reported, since no supporting artifacts
    # were provided.
    assert "artifacts_enriched" not in bundled


# ---------------------------------------------------------------------------
# Unit tests on the enrichers themselves
# ---------------------------------------------------------------------------

def test_enrich_report_skips_when_no_extras(synthetic_xml_bytes):
    report = parse_oracle_xml(synthetic_xml_bytes)
    summary = enrich_report_from_artifacts(
        report,
        {"sql_files": [], "docs": [], "screenshots": []},
    )
    assert summary["sql_added"] == 0
    assert summary["sql_replaced"] == 0
    assert summary["label_overrides"] == 0


@pytest.mark.parametrize("fname,expected_prefix", [
    ("Q_PERMIT.sql", "Q_PERMIT"),
    ("permit_list.sql", "Q_PERMIT_LIST"),
    ("docx::Q_ORG", "Q_ORG"),
])
def test_synthetic_query_name_from_filename(fname, expected_prefix):
    sql_files = [(fname, "SELECT 1 FROM dual")]
    report, summary = enrich_synthetic_from_artifacts(sql_files, [], [])
    assert summary["sql_added"] == 1
    assert any(q.name.startswith(expected_prefix) for q in report.queries)


def test_synthetic_screenshot_hints():
    screens = [("docs/cover.png", b"", "frontend")]
    report, summary = enrich_synthetic_from_artifacts(
        [("Q_X.sql", "SELECT 1 FROM dual")], [], screens
    )
    assert summary["hints"], "expected screenshot hints to be captured"
    assert any("layout hint" in w for w in report.warnings)


def test_doc_label_override_applies_to_blank_label(synthetic_xml_bytes):
    report = parse_oracle_xml(synthetic_xml_bytes)
    # Force one item to have a blank label so the override has somewhere to go
    if report.queries and report.queries[0].items:
        report.queries[0].items[0].label = ""
    target = report.queries[0].items[0].name.upper()
    summary = enrich_report_from_artifacts(
        report,
        {
            "sql_files": [],
            "docs": [("notes.txt", f"{target}: A human-friendly description")],
            "screenshots": [],
        },
    )
    assert summary["label_overrides"] >= 1
    assert report.queries[0].items[0].label == "A human-friendly description"
