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
    LayoutField,
    LayoutGroup,
    ParsedReport,
    ReportParameter,
)
from converter.preview.html_mockup import detect_report_kind as _detect_report_kind
from converter.translators.plsql_formula import (
    translate_formula_to_vb, translate_expr as _translate_oracle_expr,
    extract_placeholder_assignments as _extract_cp_assignments)


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

DEFAULT_COLUMNS = ["Column1", "Column2", "Column3", "Column4", "Column5"]


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
    the column names Oracle returns. For a simple column like T.ID,
    Oracle returns "ID" and Report Builder agrees with our <Field
    Name="ID"> declaration. But for an expression like
    UPPER(X.STATUS_DESC) with no alias, Oracle returns the column
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
        if re.search(r"(?:\)|[A-Za-z0-9_]|')\s+[A-Za-z_][A-Za-z0-9_]*\s*$", stripped):
            # Trailing identifier following ")", a word char, OR a
            # closing single-quote (string-literal concatenation) =
            # implicit Oracle alias. Don't add a second one.
            #
            # The single-quote branch covers Oracle string-concat
            # expressions like:
            #    'DEPT' || CHR(10) || UPPER(X) || ' LICENSE' Perm_Type
            # where "Perm_Type" is the implicit alias of the whole
            # concat. Without the "'" alternative we double-aliased
            # this with "AS DEPARTMENT_OF_ENVIRONMENTAL_QU" -> ORA-00923
            # "FROM keyword not found where expected" at runtime.
            #
            # Caveat: a bare "TABLE.COL" also matches the word-char
            # branch, but bare column refs were already short-circuited
            # above.
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
    """Make a name safe-ish for an RDL identifier. SSRS requires a CLR-style
    identifier: it must start with a letter or underscore, so a leading digit
    (or an all-illegal-char name) is rejected at publish time -- prefix '_'.
    Field names and every Fields!/Tablix reference both route through here, so
    they stay consistent; the DataField keeps the raw column name."""
    out = re.sub(r"[^A-Za-z0-9_]", "_", s or "")
    if not out or out[0].isdigit():
        out = "_" + out
    return out


def _q_safe(s: str) -> str:
    """Escape double-quotes for inclusion in an SSRS expression literal."""
    return (s or "").replace('"', '""')


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

    F8: if the heaviest query is a FLAT <link> CHILD (parent_group set and NOT
    itself a nested master-detail), join-key augmentation may have inflated its
    item count past its master -- bind to a non-child master instead so the
    report's primary entity isn't dropped (the SAMPLE_ORDERS case). A child
    that carries its OWN nested group chain is genuinely the data-rich query, so
    it is kept (don't regress reports whose detail query is the real subject).
    """
    queries = report.queries or []
    if not queries:
        return None
    heaviest = max(queries, key=lambda q: (len(q.items or []), -queries.index(q)))
    if ((getattr(heaviest, "parent_group", "") or "").strip()
            and not _is_nested_master_detail(heaviest)):
        masters = [q for q in queries
                   if not (getattr(q, "parent_group", "") or "").strip()]
        if masters:
            return max(masters, key=lambda q: (len(q.items or []), -queries.index(q)))
    return heaviest


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
    # EXACT mapping: the layout frame binds to a <group name="G_X"> that this
    # query actually owns (parsed into q.group_names). This is authoritative --
    # Oracle pairs an arbitrary group name with a query, NOT always by suffix
    # (e.g. Q_DETAIL owns G_STATUS_1), so the suffix heuristic below
    # silently failed for multi-section reports and dropped their data.
    for gn in (getattr(q, "group_names", None) or []):
        if (gn or "").upper() == lref:
            return True
    # Fallback heuristic: Q_FOO <-> G_FOO by the portion after the first
    # underscore. Kept for reports whose parser didn't capture group names.
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

    # No layout Image field references a query column -> the report has NO
    # image. Return None rather than fabricating an <Image Source="Database">
    # bound to an arbitrary small query: that hallucinated broken images on
    # reports with no logo (the "pink field" class of over-fitting). Image
    # emission now derives strictly from parsed structure.
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
            # declared PARM_X_ID. An UNDECLARED bind is bound to the
            # =Nothing expression — passes NULL silently at runtime (the
            # Oracle semantic for an unset bind) and, critically, NEVER
            # prompts. An empty <Value/> here is a prompt trigger: SSRS
            # pops "Define Query Parameters" at Refresh Fields, which is
            # the load-bearing invariant this converter must never break.
            canonical = {p.upper(): p for p in declared_params}
            for pname in referenced:
                qp = _sub(qp_root, "QueryParameter")
                qp.set("Name", f":{pname}")
                canon = canonical.get(pname.upper())
                if canon:
                    _sub(qp, "Value", f"=Parameters!{canon}.Value")
                else:
                    _sub(qp, "Value", "=Nothing")
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
                # Bind to the report parameter if it exists; an undeclared
                # bind gets =Nothing (silent NULL) — an empty <Value/> is a
                # "Define Query Parameters" prompt trigger (never allowed).
                canon = canonical.get(pname.upper())
                if canon:
                    _sub(qp, "Value", f"=Parameters!{canon}.Value")
                else:
                    _sub(qp, "Value", "=Nothing")

    # <Fields>
    fields = _sub(ds, "Fields")
    for item in query.items or []:
        # Wild-corpus net: a <dataItem> with no usable name (Oracle's own
        # docs ship one with the name attribute missing) must be SKIPPED —
        # it otherwise becomes a field named "_", which SSRS rejects at
        # publish time ("Field names must be CLS-compliant identifiers").
        nm = (item.name or "").strip()
        if not nm:
            continue
        f = _sub(fields, "Field")
        f.set("Name", _safe(nm) or "Field1")
        _sub(f, "DataField", nm)
        _rdsub(f, "TypeName", _ssrs_field_type(item))
    if len(fields) == 0:
        # A dataset with ZERO fields can't feed any data region and the
        # report engine can't even create a data reader for it. Guarantee
        # one placeholder field (same convention the sub-report stub uses).
        ph = _sub(fields, "Field")
        ph.set("Name", "PLACEHOLDER")
        _sub(ph, "DataField", "PLACEHOLDER")
        _rdsub(ph, "TypeName", "System.String")
    return ds


_FORMULA_DATASET_NAME = "DS_REPORT_FORMULAS"


def _formula_dataset_columns(report: ParsedReport) -> List[str]:
    """Ordered, de-duplicated list of CF_/CP_ formula + placeholder column
    names that feed the synthetic formula-resolution dataset.

    Oracle ``<summary>`` aggregate columns are excluded -- those
    re-implement as SSRS aggregate expressions, not scalar dataset
    fields. Fully generic: driven by what the parser collected into
    ``report.formulas``, never by column names.
    """
    names: List[str] = []
    seen: Set[str] = set()
    for f in (getattr(report, "formulas", None) or []):
        name = (getattr(f, "name", "") or "").strip()
        if not name or name.upper() in seen:
            continue
        body = (getattr(f, "plsql_body", "") or "").strip().lower()
        if body.startswith("oracle <summary"):
            continue
        seen.add(name.upper())
        names.append(name)
    return names


_TRIVIAL_RETURN_RX = re.compile(
    r"^\s*RETURN\s*\(\s*([:&]?[A-Za-z_][A-Za-z0-9_]*)\s*\)\s*;?\s*$",
    re.IGNORECASE,
)


def _try_simple_formula_expression(plsql_body: str) -> Optional[str]:
    """If a formula body is trivially ``RETURN(:bind);`` or
    ``RETURN(column_name);`` return the equivalent SQL expression
    (``:bind`` or ``column_name``). Returns None for anything more
    complex - those need user-written SQL.
    """
    if not plsql_body:
        return None
    # Strip surrounding FUNCTION ... IS BEGIN ... END; wrapper so the
    # body's RETURN line surfaces as the only non-trivial token.
    inner = re.sub(
        r"^\s*FUNCTION\s+\w+(?:\s*\([^)]*\))?\s*RETURN\s+\w+(?:\([^)]*\))?\s*"
        r"(?:IS|AS)\s+BEGIN\s+", "", plsql_body, flags=re.IGNORECASE | re.DOTALL,
    )
    inner = re.sub(r"\s*END\s*\w*\s*;?\s*$", "", inner, flags=re.IGNORECASE)
    # Strip an exception block at the tail.
    inner = re.sub(r"EXCEPTION\b.*$", "", inner, flags=re.IGNORECASE | re.DOTALL)
    inner = inner.strip().rstrip(";").strip()
    m = _TRIVIAL_RETURN_RX.match(inner)
    if not m:
        return None
    tok = m.group(1)
    # ``RETURN(:Foo);`` -> ``:Foo`` (a SQL bind reference, valid in SSRS Oracle).
    # ``RETURN(Foo);``  -> ``Foo`` (a column reference, valid wherever Foo is bound).
    if tok.startswith("&"):
        return None  # & substitutions don't translate to a SQL select-list item
    return tok


def _sanitize_plsql_for_sql_comment(plsql: str) -> str:
    """Make a PL/SQL body safe to embed in an Oracle ``/* ... */`` block.
    Oracle/PG/SQL Server all forbid nested ``*/`` inside a block comment,
    so the lone closing token has to be escaped. Keep the body otherwise
    verbatim so the user sees exactly what the original report computed.
    """
    if not plsql:
        return ""
    return plsql.replace("*/", "* /")


def _build_formula_dataset(report: ParsedReport,
                           target_db: str = "oracle") -> Optional[ET.Element]:
    """Synthesize one single-row DataSet carrying every Oracle formula
    (CF_*) and placeholder (CP_*) column the report computes.

    Oracle Reports computes these in PL/SQL; SSRS has no formula
    construct, so this dataset is the bridge: the user opens it in
    Report Builder and replaces each column's placeholder with a real
    SQL expression. The textboxes already bind to these columns via
    ``=First(Fields!CF_X.Value, "DS_REPORT_FORMULAS")``, so once the
    SELECT returns real values, the certificate / letter body lights
    up without touching any other RDL.

    To make that easier, every column ships with the ORIGINAL PL/SQL
    body inlined as a ``/* ... */`` comment next to the placeholder.
    Trivial bodies of the form ``RETURN(:bind);`` or
    ``RETURN(column_name);`` are auto-translated to the equivalent SQL
    expression on the spot - so a passthrough formula needs no manual
    work at all.

    Returns None when the report computes no formula columns.
    """
    cols = _formula_dataset_columns(report)
    if not cols:
        return None

    # Build a {name: formula_obj} map so we can pull the PL/SQL body for
    # each select-list column. Empty-bodied placeholders (CP_* that get
    # populated as a side-effect of a CF_* function) get a short note
    # pointing the user at the formula that drives them.
    by_name = {(getattr(f, "name", "") or "").upper():
               f for f in (getattr(report, "formulas", None) or [])}

    # The SELECT keeps just one expression per column with the alias as
    # the final token, so the auto-alias validator and the field-list
    # generator stay happy. Per-column help (auto-translation note OR the
    # original PL/SQL body) lives in a comment manifest emitted BEFORE
    # the SELECT keyword.
    select_items: List[str] = []
    manifest_lines: List[str] = []
    auto_translated = 0
    for c in cols:
        f = by_name.get(c.upper())
        body = (getattr(f, "plsql_body", "") or "").strip() if f else ""
        col_safe = _safe(c)
        expr = _try_simple_formula_expression(body) if body else None
        if expr is not None:
            select_items.append(f"{expr} AS {col_safe}")
            manifest_lines.append(
                f"   * {col_safe}: AUTO-TRANSLATED from trivial "
                f"RETURN -> ``{expr}``. No manual work needed."
            )
            auto_translated += 1
            continue
        select_items.append(f"NULL AS {col_safe}")
        if body:
            safe_body = _sanitize_plsql_for_sql_comment(body)
            manifest_lines.append(
                f"   * {col_safe}: REPLACE the ``NULL AS {col_safe}`` below "
                f"with a SQL expression. Original Oracle PL/SQL:\n"
                + "\n".join("     " + ln for ln in safe_body.splitlines())
            )
        else:
            manifest_lines.append(
                f"   * {col_safe}: placeholder set as a side-effect by one "
                f"of the CF_* functions above. Replace the NULL with the "
                f"value you want this column to carry."
            )

    summary = (
        f"   ({auto_translated} of {len(cols)} columns auto-translated; "
        f"{len(cols) - auto_translated} still need manual SQL.)"
        if auto_translated else
        f"   ({len(cols)} columns to fill in.)"
    )
    header_lines = [
        "/* ----------------------------------------------------------------",
        "   Formula-resolution dataset (generated). Oracle Reports computed",
        "   these CF_/CP_ columns in PL/SQL, which SSRS has no construct for.",
        "   Per-column notes:",
        "",
        *manifest_lines,
        "",
        summary,
        "   Once you've replaced the NULLs, Refresh Fields and the textboxes",
        "   bound to these columns will populate immediately.",
        "---------------------------------------------------------------- */",
    ]
    header = "\n".join(header_lines) + "\n"

    select_list = ",\n       ".join(select_items)
    from_clause = "" if target_db == "sqlserver" else "\nFROM DUAL"
    command_text = f"{header}SELECT {select_list}{from_clause}"

    ds = ET.Element(_q("DataSet"))
    ds.set("Name", _FORMULA_DATASET_NAME)
    query = _sub(ds, "Query")
    _sub(query, "DataSourceName", "SharedDataSource")
    _sub(query, "CommandText", command_text)
    fields = _sub(ds, "Fields")
    for c in cols:
        fld = _sub(fields, "Field")
        fld.set("Name", _safe(c))
        _sub(fld, "DataField", _safe(c))
        _rdsub(fld, "TypeName", "System.String")
    if len(fields) == 0:
        ds.remove(fields)  # an empty <Fields/> is XSD-invalid (needs >=1 Field)
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
        placeholder_sql = "SELECT 1 AS Column1, 0 AS Column2, '' AS Column3"
        # Use a neutral placeholder name (NOT Q_PERMIT) so the converter
        # stays generic across reports. Columns are equally generic.
        placeholder = DataQuery(
            name="Q_MAIN",
            sql=placeholder_sql,
            tsql=placeholder_sql,
            items=[
                DataItem(name="Column1", datatype="character"),
                DataItem(name="Column2", datatype="number"),
                DataItem(name="Column3", datatype="character"),
            ],
        )
        root.append(_build_dataset(placeholder, declared, target_db=target_db,
                                   param_types=param_types))
        return root

    seen_ds_names: set = set()
    for q in report.queries:
        ds = _build_dataset(q, declared, target_db=target_db,
                            param_types=param_types)
        # SSRS rejects duplicate <DataSet Name>. Keep the first occurrence's
        # name; disambiguate later collisions (a scoped reference to an
        # ambiguous duplicate name resolves to the first anyway).
        base = ds.get("Name") or "DataSet1"
        nm, k = base, 2
        while nm in seen_ds_names:
            nm, k = f"{base}_{k}", k + 1
        if nm != base:
            ds.set("Name", nm)
        seen_ds_names.add(nm)
        root.append(ds)
    return root


# ---------------------------------------------------------------------------
# ReportParameters
# ---------------------------------------------------------------------------


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

    EVERY parameter gets <Nullable>true</Nullable> AND a concrete
    <DefaultValue> of the =Nothing expression (typed NULL). That pair is
    what suppresses the "Define Query Parameters" dialog at upload,
    Refresh Fields, and run time — the load-bearing invariant of this
    converter. The SQL CommandText handles the rest via the standard
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
        # SSRS 2008/01 schema element order:
        #   DataType -> Nullable -> DefaultValue -> AllowBlank
        #            -> Prompt -> Hidden
        _sub(rp, "DataType", ptype)
        # Nullable for EVERY type (String included) so a typed-NULL default is
        # always valid and SSRS never has to ask for a value.
        _sub(rp, "Nullable", "true")
        # LOAD-BEARING -- never emit an EMPTY <Value/> default.
        # EVERY parameter gets a CONCRETE =Nothing (typed-NULL) default. An
        # empty <Value/> is NOT a usable default: when a dataset's query
        # parameter maps to a report parameter whose default is empty, SSRS
        # pops the "Define Query Parameters" dialog at Refresh-Fields time and
        # then fails ("missing a value"). A real =Nothing default lets the user
        # upload -> repoint the shared data source -> Refresh Fields -> enter
        # creds, with NO parameter prompt. The Oracle SQL's (:P IS NULL OR
        # col = :P) / NVL(:P, ...) guards handle the NULL cleanly, so an
        # unfilled parameter widens the result set instead of erroring.
        # This invariant must hold for EVERY parameter of EVERY report.
        dv = _sub(rp, "DefaultValue")
        dv_values = _sub(dv, "Values")
        _sub(dv_values, "Value", "=Nothing")
        # AllowBlank is valid ONLY for String. With DefaultValue above
        # we don't strictly need it, but keeping it is harmless and
        # documents intent (the user can submit empty input too).
        if ptype == "String":
            _sub(rp, "AllowBlank", "true")
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
    """Decide what columns the main Tablix should show.

    NEVER silently drop data columns. A wide source report (e.g. a
    warehouse roll-up with 54 measures, wild-corpus verified) must keep
    EVERY column — dropping them is exactly the silent-column-loss the
    converter promises not to do. SSRS paginates wide tables horizontally,
    and the user can adjust widths in Report Builder; an honest wide table
    beats a lossy narrow one. Column WIDTH is adapted to the count in
    _build_tablix; here we only choose WHICH columns (all of them)."""
    layout_cols = _collect_layout_columns(report, main.name)
    if layout_cols:
        return layout_cols
    if main.items:
        return [it.name for it in main.items]
    return list(DEFAULT_COLUMNS)


def _emit_drillthrough(run, dt):
    """Emit <ActionInfo><Actions><Action><Drillthrough> on a TextRun, placed
    BETWEEN <Value> and <Style> (the RDL TextRun element order). ``dt`` =
    {"report_name": str, "params": [(name, value_expr), ...]}."""
    ai = _sub(run, "ActionInfo")
    act = _sub(_sub(ai, "Actions"), "Action")
    dr = _sub(act, "Drillthrough")
    _sub(dr, "ReportName", dt.get("report_name") or "")
    params = dt.get("params") or []
    if params:
        pel = _sub(dr, "Parameters")
        for pname, pval in params:
            p = _sub(pel, "Parameter")
            p.set("Name", pname)
            _sub(p, "Value", pval)


def _extract_url_params(report, formula_names_upper):
    """Parse Oracle URL-builder formula bodies for the ``'&PARAM=' || <source>``
    pairs that define what a hyperlink/drill-through actually passes to the
    child report. e.g. CF_URL_Envelope builds
        ... || '&P_ORG_ID=' || :Org_Id || '&P_SITE_ID=' || :SA_Site_Id
    -> [("P_ORG_ID", "Org_Id"), ("P_SITE_ID", "SA_Site_Id")]. Oracle report-
    SERVER params (report/destype/desformat/desname) are excluded -- those drive
    the old Reports server, not the SSRS child. Generic: the param names and
    sources come straight from the formula text, nothing hardcoded."""
    SERVER = {"REPORT", "DESTYPE", "DESFORMAT", "DESNAME"}
    out, seen = [], set()
    for f in (getattr(report, "formulas", None) or []):
        if (getattr(f, "name", "") or "").upper() not in formula_names_upper:
            continue
        body = getattr(f, "plsql_body", "") or ""
        for pm, src in re.findall(
                r"'[&?]([A-Za-z_]\w*)\s*=\s*'\s*\|\|\s*:?([A-Za-z_]\w*)", body):
            up = pm.upper()
            if up in SERVER or up in seen:
                continue
            seen.add(up)
            out.append((pm, src))
    return out


def _drillthrough_for(report, lf):
    """Build a Drillthrough dict for a layout field that participates in a
    detected sub-report link, matched TWO ways:

      * the field carries ``<webSettings hyperlink="&CF_URL_X">`` (the
        Oracle click surface), OR
      * the field's SOURCE *is* one of the link's URL-builder formulas
        (the cover textbox that displays the computed URL text — in Oracle
        that text is itself the clickable link, so it must be clickable in
        SSRS too; this is the cover-page "Generate Envelopes" line).

    PREFERS the REAL parameters the child report takes, parsed from the
    URL-builder formula's ``'&PARAM=' || src`` pairs (e.g.
    P_ORG_ID=Org_Id), each resolved to its correct SSRS expression (a
    per-row Fields! ref, a cross-dataset Lookup(), or a Parameter). Falls
    back to forwarding declared parent params when the URL has no explicit
    pairs. Generic -- everything comes from the parsed report."""
    candidates = set()
    formula = (getattr(lf, "hyperlink", "") or "").strip()
    if formula:
        candidates.add(formula.upper().lstrip("&"))
        candidates.add(formula.upper())
    src = (getattr(lf, "source", "") or "").strip()
    if src:
        candidates.add(src.upper())
    if not candidates:
        return None
    try:
        from ..subreports import detect_subreport_links
        links = detect_subreport_links(report)
    except Exception:
        return None
    declared = {(getattr(p, "name", "") or "").upper(): getattr(p, "name", "")
                for p in (report.parameters or []) if getattr(p, "name", "")}
    for ln in links:
        names = {x.strip().upper() for x in (ln.get("url_formula") or "").split(",")}
        if not ((candidates & names) and ln.get("child_name")):
            continue
        params = []
        url_params = _extract_url_params(report, names)
        if url_params:
            # Resolve each source the same way body fields resolve: a column in
            # the current row -> Fields!; a column from a linked child query ->
            # Lookup(); a parameter -> Parameters!. So P_ORG_ID gets the org of
            # the permittee on THIS page.
            try:
                resolve = _build_token_resolver(report)
                main = _pick_main_query(report)
                ds = main.name if main else ""
            except Exception:  # noqa: BLE001
                resolve, ds = None, ""
            for pname, src in url_params:
                expr = "=Nothing"
                if resolve is not None:
                    kind, ssrs, _n = resolve(src, ds)
                    if kind == "param":
                        expr = f"=Parameters!{_safe(ssrs)}.Value"
                    elif kind == "field":
                        expr = f"=Fields!{_safe(ssrs)}.Value"
                    elif str(ssrs).startswith("="):   # field_other_ds / formula
                        expr = ssrs
                params.append((pname, expr))
        else:
            for b in (ln.get("bind_params") or []):
                canon = declared.get((b or "").upper())
                if canon:
                    params.append((canon, f"=Parameters!{_safe(canon)}.Value"))
        return {"report_name": ln["child_name"], "params": params}
    return None


def _build_textbox(parent: ET.Element, name: str, value: str,
                   bold: bool = False, font_size: str = "10pt",
                   bg: Optional[str] = None, fg: Optional[str] = None,
                   text_align: Optional[str] = None,
                   vertical_align: Optional[str] = None,
                   border_color: str = "#d0d0d0",
                   padding: str = "4pt",
                   can_grow: bool = True,
                   font_family: Optional[str] = None,
                   underline: bool = False,
                   drillthrough: Optional[dict] = None) -> ET.Element:
    """Emit a styled Textbox.

    The optional kwargs let _build_tablix dial in mockup-matching styling
    (header band color, white-on-band foreground, alternating row
    background expression, centered headers, etc.) without forking the
    function. All new kwargs default to backwards-compatible values.
    """
    tb = _sub(parent, "Textbox")
    tb.set("Name", name)
    paragraphs = _sub(tb, "Paragraphs")
    para = _sub(paragraphs, "Paragraph")
    if text_align:
        para_style = _sub(para, "Style")
        _sub(para_style, "TextAlign", text_align)
    runs = _sub(para, "TextRuns")
    run = _sub(runs, "TextRun")
    _sub(run, "Value", value)
    if drillthrough:
        _emit_drillthrough(run, drillthrough)
    style = _sub(run, "Style")
    _sub(style, "FontSize", font_size)
    if font_family:
        _sub(style, "FontFamily", font_family)
    if bold:
        _sub(style, "FontWeight", "Bold")
    if underline:
        _sub(style, "TextDecoration", "Underline")
    if fg:
        _sub(style, "Color", fg)
    tb_style = _sub(tb, "Style")
    border = _sub(tb_style, "Border")
    _sub(border, "Style", "Solid")
    _sub(border, "Color", border_color)
    _sub(border, "Width", "0.5pt")
    if bg:
        _sub(tb_style, "BackgroundColor", bg)
    if vertical_align:
        _sub(tb_style, "VerticalAlign", vertical_align)
    _sub(tb_style, "PaddingLeft", padding)
    _sub(tb_style, "PaddingRight", padding)
    _sub(tb_style, "PaddingTop", padding)
    _sub(tb_style, "PaddingBottom", padding)
    _sub(tb, "CanGrow", "true" if can_grow else "false")
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

    # Style the Tablix to mirror the in-app mockup: banded header,
    # white bold header text, light alternating detail rows, visible
    # cell borders, comfortable padding. All colors are pulled from
    # the parsed report's visualSettings when present and only fall
    # back to a neutral palette when the source XML carried no color
    # information. NOTHING is keyed off a specific report.
    main_group = _find_group_for_query(report, main.name)
    # Mockup defaults (must match converter.preview.html_mockup):
    #   band/header = #666666, header text = white,
    #   detail bg = #f5f5f5 (lightest gray), alt row = #ffffff.
    header_bg = "#4a6a8a"  # subtle slate blue band -- matches screenshot 1
    header_fg = "#ffffff"
    detail_bg = "#ffffff"
    alt_row_bg = "#f5f7fa"
    if main_group is not None:
        gb = getattr(main_group, "background_color", "")
        fg = getattr(main_group, "foreground_color", "")
        if gb:
            header_bg = gb
        if fg:
            header_fg = fg

    # Determine if a master-detail nested group should be emitted.
    detail_query = _pick_detail_query(report, main.name)
    triggers = _layout_format_triggers(report)

    # TablixBody
    body = _sub(tablix, "TablixBody")
    cols_el = _sub(body, "TablixColumns")
    # Adaptive column width: a few columns get a comfortable 1.5in; a wide
    # report (dozens of measures) shrinks toward a 0.6in floor so the whole
    # table stays legible and SSRS paginates it horizontally, rather than a
    # fixed 1.5in that would make a 54-column table 81in wide. Never drops
    # columns — width adapts, data is complete.
    _ncol = max(1, len(columns))
    if _ncol <= 6:
        _colw = 1.5
    else:
        _colw = max(0.6, round(9.0 / _ncol, 2))
    for _ in columns:
        c = _sub(cols_el, "TablixColumn")
        _sub(c, "Width", f"{_colw}in")

    rows_el = _sub(body, "TablixRows")

    # Header row -- band background, white bold text, centered.
    header_row = _sub(rows_el, "TablixRow")
    _sub(header_row, "Height", "0.30in")
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
            fg=header_fg,
            text_align="Center",
            vertical_align="Middle",
            border_color="#a0a0a0",
            padding="5pt",
        )

    # Detail row -- alternating bg via row-number expression so output
    # mirrors the banded look in the in-app mockup.
    detail_row = _sub(rows_el, "TablixRow")
    _sub(detail_row, "Height", "0.28in")
    detail_cells = _sub(detail_row, "TablixCells")
    alt_expr = (
        '=IIf(RowNumber(Nothing) Mod 2 = 0, "'
        + alt_row_bg + '", "' + detail_bg + '")'
    )
    # Sub-report links on tabular reports: an Oracle column rendered
    # through a layout field carrying <webSettings hyperlink="&CF_URL_X">
    # must stay clickable in the SSRS table — the same Drillthrough the
    # per-record path emits. Map column -> its layout field once.
    col_dt = {}
    try:
        for _y, _x, _d, lf in _layout_fields_in_order(report):
            src = (getattr(lf, "source", "") or "").upper()
            if src and src not in col_dt:
                dt = _drillthrough_for(report, lf)
                if dt:
                    col_dt[src] = dt
    except Exception:  # noqa: BLE001 -- links must never break the table
        col_dt = {}
    for col in columns:
        cell = _sub(detail_cells, "TablixCell")
        contents = _sub(cell, "CellContents")
        dt = col_dt.get(col.upper())
        _build_textbox(
            contents,
            f"Cell_{_safe(col)}",
            f"=Fields!{_safe(col)}.Value",
            bg=alt_expr,
            vertical_align="Middle",
            border_color="#d0d0d0",
            padding="4pt",
            fg="#0b5cad" if dt else None,
            underline=bool(dt),
            drillthrough=dt,
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
        # Outer group — derives its key from the parsed dataset structure
        # (first column of the main query) rather than report-specific names.
        outer_mem = _sub(row_members, "TablixMember")
        outer_group = _sub(outer_mem, "Group")
        outer_group.set("Name", "OuterGroup")
        outer_grp_exprs = _sub(outer_group, "GroupExpressions")
        group_key = _pick_group_key(main, [])
        _sub(
            outer_grp_exprs,
            "GroupExpression",
            f"=Fields!{_safe(group_key)}.Value" if group_key else "=1",
        )
        # Conditional visibility hint based on first format trigger (placeholder).
        if triggers:
            grp_name, trig_name = triggers[0]
            outer_mem.append(
                ET.Comment(
                    f" original PL/SQL format trigger: {trig_name} (group {grp_name}) "
                )
            )
            visibility = _sub(outer_mem, "Visibility")
            _sub(visibility, "Hidden", "false")
        # Nested children: inner Org group + detail
        inner_members = _sub(outer_mem, "TablixMembers")
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

    # Master-detail link support (#3): map query name -> DataQuery so the
    # cross-dataset resolver can emit a Lookup() back to the master instead
    # of blanking the field to =Nothing.
    query_by_name = {(q.name or "").upper(): q for q in (report.queries or [])}

    def _lookup_for_child(result_col, child_ds, bound_ds):
        """If child_ds is a linked detail (Oracle <link>) of the bound master
        dataset, return a VALID SSRS Lookup() pulling result_col from the
        child; else None. Join key = a child bind variable naming a column of
        the bound dataset (e.g. Q_ORG binds :Site_Id, a Q_PERMIT column); the
        child-side key is the child column matching that name. Purely
        structural -- no report-specific names/values."""
        q = query_by_name.get((child_ds or "").upper())
        if q is None or not getattr(q, "parent_group", ""):
            return None
        bound_cols = dataset_fields.get((bound_ds or "").upper(), {})
        child_cols = dataset_fields.get((child_ds or "").upper(), {})
        if not bound_cols or not child_cols:
            return None
        # Collect ALL correlation key pairs and join on the COMPOSITE.
        # Joining on just the first bind is wrong with real data: e.g. a
        # child correlated on (Prog_Id, Site_Id) where Prog_Id is the SAME
        # value on every row — a single-key Lookup on Prog_Id returns the
        # FIRST child row for every master row (same permittee on every
        # cert). The composite key reproduces Oracle's full correlation.
        pairs = []  # (master_field, child_field)
        seen_keys = set()
        for b in re.findall(r":([A-Za-z_][A-Za-z0-9_]*)", q.sql or ""):
            bu = b.upper()
            if bu in seen_keys or bu not in bound_cols:
                continue
            dest = child_cols.get(bu)
            if not dest:
                for cu, cn in child_cols.items():
                    if cu == bu or cu.endswith("_" + bu) or cu.endswith(bu):
                        dest = cn
                        break
            if dest:
                seen_keys.add(bu)
                pairs.append((bound_cols[bu], dest))
        if not pairs:
            return None
        if len(pairs) == 1:
            src = f"Fields!{_safe(pairs[0][0])}.Value"
            dst = f"Fields!{_safe(pairs[0][1])}.Value"
        else:
            src = ' & "|" & '.join(
                f"Fields!{_safe(m)}.Value" for m, _c in pairs)
            dst = ' & "|" & '.join(
                f"Fields!{_safe(c)}.Value" for _m, c in pairs)
        return (
            f'=Lookup({src}, {dst}, '
            f'Fields!{_safe(result_col)}.Value, "{_safe(child_ds)}")'
        )

    # Build formula lookup: name_upper -> FormulaColumn-like object with
    # ``.name`` and ``.plsql_body`` attributes. Tolerate a missing
    # ``formulas`` attribute (older ParsedReport instances).
    formula_by_name: dict = {}
    for f in (getattr(report, "formulas", None) or []):
        fname = getattr(f, "name", "") or ""
        if not fname:
            continue
        formula_by_name.setdefault(fname.upper(), f)
    # Formula/placeholder columns carried by the synthetic
    # formula-resolution dataset (see _build_formula_dataset). A token
    # in this set binds to that dataset's field instead of =Nothing.
    formula_ds_cols = {c.upper() for c in _formula_dataset_columns(report)}

    # Oracle PLACEHOLDER outputs (:CP_X := expr) that CF_ formulas set as
    # side-effects -- recover them so a CP_ reference shows its COMPUTED value
    # instead of a blank. {CP_NAME_UPPER: oracle_expr}; translated scope-safe
    # at resolve time (an out-of-scope/external one falls back to placeholder).
    cp_exprs: dict = {}
    for _f in (getattr(report, "formulas", None) or []):
        try:
            for _k, _v in _extract_cp_assignments(getattr(_f, "plsql_body", "") or "").items():
                cp_exprs.setdefault(_k, _v)
        except Exception:  # noqa: BLE001
            pass

    # Layout FIELD objects by name -> source. An expression (often a formula /
    # letter body) may reference a DISPLAY field by its layout NAME (e.g.
    # F_CF_AVG_HAUL) whose SOURCE is the column/formula that actually lives in a
    # dataset (CF_Avg_Haul). Map name -> source so we resolve to the real column
    # and never emit a dangling Fields!F_X.Value (SSRS runtime "field not found").
    layout_field_source: dict = {}

    def _collect_layout_fields(groups):
        for g in (groups or []):
            for fld in (getattr(g, "fields", None) or []):
                nm = (getattr(fld, "name", "") or "")
                src = (getattr(fld, "source", "") or "")
                if nm and src and nm.upper() != src.upper():
                    layout_field_source.setdefault(nm.upper(), src)
            _collect_layout_fields(getattr(g, "children", None) or [])

    _collect_layout_fields(getattr(report, "layout", None))

    def resolve(token: str, dataset_name: str = "", _depth: int = 0):
        if not token:
            return ("field_unverified", token, "empty token")
        u = token.upper()
        # 1) An EXACT declared parameter name wins outright (token IS the
        #    parameter, e.g. "P_SITE_NAME").
        if u in param_canonical and param_canonical[u].upper() == u:
            return ("param", param_canonical[u], "")
        # 2) Field in the enclosing dataset scope -- an exact data column
        #    must beat a PREFIX-STRIPPED parameter alias (the Q_VISIT column
        #    SITE_NAME must bind to the record's value, not the P_SITE_NAME
        #    filter input). param_canonical also holds stripped aliases
        #    (P_SITE_NAME -> SITE_NAME); those are deferred to step 2b.
        ds_key = (dataset_name or "").upper()
        if ds_key and ds_key in dataset_fields and u in dataset_fields[ds_key]:
            return ("field", dataset_fields[ds_key][u], "")
        # 2b) Prefix-stripped parameter alias, only when no same-name field
        #     exists in the bound dataset.
        if u in param_canonical:
            return ("param", param_canonical[u], "")
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
            # DETERMINISTIC TRANSLATION (the core feature): compile the formula's
            # PL/SQL body to a VB.NET expression that COMPUTES inline, instead of
            # a placeholder. Only inline when EVERY bind resolves to a parameter
            # or a field IN THE BOUND DATASET, so we never emit an out-of-scope /
            # dangling Fields! ref. Gated to _depth==0 so cyclic/nested formula
            # refs fall back safely (no infinite recursion). When it can't fully,
            # the placeholder below still applies -- a broken expr never ships.
            if _depth == 0:
                def _formula_ref(nm):
                    k2, s2, _n2 = resolve(nm, dataset_name, _depth + 1)
                    if k2 == "param":
                        return f"Parameters!{s2}.Value"
                    if k2 == "field":
                        return f"Fields!{s2}.Value"
                    raise ValueError(f"unsafe ref {nm!r} ({k2})")
                # CP_ placeholder OUTPUT computed by another CF_ formula's
                # side-effect (:CP_X := expr). Use its extracted+translated
                # expression so the value shows instead of a blank.
                if u in cp_exprs:
                    try:
                        _cp = _translate_oracle_expr(cp_exprs[u], _formula_ref)
                    except Exception:  # noqa: BLE001
                        _cp = {"ok": False}
                    if _cp.get("ok") and _cp.get("vb"):
                        return ("formula", "=" + _cp["vb"],
                                f"placeholder {canonical!r} computed from its CF_ "
                                f"assignment -> {_cp['vb'][:100]}")
                try:
                    _tr = translate_formula_to_vb(getattr(f, "plsql_body", "") or "",
                                                  _formula_ref)
                except Exception:  # noqa: BLE001
                    _tr = {"ok": False}
                if _tr.get("ok") and _tr.get("expr"):
                    return ("formula", _tr["expr"],
                            f"formula {canonical!r} translated deterministically -> "
                            f"{_tr['expr'][:120]}")
            if u in formula_ds_cols:
                # Carried as a column of the formula-resolution dataset.
                # Bind to it with a scoped First() so the value resolves
                # from any context (page header/footer or a Tablix bound
                # to a different dataset). The user completes the
                # column's SQL and Refresh Fields wires it up.
                literal_expr = (
                    f'=First(Fields!{_safe(canonical)}.Value, '
                    f'"{_FORMULA_DATASET_NAME}")'
                )
                note = (
                    f"formula {canonical!r}: bound to dataset "
                    f"{_FORMULA_DATASET_NAME}.{canonical}. SSRS has no "
                    f"formula construct -- replace that column's NULL with "
                    f"its SQL expression, then Refresh Fields. "
                    f"PL/SQL: {body_preview!r}"
                )
            else:
                # No dataset column (e.g. an Oracle <summary> aggregate).
                # Emit =Nothing so the PDF shows nothing; the audit note
                # records what to re-implement.
                literal_expr = "=Nothing"
                note = (
                    f"PLACEHOLDER (formula): <{canonical} \u2014 populate "
                    f"at deploy time>. token {token!r} maps to Oracle "
                    f"formula {canonical!r}; emitted =Nothing. "
                    f"Re-implement as an SSRS calculated field."
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
            _lk = _lookup_for_child(canonical, owner_ds, dataset_name)
            if _lk:
                return ("field_other_ds", _lk,
                        f"master-detail Lookup: {canonical} pulled from "
                        f"{owner_ds} via Oracle <link>; verify join key after upload.")
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
        # 4b) Layout FIELD NAME -> resolve to its SOURCE column, then re-resolve.
        # Prevents a dangling Fields!F_X.Value when an expression references a
        # display field by its layout name instead of the underlying column
        # (e.g. F_CF_AVG_HAUL -> CF_Avg_Haul -> scoped formula ref).
        if _depth < 4 and u in layout_field_source:
            return resolve(layout_field_source[u], dataset_name, _depth + 1)
        # 5) Fallback - legacy behavior, marked unverified.
        return (
            "field_unverified",
            token,
            f"token {token!r} not declared as Parameter or DataItem; emitted as Fields!{token}.Value",
        )

    return resolve


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

    def _chunk_atoms(chunk: str) -> List[str]:
        """Turn a literal text chunk into VB expression atoms.

        A RAW newline inside a VB string literal is silently swallowed by
        the report expression compiler (verified by rendering through the
        real MS engine: multi-line blocks collapsed onto one line). So
        newlines must become explicit vbCrLf atoms. Runs of blank /
        whitespace-only lines collapse to a single vbCrLf — that matches
        what Oracle's own renderer produces for these pretty-printed
        CDATA text blocks (ground truth: the Oracle PDF stacks the lines
        directly). Lines are stripped of XML indentation, EXCEPT that a
        single space is preserved where the chunk abuts a token on the
        same line ("... IS DUE " & Fields!X — the space matters).
        """
        lines = chunk.split("\n")
        atoms: List[str] = []
        pending_break = False
        for i, ln in enumerate(lines):
            s = ln.strip()
            # Preserve token-adjacent single spaces on the SAME line:
            #  * first line, chunk follows a token -> keep one leading space
            #  * last line, a token follows the chunk -> keep one trailing
            if s:
                if i == 0 and ln[:1].isspace():
                    s = " " + s
                if i == len(lines) - 1 and ln[-1:].isspace():
                    s = s + " "
            if i > 0:
                pending_break = True
            if s:
                if pending_break:
                    atoms.append("vbCrLf")
                pending_break = False
                atoms.append('"' + _q_safe(s) + '"')
        if pending_break:
            atoms.append("vbCrLf")
        return atoms

    parts: List[str] = []
    last = 0
    any_token = False
    for m in combined_re.finditer(text):
        any_token = True
        literal_chunk = text[last:m.start()]
        if literal_chunk:
            parts.extend(_chunk_atoms(literal_chunk))
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
        parts.extend(_chunk_atoms(tail))
    if not any_token or not parts:
        return text, False
    # Drop leading/trailing line breaks so boxes don't grow blank lines.
    while parts and parts[0] == "vbCrLf":
        parts.pop(0)
    while parts and parts[-1] == "vbCrLf":
        parts.pop()
    if not parts:
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

    # Body-direct items have NO row context, so a Lookup() there is never
    # valid: its per-row source argument has no scope, and wrapping the
    # args in First() produces "aggregate function in an argument to a
    # Lookup function" — a PUBLISH-time rejection (caught by rendering
    # through the real MS engine). The faithful degradation outside a data
    # region is the dataset-scoped aggregate of the RESULT expression:
    #     Lookup(src, dst, RESULT, "DS")  ->  First(RESULT, "DS")
    def _degrade_body_level_lookups(text: str) -> str:
        out = text
        while True:
            m_l = re.search(r"\bLookup(Set)?\s*\(", out)
            if not m_l:
                return out
            depth, i = 1, m_l.end()
            commas = []
            while i < len(out) and depth:
                ch = out[i]
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        break
                elif ch == "," and depth == 1:
                    commas.append(i)
                i += 1
            if depth != 0 or len(commas) != 3:
                return out  # malformed/unexpected — leave untouched
            arg3 = out[commas[1] + 1: commas[2]].strip()
            arg4 = out[commas[2] + 1: i].strip()
            repl = f"First({arg3}, {arg4})"
            out = out[: m_l.start()] + repl + out[i + 1:]

    expr = _degrade_body_level_lookups(expr)

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


# ---------------------------------------------------------------------------
# F1: Oracle formatMask -> SSRS <Format>
# ---------------------------------------------------------------------------

_ORACLE_DATE_TOKENS = [
    ("MONTH", "MMMM"), ("MON", "MMM"),
    ("DAY", "dddd"), ("DY", "ddd"),
    ("YYYY", "yyyy"), ("RRRR", "yyyy"), ("YY", "yy"), ("RR", "yy"),
    ("HH24", "HH"), ("HH12", "hh"), ("HH", "hh"),
    ("MI", "mm"), ("SS", "ss"),
    ("A.M.", "tt"), ("P.M.", "tt"), ("AM", "tt"), ("PM", "tt"),
    ("MM", "MM"), ("DD", "dd"),
]


def _oracle_date_to_net(mu: str) -> str:
    """Translate an Oracle date mask (uppercased) to a .NET date format,
    matching the longest token first; separators (/ - : . , space) pass through."""
    out = []
    i, n = 0, len(mu)
    while i < n:
        for tok, net in _ORACLE_DATE_TOKENS:
            if mu.startswith(tok, i):
                out.append(net)
                i += len(tok)
                break
        else:
            out.append(mu[i])
            i += 1
    return "".join(out)


def _oracle_number_to_net(m: str) -> str:
    """Translate an Oracle numeric mask to a .NET custom numeric format:
    9/N -> # (optional digit), 0 -> 0 (required), keep , . $ %, G->',' D->'.'."""
    out = []
    for ch in m:
        if ch in "9N":
            out.append("#")
        elif ch in "0,.$%":
            out.append(ch)
        elif ch == "G":
            out.append(",")
        elif ch == "D":
            out.append(".")
        # FM / MI / S / PR / parens etc. are dropped (best effort)
    return "".join(out)


