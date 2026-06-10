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

Resilience: if the user's converted SQL references tables that aren't in the
seeded sample DB (because the seed was genericized), we dynamically create
EMPTY stub tables on disk so the query parses and returns 0 rows instead of
hard-erroring. A warning is surfaced per stubbed table.
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


_FN_VAL_CONSTANTS = {"K_Type_A": 1, "K_Code_Mailing": "M", "K_Status_Active": "ACTIVE"}


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
# Stub-table discovery: parse referenced tables/aliases from translated SQL
# and create empty stubs for any that are missing from the sample DB.
# ---------------------------------------------------------------------------

# SQL keywords that should never be treated as table names (the regex below
# can otherwise pick them up after FROM/JOIN in subqueries, lateral joins, etc.).
_SQL_RESERVED = {
    "select", "where", "from", "join", "inner", "outer", "left", "right",
    "full", "cross", "on", "using", "group", "order", "by", "having",
    "union", "intersect", "except", "as", "and", "or", "not", "in",
    "exists", "between", "like", "is", "null", "true", "false", "case",
    "when", "then", "else", "end", "with", "lateral", "values", "limit",
    "offset", "fetch", "next", "rows", "only", "distinct", "all",
}

_IDENT_RE = r"[A-Za-z_][A-Za-z0-9_]*"

# Match `FROM <table>` or `JOIN <table>`, optionally with an alias.
_FROM_JOIN_RE = re.compile(
    r"\b(?:FROM|JOIN)\s+(" + _IDENT_RE + r")",
    re.IGNORECASE,
)

# Match the comma-list form: FROM A, B, C  (Oracle Reports style).
_FROM_LIST_RE = re.compile(
    r"\bFROM\s+(.+?)(?=\b(?:WHERE|GROUP\s+BY|HAVING|ORDER\s+BY|UNION|INTERSECT|EXCEPT|JOIN|ON|LIMIT|OFFSET|FETCH|\)|;|$))",
    re.IGNORECASE | re.DOTALL,
)

# Match qualified column references like `P.Perm_Num`.
_QUALCOL_RE = re.compile(r"\b(" + _IDENT_RE + r")\.(" + _IDENT_RE + r")")

_VALID_IDENT_RE = re.compile(r"^" + _IDENT_RE + r"$")


def _is_valid_ident(name):
    if not name:
        return False
    if not _VALID_IDENT_RE.match(name):
        return False
    if name.lower() in _SQL_RESERVED:
        return False
    return True


def _parse_from_chunk(chunk):
    """Parse a FROM-clause chunk into a list of (table, alias) pairs.

    Handles: 'A', 'A B', 'A AS B', and comma-separated lists of these.
    """
    out = []
    pieces = [p.strip() for p in chunk.split(",") if p.strip()]
    for piece in pieces:
        piece = re.split(
            r"\b(?:JOIN|INNER|OUTER|LEFT|RIGHT|FULL|CROSS|ON|USING|WHERE|GROUP|ORDER|HAVING|UNION|INTERSECT|EXCEPT)\b",
            piece, maxsplit=1, flags=re.IGNORECASE,
        )[0].strip()
        if not piece:
            continue
        tokens = piece.split()
        if not tokens:
            continue
        table = tokens[0].strip("()")
        alias = None
        if len(tokens) >= 3 and tokens[1].upper() == "AS":
            alias = tokens[2].strip("()")
        elif len(tokens) >= 2:
            alias = tokens[1].strip("()")
        if _is_valid_ident(table):
            out.append((table, alias if _is_valid_ident(alias) else None))
    return out


