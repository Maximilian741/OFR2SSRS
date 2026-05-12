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
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

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
_ORACLE_BIND_VAR_RE = re.compile(r":(P_\w+)", re.IGNORECASE)


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


def _detect_oracle_bind_vars(sql: str) -> List[str]:
    """Find unique :P_FOO references in Oracle SQL text, preserving order."""
    if not sql:
        return []
    seen: Set[str] = set()
    out: List[str] = []
    for m in _ORACLE_BIND_VAR_RE.finditer(sql):
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

def _build_data_sources(target_db: str = "oracle") -> ET.Element:
    """Emit <DataSources>.

    IMPORTANT: We always emit ``<DataProvider>SQL</DataProvider>`` regardless
    of ``target_db``. Why: the user's normal workflow is to upload the RDL,
    then in Report Builder swap the embedded DataSource for a SHARED data
    source on their report server. That shared data source carries its own
    provider/connect-string. The embedded provider only matters for one
    thing: Report Builder needs to RECOGNIZE the value so it can open the
    Data Source Properties dialog. ``SQL`` is universally registered on
    every SSRS edition. Setting it to ``OracleClient`` previously caused
    "Select connection type" / "rsDataExtensionNotFound" errors on servers
    where the Oracle data extension wasn't installed — which broke the
    swap-to-shared-connection workflow entirely.

    ``target_db`` is preserved on the signature because _build_dataset
    still uses it to pick the CommandText flavor (Oracle SQL vs T-SQL),
    which is the part of the RDL that actually has to match the runtime
    backend.
    """
    ds_root = ET.Element(_q("DataSources"))
    ds = _sub(ds_root, "DataSource")
    ds.set("Name", "DS_Main")
    cp = _sub(ds, "ConnectionProperties")
    _sub(cp, "DataProvider", "SQL")
    _sub(cp, "ConnectString", "Data Source=localhost;Initial Catalog=AppDb")
    _rdsub(ds, "SecurityType", "Integrated")
    return ds_root


# ---------------------------------------------------------------------------
# DataSets
# ---------------------------------------------------------------------------

def _build_dataset(query: DataQuery, declared_params: Iterable[str],
                   target_db: str = "oracle") -> ET.Element:
    """Build one <DataSet> element from a DataQuery.

    ``target_db`` selects which CommandText flavor and parameter prefix to
    emit:

    * ``"oracle"`` (default) — emit ``query.sql`` verbatim (original Oracle
      SQL with ``:P_PARAM`` bind vars preserved). QueryParameters are
      declared with a leading colon (``:P_PARAM``) per the Oracle Reports
      bind-var convention. If ``query.sql`` is empty we fall back to
      ``query.tsql`` rather than emit an empty CommandText.
    * ``"sqlserver"`` — emit ``query.tsql`` (translated T-SQL with
      ``@P_PARAM`` bind vars), legacy behavior.
    """
    ds = ET.Element(_q("DataSet"))
    ds.set("Name", _safe(query.name) or "DataSet1")

    # <Query>
    q_el = _sub(ds, "Query")
    _sub(q_el, "DataSourceName", "DS_Main")

    if target_db == "oracle":
        # Prefer the original Oracle SQL; fall back to the translated tsql
        # if the parser never captured a sql payload for this query (rare —
        # only happens for synthetic placeholder queries built downstream
        # without populating .sql). In that fallback case the emitted text
        # may still contain T-SQL constructs; user will need to hand-edit.
        cmd_text = (query.sql or "").strip()
        used_fallback = False
        if not cmd_text:
            cmd_text = (query.tsql or "").strip()
            used_fallback = True
        if not cmd_text:
            cmd_text = f"-- empty query for {query.name}"
        # Replace any &LEXICAL_REF in the SQL with a SQL comment so Oracle
        # accepts the statement. Lexical refs are Oracle Reports
        # text-substitution templates (e.g. "&P_CRITERIA_PERMIT" expands to
        # a dynamic WHERE clause). SSRS has no direct equivalent — the
        # developer needs to reimplement the dynamic logic at deploy time,
        # but commenting out the ref keeps the SQL syntactically valid for
        # "Refresh Fields" / schema inspection right now. Only applied for
        # the Oracle target; the T-SQL path uses query.tsql which has
        # already had refs translated to =Parameters!X.Value.
        cmd_text = re.sub(
            r"&([A-Z_][A-Z0-9_]*)",
            r"/* lexical ref &\1 -- reimplement as dynamic WHERE/SELECT at deploy time */",
            cmd_text,
            flags=re.IGNORECASE,
        )
        _sub(q_el, "CommandText", cmd_text)

        # Oracle bind vars look like :P_FOO. Declare each one referenced in
        # the SQL as a <QueryParameter Name=":P_FOO"> bound to the report
        # parameter (or empty when undeclared).
        referenced = _detect_oracle_bind_vars(cmd_text)
        if used_fallback:
            # Fallback path: text is actually T-SQL, so look for @P_FOO too.
            for n in _detect_query_parameters(cmd_text):
                if n not in referenced:
                    referenced.append(n)
        if referenced:
            qp_root = _sub(q_el, "QueryParameters")
            declared_set = {p.upper() for p in declared_params}
            for pname in referenced:
                qp = _sub(qp_root, "QueryParameter")
                qp.set("Name", f":{pname}")
                if pname.upper() in declared_set:
                    _sub(qp, "Value", f"=Parameters!{pname}.Value")
                else:
                    _sub(qp, "Value", "")
    else:
        # T-SQL path: existing behavior. Prefer .tsql, fall back to .sql.
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


def _build_data_sets(report: ParsedReport, target_db: str = "oracle") -> ET.Element:
    root = ET.Element(_q("DataSets"))
    declared = [p.name for p in report.parameters or []]
    if not report.queries:
        # Always emit at least one dataset so the Tablix has something to bind.
        # For Oracle target we put the placeholder in .sql so CommandText
        # comes through unchanged; for sqlserver we put it in .tsql.
        placeholder_sql = "SELECT 1 AS Permit, 0 AS Renewal_Year, '' AS Site_Name"
        placeholder = DataQuery(
            name="Q_PERMIT",
            sql=placeholder_sql,
            tsql=placeholder_sql,
            items=[
                DataItem(name="Permit", datatype="character"),
                DataItem(name="Renewal_Year", datatype="number"),
                DataItem(name="Site_Name", datatype="character"),
            ],
        )
        root.append(_build_dataset(placeholder, declared, target_db=target_db))
        return root

    for q in report.queries:
        root.append(_build_dataset(q, declared, target_db=target_db))
    return root


# ---------------------------------------------------------------------------
# ReportParameters
# ---------------------------------------------------------------------------

def _default_value_text(p, dtype: str) -> str:
    """Pick a DefaultValue Value text that matches the parameter's declared
    DataType. Empty <Value/> is invalid for non-string types and triggers
    'DefaultValue doesn't have the expected type' at upload. For String
    types, an empty string (resulting in an empty <Value/> element) is
    accepted as empty/NULL. For Integer/Float/DateTime/Boolean, emit the
    VB.NET null literal '=Nothing' which SSRS accepts for any DataType."""
    iv = (p.initial_value or "").strip() if p.initial_value else ""
    if iv:
        return iv  # explicit default from the source XML; keep verbatim
    dt = (dtype or "String").strip()
    if dt == "String":
        return ""                    # empty string = NULL/empty for String
    if dt in ("Integer", "Float"):
        return "=Nothing"            # VB null literal, accepted by Integer/Float
    if dt == "DateTime":
        return "=Nothing"
    if dt == "Boolean":
        return "=Nothing"
    return "=Nothing"                # safe fallback for any other DataType