def _oracle_mask_to_net(mask: str) -> str:
    """Translate an Oracle Reports formatMask to a .NET (SSRS) format string.
    Returns "" when empty or unrecognized (caller then emits no <Format>).
    Generic -- pattern-driven, no per-report logic."""
    if not mask:
        return ""
    m = mask.strip()
    # Oracle fill-mode / format modifiers (FM, FX) have no .NET equivalent.
    m = re.sub(r"(?i)F[MX]", "", m).strip()
    if not m:
        return ""
    mu = m.upper()
    is_date = (
        any(tok in mu for tok in ("YYYY", "RRRR", "MON", "MONTH", "DAY",
                                  "DY", "HH", "AM", "PM"))
        or ("MM" in mu and "DD" in mu)
        or ("YY" in mu and ("/" in mu or "-" in mu))
    )
    if is_date:
        net = _oracle_date_to_net(mu)
        return net if any(c in net for c in "yMdHhms") else ""
    if any(c in m for c in "09N$%"):
        return _oracle_number_to_net(m)
    return ""


def _format_index(report) -> dict:
    """Map {field SOURCE name (upper) -> .NET format} for every layout field
    carrying an Oracle formatMask. Used to stamp <Format> on the matching
    Textbox values so SSRS renders currency / dates / thousands like Oracle."""
    idx: dict = {}

    def walk(groups):
        for g in groups or []:
            for f in (g.fields or []):
                mask = getattr(f, "format_mask", "") or ""
                src = (getattr(f, "source", "") or "").strip()
                if mask and src:
                    net = _oracle_mask_to_net(mask)
                    if net:
                        idx.setdefault(src.upper(), net)
            walk(getattr(g, "children", None) or [])

    walk(getattr(report, "layout", None) or [])
    return idx


_PURE_FIELD_RE = re.compile(
    r'^\s*=\s*(?:First\(\s*)?Fields!([A-Za-z0-9_]+)\.Value'
    r'(?:\s*,\s*"[^"]*")?\s*\)?\s*$'
)


def _apply_field_formats(root, report) -> None:
    """Central post-pass: stamp <Format> onto every Textbox whose value is a
    PURE field reference (=Fields!X.Value, optionally wrapped in First(...))
    when X carried an Oracle formatMask. One archetype-agnostic pass covers the
    grid / card / nested / per-record builders identically. Concatenated values
    (label & field) are skipped -- a numeric/date format can't apply to those."""
    idx = _format_index(report)
    if not idx:
        return
    for tb in root.iter(_q("Textbox")):
        run = next(iter(tb.iter(_q("TextRun"))), None)
        if run is None:
            continue
        val_el = run.find(_q("Value"))
        if val_el is None or not (val_el.text or "").strip():
            continue
        mobj = _PURE_FIELD_RE.match(val_el.text)
        if not mobj:
            continue
        net = idx.get(mobj.group(1).upper())
        if not net:
            continue
        style = run.find(_q("Style"))
        if style is None:
            style = _sub(run, "Style")
        if style.find(_q("Format")) is None:
            _sub(style, "Format", net)


_ORACLE_PAGE_BUILTINS = {
    "PAGENUMBER", "PHYSICALPAGENUMBER", "LOGICALPAGENUMBER",
    "TOTALPHYSICALPAGES", "TOTALLOGICALPAGES", "TOTALPAGES",
    "PANELNUMBER", "TOTALPANELS",
}


def _repair_dangling_field_refs(root, report) -> None:
    """Generic publish-safety net (wild-corpus verified): rewrite every
    ``Fields!X.Value`` whose X is NOT a field of the textbox's dataset scope.

    Secondary layout builders bind layout sources verbatim; in the wild, X
    is frequently really:
      * a report PARAMETER (case/prefix variants)    -> Parameters!P.Value
      * a field of ANOTHER dataset                   -> First(F, "ThatDS")
      * the same field with different CASING         -> exact-cased field
      * an Oracle page builtin (PageNumber etc.)     -> Nothing (body scope)
      * unknown                                       -> Nothing (honest)
    Every one of these as a raw Fields! ref is a PUBLISH-time rejection on
    the server ("refers to the field X ... not in the dataset") — caught by
    rendering hunted internet artifacts through the real MS engine."""
    # Truth = what was actually EMITTED into the RDL.
    ds_fields: dict = {}
    for ds in root.iter(_q("DataSet")):
        nm = ds.get("Name") or ""
        ds_fields[nm] = {f.get("Name") for f in ds.iter(_q("Field"))
                         if f.get("Name")}
    params = {}
    for rp in root.iter(_q("ReportParameter")):
        nm = rp.get("Name") or ""
        if nm:
            params[nm.upper()] = nm
    parent = {c: p for p in root.iter() for c in p}

    def scope_of(el) -> str:
        cur = el
        while cur is not None:
            if cur.tag == _q("Tablix"):
                return cur.findtext(_q("DataSetName")) or ""
            cur = parent.get(cur)
        return ""

    ref_re = re.compile(r"Fields!([A-Za-z_][A-Za-z0-9_]*)\.Value")
    _CALL_RE = re.compile(
        r"\b(First|Last|Sum|Avg|Min|Max|Count|CountDistinct|CountRows|"
        r"StDev|StDevP|Var|VarP|Aggregate|RunningValue|Previous|"
        r"Lookup|LookupSet|MultiLookup)\s*\(")

    def _protected_spans(text: str):
        """Spans of aggregate/Lookup calls that carry a top-level string
        argument (a dataset scope). Refs inside them are ALREADY scoped —
        rewriting one nests an aggregate inside an aggregate/Lookup, which
        SSRS rejects at publish time."""
        spans = []
        for m in _CALL_RE.finditer(text):
            depth, i, has_str = 1, m.end(), False
            while i < len(text) and depth:
                ch = text[i]
                if ch == '"':
                    j = text.find('"', i + 1)
                    if depth == 1 and j > i:
                        has_str = True
                    i = j if j > i else len(text)
                elif ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                i += 1
            if has_str and depth == 0:
                spans.append((m.start(), i))
        return spans

    for v in root.iter(_q("Value")):
        text = v.text or ""
        if "Fields!" not in text:
            continue
        scope_ds = scope_of(v)
        scope_fields = ds_fields.get(scope_ds, set())
        spans = _protected_spans(text)

        def _fix_one(m):
            if any(s <= m.start() < e for s, e in spans):
                return m.group(0)        # already inside a scoped call
            nm = m.group(1)
            if nm in scope_fields:
                return m.group(0)
            up = nm.upper()
            # Same field, different casing, in the scope dataset.
            cased = next((f for f in scope_fields if f.upper() == up), None)
            if cased:
                return f"Fields!{cased}.Value"
            if up in params:
                return f"Parameters!{params[up]}.Value"
            if ("P_" + up) in params:
                return f"Parameters!{params['P_' + up]}.Value"
            if up in _ORACLE_PAGE_BUILTINS:
                return "Nothing"
            for d, fs in ds_fields.items():
                hit = next((f for f in fs if f.upper() == up), None)
                if hit:
                    return f'First(Fields!{hit}.Value, "{d}")'
            return "Nothing"

        new_text = ref_re.sub(_fix_one, text)
        if new_text != text:
            v.text = new_text


def _scope_body_direct_field_refs(root, report) -> None:
    """Upload-safety net for ANY report shape: a Textbox <Value> that
    references Fields!X.Value while NOT inside a data region (Tablix/List/
    Matrix) must be a dataset-scoped aggregate, or SSRS rejects the upload
    ('...references a field ... without a scope'). Walk the finished tree;
    inside a data region leave values alone, outside wrap bare refs via the
    existing _wrap_unscoped_aggregates. Idempotent + conservative (only touches
    fields with a KNOWN dataset) -- a no-op for already-correct reports and a
    guard for future shapes the archetype renderers haven't met yet."""
    regions = {_q("Tablix"), _q("List"), _q("Matrix"), _q("Chart"),
               _q("CustomReportItem")}
    val_tag = _q("Value")

    def walk(el, in_region):
        here = in_region or (el.tag in regions)
        for child in el:
            if child.tag == val_tag and not here:
                t = child.text or ""
                if t and "Fields!" in t:
                    child.text = _wrap_unscoped_aggregates(
                        t, report, in_tablix_scope=False)
            walk(child, here)

    walk(root, False)


def _is_id_field(name: str) -> bool:
    """Heuristic: does this column look like an ID/number field?
    Used to pick the "record number" line of a card. Generic suffix
    match -- no specific column names hardcoded."""
    u = (name or "").upper()
    return u.endswith("_ID") or u.endswith("_NUM") or u.endswith("ID")


def _ssrs_summary_fn(fn: str) -> str:
    """Map an Oracle Reports <summary> function token to the SSRS aggregate
    name. Oracle emits full words (average/minimum/maximum/std deviation) as
    well as the short forms (avg/min/max); accept both spellings. Unknown
    tokens fall back to Sum. Generic -- no per-report logic."""
    f = (fn or "sum").strip().lower()
    return {
        "count": "Count", "sum": "Sum",
        "avg": "Avg", "average": "Avg",
        "max": "Max", "maximum": "Max",
        "min": "Min", "minimum": "Min",
        "first": "First", "last": "Last",
        "stddev": "StDev", "std deviation": "StDev", "stdeviation": "StDev",
        "variance": "Var", "var": "Var",
        "count distinct": "CountDistinct", "countdistinct": "CountDistinct",
    }.get(f, "Sum")


def _summary_total_expr(summaries, main, declared) -> str:
    """Build ONE concatenated total expression from a group's declared Oracle
    <summary> list -- e.g. ="Region Total: " & Sum(Fields!SALES.Value) & "   " &
    "Avg Margin: " & Avg(Fields!MARGIN.Value). Returns "" when no summary's
    source binds to a real dataset column. Generic: EVERY declared summary is
    emitted (not just the first), each via the Oracle->SSRS function map."""
    segs = []
    for sm in (summaries or []):
        src = (sm.get("source") or "").upper()
        if not src or src not in declared:
            continue
        fn = _ssrs_summary_fn(sm.get("function") or "sum")
        label = _clean_label(sm.get("label") or "") or "Total"
        segs.append(f'"{label}: " & {fn}(Fields!{_safe(_orig_name(main, src))}.Value)')
    if not segs:
        return ""
    return "=" + ' & "   " & '.join(segs)


def _clean_label(text):
    """Strip trailing colon + whitespace so we never get "Status::"."""
    if not text:
        return ""
    s = str(text).strip().rstrip(":").strip()
    return s


def _abbrev_expand(name):
    """Generic abbrev->word expansion for labels derived from SQL names."""
    if not name:
        return ""
    s = name.replace("_", " ").strip()
    subs = [
        (r"\bRecvd\b", "Received"),
        (r"\bCnty\b",  "County"),
        (r"\bAddr\b",  "Address"),
        (r"\bDt\b",    "Date"),
        (r"\bDesc\b",  "Description"),
        (r"\bId\b",    "ID"),
        (r"\bNum\b",   "Number"),
    ]
    for pat, repl in subs:
        s = re.sub(pat, repl, s, flags=re.IGNORECASE)
    return " ".join(
        w if (w.isupper() and len(w) <= 4) else w.title()
        for w in s.split()
    )


