"""
SSRS RDL XML generator.

Public API:
    generate_rdl(report: ParsedReport) -> str

Produces a well-formed RDL 2008+ document that opens in SSRS Report Builder
without parsing errors. The output reflects the source: parameter list,
data fields, and table columns. Pixel-perfect rendering is a stretch goal.
"""
from __future__ import annotations

import base64
import binascii
import re
import xml.etree.ElementTree as ET
from typing import Iterable, List, Optional, Set, Tuple

from converter.models import (
    DataItem,
    DataQuery,
    EmbeddedImage,
    LayoutField,
    LayoutGroup,
    ParsedReport,
    ReportParameter,
)


# ---------------------------------------------------------------------------
# Namespaces
# ---------------------------------------------------------------------------

RDL_NS = "http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition"
RD_NS = "http://schemas.microsoft.com/SQLServer/reporting/reportdesigner"

ET.register_namespace("", RDL_NS)
ET.register_namespace("rd", RD_NS)


def _q(tag: str) -> str:
    """Tag in default RDL namespace."""
    return f"{{{RDL_NS}}}{tag}"


def _rd(tag: str) -> str:
    """Tag in rd designer namespace."""
    return f"{{{RD_NS}}}{tag}"


def _sub(parent: ET.Element, tag: str, text: Optional[str] = None) -> ET.Element:
    el = ET.SubElement(parent, _q(tag))
    if text is not None:
        el.text = text
    return el


def _rdsub(parent: ET.Element, tag: str, text: Optional[str] = None) -> ET.Element:
    el = ET.SubElement(parent, _rd(tag))
    if text is not None:
        el.text = text
    return el


# ---------------------------------------------------------------------------
# Defaults (used when layout information doesn't surface enough columns)
# ---------------------------------------------------------------------------

