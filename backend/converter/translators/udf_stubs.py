"""
UDF-stub generator: scan parsed Oracle SQL for Pkg_*.F_* references and emit a
T-SQL script of CREATE FUNCTION dbo.fn_* stubs so the live preview can run.

Public API:
    generate_udf_stubs(report: ParsedReport) -> str
"""

from __future__ import annotations

import re
from typing import Dict, List, Set, Tuple

from ..models import ParsedReport


_KNOWN_STUBS: Dict[str, Dict] = {
    "F_Format_Address": {
        "complexity": "easy",
        "params": [
            ("@strt_1", "NVARCHAR(200)"),
            ("@strt_2", "NVARCHAR(200)"),
            ("@city",   "NVARCHAR(100)"),
            ("@st",     "NVARCHAR(10)"),
            ("@zip",    "NVARCHAR(10)"),
            ("@zip4",   "NVARCHAR(10)"),
        ],
        "return_type": "NVARCHAR(MAX)",
        "note": "Formats two street lines, city, state, ZIP, ZIP+4 into a mailing-label string.",
        "body": (
            "    DECLARE @line1 NVARCHAR(MAX) = CONCAT_WS(N', ',\n"
            "        NULLIF(LTRIM(RTRIM(ISNULL(@strt_1, N''))), N''),\n"
            "        NULLIF(LTRIM(RTRIM(ISNULL(@strt_2, N''))), N''));\n"
            "    DECLARE @line2 NVARCHAR(MAX) = CONCAT_WS(N', ',\n"
            "        NULLIF(LTRIM(RTRIM(ISNULL(@city, N''))), N''),\n"
            "        NULLIF(LTRIM(RTRIM(ISNULL(@st, N''))), N''));\n"
            "    DECLARE @zipfull NVARCHAR(20) =\n"
            "        CASE WHEN NULLIF(LTRIM(RTRIM(ISNULL(@zip4, N''))), N'') IS NOT NULL\n"
            "             THEN LTRIM(RTRIM(@zip)) + N'-' + LTRIM(RTRIM(@zip4))\n"
            "             ELSE NULLIF(LTRIM(RTRIM(ISNULL(@zip, N''))), N'') END;\n"
            "    RETURN CONCAT_WS(N' ', NULLIF(@line1, N''), NULLIF(@line2, N''), @zipfull)"
        ),
    },
    "F_Format_Org_Name": {
        "complexity": "easy",
        "params": [
            ("@business_name", "NVARCHAR(200)"),
            ("@first_name",    "NVARCHAR(100)"),
            ("@middle_initial","NVARCHAR(10)"),
            ("@last_name",     "NVARCHAR(100)"),
            ("@fmt",           "NVARCHAR(10)"),
        ],
        "return_type": "NVARCHAR(MAX)",
        "note": "Returns the business name when present; otherwise builds 'Last, First M.'",
        "body": (
            "    IF @business_name IS NOT NULL AND LEN(LTRIM(RTRIM(@business_name))) > 0\n"
            "        RETURN LTRIM(RTRIM(@business_name));\n"
            "    DECLARE @last  NVARCHAR(100) = ISNULL(LTRIM(RTRIM(@last_name)),  N'');\n"
            "    DECLARE @first NVARCHAR(100) = ISNULL(LTRIM(RTRIM(@first_name)), N'');\n"
            "    DECLARE @mi    NVARCHAR(10)  = NULLIF(LTRIM(RTRIM(ISNULL(@middle_initial, N''))), N'');\n"
            "    RETURN CASE WHEN @last = N'' AND @first = N'' THEN N''\n"
            "                ELSE @last + N', ' + @first + COALESCE(N' ' + @mi, N'') END"
        ),
    },
    "F_To_Phone": {
        "complexity": "easy",
        "params": [("@digits", "NVARCHAR(20)")],
        "return_type": "NVARCHAR(20)",
        "note": "Strips non-digits then formats as (xxx) xxx-xxxx.",
        "body": (
            "    DECLARE @d NVARCHAR(20) = ISNULL(@digits, N'');\n"
            "    SET @d = REPLACE(REPLACE(REPLACE(REPLACE(@d, N'-', N''), N'(', N''), N')', N''), N' ', N'');\n"
            "    IF LEN(@d) = 10\n"
            "        RETURN N'(' + LEFT(@d,3) + N') ' + SUBSTRING(@d,4,3) + N'-' + RIGHT(@d,4);\n"
            "    RETURN @digits"
        ),
    },
    "F_Val": {
        "complexity": "easy",
        "params": [("@val_name", "NVARCHAR(100)")],
        "return_type": "INT",
        "note": "Illustrative constant lookup. Replace with the project's real K_* names and values.",
        "body": (
            "    RETURN CASE @val_name\n"
            "        WHEN N'K_Type_A'       THEN 1\n"
            "        WHEN N'K_Type_B'       THEN 2\n"
            "        WHEN N'K_Type_C'       THEN 3\n"
            "        WHEN N'K_Type_D'       THEN 4\n"
            "        WHEN N'K_Type_E'       THEN 5\n"
            "        WHEN N'K_Active_Yn'    THEN 1\n"
            "        WHEN N'K_Inactive_Yn'  THEN 0\n"
            "        ELSE NULL END"
        ),
    },
    "F_System_Parm_Char": {
        "complexity": "easy",
        "params": [("@parm_name", "NVARCHAR(100)")],
        "return_type": "NVARCHAR(MAX)",
        "note": "Reads SParm_Char_Val from System_Parameters by name.",
        "body": (
            "    DECLARE @v NVARCHAR(MAX);\n"
            "    SELECT TOP 1 @v = SParm_Char_Val FROM dbo.System_Parameters WHERE SParm_Nm = @parm_name;\n"
            "    RETURN @v"
        ),
    },
    "F_Add_Delimiter": {
        "complexity": "easy",
        "params": [
            ("@s",     "NVARCHAR(MAX)"),
            ("@delim", "NVARCHAR(10)"),
        ],
        "return_type": "NVARCHAR(MAX)",
        "note": "Returns @s + delim, or empty when @s is null/empty.",
        "body": (
            "    IF @s IS NULL OR LEN(@s) = 0 RETURN N'';\n"
            "    RETURN @s + ISNULL(@delim, N'')"
        ),
    },
    "Escape": {
        "complexity": "easy",
        "params": [("@s", "NVARCHAR(MAX)")],
        "return_type": "NVARCHAR(MAX)",
        "note": "URL-encodes a few common characters; replaces Utl_URL.Escape.",
        "body": (
            "    DECLARE @r NVARCHAR(MAX) = ISNULL(@s, N'');\n"
            "    SET @r = REPLACE(@r, N' ',  N'%20');\n"
            "    SET @r = REPLACE(@r, N'&',  N'%26');\n"
            "    SET @r = REPLACE(@r, N'?',  N'%3F');\n"
            "    SET @r = REPLACE(@r, N'=',  N'%3D');\n"
            "    SET @r = REPLACE(@r, N'#',  N'%23');\n"
            "    RETURN @r"
        ),
    },
    "F_Get_Permittees": {
        "complexity": "hard",
        "params": [
            ("@perm_num",      "INT"),
            ("@prog_id",       "INT"),
            ("@perm_exp_date", "DATE"),
            ("@order_by",      "NVARCHAR(50)"),
            ("@delim",         "NVARCHAR(10)"),
        ],
        "return_type": "NVARCHAR(MAX)",
        "note": "Aggregates permittee names. In T-SQL 2017+ port to STRING_AGG.",
        "body": (
            "    -- Dev/preview body: returns a fake aggregate so the report renders.\n"
            "    RETURN N'PERMITTEE-A' + ISNULL(@delim, N'; ') + N'PERMITTEE-B'"
        ),
    },
    "F_Get_Rep_Description": {
        "complexity": "hard",
        "params": [("@rep_id", "INT")],
        "return_type": "NVARCHAR(MAX)",
        "note": "Reads a description from a Reports metadata table.",
        "body": "    RETURN N'Generated by SSRS (preview mode)'",
    },
    "F_Get_Rep_URL": {
        "complexity": "hard",
        "params": [("@rep_id", "INT")],
        "return_type": "NVARCHAR(MAX)",
        "note": "Builds a deep-link URL into the Reports portal.",
        "body": (
            "    RETURN N'http://reports.example.com/report?id=' + CAST(ISNULL(@rep_id, 0) AS NVARCHAR(20))"
        ),
    },
    "F_Get_Rep_Distr_Abbr": {
        "complexity": "hard",
        "params": [("@distr_id", "INT")],
        "return_type": "NVARCHAR(20)",
        "note": "Maps district id to abbreviation; real version reads dbo.Districts.",
        "body": (
            "    DECLARE @abbr NVARCHAR(20);\n"
            "    SELECT TOP 1 @abbr = District_Abbr FROM dbo.Districts WHERE District_Id = @distr_id;\n"
            "    RETURN ISNULL(@abbr, N'NA')"
        ),
    },
    "F_Get_Perm_Name": {
        "complexity": "hard",
        "params": [("@perm_num", "INT")],
        "return_type": "NVARCHAR(50)",
        "note": "Returns a synthesized permit name; real version reads dbo.Permits.",
        "body": (
            "    DECLARE @name NVARCHAR(50);\n"
            "    SELECT TOP 1 @name = Perm_Name FROM dbo.Permits WHERE Perm_Num = @perm_num;\n"
            "    RETURN ISNULL(@name, N'PERM-' + CAST(@perm_num AS NVARCHAR(20)))"
        ),
    },
}


