"""Tests for the PL/SQL -> T-SQL translator (converter.translators.plsql_to_tsql)."""
from __future__ import annotations

import pytest


def test_decode_to_case():
    from converter.translators.plsql_to_tsql import translate_sql
    out, _ = translate_sql("SELECT DECODE(status, 1, 'A', 2, 'B', 'X') FROM t")
    up = out.upper()
    assert "CASE" in up
    assert "WHEN" in up
    # DECODE keyword should not appear in the rewritten SQL
    assert "DECODE(" not in up


def test_nvl_to_isnull():
    from converter.translators.plsql_to_tsql import translate_sql
    out, _ = translate_sql("SELECT NVL(a, 0) FROM t")
    assert "ISNULL(" in out.upper()
    assert "NVL(" not in out.upper()


def test_nvl2_to_case():
    from converter.translators.plsql_to_tsql import translate_sql
    out, _ = translate_sql("SELECT NVL2(a, 'yes', 'no') FROM t")
    up = out.upper()
    assert "CASE" in up and "WHEN" in up
    assert "IS NOT NULL" in up
    assert "NVL2(" not in up


def test_to_char_to_format():
    from converter.translators.plsql_to_tsql import translate_sql
    out, _ = translate_sql("SELECT TO_CHAR(d, 'YYYY-MM-DD') FROM t")
    up = out.upper()
    assert "FORMAT(" in up
    assert "TO_CHAR(" not in up
    # YYYY -> yyyy in .NET format string
    assert "yyyy" in out


def test_outer_join_plus_to_left_join():
    from converter.translators.plsql_to_tsql import translate_sql
    sql = "SELECT a.x, b.y FROM a, b WHERE a.id = b.aid(+)"
    out, warns = translate_sql(sql)
    up = out.upper()
    # The (+) marker should not survive
    assert "(+)" not in out
    # It should look like a join now (LEFT JOIN or LEFT OUTER JOIN), or at
    # minimum carry a warning describing the rewrite.
    rewrote = "LEFT JOIN" in up or "LEFT OUTER JOIN" in up
    warned = any("(+)" in w or "outer" in w.lower() for w in warns)
    assert rewrote or warned, (
        f"Expected (+) handling, got SQL={out!r} warns={warns!r}"
    )


def test_bind_var_substitution():
    from converter.translators.plsql_to_tsql import translate_sql
    out, _ = translate_sql("SELECT * FROM t WHERE x = :P_RENEWAL_YEAR")
    assert "@P_RENEWAL_YEAR" in out
    # Original :P_ form should be gone
    assert ":P_RENEWAL_YEAR" not in out


def test_sysdate_to_getdate():
    from converter.translators.plsql_to_tsql import translate_sql
    out, _ = translate_sql("SELECT SYSDATE FROM dual")
    assert "GETDATE()" in out.upper()


def test_substr_to_substring():
    from converter.translators.plsql_to_tsql import translate_sql
    out, _ = translate_sql("SELECT SUBSTR(name, 1, 3) FROM t")
    assert "SUBSTRING(" in out.upper()
    assert "SUBSTR(" not in out.upper()


def test_instr_to_charindex():
    from converter.translators.plsql_to_tsql import translate_sql
    out, _ = translate_sql("SELECT INSTR(name, 'x') FROM t")
    up = out.upper()
    assert "CHARINDEX(" in up
    assert "INSTR(" not in up


def test_pipe_concat_to_plus():
    from converter.translators.plsql_to_tsql import translate_sql
    out, _ = translate_sql("SELECT first_name || ' ' || last_name FROM t")
    # Pipe-concat should be replaced with the T-SQL '+' operator
    assert "||" not in out


def test_translate_returns_tuple():
    from converter.translators.plsql_to_tsql import translate_sql
    result = translate_sql("SELECT 1 FROM t")
    assert isinstance(result, tuple) and len(result) == 2
    sql_out, warns = result
    assert isinstance(sql_out, str)
    assert isinstance(warns, list)


def test_translate_report_populates_tsql(translated_report):
    """translate_report should fill .tsql for every query that had SQL."""
    for q in translated_report.queries:
        if q.sql:
            assert q.tsql, f"Query {q.name} has no T-SQL"


def test_translate_report_idempotentish(translated_report):
    """Calling translate_report a second time should not destroy .tsql."""
    from converter.translators.plsql_to_tsql import translate_report
    before = [(q.name, q.tsql) for q in translated_report.queries]
    translate_report(translated_report)
    after = [(q.name, q.tsql) for q in translated_report.queries]
    # T-SQL text may be regenerated identically, but every query should still
    # have non-empty T-SQL.
    for (name, t) in after:
        assert t, f"{name} lost its T-SQL on second translate"