def _drop_code_twins(items):
    """Generic: if two fields share a stem and one is a code/abbrev (_NAME,
    _CODE, _ID) while the other is the descriptive form (_DESC, _DESCRIPTION,
    _TEXT), drop the code/abbrev form and keep the descriptive one.

    Example: ACTION_TYPE_NAME ("INL") + ACTION_TYPE_DESC ("Initial Notice
    Letter") -> keep only ACTION_TYPE_DESC. No report-specific column names
    are hardcoded -- works for any STEM_NAME / STEM_DESC pair (e.g.
    PROD_CODE / PROD_DESCRIPTION).
    """
    CODE_SUFFIXES = ("_NAME", "_CODE", "_ID")
    DESC_SUFFIXES = ("_DESC", "_DESCRIPTION", "_TEXT")

    def _stem_and_role(name):
        u = (name or "").upper()
        for s in DESC_SUFFIXES:
            if u.endswith(s) and len(u) > len(s):
                return u[: -len(s)], "desc"
        for s in CODE_SUFFIXES:
            if u.endswith(s) and len(u) > len(s):
                return u[: -len(s)], "code"
        return None, None

    desc_stems = set()
    for it in items:
        stem, role = _stem_and_role(it.name)
        if role == "desc":
            desc_stems.add(stem)

    out = []
    for it in items:
        stem, role = _stem_and_role(it.name)
        if role == "code" and stem in desc_stems:
            continue
        out.append(it)
    return out


def _split_card_fields(body_items):
    """Split card fields into (header, detail) by column-name prefix.

    Detail rows are per-action / per-history child rows from a join'd
    table (ACTION_*, HIST_*, LOG_*, etc). Match on COLUMN NAME only --
    NOT the user-facing label -- so a header column like STATUS_DESC
    (label="Status:") isn't mis-routed into the detail sub-table just
    because its label happens to start with the word "Status".

    Also drops code/abbrev twins of descriptive fields generically -- so a
    sub-table that has e.g. ACTION_TYPE_NAME ("INL") and ACTION_TYPE_DESC
    ("Initial Notice Letter") will keep only the descriptive one.
    """
    DETAIL_PREFIXES = ("ACTION_", "STATUS_", "HIST_", "LOG_", "EVENT_",
                       "COMMENT_", "NOTE_")
    header = []
    detail = []
    for it in body_items:
        u_name = (it.name or "").upper()
        is_detail = any(u_name.startswith(p) for p in DETAIL_PREFIXES)
        (detail if is_detail else header).append(it)
    header = _drop_code_twins(header)
    detail = _drop_code_twins(detail)
    if not header and detail:
        return detail, []
    return header, detail


def _pair_card_header_rows(header_items):
    """Group header_items into (left, right) row pairs by semantic role.

    Generic: ordered token lists declare the conventional two-up card
    layout (record identity / location / primary date on the LEFT;
    status / city / secondary date on the RIGHT). No report-specific
    column names are hardcoded -- the tokens are common SQL semantic
    words.

    A field's column NAME is checked first (the deterministic Oracle
    identifier). The user-facing LABEL is consulted only when the name
    yields no match -- labels are inconsistent and would otherwise pull
    e.g. a column named *_OBSERVED_DT (label "Bust Date") onto the
    wrong side.

    ROW ORDER within each column follows the DECLARATION ORDER of the
    pattern list, NOT the source-XML order: the LEFT list places OWNER
    before LOCATION before RECEIVED, so the rendered card opens with
    "Owner | Status", then "Location | City", then "Received | Bust"
    regardless of how the underlying SELECT happens to order columns.
    Fields that match no pattern preserve relative XML order and are
    appended below the pattern-driven rows.

    Returns: list of (left_item_or_None, right_item_or_None) tuples.
    """
    # NOTE: tokens are matched as substrings of the (underscored) NAME.
    # ORDER MATTERS: it drives the rendered row order.
    LEFT_PATTERNS = (
        "OWNER",
        "LOCATION", "ADDRESS", "ADDR",
        "RECVD", "RECEIVED",
        "REFERRED",
        "COMPLAINT_REF", "COMPLNT_REF",
        "CONTRACTOR",
    )
    RIGHT_PATTERNS = (
        "STATUS", "STAT_TYPE",
        "CITY",
        "OBSERVED", "BUST",
    )

    def _first_match_idx(haystack, patterns):
        for i, p in enumerate(patterns):
            if p in haystack:
                return i
        return None

    def _classify(it):
        u = (it.name or "").upper()
        # Right takes precedence on the name side so e.g. ADDR_CITY
        # (contains both ADDR and CITY) classifies as RIGHT -- "CITY"
        # is more specific than the generic ADDR* family. Same reason
        # for *_STAT_TYPE_*.
        r = _first_match_idx(u, RIGHT_PATTERNS)
        if r is not None:
            return ("right", r)
        l = _first_match_idx(u, LEFT_PATTERNS)
        if l is not None:
            return ("left", l)
        lbl = (it.label or "").upper()
        r = _first_match_idx(lbl, RIGHT_PATTERNS)
        if r is not None:
            return ("right", r)
        l = _first_match_idx(lbl, LEFT_PATTERNS)
        if l is not None:
            return ("left", l)
        return ("unknown", None)

    left_buckets, right_buckets, unknowns = [], [], []
    for src_idx, it in enumerate(header_items):
        side, pat_idx = _classify(it)
        if side == "left":
            left_buckets.append((pat_idx, src_idx, it))
        elif side == "right":
            right_buckets.append((pat_idx, src_idx, it))
        else:
            unknowns.append(it)

    # Sort by (pattern-list position, source-XML position) so the
    # rendered row order matches the LEFT_PATTERNS declaration order.
    left_buckets.sort(key=lambda t: (t[0], t[1]))
    right_buckets.sort(key=lambda t: (t[0], t[1]))

    def _dedupe_pattern_slots(buckets, n_patterns):
        """If multiple fields hit the same pattern slot, the first one
        (by src_idx, already sorted) keeps that slot; later duplicates
        are bumped to the next unused slot >= the pattern range so they
        don't crowd out items that match later patterns."""
        out = []
        used = set()
        bumped_floor = n_patterns
        # First pass: claim unique slots in order
        for pat_idx, src_idx, it in buckets:
            if pat_idx not in used:
                used.add(pat_idx)
                out.append((pat_idx, src_idx, it))
            else:
                # bump to next free slot beyond the pattern list
                while bumped_floor in used:
                    bumped_floor += 1
                used.add(bumped_floor)
                out.append((bumped_floor, src_idx, it))
                bumped_floor += 1
        out.sort(key=lambda t: (t[0], t[1]))
        return [t[2] for t in out]

    lefts  = _dedupe_pattern_slots(left_buckets,  len(LEFT_PATTERNS))
    rights = _dedupe_pattern_slots(right_buckets, len(RIGHT_PATTERNS))

    # Spill unknowns into the shorter column, preserving XML order.
    for it in unknowns:
        if len(lefts) <= len(rights):
            lefts.append(it)
        else:
            rights.append(it)

    rows = []
    n = max(len(lefts), len(rights))
    for i in range(n):
        l = lefts[i] if i < len(lefts) else None
        r = rights[i] if i < len(rights) else None
        rows.append((l, r))
    return rows


def _sort_detail_columns(items):
    """Sort action/history sub-table columns into canonical order:
    Type | Comments/Description | Date. Pure name-pattern."""
    def _bucket(it):
        u = (it.name or "").upper()
        if "TYPE" in u and ("DESC" in u or "NAME" in u or u.endswith("TYPE")):
            return 0
        if any(tok in u for tok in ("COMMENT", "DESCR", "NOTE", "REMARK")):
            return 1
        if any(tok in u for tok in ("_DT", "_DATE", "TIMESTAMP", "WHEN")):
            return 2
        return 3
    orig = {id(it): i for i, it in enumerate(items)}
    return sorted(items, key=lambda it: (_bucket(it), orig[id(it)]))


def _resolve_palette(report: "ParsedReport") -> Dict[str, str]:
    """Derive a styling palette from per-report <visualSettings>.

    Generic, name-agnostic heuristic. Reads colors that the parser
    already captured into LayoutGroup.background_color /
    foreground_color and LayoutField.background_color. Falls back to
    the historical hardcoded defaults whenever the source XML has no
    color signals.

    Keys returned:
        band_bg, band_fg          -- top band strip
        subhdr_bg, subhdr_fg      -- sub-header / section strip
        card_bg                   -- detail card body (always white)
        ink, ink_soft             -- body / secondary text
        rule                      -- thin separator rule

    Rules (purely heuristic):
        band_bg   = background_color of the FIRST repeating_frame whose
                    parent (or self) has a background_color set; else
                    the most-common non-white background_color across
                    all groups; else default "#03047e".
        band_fg   = if band_bg is dark (luminance < 0.5), lightest
                    foreground_color seen anywhere; else "#000000".
                    Default "#fffe31".
        subhdr_bg = lightest non-white background_color OTHER than the
                    one we chose for band_bg; default "#d6d6d6".
        subhdr_fg = same as band_bg (matches reference look); default
                    "#03047e".
        card_bg   = "#ffffff" always.
        ink, ink_soft, rule = defaults unless overridden by foreground
                    hints.
    """
    DEFAULTS = {
        "band_bg":   "#03047e",
        "band_fg":   "#fffe31",
        "subhdr_bg": "#d6d6d6",
        "subhdr_fg": "#03047e",
        "card_bg":   "#ffffff",
        "ink":       "#282828",
        "ink_soft":  "#282828",
        "rule":      "#777777",
    }

    def _norm(c):
        if not c:
            return ""
        s = str(c).strip()
        if not s:
            return ""
        if not s.startswith("#"):
            s = "#" + s
        # Normalise to 7-char uppercase #RRGGBB when possible.
        if len(s) == 7:
            return "#" + s[1:].upper()
        return s.upper()

    def _is_white(c):
        return _norm(c) in ("#FFFFFF", "#FFFFFE", "#FEFEFE")

    def _luminance(c):
        c = _norm(c)
        if not c or len(c) != 7:
            return 1.0
        try:
            r = int(c[1:3], 16) / 255.0
            g = int(c[3:5], 16) / 255.0
            b = int(c[5:7], 16) / 255.0
        except ValueError:
            return 1.0
        # Rec 601 luma; good enough for "is this dark" decisions.
        return 0.299 * r + 0.587 * g + 0.114 * b

    # Flatten every group (recurse children) so we see nested frames too.
    flat = []
    def _walk(grp):
        flat.append(grp)
        for ch in getattr(grp, "children", []) or []:
            _walk(ch)
    for g in (getattr(report, "layout", []) or []):
        _walk(g)

    # Gather candidate background_colors from groups (non-white only).
    group_bgs = []
    for g in flat:
        bg = _norm(getattr(g, "background_color", "") or "")
        if bg and not _is_white(bg):
            group_bgs.append((g, bg))

    # band_bg: prefer the first repeating_frame's background, else
    # walk parent chain (we approximate by scanning ancestors via the
    # 'flat' order, falling back to most-common bg).
    band_bg = ""
    for g, bg in group_bgs:
        if getattr(g, "kind", "") == "repeating_frame":
            band_bg = bg
            break
    if not band_bg and group_bgs:
        # most-common non-white bg across all groups
        from collections import Counter
        ctr = Counter(bg for _, bg in group_bgs)
        band_bg = ctr.most_common(1)[0][0]
    if not band_bg:
        band_bg = DEFAULTS["band_bg"]

    # subhdr_bg: lightest non-white bg that ISN'T band_bg AND is
    # genuinely light enough to read dark text on. Oracle XML often
    # carries text/foreground colors in <visualSettings> background
    # slots (e.g. #282828) which would otherwise render as a near-
    # black bar -- unreadable. Clamp to a minimum luminance so the
    # card header strip is always a readable grey.
    SUBHDR_MIN_LUM = 0.70
    other_bgs = [bg for _, bg in group_bgs
                 if bg != band_bg and _luminance(bg) >= SUBHDR_MIN_LUM]
    if other_bgs:
        subhdr_bg = max(other_bgs, key=_luminance)
    else:
        subhdr_bg = DEFAULTS["subhdr_bg"]

    # band_fg: black on light bands, lightest fg on dark bands.
    if _luminance(band_bg) >= 0.5:
        band_fg = "#000000"
    else:
        # Collect foreground hints from groups + fields.
        fgs = []
        for g in flat:
            fg = _norm(getattr(g, "foreground_color", "") or "")
            if fg and not _is_white(fg):
                fgs.append(fg)
            for f in (getattr(g, "fields", []) or []):
                ffg = _norm(getattr(f, "foreground_color", "") or "")
                if ffg and not _is_white(ffg):
                    fgs.append(ffg)
        if fgs:
            band_fg = max(fgs, key=_luminance)
            # If the lightest fg is still darker than mid-grey, fall back
            # to the bright default -- a dark band needs a light label.
            if _luminance(band_fg) < 0.5:
                band_fg = DEFAULTS["band_fg"]
        else:
            band_fg = DEFAULTS["band_fg"]

    # subhdr_fg: matches the band_bg per reference behaviour (e.g.
    # navy text on light-grey strip), but if that color has poor
    # contrast against subhdr_bg fall back to ink. After the
    # luminance clamp above subhdr_bg is always light, so a light
    # subhdr_fg would be invisible -- never let that happen.
    subhdr_fg = band_bg
    if _luminance(subhdr_fg) >= 0.6:
        # band_bg is itself light -- use ink so text is readable.
        subhdr_fg = DEFAULTS["ink"]
    elif abs(_luminance(subhdr_fg) - _luminance(subhdr_bg)) < 0.15:
        subhdr_fg = DEFAULTS["ink"]

    return {
        "band_bg":   band_bg or DEFAULTS["band_bg"],
        "band_fg":   band_fg or DEFAULTS["band_fg"],
        "subhdr_bg": subhdr_bg or DEFAULTS["subhdr_bg"],
        "subhdr_fg": subhdr_fg or DEFAULTS["subhdr_fg"],
        "card_bg":   DEFAULTS["card_bg"],
        "ink":       DEFAULTS["ink"],
        "ink_soft":  DEFAULTS["ink_soft"],
        "rule":      DEFAULTS["rule"],
    }


def _layout_geometry_index(report):
    """Walk section_main and return geometry maps used to render a nested
    master-detail report 1:1 with the Oracle layout:

      field_geo:  SOURCE_UPPER -> (x, y, width)            (data fields)
      label_geo:  list of (text, x, y, frame_bg)           (static labels/headers)

    Both are flat (positions are absolute within section_main). Purely
    structural -- no report-specific names. Returns ({}, []) when there's no
    section_main (e.g. a synthetic SQL-only report) so callers fall back to the
    generic stacked layout.
    """
    field_geo = {}
    label_geo = []

    main = None
    for g in (report.layout or []):
        if (g.kind or "").lower() == "section_main":
            main = g
            break
        for c in (g.children or []):
            if (c.kind or "").lower() == "section_main":
                main = c
                break
    if main is None:
        return field_geo, label_geo

    def walk(g, frame_bg):
        bg = getattr(g, "background_color", "") or frame_bg
        for f in (g.fields or []):
            if f.kind == "field" and f.source:
                key = f.source.upper()
                # keep the FIRST occurrence (closest to the data row)
                field_geo.setdefault(key, (
                    float(getattr(f, "x", 0.0) or 0.0),
                    float(getattr(f, "y", 0.0) or 0.0),
                    float(getattr(f, "width", 0.0) or 0.0),
                ))
            elif f.kind == "text" and (f.text or "").strip():
                label_geo.append((
                    (f.text or "").strip(),
                    float(getattr(f, "x", 0.0) or 0.0),
                    float(getattr(f, "y", 0.0) or 0.0),
                    bg,
                ))
        for c in (g.children or []):
            walk(c, bg)

    walk(main, "")
    return field_geo, label_geo


def _detail_band_fields(report):
    """Return layout data fields that form the DETAIL TABLE band: the y-row,
    within section_main, that has the most distinct-x field positions (>=2).
    Each entry is (source, x, y, width). Plus the "wrap" fields directly below
    that band (full-width comment lines like ACTION_HIST_DESCR). Returns
    (row_fields, wrap_fields, row_y) or ([], [], None). Driven purely by parsed
    geometry, including CF_/CP_ formula-bound fields (the resolver handles those
    sources downstream). No report-specific names."""
    main = None
    for g in (report.layout or []):
        if (g.kind or "").lower() == "section_main":
            main = g; break
        for c in (g.children or []):
            if (c.kind or "").lower() == "section_main":
                main = c; break
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
    # group by rounded y
    from collections import defaultdict
    by_y = defaultdict(list)
    for s, x, y, w in fields:
        by_y[round(y, 2)].append((s, x, y, w))
    # the table band = the y with the most DISTINCT x positions
    best_y = None; best_n = 0
    for y, lst in by_y.items():
        nx = len({round(x, 1) for _s, x, _y, _w in lst})
        if nx > best_n:
            best_n = nx; best_y = y
    if best_y is None or best_n < 2:
        return [], [], None
    row = sorted(by_y[best_y], key=lambda z: z[1])
    # wrap fields: single-x rows just BELOW the band (y within ~0.4in)
    wrap = []
    for y, lst in by_y.items():
        if 0 < (y - best_y) <= 0.4 and len({round(x, 1) for _s, x, _y, _w in lst}) == 1:
            wrap.extend(lst)
    wrap.sort(key=lambda z: z[2])
    return row, wrap, best_y


def _nearest_label(label_geo, x, y, max_dy=0.18, max_dx=1.4):
    """Find the static label that sits on (roughly) the same row to the LEFT of
    a field at (x, y) -- that's the field's printed caption (e.g. 'FITS Site:'
    left of the SITE_NAME value). Returns the label text (colon-stripped) or ''."""
    best = None
    best_dx = 1e9
    for text, lx, ly, _bg in label_geo:
        if abs(ly - y) <= max_dy and lx <= x + 0.05:
            dx = x - lx
            if 0 <= dx <= max_dx and dx < best_dx:
                best_dx = dx
                best = text
    return (best or "").strip().rstrip(":")


def _flatten_group_chain(groups):
    """Oracle emits master-detail groups as a flat sibling list under the
    dataSource, in master->detail ORDER (outermost first). Return that ordered
    chain of QueryGroup. A single group => not master-detail (caller falls back)."""
    chain = []

    def walk(gs):
        for g in gs:
            chain.append(g)
            if g.children:
                walk(g.children)

    walk(groups or [])
    return chain


def _nested_group_label(item):
    """Human label for a data item: its parsed defaultLabel, else a title-cased
    name. Strips a trailing colon Oracle sometimes bakes into defaultLabel."""
    lbl = (getattr(item, "label", "") or "").strip().rstrip(":")
    if lbl:
        return lbl
    return (item.name or "").replace("_", " ").title()


def _is_nested_master_detail(query):
    """True when the query's parsed group tree is a genuine master-detail
    hierarchy: >= 2 groups in the chain AND the outer group has a break column.
    Purely structural -- decided by the parsed Oracle <group> nesting/order."""
    chain = _flatten_group_chain(getattr(query, "groups", None) or [])
    if len(chain) < 2:
        return False
    return bool(chain[0].break_col)


def _is_grouped_card_report(query, report=None):
    """True when a report has genuine master-detail/card structure that the
    wallet-card Tablix represents, rather than being a FLAT table:

      * a DETAIL sub-table -- child rows whose column names carry
        ACTION_/STATUS_/HIST_/LOG_/EVENT_/COMMENT_/NOTE_ prefixes, OR
      * a linked detail query (an Oracle <link> child of this query).

    A plain column list with neither is an ordinary FLAT table and MUST render
    as a column grid (_build_tablix). The card path collapses the non-header
    fields with =First(Fields!X.Value), so forcing a flat table through it
    silently keeps only the first source row.

    NOTE: a group's break_col is deliberately NOT used as a signal -- the
    parser assigns it to the first data item of EVERY group (even a flat one,
    see oracle_xml `_build_group_tree`), so it cannot distinguish flat from
    grouped. Structural and generic; no per-report logic."""
    _header, detail = _split_card_fields(list(getattr(query, "items", None) or []))
    if detail:
        return True
    # A linked detail query makes this a master-detail report (rendered via the
    # card/Tablix detail group), not a flat table.
    if report is not None and query is not None:
        try:
            if _pick_detail_query(report, query.name) is not None:
                return True
        except Exception:
            pass
    return False


