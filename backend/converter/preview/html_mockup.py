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
        'font-weight:bold; letter-spacing:1px;">_____________, BUREAU CHIEF</div>'
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
    "CF_WUTMB_CHIEF":  "Bureau Chief, Waste & Underground Tank Management Bureau",
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
            f'<div style="font-size:11px; color:{INK_MUTED};">Director / Bureau Chief</div>'
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

    All checks are structural — driven by the parsed report's shape, never
    by formula names, agency-specific tokens, or report names tied to any
    particular customer.

    Order (most specific first):
      1. Strong letter signal — name suffix _LTR/_LETTER (an industry-wide
         Oracle Reports naming convention for correspondence reports).
      2. Strong tabular signal — at least one REPEATING FRAME carries an
         explicit background_color in its <visualSettings> (banded report).
      3. Strong certificate signal — section_main has 2+ positional sibling
         frames AND at least 2 multi-line static text blocks among them
         (permit/certificate layout: stamps, titles, legal text).
      4. Medium tabular — 2+ repeating frames with detail-data fields.
      5. Medium letter — 3+ paragraph-shaped text blocks (formal body copy).
      6. Weak tabular — any color anywhere AND no clear certificate shape.
      7. Default — certificate (degrades gracefully for unknown layouts).
    """
    name = (report.name or "").upper()
    if "_LTR" in name or "_LETTER" in name:
        return "letter"

    # Strongest tabular signal: a repeating frame with a band color.
    for g in _iter_layout(report):
        if g.kind == "repeating_frame":
            if _attr(g, "background_color"):
                return "tabular_details"
            for f in g.fields or []:
                if _attr(f, "background_color"):
                    return "tabular_details"

    # Certificate signal (positional cards/stamps with multi-line static text)
    # checked BEFORE medium tabular and letter so permit-style reports route
    # here instead of getting a generic letter template.
    main = _find_section(report.layout or [], "section_main")
    if main is not None:
        frames = [c for c in (main.children or []) if c.kind == "frame"]
        multiline_text_count = 0
        for fr in frames:
            for f in (fr.fields or []):
                text = f.text or ""
                if f.kind == "text" and "\n" in text and len(text) >= 30:
                    multiline_text_count += 1
        if len(frames) >= 2 and multiline_text_count >= 2:
            return "certificate"

    # Medium tabular: 2+ repeating frames with detail data.
    reps_with_data = [
        g for g in _iter_layout(report)
        if g.kind == "repeating_frame"
        and any(f.kind == "field" for f in (g.fields or []))
    ]
    if len(reps_with_data) >= 2:
        return "tabular_details"

    # Medium letter: 3+ paragraph-shaped text blocks (long, multiline).
    if _count_paragraphy_text_blocks(report) >= 3:
        return "letter"

    if _has_color_signal(report):
        return "tabular_details"

    # Final fallback: certificate. The certificate renderer just walks
    # whatever frames the report has, so it degrades gracefully for
    # unknown shapes — better than emitting fake banded tables.
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

    The pool is built from neutral structural keywords (id, name, date, addr,
    city, type, status, comment, count, etc.) — never specific to any one
    report. If the column name doesn't match a keyword, return a generic
    'Sample Value' placeholder. This guarantees that no report's actual
    field names or values can be hard-coded into the preview output.
    """
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
        for ln in (title.text or "").splitlines():
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
                    pending_label = txt.rstrip(":")
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


def render_mockup(report):
    """Public entry point. Dispatches on detect_report_kind()."""
    kind = detect_report_kind(report)
    if kind == "letter":
        return _render_letter_mockup(report)
    if kind == "certificate":
        return _render_certificate_mockup(report)
    return _render_tabular_details(report)
