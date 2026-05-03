"""
SSRS RDL XML generator.

Public API:
    generate_rdl(report: ParsedReport) -> str

Produces a well-formed RDL 2008+ document that opens in SSRS Report Builder
without parsing errors. The output reflects the source: parameter list,
data fields, and table columns. Pixel-perfect rendering is a stretch goal.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Iterable, List, Optional, Set, Tuple

from converter.models import (
    DataItem,
    DataQuery,
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


def _build_tablix(report: ParsedReport, main: DataQuery) -> ET.Element:
    columns = _column_names_for_main(report, main)
    if not columns:
        columns = list(DEFAULT_COLUMNS)

    tablix = ET.Element(_q("Tablix"))
    tablix.set("Name", "Tablix_Main")

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
    # Detail member
    detail_mem = _sub(row_members, "TablixMember")
    _sub(detail_mem, "Group").set("Name", "Details_Main")
    # (no GroupExpressions == the detail group)

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


def _build_body(report: ParsedReport, main: Optional[DataQuery]) -> ET.Element:
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

def _build_page(report: ParsedReport) -> ET.Element:
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
    _sub(pf, "Height", "0.3in")
    _sub(pf, "PrintOnFirstPage", "true")
    _sub(pf, "PrintOnLastPage", "true")
    pf_items = _sub(pf, "ReportItems")
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

    # Page geometry
    _sub(page, "PageHeight", "11in")
    _sub(page, "PageWidth", "8.5in")
    _sub(page, "LeftMargin", "0.5in")
    _sub(page, "RightMargin", "0.5in")
    _sub(page, "TopMargin", "0.5in")
    _sub(page, "BottomMargin", "0.5in")
    _sub(page, "Style")
    return page


# ---------------------------------------------------------------------------
# Code (helper VB functions)
# ---------------------------------------------------------------------------

CODE_BLOCK = """\
Public Function FormatDateSafe(d As Object) As String
    If IsNothing(d) OrElse IsDBNull(d) Then
        Return ""
    End If
    Try
        Return CDate(d).ToString("MMMM d, yyyy")
    Catch
        Return d.ToString()
    End Try
End Function

Public Function NullToEmpty(s As Object) As String
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


# ---------------------------------------------------------------------------
# Top-level assembly
# ---------------------------------------------------------------------------

def _build_report_root(report: ParsedReport) -> ET.Element:
    root = ET.Element(_q("Report"))

    # Required descriptive metadata first
    _rdsub(root, "ReportID", "00000000-0000-0000-0000-000000000000")
    _sub(root, "Description", report.name or "Converted Oracle Report")
    _sub(root, "Author", "Oracle2SSRS")
    _sub(root, "AutoRefresh", "0")

    # DataSources, DataSets
    root.append(_build_data_sources())
    root.append(_build_data_sets(report))

    # ReportParameters (optional)
    rps = _build_report_parameters(report)
    if rps is not None:
        root.append(rps)

    # ReportParametersLayout - omitted (optional)

    # Body
    main = _pick_main_query(report)
    root.append(_build_body(report, main))

    # Page-level sizing on Report root
    _sub(root, "Width", "8.5in")

    # Page (header/footer + size)
    root.append(_build_page(report))

    # Code section
    root.append(_build_code())

    # Language (helps Report Builder open silently)
    _sub(root, "Language", "en-US")
    _rdsub(root, "DrawGrid", "true")
    _rdsub(root, "GridSpacing", "0.083333in")
    return root


def generate_rdl(report: ParsedReport) -> str:
    """Return a complete RDL XML document as a string."""
    root = _build_report_root(report)

    # Pretty-print (Python 3.9+)
    try:
        ET.indent(root, space="  ")
    except AttributeError:
        pass

    body = ET.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="utf-8"?>\n' + body


# ---------------------------------------------------------------------------
# Self-test (runs only when invoked directly)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    sample = ParsedReport(
        name="MVWF_PERMIT",
        parameters=[
            ReportParameter(name="P_RENEWAL_YEAR", label="Renewal Year",
                            datatype="number"),
            ReportParameter(name="P_PERM_NAME", label="Permit",
                            datatype="character"),
        ],
        queries=[
            DataQuery(
                name="Q_PERMIT",
                tsql="SELECT * FROM Permit WHERE Perm_Name = @P_PERM_NAME",
                items=[
                    DataItem(name="Permit", datatype="character"),
                    DataItem(name="Renewal_Year", datatype="number"),
                    DataItem(name="Site_Name", datatype="character"),
                ],
            )
        ],
    )
    out = generate_rdl(sample)
    print(out[:1200])
    ET.fromstring(out)
    print("OK,", len(out), "chars")
