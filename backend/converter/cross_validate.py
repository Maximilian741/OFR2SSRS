"""
Cross-validation between the primary Oracle XML and supporting artifacts
(SQL docx, screenshots, rendered PDF).

The point: catch parser drift / missing queries / unexpected page counts
BEFORE the user trusts the conversion. Every check produces a structured
finding with severity (info/warning/error) so the UI can render them.

This module is purely deterministic — no LLM, no network. Re-running on the
same inputs produces the same output every time.

Public API:
    cross_validate(report, supporting) -> dict
        report:     a ParsedReport
        supporting: dict from ingest.classify_files() result. Specifically:
                    - "sql_files":  [(filename, text), ...]   text from sql.docx
                    - "docs":       [(filename, text), ...]
                    - "screenshots":[(filename, bytes, kind), ...]
                    - "pdfs":       [(filename, bytes), ...]   (added by us)

Returns:
    {
      "sql_doc":     {"checked": bool, "findings": [...], "stats": {...}},
      "pdf":         {"checked": bool, "findings": [...], "stats": {...}},
      "screenshots": {"checked": bool, "findings": [...], "stats": {...}},
      "summary":     {"info": int, "warning": int, "error": int}
    }
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# SQL doc cross-check
# ---------------------------------------------------------------------------

# Oracle Reports queries are conventionally named Q_<UPPERCASE> (Q_PERMIT,
# Q_ORG, etc.). Any line that looks like a bare query name on its own line
# is treated as a section heading.
_QNAME_HEADING = re.compile(r"^\s*(Q_[A-Z][A-Z0-9_]*)\s*$")
_FORMULA_HEADING = re.compile(r"^\s*(CF_[A-Z][A-Z0-9_]*)\s*$")
_PROC_HEADING = re.compile(r"^\s*([A-Z][A-Z0-9_]+)\s+Trigger\s*$", re.IGNORECASE)


def _extract_sql_blocks(text: str) -> Dict[str, str]:
    """Walk a SQL docx's plain text and split it into named blocks keyed by
    the section heading (Q_PERMIT, Q_ORG, CF_File, etc.).

    Returns: {block_name: block_text}
    """
    blocks: Dict[str, str] = {}
    cur_name = None
    cur_lines: List[str] = []
    for line in (text or "").splitlines():
        m = _QNAME_HEADING.match(line) or _FORMULA_HEADING.match(line)
        if m:
            if cur_name and cur_lines:
                blocks[cur_name] = "\n".join(cur_lines).strip()
            cur_name = m.group(1).upper()
            cur_lines = []
            continue
        if cur_name:
            cur_lines.append(line)
    if cur_name and cur_lines:
        blocks[cur_name] = "\n".join(cur_lines).strip()
    return blocks


def _check_sql_doc(report, sql_text_blocks: List[Tuple[str, str]]) -> Dict[str, Any]:
    """Compare the queries/formulas the parser extracted to those listed in
    the supporting SQL doc(s)."""
    findings: List[Dict[str, Any]] = []

    # ingest.py pre-splits docx into one entry per query named like
    # "<filename>::Q_PERMIT". So the input is already a flat (qname, sql) list.
    # We still try _extract_sql_blocks on entries that look like a whole doc
    # (no '::' marker), as a fallback.
    doc_blocks: Dict[str, str] = {}
    for fname, text in (sql_text_blocks or []):
        if "::" in (fname or ""):
            qname = fname.rsplit("::", 1)[1].strip().upper()
            if qname:
                doc_blocks.setdefault(qname, text or "")
        else:
            for k, v in _extract_sql_blocks(text or "").items():
                doc_blocks.setdefault(k, v)

    parser_query_names = {q.name.upper() for q in (report.queries or []) if q.name}
    parser_formula_names = {f.name.upper() for f in (report.formulas or []) if f.name}

    doc_q_names = {k for k in doc_blocks if k.startswith("Q_")}
    doc_f_names = {k for k in doc_blocks if k.startswith("CF_")}

    # Queries in doc but not parsed
    for name in sorted(doc_q_names - parser_query_names):
        findings.append({
            "severity": "warning",
            "rule": "sql_doc.query_missing_from_parser",
            "subject": name,
            "message": f"SQL doc lists {name} but the XML parser didn't extract it. "
                       "Either the doc is stale or the parser missed something.",
        })

    # We intentionally do NOT flag "parser has extras" — that's almost always
    # noise from doc lag rather than a real defect. Only the inverse direction
    # (doc has things parser missed) is actionable.

    # Formula coverage — only warn if doc lists more than parser
    for name in sorted(doc_f_names - parser_formula_names):
        findings.append({
            "severity": "warning",
            "rule": "sql_doc.formula_missing_from_parser",
            "subject": name,
            "message": f"SQL doc references formula {name}; parser didn't find it.",
        })

    # Per-query column-count check (very lightweight). For each Q_* both
    # the doc and the parser have, count distinct column references and warn
    # if they're off by > 30%.
    for name in (parser_query_names & doc_q_names):
        q = next((q for q in report.queries if q.name.upper() == name), None)
        if not q:
            continue
        parser_col_count = len(q.items or [])
        # Crude: count commas at top-level after SELECT in the doc's block
        block = doc_blocks.get(name, "")
        m = re.search(r"\bSELECT\b(.*?)\bFROM\b", block, re.IGNORECASE | re.DOTALL)
        doc_col_count = 0
        if m:
            select_clause = m.group(1)
            # Strip comments
            select_clause = re.sub(r"/\*.*?\*/", "", select_clause, flags=re.DOTALL)
            select_clause = re.sub(r"--[^\n]*", "", select_clause)
            # Top-level comma split
            depth = 0
            count = 1
            for ch in select_clause:
                if ch == "(": depth += 1
                elif ch == ")": depth -= 1
                elif ch == "," and depth == 0:
                    count += 1
            doc_col_count = count

        if parser_col_count and doc_col_count:
            diff_ratio = abs(parser_col_count - doc_col_count) / max(parser_col_count, doc_col_count)
            if diff_ratio > 0.30:
                findings.append({
                    "severity": "warning",
                    "rule": "sql_doc.column_count_mismatch",
                    "subject": name,
                    "message": f"{name}: parser found {parser_col_count} columns, "
                               f"SQL doc shows ~{doc_col_count}. >30% drift suggests "
                               "the parser missed projection items or the doc is stale.",
                })

    return {
        "checked": bool(sql_text_blocks),
        "stats": {
            "doc_blocks": len(doc_blocks),
            "doc_queries": len(doc_q_names),
            "doc_formulas": len(doc_f_names),
            "parser_queries": len(parser_query_names),
            "parser_formulas": len(parser_formula_names),
            "intersection_queries": len(parser_query_names & doc_q_names),
        },
        "findings": findings,
    }


# ---------------------------------------------------------------------------
# PDF cross-check
# ---------------------------------------------------------------------------

def _check_pdf(report, pdf_blobs: List[Tuple[str, bytes]],
               bursting_info: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    findings: List[Dict[str, Any]] = []
    if not pdf_blobs:
        return {"checked": False, "stats": {}, "findings": []}

    page_total = 0
    pdfs_processed = 0
    try:
        import pypdf  # optional
    except ImportError:
        return {
            "checked": False,
            "stats": {"pdfs_seen": len(pdf_blobs)},
            "findings": [{
                "severity": "info",
                "rule": "pdf.module_not_installed",
                "subject": "pypdf",
                "message": "Install pypdf to enable PDF page-count cross-check (pip install pypdf).",
            }],
        }

    import io
    for fname, blob in pdf_blobs:
        try:
            r = pypdf.PdfReader(io.BytesIO(blob))
            page_total += len(r.pages)
            pdfs_processed += 1
        except Exception as e:  # noqa: BLE001
            findings.append({
                "severity": "info",
                "rule": "pdf.unreadable",
                "subject": fname,
                "message": f"Could not read PDF: {type(e).__name__}: {e}",
            })

    is_bursting = bool(bursting_info and bursting_info.get("is_bursting"))

    # Heuristic: > 1 page strongly suggests bursting (per-recipient pages).
    # If parser DIDN'T flag bursting but the rendered PDF has many pages, flag it.
    if pdfs_processed and page_total > 1 and not is_bursting:
        findings.append({
            "severity": "warning",
            "rule": "pdf.bursting_undetected",
            "subject": "pages",
            "message": f"Rendered PDF has {page_total} pages, but the converter "
                       f"didn't flag this report as bursting. Manual review "
                       f"recommended — there may be distribution logic the "
                       f"parser missed (P_AS_PATH, CF_File_F, etc.).",
        })

    # Reverse: parser flagged bursting but PDF is single-page — also worth a note
    if pdfs_processed and page_total == 1 and is_bursting:
        findings.append({
            "severity": "info",
            "rule": "pdf.single_page_but_bursting",
            "subject": "pages",
            "message": "PDF is single-page but report is flagged as bursting. "
                       "May mean the test run only had 1 recipient, or the "
                       "bursting logic is conditional.",
        })

    return {
        "checked": pdfs_processed > 0,
        "stats": {
            "pdfs_seen": len(pdf_blobs),
            "pdfs_processed": pdfs_processed,
            "page_total": page_total,
            "is_bursting_detected": is_bursting,
        },
        "findings": findings,
    }


# ---------------------------------------------------------------------------
# Screenshot cross-check (very light — just count + report kinds present)
# ---------------------------------------------------------------------------

def _check_screenshots(report, screenshot_list: List[Tuple[str, bytes, str]]) -> Dict[str, Any]:
    findings: List[Dict[str, Any]] = []
    kinds = {}
    for entry in screenshot_list or []:
        # tuple may be (filename, bytes, kind) or just (filename, bytes)
        kind = entry[2] if len(entry) >= 3 else "unknown"
        kinds[kind] = kinds.get(kind, 0) + 1

    if not screenshot_list:
        return {"checked": False, "stats": {}, "findings": []}

    # If we got frontend screenshots but our parser found NO layout fields,
    # there's a parser issue.
    has_frontend = kinds.get("frontend", 0) > 0
    layout_field_count = sum(len(g.fields or []) for g in (report.layout or []))
    if has_frontend and layout_field_count == 0:
        findings.append({
            "severity": "warning",
            "rule": "screenshots.layout_empty_but_visual_provided",
            "subject": "layout",
            "message": "Frontend screenshots were provided but the parser found no "
                       "layout fields. The parser may have skipped sections it "
                       "couldn't understand — manual review of layout recommended.",
        })

    return {
        "checked": True,
        "stats": {
            "screenshots_total": len(screenshot_list),
            "by_kind": kinds,
            "parser_layout_fields": layout_field_count,
        },
        "findings": findings,
    }


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------

def cross_validate(report, supporting: Optional[Dict[str, Any]],
                   bursting_info: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Run all cross-checks. supporting may be None or a partial dict."""
    supporting = supporting or {}

    sql_doc      = _check_sql_doc(report, supporting.get("sql_files", []) or [])
    pdf_check    = _check_pdf(report, supporting.get("pdfs", []) or [], bursting_info)
    screenshots  = _check_screenshots(report, supporting.get("screenshots", []) or [])

    summary = {"info": 0, "warning": 0, "error": 0}
    for sec in (sql_doc, pdf_check, screenshots):
        for f in sec.get("findings", []):
            summary[f.get("severity", "info")] = summary.get(f.get("severity", "info"), 0) + 1

    return {
        "sql_doc":     sql_doc,
        "pdf":         pdf_check,
        "screenshots": screenshots,
        "summary":     summary,
    }


__all__ = ["cross_validate"]