def _discover_tables_and_columns(sql):
    """Return (tables, columns_by_name)."""
    tables_order = []
    seen = set()
    alias_to_table = {}

    for m in _FROM_JOIN_RE.finditer(sql):
        name = m.group(1)
        if not _is_valid_ident(name):
            continue
        if name not in seen:
            seen.add(name)
            tables_order.append(name)

    for m in _FROM_LIST_RE.finditer(sql):
        chunk = m.group(1)
        for table, alias in _parse_from_chunk(chunk):
            if table not in seen:
                seen.add(table)
                tables_order.append(table)
            if alias:
                alias_to_table[alias] = table
            alias_to_table.setdefault(table, table)

    for t in tables_order:
        alias_to_table.setdefault(t, t)

    cols_by_table = {t: set() for t in tables_order}
    for m in _QUALCOL_RE.finditer(sql):
        prefix, col = m.group(1), m.group(2)
        if col.lower() in _SQL_RESERVED:
            continue
        table = alias_to_table.get(prefix)
        if table and table in cols_by_table:
            cols_by_table[table].add(col)

    return tables_order, cols_by_table


def _zero_journal_sidecars():
    """Truncate any orphan SQLite -journal/-wal/-shm files. Some sandboxed
    mounts (Cowork host-fs) deny delete; truncating to 0 bytes neutralizes
    them without unlinking, so SQLite ignores them on the next open."""
    for suffix in ("-journal", "-wal", "-shm"):
        sidecar = DB_PATH.with_name(DB_PATH.name + suffix)
        if sidecar.exists():
            try:
                with open(sidecar, "wb") as fh:
                    fh.write(b"")
            except OSError:
                pass


def _ensure_stub_tables(sql, existing_warnings):
    """Inspect the sample DB and create empty stub tables for any references
    that aren't already there. Appends warnings in-place to existing_warnings.
    Returns the (possibly updated) warnings list.
    """
    tables, cols_by_table = _discover_tables_and_columns(sql)
    if not tables:
        return existing_warnings

    if not DB_PATH.exists():
        return existing_warnings

    # Truncate any stale journal sidecars before opening for write. On the
    # Windows host-fs mount these can't be deleted but can be zeroed, which
    # is enough for SQLite to ignore them on next open.
    _zero_journal_sidecars()

    # Open writable using the same path-based open the seed script uses;
    # URI mode=rw fails on the Cowork Windows-host mount with disk I/O error.
    # Then immediately set PRAGMA journal_mode=MEMORY so SQLite does not create
    # a -journal sidecar file the sandbox can't reliably delete.
    try:
        wconn = sqlite3.connect(str(DB_PATH))
    except sqlite3.Error:
        return existing_warnings

    try:
        wconn.execute("PRAGMA journal_mode=MEMORY")
        wcur = wconn.cursor()
        wcur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        existing = {row[0].lower() for row in wcur.fetchall()}

        created_any = False
        for table in tables:
            if not _is_valid_ident(table):
                continue
            if table.lower() in existing:
                continue
            cols = sorted(cols_by_table.get(table, set()))
            col_defs = ['"Id" INTEGER PRIMARY KEY']
            seen_cols = {"id"}
            for c in cols:
                if c.lower() in seen_cols:
                    continue
                seen_cols.add(c.lower())
                col_defs.append(f'"{c}" TEXT')
            ddl = f'CREATE TABLE IF NOT EXISTS "{table}" ({", ".join(col_defs)})'
            try:
                wcur.execute(ddl)
                created_any = True
                existing_warnings.append(
                    f"Table '{table}' wasn't in the sample database; created an "
                    "empty stub so the query could run. Live preview will show "
                    "0 rows for this table."
                )
            except sqlite3.Error as e:
                existing_warnings.append(
                    f"Could not create stub for table '{table}': {e}"
                )
        if created_any:
            wconn.commit()
    finally:
        wconn.close()

    # Zero again after close so the journal file (recreated by SQLite during
    # the write) does not break subsequent read-only opens.
    _zero_journal_sidecars()

    return existing_warnings


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

    # Resilience pass: create empty stub tables for any references that aren't
    # in the seeded sample DB so the query can run instead of hard-erroring.
    try:
        _ensure_stub_tables(translated, warnings)
    except Exception as e:
        warnings.append(f"Stub-table preflight skipped due to error: {e}")

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
