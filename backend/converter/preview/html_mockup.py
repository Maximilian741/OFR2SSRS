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

import base64
import html
import re
from typing import Dict, List, Optional

from ..models import EmbeddedImage, LayoutField, LayoutGroup, ParsedReport


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
# Certificate (positioned layout) renderer
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"&([A-Z][A-Z0-9_]*)", re.IGNORECASE)
_WS_COLLAPSE = re.compile(r"\s*\n\s*\n\s*", re.MULTILINE)

# Canned values used to fill in &TOKEN substitutions and bound fields when no
# real data source is available. These are FALLBACK placeholders only — when
# the user runs against real data via the Live Data tab, those values win.
_TOKEN_PREVIEW = {
    # Q_PERMIT columns / common &TOKEN references
    "PERMIT":          "MV-2026-0117",
    "PERM_TYPE":       "MOTOR VEHICLE WRECKING FACILITY LICENSE",
    "RENEWAL_YEAR":    "2026",
    "SITE_NAME":       "City Auto Wreckers - Bozeman",
    "SITE_ADDR":       "1442 Industrial Dr, Bozeman, MT 59715",
    "PERM_DATES":      "JANUARY 5, 2026 TO DECEMBER 31, 2026",
    "EXP_DATE":        "12/31/2026",
    "PERM_EFF_DATE":   "01/05/2026",
    "PERM_EXP_DATE":   "12/31/2026",
    "PERM_NUM":        "0117",
    "SITE_ID":         "S-2026-0117",
    "COL_SORT":        "A-001",
    # Q_ORG columns (master-detail child)
    "PERMITTEE_ADDR":  "Joseph T. Reilly\n1442 Industrial Dr\nBozeman, MT 59715",
    "PERMITTEE":       "Joseph T. Reilly",
    "SA_SITE_ID":      "S-2026-0117",
    "ORG_ID":          "ORG-1170",
    # Placeholders / formulas
    "CP_OPERATE_U":    "OPERATING AS",
    "CP_OPERATE_L":    "operating as",
    "CP_JV_ADDR":      "PO Box 200901, Helena MT 59620-0901",
    "CP_SORT_DESCR":   "Permit",
    "CP_PERMIT_DTL":   "Renewal Year = '2026'",
    "CP_JV_ENVELOPE":  "JV Standard 12 x 9 Envelope",
    "CP_URL_ALL_ENVELOPE": "(envelope generation URL)",
    "CF_PERMITTEES":   "Joseph T. Reilly",
    "CF_WUTMB_CHIEF":  "Bureau Chief, Waste & Underground Tank Management Bureau",
    "CF_MVWF_PERMIT":  "MVWF Permit(s)",
    "CF_FILE":         "MVWF-2026.RDL",
    "CF_URL_ENVELOPE": "(envelope hyperlink)",
    # Parameter-form-style references
    "P_RENEWAL_YEAR":  "2026",
    "P_AS_PATH":       "ALL",
    "P_ENVELOPE":      "JV_ENVELOPE_12",
    "P_PERM_NAME":     "ALL",
    "P_REPORT_SERVER": "ALL",
    "P_SITE_NAME":     "ALL",
    "P_STATUS_DT_BEGIN": "01/01/2026",
    "P_STATUS_DT_END":   "01/01/2026",
    "P_SUBTITLE":      "Renewal Year = '2026'",
    "P_SORT":          "Permit",
    "P_PERMITTEE":     "ALL",
    "P_PERM_NUM":      "0117",
    "P_DISTR_ABBR":    "(distribution)",
    # Built-ins
    "CURRENTDATE":     "01/05/2026",
    "DATE":            "01/05/2026",
    "SIGNATURE":       "",   # blob, omit
}


def _resolve_tokens(text: str) -> str:
    def sub(m):
        key = m.group(1).upper()
        return _TOKEN_PREVIEW.get(key, m.group(0))
    return _TOKEN_RE.sub(sub, text or "")


def _clean_text(text: str) -> str:
    """Collapse Oracle's verbose `\\n            \\n` whitespace runs."""
    if not text:
        return ""
    cleaned = _WS_COLLAPSE.sub("\n", text)
    cleaned = "\n".join(line.strip() for line in cleaned.split("\n"))
    return cleaned.strip()