def _build_nested_group_tablix(report, main):
    """Render an Oracle nested master-detail report (County -> Complaint ->
    Action, etc.) as ONE Tablix whose row hierarchy mirrors the parsed
    <group> chain. Each group level becomes:

        - a GROUP member keyed on that group's break column, and
        - a static "card" row that prints that group's OWN data items
          (label: value), plus any <summary> total on the band.

    The innermost group's card becomes the repeating detail row. Everything is
    driven by the parsed group tree -- no name guessing, so it is deterministic
    and matches the Oracle structure 1:1. Falls back to the grouped-card Tablix
    when the chain isn't a real hierarchy.
    """
    chain = _flatten_group_chain(getattr(main, "groups", None) or [])
    if len(chain) < 2:
        return _build_grouped_card_tablix(report, main)

    palette = _resolve_palette(report)
    BAND_BG = palette["band_bg"]; BAND_FG = palette["band_fg"]
    SUBHDR_BG = palette["subhdr_bg"]
    INK = palette["ink"]; RULE = palette["rule"]

    # Field-name set actually present in the dataset (so we never reference a
    # column the DataSet <Fields> doesn't declare -> avoids scope BLOCKERs).
    declared = {(it.name or "").upper() for it in (main.items or [])}

    # Layout geometry: the Oracle layout gives every field a real (x, y, width)
    # and every caption a position. Using it lets us render the green band, the
    # navy column-header row, and the column-aligned detail row 1:1 instead of a
    # generic stack. Empty when there is no section_main (synthetic SQL ingest).
    field_geo, label_geo = _layout_geometry_index(report)
    def _gx(name, default=0.0):
        g = field_geo.get((name or "").upper())
        return g[0] if g else default
    def _gw(name, default=0.0):
        g = field_geo.get((name or "").upper())
        return g[2] if g else default

    def _printable_items(group, skip_break):
        out = []
        for it in (group.items or []):
            nm = (it.name or "")
            if not nm:
                continue
            if skip_break and nm.upper() == (group.break_col or "").upper():
                # break col still printed for the OUTER band; for inner cards
                # we keep it (it's the visible key e.g. Complaint ID).
                pass
            if nm.upper() in declared:
                out.append(it)
        return out

    tablix = ET.Element(_q("Tablix"))
    tablix.set("Name", "Tablix_Nested")
    tbody = _sub(tablix, "TablixBody")
    cols = _sub(tbody, "TablixColumns")
    _sub(_sub(cols, "TablixColumn"), "Width", "7.5in")
    rows = _sub(tbody, "TablixRows")

    LINE_H = 0.24
    row_specs = []  # (kind, group, height) parallel to TablixRows for the hierarchy

    outer = chain[0]
    inner_levels = chain[1:]

    bcol = (outer.break_col or (outer.items[0].name if outer.items else ""))
    middles = inner_levels[:-1] if len(inner_levels) >= 1 else []
    last = inner_levels[-1] if inner_levels else None

    outer_items = [it for it in (outer.items or [])
                   if it.name and it.name.upper() in declared]
    # Does the OUTER group's band have real layout geometry (positioned fields
    # at distinct y-rows, like a master-detail report's colored summary
    # block)? If so, render it 1:1 -- caption + value at their parsed x,
    # with the real band color.
    outer_band_color = ""
    for it in outer_items:
        g = field_geo.get(it.name.upper())
        if g:
            # band color = the frame bg carried by the nearest label, if any
            for text, lx, ly, bg in label_geo:
                if abs(ly - g[1]) <= 0.18 and bg:
                    outer_band_color = bg
                    break
        if outer_band_color:
            break
    geo_band = bool(outer_band_color) and sum(
        1 for it in outer_items if field_geo.get(it.name.upper())) >= 2

    # ---- ROW 1: outer BAND --------------------------------------------------
    if geo_band:
        band_bg = outer_band_color
        band_fg = "#ffffff"
        ys = sorted({round(field_geo[it.name.upper()][1], 2)
                     for it in outer_items if field_geo.get(it.name.upper())})
        y0 = ys[0]
        band_h = max(0.34, (ys[-1] - ys[0]) + 0.42)
        brow = _sub(rows, "TablixRow"); _sub(brow, "Height", f"{band_h:.2f}in")
        bcell = _sub(_sub(brow, "TablixCells"), "TablixCell")
        bcont = _sub(bcell, "CellContents")
        brect = _sub(bcont, "Rectangle"); brect.set("Name", "ND_Band")
        _sub(brect, "KeepTogether", "true")
        bs = _sub(brect, "Style"); _sub(bs, "BackgroundColor", band_bg)
        bri = _sub(brect, "ReportItems")
        for it in outer_items:
            g = field_geo.get(it.name.upper())
            if not g:
                continue
            fx, fy, fw = g
            top = max(0.04, fy - y0 + 0.06)
            cap = _nearest_label(label_geo, fx, fy) or _nested_group_label(it)
            val = (f'="{cap}:  " & IIF(IsNothing(Fields!{_safe(it.name)}.Value), '
                   f'"", CStr(Fields!{_safe(it.name)}.Value))')
            _build_textbox(bri, f"Tb_ND_Band_{_safe(it.name)}", val, bold=True,
                           font_size="11pt", bg=band_bg, fg=band_fg,
                           text_align="Left", vertical_align="Middle",
                           border_color=band_bg, padding="2pt", can_grow=True)
            _tb = bri[-1]
            # Place the caption at the LABEL's x (left of the value) so the
            # "FITS Site:  GREAT FALLS..." reads as one positioned line.
            lab_x = fx
            for text, lx, ly, _bg in label_geo:
                if abs(ly - fy) <= 0.18 and lx <= fx + 0.05 and (fx - lx) <= 1.4:
                    lab_x = min(lab_x, lx)
            _sub(_tb, "Top", f"{top:.2f}in"); _sub(_tb, "Left", f"{max(0.05, lab_x):.2f}in")
            _sub(_tb, "Width", f"{max(2.0, min(fw + (fx - lab_x), 7.3)):.2f}in")
            _sub(_tb, "Height", "0.24in")
        outer_card_items = []  # already rendered in the band
    else:
        # Simple label + summary band (the County / generic case).
        band_h = 0.34
        brow = _sub(rows, "TablixRow"); _sub(brow, "Height", f"{band_h:.2f}in")
        bcell = _sub(_sub(brow, "TablixCells"), "TablixCell")
        bcont = _sub(bcell, "CellContents")
        brect = _sub(bcont, "Rectangle"); brect.set("Name", "ND_Band")
        _sub(brect, "KeepTogether", "true")
        bs = _sub(brect, "Style"); _sub(bs, "BackgroundColor", BAND_BG)
        bri = _sub(brect, "ReportItems")
        band_left = _nested_group_label(outer.items[0]) if outer.items else outer.name
        if bcol and bcol.upper() in declared:
            band_val = (f'="{band_left}: " & CStr(Fields!{_safe(bcol)}.Value)')
        else:
            band_val = f'="{band_left}"'
        _build_textbox(bri, "Tb_ND_BandL", band_val, bold=True, font_size="11pt",
                       bg=BAND_BG, fg=BAND_FG, text_align="Left",
                       vertical_align="Middle", border_color=BAND_BG, padding="5pt")
        _tb = bri[-1]
        _sub(_tb, "Top", "0in"); _sub(_tb, "Left", "0in")
        _sub(_tb, "Width", "4.5in"); _sub(_tb, "Height", f"{band_h:.2f}in")
        if outer.summaries:
            # F4: emit ALL of the outer group's declared <summary> totals, not
            # just the first -- the rest were silently dropped before.
            sval = _summary_total_expr(outer.summaries, main, declared)
            if not sval:
                sval = f'="{_clean_label(outer.summaries[0].get("label") or "") or "Total"}"'
            _build_textbox(bri, "Tb_ND_BandTotal", sval, bold=True, font_size="11pt",
                           bg=BAND_BG, fg=BAND_FG, text_align="Right",
                           vertical_align="Middle", border_color=BAND_BG, padding="5pt")
            _tb = bri[-1]
            _sub(_tb, "Top", "0in"); _sub(_tb, "Left", "4.5in")
            _sub(_tb, "Width", "3.0in"); _sub(_tb, "Height", f"{band_h:.2f}in")
        # Outer-group non-break fields render as a follow-on card (generic case).
        outer_card_items = [it for it in outer_items
                            if it.name.upper() != (outer.break_col or "").upper()]
    row_specs.append(("band", outer, band_h))

    def _emit_card_row(group, rect_name, bg, explicit_items=None, emit_summary=True):
        flds = explicit_items if explicit_items is not None else _printable_items(group, skip_break=False)
        n = max(1, len(flds))
        # 2 columns of label:value pairs
        per_col = (n + 1) // 2
        # F4b: an inner-group subtotal line for this group's declared <summary>
        # totals (scoped to the group, since the card is a group-header member).
        # The OUTER group's summaries already render on the band, so the caller
        # passes emit_summary=False there to avoid duplicating them.
        grp_total = (_summary_total_expr(getattr(group, "summaries", None), main, declared)
                     if emit_summary else "")
        h = 0.22 + per_col * LINE_H + 0.10 + ((LINE_H + 0.06) if grp_total else 0.0)
        rrow = _sub(rows, "TablixRow"); _sub(rrow, "Height", f"{h:.2f}in")
        rc = _sub(_sub(rrow, "TablixCells"), "TablixCell")
        rcont = _sub(rc, "CellContents")
        rect = _sub(rcont, "Rectangle"); rect.set("Name", rect_name)
        _sub(rect, "KeepTogether", "true")
        st = _sub(rect, "Style"); _sub(st, "BackgroundColor", bg)
        _bb = _sub(st, "BottomBorder"); _sub(_bb, "Style", "Solid")
        _sub(_bb, "Color", RULE); _sub(_bb, "Width", "0.5pt")
        ri = _sub(rect, "ReportItems")
        for i, it in enumerate(flds):
            coln = i // per_col
            rown = i % per_col
            left = 0.10 + coln * 3.75
            top = 0.08 + rown * LINE_H
            lbl = _nested_group_label(it)
            val = (f'="{lbl}: " & IIF(IsNothing(Fields!{_safe(it.name)}.Value), '
                   f'"", CStr(Fields!{_safe(it.name)}.Value))')
            _build_textbox(ri, f"Tb_{rect_name}_{_safe(it.name)}", val,
                           bold=False, font_size="9pt", bg=bg, fg=INK,
                           text_align="Left", vertical_align="Top",
                           border_color=bg, padding="2pt", can_grow=True)
            _tb = ri[-1]
            _sub(_tb, "Top", f"{top:.2f}in"); _sub(_tb, "Left", f"{left:.2f}in")
            _sub(_tb, "Width", "3.60in"); _sub(_tb, "Height", f"{LINE_H:.2f}in")
        if grp_total:
            ty = 0.08 + per_col * LINE_H + 0.04
            _build_textbox(ri, f"Tb_{rect_name}_total", grp_total,
                           bold=True, font_size="9pt", bg=bg, fg=INK,
                           text_align="Right", vertical_align="Top",
                           border_color=bg, padding="2pt", can_grow=True)
            _ttb = ri[-1]
            _sub(_ttb, "Top", f"{ty:.2f}in"); _sub(_ttb, "Left", "0.10in")
            _sub(_ttb, "Width", "7.30in"); _sub(_ttb, "Height", f"{LINE_H:.2f}in")
        return h

    # Outer-group card (its non-break fields) -- placed directly under the band.
    if outer_card_items:
        h = _emit_card_row(outer, "ND_OuterCard", "#ffffff",
                           explicit_items=outer_card_items, emit_summary=False)
        row_specs.append(("outercard", outer, h))

    for mi, grp in enumerate(middles):
        h = _emit_card_row(grp, f"ND_Card{mi}", "#ffffff")
        row_specs.append((("card", mi), grp, h))

    # 3) The INNERMOST group's fields = the repeating DETAIL row. When the
    #    detail fields have real layout geometry that forms a COLUMN ROW (>=2
    #    fields sharing a y across >=2 distinct x), render a navy column-HEADER
    #    row (from the layout's caption texts on that band) + a column-aligned
    #    detail row, exactly like the Oracle table. Otherwise stack evenly.
    det_h = 0.26
    detail_has_header = False
    if last is not None:
        dflds = _printable_items(last, skip_break=False)
        # LAYOUT-DRIVEN detail table: collect the repeating-frame field band with
        # the most distinct-x positions. Uses layout sources (incl CF_/CP_
        # formula fields) so columns match the Oracle layout exactly, not just
        # the group's raw items. Values resolve via the standard resolver
        # (Fields! / formula-dataset First() / param) so no orphan refs.
        row_layout, wrap_layout, row_y = _detail_band_fields(report)
        main_ds = main.name or ""
        is_table = len(row_layout) >= 2 and len({round(x, 1) for _s, x, _y, _w in row_layout}) >= 2

        if is_table:
            # --- navy column-header row from the layout caption texts ---
            hdr_bg = "#00008B"; hdr_fg = "#ffffff"
            headers = []  # (text, x)
            # Column captions sit just ABOVE the detail row (smaller y). Accept
            # labels whose y is 0.02-0.6in above row_y.
            for text, lx, ly, _bg in label_geo:
                if -0.02 <= (row_y - ly) <= 0.6 and text and "&<" not in text:
                    headers.append((text, lx))
            if headers:
                detail_has_header = True
                hh = 0.24
                hrow = _sub(rows, "TablixRow"); _sub(hrow, "Height", f"{hh:.2f}in")
                hc = _sub(_sub(hrow, "TablixCells"), "TablixCell")
                hcont = _sub(hc, "CellContents")
                hrect = _sub(hcont, "Rectangle"); hrect.set("Name", "ND_ColHdr")
                hst = _sub(hrect, "Style"); _sub(hst, "BackgroundColor", hdr_bg)
                hri = _sub(hrect, "ReportItems")
                hsorted = sorted(headers, key=lambda z: z[1])
                for hi, (text, hx) in enumerate(hsorted):
                    nxt = hsorted[hi + 1][1] if hi + 1 < len(hsorted) else 7.5
                    _build_textbox(hri, f"Tb_NDHdr_{hi}", text, bold=True,
                                   font_size="9pt", bg=hdr_bg, fg=hdr_fg,
                                   text_align="Left", vertical_align="Middle",
                                   border_color=hdr_bg, padding="2pt", can_grow=False)
                    _tb = hri[-1]
                    _sub(_tb, "Top", "0in"); _sub(_tb, "Left", f"{max(0.02, hx):.2f}in")
                    _sub(_tb, "Width", f"{max(0.5, nxt - hx - 0.02):.2f}in")
                    _sub(_tb, "Height", f"{hh:.2f}in")
                row_specs.append(("colhdr", last, hh))

            # --- detail row: row fields at their real x, wrap fields below ---
            wrap_h = 0.22 * len(wrap_layout)
            det_h = 0.26 + wrap_h
            drow = _sub(rows, "TablixRow"); _sub(drow, "Height", f"{det_h:.2f}in")
            dc = _sub(_sub(drow, "TablixCells"), "TablixCell")
            dcont = _sub(dc, "CellContents")
            drect = _sub(dcont, "Rectangle"); drect.set("Name", "ND_Detail")
            ds_ = _sub(drect, "Style"); _sub(ds_, "BackgroundColor", "#ffffff")
            _db = _sub(ds_, "BottomBorder"); _sub(_db, "Style", "Solid")
            _sub(_db, "Color", RULE); _sub(_db, "Width", "0.25pt")
            dri = _sub(drect, "ReportItems")
            crow = sorted(row_layout, key=lambda z: z[1])
            for ci, (src, fx, fy, fw) in enumerate(crow):
                nxt = crow[ci + 1][1] if ci + 1 < len(crow) else 7.5
                val = _field_value_for(LayoutField(name="F", source=src, kind="field"),
                                       report, dataset_name=main_ds)
                if not val:
                    continue
                _build_textbox(dri, f"Tb_NDDet_{ci}_{_safe(src)}", val,
                               font_size="9pt", bg="#ffffff", fg=INK,
                               text_align="Left", vertical_align="Top",
                               border_color="#ffffff", padding="2pt", can_grow=True)
                _tb = dri[-1]
                _sub(_tb, "Top", "0in"); _sub(_tb, "Left", f"{max(0.02, fx):.2f}in")
                _sub(_tb, "Width", f"{max(0.5, nxt - fx - 0.02):.2f}in")
                _sub(_tb, "Height", "0.24in")
            for wi, (src, fx, fy, fw) in enumerate(wrap_layout):
                val = _field_value_for(LayoutField(name="F", source=src, kind="field"),
                                       report, dataset_name=main_ds)
                if not val:
                    continue
                _build_textbox(dri, f"Tb_NDWrap_{wi}_{_safe(src)}", val,
                               font_size="9pt", bg="#ffffff", fg=INK,
                               text_align="Left", vertical_align="Top",
                               border_color="#ffffff", padding="2pt", can_grow=True)
                _tb = dri[-1]
                _sub(_tb, "Top", f"{0.26 + wi*0.22:.2f}in")
                _sub(_tb, "Left", f"{max(0.02, fx):.2f}in")
                _sub(_tb, "Width", f"{max(2.0, 7.3 - fx):.2f}in")
                _sub(_tb, "Height", "0.22in")
            row_specs.append(("detail", last, det_h))
        else:
            # No geometry -> stack evenly (the original behavior).
            ncol = max(1, len(dflds))
            col_w = round(7.5 / ncol, 3)
            drow = _sub(rows, "TablixRow"); _sub(drow, "Height", f"{det_h:.2f}in")
            dc = _sub(_sub(drow, "TablixCells"), "TablixCell")
            dcont = _sub(dc, "CellContents")
            drect = _sub(dcont, "Rectangle"); drect.set("Name", "ND_Detail")
            ds_ = _sub(drect, "Style"); _sub(ds_, "BackgroundColor", "#ffffff")
            _db = _sub(ds_, "BottomBorder"); _sub(_db, "Style", "Solid")
            _sub(_db, "Color", RULE); _sub(_db, "Width", "0.25pt")
            dri = _sub(drect, "ReportItems")
            for ci, it in enumerate(dflds):
                _build_textbox(dri, f"Tb_NDDet_{_safe(it.name)}",
                               f"=Fields!{_safe(it.name)}.Value",
                               font_size="9pt", bg="#ffffff", fg=INK,
                               text_align="Left", vertical_align="Top",
                               border_color="#ffffff", padding="3pt", can_grow=True)
                _tb = dri[-1]
                _sub(_tb, "Top", "0in"); _sub(_tb, "Left", f"{ci*col_w:.2f}in")
                _sub(_tb, "Width", f"{col_w-0.02:.2f}in"); _sub(_tb, "Height", f"{det_h:.2f}in")
            row_specs.append(("detail", last, det_h))

    # ---- COLUMN hierarchy (single) ------------------------------------------
    ch = _sub(tablix, "TablixColumnHierarchy")
    _sub(_sub(ch, "TablixMembers"), "TablixMember")

    # ---- ROW hierarchy: nested groups mirroring the chain -------------------
    rh = _sub(tablix, "TablixRowHierarchy")
    top_members = _sub(rh, "TablixMembers")

    # Outer group on the band's break column.
    outer_mem = _sub(top_members, "TablixMember")
    outer_grp = _sub(outer_mem, "Group"); outer_grp.set("Name", "ND_G0")
    oge = _sub(outer_grp, "GroupExpressions")
    _sub(oge, "GroupExpression",
         f"=Fields!{_safe(bcol)}.Value" if (bcol and bcol.upper() in declared) else "=1")
    opb = _sub(outer_grp, "PageBreak"); _sub(opb, "BreakLocation", "Start")
    cursor = _sub(outer_mem, "TablixMembers")

    # Band header member (static, repeats on new page).
    band_mem = _sub(cursor, "TablixMember")
    _sub(band_mem, "KeepWithGroup", "After")
    _sub(band_mem, "RepeatOnNewPage", "true")

    # Outer-group card member (static) -- matches the ND_OuterCard row so the
    # outer group's extra fields render once per outer-group instance.
    # RepeatOnNewPage MUST match the adjacent band header member: SSRS
    # rejects the report at publish time when static members around a
    # dynamic member disagree ("The TablixMember must have the same value
    # set for the RepeatOnNewPage property..." — caught by rendering
    # through the real engine).
    if outer_card_items:
        oc_mem = _sub(cursor, "TablixMember")
        _sub(oc_mem, "KeepWithGroup", "After")
        _sub(oc_mem, "RepeatOnNewPage", "true")

    # Middle groups: each is a nested Group member + a static card leaf.
    # EVERY static member in this hierarchy carries RepeatOnNewPage=true:
    # SSRS rejects the report at publish time when static members around a
    # dynamic member disagree on RepeatOnNewPage, and the shapes vary
    # (with/without middles, with/without a column header) so uniformity
    # is the only value that is consistent for every permutation. It also
    # matches Oracle, which reprints group headers on overflow pages.
    for mi, grp in enumerate(middles):
        gm = _sub(cursor, "TablixMember")
        gg = _sub(gm, "Group"); gg.set("Name", f"ND_G{mi+1}")
        gge = _sub(gg, "GroupExpressions")
        bk = grp.break_col
        _sub(gge, "GroupExpression",
             f"=Fields!{_safe(bk)}.Value" if (bk and bk.upper() in declared) else "=1")
        inner = _sub(gm, "TablixMembers")
        # static card row for this group level
        card_mem = _sub(inner, "TablixMember")
        _sub(card_mem, "KeepWithGroup", "After")
        _sub(card_mem, "RepeatOnNewPage", "true")
        cursor = inner

    # Column-header member (static) -- matches the ND_ColHdr row, repeats with
    # the detail group so the navy header prints above each action table.
    if detail_has_header:
        ch_mem = _sub(cursor, "TablixMember")
        _sub(ch_mem, "KeepWithGroup", "After")
        _sub(ch_mem, "RepeatOnNewPage", "true")

    # Innermost detail member (the repeating leaf).
    if last is not None:
        det_mem = _sub(cursor, "TablixMember")
        dg = _sub(det_mem, "Group"); dg.set("Name", "ND_Detail_Grp")
        dge = _sub(dg, "GroupExpressions")
        dbk = last.break_col
        _sub(dge, "GroupExpression",
             f"=Fields!{_safe(dbk)}.Value" if (dbk and dbk.upper() in declared) else "=1")

    _sub(tablix, "DataSetName", _safe(main.name))
    _sub(tablix, "Top", "0in"); _sub(tablix, "Left", "0in")
    total_h = sum(h for _, _, h in row_specs)
    _sub(tablix, "Height", f"{total_h:.2f}in")
    _sub(tablix, "Width", "7.5in")
    _sub(tablix, "Style")
    return tablix


def _orig_name(query, upper_name):
    """Return the canonical (original-case) field name for an upper-cased key."""
    for it in (query.items or []):
        if (it.name or "").upper() == upper_name:
            return it.name
    return upper_name


def _child_join_keys(master_q, child_q):
    """Return (master_col, child_col) join keys for an Oracle <link> child
    query, or None. The child's :BIND that names a master column is the join;
    the child-side key is the child column matching that name. Structural and
    generic -- mirrors the resolver's _lookup_for_child key detection."""
    if master_q is None or child_q is None:
        return None
    master_cols = {(it.name or "").upper(): it.name for it in (master_q.items or [])}
    child_cols = {(it.name or "").upper(): it.name for it in (child_q.items or [])}
    if not master_cols or not child_cols:
        return None
    for b in re.findall(r":([A-Za-z_][A-Za-z0-9_]*)", getattr(child_q, "sql", "") or ""):
        bu = b.upper()
        if bu not in master_cols:
            continue
        dest = child_cols.get(bu)
        if not dest:
            for cu, cn in child_cols.items():
                if cu == bu or cu.endswith("_" + bu) or cu.endswith(bu):
                    dest = cn
                    break
        if dest:
            return (master_cols[bu], dest)
    return None