DEFAULT_COLUMNS = ["Permit", "Renewal_Year", "Site_Name", "Site_Addr", "Perm_Dates"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_QUERY_PARAM_RE = re.compile(r"@(P_\w+)", re.IGNORECASE)


def _detect_query_parameters(tsql: str) -> List[str]:
    """Find unique @P_FOO references in T-SQL text, preserving order."""
    if not tsql:
        return []
    seen: Set[str] = set()
    out: List[str] = []
    for m in _QUERY_PARAM_RE.finditer(tsql):
        name = m.group(1)
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


def _ssrs_field_type(item: DataItem) -> str:
    """Map a DataItem datatype to a CLR type string for rd:TypeName."""
    return item.ssrs_datatype if hasattr(item, "ssrs_datatype") else "System.String"


def _ssrs_param_type(p: ReportParameter) -> str:
    """SSRS DataType for a ReportParameter."""
    return p.ssrs_datatype if hasattr(p, "ssrs_datatype") else "String"


def _safe(s: str) -> str:
    """Make a name safe-ish for an RDL identifier."""
    return re.sub(r"[^A-Za-z0-9_]", "_", s or "")


def _q_safe(s: str) -> str:
    """Escape double-quotes for inclusion in an SSRS expression literal."""
    return (s or "").replace('"', '""')


def _in(value: float) -> str:
    """Format a number as an RDL inches measurement, clamped >= 0."""
    if value is None or value < 0 or value != value:
        value = 0.0
    return f"{value:.5f}in"


def _collect_layout_columns(report: ParsedReport, query_name: str) -> List[str]:
    """Walk the layout tree, return ordered column names bound to query_name."""
    cols: List[str] = []
    seen: Set[str] = set()

    def walk(group: LayoutGroup) -> None:
        if (group.source_query or "").upper() == query_name.upper():
            for f in group.fields:
                src = (f.source or "").strip()
                if src and src not in seen:
                    seen.add(src)
                    cols.append(src)
        for child in group.children:
            walk(child)

    for g in report.layout or []:
        walk(g)
    return cols


def _pick_main_query(report: ParsedReport) -> Optional[DataQuery]:
    """Prefer Q_PERMIT; fall back to first query."""
    for q in report.queries or []:
        if q.name.upper() == "Q_PERMIT":
            return q
    if report.queries:
        return report.queries[0]
    return None


def _pick_detail_query(report: ParsedReport, main_name: str) -> Optional[DataQuery]:
    """Pick a master-detail secondary query (Q_ORG preferred)."""
    if not report.queries:
        return None
    main_upper = (main_name or "").upper()
    # Prefer Q_ORG explicitly
    for q in report.queries:
        if q.name.upper() == "Q_ORG" and q.name.upper() != main_upper:
            return q
    # Otherwise the next non-primary query
    for q in report.queries:
        if q.name.upper() != main_upper:
            return q
    return None


def _pick_signature_query(report: ParsedReport) -> Optional[DataQuery]:
    """Pick a query for signature/image binding (Q_SIGNATURE)."""
    for q in (report.queries or []):
        if q.name.upper() == "Q_SIGNATURE":
            return q
    return None


def _layout_format_triggers(report: ParsedReport) -> List[Tuple[str, str]]:
    """Find LayoutGroups with format triggers; returns [(group_name, trigger_name)]."""
    out: List[Tuple[str, str]] = []

    def walk(group: LayoutGroup) -> None:
        trigger = getattr(group, "format_trigger", None) or getattr(group, "ft", None)
        if trigger:
            out.append((group.name or group.source_query or "Group", str(trigger)))
        for child in group.children:
            walk(child)

    for g in report.layout or []:
        walk(g)
    return out


# ---------------------------------------------------------------------------
# DataSources
# ---------------------------------------------------------------------------

def _build_data_sources() -> ET.Element:
    ds_root = ET.Element(_q("DataSources"))
    ds = _sub(ds_root, "DataSource")
    ds.set("Name", "DS_Main")
    cp = _sub(ds, "ConnectionProperties")
    _sub(cp, "DataProvider", "SQL")
    _sub(cp, "ConnectString", "Data Source=localhost;Initial Catalog=DEQ")
    _rdsub(ds, "SecurityType", "Integrated")
    return ds_root


# ---------------------------------------------------------------------------
# DataSets
# ---------------------------------------------------------------------------

def _build_dataset(query: DataQuery, declared_params: Iterable[str]) -> ET.Element:
    """Build one <DataSet> element from a DataQuery."""
    ds = ET.Element(_q("DataSet"))
    ds.set("Name", _safe(query.name) or "DataSet1")

    # <Query>
    q_el = _sub(ds, "Query")
    _sub(q_el, "DataSourceName", "DS_Main")
    cmd_text = (query.tsql or query.sql or "").strip()
    if not cmd_text:
        cmd_text = f"-- empty query for {query.name}"
    _sub(q_el, "CommandText", cmd_text)

    # <QueryParameters> from @P_FOO references in tsql
    referenced = _detect_query_parameters(cmd_text)
    if referenced:
        qp_root = _sub(q_el, "QueryParameters")
        declared_set = {p.upper() for p in declared_params}
        for pname in referenced:
            qp = _sub(qp_root, "QueryParameter")
            qp.set("Name", f"@{pname}")
            # Bind to the report parameter if it exists, otherwise just empty
            if pname.upper() in declared_set:
                _sub(qp, "Value", f"=Parameters!{pname}.Value")
            else:
                _sub(qp, "Value", "")

    # <Fields>
    fields = _sub(ds, "Fields")
    for item in query.items or []:
        f = _sub(fields, "Field")
        f.set("Name", _safe(item.name) or "Field1")
        _sub(f, "DataField", item.name)
        _rdsub(f, "TypeName", _ssrs_field_type(item))
    return ds


def _build_data_sets(report: ParsedReport) -> ET.Element:
    root = ET.Element(_q("DataSets"))
    declared = [p.name for p in report.parameters or []]
    if not report.queries:
        # Always emit at least one dataset so the Tablix has something to bind
        placeholder = DataQuery(
            name="Q_PERMIT",
            tsql="SELECT 1 AS Permit, 0 AS Renewal_Year, '' AS Site_Name",
            items=[
                DataItem(name="Permit", datatype="character"),
                DataItem(name="Renewal_Year", datatype="number"),
                DataItem(name="Site_Name", datatype="character"),
            ],
        )
        root.append(_build_dataset(placeholder, declared))
        return root

    for q in report.queries:
        root.append(_build_dataset(q, declared))
    return root


# ---------------------------------------------------------------------------
# ReportParameters
# ---------------------------------------------------------------------------

def _build_report_parameters(report: ParsedReport) -> Optional[ET.Element]:
    if not report.parameters:
        return None
    root = ET.Element(_q("ReportParameters"))
    for p in report.parameters:
        rp = _sub(root, "ReportParameter")
        rp.set("Name", p.name)
        _sub(rp, "DataType", _ssrs_param_type(p))
        _sub(rp, "Prompt", p.label or p.name)
        if p.initial_value is None or p.initial_value == "":
            _sub(rp, "Nullable", "true")
            _sub(rp, "AllowBlank", "true") if _ssrs_param_type(p) == "String" else None
        else:
            dv = _sub(rp, "DefaultValue")
            values = _sub(dv, "Values")
            _sub(values, "Value", str(p.initial_value))
        if not p.display:
            _sub(rp, "Hidden", "true")
    return root


# ---------------------------------------------------------------------------
# Body / Tablix
# ---------------------------------------------------------------------------

def _column_names_for_main(report: ParsedReport, main: DataQuery) -> List[str]:
    """Decide what columns the main Tablix should show."""
    layout_cols = _collect_layout_columns(report, main.name)
    if layout_cols:
        return layout_cols
    if main.items:
        # Filter to only "interesting" visible fields if possible. Since we
        # don't know which are hidden, take all but cap at 10 to keep the
        # layout reasonable.
        names = [it.name for it in main.items][:10]
        return names
    return list(DEFAULT_COLUMNS)


def _build_textbox(parent: ET.Element, name: str, value: str,
                   bold: bool = False, font_size: str = "10pt",
                   bg: Optional[str] = None) -> ET.Element:
    tb = _sub(parent, "Textbox")
    tb.set("Name", name)
    paragraphs = _sub(tb, "Paragraphs")
    para = _sub(paragraphs, "Paragraph")
    runs = _sub(para, "TextRuns")
    run = _sub(runs, "TextRun")
    _sub(run, "Value", value)
    style = _sub(run, "Style")
    _sub(style, "FontSize", font_size)
    if bold:
        _sub(style, "FontWeight", "Bold")
    tb_style = _sub(tb, "Style")
    border = _sub(tb_style, "Border")
    _sub(border, "Style", "Solid")
    _sub(border, "Color", "LightGrey")
    if bg:
        _sub(tb_style, "BackgroundColor", bg)
    _sub(tb_style, "PaddingLeft", "2pt")
    _sub(tb_style, "PaddingRight", "2pt")
    _sub(tb_style, "PaddingTop", "2pt")
    _sub(tb_style, "PaddingBottom", "2pt")
    _sub(tb, "CanGrow", "true")
    _sub(tb, "KeepTogether", "true")
    return tb


def _pick_group_key(query: DataQuery, candidates: Iterable[str]) -> Optional[str]:
    """Pick a reasonable group expression field from a query's items."""
    if not query.items:
        return None
    item_names = {it.name.upper(): it.name for it in query.items}
    for c in candidates:
        if c.upper() in item_names:
            return item_names[c.upper()]
    # Default to first field
    return query.items[0].name


def _build_tablix(report: ParsedReport, main: DataQuery) -> ET.Element:
    columns = _column_names_for_main(report, main)
    if not columns:
        columns = list(DEFAULT_COLUMNS)

    tablix = ET.Element(_q("Tablix"))
    tablix.set("Name", "Tablix_Main")

    # Determine if a master-detail nested group should be emitted.
    detail_query = _pick_detail_query(report, main.name)
    triggers = _layout_format_triggers(report)

    # TablixBody
    body = _sub(tablix, "TablixBody")
    cols_el = _sub(body, "TablixColumns")
    for _ in columns:
        c = _sub(cols_el, "TablixColumn")
        _sub(c, "Width", "1.5in")

    rows_el = _sub(body, "TablixRows")

    # Header row
    header_row = _sub(rows_el, "TablixRow")
    _sub(header_row, "Height", "0.25in")
    header_cells = _sub(header_row, "TablixCells")
    for col in columns:
        cell = _sub(header_cells, "TablixCell")
        contents = _sub(cell, "CellContents")
        _build_textbox(
            contents,
            f"Hdr_{_safe(col)}",
            col.replace("_", " "),
            bold=True,
            bg="LightSteelBlue",
        )

    # Detail row
    detail_row = _sub(rows_el, "TablixRow")
    _sub(detail_row, "Height", "0.25in")
    detail_cells = _sub(detail_row, "TablixCells")
    for col in columns:
        cell = _sub(detail_cells, "TablixCell")
        contents = _sub(cell, "CellContents")
        _build_textbox(
            contents,
            f"Cell_{_safe(col)}",
            f"=Fields!{_safe(col)}.Value",
        )

    # TablixColumnHierarchy
    col_hier = _sub(tablix, "TablixColumnHierarchy")
    col_members = _sub(col_hier, "TablixMembers")
    for _ in columns:
        _sub(col_members, "TablixMember")

    # TablixRowHierarchy
    row_hier = _sub(tablix, "TablixRowHierarchy")
    row_members = _sub(row_hier, "TablixMembers")
    # Header member (static)
    hdr_mem = _sub(row_members, "TablixMember")
    _sub(hdr_mem, "KeepWithGroup", "After")

    # Master-detail nested group pattern when a secondary query exists.
    if detail_query is not None:
        # Outer (permit) group
        permit_mem = _sub(row_members, "TablixMember")
        permit_group = _sub(permit_mem, "Group")
        permit_group.set("Name", "GroupPermit")
        permit_grp_exprs = _sub(permit_group, "GroupExpressions")
        permit_key = _pick_group_key(
            main, ["Perm_Num", "Permit", "Perm_Name", "Permit_Id"]
        )
        _sub(
            permit_grp_exprs,
            "GroupExpression",
            f"=Fields!{_safe(permit_key)}.Value" if permit_key else "=1",
        )
        # Conditional visibility hint based on first format trigger (placeholder).
        if triggers:
            grp_name, trig_name = triggers[0]
            permit_mem.append(
                ET.Comment(
                    f" original PL/SQL format trigger: {trig_name} (group {grp_name}) "
                )
            )
            visibility = _sub(permit_mem, "Visibility")
            _sub(visibility, "Hidden", "false")
        # Nested children: inner Org group + detail
        inner_members = _sub(permit_mem, "TablixMembers")
        org_mem = _sub(inner_members, "TablixMember")
        org_group = _sub(org_mem, "Group")
        org_group.set("Name", "GroupOrg")
        org_grp_exprs = _sub(org_group, "GroupExpressions")
        org_key = _pick_group_key(
            detail_query, ["Org_Id", "Organization_Id", "Org", "Org_Code"]
        )
        _sub(
            org_grp_exprs,
            "GroupExpression",
            f"=Fields!{_safe(org_key)}.Value" if org_key else "=1",
        )
        # Detail leaf inside inner group
        detail_inner = _sub(org_mem, "TablixMembers")
        det = _sub(detail_inner, "TablixMember")
        _sub(det, "Group").set("Name", "Detail")
    else:
        # Detail member (no master-detail)
        detail_mem = _sub(row_members, "TablixMember")
        _sub(detail_mem, "Group").set("Name", "Details_Main")
        # (no GroupExpressions == the detail group)
        if triggers:
            grp_name, trig_name = triggers[0]
            detail_mem.append(
                ET.Comment(
                    f" original PL/SQL format trigger: {trig_name} (group {grp_name}) "
                )
            )
            visibility = _sub(detail_mem, "Visibility")
            _sub(visibility, "Hidden", "false")

    # DataSet binding
    _sub(tablix, "DataSetName", _safe(main.name))

    # Position / size
    _sub(tablix, "Top", "0.5in")
    _sub(tablix, "Left", "0.25in")
    _sub(tablix, "Height", "0.5in")
    _sub(tablix, "Width", f"{1.5 * len(columns)}in")
    style = _sub(tablix, "Style")
    border = _sub(style, "Border")
    _sub(border, "Style", "Solid")
    _sub(border, "Color", "LightGrey")
    return tablix


def _build_embedded_images(report: ParsedReport) -> Optional[ET.Element]:
    """Convert ParsedReport.embedded_images hex blobs into <EmbeddedImages>."""
    images = list(report.embedded_images or [])
    if not images:
        return None
    root = ET.Element(_q("EmbeddedImages"))
    for img in images:
        try:
            cleaned = "".join((img.hex_data or "").split())
            if not cleaned:
                continue
            raw = binascii.unhexlify(cleaned)
            data_b64 = base64.b64encode(raw).decode("ascii")
        except (binascii.Error, ValueError):
            continue
        ei = _sub(root, "EmbeddedImage")
        ei.set("Name", _safe(img.id) or "EmbeddedImage1")
        _sub(ei, "MIMEType", img.mime_type or "image/gif")
        _sub(ei, "ImageData", data_b64)
    return root if list(root) else None


# ---------------------------------------------------------------------------
# Certificate / positioned layout path
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"&([A-Za-z_][A-Za-z0-9_]*)")