def _img_data_uri(img: EmbeddedImage) -> str:
    if not img or not img.hex_data:
        return ""
    try:
        b = bytes.fromhex(img.hex_data.strip())
    except Exception:
        return ""
    return f"data:{img.mime_type or 'image/gif'};base64,{base64.b64encode(b).decode('ascii')}"


def _find_section(layout: List[LayoutGroup], kind: str) -> Optional[LayoutGroup]:
    for g in layout or []:
        if g.kind == kind:
            return g
    return None


def _embedded_index(report: ParsedReport) -> Dict[str, EmbeddedImage]:
    return {img.id: img for img in (report.embedded_images or [])}


def _resolve_field_value(lf: LayoutField) -> str:
    """Map a `kind=field` LayoutField to its display value for the mockup."""
    src_up = (lf.source or "").upper()
    if src_up and src_up in _TOKEN_PREVIEW:
        return _TOKEN_PREVIEW[src_up]
    if src_up == "CURRENTDATE":
        return _TOKEN_PREVIEW["CURRENTDATE"]
    # Some Oracle reports embed &TOKEN inside the field's text segment too
    if lf.text and "&" in lf.text:
        resolved = _resolve_tokens(lf.text)
        if resolved != lf.text:
            return resolved
    # Last resort: a clearly-marked placeholder so the user can spot bindings
    # that don't have canned data, instead of seeing the raw column name.
    if lf.source:
        return f"<{lf.source}>"
    return ""


def _render_field(lf: LayoutField, frame_x: float, frame_y: float,
                  embedded: Dict[str, EmbeddedImage]) -> str:
    rel_x = max(0.0, lf.x - frame_x)
    rel_y = max(0.0, lf.y - frame_y)
    style_pos = (
        f"position:absolute; left:{rel_x:.2f}in; top:{rel_y:.2f}in; "
        f"width:{lf.width:.2f}in; min-height:{lf.height:.2f}in; "
    )
    if lf.kind == "image":
        img = embedded.get(lf.image_id)
        uri = _img_data_uri(img) if img else ""
        if uri:
            return (
                f'<img src="{uri}" style="{style_pos}'
                'opacity:0.20; object-fit:contain; pointer-events:none;" '
                'alt="seal" />'
            )
        return (
            f'<div style="{style_pos}border:1px dashed {RULE}; '
            f'color:{INK_MUTED}; font-size:10px; display:flex; '
            'align-items:center; justify-content:center;">[seal]</div>'
        )

    if lf.kind == "text":
        content = _resolve_tokens(lf.text or "")
    elif lf.kind == "field":
        content = _resolve_field_value(lf)
    else:
        content = lf.text or lf.source or ""

    content = _clean_text(content)
    if not content:
        return ""

    weight = "bold" if lf.bold else "normal"
    italic = "italic" if lf.italic else "normal"
    align = lf.align if lf.align in ("left", "center", "right") else "left"
    color = lf.color or INK
    family = lf.font_family or "Arial, Helvetica, sans-serif"
    size = max(7, min(int(lf.font_size or 10), 32))
    # No overflow:hidden — multi-line captions (FOR THE PERIOD + dates) need
    # to flow past the Oracle-declared box height when our font metrics differ.
    style_text = (
        f"font-family:{family}; font-size:{size}px; "
        f"font-weight:{weight}; font-style:{italic}; "
        f"color:{_esc(color)}; text-align:{align}; "
        "line-height:1.18; white-space:pre-wrap;"
    )
    return (
        f'<div style="{style_pos}{style_text}">{_esc(content)}</div>'
    )


def _render_frame(frame: LayoutGroup, embedded: Dict[str, EmbeddedImage]) -> str:
    border = ""
    if frame.border_width and frame.border_width > 0:
        border = f"border:{max(1, int(frame.border_width))}px solid {INK};"
    inner = []
    for lf in frame.fields:
        inner.append(_render_field(lf, frame.x, frame.y, embedded))
    for child in frame.children:
        if child.kind in ("frame", "repeating_frame"):
            inner.append(
                f'<div style="position:absolute; '
                f'left:{max(0.0, child.x - frame.x):.2f}in; '
                f'top:{max(0.0, child.y - frame.y):.2f}in; '
                f'width:{child.width:.2f}in; height:{child.height:.2f}in;">'
                f'{_render_frame(child, embedded)}'
                '</div>'
            )
        else:
            for lf in child.fields:
                inner.append(_render_field(lf, frame.x, frame.y, embedded))
    return (
        f'<div style="position:relative; width:{frame.width:.2f}in; '
        f'height:{frame.height:.2f}in; {border}">'
        f'{"".join(inner)}'
        '</div>'
    )