def _build_grouped_card_tablix(report: "ParsedReport", main: "DataQuery") -> ET.Element:
    """4-row grouped Tablix matching the Oracle Reports reference output.

    Row layout (Tablix has 1 column, 7.5in wide):

      Row 0  Band header           static, repeats on new page
      Row 1  Complaint card        renders ONCE per Complaint (group header)
      Row 2  Action sub-header bar static within Complaint group
      Row 3  Action detail row     iterates per source-row within Complaint

    Row hierarchy:

      <county group>
        <band header member>     (KeepWithGroup=After, RepeatOnNewPage=true)
        <complaint group on id>
          <complaint header member>     (KeepWithGroup=After)
          <action sub-hdr member>       (KeepWithGroup=After)
          <action detail member>        (the details placeholder)

    GENERIC: field roles picked by name patterns only -- no report-specific
    constants. CanGrow=true on every textbox so values that wrap (Action
    Comments, long addresses) expand the row instead of truncating.
    """
    items = list(main.items or [])
    if not items:
        return _build_tablix(report, main)

    group_field = items[0]
    remaining = items[1:]
    id_field = None
    body_items = []
    for it in remaining:
        if id_field is None and _is_id_field(it.name):
            id_field = it
            continue
        body_items.append(it)
    if id_field is None and body_items:
        id_field = body_items.pop(0)

    # Build a parameter-label lookup so we can RELABEL an internal-ID
    # field (e.g. ORG_ID -> "Contractor") when the report has a
    # parameter whose name maps to that field (PARM_CONTRACTOR).
    # Generic: parameter PARM_X gets its label applied to a dataset
    # field whose name semantically matches X.
    _PARM_NAME_TO_FIELD = {
        "CONTRACTOR": ("ORG_ID", "ORGANIZATION_ID"),
        "ORGANIZATION": ("ORG_ID", "ORGANIZATION_ID"),
        "OPERATOR": ("OP_ID", "OPERATOR_ID"),
        "PERMITTEE": ("PERMITTEE_ID",),
    }
    _label_overrides = {}
    for _p in (report.parameters or []):
        n = (_p.name or "").upper()
        bare = re.sub(r"^(PARM_|P_)", "", n)
        if bare in _PARM_NAME_TO_FIELD:
            override_label = _clean_label(_p.label) or _abbrev_expand(bare)
            for fld_name in _PARM_NAME_TO_FIELD[bare]:
                _label_overrides[fld_name] = override_label

    def _is_internal_id(it):
        u = (it.name or "").upper()
        if id_field is not None and u == id_field.name.upper():
            return False
        # Whitelist: fields the user expects to see (overridden labels).
        if u in _label_overrides:
            return False
        return u.endswith("_ID") or (len(u) > 4 and u.endswith("ID"))
    body_items = [it for it in body_items if not _is_internal_id(it)]
    # Apply label overrides in place by mutating the LayoutField-like
    # objects we own here (defensive: only if .label is settable).
    for _it in body_items:
        ov = _label_overrides.get((_it.name or "").upper())
        if ov:
            try:
                _it.label = ov
            except Exception:
                pass

    header_items, detail_items = _split_card_fields(body_items)
    detail_items = _sort_detail_columns(detail_items)
    # Action sub-table is meaningful only when there's a Complaint-level
    # group to attach it to. Without an id_field the Tablix has just
    # band + card rows; a stray detail_items list would emit phantom
    # rows that don't match any hierarchy member.
    if id_field is None:
        detail_items = []
    _pre_paired_rows = _pair_card_header_rows(header_items)

    # Resolve palette from the report's own <visualSettings> (parser
    # populated LayoutGroup.background_color / foreground_color etc.).
    # Falls back to the original navy / yellow defaults when the source
    # XML carries no color signals -- so existing behaviour is preserved
    # for pure black-on-white reports.
    palette     = _resolve_palette(report)
    BAND_BG     = palette["band_bg"]
    BAND_FG     = palette["band_fg"]
    SUBHDR_BG   = palette["subhdr_bg"]
    SUBHDR_FG   = palette["subhdr_fg"]
    # Card body stays white per reference; the sub-header strip alone
    # carries the SUBHDR_BG color.
    CARD_BG     = palette["card_bg"]
    INK         = palette["ink"]
    INK_SOFT    = palette["ink_soft"]
    RULE        = palette["rule"]

    pair_rows = max(1, len(_pre_paired_rows))
    sub_h     = 0.30
    pair_h    = 0.26
    # Linked detail query (a separate Oracle <link> dataset): SSRS can't bind a
    # second dataset into this Tablix, so surface its columns via LookupSet --
    # all child rows, newline-aligned per column -- inside the card, otherwise
    # the detail is dropped. Only when there's no in-dataset action sub-table.
    _linked_detail_q = _pick_detail_query(report, main.name)
    _linked_keys = (_child_join_keys(main, _linked_detail_q)
                    if _linked_detail_q is not None else None)
    _linked_cols = []
    if _linked_detail_q is not None and _linked_keys and not detail_items:
        _ck = _linked_keys[1]
        _linked_cols = [it for it in (_linked_detail_q.items or [])
                        if it.name and it.name.upper() != _ck.upper()]
    linked_h  = 0.76 if _linked_cols else 0.0
    card_h    = sub_h + pair_rows * pair_h + 0.20 + linked_h
    band_h    = 0.34
    act_hdr_h = 0.28
    act_det_h = 0.30  # baseline; CanGrow expands it as needed

    tablix = ET.Element(_q("Tablix"))
    tablix.set("Name", "Tablix_Cards")
    tbody = _sub(tablix, "TablixBody")

    cols = _sub(tbody, "TablixColumns")
    _sub(_sub(cols, "TablixColumn"), "Width", "7.5in")

    rows = _sub(tbody, "TablixRows")

    # ---- ROW 0: BAND ------------------------------------------------------
    band_row = _sub(rows, "TablixRow")
    _sub(band_row, "Height", f"{band_h:.2f}in")
    band_cells = _sub(band_row, "TablixCells")
    band_cell = _sub(band_cells, "TablixCell")
    band_contents = _sub(band_cell, "CellContents")
    band_rect = _sub(band_contents, "Rectangle")
    band_rect.set("Name", "Band_Group")
    _sub(band_rect, "KeepTogether", "true")
    bs = _sub(band_rect, "Style")
    _sub(bs, "BackgroundColor", BAND_BG)
    band_ri = _sub(band_rect, "ReportItems")
    grp_label = _clean_label(group_field.label) or _abbrev_expand(group_field.name)
    _build_textbox(
        band_ri, "Tb_Band_Left",
        f'="{grp_label}: " & Fields!{_safe(group_field.name)}.Value',
        bold=True, font_size="12pt", bg=BAND_BG, fg=BAND_FG,
        text_align="Left", vertical_align="Middle",
        border_color=BAND_BG, padding="6pt", can_grow=False,
    )
    ltb = band_ri[-1]
    _sub(ltb, "Top", "0in"); _sub(ltb, "Left", "0in")
    _sub(ltb, "Width", "4.5in"); _sub(ltb, "Height", f"{band_h:.2f}in")
    # F5: render the group's DECLARED <summary> totals if the report has any --
    # the card path previously ignored them and fabricated a record count.
    # GENERIC fallback when there are no declared summaries: count DISTINCT
    # records by the detected id_field (not CountRows(), which double-counts
    # when the SQL joins parent rows to a child action/history table), else
    # CountRows().
    _card_declared = {(it.name or "").upper() for it in (main.items or [])}
    _grp_summaries = (main.groups[0].summaries
                      if getattr(main, "groups", None) else None)
    total_expr = _summary_total_expr(_grp_summaries, main, _card_declared)
    if not total_expr:
        if id_field is not None:
            total_expr = (
                f'="Total For {grp_label}: " & '
                f'CountDistinct(Fields!{_safe(id_field.name)}.Value)'
            )
        else:
            total_expr = f'="Total For {grp_label}: " & CountRows()'
    _build_textbox(
        band_ri, "Tb_Band_Right",
        total_expr,
        bold=True, font_size="12pt", bg=BAND_BG, fg=BAND_FG,
        text_align="Right", vertical_align="Middle",
        border_color=BAND_BG, padding="6pt", can_grow=False,
    )
    rtb = band_ri[-1]
    _sub(rtb, "Top", "0in"); _sub(rtb, "Left", "4.5in")
    _sub(rtb, "Width", "3.0in"); _sub(rtb, "Height", f"{band_h:.2f}in")

    # ---- ROW 1: COMPLAINT CARD (renders ONCE per Complaint) ---------------
    card_row = _sub(rows, "TablixRow")
    _sub(card_row, "Height", f"{card_h:.2f}in")
    card_cells = _sub(card_row, "TablixCells")
    card_cell = _sub(card_cells, "TablixCell")
    card_contents = _sub(card_cell, "CellContents")
    card_rect = _sub(card_contents, "Rectangle")
    card_rect.set("Name", "Card_Header")
    cs = _sub(card_rect, "Style")
    # Card header block (SubHdr + every label/value row down through
    # the last header field, e.g. Contractor) shares ONE light-grey
    # background, matching the Oracle Reports reference. Inner
    # textboxes get the same bg so they blend into a single
    # continuous grey panel.
    _sub(cs, "BackgroundColor", SUBHDR_BG)
    cb = _sub(cs, "Border"); _sub(cb, "Style", "Solid")
    _sub(cb, "Color", RULE); _sub(cb, "Width", "0.5pt")
    card_ri = _sub(card_rect, "ReportItems")

    if id_field is not None:
        id_label = _clean_label(id_field.label) or _abbrev_expand(id_field.name)
        _build_textbox(
            card_ri, "Tb_SubHdr",
            f'="{id_label}: " & Fields!{_safe(id_field.name)}.Value',
            bold=True, font_size="11pt", bg=SUBHDR_BG, fg=SUBHDR_FG,
            text_align="Left", vertical_align="Middle",
            border_color=SUBHDR_BG, padding="4pt", can_grow=False,
        )
        sub_tb = card_ri[-1]
        _sub(sub_tb, "Top", "0in"); _sub(sub_tb, "Left", "0in")
        _sub(sub_tb, "Width", "7.5in"); _sub(sub_tb, "Height", f"{sub_h:.2f}in")

    col_x = [0.0, 3.75]
    label_w = 1.20
    value_w = 2.55

    # GENERIC: pair header fields semantically (e.g. Owner|Status,
    # Location|City, Received|Bust) rather than left-then-right in raw
    # source-XML order, which puts secondary-status fields on the wrong
    # side of the card.
    paired_rows = _pre_paired_rows
    for row_idx, (left_field, right_field) in enumerate(paired_rows):
        y = sub_h + row_idx * pair_h + 0.02
        for col, field in enumerate((left_field, right_field)):
            if field is None:
                continue
            base_left = col_x[col]
            lbl_text = (_clean_label(field.label) or _abbrev_expand(field.name)) + ":"
            # Generic accent: primary-entity fields (OWNER / PERMITTEE /
            # APPLICANT / OPERATOR) get the blue accent so they pop
            # below the Complaint ID sub-header, matching the reference.
            u_name = (field.name or "").upper()
            u_label = (field.label or "").upper()
            is_primary_entity = any(
                tok in u_name or tok in u_label
                for tok in ("OWNER", "PERMITTEE", "APPLICANT", "OPERATOR")
            )
            lbl_fg = SUBHDR_FG if is_primary_entity else INK_SOFT
            _build_textbox(
                card_ri, f"Tb_Lbl_{_safe(field.name)}", lbl_text,
                bold=True, font_size="9pt", bg=SUBHDR_BG, fg=lbl_fg,
                text_align="Left", vertical_align="Top",
                border_color=SUBHDR_BG, padding="2pt", can_grow=True,
            )
            lbl_tb = card_ri[-1]
            _sub(lbl_tb, "Top", f"{y:.2f}in")
            _sub(lbl_tb, "Left", f"{base_left:.2f}in")
            _sub(lbl_tb, "Width", f"{label_w:.2f}in")
            _sub(lbl_tb, "Height", f"{pair_h - 0.04:.2f}in")
            _build_textbox(
                card_ri, f"Tb_Val_{_safe(field.name)}",
                f"=First(Fields!{_safe(field.name)}.Value)",
                font_size="9pt", bg=SUBHDR_BG, fg=INK,
                text_align="Left", vertical_align="Top",
                border_color=SUBHDR_BG, padding="2pt", can_grow=True,
            )
            val_tb = card_ri[-1]
            _sub(val_tb, "Top", f"{y:.2f}in")
            _sub(val_tb, "Left", f"{base_left + label_w:.2f}in")
            _sub(val_tb, "Width", f"{value_w:.2f}in")
            _sub(val_tb, "Height", f"{pair_h - 0.04:.2f}in")

    # ---- Linked detail: LookupSet over the separate <link> child dataset ----
    # Each detail column is one CanGrow textbox holding "Label:\n<all child
    # values joined by newlines>", so the columns sit side-by-side and their
    # rows line up. This is the SSRS-native way to show a second dataset's rows
    # inline (a Tablix binds only one dataset). For a true scrollable detail
    # GRID, join the queries or use a subreport at deploy time.
    if _linked_cols:
        _mk, _ck = _linked_keys
        det_label = re.sub(r"^Q[_ ]?", "",
                           (getattr(_linked_detail_q, "name", "") or "Detail"))
        det_label = det_label.replace("_", " ").title() or "Detail"
        dy = sub_h + pair_rows * pair_h + 0.06
        _build_textbox(
            card_ri, "Tb_LDetHdr", f'="{det_label} (linked):"',
            bold=True, font_size="9pt", bg=SUBHDR_BG, fg=SUBHDR_FG,
            text_align="Left", vertical_align="Middle",
            border_color=SUBHDR_BG, padding="2pt", can_grow=False,
        )
        _h = card_ri[-1]
        _sub(_h, "Top", f"{dy:.2f}in"); _sub(_h, "Left", "0in")
        _sub(_h, "Width", "7.5in"); _sub(_h, "Height", "0.22in")
        dy += 0.24
        ncol = max(1, len(_linked_cols))
        cw = 7.5 / ncol
        for ci, col in enumerate(_linked_cols):
            clbl = _clean_label(col.label) or _abbrev_expand(col.name)
            vals = (f'Join(LookupSet(Fields!{_safe(_mk)}.Value, '
                    f'Fields!{_safe(_ck)}.Value, CStr(Fields!{_safe(col.name)}.Value), '
                    f'"{_safe(_linked_detail_q.name)}"), vbCrLf)')
            _build_textbox(
                card_ri, f"Tb_LDet_{_safe(col.name)}",
                f'="{clbl}:" & vbCrLf & {vals}',
                font_size="9pt", bg=SUBHDR_BG, fg=INK,
                text_align="Left", vertical_align="Top",
                border_color=SUBHDR_BG, padding="2pt", can_grow=True,
            )
            _t = card_ri[-1]
            _sub(_t, "Top", f"{dy:.2f}in"); _sub(_t, "Left", f"{ci * cw:.2f}in")
            _sub(_t, "Width", f"{cw - 0.04:.2f}in"); _sub(_t, "Height", "0.40in")

    # ---- ROW 2 + ROW 3: ACTION SUB-TABLE (only if detail_items exist) ----
    if detail_items:
        n = len(detail_items)
        col_w = 7.5 / n

        act_hdr_row = _sub(rows, "TablixRow")
        _sub(act_hdr_row, "Height", f"{act_hdr_h:.2f}in")
        ah_cells = _sub(act_hdr_row, "TablixCells")
        ah_cell = _sub(ah_cells, "TablixCell")
        ah_contents = _sub(ah_cell, "CellContents")
        ah_rect = _sub(ah_contents, "Rectangle")
        ah_rect.set("Name", "Act_Header")
        # Action sub-header bar = WHITE. Side borders (Left + Right)
        # tie this row visually to the card header above and the
        # detail row below, so the action sub-table reads as part of
        # the same card instead of floating loose.
        ahs = _sub(ah_rect, "Style"); _sub(ahs, "BackgroundColor", "#ffffff")
        _ahb_l = _sub(ahs, "LeftBorder")
        _sub(_ahb_l, "Style", "Solid"); _sub(_ahb_l, "Color", RULE)
        _sub(_ahb_l, "Width", "0.75pt")
        _ahb_r = _sub(ahs, "RightBorder")
        _sub(_ahb_r, "Style", "Solid"); _sub(_ahb_r, "Color", RULE)
        _sub(_ahb_r, "Width", "0.75pt")
        _ahb_t = _sub(ahs, "TopBorder"); _sub(_ahb_t, "Style", "None")
        _ahb_b = _sub(ahs, "BottomBorder"); _sub(_ahb_b, "Style", "None")
        ah_ri = _sub(ah_rect, "ReportItems")
        for col_idx, field in enumerate(detail_items):
            head_x = col_idx * col_w
            lbl = (_clean_label(field.label) or _abbrev_expand(field.name)) + ":"
            _build_textbox(
                ah_ri, f"Tb_AHdr_{_safe(field.name)}", lbl,
                bold=True, font_size="9pt", bg="#ffffff", fg=SUBHDR_FG,
                text_align="Left", vertical_align="Middle",
                border_color="#ffffff", padding="3pt", can_grow=False,
            )
            tb = ah_ri[-1]
            _sub(tb, "Top", "0in")
            _sub(tb, "Left", f"{head_x:.2f}in")
            _sub(tb, "Width", f"{col_w - 0.02:.2f}in")
            _sub(tb, "Height", f"{act_hdr_h:.2f}in")

        act_det_row = _sub(rows, "TablixRow")
        _sub(act_det_row, "Height", f"{act_det_h:.2f}in")
        ad_cells = _sub(act_det_row, "TablixCells")
        ad_cell = _sub(ad_cells, "TablixCell")
        ad_contents = _sub(ad_cell, "CellContents")
        ad_rect = _sub(ad_contents, "Rectangle")
        ad_rect.set("Name", "Act_Detail")
        # Action detail rows stay WHITE -- only the Complaint header
        # block uses the gray SUBHDR_BG. Frame with side borders +
        # bottom rule so the detail row reads as part of the card.
        ads = _sub(ad_rect, "Style"); _sub(ads, "BackgroundColor", "#ffffff")
        _adb_l = _sub(ads, "LeftBorder")
        _sub(_adb_l, "Style", "Solid"); _sub(_adb_l, "Color", RULE)
        _sub(_adb_l, "Width", "0.75pt")
        _adb_r = _sub(ads, "RightBorder")
        _sub(_adb_r, "Style", "Solid"); _sub(_adb_r, "Color", RULE)
        _sub(_adb_r, "Width", "0.75pt")
        _adb_t = _sub(ads, "TopBorder"); _sub(_adb_t, "Style", "None")
        _adb_b = _sub(ads, "BottomBorder")
        _sub(_adb_b, "Style", "Solid"); _sub(_adb_b, "Color", RULE)
        _sub(_adb_b, "Width", "0.5pt")
        ad_ri = _sub(ad_rect, "ReportItems")
        for col_idx, field in enumerate(detail_items):
            head_x = col_idx * col_w
            _build_textbox(
                ad_ri, f"Tb_ADet_{_safe(field.name)}",
                f"=Fields!{_safe(field.name)}.Value",
                font_size="9pt", bg="#ffffff", fg=INK,
                text_align="Left", vertical_align="Top",
                border_color="#ffffff", padding="3pt", can_grow=True,
            )
            tb = ad_ri[-1]
            _sub(tb, "Top", "0in")
            _sub(tb, "Left", f"{head_x:.2f}in")
            _sub(tb, "Width", f"{col_w - 0.02:.2f}in")
            _sub(tb, "Height", f"{act_det_h:.2f}in")

    # ---- HIERARCHY --------------------------------------------------------
    col_hier = _sub(tablix, "TablixColumnHierarchy")
    col_members = _sub(col_hier, "TablixMembers")
    _sub(col_members, "TablixMember")

    row_hier = _sub(tablix, "TablixRowHierarchy")
    row_members = _sub(row_hier, "TablixMembers")

    # County group
    county_mem = _sub(row_members, "TablixMember")
    county_grp = _sub(county_mem, "Group")
    county_grp.set("Name", "GroupByFirst")
    county_exprs = _sub(county_grp, "GroupExpressions")
    _sub(county_exprs, "GroupExpression",
         f"=Fields!{_safe(group_field.name)}.Value")
    grp_pb = _sub(county_grp, "PageBreak")
    _sub(grp_pb, "BreakLocation", "Start")
    county_inner = _sub(county_mem, "TablixMembers")

    # Band header (Row 0)
    band_member = _sub(county_inner, "TablixMember")
    _sub(band_member, "KeepWithGroup", "After")
    _sub(band_member, "RepeatOnNewPage", "true")

    if id_field is not None:
        # Complaint group
        comp_mem = _sub(county_inner, "TablixMember")
        comp_grp = _sub(comp_mem, "Group")
        comp_grp.set("Name", "GroupByID")
        comp_exprs = _sub(comp_grp, "GroupExpressions")
        _sub(comp_exprs, "GroupExpression",
             f"=Fields!{_safe(id_field.name)}.Value")
        comp_inner = _sub(comp_mem, "TablixMembers")

        if detail_items:
            # 3 inner leaves matching rows 1, 2, 3:
            #   card (static) + act_hdr (static) + act_det (Group)
            card_member = _sub(comp_inner, "TablixMember")
            _sub(card_member, "KeepWithGroup", "After")
            act_hdr_member = _sub(comp_inner, "TablixMember")
            _sub(act_hdr_member, "KeepWithGroup", "After")
            act_det_member = _sub(comp_inner, "TablixMember")
            _sub(act_det_member, "Group").set("Name", "Detail_Action")
        else:
            # No action sub-table -> only Row 1 (the card) exists.
            # ONE inner leaf: the card member becomes the Complaint
            # group's iteration anchor (Group/ details placeholder).
            # MUST match TablixRows count = 2 (band + card).
            card_member = _sub(comp_inner, "TablixMember")
            _sub(card_member, "Group").set("Name", "Detail_Card")
    else:
        # No id_field: TablixRows = band + card (= 2). Hierarchy =
        # band_member + det_member (= 2). MUST stay aligned even if
        # detail_items somehow leaked in (which shouldn't happen, but
        # guard for it anyway by trimming the extra rows).
        det_member = _sub(county_inner, "TablixMember")
        _sub(det_member, "Group").set("Name", "Detail_Card")

    _sub(tablix, "DataSetName", _safe(main.name))
    _sub(tablix, "Top", "0in")
    _sub(tablix, "Left", "0in")
    # Tablix Height = sum of row heights (band + card + optionally action rows).
    tablix_h = band_h + card_h + (act_hdr_h + act_det_h if detail_items else 0.0)
    _sub(tablix, "Height", f"{tablix_h:.2f}in")
    _sub(tablix, "Width", "7.5in")
    tx_style = _sub(tablix, "Style")
    _sub(tx_style, "PaddingTop", "2pt")
    return tablix


def _extract_title_lines(report, limit: int = 3):
    """Pull the centered title lines from the top of the report layout.
    Generic -- walks every layout group's text fields whose y-position
    is in the upper region of the page, sorts by y then x, returns the
    first `limit` non-noise strings."""
    lines = []
    seen = set()

    def _iter(group):
        yield group
        for ch in (group.children or []):
            yield from _iter(ch)

    candidates = []
    for top in (report.layout or []):
        for g in _iter(top):
            for f in (g.fields or []):
                if getattr(f, "kind", "") != "text":
                    continue
                text = (getattr(f, "text", "") or "").strip()
                if not text:
                    continue
                for ln in text.splitlines():
                    s = ln.strip()
                    if not s or s in seen:
                        continue
                    y = getattr(f, "y", 0) or 0
                    x = getattr(f, "x", 0) or 0
                    if y > 2.75:
                        continue
                    candidates.append((y, x, s))
                    seen.add(s)
    candidates.sort()
    for _, _, s in candidates:
        if s.endswith(":") or s.startswith("&") or s.startswith(":"):
            continue
        if len(s) > 120:
            continue
        up = s.upper()
        if up.startswith(("ERROR", "WARNING", "NOTE:")):
            continue
        if "&" in s and any(c.isupper() for c in s):
            continue
        lines.append(s)
        if len(lines) >= limit:
            break
    return lines


def _is_header_summary_report(report) -> bool:
    """True when the report's <section name="header"> carries a full summary
    table (per-category repeating frames + many stat-row labels + bound
    summary fields) -- the shape of an accounting/status summary report whose
    real content lives in the header, not the body. Purely structural: never
    keyed on a report name. Normal page headers (title + date + page#) and
    letter criteria covers have no repeating frames, so they never match.
    """
    hdr = _section_by_kind(report, "section_header")
    if hdr is None:
        return False
    rep_frames = text_labels = summ_fields = 0
    stack = [hdr]
    while stack:
        g = stack.pop()
        if "repeating" in (getattr(g, "kind", "") or "").lower():
            rep_frames += 1
        for f in (getattr(g, "fields", None) or []):
            fk = (getattr(f, "kind", "") or "").lower()
            if fk == "text" and (getattr(f, "text", "") or "").strip():
                text_labels += 1
            elif fk == "field" and (getattr(f, "source", "") or "").upper().startswith("CS_"):
                summ_fields += 1
        stack.extend(getattr(g, "children", None) or [])
    # Genuine accounting/status summary: MULTIPLE per-category repeating frames
    # AND header fields bound to report-level column-summary aggregates (CS_*),
    # plus many stat-row labels. A letter's criteria cover has none of these.
    return rep_frames >= 2 and summ_fields >= 1 and text_labels >= 4


def _build_summary_header_cover(report) -> Optional[ET.Element]:
    """Geometry-driven leading page for a header-resident summary report:
    render <section name="header"> (the criteria cover + the whole stat
    table) as a Rectangle, reusing the same frame/field machinery the
    per-record body uses. Returns None when the report isn't a summary-header
    report, so the caller falls through to the generic cover. No per-report
    logic -- gated entirely by the structural detector above."""
    if not _is_header_summary_report(report):
        return None
    hdr = _section_by_kind(report, "section_header")
    if hdr is None:
        return None
    rect = ET.Element(_q("Rectangle"))
    rect.set("Name", "Rect_SummaryHeader")
    _sub(rect, "KeepTogether", "true")
    rect_items = _sub(rect, "ReportItems")
    counter = [0]
    cover_titles = set()
    max_by = 0.30
    frame_children = [
        c for c in (hdr.children or [])
        if "frame" in (getattr(c, "kind", "") or "").lower()
    ]
    if frame_children:
        for child in frame_children:
            by = _emit_frame_rect(
                rect_items, child, 0.0, 0.0, 7.3, report, cover_titles,
                "SummHdr", counter,
            )
            if by is not None:
                max_by = max(max_by, by)
    else:
        for lf in (hdr.fields or []):
            counter[0] += 1
            ok, by = _emit_field_textbox(
                rect_items, f"SummHdr_Tb_{counter[0]}", "", lf,
                0.0, 0.0, 7.3, 9.0, report, cover_titles,
            )
            if ok:
                max_by = max(max_by, by)
    if len(rect_items) == 0:
        return None
    # Field refs in this rectangle are body-direct (outside any data region),
    # so SSRS requires every Fields!X.Value wrapped in a scoped aggregate.
    for _v in rect.iter(_q("Value")):
        _t = _v.text or ""
        if "Fields!" in _t:
            _v.text = _wrap_unscoped_aggregates(_t, report, in_tablix_scope=False)
    rect_h = max(1.0, max_by + 0.30)
    _sub(rect, "Top", "0in")
    _sub(rect, "Left", "0.1in")
    _sub(rect, "Width", "7.3in")
    _sub(rect, "Height", f"{rect_h:.2f}in")
    style = _sub(rect, "Style")
    _sub(_sub(style, "Border"), "Style", "None")
    _sub(_sub(rect, "PageBreak"), "BreakLocation", "End")
    rect.set("data-rect-height-in", f"{rect_h:.2f}")
    return rect


def _build_cover_page(report) -> Optional[ET.Element]:
    """Cover page ("Report Parameters") -- renders on page 1 only.
    Generic: pulls title from layout via _extract_title_lines, params
    from report.parameters (filtered to exclude internal IDs)."""
    # A header-resident summary/accounting report carries its real cover AND
    # its summary table inside <section name="header"> -- render that layout
    # geometry-driven instead of a generic "Report Parameters" list.
    _summ = _build_summary_header_cover(report)
    if _summ is not None:
        return _summ
    title_lines = _extract_title_lines(report, limit=3)
    title_lines = [
        ln for ln in title_lines
        if not re.fullmatch(r"(report\s+)?parameters?", ln.strip(), re.IGNORECASE)
    ]
    raw_params = [p for p in (report.parameters or [])
                  if getattr(p, "display", True)]
    def _is_visible_param(p):
        u = (p.name or "").upper()
        if u.endswith("_ID") or (len(u) > 4 and u.endswith("ID")):
            return False
        if re.search(r"USER$", u):
            return False
        return True
    params = [p for p in raw_params if _is_visible_param(p)]
    if not title_lines and not params:
        return None

    BORDER = "#777777"
    TITLE_FG = "#03047e"
    INK = "#282828"

    rect = ET.Element(_q("Rectangle"))
    rect.set("Name", "Rect_CoverPage")
    _sub(rect, "KeepTogether", "true")
    style = _sub(rect, "Style")
    border = _sub(style, "Border")
    _sub(border, "Style", "Solid")
    _sub(border, "Color", BORDER)
    _sub(border, "Width", "1pt")
    _sub(style, "BackgroundColor", "#ffffff")

    ri = _sub(rect, "ReportItems")
    y = 0.30
    if title_lines:
        title_expr = (
            '="' + '" & vbCrLf & "'.join(
                ln.replace('"', '""') for ln in title_lines
            ) + '"'
        )
        title_h = 0.30 * len(title_lines)
        _build_textbox(
            ri, "Cov_Title", title_expr,
            bold=True, font_size="13pt", fg=TITLE_FG,
            text_align="Center", vertical_align="Middle",
            border_color="#ffffff", padding="2pt",
            font_family="Courier New",
        )
        tb = ri[-1]
        _sub(tb, "Top", f"{y:.2f}in")
        _sub(tb, "Left", "0.15in")
        _sub(tb, "Width", "6.50in")
        _sub(tb, "Height", f"{title_h:.2f}in")
        y += title_h + 0.30

    user_param = next(
        (p.name for p in (report.parameters or [])
         if re.search(r"USER$", (p.name or ""), re.IGNORECASE)),
        None,
    )
    if user_param:
        run_by_expr = (
            f"=IIf(IsNothing(Parameters!{_safe(user_param)}.Value) Or "
            f"Len(CStr(Parameters!{_safe(user_param)}.Value)) = 0, "
            f"User!UserID, Parameters!{_safe(user_param)}.Value)"
        )
    else:
        run_by_expr = "=User!UserID"
    # Detect the "record ID" field generically so the Total counts DISTINCT
    # records, not the row count of a join. Walk every dataset's items and
    # pick the first field that looks like an ID. Falls back to CountRows()
    # only if no ID-shaped column exists anywhere in the report.
    # Aggregates living in Body (outside any data region) MUST carry an
    # explicit dataset scope when the report has multiple datasets,
    # otherwise SSRS rejects upload with:
    #   "<Cov_MetaVal_2> uses an aggregate expression without a scope.
    #    A scope is required for all aggregates used outside of a data
    #    region unless the report contains exactly one dataset."
    # Detect (a) the id-shaped field, (b) the dataset it belongs to.
    cover_id_field = None
    cover_id_dataset = None
    for q in (report.queries or []):
        for it in (q.items or []):
            if _is_id_field(it.name):
                cover_id_field = it.name
                cover_id_dataset = q.name
                break
        if cover_id_field:
            break
    if cover_id_field and cover_id_dataset:
        total_expr = (
            f"=CountDistinct(Fields!{_safe(cover_id_field)}.Value, "
            f'"{_safe(cover_id_dataset)}")'
        )
    elif cover_id_field:
        total_expr = f"=CountDistinct(Fields!{_safe(cover_id_field)}.Value)"
    else:
        # Fallback: pick the first dataset and CountRows it (also scoped).
        first_ds = next(
            (q.name for q in (report.queries or [])), None
        )
        if first_ds:
            total_expr = f'=CountRows("{_safe(first_ds)}")'
        else:
            total_expr = "=CountRows()"
    meta_lines = [
        ("Run Date:", '=Format(Globals!ExecutionTime, "MM/dd/yyyy HH:mm:ss")'),
        ("Run By:", run_by_expr),
        ("Total of ALL Records:", total_expr),
    ]
    for idx, (lbl, val) in enumerate(meta_lines):
        _build_textbox(
            ri, f"Cov_MetaLbl_{idx}", lbl,
            bold=True, font_size="10pt", fg=INK,
            text_align="Right", vertical_align="Middle",
            border_color="#ffffff", padding="2pt",
        )
        ltb = ri[-1]
        _sub(ltb, "Top", f"{y:.2f}in"); _sub(ltb, "Left", "1.8in")
        _sub(ltb, "Width", "2.0in"); _sub(ltb, "Height", "0.24in")
        _build_textbox(
            ri, f"Cov_MetaVal_{idx}", val,
            bold=True, font_size="10pt", fg=INK,
            text_align="Left", vertical_align="Middle",
            border_color="#ffffff", padding="2pt",
        )
        vtb = ri[-1]
        _sub(vtb, "Top", f"{y:.2f}in"); _sub(vtb, "Left", "3.9in")
        _sub(vtb, "Width", "2.7in"); _sub(vtb, "Height", "0.24in")
        y += 0.28

    y += 0.30
    _build_textbox(
        ri, "Cov_ParamsHdr", "Report Parameters",
        bold=True, font_size="12pt", fg=INK,
        text_align="Center", vertical_align="Middle",
        border_color="#ffffff", padding="2pt",
    )
    hdr_tb = ri[-1]
    _sub(hdr_tb, "Top", f"{y:.2f}in"); _sub(hdr_tb, "Left", "0.15in")
    _sub(hdr_tb, "Width", "6.50in"); _sub(hdr_tb, "Height", "0.30in")
    y += 0.40

    for idx, p in enumerate(params):
        lbl_text = (_clean_label(p.label) or _abbrev_expand(
            p.name.replace("PARM_", "").replace("P_", ""))) + ":"
        _build_textbox(
            ri, f"Cov_ParmLbl_{idx}", lbl_text,
            bold=True, font_size="10pt", fg=INK,
            text_align="Right", vertical_align="Middle",
            border_color="#ffffff", padding="2pt",
        )
        ltb = ri[-1]
        _sub(ltb, "Top", f"{y:.2f}in"); _sub(ltb, "Left", "1.5in")
        _sub(ltb, "Width", "2.5in"); _sub(ltb, "Height", "0.22in")
        _build_textbox(
            ri, f"Cov_ParmVal_{idx}",
            f"=Parameters!{_safe(p.name)}.Value",
            font_size="10pt", fg=INK,
            text_align="Left", vertical_align="Middle",
            border_color="#ffffff", padding="2pt",
        )
        vtb = ri[-1]
        _sub(vtb, "Top", f"{y:.2f}in"); _sub(vtb, "Left", "4.1in")
        _sub(vtb, "Width", "2.5in"); _sub(vtb, "Height", "0.22in")
        y += 0.26

    rect_h = y + 0.40
    _sub(rect, "Top", "0in"); _sub(rect, "Left", "0.35in")
    _sub(rect, "Width", "6.8in"); _sub(rect, "Height", f"{rect_h:.2f}in")
    rect.set("data-rect-height-in", f"{rect_h:.4f}")
    return rect