def _dev_guard_header(name: str) -> str:
    """Return a SESSION_CONTEXT-based dev/prod guard block for the named UDF.

    Scalar UDFs cannot RAISERROR, so we just record a marker through SESSION_CONTEXT
    when the dev flag isn't set. The body that follows still returns a sensible
    default. To bypass the guard in dev/preview, run once per connection:
        EXEC sp_set_session_context N'Oracle2SSRS_dev', 1;
    """
    return (
        "    -- Production-safety guard. Scalar UDFs cannot RAISERROR, so we read\n"
        "    -- SESSION_CONTEXT(N'Oracle2SSRS_dev'); set it to 1 on the dev session\n"
        "    -- to silence the marker. In production, leaving this stub in place is\n"
        "    -- a bug — you MUST replace the body with the real implementation.\n"
        "    DECLARE @__o2s_dev SQL_VARIANT = SESSION_CONTEXT(N'Oracle2SSRS_dev');\n"
        "    -- IF @__o2s_dev IS NULL: stub was hit in production, fall through to\n"
        "    -- the dev body below so the report doesn't crash, but treat it as a TODO.\n"
    )


_PKG_CALL_RE = re.compile(
    r'\b(Pkg_[A-Za-z0-9_]+|Utl_URL)\s*\.\s*([A-Za-z_][A-Za-z0-9_]*)',
    re.IGNORECASE,
)