def _build_report_parameters(report: ParsedReport) -> Optional[ET.Element]:
    if not report.parameters:
        return None
    root = ET.Element(_q("ReportParameters"))
    for p in report.parameters:
        rp = _sub(root, "ReportParameter")
        rp.set("Name", p.name)
        # SSRS 2008/01 ReportParameter element order: DataType, Nullable,
        # DefaultValue, AllowBlank, Prompt, Hidden, MultiValue, ValidValues,
        # UsedInQuery. Emitting elements out of order fails schema
        # validation in Report Builder.
        ptype = _ssrs_param_type(p)
        _sub(rp, "DataType", ptype)
        has_initial = not (p.initial_value is None or p.initial_value == "")
        if not has_initial:
            _sub(rp, "Nullable", "true")
        # DefaultValue: pick a Value text that matches the parameter's
        # declared DataType. Empty <Value/> is only valid for String type;
        # for Integer/Float/DateTime/Boolean we emit '=Nothing' (VB null
        # literal) which SSRS accepts as a valid expression for any type.
        dv_text = _default_value_text(p, ptype)
        dv = _sub(rp, "DefaultValue")
        values = _sub(dv, "Values")
        v_el = _sub(values, "Value")
        if dv_text:
            v_el.text = dv_text
        # else: leave as empty <Value/> which is only emitted for String DataType
        if not has_initial and ptype == "String":
            _sub(rp, "AllowBlank", "true")
        _sub(rp, "Prompt", p.label or p.name)
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


def _find_group_for_query(report: ParsedReport, query_name: str) -> Optional[LayoutGroup]:
    """Return the first LayoutGroup whose source_query matches query_name."""
    target = (query_name or "").upper()
    if not target:
        return None

    def walk(group: LayoutGroup) -> Optional[LayoutGroup]:
        if (group.source_query or "").upper() == target:
            return group
        for child in group.children or []:
            hit = walk(child)
            if hit is not None:
                return hit
        return None

    for g in report.layout or []:
        hit = walk(g)
        if hit is not None:
            return hit
    return None


def _build_tablix(report: ParsedReport, main: DataQuery) -> ET.Element:
    columns = _column_names_for_main(report, main)
    if not columns:
        columns = list(DEFAULT_COLUMNS)

    tablix = ET.Element(_q("Tablix"))
    tablix.set("Name", "Tablix_Main")

    # If the layout has a repeating frame for this query and it carries a
    # background_color, use that for the header band; otherwise fall back
    # to the default LightSteelBlue.
    main_group = _find_group_for_query(report, main.name)
    header_bg = "LightSteelBlue"
    detail_bg = ""
    if main_group is not None:
        gb = getattr(main_group, "background_color", "")
        if gb:
            header_bg = gb
            detail_bg = gb

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
            bg=header_bg,
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
            bg=detail_bg or None,
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

# Bind-var refs like :P_RENEWAL_YEAR (Oracle Reports style). Inside dataset
# CommandText these become @P_X (handled by the translator); outside (i.e. in
# layout text expressions) they should resolve to =Parameters!P_X.Value.
_BIND_VAR_RE = re.compile(r":([A-Za-z_][A-Za-z0-9_]*)")


def _build_token_resolver(report: ParsedReport):
    """Return a callable ``resolver(token, dataset_name) -> (kind, ssrs_name, note)``.

    ``kind`` is one of:
      * "param"            - token resolves to a declared ReportParameter.
                             ``ssrs_name`` is the canonical declared name
                             (with P_ prefix). Caller emits
                             ``=Parameters!P_X.Value``.
      * "field"            - token resolves to a DataItem in the given
                             dataset scope. ``ssrs_name`` is the dataset-safe
                             field name. Caller emits ``=Fields!X.Value``.
      * "field_other_ds"   - token matches a DataItem in a SIBLING dataset
                             (not the one bound by the enclosing Tablix).
                             ``ssrs_name`` is a literal SSRS expression
                             (a static string placeholder), NOT a field
                             reference. This keeps the SSRS preflight clean
                             (no out-of-scope ``Fields!`` reference) while
                             documenting via ``note`` that the user must
                             rewire the textbox to the correct dataset
                             at deploy time.
      * "formula"          - token matches a FormulaColumn (Oracle CF_*/CP_*
                             derived value). ``ssrs_name`` is a literal SSRS
                             expression (a static string placeholder), NOT
                             a field reference, since SSRS has no native
                             Oracle Reports formula construct. ``note``
                             points the user at the PL/SQL body so they
                             can re-implement as a calculated field.
      * "field_unverified" - fallback: token does not match any param, any
                             known DataItem, or any formula. The legacy
                             behavior of emitting ``=Fields!X.Value`` is
                             preserved so we don't regress unanalyzable
                             reports; ``note`` flags the binding as suspect.

    Parameter names are matched both with their declared prefix (P_X, PARM_X)
    and the bare suffix (X). Matching is case-insensitive; the returned
    ``ssrs_name`` for params is the canonical declared spelling, so SSRS
    parameter references use ``=Parameters!P_X.Value`` consistently.
    """
    # Build param lookup: bare-upper -> canonical declared name.
    param_canonical: dict = {}
    for p in (report.parameters or []):
        canon = p.name
        param_canonical[canon.upper()] = canon
        upper = canon.upper()
        for prefix in ("P_", "PARM_"):
            if upper.startswith(prefix):
                stripped = canon[len(prefix):]
                if stripped:
                    param_canonical.setdefault(stripped.upper(), canon)

    # Build dataset-field lookup: dataset_upper -> {field_upper -> canonical}
    # and an "all fields" map for cross-scope detection that also remembers
    # the owning dataset name (for the audit message).
    dataset_fields: dict = {}
    all_field_owner: dict = {}  # field_upper -> (canonical_name, dataset_name)
    for q in (report.queries or []):
        per: dict = {}
        for it in (q.items or []):
            if not it.name:
                continue
            per[it.name.upper()] = it.name
            all_field_owner.setdefault(it.name.upper(), (it.name, q.name or ""))
        dataset_fields[(q.name or "").upper()] = per

    # Build formula lookup: name_upper -> FormulaColumn-like object with
    # ``.name`` and ``.plsql_body`` attributes. Tolerate a missing
    # ``formulas`` attribute (older ParsedReport instances).
    formula_by_name: dict = {}
    for f in (getattr(report, "formulas", None) or []):
        fname = getattr(f, "name", "") or ""
        if not fname:
            continue
        formula_by_name.setdefault(fname.upper(), f)

    def resolve(token: str, dataset_name: str = ""):
        if not token:
            return ("field_unverified", token, "empty token")
        u = token.upper()
        # 1) Declared parameter wins (case-insensitive, with or without prefix).
        if u in param_canonical:
            return ("param", param_canonical[u], "")
        # 2) Field in the enclosing dataset scope.
        ds_key = (dataset_name or "").upper()
        if ds_key and ds_key in dataset_fields and u in dataset_fields[ds_key]:
            return ("field", dataset_fields[ds_key][u], "")
        # 3) Formula match (Oracle CF_*/CP_*). SSRS has no native formula
        # construct, so we emit =Nothing here -- the user-visible PDF must
        # show NOTHING (not a literal "<X -- populate at deploy time>"
        # string, which exports verbatim into the rendered PDF and looks
        # unprofessional). The audit note below preserves the original
        # placeholder text for the deployment-checklist tab so a developer
        # can still see which textboxes need rewiring.
        if u in formula_by_name:
            f = formula_by_name[u]
            canonical = getattr(f, "name", token) or token
            body_preview = (getattr(f, "plsql_body", "") or "").strip()
            body_preview = " ".join(body_preview.split())
            if len(body_preview) > 120:
                body_preview = body_preview[:117] + "..."
            literal_expr = "=Nothing"
            note = (
                f"PLACEHOLDER (formula): <{canonical} \u2014 populate at "
                f"deploy time>. token {token!r} maps to Oracle formula "
                f"{canonical!r}; SSRS has no native formula construct, "
                f"emitted =Nothing so the PDF export shows nothing. "
                f"Re-implement the PL/SQL body as an SSRS calculated "
                f"field. PL/SQL: {body_preview!r}"
                if body_preview
                else (
                    f"PLACEHOLDER (formula): <{canonical} \u2014 populate "
                    f"at deploy time>. token {token!r} maps to Oracle "
                    f"formula {canonical!r}; emitted =Nothing. "
                    f"Re-implement as an SSRS calculated field."
                )
            )
            return ("formula", literal_expr, note)
        # 4) Field that exists in a DIFFERENT dataset (cross-scope match).
        # Emitting =Fields!X.Value here would fail SSRS scope validation
        # because the enclosing Tablix is bound to a different dataset.
        # Emit =Nothing so the rendered PDF shows nothing, and capture the
        # rewire instructions in the audit note for the deployment
        # checklist (the user-visible PDF must not show literal
        # "<X -- from dataset Q -- rewire at deploy time>" strings).
        if u in all_field_owner:
            canonical, owner_ds = all_field_owner[u]
            literal_expr = "=Nothing"
            note = (
                f"PLACEHOLDER (cross-dataset): <{canonical} \u2014 from "
                f"dataset {owner_ds}, rewire at deploy time>. token "
                f"{token!r} not in dataset {dataset_name!r}; matches "
                f"DataItem in sibling dataset {owner_ds!r}. Emitted "
                f"=Nothing so the PDF stays clean. To populate, move "
                f"this textbox into the {owner_ds} Tablix (master-detail "
                f"rewire) or add the field to {dataset_name!r} via a join."
            )
            return ("field_other_ds", literal_expr, note)
        # 5) Fallback - legacy behavior, marked unverified.
        return (
            "field_unverified",
            token,
            f"token {token!r} not declared as Parameter or DataItem; emitted as Fields!{token}.Value",
        )

    return resolve




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


