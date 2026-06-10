"""Batch migration + Migration Assessment.

Convert MANY Oracle reports in one pass and produce the artifact a
migration consultancy bills weeks for: a per-report verdict table
(upload-readiness, fidelity score, effort tier, concrete reasons) plus an
executive summary — and a zip with every generated RDL.

Effort tiers (deterministic, derived from the converter's own signals):
  automatic    preflight READY, fidelity >= 0.90, no blockers
  light-touch  preflight READY, fidelity >= 0.70
  assisted     converts, but carries blockers / low fidelity
  manual       conversion failed (engine fallback shipped)

Optionally each RDL is rendered through Microsoft's actual report engine
(tools/renderlab) and the verdict (pages / blank pages / warnings) is
stamped into the assessment — verification competitors don't have.
"""
from __future__ import annotations

import html
import io
import json
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import convert
from .licensing import batch_limit, tier_label


def _safe_name(s: str) -> str:
    return "".join(c for c in (s or "report") if c.isalnum() or c in "._-") \
        or "report"


def _renderlab():
    """Lazy renderlab import; returns (render_rdl, ok) or (None, False)."""
    try:
        import sys
        root = Path(__file__).resolve().parents[2]
        rl = root / "tools" / "renderlab"
        if str(rl) not in sys.path:
            sys.path.insert(0, str(rl))
        from render import render_rdl, lib_ready  # type: ignore
        return (render_rdl, bool(lib_ready()))
    except Exception:  # noqa: BLE001
        return (None, False)


def _effort(verdict: str, fidelity: Optional[float],
            blockers: int, reds: int) -> str:
    if verdict == "ERROR":
        return "manual"
    fid = fidelity if isinstance(fidelity, (int, float)) else 0.0
    if verdict == "READY" and fid >= 0.90 and blockers == 0:
        return "automatic"
    if verdict == "READY" and fid >= 0.70 and blockers == 0:
        return "light-touch"
    return "assisted"


def _measure_pdf(pdf_path: str) -> Dict[str, Any]:
    try:
        from pypdf import PdfReader
        r = PdfReader(pdf_path)
        blanks = []
        for i, page in enumerate(r.pages):
            txt = (page.extract_text() or "").strip()
            residual = "".join(
                ln for ln in txt.splitlines()
                if not ln.strip().lower().startswith(("page ", "report run on"))
            ).strip()
            if len(residual) < 8:
                blanks.append(i + 1)
        return {"pages": len(r.pages), "blank_pages": blanks}
    except Exception as e:  # noqa: BLE001
        return {"pages": None, "blank_pages": [],
                "measure_error": f"{type(e).__name__}: {e}"}


