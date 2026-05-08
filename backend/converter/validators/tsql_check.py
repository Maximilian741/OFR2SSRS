"""
Lightweight, pure-Python T-SQL validator for the Oracle->SSRS pipeline.

We are NOT allowed to phone home to a real SQL Server. Instead, we run a
collection of static, lexical checks designed to catch the things that will
prevent the generated RDL from running once the user opens it in Report
Builder against a live target database.

Public API:
    validate_tsql(sql: str) -> list[dict]
    validate_report(report: ParsedReport) -> list[dict]

Each issue dict has the shape:
    {
        "severity": "error" | "warning" | "info",
        "line":     int | None,
        "col":      int | None,
        "message":  str,
        "rule":     str,
        "scope":    str,        # e.g. "Q_PERMIT" or "report"
        "excerpt":  str,        # the SQL line the issue points at (best-effort)
    }
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _line_of(sql: str, idx: int) -> Tuple[int, int]:
    """Return 1-based (line, column) for a string offset."""
    if idx < 0:
        return (1, 1)
    if idx > len(sql):
        idx = len(sql)
    pre = sql[:idx]
    line = pre.count("\n") + 1
    last_nl = pre.rfind("\n")
    col = idx - last_nl  # last_nl == -1 -> col == idx + 1, fine
    return (line, col)


def _excerpt_of(sql: str, line: Optional[int]) -> str:
    if not line or line < 1:
        return ""
    lines = sql.splitlines()
    if line > len(lines):
        return ""
    return lines[line - 1].strip()[:200]


def _strip_strings_and_comments(sql: str) -> str:
    """Replace string literals and comments with spaces of equal length so
    offsets line up with the original. Used to avoid false positives when an
    Oracle-only keyword appears inside a quoted string or comment."""
    out = []
    i = 0
    n = len(sql)
    while i < n:
        c = sql[i]
        # ' string '
        if c == "'":
            out.append(" ")
            i += 1
            while i < n:
                if sql[i] == "'":
                    if i + 1 < n and sql[i + 1] == "'":
                        out.append("  ")
                        i += 2
                        continue
                    out.append(" ")
                    i += 1
                    break
                out.append("\n" if sql[i] == "\n" else " ")
                i += 1
            continue
        # -- line comment
        if c == "-" and i + 1 < n and sql[i + 1] == "-":
            while i < n and sql[i] != "\n":
                out.append(" ")
                i += 1
            continue
        # /* block comment */
        if c == "/" and i + 1 < n and sql[i + 1] == "*":
            out.append("  ")
            i += 2
            while i < n - 1 and not (sql[i] == "*" and sql[i + 1] == "/"):
                out.append("\n" if sql[i] == "\n" else " ")
                i += 1
            if i < n - 1:
                out.append("  ")
                i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _make(severity: str, sql: str, idx: Optional[int], message: str,
          rule: str, scope: str) -> Dict[str, Any]:
    if idx is None:
        line, col = (None, None)
    else:
        line, col = _line_of(sql, idx)
    return {
        "severity": severity,
        "line":     line,
        "col":      col,
        "message":  message,
        "rule":     rule,
        "scope":    scope,
        "excerpt":  _excerpt_of(sql, line) if line else "",
    }


# ---------------------------------------------------------------------------
# Individual rule checks
# ---------------------------------------------------------------------------

# Tokens that should never survive translation. Pattern -> (rule, message).
_ORACLE_LEFTOVER = [
    (re.compile(r"\bDECODE\s*\(", re.I),
     ("oracle.decode",
      "DECODE() is Oracle-only; rewrite as CASE WHEN ... THEN ... END.")),
    (re.compile(r"\bNVL2\s*\(", re.I),
     ("oracle.nvl2",
      "NVL2() is Oracle-only; rewrite as CASE WHEN x IS NOT NULL THEN a ELSE b END.")),
    (re.compile(r"\bNVL\s*\(", re.I),
     ("oracle.nvl",
      "NVL() is Oracle-only; rewrite as ISNULL(...) or COALESCE(...).")),
    (re.compile(r"\bTO_CHAR\s*\(", re.I),
     ("oracle.to_char",
      "TO_CHAR() is Oracle-only; rewrite as CONVERT(NVARCHAR, x, fmt) or FORMAT().")),
    (re.compile(r"\bTO_DATE\s*\(", re.I),
     ("oracle.to_date",
      "TO_DATE() is Oracle-only; rewrite as CONVERT(DATE, x, fmt) or TRY_CONVERT.")),
    (re.compile(r"\bTO_NUMBER\s*\(", re.I),
     ("oracle.to_number",
      "TO_NUMBER() is Oracle-only; rewrite as CAST(x AS DECIMAL/INT) or TRY_CAST.")),
    (re.compile(r"\bSYSDATE\b", re.I),
     ("oracle.sysdate",
      "SYSDATE is Oracle-only; use GETDATE() in T-SQL.")),
    (re.compile(r"\bSYSTIMESTAMP\b", re.I),
     ("oracle.systimestamp",
      "SYSTIMESTAMP is Oracle-only; use SYSDATETIMEOFFSET() in T-SQL.")),
    (re.compile(r"\bSUBSTR\s*\(", re.I),
     ("oracle.substr",
      "SUBSTR() is Oracle-only; use SUBSTRING() in T-SQL.")),
    (re.compile(r"\bINSTR\s*\(", re.I),
     ("oracle.instr",
      "INSTR() is Oracle-only; use CHARINDEX() in T-SQL.")),
    (re.compile(r"\bROWNUM\b", re.I),
     ("oracle.rownum",
      "ROWNUM is Oracle-only; use TOP n or ROW_NUMBER() OVER (...).")),
    (re.compile(r"\bMINUS\b", re.I),
     ("oracle.minus",
      "MINUS is Oracle-only; use EXCEPT in T-SQL.")),
    (re.compile(r"\bCONNECT\s+BY\b", re.I),
     ("oracle.connect_by",
      "CONNECT BY is Oracle-only; rewrite as a recursive CTE (WITH ... AS).")),
    (re.compile(r"\bSTART\s+WITH\b", re.I),
     ("oracle.start_with",
      "START WITH is Oracle-only; rewrite as the seed leg of a recursive CTE.")),
    (re.compile(r"\bDUAL\b", re.I),
     ("oracle.dual",
      "DUAL is Oracle-only; in T-SQL just SELECT without FROM.")),
    (re.compile(r"\bLISTAGG\s*\(", re.I),
     ("oracle.listagg",
      "LISTAGG() is Oracle-only; use STRING_AGG() in T-SQL 2017+.")),
]

# (+) outer-join hint that survived translation.
_OUTER_JOIN_HINT = re.compile(r"\(\s*\+\s*\)")

# Lexical references like &P_CRITERIA_PERMIT.
_LEX_REF = re.compile(r"&[A-Za-z_][A-Za-z0-9_]*\.?")

# Bind variables :foo (excluding ::cast).
_BIND_VAR = re.compile(r"(?<!:):[A-Za-z_][A-Za-z0-9_]*\b")

# @P_* parameters (T-SQL style)
_AT_PARAM = re.compile(r"@P_[A-Za-z0-9_]+", re.I)

# SELECT *
_SELECT_STAR = re.compile(r"\bSELECT\s+\*", re.I)

# Pkg_* tokens not prefixed with dbo.
_PKG_TOKEN = re.compile(r"(?<!\.)\bPkg_[A-Za-z0-9_]+", re.I)

# Identifier > 128 chars (find any [..]/`..` or bareword over 128 chars).
_LONG_IDENT_BARE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]{128,}\b")
_LONG_IDENT_BRK  = re.compile(r"\[([^\]]{129,})\]")


def _check_brackets_and_quotes(sql: str, scope: str) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    stack: List[Tuple[str, int]] = []
    in_str = False
    in_block_cmt = False
    in_line_cmt = False
    i = 0
    n = len(sql)
    while i < n:
        c = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""
        if in_line_cmt:
            if c == "\n":
                in_line_cmt = False
            i += 1
            continue
        if in_block_cmt:
            if c == "*" and nxt == "/":
                in_block_cmt = False
                i += 2
                continue
            i += 1
            continue
        if in_str:
            if c == "'":
                if nxt == "'":
                    i += 2
                    continue
                in_str = False
            i += 1
            continue
        # not inside anything
        if c == "-" and nxt == "-":
            in_line_cmt = True
            i += 2
            continue
        if c == "/" and nxt == "*":
            in_block_cmt = True
            i += 2
            continue
        if c == "'":
            in_str = True
            i += 1
            continue
        if c == "(":
            stack.append((c, i))
        elif c == ")":
            if not stack:
                issues.append(_make(
                    "error", sql, i,
                    "Unmatched closing parenthesis ')'.",
                    "syntax.parens",
                    scope,
                ))
            else:
                stack.pop()
        i += 1
    if in_str:
        issues.append(_make(
            "error", sql, n - 1,
            "Unterminated string literal (missing closing quote).",
            "syntax.quote",
            scope,
        ))
    if in_block_cmt:
        issues.append(_make(
            "error", sql, n - 1,
            "Unterminated block comment (missing */).",
            "syntax.comment",
            scope,
        ))
    for _ch, idx in stack:
        issues.append(_make(
            "error", sql, idx,
            "Unmatched opening parenthesis '('.",
            "syntax.parens",
            scope,
        ))
    return issues


def _check_oracle_leftovers(sql: str, sanitized: str, scope: str) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    for rx, (rule, msg) in _ORACLE_LEFTOVER:
        for m in rx.finditer(sanitized):
            issues.append(_make("error", sql, m.start(), msg, rule, scope))
    for m in _OUTER_JOIN_HINT.finditer(sanitized):
        issues.append(_make(
            "error", sql, m.start(),
            "Oracle '(+)' outer-join hint did not get rewritten; convert to "
            "an explicit LEFT/RIGHT JOIN ... ON ... .",
            "oracle.outer_join_hint",
            scope,
        ))
    for m in _LEX_REF.finditer(sanitized):
        issues.append(_make(
            "warning", sql, m.start(),
            f"Lexical reference '{m.group(0)}' is unresolved; SSRS does not "
            "support &P_* substitution. Use a Tablix Filter, a parameter "
            "expression, or sp_executesql with @ParmDefinitions.",
            "oracle.lex_ref",
            scope,
        ))
    for m in _BIND_VAR.finditer(sanitized):
        issues.append(_make(
            "warning", sql, m.start(),
            f"Oracle bind variable '{m.group(0)}' should be rewritten as a "
            f"T-SQL parameter (e.g. @{m.group(0)[1:]}).",
            "oracle.bind_var",
            scope,
        ))
    for m in _PKG_TOKEN.finditer(sanitized):
        issues.append(_make(
            "warning", sql, m.start(),
            f"Reference to Oracle package '{m.group(0)}' without 'dbo.' "
            "prefix; in SSRS this needs to be ported to dbo.fn_* or fully "
            "qualified.",
            "oracle.pkg_unqualified",
            scope,
        ))
    return issues


def _check_select_star(sql: str, sanitized: str, scope: str) -> List[Dict[str, Any]]:
    return [
        _make("warning", sql, m.start(),
              "SELECT * makes RDL Tablix bindings fragile; list columns "
              "explicitly so the report doesn't break when columns are added "
              "or renamed.",
              "style.select_star", scope)
        for m in _SELECT_STAR.finditer(sanitized)
    ]


def _check_missing_semicolons(sql: str, sanitized: str, scope: str) -> List[Dict[str, Any]]:
    """Info-level. Most SSRS dataset queries don't require a trailing
    semicolon, but flag if there is more than one statement and any of them is
    missing a separator."""
    stripped = sanitized.strip()
    if not stripped:
        return []
    # If there's any GO or semicolon already we assume they thought about it.
    if ";" in stripped or re.search(r"^\s*GO\s*$", stripped, re.I | re.M):
        return []
    # Heuristic: only emit info if the query looks like multiple statements
    # (very rough — count top-level SELECTs).
    selects = re.findall(r"\bSELECT\b", stripped, re.I)
    if len(selects) >= 2:
        return [_make("info", sql, None,
                      "Statement has no terminating ';'. Not strictly required "
                      "for RDL datasets, but recommended for clarity.",
                      "style.semicolon", scope)]
    return []


def _check_long_identifiers(sql: str, sanitized: str, scope: str) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    for m in _LONG_IDENT_BARE.finditer(sanitized):
        issues.append(_make(
            "error", sql, m.start(),
            f"Identifier is {len(m.group(0))} characters; SQL Server limits "
            "identifiers to 128 characters.",
            "syntax.identifier_too_long",
            scope,
        ))
    for m in _LONG_IDENT_BRK.finditer(sanitized):
        issues.append(_make(
            "error", sql, m.start(),
            f"Bracketed identifier is {len(m.group(1))} characters; SQL "
            "Server limits identifiers to 128 characters.",
            "syntax.identifier_too_long",
            scope,
        ))
    return issues


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_tsql(sql: str, scope: str = "query") -> List[Dict[str, Any]]:
    """Run every static check against `sql` and return a flat list of issues."""
    if not sql or not sql.strip():
        return []
    sanitized = _strip_strings_and_comments(sql)
    issues: List[Dict[str, Any]] = []
    issues += _check_brackets_and_quotes(sql, scope)
    issues += _check_oracle_leftovers(sql, sanitized, scope)
    issues += _check_select_star(sql, sanitized, scope)
    issues += _check_missing_semicolons(sql, sanitized, scope)
    issues += _check_long_identifiers(sql, sanitized, scope)
    return issues


def validate_report(report) -> List[Dict[str, Any]]:
    """Validate every dataset SQL on the report plus do a few report-level
    cross-checks (params referenced but not declared, etc.). `report` is a
    ParsedReport (we don't import it, just duck-type)."""
    issues: List[Dict[str, Any]] = []

    declared_params = {p.name.upper() for p in getattr(report, "parameters", []) if getattr(p, "name", None)}

    for q in getattr(report, "queries", []) or []:
        scope = getattr(q, "name", None) or "query"
        sql = getattr(q, "tsql", "") or ""
        if not sql:
            issues.append({
                "severity": "warning",
                "line":     None,
                "col":      None,
                "message":  "No T-SQL emitted for this dataset; the translator "
                            "may have failed to convert the Oracle SQL.",
                "rule":     "report.empty_tsql",
                "scope":    scope,
                "excerpt":  "",
            })
            continue
        per_q = validate_tsql(sql, scope=scope)
        issues.extend(per_q)

        # Cross-check @P_* references against declared parameters.
        sanitized = _strip_strings_and_comments(sql)
        seen_at_params = {m.group(0)[1:].upper() for m in _AT_PARAM.finditer(sanitized)}
        for at_name in sorted(seen_at_params):
            if at_name not in declared_params:
                # find first hit for line info
                first = re.search(r"@" + re.escape(at_name) + r"\b", sanitized, re.I)
                idx = first.start() if first else None
                issues.append(_make(
                    "error", sql, idx,
                    f"Query references @{at_name} but the report has no "
                    "matching parameter declaration; SSRS will fail to bind it.",
                    "report.param_unbound",
                    scope,
                ))

        # Lexical refs on the report level too — surface a single rolled-up note.
        lex_hits = {m.group(0) for m in _LEX_REF.finditer(sanitized)}
        if lex_hits:
            issues.append({
                "severity": "warning",
                "line":     None,
                "col":      None,
                "message":  "Dataset still contains Oracle lexical refs: "
                            + ", ".join(sorted(lex_hits))
                            + ". See deployment checklist step 5.",
                "rule":     "report.lex_refs",
                "scope":    scope,
                "excerpt":  "",
            })

    # Report-level: warn if any parameter is declared but never used.
    if declared_params:
        used = set()
        for q in getattr(report, "queries", []) or []:
            sql = getattr(q, "tsql", "") or ""
            sanitized = _strip_strings_and_comments(sql)
            for m in _AT_PARAM.finditer(sanitized):
                used.add(m.group(0)[1:].upper())
        unused = declared_params - used
        for p in sorted(unused):
            issues.append({
                "severity": "info",
                "line":     None,
                "col":      None,
                "message":  f"Parameter @{p} is declared on the report but "
                            "never referenced in any dataset query.",
                "rule":     "report.param_unused",
                "scope":    "report",
                "excerpt":  "",
            })

    return issues


__all__ = ["validate_tsql", "validate_report"]
