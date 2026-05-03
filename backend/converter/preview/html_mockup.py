"""
HTML mockup renderer.

Produces a self-contained HTML *fragment* (no <html>/<head>/<body>) that
visually approximates how the SSRS / Oracle Reports output should look.
The frontend injects this fragment into the "HTML Mockup" tab.

All styling is inline so the fragment can be embedded anywhere safely.

Real Oracle Reports / SSRS output is on plain white paper with pure black
ink and no color. We render strictly in black and white -- no tan, no
cream, no navy -- like a printed government form.
"""
from __future__ import annotations

import html
from typing import List

from ..models import ParsedReport, DataQuery, ReportParameter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _esc(s):
    return html.escape("" if s is None else str(s), quote=True)


def _pick_main_query(report):
    if not report.queries:
        return None
    for q in report.queries:
        if q.name and q.name.upper() == "Q_PERMIT":
            return q
    return max(report.queries, key=lambda q: len(q.items or []))


def _column_labels(query):
    if not query or not query.items:
        return ["Permit", "Facility Name", "City", "Owner", "Renewal Year", "Status"]
    cols = []
    for item in query.items:
        label = (item.label or item.name or "").strip()
        if not label:
            continue
        cols.append(label)
    return cols or ["Column 1", "Column 2", "Column 3"]


def _sample_rows(columns):
    canned = {
        "permit": ["MV-2026-0117", "MV-2026-0231"],
        "facility": ["City Auto Wreckers - Bozeman", "Big Sky Salvage Yard - Billings"],
        "facility name": ["City Auto Wreckers - Bozeman", "Big Sky Salvage Yard - Billings"],
        "name": ["City Auto Wreckers - Bozeman", "Big Sky Salvage Yard - Billings"],
        "city": ["Bozeman", "Billings"],
        "address": ["1442 Industrial Dr", "808 Yellowstone Ave"],
        "site addr": ["1442 Industrial Dr, Bozeman, MT 59715", "808 Yellowstone Ave, Billings, MT 59101"],
        "owner": ["Joseph T. Reilly", "Maria L. Hendricks"],
        "permittee": ["Reilly, Joseph T.", "Hendricks, Maria L."],
        "renewal year": ["2026", "2026"],
        "year": ["2026", "2026"],
        "status": ["Active", "Active"],
        "phone": ["(406) 555-0144", "(406) 555-0299"],
        "email": ["permits@cityautowreckers.example", "office@bigskysalvage.example"],
        "zip": ["59715", "59101"],
        "state": ["MT", "MT"],
        "county": ["Gallatin", "Yellowstone"],
        "expires": ["12/31/2026", "12/31/2026"],
        "issued": ["01/05/2026", "01/12/2026"],
        "fee": ["$250.00", "$250.00"],
        "perm dates": ["JANUARY 5, 2026 TO DECEMBER 31, 2026", "JANUARY 12, 2026 TO DECEMBER 31, 2026"],
        "perm type": ["MOTOR VEHICLE WRECKING FACILITY LICENSE", "MOTOR VEHICLE WRECKING FACILITY LICENSE"],
    }
    fallback_a = ["A-001", "Sample Co. A", "Helena", "Owner A", "2026", "Active"]
    fallback_b = ["A-002", "Sample Co. B", "Missoula", "Owner B", "2026", "Active"]

    row_a, row_b = [], []
    for i, col in enumerate(columns):
        key = col.lower().replace("_", " ").strip()
        if key in canned:
            row_a.append(canned[key][0])
            row_b.append(canned[key][1])
        else:
            matched = False
            for k, v in canned.items():
                if k in key or key in k:
                    row_a.append(v[0])
                    row_b.append(v[1])
                    matched = True
                    break
            if not matched:
                row_a.append(fallback_a[i % len(fallback_a)])
                row_b.append(fallback_b[i % len(fallback_b)])
    return [row_a, row_b]