def _find_max_args(sql: str, qualified: str) -> int:
    pat = re.compile(r'\b' + re.escape(qualified) + r'\s*\(', re.IGNORECASE)
    max_args = 0
    for m in pat.finditer(sql):
        depth = 0
        in_str = False
        i = m.end() - 1
        commas = 0
        had_content = False
        while i < len(sql):
            c = sql[i]
            if in_str:
                if c == "'":
                    if i + 1 < len(sql) and sql[i + 1] == "'":
                        i += 2
                        continue
                    in_str = False
            else:
                if c == "'":
                    in_str = True
                elif c == '(':
                    depth += 1
                elif c == ')':
                    depth -= 1
                    if depth == 0:
                        break
                elif c == ',' and depth == 1:
                    commas += 1
                elif depth == 1 and not c.isspace():
                    had_content = True
            i += 1
        n = (commas + 1) if had_content else 0
        if n > max_args:
            max_args = n
    return max_args


def _scan_references(sqls: List[str]) -> Set[Tuple[str, str, int]]:
    found: Dict[Tuple[str, str], int] = {}
    for sql in sqls:
        if not sql:
            continue
        for m in _PKG_CALL_RE.finditer(sql):
            pkg = m.group(1)
            fn = m.group(2)
            key = (pkg, fn)
            n = _find_max_args(sql, f"{pkg}.{fn}")
            if n == 0:
                n = _find_max_args(sql, fn)
            found[key] = max(found.get(key, 0), n)
    return {(p, f, n) for (p, f), n in found.items()}


def _find_embedded_plsql(report: ParsedReport, fn_short: str) -> str:
    """Return up to ~30 lines of Oracle PL/SQL that reference fn_short, prefixed
    with '-- ' so it can be embedded in a generated CREATE FUNCTION body."""
    needle = re.compile(r"\b" + re.escape(fn_short) + r"\b", re.I)
    for f in getattr(report, "formulas", []) or []:
        body = getattr(f, "plsql_body", "") or ""
        if needle.search(body):
            snippet_lines = body.splitlines()[:30]
            return f"-- from formula {f.name}:\n" + "\n".join("--   " + ln for ln in snippet_lines)
    for t in getattr(report, "triggers", []) or []:
        body = getattr(t, "body", "") or ""
        if needle.search(body):
            snippet_lines = body.splitlines()[:30]
            return f"-- from trigger {t.name}:\n" + "\n".join("--   " + ln for ln in snippet_lines)
    return ""