def batch_convert(items: List[Tuple[str, bytes]],
                  target_db: str = "oracle",
                  render: bool = False,
                  limit: Optional[int] = "default") -> Dict[str, Any]:
    """Convert a list of (filename, xml_bytes). Returns
    {results, locked, tier, rendered} — results in input order."""
    if limit == "default":
        limit = batch_limit()
    render_fn, render_ok = _renderlab() if render else (None, False)

    results: List[Dict[str, Any]] = []
    locked: List[str] = []
    for idx, (fname, blob) in enumerate(items):
        stem = _safe_name(Path(fname).stem)
        if limit is not None and idx >= limit:
            locked.append(stem)
            continue
        row: Dict[str, Any] = {"name": stem, "source_file": fname}
        try:
            data = convert(blob, target_db=target_db)
            pf = data.get("preflight") or {}
            fid = (data.get("fidelity_report") or {})
            issues = pf.get("issues") or []
            blockers = sum(1 for i in issues
                           if (i.get("severity") or "").upper() == "BLOCKER")
            reds = sum(1 for i in issues
                       if (i.get("severity") or "").upper() == "RED")
            verdict = pf.get("verdict") or "?"
            if data.get("conversion_error"):
                verdict = "ERROR"
                row["error"] = data["conversion_error"]
            row.update({
                "ok": verdict != "ERROR",
                "verdict": verdict,
                "fidelity": fid.get("score"),
                "blockers": blockers,
                "reds": reds,
                "needs_attention": (fid.get("needs_attention") or [])[:3],
                "parameters": len((data.get("report") or {}).get("parameters") or []),
                "datasets": len((data.get("report") or {}).get("queries") or []),
                "subreports": [l.get("child_name")
                               for l in (data.get("subreport_links") or [])
                               if l.get("child_name")],
                "bursting": bool((data.get("bursting") or {}).get("is_bursting")),
                "rdl_xml": data.get("rdl_xml") or "",
            })
            row["effort"] = _effort(verdict, row["fidelity"], blockers, reds)
        except Exception as e:  # noqa: BLE001
            row.update({"ok": False, "verdict": "ERROR", "effort": "manual",
                        "error": f"{type(e).__name__}: {e}", "rdl_xml": ""})
        # Optional: real-engine render verification.
        if render_ok and row.get("rdl_xml"):
            try:
                with tempfile.TemporaryDirectory(prefix="o2s_batch_") as td:
                    rp = Path(td) / (stem + ".rdl")
                    rp.write_text(row["rdl_xml"], encoding="utf-8")
                    res = render_fn(rp, Path(td) / (stem + ".pdf"), rows=3)
                    row["render_ok"] = bool(res.get("ok"))
                    row["render_warnings"] = sum(
                        1 for ln in (res.get("log") or "").splitlines()
                        if ln.startswith("WARN"))
                    if res.get("ok") and res.get("pdf"):
                        row.update(_measure_pdf(res["pdf"]))
                    if not res.get("ok"):
                        row["effort"] = "assisted" \
                            if row["effort"] in ("automatic", "light-touch") \
                            else row["effort"]
            except Exception as e:  # noqa: BLE001
                row["render_ok"] = None
                row["render_error"] = f"{type(e).__name__}: {e}"
    # (loop end)
        results.append(row)
    return {"results": results, "locked": locked,
            "tier": tier_label(), "rendered": bool(render_ok and render)}


# ---------------------------------------------------------------------------
# Assessment artifact
# ---------------------------------------------------------------------------

_TIER_COLORS = {"automatic": "#15803d", "light-touch": "#2563eb",
                "assisted": "#b45309", "manual": "#b91c1c"}


def build_assessment_html(batch: Dict[str, Any],
                          title: str = "Oracle Reports → SSRS Migration Assessment") -> str:
    rs = batch.get("results") or []
    counts = {t: sum(1 for r in rs if r.get("effort") == t)
              for t in ("automatic", "light-touch", "assisted", "manual")}
    total = len(rs)
    rendered = batch.get("rendered")
    esc = html.escape

    def pct(n):
        return f"{(100 * n / total):.0f}%" if total else "0%"

    rows_html = []
    for r in rs:
        fid = r.get("fidelity")
        fid_s = f"{fid:.2f}" if isinstance(fid, (int, float)) else "—"
        eff = r.get("effort") or "?"
        color = _TIER_COLORS.get(eff, "#334155")
        render_s = "—"
        if rendered:
            if r.get("render_ok") is True:
                blanks = r.get("blank_pages") or []
                render_s = (f"✓ {r.get('pages', '?')} pages"
                            + (f", BLANK {blanks}" if blanks else ""))
            elif r.get("render_ok") is False:
                render_s = "✗ engine rejected"
        notes = "; ".join(
            esc(str(n)) for n in (r.get("needs_attention") or []))
        if r.get("error"):
            notes = esc(str(r["error"])[:140])
        subs = ", ".join(r.get("subreports") or [])
        extra = []
        if subs:
            extra.append("drill-through → " + esc(subs))
        if r.get("bursting"):
            extra.append("bursting detected")
        if extra:
            notes = (notes + " | " if notes else "") + "; ".join(extra)
        rows_html.append(
            "<tr>"
            f"<td>{esc(r.get('name', '?'))}</td>"
            f"<td style='color:{color};font-weight:600'>{esc(eff)}</td>"
            f"<td>{esc(str(r.get('verdict', '?')))}</td>"
            f"<td style='text-align:center'>{fid_s}</td>"
            f"<td style='text-align:center'>{r.get('datasets', '—')}</td>"
            f"<td style='text-align:center'>{r.get('parameters', '—')}</td>"
            f"<td>{esc(render_s)}</td>"
            f"<td style='font-size:11px;color:#475569'>{notes}</td>"
            "</tr>")
    for name in (batch.get("locked") or []):
        rows_html.append(
            "<tr style='opacity:.55'>"
            f"<td>{esc(name)}</td>"
            "<td colspan='7'>not converted — over the "
            f"{esc(batch.get('tier', ''))} batch limit</td></tr>")

    summary_cells = "".join(
        f"<div class='stat'><div class='n' style='color:{_TIER_COLORS[t]}'>"
        f"{counts[t]}</div><div class='l'>{t}<br><span>{pct(counts[t])}"
        f"</span></div></div>"
        for t in ("automatic", "light-touch", "assisted", "manual"))

    verified_line = (
        "<p class='verified'>Every converted report below was additionally "
        "rendered through <b>Microsoft's ReportViewer processing engine</b> "
        "(the same RDL code path SSRS executes) with synthetic data; page "
        "cadence and blank-page checks come from the actual rendered "
        "PDFs.</p>" if rendered else "")

    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>{esc(title)}</title>