def _build_letter_cover_page(report) -> Optional[ET.Element]:
    """Layout-driven cover for letter / certificate reports.

    Walks <section name="header"> positional fields and renders them
    as a 2-column label/value form, mirroring what html_mockup's
    parameter-form preview shows. This is the cover for reports
    whose XML carries a custom Parameter Form with its OWN labels
    (e.g. "Selection Criteria:", "*Generate Envelopes:", etc.) --
    the generic `_build_cover_page` emits a "Report Parameters"
    list of declared parameters, which is the WRONG cover for
    these reports.

    Generic - no per-report logic:
      * Picks the section_header (or first section_header-shaped
        group) via _section_by_kind.
      * Pairs each label (kind=text, lower x) with the next field-
        kind item at roughly the same y (higher x).
      * Keeps starred label rows (*Sort Order, *Generate Envelopes) and
        hyperlink-description rows — the Oracle cover shows these in
        production (verified against Oracle frontend screenshots).
      * Skips rows with empty bound source.
      * Returns None when section_header has no form-shaped content,
        so the caller can fall back to the generic cover.
    """
    header_section = _section_by_kind(report, "section_header")
    if header_section is None:
        return None

    fields = _layout_fields_in_order_from_section(header_section)
    if not fields:
        return None

    # Bucket by y row (within 0.10 inch -> same row), preserve y/x sort.
    rows = []
    current_y = None
    bucket = []
    for entry in fields:
        y = entry[0]
        if current_y is None or abs(y - current_y) > 0.10:
            if bucket:
                rows.append(bucket)
            bucket = [entry]
            current_y = y
        else:
            bucket.append(entry)
    if bucket:
        rows.append(bucket)

    # Build (label, value-expr) pairs from each row.
    # Some rows are continuation text (e.g. a suggestion note at x≈2.25
    # with no label on the left) — append them to the previous row's value
    # rather than create a broken label/value pair.
    pairs = []
    # Estimate label vs value x boundary: labels are at x<1.5, values at x>=2.
    _VAL_X_THRESH = 1.5
    for row in rows:
        # Skip pure-decoration rows (no text & no field).
        texts = [(x, f) for (_y, x, _d, f) in row
                 if (f.kind or "field") == "text"
                 and (f.text or "").strip()]
        fld_items = [(x, f) for (_y, x, _d, f) in row
                     if (f.kind or "field") == "field"
                     and (f.source or "").strip()]
        if not texts and not fld_items:
            continue

        # Label: leftmost text AT the label x position (x < threshold).
        # Value-source: leftmost field; if no field, rightmost text.
        left_texts = [(x, f) for x, f in texts if x < _VAL_X_THRESH]
        right_texts = [(x, f) for x, f in texts if x >= _VAL_X_THRESH]
        label_field = sorted(left_texts, key=lambda t: t[0])[0][1] if left_texts else None
        value_field = sorted(fld_items, key=lambda t: t[0])[0][1] if fld_items else None

        # Continuation-text row: text only, positioned in the VALUE column
        # (x >= threshold), no label text on the left. Append to the
        # previous pair's value as a line break. e.g. the "*The sort order
        # of envelopes..." note sits below *Generate Envelopes at x=2.25.
        if label_field is None and value_field is None and right_texts:
            cont_text = sorted(right_texts, key=lambda t: t[0])[0][1]
            ct = (cont_text.text or "").strip().replace('"', '""')
            if pairs and ct:
                prev_lbl, prev_val, prev_vf = pairs[-1]
                # Append as a continuation line in the value expression
                if prev_val.startswith('="'):
                    # Static text — chain with &
                    pairs[-1] = (prev_lbl, prev_val[:-1] + ' ' + ct + '"',
                                 prev_vf)
                else:
                    # Field expression — can't easily append; emit as
                    # separate note row with empty label
                    pairs.append(("", f'="{ct}"', None))
            elif ct:
                pairs.append(("", f'="{ct}"', None))
            continue

        # Standalone field with no label (e.g. the URL line below
        # "Hyperlinks" headings). Emit with an empty label; the FIELD
        # OBJECT rides along so a sub-report URL becomes clickable below.
        if label_field is None and value_field is not None:
            value_expr = _field_value_for(
                value_field, report,
                dataset_name=(report.queries[0].name if report.queries else ""),
            )
            pairs.append(("", value_expr or "=Nothing", value_field))
            continue

        # Normal label + value pair.
        label_txt = (label_field.text or "") if label_field is not None else ""

        if value_field is not None:
            value_expr = _field_value_for(
                value_field, report,
                dataset_name=(report.queries[0].name if report.queries else ""),
            )
            if not value_expr:
                value_expr = "=Nothing"
        elif texts and len(texts) > 1:
            # A row of multiple text fields. Pick the rightmost text
            # as the "value" so a "Label: Static-text" pair renders.
            right = sorted(texts, key=lambda t: t[0])[-1][1]
            if right is not label_field:
                _t = (right.text or "").replace('"', '""')
                value_expr = f'="{_t}"'
            else:
                continue
        else:
            continue

        pairs.append((label_txt, value_expr, value_field))

    if not pairs:
        return None

    # Title text via _extract_title_lines (re-used from generic cover).
    title_lines = _extract_title_lines(report, limit=3)
    title_lines = [
        ln for ln in title_lines
        if not re.fullmatch(r"(report\s+)?parameters?", ln.strip(), re.IGNORECASE)
    ]

    BORDER = "#777777"
    TITLE_FG = "#03047e"
    INK = "#282828"

    rect = ET.Element(_q("Rectangle"))
    rect.set("Name", "Rect_CoverPage")
    _sub(rect, "KeepTogether", "true")
    style = _sub(rect, "Style")
    border = _sub(style, "Border")
    _sub(border, "Style", "Solid"); _sub(border, "Color", BORDER)
    _sub(border, "Width", "1pt")
    _sub(style, "BackgroundColor", "#ffffff")

    ri = _sub(rect, "ReportItems")

    y = 0.30
    if title_lines:
        title_expr = (
            '="' + '" & vbCrLf & "'.join(
                ln.replace('"', '""') for ln in title_lines
            ) + '"'
        )
        title_h = 0.34 * len(title_lines)
        _build_textbox(
            ri, "Cov_Title", title_expr,
            bold=True, font_size="14pt", fg=TITLE_FG,
            text_align="Center", vertical_align="Middle",
            border_color="#ffffff", padding="2pt",
            font_family="Courier New",
        )
        tb = ri[-1]
        _sub(tb, "Top", f"{y:.2f}in"); _sub(tb, "Left", "0.15in")
        _sub(tb, "Width", "6.50in"); _sub(tb, "Height", f"{title_h:.2f}in")
        y += title_h + 0.40

    # Meta row: Run Date / Total of ALL Records. Total scoped to
    # the first dataset to satisfy SSRS aggregate-scope rules.
    first_ds = next((q.name for q in (report.queries or [])), None)
    total_expr = (f'=CountRows("{_safe(first_ds)}")'
                  if first_ds else "=CountRows()")
    meta_lines = [
        ("Run Date:", '=Format(Globals!ExecutionTime, "MM/dd/yyyy HH:mm:ss")'),
        ("Total of ALL Records:", total_expr),
    ]
    for idx, (lbl, val) in enumerate(meta_lines):
        _build_textbox(
            ri, f"LcCov_MetaLbl_{idx}", lbl,
            bold=True, font_size="10pt", fg=INK,
            text_align="Right", vertical_align="Middle",
            border_color="#ffffff", padding="2pt",
        )
        ltb = ri[-1]
        _sub(ltb, "Top", f"{y:.2f}in"); _sub(ltb, "Left", "1.8in")
        _sub(ltb, "Width", "2.0in"); _sub(ltb, "Height", "0.24in")
        _build_textbox(
            ri, f"LcCov_MetaVal_{idx}", val,
            bold=True, font_size="10pt", fg=INK,
            text_align="Left", vertical_align="Middle",
            border_color="#ffffff", padding="2pt",
        )
        vtb = ri[-1]
        _sub(vtb, "Top", f"{y:.2f}in"); _sub(vtb, "Left", "3.9in")
        _sub(vtb, "Width", "2.7in"); _sub(vtb, "Height", "0.24in")
        y += 0.28

    y += 0.30

    # Layout-driven label:value rows.
    lbl_idx = 0
    for label_txt, value_expr, value_field in pairs:
        # Sub-report link surface? (field carries an Oracle hyperlink, OR
        # the field's source IS a link's URL-builder formula). Make the
        # textbox a real SSRS Drillthrough + style it like a link --
        # Oracle's cover URL was clickable, dead text here is a fidelity
        # bug (and the user can't reach the child report without it).
        dt = _drillthrough_for(report, value_field) \
            if value_field is not None else None
        if dt:
            # The link's display text usually comes from an Oracle URL/label
            # formula that needs live data or wired formulas to compute. On
            # a fresh server deployment it computes to EMPTY -> a clickable
            # but INVISIBLE textbox (user-reported: "the link did not
            # work"). Guarantee something visible to click.
            child_lbl = (dt.get("report_name") or "sub-report").replace('"', "")
            inner = value_expr[1:] if value_expr.startswith("=") \
                else '"' + value_expr.replace('"', '""') + '"'
            value_expr = (f'=IIf(Len(Trim(CStr(({inner}) & ""))) = 0, '
                          f'"Open {child_lbl}", CStr(({inner}) & ""))')
        link_kw = ({"fg": "#0b5cad", "underline": True, "drillthrough": dt}
                   if dt else {"fg": INK})
        if label_txt:
            # Normal label:value pair — label on the left, value on the right.
            _build_textbox(
                ri, f"LcCov_Lbl_{lbl_idx}", label_txt,
                bold=True, font_size="10pt", fg=INK,
                text_align="Right", vertical_align="Middle",
                border_color="#ffffff", padding="2pt", can_grow=True,
            )
            ltb = ri[-1]
            _sub(ltb, "Top", f"{y:.2f}in"); _sub(ltb, "Left", "0.40in")
            _sub(ltb, "Width", "2.40in"); _sub(ltb, "Height", "0.26in")
            _build_textbox(
                ri, f"LcCov_Val_{lbl_idx}", value_expr,
                font_size="10pt",
                text_align="Left", vertical_align="Middle",
                border_color="#ffffff", padding="2pt", can_grow=True,
                **link_kw,
            )
            vtb = ri[-1]
            _sub(vtb, "Top", f"{y:.2f}in"); _sub(vtb, "Left", "2.95in")
            _sub(vtb, "Width", "3.60in"); _sub(vtb, "Height", "0.26in")
        else:
            # Standalone value with no label (continuation note or the
            # URL line itself). Render as a full-width textbox spanning
            # the value column.
            _build_textbox(
                ri, f"LcCov_Val_{lbl_idx}", value_expr,
                font_size="9pt",
                text_align="Left", vertical_align="Middle",
                border_color="#ffffff", padding="2pt", can_grow=True,
                **link_kw,
            )
            vtb = ri[-1]
            _sub(vtb, "Top", f"{y:.2f}in"); _sub(vtb, "Left", "2.95in")
            _sub(vtb, "Width", "3.60in"); _sub(vtb, "Height", "0.26in")
        lbl_idx += 1
        y += 0.30

    rect_h = y + 0.40
    _sub(rect, "Top", "0in"); _sub(rect, "Left", "0.35in")
    _sub(rect, "Width", "6.8in"); _sub(rect, "Height", f"{rect_h:.2f}in")
    rect.set("data-rect-height-in", f"{rect_h:.4f}")
    return rect


def _layout_fields_in_order(report):
    """Yield (depth, field) tuples for every LayoutField under any
    section/frame, sorted by y then x. Used by _build_per_record_body
    to render fields in their natural top-down screen order."""
    out = []

    def walk(group, depth=0):
        for f in group.fields or []:
            y = getattr(f, "y", 0.0) or 0.0
            x = getattr(f, "x", 0.0) or 0.0
            out.append((y, x, depth, f))
        for ch in group.children or []:
            walk(ch, depth + 1)

    for top in (report.layout or []):
        walk(top, 0)
    out.sort(key=lambda t: (t[0], t[1]))
    return out


def _layout_fields_in_order_from_section(section):
    """Same shape as _layout_fields_in_order, but walks ONE section
    subtree. Used by _build_per_record_body so it renders only the
    actual per-record content from <section name="main"> and skips
    the Parameter Form labels living in <section name="header">.

    Mirrors what html_mockup's _render_certificate /
    _render_certificate_mockup do: find section_main, render only
    its frame children.
    """
    out = []
    if section is None:
        return out

    def walk(group, depth=0):
        for f in group.fields or []:
            y = getattr(f, "y", 0.0) or 0.0
            x = getattr(f, "x", 0.0) or 0.0
            out.append((y, x, depth, f))
        for ch in group.children or []:
            walk(ch, depth + 1)

    walk(section, 0)
    out.sort(key=lambda t: (t[0], t[1]))
    return out


_BLOB_DATATYPES = frozenset({
    "blob", "binlob", "binarylob", "longraw", "long raw", "long_raw",
    "bfile", "image",
})


def _image_field_binding(lf, report):
    """If a kind='field' LayoutField is bound to a database BLOB / image
    column, return ``(dataset_name, column_name)``; otherwise None.

    Generic: keyed off the DataItem ``datatype`` parsed from the source
    XML (Oracle marks signature / photo columns ``datatype="blob"``),
    never off a column name. This lets the per-record body emit a real
    <Image> for the blob instead of a textbox showing raw bytes.
    """
    if (getattr(lf, "kind", "field") or "field") != "field":
        return None
    src = (getattr(lf, "source", "") or "").strip().lstrip("&:")
    if not src:
        return None
    su = src.upper()
    for q in (report.queries or []):
        for it in (q.items or []):
            if (it.name or "").upper() != su:
                continue
            dt = (getattr(it, "datatype", "") or "").strip().lower()
            return (q.name or "", it.name) if dt in _BLOB_DATATYPES else None
    return None


def _section_has_image_field(section, report) -> bool:
    """True when any field anywhere under ``section`` is bound to a
    database BLOB/image column (see _image_field_binding)."""
    if section is None:
        return False

    def walk(g) -> bool:
        for f in (getattr(g, "fields", None) or []):
            if _image_field_binding(f, report):
                return True
        for c in (getattr(g, "children", None) or []):
            if walk(c):
                return True
        return False

    return walk(section)


def _emit_db_image(parent_items, name, ds_name, col_name,
                   left, top, width, height):
    """Emit an <Image> bound to a database BLOB column. Mirrors the
    signature-image shape SSRS accepts: Source=Database with a scoped
    First() so the value resolves even from inside another dataset's
    data region."""
    img = _sub(parent_items, "Image")
    img.set("Name", _safe(name))
    _sub(img, "Source", "Database")
    col = _safe(col_name)
    if ds_name:
        _sub(img, "Value",
             '=First(Fields!' + col + '.Value, "' + _safe(ds_name) + '")')
    else:
        _sub(img, "Value", '=First(Fields!' + col + '.Value)')
    _sub(img, "MIMEType", "image/png")
    _sub(img, "Sizing", "FitProportional")
    _sub(img, "Top", f"{top:.2f}in")
    _sub(img, "Left", f"{left:.2f}in")
    _sub(img, "Height", f"{height:.2f}in")
    _sub(img, "Width", f"{width:.2f}in")


def _image_asset_for(lf, report) -> Optional[str]:
    """Return the EmbeddedImage NAME for an image-kind layout field, when
    image bytes are available (parsed from the Oracle export's binaryData
    or user-uploaded). ``*`` is the wildcard slot — one uploaded image
    applied to every placeholder without a specific match."""
    assets = getattr(report, "_image_assets", None) or {}
    if not assets:
        return None
    key = ((getattr(lf, "image_id", "") or getattr(lf, "name", "") or "")
           .upper())
    return assets.get(key) or assets.get("*")


def _emit_embedded_image(parent_items, name, emb_name,
                         left, top, width, height):
    """Emit an <Image Source="Embedded"> at fixed geometry (state seals,
    logos, watermarks). Painted in source order, so an image that appears
    before overlapping text in the Oracle layout renders BEHIND it —
    Oracle's own watermark behavior."""
    img = _sub(parent_items, "Image")
    img.set("Name", _safe(name))
    _sub(img, "Source", "Embedded")
    _sub(img, "Value", emb_name)
    _sub(img, "Sizing", "FitProportional")
    _sub(img, "Top", f"{top:.2f}in")
    _sub(img, "Left", f"{left:.2f}in")
    _sub(img, "Height", f"{height:.2f}in")
    _sub(img, "Width", f"{width:.2f}in")


def _emit_field_textbox(
    parent_items, name, value, lf, ox, oy, rect_w, rect_h, report,
    cover_title_lines,
):
    """Emit ONE LayoutField as a Textbox positioned relative to its
    containing frame's (ox, oy) origin. Used by _emit_frame_rect.
    Returns (emitted_bool, bottom_y_relative)."""
    kind = getattr(lf, "kind", "field") or "field"
    text = (getattr(lf, "text", "") or "").strip()
    source = (getattr(lf, "source", "") or "").strip()

    if kind == "text" and not text:
        return (False, 0.0)
    if kind == "field" and not source:
        return (False, 0.0)
    if kind == "text" and text.strip().lower() in cover_title_lines:
        return (False, 0.0)
    u_text = text.strip()
    u_lower = u_text.lower()
    # Keep starred labels (*Sort Order, *Generate Envelopes) and hyperlink
    # descriptions — these are REAL cover content shown in production
    # (verified against Oracle frontend screenshots). Only suppress the
    # Oracle format-trigger ERROR/empty-state branch below.
    # Oracle format-trigger ERROR/empty-state branch (e.g. "ERROR: No CURRENT
    # Permittee as of ...") prints ONLY on the failure path; at run time the
    # happy path hides it via the trigger. We can't evaluate the trigger, so
    # suppress it rather than overlap it onto the real value field. Confirmed
    # 1:1 against the Oracle PDF (which shows the permittee, not the error).
    if kind == "text" and re.match(r"(?i)^\s*error\b\s*:", u_text):
        return (False, 0.0)

    # A field bound to a database BLOB column (Oracle's chief-signature
    # blob) must render as a real <Image>, not a textbox of raw bytes.
    img_bind = _image_field_binding(lf, report)

    # Layout <image> object (state seal / logo / watermark) with available
    # bytes — from the Oracle export's binaryData or a user upload. Emit a
    # real embedded Image at the Oracle geometry. Without bytes the
    # placeholder is skipped (current behavior, nothing to draw).
    if kind == "image" and img_bind is None:
        emb_name = _image_asset_for(lf, report)
        if not emb_name:
            return (False, 0.0)
        fx = float(getattr(lf, "x", 0.0) or 0.0)
        fy = float(getattr(lf, "y", 0.0) or 0.0)
        iw = float(getattr(lf, "width", 0.0) or 0.0) or 1.0
        ih = float(getattr(lf, "height", 0.0) or 0.0) or 1.0
        i_left = max(0.02, fx - ox)
        i_top = max(0.02, fy - oy)
        i_w = max(0.2, min(iw, rect_w - i_left - 0.02))
        i_h = max(0.2, ih)
        _emit_embedded_image(parent_items, name, emb_name,
                             i_left, i_top, i_w, i_h)
        return (True, i_top + i_h)

    bold = bool(getattr(lf, "bold", False))
    fs = getattr(lf, "font_size", 0) or 10
    fcolor = getattr(lf, "color", "") or "#111111"
    align = (getattr(lf, "align", "") or "left").lower()
    text_align = {
        "left": "Left", "center": "Center", "centre": "Center",
        "right": "Right", "start": "Left", "end": "Right",
    }.get(align, "Left")
    font_size = f"{fs}pt" if fs else "10pt"

    # Heuristic centring: bold, large text near the top of its frame
    # is almost always a title -- centre it.
    fx_abs = float(getattr(lf, "x", 0.0) or 0.0)
    fy_abs = float(getattr(lf, "y", 0.0) or 0.0)
    if (kind == "text" and bold and fs and fs >= 11
            and (fy_abs - oy) <= 1.5
            and text_align == "Left"):
        text_align = "Center"

    value_expr = ""
    if img_bind is None:
        if kind == "text":
            # Oracle's XML export pretty-prints CDATA text: REAL line
            # breaks and wrap-indentation both arrive as "\n + spaces".
            # The deterministic discriminator is Oracle's own geometry:
            # a box whose height only fits ONE line of its font renders
            # as one line in Oracle (newlines were formatting noise), a
            # taller box stacks. Match that. (Verified against the real
            # Oracle PDF: 'expires <date>' [0.16in @8pt] is one line;
            # the title block [0.70in @20pt] stacks.)
            _fh = float(getattr(lf, "height", 0.0) or 0.0) or 0.22
            # 1.15 leading matches Oracle's renderer at the boundary cases
            # (0.70in @ 20pt stacks two lines; 0.16in @ 8pt is one line).
            _line_h = max(6.0, float(fs)) * 1.15 / 72.0
            if _fh < 2 * _line_h:
                text = re.sub(r"\s*\n\s*", " ", text).strip()
            resolved, is_expr = _resolve_text_expression(
                text, report,
                dataset_name=(_pick_main_query(report).name
                              if _pick_main_query(report) else ""),
            )
            if is_expr:
                value_expr = resolved
            else:
                lines = [ln.strip() for ln in text.split(chr(10))]
                esc = [ln.replace('"', '""') for ln in lines]
                value_expr = ('=' + ' & vbCrLf & '.join(
                    '"' + ln + '"' for ln in esc))
        else:
            value_expr = _field_value_for(
                lf, report,
                dataset_name=(_pick_main_query(report).name
                              if _pick_main_query(report) else ""),
            )
            if not value_expr:
                return (False, 0.0)

    # Position relative to the parent frame's (ox, oy).
    rel_left = max(0.02, fx_abs - ox)
    rel_top = max(0.02, fy_abs - oy)
    fw = float(getattr(lf, "width", 0.0) or 0.0)
    fh = float(getattr(lf, "height", 0.0) or 0.0)
    if fw <= 0:
        fw = max(0.5, rect_w - rel_left - 0.02)
    if fh <= 0:
        fh = 0.22
    place_w = max(0.40, min(fw, rect_w - rel_left - 0.02))
    place_h = max(0.18, fh)

    if img_bind is not None:
        ds_name, col = img_bind
        _emit_db_image(parent_items, name, ds_name, col,
                       rel_left, rel_top, place_w, place_h)
        return (True, rel_top + place_h)

    _build_textbox(
        parent_items, name, value_expr,
        bold=bold, font_size=font_size, fg=fcolor,
        text_align=text_align, vertical_align="Top",
        border_color="#ffffff", padding="2pt",
        # FIXED boxes, like Oracle. CanGrow=true lets a multi-line value
        # grow past its declared height and PUSH every sibling below it;
        # on a tightly budgeted per-record page (content ~11.2in of an
        # 11.3in printable area) a 0.1in cascade spills each record onto
        # a second page (measured with the real MS engine). Oracle's
        # fixed-position layouts guarantee the declared box fits the
        # content, so fixed boxes are both safe AND faithful.
        can_grow=False,
        drillthrough=_drillthrough_for(report, lf),
    )
    tb = parent_items[-1]
    _sub(tb, "Top", f"{rel_top:.2f}in")
    _sub(tb, "Left", f"{rel_left:.2f}in")
    _sub(tb, "Width", f"{place_w:.2f}in")
    _sub(tb, "Height", f"{place_h:.2f}in")
    return (True, rel_top + place_h)


def _emit_frame_rect(
    parent_items, group, parent_x, parent_y, parent_w,
    report, cover_title_lines, name_prefix, counter,
):
    """Emit a LayoutGroup as a bordered Rectangle nested under
    parent_items. Coords inside the rect are relative to the group's
    own origin (mirrors html_mockup._render_frame).

    Generic - draws a 1pt border whenever the source XML's
    border_width > 0. Recurses into nested frames so child cards /
    sub-frames each get their own bordered Rectangle.
    """
    gx = float(getattr(group, "x", 0.0) or 0.0)
    gy = float(getattr(group, "y", 0.0) or 0.0)
    gw = float(getattr(group, "width", 0.0) or 0.0)
    gh = float(getattr(group, "height", 0.0) or 0.0)
    if gw <= 0:
        gw = max(1.0, parent_w - max(0.0, gx - parent_x) - 0.05)
    if gh <= 0:
        gh = 1.0

    rect_left = max(0.02, gx - parent_x)
    rect_top = max(0.02, gy - parent_y)
    rect_w = max(0.5, min(gw, parent_w - rect_left - 0.02))
    rect_h = max(0.5, gh)

    rect = _sub(parent_items, "Rectangle")
    rect.set("Name", f"{name_prefix}_Rect_{counter[0]}")
    counter[0] += 1
    _sub(rect, "KeepTogether", "true")
    style = _sub(rect, "Style")
    border_w = float(getattr(group, "border_width", 0) or 0)
    if border_w > 0:
        # Visible frame: white card + black border (Oracle look).
        _sub(style, "BackgroundColor", "#ffffff")
        border = _sub(style, "Border")
        _sub(border, "Style", "Solid")
        _sub(border, "Color", "#000000")
        _sub(border, "Width", "1pt")
    else:
        # Invisible grouping frame: NO background fill. A white fill on a
        # borderless container paints OVER sibling text that slightly
        # underlaps it (measured: the signature container's fill shaved
        # the ascenders of the line below). Transparent is faithful —
        # Oracle's frames are grouping constructs, not painted cards.
        border = _sub(style, "Border")
        _sub(border, "Style", "None")
    inner = _sub(rect, "ReportItems")

    max_y_used = 0.0
    # Render this group's own fields first.
    for lf in (group.fields or []):
        nm = f"{name_prefix}_Tb_{counter[0]}"
        counter[0] += 1
        ok, by = _emit_field_textbox(
            inner, nm, "", lf, gx, gy, rect_w, rect_h,
            report, cover_title_lines,
        )
        if ok:
            max_y_used = max(max_y_used, by)

    # Recurse into child frames (each gets its own bordered Rectangle).
    for child in (group.children or []):
        ckind = (child.kind or "").lower()
        if "frame" in ckind or ckind == "repeating_frame":
            cy = _emit_frame_rect(
                inner, child, gx, gy, rect_w, report,
                cover_title_lines, name_prefix, counter,
            )
            if cy is not None:
                max_y_used = max(max_y_used, cy)
        else:
            # Non-frame group: flatten its fields into this rect.
            for lf in (child.fields or []):
                nm = f"{name_prefix}_Tb_{counter[0]}"
                counter[0] += 1
                ok, by = _emit_field_textbox(
                    inner, nm, "", lf, gx, gy, rect_w, rect_h,
                    report, cover_title_lines,
                )
                if ok:
                    max_y_used = max(max_y_used, by)

    # Now stamp Top/Left/Height/Width on the Rectangle.
    final_h = max(rect_h, max_y_used + 0.10)
    _sub(rect, "Top", f"{rect_top:.2f}in")
    _sub(rect, "Left", f"{rect_left:.2f}in")
    _sub(rect, "Width", f"{rect_w:.2f}in")
    _sub(rect, "Height", f"{final_h:.2f}in")
    return rect_top + final_h


# --- Page-chrome sizing helpers ------------------------------------------
# The generated <Page> always carries a title PageHeader and a PageFooter.
# A per-record certificate body has to be budgeted against PageHeight
# MINUS that chrome, or the foot of each record (typically the wallet
# cards) spills onto a second page.

_PAGE_MARGIN_IN = 0.5          # top == bottom
# Horizontal margins are SMALLER than the body has slack for, so that
#   body width (7.5) + LeftMargin + RightMargin  <  PageWidth (8.5)
# STRICTLY holds. When body+margins EQUALS the page width, SSRS's PDF renderer
# emits a blank page after every page ("blank page after every page" -- THE
# classic SSRS bug). 7.5 + 0.25 + 0.25 = 8.0 < 8.5 -> 0.5in of safety, and
# 0.25in also matches the source report's own page margins.
_PAGE_HMARGIN_IN = 0.25        # left == right
_PAGE_FOOTER_HEIGHT_IN = 0.6


def _resolved_title_lines(report):
    """Title lines for the PageHeader, minus the generic 'Report
    Parameters' caption (which is never a real report title)."""
    lines = _extract_title_lines(report, limit=3)
    return [
        ln for ln in lines
        if not re.fullmatch(r"(report\s+)?parameters?", ln.strip(),
                            re.IGNORECASE)
    ]


def _page_header_height(report) -> float:
    """Height of the <PageHeader> band that _build_page emits, in
    inches. Kept in lock-step with _build_page so the page-height
    budget computed in _build_report_root matches the real header."""
    title_lines = _resolved_title_lines(report)
    if title_lines:
        return 0.20 + 0.22 * len(title_lines) + 0.30
    return 0.25


