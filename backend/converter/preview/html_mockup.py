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
    # Generic main-query selector: pick the query with the most items.
    # F8: a FLAT <link> child (parent_group set, no nested group chain of its
    # own) may be inflated past its master by join-key augmentation -- prefer a
    # non-child master so the preview binds to the primary entity. A child that
    # carries its OWN nested groups is the genuine data-rich query; keep it.
    if not report.queries:
        return None
    heaviest = max(report.queries, key=lambda q: len(q.items or []))

    def _has_nested_groups(q):
        return any(getattr(g, "children", None)
                   for g in (getattr(q, "groups", None) or []))

    if ((getattr(heaviest, "parent_group", "") or "").strip()
            and not _has_nested_groups(heaviest)):
        masters = [q for q in report.queries
                   if not (getattr(q, "parent_group", "") or "").strip()]
        if masters:
            return max(masters, key=lambda q: len(q.items or []))
    return heaviest


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


# ---------------------------------------------------------------------------
# Token / sample-value resolution (preview fill)
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"&([A-Z][A-Z0-9_]*)", re.IGNORECASE)
_WS_COLLAPSE = re.compile(r"\s*\n\s*\n\s*", re.MULTILINE)

# Canned values used to fill in &TOKEN substitutions and bound fields when no
# real data source is available. These are FALLBACK placeholders only — when
# the user runs against real data via the Live Data tab, those values win.
_TOKEN_PREVIEW = {
    # Common Oracle &TOKEN / column references -> neutral, domain-agnostic
    # sample values. FALLBACK placeholders only; real values win on Live Data.
    # Verified against Oracle frontend screenshots: values should look like
    # what the user sees in Oracle Reports production output.
    "PERM_TYPE":       "DEPARTMENT OF SAMPLE SERVICES\nSAMPLE FACILITY LICENSE",
    "RENEWAL_YEAR":    "2026",
    "PERMIT":          "SMPL-0170",
    "SITE_NAME":       "Sample Site One",
    "SITE_ADDR":       "100 Main St, Springfield, ST 00000",
    "PERM_DATES":      "JANUARY 5, 2026 TO DECEMBER 31, 2026",
    "EXP_DATE":        "12/31/2026",
    "PERM_EFF_DATE":   "01/05/2026",
    "PERM_EXP_DATE":   "12/31/2026",
    "PERM_NUM":        "0170",
    "SITE_ID":         "10042",
    "COL_SORT":        "A-001",
    # Master-detail child columns
    "PERMITTEE_ADDR":  "Sample Org One\n100 Main St\nSample City, ST 00000",
    "PERMITTEE":       "Sample Org One",
    "SA_SITE_ID":      "10042",
    "ORG_ID":          "20031",
    # Placeholder / formula fallbacks (STRUCTURAL names only -- never a
    # client-specific token; suffix keywords below cover the rest)
    "CP_OPERATE_U":    "IS LICENSED TO OPERATE",
    "CP_OPERATE_L":    "is licensed to operate",
    "CP_SORT_DESCR":   "Permit",
    "CP_PERMIT_DTL":   "Renewal Year = '2026'",
    "CF_PERMITTEES":   "Sample Org One",
    "CF_FILE":         "SAMPLE_REPORT-2026.RDL",
    # Parameter-form-style references
    "P_RENEWAL_YEAR":  "2026",
    "P_ENVELOPE":      "SAMPLE_ENVELOPE.rep",
    "P_PERM_NAME":     "ALL",
    "P_REPORT_SERVER": "",
    "P_SITE_NAME":     "ALL",
    "P_STATUS_DT_BEGIN": "01/01/2026",
    "P_STATUS_DT_END":   "12/31/2026",
    "P_SUBTITLE":      "Renewal Year = '2026'\nStatus Date between 01/01/2026 and 12/31/2026",
    "P_SORT":          "Permit",
    "P_PERMITTEE":     "ALL",
    "P_PERM_NUM":      "0170",
    "P_DISTR_ABBR":    "DIST",
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
# CSS font stack for the current report's TITLE, set per-render from the parsed
# Oracle <font face> (honors the source font instead of hardcoding Courier).
_ACTIVE_TITLE_FONT = "Arial, Helvetica, sans-serif"


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
        key = m.group(1)
        u = key.upper()
        if u in _TOKEN_PREVIEW:
            return _TOKEN_PREVIEW[u]
        if "YEAR" in u:
            return "2026" if ("PREVIOUS" not in u and "PREV" not in u) else "2025"
        # CF_/CP_ formulas and any other unmatched token -> a neutral sample,
        # NEVER the raw &TOKEN (which reads as broken in the preview).
        return _sample_for_source(key, 0)
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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


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


def _has_columnar_repeating_frame(report):
    """True when a data-bound repeating frame lays its fields out as a
    horizontal table ROW: >= 2 field-kind children share a y-band AND sit
    at >= 2 distinct x positions (= table columns; the frame repeats DOWN to
    make rows). That is the structural signature of a columnar data table.
    Letters and certificates stack their per-record fields vertically (one
    field per y-band), so this never fires for them -- a clean tabular-vs-
    document discriminator. Purely geometric: no report names/fields/values.
    """
    Y_BAND = 0.12  # in; fields within ~1/8in vertically count as one row
    for g in _iter_layout(report):
        if (g.kind or "") != "repeating_frame":
            continue
        bands = {}
        for f in (g.fields or []):
            if (f.kind or "") != "field":
                continue
            yb = round((f.y or 0.0) / Y_BAND)
            bands.setdefault(yb, set()).add(round(f.x or 0.0, 2))
        for xs in bands.values():
            if len(xs) >= 2:
                return True
    return False


def detect_report_kind(report):
    """Return one of 'letter', 'tabular_details', 'certificate'.

    All checks are structural - never name-based or customer-specific.
    Order matters: positive-shape signals (certificate panel layout,
    paragraph-body letter) are checked first; weaker "colored = tabular"
    heuristics only fire after positive signals have had a chance. This
    prevents a letter that happens to use colored repeating frames for
    signature blocks, logos, or address lists from being misclassified
    as tabular.
    """
    # 0. STRONGEST tabular signal, checked FIRST: a data-bound repeating
    #    frame whose fields form a horizontal ROW (>=2 fields across >=2
    #    distinct x = table columns). A tabular report that ALSO carries
    #    explanatory prose or wrapping letter text otherwise gets misread
    #    as letter/certificate by the
    #    weaker heuristics below and loses its data grid. Letters and
    #    certificates stack fields vertically, so this never fires for them.
    if _has_columnar_repeating_frame(report):
        return "tabular_details"

    # 1. section_main with multiple frames carrying multi-line text:
    #    classic certificate shape (header + body + footer panels).
    #    Checked first because the certificate shape is unambiguous and
    #    its multiline texts can also satisfy the paragraph-block letter
    #    heuristic below.
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

    # 2. Three or more paragraph-shaped static text blocks (multi-line,
    #    >=80 chars each) is a strong letter signal - body paragraphs.
    #    A tabular report rarely carries that much prose; running this
    #    before the color-based tabular fallbacks lets letters win even
    #    when they have colored repeating sub-frames for signature /
    #    logo / address areas.
    if _count_paragraphy_text_blocks(report) >= 3:
        return "letter"

    # 3. Repeating frame with explicit background color AND data fields:
    #    typical row banding on a data table. The data-field gate keeps
    #    decorative colored frames (logos, signatures) out of this branch.
    for g in _iter_layout(report):
        if g.kind != "repeating_frame":
            continue
        has_data_field = any(f.kind == "field" for f in (g.fields or []))
        if not has_data_field:
            continue
        if _attr(g, "background_color"):
            return "tabular_details"
        for f in g.fields or []:
            if _attr(f, "background_color"):
                return "tabular_details"

    # 4. Two or more repeating frames bound to data columns: tabular.
    reps_with_data = [
        g for g in _iter_layout(report)
        if g.kind == "repeating_frame"
        and any(f.kind == "field" for f in (g.fields or []))
    ]
    if len(reps_with_data) >= 2:
        return "tabular_details"

    # 5. Any color signal at all - weak tabular hint.
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


def _find_cover_title(report):
    """The report/agency title that heads the COVER page. Searches
    section_header only (where the cover lives) and skips field labels
    (text ending ':'), the 'Report Parameters' heading, canned run-info
    rows, and Parameter-Form author-notes -- so it returns the real title
    text (report name / letterhead) rather than a heading or an unresolved
    runtime token from the body. Generic, structural; LayoutField or None."""
    best = None
    best_key = (-1, -1)
    for g in report.layout or []:
        if g.kind != "section_header":
            continue
        stack = [g]
        while stack:
            gr = stack.pop()
            for f in (gr.fields or []):
                if f.kind != "text":
                    continue
                t = (f.text or "").strip()
                if (not t or t.endswith(":") or _PARAMS_HEADING_RE.match(t)
                        or _is_canned_run_label(t) or _is_param_form_note(t)):
                    continue
                sz = int(f.font_size or 0)
                centered = 1 if (f.align or "").lower() == "center" else 0
                bold = 1 if f.bold else 0
                key = (sz, centered + bold)
                if key > best_key:
                    best_key = key
                    best = f
            stack.extend(gr.children or [])
    if best is not None:
        return best
    # Fallback: a whole-layout title, but reject the 'Report Parameters'
    # heading and a bare unresolved token (e.g. a body field like "&PERMIT")
    # so the cover never shows a heading or a raw &TOKEN as its title.
    fb = _find_title_text(report)
    if fb is not None:
        t = (fb.text or "").strip()
        if _PARAMS_HEADING_RE.match(t) or _is_param_form_note(t):
            return None
        resolved = _resolve_tokens(t).strip()
        if resolved.startswith("&") and " " not in resolved:
            return None
        return fb
    return None


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
        # SECURITY: only accept a well-formed hex literal. Callers concatenate
        # this value directly into inline style="..." attributes, so returning
        # an arbitrary "#"-prefixed string verbatim would let report-controlled
        # color text (e.g. `#000"></div><img src=x onerror=...>`) break out of
        # the style and inject markup (stored XSS). Malformed -> fallback.
        if re.fullmatch(r"#[0-9A-Fa-f]{3}(?:[0-9A-Fa-f]{3})?", color):
            return color
        return fallback
    try:
        from converter.parsers.oracle_colors import resolve_color
        resolved = resolve_color(color)
        if resolved:
            return resolved
    except Exception:
        pass
    return fallback


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

    Otherwise (frontend mode) checks _TOKEN_PREVIEW first (which has
    domain-appropriate sample values for known Oracle columns), then falls
    back to the structural keyword pools.
    """
    if _ACTIVE_MODE == "backend":
        return _placeholder_for_source(src)
    # Check the curated TOKEN_PREVIEW map first — it has better sample
    # values for known Oracle report columns (e.g. PERM_TYPE → department
    # title, not "Type Alpha").
    u = (src or "").upper().strip()
    if u in _TOKEN_PREVIEW and _TOKEN_PREVIEW[u]:
        return _TOKEN_PREVIEW[u]
    key = (src or "").lower().replace("_", " ").strip()

    # Structural keyword pools. Each pool has 2 fictional alternatives so two
    # sample rows look different. NEVER use customer/jurisdiction-specific
    # tokens (no jurisdiction- or subject-specific words).
    NAME_POOL    = ["Sample Org One", "Sample Org Two"]
    PERSON_POOL  = ["Alex Rivera", "Jordan Casey"]
    ADDR_POOL    = ["100 Main St, Springfield, ST 00000",
                    "200 Commerce Way, Riverside, ST 00000"]
    CITY_POOL    = ["Springfield", "Riverside"]
    DATE_POOL    = ["03/12/2026", "04/02/2026"]
    NUM_POOL     = ["1001", "1002"]
    SHORT_ID     = ["ID-0001", "ID-0002"]
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
        # Generic structural keywords (envelope/chief cover the common
        # Oracle CF_/CP_ formula naming without any client tokens).
        (("envelope",),                    ["Sample Envelope Report",
                                            "Sample Envelope Report"]),
        (("chief", "director", "officer"), ["Sample Chief, Bureau Chief",
                                            "Sample Director, Division"]),
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


def _is_design_fill(raw):
    """Oracle Reports frames default to pale pink/lavender design-time fills
    (#FFE0FF, #FFBFFF, ...) that are NOT meant to print. Detect them by their
    pink/lavender tint (high red + blue, lower green); legitimate light bands
    (gray / blue / yellow) are kept."""
    try:
        h = (raw or "").strip().lstrip("#")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        if len(h) != 6:
            return False
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return r >= 224 and b >= 224 and g < r - 16
    except Exception:
        return False


def _band_bg(raw, default):
    """Normalize an Oracle frame fill, dropping pale design-time pink/lavender
    fills (#FFE0FF, #FFBFFF, ...) back to the supplied default so they don't
    leak into the preview as a printed band. Normalizes FIRST so it works
    whether the source value is hex or an Oracle color name."""
    c = _normalize_color(raw, default)
    return default if _is_design_fill(c) else c


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


def _is_param_form_note(text):
    """Detect rows that are purely internal Oracle builder notes — NOT user-
    facing content. The starred labels (*Sort Order, *Generate Envelopes)
    and '[Permittee] is a hyperlink to ...' ARE shown in the real Oracle
    report's cover page (verified against Oracle frontend screenshots), so
    they are NOT notes and must NOT be suppressed. Only truly internal
    builder instructions (if any) would return True here."""
    # Previously suppressed *Sort Order, *Generate Envelopes, and
    # "is a hyperlink" — but the Oracle production PDF and frontend
    # screenshots show all of these on the cover page. Keep them.
    return False


def _is_conditional_error_text(text):
    """Oracle Reports format-trigger ERROR/empty-state branches (e.g.
    'ERROR:  No CURRENT Permittee as of ...') print ONLY when the data hits that
    condition; on the happy path Oracle hides them. A static preview can't
    evaluate the trigger, so suppress these clear error-branch messages rather
    than show an error the real report wouldn't. Generic -- keyed on the
    'ERROR:' label convention, no per-report text."""
    return bool(re.match(r"(?i)^\s*error\b\s*:", text or ""))


def _font_css(face, fallback="Arial, Helvetica, sans-serif"):
    """Map a parsed Oracle <font face> name to a CSS font-family stack, honoring
    the source font; sane fallback when none is given. Generic, no per-report
    logic -- classifies by family so the browser substitution stays sensible."""
    f = (face or "").strip()
    if not f:
        return fallback
    low = f.lower()
    if "courier" in low or "consol" in low or "monospace" in low:
        return "'%s', 'Courier New', monospace" % f
    if any(s in low for s in ("times", "georgia", "garamond", "serif",
                              "roman", "palatino", "book antiqua", "cambria")):
        return "'%s', Georgia, 'Times New Roman', serif" % f
    # Arial / Verdana / MS Sans Serif / Helvetica / Tahoma / Segoe / Calibri ...
    return "'%s', Arial, Helvetica, sans-serif" % f


def _title_font_css(report):
    """CSS font stack for the report TITLE, honoring the source Oracle font.
    Prefers the cover-title element's own <font face>; else the most prominent
    (largest, bold-weighted) header text font; else the sans fallback. Generic."""
    try:
        tf = _find_cover_title(report)
    except Exception:
        tf = None
    if tf is not None and (getattr(tf, "font_family", "") or "").strip():
        return _font_css(tf.font_family)
    best = None  # (weight, face)

    def walk(gs):
        for g in (gs or []):
            for f in (getattr(g, "fields", None) or []):
                if getattr(f, "kind", "") == "text":
                    fam = (getattr(f, "font_family", "") or "").strip()
                    if fam:
                        w = (int(getattr(f, "font_size", 0) or 0)
                             + (4 if getattr(f, "bold", False) else 0))
                        yield (w, fam)
            yield from walk(getattr(g, "children", None) or [])

    for w, fam in walk(getattr(report, "layout", None)):
        if best is None or w > best[0]:
            best = (w, fam)
    return _font_css(best[1]) if best else _font_css("")


def _header_label_value_pairs(items):
    """From a flat item list, return (label_text, value_item) pairs where a
    `text` ending in ':' is horizontally followed by a `field` (or another
    text with a source) on the same row (y diff < 0.2 in)."""
    texts  = [it for it in items if it[0] == "text"
              and (it[3] or "").strip().endswith(":")
              and not _is_param_form_note((it[3] or "").strip())]
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
            # Prefer field over text at similar dx (sub-pixel x differences
            # in Oracle XML can make a continuation-note text at x=2.250
            # beat a field at x=2.254 — wrong pairing).
            if best is None:
                best = cand
                best_dx = dx
            elif dx < best_dx - 0.05:
                best = cand
                best_dx = dx
            elif abs(dx - best_dx) <= 0.05 and ckind == "field" and best[0] != "field":
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

    # No title block here: the criteria cover's section_header has no reliable
    # letterhead (a real report's agency title lives in section_main, which the
    # cover doesn't reach), and the largest section_header text is often a
    # mid-document heading (e.g. "VENDOR INVOICE") that would mislead. The
    # legacy param-cover keeps its title; this rich criteria cover stays clean.

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
        # Skip rows whose VALUE is a Parameter-Form author note (e.g.
        # "[X] is a hyperlink to ..."). Oracle hides these from the printed
        # report; the label alone ("Hyperlink in Letter:") is meaningless
        # without the note, so drop the whole row.
        if vkind != "field" and _is_param_form_note((vtext or "").strip()):
            continue
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
        if _is_param_form_note(t):
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

    # Title block — the cover's own heading (section_header), not a body token.
    title_field = _find_cover_title(report)
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
            '<div style="font-family:' + _ACTIVE_TITLE_FONT + '; '
            'font-size:14px; font-weight:bold; color:' + title_color + '; '
            'text-align:center; letter-spacing:0.4px; line-height:1.5;">'
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
        # Show the declared default (initial value) if the report sets one;
        # otherwise leave it blank. Real Oracle cover pages print parameters
        # blank unless a value was supplied at run time, so a fabricated
        # sample here would be less faithful than an empty slot. Generic --
        # driven by the parsed default, no per-report highlighting.
        show_val = str(p.initial_value) if getattr(p, "initial_value", None) else ""
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


def _has_cover_page(report):
    """True when the report's section_header carries a Parameter-Form-style
    cover -- a 'Report Parameters' heading, or >=2 non-canned `label: value`
    criteria rows -- worth printing as a standalone page 1. Mirrors the RDL's
    decision to emit _build_letter_cover_page / _build_cover_page so the
    preview's first page matches the real report's. Generic, structural."""
    items = _collect_header_layout_items(report)
    if _header_has_parameters_heading(items):
        return True
    pairs = _header_label_value_pairs(items)
    non_canned = [p for p in pairs if not _is_canned_run_label(p[0])]
    return len(non_canned) >= 2


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
            '<div style="font-family:' + _ACTIVE_TITLE_FONT + '; '
            'font-size:12px; font-weight:bold; color:' + title_color + '; '
            'text-align:center; letter-spacing:0.4px; line-height:1.4;">'
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
    band_bg = _band_bg(_attr(top_rep, "background_color", ""), "#000079")
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

    # Find inner frames: detail (e.g. R_G_OUTER) and grandchild (e.g. R_G_INNER)
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


def _detect_multi_section_preview(report):
    """Mirror of generators.rdl._detect_multi_section for the preview, so the
    HTML mockup and the generated RDL agree on whether a report is a multi-
    section dashboard. Returns [{"header","tables":[(group_src,[cols])]}] or None.
    Purely structural; no report-specific names."""
    sm = _find_section(report.layout or [], "section_main")
    if sm is None:
        return None
    frames = [c for c in (sm.children or [])
              if "frame" in (c.kind or "").lower()
              and (c.kind or "").lower() != "repeating_frame"]
    if len(frames) < 2:
        return None

    def header_text(frame):
        # Top-left static text = the section title (see rdl._header_text).
        cands = []
        def walk(g, in_rep):
            ir = in_rep or (g.kind or "").lower() == "repeating_frame"
            for f in (g.fields or []):
                if f.kind == "text" and not ir:
                    t = (f.text or "").strip()
                    if t and "&<" not in t and not t.lower().endswith(".rdf"):
                        cands.append((float(getattr(f, "y", 0.0) or 0.0),
                                      float(getattr(f, "x", 0.0) or 0.0),
                                      t.split("\n")[0].strip()))
            for c in (g.children or []):
                walk(c, ir)
        walk(frame, False)
        if not cands:
            return ""
        cands.sort(key=lambda c: (c[0], c[1]))
        return cands[0][2]

    def tables_in(frame):
        out, seen = [], set()
        def walk(g):
            if (g.kind or "").lower() == "repeating_frame":
                cols = []
                for f in (g.fields or []):
                    if (f.kind or "") == "field":
                        s = (f.source or "").strip()
                        if s and s not in cols:
                            cols.append(s)
                if g.source_query and cols:
                    key = (g.source_query.upper(), tuple(c.upper() for c in cols))
                    if key not in seen:
                        seen.add(key)
                        out.append((g.source_query, cols))
            for c in (g.children or []):
                walk(c)
        walk(frame)
        return out

    sections, distinct = [], set()
    for fr in sorted(frames, key=lambda f: (f.y or 0.0)):
        t = tables_in(fr)
        if not t:
            continue
        for src, _ in t:
            distinct.add(src.upper())
        sections.append({"header": header_text(fr), "tables": t})
    if len(sections) < 2 or len(distinct) < 2:
        return None
    return sections


def _render_multi_section_page(report, sections, page_label):
    """Render the multi-section dashboard as ONE page: each section is a header
    band + a small data table (2 sample rows) drawn from its own columns."""
    title_field = _find_title_text(report)
    title_color = "#000080"
    title_lines = [report.name or "Report"]
    if title_field is not None:
        title_raw = _resolve_tokens(title_field.text or "")
        title_color = _normalize_color(_attr(title_field, "color", ""), "#000080")
        title_lines = [re.sub(r"&<[^>]+>", "", ln).strip()
                       for ln in title_raw.splitlines() if ln.strip()][:3] or title_lines
    head = ""
    for ln in title_lines:
        head += (
            '<div style="font-family:' + _ACTIVE_TITLE_FONT + '; '
            'font-size:13px; font-weight:bold; color:' + title_color + '; '
            'text-align:center; letter-spacing:0.4px; line-height:1.4;">'
            + _esc(ln) + '</div>'
        )
    head += (
        '<div style="display:flex; justify-content:space-between; '
        'align-items:baseline; margin:10px 0 12px; font-size:12px; color:'
        + _TAB_INK + ';"><div>Report run on:&nbsp;<span style="font-weight:normal;">'
        + _esc(_sample_for_source("date", 0)) + ' 1:00 PM</span></div>'
        '<div style="font-style:italic; color:#000079;">' + _esc(page_label)
        + '</div></div>'
    )

    blocks = []
    for sec in sections:
        hdr = sec.get("header", "")
        if hdr:
            blocks.append(
                '<div style="background:' + _TAB_BAND_BG + '; color:' + _TAB_BAND_FG
                + '; font-weight:bold; font-size:12px; padding:5px 10px; '
                'margin-top:14px;">' + _esc(hdr) + '</div>'
            )
        # First table of the section drives the columns shown.
        for (src, cols) in sec["tables"][:1]:
            shown = cols[:6] or ["Value"]
            th = "".join(
                '<th style="text-align:left; font-size:11px; padding:3px 8px; '
                'border-bottom:1px solid ' + _TAB_RULE_LIGHT + '; color:'
                + _TAB_INK_SOFT + ';">' + _esc(c.replace("_", " ")) + '</th>'
                for c in shown
            )
            rows = ""
            for ri in range(2):
                tds = "".join(
                    '<td style="font-size:11px; padding:3px 8px; border-bottom:1px '
                    'solid ' + _TAB_RULE_LIGHT + '; color:' + _TAB_INK + ';">'
                    + _esc(_sample_for_source(c, ri)) + '</td>'
                    for c in shown
                )
                bg = _TAB_PAPER if ri % 2 else _TAB_DETAIL_BG
                rows += '<tr style="background:' + bg + ';">' + tds + '</tr>'
            blocks.append(
                '<table style="width:100%; border-collapse:collapse; margin:2px 0 '
                '4px;"><thead><tr>' + th + '</tr></thead><tbody>' + rows
                + '</tbody></table>'
            )

    return _render_page(head + "".join(blocks), label=page_label)


def _nd_geometry(report):
    """Mirror of generators.rdl._layout_geometry_index for the preview: map
    field SOURCE_UPPER -> (x, y, w) and collect label texts (text, x, y, bg)
    from section_main. Returns ({}, []) when no section_main."""
    field_geo = {}
    label_geo = []
    main = _find_section(report.layout or [], "section_main")
    if main is None:
        return field_geo, label_geo

    def walk(g, frame_bg):
        bg = getattr(g, "background_color", "") or frame_bg
        for f in (g.fields or []):
            if f.kind == "field" and f.source:
                field_geo.setdefault(f.source.upper(), (
                    float(getattr(f, "x", 0.0) or 0.0),
                    float(getattr(f, "y", 0.0) or 0.0),
                    float(getattr(f, "width", 0.0) or 0.0)))
            elif f.kind == "text" and (f.text or "").strip():
                label_geo.append(((f.text or "").strip(),
                                  float(getattr(f, "x", 0.0) or 0.0),
                                  float(getattr(f, "y", 0.0) or 0.0), bg))
        for c in (g.children or []):
            walk(c, bg)

    walk(main, "")
    return field_geo, label_geo


def _nd_detail_band(report):
    """The repeating-frame field row with the most distinct-x positions = the
    detail TABLE row; plus wrap fields just below. Returns (row, wrap, row_y)
    where row/wrap are [(source, x, y, w)]."""
    main = _find_section(report.layout or [], "section_main")
    if main is None:
        return [], [], None
    fields = []

    def walk(g):
        for f in (g.fields or []):
            if f.kind == "field" and f.source:
                fields.append((f.source, float(getattr(f, "x", 0.0) or 0.0),
                               float(getattr(f, "y", 0.0) or 0.0),
                               float(getattr(f, "width", 0.0) or 0.0)))
        for c in (g.children or []):
            walk(c)

    walk(main)
    if not fields:
        return [], [], None
    from collections import defaultdict
    by_y = defaultdict(list)
    for s, x, y, w in fields:
        by_y[round(y, 2)].append((s, x, y, w))
    best_y, best_n = None, 0
    for y, lst in by_y.items():
        nx = len({round(x, 1) for _s, x, _y, _w in lst})
        if nx > best_n:
            best_n, best_y = nx, y
    if best_y is None or best_n < 2:
        return [], [], None
    row = sorted(by_y[best_y], key=lambda z: z[1])
    wrap = []
    for y, lst in by_y.items():
        if 0 < (y - best_y) <= 0.4 and len({round(x, 1) for _s, x, _y, _w in lst}) == 1:
            wrap.extend(lst)
    wrap.sort(key=lambda z: z[2])
    return row, wrap, best_y


def _nd_nearest_label(label_geo, x, y, max_dy=0.18, max_dx=1.4):
    best, best_dx = None, 1e9
    for text, lx, ly, _bg in label_geo:
        if abs(ly - y) <= max_dy and lx <= x + 0.05:
            dx = x - lx
            if 0 <= dx <= max_dx and dx < best_dx:
                best_dx, best = dx, text
    return (best or "").strip().rstrip(":")


def _is_nested_master_detail_preview(report):
    """True when the main query has a nested <group> chain (>=2 levels) AND a
    detail TABLE band -- the master-detail table shape. Mirrors the RDL
    routing so the preview matches what uploads."""
    main = None
    for q in (report.queries or []):
        if getattr(q, "groups", None):
            if main is None or len(q.items or []) > len(main.items or []):
                main = q
    if main is None:
        return False
    chain = []

    def walk(gs):
        for g in gs:
            chain.append(g)
            walk(g.children or [])

    walk(main.groups or [])
    if len(chain) < 2:
        return False
    row, _wrap, _y = _nd_detail_band(report)
    return len(row) >= 2 and len({round(x, 1) for _s, x, _y2, _w in row}) >= 2


def _render_nested_master_detail_page(report, sample_idx, page_num, total_pages):
    """Render ONE per-master page 1:1 with the Oracle nested layout: the colored
    master band (real frame bg + caption:value lines), the column-header strip,
    and 2 sample detail rows aligned to the header columns. Geometry-driven so
    it matches the generated RDL."""
    field_geo, label_geo = _nd_geometry(report)
    row, wrap, row_y = _nd_detail_band(report)

    # Group chain (same structure the RDL builder uses): outer band, middle
    # cards, inner detail. Restrict each band/card to ITS GROUP'S items so the
    # report title, run-date, and page-number fields never leak into a band.
    _main = None
    for q in (report.queries or []):
        if getattr(q, "groups", None):
            if _main is None or len(q.items or []) > len(_main.items or []):
                _main = q
    _chain = []
    def _w(gs):
        for g in gs:
            _chain.append(g); _w(g.children or [])
    _w(_main.groups if _main else [])
    _outer = _chain[0] if _chain else None
    _middles = _chain[1:-1] if len(_chain) >= 3 else []

    # Source -> Oracle defaultLabel (deterministic captions; far more reliable
    # than geometric label matching). e.g. ACCT_ID -> "Account ID:".
    _src_label = {}
    for q in (report.queries or []):
        for it in (q.items or []):
            if it.name and it.label:
                _src_label.setdefault(it.name.upper(), it.label.strip())

    def _cap_for(src, x=None, y=None):
        # Prefer the layout's printed caption at this field's position (e.g.
        # the report prints a caption next to the field), else the Oracle
        # DataItem defaultLabel (e.g. a code column -> a readable label),
        # else a title-cased name.
        if x is not None and y is not None:
            geo_cap = _nd_nearest_label(label_geo, x, y)
            if geo_cap:
                return geo_cap
        lab = _src_label.get((src or "").upper(), "")
        return lab.rstrip(":") if lab else (src or "").replace("_", " ").title()

    def _group_field_rows(group):
        """y-grouped (source,x,y,w) for a group's items that have geometry."""
        out = {}
        for it in (group.items or []):
            g = field_geo.get((it.name or "").upper())
            if g:
                out.setdefault(round(g[1], 2), []).append((it.name, g[0], g[1], g[2]))
        return out

    # Title: the largest BOLD centered static text in section_main / margin
    # (NOT _find_title_text, which can return the parameter-form heading
    # "Report Parameters"). Pick the bold text with the longest content in the
    # top band of the layout.
    title_lines = [report.name or "Report"]
    title_color = "#000080"
    _title_cands = []
    def _ttx(g):
        for f in (g.fields or []):
            t = (f.text or "").strip()
            if (f.kind == "text" and getattr(f, "bold", False) and t
                    and "&<" not in t and len(re.sub(r"\s+", "", t)) >= 20):
                _title_cands.append(f)
        for c in (g.children or []):
            _ttx(c)
    for g in (report.layout or []):
        if (g.kind or "").lower() in ("section_main", "section_header") or "section" in (g.kind or "").lower():
            _ttx(g)
    if _title_cands:
        tf = max(_title_cands, key=lambda f: len((f.text or "").strip()))
        title_color = _normalize_color(_attr(tf, "color", ""), "#000080")
        title_lines = [re.sub(r"&<[^>]+>", "", ln).strip()
                       for ln in (tf.text or "").splitlines() if ln.strip()][:3] or title_lines
    head = ""
    for ln in title_lines:
        head += ('<div style="font-family:' + _ACTIVE_TITLE_FONT + ';font-size:13px;'
                 'font-weight:bold;color:' + title_color + ';text-align:center;'
                 'letter-spacing:0.4px;line-height:1.4;">' + _esc(ln) + '</div>')
    head += ('<div style="display:flex;justify-content:space-between;'
             'align-items:baseline;margin:8px 0 10px;font-size:12px;color:#111;">'
             '<div>Report run on:&nbsp;<span style="font-weight:normal;">'
             + _esc(_sample_for_source("date", 0)) + ' 1:00 PM</span></div>'
             '<div style="font-style:italic;color:#000079;">Page ' + str(page_num)
             + ' of ' + str(total_pages) + '</div></div>')

    def _render_caption_block(group, bg, fg, pad="10px 14px", fs="12px"):
        rows_by_y = _group_field_rows(group)
        inner = ""
        for yk in sorted(rows_by_y):
            line = ""
            for s, x, y, w in sorted(rows_by_y[yk], key=lambda z: z[1]):
                cap = _cap_for(s, x, y)
                val = _sample_for_source(s, sample_idx)
                line += ('<span style="margin-right:26px;white-space:nowrap;">'
                         '<b>' + _esc(cap + ":  ") + '</b>' + _esc(val) + '</span>')
            if line:
                inner += '<div style="margin:3px 0;">' + line + '</div>'
        return ('<div style="background:' + bg + ';color:' + fg + ';font-weight:bold;'
                'font-size:' + fs + ';padding:' + pad + ';">' + inner + '</div>')

    # ---- master band (OUTER group only) ----
    band_bg = "#006400"
    if _outer is not None:
        oy = min((field_geo[it.name.upper()][1] for it in _outer.items
                  if field_geo.get((it.name or "").upper())), default=0.0)
        for _t, _lx, _ly, bg in label_geo:
            if (bg and abs(_ly - oy) <= 0.4
                    and not _is_design_fill(_normalize_color(bg, ""))):
                band_bg = bg
                break
    band = _render_caption_block(_outer, band_bg, "#fff") if _outer else ""
    # ---- middle-group cards (white) ----
    for _mg in _middles:
        band += _render_caption_block(_mg, "#f3f3f3", "#111", pad="6px 14px", fs="11px")

    # ---- column header strip: each detail column's OWN label at its x ----
    total_w = 7.5
    def pct(x):
        return max(0.0, min(100.0, (x / total_w) * 100.0))
    hdr_html = ""
    for hi, (s, hx, hy, hw) in enumerate(row):
        nxt = row[hi + 1][1] if hi + 1 < len(row) else total_w
        hdr_html += ('<div style="position:absolute;left:' + f"{pct(hx):.1f}" + '%;'
                     'width:' + f"{pct(nxt)-pct(hx):.1f}" + '%;color:#fff;font-weight:bold;'
                     'font-size:11px;padding:3px 4px;">' + _esc(_cap_for(s, hx, hy)) + '</div>')
    hdr = ('<div style="position:relative;height:22px;background:#00008B;">'
           + hdr_html + '</div>')

    det_rows = ""
    for ri in range(2):
        cells = ""
        for ci, (s, x, y, w) in enumerate(row):
            nxt = row[ci + 1][1] if ci + 1 < len(row) else total_w
            v = _sample_for_source(s, ri)
            cells += ('<div style="position:absolute;left:' + f"{pct(x):.1f}" + '%;'
                      'width:' + f"{pct(nxt)-pct(x):.1f}" + '%;font-size:11px;'
                      'padding:3px 4px;color:#111;">' + _esc(v) + '</div>')
        wrap_html = ""
        for wi, (s, x, y, w) in enumerate(wrap):
            wv = _sample_for_source(s, ri)
            wrap_html += ('<div style="font-size:11px;padding:1px 4px 1px '
                          + f"{pct(x):.0f}" + '%;color:#333;">'
                          + '<i>' + _esc(_nd_nearest_label(label_geo, x, y) or "") + '</i> '
                          + _esc(wv) + '</div>')
        h = 22 + len(wrap) * 16
        det_rows += ('<div style="position:relative;height:22px;border-bottom:'
                     '1px solid #ccc;">' + cells + '</div>' + wrap_html)

    body = head + band + hdr + det_rows
    return _render_page(body, label="Page " + str(page_num))


def _render_nested_master_detail_pages(report):
    NUM = 2
    # Page 1 mirrors the RDL's _build_cover_page: the Run-info / Report
    # Parameters cover that every Oracle detail report prints ahead of the
    # repeating detail. The detail samples then follow as pages 2..N.
    total = NUM + 1
    pages = [_render_header_summary_page(report, page_label="Page 1 of %d" % total)]
    pages += [_render_nested_master_detail_page(report, i, i + 2, total)
              for i in range(NUM)]
    return _render_pages_wrapper(pages)


def _render_tabular_pages(report):
    """Multi-page tabular: page 1 = header summary, pages 2-4 = detail pages.

    Multi-section dashboards (several independent tables down one page) render
    as a single dashboard page so the preview matches the multi-section RDL."""
    if _is_nested_master_detail_preview(report):
        return _render_nested_master_detail_pages(report)
    _sections = _detect_multi_section_preview(report)
    if _sections:
        return _render_pages_wrapper([
            _render_multi_section_page(report, _sections, "Page 1 of 1")
        ])
    NUM_DETAIL_PAGES = 3
    total_pages = 1 + NUM_DETAIL_PAGES
    pages = [_render_header_summary_page(report, page_label="Page 1 of " + str(total_pages))]
    for i in range(NUM_DETAIL_PAGES):
        pages.append(_render_tabular_detail_page(
            report, sample_idx=i, page_num=2 + i, total_pages=total_pages,
        ))
    return _render_pages_wrapper(pages)


def _doc_resolve_tokens(text, report):
    """Resolve Oracle &TOKEN substitutions in document text GENERICALLY (no
    per-report dictionary). A token that names a report field -> a sample value;
    a year-ish token -> a sample year; a CF_/CP_ formula token -> a clean
    bracketed marker; anything else -> a neutral sample. Never leaves a raw
    '&FOO' in the rendered preview."""
    if not text:
        return ""
    field_names = set()
    for q in (report.queries or []):
        for it in (q.items or []):
            if it.name:
                field_names.add(it.name.upper())
    param_names = {(p.name or "").upper() for p in (report.parameters or [])}

    def sub(m):
        key = m.group(1)
        u = key.upper()
        if u in _TOKEN_PREVIEW:
            return _TOKEN_PREVIEW[u]
        if "YEAR" in u:
            return "2026" if "PREVIOUS" not in u and "PREV" not in u else "2025"
        if u.startswith(("CF_", "CP_")):
            # PL/SQL-computed formula -- the preview can't run it, so show a
            # neutral SAMPLE value (consistent with every other field in this
            # sample-data preview) instead of a broken-looking [CF_X] token.
            return _sample_for_source(key, 0)
        if u in field_names or u in param_names or u.startswith(("F_", "P_", "PARM_")):
            return _sample_for_source(key, 0)
        # bare word token -> neutral sample
        return _sample_for_source(key, 0)

    return _TOKEN_RE.sub(sub, text)


def _decollide(elems):
    """Push overlapping positioned text/field elements down so they stack
    instead of piling on top of each other. Oracle Reports lets conditionally-
    shown fields share ONE design slot (only one prints at runtime, via format
    triggers) and elastic frames grow/reflow; a static preview would paint them
    all on top of each other. Only elements sharing nearly the same LEFT edge
    (vertically stacked, not side-by-side table columns) are reflowed, so table
    layouts are left untouched. Panels and images keep their positions."""
    movable = [e for e in elems if e.get("kind") in ("text", "field")]
    movable.sort(key=lambda e: (round(float(e.get("y", 0) or 0), 3),
                                round(float(e.get("x", 0) or 0), 3)))
    placed = []  # (x_left, y_top, y_bottom)
    for e in movable:
        xl = float(e.get("x", 0) or 0)
        yt = float(e.get("y", 0) or 0)
        # Estimate RENDERED height: multi-line text (Oracle elastic frames /
        # concatenated formula paragraphs) is taller than its one-line design
        # slot, so trusting the small declared height would let the next field
        # overlap its wrapped content.
        nlines = ((e.get("text") or "").count("\n") + 1) if e.get("kind") == "text" else 1
        line_in = max(0.16, int(e.get("size") or 9) / 72.0 * 1.35)
        h = max(float(e.get("h") or 0) or 0.0, nlines * line_in)
        yb = yt + h
        guard, moved = 0, True
        while moved and guard < 300:
            moved = False
            guard += 1
            for (pxl, pyt, pyb) in placed:
                if abs(xl - pxl) < 0.20 and yt < pyb - 0.01 and yb > pyt + 0.01:
                    shift = pyb + 0.03 - yt
                    yt += shift
                    yb += shift
                    moved = True
        e["y"] = yt
        placed.append((xl, yt, yb))
    return elems


def _doc_collect_positioned(report, section="section_main"):
    """Walk the given section and return every positioned element as a flat
    list of dicts: {kind, text|source, x, y, w, h, bold, size, color, align,
    bg}. Geometry is absolute within the section. Generic: nothing
    report-specific. ``section`` is "section_main" for a per-record document,
    or "section_header" for a header-resident summary report's leading page.
    """
    main = _find_section(report.layout or [], section)
    if main is None:
        return [], 8.5, 11.0
    out = []

    def walk(g, frame_bg):
        gbg_raw = getattr(g, "background_color", "")
        # Oracle Reports frames carry pale design-time fill hints (light pinks/
        # lavenders like #FFE0FF, #FFBFFF) that are NOT meant to print -- the
        # real document is on white paper. Only paint a panel when the fill is a
        # genuine MEANINGFUL band (dark, e.g. the #3D3D3D invoice shading).
        gbg = gbg_raw if (gbg_raw and _is_dark(gbg_raw)) else ""
        bg = gbg or frame_bg
        if gbg and (g.width or 0) > 0.2 and (g.height or 0) > 0.05:
            out.append({"kind": "panel", "x": float(g.x or 0), "y": float(g.y or 0),
                        "w": float(g.width or 0), "h": float(g.height or 0),
                        "bg": gbg})
        for f in (g.fields or []):
            x = float(getattr(f, "x", 0.0) or 0.0)
            y = float(getattr(f, "y", 0.0) or 0.0)
            w = float(getattr(f, "width", 0.0) or 0.0)
            h = float(getattr(f, "height", 0.0) or 0.0)
            col = _normalize_color(getattr(f, "color", "") or "", "#000000")
            common = {"x": x, "y": y, "w": w, "h": h,
                      "bold": bool(getattr(f, "bold", False)),
                      "size": int(getattr(f, "font_size", 0) or 9),
                      "color": col,
                      "align": (getattr(f, "align", "") or "left").lower(),
                      "bg": bg}
            if f.kind == "text":
                if _is_conditional_error_text(f.text or ""):
                    continue  # Oracle format-trigger error branch -- hidden at runtime
                t = _clean_text(_doc_resolve_tokens(f.text or "", report))
                if t:
                    out.append({"kind": "text", "text": t, **common})
            elif f.kind == "field":
                out.append({"kind": "field", "source": f.source or "", **common})
            elif f.kind == "image":
                out.append({"kind": "image", "source": f.source or f.image_id or "",
                            **common})
        for c in (g.children or []):
            walk(c, bg)

    walk(main, "")
    out = _decollide(out)
    # page size from section body, fallback to letter
    pw, ph = 8.5, 11.0
    try:
        bsec = main
        pw = float(getattr(bsec, "width", 0) or 0) or 8.5
    except Exception:
        pass
    # derive max extents
    maxx = max([e["x"] + (e["w"] or 1.0) for e in out], default=8.0)
    maxy = max([e["y"] + (e["h"] or 0.2) for e in out], default=10.0)
    return out, max(8.0, maxx + 0.3), max(10.0, maxy + 0.4)


def _doc_field_caption_and_value(src, report, label_map, idx):
    """For a data field, return 'Caption: value' sample text. Uses the Oracle
    defaultLabel when present, else the field name; value from _sample_for_source.
    CF_/CP_ formula fields show a bracketed formula marker (they're computed)."""
    u = (src or "").upper()
    if u in ("CURRENTDATE", "CURRENT_DATE"):
        return _sample_for_source("date", idx)
    if u.startswith(("CF_", "CP_")):
        # PL/SQL-computed formula -> show a sample value (not a raw [CF_X]
        # token) so the sample-data preview reads as a finished document.
        return _sample_for_source(src, idx)
    if u.startswith(("P_", "PARM_")):
        return _sample_for_source(src, idx)
    return _sample_for_source(src, idx)


def _render_generic_document_page(report, idx, page_num, total_pages,
                                  section="section_main"):
    """Paint a section's actual frames/texts/fields at their real positions.
    This is the GENERAL geometry-driven renderer -- it shows whatever the
    report contains (letterhead, address block, body, signature, invoice, or a
    header-resident summary/criteria table), never hardcoded sample content."""
    elems, pw, ph = _doc_collect_positioned(report, section)
    PAD = 0.0
    SCALE = 96.0  # px per inch on screen

    # field source -> defaultLabel
    label_map = {}
    for q in (report.queries or []):
        for it in (q.items or []):
            if it.name:
                label_map[it.name.upper()] = (it.label or "").strip()
    # embedded images by id
    emb = {im.id.upper(): im for im in (report.embedded_images or [])}

    def px(v):
        return f"{v * SCALE:.0f}px"

    # title band (top) -- the largest bold centered text already lives in elems
    parts = []
    # panels first (z-order: background)
    for e in elems:
        if e["kind"] != "panel":
            continue
        parts.append(
            '<div style="position:absolute;left:' + px(e["x"]) + ';top:' + px(e["y"])
            + ';width:' + px(e["w"]) + ';height:' + px(e["h"]) + ';background:'
            + _esc(e["bg"]) + ';"></div>')
    # then text/fields/images
    for e in elems:
        if e["kind"] == "panel":
            continue
        left = px(e["x"]); top = px(e["y"])
        w = e["w"] if e["w"] > 0 else (pw - e["x"] - 0.1)
        width = px(max(0.4, w))
        align = {"start": "left", "end": "right", "centre": "center"}.get(e["align"], e["align"])
        if align not in ("left", "right", "center"):
            align = "left"
        # white text on dark panels
        fg = e["color"]
        bg = e.get("bg", "")
        if bg and _is_dark(bg) and fg.lower() in ("#000000", "#111111", "#000"):
            fg = "#ffffff"
        style = ("position:absolute;left:" + left + ";top:" + top + ";width:" + width
                 + ";font-size:" + str(max(7, min(16, e["size"]))) + "px;"
                 + ("font-weight:bold;" if e["bold"] else "")
                 + "color:" + fg + ";text-align:" + align + ";line-height:1.25;"
                 "white-space:pre-wrap;")
        if e["kind"] == "text":
            parts.append('<div style="' + style + '">' + _esc(e["text"]) + '</div>')
        elif e["kind"] == "field":
            val = _doc_field_caption_and_value(e["source"], report, label_map, idx)
            parts.append('<div style="' + style + '">' + _esc(val) + '</div>')
        elif e["kind"] == "image":
            im = emb.get((e["source"] or "").upper())
            if im is None:
                # Wildcard upload: one user image applied to every
                # placeholder that has no specific match.
                im = next((x for x in (report.embedded_images or [])
                           if getattr(x, "wildcard", False)), None)
            uri = _img_data_uri(im) if im else ""
            if uri:
                h = px(max(0.3, e["h"])) if e.get("h") else width
                parts.append('<img src="' + uri + '" style="position:absolute;left:'
                             + left + ';top:' + top + ';width:' + width
                             + ';height:' + h + ';object-fit:contain;"/>')
            else:
                parts.append('<div style="' + style + ';border:1px dashed #aaa;'
                             'color:#888;text-align:center;">[' + _esc(e["source"] or "image")
                             + ']</div>')

    inner = ('<div style="position:relative;width:' + px(pw) + ';height:' + px(ph)
             + ';margin:0 auto;background:#fff;">' + "".join(parts) + '</div>')
    return _render_page(inner, label="Page " + str(page_num) + " of " + str(total_pages),
                        max_width=f"{pw + 0.3:.1f}in")


def _is_dark(hexc):
    try:
        h = hexc.lstrip("#")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return (0.299 * r + 0.587 * g + 0.114 * b) < 110
    except Exception:
        return False


def _render_no_content_page(report):
    """Shown when a file has no Oracle Reports layout AND no data -- e.g. a
    Word/PDF/SQL file saved with an .xml name. Honest message, never a blank."""
    name = _esc(getattr(report, "name", "") or "This file")
    inner = (
        '<div style="padding:48px 40px;text-align:center;color:#64748b;">'
        '<div style="font-size:16px;font-weight:700;color:#0a2540;margin-bottom:10px;">'
        'No renderable report content</div>'
        '<div style="font-size:13px;line-height:1.5;max-width:30em;margin:0 auto;">'
        + name + ' has no Oracle Reports layout or data the converter can read. '
        'It may be a Word, PDF, or SQL file saved with an .xml name rather than '
        'an Oracle Reports XML export.</div></div>'
    )
    return _render_page(inner, label="Page 1 of 1")


def _render_generic_document_pages(report):
    """One sample page (the document repeats per record in SSRS).

    Falls back gracefully when there's no positional layout to draw: render the
    report's DATA as a table if any query parsed, else an honest 'no content'
    page. This prevents a blank preview for data-only exports, reports whose
    layout format we don't parse yet, or non-report files mislabeled .xml."""
    elems, _pw, _ph = _doc_collect_positioned(report)
    if not elems:
        if any(getattr(q, "items", None) for q in (report.queries or [])):
            return _render_tabular_pages(report)
        return _render_pages_wrapper([_render_no_content_page(report)])
    # Cover page: permits / letters whose XML carries a Parameter-Form
    # section_header (Run-info + selection criteria) print it as page 1
    # before the per-record body -- mirror the RDL's _build_letter_cover_page
    # so the preview's first page matches the real report's first page.
    if _has_cover_page(report):
        total = 2
        return _render_pages_wrapper([
            _render_header_summary_page(report, page_label="Page 1 of %d" % total),
            _render_generic_document_page(report, 0, 2, total, section="section_main"),
        ])
    return _render_pages_wrapper([
        _render_generic_document_page(report, 0, 1, 1)
    ])


def _is_header_summary_preview(report):
    """Structural detector (mirrors the RDL generator): the report's
    section_header carries a full summary table -- several per-category
    repeating frames + header fields bound to column-summary aggregates (CS_*)
    + many stat-row labels. The shape of an accounting/status report whose real
    content lives in the header. Never keyed on a report name."""
    hdr = _find_section(report.layout or [], "section_header")
    if hdr is None:
        return False
    rep_frames = labels = summ = 0
    stack = [hdr]
    while stack:
        g = stack.pop()
        if "repeating" in (getattr(g, "kind", "") or "").lower():
            rep_frames += 1
        for f in (getattr(g, "fields", None) or []):
            fk = (getattr(f, "kind", "") or "").lower()
            if fk == "text" and (getattr(f, "text", "") or "").strip():
                labels += 1
            elif fk == "field" and (getattr(f, "source", "") or "").upper().startswith("CS_"):
                summ += 1
        stack.extend(getattr(g, "children", None) or [])
    return rep_frames >= 2 and summ >= 1 and labels >= 4


def _render_header_summary_pages(report):
    """Header-resident summary/accounting report: page 1 is the section_header
    criteria cover + summary table (geometry-driven); page 2 is the
    section_main detail layout, when present."""
    main = _find_section(report.layout or [], "section_main")
    total = 2 if main is not None else 1
    pages = [_render_generic_document_page(report, 0, 1, total,
                                           section="section_header")]
    if main is not None:
        pages.append(_render_generic_document_page(report, 0, 2, total,
                                                   section="section_main"))
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
            '<div style="font-family:' + _ACTIVE_TITLE_FONT + '; '
            'font-size:14px; font-weight:bold; color:' + title_color + '; '
            'text-align:center; letter-spacing:0.4px;">' + _esc(ln) + '</div>'
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
            '<div style="font-family:' + _ACTIVE_TITLE_FONT + '; '
            'font-size:12px; font-weight:bold; color:' + title_color + '; '
            'text-align:center; letter-spacing:0.4px;">' + _esc(ln) + '</div>'
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
    # or any field whose name matches /date/i) so we don't hardcode any
    # report's specific date field name. Falls back to a generic placeholder.
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
        band_bg = _band_bg(_attr(top_rep, "background_color", ""), "#000079")
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
    global _ACTIVE_MODE, _ACTIVE_TITLE_FONT
    prev = _ACTIVE_MODE
    prev_font = _ACTIVE_TITLE_FONT
    _ACTIVE_MODE = "backend" if mode == "backend" else "frontend"
    try:
        _ACTIVE_TITLE_FONT = _title_font_css(report)
    except Exception:
        _ACTIVE_TITLE_FONT = "Arial, Helvetica, sans-serif"
    try:
        if mode == "backend":
            return _render_design_view(report)
        kind = detect_report_kind(report)
        if _is_header_summary_preview(report):
            # Accounting/status report whose criteria cover + summary table
            # live in section_header -- render that geometry-driven, then the
            # section_main detail page.
            return _render_header_summary_pages(report)
        if kind in ("letter", "certificate"):
            # Letters AND certificates are single positional documents -- render
            # the ACTUAL section_main layout (frames/texts/fields at their real
            # positions, real colors), never hardcoded sample content.
            return _render_generic_document_pages(report)
        return _render_tabular_pages(report)
    finally:
        _ACTIVE_MODE = prev
        _ACTIVE_TITLE_FONT = prev_font