def _param_value(p):
    if p.initial_value:
        return str(p.initial_value)
    dt = (p.datatype or "").lower()
    if dt == "date":
        return "01/01/2026"
    if dt == "number":
        return "2026"
    return "ALL"


def _format_param_summary(report):
    parts = []
    for p in report.parameters:
        if not p.display:
            continue
        label = p.label or p.name.replace("P_", "").replace("_", " ").title()
        val = _param_value(p)
        parts.append(f"{_esc(label)} = '<b>{_esc(val)}</b>'")
        if len(parts) >= 3:
            break
    if not parts:
        return "Renewal Year = '<b>2026</b>'"
    return " &nbsp; * &nbsp; ".join(parts)


# ---------------------------------------------------------------------------
# Section builders -- pure black ink on white paper, no color.
# ---------------------------------------------------------------------------

# Strict black-and-white palette. NO blue, navy, tan, cream, brown.
PAPER       = "#ffffff"   # paper
INK         = "#000000"   # pure black ink
INK_SOFT    = "#333333"   # body text
INK_MUTED   = "#666666"   # captions / metadata
RULE        = "#888888"   # standard rule
RULE_LIGHT  = "#d0d0d0"   # light dividers
ROW_ALT     = "#f2f2f2"   # zebra alt row
TH_BG       = "#000000"   # table header background (solid black)
TH_FG       = "#ffffff"   # table header text


def _render_header():
    return (
        '<div style="text-align:center; padding-bottom:14px; '
        f'border-bottom:2px solid {INK}; margin-bottom:18px;">'
        f'<div style="font-size:11px; letter-spacing:2px; color:{INK_MUTED}; '
        'text-transform:uppercase;">State of Montana</div>'
        f'<div style="font-size:20px; font-weight:bold; letter-spacing:1px; '
        f'color:{INK}; margin-top:4px;">DEPARTMENT OF ENVIRONMENTAL QUALITY</div>'
        f'<div style="font-size:15px; font-weight:bold; color:{INK}; '
        'margin-top:6px;">MOTOR VEHICLE WRECKING FACILITY LICENSE</div>'
        f'<div style="font-size:11px; color:{INK_MUTED}; margin-top:6px; '
        'font-style:italic;">Issued under the authority of MCA 75-10-501 et seq.</div>'
        '</div>'
    )


def _render_subtitle(report):
    summary = _format_param_summary(report)
    return (
        f'<div style="text-align:center; font-size:12px; color:{INK_SOFT}; '
        f'margin-bottom:18px; padding:6px 0; border-bottom:1px dashed {RULE_LIGHT};">'
        f'Report parameters &nbsp;|&nbsp; {summary}'
        '</div>'
    )


def _render_param_form(report):
    if not report.parameters:
        return ""
    rows = []
    for p in report.parameters:
        if not p.display:
            continue
        label = _esc(p.label or p.name)
        val = _esc(_param_value(p))
        dtype = _esc(p.ssrs_datatype)
        rows.append(
            '<tr>'
            f'<td style="padding:6px 10px; font-weight:bold; color:{INK}; '
            f'width:35%; border-bottom:1px dotted {RULE_LIGHT};">{label}</td>'
            f'<td style="padding:6px 10px; border-bottom:1px dotted {RULE_LIGHT};">'
            f'<span style="display:inline-block; min-width:160px; padding:3px 8px; '
            f'background:{PAPER}; border:1px solid {INK}; color:{INK}; '
            'font-family:Georgia,\'Times New Roman\',Times,serif; font-size:12px;">'
            f'{val}</span>'
            f'<span style="color:{INK_MUTED}; font-size:10px; margin-left:10px;">'
            f'({dtype})</span></td>'
            '</tr>'
        )
    if not rows:
        return ""
    return (
        f'<div style="margin-bottom:20px; background:{PAPER}; '
        f'border:1px solid {RULE}; padding:12px 16px;">'
        f'<div style="font-size:13px; font-weight:bold; color:{INK}; '
        'margin-bottom:8px; text-transform:uppercase; letter-spacing:1px;">'
        'Parameter Form</div>'
        '<table style="width:100%; border-collapse:collapse; font-size:12px;">'
        f'{"".join(rows)}'
        '</table>'
        '</div>'
    )