def _resolve_text_expression(
    text: str,
    report: ParsedReport,
    dataset_name: str = "",
    audit_notes: Optional[List[str]] = None,
) -> Tuple[str, bool]:
    """Resolve &TOKEN (and :P_TOKEN bind-var) substitutions to either a literal
    or an SSRS expression.

    Returns (value, is_expression). When is_expression=False, value is a literal.

    Routing rules (see _build_token_resolver):
      &P_X / &X where X is a declared param  -> Parameters!P_X.Value
      :P_X bind-var (any scope)              -> Parameters!P_X.Value
      &X where X is a DataItem of dataset_name -> Fields!X.Value
      otherwise                              -> Fields!X.Value + audit note
    """
    if not text:
        return "", False
    has_amp = "&" in text and bool(_TOKEN_RE.search(text))
    has_bind = ":" in text and bool(_BIND_VAR_RE.search(text))
    if not has_amp and not has_bind:
        return text, False

    resolver = _build_token_resolver(report)

    # Walk a combined pattern (&TOKEN | :TOKEN) so we preserve ordering.
    combined_re = re.compile(
        r"(?P<amp>&[A-Za-z_][A-Za-z0-9_]*)|(?P<bind>:[A-Za-z_][A-Za-z0-9_]*)"
    )

    parts: List[str] = []
    last = 0
    any_token = False
    for m in combined_re.finditer(text):
        any_token = True
        literal_chunk = text[last:m.start()]
        if literal_chunk:
            parts.append('"' + _q_safe(literal_chunk) + '"')
        token = (m.group("amp") or m.group("bind"))[1:]
        kind, name, note = resolver(token, dataset_name)
        if kind == "param":
            parts.append(f"Parameters!{name}.Value")
        elif kind == "field":
            parts.append(f"Fields!{_safe(name)}.Value")
            if note and audit_notes is not None:
                audit_notes.append(note)
        elif kind in ("formula", "field_other_ds"):
            # ``name`` is already a complete SSRS expression starting with =
            # (a literal string placeholder). Strip the leading = since the
            # outer expression is rebuilt by joining ``parts`` with " & ".
            literal = name
            if literal.startswith("="):
                literal = literal[1:]
            parts.append(literal)
            if note and audit_notes is not None:
                audit_notes.append(note)
        else:
            parts.append(f"Fields!{_safe(name)}.Value")
            if note and audit_notes is not None:
                audit_notes.append(note)
        last = m.end()
    tail = text[last:]
    if tail:
        parts.append('"' + _q_safe(tail) + '"')
    if not any_token or not parts:
        return text, False
    expr = "=" + " & ".join(parts)
    return expr, True


def _field_value_for(
    lf: LayoutField,
    report: ParsedReport,
    dataset_name: str = "",
    audit_notes: Optional[List[str]] = None,
) -> str:
    """Return the SSRS <Value> string for a kind=field LayoutField.

    Routes through _build_token_resolver so that:
      * a source matching a declared ReportParameter (with or without P_ prefix)
        emits =Parameters!P_X.Value
      * a source matching a DataItem in ``dataset_name`` emits =Fields!X.Value
      * anything else falls back to =Fields!X.Value with an audit note (legacy
        behavior preserved so we don't regress unanalyzable reports).
    """
    raw = (lf.source or lf.text or "").strip()
    if not raw:
        return ""
    # Strip leading & or : the caller may have left on the source string.
    src = raw.lstrip("&:")
    upper = src.upper()
    if upper == "CURRENTDATE" or upper == "CURRENT_DATE":
        return "=Globals!ExecutionTime"
    resolver = _build_token_resolver(report)
    kind, name, note = resolver(src, dataset_name)
    if kind == "param":
        return f"=Parameters!{name}.Value"
    if kind in ("formula", "field_other_ds"):
        # ``name`` is already a full SSRS expression (e.g. ="<...>") that is
        # a safe literal — emit it verbatim, NOT wrapped in Fields!.
        if note and audit_notes is not None:
            audit_notes.append(note)
        return name
    if note and audit_notes is not None:
        audit_notes.append(note)
    return f"=Fields!{_safe(name)}.Value"


_UNSCOPED_FIELDS_RE = re.compile(r"Fields!([A-Za-z_][A-Za-z0-9_]*)\.Value")


