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
    """Build 2 fictional sample rows for a list of column names.

    Delegates per-column lookup to _sample_for_source so all placeholder
    data lives in ONE pool — structural keyword-based, never customer- or
    report-specific.
    """
    row_a, row_b = [], []
    for col in columns:
        row_a.append(_sample_for_source(col, 0))
        row_b.append(_sample_for_source(col, 1))
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
        'text-transform:uppercase;"><State / Org></div>'
        f'<div style="font-size:20px; font-weight:bold; letter-spacing:1px; '
        f'color:{INK}; margin-top:4px;"><AGENCY DEPARTMENT NAME></div>'
        f'<div style="font-size:15px; font-weight:bold; color:{INK}; '
        'margin-top:6px;"><REPORT TITLE></div>'
        f'<div style="font-size:11px; color:{INK_MUTED}; margin-top:6px; '
        'font-style:italic;">Issued under the authority of <statutory citation></div>'
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
        'font-weight:bold; letter-spacing:1px;">_____________, <Title></div>'
        f'<div style="font-size:10px; color:{INK_MUTED}; font-style:italic;">'
        '<Bureau></div>'
        '</td>'
        '<td style="width:5%;"></td>'
        f'<td style="vertical-align:bottom; font-size:10px; color:{INK_SOFT}; '
        'line-height:1.5;">'
        f'<div style="font-weight:bold; color:{INK};"><Agency></div>'
        '<div><street></div>'
        '<div><PO box></div>'
        '<div><city, state, zip></div>'
        f'<div style="margin-top:4px; color:{INK_MUTED};"><phone></div>'
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
    "PERM_TYPE":       "<REPORT TITLE>",
    "RENEWAL_YEAR":    "2026",
    "SITE_NAME":       "Acme Holdings - Springfield",
    "SITE_ADDR":       "100 Main St, Springfield, ST 00000",
    "PERM_DATES":      "JANUARY 5, 2026 TO DECEMBER 31, 2026",
    "EXP_DATE":        "12/31/2026",
    "PERM_EFF_DATE":   "01/05/2026",
    "PERM_EXP_DATE":   "12/31/2026",
    "PERM_NUM":        "0117",
    "SITE_ID":         "S-2026-0117",
    "COL_SORT":        "A-001",
    # Q_ORG columns (master-detail child)
    "PERMITTEE_ADDR":  "Jane Q. Public\n100 Main St\nSpringfield, ST 00000",
    "PERMITTEE":       "Jane Q. Public",
    "SA_SITE_ID":      "S-2026-0117",
    "ORG_ID":          "ORG-1170",
    # Placeholders / formulas
    "CP_OPERATE_U":    "OPERATING AS",
    "CP_OPERATE_L":    "operating as",
    "CP_JV_ADDR":      "PO Box 1000, Capital City, ST 00000",
    "CP_SORT_DESCR":   "Permit",
    "CP_PERMIT_DTL":   "Renewal Year = '2026'",
    "CP_JV_ENVELOPE":  "JV Standard 12 x 9 Envelope",
    "CP_URL_ALL_ENVELOPE": "(envelope generation URL)",
    "CF_PERMITTEES":   "Jane Q. Public",
    "CF_WUTMB_CHIEF":  "Sample Chief, Sample Bureau",
    "CF_SAMPLE_PERMIT": "Sample Permit(s)",
    "CF_FILE":         "SAMPLE_INSPECTION-2026.RDL",
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


# Module-level rendering mode. "frontend" fills the preview with fictional
# sample data — looks like the SSRS-rendered output the user will see.
# "backend" leaves field/token references visible as «placeholders» — looks
# like the Report Builder design surface before binding to data.
_ACTIVE_MODE = "frontend"


def _placeholder_for_source(src):
    """Backend-mode placeholder for a field/column reference."""
    name = (src or "").strip() or "FIELD"
    return f"«F_{name}»"


