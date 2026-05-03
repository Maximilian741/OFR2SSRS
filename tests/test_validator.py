"""Tests for the T-SQL validator (converter.validators.tsql_check)."""
from __future__ import annotations

import pytest


def test_validate_tsql_returns_list():
    from converter.validators.tsql_check import validate_tsql
    out = validate_tsql("SELECT 1")
    assert isinstance(out, list)


def test_validate_tsql_clean_query_has_no_errors():
    from converter.validators.tsql_check import validate_tsql
    issues = validate_tsql("SELECT a, b FROM dbo.foo WHERE x = @P_X")
    severities = {i.get("severity") for i in issues}
    assert "error" not in severities, f"Unexpected errors: {issues}"


def test_validate_tsql_flags_decode():
    from converter.validators.tsql_check import validate_tsql
    issues = validate_tsql("SELECT DECODE(x, 1, 'a') FROM t")
    rules = {i["rule"] for i in issues}
    assert "oracle.decode" in rules


def test_validate_tsql_flags_nvl():
    from converter.validators.tsql_check import validate_tsql
    issues = validate_tsql("SELECT NVL(a, 0) FROM t")
    rules = {i["rule"] for i in issues}
    assert "oracle.nvl" in rules


def test_validate_tsql_flags_sysdate():
    from converter.validators.tsql_check import validate_tsql
    issues = validate_tsql("SELECT SYSDATE FROM t")
    rules = {i["rule"] for i in issues}
    assert "oracle.sysdate" in rules


def test_validate_tsql_flags_dual():
    from converter.validators.tsql_check import validate_tsql
    issues = validate_tsql("SELECT 1 FROM DUAL")
    rules = {i["rule"] for i in issues}
    assert "oracle.dual" in rules


def test_validate_tsql_flags_outer_join_plus():
    from converter.validators.tsql_check import validate_tsql
    issues = validate_tsql("SELECT a.x FROM a, b WHERE a.id = b.id(+)")
    # Some rule must catch surviving (+) syntax (rule name varies but should
    # contain 'outer' or 'plus').
    msgs = " ".join(i["message"].lower() for i in issues)
    rules = " ".join(i["rule"].lower() for i in issues)
    assert "(+)" in msgs or "outer" in msgs or "outer" in rules or "plus" in rules


def test_validate_tsql_each_issue_has_required_keys():
    from converter.validators.tsql_check import validate_tsql
    issues = validate_tsql("SELECT NVL(a,0), DECODE(x,1,2) FROM DUAL")
    assert issues, "expected several issues"
    required = {"severity", "message", "rule", "scope"}
    for i in issues:
        missing = required - set(i.keys())
        assert not missing, f"issue missing keys {missing}: {i}"
        assert i["severity"] in ("error", "warning", "info")


def test_validate_report_returns_list(translated_report):
    from converter.validators.tsql_check import validate_report
    issues = validate_report(translated_report)
    assert isinstance(issues, list)


def test_validate_report_runs_clean_after_translation(translated_report):
    """After translation, the sample report should have no Oracle leftover errors."""
    from converter.validators.tsql_check import validate_report
    issues = validate_report(translated_report)
    # Translation should have removed DECODE/NVL/etc., so we should see no
    # error-severity issues for those Oracle-leftover rules.
    leftover_errors = [
        i for i in issues
        if i.get("severity") == "error"
        and i.get("rule", "").startswith("oracle.")
    ]
    assert not leftover_errors, (
        f"Translator left Oracle-only constructs in T-SQL: {leftover_errors[:3]}"
    )


def test_validate_report_handles_empty_report():
    from converter.validators.tsql_check import validate_report
    from converter.models import ParsedReport
    rep = ParsedReport(name="EMPTY")
    issues = validate_report(rep)
    assert isinstance(issues, list)
