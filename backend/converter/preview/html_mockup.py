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
# Oracle inline FIELD REFERENCE inside boilerplate text: &<FIELD_NAME>. These
# interpolate a record's value into a sentence (form letters, mailing labels:
# "Dear &<FIRST_NAME>"). They must resolve to a sample value -- NOT be stripped
# to empty (which left letters reading "Dear Mr./Miss ,").
_ANGLE_TOKEN_RE = re.compile(r"&<\s*([^>]+?)\s*>")
# Page-number / pane builtins that Oracle/SSRS expose via &<...>; these are NOT
# data fields and should drop out of a static preview (no live page context).
_PAGE_BUILTINS = {
    "PHYSICALPAGENUMBER", "PAGENUMBER", "TOTALPAGES", "TOTALPHYSICALPAGES",
    "TOTALLOGICALPAGES", "TOTALPANES", "PANENUMBER", "PAGE", "PAGES",
}
_WS_COLLAPSE = re.compile(r"\s*\n\s*\n\s*", re.MULTILINE)


def _is_page_builtin(name: str) -> bool:
    return name.upper().replace(" ", "").replace("_", "") in _PAGE_BUILTINS

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


def _resolve_tokens(text: str, idx: int = 0) -> str:
    if _ACTIVE_MODE == "backend":
        def _ang_back(m):
            name = m.group(1).strip()
            return "" if _is_page_builtin(name) else f"«&{name.upper()}»"
        text = _ANGLE_TOKEN_RE.sub(_ang_back, text or "")
        return _TOKEN_RE.sub(
            lambda m: f"«&{m.group(1).upper()}»",
            text,
        )

    def _one(key):
        u = key.upper()
        if u in _TOKEN_PREVIEW:
            return _TOKEN_PREVIEW[u]
        if "YEAR" in u:
            return "2026" if ("PREVIOUS" not in u and "PREV" not in u) else "2025"
        # CF_/CP_ formulas and any other unmatched token -> a neutral sample,
        # NEVER the raw &TOKEN (which reads as broken in the preview). ``idx``
        # lets a tiled preview (mailing labels) vary values per cell.
        return _sample_for_source(key, idx)

    # &<FIELD> inline references first: page builtins drop out, data fields
    # resolve to a sample value so the sentence reads naturally.
    def _angle(m):
        name = m.group(1).strip()
        return "" if _is_page_builtin(name) else _one(name)
    text = _ANGLE_TOKEN_RE.sub(_angle, text or "")
    return _TOKEN_RE.sub(lambda m: _one(m.group(1)), text)


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


def _iter_group(g):
    """Yield g and every descendant group in its subtree."""
    stack = [g]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(getattr(n, "children", None) or [])


def _is_columnar_repeating(node):
    """True when THIS node is a repeating_frame whose field children form a
    horizontal table ROW (>= 2 field-kind children at >= 2 distinct x within a
    y-band). The single-node version of _has_columnar_repeating_frame, used to
    locate the table inside one frame of a positional document packet."""
    if (getattr(node, "kind", "") or "") != "repeating_frame":
        return False
    Y_BAND = 0.12
    bands = {}
    for f in (node.fields or []):
        if (f.kind or "") != "field":
            continue
        yb = round((f.y or 0.0) / Y_BAND)
        bands.setdefault(yb, set()).add(round(f.x or 0.0, 2))
    return any(len(xs) >= 2 for xs in bands.values())


def _group_columnar_repeating(g):
    """True when g's subtree contains a columnar (table-shaped) repeating frame."""
    return any(_is_columnar_repeating(n) for n in _iter_group(g))


def _group_paragraph_blocks(g):
    """Count body-paragraph text blocks in g's subtree -- the signal that a
    frame is a PROSE page (a memo/letter), not a data grid. A paragraph is a
    long block (>= 80 chars) that reads like a SENTENCE (>= 12 words), so a
    single long token -- e.g. a 120-char column-name header on a wide stress
    report -- is NOT mistaken for prose (which would false-positive the packet
    detector)."""
    n = 0
    for node in _iter_group(g):
        for f in (getattr(node, "fields", None) or []):
            if getattr(f, "kind", "") != "text":
                continue
            t = (getattr(f, "text", "") or "").strip()
            if len(t) >= 80 and len(t.split()) >= 12:
                n += 1
    return n


def _is_positional_document_packet(report):
    """section_main is a multi-frame POSITIONAL DOCUMENT PACKET -- e.g. a memo
    cover page + a data table + a closing letter, each its own sheet via
    Oracle pageBreakAfter -- rather than a classic tabular listing. Structural
    signal: >= 2 page-separated top-level content frames where at least one is
    a PROSE page (body paragraphs, no columnar table) and either another is a
    TABLE page or there is a second prose page.

    Such packets must render geometry-faithfully (each frame on its own sheet,
    the table tiled into rows in place) -- the tabular cover+detail template
    fabricates a run-info cover and silently discards the prose frames
    (wild-corpus verified: a memo + data table + warrant letter packet that the
    generic template rendered as a fake 'Run By / Total of ALL Records' card).
    Generic: keyed on frame shape, never a report name."""
    main = _find_section(report.layout or [], "section_main")
    if main is None:
        return False
    groups = [c for c in (main.children or [])
              if not _is_footer_frame(c) and _frame_has_content(c)]
    if len(groups) < 2:
        return False
    prose = sum(1 for g in groups
                if _group_paragraph_blocks(g) >= 1 and not _group_columnar_repeating(g))
    tables = sum(1 for g in groups if _group_columnar_repeating(g))
    return prose >= 1 and (tables >= 1 or prose >= 2)


def _is_single_record_form(report):
    """section_main is a POSITIONAL SINGLE-RECORD FORM -- an invoice/requisition/
    order form: one master record per physical page (Oracle maxRecordsPerPage=1)
    whose master fields are scattered at absolute positions (a vendor block, a
    bill-to block, an office-use box) WITH an embedded columnar line-item table.

    This is neither a tabular list (many records stacked per page) nor a pure
    letter (no columnar table). The generic tabular template mis-renders it --
    collapsing the positional master into a navy band and never laying out the
    form -- so route it through the geometry-faithful per-record document
    renderer instead (tile_tables=True tiles the line-item grid in place).

    Structural, never keyed on a report name. Checked AFTER the letter/
    certificate route so a maxRecordsPerPage=1 LETTER (no columnar table) keeps
    its prose-document path untouched."""
    main = _find_section(report.layout or [], "section_main")
    if main is None:
        return False
    # An embedded columnar line-item table must be present (else it's a pure
    # letter/certificate, handled elsewhere).
    if not _has_columnar_repeating_frame(report):
        return False
    # EXACTLY ONE line-item table. A genuine form (invoice/requisition) has one
    # master block + one detail table. A report with SEVERAL columnar tables, or
    # a deeply nested master-detail-detail, is a multi-table layout whose frames
    # sit at different positions -- the single-sheet positional tile would pile
    # them on top of each other. Those keep the tabular / nested-MD path.
    columnar = 0
    maxrec1 = False
    stack = [main]
    while stack:
        g = stack.pop()
        if "repeating" in (getattr(g, "kind", "") or "").lower():
            if _is_columnar_repeating(g):
                columnar += 1
            if int(getattr(g, "max_records_per_page", 0) or 0) == 1:
                maxrec1 = True
        stack.extend(getattr(g, "children", None) or [])
    if columnar != 1 or not maxrec1:
        return False
    # A deeply nested master-detail TABLE is handled by the nested-MD renderer.
    if _is_nested_master_detail_preview(report):
        return False
    return True


def _render_single_record_form_pages(report):
    """Render a positional single-record form (one master record = one page):
    paint section_main's frames/fields at their real geometry and tile the
    embedded line-item table in place. Geometry-faithful; no fabricated cover,
    no run-info card, no navy band collapse."""
    page = _render_generic_document_page(
        report, 0, 1, 1, section="section_main", tile_tables=True, lift_title=True)
    return _render_pages_wrapper([page])


def _any_maxrec1(report):
    """Any repeating frame declares Oracle maxRecordsPerPage==1 -- one master
    record fills a physical page. The defining signal of a per-record document
    (a form/certificate printed one-per-page), as opposed to a list that stacks
    many records per page."""
    for g in _iter_layout(report):
        if "repeating" in (getattr(g, "kind", "") or "").lower():
            if int(getattr(g, "max_records_per_page", 0) or 0) == 1:
                return True
    return False


def _block_heading_count(report):
    """Count STANDALONE block-heading labels in section_main: short static-text
    captions that (a) sit ALONE on their y-row (no other label within ~0.12in y
    -- so NOT a shared column-header row), (b) do NOT end in ':' (section
    captions, not field captions), (c) are <=3 words / <=28 chars, (d) carry no
    raw &token, and (e) have a DATA field positioned just below them. A
    per-facility FORM has several (Plant Location / SIC-NAIC / Emissions Contact
    ...); a nested-MD list's master band has 0 (its captions end ':'); a tabular
    column-header row has 0 (its labels share one y-band, so none is 'alone')."""
    main = _find_section(report.layout or [], "section_main")
    if main is None:
        return 0
    texts, fields = [], []
    for n in _iter_group(main):
        for f in (n.fields or []):
            if (f.kind or "") == "text":
                t = (f.text or "").strip()
                if t:
                    texts.append((f, t))
            elif (f.kind or "") == "field":
                fields.append(f)
    count, seen_y = 0, set()
    for f, t in texts:
        if t.endswith(":") or "&" in t:
            continue
        if not (1 <= len(t.split()) <= 3) or len(t) > 28:
            continue
        y, x = (f.y or 0.0), (f.x or 0.0)
        alone = not any(g is not f and abs((g.y or 0.0) - y) <= 0.12
                        for g, _ in texts)
        if not alone:
            continue
        below = any(0.02 <= (fl.y or 0.0) - y <= 1.3 and abs((fl.x or 0.0) - x) < 0.6
                    for fl in fields)
        if not below:
            continue
        yb = round(y / 0.12)
        if yb in seen_y:
            continue
        seen_y.add(yb)
        count += 1
    return count


def _outer_master_field_count(report):
    """Number of layout DATA fields bound to the OUTERMOST data query group's
    own columns -- how many attributes the master record itself carries. A
    per-facility FORM's master owns many (address / contact / id blocks); a
    MULTI-SECTION accounting report's outer group owns ~0 (it is a pure section
    grouping whose data lives in repeating detail rows). Keeps the nested-MD
    block-heading signal from capturing a multi-section summary as a form."""
    main = None
    for q in (report.queries or []):
        if getattr(q, "groups", None):
            if main is None or len(q.items or []) > len(main.items or []):
                main = q
    if main is None or not getattr(main, "groups", None):
        return 0
    names = {(it.name or "").upper() for it in (main.groups[0].items or [])}
    if not names:
        return 0
    cnt = 0
    for top in (report.layout or []):
        for g in _iter_group(top):
            for f in (g.fields or []):
                if (f.kind or "") == "field" and (f.source or "").upper() in names:
                    cnt += 1
    return cnt


def _dense_labeled_master_block(report):
    """A single layout node in section_main owns a DENSE labeled block: >=6
    static-text labels AND >=6 value fields spread over >=6 distinct y-rows --
    a stacked label:value FORM block (Plant Location / Mailing Address / SIC-NAIC
    ... all in one master container). A flat tabular list never matches: its
    value fields live in a ONE-ROW repeating frame (1-2 y-bands), and its labels
    are a single column-header row -- so no single node stacks >=6 values down
    >=6 rows."""
    main = _find_section(report.layout or [], "section_main")
    if main is None:
        return False
    for n in _iter_group(main):
        labels = sum(1 for f in (n.fields or [])
                     if (f.kind or "") == "text" and (f.text or "").strip())
        vals = [f for f in (n.fields or []) if (f.kind or "") == "field"]
        if labels >= 6 and len(vals) >= 6:
            yrows = {round((f.y or 0.0) / 0.12) for f in vals}
            if len(yrows) >= 6:
                return True
    return False


def _is_per_record_document(report):
    """section_main is a per-record POSITIONAL DOCUMENT -- a per-facility labeled
    FORM or a CERTIFICATE -- that the generic tabular / nested-MD templates
    WRONGLY collapse into a data grid (dropping the labeled blocks + any state
    seal). Routes such reports to the geometry-faithful per-record document
    renderer instead.

    Structural, never keyed on a report name. Three independent POSITIVE signals
    (any one marks a positional document), each gated so the reports that already
    render correctly are never pulled out of their path:
      * Signal A -- Oracle maxRecordsPerPage==1 (one record per page) with no
        full data grid (catches per-record permits / accreditation forms).
      * Signal B -- a nested-MD-shaped report whose master frame carries >=3
        STANDALONE block-heading labels (catches a per-facility inventory form;
        a genuine nested-MD data list scores 0).
      * Signal C -- a CERTIFICATE: an embedded seal/logo image co-located with a
        prose body paragraph.
      * Signal D -- a single DENSE labeled master block (catches a per-facility
        summary form; flat lists score 0).
    """
    main = _find_section(report.layout or [], "section_main")
    if main is None:
        return False
    # Reports already handled well keep their own routes.
    if _is_single_record_form(report):        # positional invoice/requisition
        return False
    if _is_header_summary_preview(report):     # accounting/criteria summary
        return False
    # A: one master record per physical page, no full data grid.
    if _any_maxrec1(report):
        return True
    # B: nested-MD-shaped but really a labeled facility FORM -- its master
    #    record owns its own dense field set (>=4 data fields: address/contact/
    #    id blocks) AND prints >=3 standalone block headings. A MULTI-SECTION
    #    accounting report is also nested + block-headed, but its outer group
    #    owns ~0 data fields (pure section grouping) -- excluded here, handled
    #    by the multi-section archetype instead.
    if (_is_nested_master_detail_preview(report)
            and _block_heading_count(report) >= 3
            and _outer_master_field_count(report) >= 4):
        return True
    # C: certificate -- embedded seal/logo image + a prose paragraph.
    if (report.embedded_images or []) and _group_paragraph_blocks(main) >= 1:
        return True
    # D: a dense stacked label:value master block.
    if _dense_labeled_master_block(report):
        return True
    return False


def _has_nested_repeating_frames(report):
    """True when a repeating frame CONTAINS another repeating frame anywhere in
    section_main -- i.e. a deep master-detail HIERARCHY (applicant>employer>
    address, application>course), not a flat form with leaf sub-tables. Such a
    document is already laid out correctly by its absolute Oracle geometry;
    tiling its nested frames inflates each one and reflows the surrounding labels
    out of order (the Employer label drifts below its own address; the Course
    block floats above the Employer). Structural, no report names."""
    main = _find_section(report.layout or [], "section_main")
    if main is None:
        return False

    def rep_descendant(node):
        for c in (node.children or []):
            if "repeating" in (getattr(c, "kind", "") or "").lower():
                return True
            if rep_descendant(c):
                return True
        return False

    def walk(node):
        for c in (node.children or []):
            if "repeating" in (getattr(c, "kind", "") or "").lower() and rep_descendant(c):
                return True
            if walk(c):
                return True
        return False

    return walk(main)