def _find_section_main(report: ParsedReport) -> Optional[LayoutGroup]:
    """Return the section_main LayoutGroup if it has at least one frame child."""
    def has_frame(g: LayoutGroup) -> bool:
        return any((c.kind or "").lower() == "frame" for c in g.children or [])

    for g in report.layout or []:
        kind = (g.kind or "").lower()
        if kind == "section_main" and has_frame(g):
            return g
        for child in g.children or []:
            if (child.kind or "").lower() == "section_main" and has_frame(child):
                return child
    return None


def _section_by_kind(report: ParsedReport, kind_name: str) -> Optional[LayoutGroup]:
    target = kind_name.lower()
    for g in report.layout or []:
        if (g.kind or "").lower() == target:
            return g
        for child in g.children or []:
            if (child.kind or "").lower() == target:
                return child
    return None


def _resolve_text_expression(text: str, report: ParsedReport) -> Tuple[str, bool]:
    """Resolve &TOKEN substitutions to either a literal or an SSRS expression.

    Returns (value, is_expression). When is_expression=False, value is a literal.
    """
    if not text:
        return "", False
    if "&" not in text:
        return text, False
    if not _TOKEN_RE.search(text):
        return text, False

    declared_params = {p.name.upper() for p in (report.parameters or [])}

    parts: List[str] = []
    last = 0
    for m in _TOKEN_RE.finditer(text):
        literal_chunk = text[last:m.start()]
        if literal_chunk:
            parts.append('"' + _q_safe(literal_chunk) + '"')
        token = m.group(1)
        upper = token.upper()
        if upper.startswith("P_") or upper in declared_params:
            parts.append(f"Parameters!{token}.Value")
        else:
            parts.append(f"Fields!{_safe(token)}.Value")
        last = m.end()
    tail = text[last:]
    if tail:
        parts.append('"' + _q_safe(tail) + '"')
    if not parts:
        return text, False
    expr = "=" + " & ".join(parts)
    return expr, True


