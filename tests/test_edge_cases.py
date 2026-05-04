"""Edge-case tests for the Oracle XML parser and PL/SQL -> T-SQL translator.

These tests focus on inputs that are well-formed XML but exercise the
parser/translator at the boundaries of what the hackathon sample exposes.
The goal is "no crashes" -- translation may emit warnings, but parse
should always return a ParsedReport and translate_report should never
raise.
"""
from __future__ import annotations

import pytest

from converter.parsers.oracle_xml import parse_oracle_xml
from converter.translators.plsql_to_tsql import translate_report
from converter.models import ParsedReport


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_and_translate(xml: bytes) -> ParsedReport:
    rep = parse_oracle_xml(xml)
    assert isinstance(rep, ParsedReport)
    # translate_report must NOT raise on any well-formed input
    translate_report(rep)
    return rep


def _wrap_report(body: str = "", name: str = "TEST", dtd: str = "9.0.2.0.10") -> bytes:
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<report name="{name}" DTDVersion="{dtd}">\n'
        f'{body}\n'
        '</report>\n'
    )
    return xml.encode("utf-8")


# ---------------------------------------------------------------------------
# Empty / minimal documents
# ---------------------------------------------------------------------------

def test_empty_xml_body_no_report_root():
    """Well-formed XML with a non-<report> root should not crash."""
    xml = b'<?xml version="1.0" encoding="UTF-8"?><emptydoc/>'
    rep = _parse_and_translate(xml)
    # name comes from root @name, which doesn't exist on <emptydoc/>
    assert rep.name == ""
    assert rep.parameters == []
    assert rep.queries == []
    assert rep.layout == []


def test_report_root_only_no_data_or_layout():
    """<report> with no <data> or <layout> children parses cleanly."""
    xml = _wrap_report("")
    rep = _parse_and_translate(xml)
    assert rep.name == "TEST"
    assert rep.dtd_version == "9.0.2.0.10"
    assert rep.parameters == []
    assert rep.queries == []
    assert rep.formulas == []
    assert rep.layout == []
    assert rep.triggers == []


def test_report_with_empty_data_section():
    """<report><data/></report> has zero queries and zero parameters."""
    xml = _wrap_report("<data></data>")
    rep = _parse_and_translate(xml)
    assert rep.queries == []
    assert rep.parameters == []


def test_parameters_but_zero_queries():
    """A report with parameters but no <dataSource> elements."""
    body = """
    <data>
      <userParameter name="P_YEAR" datatype="number" width="4"/>
      <userParameter name="P_NAME" datatype="character" width="30"/>
    </data>
    """
    xml = _wrap_report(body)
    rep = _parse_and_translate(xml)
    assert len(rep.parameters) == 2
    assert rep.queries == []
    assert {p.name for p in rep.parameters} == {"P_YEAR", "P_NAME"}


def test_one_query_zero_items():
    """A <dataSource> whose <group> has no <dataItem> children."""
    body = """
    <data>
      <dataSource name="Q_EMPTY">
        <select>SELECT 1 FROM DUAL</select>
        <group name="G_EMPTY"/>
      </dataSource>
    </data>
    """
    xml = _wrap_report(body)
    rep = _parse_and_translate(xml)
    assert len(rep.queries) == 1
    assert rep.queries[0].name == "Q_EMPTY"
    assert rep.queries[0].items == []
    # translation should still have produced *some* tsql string
    assert isinstance(rep.queries[0].tsql, str)


# ---------------------------------------------------------------------------
# Parameter name oddities
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("pname", [
    "P_" + "X" * 200,                 # very long
    "P_1",
    "P_",
    "P_FOO_BAR_BAZ_QUX",
    "P_FOO_BAR_BAZ_QUX_QUUX_CORGE",
])
def test_weird_parameter_names(pname):
    body = f'<data><userParameter name="{pname}" datatype="character"/></data>'
    xml = _wrap_report(body)
    rep = _parse_and_translate(xml)
    assert len(rep.parameters) == 1
    assert rep.parameters[0].name == pname
    # ssrs_datatype must still resolve to a known string
    assert rep.parameters[0].ssrs_datatype in {"String", "Integer", "DateTime"}