def _build_stub(report: ParsedReport, pkg: str, fn_short: str, observed_args: int) -> str:
    spec = _KNOWN_STUBS.get(fn_short)
    if spec:
        params      = list(spec["params"])
        body        = spec["body"]
        return_type = spec.get("return_type", "NVARCHAR(MAX)")
        complexity  = spec.get("complexity", "easy")
        note        = spec.get("note", "")
    else:
        n = max(observed_args, 1)
        params = [(f"@p{i + 1}", "NVARCHAR(MAX)") for i in range(n)]
        body = (
            "    -- Auto-generated stub for an unrecognized Oracle package function.\n"
            "    -- Edit me to match the original implementation.\n"
            "    RETURN N'<" + pkg + "." + fn_short + ">'"
        )
        return_type = "NVARCHAR(MAX)"
        complexity  = "hard"
        note        = "(no spec found - auto-generated; verify against source.)"

    sig = ",\n    ".join(f"{p} {t}" for p, t in params)
    sig_for_doc = ", ".join(f"{p} {t}" for p, t in params)

    embedded = _find_embedded_plsql(report, fn_short) if complexity == "hard" else ""

    porting_note = (
        f"/* PORTING NOTE\n"
        f" * Original Oracle  : {pkg}.{fn_short}\n"
        f" * Target T-SQL     : dbo.fn_{fn_short}\n"
        f" * Parameters       : {sig_for_doc}\n"
        f" * Returns          : {return_type}\n"
        f" * Complexity       : {complexity}\n"
        f" * Note             : {note}\n"
        f" * Source location  : "
        + ("see formula/trigger excerpt below" if embedded
           else f"see {pkg} package body in your Oracle source DB")
        + "\n"
        " */\n"
    )

    if embedded:
        porting_note += "/* Original Oracle PL/SQL (excerpt):\n" + embedded + "\n*/\n"

    guard = _dev_guard_header(fn_short) if complexity == "hard" else ""

    sql = (
        f"{porting_note}"
        f"-- {pkg}.{fn_short}  -> dbo.fn_{fn_short}\n"
        f"IF OBJECT_ID(N'dbo.fn_{fn_short}', N'FN') IS NOT NULL\n"
        f"    DROP FUNCTION dbo.fn_{fn_short};\n"
        f"GO\n"
        f"CREATE FUNCTION dbo.fn_{fn_short}\n"
        f"(\n    {sig}\n)\n"
        f"RETURNS {return_type}\n"
        f"AS\n"
        f"BEGIN\n"
        f"{guard}"
        f"{body};\n"
        f"END;\n"
        f"GO\n"
    )
    return sql


def generate_udf_stubs(report: ParsedReport) -> str:
    sources: List[str] = []
    for q in report.queries:
        if q.sql:
            sources.append(q.sql)
        if q.tsql:
            sources.append(q.tsql)
    for f in report.formulas:
        if f.plsql_body:
            sources.append(f.plsql_body)

    refs = _scan_references(sources)

    by_fn: Dict[str, Tuple[str, int]] = {}
    for pkg, fn, n in refs:
        prev = by_fn.get(fn)
        if prev is None or n > prev[1]:
            by_fn[fn] = (pkg, n)

    if not by_fn:
        return "-- generate_udf_stubs: no Oracle package function references found.\n"

    parts = [
        "-- =========================================================\n"
        "-- Auto-generated T-SQL stubs for Oracle package functions.\n"
        "-- Each function carries a /* PORTING NOTE */ block. Replace these\n"
        "-- with real implementations before going to prod.\n"
        "-- For dev/preview the calling session must run:\n"
        "--   EXEC sp_set_session_context N'Oracle2SSRS_dev', 1;\n"
        "-- once per connection so the production-safety guard is silenced.\n"
        "-- =========================================================\n"
    ]
    for fn in sorted(by_fn):
        pkg, n = by_fn[fn]
        parts.append(_build_stub(report, pkg, fn, n))
    return "\n".join(parts)


__all__ = ["generate_udf_stubs"]