def _build_field_owner_map(report: ParsedReport) -> dict:
    """Return {field_name_upper: dataset_name} from all declared queries.

    Used to look up which dataset declares a Fields!X.Value reference so
    we can wrap aggregates with an explicit scope when the host element
    is outside any Tablix (page header/footer/body-not-in-list).
    """
    owner: dict = {}
    for q in (report.queries or []):
        for it in (q.items or []):
            if not getattr(it, "name", None):
                continue
            owner.setdefault(it.name.upper(), q.name or "")
    return owner


def _wrap_unscoped_aggregates(
    expr: str,
    report: ParsedReport,
    in_tablix_scope: bool,
) -> str:
    """Wrap bare =Fields!X.Value references in First(..., "DS") when the
    enclosing element is outside any data region (in_tablix_scope=False).

    SSRS requires every Fields! reference in page header/footer/body-direct
    items to be wrapped in an aggregate that carries an explicit dataset
    scope. Without it, upload fails with:
      "The Value expression ... references a field in an aggregate
       expression without a scope."

    Idempotent: a Fields!X.Value already wrapped (e.g. by an existing
    First(..., "DS") call) is left alone -- the regex only touches bare
    occurrences not already preceded by an aggregate+open-paren and not
    immediately followed by a scope arg.
    """
    if in_tablix_scope:
        return expr
    if not expr or "Fields!" not in expr:
        return expr
    owner = _build_field_owner_map(report)

    AGG_NAMES = ("First", "Last", "Sum", "Avg", "Min", "Max", "Count",
                 "CountDistinct", "CountRows", "StDev", "StDevP",
                 "Var", "VarP")

    def _already_scoped(text: str, match: re.Match) -> bool:
        # Check whether the Fields!X.Value match is already inside an
        # aggregate call that supplies a scope argument. We scan backwards
        # for an aggregate name + "(" and forward for "," "<DS>" before ")".
        # This is heuristic but only fails open (leaves the expression
        # unchanged) -- preflight will still catch missed cases.
        before = text[: match.start()]
        # Look at the last aggregate-name token + "(" before this match.
        m_open = re.search(
            r"(" + "|".join(AGG_NAMES) + r")\s*\(\s*$",
            before,
        )
        if not m_open:
            return False
        # Find the matching closing paren after this match.
        depth = 1
        i = match.end()
        scope_arg = ""
        while i < len(text) and depth > 0:
            ch = text[i]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    break
            elif ch == "," and depth == 1:
                scope_arg = text[i + 1 :].lstrip()
                break
            i += 1
        return bool(re.match(r'"[^"]+"', scope_arg))

    def _replace(m: re.Match) -> str:
        field = m.group(1)
        if _already_scoped(expr, m):
            return m.group(0)
        ds = owner.get(field.upper(), "")
        if not ds:
            # No declared dataset for this field -- leave alone; preflight
            # will surface it.
            return m.group(0)
        return f'First(Fields!{field}.Value, "{ds}")'

    return _UNSCOPED_FIELDS_RE.sub(_replace, expr)


def _apply_field_style(style_el: ET.Element, lf) -> None:
    """Apply font/color attributes to a TextRun <Style>.

    Note: BackgroundColor and Border belong on the outer Textbox <Style>,
    not the TextRun <Style>; use _apply_textbox_style for those.
    """
    if getattr(lf, "font_size", None):
        _sub(style_el, "FontSize", f"{int(lf.font_size)}pt")
    if getattr(lf, "font_family", ""):
        _sub(style_el, "FontFamily", lf.font_family)
    if getattr(lf, "bold", False):
        _sub(style_el, "FontWeight", "Bold")
    if getattr(lf, "italic", False):
        _sub(style_el, "FontStyle", "Italic")
    # Foreground/text color: prefer `color`, fall back to `foreground_color`.
    fg = getattr(lf, "color", "") or getattr(lf, "foreground_color", "")
    if fg:
        _sub(style_el, "Color", fg)
    if getattr(lf, "align", ""):
        _sub(style_el, "TextAlign", _ssrs_text_align(lf.align))


def _ssrs_text_align(value: str) -> str:
    """Map Oracle Reports alignment tokens to SSRS TextAlign values.

    Oracle uses start/end/center (and occasionally fill, justify, flush).
    SSRS 2008/01 schema accepts: Default, Left, Center, Right, General.
    Emitting "Start" or "End" causes upload to fail with:
        "Start is not a valid value. Line X, position Y."
    """
    if not value:
        return "Default"
    v = value.strip().lower()
    return {
        "start":   "Left",
        "left":    "Left",
        "end":     "Right",
        "right":   "Right",
        "center":  "Center",
        "centre":  "Center",
        "middle":  "Center",
        "fill":    "Left",
        "justify": "General",
        "flush":   "Left",
    }.get(v, "Default")


def _apply_textbox_style(tb_style_el: ET.Element, lf) -> None:
    """Apply BackgroundColor + Border (and padding) onto the Textbox <Style>.

    Defensive on attribute access so this works whether or not Agent A has
    added background_color / border_color yet.
    """
    bg = getattr(lf, "background_color", "")
    if bg:
        _sub(tb_style_el, "BackgroundColor", bg)
    bc = getattr(lf, "border_color", "")
    if bc:
        border = _sub(tb_style_el, "Border")
        _sub(border, "Color", bc)
        _sub(border, "Style", "Solid")


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
        _sub(para_style, "TextAlign", _ssrs_text_align(lf.align))
    _sub(tb, "CanGrow", "true")
    _sub(tb, "KeepTogether", "true")
    rel_x = max(0.0, lf.x - origin_x)
    rel_y = max(0.0, lf.y - origin_y)
    _sub(tb, "Top", _in(rel_y))
    _sub(tb, "Left", _in(rel_x))
    _sub(tb, "Width", _in(lf.width if lf.width > 0 else 1.0))
    _sub(tb, "Height", _in(lf.height if lf.height > 0 else 0.2))
    tb_style = _sub(tb, "Style")
    _apply_textbox_style(tb_style, lf)
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
    """Emit an <Image> element bound to an embedded image.

    Per RDL 2008/01 schema, <Image> requires:
      * <Source> in {External, Embedded, Database}
      * <Value> whose meaning depends on Source. For Embedded the Value
        MUST be the literal Name of an <EmbeddedImage> declared in the
        report-level <EmbeddedImages> collection. SSRS upload rejects the
        report with "Value ... is not a valid Value" if the name doesn't
        resolve.

    If the layout field references an image_id that we don't have
    embedded image bytes for, we cannot honor an Embedded reference. We
    fall back to a placeholder Textbox (preserving position/size) so the
    RDL stays schema-valid and uploads cleanly.
    """
    image_id = lf.image_id or lf.source
    if not image_id:
        return None
    safe_id = _safe(image_id)
    # Honor Embedded only when the referenced image actually exists in
    # the report-level <EmbeddedImages>. Otherwise we'd ship a dangling
    # Embedded ref that fails SSRS schema validation at upload.
    if safe_id not in embedded_index:
        # Fall back to a positioned, empty textbox placeholder so the
        # surrounding layout still validates and the user sees a visible
        # gap where the missing image used to be. This is preferable to
        # emitting an invalid Image element.
        tb = _sub(parent, "Textbox")
        tb.set("Name", name)
        paragraphs = _sub(tb, "Paragraphs")
        para = _sub(paragraphs, "Paragraph")
        runs = _sub(para, "TextRuns")
        run = _sub(runs, "TextRun")
        _sub(run, "Value", "")
        _sub(run, "Style")
        rel_x = max(0.0, lf.x - origin_x)
        rel_y = max(0.0, lf.y - origin_y)
        _sub(tb, "Top", _in(rel_y))
        _sub(tb, "Left", _in(rel_x))
        _sub(tb, "Width", _in(lf.width if lf.width > 0 else 0.5))
        _sub(tb, "Height", _in(lf.height if lf.height > 0 else 0.5))
        _sub(tb, "Style")
        return tb
    img = _sub(parent, "Image")
    img.set("Name", name)
    _sub(img, "Source", "Embedded")
    _sub(img, "Value", safe_id)
    mime = embedded_index.get(safe_id, "image/gif")
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
    dataset_name: str = "",
    audit_notes: Optional[List[str]] = None,
    in_tablix_scope: bool = True,
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
        value, is_expr = _resolve_text_expression(
            text, report, dataset_name=dataset_name, audit_notes=audit_notes
        )
        if is_expr:
            value = _wrap_unscoped_aggregates(value, report, in_tablix_scope)
        _emit_positioned_textbox(parent, name, value, is_expr, lf, origin_x, origin_y)
        return
    value = _field_value_for(
        lf, report, dataset_name=dataset_name, audit_notes=audit_notes
    )
    if not value:
        value = lf.text or lf.source or ""
        _emit_positioned_textbox(parent, name, value, False, lf, origin_x, origin_y)
        return
    value = _wrap_unscoped_aggregates(value, report, in_tablix_scope)
    _emit_positioned_textbox(parent, name, value, True, lf, origin_x, origin_y)


