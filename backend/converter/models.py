"""
Shared data model for the Oracle -> SSRS converter pipeline.

All five agent modules read/write to these dataclasses, so they stay decoupled.

Pipeline:
    raw bytes (.xml/.rdf)
        -> parsers/oracle_xml.py        -> ParsedReport
        -> translators/plsql_to_tsql.py -> ParsedReport (with .tsql filled in on each query)
        -> generators/rdl.py            -> rdl_xml: str
        -> preview/*                    -> html_mockup, side_by_side, live_data
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Dict, Optional


# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------

@dataclass
class ReportParameter:
    """One Oracle userParameter, normalized."""
    name: str                       # e.g. P_RENEWAL_YEAR
    label: str = ""                 # human label, e.g. "Renewal Year"
    datatype: str = "character"     # character | number | date
    width: int = 0
    precision: int = 0
    initial_value: Optional[str] = None
    input_mask: Optional[str] = None   # e.g. MM/DD/YYYY
    display: bool = True               # display="no" -> False (internal-only)

    @property
    def ssrs_datatype(self) -> str:
        """Map an Oracle Reports parameter datatype to an SSRS DataType.

        Defaults to String for everything except Date/DateTime so the
        SSRS parameter widget accepts a blank input. SSRS's Integer
        widget rejects empty values, which forces the user to type a
        placeholder before they can run the report -- exactly the
        runtime friction perplexity's hand-tweaked RDLs avoid by
        declaring numeric/ID params as String. Oracle's implicit
        type coercion turns the string-form numeric back into a number
        when the bind is forwarded.
        """
        dt = (self.datatype or "").lower()
        if dt in ("date", "datetime", "timestamp"):
            return "DateTime"
        return "String"


# ---------------------------------------------------------------------------
# Data items / fields
# ---------------------------------------------------------------------------

@dataclass
class DataItem:
    """One column emitted by an Oracle dataSource (becomes a Field in RDL)."""
    name: str                       # e.g. Permit
    expression: str = ""            # original Oracle expression
    datatype: str = "vchar2"
    width: int = 0
    label: str = ""
    scale: Optional[int] = None     # Oracle NUMBER scale (digits right of '.')
    precision: Optional[int] = None  # Oracle NUMBER precision (total digits)

    @property
    def ssrs_datatype(self) -> str:
        """Map the Oracle column type to a .NET (SSRS) type name.

        NUMBER is the subtle one: a fixed scale > 0 (or unknown scale) must
        become Decimal, NOT Int32 -- mapping money/rates/percentages to Int32
        truncates the fractional part, and a >9-digit id overflows Int32.
        Only an explicit scale 0 with small precision is a true integer.
        """
        dt = (self.datatype or "").lower()
        if dt in ("date", "datetime", "timestamp"):
            return "System.DateTime"
        if dt in ("float", "real", "double", "binary_float", "binary_double"):
            return "System.Double"
        if dt in ("currency", "money"):
            return "System.Decimal"
        if dt in ("number", "numeric", "decimal", "integer", "int"):
            if self.scale is not None and self.scale > 0:
                return "System.Decimal"
            if dt in ("integer", "int"):
                return "System.Int32"
            # NUMBER with scale 0: small precision is a genuine Int32; unknown
            # or large precision stays Decimal so large ids don't overflow.
            if self.scale == 0 and self.precision is not None and self.precision <= 9:
                return "System.Int32"
            return "System.Decimal"
        return "System.String"


# ---------------------------------------------------------------------------
# Queries / data sources
# ---------------------------------------------------------------------------

@dataclass
class QueryGroup:
    """One Oracle <group> inside a dataSource, preserving the NESTING.

    Oracle Reports nests groups for master-detail (e.g. G_OUTER contains
    G_MID contains G_INNER). Each group owns its own data items and may
    carry <summary> aggregates (e.g. "Total Per Group"). The flat
    DataQuery.items list loses this hierarchy; this tree preserves it so the
    generator can emit a real nested-group Tablix that renders 1:1.
    """
    name: str                       # e.g. G_CNTY_NM
    items: List["DataItem"] = field(default_factory=list)   # items DIRECTLY in this group
    summaries: List[Dict[str, str]] = field(default_factory=list)  # {name, source, function, label}
    children: List["QueryGroup"] = field(default_factory=list)
    break_col: str = ""             # the first data item = this group's break key


@dataclass
class DataQuery:
    """One Oracle dataSource. After the translator runs, .tsql is populated."""
    name: str                       # e.g. Q_MAIN
    sql: str = ""                   # original Oracle SQL
    tsql: str = ""                  # translated T-SQL (filled by translator)
    items: List[DataItem] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)   # translation warnings
    # Nested <group> tree (master-detail hierarchy + summaries). Empty for
    # single-group queries; populated for nested master-detail reports.
    groups: List["QueryGroup"] = field(default_factory=list)
    # Oracle <link parentGroup=... childQuery=... condition=...>: this query
    # is the CHILD (detail) of parent_group; condition "eq" => 1:1. Used so
    # the generator can emit a Lookup() back to the master instead of =Nothing.
    parent_group: str = ""
    link_condition: str = ""
    # Explicit <link parentColumn=.. childColumn=..> join keys to the master
    # (one pair per <link>; composite when a child links on >1 column). These
    # are Oracle's EXACT join keys -- used for a correct cross-dataset Lookup/
    # LookupSet without guessing from column-name stems.
    link_pairs: List = field(default_factory=list)  # [(parent_col, child_col)]
    # Names of the Oracle <group> elements this query owns (e.g. G_MAIN
    # owns group G_STATUS_1). A layout repeating-frame binds to a
    # GROUP name, not the query name, so this lets the generator map a frame
    # back to its exact query instead of guessing by name suffix.
    group_names: List[str] = field(default_factory=list)

    def add_warning(self, msg: str) -> None:
        if msg and msg not in self.notes:
            self.notes.append(msg)


# ---------------------------------------------------------------------------
# Formula columns (PL/SQL functions producing a derived value)
# ---------------------------------------------------------------------------

@dataclass
class FormulaColumn:
    """An Oracle CF_*_F formula. Becomes a calculated field or scalar UDF.

    When this column is actually an Oracle <summary> (a count/sum/avg total),
    ``agg_function`` + ``agg_source`` are set so a &TOKEN reference resolves
    to a REAL SSRS aggregate (=Count(Fields!src.Value, "DS")) instead of a
    NULL placeholder -- so report totals actually compute."""
    name: str                       # e.g. CF_File
    return_type: str = "VARCHAR2"
    plsql_body: str = ""            # original PL/SQL
    tsql_body: str = ""             # translated body (best-effort)
    notes: List[str] = field(default_factory=list)
    agg_function: str = ""          # summary function: count|sum|avg|min|max
    agg_source: str = ""            # the data column being aggregated
    agg_scope: str = ""             # Oracle reset/compute scope: "report" (grand
    #                                 total) or a group name (subtotal). Drives
    #                                 WHERE the total renders (report footer vs a
    #                                 group footer) and its SSRS aggregate scope.


# ---------------------------------------------------------------------------
# Embedded images (binaryData blobs from Oracle layout)
# ---------------------------------------------------------------------------

@dataclass
class EmbeddedImage:
    """A hex-encoded image (state seal etc.) extracted from <image><binaryData>.

    The generator emits these into <EmbeddedImages> at the RDL report root and
    references them from <Image Source="Embedded" Value="<id>">.
    """
    id: str                         # safe identifier (also the RDL EmbeddedImage Name)
    mime_type: str = "image/gif"    # gif | png | jpeg
    hex_data: str = ""              # raw hex string from <binaryData>


# ---------------------------------------------------------------------------
# Layout tree (groups / repeating frames / fields)
# ---------------------------------------------------------------------------

# LayoutField.kind values:
#   "text"  - boilerplate text (literal, may contain &TOKEN substitutions)
#   "field" - data-bound field (source = column / formula / placeholder)
#   "image" - embedded image (image_id refers to an EmbeddedImage on the report)
#   "line"  - decorative line/rule

@dataclass
class LayoutField:
    """A boilerplate or printed field on the layout."""
    name: str
    source: str = ""                # column / formula referenced
    text: str = ""                  # static text if any (for kind="text")
    kind: str = "field"             # text | field | image | line
    image_id: str = ""              # EmbeddedImage.id when kind="image"
    bold: bool = False
    italic: bool = False
    underline: bool = False
    font_size: int = 10
    font_family: str = ""
    color: str = ""                 # text color (e.g. "Red")
    align: str = ""                 # left | center | right
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0
    format_trigger: str = ""        # name of PL/SQL format trigger function
    # Color/style attributes captured from <visualSettings> on the element
    background_color: str = ""       # fillBackgroundColor (CSS-ready, e.g. "#808080")
    foreground_color: str = ""       # fillForegroundColor (alternate fill / accent)
    fill_pattern: str = ""           # "solid" | "transparent" | ""
    border_color: str = ""           # from lineColor / edgeLineColor
    border_width: float = 0.0        # from <visualSettings lineWidth> (pt); a
                                     # drawn <rectangle>/<line>/<box> graphic
    visible: bool = True             # Oracle visible="no" -> a computation-only
                                     # field (feeds a formula/token); never drawn
    # Oracle <webSettings hyperlink="&CF_URL_X">: the formula/placeholder
    # token (leading & stripped) that builds the drill-through URL.
    hyperlink: str = ""
    # Oracle formatMask (e.g. "$NNN,NN0.00", "DD-MON-YYYY") -> SSRS <Format>.
    format_mask: str = ""
    # Oracle rotationAngle (centidegrees, counter-clockwise; e.g. 27000 = 270deg)
    # -> degrees. A sideways window-envelope address prints at 270deg. 0 = upright.
    rotation: float = 0.0
    # Per-segment rich text for a kind="text" boilerplate that mixes fonts within
    # one object (Oracle <textSegment>s, each with its own <font>). Each entry:
    # {"text": str, "bold": bool, "italic": bool, "underline": bool,
    #  "size": int, "color": str}. Empty when the text is uniform. The generator
    # emits one TextRun per segment so e.g. an UNbold caption + a BOLD value on
    # the next line render with their real weights (the license body).
    segments: list = field(default_factory=list)


# LayoutGroup.kind values:
#   "section_header"     - <section name="header">
#   "section_main"       - <section name="main">
#   "section_trailer"    - <section name="trailer">
#   "frame"              - <frame> container (positioned, may have a border)
#   "repeating_frame"    - <repeatingFrame> bound to a data source
#   "_default"           - synthetic catch-all bucket

@dataclass
class LayoutGroup:
    """A repeating frame + its fields. Maps to an RDL Tablix or List."""
    name: str
    kind: str = "_default"
    source_query: str = ""
    fields: List[LayoutField] = field(default_factory=list)
    children: List["LayoutGroup"] = field(default_factory=list)
    # Geometry (frames carry x/y/w/h for positioned RDL emission)
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0
    # Border attributes from <visualSettings lineWidth="N" linePattern="solid"/>
    border_width: float = 0.0
    border_pattern: str = ""
    # Color/style attributes captured from <visualSettings> on the frame
    background_color: str = ""
    foreground_color: str = ""
    fill_pattern: str = ""
    border_color: str = ""
    # Section repeat-on (e.g. main repeats per G_PERMIT row)
    repeat_on: str = ""
    # Format trigger name (PL/SQL function controlling visibility)
    format_trigger: str = ""
    # Matrix (cross-tab) wiring. On kind="matrix" groups: the Oracle
    # attributes linking the matrix to its dimension frames/cross-product
    # group (9.0.2 style: horizontalFrame/verticalFrame/xProductGroup).
    # The 6i style instead nests kind="matrix_col"/"matrix_row"/
    # "matrix_cell" child groups whose fields carry the dimensions/cells.
    matrix_attrs: Dict[str, str] = field(default_factory=dict)
    # Oracle <generalLayout pageBreakBefore="yes"/>: this frame starts a NEW
    # physical page. Load-bearing for reports that pack several logical pages
    # (e.g. a criteria cover + a stat table) into one <section>; without it
    # the preview/RDL stack them on one sheet. The authoritative page-split
    # signal Oracle itself uses.
    page_break_before: bool = False
    # Oracle <generalLayout pageBreakAfter="yes"/>: force a page break AFTER this
    # frame. Used by positional document PACKETS (a memo cover + a data table +
    # a closing letter as sibling frames in one section) to land each on its own
    # sheet; without it the RDL flattens all three onto one page.
    page_break_after: bool = False
    # repeatingFrame printDirection: "down" (one per row, normal), "across"
    # / "acrossDown" (tile labels across then down -> the mailing-label
    # multi-up shape). Drives the label archetype.
    print_direction: str = ""
    # repeatingFrame maxRecordsPerPage: how many master records Oracle prints per
    # physical page. ==1 means ONE record fills a whole page -- the positional
    # FORM/invoice shape (a vendor block + line-item table per record), as
    # opposed to a tabular list (many records stacked per page). 0 = unset.
    max_records_per_page: int = 0


# ---------------------------------------------------------------------------
# Triggers (just text we expose for the side-by-side view)
# ---------------------------------------------------------------------------

@dataclass
class TriggerCode:
    name: str
    body: str = ""


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------

@dataclass
class ParsedReport:
    """Everything we extract from an Oracle Reports artifact."""
    name: str = ""
    dtd_version: str = ""
    parameters: List[ReportParameter] = field(default_factory=list)
    queries: List[DataQuery] = field(default_factory=list)
    formulas: List[FormulaColumn] = field(default_factory=list)
    layout: List[LayoutGroup] = field(default_factory=list)
    triggers: List[TriggerCode] = field(default_factory=list)
    embedded_images: List[EmbeddedImage] = field(default_factory=list)
    # Oracle <graph>/<chart>/<rw:graph> objects. We don't auto-translate the
    # full chart definition (SSRS Chart is a different model), but we MUST
    # surface them so a chart is never silently dropped -- each is
    # {title, plot_value, category, type}.
    charts: List[Dict[str, str]] = field(default_factory=list)
    raw_xml: str = ""               # for the side-by-side view
    warnings: List[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # JSON helpers (used by the API layer to ship data to the frontend)
    # ------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "dtd_version": self.dtd_version,
            "parameters": [p.__dict__ for p in self.parameters],
            "queries": [
                {
                    "name": q.name,
                    "sql": q.sql,
                    "tsql": q.tsql,
                    "items": [i.__dict__ for i in q.items],
                    "notes": q.notes,
                }
                for q in self.queries
            ],
            "formulas": [f.__dict__ for f in self.formulas],
            "layout": [_layout_to_dict(g) for g in self.layout],
            "triggers": [t.__dict__ for t in self.triggers],
            "embedded_images": [
                {"id": img.id, "mime_type": img.mime_type, "size": len(img.hex_data) // 2}
                for img in self.embedded_images
            ],
            "warnings": self.warnings,
        }


def _layout_to_dict(g: LayoutGroup) -> Dict[str, Any]:
    return {
        "name": g.name,
        "kind": g.kind,
        "source_query": g.source_query,
        "x": g.x, "y": g.y, "width": g.width, "height": g.height,
        "border_width": g.border_width,
        "border_pattern": g.border_pattern,
        "background_color": g.background_color,
        "foreground_color": g.foreground_color,
        "fill_pattern": g.fill_pattern,
        "border_color": g.border_color,
        "repeat_on": g.repeat_on,
        "fields": [f.__dict__ for f in g.fields],
        "children": [_layout_to_dict(c) for c in g.children],
    }
