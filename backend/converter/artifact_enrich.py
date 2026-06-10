"""
Artifact enrichment helpers for parent-report bundle stacking.

These helpers are PURE / I/O-free. They mutate or build a ParsedReport using
the supporting artifacts that came along with the primary Oracle XML (or, in
the no-XML case, build a synthetic report richer than raw-SQL alone).

Public API:
    enrich_report_from_artifacts(report, classification) -> dict
        Mutate a ParsedReport in place using SQL artifacts (replace/extend),
        doc-derived column labels, and screenshot filename hints. Returns a
        small dict summarizing what was applied:
            {
              "sql_added": int,            # NEW DataQuery rows added
              "sql_replaced": int,         # existing query SQL upgraded
              "label_overrides": int,      # DataItem.label fields populated
              "hints": [str, ...],         # screenshot/layout hint notes
            }

    enrich_synthetic_from_artifacts(sql_files, docs, screenshots,
                                    bundle_label="BUNDLE") -> ParsedReport
        Build a ParsedReport from raw SQL files, then enrich with doc-derived
        labels and screenshot hints. Used when no Oracle XML is present.

The whole module is GENERIC -- no report names, no column names, no parameter
names hardcoded. Only structural patterns ("Q_*" query naming, "COLUMN: desc"
key/value scans) are used, and even those have fallbacks.
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Tuple

from .models import DataItem, DataQuery, ParsedReport


# ---------------------------------------------------------------------------
# Filename / name helpers (kept local so this module has zero deps on ingest)
# ---------------------------------------------------------------------------

_QUERY_PREFIX_RE = re.compile(r"^[Qq]_")
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_]")


def _basename(name: str) -> str:
    base = os.path.basename(name or "")
    # synthetic separator used by ingest for docx-embedded SQL blocks
    if "::" in base:
        base = base.rsplit("::", 1)[-1]
    return base


def _query_name_from_filename(filename: str) -> str:
    base = _basename(filename)
    stem, _ = os.path.splitext(base)
    safe = _SAFE_NAME_RE.sub("_", stem).strip("_")
    if not safe:
        safe = "Q_DOC"
    if not _QUERY_PREFIX_RE.match(safe):
        safe = "Q_" + safe
    return safe.upper()


def _select_column_count(sql: str) -> int:
    """Best-effort count of visible top-level SELECT columns.

    Used as a 'completeness' proxy when deciding whether an artifact's SQL
    should replace the parser's SQL. Counts commas at paren-depth zero
    between SELECT and FROM. Returns 0 when the shape doesn't look like a
    SELECT or we can't tell.
    """
    if not sql:
        return 0
    text = sql
    # Strip block + line comments so commas inside them don't inflate the count
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)
    text = re.sub(r"--[^\n]*", " ", text)
    m = re.search(r"\bSELECT\b(.*?)\bFROM\b", text, re.IGNORECASE | re.DOTALL)
    if not m:
        return 0
    body = m.group(1)
    depth = 0
    commas = 0
    in_s = False
    quote = ""
    for ch in body:
        if in_s:
            if ch == quote:
                in_s = False
            continue
        if ch in ("'", '"'):
            in_s = True
            quote = ch
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1 if depth > 0 else 0
        elif ch == "," and depth == 0:
            commas += 1
    # column count = commas + 1 (assuming at least one column)
    return commas + 1


def _sql_is_more_complete(candidate: str, existing: str) -> bool:
    """Decide whether to swap an existing query's SQL for the artifact's SQL.

    Heuristic: prefer the one with MORE visible select columns; ties broken
    by length. Empty existing always loses to non-empty candidate.
    """
    cand = (candidate or "").strip()
    exist = (existing or "").strip()
    if not cand:
        return False
    if not exist:
        return True
    cc = _select_column_count(cand)
    ec = _select_column_count(exist)
    if cc != ec:
        return cc > ec
    return len(cand) > len(exist)


# ---------------------------------------------------------------------------
# Doc label scan ("COLUMN: human label")
# ---------------------------------------------------------------------------

# Match a line like:   PERMIT_NO: The permit identifier
# or:                  permit_no - the permit identifier
# The "key" part must look like a column/identifier (uppercase or snake_case,
# 2-40 chars, may include digits). The "value" must have at least 2 chars.
_LABEL_LINE_RE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]{1,39})\s*[:\-–—]\s*(.{2,200})\s*$"
)


def _scan_doc_labels(text: str) -> Dict[str, str]:
    """Extract a flat {COLUMN: description} map from a free-text doc."""
    out: Dict[str, str] = {}
    if not text:
        return out
    for raw in text.splitlines():
        ln = raw.strip()
        if not ln:
            continue
        # Skip lines that look like SQL or code (would generate noise)
        if re.match(r"^(SELECT|FROM|WHERE|JOIN|GROUP|ORDER|HAVING|UNION|--|/\*)", ln, re.IGNORECASE):
            continue
        m = _LABEL_LINE_RE.match(ln)
        if not m:
            continue
        key = m.group(1).strip().upper()
        val = m.group(2).strip()
        # Skip when the "value" itself looks like SQL or contains parens of a function call
        if re.search(r"\bSELECT\b|\bFROM\b|\(.*\)", val, re.IGNORECASE):
            continue
        # First occurrence wins (docs usually define a term once)
        if key not in out:
            out[key] = val
    return out


def _label_is_blank_or_auto(item: DataItem) -> bool:
    """A label is considered overridable if it's empty, or it just equals the
    column name (or a TitleCase echo of it)."""
    lab = (item.label or "").strip()
    if not lab:
        return True
    nm = (item.name or "").strip()
    if not nm:
        return False
    if lab.lower() == nm.lower():
        return True
    # auto-derived "Hire Year" from "hire_year"
    derived = nm.replace("_", " ").strip().title()
    if lab == derived:
        return True
    return False


def _apply_doc_labels(report: ParsedReport, labels: Dict[str, str]) -> int:
    if not labels:
        return 0
    applied = 0
    upper = {k.upper(): v for k, v in labels.items()}
    for q in report.queries:
        for item in q.items:
            if not _label_is_blank_or_auto(item):
                continue
            key = (item.name or "").upper()
            if key in upper:
                item.label = upper[key]
                applied += 1
    return applied


# ---------------------------------------------------------------------------
# Screenshot hints
# ---------------------------------------------------------------------------

def _screenshot_hints(screenshots: List[Tuple[str, bytes, str]]) -> List[str]:
    hints: List[str] = []
    for entry in screenshots or []:
        try:
            fn = entry[0]
        except Exception:
            continue
        if not fn:
            continue
        base = _basename(fn)
        kind = entry[2] if len(entry) > 2 else "unknown"
        hints.append(f"layout hint: {kind or 'unknown'} :: {base}")
    return hints


# ---------------------------------------------------------------------------
# SQL artifact merge (used by both enrich paths)
# ---------------------------------------------------------------------------

def _apply_sql_artifacts(report: ParsedReport,
                         sql_files: List[Tuple[str, str]]) -> Tuple[int, int]:
    """Merge artifact SQL into report.queries.

    - If a query with a matching name already exists, replace its .sql only when
      the artifact looks more complete.
    - Otherwise append a NEW DataQuery for the artifact.

    Returns (added, replaced) counts.
    """
    added = 0
    replaced = 0
    if not sql_files:
        return added, replaced

    # Index existing queries by upper name for O(1) lookup.
    by_name: Dict[str, DataQuery] = {
        (q.name or "").upper(): q for q in report.queries if q.name
    }
    existing_names = set(by_name.keys())

    for fname, sql_text in sql_files:
        sql_clean = (sql_text or "").strip()
        if not sql_clean:
            continue
        candidate_name = _query_name_from_filename(fname)
        if candidate_name in by_name:
            existing = by_name[candidate_name]
            if _sql_is_more_complete(sql_clean, existing.sql):
                existing.sql = sql_clean
                existing.tsql = ""  # force re-translation downstream
                existing.add_warning(
                    f"SQL upgraded from artifact: {os.path.basename(fname)}"
                )
                replaced += 1
            continue
        # Unique name (avoid duplicates against ones added earlier this run)
        name = candidate_name
        n = 2
        while name in existing_names:
            name = f"{candidate_name}_{n}"
            n += 1
        existing_names.add(name)
        new_q = DataQuery(name=name, sql=sql_clean)
        new_q.add_warning(f"Added from bundle artifact: {os.path.basename(fname)}")
        report.queries.append(new_q)
        by_name[name] = new_q
        added += 1
    return added, replaced


# ---------------------------------------------------------------------------
# Public enrichers
# ---------------------------------------------------------------------------

def enrich_report_from_artifacts(report: ParsedReport,
                                 classification: Dict[str, Any]) -> Dict[str, Any]:
    """Mutate ``report`` in place using bundle artifacts. Returns a summary."""
    summary = {
        "sql_added": 0,
        "sql_replaced": 0,
        "label_overrides": 0,
        "hints": [],
    }
    if report is None or not classification:
        return summary

    sql_files = classification.get("sql_files") or []
    docs = classification.get("docs") or []
    screenshots = classification.get("screenshots") or []

    # 1) SQL artifacts: replace + add
    added, replaced = _apply_sql_artifacts(report, sql_files)
    summary["sql_added"] = added
    summary["sql_replaced"] = replaced

    # 2) Doc-derived labels
    merged_labels: Dict[str, str] = {}
    for _fn, txt in docs:
        for k, v in _scan_doc_labels(txt).items():
            if k not in merged_labels:
                merged_labels[k] = v
    summary["label_overrides"] = _apply_doc_labels(report, merged_labels)

    # 3) Screenshot filename hints
    hints = _screenshot_hints(screenshots)
    for h in hints:
        if h not in report.warnings:
            report.warnings.append(h)
    summary["hints"] = hints
    return summary


def enrich_synthetic_from_artifacts(sql_files: List[Tuple[str, str]],
                                    docs: List[Tuple[str, str]],
                                    screenshots: List[Tuple[str, bytes, str]],
                                    bundle_label: str = "BUNDLE") -> Tuple[ParsedReport, Dict[str, Any]]:
    """Build a richer ParsedReport from raw artifacts when no XML is present.

    Returns (report, enrichment_summary). enrichment_summary has the same shape
    as ``enrich_report_from_artifacts``.
    """
    # Derive a nicer report name from the first SQL filename if no explicit
    # bundle label was given (or it's the default placeholder).
    name = (bundle_label or "BUNDLE").strip() or "BUNDLE"
    if name == "BUNDLE":
        for fn, _ in sql_files or []:
            base = _basename(fn).split(".")[0]
            if base:
                name = _SAFE_NAME_RE.sub("_", base).upper().strip("_") or "BUNDLE"
                break
    report = ParsedReport(name=name, dtd_version="(synthetic)")

    summary = {
        "sql_added": 0,
        "sql_replaced": 0,
        "label_overrides": 0,
        "hints": [],
    }

    # 1) Build the queries from raw SQL artifacts
    added, replaced = _apply_sql_artifacts(report, sql_files or [])
    summary["sql_added"] = added
    summary["sql_replaced"] = replaced

    if not report.queries:
        report.warnings.append(
            "No SQL queries could be extracted from the bundle."
        )
    else:
        report.warnings.append(
            "Synthetic report: built from raw artifacts (no Oracle XML provided)."
        )

    # 2) Doc-derived labels (operate against whatever queries we just built;
    # column items are usually empty for synthetic reports, so labels may not
    # find a home -- still useful to capture them as warnings for the UI).
    merged_labels: Dict[str, str] = {}
    for _fn, txt in docs or []:
        for k, v in _scan_doc_labels(txt).items():
            if k not in merged_labels:
                merged_labels[k] = v
    applied = _apply_doc_labels(report, merged_labels)
    summary["label_overrides"] = applied
    if merged_labels and applied == 0:
        # Surface the captured labels so the user can see we noticed them
        report.warnings.append(
            f"Captured {len(merged_labels)} column label(s) from docs "
            f"(no matching data items yet)."
        )

    # 3) Screenshot hints
    hints = _screenshot_hints(screenshots or [])
    for h in hints:
        if h not in report.warnings:
            report.warnings.append(h)
    summary["hints"] = hints

    return report, summary


__all__ = [
    "enrich_report_from_artifacts",
    "enrich_synthetic_from_artifacts",
]