def _emit_frame(
    parent_items: ET.Element,
    frame: LayoutGroup,
    report: ParsedReport,
    embedded_index: dict,
    parent_x: float,
    parent_y: float,
    dataset_name: str = "",
    audit_notes: Optional[List[str]] = None,
    in_tablix_scope: bool = True,
) -> None:
    # Frames bound to a query introduce their own dataset scope for any
    # &TOKEN / :TOKEN references inside their fields.
    scope_ds = frame.source_query or dataset_name
    if frame.width <= 0 and frame.height <= 0:
        rect_origin_x = parent_x
        rect_origin_y = parent_y
        for f in frame.fields or []:
            _emit_layout_field(parent_items, f, report, embedded_index,
                               rect_origin_x, rect_origin_y,
                               dataset_name=scope_ds, audit_notes=audit_notes,
                               in_tablix_scope=in_tablix_scope)
        for child in frame.children or []:
            _emit_frame(parent_items, child, report, embedded_index,
                        rect_origin_x, rect_origin_y,
                        dataset_name=scope_ds, audit_notes=audit_notes,
                        in_tablix_scope=in_tablix_scope)
        return

    rect = _sub(parent_items, "Rectangle")
    rect.set("Name", f"Rect_{_safe(frame.name) or 'Frame'}")
    # Build the ReportItems into a detached element first; only attach it
    # to the Rectangle if at least one child report item lands there.
    # Per RDL 2008/01 schema <ReportItems> requires >=1 child item and
    # SSRS upload fails with:
    #   "The element 'ReportItems' has incomplete content. List of
    #    possible elements expected: ..."
    # when it's emitted empty.
    inner_items = ET.Element(_q("ReportItems"))

    frame_origin_x = frame.x
    frame_origin_y = frame.y

    for f in frame.fields or []:
        _emit_layout_field(inner_items, f, report, embedded_index,
                           frame_origin_x, frame_origin_y,
                           dataset_name=scope_ds, audit_notes=audit_notes,
                           in_tablix_scope=in_tablix_scope)

    for child in frame.children or []:
        child_kind = (child.kind or "").lower()
        child_scope_ds = child.source_query or scope_ds
        if child_kind in ("frame", "repeating_frame"):
            child_rect = _sub(inner_items, "Rectangle")
            child_rect.set("Name", f"Rect_{_safe(child.name) or 'SubFrame'}")
            # Same deferred-attach pattern as the parent Rectangle: only
            # emit <ReportItems> if there is actual content underneath.
            child_inner = ET.Element(_q("ReportItems"))
            for cf in child.fields or []:
                _emit_layout_field(child_inner, cf, report, embedded_index,
                                   child.x, child.y,
                                   dataset_name=child_scope_ds,
                                   audit_notes=audit_notes,
                                   in_tablix_scope=in_tablix_scope)
            for grand in child.children or []:
                _emit_frame(child_inner, grand, report, embedded_index,
                            child.x, child.y,
                            dataset_name=child_scope_ds,
                            audit_notes=audit_notes,
                            in_tablix_scope=in_tablix_scope)
            if len(list(child_inner)) > 0:
                child_rect.append(child_inner)
            _sub(child_rect, "Top", _in(max(0.0, child.y - frame_origin_y)))
            _sub(child_rect, "Left", _in(max(0.0, child.x - frame_origin_x)))
            _sub(child_rect, "Height", _in(child.height if child.height > 0 else 0.5))
            _sub(child_rect, "Width", _in(child.width if child.width > 0 else 1.0))
            cstyle = _sub(child_rect, "Style")
            child_bg = getattr(child, "background_color", "")
            if child_bg:
                _sub(cstyle, "BackgroundColor", child_bg)
            child_bc = getattr(child, "border_color", "")
            if (child.border_width or 0) > 0:
                cborder = _sub(cstyle, "Border")
                _sub(cborder, "Style", "Solid")
                _sub(cborder, "Color", child_bc or "Black")
                _sub(cborder, "Width", "1pt")
            elif child_bc:
                cborder = _sub(cstyle, "Border")
                _sub(cborder, "Style", "Solid")
                _sub(cborder, "Color", child_bc)
        else:
            _emit_frame(inner_items, child, report, embedded_index,
                        frame_origin_x, frame_origin_y,
                        dataset_name=child_scope_ds,
                        audit_notes=audit_notes,
                        in_tablix_scope=in_tablix_scope)

    # Attach the inner ReportItems only when it has actual content. An
    # empty <ReportItems/> is a schema violation in RDL 2008/01.
    if len(list(inner_items)) > 0:
        # Element ordering in <Rectangle>: ReportItems must come BEFORE
        # Top/Left/Height/Width per the 2008/01 schema sequence.
        rect.append(inner_items)

    rel_x = max(0.0, frame.x - parent_x)
    rel_y = max(0.0, frame.y - parent_y)
    _sub(rect, "Top", _in(rel_y))
    _sub(rect, "Left", _in(rel_x))
    _sub(rect, "Height", _in(frame.height))
    _sub(rect, "Width", _in(frame.width))
    rstyle = _sub(rect, "Style")
    frame_bg = getattr(frame, "background_color", "")
    if frame_bg:
        _sub(rstyle, "BackgroundColor", frame_bg)
    frame_bc = getattr(frame, "border_color", "")
    if (frame.border_width or 0) > 0:
        rborder = _sub(rstyle, "Border")
        _sub(rborder, "Style", "Solid")
        _sub(rborder, "Color", frame_bc or "Black")
        _sub(rborder, "Width", f"{max(0.5, frame.border_width):.2f}pt")
    elif frame_bc:
        rborder = _sub(rstyle, "Border")
        _sub(rborder, "Style", "Solid")
        _sub(rborder, "Color", frame_bc)


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