def _render_certificate(report: ParsedReport) -> str:
    main = _find_section(report.layout or [], "section_main")
    if main is None:
        return ""
    frames = [c for c in main.children if c.kind == "frame"]
    if not frames:
        return ""
    embedded = _embedded_index(report)

    max_x = max((f.x + f.width for f in frames), default=8.0)
    max_y = max((f.y + f.height for f in frames), default=11.0)
    page_w = max(8.0, max_x + 0.2)
    page_h = max(10.5, max_y + 0.2)

    children = []
    for f in frames:
        children.append(
            f'<div style="position:absolute; left:{f.x:.2f}in; top:{f.y:.2f}in; '
            f'width:{f.width:.2f}in; height:{f.height:.2f}in;">'
            f'{_render_frame(f, embedded)}'
            '</div>'
        )

    label = (
        f'<div style="font-size:11px; color:{INK_MUTED}; '
        'text-transform:uppercase; letter-spacing:1px; margin:24px 0 8px;">'
        'Page 2 &mdash; Certificate (one per permit)</div>'
    )
    sheet = (
        f'<div style="position:relative; width:{page_w:.2f}in; '
        f'height:{page_h:.2f}in; background:{PAPER}; '
        f'border:1px solid {RULE_LIGHT}; margin:0 auto; '
        'box-shadow:0 1px 3px rgba(0,0,0,0.08);">'
        f'{"".join(children)}'
        '</div>'
    )
    return label + sheet


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _is_letter_style(report):
    """Heuristic: a 'letter' report has many text/paragraph formulas
    (CF_PARA_*, CF_DIRECTOR, CF_GOVERNOR, etc.) or a name ending in _LTR_*
    or _LETTER. Tabular reports have a main query with many dataItems
    and few/no paragraph formulas."""
    name = (report.name or "").upper()
    if "_LTR" in name or "_LETTER" in name:
        return True
    # Check formulas for letter-shaped names
    letter_signals = 0
    for f in report.formulas:
        fn = (f.name or "").upper()
        if fn.startswith("CF_PARA") or fn.startswith("CF_DIRECTOR") or            fn.startswith("CF_GOVERNOR") or fn.startswith("CF_SIGN") or            fn.startswith("CF_BODY") or fn.startswith("CF_GREETING") or            fn.startswith("CF_SALUT"):
            letter_signals += 1
    if letter_signals >= 1:
        return True
    return False


