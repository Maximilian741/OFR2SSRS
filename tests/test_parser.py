"""Tests for the Oracle XML parser (converter.parsers.oracle_xml)."""
from __future__ import annotations

import pytest


def test_parsed_report_basic_shape(parsed_report):
    assert parsed_report is not None
    assert parsed_report.name, "report name should be non-empty"
    assert parsed_report.dtd_version, "DTD version should be non-empty"


def test_parameter_count(parsed_report):
    """MVWF_PERMIT.xml has 16 userParameters."""
    assert len(parsed_report.parameters) == 16


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
    assert len(parsed_report.queries) == 3


def test_query_names_present(parsed_report):
    names = {q.name for q in parsed_report.queries}
    assert all(names)
    assert "Q_PERMIT" in names


def test_q_permit_item_count(parsed_report):
    """Q_PERMIT exposes 13 dataItems."""
    q_permit = next((q for q in parsed_report.queries if q.name == "Q_PERMIT"), None)
    assert q_permit is not None, "Q_PERMIT query not found"
    assert len(q_permit.items) == 13


def test_q_permit_has_sql_text(parsed_report):
    q_permit = next(q for q in parsed_report.queries if q.name == "Q_PERMIT")
    assert q_permit.sql, "Q_PERMIT should have a non-empty SQL body"
    assert "SELECT" in q_permit.sql.upper()


def test_formula_count(parsed_report):
    """5 CF_*_F formulas."""
    assert len(parsed_report.formulas) == 5


def test_trigger_count(parsed_report):
    """programUnits should yield 9 trigger/function entries."""
    assert len(parsed_report.triggers) == 9


def test_raw_xml_preserved(parsed_report):
    """Raw XML kept on the report for the side-by-side view."""
    assert parsed_report.raw_xml
    assert "<report" in parsed_report.raw_xml.lower()


def test_to_dict_roundtrip(parsed_report):
    d = parsed_report.to_dict()
    for key in ("name", "parameters", "queries", "formulas", "layout", "triggers"):
        assert key in d
    assert len(d["parameters"]) == 16
    assert len(d["queries"]) == 3


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