def _resolve_tokens(text: str) -> str:
    if _ACTIVE_MODE == "backend":
        return _TOKEN_RE.sub(
            lambda m: f"«&{m.group(1).upper()}»",
            text or "",
        )
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
    """Structural heuristic for letter-style reports. No customer/agency-
    specific token matching — purely shape-based:

      * Name suffix _LTR / _LETTER (a convention many Oracle Reports shops use)
      * OR 3+ paragraph-shaped text blocks in the layout
      * OR a single static text block ≥ 400 characters (a body paragraph)
    """
    name = (report.name or "").upper()
    if "_LTR" in name or "_LETTER" in name:
        return True
    if _count_paragraphy_text_blocks(report) >= 3:
        return True
    for g in _iter_layout(report):
        for f in g.fields or []:
            if f.kind == "text" and len(f.text or "") >= 400:
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

    # Filter formulas list, find a "letter-name" formula like CF_LTR_* / CF_LETTER_*
    letter_title_formula = None
    for f in report.formulas:
        fn = (f.name or "").upper()
        if fn.startswith("CF_LTR_") or fn.startswith("CF_LETTER_")            or fn.startswith("CF_TITLE"):
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
        'text-transform:uppercase;"><State / Org></div>'
        f'<div style="font-size:18px; font-weight:bold; letter-spacing:0.8px; '
        f'color:{INK}; margin-top:4px;"><AGENCY DEPARTMENT NAME></div>'
        f'<div style="font-size:11px; color:{INK_MUTED}; margin-top:4px;">'
        '<street>, <PO box>, <city, state, zip></div>'
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
            f'<div style="font-size:11px; color:{INK_MUTED};">Director / Title</div>'
            '</div>'
        )
    else:
        body.append(
            f'<div style="border-top:1px solid {INK}; width:60%; padding-top:4px;">'
            f'<div style="font-size:12px; font-weight:bold; color:{INK};">[Director]</div>'
            f'<div style="font-size:11px; color:{INK_MUTED};"><Agency></div>'
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


# ---------------------------------------------------------------------------
# Tabular-details template (banded header rows + colored detail blocks)
# ---------------------------------------------------------------------------

# Default palette for the tabular-details variant. Used when the parsed model
# does not carry explicit background_color / foreground_color attributes
# (Agent A's color-parsing work is wiring those up in parallel).
# Neutral fallback palette. The renderer ALWAYS prefers colors parsed from
# the report's own <visualSettings> elements (via oracle_colors.resolve_color);
# these constants are used ONLY when the source XML has no color information
# for a given element. They are deliberately grayscale so an uncolored report
# from any source looks like a generic monochrome document, not like a
# specific previously-seen report.
_TAB_TITLE_RED   = "#1a1a1a"   # default title color (near-black, NOT red)
_TAB_BAND_BG     = "#666666"   # neutral mid-gray group-header band
_TAB_BAND_FG     = "#ffffff"
_TAB_DETAIL_BG   = "#f5f5f5"   # very light gray detail block
_TAB_SUBBAND_BG  = "#7a7a7a"
_TAB_SUBBAND_FG  = "#ffffff"
_TAB_INK         = "#111111"
_TAB_INK_SOFT    = "#444444"
_TAB_INK_MUTED   = "#777777"
_TAB_PAPER       = "#ffffff"
_TAB_RULE_LIGHT  = "#d0d0d0"


def _attr(obj, name, default=""):
    """Defensive accessor for attributes added by Agent A's color work.
    Returns `default` when the attribute is missing OR present but empty."""
    val = getattr(obj, name, None)
    if val is None or val == "":
        return default
    return val


def _iter_layout(report):
    """Yield every LayoutGroup in the report, depth-first."""
    def walk(g):
        yield g
        for ch in g.children or []:
            yield from walk(ch)
    for g in report.layout or []:
        yield from walk(g)


def _has_color_signal(report):
    """True if any frame or field carries a non-empty background_color
    (i.e. Agent A's color parsing has populated visual settings on this
    report). Falls back to False when those attributes don't exist yet."""
    for g in _iter_layout(report):
        if _attr(g, "background_color"):
            return True
        if _attr(g, "foreground_color"):
            return True
        for f in g.fields or []:
            if _attr(f, "background_color"):
                return True
    return False


def _count_paragraphy_text_blocks(report):
    """Count LayoutText blocks that look like body paragraphs."""
    n = 0
    for g in _iter_layout(report):
        for f in g.fields or []:
            if f.kind != "text":
                continue
            text = (f.text or "").strip()
            if len(text) >= 80 and "\n" in text:
                n += 1
    return n


def _repeating_frames(report):
    return [g for g in _iter_layout(report) if g.kind == "repeating_frame"]


def detect_report_kind(report):
    """Return one of 'letter', 'tabular_details', 'certificate'.

    All checks are structural - never customer-specific.
    """
    name = (report.name or "").upper()
    if "_LTR" in name or "_LETTER" in name:
        return "letter"

    for g in _iter_layout(report):
        if g.kind == "repeating_frame":
            if _attr(g, "background_color"):
                return "tabular_details"
            for f in g.fields or []:
                if _attr(f, "background_color"):
                    return "tabular_details"

    main = _find_section(report.layout or [], "section_main")
    if main is not None:
        frames = [c for c in (main.children or []) if c.kind == "frame"]
        multiline_text_count = 0
        for fr in frames:
            for f in (fr.fields or []):
                t = f.text or ""
                if f.kind == "text" and "\n" in t and len(t) >= 30:
                    multiline_text_count += 1
        if len(frames) >= 2 and multiline_text_count >= 2:
            return "certificate"

    reps_with_data = [
        g for g in _iter_layout(report)
        if g.kind == "repeating_frame"
        and any(f.kind == "field" for f in (g.fields or []))
    ]
    if len(reps_with_data) >= 2:
        return "tabular_details"

    if _count_paragraphy_text_blocks(report) >= 3:
        return "letter"

    if _has_color_signal(report):
        return "tabular_details"

    return "certificate"


def _find_title_text(report):
    """Pick the most title-like static-text field: largest font, centered,
    bold preferred. Returns the LayoutField or None."""
    best = None
    best_key = (-1, -1)
    for g in _iter_layout(report):
        for f in g.fields or []:
            if f.kind != "text":
                continue
            if not (f.text or "").strip():
                continue
            sz = int(f.font_size or 0)
            centered = 1 if (f.align or "").lower() == "center" else 0
            bold = 1 if f.bold else 0
            key = (sz, centered + bold)
            if key > best_key:
                best_key = key
                best = f
    return best


def _section_main_text_fields(report):
    """Static text fields found directly in section_main (excluding
    descent into repeating frames)."""
    main = _find_section(report.layout or [], "section_main")
    if main is None:
        return []
    out = []
    def walk(g, depth=0):
        if depth > 0 and g.kind == "repeating_frame":
            return
        for f in g.fields or []:
            if f.kind == "text" and (f.text or "").strip():
                out.append(f)
        for ch in g.children or []:
            walk(ch, depth + 1)
    walk(main)
    return out


def _normalize_color(color, fallback):
    """Coerce an Oracle color token into a #RRGGBB hex string.

    Order of resolution:
      1. Empty / None → fallback.
      2. Already a hex literal (#abc, #aabbcc) → returned as-is.
      3. Otherwise delegate to oracle_colors.resolve_color() which handles
         named colors (red, darkblue, gray16, navy, ...) AND r/g/b triplets
         (r0g0b50 = rgb(0,0,127) in Oracle's 0-100 scale).
      4. If the resolver can't decode it, return fallback.
    """
    if not color:
        return fallback
    if isinstance(color, str) and color.startswith("#"):
        return color
    try:
        from converter.parsers.oracle_colors import resolve_color
        resolved = resolve_color(color)
        if resolved:
            return resolved
    except Exception:
        pass
    return fallback


def _render_tabular_title(report):
    title_field = _find_title_text(report)
    raw_color = _attr(title_field, "color", "") if title_field else ""
    title_color = _normalize_color(raw_color, _TAB_TITLE_RED)

    candidates = [
        f for f in _section_main_text_fields(report)
        if (f.align or "").lower() == "center" and int(f.font_size or 0) >= 10
    ]
    candidates.sort(key=lambda f: (-int(f.font_size or 0), -1 if f.bold else 0))
    seen, lines = set(), []
    for f in candidates:
        t = (f.text or "").strip()
        if not t or t in seen:
            continue
        seen.add(t)
        lines.append(t)
        if len(lines) >= 3:
            break
    if not lines:
        nm = report.name or "Detail Report"
        lines = [nm, "Detail Report"]

    parts = []
    for i, t in enumerate(lines):
        size = 18 if i == 0 else (14 if i == 1 else 12)
        parts.append(
            '<div style="font-size:' + str(size) + 'px; font-weight:bold; '
            'color:' + title_color + '; text-align:center; '
            'line-height:1.25; margin:2px 0;">' + _esc(t) + '</div>'
        )
    return (
        '<div style="text-align:center; padding:14px 0 18px; '
        'border-bottom:1px solid ' + _TAB_RULE_LIGHT + '; margin-bottom:18px;">'
        + "".join(parts) + '</div>'
    )


def _render_tabular_param_form(report):
    visible = [p for p in (report.parameters or []) if p.display]
    if not visible:
        return ""
    rows = []
    for p in visible[:8]:
        raw_label = p.label or p.name
        raw_label = raw_label.replace("P_", "").replace("PARM_", "")
        label = _esc(raw_label.replace("_", " ").title())
        val = _esc(_param_value(p))
        rows.append(
            '<tr>'
            '<td style="padding:3px 10px 3px 0; text-align:right; '
            'color:' + _TAB_INK_SOFT + '; font-weight:bold; width:35%; '
            'font-size:12px;">' + label + ':</td>'
            '<td style="padding:3px 0; border-bottom:1px solid ' + _TAB_INK + '; '
            'color:' + _TAB_INK + '; font-size:12px;">' + val + '</td>'
            '</tr>'
        )
    return (
        '<div style="margin:0 auto 18px; max-width:560px;">'
        '<table style="width:100%; border-collapse:collapse; '
        'font-family:Arial,Helvetica,sans-serif;">'
        + "".join(rows) + '</table></div>'
    )


def _detail_field_pairs(group):
    """Return [(label, source), ...] pairs for the detail rows inside a
    repeating frame. We pair static-text labels with the field bound
    right after them, falling back to source-name labels."""
    pairs = []
    fields = list(group.fields or [])
    fields.sort(key=lambda f: (round(f.y, 2), round(f.x, 2)))
    pending_label = None
    for f in fields:
        if f.kind == "text":
            txt = (f.text or "").strip().rstrip(":")
            if txt:
                pending_label = txt
        elif f.kind == "field":
            label = pending_label or (f.source or f.name or "").replace("_", " ").title()
            pairs.append((label, f.source or f.name))
            pending_label = None
    if not pairs:
        for child in group.children or []:
            pairs.extend(_detail_field_pairs(child))
    return pairs


def _sample_for_source(src, idx):
    """Return a fictional sample value for a column/source name.

    In BACKEND mode the value is a visible placeholder (e.g. «F_PERM_NUM»)
    so the preview shows the Report Builder design view instead of sample-
    filled output.

    Otherwise (frontend mode) the pool is built from neutral structural
    keywords (id, name, date, addr, city, type, status, comment, count,
    etc.) — never specific to any one report.
    """
    if _ACTIVE_MODE == "backend":
        return _placeholder_for_source(src)
    key = (src or "").lower().replace("_", " ").strip()

    # Structural keyword pools. Each pool has 2 fictional alternatives so two
    # sample rows look different. NEVER use customer/jurisdiction-specific
    # tokens (no "County A", no "Montana", no "Methamphetamine", etc.).
    NAME_POOL    = ["Acme Holdings", "Northwind Industries"]
    PERSON_POOL  = ["Alex Rivera", "Jordan Casey"]
    ADDR_POOL    = ["100 Main St, Springfield, ST 00000",
                    "200 Commerce Way, Riverside, ST 00000"]
    CITY_POOL    = ["Springfield", "Riverside"]
    DATE_POOL    = ["03/12/2026", "04/02/2026"]
    NUM_POOL     = ["1001", "1002"]
    SHORT_ID     = ["A-0117", "A-0231"]
    TYPE_POOL    = ["Type Alpha", "Type Bravo"]
    STATUS_POOL  = ["Active", "Pending"]
    COMMENT_POOL = ["Initial review completed.", "Follow-up scheduled."]
    GROUP_POOL   = ["Group One", "Group Two"]
    EMAIL_POOL   = ["[email protected]", "[email protected]"]
    PHONE_POOL   = ["(555) 010-1001", "(555) 010-1002"]

    # Keyword → pool dispatch (order matters: more-specific keywords first).
    KEYWORD_MAP = [
        (("email",),                       EMAIL_POOL),
        (("phone", "tel",),                PHONE_POOL),
        (("contractor", "owner", "permittee", "company", "org", "facility"), NAME_POOL),
        (("contact", "user", "person", "signer", "manager"), PERSON_POOL),
        (("addr", "street", "location"),   ADDR_POOL),
        (("city", "town"),                 CITY_POOL),
        (("date", "dt"),                   DATE_POOL),
        (("comment", "descr", "notes", "remark"), COMMENT_POOL),
        (("status",),                      STATUS_POOL),
        (("type",),                        TYPE_POOL),
        (("count", "total", "qty", "num", "number"), NUM_POOL),
        (("id", "code", "key"),            SHORT_ID),
        (("nm", "name", "label", "group"), GROUP_POOL),
    ]
    for keywords, pool in KEYWORD_MAP:
        for kw in keywords:
            if kw and kw in key:
                return pool[idx % len(pool)]
    return ["Sample Value A", "Sample Value B"][idx % 2]


def _render_band(bg, fg, content_html, weight="bold", size=12, pad="6px 12px"):
    return (
        '<div style="background:' + bg + '; color:' + fg + '; padding:' + pad + '; '
        'font-family:Arial,Helvetica,sans-serif; font-size:' + str(size) + 'px; '
        'font-weight:' + weight + '; letter-spacing:0.3px;">'
        + content_html + '</div>'
    )


def _render_repeating_block(rep_frame, sample_idx, defaults):
    """Render one (band + detail block + optional sub-band) trio for a
    repeating frame using fictional sample data."""
    bg = _normalize_color(_attr(rep_frame, "background_color", ""), defaults["band_bg"])
    fg = _normalize_color(_attr(rep_frame, "foreground_color", ""), defaults["band_fg"])

    pairs = _detail_field_pairs(rep_frame)
    band_label_parts = []
    if pairs:
        for label, src in pairs[:2]:
            val = _sample_for_source(src, sample_idx)
            band_label_parts.append(
                _esc(label) + ': <span style="font-weight:bold;">'
                + _esc(val) + '</span>'
            )
    if not band_label_parts:
        nm = rep_frame.source_query or rep_frame.name or "Group"
        band_label_parts.append(
            'Group: <span style="font-weight:bold;">' + _esc(nm) + '</span>'
        )
    band = _render_band(bg, fg, " &nbsp; &nbsp; ".join(band_label_parts))

    detail_pairs = pairs[2:] if len(pairs) > 2 else pairs
    detail_bg = defaults["detail_bg"]
    detail_rows = []
    for label, src in detail_pairs:
        val = _sample_for_source(src, sample_idx)
        detail_rows.append(
            '<tr>'
            '<td style="padding:5px 12px; font-weight:bold; color:'
            + _TAB_INK_SOFT + '; width:28%; vertical-align:top; '
            'font-size:12px;">' + _esc(label) + ':</td>'
            '<td style="padding:5px 12px; color:' + _TAB_INK + '; '
            'font-size:12px;">' + _esc(val) + '</td>'
            '</tr>'
        )

    nested = []
    def collect_nested(g):
        for ch in g.children or []:
            if ch.kind == "repeating_frame" and ch is not rep_frame:
                nested.append(ch)
            else:
                collect_nested(ch)
    collect_nested(rep_frame)

    sub_blocks = []
    for sub in nested[:1]:
        sub_pairs = _detail_field_pairs(sub)
        if not sub_pairs:
            continue
        sub_bg = _normalize_color(_attr(sub, "background_color", ""), defaults["subband_bg"])
        sub_fg = _normalize_color(_attr(sub, "foreground_color", ""), defaults["subband_fg"])
        head_cells = "".join(
            '<th style="background:' + sub_bg + '; color:' + sub_fg + '; '
            'padding:6px 10px; text-align:left; font-size:11px; '
            'font-weight:bold; text-transform:uppercase; '
            'letter-spacing:0.4px; border:1px solid ' + sub_bg + ';">'
            + _esc(lbl) + '</th>'
            for lbl, _src in sub_pairs
        )
        body_cells = "".join(
            '<td style="padding:6px 10px; background:' + _TAB_PAPER + '; '
            'border:1px solid ' + _TAB_RULE_LIGHT + '; color:' + _TAB_INK + '; '
            'font-size:12px;">' + _esc(_sample_for_source(src, sample_idx))
            + '</td>'
            for _lbl, src in sub_pairs
        )
        sub_blocks.append(
            '<table style="width:100%; border-collapse:collapse; '
            'margin:0; font-family:Arial,Helvetica,sans-serif;">'
            '<thead><tr>' + head_cells + '</tr></thead>'
            '<tbody><tr>' + body_cells + '</tr></tbody>'
            '</table>'
        )

    detail_html = ""
    if detail_rows:
        detail_html = (
            '<div style="background:' + detail_bg + '; padding:8px 4px; '
            'border:1px solid ' + _TAB_RULE_LIGHT + '; border-top:none;">'
            '<table style="width:100%; border-collapse:collapse; '
            'font-family:Arial,Helvetica,sans-serif;">'
            + "".join(detail_rows) + '</table></div>'
        )

    return (
        '<div style="margin-bottom:18px;">'
        + band + detail_html + "".join(sub_blocks)
        + '</div>'
    )


def _render_tabular_details(report):
    """Render a banded, colored tabular-details preview that mirrors the
    METH_DETAILS-style screenshot.

      [centered red title block]
      [right-aligned param form]
      [navy band: County: X    Total For County: N]
      [light-gray detail block (one record)]
      [navy sub-band: Action Type | Comments | Date]
      [white detail row under the sub-band]
      ... repeated for two fictional rows ...
    """
    defaults = {
        "band_bg":    _TAB_BAND_BG,
        "band_fg":    _TAB_BAND_FG,
        "detail_bg":  _TAB_DETAIL_BG,
        "subband_bg": _TAB_SUBBAND_BG,
        "subband_fg": _TAB_SUBBAND_FG,
    }

    blocks = []
    blocks.append(_render_tabular_title(report))
    blocks.append(_render_tabular_param_form(report))

    main = _find_section(report.layout or [], "section_main")
    top_rep = None
    if main is not None:
        def find_rep(g):
            if g.kind == "repeating_frame":
                return g
            for ch in g.children or []:
                r = find_rep(ch)
                if r is not None:
                    return r
            return None
        top_rep = find_rep(main)

    if top_rep is not None:
        for idx in (0, 1):
            blocks.append(_render_repeating_block(top_rep, idx, defaults))
    else:
        # Fallback when the layout has NO repeating frames: emit a plain
        # banded data table from the main query's column labels. No fake
        # "County" header — the band label comes from the first column.
        query = _pick_main_query(report)
        columns = _column_labels(query)
        rows = _sample_rows(columns)
        first_label = columns[0] if columns else "Group"
        for idx, row in enumerate(rows):
            band_value = _esc(row[0]) if row else "Sample Group " + str(idx + 1)
            header_html = (
                _esc(first_label) + ': <b>' + band_value + '</b>'
            )
            blocks.append(_render_band(defaults["band_bg"], defaults["band_fg"], header_html))
            detail_rows = "".join(
                '<tr>'
                '<td style="padding:5px 12px; font-weight:bold; color:'
                + _TAB_INK_SOFT + '; width:28%; font-size:12px;">'
                + _esc(col) + ':</td>'
                '<td style="padding:5px 12px; color:' + _TAB_INK + '; '
                'font-size:12px;">' + _esc(val) + '</td>'
                '</tr>'
                for col, val in zip(columns, row)
            )
            blocks.append(
                '<div style="background:' + defaults["detail_bg"] + '; '
                'padding:8px 4px; border:1px solid ' + _TAB_RULE_LIGHT + '; '
                'border-top:none; margin-bottom:18px;">'
                '<table style="width:100%; border-collapse:collapse;">'
                + detail_rows + '</table></div>'
            )

    blocks.append(
        '<div style="margin-top:18px; padding-top:10px; '
        'border-top:1px dashed ' + _TAB_RULE_LIGHT + '; font-size:10px; '
        'color:' + _TAB_INK_MUTED + '; font-style:italic; text-align:center;">'
        'Tabular-details preview. Two fictional rows shown; live data is '
        'bound at runtime via SSRS.</div>'
    )

    body = "".join(blocks)
    return (
        '<div style="font-family:Arial,Helvetica,sans-serif; '
        'background:' + _TAB_PAPER + '; color:' + _TAB_INK + '; '
        'padding:32px 36px; border:1px solid ' + _TAB_RULE_LIGHT + '; '
        'max-width:920px; margin:0 auto; line-height:1.4;">'
        + body + '</div>'
    )


def _render_certificate_mockup(report):
    """Render a certificate/permit-style report.

    Walks section_main's frames as vertically-stacked panels. Each panel:
      * Title row (the largest centered text field, if any)
      * Detail grid (key/value pairs of remaining fields with sample values)
      * Body paragraph blocks for any multi-line static text >= 30 chars

    All colors come from the parsed visualSettings on each frame/field;
    fallback is neutral grayscale only when the XML has no color information.
    """
    main = _find_section(report.layout or [], "section_main")
    if main is None:
        return _render_tabular_details(report)
    frames = [c for c in (main.children or []) if c.kind == "frame"]
    if not frames:
        return _render_tabular_details(report)

    title = _find_title_text(report)
    title_color = _normalize_color(
        _attr(title, "color", "") if title else "",
        _TAB_TITLE_RED,
    )
    title_lines = []
    if title is not None:
        # Resolve &TOKEN and :P_PARAM in the title BEFORE splitting so
        # multi-line titles like "STATE OF MONTANA\n&RENEWAL_YEAR"
        # render as "STATE OF MONTANA / 2026" in frontend mode (and
        # as "STATE OF MONTANA / «&RENEWAL_YEAR»" in backend mode).
        title_raw = _resolve_tokens(title.text or "")
        title_raw = re.sub(
            r":P_[A-Za-z][A-Za-z0-9_]*",
            lambda m: _sample_for_source(m.group(0)[1:], 0),
            title_raw,
        )
        for ln in title_raw.splitlines():
            ln = ln.strip()
            if ln:
                title_lines.append(ln)
    if not title_lines:
        title_lines = [report.name or "Report"]

    title_html_bits = [
        '<div style="text-align:center; padding:18px 0 14px; '
        'border-bottom:1px solid ' + _TAB_RULE_LIGHT + '; '
        'margin-bottom:18px;">'
    ]
    for i, ln in enumerate(title_lines[:4]):
        size = 22 if i == 0 else (16 if i == 1 else 13)
        title_html_bits.append(
            '<div style="font-size:' + str(size) + 'px; font-weight:bold; '
            'color:' + title_color + '; line-height:1.25; '
            'margin:2px 0; letter-spacing:0.3px;">'
            + _esc(ln) + '</div>'
        )
    title_html_bits.append('</div>')
    title_html = "".join(title_html_bits)

    param_html = _render_tabular_param_form(report)

    panels = []
    for fr in frames:
        text_blocks = []
        kv_pairs = []
        img_blocks = []

        fields = sorted(
            fr.fields or [],
            key=lambda f: (round(f.y or 0.0, 2), round(f.x or 0.0, 2)),
        )
        pending_label = None
        for f in fields:
            if f.kind == "image":
                img_blocks.append(f)
                continue
            if f.kind == "text":
                txt = (f.text or "").strip()
                if not txt:
                    continue
                if "\n" in txt or len(txt) >= 40:
                    text_blocks.append(f)
                    pending_label = None
                else:
                    # Resolve &TOKEN and :P_PARAM in short label text too —
                    # otherwise card panels show raw "&PERM_TYPE" labels.
                    label_text = _resolve_tokens(txt)
                    label_text = re.sub(r":P_[A-Za-z][A-Za-z0-9_]*",
                                        lambda m: _sample_for_source(m.group(0)[1:], 0),
                                        label_text)
                    pending_label = label_text.rstrip(":")
            elif f.kind == "field":
                label = pending_label or (f.source or f.name or "").replace("_", " ").title()
                val = _sample_for_source(f.source or f.name, 0)
                kv_pairs.append((label, f.source or f.name, val))
                pending_label = None

        inner_reps = []
        def collect_reps(g, acc):
            for ch in g.children or []:
                if ch.kind == "repeating_frame":
                    acc.append(ch)
                else:
                    collect_reps(ch, acc)
        collect_reps(fr, inner_reps)

        if not (text_blocks or kv_pairs or img_blocks or inner_reps):
            continue

        panel_bg = _normalize_color(_attr(fr, "background_color", ""), _TAB_PAPER)
        panel_bits = []

        for tb in text_blocks:
            raw = (tb.text or "").strip()
            # Resolve &TOKEN lexical refs and :P_PARAM bind vars so the
            # body text shows real-looking values (e.g. "STATE OF MONTANA
            # 2026") instead of raw "&RENEWAL_YEAR" placeholders. In
            # backend mode the resolver leaves them as «PLACEHOLDER».
            raw = _resolve_tokens(raw)
            raw = re.sub(
                r":P_[A-Za-z][A-Za-z0-9_]*",
                lambda m: _sample_for_source(m.group(0)[1:], 0),
                raw,
            )
            color = _normalize_color(_attr(tb, "color", ""), _TAB_INK)
            bg = _normalize_color(_attr(tb, "background_color", ""), "transparent")
            size = max(11, int(tb.font_size or 12))
            align = (tb.align or "start").lower()
            css_align = {"start": "left", "end": "right", "center": "center"}.get(align, "left")
            font_weight = "bold" if tb.bold else "normal"
            font_style = "italic" if tb.italic else "normal"
            esc_text = _esc(raw).replace("\n", "<br>")
            panel_bits.append(
                '<div style="font-size:' + str(size) + 'px; color:' + color + '; '
                'background:' + bg + '; text-align:' + css_align + '; '
                'font-weight:' + font_weight + '; font-style:' + font_style + '; '
                'margin:6px 0; line-height:1.4; white-space:pre-wrap;">'
                + esc_text + '</div>'
            )

        if kv_pairs:
            rows_html = []
            for label, _src, val in kv_pairs:
                rows_html.append(
                    '<tr>'
                    '<td style="padding:3px 12px 3px 0; text-align:right; '
                    'color:' + _TAB_INK_SOFT + '; font-weight:bold; '
                    'width:32%; font-size:12px; vertical-align:top;">'
                    + _esc(label) + ':</td>'
                    '<td style="padding:3px 0; color:' + _TAB_INK + '; '
                    'font-size:12px; border-bottom:1px solid '
                    + _TAB_RULE_LIGHT + ';">' + _esc(val) + '</td>'
                    '</tr>'
                )
            panel_bits.append(
                '<table style="width:100%; border-collapse:collapse; '
                'margin:8px 0;">' + "".join(rows_html) + '</table>'
            )

        for ib in img_blocks:
            panel_bits.append(
                '<div style="margin:8px 0; padding:18px; text-align:center; '
                'border:1px dashed ' + _TAB_RULE_LIGHT + '; color:'
                + _TAB_INK_MUTED + '; font-size:11px; font-style:italic;">'
                '[image placeholder: ' + _esc(ib.name or "image") + ']</div>'
            )

        rep_defaults = {
            "band_bg":    _TAB_BAND_BG,
            "band_fg":    _TAB_BAND_FG,
            "detail_bg":  _TAB_DETAIL_BG,
            "subband_bg": _TAB_SUBBAND_BG,
            "subband_fg": _TAB_SUBBAND_FG,
        }
        for rep in inner_reps[:2]:
            for idx in (0, 1):
                panel_bits.append(_render_repeating_block(rep, idx, rep_defaults))

        raw_panel_label = (fr.name or "").lstrip("M_").replace("_", " ").title().strip()
        if raw_panel_label and len(raw_panel_label) > 1:
            panel_bits.insert(
                0,
                '<div style="font-size:11px; color:' + _TAB_INK_MUTED + '; '
                'text-transform:uppercase; letter-spacing:1px; '
                'margin:14px 0 6px;">' + _esc(raw_panel_label) + '</div>',
            )

        panels.append(
            '<div style="background:' + panel_bg + '; padding:14px 18px; '
            'margin:0 0 16px; border:1px solid ' + _TAB_RULE_LIGHT + '; '
            'border-radius:4px;">' + "".join(panel_bits) + '</div>'
        )

    footer = (
        '<div style="margin-top:18px; padding-top:10px; '
        'border-top:1px dashed ' + _TAB_RULE_LIGHT + '; font-size:10px; '
        'color:' + _TAB_INK_MUTED + '; font-style:italic; text-align:center;">'
        'Certificate-style preview. Sample values shown; live data is '
        'bound at runtime via SSRS.</div>'
    )

    body = title_html + (param_html or "") + "".join(panels) + footer
    return (
        '<div style="font-family:Arial,Helvetica,sans-serif; '
        'background:' + _TAB_PAPER + '; color:' + _TAB_INK + '; '
        'padding:32px 36px; border:1px solid ' + _TAB_RULE_LIGHT + '; '
        'max-width:920px; margin:0 auto; line-height:1.4;">'
        + body + '</div>'
    )



# ---------------------------------------------------------------------------
# Multi-page rendering
#
# The artifacts the user provided show that each report should preview as a
# stack of distinct "pages" (a header summary page, then one or more detail
# pages). Frontend mode = fictional sample data so the page looks like the
# rendered SSRS output. Backend mode = Report Builder design surface, with
# F_FIELD_NAME placeholders visible.
# ---------------------------------------------------------------------------

# Soft gray "desk" background behind the white sheets, plus a subtle drop
# shadow per sheet. All styling is inline so the fragment is portable.
_PAGE_DESK_BG  = "#e8eaef"
_PAGE_SHEET_BG = "#ffffff"
_PAGE_BORDER   = "#cccccc"


def _render_page(content_html, label=None, max_width="8.25in", min_height="10.5in",
                 first_page=True):
    """Wrap content in a paper-sheet div so the preview shows page boundaries.

    When ``first_page`` is False, a faint dashed horizontal rule is emitted
    above the page label so the divider reads clearly between pages of a
    multi-page preview. The label itself is rendered in the navy band color
    used elsewhere in the report, at a larger weight than the older grey
    caption it replaces.
    """
    rule_html = ""
    if label and not first_page:
        rule_html = (
            '<div style="border-top:1px dashed #cbd5e1; '
            'max-width:8.25in; margin:0 auto 12px;"></div>'
        )
    label_html = ""
    if label:
        label_html = (
            '<div style="text-align:center; font-size:13px; font-weight:600; '
            'color:#000079; letter-spacing:0.3px; '
            'margin:16px 0 6px;">' + _esc(label) + '</div>'
        )
    return (
        rule_html
        + label_html
        + '<div style="background:' + _PAGE_SHEET_BG + '; '
        'max-width:' + max_width + '; min-height:' + min_height + '; '
        'margin:18px auto; padding:0.6in 0.7in; '
        'border:1px solid ' + _PAGE_BORDER + '; '
        'box-shadow:0 4px 14px rgba(15,23,42,0.10); '
        'position:relative; font-family:Arial,Helvetica,sans-serif; '
        'color:' + _TAB_INK + '; line-height:1.4;">'
        + content_html
        + '</div>'
    )


def _render_pages_wrapper(pages_html):
    """Concatenate page HTML strings inside the desk-background container.

    A faint dashed horizontal rule is inserted between every adjacent pair of
    pages so the boundary between successive sheets is easy to read while
    scrolling through a multi-page preview. The first page has no rule above
    it.
    """
    _DIVIDER = (
        '<div style="border-top:1px dashed #cbd5e1; '
        'max-width:8.25in; margin:0 auto 12px;"></div>'
    )
    if not pages_html:
        joined = ""
    else:
        joined = pages_html[0]
        for p in pages_html[1:]:
            joined += _DIVIDER + p
    return (
        '<div style="background:' + _PAGE_DESK_BG + '; '
        'padding:24px 0; min-height:100%;">'
        + joined
        + '</div>'
    )


# ---------------------------------------------------------------------------
# Frontend: shared header-summary page
# ---------------------------------------------------------------------------

def _collect_header_layout_items(report):
    """Walk section_header (including nested frames) and return a flat list of
    (kind, name, source, text, x, y, width, height) tuples for every text/field."""
    items = []
    for g in report.layout or []:
        if g.kind != "section_header":
            continue
        def walk(gr):
            for f in (gr.fields or []):
                if f.kind not in ("text", "field"):
                    continue
                items.append((
                    f.kind, f.name, f.source or "", (f.text or ""),
                    f.x, f.y, f.width, f.height, f
                ))
            for ch in (gr.children or []):
                walk(ch)
        walk(g)
    return items


def _header_label_value_pairs(items):
    """From a flat item list, return (label_text, value_item) pairs where a
    `text` ending in ':' is horizontally followed by a `field` (or another
    text with a source) on the same row (y diff < 0.2 in)."""
    texts  = [it for it in items if it[0] == "text" and (it[3] or "").strip().endswith(":")]
    others = [it for it in items if it is not None]
    pairs = []
    used  = set()
    for lab in texts:
        _, lname, _, ltext, lx, ly, lw, lh, _lf = lab
        # find best candidate to the right on the same row
        best = None
        best_dx = None
        for cand in others:
            ckind, cname, csrc, ctext, cx, cy, cw, ch, _cf = cand
            if cname == lname:
                continue
            if abs(cy - ly) > 0.25:
                continue
            if cx + 0.001 < lx + lw - 0.01:
                continue  # must be to the right of the label
            # prefer field over text; require some content tie
            if ckind == "text" and not csrc and not (ctext or "").strip():
                continue
            dx = cx - (lx + lw)
            if dx < -0.05 or dx > 3.5:
                continue
            if best is None or dx < best_dx:
                best = cand
                best_dx = dx
        if best is not None and id(best) not in used:
            pairs.append((ltext.strip().rstrip(":").strip(), best))
            used.add(id(best))
    return pairs


_CANNED_RUN_LABELS = ("run date", "run by", "total")
_PARAMS_HEADING_RE = re.compile(r"^\*?\s*report\s+parameters\s*$", re.I)


def _header_has_parameters_heading(items):
    """True if section_header carries a standalone 'Report Parameters' heading
    text (any case, optional leading *). This is the structural marker that
    the existing centered template was designed around."""
    for it in items:
        kind, _name, _src, text, _x, _y, _w, _h, _f = it
        if kind != "text":
            continue
        if _PARAMS_HEADING_RE.match((text or "").strip()):
            return True
    return False


def _is_canned_run_label(text):
    t = (text or "").strip().lower().rstrip(":").strip()
    return any(t.startswith(prefix) for prefix in _CANNED_RUN_LABELS)


def _render_rich_header_page(report, items, pairs, page_label):
    """Render page 1 as a left-aligned `<label>: <value>` info list, mirroring
    the section_header layout (rows sorted top-to-bottom)."""

    # Sort pairs by the label's y, then x.
    def label_pos(p):
        lt = p[0]
        # find the matching label item
        for it in items:
            if it[0] == "text" and (it[3] or "").strip().rstrip(":").strip() == lt:
                return (it[5], it[4])
        return (0.0, 0.0)
    pairs_sorted = sorted(pairs, key=label_pos)

    rows_html_bits = []
    for label_text, value_item in pairs_sorted:
        vkind, _vname, vsrc, vtext, _vx, _vy, _vw, _vh, _vf = value_item
        if vkind == "field":
            if (vsrc or "").lower() == "currentdate":
                val = _sample_for_source("date", 0)
            else:
                val = _sample_for_source(vsrc or vtext or "value", 0)
        else:
            # static text: resolve any &TOKEN substitutions
            val = _resolve_tokens(vtext or "")
            val = re.sub(r"&<[^>]+>", "", val).strip()
        rows_html_bits.append(
            '<div style="display:flex; align-items:baseline; margin:4px 0;">'
            '<div style="min-width:200px; max-width:240px; text-align:left; '
            'padding-right:12px; font-weight:bold; color:' + _TAB_INK + '; '
            'font-size:13px;">' + _esc(label_text) + ':</div>'
            '<div style="flex:1; text-align:left; color:' + _TAB_INK + '; '
            'font-size:13px; font-weight:bold;">'
            + _esc(val) + '</div></div>'
        )

    # Also surface any unpaired wide text blocks (e.g. footnote/info lines)
    # that sit below the last pair — render as plain italic notes.
    used_ids = {id(v) for _, v in pairs}
    used_ids.update(id(it) for it in items
                    if it[0] == "text" and (it[3] or "").strip().endswith(":"))
    last_y = max((label_pos(p)[0] for p in pairs_sorted), default=0.0)
    notes = []
    for it in items:
        if id(it) in used_ids:
            continue
        kind, _n, _s, text, _x, y, _w, _h, _f = it
        if kind != "text":
            continue
        t = (text or "").strip()
        if not t or t.endswith(":"):
            continue
        if y < last_y - 0.1:
            continue
        notes.append((y, t))
    notes.sort()
    notes_html = ""
    for _y, t in notes[:4]:
        notes_html += (
            '<div style="margin:6px 0 0; color:' + _TAB_INK_SOFT + '; '
            'font-size:12px; font-style:italic;">' + _esc(t) + '</div>'
        )

    inner = (
        '<div style="padding:36px 32px 28px; max-width:640px; margin:0 auto;">'
        + "".join(rows_html_bits)
        + notes_html
        + '</div>'
    )
    return _render_page(inner, label=page_label)


def _render_header_summary_page(report, page_label="Page 1 — Header summary"):
    """Page 1: dispatches between a structured `<label>: <value>` info list
    (when the report's section_header is rich, e.g. label+field pairs that
    are themselves the header content) and the legacy centered "title +
    Run Date/Run By/Total + Report Parameters" template (when the header is
    sparse and a separate 'Report Parameters' heading divides parameter
    rows from a centered title)."""

    # --- Rich-header detection ---------------------------------------------
    items = _collect_header_layout_items(report)
    pairs = _header_label_value_pairs(items)
    has_params_heading = _header_has_parameters_heading(items)

    # Pairs that are NOT canned run-info rows (Run Date / Run By / Total).
    non_canned_pairs = [p for p in pairs if not _is_canned_run_label(p[0])]
    # Y-span of those non-canned pairs (in inches).
    if non_canned_pairs:
        ys = []
        for label_text, value_item in non_canned_pairs:
            ys.append(value_item[5])
            for it in items:
                if it[0] == "text" and (it[3] or "").strip().rstrip(":").strip() == label_text:
                    ys.append(it[5])
                    break
        y_span = max(ys) - min(ys) if ys else 0.0
    else:
        y_span = 0.0

    # Rich when:
    #  * there are >= 4 non-canned label:value pairs spanning > 2 inches AND
    #  * there is NO standalone "Report Parameters" heading (which is the
    #    structural marker of the legacy centered template).
    is_rich = (len(non_canned_pairs) >= 4
               and y_span > 2.0
               and not has_params_heading)

    if is_rich:
        return _render_rich_header_page(report, items, pairs, page_label)

    # Title block — use the largest centered text from the layout if present.
    title_field = _find_title_text(report)
    if title_field is not None:
        title_raw = _resolve_tokens(title_field.text or "")
        title_raw = re.sub(
            r":P_[A-Za-z][A-Za-z0-9_]*",
            lambda m: _sample_for_source(m.group(0)[1:], 0),
            title_raw,
        )
        title_color = _normalize_color(
            _attr(title_field, "color", ""), "#000080"
        )
        title_lines = [ln.strip() for ln in title_raw.splitlines() if ln.strip()]
    else:
        title_color = "#000080"
        title_lines = [report.name or "Report", "Detail Report"]

    # Strip SSRS angle-bracket builtins like &<PhysicalPageNumber> from titles
    title_lines = [re.sub(r"&<[^>]+>", "", ln).strip() for ln in title_lines]
    title_lines = [ln for ln in title_lines if ln]

    title_html_bits = []
    for ln in title_lines[:4]:
        title_html_bits.append(
            '<div style="font-family:\'Courier New\',Courier,monospace; '
            'font-size:14px; font-weight:bold; color:' + title_color + '; '
            'text-align:center; letter-spacing:2px; line-height:1.5;">'
            + _esc(ln) + '</div>'
        )
    title_html = ("<div style=\"margin:0 0 28px;\">"
                  + "".join(title_html_bits) + "</div>")

    # Run-info rows: Run Date, Run By, Total of ALL Records.
    # These come from the parsed header section if present, otherwise fall
    # back to plausible fictional values.
    run_rows_html_bits = []
    canned_run_rows = [
        ("Run Date",            _sample_for_source("date", 0) + " " + "13:00:00"),
        ("Run By",              _sample_for_source("user", 0)),
        ("Total of ALL Records", _sample_for_source("count", 0)),
    ]
    for label, val in canned_run_rows:
        run_rows_html_bits.append(
            '<div style="display:flex; justify-content:center; '
            'align-items:baseline; margin:3px 0;">'
            '<div style="width:160px; text-align:right; padding-right:8px; '
            'font-weight:bold; color:' + _TAB_INK + '; font-size:13px;">'
            + _esc(label) + ':</div>'
            '<div style="min-width:160px; text-align:left; color:'
            + _TAB_INK + '; font-size:13px; font-weight:bold;">'
            + _esc(val) + '</div></div>'
        )
    run_html = ("<div style=\"margin:0 0 24px;\">"
                + "".join(run_rows_html_bits) + "</div>")

    # Report Parameters heading
    rp_heading = (
        '<div style="text-align:center; margin:14px 0 12px;">'
        '<span style="font-weight:bold; font-size:14px; color:' + _TAB_INK + '; '
        'border-bottom:2px solid ' + _TAB_INK + '; padding-bottom:2px; '
        'font-style:italic;">Report Parameters</span></div>'
    )

    # Parameter list (label: value, label on right). Visible params only.
    param_rows = []
    for p in (report.parameters or []):
        if not getattr(p, "display", True):
            continue
        raw_label = (p.label or p.name or "").replace("P_", "").replace("PARM_", "")
        label_pretty = raw_label.replace("_", " ").title()
        val = _param_value(p)
        # Param values that look like literal "ALL" / dates / numbers — keep them.
        # For the page-1 page, show blanks for most params except a few highlights.
        # (The reference artifact shows most parameters blank with only "County: CUSTER".)
        show_val = val if (val not in ("ALL", "") or label_pretty.lower().startswith("county")) else ""
        param_rows.append(
            '<div style="display:flex; align-items:baseline; margin:2px 0;">'
            '<div style="width:200px; text-align:right; padding-right:8px; '
            'font-weight:bold; color:' + _TAB_INK + '; font-size:13px;">'
            + _esc(label_pretty) + ':</div>'
            '<div style="min-width:140px; text-align:left; color:'
            + _TAB_INK + '; font-size:13px; font-weight:bold;">'
            + _esc(show_val) + '</div></div>'
        )
    param_list_html = "".join(param_rows) if param_rows else (
        '<div style="text-align:center; color:#888; font-style:italic; '
        'font-size:12px;">(no parameters declared)</div>'
    )

    # Wrap in a rounded outlined container to match the artifact look.
    inner = (
        '<div style="border:1px solid #888; border-radius:8px; '
        'padding:36px 24px 28px; max-width:520px; margin:0 auto;">'
        + title_html + run_html + rp_heading + param_list_html
        + '</div>'
    )
    return _render_page(inner, label=page_label)


# ---------------------------------------------------------------------------
# Frontend: TABULAR multi-page
# ---------------------------------------------------------------------------

def _render_tabular_detail_page(report, sample_idx, page_num, total_pages):
    """One detail page — navy County band + 2-3 complaint blocks with
    alternating gray/white shading + action sub-table per complaint."""

    # Mini-title at top of detail page (smaller than page 1)
    title_field = _find_title_text(report)
    title_color = "#000080"
    title_lines = ["Report"]
    if title_field is not None:
        title_raw = _resolve_tokens(title_field.text or "")
        title_color = _normalize_color(_attr(title_field, "color", ""), "#000080")
        title_lines = [re.sub(r"&<[^>]+>", "", ln).strip()
                       for ln in title_raw.splitlines() if ln.strip()][:3]
    title_top = ""
    for ln in title_lines:
        title_top += (
            '<div style="font-family:\'Courier New\',Courier,monospace; '
            'font-size:12px; font-weight:bold; color:' + title_color + '; '
            'text-align:center; letter-spacing:2px; line-height:1.4;">'
            + _esc(ln) + '</div>'
        )

    # "Report run on: ..." left, "Page N of M" right
    run_line = (
        '<div style="display:flex; justify-content:space-between; '
        'align-items:baseline; margin:12px 0 6px; font-size:12px; '
        'color:' + _TAB_INK + ';">'
        '<div>Report run on:&nbsp;<span style="font-weight:normal;">'
        + _esc(_sample_for_source("date", 0)) + ' 1:00 PM</span></div>'
        '<div style="font-style:italic; color:#000079;">Page '
        + str(page_num) + ' of ' + str(total_pages) + '</div></div>'
    )

    # Find the OUTER repeating frame (R_G_CNTY_NM equivalent — has band fields).
    main = _find_section(report.layout or [], "section_main")
    top_rep = None
    if main is not None:
        def find_rep(g):
            if g.kind == "repeating_frame":
                return g
            for ch in g.children or []:
                r = find_rep(ch)
                if r is not None:
                    return r
            return None
        top_rep = find_rep(main)

    if top_rep is None:
        # No repeating frame at all — emit a generic table from query columns
        body = '<div style="color:#888; font-style:italic;">(no repeating frames)</div>'
        return _render_page(title_top + run_line + body, label="Page " + str(page_num))

    # Band from outer frame
    band_bg = _normalize_color(_attr(top_rep, "background_color", ""), "#000079")
    band_fg = _normalize_color(_attr(top_rep, "foreground_color", ""), "#FFFF00")
    outer_pairs = _detail_field_pairs(top_rep)
    band_label_parts = []
    for label, src in outer_pairs[:2]:
        val = _sample_for_source(src, sample_idx)
        band_label_parts.append(
            '<span style="font-weight:bold;">' + _esc(label)
            + ':</span> <span style="font-weight:bold; margin-right:32px;">'
            + _esc(val) + '</span>'
        )
    band_html = (
        '<div style="background:' + band_bg + '; color:' + band_fg + '; '
        'padding:7px 14px; font-size:13px; '
        'display:flex; justify-content:space-between;">'
        '<div>' + (band_label_parts[0] if band_label_parts else "") + '</div>'
        '<div>' + (band_label_parts[1] if len(band_label_parts) > 1 else "") + '</div>'
        '</div>'
    )

    # Find inner frames: detail (e.g. R_G_CVID) and grandchild (e.g. R_G_STATUS_DATE)
    nested_all = []
    def collect_nested(g):
        for ch in g.children or []:
            if ch.kind == "repeating_frame" and ch is not top_rep:
                nested_all.append(ch)
            collect_nested(ch)
    collect_nested(top_rep)

    def _has_data(g):
        if any(f.kind == "field" for f in (g.fields or [])):
            return True
        return any(_has_data(ch) for ch in (g.children or []))
    nested = [g for g in nested_all if _has_data(g)]

    detail_rep = nested[0] if nested else None
    action_rep = nested[1] if len(nested) > 1 else None
    if not detail_rep:
        return _render_page(title_top + run_line + band_html, label="Page " + str(page_num))

    detail_pairs = _detail_field_pairs(detail_rep)
    action_pairs = _detail_field_pairs(action_rep) if action_rep else []

    # Emit 3 complaint blocks per page with alternating shading
    NUM_COMPLAINTS = 3
    SHADE_A = "#ececec"   # neutral mid-gray (matches artifact)
    SHADE_B = "#f7f7f7"   # neutral near-white (matches artifact)
    blocks = []
    for ci in range(NUM_COMPLAINTS):
        shade = SHADE_A if ci % 2 == 0 else SHADE_B
        # Use a different sample_idx-derived seed per complaint
        seed = sample_idx * 10 + ci

        # Find Complaint ID field (the first key-like field) for the blue header
        id_label = "Item ID"
        id_val = _sample_for_source("id", seed)
        if detail_pairs:
            id_label = detail_pairs[0][0]
            id_val = _sample_for_source(detail_pairs[0][1], seed)

        # Render the rest of detail_pairs in a 2-column key/value grid
        # (Owner/Location on left, Status/City on right) — best-effort by even/odd index.
        left_pairs, right_pairs = [], []
        for i, (label, src) in enumerate(detail_pairs[1:]):
            (left_pairs if i % 2 == 0 else right_pairs).append((label, src))

        def render_kv_col(pairs):
            rows = []
            for label, src in pairs:
                v = _sample_for_source(src, seed)
                rows.append(
                    '<div style="display:flex; margin:2px 0; font-size:12px;">'
                    '<div style="font-weight:bold; color:' + _TAB_INK + '; '
                    'min-width:90px; padding-right:6px;">' + _esc(label)
                    + ':</div><div style="color:' + _TAB_INK + ';">'
                    + _esc(v) + '</div></div>'
                )
            return "".join(rows)

        complaint_header = (
            '<div style="color:#000079; font-weight:bold; font-size:13px; '
            'padding:6px 14px 4px;">' + _esc(id_label) + ':&nbsp;' + _esc(id_val) + '</div>'
        )
        complaint_body = (
            '<div style="display:flex; padding:2px 12px 8px;">'
            '<div style="flex:1; padding-right:18px;">' + render_kv_col(left_pairs) + '</div>'
            '<div style="flex:1;">' + render_kv_col(right_pairs) + '</div>'
            '</div>'
        )

        # Action sub-table (only if present)
        action_html = ""
        if action_pairs:
            action_head = (
                '<div style="display:flex; padding:4px 12px 2px; '
                'border-top:1px solid #bbbbbb; font-size:12px; '
                'font-style:italic; color:' + _TAB_INK + ';">'
            )
            action_head_bits = []
            for label, _src in action_pairs[:3]:
                action_head_bits.append(
                    '<div style="flex:1; font-weight:bold;">' + _esc(label) + ':</div>'
                )
            action_head += "".join(action_head_bits) + '</div>'

            # Emit 1-2 sample action rows
            n_actions = 2 if ci % 2 == 1 else 1
            action_rows = []
            for ai in range(n_actions):
                action_seed = seed * 100 + ai
                action_row_bits = []
                for label, src in action_pairs[:3]:
                    v = _sample_for_source(src, action_seed)
                    action_row_bits.append(
                        '<div style="flex:1; padding-right:6px;">' + _esc(v) + '</div>'
                    )
                action_rows.append(
                    '<div style="display:flex; padding:2px 12px; font-size:12px; '
                    'color:' + _TAB_INK + ';">'
                    + "".join(action_row_bits) + '</div>'
                )
            action_html = action_head + "".join(action_rows)

        blocks.append(
            '<div style="background:' + shade + '; margin:0; '
            'border-bottom:1px solid #cccccc;">'
            + complaint_header + complaint_body + action_html
            + '</div>'
        )

    return _render_page(
        title_top + run_line + band_html + "".join(blocks),
        label="Page " + str(page_num) + " — Detail"
    )


def _render_tabular_pages(report):
    """Multi-page tabular: page 1 = header summary, pages 2-4 = detail pages."""
    NUM_DETAIL_PAGES = 3
    total_pages = 1 + NUM_DETAIL_PAGES
    pages = [_render_header_summary_page(report, page_label="Page 1 of " + str(total_pages))]
    for i in range(NUM_DETAIL_PAGES):
        pages.append(_render_tabular_detail_page(
            report, sample_idx=i, page_num=2 + i, total_pages=total_pages,
        ))
    return _render_pages_wrapper(pages)


# ---------------------------------------------------------------------------
# Frontend: CERTIFICATE multi-page
# ---------------------------------------------------------------------------

def _certificate_sample_facts(idx):
    """Returns a fictional permit's identity fields, varied per page."""
    POOL = [
        {
            "perm_num":   "A-0117",
            "perm_type":  "STATE OF SAMPLE",
            "renewal":    "2026",
            "dept":       "SAMPLE AGENCY",
            "license":    "SAMPLE FACILITY LICENSE",
            "perm_id":    "PERM-0117",
            "address1":   "100 Main St",
            "address2":   "Springfield, ST 00000",
            "operator":   "Alex Rivera",
            "facility":   "Acme Holdings (117)",
            "perm_dates": "JANUARY 1, 2026 TO DECEMBER 31, 2026",
            "exp_date":   "DECEMBER 31, 2026",
        },
        {
            "perm_num":   "A-0231",
            "perm_type":  "STATE OF SAMPLE",
            "renewal":    "2026",
            "dept":       "SAMPLE AGENCY",
            "license":    "SAMPLE FACILITY LICENSE",
            "perm_id":    "PERM-0231",
            "address1":   "200 Commerce Way",
            "address2":   "Riverside, ST 00000",
            "operator":   "Jordan Casey",
            "facility":   "Northwind Industries (231)",
            "perm_dates": "JANUARY 1, 2026 TO DECEMBER 31, 2026",
            "exp_date":   "DECEMBER 31, 2026",
        },
        {
            "perm_num":   "A-0314",
            "perm_type":  "STATE OF SAMPLE",
            "renewal":    "2026",
            "dept":       "SAMPLE AGENCY",
            "license":    "SAMPLE FACILITY LICENSE",
            "perm_id":    "PERM-0314",
            "address1":   "300 Industrial Blvd",
            "address2":   "Lakeside, ST 00000",
            "operator":   "Sam Lee",
            "facility":   "Globex Group (314)",
            "perm_dates": "JANUARY 1, 2026 TO DECEMBER 31, 2026",
            "exp_date":   "DECEMBER 31, 2026",
        },
    ]
    return POOL[idx % len(POOL)]


def _render_certificate_body(facts):
    """The big certificate body — STATE OF X / DEPT / LICENSE / permit num / etc."""
    return (
        '<div style="text-align:center; padding:20px 24px 18px;">'
        '<div style="font-size:22px; font-weight:bold; letter-spacing:1px;">'
        + _esc(facts["perm_type"]) + '</div>'
        '<div style="font-size:20px; font-weight:bold; margin-top:4px;">'
        + _esc(facts["renewal"]) + '</div>'
        '<div style="font-size:14px; font-weight:bold; margin-top:6px;">'
        + _esc(facts["dept"]) + '</div>'
        '<div style="font-size:14px; font-weight:bold;">'
        + _esc(facts["license"]) + '</div>'
        '<div style="font-size:28px; font-weight:bold; margin:22px 0 14px;">'
        + _esc(facts["perm_num"]) + '</div>'
        '<div style="font-size:14px; font-weight:bold;">'
        + _esc(facts["address1"]) + '</div>'
        '<div style="font-size:14px; font-weight:bold;">'
        + _esc(facts["address2"]) + '</div>'
        '<div style="font-size:14px; font-weight:bold; margin-top:4px;">'
        + _esc(facts["operator"]) + '</div>'
        '<div style="font-size:12px; margin-top:8px;">IS LICENSED TO OPERATE</div>'
        '<div style="font-size:16px; font-weight:bold; margin-top:4px;">'
        + _esc(facts["facility"]) + '</div>'
        '<div style="font-size:12px; margin-top:8px;">LOCATED AT</div>'
        '<div style="font-size:14px; font-weight:bold; margin-top:4px;">'
        + _esc(facts["address1"]) + '</div>'
        '<div style="font-size:14px; font-weight:bold;">'
        + _esc(facts["address2"]) + '</div>'
        '<div style="font-size:12px; margin-top:10px;">FOR THE PERIOD</div>'
        '<div style="font-size:15px; font-weight:bold; margin-top:4px;">'
        + _esc(facts["perm_dates"]) + '</div>'
        '</div>'
        '<div style="padding:12px 24px 6px; font-size:11px; '
        'line-height:1.5; color:' + _TAB_INK + ';">'
        'THIS ANNUAL LICENSE IS CONDITIONED ON THE CONSTRUCTION AND MANAGEMENT '
        'OF THE FACILITY AS APPROVED BY THE DEPARTMENT AND ON CONDITIONS '
        'IMPOSED BY THE ORIGINAL LICENSE.  THE LICENSEE SHOULD BE AWARE THAT '
        'ITS FAILURE TO COMPLY WITH APPLICABLE LAWS OR RULES MAY RESULT IN '
        'ENFORCEMENT ACTIONS, LICENSE REVOCATION, OR DENIAL OF AN APPLICATION '
        'FOR RENEWAL.'
        '</div>'
        '<div style="padding:36px 24px 6px;">'
        '<div style="font-family:\'Brush Script MT\',cursive; font-size:22px; '
        'color:#222; border-bottom:1px solid #444; '
        'padding-bottom:4px; max-width:280px;">Sample Signature</div>'
        '<div style="font-size:12px; font-weight:bold; margin-top:6px;">'
        'SAMPLE NAME, SAMPLE TITLE</div>'
        '<div style="font-size:11px; color:' + _TAB_INK_SOFT + '; '
        'margin-top:2px;">PO BOX 0000<br>SAMPLE ST 00000-0000<br>'
        'SAMPLE OFFICE<br>(000)555-0100</div>'
        '<div style="text-align:center; font-size:11px; font-weight:bold; '
        'margin-top:12px;">THIS CERTIFICATE IS NOT TRANSFERABLE. '
        'A LICENSE RENEWAL APPLICATION IS DUE ' + _esc(facts["exp_date"]) + '.</div>'
        '</div>'
    )


def _render_certificate_card(facts):
    """One wallet card — condensed permit."""
    return (
        '<div style="flex:1; border:1px solid ' + _TAB_INK + '; '
        'padding:10px 12px 14px; text-align:center; '
        'min-height:160px;">'
        '<div style="font-size:11px; font-weight:bold;">'
        + _esc(facts["perm_type"]) + ' ' + _esc(facts["renewal"]) + '</div>'
        '<div style="font-size:11px; font-weight:bold;">' + _esc(facts["dept"]) + '</div>'
        '<div style="font-size:11px; font-weight:bold;">' + _esc(facts["license"]) + '</div>'
        '<div style="font-size:16px; font-weight:bold; margin:10px 0 6px;">'
        + _esc(facts["perm_num"]) + '</div>'
        '<div style="font-size:11px; font-weight:bold;">'
        + _esc(facts["operator"]) + ' is licensed to operate</div>'
        '<div style="font-size:11px; font-weight:bold;">' + _esc(facts["facility"]) + '</div>'
        '<div style="font-size:11px; font-weight:bold;">' + _esc(facts["address1"]) + '</div>'
        '<div style="font-size:11px; font-weight:bold;">' + _esc(facts["address2"]) + '</div>'
        '<div style="font-size:11px; margin-top:10px;">expires '
        + _esc(facts["exp_date"]) + '</div>'
        '</div>'
    )


def _render_certificate_iteration_page(facts, page_num, total_pages):
    """One full permit certificate (body + two wallet cards) wrapped as
    a single page."""
    body = _render_certificate_body(facts)
    cards = (
        '<div style="display:flex; gap:16px; margin:18px 0 0; padding:0 24px;">'
        + _render_certificate_card(facts)
        + _render_certificate_card(facts)
        + '</div>'
    )
    # Outer thin black frame around the whole sheet (matches reference)
    inner = (
        '<div style="border:1.5px solid #000; padding:14px;">'
        + body + cards
        + '</div>'
    )
    return _render_page(
        inner,
        label="Page " + str(page_num) + " of " + str(total_pages) + " — Permit",
    )


def _render_certificate_pages(report):
    """Multi-page certificate: page 1 = header summary, pages 2-4 = full
    permit certificates with two wallet cards each."""
    NUM_PERMITS = 3
    total_pages = 1 + NUM_PERMITS
    pages = [_render_header_summary_page(report, page_label="Page 1 of " + str(total_pages))]
    for i in range(NUM_PERMITS):
        facts = _certificate_sample_facts(i)
        pages.append(_render_certificate_iteration_page(facts, page_num=2 + i, total_pages=total_pages))
    return _render_pages_wrapper(pages)


# ---------------------------------------------------------------------------
# Backend: Report Builder design-view (multi-page, one page per section)
# ---------------------------------------------------------------------------

_BE_BG          = "#e6e6e6"   # the design surface gray
_BE_FRAME_BG    = "#fafafa"
_BE_FIELD_BG    = "#ffffff"
_BE_FIELD_BORD  = "#888888"


def _be_field_box(label_text, name_text, label_align="right",
                  bg=None, fg=None, width="2.6in"):
    """Render one design-view field: an optional label on the left and a
    bordered placeholder box with the field's source name inside."""
    label_html = ""
    if label_text:
        label_html = (
            '<span style="display:inline-block; padding-right:6px; '
            'text-align:' + label_align + '; font-weight:bold; '
            'font-size:12px; color:' + _TAB_INK + ';">'
            + _esc(label_text) + '</span>'
        )
    box_bg = bg or _BE_FIELD_BG
    box_fg = fg or _TAB_INK
    return (
        '<span style="display:inline-flex; align-items:center; '
        'margin:1px 4px;">'
        + label_html
        + '<span style="display:inline-block; min-width:' + width + '; '
        'padding:1px 6px; border:1px solid ' + _BE_FIELD_BORD + '; '
        'background:' + box_bg + '; color:' + box_fg + '; '
        'font-size:12px; font-family:Arial,Helvetica,sans-serif;">'
        + _esc(name_text) + '</span></span>'
    )


def _render_backend_header_page(report):
    """Backend page 1 — section_header design view. Shows the title, the
    Run Date / Run By / Total fields as labeled boxes, and the Report
    Parameters list of labeled boxes (one per parameter)."""

    # Pull the static title text from layout
    title_field = _find_title_text(report)
    title_color = "#000080"
    title_lines = []
    if title_field is not None:
        title_color = _normalize_color(_attr(title_field, "color", ""), "#000080")
        for ln in (title_field.text or "").splitlines():
            ln = ln.strip()
            if ln:
                title_lines.append(re.sub(r"&<[^>]+>", "", ln).strip())
    if not title_lines:
        title_lines = [report.name or "Report"]

    title_html = ""
    for ln in title_lines[:4]:
        title_html += (
            '<div style="font-family:\'Courier New\',Courier,monospace; '
            'font-size:14px; font-weight:bold; color:' + title_color + '; '
            'text-align:center; letter-spacing:2px;">' + _esc(ln) + '</div>'
        )

    # Build the run-info rows + parameter list using design-view boxes.
    # We pull every section_header field that points at a real source
    # so the user sees ALL the F_* field placeholders, mirroring the
    # Oracle Reports Builder design canvas.
    header_section = _find_section(report.layout or [], "section_header")
    rows = []
    if header_section is not None:
        # walk fields, pairing static-text labels with following field boxes
        all_fields = []
        def walk(g):
            for f in g.fields or []:
                all_fields.append(f)
            for ch in g.children or []:
                walk(ch)
        walk(header_section)
        # Sort by (y, x) for natural top-down reading order
        all_fields.sort(key=lambda f: (round(f.y or 0.0, 2), round(f.x or 0.0, 2)))
        # Group fields that are on roughly the same y as one "row"
        rows_grouped = []
        current_row = []
        current_y = None
        for f in all_fields:
            y = round(f.y or 0.0, 1)
            if current_y is None or abs(y - current_y) < 0.05:
                current_row.append(f)
                current_y = y if current_y is None else current_y
            else:
                rows_grouped.append(current_row)
                current_row = [f]
                current_y = y
        if current_row:
            rows_grouped.append(current_row)

        for row in rows_grouped:
            # row may contain pairs: label text + field box
            pending_label = None
            row_html_bits = []
            for f in sorted(row, key=lambda x: round(x.x or 0.0, 2)):
                if f.kind == "text":
                    pending_label = (f.text or "").strip().rstrip(":")
                elif f.kind == "field":
                    name = f.source or f.name or ""
                    row_html_bits.append(_be_field_box(
                        pending_label, "F_" + name, label_align="right",
                    ))
                    pending_label = None
                elif f.kind == "image":
                    row_html_bits.append(_be_field_box(
                        pending_label, "[image " + (f.name or "") + "]",
                        label_align="right",
                    ))
                    pending_label = None
            if row_html_bits:
                rows.append('<div style="text-align:center; margin:2px 0;">'
                            + "".join(row_html_bits) + '</div>')

    if not rows:
        # Fall back: derive from report.parameters
        for p in (report.parameters or [])[:14]:
            label = (p.label or p.name or "").replace("P_", "").replace("PARM_", "")
            label = label.replace("_", " ").title()
            rows.append('<div style="text-align:center; margin:2px 0;">'
                        + _be_field_box(label, "F_" + (p.name or ""))
                        + '</div>')

    inner = (
        '<div style="background:' + _BE_FRAME_BG + '; border:1px solid #888; '
        'border-radius:8px; padding:32px 24px 28px; max-width:660px; '
        'margin:0 auto;">'
        + '<div style="margin-bottom:14px;">' + title_html + '</div>'
        + '<div style="text-align:center; margin:14px 0 12px;">'
        '<span style="font-weight:bold; font-size:14px; '
        'border-bottom:2px solid ' + _TAB_INK + '; padding-bottom:2px; '
        'font-style:italic;">Report Parameters</span></div>'
        + "".join(rows)
        + '</div>'
    )
    return _render_page(inner, label="Page 1 — Header design view")


def _render_backend_main_page(report):
    """Backend page 2 — section_main design view. Shows the repeating
    frames with their navy/yellow band colors and the field placeholders
    visible at their layout positions."""

    title_field = _find_title_text(report)
    title_color = "#000080"
    title_lines = []
    if title_field is not None:
        title_color = _normalize_color(_attr(title_field, "color", ""), "#000080")
        for ln in (title_field.text or "").splitlines():
            ln = ln.strip()
            if ln:
                title_lines.append(re.sub(r"&<[^>]+>", "", ln).strip())
    if not title_lines:
        title_lines = [report.name or "Report"]
    title_top = ""
    for ln in title_lines[:3]:
        title_top += (
            '<div style="font-family:\'Courier New\',Courier,monospace; '
            'font-size:12px; font-weight:bold; color:' + title_color + '; '
            'text-align:center; letter-spacing:2px;">' + _esc(ln) + '</div>'
        )

    main = _find_section(report.layout or [], "section_main")
    if main is None:
        return _render_page(title_top + "<div>(no main section)</div>",
                            label="Page 2 — Main design view")

    # Find outer repeating frame; emit its band + nested fields
    def find_rep(g):
        if g.kind == "repeating_frame":
            return g
        for ch in g.children or []:
            r = find_rep(ch)
            if r is not None:
                return r
        return None
    top_rep = find_rep(main)

    body_bits = []

    # "Report run on:" header row
    # Auto-detect a "current date" field from the layout (CurrentDate source,
    # or any field whose name matches /date/i) so we don't hardcode METH's
    # F_DATE1_SEC2. Falls back to a generic placeholder.
    date_field_name = "F_RunDate"
    for g in _iter_layout(report):
        for f in (g.fields or []):
            if f.kind != "field":
                continue
            src = (f.source or "").lower()
            nm = (f.name or "").lower()
            if "currentdate" in src or "date" in src or "date" in nm:
                date_field_name = "F_" + (f.name or f.source or "RunDate")
                break
        if date_field_name != "F_RunDate":
            break
    body_bits.append(
        '<div style="display:flex; justify-content:space-between; '
        'align-items:baseline; margin:12px 0 6px; font-size:12px;">'
        '<div>Report run on: '
        + _be_field_box(None, date_field_name, width="1.4in")
        + '</div><div style="font-style:italic; color:#1a3a8f;">'
        'Page &lt;PageNumber&gt;</div></div>'
    )

    if top_rep is not None:
        band_bg = _normalize_color(_attr(top_rep, "background_color", ""), "#000079")
        band_fg = _normalize_color(_attr(top_rep, "foreground_color", ""), "#FFFF00")
        outer_pairs = _detail_field_pairs(top_rep)
        # band: 2 field-placeholders in the colored bar
        left_box = ""
        right_box = ""
        if outer_pairs:
            l_label, l_src = outer_pairs[0]
            left_box = ('<span style="margin-right:6px;">' + _esc(l_label) + ':</span>'
                        + _be_field_box(None, "F_" + l_src, width="1.2in",
                                        bg="#ffffff", fg="#000000"))
        if len(outer_pairs) > 1:
            r_label, r_src = outer_pairs[1]
            right_box = ('<span style="margin-right:6px;">' + _esc(r_label) + ':</span>'
                         + _be_field_box(None, "F_" + r_src, width="1.6in",
                                         bg="#ffffff", fg="#000000"))
        body_bits.append(
            '<div style="background:' + band_bg + '; color:' + band_fg + '; '
            'padding:5px 12px; font-size:13px; font-weight:bold; '
            'display:flex; justify-content:space-between; align-items:center;">'
            '<div>' + left_box + '</div>'
            '<div>' + right_box + '</div>'
            '</div>'
        )

        # Inner detail fields
        nested_all = []
        def collect_nested(g):
            for ch in g.children or []:
                if ch.kind == "repeating_frame" and ch is not top_rep:
                    nested_all.append(ch)
                collect_nested(ch)
        collect_nested(top_rep)

        def _has_data(g):
            if any(f.kind == "field" for f in (g.fields or [])):
                return True
            return any(_has_data(ch) for ch in (g.children or []))
        nested = [g for g in nested_all if _has_data(g)]

        if nested:
            detail_rep = nested[0]
            pairs = _detail_field_pairs(detail_rep)
            # Render as a 2-column grid of (label, F_NAME) pairs
            block_bits = []
            for i, (label, src) in enumerate(pairs[:14]):
                box = _be_field_box(label, "F_" + src, width="2.0in")
                block_bits.append(box)
            body_bits.append(
                '<div style="background:#e8eaf0; padding:8px 12px; '
                'display:flex; flex-wrap:wrap; gap:6px 12px;">'
                + "".join(block_bits) + '</div>'
            )

        if len(nested) > 1:
            action_rep = nested[1]
            action_pairs = _detail_field_pairs(action_rep)
            if action_pairs:
                head_bits = []
                row_bits = []
                for label, src in action_pairs[:4]:
                    head_bits.append(
                        '<div style="flex:1; font-weight:bold; '
                        'font-style:italic; padding-right:8px;">' + _esc(label) + ':</div>'
                    )
                    row_bits.append(
                        '<div style="flex:1; padding-right:8px;">'
                        + _be_field_box(None, "F_" + src, width="1.6in")
                        + '</div>'
                    )
                body_bits.append(
                    '<div style="display:flex; padding:6px 12px; font-size:12px; '
                    'border-top:1px solid #bcc1cc;">' + "".join(head_bits) + '</div>'
                    '<div style="display:flex; padding:2px 12px;">'
                    + "".join(row_bits) + '</div>'
                )

    # Wrap in a bordered box (the Reports Builder canvas frame)
    inner = (
        '<div style="border:1.5px solid #444; background:' + _BE_FRAME_BG + '; '
        'padding:0;">' + title_top + "".join(body_bits) + '</div>'
    )
    return _render_page(inner, label="Page 2 — Main design view")


def _render_design_view(report):
    """Backend mode: a 2-page Report Builder design view.

    Page 1 = section_header design (title, run info, parameter form).
    Page 2 = section_main design (repeating frames with colored bands and
    F_FIELD_NAME placeholders).
    """
    pages = [_render_backend_header_page(report)]
    main = _find_section(report.layout or [], "section_main")
    if main is not None:
        pages.append(_render_backend_main_page(report))
    return _render_pages_wrapper(pages)


# ---------------------------------------------------------------------------
# Public dispatcher
# ---------------------------------------------------------------------------

def render_mockup(report, mode="frontend"):
    """Public entry point. Returns a multi-page HTML string.

    mode="frontend": fictional sample data, each preview = N styled pages
        that mimic the rendered SSRS output (header summary + detail pages
        for tabular, or header + multiple permit certificates for certificate).

    mode="backend": Report Builder design surface, 2 pages (section_header
        design + section_main design) with F_FIELD_NAME placeholders visible.
    """
    global _ACTIVE_MODE
    prev = _ACTIVE_MODE
    _ACTIVE_MODE = "backend" if mode == "backend" else "frontend"
    try:
        if mode == "backend":
            return _render_design_view(report)
        kind = detect_report_kind(report)
        if kind == "letter":
            # Letter style retains single-page (no obvious repeating data).
            return _render_pages_wrapper([_render_page(_render_letter_mockup(report))])
        if kind == "certificate":
            return _render_certificate_pages(report)
        return _render_tabular_pages(report)
    finally:
        _ACTIVE_MODE = prev