def _render_letter_mockup(report):
    """Render a document/letter-style mockup (header, address block, body
    paragraphs, signature) instead of the tabular permit mockup."""
    name = _esc(report.name or "Report")

    # Pull a few sample paragraph sources from the formulas list
    para_formulas = [f for f in report.formulas
                     if (f.name or "").upper().startswith("CF_PARA")]
    para_count = len(para_formulas) or 3

    # Director / Governor / signature formulas (best-effort labels)
    sign_formulas = [f for f in report.formulas
                     if any(tok in (f.name or "").upper()
                            for tok in ("DIRECTOR","GOVERNOR","CHIEF","SIGN"))]

    # Filter formulas list, find a "letter-name" formula like CF_MVWF_LTR_*
    letter_title_formula = None
    for f in report.formulas:
        fn = (f.name or "").upper()
        if fn.startswith("CF_MVWF_LTR") or fn.startswith("CF_MVWF_LETTER")            or fn.startswith("CF_TITLE"):
            letter_title_formula = f.name
            break

    INK = "#000000"; INK_SOFT = "#333333"; INK_MUTED = "#666666"
    RULE = "#222222"; RULE_LIGHT = "#cccccc"

    body = []
    # Letterhead
    body.append(
        '<div style="text-align:center; padding-bottom:14px; '
        f'border-bottom:2px solid {RULE}; margin-bottom:24px;">'
        f'<div style="font-size:11px; letter-spacing:2px; color:{INK_MUTED}; '
        'text-transform:uppercase;">State of Montana</div>'
        f'<div style="font-size:18px; font-weight:bold; letter-spacing:0.8px; '
        f'color:{INK}; margin-top:4px;">DEPARTMENT OF ENVIRONMENTAL QUALITY</div>'
        f'<div style="font-size:11px; color:{INK_MUTED}; margin-top:4px;">'
        '1520 E. Sixth Avenue, P.O. Box 200901, Helena, MT 59620-0901</div>'
        '</div>'
    )
    # Date + reference
    body.append(
        f'<div style="font-size:12px; color:{INK_SOFT}; margin-bottom:18px;">'
        '[Run Date]'
        '</div>'
    )
    # Recipient address block (rendered from query placeholders)
    body.append(
        f'<div style="font-size:12px; color:{INK}; margin-bottom:24px; line-height:1.45;">'
        '[Permittee Name]<br>'
        '[Permittee Address Line 1]<br>'
        '[Permittee City], [State] [Zip]'
        '</div>'
    )
    # Subject line
    title_label = f' &nbsp; ({_esc(letter_title_formula)})' if letter_title_formula else ''
    body.append(
        f'<div style="font-size:13px; color:{INK}; margin-bottom:18px;">'
        f'<b>RE:</b> &nbsp; {name} &mdash; Inspection Letter{title_label}'
        '</div>'
    )
    # Greeting
    body.append(
        f'<div style="font-size:13px; color:{INK}; margin-bottom:14px;">'
        'Dear [Permittee],'
        '</div>'
    )
    # Body paragraphs (placeholders, one per CF_PARA_* if present, else 3 generic)
    if para_formulas:
        for f in para_formulas[:4]:
            body.append(
                f'<p style="font-size:12px; color:{INK_SOFT}; line-height:1.55; margin:0 0 12px;">'
                f'<span style="color:{INK_MUTED}; font-style:italic;">[{_esc(f.name)}]</span> '
                'Lorem ipsum dolor sit amet, consectetur adipiscing elit. '
                'Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. '
                'Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris.'
                '</p>'
            )
    else:
        for i in range(3):
            body.append(
                f'<p style="font-size:12px; color:{INK_SOFT}; line-height:1.55; margin:0 0 12px;">'
                'Lorem ipsum dolor sit amet, consectetur adipiscing elit. '
                'This paragraph would render content from the source report\'s body queries.'
                '</p>'
            )
    # Closing
    body.append(
        f'<div style="font-size:13px; color:{INK}; margin:22px 0 8px;">Sincerely,</div>'
        f'<div style="height:50px;"></div>'
    )
    # Signature line
    if sign_formulas:
        sig = sign_formulas[0].name
        body.append(
            f'<div style="border-top:1px solid {INK}; width:60%; padding-top:4px;">'
            f'<div style="font-size:12px; font-weight:bold; color:{INK};">[{_esc(sig)}]</div>'
            f'<div style="font-size:11px; color:{INK_MUTED};">Director / Bureau Chief</div>'
            '</div>'
        )
    else:
        body.append(
            f'<div style="border-top:1px solid {INK}; width:60%; padding-top:4px;">'
            f'<div style="font-size:12px; font-weight:bold; color:{INK};">[Director]</div>'
            f'<div style="font-size:11px; color:{INK_MUTED};">Department of Environmental Quality</div>'
            '</div>'
        )
    # Footer note
    body.append(
        f'<div style="margin-top:36px; padding-top:12px; '
        f'border-top:1px dashed {RULE_LIGHT}; font-size:10px; '
        f'color:{INK_MUTED}; font-style:italic;">'
        f'Letter-style preview detected from {len(report.formulas)} formula(s) '
        f'and {len(report.queries)} query(ies). Boilerplate placeholders shown; '
        'live data is bound at runtime by SSRS.'
        '</div>'
    )

    return (
        '<div style="font-family:Georgia,\'Times New Roman\',Times,serif; '
        'background:#ffffff; color:#111111; padding:48px 64px; '
        'border:1px solid #cccccc; '
        'box-shadow:0 1px 2px rgba(0,0,0,0.04), 0 6px 24px rgba(20,24,40,0.06); '
        'max-width:780px; margin:0 auto; line-height:1.45;">'
        + ''.join(body) +
        '</div>'
    )


def render_mockup(report):
    if _is_letter_style(report):
        return _render_letter_mockup(report)
    body = "".join([
        _render_header(),
        _render_subtitle(report),
        _render_param_form(report),
        _render_data_table(report),
        _render_certificate(report),
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