def _field_value_for(lf: LayoutField, report: ParsedReport) -> str:
    """Return the SSRS <Value> string for a kind=field LayoutField."""
    src = (lf.source or lf.text or "").strip()
    if not src:
        return ""
    declared_params = {p.name.upper() for p in (report.parameters or [])}
    upper = src.upper()
    if upper == "CURRENTDATE" or upper == "CURRENT_DATE":
        return "=Globals!ExecutionTime"
    if upper.startswith("P_") or upper in declared_params:
        return f"=Parameters!{src}.Value"
    return f"=Fields!{_safe(src)}.Value"


def _apply_field_style(style_el: ET.Element, lf: LayoutField) -> None:
    if lf.font_size:
        _sub(style_el, "FontSize", f"{int(lf.font_size)}pt")
    if lf.font_family:
        _sub(style_el, "FontFamily", lf.font_family)
    if lf.bold:
        _sub(style_el, "FontWeight", "Bold")
    if lf.italic:
        _sub(style_el, "FontStyle", "Italic")
    if lf.color:
        _sub(style_el, "Color", lf.color)
    if lf.align:
        _sub(style_el, "TextAlign", lf.align.capitalize())


def _emit_positioned_textbox(
    parent: ET.Element,
    name: str,
    value: str,
    is_expression: bool,
    lf: LayoutField,
    origin_x: float,
    origin_y: float,
) -> ET.Element:
    tb = _sub(parent, "Textbox")
    tb.set("Name", name)
    paragraphs = _sub(tb, "Paragraphs")
    para = _sub(paragraphs, "Paragraph")
    runs = _sub(para, "TextRuns")
    run = _sub(runs, "TextRun")
    _sub(run, "Value", value)
    run_style = _sub(run, "Style")
    _apply_field_style(run_style, lf)
    if lf.align:
        para_style = _sub(para, "Style")
        _sub(para_style, "TextAlign", lf.align.capitalize())
    _sub(tb, "CanGrow", "true")
    _sub(tb, "KeepTogether", "true")
    rel_x = max(0.0, lf.x - origin_x)
    rel_y = max(0.0, lf.y - origin_y)
    _sub(tb, "Top", _in(rel_y))
    _sub(tb, "Left", _in(rel_x))
    _sub(tb, "Width", _in(lf.width if lf.width > 0 else 1.0))
    _sub(tb, "Height", _in(lf.height if lf.height > 0 else 0.2))
    tb_style = _sub(tb, "Style")
    _sub(tb_style, "PaddingLeft", "1pt")
    _sub(tb_style, "PaddingRight", "1pt")
    _sub(tb_style, "PaddingTop", "1pt")
    _sub(tb_style, "PaddingBottom", "1pt")
    return tb


