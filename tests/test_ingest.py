"""Tests for the multi-file ingest pipeline (converter.ingest)."""
from __future__ import annotations

import os
from pathlib import Path

import pytest


def _read_sample(name: str, samples_dir: Path) -> bytes:
    p = samples_dir / name
    if not p.exists():
        pytest.skip(f"sample missing: {p}")
    return p.read_bytes()


def test_classify_files_recognizes_xml(samples_dir):
    from converter.ingest import classify_files
    blob = _read_sample("MVWF_PERMIT.xml", samples_dir)
    cls = classify_files([("MVWF_PERMIT.xml", blob)])
    assert cls["primary_xml"] is not None
    assert cls["primary_xml"][0] == "MVWF_PERMIT.xml"


def test_classify_files_recognizes_docx(samples_dir):
    from converter.ingest import classify_files
    docx_name = "MVWF_PERMIT Sql queries.docx"
    blob = _read_sample(docx_name, samples_dir)
    cls = classify_files([(docx_name, blob)])
    # The SQL-named docx should land in sql_files (extracted blocks) or docs.
    summaries = cls["category_summary"]
    cats = {s["category"] for s in summaries}
    assert cats & {"sql", "docs"}, f"unexpected categories: {cats}"


def test_classify_files_screenshots_docx(samples_dir):
    from converter.ingest import classify_files
    docx_name = "MVWF_PERMIT frontend screenshots.docx"
    blob = _read_sample(docx_name, samples_dir)
    cls = classify_files([(docx_name, blob)])
    summaries = cls["category_summary"]
    cats = {s["category"] for s in summaries}
    # Frontend screenshots docx should produce screenshots or docs entries.
    assert cats & {"screenshot", "docs"}, f"unexpected categories: {cats}"


def test_classify_files_all_four_samples(samples_dir):
    """Drop the entire sample folder and ensure each file is classified."""
    from converter.ingest import classify_files
    files = []
    expected_names = [
        "MVWF_PERMIT.xml",
        "MVWF_PERMIT Sql queries.docx",
        "MVWF_PERMIT frontend screenshots.docx",
        "MVWF_PERMITbackend screenshots.docx",
    ]
    for n in expected_names:
        p = samples_dir / n
        if p.exists():
            files.append((n, p.read_bytes()))
    if len(files) < 4:
        pytest.skip("not all 4 sample files present")

    cls = classify_files(files)
    summary_files = {s["file"] for s in cls["category_summary"]}
    # Every input file should appear in the category summary.
    for n in expected_names:
        assert n in summary_files, f"{n} missing from classification summary"

    # The Oracle XML must be the primary
    assert cls["primary_xml"] is not None
    assert cls["primary_xml"][0] == "MVWF_PERMIT.xml"


def test_convert_bundle_with_xml_runs_full_pipeline(samples_dir):
    from converter.ingest import convert_bundle
    blob = _read_sample("MVWF_PERMIT.xml", samples_dir)
    out = convert_bundle([("MVWF_PERMIT.xml", blob)])
    assert "ingest_report" in out
    # Either a report (path 1) or an error key (path 3) must be present.
    assert "report" in out or "error" in out
    if "report" in out:
        assert isinstance(out["report"], dict)
        assert out["rdl_xml"]
        assert isinstance(out["ingest_report"], dict)


def test_convert_bundle_empty_returns_error():
    from converter.ingest import convert_bundle
    out = convert_bundle([])
    assert out.get("error") == "no_convertible_artifacts"
    assert "ingest_report" in out


def test_classify_unknown_extension():
    from converter.ingest import classify_files
    cls = classify_files([("notes.weird", b"random bytes")])
    summaries = cls["category_summary"]
    cats = {s["category"] for s in summaries}
    assert "unknown" in cats


def test_classify_files_returns_required_buckets():
    from converter.ingest import classify_files
    cls = classify_files([])
    for k in ("primary_xml", "rdf_binary", "sql_files", "docs",
              "screenshots", "unknown", "category_summary"):
        assert k in cls
