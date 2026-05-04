"""
Compare two ParsedReport objects and emit a structured diff.

Pure functions; no Flask/IO. The web layer (app.py /api/compare) parses
two Oracle XML uploads into ParsedReport objects, then calls
compare_reports() and serializes the dict to JSON.

The complexity score is a simple heuristic that lets the UI sort reports
by "how hard is this to convert" without needing to look at the raw SQL.
"""
from __future__ import annotations

import difflib
import re
from typing import Any, Dict, List, Tuple

# Type-only imports; we accept duck-typed objects so this module stays
# decoupled from the parser internals.
try:
    from .models import ParsedReport  # noqa: F401
except Exception:  # pragma: no cover - defensive
    ParsedReport = Any  # type: ignore


# ---------------------------------------------------------------------------
# Complexity heuristic
# ---------------------------------------------------------------------------

_LEX_REF_RE = re.compile(r"&[A-Za-z][A-Za-z0-9_]*")
_OUTER_JOIN_RE = re.compile(r"\(\s*\+\s*\)")


def _count_lex_refs(report) -> int:
    """Count Oracle Reports lexical-parameter references (&P_FOO style)
    across all SQL bodies. These are the trickiest things to translate."""
    n = 0
    for q in getattr(report, "queries", []) or []:
        sql = getattr(q, "sql", "") or ""
        n += len(_LEX_REF_RE.findall(sql))
    return n


def _count_outer_joins(report) -> int:
    """Count Oracle (+) outer-join markers across all SQL bodies."""
    n = 0
    for q in getattr(report, "queries", []) or []:
        sql = getattr(q, "sql", "") or ""
        n += len(_OUTER_JOIN_RE.findall(sql))
    return n


def compute_complexity(report) -> int:
    """Heuristic complexity score.

    queries * 5 + formulas * 3 + lex_refs * 10 + outer_joins * 2
    """
    queries = len(getattr(report, "queries", []) or [])
    formulas = len(getattr(report, "formulas", []) or [])
    lex_refs = _count_lex_refs(report)
    outer_joins = _count_outer_joins(report)
    return queries * 5 + formulas * 3 + lex_refs * 10 + outer_joins * 2


# ---------------------------------------------------------------------------
# Section diffs
# ---------------------------------------------------------------------------

def _index_by_name(items) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for it in items or []:
        nm = getattr(it, "name", None)
        if nm:
            out[nm] = it
    return out


def _param_signature(p) -> Tuple[str, str, int, str, str, bool]:
    return (
        getattr(p, "datatype", "") or "",
        getattr(p, "label", "") or "",
        int(getattr(p, "width", 0) or 0),
        str(getattr(p, "initial_value", "") or ""),
        str(getattr(p, "input_mask", "") or ""),
        bool(getattr(p, "display", True)),
    )


def _param_change_details(a, b) -> str:
    bits: List[str] = []
    if (getattr(a, "datatype", "") or "") != (getattr(b, "datatype", "") or ""):
        bits.append(f"datatype {a.datatype!r} -> {b.datatype!r}")
    if (getattr(a, "label", "") or "") != (getattr(b, "label", "") or ""):
        bits.append(f"label {a.label!r} -> {b.label!r}")
    if int(getattr(a, "width", 0) or 0) != int(getattr(b, "width", 0) or 0):
        bits.append(f"width {a.width} -> {b.width}")
    if str(getattr(a, "initial_value", "") or "") != str(getattr(b, "initial_value", "") or ""):
        bits.append(f"initial {a.initial_value!r} -> {b.initial_value!r}")
    if str(getattr(a, "input_mask", "") or "") != str(getattr(b, "input_mask", "") or ""):
        bits.append(f"mask {a.input_mask!r} -> {b.input_mask!r}")
    if bool(getattr(a, "display", True)) != bool(getattr(b, "display", True)):
        bits.append(f"display {a.display} -> {b.display}")
    return "; ".join(bits)