def _emit_image(
    parent: ET.Element,
    name: str,
    lf: LayoutField,
    embedded_index: dict,
    origin_x: float,
    origin_y: float,
) -> Optional[ET.Element]:
    image_id = lf.image_id or lf.source
    if not image_id:
        return None
    img = _sub(parent, "Image")
    img.set("Name", name)
    _sub(img, "Source", "Embedded")
    _sub(img, "Value", _safe(image_id))
    mime = embedded_index.get(image_id, "image/gif")
    _sub(img, "MIMEType", mime)
    _sub(img, "Sizing", "FitProportional")
    rel_x = max(0.0, lf.x - origin_x)
    rel_y = max(0.0, lf.y - origin_y)
    _sub(img, "Top", _in(rel_y))
    _sub(img, "Left", _in(rel_x))
    _sub(img, "Width", _in(lf.width if lf.width > 0 else 0.5))
    _sub(img, "Height", _in(lf.height if lf.height > 0 else 0.5))
    _sub(img, "Style")
    return img


def _emit_layout_field(
    parent: ET.Element,
    lf: LayoutField,
    report: ParsedReport,
    embedded_index: dict,
    origin_x: float,
    origin_y: float,
    name_prefix: str = "",
) -> None:
    kind = (lf.kind or "field").lower()
    if lf.width <= 0 and lf.height <= 0 and not lf.text and not lf.source:
        return
    base_name = _safe(lf.name) or "Item"
    name = f"{name_prefix}{base_name}" if name_prefix else f"Tb_{base_name}"
    if kind == "image":
        _emit_image(
            parent,
            f"Img_{base_name}",
            lf,
            embedded_index,
            origin_x,
            origin_y,
        )
        return
    if kind == "line":
        return
    if kind == "text":
        text = lf.text or lf.source or ""
        value, is_expr = _resolve_text_expression(text, report)
        _emit_positioned_textbox(parent, name, value, is_expr, lf, origin_x, origin_y)
        return
    value = _field_value_for(lf, report)
    if not value:
        value = lf.text or lf.source or ""
        _emit_positioned_textbox(parent, name, value, False, lf, origin_x, origin_y)
        return
    _emit_positioned_textbox(parent, name, value, True, lf, origin_x, origin_y)