def _center_sibling_frame_rows(frames, parent_w):
    """Return ``{id(frame): x_delta}`` that horizontally centers each
    *row* of sibling frames inside ``parent_w``.

    Frames whose vertical extents overlap belong to the same row (e.g.
    the two wallet cards that sit side by side at the foot of a
    certificate). Each row -- a single main certificate frame, or a
    strip of side-by-side cards -- is shifted as a unit so it is
    centered in ``parent_w``; every frame keeps its own width and the
    gaps between frames are preserved. A row wider than ``parent_w`` is
    pushed to the left inset so the downstream width-clamp leaves it
    centered.

    Generic: derived purely from the parsed (x, y, width, height); no
    per-report names or tokens.
    """
    boxes = []
    for f in frames:
        boxes.append((
            f,
            float(getattr(f, "x", 0.0) or 0.0),
            float(getattr(f, "y", 0.0) or 0.0),
            float(getattr(f, "width", 0.0) or 0.0),
            float(getattr(f, "height", 0.0) or 0.0),
        ))
    rows = []
    for box in sorted(boxes, key=lambda b: b[2]):
        _f, _x, y, _w, h = box
        placed = False
        for row in rows:
            ry0 = min(b[2] for b in row)
            ry1 = max(b[2] + b[4] for b in row)
            if y < ry1 - 0.05 and (y + h) > ry0 + 0.05:
                row.append(box)
                placed = True
                break
        if not placed:
            rows.append([box])
    deltas = {}
    for row in rows:
        gmin = min(b[1] for b in row)
        gmax = max(b[1] + b[3] for b in row)
        span = gmax - gmin
        if span <= 0:
            continue
        if span >= parent_w:
            # Row is wider than the body; _emit_frame_rect clamps each
            # frame to fit. Push it to the left inset so the clamp
            # leaves it centered (~0.02in each side).
            delta = 0.02 - gmin
        else:
            delta = (parent_w - span) / 2.0 - gmin
        if abs(delta) < 0.02:
            continue
        for box in row:
            deltas[id(box[0])] = delta
    return deltas


def _build_per_record_body(report, main):
    """Build a Body that renders ONE PAGE PER RECORD of the main dataset.

    Generic for letter / certificate / single-document reports. Layout
    comes straight from the source XML's positional fields: title
    text fields at the top, then label:value pairs from layout fields
    bound to dataset columns, then static paragraphs / signatures.

    Structure:
        <Body>
          <ReportItems>
            <Rectangle Rect_CoverPage/>     -- cover (Page 1)
            <Tablix Tablix_Record>          -- 1 col x 1 row, bound to main
              <TablixBody><TablixColumns>... <TablixRows>...
                <CellContents>
                  <Rectangle Rect_RecordPage>
                    <ReportItems>
                      ... stacked textboxes ...
                    </ReportItems>
                  </Rectangle>
                </CellContents>
              ...
              <TablixRowHierarchy>
                <TablixMembers>
                  <TablixMember>
                    <Group Name="Details_Record"/>
                    <PageBreak><BreakLocation>End</BreakLocation></PageBreak>
                  </TablixMember>
                </TablixMembers>
              </TablixRowHierarchy>
            </Tablix>
          </ReportItems>
          <Height>...</Height>
        </Body>

    Each iteration of the detail group renders the inner Rectangle and
    then forces a page break, giving the "one page per record" output
    that letters / certificates / per-permit documents produce.

    NOTHING per-report is hardcoded. Generic field extraction from the
    parsed layout.
    """
    body = ET.Element(_q("Body"))
    items = _sub(body, "ReportItems")

    # ---- Cover page (Report Parameters) on page 1 ------------------------
    # For letter / certificate reports the source XML carries a custom
    # Parameter Form in <section name="header"> with its own labels
    # ("Selection Criteria:", "Permit Details:", "Hyperlinks in
    # Permits:", etc.) -- a generic "Report Parameters" list is the
    # wrong cover here. Build a layout-driven cover first; fall back
    # to the generic cover for any report whose section_header isn't
    # form-shaped.
    cover_h = 0.0
    cover = _build_letter_cover_page(report)
    if cover is None:
        cover = _build_cover_page(report)
    next_top = 0.10
    if cover is not None:
        # The cover does NOT carry its own PageBreak. Reason: SSRS positions
        # body items at their ABSOLUTE Top coordinate on every page — a cover
        # End-break at 4.10in means the Tablix (at Top=4.10in) renders 4.10in
        # from the TOP of page 2, so its 11.12in cert needs 15.22in on page 2
        # but only 11.32in is printable → cert overflows → blank page 2.
        # Instead, the detail group carries PageBreak=Start, which fires
        # BEFORE the first row (separating the cover from cert 1) and before
        # every subsequent row (one cert per page). No double-break, no blank.
        items.append(cover)
        try:
            cover_h = float(cover.attrib.pop("data-rect-height-in", "0"))
        except Exception:
            cover_h = 0.0
        next_top = cover_h + 0.10

    if main is None:
        body_height_in = max(9.0, next_top + 1.0)
        _sub(body, "Height", f"{body_height_in}in")
        _sub(body, "Style")
        return body

    # ---- Compute body width from Oracle source layout --------------------
    # Oracle layouts can be wider than 7.5in (e.g. M_PERMIT at x=0.15,
    # w=7.71 → right edge 7.86). Clipping to 7.5 shifts fields left and
    # breaks centering. Compute from the actual frame span + small margin.
    _main_section = _section_by_kind(report, "section_main")
    _frame_children_pre = [
        c for c in (_main_section.children if _main_section else [])
        if "frame" in (getattr(c, "kind", "") or "").lower()
    ]
    if _frame_children_pre:
        _all_r = [float(getattr(c, "x", 0) or 0) + float(getattr(c, "width", 0) or 0)
                  for c in _frame_children_pre]
        # Cap at PageWidth - 2*margin - buffer so the width invariant
        # (body + margins < PageWidth) holds and SSRS doesn't insert
        # blank pages. 8.5 - 0.5 - 0.02 = 7.98 fits Oracle's 7.85 span.
        _MAX_BODY_W = 8.5 - 2 * _PAGE_HMARGIN_IN - 0.02  # 7.98
        BODY_W = min(_MAX_BODY_W, max(7.5, round(max(_all_r) + 0.15, 2)))
    else:
        BODY_W = 7.5

    # ---- Tablix that iterates per row of the main dataset ----------------
    tablix = ET.Element(_q("Tablix"))
    tablix.set("Name", "Tablix_Record")
    tbody = _sub(tablix, "TablixBody")

    cols = _sub(tbody, "TablixColumns")
    _sub(_sub(cols, "TablixColumn"), "Width", f"{BODY_W}in")

    rows = _sub(tbody, "TablixRows")
    record_row = _sub(rows, "TablixRow")

    # Pre-collect fields in screen order so we can compute the row
    # height precisely.
    #
    # IMPORTANT: walk ONLY the section_main subtree. Oracle Reports
    # XML splits the layout into <section name="header"> (Parameter
    # Form / instruction text like "Selection Criteria:", "*Generate
    # Envelopes:", "[Permittee] is a hyperlink to...") and
    # <section name="main"> (the actual per-record letter / permit /
    # certificate body). The previous full-tree walk leaked every
    # header-section label onto every per-record page.
    #
    # Falls back to a full walk only when no section_main is present,
    # preserving behaviour for any XML shape the existing code
    # happens to support.
    main_section = (
        _section_by_kind(report, "section_main")
        or (report.layout[0] if (report.layout or []) else None)
    )
    if main_section is not None:
        ordered = _layout_fields_in_order_from_section(main_section)
    else:
        ordered = _layout_fields_in_order(report)

    # When the layout itself carries a database-blob field (a signature
    # image inside the certificate), the image is emitted in the body at
    # its real position -- so the page-footer signature fallback must be
    # suppressed to avoid printing it twice.
    signature_in_body = _section_has_image_field(main_section, report)
    # Cap how many fields we emit so we don't make a 50in tall body.
    # Most permits / letters have <60 visible fields.
    MAX_FIELDS = 80
    keep = ordered[:MAX_FIELDS]

    # Build a set of cover-title lines so we suppress any text field
    # whose content duplicates the cover-page title (preventing a
    # multi-line letterhead, e.g. "<STATE> / <DEPARTMENT> ...", from being
    # repeated at the bottom of page 1).
    _cover_title_lines = set()
    for _ln in _extract_title_lines(report, limit=3):
        _cover_title_lines.add(_ln.strip().lower())

    # Build (text or value) lines for each field. For static text we
    # emit the text verbatim. For data-bound fields we emit
    #   <Label>: =Fields!<name>.Value
    # using the parser-derived label / column name.
    field_names_lower = {
        (it.name or "").lower(): it.name
        for q in (report.queries or [])
        for it in (q.items or [])
    }

    LINE_H = 0.22  # in
    HEADER_BAND_H = 0.30
    n_lines = max(1, len(keep))
    rect_h = max(1.0, 0.30 + n_lines * LINE_H + 0.30)
    _sub(record_row, "Height", f"{rect_h:.2f}in")

    rcells = _sub(record_row, "TablixCells")
    rcell = _sub(rcells, "TablixCell")
    rcontents = _sub(rcell, "CellContents")

    rect = _sub(rcontents, "Rectangle")
    rect.set("Name", "Rect_RecordPage")
    _sub(rect, "KeepTogether", "true")
    rstyle = _sub(rect, "Style")
    palette = _resolve_palette(report)
    _sub(rstyle, "BackgroundColor", palette["card_bg"])
    # Outer border around the per-record content -- gives the
    # certificate / letter its own visible frame on the page. The
    # individual sub-frames (named M_* children in the Oracle
    # layout) get their OWN borders from _emit_frame_rect when
    # border_width > 0.
    rborder = _sub(rstyle, "Border")
    _sub(rborder, "Style", "None")
    rect_items = _sub(rect, "ReportItems")

    # ---- Optional record-level title (first non-empty text field on row 0)
    # is rendered automatically as the first iteration of the loop below.

    # Prefer FRAME-based rendering when section_main has frame
    # children (M_PERMIT / M_CARD_L / M_CARD_R style). Each frame
    # becomes its OWN bordered Rectangle inside Rect_RecordPage,
    # giving the per-card borders the HTML mockup shows. Falls
    # through to the old flat positional loop when there are no
    # frame children to walk.
    frame_children = [
        c for c in (main_section.children if main_section else [])
        if (c.kind or "").lower() in ("frame", "repeating_frame")
        or "frame" in (c.kind or "").lower()
    ]
    used_frame_walk = False
    if frame_children:
        used_frame_walk = True
        counter = [0]
        max_by = 0.0
        # Center rows of SIBLING frames (e.g. the two wallet cards) within
        # the body width so they appear visually centered on the page.
        # The centering function groups same-y frames into rows and shifts
        # each row so its span is centered in BODY_W. Single-frame rows
        # (like the main cert) keep their Oracle position (already centered
        # by the Oracle designer).
        center_deltas = _center_sibling_frame_rows(frame_children, BODY_W)
        for child in frame_children:
            delta = center_deltas.get(id(child), 0.0)
            by = _emit_frame_rect(
                rect_items, child, -delta, 0.0, BODY_W,
                report, _cover_title_lines, "RecP", counter,
            )
            if by is not None:
                max_by = max(max_by, by)
        # Skip the flat positional loop -- frames already handled it.
        keep = []
        y = max_by

    y = max(y if used_frame_walk else 0.10, 0.10)
    emitted = 0
    for (_y, _x, _depth, f) in keep:
        kind = getattr(f, "kind", "field") or "field"
        text = (getattr(f, "text", "") or "").strip()
        source = (getattr(f, "source", "") or "").strip()
        # Skip totally empty fields.
        if kind == "text" and not text:
            continue
        if kind == "field" and not source:
            continue
        # Skip text fields whose CONTENT duplicates a cover-page title
        # line (e.g. a letterhead line appearing both on the cover
        # rectangle and inside section_main).
        if kind == "text" and text.strip().lower() in _cover_title_lines:
            continue
        # Skip text fields that are pure layout instructions / notes
        # belonging to the Parameter Form layer (defensive filter --
        # catches anything that slipped past the section scope). The
        # markers are generic Oracle Reports authoring conventions:
        #   "*..."     leading-asterisk note from the report author
        #   "&CF_..."  unresolved column-formula token
        #   "[...] is a hyperlink"  author note describing a link
        u_text = text.strip()
        u_lower = u_text.lower()
        # Keep starred labels and hyperlink descriptions — real cover
        # content shown in Oracle production (verified against frontend
        # screenshots). Only suppress via the ERROR heuristic below.
        # NOTE: do NOT skip text that merely STARTS with "&CF_" or
        # "&CP_" -- card / address blocks legitimately begin with
        # &CF_* or &CP_* substitution tokens, which get resolved
        # downstream by _resolve_text_expression. We only filter
        # pure-instruction author-notes above, never substantive
        # content.

        # Style hints from the parsed XML.
        bold = bool(getattr(f, "bold", False))
        fs = getattr(f, "font_size", 0) or 10
        fcolor = getattr(f, "color", "") or "#111111"
        align = (getattr(f, "align", "") or "left").lower()
        text_align = {
            "left": "Left", "center": "Center", "centre": "Center",
            "right": "Right", "start": "Left", "end": "Right",
        }.get(align, "Left")
        font_size = f"{fs}pt" if fs else "10pt"
        # Heuristic centring for title-shaped text: bold, font_size
        # >= 11, kind="text", in the upper 1.5in of the page, and
        # spans multiple short lines (typical of a centered agency
        # letterhead / title block at the top of a letter).
        # Generic - no per-report tokens.
        if (
            kind == "text" and bold and fs and fs >= 11
            and (_y or 0) <= 1.5
            and text_align == "Left"
        ):
            text_align = "Center"

        if kind == "text":
            # Resolve &TOKEN / :BIND substitutions through the same
            # resolver the tabular tablix uses, then fall back to a
            # multi-line VB literal for the remaining static text.
            resolved, is_expr = _resolve_text_expression(
                text, report, dataset_name=main.name or "",
            )
            if is_expr:
                value = resolved
            else:
                _lines = text.split(chr(10))
                _esc = [ln.replace('"', '""') for ln in _lines]
                value = '=' + ' & vbCrLf & '.join('"' + ln + '"' for ln in _esc)
        else:
            # Route field sources through _field_value_for so that
            # CF_/CP_ formulas, parameter refs, CurrentDate, and
            # cross-dataset refs resolve correctly (no orphan
            # Fields!X.Value refs allowed -- Report Builder rejects).
            value = _field_value_for(
                f, report, dataset_name=main.name or "",
            )
            if not value:
                # No usable source -- skip this field entirely rather
                # than emit an orphan reference.
                continue

        # Positional emission: use the layout's actual (x, y, w, h)
        # from the parsed XML so side-by-side blocks (e.g. two
        # address cards at x=0.15 and x=3.95) render as separate
        # visible cards instead of stacked text. Mirrors what
        # html_mockup._render_field does for the preview.
        fx = float(getattr(f, "x", 0.0) or 0.0)
        fy = float(getattr(f, "y", 0.0) or 0.0)
        fw = float(getattr(f, "width", 0.0) or 0.0)
        fh = float(getattr(f, "height", 0.0) or 0.0)
        # Clamp into the 7.5in body width. If geometry is missing,
        # fall back to a sensible default that stacks left-aligned.
        if fw <= 0:
            fw = 7.5 - fx if fx < 7.5 else 3.0
        if fh <= 0:
            fh = LINE_H
        # Keep at least 0.1in inside the rectangle so SSRS doesn't
        # clip the textbox against the rect's left/top edge.
        place_left = max(0.05, min(fx, 7.4))
        place_top = max(0.05, fy)
        place_w = max(0.50, min(fw, 7.5 - place_left))
        place_h = max(0.18, fh)

        _build_textbox(
            rect_items, f"Tb_Rec_{emitted}", value,
            bold=bold, font_size=font_size, fg=fcolor,
            text_align=text_align, vertical_align="Top",
            border_color="#ffffff", padding="2pt",
            can_grow=True,
            drillthrough=_drillthrough_for(report, f),
        )
        tb = rect_items[-1]
        _sub(tb, "Top", f"{place_top:.2f}in")
        _sub(tb, "Left", f"{place_left:.2f}in")
        _sub(tb, "Width", f"{place_w:.2f}in")
        _sub(tb, "Height", f"{place_h:.2f}in")
        # Track the bottom-most pixel so we can size the row to fit.
        y = max(y, place_top + place_h)
        emitted += 1

    # Fallback: dataset columns exist but NO usable positional layout was
    # found (e.g. a sub-report synthesized from raw SQL, or an XML whose
    # section_main carried no fields). Without this the per-record Rectangle
    # renders EMPTY -- blank pages. Emit the dataset's own columns as stacked
    # "Label: value" textboxes so each record actually shows its data.
    # Generic: driven only by the dataset column list, nothing per-report.
    if emitted == 0 and not used_frame_walk and main is not None:
        fy = 0.20
        for _it in (main.items or []):
            _col = (_it.name or "").strip()
            if not _col:
                continue
            _label = (_it.label or _col.replace("_", " ").title()).strip()
            _sc = _safe(_col)
            _val = ('="' + _label.replace('"', '""') + ':  " & '
                    'IIF(IsNothing(Fields!' + _sc + '.Value), "", '
                    'CStr(Fields!' + _sc + '.Value))')
            _build_textbox(
                rect_items, "Tb_Rec_" + str(emitted), _val,
                bold=False, font_size="10pt", fg="#111111",
                text_align="Left", vertical_align="Top",
                border_color="#ffffff", padding="2pt", can_grow=True,
            )
            _tb = rect_items[-1]
            _sub(_tb, "Top", f"{fy:.2f}in")
            _sub(_tb, "Left", "0.30in")
            _sub(_tb, "Width", "6.90in")
            _sub(_tb, "Height", "0.25in")
            fy += 0.30
            emitted += 1
        y = max(y, fy)

    # Adjust the row Height to the actual content extent so we
    # don't pad with a giant blank. `y` is now the max-bottom across
    # every positionally-emitted textbox (was a simple running
    # accumulator in the stack-vertically version).
    rect_h = max(1.0, y + 0.30)
    for h_el in record_row.findall(_q("Height")):
        h_el.text = f"{rect_h:.2f}in"

    # ---- Hierarchy: single detail member with PageBreak End --------------
    col_hier = _sub(tablix, "TablixColumnHierarchy")
    col_members = _sub(col_hier, "TablixMembers")
    _sub(col_members, "TablixMember")

    row_hier = _sub(tablix, "TablixRowHierarchy")
    row_members = _sub(row_hier, "TablixMembers")
    # Outer wrapper member: forces the FIRST record onto its own
    # fresh page via PageBreak Start. PageBreak must live inside
    # a <Group> per the SSRS 2008 RDL schema (not directly inside
    # TablixMember).
    #
    # IMPORTANT: the Group MUST have GroupExpressions, otherwise
    # SSRS treats it as a DETAIL member ("renders per dataset row")
    # and rejects upload with "detail members can only contain
    # static inner members" (our inner Details_Record has its own
    # Group, so it isn't static).
    #
    # GroupExpression "=1" groups every row into one group -> the
    # outer member renders once per the entire dataset, which is
    # exactly what we need to fire a single page break before the
    # first record. Inner Details_Record then iterates per row.
    outer_member = _sub(row_members, "TablixMember")
    outer_group = _sub(outer_member, "Group")
    outer_group.set("Name", "OuterPageWrapper")
    outer_ge = _sub(outer_group, "GroupExpressions")
    _sub(outer_ge, "GroupExpression", "=1")
    # No page break on the outer wrapper. The detail group's own
    # PageBreak (Start or Between — see below) handles ALL pagination.
    inner_members = _sub(outer_member, "TablixMembers")

    det_member = _sub(inner_members, "TablixMember")
    det_group = _sub(det_member, "Group")
    det_group.set("Name", "Details_Record")
    # When a COVER is present: PageBreak=Start fires BEFORE each detail
    # row, including the first. This separates the cover (page 1) from
    # cert 1 (page 2) without needing a separate End break on the cover
    # Rectangle (which caused the blank-page overflow — see above).
    # When NO cover: PageBreak=Between fires between rows only, so the
    # first cert renders right at the top of page 1 (no blank page 1).
    pb = _sub(det_group, "PageBreak")
    _sub(pb, "BreakLocation", "Start" if cover is not None else "Between")

    _sub(tablix, "DataSetName", _safe(main.name))
    # The Tablix MUST sit BELOW the cover so SSRS renders them in order
    # (Top=0 makes them overlap, hiding the first cert behind the cover).
    # Place the Tablix flush against the cover's bottom edge — zero gap.
    # The cover's PageBreak=End separates them onto different pages.
    # CRITICAL: any extra gap here (even 0.10in) can push the first
    # 11.12in TablixRow past the 11.32in printable area on page 2,
    # causing SSRS to insert a blank page.
    tablix_top = cover_h if cover is not None else next_top
    _sub(tablix, "Top", f"{tablix_top:.2f}in")
    _sub(tablix, "Left", "0in")
    _sub(tablix, "Height", f"{rect_h:.2f}in")
    _sub(tablix, "Width", f"{BODY_W}in")
    _sub(tablix, "Style")

    items.append(tablix)

    # Body must end EXACTLY at the Tablix bottom. Any slack below the last
    # record (even 0.1in) is rendered as body whitespace AFTER the final
    # record and spills onto a TRAILING BLANK PAGE (measured with the real
    # MS engine: 0.6in slack -> one blank last page on every run).
    body_height_in = round(tablix_top + rect_h, 2)
    _sub(body, "Height", f"{body_height_in}in")
    _sub(body, "Style")
    # Stamp the per-record CONTENT height. The caller
    # (_build_report_root) grows PageHeight to fit one record plus the
    # page chrome (margins + header + footer). Stamping content+1.5in
    # under-budgeted the chrome (~2.3in), so the wallet cards at the
    # foot of the certificate spilled onto a second page.
    body.set("data-required-page-height-in", f"{rect_h:.2f}")
    body.set("data-body-width-in", f"{BODY_W:.2f}")
    if signature_in_body:
        body.set("data-signature-in-body", "1")
    return body


def _detect_multi_section(report: ParsedReport):
    """Detect a multi-SECTION dashboard: section_main holds >=2 sibling frames,
    each binding (via its repeating frames) to a query, covering >=2 DISTINCT
    queries overall. Some Oracle reports stack several independent
    data tables (Status / Actions / Historical) down one page; the single-main
    Tablix path renders only ONE of them.

    Returns an ordered list of sections:
        [{"header": str, "y": float, "tables": [(DataQuery, [col,...]), ...]}, ...]
    or None when the report is not multi-section (single table, letter, etc.).

    Purely structural -- sections, queries, headers, and columns all come from
    the parsed layout/queries. No report names or fields are hardcoded.
    """
    sm = _section_by_kind(report, "section_main")
    if sm is None:
        return None
    frames = [c for c in (sm.children or [])
              if "frame" in (c.kind or "").lower()
              and (c.kind or "").lower() != "repeating_frame"]
    if len(frames) < 2:
        return None

    def _query_for_group(src):
        if not src:
            return None
        for q in (report.queries or []):
            if _query_matches_layout_ref(q, src):
                return q
        return None

    def _header_text(frame):
        # The TOP-LEFT static <text> in the frame that is NOT inside a repeating
        # frame -- the section title (e.g. "Actions"). Picks by smallest (y, x),
        # NOT DOM order: an Oracle section frame holds its title in a _HDR
        # sub-frame and a "Total ..." label in a _FTR sub-frame, and DOM order
        # can surface the footer first. Topmost-leftmost is the real title.
        # Skips page-number / file-name footer tokens.
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

    def _tables_in_frame(frame):
        # Each repeating frame -> (query, ordered column names). De-dups
        # identical (query, cols) tables.
        out = []
        seen = set()

        def walk(g):
            if (g.kind or "").lower() == "repeating_frame":
                q = _query_for_group(g.source_query)
                cols = []
                for f in (g.fields or []):
                    if (f.kind or "") == "field":
                        s = (f.source or "").strip()
                        if s and s not in cols:
                            cols.append(s)
                if q is not None and cols:
                    key = (q.name.upper(), tuple(c.upper() for c in cols))
                    if key not in seen:
                        seen.add(key)
                        out.append((q, cols))
            for c in (g.children or []):
                walk(c)

        walk(frame)
        return out

    sections = []
    distinct_q = set()
    for fr in sorted(frames, key=lambda f: (f.y or 0.0)):
        tables = _tables_in_frame(fr)
        if not tables:
            continue
        for q, _ in tables:
            distinct_q.add(q.name.upper())
        sections.append({
            "header": _header_text(fr),
            "y": fr.y or 0.0,
            "tables": tables,
        })

    # Require genuine multi-section: >=2 sections AND >=2 distinct queries.
    # A single data table stays on the existing path.
    if len(sections) < 2 or len(distinct_q) < 2:
        return None
    return sections


def _build_section_tablix(report, name, query, columns, header_text, palette):
    """One stacked Tablix for a single dashboard section: an optional header
    band, a column-header row, and a detail row bound to ``query``. Mirrors the
    proven _build_tablix shape so it always uploads cleanly."""
    cols = [c for c in (columns or []) if c]
    if not cols:
        cols = [it.name for it in (query.items or []) if it.name][:4] or ["VALUE"]

    tablix = ET.Element(_q("Tablix"))
    tablix.set("Name", _safe(name))

    band_bg = palette.get("band_bg", "#4a6a8a")
    band_fg = palette.get("band_fg", "#ffffff")
    body = _sub(tablix, "TablixBody")
    cols_el = _sub(body, "TablixColumns")
    col_w = round(7.5 / max(1, len(cols)), 3)
    for _ in cols:
        c = _sub(cols_el, "TablixColumn")
        _sub(c, "Width", f"{col_w}in")
    rows_el = _sub(body, "TablixRows")

    have_header = bool(header_text)
    if have_header:
        hrow = _sub(rows_el, "TablixRow")
        _sub(hrow, "Height", "0.30in")
        hcells = _sub(hrow, "TablixCells")
        for i, _col in enumerate(cols):
            cell = _sub(hcells, "TablixCell")
            contents = _sub(cell, "CellContents")
            _build_textbox(
                contents, f"{_safe(name)}_Band_{i}",
                header_text if i == 0 else "",
                bold=True, bg=band_bg, fg=band_fg,
                text_align="Left", vertical_align="Middle",
                border_color=band_bg, padding="5pt",
            )

    chrow = _sub(rows_el, "TablixRow")
    _sub(chrow, "Height", "0.26in")
    chcells = _sub(chrow, "TablixCells")
    for col in cols:
        cell = _sub(chcells, "TablixCell")
        contents = _sub(cell, "CellContents")
        _build_textbox(
            contents, f"{_safe(name)}_Hdr_{_safe(col)}",
            col.replace("_", " "),
            bold=True, bg="#d6d6d6", fg="#282828",
            text_align="Center", vertical_align="Middle",
            border_color="#a0a0a0", padding="4pt",
        )

    drow = _sub(rows_el, "TablixRow")
    _sub(drow, "Height", "0.24in")
    dcells = _sub(drow, "TablixCells")
    alt = '=IIf(RowNumber(Nothing) Mod 2 = 0, "#f5f7fa", "#ffffff")'
    for col in cols:
        cell = _sub(dcells, "TablixCell")
        contents = _sub(cell, "CellContents")
        _build_textbox(
            contents, f"{_safe(name)}_Cell_{_safe(col)}",
            f"=Fields!{_safe(col)}.Value",
            bg=alt, vertical_align="Middle",
            border_color="#d0d0d0", padding="3pt",
        )

    col_hier = _sub(tablix, "TablixColumnHierarchy")
    cmembers = _sub(col_hier, "TablixMembers")
    for _ in cols:
        _sub(cmembers, "TablixMember")

    row_hier = _sub(tablix, "TablixRowHierarchy")
    rmembers = _sub(row_hier, "TablixMembers")
    if have_header:
        bm = _sub(rmembers, "TablixMember")
        _sub(bm, "KeepWithGroup", "After")
    hm = _sub(rmembers, "TablixMember")
    _sub(hm, "KeepWithGroup", "After")
    dm = _sub(rmembers, "TablixMember")
    _sub(dm, "Group").set("Name", f"{_safe(name)}_Details")

    _sub(tablix, "DataSetName", _safe(query.name))
    _sub(tablix, "Left", "0in")
    _sub(tablix, "Width", "7.5in")
    _sub(tablix, "Style")
    return tablix