def _render_per_record_document_pages(report):
    """Render a per-record positional document (labeled facility form /
    certificate): paint section_main's frames/fields/images at their real
    geometry and tile any embedded sub-tables in place. One sample record/page
    (the document repeats per record in SSRS). Mirrors
    _render_generic_document_pages but forces tile_tables + lift_title so the
    boxed sub-panels (SPT/EMISSIONS, SIC/NAIC) lay out instead of collapsing."""
    # A DEEP master-detail hierarchy (a repeating frame nesting other repeating
    # frames AND a genuine nested-MD group chain) is positioned correctly by its
    # absolute Oracle geometry -- tiling only scrambles the field/cell order
    # (Employer label drifts below its address; Course block floats above it).
    # Tile ONLY flat per-facility forms (leaf sub-tables, e.g. the AIR inventory
    # SIC/NAIC table), which are NOT nested-MD (_is_nested_master_detail_preview
    # False) so they keep their correct tiling.
    _tile = not (_has_nested_repeating_frames(report)
                 and _is_nested_master_detail_preview(report))
    # Report-wide summary TRAILER frame(s) (totals, no repeating descendant --
    # MVWFR's Application/MVWFR-Status count tables) print ONCE at the report end
    # on their own page, NOT on every per-record page. The per-record page skips
    # them (skip_trailer default True); each is rendered as a trailing page below.
    _main = _find_section(report.layout or [], "section_main")
    _kids = (_main.children if _main else None) or []
    _trailers = [c for c in _kids if _is_summary_trailer_frame(c)]
    _record_frames = [c for c in _kids
                      if "frame" in (getattr(c, "kind", "") or "").lower()
                      and not _is_summary_trailer_frame(c)]
    if not _record_frames:
        _trailers = []  # don't strip a report that is ALL summary
    pages = []
    if _has_cover_page(report):
        total = 2 + len(_trailers)
        pages.append(_render_header_summary_page(
            report, page_label="Page 1 of %d" % total))
        pages.append(_render_generic_document_page(
            report, 0, 2, total, section="section_main",
            tile_tables=_tile, lift_title=True))
    else:
        total = 1 + len(_trailers)
        pages.append(_render_generic_document_page(
            report, 0, 1, total, section="section_main",
            tile_tables=_tile, lift_title=True))
    for _tr in _trailers:
        pages.append(_render_generic_document_page(
            report, 0, len(pages) + 1, total, section="section_main",
            root=_tr, tile_tables=False, lift_title=False, skip_trailer=False))
    return _render_pages_wrapper(pages)


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
    # 0a. POSITIONAL CERTIFICATE / wallet card, checked BEFORE the columnar rule:
    #     an embedded SEAL image + only a TINY columnar frame (<=2 fields, i.e. an
    #     occupation/expiration pair -- not a real data grid) + little prose. The
    #     real output is a centered name, a short prose sentence, the seal, a
    #     two-column occupation/expiration list, and a rotated address block --
    #     a document, not a table. Real data tables carry no seal; rich-prose
    #     certs/letters are caught by steps 1-2; so this only claims the
    #     seal + tiny-list wallet card that step 0 would otherwise send to a grid.
    def _has_embedded_image(rep):
        return any(getattr(f, "kind", "") == "image"
                   for g in _iter_layout(rep) for f in (g.fields or []))

    def _max_columnar_fieldcount(rep):
        best = 0
        for g in _iter_layout(rep):
            if (g.kind or "") != "repeating_frame":
                continue
            ff = [f for f in (g.fields or []) if getattr(f, "kind", "") == "field"]
            xs = {round(getattr(f, "x", 0) or 0, 1) for f in ff}
            if len(xs) >= 2 and len(ff) >= 2:
                best = max(best, len(ff))
        return best

    if (_has_embedded_image(report)
            and _max_columnar_fieldcount(report) <= 2
            and _count_paragraphy_text_blocks(report) < 2):
        return "certificate"

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

    # 2. Paragraph-shaped static text blocks (multi-line, >=80 chars each) are a
    #    strong letter signal - body paragraphs. Any report REACHING this point
    #    has NO columnar repeating frame (step 0 already returned tabular for
    #    those) and is not a certificate panel (step 1), so >=2 prose paragraphs
    #    is a document, not a table -- this catches a legal/permit LETTER that
    #    carries a small single-column data sub-frame (which would otherwise fall
    #    to the weak color-based tabular fallbacks below and lose all its prose,
    #    title, and signatory). Certificates with rich prose are already handled
    #    at step 1; real tables exit at step 0; so the floor is safe at 2.
    if _count_paragraphy_text_blocks(report) >= 2:
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
    bold preferred. Returns the LayoutField or None.

    Right-justified text (align end/right) is excluded: a report title is
    centered or left-aligned, never right-justified. Large right-justified
    bold text is a total, a value, a run-date, or a red comparison/error
    message (e.g. a format-trigger alert) -- picking it would put that line
    where the title belongs. Generic, structural; not keyed on any report
    name or specific phrase."""
    best = None
    best_key = (-1, -1)
    for g in _iter_layout(report):
        for f in g.fields or []:
            if f.kind != "text":
                continue
            if not (f.text or "").strip():
                continue
            align = (f.align or "").lower()
            if align in ("end", "right"):
                continue
            sz = int(f.font_size or 0)
            centered = 1 if align == "center" else 0
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
            # A printed caption can embed an Oracle lexical ref
            # (e.g. "ENF REQ #&F_ENF_REQ_ID APPROVAL"); resolve it so the
            # detail/band label never prints a raw &TOKEN.
            txt = _resolve_tokens((f.text or "").strip().rstrip(":"), 0)
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
    MONEY_POOL   = ["$1,250.00", "$3,480.00"]
    SHORT_ID     = ["ID-0001", "ID-0002"]
    TYPE_POOL    = ["Type Alpha", "Type Bravo"]
    STATUS_POOL  = ["Active", "Pending"]
    COMMENT_POOL = ["Initial review completed.", "Follow-up scheduled."]
    GROUP_POOL   = ["Group One", "Group Two"]
    EMAIL_POOL   = ["[email protected]", "[email protected]"]
    PHONE_POOL   = ["(555) 010-1001", "(555) 010-1002"]

    # Keyword → pool dispatch (order matters: more-specific keywords first).
    # Non-English stems (RO/ES/FR/PT/IT) are listed alongside English so the
    # many non-English wild-corpus reports read naturally -- AND so a word
    # like "nume" (RO: name) wins the NAME pool before its "num" substring
    # could fall into the NUMBER pool further down.
    KEYWORD_MAP = [
        (("email", "mail", "correo", "courriel"), EMAIL_POOL),
        (("phone", "tel", "telefon", "telefono", "telephone"), PHONE_POOL),
        # Generic structural keywords (envelope/chief cover the common
        # Oracle CF_/CP_ formula naming without any client tokens).
        (("envelope",),                    ["Sample Envelope Report",
                                            "Sample Envelope Report"]),
        (("chief", "director", "officer", "jefe", "directeur"),
                                           ["Sample Chief, Bureau Chief",
                                            "Sample Director, Division"]),
        # Money / amount / balance (placed BEFORE number so "saldo"/"valor"
        # read as currency, not a bare count).
        (("salar", "salai", "sueldo", "saldo", "importe", "monto", "montant",
          "valoare", "valor", "pret", "precio", "prix", "price", "amount",
          "balance", "subtotal", "discount", "fee", "cost", "payment", "paid"),
                                           MONEY_POOL),
        # Person names (incl. RO/ES/FR/PT) -- BEFORE the generic name row so
        # FIRST_NAME / LAST_NAME read as a person, not an org "Group One".
        (("first", "last", "fname", "lname", "surname", "given", "middle",
          "prenom", "apellido", "nombre", "prenume", "nom_",
          "client", "cliente", "persoan", "persona", "empleado", "angajat",
          "funcionar", "contact", "user", "usuario", "utilizator",
          "signer", "signator", "manager", "gerente", "employee", "emp name",
          "staff", "worker", "tenant", "applicant", "patient", "student", "author"),
                                           PERSON_POOL),
        (("contractor", "owner", "permittee", "company", "org", "facility",
          "magazin", "empresa", "compania", "societe", "store"), NAME_POOL),
        (("addr", "street", "location", "adresa", "adres", "adresse",
          "direccion", "endereco", "domicilio"), ADDR_POOL),
        # Geography: country / county / region / state. CRITICAL that these
        # come BEFORE the NUMBER row -- "country" and "county" both START with
        # "count", so a word-boundary "count" match would otherwise paint a
        # whole geography report as "1001" (wild-corpus verified: Judet-Tara).
        (("country", "nation", "pais", "pays", "tara"),
                                           ["Westeria", "Eastland"]),
        (("county", "region", "province", "provincia",
          "judet", "departament", "district", "comarca"),
                                           ["North District", "South District"]),
        (("city", "town", "oras", "ciudad", "ville", "cidade", "localidad"),
                                           CITY_POOL),
        (("date", "dt", "sysdate", "rundate", "data", "fecha", "vigencia", "fech"), DATE_POOL),
        (("comment", "descr", "notes", "remark", "observ", "nota"), COMMENT_POOL),
        (("status", "estado", "stare", "etat"), STATUS_POOL),
        (("type", "tipo", "tip"),          TYPE_POOL),
        # Generic NAME / denomination (RO denumire, ES nombre, etc.) -- still
        # before NUMBER so "nume"/"nome" never falls to the "num" substring.
        (("nume", "nombre", "nome", "denumire", "razon", "raison", "naam",
          "titre", "titulo", "name"),      GROUP_POOL),
        (("count", "total", "qty", "quantity", "num", "number", "cantidad",
          "cantitate", "cant"), NUM_POOL),
        (("id", "code", "key", "cod", "clave"), SHORT_ID),
        (("nm", "label", "group", "grup"), GROUP_POOL),
    ]
    # Boolean indicator / flag columns (Oracle '*_Ind', '*_Indicator',
    # '*_Flag', '*_YN'): a Yes/No marker, never a generic "Sample Value A"
    # text block. Precise suffix match so 'binding'/'finding' don't trip it.
    if re.search(r"(_|\b)(ind|indicator|flag|yn)$", u, re.IGNORECASE):
        return ["Y", "N"][idx % 2]
    # Word-boundary match (key has '_' normalized to spaces): a stem matches
    # only at the START of a word, so "data" matches "data nasterii" but NOT
    # "metadata", and "num" matches "perm num" but never inside "nume".
    for keywords, pool in KEYWORD_MAP:
        for kw in keywords:
            if kw and re.search(r"\b" + re.escape(kw), key):
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


def _report_is_themed(report):
    """True when the source carries a GENUINE band color -- a non-white,
    non-design background fill, OR a fill_pattern="solid" non-white foreground
    fill (Oracle stores some band colors, e.g. a navy column header, there).
    When False the report is a PLAIN receipt/list and bands must render with no
    fill (black on white), never an invented navy/yellow theme. Mirrors
    generators/rdl.py has_real_band so the RDL and the mockup agree."""
    _WH = ("#ffffff", "#fffffe", "#fefefe")

    def _iter(g):
        yield g
        for ch in (g.children or []):
            yield from _iter(ch)

    for top in (report.layout or []):
        for g in _iter(top):
            bg = _normalize_color(getattr(g, "background_color", "") or "", "")
            if bg and bg.lower() not in _WH and not _is_design_fill(bg):
                return True
            fpat = (getattr(g, "fill_pattern", "") or "").lower()
            fg = _normalize_color(getattr(g, "foreground_color", "") or "", "")
            if (fpat == "solid" and fg and fg.lower() not in _WH
                    and not _is_design_fill(fg)):
                return True
    return False


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
    t = text or ""
    if re.match(r"(?i)^\s*error\b\s*:", t):
        return True
    # A not-equal / comparison operator in DISPLAY text is a validation/alert
    # message ("Total <> Total Amount on ...", "Count != expected") that Oracle
    # prints only via a format trigger when the data fails the check -- a real
    # caption never contains <> / != / ≠. Generic, no per-report phrase.
    if re.search(r"(<>|!=|≠)", t):
        return True
    return False


def _is_conditional_alert_frame(g):
    """A plain (non-repeating) frame gated by a format trigger whose content is a
    conditional ERROR/alert branch (a totals-mismatch warning, an empty-state
    notice). Oracle shows it ONLY when the data hits the exception; a static
    sample preview can't evaluate the trigger, so the whole box -- its alert
    text AND the value field beside it -- is suppressed rather than painted over
    the normal content. Excludes repeating/data frames and any frame containing
    a repeating data frame, so real data is never dropped. Tight: requires BOTH
    a format trigger and recognizable error text inside. Generic, no names."""
    if not getattr(g, "format_trigger", ""):
        return False
    if "repeating" in (getattr(g, "kind", "") or "").lower():
        return False
    stack = list(getattr(g, "children", None) or [])
    while stack:
        c = stack.pop()
        if "repeating" in (getattr(c, "kind", "") or "").lower():
            return False
        stack.extend(getattr(c, "children", None) or [])
    for f in (getattr(g, "fields", None) or []):
        if getattr(f, "kind", "") == "text" and _is_conditional_error_text(f.text or ""):
            return True
    return False


def _is_conditional_error_source(src):
    """An Oracle *_ERROR / ERR_* formula or placeholder field is a conditional
    error/empty-state message printed ONLY via a format trigger when the data
    hits that condition; on the happy path Oracle hides it. A static preview
    can't evaluate the trigger, so suppress it -- the field version of
    _is_conditional_error_text (e.g. CP_PERMITEE_ERROR sitting over a letterhead
    logo). Keyed on the err/error name convention at a word boundary, so
    OPERATOR / TERMS / VENDOR are never caught. Generic, no report names."""
    return bool(re.search(r"(?i)(^|_)err(or)?($|_)", src or ""))


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

    # Build y-ordered ENTRIES: each label:value pair at its label y, PLUS any
    # unpaired multi-line text note at its OWN y. A note that sits BETWEEN two
    # pairs (e.g. the envelope sort-order / "Limitation:" paragraph that prints
    # under "*Generate Envelopes", above the next pair) renders in place instead
    # of being dropped. A report whose notes all sit below the last pair is
    # unaffected: the combined y-sort puts the pairs first then the notes -- the
    # same order, with byte-identical pair + note HTML, as before.
    entries = []  # (y, x, html)
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
                # Shared field resolver: a CF_/CP_ field named after the report
                # resolves to the report TITLE (a CF_<REPORT> formula -> the
                # report's title text), and a constant-blank formula renders
                # blank, instead of a generic 'Sample Value A'.
                val = _doc_field_caption_and_value(vsrc or vtext or "value", report, {}, 0)
        else:
            # static text: resolve any &TOKEN substitutions
            val = _resolve_tokens(vtext or "")
            val = re.sub(r"&<[^>]+>", "", val).strip()
        ly, lx = label_pos((label_text, value_item))
        entries.append((ly, lx,
            '<div style="display:flex; align-items:baseline; margin:4px 0;">'
            '<div style="min-width:200px; max-width:240px; text-align:left; '
            'padding-right:12px; font-weight:bold; color:' + _TAB_INK + '; '
            'font-size:13px;">' + _esc(label_text) + ':</div>'
            '<div style="flex:1; text-align:left; color:' + _TAB_INK + '; '
            'font-size:13px; font-weight:bold;">'
            + _esc(val) + '</div></div>'))

    # Unpaired wide text blocks (footnotes / info lines / a limitation
    # paragraph), captured at their own y -- no longer only below the last pair.
    used_ids = {id(v) for _, v in pairs}
    used_ids.update(id(it) for it in items
                    if it[0] == "text" and (it[3] or "").strip().endswith(":"))
    notes = []
    for it in items:
        if id(it) in used_ids:
            continue
        kind, _n, _s, text, x, y, _w, _h, _f = it
        if kind != "text":
            continue
        t = (text or "").strip()
        if not t or t.endswith(":"):
            continue
        if _is_param_form_note(t):
            continue
        notes.append((y, x, t))
    notes.sort()
    for _y, _x, t in notes[:4]:
        entries.append((_y, _x,
            '<div style="margin:6px 0 0; color:' + _TAB_INK_SOFT + '; '
            'font-size:12px; font-style:italic;">'
            + _esc(_resolve_tokens(t, 0)) + '</div>'))

    entries.sort(key=lambda e: (e[0], e[1]))
    inner = (
        '<div style="padding:36px 32px 28px; max-width:640px; margin:0 auto;">'
        + "".join(e[2] for e in entries)
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

    # Band from outer frame -- navy/yellow only when the source is actually
    # themed; a plain report gets a plain (white/black) band, never invented
    # chrome (the #1 reason a plain Oracle list looked like a styled tablix).
    if _report_is_themed(report):
        band_bg = _band_bg(_attr(top_rep, "background_color", ""), "#000079")
        band_fg = _normalize_color(_attr(top_rep, "foreground_color", ""), "#FFFF00")
    else:
        band_bg = _band_bg(_attr(top_rep, "background_color", ""), "#ffffff")
        band_fg = _normalize_color(_attr(top_rep, "foreground_color", ""), "#111111")
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

    def header_text(frame):
        # Top-left static text = the section title (see rdl._header_text). The
        # section frame itself may be a repeating GROUP frame, so its OWN top
        # text still counts as the section label -- only NESTED repeating frames
        # (the detail rows) are skipped.
        cands = []
        def walk(g, in_rep, top):
            ir = in_rep or (not top and (g.kind or "").lower() == "repeating_frame")
            for f in (g.fields or []):
                if f.kind == "text" and not ir:
                    t = (f.text or "").strip()
                    if t and "&<" not in t and not t.lower().endswith(".rdf"):
                        # Keep a band's full caption -- e.g. "Historical Property
                        # Status:  Properties prior to Oct. 1, 2005" prints as two
                        # lines in Oracle; join its non-blank lines so the subtitle
                        # isn't dropped. Single-line captions are unaffected.
                        _cap = " ".join(ln.strip() for ln in t.split("\n")
                                        if ln.strip())
                        cands.append((float(getattr(f, "y", 0.0) or 0.0),
                                      float(getattr(f, "x", 0.0) or 0.0), _cap))
            for c in (g.children or []):
                walk(c, ir, False)
        walk(frame, False, True)
        if not cands:
            return ""
        # A section TITLE sits at the LEFT; a text out in the data columns
        # (e.g. "Applications"/"Fees" headers at x>=~4) is a COLUMN label, not
        # the section name. Only accept left-region texts, so a repeating-group
        # section whose real title is a FORMULA field (no static text) does not
        # borrow a column header. Falls back to the grouping field (caller).
        left = [c for c in cands if c[1] < 3.0]
        if not left:
            return ""
        left.sort(key=lambda c: (c[0], c[1]))
        return left[0][2]

    def band_col_headers(frame):
        # The Oracle header sub-frame carries the section title (leftmost) PLUS the
        # value-column header label(s) to its right -- "Number", or "Applications"
        # / "Fees". Return those right-of-title labels (ordered by x) so the
        # preview shows the REAL column header instead of a humanized field name.
        cands = []
        def walk(g, in_rep):
            ir = in_rep or (g.kind or "").lower() == "repeating_frame"
            for f in (g.fields or []):
                if f.kind == "text" and not ir:
                    t = (f.text or "").strip()
                    if (t and "&<" not in t and not t.lower().endswith(".rdf")
                            and "total" not in t.lower()):
                        cands.append((float(getattr(f, "y", 0.0) or 0.0),
                                      float(getattr(f, "x", 0.0) or 0.0),
                                      t.split("\n")[0].strip()))
            for c in (g.children or []):
                walk(c, ir)
        walk(frame, False)
        if not cands:
            return []
        cands.sort(key=lambda z: (z[0], z[1]))
        y0 = cands[0][0]
        band = sorted([c for c in cands if abs(c[0] - y0) < 0.15],
                      key=lambda z: z[1])
        # Drop the leftmost (the section title); the rest are column headers.
        return [c[2] for c in band[1:]]

    def band_col_headers_deep(frame):
        # Fallback (mirrors rdl._band_col_headers_deep): a break report whose
        # value-column captions ("Applications"/"Fees") live INSIDE the section's
        # header REPEATING frame -- which the shallow scan skips. Collect TEXT
        # fields in the top band to the RIGHT (x>3) as the captions. Used ONLY when
        # the shallow scan is empty, so a report with section-level captions is
        # unchanged.
        cands = []
        def walk(g):
            for f in (g.fields or []):
                if (f.kind or "") == "text":
                    t = (f.text or "").strip()
                    if (t and "&<" not in t and not t.lower().endswith(".rdf")
                            and "total" not in t.lower()
                            and "number" not in t.lower()):
                        cands.append((float(getattr(f, "y", 0.0) or 0.0),
                                      float(getattr(f, "x", 0.0) or 0.0),
                                      t.split("\n")[0].strip()))
            for c in (g.children or []):
                walk(c)
        walk(frame)
        if not cands:
            return []
        cands.sort(key=lambda z: (z[0], z[1]))
        y0 = cands[0][0]
        return [c[2] for c in sorted(
            [c for c in cands if abs(c[0] - y0) < 0.2 and c[1] > 3.0],
            key=lambda z: z[1])]

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

    # Section frames usually sit directly under section_main, but some reports
    # wrap ALL of them inside ONE body container frame. Pick the container level
    # (section_main itself, or one of its child frames) that holds the most
    # sibling section frames -- a non-repeating frame containing a query-bound
    # repeating table. Generalizes to the wrapped-sections layout (e.g. an
    # accounting report whose ~9 sections nest inside a single body frame).
    def _section_frames(container):
        kids = list(container.children or [])
        plain = [c for c in kids
                 if "frame" in (c.kind or "").lower()
                 and (c.kind or "").lower() != "repeating_frame"
                 and tables_in(c)]
        # An accounting report stacks one query-bound GROUP frame per section;
        # those are REPEATING frames. Count them as sections only when there are
        # >=2 of them on DISTINCT queries, so a genuine nested master-detail (ONE
        # repeating master, e.g. a nested master-detail) is never split into sections.
        rep = [c for c in kids
               if (c.kind or "").lower() == "repeating_frame"
               and getattr(c, "source_query", None)]
        rep_qs = {(getattr(c, "source_query", "") or "").upper() for c in rep}
        if len(rep) >= 2 and len(rep_qs) >= 2:
            return plain + rep
        return plain
    _containers = [sm] + [c for c in (sm.children or [])
                          if "frame" in (c.kind or "").lower()
                          and (c.kind or "").lower() != "repeating_frame"]
    frames = []
    for _cont in _containers:
        _sf = _section_frames(_cont)
        if len(_sf) > len(frames):
            frames = _sf
    if len(frames) < 2:
        return None

    # The section's GROUPING field (leftmost field NOT inside a nested repeating
    # detail frame) -- its sample value stands in for a section title computed by
    # a formula when there is no static title text (e.g. a CF_*_Group formula
    # whose live value names the section, like "New Applications Received").
    def _grp_src(g, top=True):
        best = None
        for f in (g.fields or []):
            if (f.kind or "") == "field" and (f.source or "").strip():
                bx = float(getattr(f, "x", 0.0) or 0.0)
                if best is None or bx < best[0]:
                    best = (bx, f.source)
        for c in (g.children or []):
            if (c.kind or "").lower() == "repeating_frame":
                continue  # detail rows, not the section's grouping field
            cb = _grp_src(c, False)
            if cb and (best is None or cb[0] < best[0]):
                best = cb
        return best

    def _footer_totals(frame):
        # Mirror of rdl._detect_multi_section._footer_totals: the section's REAL
        # group-footer total LABELS ("Total Properties Closed", ...), ordered by
        # y, so the preview shows the true labeled totals (paired to tables in
        # order) instead of one generic "Subtotal". Skips trailer/page tokens and
        # anything inside a repeating (detail) frame.
        labels = []
        def walk(g, in_rep):
            ir = in_rep or (g.kind or "").lower() == "repeating_frame"
            for f in (g.fields or []):
                if (f.kind or "") == "text" and not ir:
                    t = (f.text or "").strip()
                    if (t and "&<" not in t and not t.lower().endswith(".rdf")
                            and "total" in t.lower()):
                        labels.append((float(getattr(f, "y", 0.0) or 0.0),
                                       t.split("\n")[0].strip()))
            for c in (g.children or []):
                walk(c, ir)
        walk(frame, False)
        labels.sort(key=lambda z: z[0])
        seen, out = set(), []
        for _y, lt in labels:
            if lt.lower() not in seen:
                seen.add(lt.lower())
                out.append(lt)
        return out

    def _has_aggregate(frame):
        # Mirror of rdl._detect_multi_section._has_aggregate: a section gets a
        # total only if it carries a Sum*/CS_/CF_ summary field. A plain list
        # section (label + count, no aggregate) prints no total.
        st = [frame]
        while st:
            g = st.pop()
            for f in (g.fields or []):
                if (f.kind or "") == "field" and re.match(
                        r"(?i)^(sum|cs_|cf_)", (f.source or "").strip()):
                    return True
            st.extend(g.children or [])
        return False

    sections, distinct = [], set()
    for fr in sorted(frames, key=lambda f: (f.y or 0.0)):
        t = tables_in(fr)
        if not t:
            continue
        for src, _ in t:
            distinct.add(src.upper())
        _gs = _grp_src(fr)
        _tot = _footer_totals(fr)
        _ch = band_col_headers(fr) or band_col_headers_deep(fr)
        _agg = _has_aggregate(fr)
        _hdr = header_text(fr)
        sections.append({"header": _hdr,
                         "header_src": (_gs[1] if _gs else None),
                         "tables": t,
                         "totals": _tot,
                         "col_headers": _ch,
                         "_y": float(fr.y or 0.0),
                         # A total row only when Oracle computes one: a labeled
                         # footer OR a Sum*/CS_/CF_ summary field somewhere in the
                         # section. A plain list section (label + count, no
                         # aggregate) prints no total -- mirrors the RDL.
                         "has_total": bool(_tot) or _agg,
                         # A SINGLE-SUMMARY-LINE section: a named band carrying ONE
                         # aggregate value and NO per-row detail (no "Number"
                         # column header) -- e.g. "Complaints received   35". Render
                         # it as a one-line band (label left, count right), not a
                         # table. Precise across the corpus (only fires on these).
                         "summary_line": bool(_hdr) and not _ch and _agg})

    # Post-pass: surface an orphan footer-only summary frame (label + CS_/Sum/CF_
    # aggregate, no group-frame wrapper) the main loop missed -- ASBESTOS's
    # "Enforcement Cases Ongoing". Mirrors rdl._detect_multi_section's post-pass.
    _existing = {(s["header"] or "").strip().lower() for s in sections}

    def _partner_src(parent, fy):
        best = None
        for c in (parent.children or []):
            if (c.kind or "").lower() == "repeating_frame" and getattr(
                    c, "source_query", None):
                col = next((f.source for f in (c.fields or [])
                            if (f.kind or "") == "field" and (f.source or "")),
                           None)
                cy = float(getattr(c, "y", 0.0) or 0.0)
                if col and (best is None or abs(cy - fy) < best[0]):
                    best = (abs(cy - fy), c.source_query, col)
        return (best[1], best[2]) if best else (None, None)

    def _scan_extra(g, in_rep):
        ir = in_rep or (g.kind or "").lower() == "repeating_frame"
        for c in (g.children or []):
            ck = (c.kind or "").lower()
            if "frame" in ck and ck != "repeating_frame" and not ir:
                label, has_agg = "", False
                for f in (c.fields or []):
                    if (f.kind or "") == "text":
                        t = (f.text or "").strip()
                        if (t and "&<" not in t and not t.lower().endswith(".rdf")
                                and "total" not in t.lower()
                                and "number" not in t.lower()):
                            label = label or t.split("\n")[0].strip()
                    elif (f.kind or "") == "field" and re.match(
                            r"(?i)^(cs_|sum|cf_)", (f.source or "").strip()):
                        has_agg = True
                if label and has_agg and label.lower() not in _existing:
                    fy = float(getattr(c, "y", 0.0) or 0.0)
                    _src, _col = _partner_src(g, fy)
                    if _src:
                        _existing.add(label.lower())
                        distinct.add(_src.upper())
                        sections.append({
                            "header": label, "header_src": None,
                            "tables": [(_src, [_col])], "totals": [],
                            "col_headers": [], "has_total": True,
                            "summary_line": True, "_y": fy})
            _scan_extra(c, ir)

    _scan_extra(sm, False)
    sections.sort(key=lambda s: s.get("_y", 0.0))
    if len(sections) < 2 or len(distinct) < 2:
        return None
    return sections


def _clean_section_label(src):
    """A readable, DISTINCT section title derived from the grouping field's NAME
    when the real title is a formula (no static text), so 9 sections don't all
    show one repeated sample value. Strips CF_/CS_/CP_ prefixes and a trailing
    _Group/_Type, title-cases the rest. Structural -- never a data value."""
    s = re.sub(r"^(CF_|CS_|CP_|C_)", "", (src or ""), flags=re.IGNORECASE)
    s = re.sub(r"_(Group|Grp|Type|Tye)$", "", s, flags=re.IGNORECASE)
    s = re.sub(r"_+", " ", s).strip()
    return s.title()


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
    # Selection-criteria echo (Oracle repeating top-left margin, e.g.
    # "Start Date:" / "End Date:" param block). Mirrors the RDL page-header echo
    # so both views show the criteria the run covers. Lazy-import the shared
    # detector from the RDL generator (dodges a circular import).
    echo_html = ""
    try:
        from converter.generators.rdl import _leading_param_echo as _lpe
        for i, pr in enumerate(_lpe(report) or []):
            echo_html += (
                '<div style="margin-bottom:2px;"><span style="font-weight:bold;">'
                + _esc(pr["label"]) + '</span>&nbsp;<span style="font-weight:normal;">'
                + _esc(_sample_for_source("date", i)) + '</span></div>'
            )
    except Exception:  # noqa: BLE001 -- echo is cosmetic; never break the preview
        echo_html = ""
    head += (
        '<div style="display:flex; justify-content:space-between; '
        'align-items:flex-start; margin:10px 0 12px; font-size:12px; color:'
        + _TAB_INK + ';"><div style="text-align:left;">' + echo_html + '</div>'
        '<div style="text-align:right;"><div>Report run on:&nbsp;'
        '<span style="font-weight:normal;">'
        + _esc(_sample_for_source("date", 0)) + ' 1:00 PM</span></div>'
        '<div style="font-style:italic; color:#000079;">' + _esc(page_label)
        + '</div></div></div>'
    )

    blocks = []
    for sec in sections:
        hdr = sec.get("header", "")
        _data_driven_hdr = False
        if not hdr and sec.get("header_src"):
            # No static section title (the real title is a formula) -- derive a
            # DISTINCT readable label from the grouping field's name so each
            # section reads with its own header instead of borrowing a column
            # header or repeating one sample value.
            hdr = _clean_section_label(sec["header_src"])
            _data_driven_hdr = True
        # A single-summary-line section (e.g. "Complaints received   35"): one
        # gray band carrying the label LEFT + its aggregate count RIGHT, with NO
        # detail table beneath. Render it and move on.
        if sec.get("summary_line"):
            _cnt = ""
            for (_src, _cols) in sec["tables"]:
                _num = [c for c in _cols
                        if re.match(r"^\$?-?[\d,]+(\.\d+)?$",
                                    _sample_for_source(c, 0) or "")]
                if _num:
                    _cnt = _sample_for_source(_num[-1], 7)
                    break
            if not _cnt:
                _cnt = _sample_for_source("count", 7)
            blocks.append(
                '<div style="background:' + _TAB_BAND_BG + '; color:' + _TAB_BAND_FG
                + '; font-weight:bold; font-size:12px; padding:5px 10px; '
                'margin-top:14px; display:flex; justify-content:space-between;">'
                '<span>' + _esc(hdr) + '</span><span>' + _esc(_cnt)
                + '</span></div>'
            )
            continue
        # The WIDEST table of the section drives the columns shown (a group
        # frame may expose a 1-col grouping table plus the real N-col detail).
        _wt = max(sec["tables"], key=lambda t: len(t[1]), default=None)
        _shown0 = (_wt[1][:6] if _wt else []) or ["Value"]
        # Right-align numeric columns (counts/amounts) so a stacked count table
        # reads like the Oracle output, where the Number column sits flush-right.
        _aligns0 = {c: ("right" if re.match(r"^\$?-?[\d,]+(\.\d+)?$",
                                             _sample_for_source(c, 0) or "")
                        else "left") for c in _shown0}
        _col_hdrs = sec.get("col_headers") or []
        # A 2-value-column accounting section (Applications / Fees): reorder so the
        # description/LABEL column is leftmost, then the count + fee value columns
        # (matches Oracle's layout and aligns the captions), and render the REAL
        # captions as the column-header row (label column blank). Gated on >=2
        # detected captions, so single-caption ("Number") sections are untouched.
        _multi_caps = list(_col_hdrs) if len(_col_hdrs) >= 2 else None
        if _multi_caps:
            _cnt = [c for c in _shown0 if re.search(r"(?i)(count|cnt|num)", c or "")]
            _fee = [c for c in _shown0 if re.search(r"(?i)(fee|amount)", c or "")]
            _lbl = [c for c in _shown0 if c not in _cnt and c not in _fee]
            _reord = _lbl + _cnt + _fee
            if sorted(_reord) == sorted(_shown0):
                _shown0 = _reord
            _aligns0 = dict(_aligns0)
            for _vc in _shown0[1:]:
                _aligns0[_vc] = "right"
        # When the Oracle header frame names exactly ONE value-column caption
        # (e.g. "Number"), that caption rides flush-right INSIDE the gray band
        # with NO separate column-header row -- matching the real stat report. A
        # single caption inherently means a single value column, so we gate on the
        # caption COUNT, not on auto-detecting the value sample as numeric (which
        # misses mis-typed source names like "Pemits"). Multi-value sections (e.g.
        # Applications / Fees, which expose >=2 captions or none) keep their row.
        _band_caption = _col_hdrs[0] if len(_col_hdrs) == 1 else ""
        # The value column under a single "Number" caption is the rightmost data
        # column; right-align it even when its sample wasn't auto-detected numeric.
        if _band_caption and len(_shown0) > 1:
            _aligns0 = dict(_aligns0)
            _aligns0[_shown0[-1]] = "right"
        if hdr or _band_caption:
            _cap = ('<span style="float:right; font-weight:bold;">'
                    + _esc(_band_caption) + '</span>') if _band_caption else ""
            blocks.append(
                '<div style="background:' + _TAB_BAND_BG + '; color:' + _TAB_BAND_FG
                + '; font-weight:bold; font-size:12px; padding:5px 10px; '
                'margin-top:14px;">' + _cap + _esc(hdr) + '</div>'
            )
        for (src, cols) in ([_wt] if _wt else []):
            shown = _shown0
            aligns = _aligns0
            # The value column sits under the "Number" band caption, so sample it
            # as a count even when its source NAME wasn't recognized as numeric
            # (e.g. the mis-typed "Pemits") -- so the column reads as numbers, not
            # "Sample Value A".
            _vcol = _shown0[-1] if (_band_caption and len(_shown0) > 1) else None

            def _cell(c, ri):
                return _sample_for_source("count" if c == _vcol else c, ri)
            # The "Number" caption now rides in the band, so the single-value
            # section needs no column-header row; other sections keep theirs.
            if _multi_caps:
                # Real Oracle captions ("Applications"/"Fees") over the value
                # columns; the leftmost (description) column gets no caption.
                _vc = shown[1:]
                _capmap = {c: (_multi_caps[j] if j < len(_multi_caps) else "")
                           for j, c in enumerate(_vc)}
                th = "".join(
                    '<th style="text-align:' + aligns[c] + '; font-size:11px; '
                    'padding:3px 8px; border-bottom:1px solid ' + _TAB_RULE_LIGHT
                    + '; color:' + _TAB_INK_SOFT + '; font-weight:bold;">'
                    + _esc(_capmap.get(c, "")) + '</th>'
                    for c in shown
                )
            elif _band_caption:
                th = ""
            else:
                th = "".join(
                    '<th style="text-align:' + aligns[c] + '; font-size:11px; padding:3px 8px; '
                    'border-bottom:1px solid ' + _TAB_RULE_LIGHT + '; color:'
                    + _TAB_INK_SOFT + ';">' + _esc(c.replace("_", " ")) + '</th>'
                    for c in shown
                )
            rows = ""
            for ri in range(2):
                tds = "".join(
                    '<td style="text-align:' + aligns[c] + '; font-size:11px; padding:3px 8px; '
                    'border-bottom:1px solid ' + _TAB_RULE_LIGHT + '; color:' + _TAB_INK + ';">'
                    + _esc(_cell(c, ri)) + '</td>'
                    for c in shown
                )
                bg = _TAB_PAPER if ri % 2 else _TAB_DETAIL_BG
                rows += '<tr style="background:' + bg + ';">' + tds + '</tr>'
            # Bold TOTAL row(s): an Oracle accounting section closes with its REAL
            # group-footer total label(s) (e.g. "Total Properties Closed" /
            # "Total Active Properties", or "Total Applications") under the numeric
            # column. When the section's Oracle layout exposes those labels, emit
            # one bold row PER label so the preview matches the real report; a
            # section with NO footer totals falls back to one generic "Subtotal"
            # (only when it has a numeric column). Mirrors the RDL section builder.
            _sec_totals = sec.get("totals") or []
            if not sec.get("has_total", True):
                _total_labels = []   # plain list section -> no total row
            elif _sec_totals:
                _total_labels = _sec_totals
            elif any(aligns[c] == "right" for c in shown):
                # An accounting break section (data-driven section title) closes
                # with "Total <section>"; a plain stat section keeps "Subtotal".
                _total_labels = [("Total " + hdr) if (_data_driven_hdr and hdr)
                                 else "Subtotal"]
            else:
                _total_labels = []
            for _li, _lbl in enumerate(_total_labels):
                sub_tds = ""
                for ci, c in enumerate(shown):
                    if ci == 0:
                        cell = _lbl
                    elif aligns[c] == "right":
                        cell = _cell(c, 7 + _li)
                    else:
                        cell = ""
                    sub_tds += (
                        '<td style="text-align:' + aligns[c] + '; font-weight:bold; '
                        'font-size:11px; padding:4px 8px; border-top:2px solid '
                        + _TAB_RULE_LIGHT + '; color:' + _TAB_INK + ';">'
                        + _esc(cell) + '</td>'
                    )
                rows += '<tr>' + sub_tds + '</tr>'
            _thead = ('<thead><tr>' + th + '</tr></thead>') if th else ''
            blocks.append(
                '<table style="width:100%; border-collapse:collapse; margin:2px 0 '
                '4px;">' + _thead + '<tbody>' + rows
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


def _detail_image_srcs(report):
    """UPPER-cased set of field sources that hold IMAGE data (logo/seal/blob).
    An image-bound field is never a DATA COLUMN -- including it in a detail band
    paints a sample-text cell where a picture belongs AND, when it sits a hair to
    the right of a real column (e.g. a logo field at x0.5 just after an address
    column at x0.26), squeezes that column to a sliver so its value explodes into
    a tall wrapped pile. Cached on the report. Structural -- no report names."""
    cache = getattr(report, "_nd_image_srcs", None)
    if cache is None:
        try:
            cache = {s.upper() for s in (_image_source_names(report) or set())}
        except Exception:
            cache = set()
        try:
            report._nd_image_srcs = cache
        except Exception:
            pass
    return cache


def _nd_detail_band(report):
    """The repeating-frame field row with the most distinct-x positions = the
    detail TABLE row; plus wrap fields just below. Returns (row, wrap, row_y)
    where row/wrap are [(source, x, y, w)]."""
    main = _find_section(report.layout or [], "section_main")
    if main is None:
        return [], [], None
    fields = []
    _img_srcs = _detail_image_srcs(report)

    def walk(g):
        for f in (g.fields or []):
            if (f.kind == "field" and f.source
                    and (f.source or "").upper() not in _img_srcs):
                fields.append((f.source, float(getattr(f, "x", 0.0) or 0.0),
                               float(getattr(f, "y", 0.0) or 0.0),
                               float(getattr(f, "width", 0.0) or 0.0)))
        for c in (g.children or []):
            walk(c)

    # Detail/master data lives INSIDE a repeating-frame child of section_main;
    # the section's OWN direct fields are page furniture (title, page number, a
    # full-width subtitle/criteria line, run date) that must never be mistaken
    # for a detail column. Walk only the sub-frames; fall back to the whole
    # section if a report puts its data fields straight in section_main.
    for c in (main.children or []):
        walk(c)
    if not fields:
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


def _nd_detail_band2(report):
    """A SECOND detail field-row stacked just below the primary band, sharing the
    SAME column x-grid -- an Oracle 2-rows-per-record layout where each logical
    record occupies two physical lines (e.g. Site Name / Contractor stacked in
    one column). Returns [(source, x, y, w)] sorted by x, or [] when the report
    is a normal one-line-per-record table. Structural, geometry-only."""
    main = _find_section(report.layout or [], "section_main")
    if main is None:
        return []
    fields = []
    _img_srcs = _detail_image_srcs(report)

    def walk(g):
        for f in (g.fields or []):
            if (f.kind == "field" and f.source
                    and (f.source or "").upper() not in _img_srcs):
                fields.append((f.source, float(getattr(f, "x", 0.0) or 0.0),
                               float(getattr(f, "y", 0.0) or 0.0),
                               float(getattr(f, "width", 0.0) or 0.0)))
        for c in (g.children or []):
            walk(c)

    for c in (main.children or []):
        walk(c)
    if not fields:
        return []
    row, _wrap, best_y = _nd_detail_band(report)
    if not row or best_y is None:
        return []
    row_xs = sorted({round(x, 1) for _s, x, _y, _w in row})
    from collections import defaultdict
    by_y = defaultdict(list)
    for s, x, y, w in fields:
        by_y[round(y, 2)].append((s, x, y, w))
    for y in sorted(by_y):
        if not (0 < (y - best_y) <= 0.45):
            continue
        band = by_y[y]
        xs = sorted({round(x, 1) for _s, x, _y, _w in band})
        if len(xs) < 2:
            continue
        # the second band must align with the primary columns (not a stray
        # single-cell wrap or a totals line at unrelated x positions)
        matched = sum(1 for xx in xs
                      if any(abs(xx - rx) <= 0.3 for rx in row_xs))
        if matched >= 2 and matched >= len(xs) * 0.6:
            return sorted(band, key=lambda z: z[1])
    return []


def _nd_nearest_label(label_geo, x, y, max_dy=0.18, max_dx=1.4):
    best, best_dx = None, 1e9
    for text, lx, ly, _bg in label_geo:
        if abs(ly - y) <= max_dy and lx <= x + 0.05:
            dx = x - lx
            if 0 <= dx <= max_dx and dx < best_dx:
                best_dx, best = dx, text
    # A printed caption can embed Oracle lexical refs (a group-header band
    # like "&COL_1 : &CS_COL_SITES SITE(S)", "&<PageNumber>"); resolve them
    # so no raw &TOKEN ever leaks into a band/column-header caption.
    return _resolve_tokens((best or "").strip().rstrip(":"), 0)


def _nd_header_label(label_geo, x, y):
    """The COLUMN-HEADER text sitting just ABOVE a detail field (same x band,
    within ~0.45in above). Distinct from _nd_nearest_label, which finds a
    same-row caption -- a detail table's column header is a label one band
    higher (e.g. a navy 'Individual Responsible for Action' header over the
    CF_PERFORM_BY column). Returns '' when there is no header label, so the
    caller falls back to the field's own caption."""
    best, best_d = None, 1e9
    for text, lx, ly, _bg in label_geo:
        # A group-count BAND caption ("<grp> : N SITE(S)") and a "(continued)"
        # marker sit in the y-band BETWEEN the real column-header strip and the
        # detail row -- they are NOT column headers (the band is painted
        # separately). Left in, they're the CLOSEST label above the column and
        # get mis-picked (MCP_ACTIVE_SITES lost "Location"/"Incident Dates" to
        # the site-count band + "(continued)"). Skip them, mirroring the RDL
        # col-header exclusion. The window is 0.55in (not 0.45) so the genuine
        # header one band higher than the count caption is still reachable.
        _t = (text or "").strip()
        if _t.lower() == "(continued)" or re.search(r"\(s\)\s*$", _t, re.I):
            continue
        if ly <= y + 0.02 and (y - ly) <= 0.55 and abs(lx - x) <= 0.55:
            d = abs(lx - x) + (y - ly)
            if d < best_d:
                best_d, best = d, text
    # Resolve any embedded Oracle lexical refs so a column header never
    # prints a raw &TOKEN (see _nd_nearest_label).
    return _resolve_tokens((best or "").strip().rstrip(":"), 0)


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
                # A printed caption can embed Oracle lexical refs in its text
                # (a group-header band like "&COL_1 : &CS_COL_SITES SITE(S)",
                # or "&<PageNumber>"). Resolve them to sample values / drop page
                # builtins so a raw &TOKEN never leaks into the band caption.
                return _resolve_tokens(geo_cap, 0)
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
    # Also admit the largest centered title (font-ranked) -- a report whose REAL
    # title is centered but NOT bold (e.g. a logsheet "Motor Vehicle County
    # Graveyard Logsheets for ...") would otherwise lose to a bold GROUP-HEADER
    # band lower on the page. Guard against a label / "Report Parameters" heading
    # (the reason this path historically avoided _find_title_text).
    _tf2 = _find_title_text(report)
    if _tf2 is not None:
        _t2 = (_tf2.text or "").strip()
        if (_t2 and not _t2.endswith(":") and "&<" not in _t2
                and not _PARAMS_HEADING_RE.match(_t2)):
            _title_cands.append(_tf2)
    if _title_cands:
        # Largest font wins (the title is the biggest type); ties broken by the
        # TOPMOST then longest -- so a same-size group header below the title
        # never outranks it.
        tf = max(_title_cands, key=lambda f: (
            int(f.font_size or 0), -(getattr(f, "y", 0) or 0),
            len((f.text or "").strip())))
        title_color = _normalize_color(_attr(tf, "color", ""), "#000080")
        title_lines = [_resolve_tokens(ln, 0).strip()
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
    # Use the master frame's GENUINE fill (e.g. a darkgreen band) as a colored
    # band. When NO genuine fill exists -- the master frame is plain or carries
    # only an Oracle design-time hint (pale pink/lavender that never prints) --
    # the real report shows the master as PLAIN bold text (a county/yard + status
    # header), so render plain rather than a FABRICATED green default band.
    band_bg = None
    if _outer is not None:
        oy = min((field_geo[it.name.upper()][1] for it in _outer.items
                  if field_geo.get((it.name or "").upper())), default=0.0)
        for _t, _lx, _ly, bg in label_geo:
            if (bg and abs(_ly - oy) <= 0.4
                    and not _is_design_fill(_normalize_color(bg, ""))):
                band_bg = bg
                break
    if _outer is None:
        band = ""
    elif band_bg:
        band = _render_caption_block(_outer, band_bg, "#fff")
    else:
        band = _render_caption_block(_outer, "#ffffff", "#111111", pad="2px 0")
    # A group whose master band is a COUNT CAPTION ("<grp> : N SITE(S)") instead
    # of data-field rows (MCP_ACTIVE_SITES) yields an EMPTY caption block (the
    # group key has no printed F_ field). Surface the Oracle group-band caption
    # -- the "(S)" count band in label_geo -- as the bold band line so the group
    # header isn't lost (it used to leak in via _nd_header_label as a bogus
    # column header; now excluded there). Gated to the no-data-field case, so a
    # genuine master card (METHACT's Complaint band) is untouched.
    if _outer is not None and not _group_field_rows(_outer):
        _gcap = ""
        for _t, _lx, _ly, _bg in sorted(label_geo, key=lambda z: (z[2], z[1])):
            _ts = (_t or "").strip()
            if re.search(r"\(s\)\s*$", _ts, re.I):
                _gcap = _resolve_tokens(_ts, sample_idx).strip()
                break
        if _gcap:
            band = ('<div style="font-weight:bold;font-size:12px;color:#111;'
                    'padding:3px 0 4px;">' + _esc(_gcap) + '</div>')
    # ---- middle-group cards (white) ----
    # A middle group whose fields are ALL internal keys (*_ID) or bare dates
    # (*_DATE/*_DT) is not a sub-master header -- the real report runs straight
    # from the master band into the detail header (verified: METHACT's spurious
    # "Status Date / Action History ID" line). Skip such cards (mirrors the RDL
    # builder); a genuine middle group with descriptive fields still renders.
    for _mg in _middles:
        _mrows = _group_field_rows(_mg)
        _msrcs = [s for _yk in _mrows for (s, _x, _y, _w) in _mrows[_yk]]
        if not any(s and not s.upper().endswith(("_ID", "_DATE", "_DT"))
                   for s in _msrcs):
            continue
        band += _render_caption_block(_mg, "#f3f3f3", "#111", pad="6px 14px", fs="11px")

    # ---- column header strip: each detail column's OWN label at its x ----
    total_w = 7.5
    def pct(x):
        return max(0.0, min(100.0, (x / total_w) * 100.0))
    # Navy column-header strip only when the source is themed; a plain report
    # gets plain black-on-white headers with a bottom rule (mirrors the RDL).
    _themed = _report_is_themed(report)
    _hfg = "#fff" if _themed else "#111"
    _hwrap = ("height:22px;background:#00008B;" if _themed
              else "height:22px;background:#ffffff;border-bottom:2px solid #444;")
    hdr_html = ""
    for hi, (s, hx, hy, hw) in enumerate(row):
        nxt = row[hi + 1][1] if hi + 1 < len(row) else total_w
        # Prefer the report's OWN column-header label sitting above the field
        # (the header strip's text), else the field's caption. Keeps the
        # detail header reading "Owner"/"Individual Responsible for Action"
        # rather than the raw CF_RESP_PARTY/CF_PERFORM_BY field names.
        col_cap = _nd_header_label(label_geo, hx, hy) or _cap_for(s, hx, hy)
        hdr_html += ('<div style="position:absolute;left:' + f"{pct(hx):.1f}" + '%;'
                     'width:' + f"{pct(nxt)-pct(hx):.1f}" + '%;color:' + _hfg + ';font-weight:bold;'
                     'font-size:11px;padding:3px 4px;">' + _esc(col_cap) + '</div>')
    hdr = ('<div style="position:relative;' + _hwrap + '">'
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


def _is_tabbrkleft(report):
    """A master-detail report whose MASTER (break) group fields sit on the SAME
    y-row as the detail columns -- Oracle's tabBrkLeft: the break group is the
    LEFT column(s) of ONE flat table, NOT a colored band above the detail
    (nested-MD style). The nested-MD band renderer fabricates a master band and
    collides the right-edge columns into a pile on a wide summary; a flat table
    is faithful. Structural, geometry-driven -- no report names."""
    if not _is_nested_master_detail_preview(report):
        return False
    row, _wrap, row_y = _nd_detail_band(report)
    if not row or row_y is None:
        return False
    main = None
    for q in (report.queries or []):
        if getattr(q, "groups", None):
            if main is None or len(q.items or []) > len(main.items or []):
                main = q
    if main is None or not main.groups:
        return False
    field_geo, _lg = _nd_geometry(report)
    outer_ys = [field_geo[(it.name or "").upper()][1]
                for it in (main.groups[0].items or [])
                if field_geo.get((it.name or "").upper())]
    if not outer_ys:
        return False
    # the master's own data field sits ON the detail row -> one flat line
    return min(abs(oy - row_y) for oy in outer_ys) <= 0.25


def _render_tabbrkleft_page(report, page_num, total_pages):
    """One flat table page for a tabBrkLeft report: a single navy column-header
    strip spanning master + detail columns (left-to-right by their real x), and
    sample rows -- no fabricated master band, no column collision."""
    field_geo, label_geo = _nd_geometry(report)
    row, _wrap, _row_y = _nd_detail_band(report)

    title_lines = [report.name or "Report"]
    title_color = "#000080"
    _cands = []
    def _ttx(g):
        for f in (g.fields or []):
            t = (f.text or "").strip()
            if (f.kind == "text" and getattr(f, "bold", False) and t
                    and "&<" not in t and len(re.sub(r"\s+", "", t)) >= 20):
                _cands.append(f)
        for c in (g.children or []):
            _ttx(c)
    for g in (report.layout or []):
        if "section" in (g.kind or "").lower():
            _ttx(g)
    if _cands:
        tf = max(_cands, key=lambda f: len((f.text or "").strip()))
        title_color = _normalize_color(_attr(tf, "color", ""), "#000080")
        title_lines = [_resolve_tokens(ln, 0).strip()
                       for ln in (tf.text or "").splitlines() if ln.strip()][:3] or title_lines
    head = ""
    for ln in title_lines:
        head += ('<div style="font-family:' + _ACTIVE_TITLE_FONT + ';font-size:13px;'
                 'font-weight:bold;color:' + title_color + ';text-align:center;'
                 'letter-spacing:0.4px;line-height:1.4;">' + _esc(ln) + '</div>')
    head += ('<div style="display:flex;justify-content:space-between;'
             'align-items:baseline;margin:8px 0 6px;font-size:12px;color:#111;">'
             '<div>Report run on:&nbsp;<span style="font-weight:normal;">'
             + _esc(_sample_for_source("date", 0)) + ' 1:00 PM</span></div>'
             '<div style="font-style:italic;color:#000079;">Page ' + str(page_num)
             + ' of ' + str(total_pages) + '</div></div>')

    # An Oracle 2-rows-per-record table stacks a second field band (same column
    # x-grid) under the first -- and the column-header strip is likewise two
    # stacked label rows. Render both lines per record (zebra-striped) so all
    # columns show, instead of dropping the lower band.
    row2 = _nd_detail_band2(report)

    total_w = max((x + (w or 0.0) for _s, x, _y, w in (row + row2)),
                  default=7.5) + 0.15
    def pct(x):
        return max(0.0, min(100.0, (x / total_w) * 100.0))

    # Navy column headers only when the source is themed; plain reports get
    # plain black-on-white headers with a bottom rule (mirrors the RDL).
    _themed = _report_is_themed(report)
    _hfg = "#fff" if _themed else "#111"
    _hbg = "#00008B" if _themed else "#ffffff"
    _hb = "" if _themed else "border-bottom:2px solid #444;"
    if not row2:
        hdr = ""
        for hi, (s, hx, hy, _hw) in enumerate(row):
            nxt = row[hi + 1][1] if hi + 1 < len(row) else total_w
            cap = (_nd_header_label(label_geo, hx, hy)
                   or _nd_nearest_label(label_geo, hx, hy)
                   or (s or "").replace("_", " ").title())
            hdr += ('<div style="position:absolute;left:' + f"{pct(hx):.1f}" + '%;width:'
                    + f"{pct(nxt) - pct(hx):.1f}" + '%;color:' + _hfg + ';font-weight:bold;'
                    'font-size:11px;padding:3px 4px;overflow:hidden;white-space:nowrap;'
                    'text-overflow:ellipsis;">' + _esc(cap) + '</div>')
        hdr = ('<div style="position:relative;height:22px;background:' + _hbg + ';'
               + _hb + '">' + hdr + '</div>')
        det = ""
        for ri in range(3):
            cells = ""
            for ci, (s, x, _y, _w) in enumerate(row):
                nxt = row[ci + 1][1] if ci + 1 < len(row) else total_w
                v = _sample_for_source(s, ri)
                cells += ('<div style="position:absolute;left:' + f"{pct(x):.1f}" + '%;width:'
                          + f"{pct(nxt) - pct(x):.1f}" + '%;font-size:11px;padding:3px 4px;'
                          'color:#111;overflow:hidden;white-space:nowrap;'
                          'text-overflow:ellipsis;">' + _esc(v) + '</div>')
            det += ('<div style="position:relative;height:22px;border-bottom:1px solid #ccc;">'
                    + cells + '</div>')
        return _render_page(head + hdr + det, label="Page " + str(page_num))

    # --- two-rows-per-record path ---
    # The header strip has its OWN two label bands above the detail; pair them in
    # vertical order with the two detail rows (band 0 -> primary, band 1 ->
    # secondary) so a stacked column isn't mislabeled by the nearer band.
    from collections import defaultdict as _dd
    _hbx = _dd(set)
    _hbt = _dd(list)
    for t, lx, ly, _bg in label_geo:
        if 0 < (_row_y - ly) <= 0.7:
            _hbx[round(ly, 2)].add(round(lx, 1))
            _hbt[round(ly, 2)].append((t, lx, ly))
    header_bands = sorted(y for y, xs in _hbx.items() if len(xs) >= 2)

    def _hdr_at(x, band_y):
        best, bd = None, 1e9
        if band_y is None:
            return ""
        for t, lx, _ly in _hbt.get(band_y, []):
            d = abs(lx - x)
            if d <= 0.9 and d < bd:
                bd, best = d, t
        return _resolve_tokens((best or "").strip().rstrip(":"), 0)

    lines_spec = [(row, header_bands[0] if header_bands else None)]
    lines_spec.append((row2, header_bands[1] if len(header_bands) > 1 else None))

    def _line(cells_html):
        return ('<div style="position:relative;height:18px;">' + cells_html
                + '</div>')

    hdr_inner = ""
    for cols, band_y in lines_spec:
        line = ""
        for hi, (s, hx, _hy, _hw) in enumerate(cols):
            nxt = cols[hi + 1][1] if hi + 1 < len(cols) else total_w
            cap = _hdr_at(hx, band_y) or (s or "").replace("_", " ").title()
            line += ('<div style="position:absolute;left:' + f"{pct(hx):.1f}" + '%;width:'
                     + f"{pct(nxt) - pct(hx):.1f}" + '%;color:' + _hfg + ';font-weight:bold;'
                     'font-size:11px;padding:1px 4px;overflow:hidden;white-space:nowrap;'
                     'text-overflow:ellipsis;">' + _esc(cap) + '</div>')
        hdr_inner += _line(line)
    hdr = ('<div style="background:' + _hbg + ';' + _hb + 'padding:3px 0;">'
           + hdr_inner + '</div>')

    det = ""
    for ri in range(4):
        bg = "#f2f2f2" if (ri % 2 == 1) else "#ffffff"
        rec = ""
        for cols, _band_y in lines_spec:
            cells = ""
            for ci, (s, x, _y, _w) in enumerate(cols):
                nxt = cols[ci + 1][1] if ci + 1 < len(cols) else total_w
                v = _sample_for_source(s, ri)
                cells += ('<div style="position:absolute;left:' + f"{pct(x):.1f}" + '%;width:'
                          + f"{pct(nxt) - pct(x):.1f}" + '%;font-size:11px;padding:1px 4px;'
                          'color:#111;overflow:hidden;white-space:nowrap;'
                          'text-overflow:ellipsis;">' + _esc(v) + '</div>')
            rec += _line(cells)
        det += ('<div style="background:' + bg + ';border-bottom:1px solid #ccc;">'
                + rec + '</div>')
    return _render_page(head + hdr + det, label="Page " + str(page_num))


def _render_tabbrkleft_pages(report):
    return _render_pages_wrapper(
        [_render_tabbrkleft_page(report, 1, 1)])


def _grouped_tabular_spec_mock(report):
    """Lazy-import the RDL's grouped-tabular extractor so the mockup and the RDL
    agree EXACTLY on the group header / columns / footer-total structure (a lazy
    import avoids the rdl<->html_mockup module cycle)."""
    try:
        from converter.generators.rdl import _grouped_tabular_spec
        return _grouped_tabular_spec(report)
    except Exception:  # noqa: BLE001 -- preview must never crash on a probe
        return None


def _grouped_title_lines(report):
    """The centered report title (largest type), guarded against a parameter
    heading -- shared shape with the nested-MD page's title picker."""
    title_lines = [report.name or "Report"]
    title_color = "#000080"
    cands = []

    def _ttx(g):
        for f in (g.fields or []):
            t = (f.text or "").strip()
            if (f.kind == "text" and getattr(f, "bold", False) and t
                    and "&<" not in t and len(re.sub(r"\s+", "", t)) >= 20):
                cands.append(f)
        for c in (g.children or []):
            _ttx(c)
    for g in (report.layout or []):
        if "section" in (g.kind or "").lower():
            _ttx(g)
    tf2 = _find_title_text(report)
    if tf2 is not None:
        t2 = (tf2.text or "").strip()
        if (t2 and not t2.endswith(":") and "&<" not in t2
                and not _PARAMS_HEADING_RE.match(t2)):
            cands.append(tf2)
    if cands:
        tf = max(cands, key=lambda f: (int(f.font_size or 0),
                                       -(getattr(f, "y", 0) or 0),
                                       len((f.text or "").strip())))
        title_color = _normalize_color(_attr(tf, "color", ""), "#000080")
        title_lines = [_resolve_tokens(ln, 0).strip()
                       for ln in (tf.text or "").splitlines() if ln.strip()][:3] or title_lines
    return title_lines, title_color


def _render_grouped_tabular_subtotal_page(report, spec, page_num, total_pages):
    """One page of a 2-level GROUPED TABULAR report with per-group subtotals:
    for each break group -- a plain group-header line (break-key caption + a
    right-aligned Status), the column-header strip, sample detail rows, then the
    right-aligned group-footer TOTALS stack (FY-range subtotal, then the
    Junk/-Crushed/=In-Yards lines). Geometry-driven from _grouped_tabular_spec
    so it matches the generated RDL's grouped Tablix."""
    total_w = 7.5
    for _x, _w, _s in spec["detail_cols"]:
        total_w = max(total_w, _x + (_w or 0.0) + 0.1)

    def pct(x):
        return max(0.0, min(100.0, (x / total_w) * 100.0))

    title_lines, title_color = _grouped_title_lines(report)
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

    _themed = _report_is_themed(report)
    _hfg = "#fff" if _themed else "#111"
    _hwrap = ("height:20px;background:#00008B;" if _themed
              else "height:20px;background:#ffffff;border-bottom:2px solid #444;")
    cols = spec["col_headers"]
    dcols = spec["detail_cols"]
    ghdr = spec["group_header"]
    footers = spec["footers"]

    def _group_block(gi):
        # --- group header line: break-key caption (left) + Status (right) ---
        gh = ""
        for k, val, x, _w in ghdr:
            if k == "text":
                txt = _resolve_tokens(val, gi)
            else:
                txt = _sample_for_source(val, gi)
            gh += ('<div style="position:absolute;left:' + f"{pct(x):.1f}" + '%;'
                   'font-size:12px;font-weight:bold;color:#111;white-space:nowrap;">'
                   + _esc(txt) + '</div>')
        block = ('<div style="position:relative;height:20px;margin-top:'
                 + ('14px' if gi else '2px') + ';border-bottom:1px solid #000;">'
                 + gh + '</div>')
        # --- column header strip ---
        hdr = ""
        for hi, (hx, label) in enumerate(cols):
            nxt = cols[hi + 1][0] if hi + 1 < len(cols) else total_w
            hdr += ('<div style="position:absolute;left:' + f"{pct(hx):.1f}" + '%;width:'
                    + f"{pct(nxt) - pct(hx):.1f}" + '%;color:' + _hfg + ';font-weight:bold;'
                    'font-size:11px;padding:3px 4px;overflow:hidden;white-space:nowrap;">'
                    + _esc(label) + '</div>')
        block += ('<div style="position:relative;' + _hwrap + '">' + hdr + '</div>')

        # A detail cell that is a fiscal-year / count column reads as a year /
        # number (Oracle prints "1993" / "176"), not a generic "Sample Value".
        def _dval(src, label, idx):
            key = ((src or "") + " " + (label or "")).lower()
            if "fiscal" in key or label.upper() in ("FY", "YEAR", "YR"):
                return str(1990 + (idx % 12))
            return _sample_for_source(src, idx)

        # --- sample detail rows ---
        for ri in range(3):
            cells = ""
            for ci, (x, _w, s) in enumerate(dcols):
                nxt = dcols[ci + 1][0] if ci + 1 < len(dcols) else total_w
                lbl = cols[ci][1] if ci < len(cols) else ""
                v = _dval(s, lbl, gi * 3 + ri)
                cells += ('<div style="position:absolute;left:' + f"{pct(x):.1f}" + '%;width:'
                          + f"{pct(nxt) - pct(x):.1f}" + '%;font-size:11px;padding:2px 4px;'
                          'color:#111;overflow:hidden;white-space:nowrap;'
                          'text-overflow:ellipsis;box-sizing:border-box;">' + _esc(v) + '</div>')
            block += ('<div style="position:relative;height:19px;border-bottom:1px solid #eee;">'
                      + cells + '</div>')
        # --- group-footer totals stack (each line: label(s) + right value) ---
        for line in footers:
            if not line:
                continue
            vk, vval, vx, _vw = line[-1]   # rightmost field = the total VALUE
            ftr = ""
            for k, val, x, _w in line[:-1]:
                txt = _resolve_tokens(val, gi) if k == "text" else _sample_for_source(val, gi)
                ftr += ('<div style="position:absolute;left:' + f"{pct(x):.1f}" + '%;'
                        'font-size:11px;color:#111;white-space:nowrap;">' + _esc(txt) + '</div>')
            # The total itself is always a number/count (matches the source CS_/
            # CF_/Sum aggregate), never a generic text sample.
            vtxt = (_resolve_tokens(vval, gi) if vk == "text"
                    else _sample_for_source("count", gi))
            ftr += ('<div style="position:absolute;left:' + f"{pct(vx):.1f}" + '%;width:'
                    + f"{pct(total_w) - pct(vx):.1f}" + '%;font-size:11px;font-weight:bold;'
                    'color:#111;text-align:right;padding-right:4px;border-top:1px solid #000;">'
                    + _esc(vtxt) + '</div>')
            block += ('<div style="position:relative;height:18px;">' + ftr + '</div>')
        return block

    body = head + _group_block(0) + _group_block(1)
    return _render_page(body, label="Page " + str(page_num))


def _render_grouped_tabular_subtotal_pages(report, spec):
    return _render_pages_wrapper(
        [_render_grouped_tabular_subtotal_page(report, spec, 1, 1)])


def _stacked_list_columns_mock(report):
    """Lazy-import the RDL's stacked-column extraction so the mockup and RDL
    agree EXACTLY on columns / stacked lines / header colors (a lazy import
    avoids the rdl<->html_mockup module cycle)."""
    try:
        from converter.generators.rdl import _stacked_list_columns
        return _stacked_list_columns(report)
    except Exception:  # noqa: BLE001 -- preview must never crash on a probe
        return None


def _render_stacked_list_pages(report, sl):
    """A flat tabular LIST whose record spans >=2 column-aligned STACKED lines
    (Permit/Permit-Dates | City/Type-of-Operation | Site & Alias/Permittee |
    Visited). Renders a stacked multi-line header band over zebra-striped
    records, each occupying N stacked lines -- mirrors _build_stacked_list_tablix
    so the preview matches the RDL. Geometry-driven, no report names."""
    cols = sl["columns"]
    headers = sl["headers"]
    n_lines = sl["n_lines"]
    total_w = (cols[-1]["x"] + 2.0) if cols else 9.0

    def pct(x):
        return max(0.0, min(100.0, (x / total_w) * 100.0))

    title_lines = [report.name or "Report"]
    title_color = "#000080"
    _cands = []

    def _ttx(g):
        for f in (g.fields or []):
            t = (f.text or "").strip()
            if (f.kind == "text" and getattr(f, "bold", False) and t
                    and "&<" not in t and len(re.sub(r"\s+", "", t)) >= 20):
                _cands.append(f)
        for c in (g.children or []):
            _ttx(c)
    for g in (report.layout or []):
        if "section" in (g.kind or "").lower():
            _ttx(g)
    if _cands:
        tf = max(_cands, key=lambda f: len((f.text or "").strip()))
        title_color = _normalize_color(_attr(tf, "color", ""), "#000080")
        title_lines = [_resolve_tokens(ln, 0).strip()
                       for ln in (tf.text or "").splitlines() if ln.strip()][:3] or title_lines
    head = ""
    for ln in title_lines:
        head += ('<div style="font-family:' + _ACTIVE_TITLE_FONT + ';font-size:13px;'
                 'font-weight:bold;color:' + title_color + ';text-align:center;'
                 'letter-spacing:0.4px;line-height:1.4;">' + _esc(ln) + '</div>')
    head += ('<div style="display:flex;justify-content:space-between;'
             'align-items:baseline;margin:8px 0 6px;font-size:12px;color:#111;">'
             '<div>Report run on:&nbsp;<span style="font-weight:normal;">'
             + _esc(_sample_for_source("date", 0)) + ' 1:00 PM</span></div>'
             '<div style="font-style:italic;color:#000079;">Page 1 of 1</div></div>')

    hfg = sl.get("header_fg", "#111111")
    hbg = sl.get("header_bg", "#ffffff")
    _plain = hbg.lower() in ("#ffffff", "#fff")
    hbord = "border-bottom:2px solid #444;" if _plain else ""

    def _line(cells_html):
        return '<div style="position:relative;height:18px;">' + cells_html + '</div>'

    hdr_inner = ""
    for band in headers:
        bx = sorted(band)
        line = ""
        for hi, (lx, label) in enumerate(bx):
            nxt = bx[hi + 1][0] if hi + 1 < len(bx) else (
                min((c["next"] for c in cols
                     if c["next"] is not None and c["next"] > lx), default=total_w))
            cap = _resolve_tokens((label or "").strip().rstrip(":"), 0)
            line += ('<div style="position:absolute;left:' + f"{pct(lx):.1f}" + '%;width:'
                     + f"{pct(nxt) - pct(lx):.1f}" + '%;color:' + hfg + ';font-weight:bold;'
                     'font-size:11px;padding:1px 4px;overflow:hidden;white-space:nowrap;'
                     'text-overflow:ellipsis;">' + _esc(cap) + '</div>')
        hdr_inner += _line(line)
    hdr = ('<div style="background:' + hbg + ';' + hbord + 'padding:3px 0;">'
           + hdr_inner + '</div>')

    det = ""
    for ri in range(3):
        bg = "#f2f2f2" if (ri % 2 == 1) else "#ffffff"
        rec = ""
        for li in range(n_lines):
            cells = ""
            for col in cols:
                if li >= len(col["lines"]):
                    continue
                kind, s = col["lines"][li]
                cx = col["x"]
                nxt = col["next"] if col["next"] is not None else total_w
                v = _resolve_tokens(s, ri) if kind == "text" else _sample_for_source(s, ri)
                cells += ('<div style="position:absolute;left:' + f"{pct(cx):.1f}" + '%;width:'
                          + f"{pct(nxt) - pct(cx):.1f}" + '%;font-size:11px;padding:1px 4px;'
                          'color:#111;overflow:hidden;white-space:nowrap;'
                          'text-overflow:ellipsis;">' + _esc(v) + '</div>')
            rec += _line(cells)
        det += ('<div style="background:' + bg + ';border-bottom:1px solid #ccc;">'
                + rec + '</div>')
    return _render_pages_wrapper([_render_page(head + hdr + det, label="Page 1")])


def _is_flat_tabular_list(report):
    """A plain single-group TABULAR LIST: one DOMINANT wide repeating DETAIL
    frame (x~0, the full body width) whose fields form a >=3-column row, under a
    column-header band of >=3 labels. The lone query group IS the detail (not a
    master), so the generic detail-page renderer wrongly treats the wide frame
    as a MASTER band and emits an empty navy card -- render it as a flat table
    instead (the same column-header-strip + rows engine tabBrkLeft uses).

    Structural, never keyed on a report name. Checked AFTER nested-MD and
    multi-section, so it only ever sees plain tabular reports."""
    main = _find_section(report.layout or [], "section_main")
    if main is None:
        return False
    if _is_nested_master_detail_preview(report):
        return False
    # Widest frame in the section ~= the body width (the parsed section node's
    # own width is often unset).
    widths = [float(getattr(n, "width", 0) or 0.0) for n in _iter_group(main)]
    max_w = max(widths) if widths else 0.0
    if max_w < 4.0:
        return False
    # A DOMINANT wide repeating detail frame at the left margin.
    dominant = any(
        (getattr(n, "kind", "") or "") == "repeating_frame"
        and float(getattr(n, "x", 0) or 0.0) < 0.6
        and float(getattr(n, "width", 0) or 0.0) >= 0.85 * max_w
        for n in _iter_group(main))
    if not dominant:
        return False
    row, _wrap, row_y = _nd_detail_band(report)
    if len(row) < 3 or len({round(x, 1) for _s, x, _y, _w in row}) < 3:
        return False
    # A column-header BAND: >=3 label texts in the row just ABOVE the detail row
    # (the navy header strip). Distinguishes a real grid from a labeled form.
    _fg, label_geo = _nd_geometry(report)
    hdr_labels = sum(1 for _t, _lx, ly, _bg in label_geo
                     if row_y is not None and -0.6 <= (row_y - ly) <= 0.55)
    return hdr_labels >= 3


def _render_nested_master_detail_pages(report):
    NUM = 2
    # A standalone page-1 cover ONLY when the report actually carries cover
    # content (a Parameter-Form criteria cover in section_header). A nested
    # master-detail whose title is just a repeating page-header -- e.g. one
    # with an empty <section name="header"> -- has NO cover; prepending the
    # Run-info / Report-Parameters template would invent a page (a fabricated
    # "Run By / Total of ALL Records") the real report never prints. The report
    # parameters are an INPUT prompt, not printed output, so they alone don't
    # justify a cover. Generic, structural -- mirrors _has_cover_page.
    pages = []
    if _has_cover_page(report):
        total = NUM + 1
        pages.append(_render_header_summary_page(report, page_label="Page 1 of %d" % total))
        first = 2
    else:
        total = NUM
        first = 1
    pages += [_render_nested_master_detail_page(report, i, first + i, total)
              for i in range(NUM)]
    return _render_pages_wrapper(pages)


def _render_tabular_pages(report):
    """Multi-page tabular: page 1 = header summary, pages 2-4 = detail pages.

    Multi-section dashboards (several independent tables down one page) render
    as a single dashboard page so the preview matches the multi-section RDL."""
    # Multi-section accounting dashboard wins over nested-MD: a report with >=2
    # sections from DISTINCT queries is a multi-section report, not one nested
    # master-detail. A genuine nested-MD has ONE query and is never
    # detected as multi-section, so this never steals it.
    _sections = _detect_multi_section_preview(report)
    if _sections:
        return _render_pages_wrapper([
            _render_multi_section_page(report, _sections, "Page 1 of 1")
        ])
    # Grouped TABULAR report with per-group subtotals (Oracle break report): an
    # outer break group with a header line + column-header band + detail rows +
    # a group-footer totals stack. The nested-MD CARD renderer keys the master
    # band off the query group's items, which -- for a 2-query master/detail --
    # have no printed geometry, so it emits an EMPTY band and drops the totals.
    # Render the real grouped layout instead. Gated tightly (see
    # _grouped_tabular_spec) so it never steals a card report.
    _gts = _grouped_tabular_spec_mock(report)
    if _gts is not None:
        return _render_grouped_tabular_subtotal_pages(report, _gts)
    if _is_nested_master_detail_preview(report):
        if _is_tabbrkleft(report):
            # Oracle tabBrkLeft: master = LEFT columns of one flat table, not a
            # band above the detail. Render flat (no fabricated band, no column
            # collision) instead of the group-band nested-MD page.
            return _render_tabbrkleft_pages(report)
        return _render_nested_master_detail_pages(report)
    if _is_flat_tabular_list(report):
        # A flat list whose record occupies >=2 column-aligned STACKED lines
        # (Oracle 2-line list: Permit/Permit-Dates | City/Type-of-Operation | ...)
        # -> the stacked renderer, sharing the RDL's _stacked_list_columns so the
        # preview matches the generated RDL exactly. Single-line lists fall
        # through to the flat tabBrkLeft renderer below.
        _sl = _stacked_list_columns_mock(report)
        if _sl is not None and _sl.get("n_lines", 1) >= 2:
            return _render_stacked_list_pages(report, _sl)
        # A plain single-group list whose wide detail frame the card renderer
        # would mistake for a master band (empty card). Render it flat.
        return _render_tabbrkleft_pages(report)
    NUM_DETAIL_PAGES = 3
    # A "Run Date / Run By / Total of ALL Records + Report Parameters" cover is
    # prepended ONLY when the report actually carries that cover content in its
    # section_header (a Parameter-Form criteria cover). A plain tabular list --
    # or a positional master-detail form -- has NO such page in the real Oracle
    # output: page 1 is immediately the data. Fabricating run-info there invents
    # values the report never prints, so gate it structurally (mirrors the
    # per-record-body and packet paths, which already gate on _has_cover_page).
    has_cover = _has_cover_page(report)
    total_pages = NUM_DETAIL_PAGES + (1 if has_cover else 0)
    pages = []
    if has_cover:
        pages.append(_render_header_summary_page(
            report, page_label="Page 1 of " + str(total_pages)))
    for i in range(NUM_DETAIL_PAGES):
        pages.append(_render_tabular_detail_page(
            report, sample_idx=i, page_num=len(pages) + 1, total_pages=total_pages,
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

    def _resolve_key(key):
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

    # &<FIELD> inline references (form-letter / mailing-label merge fields):
    # page builtins drop out, data fields interpolate a sample value -- so a
    # sentence reads "Dear Mr. Rivera" not "Dear Mr. &<FIRST_NAME>".
    def _angle(m):
        name = m.group(1).strip()
        return "" if _is_page_builtin(name) else _resolve_key(name)
    text = _ANGLE_TOKEN_RE.sub(_angle, text)
    return _TOKEN_RE.sub(lambda m: _resolve_key(m.group(1)), text)


def _decollide(elems):
    """Push overlapping positioned text/field elements down so they stack
    instead of piling on top of each other. Oracle Reports lets conditionally-
    shown fields share ONE design slot (only one prints at runtime, via format
    triggers) and elastic frames grow/reflow; a static preview would paint them
    all on top of each other. Only elements sharing nearly the same LEFT edge
    (vertically stacked, not side-by-side table columns) are reflowed, so table
    layouts are left untouched. Panels keep their positions. The LETTERHEAD
    image (the topmost image near the page top) also stays fixed -- but any
    OTHER image (an inline SIGNATURE graphic mid-document) flows with the text,
    so a tall body paragraph above it pushes it down instead of overlapping it
    (wild-corpus verified: an inspection letter's signature over the closing
    paragraph)."""
    # Letterhead = topmost image near the top; never moves (it's the masthead).
    _imgs = [e for e in elems if e.get("kind") == "image"]
    _letterhead = None
    if _imgs:
        _top = min(_imgs, key=lambda e: float(e.get("y", 0) or 0))
        if float(_top.get("y", 0) or 0) < 1.5:
            _letterhead = _top
    movable = [e for e in elems
               if e.get("kind") in ("text", "field")
               or (e.get("kind") == "image" and e is not _letterhead)]
    movable.sort(key=lambda e: (round(float(e.get("y", 0) or 0), 3),
                                round(float(e.get("x", 0) or 0), 3)))
    placed = []  # (x_left, y_top, y_bottom)
    # Seed with the fixed letterhead so text and inline images avoid its box.
    if _letterhead is not None:
        _lx = float(_letterhead.get("x", 0) or 0)
        _ly = float(_letterhead.get("y", 0) or 0)
        placed.append((_lx, _ly, _ly + (float(_letterhead.get("h", 0) or 0) or 0.2)))
    for e in movable:
        xl = float(e.get("x", 0) or 0)
        yt = float(e.get("y", 0) or 0)
        # Estimate RENDERED height: multi-line text (Oracle elastic frames /
        # concatenated formula paragraphs) is taller than its one-line design
        # slot, so trusting the small declared height would let the next field
        # overlap its wrapped content. An image uses its own declared height.
        if e.get("kind") == "image":
            h = float(e.get("h") or 0) or 0.3
        else:
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


def _frame_has_content(g) -> bool:
    """True if a frame (or its subtree) carries any field/text — i.e. it is
    real page content, not an empty grouping wrapper."""
    if [f for f in (getattr(g, "fields", None) or [])
            if (getattr(f, "text", "") or getattr(f, "source", "") or "").strip()]:
        return True
    return any(_frame_has_content(c) for c in (getattr(g, "children", None) or []))


def _is_footer_frame(g) -> bool:
    """A page footer band (e.g. M_*_Footer_G at the bottom of the sheet):
    attaches to every page, never its own page."""
    nm = (getattr(g, "name", "") or "").lower()
    if "footer" in nm:
        return True
    return float(getattr(g, "y", 0.0) or 0.0) >= 10.0 \
        and float(getattr(g, "height", 0.0) or 0.0) <= 0.6


def _section_page_groups(report, section="section_header"):
    """Split a header-resident section into one PAGE PER top-level content
    frame. Oracle packs several logical pages into one <section> — a criteria
    cover, a stat table (pageBreakBefore="yes"), and sometimes a repeating
    detail frame — all as sibling frames, some sharing y=0. y-banding can't
    separate two frames at the same y, so we split by FRAME IDENTITY: each
    top-level content frame becomes its own page (re-based to y=0), in
    document order. Footer frames are excluded (they repeat on every page).
    Returns a list of LayoutGroup roots; an empty list means 'render the
    whole section as one page' (the normal, unchanged path)."""
    sec = _find_section(report.layout or [], section)
    if sec is None:
        return []
    roots = [c for c in (sec.children or [])
             if not _is_footer_frame(c) and _frame_has_content(c)]
    # Only treat as multi-page when there is genuinely more than one content
    # frame (otherwise the single-page path is byte-identical to before).
    return roots if len(roots) > 1 else []


_TABLE_SAMPLE_ROWS = 12  # sample rows tiled for an embedded data table in a packet


def _doc_cell_value(source, row_idx, mask=""):
    """A table CELL shows the BARE sample value (no 'Caption:' prefix), varied
    per row so the grid reads like real data. A $/number mask or an amount-like
    column name renders as currency; a count-like column renders a small int."""
    u = (source or "").upper()
    if "$" in (mask or "") or re.search(r"(AMT|AMOUNT|TOTAL|FEE|COST|PRICE|\bPAY|RET_)", u):
        return "$" + format(100 * (row_idx + 1), ",d") + ".00"
    if u.startswith("NO_") or re.search(r"(\b|_)(NO_OF|COUNT|QTY|CNT|LIC)(\b|_|$)", u):
        return str((row_idx % 9) + 1)
    return _sample_for_source(source, row_idx)


def _is_summary_trailer_frame(fr):
    """A section_main top-level frame that is a REPORT-WIDE summary TRAILER: no
    repeating-frame descendant AND a field whose source is a report TOTAL
    (`..._TOTAL`). Such a frame (MVWFR's M_REPORT_SUMMARY_FTR count tables) prints
    ONCE at the report end, not on every per-record page. Mirrors rdl.py's
    identically-named gate (corpus scan: only MVWFR matches)."""
    if "repeating" in (getattr(fr, "kind", "") or "").lower():
        return False
    stack = list(getattr(fr, "children", None) or [])
    fields = list(getattr(fr, "fields", None) or [])
    while stack:
        c = stack.pop()
        if "repeating" in (getattr(c, "kind", "") or "").lower():
            return False
        fields += list(getattr(c, "fields", None) or [])
        stack.extend(getattr(c, "children", None) or [])
    return any(re.search(r"(?i)(^|_)total($|_)", (getattr(f, "source", "") or ""))
               for f in fields)


def _doc_collect_positioned(report, section="section_main", root=None,
                            tile_tables=False, lift_title=False,
                            skip_trailer=True, skip_repeating=False):
    """Walk the given section and return every positioned element as a flat
    list of dicts: {kind, text|source, x, y, w, h, bold, size, color, align,
    bg}. Geometry is absolute within the section. Generic: nothing
    report-specific. ``section`` is "section_main" for a per-record document,
    or "section_header" for a header-resident summary report's leading page.

    ``root`` restricts collection to ONE top-level frame's subtree (one
    physical page of a multi-page header section), re-basing y so the page
    starts at the top of its own sheet."""
    main = root if root is not None else _find_section(report.layout or [], section)
    if main is None:
        return [], 8.5, 11.0
    out = []
    # Columns that hold image data (blob/logo/signature) -- a field bound to one
    # is an image object, rendered as an image placeholder, not sample text.
    img_srcs = getattr(report, "_image_src_names", None)
    if img_srcs is None:
        img_srcs = _image_source_names(report)
        try:
            report._image_src_names = img_srcs
        except Exception:
            pass
    # Re-base AFTER collecting (below), using the real min-y of this frame's
    # content -- a frame's DECLARED y is unreliable (nested children can sit
    # above it, which produced negative y and upward bleed when we subtracted
    # the declared y directly).
    _ybase = 0.0
    _y0, _y1 = 0.0, float("inf")
    # Count columnar (table-shaped) repeating frames in this section. ONE = a
    # genuine line-item TABLE (invoice) -- tile it to the full sample length and
    # let the reflow below push any footer down after it. MORE THAN ONE = a
    # per-facility FORM whose several small sub-lists must each be clamped to the
    # space before the next block, else their 12-row samples stack into one
    # colliding pile (e.g. a facility form's SIC/NAIC + address/phone sub-lists).
    _n_columnar = sum(1 for _n in _iter_group(main) if _is_columnar_repeating(_n))

    def walk(g, frame_bg, y_bound=float("inf")):
        # A conditional ERROR/alert frame (format-trigger box with not-equal /
        # ERROR text) is hidden on the happy path -- skip its whole subtree so a
        # totals-mismatch warning never paints over the normal form/table.
        if _is_conditional_alert_frame(g):
            return
        # A REPORT-WIDE summary TRAILER frame (totals, no repeating descendant --
        # MVWFR's Application/MVWFR-Status count tables) prints ONCE at the report
        # end, on its OWN page, not on every per-record page. Skip it here; the
        # trailer page is rendered separately via root=<trailer>, skip_trailer=False.
        if skip_trailer and g is not main and _is_summary_trailer_frame(g):
            return
        # A CONDITIONAL grantee/site LIST repeating frame nested in a
        # header-summary stat table (CMVGY_GRANT_STATUS page 2: R_Budget_C /
        # R_Itemized_MI / R_Quarter_MQ3) prints ONLY when "Include Grantee and
        # Site Lists = YES"; the default summary suppresses it. The static
        # preview can't evaluate that flag, so tiling these frames piles an
        # extra "Sample Org One : ..." sub-row under every stat line. Skip the
        # repeating sub-frames so the stat table reads as the count/percent grid
        # it is. Mirrors the RDL's _emit_frame_rect(skip_repeating=True). Only
        # the section_header stat page sets this; the page-3 grid (built FROM the
        # repeating R_Org frame) never does.
        if skip_repeating and g is not main and "repeating" in (
                getattr(g, "kind", "") or "").lower():
            return
        gbg_raw = getattr(g, "background_color", "")
        # Oracle Reports frames carry pale design-time fill hints (light pinks/
        # lavenders like #FFE0FF, #FFBFFF) that are NOT meant to print -- the
        # real document is on white paper. Only paint a panel when the fill is a
        # genuine MEANINGFUL band (dark, e.g. the #3D3D3D invoice shading).
        gbg = gbg_raw if (gbg_raw and _is_dark(gbg_raw)) else ""
        bg = gbg or frame_bg
        _gy = float(g.y or 0)
        if gbg and (g.width or 0) > 0.2 and (g.height or 0) > 0.05:
            out.append({"kind": "panel", "x": float(g.x or 0),
                        "y": max(0.0, _gy - _ybase),
                        "w": float(g.width or 0), "h": float(g.height or 0),
                        "bg": gbg})
        # A columnar repeating frame is a DATA TABLE: Oracle prints it once per
        # record, so tile its single field-row into N sample rows in place (the
        # column headers live in a sibling frame just above). Without this an
        # embedded table collapses to one scattered row. Gated by tile_tables
        # so only the positional-document-packet path tiles -- letters and
        # certificates have no columnar repeating frame to tile.
        # A maxRecordsPerPage==1 frame is the per-record DOCUMENT CONTAINER (one
        # record per page), NOT a tileable leaf data table -- even when a couple
        # of its form fields happen to share a y-band (so _is_columnar_repeating
        # reads True). Tiling it would repeat the master fields into a colliding
        # pile AND the early `return` below would drop its nested sub-frames
        # (e.g. a form's responsible-party / site sub-lists). Let it fall through
        # to normal once-each field emission + child recursion so those real
        # sub-tables tile in their own right.
        if (tile_tables and _is_columnar_repeating(g)
                and int(getattr(g, "max_records_per_page", 0) or 0) != 1):
            flds = [f for f in (g.fields or []) if (getattr(f, "kind", "") or "") == "field"]
            if flds:
                rh = max((float(getattr(f, "height", 0) or 0.0) for f in flds),
                         default=0.18) or 0.18
                step = max(rh + 0.03, 0.2)
                # A lone line-item table tiles the full sample; a sub-list in a
                # multi-table FORM is clamped to the room before the next block.
                if _n_columnar > 1 and y_bound != float("inf"):
                    avail = max(0.0, y_bound - float(getattr(g, "y", 0) or 0.0))
                    n_rows = max(1, min(_TABLE_SAMPLE_ROWS, int(avail / step)))
                else:
                    n_rows = _TABLE_SAMPLE_ROWS
                for k in range(n_rows):
                    for f in flds:
                        out.append({
                            "kind": "cell", "source": getattr(f, "source", "") or "",
                            "row_idx": k,
                            "x": float(getattr(f, "x", 0.0) or 0.0),
                            "y": (float(getattr(f, "y", 0.0) or 0.0) - _ybase) + k * step,
                            "w": float(getattr(f, "width", 0.0) or 0.0), "h": rh,
                            "bold": bool(getattr(f, "bold", False)),
                            "size": int(getattr(f, "font_size", 0) or 9),
                            "color": _normalize_color(getattr(f, "color", "") or "", "#000000"),
                            "align": (getattr(f, "align", "") or "left").lower(),
                            "mask": getattr(f, "format_mask", "") or "",
                            "bg": bg})
            return  # tiled in place -- don't also emit this frame's single row
        for f in (g.fields or []):
            # Oracle visible="no" -> a computation-only field (a hidden CF_/CS_
            # statistic feeding a body &token); never drawn. Mirrors the RDL skip.
            if not getattr(f, "visible", True):
                continue
            x = float(getattr(f, "x", 0.0) or 0.0)
            y = float(getattr(f, "y", 0.0) or 0.0) - _ybase  # re-base to sheet top
            w = float(getattr(f, "width", 0.0) or 0.0)
            h = float(getattr(f, "height", 0.0) or 0.0)
            col = _normalize_color(getattr(f, "color", "") or "", "#000000")
            common = {"x": x, "y": y, "w": w, "h": h,
                      "bold": bool(getattr(f, "bold", False)),
                      "italic": bool(getattr(f, "italic", False)),
                      "underline": bool(getattr(f, "underline", False)),
                      "size": int(getattr(f, "font_size", 0) or 9),
                      "color": col,
                      "align": (getattr(f, "align", "") or "left").lower(),
                      # A field owned DIRECTLY by the section (not nested in a
                      # frame) is Oracle PAGE-MARGIN chrome -- the title band, a
                      # criteria/date banner, the header rule, the page number --
                      # printed in the margin ABOVE/BELOW the body, not interleaved
                      # with it. Tagged so the per-record form path can lift the
                      # top margin band clear of the record body.
                      "_margin": (g is main),
                      "rotation": float(getattr(f, "rotation", 0.0) or 0.0),
                      "bg": bg}
            if f.kind == "text":
                if _is_conditional_error_text(f.text or ""):
                    continue  # Oracle format-trigger error branch -- hidden at runtime
                t = _clean_text(_doc_resolve_tokens(f.text or "", report))
                if t:
                    out.append({"kind": "text", "text": t, **common})
            elif f.kind == "field":
                if _is_conditional_error_source(f.source or ""):
                    continue  # Oracle conditional *_ERROR field -- hidden at runtime
                # An Oracle boolean INDICATOR flag (source ends "_IND") is never
                # meaningful printed output -- it drives a conditional asterisk /
                # checkmark via a format trigger. Painting its raw 'Y'/'N' as a
                # positioned glyph just litters orphaned letters across the page
                # (a grant-status summary's floating 'Y' row). Skip it.
                # NOTE: rendering it as a "*" here puts the asterisks on the
                # header-summary STAT page (wrong page) instead of under the
                # page-3 grantee FY-grid -- needs the page-3 grid rebuilt first
                # (task #72), so it stays skipped until then.
                if (f.source or "").upper().endswith("_IND"):
                    continue
                # An image-data field (blob/logo/signature column) is an image
                # object, not text -- render it as an image (embedded or a
                # labelled placeholder box), never a 'Sample Value A'.
                if (f.source or "").upper() in img_srcs:
                    out.append({"kind": "image", "source": f.source or "", **common})
                else:
                    out.append({"kind": "field", "source": f.source or "", **common})
            elif f.kind == "image":
                out.append({"kind": "image", "source": f.source or f.image_id or "",
                            **common})
            elif f.kind in ("rect", "line"):
                # A DRAWN graphic: a bordered box around a panel, or a
                # horizontal/vertical rule. No data -- an outline (rect) or a
                # thin bar (line) at the Oracle geometry.
                out.append({
                    "kind": f.kind,
                    "border_width": float(getattr(f, "border_width", 0.0) or 0.0) or 1.0,
                    "border_color": _normalize_color(
                        getattr(f, "border_color", "") or "", "#000000"),
                    **common})
        # Bound each child sub-frame by the nearest sibling BELOW it that
        # overlaps its x-range (so a tiled inline sub-table stops before the
        # next column-aligned block), else by this frame's own bottom edge.
        _kids = list(g.children or [])
        _gh = float(getattr(g, "height", 0) or 0.0)
        _gbot = (_gy + _gh) if _gh > 0 else y_bound
        for c in _kids:
            _cy = float(getattr(c, "y", 0) or 0.0)
            _cx = float(getattr(c, "x", 0) or 0.0)
            _cw = float(getattr(c, "width", 0) or 0.0)
            _below = [float(getattr(s, "y", 0) or 0.0) for s in _kids
                      if s is not c
                      and (float(getattr(s, "y", 0) or 0.0) - _cy) > 0.05
                      and _cw > 0 and (float(getattr(s, "width", 0) or 0.0) > 0)
                      and (float(getattr(s, "x", 0) or 0.0) < _cx + _cw)
                      and (float(getattr(s, "x", 0) or 0.0)
                           + float(getattr(s, "width", 0) or 0.0) > _cx)]
            _cb = min(_below) if _below else _gbot
            walk(c, bg, _cb)

    walk(main, "")
    # Header-summary per-grantee GRID (CMVGY_GRANT_STATUS page 3): mirror the
    # RDL's _build_grantee_grid_tablix. The *_IND asterisk fields belong UNDER
    # the FY column headers (their x matches B_FY_* exactly, y on the grantee /
    # site rows), but the parser stores them in section_HEADER's reused
    # repeating frame (G_Budget), so the section_main walk never sees them --
    # and they're skipped as raw _IND flags anyway. Collect them from the whole
    # layout and paint a "*" at each one's real geometry, then frame the box
    # with a 1pt border = the section_main repeating frame. Gated to a
    # header-summary section_main page (root is None), so no other report is
    # touched; the _IND skip on the section_header stat page (page 2) stands.
    if (section == "section_main" and root is None
            and _is_header_summary_preview(report)):
        _seen_ind = set()
        for _sec in (report.layout or []):
            for _g in _iter_group(_sec):
                for _f in (getattr(_g, "fields", None) or []):
                    _s = (getattr(_f, "source", "") or "")
                    if (_s.upper().endswith("_IND")
                            and _s.upper() not in _seen_ind):
                        _seen_ind.add(_s.upper())
                        out.append({
                            "kind": "text", "text": "*",
                            "x": float(getattr(_f, "x", 0.0) or 0.0),
                            "y": float(getattr(_f, "y", 0.0) or 0.0),
                            "w": 0.40, "h": 0.34,
                            "bold": True, "italic": False, "underline": False,
                            "size": int(getattr(_f, "font_size", 0) or 20),
                            "color": _normalize_color(
                                getattr(_f, "color", "") or "", "#000000"),
                            "align": "center", "_margin": False,
                            "rotation": 0.0, "bg": ""})
        # 1pt box around the grid = the section_main repeating (per-grantee)
        # frame outline, the way Oracle draws it (truth image4).
        _rf = next((g for g in _iter_group(main)
                    if "repeating" in (getattr(g, "kind", "") or "").lower()),
                   None)
        if _rf is not None:
            _bw_rf = float(getattr(_rf, "width", 0.0) or 0.0) or 7.8
            _bh_rf = float(getattr(_rf, "height", 0.0) or 0.0) or 2.2
            out.append({
                "kind": "rect",
                "x": max(0.05, float(getattr(_rf, "x", 0.0) or 0.0)),
                "y": max(0.0, float(getattr(_rf, "y", 0.0) or 0.0)),
                "w": _bw_rf, "h": max(_bh_rf, 2.2),
                "border_width": 1.0, "border_color": "#000000",
                "bold": False, "italic": False, "underline": False,
                "size": 9, "color": "#000000", "align": "left",
                "_margin": False, "rotation": 0.0, "bg": ""})
    # Conditional-variant dedup: an Oracle letter often places the SAME body
    # paragraph in TWO positions -- a normal-flow copy plus a format-trigger
    # variant copy at the very top (above the letterhead) -- and shows only one
    # at runtime. A static preview paints both, piling the top copy over the
    # logo. Drop the duplicate, keeping the LATER (body-flow) instance. Only
    # long blocks (>=40 chars) so repeated short labels stay untouched.
    _by_text = {}
    for e in out:
        if e.get("kind") == "text":
            t = (e.get("text") or "").strip()
            if len(t) >= 40:
                _by_text.setdefault(t, []).append(e)
    _drop_ids = set()
    for _t, _grp in _by_text.items():
        if len(_grp) > 1:
            _grp.sort(key=lambda e: e["y"])
            for e in _grp[:-1]:
                _drop_ids.add(id(e))
    if _drop_ids:
        out = [e for e in out if id(e) not in _drop_ids]
    # Re-base to the real top of this frame's content when scoping to one
    # page-root (subtract the minimum y so the page starts at a small top
    # margin, never negative -- nested children can sit above the frame's
    # declared y).
    if root is not None and out:
        _min_y = min(e["y"] for e in out)
        _shift = _min_y - 0.25  # keep a 0.25in top margin
        if abs(_shift) > 1e-6:
            for e in out:
                e["y"] = max(0.0, e["y"] - _shift)
        # Collapse large empty vertical bands. Oracle container frames
        # "shrink to fit" at render time, so a header block designed at y=0
        # and a data block designed 4in lower print compactly together --
        # but our static geometry keeps the design gap, leaving a big blank
        # stripe (engine-verified on a header-summary frame). Close any gap
        # wider than 1.5in down to a normal 0.4in row gap. Conservative: a
        # >1.5in band with zero elements is unintended whitespace.
        ys = sorted(out, key=lambda e: e["y"])
        cursor = ys[0]["y"]
        shift_acc = 0.0
        for e in ys:
            top = e["y"] - shift_acc
            gap = top - cursor
            if gap > 1.5:
                shift_acc += gap - 0.4
                top = cursor + 0.4
            e["y"] = top
            cursor = max(cursor, top + (e["h"] or 0.2))
    # Tiled-table reflow: a columnar detail table tiled into N sample rows grows
    # far past its one-row design slot, so any fixed element positioned in the
    # rows the table now occupies (a Total footer, a separator line) would be
    # buried under the tiled grid. Push those elements DOWN to just below the
    # table so they read AFTER the line items -- the way Oracle's variable-length
    # table pushes following content down. Column headers (above the table) and
    # blocks well below it (signature / justification) are untouched. Only runs
    # when a table was actually tiled (cells present).
    _cells = [e for e in out if e.get("kind") == "cell"]
    if _cells:
        _row_h = max((e.get("h") or 0.18) for e in _cells)
        _ctop = min(e["y"] for e in _cells)
        _cbot = max(e["y"] + (e.get("h") or 0.18) for e in _cells)
        _growth = max(0.0, _cbot - (_ctop + _row_h))
        if _growth > 0.05:
            for e in out:
                if e.get("kind") == "cell":
                    continue
                if _ctop + 1e-6 < e["y"] < _cbot - 1e-6:
                    e["y"] += _growth
    # Page-margin lift (form path only): Oracle prints the report TITLE band, a
    # criteria/date banner, and the header rule in the page MARGIN -- a header
    # band ABOVE the body. When margin + body collapse into one coordinate space
    # those section-direct chrome fields land interleaved with the record body
    # (e.g. AIR's "Year of Emissions: YYYY" row + rule floating across the Plant
    # Location / Mailing Address blocks). Lift the TOP margin band (section-direct
    # fields in the upper region, keeping their own x + relative y) to the sheet
    # top and push the body down to clear it, so the page reads
    # title -> subtitle -> criteria banner -> rule -> record body like Oracle.
    if lift_title and out:
        hdr = [e for e in out if e.get("_margin") and e["y"] < 5.0]
        if hdr:
            h_top = min(e["y"] for e in hdr)
            _shift = 0.10 - h_top
            for e in hdr:
                e["y"] += _shift
            h_bot = max(e["y"] + (e.get("h") or 0.18) for e in hdr)
            body = [e for e in out if not (e.get("_margin") and e["y"] < 5.0)]
            if body:
                b_top = min(e["y"] for e in body)
                delta = (h_bot + 0.18) - b_top
                if delta > 0:
                    for e in body:
                        e["y"] += delta
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


def _humanize_report_title(name: str) -> str:
    """'MTR_GRANT_STATUS' -> 'MTR Grant Status'. Underscores become spaces; a
    pure-consonant all-caps token is treated as an acronym and kept (MTR, RPT),
    while a normal word is title-cased (GRANT -> Grant)."""
    out = []
    for tok in re.split(r"[_\s]+", (name or "").strip()):
        if not tok:
            continue
        is_acronym = tok.isupper() and not re.search(r"[AEIOU]", tok)
        out.append(tok if is_acronym else tok.capitalize())
    return " ".join(out)


def _image_source_names(report):
    """UPPER names of query columns that hold IMAGE data -- an Oracle blob /
    binLob / bfile column, or one whose name reads as an image (logo /
    signature / seal / photo / watermark). A layout field bound to one is an
    image object (a letterhead logo, a signature graphic), so the preview
    renders an image placeholder box, never a fabricated 'Sample Value A'
    text. Generic: keyed on the column's datatype + name, not a report name."""
    out = set()
    for q in (getattr(report, "queries", None) or []):
        for it in (getattr(q, "items", None) or []):
            dt = (getattr(it, "datatype", "") or "").lower()
            nm = (getattr(it, "name", "") or "")
            if dt in ("blob", "binlob", "bfile", "longraw", "long raw", "image", "binary"):
                out.add(nm.upper())
            # Name fallback for loosely-typed columns -- only UNAMBIGUOUS image
            # words, and never when the name carries a text/number/date suffix
            # (BADGE_NAME / SEAL_DATE / PHOTO_ID are NOT image blobs).
            elif (re.search(r"(logo|signature|watermark|emblem|letterhead|sig_img)",
                            nm, re.IGNORECASE)
                  and not re.search(r"(_name|_nbr|_num|_no|_id|_date|_dt|_desc|_cd|_code|_ind|_flag)$",
                                    nm, re.IGNORECASE)):
                out.add(nm.upper())
    return out


def _blank_formula_literals(report):
    """Map UPPER formula-name -> its constant return literal, for formulas that
    return ONLY a constant blank/whitespace string -- e.g. an Oracle CF_NULL
    that returns spaces to draw a blank signature/fill-in rule. Lets the
    preview render the real blank line (underlined spaces) instead of a
    fabricated 'Sample Value A'. Generic: reads the parsed PL/SQL body, never a
    report name."""
    out = {}
    for fc in (getattr(report, "formulas", None) or []):
        body = (getattr(fc, "plsql_body", "") or "")
        # Scan only the executable body (after BEGIN) so the function header
        # 'function X return Char is' isn't mistaken for a RETURN statement.
        m = re.search(r"\bbegin\b(.*)", body, re.IGNORECASE | re.DOTALL)
        scan = m.group(1) if m else body
        ret_stmts = re.findall(r"\breturn\s+([^;]+);", scan, re.IGNORECASE)

        def _blank_ret(r):
            r = r.strip()
            return (r.startswith("'") and r.endswith("'") and r[1:-1].strip() == "")

        if ret_stmts and all(_blank_ret(r) for r in ret_stmts):
            lit = ret_stmts[0].strip()[1:-1]
            out[(getattr(fc, "name", "") or "").upper()] = lit or "      "
    return out


def _static_render_formula_expr(report, src):
    """If a CF_/CP_ formula compiles (via the RDL token resolver) to a
    MULTI-LINE literal-bearing string -- Oracle boilerplate that concatenates
    fixed labels with parameter values and line breaks, e.g. a "Report Details"
    criteria summary (Display Summary Page = '...' / - Include ... / Display
    ...) -- statically render it to preview TEXT: the fixed labels kept verbatim,
    each Parameters!X.Value shown as the param's display default (else a generic
    sample), vbLf/vbCrLf as newlines. Returns None for a plain computed value
    (no string literals) or a single-line result, so the caller keeps its sample
    placeholder. Generic: reads the compiled expression, never a report name."""
    try:
        from converter.generators.rdl import _build_token_resolver
        resolver = _build_token_resolver(report)
        kind, expr, _note = resolver(src, "")
    except Exception:
        return None
    if kind != "formula" or not expr or not expr.startswith("=") or '"' not in expr:
        return None
    body = expr[1:]
    pdef = {}
    for p in (report.parameters or []):
        nm = (getattr(p, "name", "") or "")
        if nm:
            pdef[nm.upper()] = (getattr(p, "initial_value", "") or "").strip()
    out = []
    i, n = 0, len(body)
    while i < n:
        ch = body[i]
        if ch == '"':
            j = i + 1
            buf = []
            while j < n:
                if body[j] == '"' and j + 1 < n and body[j + 1] == '"':
                    buf.append('"')
                    j += 2
                    continue
                if body[j] == '"':
                    j += 1
                    break
                buf.append(body[j])
                j += 1
            out.append("".join(buf))
            i = j
            continue
        m = re.match(r"(?:Parameters!|Fields!)([A-Za-z0-9_]+)\.Value", body[i:])
        if m:
            val = pdef.get(m.group(1).upper())
            out.append(val if val else "Sample")
            i += m.end()
            continue
        m2 = re.match(r"vbCrLf|vbCr|vbLf|vbNewLine|Environment\.NewLine", body[i:])
        if m2:
            out.append("\n")
            i += m2.end()
            continue
        i += 1  # operators / parens / whitespace -- plumbing
    text = "".join(out).strip()
    # Only the genuinely MULTI-LINE boilerplate shape -- a single-line formula
    # value stays a sample so we don't second-guess ordinary computed fields.
    if "\n" not in text:
        return None
    return text


def _doc_field_caption_and_value(src, report, label_map, idx):
    """For a data field, return 'Caption: value' sample text. Uses the Oracle
    defaultLabel when present, else the field name; value from _sample_for_source.
    CF_/CP_ formula fields show a bracketed formula marker (they're computed)."""
    u = (src or "").upper()
    # A constant-blank formula (Oracle CF_NULL returning spaces) is a blank
    # signature/fill-in rule -- render its real whitespace (underlined into a
    # line), never a fabricated sample value. Memoized on the report.
    blanks = getattr(report, "_blank_formula_srcs", None)
    if blanks is None:
        blanks = _blank_formula_literals(report)
        try:
            report._blank_formula_srcs = blanks
        except Exception:
            pass
    if u in blanks:
        return blanks[u] or ""
    if u in ("CURRENTDATE", "CURRENT_DATE"):
        return _sample_for_source("date", idx)
    # A formula field named after the report (CP_<REPORTNAME> / CF_<REPORTNAME>)
    # is the report's TITLE formula -- show the report's own title, never a
    # keyword-matched sample (e.g. a CP_<REPORT>_GRANT_STATUS field must read as
    # the report name, not "Active" from a STATUS keyword match). Generic.
    rname = (getattr(report, "name", "") or "").upper()
    if rname and u.startswith(("CP_", "CF_")) and u[3:] == rname:
        return _humanize_report_title(report.name)
    if u.startswith(("CF_", "CP_")):
        # A MULTI-LINE boilerplate formula (e.g. a "Report Details" criteria
        # summary that concatenates fixed labels + parameter values) renders its
        # real label structure -- the same expression the RDL emits -- instead of
        # a single "Sample Value A" line, so preview and RDL agree. Plain
        # single-value formulas still fall through to a sample.
        _lit = _static_render_formula_expr(report, src)
        if _lit is not None:
            return _lit
        # PL/SQL-computed formula -> show a sample value (not a raw [CF_X]
        # token) so the sample-data preview reads as a finished document.
        return _sample_for_source(src, idx)
    if u.startswith(("P_", "PARM_")):
        # A display-constant parameter (a fixed Oracle initialValue, e.g. a
        # report title's division/agency sub-line bound to &P_DIVISION) shows
        # its REAL default value, not a fabricated sample -- mirrors the RDL's
        # display-constant default so preview and RDL agree.
        for p in (report.parameters or []):
            if (getattr(p, "name", "") or "").upper() == u:
                iv = (getattr(p, "initial_value", "") or "").strip()
                if iv:
                    return iv
                break
        return _sample_for_source(src, idx)
    return _sample_for_source(src, idx)


def _render_generic_document_page(report, idx, page_num, total_pages,
                                  section="section_main", root=None,
                                  tile_tables=False, lift_title=False,
                                  skip_trailer=True, skip_repeating=False):
    """Paint a section's actual frames/texts/fields at their real positions.
    This is the GENERAL geometry-driven renderer -- it shows whatever the
    report contains (letterhead, address block, body, signature, invoice, or a
    header-resident summary/criteria table), never hardcoded sample content.
    ``root`` restricts to one top-level frame when a section packs several."""
    elems, pw, ph = _doc_collect_positioned(report, section, root=root,
                                            tile_tables=tile_tables, lift_title=lift_title,
                                            skip_trailer=skip_trailer,
                                            skip_repeating=skip_repeating)
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
    # drawn graphics (boxes / rules) -- behind text, above panels
    for e in elems:
        if e["kind"] not in ("rect", "line"):
            continue
        bw = e.get("border_width", 1.0) or 1.0
        bcol = e.get("border_color", "#000000") or "#000000"
        ew = e.get("w", 0) or 0
        eh = e.get("h", 0) or 0
        gx = px(e["x"]); gy = px(e["y"])
        if e["kind"] == "rect" and ew > 0.05 and eh > 0.05:
            # a box: an outline with a transparent interior (frames its text)
            parts.append(
                '<div style="position:absolute;left:' + gx + ';top:' + gy
                + ';width:' + px(ew) + ';height:' + px(eh) + ';border:'
                + f"{max(1.0, bw):.0f}px solid " + _esc(bcol)
                + ';box-sizing:border-box;"></div>')
        elif eh > ew:
            # a vertical rule: line-thin, full declared height
            parts.append(
                '<div style="position:absolute;left:' + gx + ';top:' + gy
                + ';width:' + f"{max(1.0, bw):.0f}px" + ';height:' + px(max(0.02, eh))
                + ';background:' + _esc(bcol) + ';"></div>')
        else:
            # a horizontal rule: full declared (or page) width, line-thick
            gw = px(max(0.4, ew)) if ew > 0 else px(max(0.4, pw - e["x"] - 0.1))
            parts.append(
                '<div style="position:absolute;left:' + gx + ';top:' + gy
                + ';width:' + gw + ';height:' + f"{max(1.0, bw):.0f}px"
                + ';background:' + _esc(bcol) + ';"></div>')
    # then text/fields/images
    for e in elems:
        if e["kind"] == "panel":
            continue
        left = px(e["x"]); top = px(e["y"])
        w = e["w"] if e["w"] > 0 else (pw - e["x"] - 0.1)
        width = px(max(0.4, w))
        # Oracle rotationAngle ~270deg (a sideways window-envelope address):
        # lay the text out along the box's LONG (height) axis, then rotate -90deg
        # about the top-left corner so it reads bottom-to-top and lands back in
        # the tall box. Mirrors the RDL's WritingMode=Rotate270.
        _rot = float(e.get("rotation", 0.0) or 0.0)
        rot_css = ""
        if 247.5 <= _rot < 292.5:
            _bh = e["h"] if e["h"] > 0.05 else 1.0
            width = px(max(0.4, _bh))
            rot_css = ("transform-origin:0 0;transform:translateY(" + px(_bh)
                       + ") rotate(-90deg);")
        align = {"start": "left", "end": "right", "centre": "center"}.get(e["align"], e["align"])
        if align not in ("left", "right", "center"):
            align = "left"
        # white text on dark panels
        fg = e["color"]
        bg = e.get("bg", "")
        if bg and _is_dark(bg) and fg.lower() in ("#000000", "#111111", "#000"):
            fg = "#ffffff"
        _deco = []
        if e.get("underline"):
            _deco.append("underline")
        style = ("position:absolute;left:" + left + ";top:" + top + ";width:" + width
                 + ";font-size:" + str(max(7, min(28, e["size"]))) + "px;"
                 + ("font-weight:bold;" if e["bold"] else "")
                 + ("font-style:italic;" if e.get("italic") else "")
                 + ("text-decoration:underline;" if _deco else "")
                 + "color:" + fg + ";text-align:" + align + ";line-height:1.25;"
                 + rot_css)
        # A bound FIELD/CELL is single-line data in a fixed-width Oracle box: it
        # CLIPS overflow, it does not reflow into the block below. Wrapping a
        # too-long sample (e.g. the generic "Sample Value A" in a ~0.5in SIC code
        # column) inflated the row and collided with the next labeled block.
        # nowrap+ellipsis mirrors the real positioned field. A TEXT label/note
        # may genuinely span lines (addresses, comment paragraphs) -> keep wrap.
        field_css = "white-space:nowrap;overflow:hidden;text-overflow:ellipsis;"
        text_css = "white-space:pre-wrap;"
        if e["kind"] == "text":
            parts.append('<div style="' + style + text_css + '">' + _esc(e["text"]) + '</div>')
        elif e["kind"] == "cell":
            val = _doc_cell_value(e.get("source", ""), e.get("row_idx", 0), e.get("mask", ""))
            parts.append('<div style="' + style + field_css + '">' + _esc(val) + '</div>')
        elif e["kind"] == "field":
            val = _doc_field_caption_and_value(e["source"], report, label_map, idx)
            # A multi-line CF_/CP_ FORMULA value (a boilerplate criteria-summary
            # rendered to its label structure, e.g. CP_Report_Details) wraps like
            # a text block instead of clipping to one line. Restricted to formula
            # sources so a multi-line display-constant PARAMETER (e.g. a 2-line
            # P_SUBTITLE) keeps Oracle's single-line clip -- matching the real
            # report -- and existing baselines stay byte-identical.
            _is_formula = (e.get("source", "") or "").upper().startswith(
                ("CF_", "CP_"))
            _css = (text_css if (_is_formula and "\n" in (val or ""))
                    else field_css)
            parts.append('<div style="' + style + _css + '">' + _esc(val) + '</div>')
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


def _render_matrix_pages(report, spec):
    """Render a matrix/cross-tab report as a real PIVOT GRID in the preview
    (row dimension down the left, column dimension across the top, the measure
    in the body cells) -- matching the RDL's Tablix_Matrix and Oracle's print,
    instead of scattering the dimension fields as a flat list."""
    row_dim = spec.get("row") or "Row"
    col_dim = spec.get("col") or "Column"
    cells = spec.get("cells") or ["Value"]
    measure = cells[0]
    row_cap = _humanize_report_title(row_dim)
    col_cap = _humanize_report_title(col_dim)

    def _axis_sample(dim, j):
        # A pivot axis needs DISTINCT values per column/row. An ID/code/number
        # dimension reads as a short numeric code (101, 102, ...) not a generic
        # "Sample Value A"; a keyworded dim (City, Status) uses its pool;
        # anything else gets a distinct "Dim A / B / C" label.
        u = (dim or "").lower()
        if re.search(r"(\b|_)(id|no|num|nbr|code|key|cod)(\b|_|$)", u):
            return str(101 + j)
        base = _sample_for_source(dim, j)
        if base.startswith("Sample Value"):
            return _humanize_report_title(dim) + " " + "ABCDE"[j % 5]
        return base

    # Sample axis values (distinct per cell so the grid reads like real data).
    col_vals = [_axis_sample(col_dim, j) for j in range(3)]
    row_vals = [_axis_sample(row_dim, i) for i in range(4)]
    # Plausible numeric measure cells (matrix measures are counts/sums).
    grid = [12, 8, 5, 9, 6, 3, 7, 4, 2, 5, 3, 1]

    th = ('padding:5px 9px;border:1px solid #c4ccd6;font-size:11px;'
          'background:#4a6a8a;color:#fff;font-weight:bold;text-align:center;')
    rh = ('padding:5px 9px;border:1px solid #d0d0d0;font-size:11px;'
          'font-weight:bold;background:#eef2f6;text-align:left;white-space:nowrap;')
    td = ('padding:5px 9px;border:1px solid #d0d0d0;font-size:11px;'
          'text-align:right;')
    head = ('<tr><th style="' + th + 'text-align:left;">' + _esc(row_cap)
            + ' \\ ' + _esc(col_cap) + '</th>'
            + "".join('<th style="' + th + '">' + _esc(str(c)) + '</th>'
                      for c in col_vals)
            + '<th style="' + th + '">Total</th></tr>')
    body_rows = []
    for i, rv in enumerate(row_vals):
        tds = []
        rtot = 0
        for j in range(len(col_vals)):
            v = grid[(i * len(col_vals) + j) % len(grid)]
            rtot += v
            tds.append('<td style="' + td + '">' + str(v) + '</td>')
        body_rows.append('<tr><td style="' + rh + '">' + _esc(str(rv)) + '</td>'
                         + "".join(tds)
                         + '<td style="' + td + 'font-weight:bold;">' + str(rtot)
                         + '</td></tr>')
    table = ('<table style="border-collapse:collapse;margin:6px auto;">'
             + head + "".join(body_rows) + '</table>')
    cap = ('<div style="text-align:center;color:#64748b;font-size:11px;'
           'margin:4px 0 8px;">Cross-tab: ' + _esc(_humanize_report_title(measure))
           + ' by ' + _esc(col_cap) + ' (columns) and ' + _esc(row_cap)
           + ' (rows)</div>')
    title_html = ('<div style="text-align:center;font-weight:bold;'
                  'font-size:15px;margin-bottom:8px;">'
                  + _esc(_humanize_report_title(getattr(report, "name", "") or "Report"))
                  + '</div>')
    return _render_pages_wrapper(
        [_render_page(title_html + cap + table, label="Page 1 of 1")])


def _mockup_chart_spec(report):
    """Return a detected chart dict whose category+measure are real dataset
    columns (renderable, mirrors the RDL's <Chart> gate), else None."""
    charts = list(getattr(report, "charts", None) or [])
    if not charts:
        return None
    cols = set()
    for q in (report.queries or []):
        for it in (q.items or []):
            if it.name:
                cols.add(it.name.upper())
    for c in charts:
        cat = (c.get("category") or "").strip().upper()
        meas = (c.get("plot_value") or "").strip().upper()
        if cat and meas and cat in cols and meas in cols:
            return c
    return charts[0] if charts else None  # show something even if unbound


def _render_chart_svg(chart):
    """A small SVG bar chart for the preview -- title + sample bars + the
    '<measure> by <category>' caption -- so the mockup shows the chart the
    RDL renders (sample bars; real values come at runtime)."""
    title = (chart.get("title") or "Chart").strip() or "Chart"
    cat = _humanize_report_title(chart.get("category") or "Category")
    meas = _humanize_report_title(chart.get("plot_value") or "Value")
    vals = [62, 88, 45, 73, 34, 57, 49]
    maxv = max(vals)
    W, H, pad = 460, 220, 24
    bw = (W - 2 * pad) // len(vals)
    bars = []
    for i, v in enumerate(vals):
        bh = int((v / maxv) * 150)
        x = pad + i * bw
        y = H - 40 - bh
        bars.append(f'<rect x="{x + 4}" y="{y}" width="{bw - 10}" '
                    f'height="{bh}" rx="2" fill="#4a6a8a"/>')
        bars.append(f'<text x="{x + bw // 2}" y="{H - 24}" font-size="9" '
                    f'text-anchor="middle" fill="#64748b">{chr(65 + i)}</text>')
    svg = (f'<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
           f'role="img" style="max-width:100%;">'
           f'<line x1="{pad}" y1="{H - 40}" x2="{W - pad}" y2="{H - 40}" '
           f'stroke="#cbd5e1"/>'
           f'<line x1="{pad}" y1="20" x2="{pad}" y2="{H - 40}" '
           f'stroke="#cbd5e1"/>' + "".join(bars) + '</svg>')
    return (
        '<div style="text-align:center;">'
        '<div style="font-weight:bold;font-size:15px;margin-bottom:2px;">'
        + _esc(title) + '</div>'
        '<div style="color:#64748b;font-size:11px;margin-bottom:8px;">'
        + _esc(meas) + ' by ' + _esc(cat) + ' (sample bars)</div>'
        + svg + '</div>')


def _maybe_lead_chart(report, html):
    """If the report has a renderable chart, splice a chart sheet in as the
    first page of the preview (so mockup <-> RDL agree)."""
    spec = _mockup_chart_spec(report)
    if not spec:
        return html
    lead = _render_page(_render_chart_svg(spec), label="Chart", first_page=True)
    marker = 'min-height:100%;">'
    i = html.find(marker)
    if i < 0:
        return html
    i += len(marker)
    divider = ('<div style="border-top:1px dashed #cbd5e1; '
               'max-width:8.25in; margin:0 auto 12px;"></div>')
    return html[:i] + lead + divider + html[i:]


def _mockup_label_spec(report):
    """Detect the mailing-label / multi-up archetype for the PREVIEW: a single
    repeating frame whose printDirection tiles ACROSS and whose cell is a small
    boilerplate label box. Mirrors the RDL's _find_label_spec guards (no matrix;
    one across frame; small cell; predominantly a text block). Returns
    {cell_w, cell_h, text} or None."""
    # No matrix anywhere.
    for g in _iter_layout(report):
        if (getattr(g, "kind", "") or "") in (
                "matrix", "matrix_col", "matrix_row", "matrix_cell"):
            return None
    across = [g for g in _iter_layout(report)
              if getattr(g, "kind", "") == "repeating_frame"
              and "across" in (getattr(g, "print_direction", "") or "").lower()]
    if len(across) != 1:
        return None
    frame = across[0]
    cw = float(getattr(frame, "width", 0.0) or 0.0)
    ch = float(getattr(frame, "height", 0.0) or 0.0)
    if not (0.5 <= cw <= 4.5 and 0.2 <= ch <= 3.0):
        return None
    texts, datafields = [], 0

    def collect(g):
        nonlocal datafields
        for f in (g.fields or []):
            if getattr(f, "kind", "") == "text" and len((f.text or "").strip()) >= 12:
                texts.append(f.text)
            elif getattr(f, "kind", "") == "field":
                datafields += 1
        for c in (g.children or []):
            collect(c)

    collect(frame)
    if not texts or datafields > len(texts):
        return None
    return {"cell_w": cw, "cell_h": max(0.6, ch), "text": "\n".join(texts)}


def _render_label_pages(report):
    """Mailing labels: tile the resolved label cell MULTI-UP across the sheet
    then down (matching the RDL's newspaper Columns + Oracle's actual print),
    instead of one label per page."""
    spec = _mockup_label_spec(report)
    if not spec:
        return _render_generic_document_pages(report)
    cell_w, cell_h = spec["cell_w"], spec["cell_h"]
    usable = 7.0  # sheet content width (8.25in max - ~0.6in margins each side)
    gap = 0.12
    ncols = max(1, int((usable + gap) // (cell_w + gap)))
    nrows = max(3, int(9.0 // (cell_h + gap)))
    total = ncols * nrows
    cells = []
    for i in range(total):
        body = _esc(_resolve_tokens(spec["text"], i)).replace("\n", "<br>")
        cells.append(
            '<div style="display:inline-block;vertical-align:top;'
            'width:' + ("%.3fin" % cell_w) + ';height:' + ("%.3fin" % cell_h) + ';'
            'margin:0 ' + ("%.3fin" % (gap / 2)) + ' ' + ("%.3fin" % gap) + ' '
            + ("%.3fin" % (gap / 2)) + ';padding:0.06in 0.08in;box-sizing:border-box;'
            'border:1px dashed #c7d2e0;font-size:10px;line-height:1.25;'
            'overflow:hidden;white-space:pre-wrap;">' + body + '</div>')
    inner = ('<div style="text-align:left;width:' + ("%.3fin" % usable)
             + ';margin:0 auto;">' + "".join(cells) + '</div>')
    note = ('<div style="text-align:center;color:#64748b;font-size:11px;'
            'margin:4px 0 10px;">Mailing labels — ' + str(ncols)
            + '-up per row (tiled across, then down)</div>')
    return _render_pages_wrapper([_render_page(note + inner, label="Page 1 of 1")])


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


def _render_document_packet_pages(report):
    """Render a POSITIONAL DOCUMENT PACKET: section_main packs several
    page-break-separated top-level frames (e.g. a memo cover + a data table +
    a closing letter). Draw each frame on its OWN sheet, geometry-faithful, in
    document order -- a columnar repeating frame inside a frame tiles into a
    real multi-row table in place. Mirrors _render_header_summary_pages but for
    section_main, so a memo/table/letter packet renders 1:1 instead of being
    forced through the tabular cover+detail template."""
    roots = _section_page_groups(report, "section_main")
    if not roots:
        return _render_pages_wrapper([
            _render_generic_document_page(report, 0, 1, 1, tile_tables=True)])
    total = len(roots)
    pages = [
        _render_generic_document_page(report, 0, i + 1, total,
                                      section="section_main", root=r, tile_tables=True)
        for i, r in enumerate(roots)
    ]
    return _render_pages_wrapper(pages)


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
    """Header-resident summary/accounting report. The section_header itself
    may pack SEVERAL physical pages (e.g. a criteria cover + a stat table
    separated by Oracle's pageBreakBefore) -- split it on those breaks so
    each renders on its own sheet, matching the Oracle output 1:1. Then the
    section_main detail layout, when present, is a further page."""
    roots = _section_page_groups(report, "section_header")
    main = _find_section(report.layout or [], "section_main")
    pages = []
    if roots:
        total = len(roots) + (1 if main is not None else 0)
        for i, r in enumerate(roots):
            # skip_repeating=True drops the conditional grantee/site LIST
            # sub-frames from the stat page (mirrors the RDL skip); the criteria
            # cover root has none, so it's a no-op there.
            pages.append(_render_generic_document_page(
                report, 0, i + 1, total, section="section_header", root=r,
                skip_repeating=True))
        if main is not None:
            pages.append(_render_generic_document_page(
                report, 0, len(roots) + 1, total, section="section_main"))
    else:
        # Single content frame -> whole-section render (unchanged path).
        total = 2 if main is not None else 1
        pages.append(_render_generic_document_page(
            report, 0, 1, total, section="section_header", skip_repeating=True))
        if main is not None:
            pages.append(_render_generic_document_page(
                report, 0, 2, total, section="section_main"))
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
        # Matrix / cross-tab: render a real pivot grid (the RDL pivots via
        # Tablix_Matrix; the preview must match, not scatter the fields). The
        # spec is stashed on the report during RDL generation (runs first).
        _mspec = getattr(report, "_matrix_spec", None)
        if _mspec and _mspec.get("dominant") and _mspec.get("row") and _mspec.get("col"):
            _result = _render_matrix_pages(report, _mspec)
        # Mailing-label / multi-up archetype: tile the label cell across the
        # sheet (matches the RDL's newspaper Columns + Oracle's print), instead
        # of one label per page through the document path.
        elif _mockup_label_spec(report):
            _result = _render_label_pages(report)
        elif _is_header_summary_preview(report):
            # Accounting/status report whose criteria cover + summary table
            # live in section_header -- render that geometry-driven, then the
            # section_main detail page.
            _result = _render_header_summary_pages(report)
        elif _is_positional_document_packet(report):
            # A multi-frame positional document (memo cover + data table +
            # closing letter, etc.) -- render each top-level frame on its own
            # sheet, geometry-faithful, with embedded tables tiled in place.
            # Checked before the tabular fallback because such packets DO carry
            # a columnar repeating frame (so detect_report_kind calls them
            # tabular) yet must NOT lose their prose frames to the cover+detail
            # template.
            _result = _render_document_packet_pages(report)
        elif kind in ("letter", "certificate"):
            # Letters AND certificates are single positional documents -- render
            # the ACTUAL section_main layout (frames/texts/fields at their real
            # positions, real colors), never hardcoded sample content.
            _result = _render_generic_document_pages(report)
        elif _is_single_record_form(report):
            # Positional single-record FORM (invoice/requisition): one master
            # record per page (maxRecordsPerPage=1) with a scattered vendor/
            # bill-to/office block + an embedded line-item table. The tabular
            # template collapses the form into a navy band; render the real
            # geometry per record with the line-item grid tiled in place.
            _result = _render_single_record_form_pages(report)
        elif _is_per_record_document(report):
            # Per-record POSITIONAL DOCUMENT -- a per-facility labeled FORM
            # (Plant Location / Mailing Address / SIC-NAIC blocks) or a
            # CERTIFICATE (centered name + body paragraph + state seal). The
            # tabular / nested-MD templates collapse these into a navy-banded
            # grid and drop the labeled blocks + the seal; render the real
            # section_main geometry per record, tiling any sub-tables in place.
            _result = _render_per_record_document_pages(report)
        else:
            _result = _render_tabular_pages(report)
        # A detected chart renders as a real <Chart> in the RDL -- show it in
        # the preview too (leading sheet) so mockup and RDL agree.
        return _maybe_lead_chart(report, _result)
    finally:
        _ACTIVE_MODE = prev
        _ACTIVE_TITLE_FONT = prev_font