def _diff_parameters(ra, rb) -> Dict[str, Any]:
    a = _index_by_name(getattr(ra, "parameters", []) or [])
    b = _index_by_name(getattr(rb, "parameters", []) or [])
    only_a = sorted(set(a) - set(b))
    only_b = sorted(set(b) - set(a))
    in_both: List[Dict[str, Any]] = []
    for name in sorted(set(a) & set(b)):
        sa = _param_signature(a[name])
        sb = _param_signature(b[name])
        changed = sa != sb
        in_both.append({
            "name": name,
            "changed": changed,
            "details": _param_change_details(a[name], b[name]) if changed else "",
        })
    return {"only_in_a": only_a, "only_in_b": only_b, "in_both": in_both}


def _unified_diff(text_a: str, text_b: str, label_a: str, label_b: str) -> str:
    a_lines = (text_a or "").splitlines(keepends=True)
    b_lines = (text_b or "").splitlines(keepends=True)
    if not a_lines and not b_lines:
        return ""
    diff = difflib.unified_diff(
        a_lines, b_lines, fromfile=label_a, tofile=label_b, lineterm=""
    )
    return "".join(diff)


def _query_complexity(q) -> int:
    sql = getattr(q, "sql", "") or ""
    lex = len(_LEX_REF_RE.findall(sql))
    oj = len(_OUTER_JOIN_RE.findall(sql))
    # rough per-query score: length of body + lex/outer-join weight
    return len(sql.splitlines()) + lex * 5 + oj * 2


def _formula_complexity(f) -> int:
    body = getattr(f, "plsql_body", "") or ""
    return len(body.splitlines())


def _diff_named_bodies(items_a, items_b, body_attr: str, complexity_fn) -> Dict[str, Any]:
    a = _index_by_name(items_a or [])
    b = _index_by_name(items_b or [])
    only_a = sorted(set(a) - set(b))
    only_b = sorted(set(b) - set(a))
    in_both: List[Dict[str, Any]] = []
    for name in sorted(set(a) & set(b)):
        ta = getattr(a[name], body_attr, "") or ""
        tb = getattr(b[name], body_attr, "") or ""
        in_both.append({
            "name": name,
            "sql_unified_diff": _unified_diff(ta, tb, f"a/{name}", f"b/{name}"),
            "complexity_a": complexity_fn(a[name]),
            "complexity_b": complexity_fn(b[name]),
        })
    return {"only_in_a": only_a, "only_in_b": only_b, "in_both": in_both}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compare_reports(report_a, report_b) -> Dict[str, Any]:
    """Compare two ParsedReport objects. Returns a JSON-serializable dict."""
    name_a = getattr(report_a, "name", "") or "Report A"
    name_b = getattr(report_b, "name", "") or "Report B"

    params = _diff_parameters(report_a, report_b)
    queries = _diff_named_bodies(
        getattr(report_a, "queries", []) or [],
        getattr(report_b, "queries", []) or [],
        body_attr="sql",
        complexity_fn=_query_complexity,
    )
    formulas = _diff_named_bodies(
        getattr(report_a, "formulas", []) or [],
        getattr(report_b, "formulas", []) or [],
        body_attr="plsql_body",
        complexity_fn=_formula_complexity,
    )

    ca = compute_complexity(report_a)
    cb = compute_complexity(report_b)

    # Summary line
    p_changed = sum(1 for p in params["in_both"] if p["changed"])
    q_changed = sum(
        1 for q in queries["in_both"] if q["sql_unified_diff"]
    )
    f_changed = sum(
        1 for f in formulas["in_both"] if f["sql_unified_diff"]
    )
    summary = (
        f"{name_a} vs {name_b}: "
        f"params +{len(params['only_in_b'])}/-{len(params['only_in_a'])}/~{p_changed}, "
        f"queries +{len(queries['only_in_b'])}/-{len(queries['only_in_a'])}/~{q_changed}, "
        f"formulas +{len(formulas['only_in_b'])}/-{len(formulas['only_in_a'])}/~{f_changed}, "
        f"complexity {ca} -> {cb} (delta {cb - ca:+d})"
    )

    return {
        "name_a": name_a,
        "name_b": name_b,
        "parameters": params,
        "queries": queries,
        "formulas": formulas,
        "complexity_score": {"a": ca, "b": cb, "delta": cb - ca},
        "summary": summary,
    }