def _build_multi_section_body(report: ParsedReport, sections) -> ET.Element:
    """Body that stacks one Tablix per detected dashboard section, top to
    bottom. Each section binds to its OWN query so all sections render --
    fixing the 'only the first table shows' multi-section bug."""
    body = ET.Element(_q("Body"))
    items = _sub(body, "ReportItems")
    palette = _resolve_palette(report)

    top = 0.10
    idx = 0
    SECTION_GAP = 0.30
    EST_ROW = 0.24
    for sec in sections:
        header_used = False
        for (query, cols) in sec["tables"]:
            name = f"Tbx_S{idx}"
            # Only the FIRST table in a section carries the section header band.
            hdr = sec.get("header", "") if not header_used else ""
            header_used = True
            tx = _build_section_tablix(report, name, query, cols, hdr, palette)
            _sub(tx, "Top", f"{top:.2f}in")
            items.append(tx)
            est = 0.30 + 0.26 + EST_ROW * 6
            top += est + SECTION_GAP
            idx += 1

    _sub(body, "Height", f"{max(9.0, top + 0.5):.2f}in")
    _sub(body, "Style")
    return body


def _find_matrix_spec(report) -> Optional[dict]:
    """Locate an Oracle cross-tab (matrix) and derive its SSRS wiring.

    Returns {row, col, cells, query, dominant} or None. Handles BOTH wild
    dialects: 6i inline (<matrix> nesting matrixCol/matrixRow/matrixCell
    with the dimension/cell fields) and 9.0.2 frame-ref (<matrix
    horizontalFrame=... verticalFrame=...> pointing at repeatingFrames).
    ``dominant`` is True only when the matrix is its section's primary
    content — mixed layouts keep the existing (safe) rendering path."""
    matrices = []

    def walk(g, root):
        if (getattr(g, "kind", "") or "") == "matrix":
            matrices.append((g, root))
        for c in (getattr(g, "children", None) or []):
            walk(c, root)

    for lg in (report.layout or []):
        walk(lg, lg)
    if not matrices:
        return None
    mx, sec_root = matrices[0]

    def dim_fields(kind):
        out = []
        for c in (mx.children or []):
            if (getattr(c, "kind", "") or "") == kind:
                out.extend([(f.source or "").strip()
                            for f in (c.fields or [])
                            if (f.source or "").strip()])
        return out

    col_fields = dim_fields("matrix_col")
    row_fields = dim_fields("matrix_row")
    cell_fields = dim_fields("matrix_cell")

    if not (col_fields and row_fields):
        attrs = getattr(mx, "matrix_attrs", {}) or {}

        def frame_fields(frame_name):
            if not frame_name:
                return []
            found = []

            def w(g):
                if (g.name or "") == frame_name:
                    found.extend([(f.source or "").strip()
                                  for f in (g.fields or [])
                                  if (f.source or "").strip()])
                for c in (g.children or []):
                    w(c)

            for lg in (report.layout or []):
                w(lg)
            return found

        col_fields = col_fields or frame_fields(attrs.get("horizontalFrame"))
        row_fields = row_fields or frame_fields(attrs.get("verticalFrame"))

    if not (col_fields and row_fields):
        return None
    cells = cell_fields or col_fields[1:2] or row_fields[1:2]
    if not cells:
        return None

    # Dominance: any field-bearing NON-matrix group in the same section?
    def count_others(g, inside_mx):
        ins = inside_mx or (g is mx)
        n = 0
        if (not ins and (g.fields or [])
                and (getattr(g, "kind", "") or "")
                not in ("matrix", "matrix_col", "matrix_row", "matrix_cell")):
            n = 1
        for c in (getattr(g, "children", None) or []):
            n += count_others(c, ins)
        return n

    dominant = count_others(sec_root, False) == 0

    query = None
    for q in (report.queries or []):
        names = {(it.name or "").upper() for it in (q.items or [])}
        if cells[0].upper() in names or col_fields[0].upper() in names:
            query = q
            break
    if query is None:
        query = _pick_main_query(report)
    if query is None:
        return None
    return {"row": row_fields[0], "col": col_fields[0], "cells": cells,
            "query": query, "dominant": dominant}


def _prepare_matrix(report) -> None:
    """Pre-pass (BEFORE datasets are built): stash the matrix spec and make
    sure the bound dataset declares every dimension/measure column — wild
    cross-products often keep these only in <summary>/crossProduct items
    that no plain query group declares."""
    spec = _find_matrix_spec(report)
    report._matrix_spec = spec
    if not spec or not spec.get("dominant"):
        return
    from ..models import DataItem
    q = spec["query"]
    have = {(it.name or "").upper() for it in (q.items or [])}
    for cname in [spec["row"], spec["col"], *spec["cells"]]:
        if cname.upper() not in have:
            is_measure = cname in spec["cells"]
            q.items.append(DataItem(
                name=cname, expression=cname,
                datatype="number" if is_measure else "vchar2"))
            have.add(cname.upper())


def _build_matrix_tablix(report, spec) -> ET.Element:
    """A REAL two-axis SSRS cross-tab: dynamic row group (down), dynamic
    column group (across), Sum() cells. Multiple measures stack inside the
    cell as labeled lines (data-complete; geometry approximated)."""
    ds = _safe(spec["query"].name)
    row_f, col_f = _safe(spec["row"]), _safe(spec["col"])
    cells = [_safe(c) for c in spec["cells"]]

    t = ET.Element(_q("Tablix"))
    t.set("Name", "Tablix_Matrix")
    body = _sub(t, "TablixBody")
    cols_el = _sub(body, "TablixColumns")
    for w in ("1.8in", "1.5in"):
        _sub(_sub(cols_el, "TablixColumn"), "Width", w)
    rows_el = _sub(body, "TablixRows")

    hdr_bg, hdr_fg = "#4a6a8a", "#ffffff"
    r0 = _sub(rows_el, "TablixRow")
    _sub(r0, "Height", "0.30in")
    c0 = _sub(r0, "TablixCells")
    cont = _sub(_sub(c0, "TablixCell"), "CellContents")
    _build_textbox(cont, "Mx_Corner", spec["row"].replace("_", " "),
                   bold=True, bg=hdr_bg, fg=hdr_fg, text_align="Left",
                   vertical_align="Middle", border_color="#a0a0a0",
                   padding="5pt")
    cont = _sub(_sub(c0, "TablixCell"), "CellContents")
    _build_textbox(cont, "Mx_ColHdr", f"=Fields!{col_f}.Value",
                   bold=True, bg=hdr_bg, fg=hdr_fg, text_align="Center",
                   vertical_align="Middle", border_color="#a0a0a0",
                   padding="5pt")

    r1 = _sub(rows_el, "TablixRow")
    _sub(r1, "Height", f"{0.25 + 0.15 * max(0, len(cells) - 1):.2f}in")
    c1 = _sub(r1, "TablixCells")
    cont = _sub(_sub(c1, "TablixCell"), "CellContents")
    _build_textbox(cont, "Mx_RowHdr", f"=Fields!{row_f}.Value",
                   bold=True, text_align="Left", vertical_align="Middle",
                   border_color="#d0d0d0", padding="4pt")
    if len(cells) == 1:
        expr = f"=Sum(Fields!{cells[0]}.Value)"
    else:
        parts = [f'"{c.replace("_", " ")}: " & Sum(Fields!{c}.Value)'
                 for c in cells]
        expr = "=" + " & vbCrLf & ".join(parts)
    cont = _sub(_sub(c1, "TablixCell"), "CellContents")
    _build_textbox(cont, "Mx_Cell", expr, text_align="Right",
                   vertical_align="Middle", border_color="#d0d0d0",
                   padding="4pt")

    ch = _sub(t, "TablixColumnHierarchy")
    chm = _sub(ch, "TablixMembers")
    _sub(chm, "TablixMember")                      # static row-header column
    dyn = _sub(chm, "TablixMember")
    g = _sub(dyn, "Group")
    g.set("Name", "MxColG")
    _sub(_sub(g, "GroupExpressions"), "GroupExpression",
         f"=Fields!{col_f}.Value")
    se = _sub(dyn, "SortExpressions")
    s = _sub(se, "SortExpression")
    _sub(s, "Value", f"=Fields!{col_f}.Value")

    rh = _sub(t, "TablixRowHierarchy")
    rhm = _sub(rh, "TablixMembers")
    # NOTE: the column-header member carries NO KeepWithGroup/RepeatOnNewPage.
    # On a self-contained matrix those properties make SSRS reserve a
    # follow-on page for "repeated headers" that never materializes ->
    # trailing blank page (engine-measured). The dynamic column header
    # already repeats naturally with horizontal pagination.
    _sub(rhm, "TablixMember")
    dynr = _sub(rhm, "TablixMember")
    gr = _sub(dynr, "Group")
    gr.set("Name", "MxRowG")
    _sub(_sub(gr, "GroupExpressions"), "GroupExpression",
         f"=Fields!{row_f}.Value")
    ser = _sub(dynr, "SortExpressions")
    sr = _sub(ser, "SortExpression")
    _sub(sr, "Value", f"=Fields!{row_f}.Value")

    _sub(t, "DataSetName", ds)
    _sub(t, "Top", "0.10in")
    _sub(t, "Left", "0.05in")
    _sub(t, "Height", "0.55in")
    _sub(t, "Width", "3.3in")
    return t


def _build_body(report: ParsedReport, main: Optional[DataQuery]) -> ET.Element:
    """Build the <Body> for a tabular grouped-card report. Letter /
    certificate reports use _build_certificate_body via the caller.

    Cover Rectangle and Tablix MUST be stacked vertically (cover at
    Top=0, Tablix at Top = cover_height + 0.1in). If they share the
    same Top, SSRS's PDF renderer puts both on page 1 -- you get the
    cover text AND the first data card overlapping/interleaved on
    page 1. The PageBreak Start on the Tablix's outer group still
    fires, but only AFTER the visual overlap is rendered.
    """
    body = ET.Element(_q("Body"))
    items = _sub(body, "ReportItems")
    cover_top = 0.0
    next_top = 0.10  # default if no cover

    cover = _build_cover_page(report)
    if cover is not None:
        items.append(cover)
        try:
            cover_h = float(cover.attrib.pop("data-rect-height-in", "0"))
        except Exception:
            cover_h = 0.0
        next_top = cover_top + cover_h + 0.10

    if main is not None:
        # Nested master-detail (parsed Oracle <group> chain, e.g. County ->
        # Complaint -> Action) renders via the deterministic group-tree Tablix;
        # a genuinely grouped/card report keeps the wallet-card path; a FLAT
        # table (no group break, no detail sub-table) renders as a plain column
        # grid -- the card path would otherwise collapse every row but the
        # first via =First(), silently dropping the report's data.
        _mspec = getattr(report, "_matrix_spec", None)
        _is_matrix = bool(_mspec and _mspec.get("dominant"))
        if _is_matrix:
            tablix = _build_matrix_tablix(report, _mspec)
        elif _is_nested_master_detail(main):
            tablix = _build_nested_group_tablix(report, main)
        elif _is_grouped_card_report(main, report):
            tablix = _build_grouped_card_tablix(report, main)
        else:
            tablix = _build_tablix(report, main)
        # The Tablix builder writes its own provisional Top -- replace it
        # so the Tablix sits BELOW the cover in body coordinates.
        for t in tablix.findall(_q("Top")):
            tablix.remove(t)
        _sub(tablix, "Top", f"{next_top:.2f}in")
        items.append(tablix)
    else:
        _is_matrix = False

    # Body height encloses cover + Tablix bottom + small pad. A matrix
    # grows BOTH directions at render time from a tiny design footprint —
    # the 9in floor would leave trailing blank pages (engine-verified).
    body_height_in = (round(next_top + 1.0, 2) if _is_matrix
                      else max(9.0, next_top + 2.0))
    _sub(body, "Height", f"{body_height_in}in")
    _sub(body, "Style")
    return body


def _build_page(report: ParsedReport, page_height_in: float = 11.0,
                footer_on_first_page: bool = True,
                signature_in_footer: bool = True) -> ET.Element:
    """Page-level dimensions + optional header/footer."""
    page = ET.Element(_q("Page"))

    # PageHeader: title block extracted from layout.
    title_lines = _resolved_title_lines(report)
    if title_lines:
        header_h = 0.20 + 0.22 * len(title_lines) + 0.30
        ph = _sub(page, "PageHeader")
        _sub(ph, "Height", f"{header_h:.2f}in")
        _sub(ph, "PrintOnFirstPage", "true")
        _sub(ph, "PrintOnLastPage", "true")
        ph_items = _sub(ph, "ReportItems")
        title_expr = (
            '="' + '" & vbCrLf & "'.join(
                ln.replace('"', '""') for ln in title_lines
            ) + '"'
        )
        _build_textbox(
            ph_items, "Tb_PageTitle", title_expr,
            bold=True, font_size="11pt",
            fg="#03047e",
            text_align="Center", vertical_align="Middle",
            border_color="#ffffff", padding="2pt",
            font_family="Courier New",
        )
        title_tb = ph_items[-1]
        _sub(title_tb, "Top", "0.05in")
        _sub(title_tb, "Left", "0.1in")
        _sub(title_tb, "Width", "7.3in")
        _sub(title_tb, "Height", f"{0.22 * len(title_lines):.2f}in")
        meta_y = 0.10 + 0.22 * len(title_lines)
        _build_textbox(
            ph_items, "Tb_RunOn",
            '="Report run on: " & Format(Globals!ExecutionTime, "MM/dd/yyyy h:mm tt")',
            font_size="9pt", fg="#444444",
            text_align="Left", vertical_align="Middle",
            border_color="#ffffff", padding="2pt",
        )
        run_tb = ph_items[-1]
        _sub(run_tb, "Top", f"{meta_y:.2f}in")
        _sub(run_tb, "Left", "0.1in")
        _sub(run_tb, "Width", "4.0in")
        _sub(run_tb, "Height", "0.20in")
        _build_textbox(
            ph_items, "Tb_PageNum",
            '="Page " & Globals!PageNumber & " of " & Globals!TotalPages',
            font_size="9pt", fg="#444444",
            text_align="Right", vertical_align="Middle",
            border_color="#ffffff", padding="2pt",
        )
        pg_tb = ph_items[-1]
        _sub(pg_tb, "Top", f"{meta_y:.2f}in")
        _sub(pg_tb, "Left", "4.2in")
        _sub(pg_tb, "Width", "3.2in")
        _sub(pg_tb, "Height", "0.20in")
    else:
        ph = _sub(page, "PageHeader")
        _sub(ph, "Height", "0.25in")
        _sub(ph, "PrintOnFirstPage", "true")
        _sub(ph, "PrintOnLastPage", "true")

    pf = _sub(page, "PageFooter")
    _sub(pf, "Height", f"{_PAGE_FOOTER_HEIGHT_IN}in")
    # Suppress the footer on page 1 when page 1 is a cover sheet --
    # otherwise the signature image carried in the footer prints on
    # the cover, where it does not belong.
    _sub(pf, "PrintOnFirstPage", "true" if footer_on_first_page else "false")
    _sub(pf, "PrintOnLastPage", "true")
    # Footer signature is a FALLBACK only -- skip it when the signature
    # blob is already placed inside the body at its real layout position.
    sig_q = _pick_signature_query(report) if signature_in_footer else None
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
            _sub(img, "Value",
                 '=First(Fields!' + sf + '.Value, "' + _safe(ds_name) + '")')
        else:
            _sub(img, "Value", '=""')
        _sub(img, "MIMEType", "image/png")
        _sub(img, "Sizing", "FitProportional")
        _sub(img, "Top", "0.05in")
        _sub(img, "Left", "0.05in")
        _sub(img, "Height", "0.45in")
        _sub(img, "Width", "1.5in")

    _sub(page, "PageHeight", f"{page_height_in:.2f}in")
    _sub(page, "PageWidth", "8.5in")
    _sub(page, "LeftMargin", f"{_PAGE_HMARGIN_IN}in")
    _sub(page, "RightMargin", f"{_PAGE_HMARGIN_IN}in")
    _sub(page, "TopMargin", f"{_PAGE_MARGIN_IN}in")
    _sub(page, "BottomMargin", f"{_PAGE_MARGIN_IN}in")
    return page


def _build_code() -> ET.Element:
    """Minimal <Code> element. SSRS accepts an empty body."""
    return ET.Element(_q("Code"))


def _strip_empty_required_containers(root: ET.Element) -> None:
    """Remove empty containers SSRS rejects with 'has incomplete content'."""
    must_have_child = {
        "ReportItems", "CellContents", "DataSources", "DataSets",
        "ReportParameters",
    }
    for parent in root.iter():
        for child in list(parent):
            tag = child.tag
            if not isinstance(tag, str):
                continue
            local = tag.split("}", 1)[-1]
            if local in must_have_child and len(list(child)) == 0:
                parent.remove(child)


def _build_embedded_images(report) -> Optional[ET.Element]:
    """<EmbeddedImages> from the report's parsed/uploaded image assets.

    Also registers ``report._image_assets`` (UPPER id -> RDL EmbeddedImage
    name) so the layout emitters can reference the right image. Hex data
    (Oracle binaryData) is converted to the base64 the RDL schema wants.
    """
    import base64 as _b64
    assets = {}
    el = ET.Element(_q("EmbeddedImages"))
    for im in (getattr(report, "embedded_images", None) or []):
        hx = (getattr(im, "hex_data", "") or "").strip()
        if not hx:
            continue
        try:
            b64 = _b64.b64encode(bytes.fromhex(hx)).decode("ascii")
        except ValueError:
            continue
        rdl_name = _safe(im.id) or f"Img{len(assets)}"
        emb = _sub(el, "EmbeddedImage")
        emb.set("Name", rdl_name)
        _sub(emb, "MIMEType", im.mime_type or "image/gif")
        _sub(emb, "ImageData", b64)
        assets[im.id.upper()] = rdl_name
        if getattr(im, "wildcard", False):
            assets["*"] = rdl_name
    report._image_assets = assets
    return el if len(el) else None


def _build_report_root(report: ParsedReport, target_db: str = "oracle") -> ET.Element:
    """Root <Report> element with all children in SSRS schema order."""
    _augment_parameters_from_binds(report)
    # Matrix pre-pass MUST run before datasets are built (it may add the
    # cross-product dimension/measure columns to the bound query).
    try:
        _prepare_matrix(report)
    except Exception:  # noqa: BLE001 -- matrix support must never block
        report._matrix_spec = None
    root = ET.Element(_q("Report"))
    root.append(_build_data_sources(target_db=target_db))
    datasets_el = _build_data_sets(report, target_db=target_db)
    # Append the synthetic formula-resolution dataset so CF_/CP_ tokens
    # can bind to a real field the user wires up on Refresh Fields.
    formula_ds = _build_formula_dataset(report, target_db=target_db)
    if formula_ds is not None:
        datasets_el.append(formula_ds)
    root.append(datasets_el)
    # Embedded images (state seals / logos / watermarks) -- parsed from the
    # Oracle export's binaryData or uploaded by the user. Must be built
    # BEFORE the body so the layout emitters see report._image_assets.
    emb_el = _build_embedded_images(report)
    if emb_el is not None:
        root.append(emb_el)
    rps = _build_report_parameters(report)
    if rps is not None:
        root.append(rps)

    main = _pick_main_query(report)
    # Generic routing by detected report kind -- same heuristic the
    # preview uses, so the RDL matches what the user sees in the app.
    try:
        kind = _detect_report_kind(report)
    except Exception:
        kind = "tabular_details"

    # A genuine Oracle nested <group> chain (master-detail: County -> Complaint
    # -> Action) routes to the deterministic nested-group Tablix -- BUT ONLY
    # when the report isn't a letter/certificate. Letters can carry a
    # data-group chain too (e.g. a letter that groups its addressees by
    # Site/Visit), yet their deliverable is a per-record document, not a data
    # grid; those must keep the per-record path. So the nested override applies
    # to tabular reports only. ``deep`` (>=3 levels, e.g. County/Complaint/
    # Action) is the unambiguous master-detail signal we also honor when the
    # kind heuristic fell back to certificate on a thin/absent layout.
    # A DOMINANT cross-tab outranks every other archetype: the matrix IS
    # the report (e.g. the classic videosales demo). Mixed layouts where a
    # matrix is just one frame among many keep their existing path.
    _mspec0 = getattr(report, "_matrix_spec", None)
    if _mspec0 and _mspec0.get("dominant") and main is not None:
        body = _build_body(report, main)  # matrix-aware
        root.append(body)
        _sub(root, "Width", "7.0in")
        # Collapse the empty whitespace the dynamic matrix leaves around
        # itself -- without this SSRS emits a trailing blank page from the
        # design-time slack below/right of the tiny matrix footprint
        # (engine-measured: page 2 had zero words before this).
        _rdsub(root, "ConsumeContainerWhitespace", "true")
        root.append(_build_page(report, page_height_in=11.0,
                                footer_on_first_page=True,
                                signature_in_footer=False))
        root.append(_build_code())
        _sub(root, "Language", "en-US")
        _rdsub(root, "DrawGrid", "true")
        _rdsub(root, "GridSpacing", "0.083333in")
        _strip_empty_required_containers(root)
        return root

    _nested = main is not None and _is_nested_master_detail(main)
    _deep = _nested and len(_flatten_group_chain(main.groups)) >= 3
    if _nested and (kind == "tabular_details" or _deep):
        body = _build_body(report, main)  # _build_body picks the nested Tablix
        root.append(body)
        _sub(root, "Width", "7.5in")
        root.append(_build_page(report, page_height_in=11.0,
                                footer_on_first_page=True,
                                signature_in_footer=False))
        root.append(_build_code())
        _sub(root, "Language", "en-US")
        _rdsub(root, "DrawGrid", "true")
        _rdsub(root, "GridSpacing", "0.083333in")
        _strip_empty_required_containers(root)
        return root

    signature_in_body = False
    if kind in ("certificate", "letter") and main is not None:
        # One page per record (permits, letters, single-document reports).
        body = _build_per_record_body(report, main)
        # Grow PageHeight so ONE record plus the page chrome (top +
        # bottom margins + header + footer) fits on a single physical
        # page. Budgeting only max(11, content) left the wallet cards
        # at the foot of the certificate spilling onto a second page.
        try:
            req = float(body.attrib.pop("data-required-page-height-in", "0"))
        except Exception:
            req = 0.0
        signature_in_body = body.attrib.pop("data-signature-in-body", "") == "1"
        chrome = (2 * _PAGE_MARGIN_IN
                  + _page_header_height(report)
                  + _PAGE_FOOTER_HEIGHT_IN)
        page_height = max(11.0, req + chrome + 0.2)
    elif kind == "tabular_details":
        # Multi-section dashboard (several independent tables down one page,
        # each bound to a different query) -> stack one Tablix per section so
        # they ALL render. Falls back to the single-main grouped Tablix when
        # the report is a single data table.
        sections = None
        try:
            sections = _detect_multi_section(report)
        except Exception:
            sections = None
        if sections:
            body = _build_multi_section_body(report, sections)
        else:
            body = _build_body(report, main)
        page_height = 11.0
    else:
        body = _build_body(report, main)
        page_height = 11.0

    root.append(body)
    # Use the body's computed width (derived from Oracle source layout)
    # so the content span isn't clipped. Falls back to 7.5in for reports
    # without wide frame children.
    _report_w = float(body.attrib.pop("data-body-width-in", "7.5"))
    _sub(root, "Width", f"{_report_w}in")
    # Page 1 is a cover sheet for certificate / letter reports, so the
    # signature footer must not print on it. And when the signature blob
    # is rendered inside the body, drop the footer copy entirely.
    root.append(_build_page(
        report, page_height_in=page_height,
        footer_on_first_page=(kind not in ("certificate", "letter")),
        signature_in_footer=not signature_in_body,
    ))
    root.append(_build_code())
    _sub(root, "Language", "en-US")
    _rdsub(root, "DrawGrid", "true")
    _rdsub(root, "GridSpacing", "0.083333in")
    _strip_empty_required_containers(root)
    return root


def _ensure_layout_images_emitted(root: ET.Element, report) -> None:
    """Safety net: every asset-backed layout image placeholder must end up
    in the RDL. The per-record/frame builders emit them in place; flat /
    tabular body builders don't walk layout image objects, so any leftover
    placeholder (a logo on a plain table report) is appended BODY-DIRECT
    at its absolute layout coordinates here."""
    assets = getattr(report, "_image_assets", None) or {}
    if not assets:
        return
    body = root.find(_q("Body"))
    if body is None:
        return
    referenced = {
        (img.findtext(_q("Value")) or "")
        for img in root.iter(_q("Image"))
        if (img.findtext(_q("Source")) or "") == "Embedded"
    }
    ri = body.find(_q("ReportItems"))
    body_h_el = body.find(_q("Height"))
    try:
        body_h = float((body_h_el.text or "0").replace("in", "")) \
            if body_h_el is not None else 0.0
    except ValueError:
        body_h = 0.0
    max_bottom = body_h
    counter = 0

    def walk(g):
        nonlocal max_bottom, counter
        for f in (getattr(g, "fields", None) or []):
            if getattr(f, "kind", "") != "image":
                continue
            ref = _image_asset_for(f, report)
            if not ref or ref in referenced:
                continue
            nonlocal_ri = body.find(_q("ReportItems"))
            target = nonlocal_ri if nonlocal_ri is not None \
                else _sub(body, "ReportItems")
            counter += 1
            x = float(getattr(f, "x", 0.0) or 0.0)
            y = float(getattr(f, "y", 0.0) or 0.0)
            w = float(getattr(f, "width", 0.0) or 0.0) or 1.0
            h = float(getattr(f, "height", 0.0) or 0.0) or 1.0
            _emit_embedded_image(target, f"BodyImg_{counter}", ref,
                                 max(0.0, x), max(0.0, y), w, h)
            referenced.add(ref)
            max_bottom = max(max_bottom, y + h)
        for c in (getattr(g, "children", None) or []):
            walk(c)

    for lg in (getattr(report, "layout", None) or []):
        walk(lg)
    if counter and body_h_el is not None and max_bottom > body_h:
        body_h_el.text = f"{max_bottom + 0.1:.2f}in"


def generate_rdl(report: ParsedReport, target_db: str = "oracle") -> str:
    """Return a complete RDL XML document as a string."""
    target_db = (target_db or "oracle").lower()
    if target_db not in ("oracle", "sqlserver"):
        target_db = "oracle"
    root = _build_report_root(report, target_db=target_db)
    _ensure_layout_images_emitted(root, report)
    # Publish-safety nets, in order: (1) repair dangling Fields! refs
    # (params / formulas / other-dataset columns / page builtins bound as
    # raw fields by secondary builders), THEN (2) scope any remaining
    # body-direct Fields! refs. Order matters: repairs first so the scoper
    # only sees genuinely-in-scope refs.
    _repair_dangling_field_refs(root, report)
    _scope_body_direct_field_refs(root, report)
    # Fidelity: stamp <Format> onto field values that carried an Oracle
    # formatMask, so currency / dates / thousands render like the original.
    _apply_field_formats(root, report)
    try:
        ET.indent(root, space="  ")
    except AttributeError:
        pass
    body = ET.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="utf-8"?>\n' + body