<style>
 body{{font-family:Segoe UI,Arial,sans-serif;color:#0f172a;margin:32px;
      max-width:1100px}}
 h1{{font-size:22px;margin-bottom:2px}}
 .sub{{color:#64748b;font-size:13px;margin-bottom:18px}}
 .stats{{display:flex;gap:14px;margin:18px 0}}
 .stat{{border:1px solid #e2e8f0;border-radius:10px;padding:12px 18px;
       text-align:center;min-width:110px}}
 .stat .n{{font-size:26px;font-weight:700}}
 .stat .l{{font-size:12px;color:#475569;text-transform:capitalize}}
 .stat .l span{{color:#94a3b8}}
 table{{border-collapse:collapse;width:100%;font-size:13px;margin-top:10px}}
 th{{text-align:left;background:#f1f5f9;padding:7px 9px;font-size:12px;
    text-transform:uppercase;letter-spacing:.03em;color:#475569}}
 td{{padding:7px 9px;border-bottom:1px solid #e2e8f0;vertical-align:top}}
 .verified{{background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;
           padding:10px 14px;font-size:13px}}
 .foot{{margin-top:26px;color:#94a3b8;font-size:11.5px}}
</style></head><body>
<h1>{esc(title)}</h1>
<div class="sub">{total} report(s) analyzed · generated by Oracle2SSRS
 ({esc(batch.get('tier', ''))})</div>
<div class="stats">{summary_cells}</div>
{verified_line}
<table>
<tr><th>Report</th><th>Effort</th><th>Upload check</th><th>Fidelity</th>
<th>Datasets</th><th>Params</th><th>Engine render</th><th>Notes</th></tr>
{''.join(rows_html)}
</table>
<div class="foot">Effort tiers are derived from the converter's own
deterministic signals (upload preflight, source→RDL fidelity scoring,
publish-rule checks). “automatic” = upload as-is; “light-touch” = upload
then complete the listed items in Report Builder; “assisted” = review the
flagged blockers; “manual” = the source needs hands-on conversion.</div>
</body></html>"""


def build_batch_zip(batch: Dict[str, Any]) -> bytes:
    """Zip: every generated RDL + ASSESSMENT.html + assessment.json."""
    buf = io.BytesIO()
    slim = []
    used: set = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for r in batch.get("results") or []:
            if r.get("rdl_xml"):
                base = r["name"]
                entry, n = f"rdl/{base}.rdl", 2
                while entry in used:                  # distinct same-stem files
                    entry, n = f"rdl/{base}_{n}.rdl", n + 1
                used.add(entry)
                z.writestr(entry, r["rdl_xml"])
            slim.append({k: v for k, v in r.items() if k != "rdl_xml"})
        z.writestr("ASSESSMENT.html", build_assessment_html(batch))
        z.writestr("assessment.json", json.dumps(
            {**{k: v for k, v in batch.items() if k != "results"},
             "results": slim}, indent=2, default=str))
    return buf.getvalue()


__all__ = ["batch_convert", "build_assessment_html", "build_batch_zip"]