def _emit_frame(
    parent_items: ET.Element,
    frame: LayoutGroup,
    report: ParsedReport,
    embedded_index: dict,
    parent_x: float,
    parent_y: float,
) -> None:
    if frame.width <= 0 and frame.height <= 0:
        rect_origin_x = parent_x
        rect_origin_y = parent_y
        for f in frame.fields or []:
            _emit_layout_field(parent_items, f, report, embedded_index,
                               rect_origin_x, rect_origin_y)
        for child in frame.children or []:
            _emit_frame(parent_items, child, report, embedded_index,
                        rect_origin_x, rect_origin_y)
        return

    rect = _sub(parent_items, "Rectangle")
    rect.set("Name", f"Rect_{_safe(frame.name) or 'Frame'}")
    inner_items = _sub(rect, "ReportItems")

    frame_origin_x = frame.x
    frame_origin_y = frame.y

    for f in frame.fields or []:
        _emit_layout_field(inner_items, f, report, embedded_index,
                           frame_origin_x, frame_origin_y)

    for child in frame.children or []:
        child_kind = (child.kind or "").lower()
        if child_kind in ("frame", "repeating_frame"):
            child_rect = _sub(inner_items, "Rectangle")
            child_rect.set("Name", f"Rect_{_safe(child.name) or 'SubFrame'}")
            child_inner = _sub(child_rect, "ReportItems")
            for cf in child.fields or []:
                _emit_layout_field(child_inner, cf, report, embedded_index,
                                   child.x, child.y)
            for grand in child.children or []:
                _emit_frame(child_inner, grand, report, embedded_index,
                            child.x, child.y)
            _sub(child_rect, "Top", _in(max(0.0, child.y - frame_origin_y)))
            _sub(child_rect, "Left", _in(max(0.0, child.x - frame_origin_x)))
            _sub(child_rect, "Height", _in(child.height if child.height > 0 else 0.5))
            _sub(child_rect, "Width", _in(child.width if child.width > 0 else 1.0))
            cstyle = _sub(child_rect, "Style")
            if (child.border_width or 0) > 0:
                cborder = _sub(cstyle, "Border")
                _sub(cborder, "Style", "Solid")
                _sub(cborder, "Color", "Black")
                _sub(cborder, "Width", "1pt")
        else:
            _emit_frame(inner_items, child, report, embedded_index,
                        frame_origin_x, frame_origin_y)

    rel_x = max(0.0, frame.x - parent_x)
    rel_y = max(0.0, frame.y - parent_y)
    _sub(rect, "Top", _in(rel_y))
    _sub(rect, "Left", _in(rel_x))
    _sub(rect, "Height", _in(frame.height))
    _sub(rect, "Width", _in(frame.width))
    rstyle = _sub(rect, "Style")
    if (frame.border_width or 0) > 0:
        rborder = _sub(rstyle, "Border")
        _sub(rborder, "Style", "Solid")
        _sub(rborder, "Color", "Black")
        _sub(rborder, "Width", f"{max(0.5, frame.border_width):.2f}pt")


def _certificate_extents(section_main: LayoutGroup) -> Tuple[float, float]:
    max_w = 0.0
    max_h = 0.0
    for child in section_main.children or []:
        if (child.kind or "").lower() not in ("frame", "repeating_frame"):
            continue
        if child.width <= 0 and child.height <= 0:
            continue
        right = child.x + child.width
        bottom = child.y + child.height
        if right > max_w:
            max_w = right
        if bottom > max_h:
            max_h = bottom
    return max_w, max_h


