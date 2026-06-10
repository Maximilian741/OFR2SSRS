"""Regression lock-in for the synthetic formula-resolution dataset.

Oracle Reports computes CF_* / CP_* columns in PL/SQL; SSRS has no
formula construct, so the generator used to emit =Nothing and the
values rendered blank (e.g. a certificate's bureau-chief name block).

The generator now emits one DataSet -- DS_REPORT_FORMULAS -- carrying
every formula/placeholder column. The user wires it up like any other
dataset: point the shared DataSource at the database and Refresh
Fields. These tests pin the dataset's shape; everything is generic
(driven by report.formulas, never by column names).
"""
from __future__ import annotations

from converter.generators.rdl import (
    _FORMULA_DATASET_NAME,
    _build_formula_dataset,
    _formula_dataset_columns,
)

RDL_NS = "{http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition}"


class _Formula:
    def __init__(self, name, plsql_body=""):
        self.name, self.plsql_body = name, plsql_body


class _Report:
    def __init__(self, formulas):
        self.formulas = formulas


def test_columns_list_formula_and_placeholder_names():
    rep = _Report([_Formula("CF_CHIEF"), _Formula("CP_ADDR")])
    assert _formula_dataset_columns(rep) == ["CF_CHIEF", "CP_ADDR"]


def test_columns_exclude_summary_aggregates():
    # <summary> columns are aggregates -> SSRS aggregate expr, not a field.
    rep = _Report([
        _Formula("CF_CHIEF"),
        _Formula("CS_TOTAL", "Oracle <summary function='sum' source='AMT'>"),
    ])
    assert _formula_dataset_columns(rep) == ["CF_CHIEF"]


def test_columns_are_deduplicated():
    rep = _Report([_Formula("CF_X"), _Formula("cf_x")])
    assert _formula_dataset_columns(rep) == ["CF_X"]


def test_no_dataset_when_report_has_no_formulas():
    assert _build_formula_dataset(_Report([])) is None


def test_dataset_has_a_field_per_formula_column():
    rep = _Report([_Formula("CF_CHIEF"), _Formula("CP_ADDR")])
    ds = _build_formula_dataset(rep, target_db="oracle")
    assert ds is not None
    assert ds.get("Name") == _FORMULA_DATASET_NAME
    fields = ds.find(RDL_NS + "Fields")
    names = [f.get("Name") for f in fields]
    assert names == ["CF_CHIEF", "CP_ADDR"]


def test_oracle_commandtext_is_a_runnable_select():
    rep = _Report([_Formula("CF_CHIEF"), _Formula("CP_ADDR")])
    ds = _build_formula_dataset(rep, target_db="oracle")
    cmd = ds.find(RDL_NS + "Query").findtext(RDL_NS + "CommandText")
    assert "SELECT" in cmd and "FROM DUAL" in cmd
    # every column is present so Refresh Fields surfaces them all
    assert "CF_CHIEF" in cmd and "CP_ADDR" in cmd


def test_sqlserver_commandtext_omits_from_dual():
    rep = _Report([_Formula("CF_CHIEF")])
    ds = _build_formula_dataset(rep, target_db="sqlserver")
    cmd = ds.find(RDL_NS + "Query").findtext(RDL_NS + "CommandText")
    assert "DUAL" not in cmd, "SQL Server has no DUAL table"