def _render_data_table(report):
    query = _pick_main_query(report)
    columns = _column_labels(query)
    rows = _sample_rows(columns)

    qname = _esc(query.name if query else "Q_PERMIT")
    head_cells = "".join(
        f'<th style="background:{TH_BG}; color:{TH_FG}; padding:8px 10px; '
        f'text-align:left; border:1px solid {TH_BG}; font-size:11px; '
        'text-transform:uppercase; letter-spacing:0.5px;">'
        f'{_esc(c)}</th>'
        for c in columns
    )
    body = []
    for i, r in enumerate(rows):
        bg = PAPER if i % 2 == 0 else ROW_ALT
        cells = "".join(
            f'<td style="padding:7px 10px; border:1px solid {RULE_LIGHT}; '
            f'background:{bg}; font-size:12px; color:{INK};">{_esc(c)}</td>'
            for c in r
        )
        body.append(f"<tr>{cells}</tr>")

    return (
        '<div style="margin-bottom:22px;">'
        f'<div style="font-size:12px; font-weight:bold; color:{INK}; '
        f'margin-bottom:6px;">Data: {qname}</div>'
        '<table style="width:100%; border-collapse:collapse; '
        f'border:1px solid {INK}; font-family:Georgia,\'Times New Roman\',Times,serif;">'
        f'<thead><tr>{head_cells}</tr></thead>'
        f'<tbody>{"".join(body)}</tbody>'
        '</table>'
        f'<div style="font-size:10px; color:{INK_MUTED}; margin-top:4px; '
        'font-style:italic;">Sample preview rows. Live data is shown on the '
        '"Live Data" tab.</div>'
        '</div>'
    )


def _render_signature_block():
    return (
        '<div style="margin-top:36px; padding-top:18px; '
        f'border-top:1px solid {INK};">'
        '<table style="width:100%;">'
        '<tr>'
        '<td style="width:55%; vertical-align:bottom;">'
        f'<div style="border-bottom:1px solid {INK}; height:34px;"></div>'
        f'<div style="font-size:11px; color:{INK}; margin-top:4px; '
        'font-weight:bold; letter-spacing:1px;">_____________, BUREAU CHIEF</div>'
        f'<div style="font-size:10px; color:{INK_MUTED}; font-style:italic;">'
        'Waste &amp; Underground Tank Management Bureau</div>'
        '</td>'
        '<td style="width:5%;"></td>'
        f'<td style="vertical-align:bottom; font-size:10px; color:{INK_SOFT}; '
        'line-height:1.5;">'
        f'<div style="font-weight:bold; color:{INK};">Department of Environmental Quality</div>'
        '<div>1520 E. Sixth Avenue</div>'
        '<div>P.O. Box 200901</div>'
        '<div>Helena, MT 59620-0901</div>'
        f'<div style="margin-top:4px; color:{INK_MUTED};">(406) 444-2544</div>'
        '</td>'
        '</tr>'
        '</table>'
        '</div>'
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def render_mockup(report):
    body = "".join([
        _render_header(),
        _render_subtitle(report),
        _render_param_form(report),
        _render_data_table(report),
        _render_signature_block(),
    ])

    # Pure black-and-white paper. Thin gray border to suggest a printed sheet.
    return (
        '<div style="font-family:Georgia,\'Times New Roman\',Times,serif; '
        f'background:{PAPER}; color:{INK}; padding:48px 56px; '
        f'border:1px solid {RULE_LIGHT}; '
        'max-width:900px; margin:0 auto; line-height:1.45;">'
        f'{body}'
        '</div>'
    )
