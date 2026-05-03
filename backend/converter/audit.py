"""
Translation audit trail.

Produces a structured, ordered timeline of every translation decision the
pipeline made while turning an Oracle Reports artifact into SSRS / T-SQL.
This is the artifact reviewers and auditors look at to verify "nothing weird
happened" - it is purely DERIVED from the existing report state and never
mutates the report.

Public API:
    build_audit_trail(report) -> list[dict]
        Each dict has keys:
            step (int):     1-based index, monotonic across the whole timeline
            stage (str):    one of "parse" | "translate" | "generate" | "validate"
            scope (str):    where the change happened (e.g. "Q_PERMIT", "CF_File_F",
                            "report")
            rule (str):     short tag identifying the rewrite rule
                            (e.g. "DECODE->CASE")
            before (str):   <=80-char snippet of the original text
            after (str):    <=80-char snippet of the translated text
            rationale (str): one-sentence explanation

Sources of audit entries:
    1. report.queries[*].notes              -> stage="translate", scope=q.name
    2. report.formulas[*].notes             -> stage="translate", scope=f.name
    3. report.warnings                      -> stage="parse" or "validate" (heuristic)
    4. Synthetic diff-based detections on each q.sql vs q.tsql and on each
       f.plsql_body vs f.tsql_body.  We scan for known Oracle patterns that
       are NOT present in the translated text and emit one entry per pattern.

The module never modifies the report.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SNIPPET_LIMIT = 80


def _snippet(text: Optional[str], limit: int = _SNIPPET_LIMIT) -> str:
    """Return a single-line, length-capped representation of `text`."""
    if not text:
        return ""
    s = re.sub(r"\s+", " ", str(text)).strip()
    if len(s) <= limit:
        return s
    return s[: max(0, limit - 1)].rstrip() + "..."


def _around(text: str, idx: int, span: int = _SNIPPET_LIMIT) -> str:
    """Return a snippet of `text` centered on character index `idx`."""
    if idx < 0:
        return _snippet(text)
    half = span // 2
    start = max(0, idx - half)
    end = min(len(text), idx + half)
    chunk = text[start:end]
    if start > 0:
        chunk = "..." + chunk
    if end < len(text):
        chunk = chunk + "..."
    return _snippet(chunk, span)


# ---------------------------------------------------------------------------
# Pattern catalog
# ---------------------------------------------------------------------------
#
# Each entry: (rule_tag, oracle_regex, tsql_regex_or_None, rationale)
# The Oracle pattern must appear in the BEFORE text and (if a t-sql pattern
# is given) NOT appear in the AFTER text - that's our "this rewrite happened"
# heuristic. If t-sql pattern is None, the absence of the Oracle pattern in
# AFTER is the signal.

_PATTERNS: List[Tuple[str, "re.Pattern[str]", Optional["re.Pattern[str]"], str]] = [
    (
        "DECODE->CASE",
        re.compile(r"(?<![A-Za-z0-9_$])DECODE\s*\(", re.IGNORECASE),
        None,
        "Oracle DECODE() has no T-SQL equivalent; rewritten as a CASE expression.",
    ),
    (
        "NVL->ISNULL",
        re.compile(r"(?<![A-Za-z0-9_$])NVL\s*\(", re.IGNORECASE),
        None,
        "NVL() replaced with ISNULL() (single-argument null coalescing).",
    ),
    (
        "NVL2->CASE",
        re.compile(r"(?<![A-Za-z0-9_$])NVL2\s*\(", re.IGNORECASE),
        None,
        "NVL2() expanded to a CASE WHEN x IS NOT NULL THEN ... ELSE ... END.",
    ),
    (
        "TO_CHAR->FORMAT",
        re.compile(r"(?<![A-Za-z0-9_$])TO_CHAR\s*\(", re.IGNORECASE),
        None,
        "TO_CHAR() rewritten using SQL Server FORMAT() / CAST(... AS NVARCHAR).",
    ),
    (
        "TO_DATE->CONVERT",
        re.compile(r"(?<![A-Za-z0-9_$])TO_DATE\s*\(", re.IGNORECASE),
        None,
        "TO_DATE() rewritten as TRY_CONVERT(date, ...) / CONVERT(date, ..., 101).",
    ),
    (
        "SYSDATE->GETDATE",
        re.compile(r"(?<![A-Za-z0-9_$])SYSDATE(?![A-Za-z0-9_$])", re.IGNORECASE),
        None,
        "Oracle SYSDATE replaced with T-SQL GETDATE().",
    ),
    (
        "INSTR->CHARINDEX",
        re.compile(r"(?<![A-Za-z0-9_$])INSTR\s*\(", re.IGNORECASE),
        None,
        "INSTR(s,sub) rewritten as CHARINDEX(sub, s); argument order swapped.",
    ),
    (
        "SUBSTR->SUBSTRING",
        re.compile(r"(?<![A-Za-z0-9_$])SUBSTR\s*\(", re.IGNORECASE),
        None,
        "SUBSTR() rewritten as SUBSTRING(); explicit length added if missing.",
    ),
    (
        "CHR->CHAR",
        re.compile(r"(?<![A-Za-z0-9_$])CHR\s*\(", re.IGNORECASE),
        None,
        "Oracle CHR(n) renamed to T-SQL CHAR(n).",
    ),
    (
        "||->+",
        re.compile(r"\|\|"),
        None,
        "Oracle '||' string concatenation rewritten as T-SQL '+' (NULL semantics differ).",
    ),
    (
        "(+)->LEFT JOIN",
        re.compile(r"\(\+\)"),
        None,
        "Oracle '(+)' outer-join syntax rewritten using ANSI LEFT JOIN.",
    ),
    (
        ":bind->@bind",
        re.compile(r"(?<![A-Za-z0-9_$:]):[A-Za-z][A-Za-z0-9_$]*"),
        None,
        "Oracle :bind variables rewritten as T-SQL @parameter references.",
    ),
    (
        "&lexical",
        re.compile(r"&[A-Za-z][A-Za-z0-9_$]*"),
        None,
        "Oracle &LEXICAL reference flagged; T-SQL has no equivalent (use Tablix filter or sp_executesql).",
    ),
    (
        "TRUNC->CAST/DATEFROMPARTS",
        re.compile(r"(?<![A-Za-z0-9_$])TRUNC\s*\(", re.IGNORECASE),
        None,
        "TRUNC() rewritten as CAST(... AS DATE) or DATEFROMPARTS() depending on unit.",
    ),
    (
        "LISTAGG->STRING_AGG",
        re.compile(r"(?<![A-Za-z0-9_$])LISTAGG\s*\(", re.IGNORECASE),
        None,
        "LISTAGG() rewritten as STRING_AGG(); WITHIN GROUP semantics preserved.",
    ),
    (
        "ROWNUM->TOP",
        re.compile(r"(?<![A-Za-z0-9_$])ROWNUM(?![A-Za-z0-9_$])", re.IGNORECASE),
        None,
        "WHERE ROWNUM <= N rewritten as SELECT TOP (N).",
    ),
    (
        "FROM DUAL removed",
        re.compile(r"\bFROM\s+DUAL\b", re.IGNORECASE),
        None,
        "FROM DUAL clause removed; T-SQL does not require a dummy table.",
    ),
    (
        "Pkg.Fn->dbo.fn_*",
        re.compile(r"\b(?:Pkg_[A-Za-z0-9_]+|Utl_URL)\s*\.\s*[A-Za-z_][A-Za-z0-9_]*", re.IGNORECASE),
        None,
        "Oracle package function call replaced with T-SQL dbo.fn_* UDF stub.",
    ),
]


# Map common note keywords to a rule tag, so notes already produced by the
# translator get a stable, reviewer-friendly rule label.
_NOTE_TAG_HINTS: List[Tuple[str, str]] = [
    ("DECODE", "DECODE->CASE"),
    ("NVL2", "NVL2->CASE"),
    ("NVL", "NVL->ISNULL"),
    ("TO_CHAR", "TO_CHAR->FORMAT"),
    ("TO_DATE", "TO_DATE->CONVERT"),
    ("SYSDATE", "SYSDATE->GETDATE"),
    ("INSTR", "INSTR->CHARINDEX"),
    ("SUBSTR", "SUBSTR->SUBSTRING"),
    ("CHR", "CHR->CHAR"),
    ("||", "||->+"),
    ("'||'", "||->+"),
    ("(+)", "(+)->LEFT JOIN"),
    ("outer-join", "(+)->LEFT JOIN"),
    ("OUTER JOIN", "(+)->LEFT JOIN"),
    ("LEFT JOIN", "(+)->LEFT JOIN"),
    ("bind variable", ":bind->@bind"),
    ("lexical", "&lexical"),
    ("LISTAGG", "LISTAGG->STRING_AGG"),
    ("ROWNUM", "ROWNUM->TOP"),
    ("FROM DUAL", "FROM DUAL removed"),
    ("DUAL", "FROM DUAL removed"),
    ("TRUNC", "TRUNC->CAST/DATEFROMPARTS"),
    ("ROUND", "ROUND(date)->DATEFROMPARTS"),
    ("Package", "Pkg.Fn->dbo.fn_*"),
    ("package function", "Pkg.Fn->dbo.fn_*"),
    ("UDF", "Pkg.Fn->dbo.fn_*"),
    ("EXISTS", "EXISTS+HAVING review"),
    ("HAVING", "EXISTS+HAVING review"),
    ("RTRIM", "RTRIM/LTRIM review"),
    ("LTRIM", "RTRIM/LTRIM review"),
]


def _tag_from_note(note: str) -> str:
    """Best-effort: pick a short rule tag from a free-form translator note."""
    if not note:
        return "translator note"
    for needle, tag in _NOTE_TAG_HINTS:
        if needle.lower() in note.lower():
            return tag
    # Fall back to the first 3-4 words of the note as a rough tag.
    words = re.findall(r"[A-Za-z_][A-Za-z_0-9]*", note)
    if words:
        return " ".join(words[:3])
    return "translator note"


def _stage_for_warning(text: str) -> str:
    """Heuristic: report-level warnings come from either parsing or validation."""
    if not text:
        return "parse"
    low = text.lower()
    if any(k in low for k in (
        "validation", "validator", "invalid", "schema",
        "rdl", "missing close", "well-formed", "xsd",
    )):
        return "validate"
    if any(k in low for k in ("generate", "generator", "rdl ")):
        return "generate"
    return "parse"


# ---------------------------------------------------------------------------
# Detection passes
# ---------------------------------------------------------------------------

def _detect_pattern_changes(
    before: str, after: str
) -> List[Tuple[str, str, str, str]]:
    """Scan a (before, after) pair for known Oracle patterns that disappeared.

    Returns list of (rule, before_snippet, after_snippet, rationale).
    Each pattern fires at most once per pair.
    """
    results: List[Tuple[str, str, str, str]] = []
    if not before or not after:
        return results
    if before == after:
        return results
    for rule, ora_pat, _tsql_pat, rationale in _PATTERNS:
        m_before = ora_pat.search(before)
        if not m_before:
            continue
        # The rewrite "happened" if the pattern is gone (or strictly less
        # frequent) in the translated text.
        before_count = len(ora_pat.findall(before))
        after_count = len(ora_pat.findall(after))
        if after_count >= before_count:
            continue
        b_snip = _around(before, m_before.start())
        # Try to point AFTER snippet at where the original index roughly maps
        # - cheap heuristic: same character position, clipped.
        a_idx = min(m_before.start(), max(0, len(after) - 1))
        a_snip = _around(after, a_idx)
        results.append((rule, b_snip, a_snip, rationale))
    return results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_audit_trail(report: Any) -> List[Dict[str, Any]]:
    """Build an ordered audit trail of all translation decisions.

    The returned list of dicts is suitable for direct JSON serialization.
    See module docstring for entry schema.
    """
    entries: List[Dict[str, Any]] = []

    def _emit(stage: str, scope: str, rule: str, before: str, after: str,
              rationale: str) -> None:
        entries.append({
            "step": 0,  # filled in at the end
            "stage": stage,
            "scope": scope or "report",
            "rule": rule or "translator note",
            "before": _snippet(before),
            "after": _snippet(after),
            "rationale": rationale or "",
        })

    # ---- 1. Parse stage: every parsed query / formula gets one parse entry.
    queries = list(getattr(report, "queries", []) or [])
    formulas = list(getattr(report, "formulas", []) or [])
    parameters = list(getattr(report, "parameters", []) or [])

    if parameters:
        names = ", ".join(getattr(p, "name", "?") for p in parameters[:6])
        _emit(
            "parse", "report", "parameters extracted",
            "<oracle xml userParameter elements>", names,
            f"Extracted {len(parameters)} report parameter(s) from Oracle XML.",
        )

    for q in queries:
        sql = getattr(q, "sql", "") or ""
        _emit(
            "parse", getattr(q, "name", "?"), "query extracted",
            sql, sql,
            f"Captured Oracle dataSource '{getattr(q, 'name', '?')}' "
            f"({len(sql)} chars of SQL).",
        )

    for f in formulas:
        body = getattr(f, "plsql_body", "") or ""
        _emit(
            "parse", getattr(f, "name", "?"), "formula extracted",
            body, body,
            f"Captured Oracle formula column '{getattr(f, 'name', '?')}' "
            f"({len(body)} chars of PL/SQL).",
        )

    # ---- 2. Translate stage: synthetic diff entries + existing notes.
    for q in queries:
        scope = getattr(q, "name", "?")
        sql = getattr(q, "sql", "") or ""
        tsql = getattr(q, "tsql", "") or ""
        if sql and tsql and sql != tsql:
            for rule, b_snip, a_snip, rationale in _detect_pattern_changes(sql, tsql):
                _emit("translate", scope, rule, b_snip, a_snip, rationale)
        for note in getattr(q, "notes", []) or []:
            _emit(
                "translate", scope, _tag_from_note(note),
                _snippet(sql), _snippet(tsql), note,
            )

    for f in formulas:
        scope = getattr(f, "name", "?")
        plsql = getattr(f, "plsql_body", "") or ""
        tsql = getattr(f, "tsql_body", "") or ""
        if plsql and tsql and plsql != tsql:
            for rule, b_snip, a_snip, rationale in _detect_pattern_changes(plsql, tsql):
                _emit("translate", scope, rule, b_snip, a_snip, rationale)
        for note in getattr(f, "notes", []) or []:
            _emit(
                "translate", scope, _tag_from_note(note),
                _snippet(plsql), _snippet(tsql), note,
            )

    # ---- 3 + 4. Report-level warnings -> generic entries (parse / validate / generate).
    for w in getattr(report, "warnings", []) or []:
        stage = _stage_for_warning(w)
        _emit(stage, "report", _tag_from_note(w), w, "", w)

    # ---- Renumber.
    for i, e in enumerate(entries, start=1):
        e["step"] = i

    return entries


# ---------------------------------------------------------------------------
# Convenience: text-mode rendering (used by CLI / debug tools)
# ---------------------------------------------------------------------------

def format_audit_trail(report: Any) -> str:
    """Return a human-readable text rendering of the audit trail."""
    rows = build_audit_trail(report)
    if not rows:
        return "(no audit entries; nothing was translated)"
    lines: List[str] = []
    lines.append(
        f"Translation audit trail ({len(rows)} entr"
        f"{'y' if len(rows) == 1 else 'ies'})"
    )
    lines.append("=" * 72)
    for e in rows:
        lines.append(
            f"#{e['step']:>3}  [{e['stage']:<9}]  {e['scope']}  -  {e['rule']}"
        )
        if e["before"]:
            lines.append(f"      before : {e['before']}")
        if e["after"]:
            lines.append(f"      after  : {e['after']}")
        if e["rationale"]:
            lines.append(f"      why    : {e['rationale']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


__all__ = ["build_audit_trail", "format_audit_trail"]
