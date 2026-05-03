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
        return {
            "character": "String",
            "number": "Integer",
            "date": "DateTime",
        }.get(self.datatype.lower(), "String")


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

    @property
    def ssrs_datatype(self) -> str:
        dt = (self.datatype or "").lower()
        if dt in ("number", "integer"):
            return "System.Int32"
        if dt == "date":
            return "System.DateTime"
        return "System.String"


# ---------------------------------------------------------------------------
# Queries / data sources
# ---------------------------------------------------------------------------

@dataclass
class DataQuery:
    """One Oracle dataSource. After the translator runs, .tsql is populated."""
    name: str                       # e.g. Q_PERMIT
    sql: str = ""                   # original Oracle SQL
    tsql: str = ""                  # translated T-SQL (filled by translator)
    items: List[DataItem] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)   # translation warnings

    def add_warning(self, msg: str) -> None:
        if msg and msg not in self.notes:
            self.notes.append(msg)


# ---------------------------------------------------------------------------
# Formula columns (PL/SQL functions producing a derived value)
# ---------------------------------------------------------------------------

@dataclass
class FormulaColumn:
    """An Oracle CF_*_F formula. Becomes a calculated field or scalar UDF."""
    name: str                       # e.g. CF_File
    return_type: str = "VARCHAR2"
    plsql_body: str = ""            # original PL/SQL
    tsql_body: str = ""             # translated body (best-effort)
    notes: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Layout tree (groups / repeating frames / fields)
# ---------------------------------------------------------------------------

@dataclass
class LayoutField:
    """A boilerplate or printed field on the layout."""
    name: str
    source: str = ""                # column / formula referenced
    text: str = ""                  # static text if any
    bold: bool = False
    font_size: int = 10
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0


@dataclass
class LayoutGroup:
    """A repeating frame + its fields. Maps to an RDL Tablix or List."""
    name: str
    source_query: str = ""
    fields: List[LayoutField] = field(default_factory=list)
    children: List["LayoutGroup"] = field(default_factory=list)


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
            "warnings": self.warnings,
        }


def _layout_to_dict(g: LayoutGroup) -> Dict[str, Any]:
    return {
        "name": g.name,
        "source_query": g.source_query,
        "fields": [f.__dict__ for f in g.fields],
        "children": [_layout_to_dict(c) for c in g.children],
    }