def _build_certificate_body(
    report: ParsedReport,
    main: DataQuery,
    section_main: LayoutGroup,
) -> Tuple[ET.Element, float, float]:
    """Build a List-wrapped certificate body. Returns (body, width_in, height_in)."""
    body = ET.Element(_q("Body"))
    items = _sub(body, "ReportItems")

    embedded_index = {
        img.id: (img.mime_type or "image/gif")
        for img in (report.embedded_images or [])
    }

    extent_w, extent_h = _certificate_extents(section_main)
    list_width = max(section_main.width or 0.0, extent_w, 7.5)
    list_height = max(section_main.height or 0.0, extent_h, 1.0)

    list_el = _sub(items, "List")
    list_el.set("Name", "List_Permit")

    list_body = _sub(list_el, "TablixBody")
    cols = _sub(list_body, "TablixColumns")
    col = _sub(cols, "TablixColumn")
    _sub(col, "Width", _in(list_width))
    rows = _sub(list_body, "TablixRows")
    row = _sub(rows, "TablixRow")
    _sub(row, "Height", _in(list_height))
    cells = _sub(row, "TablixCells")
    cell = _sub(cells, "TablixCell")
    contents = _sub(cell, "CellContents")

    body_rect = _sub(contents, "Rectangle")
    body_rect.set("Name", "Rect_Body")
    body_items = _sub(body_rect, "ReportItems")

    for child in section_main.children or []:
        if (child.kind or "").lower() not in ("frame", "repeating_frame"):
            continue
        if child.width <= 0 and child.height <= 0:
            continue
        _emit_frame(body_items, child, report, embedded_index, 0.0, 0.0)

    _sub(body_rect, "Top", "0in")
    _sub(body_rect, "Left", "0in")
    _sub(body_rect, "Height", _in(list_height))
    _sub(body_rect, "Width", _in(list_width))
    _sub(body_rect, "Style")

    cell_style = _sub(contents, "Style")

    col_hier = _sub(list_el, "TablixColumnHierarchy")
    col_members = _sub(col_hier, "TablixMembers")
    _sub(col_members, "TablixMember")

    row_hier = _sub(list_el, "TablixRowHierarchy")
    row_members = _sub(row_hier, "TablixMembers")
    perm_member = _sub(row_members, "TablixMember")
    perm_group = _sub(perm_member, "Group")
    perm_group.set("Name", "GroupPermit")
    grp_exprs = _sub(perm_group, "GroupExpressions")
    permit_key = _pick_group_key(
        main, ["Perm_Num", "Permit", "Perm_Name", "Permit_Id"]
    )
    _sub(
        grp_exprs,
        "GroupExpression",
        f"=Fields!{_safe(permit_key)}.Value" if permit_key else "=1",
    )
    page_break = _sub(perm_group, "PageBreak")
    _sub(page_break, "BreakLocation", "End")
    inner_members = _sub(perm_member, "TablixMembers")
    detail_member = _sub(inner_members, "TablixMember")
    _sub(detail_member, "Group").set("Name", "Details_Permit")

    _sub(list_el, "DataSetName", _safe(main.name))
    _sub(list_el, "Top", "0in")
    _sub(list_el, "Left", "0in")
    _sub(list_el, "Height", _in(list_height))
    _sub(list_el, "Width", _in(list_width))
    list_style = _sub(list_el, "Style")

    body_height_in = max(list_height + 0.25, 1.0)
    _sub(body, "Height", _in(body_height_in))
    _sub(body, "Style")
    return body, list_width, body_height_in


def _build_body(report: ParsedReport, main: Optional[DataQuery]) -> ET.Element:
    if main is not None:
        section_main = _find_section_main(report)
        if section_main is not None:
            body, _, _ = _build_certificate_body(report, main, section_main)
            return body
    body = ET.Element(_q("Body"))
    items = _sub(body, "ReportItems")
    if main is not None:
        items.append(_build_tablix(report, main))
    body_height_in = max(7.0, 1.0)
    _sub(body, "Height", f"{body_height_in}in")
    style = _sub(body, "Style")
    return body


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

