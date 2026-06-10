"""Fidelity report -- a self-check the converter runs on its OWN output.

The XSD/preflight gates answer "will it upload?". This answers the other half:
"is it a faithful 1:1 copy, and what still needs manual wiring?". It parses the
generated RDL back and compares it to the parsed Oracle source, so the user
always knows -- per report -- exactly what was preserved and what to check. The
honest-tool counterpart to the upload gates; nothing is silently dropped.

Generic and structural -- no per-report logic.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, Iterable, List

RD = "{http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition}"


def _safe_up(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", (s or "")).upper()


def _walk_fields(groups) -> Iterable:
    for g in groups or []:
        for f in (getattr(g, "fields", None) or []):
            yield f
        yield from _walk_fields(getattr(g, "children", None) or [])


def _walk_summaries(groups) -> Iterable[dict]:
    for g in groups or []:
        for sm in (getattr(g, "summaries", None) or []):
            yield sm
        yield from _walk_summaries(getattr(g, "children", None) or [])


def build_fidelity_report(parsed, rdl_xml: str) -> Dict[str, Any]:
    """Return a structured source->RDL coverage report for one conversion."""
    try:
        root = ET.fromstring(rdl_xml)
    except Exception:
        root = None

    rdl_params: set = set()
    rdl_datafields: set = set()
    rdl_field_names: set = set()
    rdl_refs: set = set()
    if root is not None:
        rdl_params = {(rp.get("Name") or "").upper() for rp in root.iter(RD + "ReportParameter")}
        rdl_datafields = {(df.text or "").upper() for df in root.iter(RD + "DataField")}
        rdl_field_names = {(f.get("Name") or "").upper() for f in root.iter(RD + "Field")}
        rdl_refs = {m.upper() for m in re.findall(r"Fields!([A-Za-z0-9_]+)\.Value", rdl_xml or "")}

    cats: Dict[str, Any] = {}
    needs: List[str] = []

    # 1) Parameters -> ReportParameters (HARD: each must survive).
    params = [p.name for p in (parsed.parameters or []) if getattr(p, "name", "")]
    p_dropped = [n for n in params
                 if n.upper() not in rdl_params and _safe_up(n) not in rdl_params]
    cats["parameters"] = {"preserved": len(params) - len(p_dropped),
                          "total": len(params), "dropped": p_dropped}
    if p_dropped:
        needs.append(f"{len(p_dropped)} parameter(s) not emitted as ReportParameter: {p_dropped}")

    # 2) Dataset columns (query items) -> a dataset Field (HARD: no silent drop).
    cols: List[str] = []
    for q in (parsed.queries or []):
        for it in (q.items or []):
            if getattr(it, "name", ""):
                cols.append(it.name)
    c_dropped = [c for c in cols
                 if c.upper() not in rdl_datafields and _safe_up(c) not in rdl_field_names]
    cats["columns"] = {"preserved": len(cols) - len(c_dropped),
                       "total": len(cols), "dropped": sorted(set(c_dropped))}
    if c_dropped:
        needs.append(f"{len(set(c_dropped))} source column(s) not bound to any dataset: "
                     f"{sorted(set(c_dropped))}")

    # 3) Layout data-bound fields -> referenced in the RDL (informational).
    lfields = [f for f in _walk_fields(getattr(parsed, "layout", None))
               if getattr(f, "kind", "") == "field" and (getattr(f, "source", "") or "").strip()]
    lsrcs = sorted({f.source for f in lfields})

    def _bound(src: str) -> bool:
        u, su = src.upper(), _safe_up(src)
        return any(x in rdl_refs or x in rdl_field_names or x in rdl_params
                   for x in (u, su))

    unbound = sorted({s for s in lsrcs if not _bound(s)
                      and not re.match(r"&?(CF|CP|P)_", s, re.I)
                      and not re.match(r"^&", s)})
    cats["layout_fields"] = {"bound": len(lsrcs) - len([s for s in lsrcs if not _bound(s)]),
                             "total": len(lsrcs), "unbound_nonformula": unbound}
    # A layout field is a column Oracle EXPLICITLY placed on the page. If
    # it is a real data column (declared by a query) yet appears nowhere in
    # the generated RDL, the generator dropped it from the display — a true
    # 1:1 miss (wild-corpus verified: a 54-column report that rendered 10).
    # Surfaced in needs_attention so it can never hide behind a 1.0 score
    # again. Excludes formula/param/lexical sources (handled separately).
    _col_up = {c.upper() for c in cols}
    real_unbound = [s for s in unbound if s.upper() in _col_up]
    if real_unbound:
        needs.append(
            f"{len(real_unbound)} data column(s) placed in the Oracle layout "
            f"are not displayed in the RDL — likely dropped: {real_unbound[:12]}")

    # 4) Oracle PL/SQL formulas (CF_/CP_) -> NULL placeholders (wireable 1:1).
    formula_srcs = sorted({s.lstrip("&") for s in lsrcs
                           if re.match(r"&?(CF|CP)_", s, re.I)})
    f_wired = [s for s in formula_srcs if _safe_up(s) in rdl_field_names]
    cats["formulas"] = {"wired": len(f_wired), "total": len(formula_srcs), "names": formula_srcs}
    if formula_srcs:
        needs.append(f"{len(formula_srcs)} Oracle PL/SQL formula(s) wired as NULL placeholders "
                     "(DS_REPORT_FORMULAS) -- supply the SQL/UDF at deploy time")

    # 5) Declared <summary> totals -> an aggregate expression (informational).
    summ: List[dict] = []
    for q in (parsed.queries or []):
        summ.extend(_walk_summaries(getattr(q, "groups", None) or []))
    SSRS_AGG = re.compile(r"(?:Sum|Avg|Count|CountDistinct|Min|Max|First|Last|StDev|Var)"
                          r"\(Fields!", re.I)
    n_agg = len(SSRS_AGG.findall(rdl_xml or ""))
    cats["summaries"] = {"declared": len(summ), "aggregates_in_rdl": n_agg}

    # HARD score = the must-not-drop categories (params + columns).
    hard_total = cats["parameters"]["total"] + cats["columns"]["total"]
    hard_kept = cats["parameters"]["preserved"] + cats["columns"]["preserved"]
    score = round(hard_kept / hard_total, 3) if hard_total else 1.0

    summary = (f"{cats['columns']['preserved']}/{cats['columns']['total']} columns + "
               f"{cats['parameters']['preserved']}/{cats['parameters']['total']} params bound"
               + (f"; {cats['formulas']['total']} formula(s) need wiring"
                  if cats["formulas"]["total"] else ""))

    return {
        "score": score,                 # 1.0 = no silent loss of columns/params
        "summary": summary,
        "categories": cats,
        "needs_attention": needs,
    }
