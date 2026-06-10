"""The report is UNTRUSTED. Report/parameter/column names flow into the
generated burst PowerShell + T-SQL artifacts; a hostile name must not be able
to close a PS hashtable or break out of a SQL string/comment and inject code
that later runs on the customer's host. Regression guard for that class.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter.bursting import (  # noqa: E402
    build_burst_query,
    build_powershell_dds_script,
    build_email_burst_query,
)


class _P:
    def __init__(self, name, label="", display=True):
        self.name = name
        self.label = label
        self.display = display


class _R:
    def __init__(self, name, params=None, queries=None):
        self.name = name
        self.parameters = params or []
        self.queries = queries or []


def test_powershell_param_name_cannot_close_hashtable():
    hostile = 'X = $null }\n    Invoke-Expression "calc" # '
    rep = _R("Rpt", params=[_P(hostile, label="L")])
    ps = build_powershell_dds_script(rep, {}, "report.rdl")
    block = ps.split("$ReportParameters = @{", 1)[1].split("}", 1)[0]
    # The hostile name is reduced to a clean identifier on ONE line -- the
    # injected brace, newline, quote, and the Invoke-Expression *cmdlet* (which
    # has a hyphen the sanitizer strips) cannot appear inside the hashtable.
    assert '"' not in block
    assert "Invoke-Expression" not in block
    assert "\n    Invoke" not in block


def test_powershell_report_name_is_escaped():
    rep = _R('R"X', params=[_P("P_X", label="L")])
    ps = build_powershell_dds_script(rep, {}, "report.rdl")
    assert 'R`"X' in ps      # the embedded double-quote is backtick-escaped
    assert 'R"X' not in ps    # ...so the raw, string-terminating form is gone


def test_sql_filename_pattern_cannot_break_literal():
    rep = _R("Rpt", queries=[])
    info = {"burst_key_field": "Perm_Num",
            "filename_pattern": "x'); DROP TABLE Users"}
    sql = build_burst_query(rep, info)
    assert "x''); DROP TABLE Users" in sql  # the quote is DOUBLED -> stays in the literal
    assert "'x');" not in sql                # ...so the single-quote break-out never forms


def test_sql_burst_key_is_identifier_sanitized():
    rep = _R("Rpt")
    info = {"burst_key_field": "id; DROP TABLE x --"}
    sql = build_burst_query(rep, info)
    assert "DROP TABLE" not in sql


def test_email_burst_query_report_name_no_newline_breakout():
    rep = _R("Line1\n-- evil\nLine2")
    sql = build_email_burst_query(rep, {})
    # report name collapses to one line; no injected -- comment line survives
    assert "\n-- evil" not in sql
