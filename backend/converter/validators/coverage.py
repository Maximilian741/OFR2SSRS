"""CONTENT-COVERAGE CONTRACT — the converter audits its own output and
says out loud what did not make it across.

Content has gone missing quietly more than once: a title segment, a
subtitle line, twenty-one margin fields, five detail columns, an entire
operator header frame. Every one was invisible because nothing asserted
that source content REACHES the artifact — and a fidelity *score* can
drift a point without anyone noticing.

So the rule here is binary, per visible data-bound layout field:

    ACCOUNTED   its data is reachable from the emitted RDL, or
    DECLARED    we tell the user, in plain language, that it is not.

There is no silent third category. A field that vanishes without a word
is the failure mode this module exists to make impossible.

Disclosures are AMBER, never BLOCKER: a dropped field is a fidelity gap
to disclose, not a reason to refuse a report that otherwise deploys and
runs.
"""
from __future__ import annotations

import re
from typing import Dict, List, Tuple

_BUILTIN_SOURCES = {
    "CURRENTDATE", "CURRENT_DATE", "PAGENUMBER", "TOTALPAGES",
    "PHYSICALPAGENUMBER", "LOGICALPAGENUMBER", "TOTALPHYSICALPAGES",
    "TOTALLOGICALPAGES", "PANELNUMBER", "TOTALPANELS",
}


def visible_data_fields(report) -> List[Tuple[str, str]]:
    """``[(field_name, source)]`` for every VISIBLE, data-bound layout
    field. Hidden fields, drawing primitives and pure boilerplate carry no
    data a reader can lose, so they are out of scope."""
    out: List[Tuple[str, str]] = []

    def walk(groups):
        for g in (groups or []):
            for f in (getattr(g, "fields", None) or []):
                if (getattr(f, "kind", "") or "") != "field":
                    continue
                if not getattr(f, "visible", True):
                    continue
                src = (getattr(f, "source", "") or "").strip()
                if src:
                    out.append(((getattr(f, "name", "") or ""), src))
            walk(getattr(g, "children", None) or [])

    walk(getattr(report, "layout", None) or [])
    return out


def _safe(name: str) -> str:
    return re.sub(r"\W", "_", name or "")


def is_accounted(src: str, rdl: str, report) -> bool:
    """True when this source's DATA is reachable from the emitted RDL."""
    s, safe = re.escape(src), re.escape(_safe(src))
    for pat in (rf"Fields!{s}\b", rf"Fields!{safe}\b",
                rf"Parameters!{s}\b", rf"Parameters!P_{s}\b",
                rf"DataField>{s}<", rf"DataField>{safe}<"):
        if re.search(pat, rdl, re.IGNORECASE):
            return True

    # An Oracle <summary> is accounted for when its UNDERLYING column is
    # referenced — we re-implement it as a real aggregate or lookup.
    for q in (getattr(report, "queries", None) or []):
        stack = list(getattr(q, "groups", None) or [])
        while stack:
            g = stack.pop()
            for sm in (getattr(g, "summaries", None) or []):
                if (sm.get("name") or "").upper() == src.upper():
                    u = _safe(sm.get("source") or "")
                    if u and re.search(rf"Fields!{re.escape(u)}\b", rdl,
                                       re.IGNORECASE):
                        return True
            stack.extend(getattr(g, "children", None) or [])

    by_name: Dict[str, object] = {
        (getattr(f, "name", "") or "").upper(): f
        for f in (getattr(report, "formulas", None) or [])}
    f = by_name.get(src.upper())
    if f is not None:
        agg_src = (getattr(f, "agg_source", "") or "").strip()
        if agg_src and re.search(rf"Fields!{re.escape(_safe(agg_src))}\b",
                                 rdl, re.IGNORECASE):
            return True
        # TRANSITIVE: an Oracle auto-summary (SumCF_XPerY) may aggregate a
        # FORMULA column, which this converter compiles INLINE into an
        # expression over that formula's own base columns. The summary
        # name never appears in the RDL, yet the data is fully there — so
        # follow one hop and look for those base columns.
        inner = by_name.get(agg_src.upper()) or f
        body = getattr(inner, "plsql_body", "") or ""
        if body:
            cols = {c.upper() for c in re.findall(r":([A-Za-z_]\w*)", body)}
            for q in (getattr(report, "queries", None) or []):
                for it in (getattr(q, "items", None) or []):
                    nm = getattr(it, "name", "") or ""
                    if nm.upper() in cols and re.search(
                            rf"Fields!{re.escape(_safe(nm))}\b", rdl,
                            re.IGNORECASE):
                        return True

    if src.upper() in _BUILTIN_SOURCES:
        return bool(re.search(r"Globals!", rdl))
    return False


def unaccounted_fields(report, rdl: str) -> List[Tuple[str, str]]:
    """Visible data fields whose data did not reach the RDL."""
    seen = set()
    out: List[Tuple[str, str]] = []
    for name, src in visible_data_fields(report):
        key = src.upper()
        if key in seen:
            continue
        seen.add(key)
        if not is_accounted(src, rdl, report):
            out.append((name, src))
    return out


def coverage_issues(report, rdl: str) -> List[Dict[str, str]]:
    """AMBER disclosures naming every field that did not make it across."""
    missing = unaccounted_fields(report, rdl)
    if not missing:
        return []
    listed = ", ".join(f"{n or src} ({src})" for n, src in missing[:12])
    more = "" if len(missing) <= 12 else f" (+{len(missing) - 12} more)"
    return [{
        "severity": "AMBER",
        "rule": "fidelity.unmapped_source_fields",
        "message": (
            f"{len(missing)} visible source field(s) have no data binding "
            f"in the generated report — their values will be blank: "
            f"{listed}{more}. Each is either an Oracle construct with no "
            f"SSRS equivalent (a formula over another formula, a dropped "
            f"header frame) or a value this converter could not resolve. "
            f"Wire them up in Report Builder, or ask for them to be added."
        ),
    }]
