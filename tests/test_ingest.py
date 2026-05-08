"""
Tests for ingest.classify_files / convert_bundle.

Uses the synthetic_xml_bytes fixture so the test suite never depends on
files in samples/oracle/.
"""
from converter.ingest import classify_files, convert_bundle


def test_classify_files_recognizes_xml(synthetic_xml_bytes):
    out = classify_files([("TEST_REPORT.xml", synthetic_xml_bytes)])
    assert out.get("primary_xml") is not None
    assert out["primary_xml"][0] == "TEST_REPORT.xml"


def test_classify_files_recognizes_unknown_xml():
    blob = b'<?xml version="1.0"?><not_a_report />'
    out = classify_files([("not.xml", blob)])
    # Should not classify as primary_xml since root is not <report>
    assert out.get("primary_xml") is None


def test_classify_files_handles_sql():
    sql = b"SELECT * FROM employees;"
    out = classify_files([("query.sql", sql)])
    assert any(name == "query.sql" for name, _ in out.get("sql_files", []))


def test_classify_files_handles_random_binary():
    blob = b"\x00\x01\x02junk"
    out = classify_files([("junk.bin", blob)])
    assert out.get("primary_xml") is None


def test_convert_bundle_with_xml_runs_full_pipeline(synthetic_xml_bytes):
    out = convert_bundle([("TEST_REPORT.xml", synthetic_xml_bytes)])
    assert "report" in out
    assert "rdl_xml" in out
    assert len(out["rdl_xml"]) > 0


def test_convert_bundle_no_artifacts_returns_error():
    out = convert_bundle([("garbage.bin", b"\x00\x01")])
    # Should set error since no convertible artifact
    assert out.get("error") == "no_convertible_artifacts" or "report" not in out


def test_classify_files_category_summary_shape(synthetic_xml_bytes):
    out = classify_files([
        ("report.xml", synthetic_xml_bytes),
        ("query.sql", b"SELECT 1;"),
    ])
    summary = out.get("category_summary", [])
    assert isinstance(summary, list)
    assert len(summary) >= 1
    for item in summary:
        assert "category" in item
        assert "file" in item


def test_convert_bundle_includes_ingest_report(synthetic_xml_bytes):
    out = convert_bundle([("TEST_REPORT.xml", synthetic_xml_bytes)])
    assert "ingest_report" in out
    ir = out["ingest_report"]
    assert "category_summary" in ir or "primary_xml" in ir