def test_parameter_label_with_unicode():
    body = (
        '<data>'
        '<userParameter name="P_NAME" label="Café Owner" '
        'datatype="character"/>'
        '</data>'
    )
    xml = _wrap_report(body)
    rep = _parse_and_translate(xml)
    assert rep.parameters[0].label == "Café Owner"


# ---------------------------------------------------------------------------
# SQL oddities
# ---------------------------------------------------------------------------

def test_sql_with_deeply_nested_parens():
    """SQL with deeply nested parens shouldn't blow the translator."""
    nested = "(" * 40 + "1" + ")" * 40
    sql = f"SELECT {nested} FROM DUAL"
    body = f"""
    <data>
      <dataSource name="Q_NEST">
        <select>{sql}</select>
        <group name="G">
          <dataItem name="C1" datatype="number"/>
        </group>
      </dataSource>
    </data>
    """
    xml = _wrap_report(body)
    rep = _parse_and_translate(xml)
    assert len(rep.queries) == 1
    q = rep.queries[0]
    assert q.sql == sql
    # T-SQL output should contain the same nested parens balance
    assert q.tsql.count("(") == q.tsql.count(")")


def test_sql_with_unicode_column_names():
    """Unicode in identifiers should round-trip without crashing."""
    sql = "SELECT café_id, naïve_score FROM café_table"
    body = f"""
    <data>
      <dataSource name="Q_UNI">
        <select>{sql}</select>
        <group name="G">
          <dataItem name="café_id" datatype="number"/>
        </group>
      </dataSource>
    </data>
    """
    xml = _wrap_report(body)
    rep = _parse_and_translate(xml)
    assert len(rep.queries) == 1
    q = rep.queries[0]
    # Unicode should survive the parse
    assert "café_id" in q.sql
    # Translator may or may not preserve it byte-for-byte, but must not blow up
    assert isinstance(q.tsql, str)


def test_sql_empty_string():
    body = """
    <data>
      <dataSource name="Q_EMPTYSQL">
        <select></select>
        <group name="G"/>
      </dataSource>
    </data>
    """
    xml = _wrap_report(body)
    rep = _parse_and_translate(xml)
    assert rep.queries[0].sql == ""
    assert isinstance(rep.queries[0].tsql, str)


# ---------------------------------------------------------------------------
# DTD version variants
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("dtd", [
    "9.0.2.0.10",
    "11.0",
    "11.1.2.0.0",
    "12.2.1.4",
    "",
    "not-a-version",
])
def test_dtd_version_variants(dtd):
    xml = _wrap_report("", dtd=dtd)
    rep = _parse_and_translate(xml)
    assert rep.dtd_version == dtd


# ---------------------------------------------------------------------------
# Report-name oddities
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("rname", [
    "report with spaces",
    "report/with/slashes",
    "report.with.dots",
    "report-with-dashes",
    "MIXED_Case.Report 01",
    "",
])
def test_report_name_oddities(rname):
    xml = _wrap_report("", name=rname)
    rep = _parse_and_translate(xml)
    assert rep.name == rname
    # raw_xml and warnings should still be present (warnings may be empty list)
    assert isinstance(rep.warnings, list)


# ---------------------------------------------------------------------------
# Translation never raises, even with empty / odd content
# ---------------------------------------------------------------------------

def test_translate_report_on_empty_report():
    rep = ParsedReport(name="empty")
    # Should not raise even with zero queries / formulas
    translate_report(rep)
    assert rep.name == "empty"


def test_parse_returns_warnings_list_always():
    """Even pathological inputs should return a list .warnings (never None)."""
    xml = _wrap_report("")
    rep = parse_oracle_xml(xml)
    assert rep.warnings is not None
    assert isinstance(rep.warnings, list)