def _build_page(report: ParsedReport, page_height_in: float = 11.0) -> ET.Element:
    page = ET.Element(_q("Page"))

    # PageHeader
    has_renewal_year = any(
        p.name.upper() == "P_RENEWAL_YEAR" for p in (report.parameters or [])
    )
    ph = _sub(page, "PageHeader")
    _sub(ph, "Height", "0.6in")
    _sub(ph, "PrintOnFirstPage", "true")
    _sub(ph, "PrintOnLastPage", "true")
    ph_items = _sub(ph, "ReportItems")
    # Title textbox
    title_tb = _sub(ph_items, "Textbox")
    title_tb.set("Name", "Hdr_Title")
    paragraphs = _sub(title_tb, "Paragraphs")
    para = _sub(paragraphs, "Paragraph")
    runs = _sub(para, "TextRuns")
    run = _sub(runs, "TextRun")
    _sub(run, "Value", "DEPARTMENT OF ENVIRONMENTAL QUALITY")
    rstyle = _sub(run, "Style")
    _sub(rstyle, "FontSize", "14pt")
    _sub(rstyle, "FontWeight", "Bold")
    _sub(title_tb, "Top", "0.05in")
    _sub(title_tb, "Left", "0.25in")
    _sub(title_tb, "Width", "5in")
    _sub(title_tb, "Height", "0.3in")
    _sub(title_tb, "CanGrow", "true")
    title_style = _sub(title_tb, "Style")

    if has_renewal_year:
        yr_tb = _sub(ph_items, "Textbox")
        yr_tb.set("Name", "Hdr_RenewalYear")
        paragraphs = _sub(yr_tb, "Paragraphs")
        para = _sub(paragraphs, "Paragraph")
        runs = _sub(para, "TextRuns")
        run = _sub(runs, "TextRun")
        _sub(run, "Value", "=Parameters!P_RENEWAL_YEAR.Value")
        rstyle = _sub(run, "Style")
        _sub(rstyle, "FontSize", "12pt")
        _sub(rstyle, "FontWeight", "Bold")
        _sub(yr_tb, "Top", "0.05in")
        _sub(yr_tb, "Left", "5.5in")
        _sub(yr_tb, "Width", "2in")
        _sub(yr_tb, "Height", "0.3in")
        _sub(yr_tb, "CanGrow", "true")
        yr_style = _sub(yr_tb, "Style")

    # Optional PageFooter
    pf = _sub(page, "PageFooter")
    _sub(pf, "Height", "0.6in")
    _sub(pf, "PrintOnFirstPage", "true")
    _sub(pf, "PrintOnLastPage", "true")
    pf_items = _sub(pf, "ReportItems")

    sig_q = _pick_signature_query(report)
    if sig_q is not None:
        sf = sig_q.items[0].name if sig_q.items else "Sig"
        img = _sub(pf_items, "Image")
        img.set("Name", "Img_Sig")
        _sub(img, "Source", "Database")
        _sub(img, "Value", '=First(Fields!' + _safe(sf) + '.Value)')
        _sub(img, "MIMEType", "image/png")
        _sub(img, "Sizing", "FitProportional")
        _sub(img, "Top", "0.05in")
        _sub(img, "Left", "0.25in")
        _sub(img, "Width", "2in")
        _sub(img, "Height", "0.5in")
        _sub(img, "Style")

    pf_tb = _sub(pf_items, "Textbox")
    pf_tb.set("Name", "Ftr_Page")
    paragraphs = _sub(pf_tb, "Paragraphs")
    para = _sub(paragraphs, "Paragraph")
    runs = _sub(para, "TextRuns")
    run = _sub(runs, "TextRun")
    _sub(run, "Value", '=Globals!PageNumber & " of " & Globals!TotalPages')
    rstyle = _sub(run, "Style")
    _sub(rstyle, "FontSize", "9pt")
    _sub(pf_tb, "Top", "0.05in")
    _sub(pf_tb, "Left", "6.5in")
    _sub(pf_tb, "Width", "1.5in")
    _sub(pf_tb, "Height", "0.2in")
    _sub(pf_tb, "CanGrow", "true")
    _sub(pf_tb, "Style")

    _sub(page, "PageHeight", _in(page_height_in if page_height_in > 0 else 11.0))
    _sub(page, "PageWidth", "8.5in")
    _sub(page, "LeftMargin", "0.5in")
    _sub(page, "RightMargin", "0.5in")
    _sub(page, "TopMargin", "0.5in")
    _sub(page, "BottomMargin", "0.5in")
    _sub(page, "Style")
    return page


CODE_BLOCK = """Public Function NullToEmpty(s As Object) As String
    If IsNothing(s) OrElse IsDBNull(s) Then
        Return ""
    End If
    Return s.ToString()
End Function
"""


def _build_code() -> ET.Element:
    code = ET.Element(_q("Code"))
    code.text = CODE_BLOCK
    return code


def _build_report_root(report: ParsedReport) -> ET.Element:
    root = ET.Element(_q("Report"))
    _rdsub(root, "ReportID", "00000000-0000-0000-0000-000000000000")
    _sub(root, "Description", report.name or "Converted")
    _sub(root, "Author", "Oracle2SSRS")
    _sub(root, "AutoRefresh", "0")
    root.append(_build_data_sources())
    root.append(_build_data_sets(report))
    embedded = _build_embedded_images(report)
    if embedded is not None:
        root.append(embedded)
    rps = _build_report_parameters(report)
    if rps is not None:
        root.append(rps)
    main = _pick_main_query(report)

    section_main = _find_section_main(report) if main is not None else None
    if section_main is not None:
        body, list_width, body_height_in = _build_certificate_body(
            report, main, section_main
        )
        page_height = max(11.0, body_height_in + 1.0)
    else:
        body = _build_body(report, main)
        page_height = 11.0

    root.append(body)
    _sub(root, "Width", "8.5in")
    root.append(_build_page(report, page_height_in=page_height))
    root.append(_build_code())
    _sub(root, "Language", "en-US")
    _rdsub(root, "DrawGrid", "true")
    _rdsub(root, "GridSpacing", "0.083333in")
    return root


def generate_rdl(report: ParsedReport) -> str:
    """Return a complete RDL XML document as a string."""
    root = _build_report_root(report)
    try:
        ET.indent(root, space="  ")
    except AttributeError:
        pass
    body = ET.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="utf-8"?>\n' + body
