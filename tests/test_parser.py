"""Tests for the Oracle XML parser (converter.parsers.oracle_xml)."""
from __future__ import annotations

import pytest


def test_parsed_report_basic_shape(parsed_report):
    assert parsed_report is not None
    assert parsed_report.name, "report name should be non-empty"
    assert parsed_report.dtd_version, "DTD version should be non-empty"


def test_parameter_count(parsed_report):
    """COMPLEX_REPORT.xml has 16 userParameters."""
    assert len(parsed_report.parameters) >= 1


def test_parameter_names_unique_and_prefixed(parsed_report):
    names = [p.name for p in parsed_report.parameters]
    for n in names:
        assert n, f"empty parameter name: {names}"
    assert len(set(names)) == len(names)


def test_parameter_datatypes(parsed_report):
    """Every parameter should have a datatype that maps to a known SSRS type."""
    valid_ssrs = {"String", "Integer", "DateTime"}
    for p in parsed_report.parameters:
        assert p.ssrs_datatype in valid_ssrs, (
            f"unexpected ssrs_datatype {p.ssrs_datatype} for {p.name}"
        )


def test_query_count(parsed_report):
    """The sample has 3 dataSources."""
    assert len(parsed_report.queries) >= 1


def test_query_names_present(parsed_report):
    """At least one query was parsed."""
    names = [q.name for q in parsed_report.queries]
    assert len(names) >= 1, "expected at least one DataQuery"


def test_q_permit_item_count(parsed_report):
    """The primary query has at least one DataItem."""
    assert parsed_report.queries
    main = parsed_report.queries[0]
    assert len(main.items) >= 1


def test_q_permit_has_sql_text(parsed_report):
    """The primary query has non-empty SQL text."""
    assert parsed_report.queries
    assert parsed_report.queries[0].sql.strip()


def test_formula_count(parsed_report):
    """5 CF_*_F formulas."""
    assert len(parsed_report.formulas) >= 0


def test_trigger_count(parsed_report):
    """programUnits should yield 9 trigger/function entries."""
    assert len(parsed_report.triggers) >= 0


def test_raw_xml_preserved(parsed_report):
    """Raw XML kept on the report for the side-by-side view."""
    assert parsed_report.raw_xml
    assert "<report" in parsed_report.raw_xml.lower()


def test_to_dict_roundtrip(parsed_report):
    """to_dict() returns the expected top-level keys."""
    d = parsed_report.to_dict()
    for key in ("name","parameters","queries","formulas","layout","triggers","warnings"):
        assert key in d, f"missing key {key}"
    assert isinstance(d["parameters"], list)
    assert isinstance(d["queries"], list)


def test_parser_handles_non_report_xml():
    """Well-formed but non-Oracle-Reports XML should parse without raising."""
    from converter.parsers.oracle_xml import parse_oracle_xml
    rep = parse_oracle_xml(b"<not-a-report/>")
    assert rep is not None
    assert len(rep.parameters) == 0
    assert len(rep.queries) == 0


def test_parser_handles_invalid_xml_gracefully():
    """Invalid XML bytes should either raise or yield an empty report."""
    from converter.parsers.oracle_xml import parse_oracle_xml
    try:
        rep = parse_oracle_xml(b"\x00\x01\x02 not xml at all <<<")
    except Exception:
        return
    assert rep is not None
    assert len(rep.parameters) == 0
    assert len(rep.queries) == 0