def _classify_section_main_fields(section_main: LayoutGroup) -> Dict[str, Any]:
    """Walk section_main depth-first and pull out the values we need for a
    simple stacked certificate layout.

    Returns a dict with these keys (any missing value is None / []):
      title_lines      List[str]   - centered multi-line title (>=2 lines preferred)
      title_field      LayoutField - layout field that supplied title_lines
      perm_type        LayoutField - secondary "license type" field
      permit_field     LayoutField - the big PERMIT NUMBER field (bound or text)
      permittee_field  LayoutField - the operator/permittee name field
      site_addr_field  LayoutField - the site address field
      site_text        LayoutField - "is licensed to operate / located at" text block
      perm_dates       LayoutField - the "for the period ..." text block
      error_text       LayoutField - the "ERROR: ..." conditional text
      legal_text       LayoutField - the long static legal disclaimer (>=200 chars)
      signature_text   LayoutField - the signer name / title text block
      transfer_text    LayoutField - the "THIS CERTIFICATE IS NOT TRANSFERABLE" line
      signature_field  LayoutField - the signature image/text field
      card_l_fields    List[LayoutField] - all fields under any M_CARD_L frame
      card_r_fields    List[LayoutField] - all fields under any M_CARD_R frame
    """
    out: Dict[str, Any] = {
        "title_lines": [],
        "title_field": None,
        "perm_type": None,
        "permit_field": None,
        "permittee_field": None,
        "site_addr_field": None,
        "site_text": None,
        "perm_dates": None,
        "error_text": None,
        "legal_text": None,
        "signature_text": None,
        "transfer_text": None,
        "signature_field": None,
        "card_l_fields": [],
        "card_r_fields": [],
    }

    def _is_title(f: LayoutField) -> bool:
        t = (f.text or "")
        if not t.strip():
            return False
        if (f.align or "").lower() != "center":
            return False
        if "\n" not in t:
            return False
        u = t.upper()
        if "STATE OF" in u or "DEPARTMENT" in u or "DIVISION" in u:
            return True
        return int(f.font_size or 0) >= 18

    longest_legal: Optional[LayoutField] = None
    longest_legal_len = 0

    def walk(group: LayoutGroup,
             in_card_l: bool = False,
             in_card_r: bool = False) -> None:
        nonlocal longest_legal, longest_legal_len
        name_upper = (group.name or "").upper()
        if "CARD_L" in name_upper:
            in_card_l = True
        if "CARD_R" in name_upper:
            in_card_r = True

        for f in group.fields or []:
            if in_card_l:
                out["card_l_fields"].append(f)
                continue
            if in_card_r:
                out["card_r_fields"].append(f)
                continue

            kind = (f.kind or "").lower()
            text = (f.text or "")
            src_upper = (f.source or "").upper()
            name_u = (f.name or "").upper()

            if kind == "text":
                if _is_title(f) and not out["title_lines"]:
                    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
                    if lines:
                        out["title_lines"] = lines
                        out["title_field"] = f
                        continue
                up = text.upper()
                if up.startswith("ERROR") and out["error_text"] is None:
                    out["error_text"] = f
                    continue
                if (
                    "FOR THE PERIOD" in up
                    or "&PERM_DATES" in up
                ) and out["perm_dates"] is None:
                    out["perm_dates"] = f
                    continue
                if (
                    ("LICENSED TO OPERATE" in up
                     or "&CP_OPERATE" in up
                     or "&SITE_NAME" in up)
                    and out["site_text"] is None
                ):
                    out["site_text"] = f
                    continue
                if "TRANSFERABLE" in up and out["transfer_text"] is None:
                    out["transfer_text"] = f
                    continue
                if (
                    "&PERMIT" in up
                    and out["permit_field"] is None
                    and int(f.font_size or 0) >= 18
                ):
                    out["permit_field"] = f
                    continue
                if (
                    "WUTMB" in up
                    or "CHIEF" in up
                    or ("&CF_" in up and "SIGN" in name_u)
                    or "BUREAU" in up
                ) and out["signature_text"] is None:
                    out["signature_text"] = f
                    continue
                # Longest static text wins as the legal disclaimer.
                if len(text) > longest_legal_len:
                    longest_legal_len = len(text)
                    longest_legal = f
                continue

            if kind == "field":
                if (
                    src_upper in ("PERMIT", "PERM_NUM")
                    or src_upper.endswith("_PERMIT")
                    or "PERMIT_NUM" in src_upper
                    or src_upper.endswith("_NUM")
                ) and out["permit_field"] is None:
                    out["permit_field"] = f
                    continue
                if (
                    "PERM_TYPE" in src_upper or "PERM_TYPE" in name_u
                ) and out["perm_type"] is None:
                    out["perm_type"] = f
                    continue
                if (
                    "PERMITTEE" in src_upper or "PERMITTEE" in name_u
                ) and out["permittee_field"] is None:
                    out["permittee_field"] = f
                    continue
                if (
                    "SITE_ADDR" in src_upper or "SITE_ADDR" in name_u
                ) and out["site_addr_field"] is None:
                    out["site_addr_field"] = f
                    continue
                if (
                    "SIGNATURE" in src_upper or "SIGNATURE" in name_u
                ) and out["signature_field"] is None:
                    out["signature_field"] = f
                    continue

        for c in group.children or []:
            walk(c, in_card_l=in_card_l, in_card_r=in_card_r)

    walk(section_main)

    if longest_legal is not None and len(longest_legal.text or "") >= 200:
        out["legal_text"] = longest_legal
    return out


def _emit_stacked_textbox(
    parent: ET.Element,
    name: str,
    value: str,
    is_expression: bool,
    top_in: float,
    height_in: float,
    width_in: float = 7.5,
    left_in: float = 0.0,
    font_size: int = 10,
    bold: bool = False,
    align: str = "left",
    can_grow: bool = True,
) -> ET.Element:
    """Emit a simple stacked Textbox at a fixed Top offset inside a Rectangle.

    Differs from _emit_positioned_textbox in that the geometry comes from
    explicit kwargs (so we don't carry over the Oracle inch coordinates
    that pushed the body to ~14in), and the style is a small bundle of
    SSRS-friendly defaults rather than _apply_field_style.
    """
    tb = _sub(parent, "Textbox")
    tb.set("Name", name)
    paragraphs = _sub(tb, "Paragraphs")
    para = _sub(paragraphs, "Paragraph")
    runs = _sub(para, "TextRuns")
    run = _sub(runs, "TextRun")
    _sub(run, "Value", value if (is_expression or value) else "=Nothing")
    run_style = _sub(run, "Style")
    _sub(run_style, "FontSize", f"{int(font_size) if font_size else 10}pt")
    if bold:
        _sub(run_style, "FontWeight", "Bold")
    if align:
        para_style = _sub(para, "Style")
        _sub(para_style, "TextAlign", _ssrs_text_align(align))
    if can_grow:
        _sub(tb, "CanGrow", "true")
    _sub(tb, "KeepTogether", "true")
    _sub(tb, "Top", _in(top_in))
    _sub(tb, "Left", _in(left_in))
    _sub(tb, "Width", _in(width_in))
    _sub(tb, "Height", _in(height_in))
    tb_style = _sub(tb, "Style")
    _sub(tb_style, "PaddingLeft", "2pt")
    _sub(tb_style, "PaddingRight", "2pt")
    _sub(tb_style, "PaddingTop", "1pt")
    _sub(tb_style, "PaddingBottom", "1pt")
    return tb


