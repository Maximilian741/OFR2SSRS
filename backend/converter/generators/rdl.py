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

# Oracle SQL bind variables / T-SQL @-parameters. We MUST NOT hardcode the
# "P_" prefix: real Oracle Reports use a mix of P_*, PARM_*, IN_*, V_*,
# and bare-name conventions per shop. Match any identifier that follows
# the sigil so the converter generalizes across reports.
#
# The patterns deliberately exclude PL/SQL token sequences that look like
# bind vars but aren't (e.g. "::TYPE" cast operator, "name:" label). The
# leading sigil must be at a word boundary; the identifier must start
# with a letter or underscore.
_QUERY_PARAM_RE = re.compile(r"(?<![@\w])@([A-Za-z_]\w*)")
_ORACLE_BIND_VAR_RE = re.compile(r"(?<![:\w])\:([A-Za-z_]\w*)")


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



def _alias_select_items(sql: str, item_names) -> str:
    """Ensure every item in the top-level SELECT list has an SQL alias
    matching the dataset Field name we declared in the RDL.

    Why this is mandatory for SSRS: when the user clicks "Refresh Fields"
    in Report Builder, SSRS rebuilds the dataset's Fields collection from
    the column names Oracle returns. For a simple column like VMH.CVID,
    Oracle returns "CVID" and Report Builder agrees with our <Field
    Name="CVID"> declaration. But for an expression like
    UPPER(ST.STAT_TYPE_DESC) with no alias, Oracle returns the column
    name as the verbatim expression text. Report Builder normalizes that
    one way; our converter normalized it another way. After a Refresh
    Fields the dataset Field is named one thing and every Tablix cell
    still references the other -> "Report item expressions can only
    refer to fields within the current dataset scope" and Save is blocked.

    The fix is what every hand-tweaked working RDL does: add
    ``AS <field_name>`` to each unaliased SELECT item so Oracle returns
    exactly the column name we declared. Pure string rewrite, no SQL
    parser needed. Driven entirely by ``item_names`` (the order the
    parser populated query.items) -- no per-report knowledge anywhere.
    """
    if not sql or not item_names:
        return sql

    upper = sql.upper()
    sel_idx = upper.find("SELECT")
    if sel_idx < 0:
        return sql

    depth = 0
    from_idx = -1
    i = sel_idx + len("SELECT")
    while i < len(sql):
        ch = sql[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0 and upper[i:i + 4] == "FROM" and (
            i + 4 == len(sql) or not sql[i + 4].isalnum()
        ):
            from_idx = i
            break
        i += 1
    if from_idx < 0:
        return sql

    sel_body = sql[sel_idx + len("SELECT"):from_idx]
    parts = []
    cur = []
    depth = 0
    for ch in sel_body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if cur:
        parts.append("".join(cur))

    new_parts = []
    for idx, raw in enumerate(parts):
        stripped = raw.strip()
        if not stripped:
            new_parts.append(raw)
            continue
        # Already aliased? Oracle SQL allows BOTH explicit "expr AS name"
        # AND implicit "expr name" (a bare trailing identifier). We must
        # detect both -- the implicit form previously slipped through and
        # got double-aliased ("Pkg_JV_Util.F(...) Violations AS DERIVED").
        # The implicit form is recognized as: ")" or a word char, then
        # whitespace, then an identifier at end of item.
        if re.search(r"\bAS\b\s+[A-Za-z_][A-Za-z0-9_]*\s*$", stripped, re.IGNORECASE):
            new_parts.append(raw)
            continue
        if re.search(r"(?:\)|[A-Za-z0-9_])\s+[A-Za-z_][A-Za-z0-9_]*\s*$", stripped):
            # Trailing identifier following ")" or another word char =
            # implicit Oracle alias. Don't add a second one.
            # Caveat: a bare "TABLE.COL" also matches, but bare column
            # refs were already short-circuited above.
            new_parts.append(raw)
            continue
        # Bare column ref (TABLE.COL or COL) -- Oracle returns the COL
        # part as the column name, which matches what our parser
        # already extracted as item.name. No alias needed.
        if re.fullmatch(
            r"\s*[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?\s*",
            stripped,
        ):
            new_parts.append(raw)
            continue
        # Derive a deterministic alias from the expression itself --
        # the same normalization Oracle Reports uses when it auto-names
        # an unaliased column (strip non-word chars, uppercase, collapse
        # runs of underscores). This guarantees the alias matches the
        # <Field Name="..."> our parser declared, regardless of where
        # item_names puts this item in its own ordering.
        derived = re.sub(r"[^A-Za-z0-9]+", "_", stripped).strip("_").upper()
        # Avoid SQL-reserved or absurd aliases. Cap at 30 chars
        # (Oracle pre-12c identifier limit) and ensure it starts with
        # a letter.
        if not derived or not re.match(r"^[A-Za-z]", derived):
            new_parts.append(raw)
            continue
        derived = derived[:30]
        alias = derived
        # Preserve trailing whitespace so re-join doesn't shift formatting.
        m_trail = re.match(r"(.*?)(\s*)$", raw, re.DOTALL)
        body_part, trail = m_trail.group(1), m_trail.group(2)
        new_parts.append(f"{body_part} AS {alias}{trail}")

    return sql[:sel_idx + len("SELECT")] + ",".join(new_parts) + sql[from_idx:]


def _make_ssrs_oracle_compatible(sql: str, param_types: dict) -> str:
    """Rewrite an Oracle SQL CommandText so it actually runs through the
    SSRS Oracle data extension.

    Oracle Reports has its own bind layer that auto-coerces date binds and
    handles legacy (+) outer joins flawlessly. SSRS's Oracle extension does
    NOT. Two specific rewrites make the difference between "runs" and
    "Query execution failed for dataset Q_1":

    1. Every reference to a DateTime-typed bind ``:P`` is rewritten to
       ``TO_DATE(:P, 'YYYY-MM-DD')``. Without this, SSRS passes the
       parameter as a typed DateTime and Oracle's NVL / BETWEEN combinations
       can blow up with ORA-00932 / ORA-01843 when the bind is NULL.
       This mirrors the perplexity-rebuilt RDLs the user has confirmed
       working in their SSRS instance.

    2. Oracle ``(+)`` outer joins are left untouched here -- they are
       syntactically valid Oracle SQL and SSRS's data extension forwards
       them verbatim. We document the convention so callers can swap in a
       proper (+) -> ANSI LEFT JOIN rewrite later without changing the
       call sites.

    The function is purely a string rewrite driven by ``param_types`` (a
    dict of ``{bind_name: ssrs_datatype}``). NOTHING is hard-coded -- if
    no DateTime params exist, the SQL is returned unchanged.
    """
    if not sql or not param_types:
        return sql

    # Identify which bind names map to DateTime. Build a case-insensitive
    # lookup so :PARM_RECVD_START_DT in the SQL matches PARM_RECVD_START_DT
    # in the parameter list regardless of declared casing.
    date_binds = {name.upper() for name, dtype in param_types.items()
                  if (dtype or "").lower() == "datetime"}
    if not date_binds:
        return sql

    # For each :BIND reference, wrap it only if:
    #   * the bind is in date_binds
    #   * it is NOT already inside a TO_DATE(:BIND, ...) call (avoid
    #     double-wrapping)
    # We do this in one pass with a regex callback.
    bind_re = re.compile(r":([A-Za-z_][A-Za-z0-9_]*)")

    def _wrap(match: "re.Match") -> str:
        name = match.group(1)
        if name.upper() not in date_binds:
            return match.group(0)
        # Already wrapped? Peek 7 chars before the match for "TO_DATE".
        start = match.start()
        prefix = sql[max(0, start - 8):start].upper()
        if prefix.rstrip().endswith("TO_DATE("):
            return match.group(0)
        return f"TO_DATE(:{name}, 'YYYY-MM-DD')"

    return bind_re.sub(_wrap, sql)


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
    """Walk the layout tree, return ordered column names bound to query_name.

    Layout repeating-frames carry the GROUP name (G_FOO) in source_query;
    queries are named Q_FOO. We match either an exact hit or the Oracle
    Q_/G_ convention via the shared suffix (see _query_matches_layout_ref).
    """
    cols: List[str] = []
    seen: Set[str] = set()
    # Build a stub DataQuery so we can reuse the suffix-matching helper.
    target_stub = DataQuery(name=query_name or "")

    def walk(group: LayoutGroup) -> None:
        if group.source_query and _query_matches_layout_ref(
            target_stub, group.source_query
        ):
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
    """Pick the report's primary dataset using structural signals only.

    Heuristic (no per-report name hardcoding): the query with the most
    DataItems is the main one. Oracle Reports puts the bulk of the SELECT
    list in one query and uses smaller satellite queries for sub-bands
    (org, signature, etc.). Ties are broken by source-order so a single
    placeholder query (e.g. when there's only one) still works.
    """
    queries = report.queries or []
    if not queries:
        return None
    return max(queries, key=lambda q: (len(q.items or []), -queries.index(q)))


def _layout_group_query_names(report: ParsedReport) -> Set[str]:
    """Collect every source_query referenced anywhere in the layout tree.

    Returns an upper-cased set. Used by detail/signature pickers to map
    layout repeating-frame bindings back to query objects regardless of
    the Q_/G_ naming convention (Oracle pairs Q_FOO with G_FOO; matching
    is done by the longest common suffix).
    """
    names: Set[str] = set()

    def walk(g: LayoutGroup) -> None:
        if g.source_query:
            names.add(g.source_query.upper())
        for c in g.children or []:
            walk(c)

    for g in report.layout or []:
        walk(g)
    return names


def _query_matches_layout_ref(q: DataQuery, layout_ref: str) -> bool:
    """True when layout_ref names the same data group as DataQuery q.

    Oracle convention: Q_FOO query <-> G_FOO group. We match by the
    portion AFTER the first underscore, case-insensitive, so Q_PERMIT
    matches G_PERMIT and Q_1 matches G_1 without baking names in.
    """
    if not layout_ref:
        return False
    lref = layout_ref.upper()
    qname = (q.name or "").upper()
    if lref == qname:
        return True
    q_tail = qname.split("_", 1)[1] if "_" in qname else qname
    l_tail = lref.split("_", 1)[1] if "_" in lref else lref
    return bool(q_tail) and q_tail == l_tail


def _pick_detail_query(report: ParsedReport, main_name: str) -> Optional[DataQuery]:
    """Pick a master-detail secondary query using layout structure.

    Structural rule: walk the layout. Any repeating_frame whose parent
    chain contains a repeating_frame bound to ``main_name`` and whose
    own source_query maps to a DIFFERENT DataQuery is the detail. This
    captures the master-detail pattern without hardcoding names.

    Fallbacks (in order, so genericity stays graceful):
      1. Any query bound by a repeating_frame anywhere in the layout
         that isn't the main query.
      2. The first non-main query in source order.
    """
    queries = report.queries or []
    if not queries:
        return None
    main_upper = (main_name or "").upper()

    others = [q for q in queries if q.name.upper() != main_upper]
    if not others:
        return None

    def find_nested_under_main(group: LayoutGroup, under_main: bool) -> Optional[DataQuery]:
        kind = (group.kind or "").lower()
        bound_to_main = bool(group.source_query) and any(
            _query_matches_layout_ref(q, group.source_query)
            for q in queries
            if q.name.upper() == main_upper
        )
        now_under_main = under_main or (kind == "repeating_frame" and bound_to_main)
        if (
            now_under_main
            and kind == "repeating_frame"
            and group.source_query
            and not bound_to_main
        ):
            for q in others:
                if _query_matches_layout_ref(q, group.source_query):
                    return q
        for child in group.children or []:
            hit = find_nested_under_main(child, now_under_main)
            if hit is not None:
                return hit
        return None

    for g in report.layout or []:
        hit = find_nested_under_main(g, False)
        if hit is not None:
            return hit

    # Fallback 1: any non-main query referenced by some repeating_frame.
    layout_refs = _layout_group_query_names(report)
    for q in others:
        if any(_query_matches_layout_ref(q, r) for r in layout_refs):
            return q

    # Fallback 2: first non-main query in source order.
    return others[0]


def _pick_signature_query(report: ParsedReport) -> Optional[DataQuery]:
    """Pick a query that supplies an image/signature blob.

    Structural signals (no name hardcoding):
      * The layout contains an ``Image`` field whose source matches one
        of the query's DataItems.
      * Failing that, a single-item query whose lone field is not also
        the main/detail group key (a small query like Q_SIGNATURE that
        produces just a BLOB).

    Returns None when no plausible candidate exists; callers must handle.
    """
    queries = report.queries or []
    if not queries:
        return None

    # Build a set of every field source referenced by an Image layout
    # field anywhere in the report.
    image_sources: Set[str] = set()

    def walk(g: LayoutGroup) -> None:
        for f in g.fields or []:
            if (f.kind or "").lower() == "image":
                src = (f.source or f.image_id or "").upper()
                if src:
                    image_sources.add(src)
        for c in g.children or []:
            walk(c)

    for g in report.layout or []:
        walk(g)

    if image_sources:
        for q in queries:
            for it in q.items or []:
                if (it.name or "").upper() in image_sources:
                    return q

    # Structural fallback: smallest query (typically a single-item BLOB
    # carrier). Skip the main query so we don't return it twice.
    main = _pick_main_query(report)
    candidates = [q for q in queries if q is not main and q.items]
    if not candidates:
        return None
    return min(candidates, key=lambda q: len(q.items or []))


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
    """Emit <DataSources> as a SHARED DataSourceReference (NOT an embedded
    connection-properties block).

    Why this shape: two perplexity-rebuilt RDLs the user got running in
    SSRS without the "Define Query Parameters" dialog popping at Refresh
    Fields BOTH used the same DataSource structure -- a reference to a
    named shared data source on the report server, with cached creds.
    The embedded ``<ConnectionProperties>`` form forces SSRS to evaluate
    query parameters at design time (it doesn't have cached credentials
    to use) and pops the prompt. A ``<DataSourceReference>`` lets SSRS
    use the shared DS's stored credentials silently.

    The user's workflow already swaps the data source post-upload to
    point at the right shared DS in their folder. Emitting a placeholder
    name here just means they confirm/repoint it as a single step, which
    is what they were already doing.

    ``target_db`` is preserved on the signature because _build_dataset
    still uses it for the CommandText flavor (Oracle SQL vs T-SQL).
    """
    ds_root = ET.Element(_q("DataSources"))
    ds = _sub(ds_root, "DataSource")
    # Placeholder name. User repoints to their actual shared DS on the
    # report server (e.g. "BETA") via Report Builder's Data Source
    # Properties dialog. SSRS only cares that the reference NAME exists
    # in the deployed folder; the local placeholder string doesn't matter.
    ds.set("Name", "SharedDataSource")
    _sub(ds, "DataSourceReference", "SharedDataSource")
    _rdsub(ds, "SecurityType", "None")
    # Placeholder GUID; SSRS regenerates this when the data source is
    # actually wired up at deploy time. Keeping a stable value here so
    # the RDL diff stays clean across regenerations.
    _rdsub(ds, "DataSourceID", "00000000-0000-0000-0000-000000000001")
    return ds_root


# ---------------------------------------------------------------------------
# DataSets
# ---------------------------------------------------------------------------

def _build_dataset(query: DataQuery, declared_params: Iterable[str],
                   target_db: str = "oracle",
                   param_types: Optional[dict] = None) -> ET.Element:
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
    # Must match the <DataSource Name="..."> in _build_data_sources. The
    # data source is emitted as a SHARED reference named "SharedDataSource";
    # SSRS rejects the RDL at upload with "dataset Q_X refers to the data
    # source DS_Main, which does not exist" if these don't match.
    _sub(q_el, "DataSourceName", "SharedDataSource")

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
        # Strip trailing semicolons + whitespace. Oracle Reports XMLs preserve
        # the SQL the developer typed in SQL*Plus / PL/SQL, often ending with
        # ";". The SSRS Oracle data extension sends each CommandText as a
        # single statement via ADO.NET; Oracle's parser rejects the trailing
        # ";" with ORA-00933 "SQL command not properly ended" at refresh
        # fields + report execution. User verified this is the actual issue
        # (the source-of-truth RDL happened to also have it but the user
        # works around it manually). Strip ALL trailing ";" + whitespace.
        cmd_text = re.sub(r"[;\s]+$", "", cmd_text)
        # SSRS Oracle extension compatibility: wrap every :DATE_PARAM bind
        # reference in TO_DATE(:P, 'YYYY-MM-DD'). Without this rewrite,
        # NULL DateTime binds cause "Query execution failed for dataset Q_1"
        # at runtime. This is purely type-driven from param_types -- no
        # report-specific bind names are hardcoded.
        if param_types:
            cmd_text = _make_ssrs_oracle_compatible(cmd_text, param_types)
        # Alias unaliased SELECT expressions so Refresh Fields produces
        # column names matching our <Field Name="..."> declarations.
        # Without this, expressions like UPPER(col) come back from Oracle
        # as the verbatim expression text and Tablix cells lose their
        # field bindings on Save in Report Builder.
        item_names = [it.name for it in (query.items or [])]
        if item_names:
            cmd_text = _alias_select_items(cmd_text, item_names)
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
            # Case-insensitive lookup mapping bind-var spelling back to the
            # canonical declared ReportParameter name. SSRS Parameters! refs
            # are case-sensitive, so a bind :PARM_X_id must resolve to the
            # declared PARM_X_ID. Empty <Value/> means the bind passes NULL
            # at runtime -- correct for the common Oracle pattern
            # WHERE :P IS NULL OR col IN (:P).
            canonical = {p.upper(): p for p in declared_params}
            for pname in referenced:
                qp = _sub(qp_root, "QueryParameter")
                qp.set("Name", f":{pname}")
                canon = canonical.get(pname.upper())
                if canon:
                    _sub(qp, "Value", f"=Parameters!{canon}.Value")
                else:
                    _sub(qp, "Value", "")
    else:
        # T-SQL path: existing behavior. Prefer .tsql, fall back to .sql.
        cmd_text = (query.tsql or query.sql or "").strip()
        if not cmd_text:
            cmd_text = f"-- empty query for {query.name}"
        # Same trailing-semicolon strip as Oracle path (Oracle Reports XMLs
        # can carry a ";" in the SQL whether we target Oracle or SQL Server).
        cmd_text = re.sub(r"[;\s]+$", "", cmd_text)
        _sub(q_el, "CommandText", cmd_text)

        # <QueryParameters> from @P_FOO references in tsql
        referenced = _detect_query_parameters(cmd_text)
        if referenced:
            qp_root = _sub(q_el, "QueryParameters")
            canonical = {p.upper(): p for p in declared_params}
            for pname in referenced:
                qp = _sub(qp_root, "QueryParameter")
                qp.set("Name", f"@{pname}")
                # Bind to the report parameter if it exists, otherwise just empty
                canon = canonical.get(pname.upper())
                if canon:
                    _sub(qp, "Value", f"=Parameters!{canon}.Value")
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
    # Build a {bind_name -> ssrs_datatype} map so the dataset builder can
    # apply type-driven SQL rewrites (e.g. TO_DATE-wrap DateTime binds)
    # without any per-report knowledge.
    param_types = {p.name: _ssrs_param_type(p) for p in report.parameters or []}
    if not report.queries:
        # Always emit at least one dataset so the Tablix has something to bind.
        # For Oracle target we put the placeholder in .sql so CommandText
        # comes through unchanged; for sqlserver we put it in .tsql.
        placeholder_sql = "SELECT 1 AS Permit, 0 AS Renewal_Year, '' AS Site_Name"
        # Use a neutral placeholder name (NOT Q_PERMIT) so the converter
        # stays generic across reports. Columns are equally generic.
        placeholder = DataQuery(
            name="Q_MAIN",
            sql=placeholder_sql,
            tsql=placeholder_sql,
            items=[
                DataItem(name="Permit", datatype="character"),
                DataItem(name="Renewal_Year", datatype="number"),
                DataItem(name="Site_Name", datatype="character"),
            ],
        )
        root.append(_build_dataset(placeholder, declared, target_db=target_db,
                                   param_types=param_types))
        return root

    for q in report.queries:
        root.append(_build_dataset(q, declared, target_db=target_db,
                                   param_types=param_types))
    return root


# ---------------------------------------------------------------------------
# ReportParameters
# ---------------------------------------------------------------------------

def _default_value_text(p, dtype: str) -> str:
    """Pick a DefaultValue Value text for the parameter's declared DataType.

    Empirically (verified against a working hand-tweaked RDL the user
    deployed successfully):
      * String   -> '' (empty string; emits an empty <Value/>)
      * Integer/Float/DateTime/Boolean -> '=Nothing' (VB null literal)

    We previously tried concrete literals (0, 1/1/1900, false) hoping to
    suppress Report Builder's "Define Query Parameters" dialog. That dialog
    appears regardless — it's SSRS's design-time caching prompt and the
    user just clicks "Pass NULL" + OK on first refresh. But concrete-literal
    defaults BROKE actual runtime execution: SSRS would pass 0 / 1/1/1900 /
    false to Oracle instead of NULL, making the (:P IS NULL OR col IN (:P))
    branch never match and returning empty result sets.

    Nullable=true on the parameter + '=Nothing' default lets SSRS pass NULL
    correctly at runtime. AllowBlank=true on String params lets the user
    submit an empty value that SSRS also forwards as NULL."""
    iv = (p.initial_value or "").strip() if p.initial_value else ""
    if iv:
        return iv  # explicit default from the source XML; keep verbatim
    dt = (dtype or "String").strip()
    if dt == "String":
        return ""               # empty Value element = NULL/empty for String
    return "=Nothing"           # all non-String types use VB null literal


def _augment_parameters_from_binds(report: ParsedReport) -> None:
    """Add a ReportParameter for any bind variable that is referenced in a
    query's SQL but NOT declared in the source XML's <userParameter> list.

    Oracle Reports tolerates "implicit" binds -- a sub-query can reference
    a :BIND without the report ever declaring it as a user parameter. Oracle Reports' bind layer just routes those binds to
    column values or session context. SSRS does NOT tolerate this: every
    QueryParameter in a DataSet must point to a declared ReportParameter
    via =Parameters!X.Value, or the dataset binds to an empty string and
    silently returns no rows.

    Perplexity's hand-tweaked RDLs auto-declare these as
    String / AllowBlank=true (we verified this against the user's known-
    working RDLs the user staged as source-of-truth fixtures). We replicate that pattern
    here so the generator produces an SSRS-runnable RDL for any Oracle
    Reports XML, no per-report knowledge required.
    """
    if not report.queries:
        return
    declared = {p.name.upper() for p in (report.parameters or [])}
    seen_extra: Set[str] = set()
    for q in report.queries:
        for sql in (q.sql, q.tsql):
            if not sql:
                continue
            for bind in _detect_oracle_bind_vars(sql):
                up = bind.upper()
                if up in declared or up in seen_extra:
                    continue
                seen_extra.add(up)
                # Synthesize a String/AllowBlank parameter using the bind
                # name verbatim. datatype="character" maps to ssrs_datatype
                # "String" via models.ReportParameter; AllowBlank handling
                # happens later in _build_report_parameters.
                rp = ReportParameter(
                    name=bind,
                    datatype="character",
                    label=bind,
                    display=True,
                )
                report.parameters.append(rp)


def _build_report_parameters(report: ParsedReport) -> Optional[ET.Element]:
    """Emit <ReportParameters> matching the perplexity-rebuilt RDL pattern.

    Each parameter gets exactly what is needed to make it optional at
    run time so the user can fill in only the fields they care about:

      * String  -> <AllowBlank>true</AllowBlank>     (empty input -> NULL)
      * other   -> <Nullable>true</Nullable>         (blank field -> NULL)

    No <DefaultValue> is emitted -- that's what suppresses the
    "Define Query Parameters" dialog at Refresh-Fields time and lets the
    runtime user leave any combination of fields blank. The SQL
    CommandText handles the rest via the standard
    (:P IS NULL OR col = :P) pattern, so an unfilled parameter widens
    the result set instead of erroring or returning nothing.

    SSRS 2008/01 schema element order for ReportParameter is:
    DataType -> Nullable -> DefaultValue -> AllowBlank -> Prompt -> Hidden.
    """
    if not report.parameters:
        return None
    root = ET.Element(_q("ReportParameters"))
    for p in report.parameters:
        rp = _sub(root, "ReportParameter")
        rp.set("Name", p.name)
        ptype = _ssrs_param_type(p)
        _sub(rp, "DataType", ptype)
        # Nullable is valid for every type EXCEPT String; AllowBlank is
        # valid ONLY for String. Either makes the param optional at
        # runtime -- SSRS forwards an unfilled value as NULL into the
        # SQL's (:P IS NULL OR col = :P) guards.
        if ptype == "String":
            _sub(rp, "AllowBlank", "true")
        else:
            _sub(rp, "Nullable", "true")
        # Use the parameter's declared name verbatim as the prompt; SSRS
        # auto-renders underscores as spaces in the parameter form.
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
    """Return the first LayoutGroup whose source_query matches query_name.

    Match accepts the Oracle Q_/G_ convention (Q_PERMIT <-> G_PERMIT) so
    we don't have to bake the prefix translation into every call site.
    """
    if not query_name:
        return None
    target_stub = DataQuery(name=query_name)

    def walk(group: LayoutGroup) -> Optional[LayoutGroup]:
        if group.source_query and _query_matches_layout_ref(
            target_stub, group.source_query
        ):
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
    """Return the section_main LayoutGroup ONLY if the report is shaped like a
    positional/letter layout that benefits from the stacked-Tablix
    certificate body.

    A tabular report ALSO has a section_main with frame children -- the
    detection used to be "has section_main + at least one frame" which
    routed every single Oracle Reports XML to the cert path. That fills
    the body with positional textbox stacks pulled from the layout
    fields, which is wrong for reports whose primary deliverable is a
    flat data grid. Those reports
    need _build_body's flat Tablix instead.

    Shape-based gate (no per-report hardcoding):

      * A repeating-frame layout (kind contains "repeating") OR a
        single large frame that hosts a tabular grid -> NOT cert path.
        Detected by: the section's frame children contain at least one
        repeating frame or a frame with >= 6 fields laid out roughly
        on the same horizontal band (i.e. a row of column headers).
      * Otherwise, if the section has free-standing text/field blocks
        positioned at varying y coordinates (typical of letters and
        certificates) AND the main query has <= 8 columns, take the
        cert path.

    Returns the section_main LayoutGroup when cert-routing is
    appropriate, None otherwise.
    """
    def _frame_children(g: LayoutGroup) -> List[LayoutGroup]:
        return [c for c in (g.children or [])
                if (c.kind or "").lower() == "frame"
                or "frame" in (c.kind or "").lower()]

    def _looks_tabular(section: LayoutGroup) -> bool:
        # Any repeating-frame anywhere under the section -> tabular.
        stack = list(section.children or [])
        while stack:
            node = stack.pop()
            k = (node.kind or "").lower()
            if "repeat" in k or k.endswith("_repeating") or k == "repeating_frame":
                return True
            # A frame holding >= 6 fields is almost certainly a grid row /
            # column-header band, not a positional letter block.
            if "frame" in k and len(node.fields or []) >= 6:
                return True
            stack.extend(node.children or [])
        return False

    def _looks_positional(section: LayoutGroup) -> bool:
        # Count standalone text fields whose y-positions vary a lot
        # (letter / certificate bodies have stacked paragraphs).
        ys = []
        stack = list(section.children or [])
        while stack:
            node = stack.pop()
            for f in (node.fields or []):
                if getattr(f, "kind", "") in ("text", "field"):
                    ys.append(getattr(f, "y", 0) or 0)
            stack.extend(node.children or [])
        if len(ys) < 4:
            return False
        return (max(ys) - min(ys)) > 100  # spread across the page

    candidates = []
    for g in report.layout or []:
        kind = (g.kind or "").lower()
        if kind == "section_main" and _frame_children(g):
            candidates.append(g)
        for child in g.children or []:
            if (child.kind or "").lower() == "section_main" and _frame_children(child):
                candidates.append(child)

    for section in candidates:
        if _looks_tabular(section):
            continue  # tabular reports fall through to flat Tablix path
        if _looks_positional(section):
            return section
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
    # Legal block — tightened from 3.25in to 2.60in so the transfer/sig/cards
    # below it all fit before the body Rectangle's 10.00in ceiling. CanGrow=true
    # in _emit_stacked_textbox lets the box still expand if the text is long;
    # this just changes the BASELINE height that subsequent stacking math uses.
    _emit_stacked_textbox(
        body_items, "Tb_Legal", legal_val, legal_is_expr,
        top_in=4.30, height_in=2.60, width_in=list_width,
        font_size=9, bold=False, align="left",
    )

    # --- Transfer / renewal-due notice ---
    transfer_val, transfer_is_expr = _textbox_value_from_field(
        classified["transfer_text"], report, ds
    )
    _emit_stacked_textbox(
        body_items, "Tb_Transfer", transfer_val, transfer_is_expr,
        top_in=6.95, height_in=0.25, width_in=list_width,
        font_size=8, bold=True, align="center",
    )

    # --- Signature line + signer block ---
    sig_val, sig_is_expr = _textbox_value_from_field(
        classified["signature_text"], report, ds
    )
    _emit_stacked_textbox(
        body_items, "Tb_Sig", sig_val, sig_is_expr,
        top_in=7.25, height_in=0.95, width_in=list_width,
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

    # Cards live at Top=8.45in with Height=1.40in → end at 9.85in.
    # MUST be < body_height (currently 10.00in) so SSRS doesn't paginate
    # them onto the next sheet. Previous Top=8.80in pushed the bottom edge
    # to 10.20in (0.20in overflow) and SSRS split cards to a separate page.
    _emit_card(
        "Rect_CardL", classified["card_l_fields"],
        top_in=8.45, left_in=0.00, width_in=3.50, height_in=1.40,
    )
    _emit_card(
        "Rect_CardR", classified["card_r_fields"],
        top_in=8.45, left_in=4.00, width_in=3.50, height_in=1.40,
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

    # PageHeader: SSRS 2008/01 schema requires <ReportItems> to contain at
    # least one child element. We never put anything in the header, so we
    # omit <ReportItems> entirely (it is optional on PageHeader).
    ph = _sub(page, "PageHeader")
    _sub(ph, "Height", "0.25in")
    _sub(ph, "PrintOnFirstPage", "true")
    _sub(ph, "PrintOnLastPage", "true")

    pf = _sub(page, "PageFooter")
    _sub(pf, "Height", "0.6in")
    _sub(pf, "PrintOnFirstPage", "true")
    _sub(pf, "PrintOnLastPage", "true")

    # Same rule for PageFooter: only create <ReportItems> when we have a
    # child to put inside. An empty <ReportItems /> triggers
    # "deserialization failed: ReportItems has incomplete content" at
    # upload time and blocks the report from being published.
    sig_q = _pick_signature_query(report)
    if sig_q is not None and sig_q.items:
        pf_items = _sub(pf, "ReportItems")
        sf = sig_q.items[0].name
        ds_name = None
        for q in (report.queries or []):
            if any((it.name or "").upper() == sf.upper() for it in (q.items or [])):
                ds_name = q.name
                break
        img = _sub(pf_items, "Image")
        img.set("Name", "Img_Sig")
        _sub(img, "Source", "Database")
        if ds_name:
            _sub(img, "Value", '=First(Fields!' + sf + '.Value, "' + _safe(ds_name) + '")')
        else:
            _sub(img, "Value", '=""')
        _sub(img, "MIMEType", "image/png")
        _sub(img, "Sizing", "FitProportional")
        _sub(img, "Top", "0.05in")
        _sub(img, "Left", "0.05in")
        _sub(img, "Height", "0.45in")
        _sub(img, "Width", "1.5in")

    _sub(page, "PageHeight", str(page_height_in) + "in")
    _sub(page, "PageWidth", "8.5in")
    _sub(page, "LeftMargin", "0.5in")
    _sub(page, "RightMargin", "0.5in")
    _sub(page, "TopMargin", "0.5in")
    _sub(page, "BottomMargin", "0.5in")
    return page


def _build_code() -> ET.Element:
    """Minimal <Code> block. SSRS accepts an empty body."""
    return ET.Element(_q("Code"))


def _strip_empty_required_containers(root: ET.Element) -> None:
    """Remove any empty must-have-child container that slipped through.
    SSRS rejects empty <ReportItems>/<CellContents>/<DataSources>/<DataSets>/
    <ReportParameters> at upload with "has incomplete content"."""
    must_have_child = {
        "ReportItems", "CellContents", "DataSources", "DataSets",
        "ReportParameters",
    }
    for parent in root.iter():
        for child in list(parent):
            tag = child.tag
            # Comments / PIs have a callable .tag; skip them.
            if not isinstance(tag, str):
                continue
            local = tag.split("}", 1)[-1]
            if local in must_have_child and len(list(child)) == 0:
                parent.remove(child)


def _build_report_root(report: ParsedReport, target_db: str = "oracle") -> ET.Element:
    """Build the root <Report> element with namespaces and all children in
    the SSRS 2008/01 schema order."""
    _augment_parameters_from_binds(report)

    root = ET.Element(_q("Report"))

    root.append(_build_data_sources(target_db=target_db))
    root.append(_build_data_sets(report, target_db=target_db))

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

    _strip_empty_required_containers(root)
    return root


def generate_rdl(report: ParsedReport, target_db: str = "oracle") -> str:
    """Return a complete RDL XML document as a string."""
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
