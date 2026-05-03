"""
Live preview engine for the Oracle->SSRS converter.

Runs translated T-SQL against the seeded SQLite sample database. Because T-SQL
doesn't run natively in SQLite, we:
  1. Lightly rewrite a handful of T-SQL constructs into SQLite syntax.
  2. Register Python UDFs on the connection that mirror SQL Server scalar
     functions and the Agent-2 dbo.fn_* package stubs.
  3. Translate @P_FOO bind params into SQLite-style :P_FOO and pass the dict
     straight through.

Public API:
    run_query(sql, parameters) -> (rows, columns, warnings)

Read-only: the connection is opened with mode=ro via a file: URI so the engine
cannot mutate the sample database.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple


DB_PATH = Path(__file__).resolve().parents[2] / "db" / "sample.sqlite"


# ---------------------------------------------------------------------------
# Python UDFs that mirror SQL Server scalar functions / dbo.fn_* package
# ---------------------------------------------------------------------------

def _safe(s):
    return "" if s is None else str(s)


def fn_FORMAT_DATE_LONG(d):
    if d is None or d == "":
        return ""
    s = str(d)
    fmts = ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%m/%d/%Y", "%Y/%m/%d")
    for f in fmts:
        try:
            return datetime.strptime(s[:19] if " " in s else s[:10], f).strftime("%B %d, %Y")
        except ValueError:
            continue
    return s


def fn_F_Format_Address(strt1, strt2, city, st, zip_, zip4, *opts):
    parts = []
    line = " ".join(p for p in (_safe(strt1), _safe(strt2)) if p.strip())
    if line.strip():
        parts.append(line.strip())
    if _safe(city).strip():
        parts.append(_safe(city).strip())
    state_zip = _safe(st).strip()
    z = _safe(zip_).strip()
    z4 = _safe(zip4).strip()
    if z and z4:
        z = f"{z}-{z4}"
    if state_zip and z:
        parts.append(f"{state_zip} {z}")
    elif state_zip:
        parts.append(state_zip)
    elif z:
        parts.append(z)
    return ", ".join(parts)


def fn_F_Format_Org_Name(busn, frst, mi, lst, fmt=None):
    if _safe(busn).strip():
        return _safe(busn).strip()
    last = _safe(lst).strip()
    first = _safe(frst).strip()
    middle = _safe(mi).strip()
    if last and first:
        if middle:
            return f"{last}, {first} {middle}"
        return f"{last}, {first}"
    return first or last or ""


def fn_F_Get_Permittees(perm_num, prog_id=None, eff_date=None, fmt=None, delim="; "):
    return f"Doe, John{delim}Smith, Jane"


def fn_F_To_Phone(num):
    digits = re.sub(r"\D", "", _safe(num))
    if len(digits) == 10:
        return f"({digits[0:3]}) {digits[3:6]}-{digits[6:10]}"
    return _safe(num)


def fn_F_Get_Rep_Description(name, prog_id=None):
    return f"{_safe(name)} Permit".strip()


def fn_F_Get_Perm_Name(perm_num):
    return _safe(perm_num)


_FN_VAL_CONSTANTS = {"K_Prog_Id_JV": 1, "K_AdTyp_Mailing": "M", "K_Stat_JVA": "JVA"}


def fn_F_Val(name):
    return _FN_VAL_CONSTANTS.get(_safe(name), None)


def fn_F_System_Parm_Char(name):
    return "JOHN DOE"


# ---------------------------------------------------------------------------
# T-SQL -> SQLite light translation
# ---------------------------------------------------------------------------

_FORMAT_LONG_RE = re.compile(r"FORMAT\s*\(\s*([^,]+?)\s*,\s*'MMMM\s+dd,\s*yyyy'\s*\)", re.IGNORECASE)
_FORMAT_YYYY_RE = re.compile(r"FORMAT\s*\(\s*([^,]+?)\s*,\s*'yyyy'\s*\)", re.IGNORECASE)
_FORMAT_MDY_RE = re.compile(r"FORMAT\s*\(\s*([^,]+?)\s*,\s*'MM/dd/yyyy'\s*\)", re.IGNORECASE)
_ISNULL_RE = re.compile(r"\bISNULL\s*\(", re.IGNORECASE)
_GETDATE_RE = re.compile(r"\bGETDATE\s*\(\s*\)", re.IGNORECASE)
_TRY_CONVERT_DATE_RE = re.compile(r"\bTRY_CONVERT\s*\(\s*date\s*,\s*([^)]+?)\s*\)", re.IGNORECASE)
_CONVERT_DATE_101_RE = re.compile(r"\bCONVERT\s*\(\s*date\s*,\s*([^,)]+?)\s*,\s*101\s*\)", re.IGNORECASE)
_CHARINDEX_RE = re.compile(r"\bCHARINDEX\s*\(\s*([^,]+?)\s*,\s*([^)]+?)\s*\)", re.IGNORECASE)
_DBO_RE = re.compile(r"\bdbo\.(?=fn_)", re.IGNORECASE)
_GLUE_KEYWORD_RE = re.compile(r"(\w)(GROUP\s+BY|HAVING|WHERE|ORDER\s+BY|UNION)")
_PLUS_CONCAT_RE = re.compile(r"(?<=\S)\s+\+\s+(?=\S)")
_PARAM_RE = re.compile(r"(?<![\w@])@(P_[A-Za-z0-9_]+)")


def _translate(sql):
    warnings = []
    s = sql

    if _FORMAT_LONG_RE.search(s):
        s = _FORMAT_LONG_RE.sub(r"FN_FORMAT_DATE_LONG(\1)", s)
        warnings.append("FORMAT(...,'MMMM dd, yyyy') routed through FN_FORMAT_DATE_LONG UDF")

    if _FORMAT_YYYY_RE.search(s):
        s = _FORMAT_YYYY_RE.sub(r"STRFTIME('%Y', \1)", s)
        warnings.append("FORMAT(...,'yyyy') rewritten as STRFTIME('%Y', ...)")

    if _FORMAT_MDY_RE.search(s):
        s = _FORMAT_MDY_RE.sub(r"STRFTIME('%m/%d/%Y', \1)", s)
        warnings.append("FORMAT(...,'MM/dd/yyyy') rewritten as STRFTIME('%m/%d/%Y', ...)")

    if _ISNULL_RE.search(s):
        s = _ISNULL_RE.sub("COALESCE(", s)
        warnings.append("Function ISNULL substituted with COALESCE")

    if _GETDATE_RE.search(s):
        s = _GETDATE_RE.sub("DATE('now')", s)
        warnings.append("GETDATE() substituted with DATE('now')")

    if _TRY_CONVERT_DATE_RE.search(s):
        s = _TRY_CONVERT_DATE_RE.sub(r"DATE(\1)", s)
        warnings.append("TRY_CONVERT(date, ...) rewritten as DATE(...)")

    if _CONVERT_DATE_101_RE.search(s):
        s = _CONVERT_DATE_101_RE.sub(r"DATE(\1)", s)
        warnings.append("CONVERT(date, ..., 101) rewritten as DATE(...)")

    if _CHARINDEX_RE.search(s):
        s = _CHARINDEX_RE.sub(r"INSTR(\2, \1)", s)
        warnings.append("CHARINDEX(sub, s) rewritten as INSTR(s, sub)")

    if _DBO_RE.search(s):
        s = _DBO_RE.sub("", s)
        warnings.append("dbo.* schema prefix stripped (SQLite has no schemas)")

    if _GLUE_KEYWORD_RE.search(s):
        s = _GLUE_KEYWORD_RE.sub(r"\1\n\2", s)
        warnings.append("Inserted whitespace before clause keywords merged by translator")

    # T-SQL '+' string concat -> SQLite '||'. SQLite's '+' coerces to numeric so
    # any visible string concat ends up as 0. For the live preview we just rewrite
    # every standalone ' + ' to ' || '; numeric expressions in projection lists are
    # rare in this codebase and would surface as warnings if hit.
    if _PLUS_CONCAT_RE.search(s):
        s = _PLUS_CONCAT_RE.sub(" || ", s)
        warnings.append("T-SQL '+' string concat rewritten as '||' for SQLite")

    if _PARAM_RE.search(s):
        s = _PARAM_RE.sub(r":\1", s)
        warnings.append("T-SQL @P_* parameters rebound as SQLite :P_* placeholders")

    warnings.insert(0, "Translated T-SQL adapted for SQLite")
    return s, warnings


# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------

def _open_readonly():
    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"Sample database not found at {DB_PATH}. "
            "Run backend/db/seed_sample_db.py first."
        )
    uri = f"file:{DB_PATH.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    _register_udfs(conn)
    return conn


def _register_udfs(conn):
    conn.create_function("FN_FORMAT_DATE_LONG", 1, fn_FORMAT_DATE_LONG)
    conn.create_function("fn_FORMAT_DATE_LONG", 1, fn_FORMAT_DATE_LONG)
    for n in (6, 7, 8):
        conn.create_function("fn_F_Format_Address", n, fn_F_Format_Address)
    for n in (4, 5):
        conn.create_function("fn_F_Format_Org_Name", n, fn_F_Format_Org_Name)
    for n in (1, 2, 3, 4, 5):
        conn.create_function("fn_F_Get_Permittees", n, fn_F_Get_Permittees)
    conn.create_function("fn_F_To_Phone", 1, fn_F_To_Phone)
    conn.create_function("fn_F_Get_Rep_Description", 1, fn_F_Get_Rep_Description)
    conn.create_function("fn_F_Get_Rep_Description", 2, fn_F_Get_Rep_Description)
    conn.create_function("fn_F_Get_Perm_Name", 1, fn_F_Get_Perm_Name)
    conn.create_function("fn_F_Val", 1, fn_F_Val)
    conn.create_function("fn_F_System_Parm_Char", 1, fn_F_System_Parm_Char)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_query(sql, parameters):
    """Execute translated T-SQL against the sample SQLite DB.

    Returns (rows, columns, warnings).
    """
    translated, warnings = _translate(sql or "")
    conn = _open_readonly()
    try:
        cur = conn.cursor()
        try:
            cur.execute(translated, parameters or {})
        except sqlite3.Error as e:
            warnings.append(f"SQLite execution error: {e}")
            return [], [], warnings

        columns = [d[0] for d in cur.description] if cur.description else []
        raw_rows = cur.fetchall()
        rows = [{col: row[col] for col in columns} for row in raw_rows]
        return rows, columns, warnings
    finally:
        conn.close()