def _textbox_value_from_field(
    lf: Optional[LayoutField],
    report: ParsedReport,
    dataset_name: str,
) -> Tuple[str, bool]:
    """Resolve a LayoutField to (value, is_expression) using the existing
    token/Field/Parameter resolver. Returns ("=Nothing", True) when lf is
    missing so the placeholder stays clean in PDF export."""
    if lf is None:
        return "=Nothing", True
    kind = (lf.kind or "").lower()
    if kind == "text":
        text = lf.text or lf.source or ""
        if not text.strip():
            return "=Nothing", True
        value, is_expr = _resolve_text_expression(
            text, report, dataset_name=dataset_name, audit_notes=None
        )
        if is_expr:
            value = _wrap_unscoped_aggregates(value, report, True)
            return value, True
        return value, False
    # kind == "field"
    value = _field_value_for(
        lf, report, dataset_name=dataset_name, audit_notes=None
    )
    if not value:
        fallback = lf.text or lf.source or ""
        if not fallback.strip():
            return "=Nothing", True
        return fallback, False
    value = _wrap_unscoped_aggregates(value, report, True)
    return value, True


def _build_certificate_body(
    report: ParsedReport,
    main: DataQuery,
    section_main: LayoutGroup,
) -> Tuple[ET.Element, float, float]:
    """Build a Tablix-wrapped certificate body using a SIMPLE STACKED layout.

    The Oracle XML positions every textbox at an inch-coordinate authored
    for Oracle Reports' renderer; when SSRS lays those out the body ends
    up ~14in tall and one permit splits across four PDF pages. To produce
    a clean 1-page-per-permit PDF we ignore the positional coordinates
    entirely and emit a single Rectangle whose ReportItems are a vertical
    stack of textboxes at hand-picked Top offsets.

    Layout (all widths 7.5in unless noted):
        Title block         (3-line centered bold)          0.00 .. 0.90
        License type        (centered, bold)                0.90 .. 1.15
        Permit number       (big bold centered)             1.15 .. 1.65
        Permittee block     (operator / address)            1.65 .. 2.40
        "Licensed to operate" + facility                    2.40 .. 3.00
        "Located at" + site address                         3.00 .. 3.70
        "For the period" + dates                            3.70 .. 4.30
        Legal disclaimer    (small)                         4.30 .. 7.60
        Transfer notice                                     7.60 .. 7.90
        Signature line + signer block                       7.90 .. 8.70
        Two wallet cards side-by-side (3.5in each, gap)     8.80 .. 10.00
    """
    body = ET.Element(_q("Body"))
    items = _sub(body, "ReportItems")

    embedded_index = {
        _safe(img.id): (img.mime_type or "image/gif")
        for img in (report.embedded_images or [])
        if img.id
    }

    list_width = 7.5
    body_height = 10.0  # total stacked-content height inside the Rectangle

    # Tablix shell (single cell wrapping one Rectangle).
    list_el = _sub(items, "Tablix")
    list_el.set("Name", "Tablix_Permit")

    list_body = _sub(list_el, "TablixBody")
    cols = _sub(list_body, "TablixColumns")
    col = _sub(cols, "TablixColumn")
    _sub(col, "Width", _in(list_width))
    rows = _sub(list_body, "TablixRows")
    row = _sub(rows, "TablixRow")
    _sub(row, "Height", _in(body_height))
    cells = _sub(row, "TablixCells")
    cell = _sub(cells, "TablixCell")
    contents = _sub(cell, "CellContents")

    body_rect = _sub(contents, "Rectangle")
    body_rect.set("Name", "Rect_Body")
    body_items = _sub(body_rect, "ReportItems")

    # --- Pull values out of section_main (no _emit_frame call!) ---
    classified = _classify_section_main_fields(section_main)
    ds = main.name

    # --- Title block (single CanGrow textbox, 3 lines via vbCrLf) ---
    title_lines = classified["title_lines"] or [
        "STATE", "DEPARTMENT", "LICENSE",
    ]
    title_field = classified["title_field"]
    title_lines = [ln for ln in title_lines if not ln.startswith("&")]
    if not title_lines:
        title_lines = ["LICENSE"]
    safe_lines = [_q_safe(ln) for ln in title_lines[:3]]
    title_expr = "=" + " & vbCrLf & ".join(
        '"' + s + '"' for s in safe_lines
    )
    _emit_stacked_textbox(
        body_items, "Tb_Title", title_expr, True,
        top_in=0.00, height_in=0.90, width_in=list_width,
        font_size=(title_field.font_size if title_field else 18) or 18,
        bold=True, align="center",
    )

    # --- License type (perm_type) ---
    pt_val, pt_is_expr = _textbox_value_from_field(
        classified["perm_type"], report, ds
    )
    _emit_stacked_textbox(
        body_items, "Tb_PermType", pt_val, pt_is_expr,
        top_in=0.92, height_in=0.22, width_in=list_width,
        font_size=14, bold=True, align="center",
    )

    # --- PERMIT NUMBER (big bold centered) ---
    perm_val, perm_is_expr = _textbox_value_from_field(
        classified["permit_field"], report, ds
    )
    _emit_stacked_textbox(
        body_items, "Tb_PermitNum", perm_val, perm_is_expr,
        top_in=1.18, height_in=0.45, width_in=list_width,
        font_size=22, bold=True, align="center",
    )

    # --- Permittee block (operator + address) ---
    permittee_val, permittee_is_expr = _textbox_value_from_field(
        classified["permittee_field"], report, ds
    )
    _emit_stacked_textbox(
        body_items, "Tb_Permittee", permittee_val, permittee_is_expr,
        top_in=1.68, height_in=0.70, width_in=list_width,
        font_size=14, bold=True, align="center",
    )

    # --- "Is licensed to operate / located at" combined ---
    site_text_val, site_text_is_expr = _textbox_value_from_field(
        classified["site_text"], report, ds
    )
    _emit_stacked_textbox(
        body_items, "Tb_SiteText", site_text_val, site_text_is_expr,
        top_in=2.42, height_in=0.55, width_in=list_width,
        font_size=11, bold=False, align="center",
    )

    # --- Site address line ---
    site_addr_val, site_addr_is_expr = _textbox_value_from_field(
        classified["site_addr_field"], report, ds
    )
    _emit_stacked_textbox(
        body_items, "Tb_SiteAddr", site_addr_val, site_addr_is_expr,
        top_in=3.00, height_in=0.65, width_in=list_width,
        font_size=14, bold=True, align="center",
    )

    # --- For the period <dates> ---
    dates_val, dates_is_expr = _textbox_value_from_field(
        classified["perm_dates"], report, ds
    )
    _emit_stacked_textbox(
        body_items, "Tb_PermDates", dates_val, dates_is_expr,
        top_in=3.70, height_in=0.55, width_in=list_width,
        font_size=11, bold=False, align="center",
    )

    # --- Legal disclaimer (longest static text) ---
    legal_val, legal_is_expr = _textbox_value_from_field(
        classified["legal_text"], report, ds
    )
    _emit_stacked_textbox(
        body_items, "Tb_Legal", legal_val, legal_is_expr,
        top_in=4.30, height_in=3.25, width_in=list_width,
        font_size=9, bold=False, align="left",
    )

    # --- Transfer / renewal-due notice ---
    transfer_val, transfer_is_expr = _textbox_value_from_field(
        classified["transfer_text"], report, ds
    )
    _emit_stacked_textbox(
        body_items, "Tb_Transfer", transfer_val, transfer_is_expr,
        top_in=7.60, height_in=0.25, width_in=list_width,
        font_size=8, bold=True, align="center",
    )

    # --- Signature line + signer block ---
    sig_val, sig_is_expr = _textbox_value_from_field(
        classified["signature_text"], report, ds
    )
    _emit_stacked_textbox(
        body_items, "Tb_Sig", sig_val, sig_is_expr,
        top_in=7.90, height_in=0.85, width_in=list_width,
        font_size=10, bold=False, align="left",
    )

    # --- Two wallet cards (side-by-side rectangles) ---
    def _emit_card(rect_name: str, card_fields, top_in: float, left_in: float,
                   width_in: float, height_in: float) -> None:
        card = _sub(body_items, "Rectangle")
        card.set("Name", rect_name)
        card_items = _sub(card, "ReportItems")

        # Pick best-known card sub-fields by text content.
        card_lines: List[Tuple[Optional[LayoutField], int, bool]] = []
        # We want: state/year line, permit type, permit number,
        # permittee block, expires line. Use first match by heuristic.
        state_field = None
        type_field = None
        num_field = None
        site_field = None
        exp_field = None
        for f in card_fields:
            t_up = (f.text or "").upper()
            n_up = (f.name or "").upper()
            if "STATE" in t_up and state_field is None:
                state_field = f
                continue
            if "&PERMIT" in t_up and num_field is None:
                num_field = f
                continue
            if ("&PERM_TYPE" in t_up or "PERM_TYPE" in n_up) and type_field is None:
                type_field = f
                continue
            if ("EXPIRES" in t_up or "EXP_DATE" in t_up) and exp_field is None:
                exp_field = f
                continue
            if "&CF_PERMITTEES" in t_up or "CP_OPERATE" in t_up:
                if site_field is None:
                    site_field = f
                continue

        rows_spec = [
            (state_field, 0.00, 0.22, 9, True, "center"),
            (type_field,  0.22, 0.20, 9, True, "center"),
            (num_field,   0.42, 0.30, 12, True, "center"),
            (site_field,  0.72, 0.45, 8, True, "center"),
            (exp_field,   1.17, 0.20, 8, False, "center"),
        ]
        for idx, (fld, top, h, fs, bold, align) in enumerate(rows_spec):
            v, is_expr = _textbox_value_from_field(fld, report, ds)
            _emit_stacked_textbox(
                card_items, f"{rect_name}_Row{idx}", v, is_expr,
                top_in=top, height_in=h, width_in=width_in - 0.2,
                left_in=0.10,
                font_size=fs, bold=bold, align=align,
            )

        _sub(card, "KeepTogether", "true")
        _sub(card, "Top", _in(top_in))
        _sub(card, "Left", _in(left_in))
        _sub(card, "Height", _in(height_in))
        _sub(card, "Width", _in(width_in))
        card_style = _sub(card, "Style")
        # Light border so the wallet card reads as a card
        border = _sub(card_style, "Border")
        _sub(border, "Style", "Solid")
        _sub(border, "Width", "0.5pt")

    _emit_card(
        "Rect_CardL", classified["card_l_fields"],
        top_in=8.80, left_in=0.00, width_in=3.50, height_in=1.40,
    )
    _emit_card(
        "Rect_CardR", classified["card_r_fields"],
        top_in=8.80, left_in=4.00, width_in=3.50, height_in=1.40,
    )

    # Rectangle KeepTogether + geometry (after ReportItems, before Top/etc.).
    _sub(body_rect, "KeepTogether", "true")
    _sub(body_rect, "Top", "0in")
    _sub(body_rect, "Left", "0in")
    _sub(body_rect, "Height", _in(body_height))
    _sub(body_rect, "Width", _in(list_width))
    _sub(body_rect, "Style")

    # Column/row hierarchy and detail group (unchanged structurally).
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
    _sub(list_el, "KeepTogether", "true")
    _sub(list_el, "Top", "0in")
    _sub(list_el, "Left", "0in")
    _sub(list_el, "Height", _in(body_height))
    _sub(list_el, "Width", _in(list_width))
    _sub(list_el, "Style")

    body_height_in = body_height + 0.25
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

    # PageHeader -- intentionally MINIMAL. The page-header title used to
    # duplicate the in-body "STATE OF MONTANA / DEPARTMENT OF ENVIRONMENTAL
    # QUALITY / ..." title block, which on PDF export produced two stacked
    # title blocks per page. The body emits the title (it's part of the
    # certificate layout); the page header now stays empty so each PDF
    # page shows the title exactly once.
    ph = _sub(page, "PageHeader")
    _sub(ph, "Height", "0.25in")
    _sub(ph, "PrintOnFirstPage", "true")
    _sub(ph, "PrintOnLastPage", "true")
    # Note: <ReportItems> must have >=1 child per RDL 2008/01 schema.
    # When we have nothing to put in the header we simply omit
    # <ReportItems> entirely -- it's optional on PageHeader.

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
        # Page footer is OUTSIDE any data region, so aggregate Fields! refs
        # MUST carry an explicit dataset scope or SSRS rejects upload with:
        #   "The Value expression for the image 'Img_Sig' references a field
        #    in an aggregate expression without a scope. A scope is required
        #    for all aggregates in the page header or footer which reference
        #    fields."
        _sub(
            img,
            "Value",
            f'=First(Fields!{_safe(sf)}.Value, "{_safe(sig_q.name)}")',
        )
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


def _build_report_root(report: ParsedReport, target_db: str = "oracle") -> ET.Element:
    root = ET.Element(_q("Report"))
    _rdsub(root, "ReportID", "00000000-0000-0000-0000-000000000000")
    _sub(root, "Description", report.name or "Converted")
    _sub(root, "Author", "Oracle2SSRS")
    _sub(root, "AutoRefresh", "0")
    root.append(_build_data_sources(target_db=target_db))
    root.append(_build_data_sets(report, target_db=target_db))
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


def generate_rdl(report: ParsedReport, target_db: str = "oracle") -> str:
    """Return a complete RDL XML document as a string.

    ``target_db``: ``"oracle"`` (default) emits the original Oracle SQL in
    each <CommandText> with ``:P_PARAM`` bind vars and an ``OracleClient``
    DataProvider. ``"sqlserver"`` emits the translated T-SQL with
    ``@P_PARAM`` bind vars and a ``SQL`` DataProvider.
    """
    target_db = (target_db or "oracle").lower()
    if target_db not in ("oracle", "sqlserver"):
        target_db = "oracle"
    root = _build_report_root(report, target_db=target_db)
    try:
        ET.indent(root, space="  ")
    except AttributeError:
        pass
    body = ET.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="utf-8"?>\n' + body
