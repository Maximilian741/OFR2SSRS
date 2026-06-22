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
from converter.preview.html_mockup import (
    _is_positional_document_packet as _is_doc_packet,
    _group_columnar_repeating as _frame_has_columnar_table,
    _is_single_record_form as _is_single_record_form,
    _has_cover_page as _has_cover_page,
    _is_conditional_alert_frame as _is_conditional_alert_frame,
)
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
        #
        # Oracle identifiers legally include '$' and '#' (e.g. the classic
        # legacy columns EMP#, DEPT#, INV#, or AMT$). Those chars MUST be in
        # the char class here: if a bare "emp#" slips through to the alias
        # deriver below it becomes "emp# AS EMP", renaming the result column
        # to EMP -- but the <Field>'s DataField keeps the raw "EMP#" (see
        # _safe's contract), so the field binds to a column that no longer
        # exists and renders BLANK. Treating emp#/amt$ as the bare column
        # they are means NO alias, Oracle returns EMP#/AMT$ verbatim, and the
        # DataField matches. (Leading char stays letter/_ -- Oracle forbids a
        # leading $ or #.)
        if re.fullmatch(
            r"\s*[A-Za-z_][A-Za-z0-9_$#]*(\.[A-Za-z_][A-Za-z0-9_$#]*)?\s*",
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


def _ssrs_text_align(align: str):
    """Map an Oracle field alignment to an SSRS <TextAlign>, or None when the
    field has no EXPLICIT alignment. None lets SSRS use 'General' (numbers/dates
    right, text left -- which matches Oracle's own datatype default), so we only
    override when the report author explicitly chose start/end/center. Dropping
    an explicit alignment is a real 1:1 miss -- e.g. a CENTERED header would
    otherwise render left under General."""
    a = (align or "").strip().lower()
    if a in ("end", "right"):
        return "Right"
    if a in ("center", "centre", "middle"):
        return "Center"
    if a in ("start", "left"):
        return "Left"
    return None


def _collect_layout_columns(report: ParsedReport, query_name: str) -> List[str]:
    """Walk the layout tree, return ordered column names bound to query_name.

    Layout repeating-frames carry the GROUP name (G_FOO) in source_query;
    queries are named Q_FOO. We match either an exact hit or the Oracle
    Q_/G_ convention via the shared suffix (see _query_matches_layout_ref).
    """
    cols: List[tuple] = []  # (x, y, src)
    seen: Set[str] = set()
    # Build a stub DataQuery so we can reuse the suffix-matching helper.
    target_stub = DataQuery(name=query_name or "")

    def walk(group: LayoutGroup, matched: bool = False) -> None:
        if group.source_query and _query_matches_layout_ref(
            target_stub, group.source_query
        ):
            matched = True
        if matched:
            # Collect this record's data fields, INCLUDING those in nested plain
            # frames (e.g. a 2nd-row M_ROW_2 carrying Permitee / Type-of-Operation
            # columns) -- otherwise a multi-row record silently loses columns.
            for f in group.fields:
                src = (f.source or "").strip()
                if src and src not in seen:
                    seen.add(src)
                    cols.append((float(getattr(f, "x", 0) or 0.0),
                                 float(getattr(f, "y", 0) or 0.0), src))
        for child in group.children:
            ckind = (getattr(child, "kind", "") or "").lower()
            # Do NOT pull a DIFFERENT query's nested repeating sub-list into this
            # record's columns (that's a separate detail band / linked child).
            if (matched and ckind == "repeating_frame" and child.source_query
                    and not _query_matches_layout_ref(target_stub, child.source_query)):
                continue
            walk(child, matched)

    for g in report.layout or []:
        walk(g)
    # Order columns left-to-right by layout x (then y) so they appear in the
    # report's real column order, not field-declaration order. Stable sort keeps
    # insertion order for ties / x==0 synthetic columns.
    cols.sort(key=lambda c: (round(c[0], 2), round(c[1], 2)))
    return [src for _x, _y, src in cols]


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
    # Deep nested master-detail: the heaviest query can be an INNER detail (e.g.
    # a per-course query carrying the bulk of the SELECT) while the report's
    # primary entity is the OUTERMOST group's query. When the heaviest query is a
    # nested child (parent_group set) AND a ROOT master exists that is itself a
    # genuine nested-MD master (its own group chain breaks), bind to that root so
    # the body iterates per top-level record, not per innermost detail. Gated on
    # the root being nested-MD so a flat report whose detail IS the subject is
    # never rebound.
    if (getattr(heaviest, "parent_group", "") or "").strip():
        root_masters = [q for q in queries
                        if not (getattr(q, "parent_group", "") or "").strip()
                        and _is_nested_master_detail(q)]
        if root_masters:
            return max(root_masters,
                       key=lambda q: (len(q.items or []), -queries.index(q)))
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

def _empty_query_placeholder(query) -> str:
    """A query with COLUMNS but no SQL came from a NON-SQL source (a text/CSV/
    XML pluggable data source). Emit a COMMENTED scaffold -- the expected
    columns + a starter SELECT -- so the dataset is easy to wire in Report
    Builder, instead of a bare '-- empty query' the user has to decode."""
    cols = [(getattr(it, "name", "") or "").strip()
            for it in (getattr(query, "items", None) or [])
            if (getattr(it, "name", "") or "").strip()]
    if not cols:
        return f"-- empty query for {getattr(query, 'name', 'dataset')}"
    shown = ", ".join(cols[:16]) + (" /* ...more... */" if len(cols) > 16 else "")
    return (
        "-- This dataset originally read from a NON-SQL source (a text/CSV/XML\n"
        "-- pluggable data source). It has no relational query. To make it\n"
        "-- return data: point this dataset at your data source in Report\n"
        "-- Builder, OR replace the lines below with a real query.\n"
        f"-- Columns the report expects: {shown}\n"
        f"-- SELECT {shown} FROM <your_source>"
    )


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
            cmd_text = _empty_query_placeholder(query)
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
                    _sub(qp, "Value", f"=Parameters!{_safe(canon)}.Value")
                else:
                    _sub(qp, "Value", "=Nothing")
    else:
        # T-SQL path: existing behavior. Prefer .tsql, fall back to .sql.
        cmd_text = (query.tsql or query.sql or "").strip()
        if not cmd_text:
            cmd_text = _empty_query_placeholder(query)
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
                    _sub(qp, "Value", f"=Parameters!{_safe(canon)}.Value")
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
    # Oracle parameters with a declared initialValue that are NOT query binds
    # (never referenced in any dataset's SQL) are DISPLAY CONSTANTS -- a report
    # title's division/agency sub-line, an address-type code, etc. Honor their
    # Oracle default so the value actually PRINTS (e.g. the centered subtitle
    # "DEQ Air Resources Management Bureau"), instead of the typed-NULL =Nothing
    # a query-filter param needs. Query-filter params are LEFT on =Nothing so the
    # (:P IS NULL OR col=:P) guard still widens to all rows and the load-bearing
    # param-prompt bypass is untouched. Generic: the value comes straight from
    # the XML's initialValue, nothing report-specific.
    _query_binds: Set[str] = set()
    for q in (report.queries or []):
        for b in _detect_query_parameters(getattr(q, "tsql", "") or ""):
            _query_binds.add(b.upper())
        for b in _detect_oracle_bind_vars(getattr(q, "sql", "") or ""):
            _query_binds.add(b.upper())
    # Parameters that are the SOURCE of a printed layout field -- e.g. a title's
    # division sub-line F_DIVISION bound to &P_DIVISION. ONLY these get their
    # Oracle initialValue honored as a default (so the value visibly prints);
    # system/path params (P_AS_PATH) and query binds are left on =Nothing.
    _display_sources: Set[str] = set()

    def _collect_sources(g):
        for f in (getattr(g, "fields", None) or []):
            if (getattr(f, "kind", "") or "") == "field":
                s = (getattr(f, "source", "") or "").lstrip("&:").strip().upper()
                if s:
                    _display_sources.add(s)
        for c in (getattr(g, "children", None) or []):
            _collect_sources(c)
    for _t in (report.layout or []):
        _collect_sources(_t)
    root = ET.Element(_q("ReportParameters"))
    for p in report.parameters:
        rp = _sub(root, "ReportParameter")
        # SSRS parameter Name must be a valid identifier. An Oracle param name
        # can legally contain $ or # (e.g. P_BAL$) -- _safe maps it to a valid
        # SSRS Name. References below use _safe(name) too, so they MATCH (a raw
        # decl + _safe'd refs would dangle). No-op for the usual P_X names.
        rp.set("Name", _safe(p.name))
        ptype = _ssrs_param_type(p)
        # SSRS 2008/01 schema element order:
        #   DataType -> Nullable -> DefaultValue -> AllowBlank
        #            -> Prompt -> Hidden
        _sub(rp, "DataType", ptype)
        # Nullable for EVERY type (String included) so a typed-NULL default is
        # always valid and SSRS never has to ask for a value.
        _sub(rp, "Nullable", "true")
        # Every parameter gets a CONCRETE <DefaultValue> (never an empty
        # <Value/>) so SSRS never prompts: a query-filter param gets the typed-
        # NULL =Nothing default (widen-to-all via the SQL guards); a display-
        # constant param gets its Oracle initialValue so it actually prints.
        # See the two branches below.
        dv = _sub(rp, "DefaultValue")
        dv_values = _sub(dv, "Values")
        _iv = (getattr(p, "initial_value", "") or "").strip()
        _is_bind = (
            _safe(p.name).upper() in _query_binds
            or p.name.upper() in _query_binds
            or ("P_" + p.name).upper() in _query_binds
            or p.name.upper().lstrip("P_") in _query_binds
        )
        _is_display = (_safe(p.name).upper() in _display_sources
                       or p.name.upper() in _display_sources)
        if _iv and not _is_bind and _is_display and ptype == "String":
            # A display-constant string parameter shown by a printed layout
            # field: emit its Oracle default verbatim as a literal (a leading
            # '=' is escaped so an unusual default is never misread as an SSRS
            # expression) so the value actually prints.
            _lit = ('="' + _iv.replace('"', '""') + '"') if _iv.startswith("=") else _iv
            _sub(dv_values, "Value", _lit)
        else:
            # LOAD-BEARING -- never emit an EMPTY <Value/> default. Query-filter
            # params (and any param with no Oracle default) keep a CONCRETE
            # =Nothing (typed-NULL) default. An empty <Value/> is NOT a usable
            # default: when a dataset's query parameter maps to a report
            # parameter whose default is empty, SSRS pops the "Define Query
            # Parameters" dialog at Refresh-Fields time and then fails ("missing
            # a value"). A real =Nothing default lets the user upload -> repoint
            # the shared data source -> Refresh Fields -> enter creds, with NO
            # parameter prompt. The Oracle SQL's (:P IS NULL OR col = :P) /
            # NVL(:P, ...) guards handle the NULL cleanly, so an unfilled
            # parameter widens the result set instead of erroring. This invariant
            # must hold for EVERY query-bound parameter of EVERY report.
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
                   italic: bool = False,
                   underline: bool = False,
                   writing_mode: Optional[str] = None,
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
    if italic:
        _sub(style, "FontStyle", "Italic")
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
    # WritingMode follows the Padding* elements in the SSRS Style sequence; a
    # rotated field (Oracle rotationAngle, e.g. a sideways window-envelope
    # address) sets Rotate270 so the engine turns the text 90deg CCW.
    if writing_mode:
        _sub(tb_style, "WritingMode", writing_mode)
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


def _column_captions(report, columns):
    """Map a flat-table column source -> its REAL Oracle column-header label text
    (e.g. PERM_NAME -> "Permit", CF_TYPE_OPERATION -> "Type of Operation") so the
    table reads like the report instead of humanized field names. The header band
    sits ABOVE the detail row and each header aligns in x with its column; for a
    2-row-per-record table the header's y-rank tracks the field's y-rank. Falls
    back to the humanized name when no header label aligns. Structural, never
    keyed on a report name."""
    from collections import defaultdict
    try:
        field_geo, label_geo = _layout_geometry_index(report)
        _row, _wrap, row_y = _detail_band_fields(report)
    except Exception:  # noqa: BLE001 -- captions must never break the table
        return {}
    if row_y is None:
        row_y = 1e9
    # header-band labels: texts ABOVE the detail row, not "Label:" value pairs.
    # Page-continuation markers ("(continued)") ride at the very top of a broken
    # page and otherwise win the x-bucket over the real caption ("I") because they
    # sort higher -- never let one stand in for a column header.
    _CONT = {"(continued)", "continued", "(cont.)", "(cont)", "cont.", "(more)"}
    hbuckets = defaultdict(list)  # round(x) -> [(y, text)]
    for t, lx, ly, _b in (label_geo or []):
        s = (t or "").strip()
        # A column caption is a short static label: never a page-continuation
        # marker, and never a line carrying an Oracle lexical &TOKEN (those are
        # the report TITLE / criteria line that happens to sit above a column,
        # e.g. "...Logsheets for &REPORT_VEHICLE_TYPE" -- it must not displace the
        # real caption "Vehicle Type" beneath it).
        if (s and not s.endswith(":") and ly < row_y - 0.02
                and s.lower() not in _CONT
                and not re.search(r"&[A-Za-z_<]", s)):
            hbuckets[round(lx)].append((ly, s))
    cbuckets = defaultdict(list)  # round(x) -> [(y, col)]
    for col in columns:
        fg = field_geo.get(col.upper())
        if fg:
            cbuckets[round(fg[0])].append((fg[1], col))
    caps = {}
    for cx, cols_here in cbuckets.items():
        if not hbuckets:
            break
        hx = min(hbuckets.keys(), key=lambda h: abs(h - cx))
        if abs(hx - cx) > 1:
            continue
        heads = sorted(hbuckets[hx])          # header rows top-to-bottom
        for rank, (_cy, col) in enumerate(sorted(cols_here)):
            if rank < len(heads):
                caps[col.upper()] = heads[rank][1]
    return caps


def _is_neutral_dark(c):
    """True for a near-neutral DARK gray (r~=g~=b, low luminance) -- an Oracle
    row-striping / design fill (e.g. gray8 #141414, transparent pattern) that the
    real report does NOT print as a header band. Lets a flat-table header render
    PLAIN instead of a near-black bar, without touching genuinely colored bands
    (navy #00008B, darkgreen #006400 -- those have a high channel spread)."""
    if not c or not isinstance(c, str):
        return False
    s = c.strip().lstrip("#")
    if len(s) != 6:
        return False
    try:
        r, g, b = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    except ValueError:
        return False
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    return (max(r, g, b) - min(r, g, b)) <= 28 and lum < 90


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
    # Header styling from the resolved palette, not an invented slate-blue band.
    _pal = _resolve_palette(report)
    header_bg = _pal.get("band_bg") or "#4a6a8a"
    header_fg = _pal.get("band_fg") or "#ffffff"
    detail_bg = "#ffffff"
    alt_row_bg = "#f5f7fa"
    if main_group is not None:
        gb = getattr(main_group, "background_color", "")
        fg = getattr(main_group, "foreground_color", "")
        if gb:
            header_bg = gb
        if fg:
            header_fg = fg
    # A PLAIN report (no real source band) OR a near-neutral dark-gray fill (an
    # Oracle row-striping/design artifact, not a real header band) renders a plain
    # black-on-white header -- never an invented or near-black band.
    if (not _pal.get("themed", True)) or _is_neutral_dark(header_bg):
        header_bg = "#ffffff"
        header_fg = "#111111"

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
    # Target table width: ~9in for a portrait report (unchanged), but a wide
    # LANDSCAPE page (Oracle content spanning >portrait) lets the columns use
    # the full page rather than compressing toward the 0.6in floor. max(9.0,..)
    # keeps portrait byte-identical -- only a landscape page widens the target.
    # Subtract the tablix's own 0.25in Left indent (set below) + a small safety
    # so columns + indent fit inside the report body width and the last column
    # doesn't spill onto a horizontal-continuation page.
    _target_w = max(9.0, _page_width_for(report) - 2 * _PAGE_HMARGIN_IN - 0.30)
    if _ncol <= 6:
        _colw = max(1.5, round(_target_w / _ncol, 2)) if _target_w > 9.0 else 1.5
    else:
        _colw = max(0.6, round(_target_w / _ncol, 2))
    # Per-column widths from the Oracle layout: a wide description column stays
    # wide and narrow code columns stay narrow, instead of one uniform width
    # for all. Use each field's own width; scale DOWN proportionally only if the
    # set overflows the target (never stretch -- preserve Oracle's widths when
    # they fit); floor at 0.5in for legibility. Falls back to the uniform _colw
    # when ANY column has no layout field width (mixed/synthetic columns).
    _ora_w = {}
    _ora_h = {}
    try:
        for _y, _x, _d, _lf in _layout_fields_in_order(report):
            _s = (getattr(_lf, "source", "") or "").upper()
            _w = float(getattr(_lf, "width", 0) or 0)
            _h = float(getattr(_lf, "height", 0) or 0)
            if _s and _s not in _ora_w and _w > 0:
                _ora_w[_s] = _w
            if _s and _s not in _ora_h and _h > 0:
                _ora_h[_s] = _h
    except Exception:  # noqa: BLE001 -- widths must never break the table
        _ora_w, _ora_h = {}, {}
    # Detail row height: the TALLEST Oracle detail field (so a field Oracle drew
    # 0.6in tall keeps that height) -- floored at the default 0.28in (so the
    # corpus, whose fields are <=0.28, is UNCHANGED) and capped at 2in to ignore
    # a stray giant. CanGrow still grows the row for multi-line data beyond this.
    _detail_h = min(2.0, max(0.28, max(
        (_ora_h.get(c.upper(), 0.0) for c in columns), default=0.0)))
    _widths = [_ora_w.get(c.upper(), 0.0) for c in columns]
    if all(w > 0 for w in _widths) and _widths:
        _sum = sum(_widths)
        _scale = min(1.0, _target_w / _sum) if _sum > 0 else 1.0
        _per_col = [max(0.5, round(w * _scale, 2)) for w in _widths]
        # The 0.5in floor + per-column rounding can push the total a hair past the
        # usable width and spill the last column onto a 2nd page. Trim that small
        # overflow from the columns ABOVE the floor, proportionally, so the whole
        # table fits one page (never below the 0.5in floor).
        _over = sum(_per_col) - _target_w
        if _over > 0.01:
            _slack = [(i, w - 0.5) for i, w in enumerate(_per_col) if w - 0.5 > 0.01]
            _slack_total = sum(s for _i, s in _slack)
            if _slack_total > 0:
                for _i, _s in _slack:
                    _per_col[_i] = round(_per_col[_i] - _over * (_s / _slack_total), 2)
    else:
        _per_col = [_colw] * len(columns)
    for _w in _per_col:
        c = _sub(cols_el, "TablixColumn")
        _sub(c, "Width", f"{_w}in")

    rows_el = _sub(body, "TablixRows")

    # Header row -- band background, white bold text, centered.
    header_row = _sub(rows_el, "TablixRow")
    _sub(header_row, "Height", "0.30in")
    header_cells = _sub(header_row, "TablixCells")
    _caps = _column_captions(report, columns)
    for col in columns:
        cell = _sub(header_cells, "TablixCell")
        contents = _sub(cell, "CellContents")
        _build_textbox(
            contents,
            f"Hdr_{_safe(col)}",
            _caps.get(col.upper(), col.replace("_", " ")),
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
    _sub(detail_row, "Height", f"{_detail_h:.2f}in")
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
    col_align = {}
    col_font = {}
    try:
        for _y, _x, _d, lf in _layout_fields_in_order(report):
            src = (getattr(lf, "source", "") or "").upper()
            if src and src not in col_dt:
                dt = _drillthrough_for(report, lf)
                if dt:
                    col_dt[src] = dt
            if src and src not in col_align:
                ta = _ssrs_text_align(getattr(lf, "align", ""))
                if ta:
                    col_align[src] = ta
            if src and src not in col_font:
                # Carry the field's Oracle FONT into the data cell -- face,
                # size, bold, italic were parsed but never emitted, so every
                # cell fell back to the default 10pt Arial regardless of the
                # original (e.g. Courier New numerics rendered as Arial).
                fam = (getattr(lf, "font_family", "") or "").strip()
                sz = getattr(lf, "font_size", None)
                col_font[src] = (
                    fam or None,
                    f"{int(sz)}pt" if sz else None,
                    bool(getattr(lf, "bold", False)),
                    bool(getattr(lf, "italic", False)),
                )
    except Exception:  # noqa: BLE001 -- links must never break the table
        col_dt = {}
    for col in columns:
        cell = _sub(detail_cells, "TablixCell")
        contents = _sub(cell, "CellContents")
        dt = col_dt.get(col.upper())
        fam, sz, bld, ital = col_font.get(col.upper(), (None, None, False, False))
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
            text_align=col_align.get(col.upper()),
            font_family=fam,
            font_size=sz or "10pt",
            bold=bld,
            italic=ital,
        )

    # Report-level <summary> grand totals -> a static FOOTER total row, so a
    # flat report's "Total: N" line actually renders (the summary tokens
    # already compile to real SSRS aggregates via the resolver). Only on the
    # flat path; master-detail keeps its own structure. Each total lands in
    # the column it summarizes; a "Total" label sits in column 0.
    _SS_AGG = {"count": "Count", "sum": "Sum", "avg": "Avg", "average": "Avg",
               "min": "Min", "max": "Max", "stddev": "StDev",
               "variance": "Var", "% of total": "Sum"}
    _col_up = {c.upper(): c for c in columns}
    summ_aggs: dict = {}
    for f in (getattr(report, "formulas", None) or []):
        _fn = (getattr(f, "agg_function", "") or "").lower()
        _src = (getattr(f, "agg_source", "") or "").strip()
        if _fn and _src and _src.upper() in _col_up:
            _real = _col_up[_src.upper()]
            summ_aggs[_real.upper()] = (
                f'={_SS_AGG.get(_fn, "Sum")}(Fields!{_safe(_real)}.Value, '
                f'"{_safe(main.name)}")')
    _emit_footer = bool(summ_aggs) and detail_query is None
    if _emit_footer:
        foot_row = _sub(rows_el, "TablixRow")
        _sub(foot_row, "Height", "0.28in")
        foot_cells = _sub(foot_row, "TablixCells")
        for i, col in enumerate(columns):
            cell = _sub(foot_cells, "TablixCell")
            contents = _sub(cell, "CellContents")
            expr = summ_aggs.get(col.upper())
            val = expr if expr else ('="Total"' if i == 0 else '=""')
            _build_textbox(contents, f"Foot_{_safe(col)}", val, bold=True,
                           bg="#eef2f6", vertical_align="Middle",
                           border_color="#d0d0d0", padding="4pt")

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
        # Static footer member (the grand-total row), AFTER the detail group.
        if _emit_footer:
            _sub(row_members, "TablixMember")

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


def _one_to_many_link_children(report) -> set:
    """UPPER names of linked CHILD queries rendered as a COLUMNAR REPEATING
    detail TABLE (1:many) -- e.g. JV's Q_VEHICLE: a vehicle list under each
    tower. A 1:1 linked child (a single signature/contact field, no repeating
    table) is NOT included -- its scalar Lookup() is already correct. These
    1:many children get =Join(LookupSet(...)) instead, and their Oracle <link>
    WHERE filter is stripped so the child dataset carries ALL rows. Structural
    (the layout shape decides), never keyed on a report name."""
    # repeating frames keyed by the GROUP they bind to (source_query = group name)
    rep_cols: dict = {}
    def _walk(g):
        if "repeating" in (getattr(g, "kind", "") or "").lower():
            sq = (getattr(g, "source_query", "") or "").upper()
            if sq:
                xs = {round(float(getattr(f, "x", 0) or 0), 1)
                      for f in (getattr(g, "fields", None) or [])
                      if (getattr(f, "kind", "") or "") == "field"}
                rep_cols[sq] = max(rep_cols.get(sq, 0), len(xs))
        for c in (getattr(g, "children", None) or []):
            _walk(c)
    for g in (getattr(report, "layout", None) or []):
        _walk(g)
    out = set()
    for q in (getattr(report, "queries", None) or []):
        if not (getattr(q, "parent_group", "") or "").strip():
            continue
        for gn in (getattr(q, "group_names", None) or []):
            # >=2 distinct field x-positions in the bound repeating frame = a
            # columnar detail TABLE (many rows), not a single positional field.
            if rep_cols.get((gn or "").upper(), 0) >= 2:
                out.add((q.name or "").upper())
                break
    return out


def _strip_link_filter_predicates(report, child_names: set) -> None:
    """For each 1:many linked CHILD query, remove the Oracle <link> correlation
    predicate (``AND <expr> = :<param>``) from its SQL/T-SQL so the child
    dataset returns ALL rows -- the per-master-row join is done in SSRS by
    LookupSet on the key column the generator already SELECTs. Without this the
    child stays filtered to ONE master value and the Lookup/LookupSet only
    resolves for that single master row.

    FAIL-SAFE: only a predicate whose bind param matches a MASTER column (the
    link correlation) is removed, and only the ``AND ...`` form (never the
    leading WHERE predicate, so the WHERE clause is never left empty). Anything
    ambiguous is left intact -> worst case is today's behavior, never a broken
    query. The dataset builder re-derives QueryParameters from the remaining
    ``:X`` refs, so the now-unused link parameter drops automatically."""
    qs = getattr(report, "queries", None) or []
    owner: dict = {}
    for q in qs:
        for gn in (getattr(q, "group_names", None) or []):
            owner[(gn or "").upper()] = q
    for q in qs:
        if (q.name or "").upper() not in child_names:
            continue
        master = owner.get((getattr(q, "parent_group", "") or "").upper())
        if master is None:
            continue
        master_cols = {(getattr(it, "name", "") or "").upper()
                       for it in (getattr(master, "items", None) or [])}
        if not master_cols:
            continue

        def _maybe_drop(m):
            return " " if m.group(1).upper() in master_cols else m.group(0)

        for attr in ("sql", "tsql"):
            s = getattr(q, attr, "") or ""
            if not s:
                continue
            # Form B (the converter's optional-param NULL guard, which is how the
            # Oracle link predicate is emitted): AND (:p IS NULL OR <expr> = :p)
            new = re.sub(
                r"(?i)\bAND\s*\(\s*[:@]([A-Za-z_]\w*)\s+IS\s+NULL\s+OR\s+"
                r"[\w.]+\s*=\s*[:@]\1\s*\)",
                _maybe_drop, s)
            # Form A (bare equality): AND <expr> = :p
            new = re.sub(r"(?i)\bAND\s+[\w.]+\s*=\s*[:@]([A-Za-z_]\w*)\b(?!\s*\))",
                         _maybe_drop, new)
            if new != s:
                setattr(q, attr, new)


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
        if (child_ds or "").upper() in getattr(report, "_one_to_many_children", set()):
            # 1:many detail TABLE (e.g. a vehicle list per tower): list ALL the
            # child values correlated to THIS master row, newline-joined.
            # LookupSet returns the matching array; Join renders it down a cell
            # (a scalar Lookup would show only the FIRST child row). The child
            # dataset's link WHERE filter was stripped (see _build_report_root)
            # so it holds every row for the match to work across master rows.
            return (
                f'=Join(LookupSet({src}, {dst}, '
                f'Fields!{_safe(result_col)}.Value, "{_safe(child_ds)}"), vbCrLf)'
            )
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
    # field-name (UPPER) -> owning dataset, so a summary aggregate gets a
    # correct dataset scope.
    _agg_owner: dict = {}
    for _q in (getattr(report, "queries", None) or []):
        for _it in (getattr(_q, "items", None) or []):
            if getattr(_it, "name", ""):
                _agg_owner.setdefault(_it.name.upper(), _q.name)
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
            # Oracle <summary> (count/sum/avg/...) -> a REAL SSRS aggregate
            # over its source column, scoped to that column's dataset. This
            # makes report grand totals actually COMPUTE instead of shipping
            # a NULL placeholder (wild-corpus verified: a count footer total).
            _aggfn = (getattr(f, "agg_function", "") or "").lower()
            _aggsrc = (getattr(f, "agg_source", "") or "").strip()
            if _aggfn and _aggsrc:
                _SS = {"count": "Count", "sum": "Sum", "avg": "Avg",
                       "average": "Avg", "min": "Min", "max": "Max",
                       "stddev": "StDev", "variance": "Var",
                       "% of total": "Sum", "first": "First", "last": "Last"}
                _fn = _SS.get(_aggfn, "Sum")
                _k, _s, _n = resolve(_aggsrc, dataset_name, _depth + 1)
                if _k == "field":
                    _own = _agg_owner.get(_aggsrc.upper()) or dataset_name
                    expr = (f'={_fn}(Fields!{_safe(_s)}.Value, "{_safe(_own)}")'
                            if _own else f'={_fn}(Fields!{_safe(_s)}.Value)')
                    return ("formula", expr,
                            f"Oracle summary {_aggfn}({_aggsrc}) -> {_fn}()")
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


def _oracle_number_to_net(m: str, fill_mode: bool = False) -> str:
    """Translate an Oracle numeric mask to a .NET custom numeric format:
    9/N -> # (optional digit), 0 -> 0 (required), keep , . $ %, G->',' D->'.'.
    Oracle sign elements (MI/PR/S) become a .NET negative SECTION (pos;neg).
    ``fill_mode`` True == the mask carried FM/FX -> suppress trailing fraction
    zeros (the FM modifier's documented effect)."""
    # Scientific notation: Oracle EEEE normalizes the mantissa to ONE leading
    # digit and the digit positions after the decimal set the mantissa
    # precision -- 9.9999EEEE -> "1.2346E+08" (verified vs the Oracle docs +
    # real .NET). .NET expresses this as 0[.000...]E+00. Detect early: EEEE
    # overrides the normal digit translation entirely.
    if "EEEE" in m.upper():
        before = re.split(r"(?i)EEEE", m, maxsplit=1)[0]
        frac = re.split(r"[.Dd]", before)
        decimals = sum(1 for c in frac[-1] if c in "90Nn") if len(frac) > 1 else 0
        mant = "0" + ("." + "0" * decimals if decimals else "")
        return mant + "E+00"
    # Oracle negative-presentation elements appear at fixed positions: MI/PR are
    # TRAILING, S is the first OR last char. Strip whichever is present; it is
    # re-expressed as a .NET "positive;negative" section below. Verified 1:1
    # against the Oracle TO_CHAR docs:
    #   (default) -> leading minus (the .NET default)   MI -> "1234-"
    #   PR -> "<1234>"   S(lead) -> "+/-1234"   S(trail) -> "1234+/-"
    sign = None
    body, mu = m, m.upper()
    if mu.endswith("PR"):
        sign, body = "PR", m[:-2]
    elif mu.endswith("MI"):
        sign, body = "MI", m[:-2]
    elif mu.startswith("S"):
        sign, body = "SL", m[1:]
    elif mu.endswith("S"):
        sign, body = "ST", m[:-1]

    # Split integer / fractional around the FIRST decimal separator (Oracle 'D'
    # or a literal '.'). The two parts have DIFFERENT '9' semantics, so they
    # must be translated separately (below).
    sep_idx = next((i for i, ch in enumerate(body) if ch in "Dd."), None)
    int_src = body if sep_idx is None else body[:sep_idx]
    frac_src = "" if sep_idx is None else body[sep_idx + 1:]

    def _digits(src: str, fractional: bool) -> str:
        o = []
        for ch in src:
            if ch in "9N":
                # Oracle '9'/'N': in the FRACTION they PAD trailing zeros
                # (TO_CHAR(1.5,'9.99')->"1.50") unless FM/FX fill-mode is on,
                # which suppresses them -> '#'. In the INTEGER part they suppress
                # LEADING zeros -> '#' (the units position is promoted to a
                # required '0' afterward so a zero value still shows "0").
                o.append("0" if (fractional and not fill_mode) else "#")
            elif ch in "0,$":
                o.append(ch)
            elif ch == "%":
                # Oracle '%' is a LITERAL percent sign (Oracle's x100 scaling
                # element is 'V'; verified vs the Oracle number-format docs).
                # .NET's bare '%' MULTIPLIES by 100, turning "50%" into "5000%".
                # Escape to a literal.
                o.append("\\%")
            elif ch == "G":
                o.append(",")
            # D handled as the split point; FM/FX/V/L/parens dropped (best effort)
        return "".join(o)

    head = _digits(int_src, False)
    tail = _digits(frac_src, True)

    # Units position: Oracle '9' renders a ZERO value's integer part as "0"
    # (documented), but .NET '#' is a non-zero placeholder that blanks 0. If the
    # integer part has no required '0', promote its LAST '#' so zero shows "0"
    # while leading positions stay '#' (no spurious leading zeros).
    if head and "0" not in head:
        idx = head.rfind("#")
        if idx >= 0:
            head = head[:idx] + "0" + head[idx + 1:]
    if not head and (tail or sep_idx is not None):
        head = "0"                       # pure-decimal mask -> leading 0

    fmt = head + ("." + tail if sep_idx is not None else "")
    if not fmt or fmt == ".":
        return ""
    # Re-express the stripped Oracle sign element as a .NET negative section.
    # 2 sections (positive;negative) -- .NET reuses the positive section for
    # zero, matching Oracle (MI/PR show a zero as the positive form, not a
    # minus). '<' '>' '+' '-' are all literals in a .NET format string.
    if sign == "MI":
        return f"{fmt};{fmt}-"
    if sign == "PR":
        return f"{fmt};<{fmt}>"
    if sign == "SL":
        return f"+{fmt};-{fmt}"
    if sign == "ST":
        return f"{fmt}+;{fmt}-"
    return fmt


def _oracle_mask_to_net(mask: str) -> str:
    """Translate an Oracle Reports formatMask to a .NET (SSRS) format string.
    Returns "" when empty or unrecognized (caller then emits no <Format>).
    Generic -- pattern-driven, no per-report logic."""
    if not mask:
        return ""
    m = mask.strip()
    # Oracle fill-mode modifier FM/FX suppresses padding. It has no .NET token,
    # but it DOES change numeric output (suppresses trailing-fraction zeros), so
    # remember it before stripping and thread it into the numeric translator.
    fill_mode = bool(re.search(r"(?i)F[MX]", m))
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
        return _oracle_number_to_net(m, fill_mode)
    return ""


def _spelled_case(mask: str, net: str) -> str:
    """Return the case transform a date mask needs: 'U', 'L', or ''.

    Oracle takes the case of a spelled month/day element from the format model
    (MON->JAN, Mon->Jan, mon->jan -- verified vs the Oracle date-format docs),
    but a .NET format string can render a spelled name (MMM/MMMM/dddd) only in
    proper case. So an UPPER or lower spelled element needs a UCase()/LCase()
    wrap on the value expression. Restricted to masks whose .NET format carries
    a spelled NAME (MMM or ddd) and NO time token (H h m s t): for those, the
    VB Format() used in the wrap is unambiguous AND null-safe. Masks that mix a
    spelled month with time fall back to proper case (rare) -- correctness over
    a fragile expression."""
    if ("MMM" not in net) and ("ddd" not in net):
        return ""
    if any(c in net for c in "Hhmst"):
        return ""
    for tok in ("MONTH", "MON", "DAY", "DY", "RM"):
        mt = re.search(tok, mask, re.IGNORECASE)
        if mt:
            t = mt.group(0)
            return "U" if t.isupper() else ("L" if t.islower() else "")
    return ""


def _format_index(report) -> dict:
    """Map {field SOURCE name (upper) -> (.NET format, case)} for every layout
    field carrying an Oracle formatMask. Used to stamp <Format> on the matching
    Textbox values so SSRS renders currency / dates / thousands like Oracle.
    ``case`` is '', 'U', or 'L' -- a spelled-date case transform (see above)."""
    idx: dict = {}

    def walk(groups):
        for g in groups or []:
            for f in (g.fields or []):
                mask = getattr(f, "format_mask", "") or ""
                src = (getattr(f, "source", "") or "").strip()
                if mask and src:
                    net = _oracle_mask_to_net(mask)
                    if net:
                        idx.setdefault(src.upper(), (net, _spelled_case(mask, net)))
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
        entry = idx.get(mobj.group(1).upper())
        if not entry:
            continue
        net, case = entry
        # Spelled-date case transform: a .NET <Format> can't force UPPER/lower
        # case, so wrap the value. Only when the value is the plain field ref
        # (no First()/scope) and the mask was case-flagged safe (_spelled_case:
        # spelled name, no time token -> VB Format is unambiguous + null-safe).
        fld = mobj.group(1)
        if case and val_el.text.strip() == f"=Fields!{fld}.Value":
            fn = "UCase" if case == "U" else "LCase"
            val_el.text = f'={fn}(Format(Fields!{fld}.Value, "{net}"))'
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


def _find_band_caption_text(label_geo, bcol):
    """The Oracle group-band caption is the layout TEXT that references the
    group's break column as a lexical token (e.g. "&COL_1 : &CS_COL_SITES
    SITE(S)"). Return its raw text, or "" if none exists."""
    bcu = (bcol or "").upper()
    if not bcu:
        return ""
    needle = "&" + bcu
    for text, _lx, _ly, _bg in (label_geo or []):
        if text and needle in text.upper():
            return text
    return ""


def _resolve_band_caption(text, group, main, declared):
    """Resolve an Oracle group-band caption TEXT (e.g. "&COL_1 : &CS_COL_SITES
    SITE(S)") into a VB expression that reads exactly like the report instead of
    a "<FieldName>: <value>" field-name dump. Field tokens -> CStr(Fields!X.Value);
    SUMMARY tokens -> the summary's aggregate (Count/Sum/...) so we NEVER emit a
    dangling Fields!CS_X.Value reference (which would break upload); plain text ->
    a quoted literal. Returns "" when nothing resolves so the caller can fall back
    to a synthesized label."""
    if not text or "&" not in text:
        return ""
    smap = {}
    for sm in (getattr(group, "summaries", None) or []):
        nm = (sm.get("name") or "").upper()
        src = (sm.get("source") or "").upper()
        if nm and src and src in declared:
            fn = _ssrs_summary_fn(sm.get("function") or "sum")
            smap[nm] = f"{fn}(Fields!{_safe(_orig_name(main, src))}.Value)"
    parts = re.split(r"(&[A-Za-z_][A-Za-z0-9_]*)", text)
    out = []
    any_field = False
    for p in parts:
        if not p:
            continue
        if p.startswith("&"):
            nm = p[1:].upper()
            if nm in smap:
                out.append(smap[nm]); any_field = True
            elif nm in declared:
                out.append(f"CStr(Fields!{_safe(_orig_name(main, nm))}.Value)")
                any_field = True
            # unknown token -> dropped (safe: no dangling ref)
        else:
            out.append('"' + p.replace('"', '""') + '"')
    if not any_field or not out:
        return ""
    return "=" + " & ".join(out)


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
    #
    # Oracle Reports frames carry NON-PRINTING design-time fill hints that are
    # specifically light PINKS / LAVENDERS / MAGENTAS (r100g88b100 = #FFE0FF,
    # #FFBFFF): RED and BLUE both near-max with green lower. Using one as the
    # master band paints a pink bar the report never shows (a tabBrkLeft list
    # whose group band is plain text on white). Exclude ONLY that pink/lavender
    # family -- a genuine band (a darkgreen master band, a navy summary header, a
    # chosen cream #FFEEAA) never has BOTH red and blue maxed, so it is kept.
    def _is_oracle_design_fill(c):
        c = _norm(c)
        if len(c) != 7:
            return False
        try:
            r = int(c[1:3], 16); g = int(c[3:5], 16); b = int(c[5:7], 16)
        except ValueError:
            return False
        return r >= 235 and b >= 235 and g <= 235

    # Does the source carry ANY genuine band color? A non-white, non-design
    # background fill, OR a non-white SOLID foreground fill (Oracle stores some
    # band colors -- e.g. a navy column-header -- as fillForegroundColor with
    # fillPattern="solid"). When NONE exists the report is a PLAIN receipt/list
    # and must render neutral -- no invented navy/yellow band theme (the #1
    # reason a plain Oracle report came out looking like a styled SSRS tablix).
    has_real_band = False
    for g in flat:
        bgc = _norm(getattr(g, "background_color", "") or "")
        if bgc and not _is_white(bgc) and not _is_oracle_design_fill(bgc):
            has_real_band = True
            break
        fgc = _norm(getattr(g, "foreground_color", "") or "")
        fpat = (getattr(g, "fill_pattern", "") or "").lower()
        if (fpat == "solid" and fgc and not _is_white(fgc)
                and not _is_oracle_design_fill(fgc)):
            has_real_band = True
            break

    band_bg = ""
    for g, bg in group_bgs:
        if (getattr(g, "kind", "") == "repeating_frame"
                and not _is_oracle_design_fill(bg)):
            band_bg = bg
            break
    if not band_bg:
        # most-common non-white, non-design-hint bg across all groups
        meaningful = [bg for _, bg in group_bgs if not _is_oracle_design_fill(bg)]
        if meaningful:
            from collections import Counter
            ctr = Counter(meaningful)
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

    if not has_real_band:
        # PLAIN report -- no source band color anywhere. Neutral palette: the
        # group band is white (invisible) with black bold text, never the
        # navy/yellow house theme. Reports WITH a genuine band keep it (below).
        return {
            "band_bg":   "#ffffff",
            "band_fg":   "#000000",
            "subhdr_bg": "#ffffff",
            "subhdr_fg": "#000000",
            "card_bg":   DEFAULTS["card_bg"],
            "ink":       DEFAULTS["ink"],
            "ink_soft":  DEFAULTS["ink_soft"],
            "rule":      DEFAULTS["rule"],
            "themed":    False,
        }
    return {
        "band_bg":   band_bg or DEFAULTS["band_bg"],
        "band_fg":   band_fg or DEFAULTS["band_fg"],
        "subhdr_bg": subhdr_bg or DEFAULTS["subhdr_bg"],
        "subhdr_fg": subhdr_fg or DEFAULTS["subhdr_fg"],
        "card_bg":   DEFAULTS["card_bg"],
        "ink":       DEFAULTS["ink"],
        "ink_soft":  DEFAULTS["ink_soft"],
        "rule":      DEFAULTS["rule"],
        "themed":    True,
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
            # A BLOB/image-bound field (a logo/seal/photo) is NOT a data column:
            # leaving it in the detail band paints an empty cell where a picture
            # belongs AND, when it sits just right of a real column (e.g. a logo
            # field at x0.5 vs an address column at x0.26), squeezes that column
            # to a sliver. Mirrors the mockup _detail_image_srcs skip. Generic.
            if (f.kind == "field" and f.source
                    and not _image_field_binding(f, report)):
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


def _stacked_list_columns(report):
    """For a FLAT tabular LIST whose record spans MULTIPLE stacked physical lines
    (Oracle draws e.g. Permit over Permit-Dates in column 1, City over Type-of-
    Operation in column 2), return the per-column stacked structure:
        {"columns": [ {"x", "next", "lines": [(kind, src_or_text)]} ... ],
         "headers": [ [(x, label_text), ...]  per stacked header band ],
         "n_lines": int, "themed": bool}
    or None when the layout is NOT a multi-line stacked record (a single-band
    list, or no detail frame). Geometry-only; uses ONLY the DETAIL repeating
    frame's own fields, so a section-level criteria subtitle (a full-width
    P_SUBTITLE sitting outside the frame) is never mistaken for a record column,
    and the shared _detail_band_fields detector is left untouched. No report
    names."""
    from collections import defaultdict
    main_sec = _section_by_kind(report, "section_main")
    if main_sec is None:
        return None
    frames = []

    def _collect_frames(g):
        if (getattr(g, "kind", "") or "").lower() == "repeating_frame":
            frames.append(g)
        for c in (getattr(g, "children", None) or []):
            _collect_frames(c)
    _collect_frames(main_sec)

    def _frame_fields(fr):
        out = []

        def _w(n):
            for f in (getattr(n, "fields", None) or []):
                k = (getattr(f, "kind", "") or "")
                if k not in ("field", "text"):
                    continue
                if k == "field" and _image_field_binding(f, report):
                    continue
                src = (getattr(f, "source", "") or "").strip()
                txt = (getattr(f, "text", "") or "").strip()
                if k == "field" and not src:
                    continue
                if k == "text" and not txt:
                    continue
                out.append((round(float(getattr(f, "y", 0) or 0), 2),
                            float(getattr(f, "x", 0) or 0),
                            float(getattr(f, "width", 0) or 0),
                            k, src or txt))
            for c in (getattr(n, "children", None) or []):
                _w(c)
        _w(fr)
        return out

    best_fields = None
    best_cols = 0
    for fr in frames:
        flds = _frame_fields(fr)
        if not flds:
            continue
        by = defaultdict(set)
        for y, x, _w, _k, _s in flds:
            by[y].add(round(x, 1))
        nx = max((len(v) for v in by.values()), default=0)
        if nx > best_cols:
            best_cols = nx
            best_fields = flds
    if best_fields is None or best_cols < 2:
        return None
    flds = best_fields
    by_y = defaultdict(list)
    for y, x, w, k, s in flds:
        by_y[y].append((x, w, k, s))
    prim_y = max(by_y, key=lambda y: len({round(x, 1) for x, _w, _k, _s in by_y[y]}))
    col_xs = sorted({round(x, 1) for x, _w, _k, _s in by_y[prim_y]})
    if len(col_xs) < 2:
        return None
    # The line-1 (primary) band must be predominantly DATA fields, not static
    # text labels. A band whose top line is mostly captions ("Applications" /
    # "Fees" over their counts) is a header/summary table, NOT a per-record
    # stacked list -- keep it on the flat path.
    _prim_field_cols = {
        round(min(col_xs, key=lambda c: abs(c - x)), 1)
        for x, _w, k, _s in by_y[prim_y] if k == "field"}
    if len(_prim_field_cols) < len(col_xs) * 0.5:
        return None

    def _aligned(y):
        xs = {round(x, 1) for x, _w, _k, _s in by_y[y]}
        return sum(1 for cx in col_xs if any(abs(cx - xx) <= 0.3 for xx in xs))

    sec_bands = [y for y in by_y if y > prim_y + 0.01
                 and _aligned(y) >= 2 and _aligned(y) >= len(col_xs) * 0.5]
    if not sec_bands:
        return None  # single-line list -> keep the flat one-column-per-field grid

    cols = []
    for ci, cx in enumerate(col_xs):
        nxt = col_xs[ci + 1] if ci + 1 < len(col_xs) else None
        lines = []
        for y in sorted(by_y):
            for x, _w, k, s in by_y[y]:
                near = min(col_xs, key=lambda c: abs(c - x))
                if abs(near - cx) < 1e-6 and abs(x - cx) <= 0.6:
                    lines.append((y, k, s))
        lines.sort(key=lambda z: z[0])
        cols.append({"x": cx, "next": nxt, "lines": [(k, s) for _y, k, s in lines]})
    n_lines = max((len(c["lines"]) for c in cols), default=1)

    try:
        _fg, label_geo = _layout_geometry_index(report)
    except Exception:
        label_geo = []
    hb = defaultdict(list)
    for t, lx, ly, _bg in (label_geo or []):
        if t and 0 < (prim_y - ly) <= 0.8 and "&<" not in t:
            hb[round(ly, 2)].append((lx, t))
    headers = [sorted(hb[y]) for y in sorted(hb)
               if len({round(x, 1) for x, _t in hb[y]}) >= 2]
    palette = _resolve_palette(report)
    # Resolve the header band color ONCE here (mirroring _build_tablix's
    # neutralization) so the RDL builder and the mockup render agree exactly: a
    # plain list (or a near-neutral dark-gray design artifact) gets a plain
    # black-on-white header, never an invented navy band.
    hdr_bg = palette.get("band_bg") or "#00008B"
    hdr_fg = palette.get("band_fg") or "#ffffff"
    if (not palette.get("themed", True)) or _is_neutral_dark(hdr_bg):
        hdr_bg = "#ffffff"; hdr_fg = "#111111"
    return {"columns": cols, "headers": headers, "n_lines": n_lines,
            "themed": bool(palette.get("themed", True)),
            "header_bg": hdr_bg, "header_fg": hdr_fg}


def _grouped_tabular_spec(report):
    """A 2-level GROUPED TABULAR report with per-group SUBTOTALS (Oracle's
    classic break report): an OUTER repeating frame that DIRECTLY owns a group
    HEADER line (a break-key caption + status, e.g. "<Graveyard> : <id>
    Status:<x>") sitting above a COLUMN-HEADER band, an inner DETAIL repeating
    frame of >=3 data columns, and GROUP-FOOTER frames carrying summary fields
    (CS_/CF_/Sum) below the detail (the "= Total ... / - Crushed" totals stack).

    Returns:
        {"grp_key", "group_header":[(kind,src_or_text,x,w)],
         "col_headers":[(x,label)], "detail_cols":[(x,w,src)],
         "footers":[[(kind,src_or_text,x,w)] per line top->bottom],
         "themed":bool}
    or None when the layout is NOT this archetype.

    Purely STRUCTURAL / geometry-driven (no report names). The discriminators
    that keep it from stealing the existing nested-MD CARD reports (METHACT,
    ASBINSPC) are: the outer frame must DIRECTLY own the group-header data field
    (a card report nests its master fields one level deeper, so its outer frame
    owns none), AND there must be both a >=3-label column header AND a summary
    footer. Single-record FORMS (one record/page) are excluded outright."""
    try:
        main_sec = _section_by_kind(report, "section_main")
        if main_sec is None:
            return None
        # One-record-per-page positional forms are a different archetype.
        def _walk(n):
            yield n
            for c in (getattr(n, "children", None) or []):
                yield from _walk(c)
        for n in _walk(main_sec):
            if int(getattr(n, "max_records_per_page", 0) or 0) == 1:
                return None
        reps = [n for n in _walk(main_sec)
                if (getattr(n, "kind", "") or "") == "repeating_frame"]
        if not reps:
            return None

        def _has_rep_child(rf):
            return any((getattr(k, "kind", "") or "") == "repeating_frame"
                       for k in _walk(rf) if k is not rf)

        def _data_fields(node):
            return [f for f in (getattr(node, "fields", None) or [])
                    if (getattr(f, "kind", "") or "") == "field"
                    and (getattr(f, "source", "") or "").strip()]

        # DETAIL = the innermost repeating frame with the most data columns.
        leaves = [rf for rf in reps if not _has_rep_child(rf)]
        detail_rf = max(leaves, key=lambda rf: len(_data_fields(rf)),
                        default=None) if leaves else None
        if detail_rf is None:
            return None
        dcols = sorted(((float(getattr(f, "x", 0) or 0),
                         float(getattr(f, "width", 0) or 0),
                         (getattr(f, "source", "") or "").strip())
                        for f in _data_fields(detail_rf)), key=lambda z: z[0])
        if len(dcols) < 3:
            return None
        drow_y = min(float(getattr(f, "y", 0) or 0) for f in _data_fields(detail_rf))

        # OUTER = the largest repeating frame that is an ancestor of detail_rf.
        det_ids = {id(detail_rf)}
        outers = [rf for rf in reps
                  if rf is not detail_rf
                  and id(detail_rf) in {id(k) for k in _walk(rf)}]
        outer_rf = max(outers, key=lambda rf: sum(1 for _ in _walk(rf)),
                       default=None) if outers else None
        if outer_rf is None:
            return None

        # COLUMN-HEADER band: >=3 static text labels just ABOVE the detail row.
        col_hdr = []
        for n in _walk(outer_rf):
            for f in (getattr(n, "fields", None) or []):
                if (getattr(f, "kind", "") or "") != "text":
                    continue
                t = (getattr(f, "text", "") or "").strip()
                fy = float(getattr(f, "y", 0) or 0)
                if t and 0.0 < (drow_y - fy) <= 0.5:
                    col_hdr.append((float(getattr(f, "x", 0) or 0), t))
        if len(col_hdr) < 3:
            return None
        col_hdr.sort(key=lambda z: z[0])
        chy = min(fy for fy in
                  (float(getattr(f, "y", 0) or 0)
                   for n in _walk(outer_rf) for f in (getattr(n, "fields", None) or [])
                   if (getattr(f, "kind", "") or "") == "text"
                   and (getattr(f, "text", "") or "").strip()
                   and 0.0 < (drow_y - float(getattr(f, "y", 0) or 0)) <= 0.5))

        # GROUP HEADER: the outer frame's OWN fields/text above the column band.
        # The break-key DATA field (leftmost field) anchors the group; a
        # "(continued)" carry-over marker is dropped (a sample render shows the
        # first occurrence). Requires >=1 directly-owned data field -> the card
        # reports (whose outer frame owns none) never qualify.
        grp_key = None
        group_header = []
        for f in (getattr(outer_rf, "fields", None) or []):
            k = (getattr(f, "kind", "") or "")
            if k not in ("field", "text"):
                continue
            fy = float(getattr(f, "y", 0) or 0)
            if fy >= chy - 0.001:
                continue
            txt = (getattr(f, "text", "") or "").strip()
            src = (getattr(f, "source", "") or "").strip()
            if k == "text" and txt.lower() == "(continued)":
                continue
            if k == "field" and not src:
                continue
            if k == "text" and not txt:
                continue
            if k == "field" and grp_key is None:
                grp_key = src.upper()
            group_header.append((k, src if k == "field" else txt,
                                 float(getattr(f, "x", 0) or 0),
                                 float(getattr(f, "width", 0) or 0)))
        if grp_key is None:
            return None
        group_header.sort(key=lambda z: z[2])

        # GROUP FOOTERS: non-repeating sub-frames below the detail row, carrying
        # the per-group totals. The repeated break-key field (same source as the
        # group header) is dropped so the totals stack stays clean/right-aligned.
        foot_by_y = {}
        has_summary = False
        for n in _walk(outer_rf):
            if (getattr(n, "kind", "") or "") == "repeating_frame":
                continue
            for f in (getattr(n, "fields", None) or []):
                k = (getattr(f, "kind", "") or "")
                if k not in ("field", "text"):
                    continue
                fy = float(getattr(f, "y", 0) or 0)
                if fy <= drow_y + 0.05:
                    continue
                txt = (getattr(f, "text", "") or "").strip()
                src = (getattr(f, "source", "") or "").strip()
                if k == "field" and src.upper() == grp_key:
                    continue
                if k == "field" and not src:
                    continue
                if k == "text" and not txt:
                    continue
                su = src.upper()
                if k == "field" and (su.startswith(("CS_", "CF_"))
                                     or su.startswith("SUM")):
                    has_summary = True
                foot_by_y.setdefault(round(fy, 2), []).append(
                    (k, src if k == "field" else txt,
                     float(getattr(f, "x", 0) or 0),
                     float(getattr(f, "width", 0) or 0)))
        if not has_summary:
            return None
        footers = [sorted(foot_by_y[y], key=lambda z: z[2])
                   for y in sorted(foot_by_y)]

        palette = _resolve_palette(report)
        return {
            "grp_key": grp_key,
            "group_header": group_header,
            "col_headers": col_hdr,
            "detail_cols": dcols,
            "footers": footers,
            "themed": bool(palette.get("themed", True)),
        }
    except Exception:  # noqa: BLE001 -- routing/extraction must never crash
        return None


def _is_grouped_tabular_subtotal(report):
    """Route gate for the grouped-tabular-with-subtotals archetype."""
    return _grouped_tabular_spec(report) is not None


def _is_stacked_list_rdl(report, main):
    """A FLAT tabular list whose record occupies >=2 column-aligned STACKED lines
    (Permit/Permit-Dates) -> route to _build_stacked_list_tablix. A single-line
    list (one band) returns False and keeps the flat one-column-per-field grid.
    Structural, no report names."""
    try:
        if not _is_flat_tabular_list_rdl(report, main):
            return False
        sl = _stacked_list_columns(report)
        return sl is not None and sl.get("n_lines", 1) >= 2
    except Exception:  # noqa: BLE001 -- routing must never crash the build
        return False


def _build_stacked_list_tablix(report, main):
    """Render a FLAT tabular list whose record occupies MULTIPLE stacked physical
    lines as a single-column Tablix: a stacked multi-line header band over a
    detail row whose ONE cell stacks each column's fields vertically (Oracle
    2-line list: Permit/Permit-Dates | City/Type-of-Operation | Site & Alias/
    Permittee | Visited). Iterates every row of the main dataset via a Details
    group. Geometry-driven, mirrors the ND_Detail positioned-textbox pattern;
    falls back to the flat grid when the stacked structure can't be derived."""
    sl = _stacked_list_columns(report)
    if sl is None:
        return _build_tablix(report, main)
    cols = sl["columns"]
    headers = sl["headers"]
    n_lines = sl["n_lines"]
    main_ds = main.name or ""
    palette = _resolve_palette(report)
    INK = palette.get("ink", "#282828")
    RULE = palette.get("rule", "#d0d0d0")
    _target_w = max(9.0, _page_width_for(report) - 2 * _PAGE_HMARGIN_IN - 0.30)
    _span = (cols[-1]["x"] + 2.0) if cols else 9.0
    BODY_W = min(_target_w, max(7.0, _span))

    def _col_right(ci):
        nxt = cols[ci]["next"]
        return (nxt - 0.05) if nxt is not None else BODY_W

    LINE_H = 0.20
    hdr_h = max(0.22, LINE_H * max(1, len(headers)) + 0.04)
    det_h = max(0.24, LINE_H * max(1, n_lines) + 0.06)
    # Header band color resolved once in _stacked_list_columns (so the mockup
    # render agrees exactly).
    hdr_bg = sl.get("header_bg", "#ffffff")
    hdr_fg = sl.get("header_fg", "#111111")

    tablix = ET.Element(_q("Tablix"))
    tablix.set("Name", "Tablix_StackedList")
    tbody = _sub(tablix, "TablixBody")
    tcols = _sub(tbody, "TablixColumns")
    _sub(_sub(tcols, "TablixColumn"), "Width", f"{BODY_W:.2f}in")
    trows = _sub(tbody, "TablixRows")

    # --- header row: stacked label bands (each column gets its 1..N labels) ---
    hrow = _sub(trows, "TablixRow"); _sub(hrow, "Height", f"{hdr_h:.2f}in")
    hcont = _sub(_sub(_sub(hrow, "TablixCells"), "TablixCell"), "CellContents")
    hrect = _sub(hcont, "Rectangle"); hrect.set("Name", "SL_ColHdr")
    hst = _sub(hrect, "Style"); _sub(hst, "BackgroundColor", hdr_bg)
    hri = _sub(hrect, "ReportItems")
    for bi, band in enumerate(headers):
        bx = sorted(band)
        for hi, (lx, label) in enumerate(bx):
            nxt = bx[hi + 1][0] if hi + 1 < len(bx) else (
                min((c["next"] for c in cols
                     if c["next"] is not None and c["next"] > lx), default=BODY_W))
            text, _ = _resolve_text_expression(label, report, main_ds)
            _build_textbox(hri, f"Tb_SLHdr_{bi}_{hi}", text, bold=True,
                           font_size="8pt", bg=hdr_bg, fg=hdr_fg,
                           text_align="Left", vertical_align="Top",
                           border_color=hdr_bg, padding="2pt", can_grow=False)
            _tb = hri[-1]
            _sub(_tb, "Top", f"{bi * LINE_H:.2f}in")
            _sub(_tb, "Left", f"{max(0.02, lx):.2f}in")
            _sub(_tb, "Width", f"{max(0.4, nxt - lx - 0.04):.2f}in")
            _sub(_tb, "Height", f"{LINE_H:.2f}in")

    # --- detail row: each column's fields stacked vertically in ONE cell ---
    drow = _sub(trows, "TablixRow"); _sub(drow, "Height", f"{det_h:.2f}in")
    dcont = _sub(_sub(_sub(drow, "TablixCells"), "TablixCell"), "CellContents")
    drect = _sub(dcont, "Rectangle"); drect.set("Name", "SL_Detail")
    dst = _sub(drect, "Style"); _sub(dst, "BackgroundColor", "#ffffff")
    _db = _sub(dst, "BottomBorder"); _sub(_db, "Style", "Solid")
    _sub(_db, "Color", RULE); _sub(_db, "Width", "0.25pt")
    dri = _sub(drect, "ReportItems")
    for ci, col in enumerate(cols):
        cx = col["x"]; cright = _col_right(ci)
        for li, (kind, s) in enumerate(col["lines"]):
            if kind == "text":
                val, _isx = _resolve_text_expression(s, report, main_ds)
            else:
                val = _field_value_for(
                    LayoutField(name="F", source=s, kind="field"),
                    report, dataset_name=main_ds)
            if not val:
                continue
            _build_textbox(dri, f"Tb_SLDet_{ci}_{li}_{_safe(s)[:18]}", val,
                           font_size="8pt", bg="#ffffff", fg=INK,
                           text_align="Left", vertical_align="Top",
                           border_color="#ffffff", padding="2pt", can_grow=False)
            _tb = dri[-1]
            _sub(_tb, "Top", f"{li * LINE_H:.2f}in")
            _sub(_tb, "Left", f"{max(0.02, cx):.2f}in")
            _sub(_tb, "Width", f"{max(0.4, cright - cx - 0.04):.2f}in")
            _sub(_tb, "Height", f"{LINE_H:.2f}in")

    # --- hierarchy: one column, header member (static) + Details group ---
    _sub(_sub(_sub(tablix, "TablixColumnHierarchy"), "TablixMembers"),
         "TablixMember")
    rmems = _sub(_sub(tablix, "TablixRowHierarchy"), "TablixMembers")
    _sub(_sub(rmems, "TablixMember"), "KeepWithGroup", "After")
    dmem = _sub(rmems, "TablixMember")
    _sub(dmem, "Group").set("Name", "Details_Main")

    _sub(tablix, "DataSetName", _safe(main_ds))
    _sub(tablix, "Top", "0.5in")
    _sub(tablix, "Left", "0.25in")
    _sub(tablix, "Height", f"{hdr_h + det_h:.2f}in")
    _sub(tablix, "Width", f"{BODY_W:.2f}in")
    style = _sub(tablix, "Style")
    _sub(_sub(style, "Border"), "Style", "None")
    return tablix


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


def _is_flat_tabular_list_rdl(report, main):
    """Route a plain TABULAR LIST -- a >=3-column detail row under a >=3-label
    column-header band, NOT a nested master-detail and NOT a per-record FORM --
    to the flat column grid (_build_tablix) rather than a wallet/grouped CARD
    Tablix (which wraps it in a fabricated band + label:value stack). Reuses the
    preview's tuned `_is_flat_tabular_list` detector so the RDL and mockup agree,
    and excludes Oracle maxRecordsPerPage==1 forms (a requisition/letter prints
    ONE record per page -- a positional form, not a continuous list). Structural,
    never keyed on a report name."""
    # A per-record FORM (one master record per physical page) is not a list.
    def _walk(g):
        yield g
        for c in (g.children or []):
            yield from _walk(c)

    for top in (report.layout or []):
        for g in _walk(top):
            if int(getattr(g, "max_records_per_page", 0) or 0) == 1:
                return False

    try:
        field_geo, label_geo = _layout_geometry_index(report)
        row, _wrap, row_y = _detail_band_fields(report)
    except Exception:  # noqa: BLE001
        return False
    if row_y is None:
        return False
    # The detail must be a FLAT >=3-column row...
    if len(row) < 3 or len({round(x, 1) for _s, x, _y, _w in row}) < 3:
        return False
    # ...under a >=3-label column-header band sitting just above the detail row.
    hdr_labels = sum(1 for _t, _lx, ly, _bg in (label_geo or [])
                     if -0.05 <= (row_y - ly) <= 0.6)
    if hdr_labels < 3:
        return False
    # NOT a genuine nested master-detail: the OUTER group must not carry a
    # stacked master BAND well ABOVE the detail row (its fields span >=2 y-rows
    # or sit >0.9in above the detail, e.g. METHACT's Complaint-ID band over an
    # Action table). A grouped-but-FLAT list (e.g. a permittee roster with 2 sort
    # groups) has its outer field(s) IN the detail row -> route flat.
    try:
        chain = _flatten_group_chain(getattr(main, "groups", None) or [])
    except Exception:  # noqa: BLE001
        chain = []
    if len(chain) >= 2:
        outer = chain[0]
        oys = [field_geo[(it.name or "").upper()][1]
               for it in (outer.items or [])
               if field_geo.get((it.name or "").upper())]
        if oys and ((row_y - min(oys)) > 0.9 or (max(oys) - min(oys)) > 0.5):
            return False
    return True


def _is_positional_form_rdl(report):
    """A POSITIONAL single-record FORM: prints ONE master record per page (Oracle
    maxRecordsPerPage=1 OR a frame pageBreakAfter) whose master fields are
    SCATTERED in labeled blocks (a frame with >=4 distinct field y-rows) WITH at
    least one embedded sub-table -- e.g. an emissions-inventory form (Plant
    Location / Mailing Address blocks + SIC/NAIC + an SPT/EMISSIONS box). Broader
    than _is_single_record_form (which needs EXACTLY one columnar table + maxRec=1),
    so it catches multi-sub-table forms the generic path renders as a navy nested-
    MD/card with field-name dumps. Excludes flat tabular lists. Structural, never
    keyed on a report name."""
    main = None
    for g in (report.layout or []):
        if (g.kind or "").lower() == "section_main":
            main = g
            break
    if main is None:
        return False
    try:
        from ..preview.html_mockup import _has_columnar_repeating_frame
        if not _has_columnar_repeating_frame(report):
            return False
    except Exception:  # noqa: BLE001
        return False
    one_per_page = False
    scattered = False

    def _walk(g):
        nonlocal one_per_page, scattered
        if (bool(getattr(g, "page_break_after", False))
                or int(getattr(g, "max_records_per_page", 0) or 0) == 1):
            one_per_page = True
        fys = {round(getattr(f, "y", 0) or 0, 1)
               for f in (g.fields or []) if getattr(f, "kind", "") == "field"}
        if len(fys) >= 4:
            scattered = True
        for c in (g.children or []):
            _walk(c)

    _walk(main)
    if not (one_per_page and scattered):
        return False
    try:
        m = _pick_main_query(report)
        if m is not None and _is_flat_tabular_list_rdl(report, m):
            return False
    except Exception:  # noqa: BLE001
        pass
    return True


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
        # Prefer the master frame's OWN band caption text (e.g.
        # "&COL_1 : &CS_COL_SITES SITE(S)") so the band reads exactly like the
        # report -- field tokens bind to Fields!, the SUMMARY token to its
        # aggregate. Falls back to a synthesized "Label: value" only when the
        # source carries no such caption.
        band_caption = _resolve_band_caption(
            _find_band_caption_text(label_geo, bcol), outer, main, declared)
        if band_caption:
            band_val = band_caption
        else:
            band_left = _nested_group_label(outer.items[0]) if outer.items else outer.name
            if bcol and bcol.upper() in declared:
                band_val = (f'="{band_left}: " & CStr(Fields!{_safe(bcol)}.Value)')
            else:
                band_val = f'="{band_left}"'
        _build_textbox(bri, "Tb_ND_BandL", band_val, bold=True, font_size="11pt",
                       bg=BAND_BG, fg=BAND_FG, text_align="Left",
                       vertical_align="Middle", border_color=BAND_BG, padding="5pt")
        _tb = bri[-1]
        _band_w = 7.4 if band_caption else 4.5
        _sub(_tb, "Top", "0in"); _sub(_tb, "Left", "0in")
        _sub(_tb, "Width", f"{_band_w:.2f}in"); _sub(_tb, "Height", f"{band_h:.2f}in")
        # The Oracle caption already prints the group count inline, so don't ALSO
        # emit a separate right-hand total cell (it would duplicate the count).
        if outer.summaries and not band_caption:
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

    # A middle-group card field that is an internal key (*_ID) or that DUPLICATES
    # a detail column adds a spurious sub-band between the master band and the
    # detail header -- the real report runs straight from the band into the
    # detail (verified: METHACT's "Status Date / Action History ID" line). Drop
    # those and skip the card entirely when nothing descriptive remains; a
    # genuine middle sub-master with real header fields still renders.
    _det_names = ({(_it.name or "").upper()
                   for _it in _printable_items(last, skip_break=False)}
                  if last is not None else set())
    _skip_card = set()  # middle indices whose card row AND member are suppressed
    for mi, grp in enumerate(middles):
        _mid_items = [_it for _it in _printable_items(grp, skip_break=False)
                      if not (_it.name or "").upper().endswith(
                          ("_ID", "_DATE", "_DT"))
                      and (_it.name or "").upper() not in _det_names]
        if not _mid_items:
            _skip_card.add(mi)
            continue
        h = _emit_card_row(grp, f"ND_Card{mi}", "#ffffff",
                           explicit_items=_mid_items)
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
            # --- column-header row from the layout caption texts ---
            # Navy band only when the source is actually themed; a plain
            # receipt/list gets plain black-on-white headers, not invented navy.
            if palette.get("themed", True):
                hdr_bg = "#00008B"; hdr_fg = "#ffffff"
            else:
                hdr_bg = "#ffffff"; hdr_fg = "#000000"
            headers = []  # (text, x)
            # Column captions sit just ABOVE the detail row (smaller y). But
            # several labels can fall in that 0.6in window that are NOT column
            # headers: the report TITLE (a wide centered line just above the
            # header strip) and stray markers like "(continued)" / "Status:".
            # The REAL header strip is the single y-band whose labels line up
            # with the detail COLUMNS. Group the candidates by y-band and keep
            # only the band that best aligns with the detail-column x positions
            # -- so the title never renders as a navy column header overlapping
            # the real ones. Generic, geometry-only.
            # The outer-group BAND caption ("&<bcol> : N SITE(S)") and a
            # "(continued)" continuation marker both sit in the same y-window as
            # the real column-header strip, and the group caption is the CLOSER
            # band -- so the y tie-break would pick it and re-print the group
            # header as a column header (MCP_ACTIVE_SITES rendered
            # "Col_1 : N SITE(S)" where Oracle prints "Location"). The caption is
            # already painted as the group band (Tb_ND_Band*), so exclude it +
            # the "(continued)" marker; the genuine detail-column labels then win.
            _bctok = ("&" + bcol).lower() if bcol else None

            def _is_group_caption(t):
                tl = (t or "").strip().lower()
                if tl == "(continued)":
                    return True
                return bool(_bctok and _bctok in tl)

            _cand = [(text, lx, ly) for text, lx, ly, _bg in label_geo
                     if -0.02 <= (row_y - ly) <= 0.6 and text and "&<" not in text
                     and not _is_group_caption(text)]
            if _cand:
                _det_xs = [x for _s, x, _y, _w in row_layout]
                _bands = {}
                for text, lx, ly in _cand:
                    _bands.setdefault(round(ly, 1), []).append((text, lx))

                def _aligned(band):
                    return sum(1 for _t, lx in band
                               if any(abs(lx - dx) <= 0.4 for dx in _det_xs))

                # best-aligned band; tie -> the one closest to the detail row.
                _best_y = max(_bands, key=lambda y: (_aligned(_bands[y]), y))
                headers = list(_bands[_best_y])
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
                    # Resolve any Oracle &TOKEN in the caption (a &PARAM ->
                    # Parameters!..Value, a &FORMULA -> its placeholder) so the
                    # column-header band never prints a raw "&REPORT_VEHICLE_TYPE".
                    text, _ = _resolve_text_expression(text, report, main_ds)
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
    # A per-MASTER CARD band (geo_band: a colored master frame carrying >=2
    # labeled fields -- e.g. METHACT's green Complaint-ID card with FITS Site /
    # Incident Site / Location) is a full-page document per outer group, so it
    # page-breaks before each instance like Oracle. A thin group-CAPTION band
    # (the "<County-City> : N SITE(S)" list case, rendered as the single
    # Tb_ND_BandL caption) packs many groups per page; breaking per group there
    # emits a blank leading page + one sparse group per sheet (MCP_ACTIVE_SITES).
    # Gate the break on the band shape so the dense-list case reflows.
    if geo_band:
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
        # static card row for this group level -- skipped (row + member in
        # lockstep) when the card was a pure key/date band suppressed above, so
        # the tablix keeps TablixRows == innermost-member count.
        if mi not in _skip_card:
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


def _grouped_tabular_title(report):
    """The centered report title for a grouped-tabular report: the LARGEST-font
    static text in the layout (Oracle's title is the biggest type), with Oracle
    &TOKENs resolved -- not the lower-y "(continued)" carry-over marker or a
    column-header label that _extract_title_lines' y-ranking would otherwise
    pick. Mirrors the mockup's font-ranked title selection so the two AGREE."""
    cands = []

    def _walk(g):
        for f in (getattr(g, "fields", None) or []):
            t = (getattr(f, "text", "") or "").strip()
            if (getattr(f, "kind", "") == "text" and t and "&<" not in t
                    and not t.endswith(":") and t.lower() != "(continued)"):
                cands.append(f)
        for c in (getattr(g, "children", None) or []):
            _walk(c)
    for g in (report.layout or []):
        _walk(g)
    if not cands:
        return []
    tf = max(cands, key=lambda f: (int(getattr(f, "font_size", 0) or 0),
                                   -(getattr(f, "y", 0) or 0),
                                   len((getattr(f, "text", "") or "").strip())))
    try:
        from converter.preview.html_mockup import _resolve_tokens
        out = [_resolve_tokens(ln, 0).strip()
               for ln in (tf.text or "").splitlines() if ln.strip()][:3]
    except Exception:  # noqa: BLE001 -- never crash title resolution
        out = [re.sub(r"&[A-Za-z_]\w*", "", (tf.text or "")).strip()]
    return [ln for ln in out if ln]


def _build_grouped_tabular_subtotal_tablix(report, main):
    """Render a 2-level GROUPED TABULAR report with per-group SUBTOTALS as a
    grouped Tablix (the Oracle break report): bound to the DETAIL dataset,
    grouped on the break/join key, with -- per group -- a header band (break-key
    caption + Status), a column-header strip, the iterating detail row, and the
    group-footer TOTALS stack. Master-side fields (the break caption, status,
    and group totals that live in the MASTER dataset) are surfaced via Lookup on
    the join key; in-detail aggregates use Sum(). Additive + tightly gated (see
    _grouped_tabular_spec); falls back to the flat grid if extraction fails."""
    spec = _grouped_tabular_spec(report)
    if spec is None:
        return _build_tablix(report, main)
    main_ds = main.name or ""

    src2ds = {}
    for q in (report.queries or []):
        for it in (q.items or []):
            if it.name:
                src2ds.setdefault(it.name.upper(), q.name)
    formula_cols = {c.upper() for c in _formula_dataset_columns(report)}
    detail_items = {(it.name or "").upper() for it in (main.items or [])}

    # Master dataset = the OTHER query that owns the group-header field(s).
    master_ds = None
    for k, v, _x, _w in spec["group_header"]:
        if k == "field":
            ds = src2ds.get((v or "").upper())
            if ds and ds != main_ds:
                master_ds = ds
                break
    # Join key = a field name present in BOTH datasets (prefer an ID/SITE key).
    join_key = None
    if master_ds:
        master_items = {(it.name or "").upper()
                        for q in (report.queries or []) if q.name == master_ds
                        for it in (q.items or [])}
        common = [n for n in detail_items if n in master_items]
        common.sort(key=lambda n: (0 if ("ID" in n or "SITE" in n) else 1, len(n)))
        join_key = common[0] if common else None
    total_col = spec["detail_cols"][-1][2] if spec["detail_cols"] else None

    def _master_expr(src):
        if join_key and master_ds:
            return (f'=Lookup(Fields!{_safe(join_key)}.Value, '
                    f'Fields!{_safe(join_key)}.Value, Fields!{_safe(src)}.Value, '
                    f'"{master_ds}")')
        return f'=First(Fields!{_safe(src)}.Value)'

    def _value_expr(src):
        u = (src or "").upper()
        if u in detail_items:
            return f'=First(Fields!{_safe(src)}.Value)'
        ds = src2ds.get(u)
        if ds and ds != main_ds and join_key:
            return _master_expr(src)
        if total_col:
            return f'=Sum(Val(Fields!{_safe(total_col)}.Value))'
        return '="0"'

    def _label_expr(kind, val):
        if kind == "text":
            txt, is_expr = _resolve_text_expression(val, report, main_ds)
            # A literal that itself begins with '=' (Oracle's "= Vehicles in
            # Yards" running-total label) must be quoted into an expression, else
            # SSRS parses the leading '=' AS the expression marker and the label
            # is lost. (is_expr already-built expressions are emitted as-is.)
            if not is_expr and txt[:1] == "=":
                return '="' + txt.replace('"', '""') + '"'
            return txt
        if (val or "").upper() in formula_cols:
            return f'=First(Fields!{_safe(val)}.Value, "{_FORMULA_DATASET_NAME}")'
        return _value_expr(val)

    palette = _resolve_palette(report)
    themed = bool(spec.get("themed"))
    HDR_BG = palette.get("band_bg", "#ffffff") if themed else "#ffffff"
    HDR_FG = palette.get("band_fg", "#ffffff") if themed else "#111111"
    if not themed:
        HDR_BG, HDR_FG = "#ffffff", "#111111"

    cols = spec["col_headers"]
    dcols = spec["detail_cols"]
    ghdr = spec["group_header"]
    footers = spec["footers"]
    BODY_W = max([7.5] + [x + (w or 0.0) + 0.1 for x, w, _s in dcols]
                 + [x + 0.9 for x, _l in cols])
    BODY_W = min(BODY_W, 7.5)

    def _col_w(i, seq_x, last):
        nxt = seq_x[i + 1] if i + 1 < len(seq_x) else last
        return max(0.4, nxt - seq_x[i] - 0.02)

    tablix = ET.Element(_q("Tablix"))
    tablix.set("Name", "Tablix_GroupedSubtotal")
    tbody = _sub(tablix, "TablixBody")
    tcols = _sub(tbody, "TablixColumns")
    _sub(_sub(tcols, "TablixColumn"), "Width", f"{BODY_W:.2f}in")
    trows = _sub(tbody, "TablixRows")

    GLINE_H = 0.22
    FLINE_H = 0.28   # footer lines: roomier so a long placeholder doesn't collide
    hdr_h = GLINE_H * 2 + 0.06
    det_h = 0.20
    ftr_h = max(0.20, FLINE_H * len(footers) + 0.04)

    # ---- ROW 0: group header band + column-header strip ----
    hrow = _sub(trows, "TablixRow"); _sub(hrow, "Height", f"{hdr_h:.2f}in")
    hc = _sub(_sub(_sub(hrow, "TablixCells"), "TablixCell"), "CellContents")
    hrect = _sub(hc, "Rectangle"); hrect.set("Name", "GTS_Hdr")
    hst = _sub(hrect, "Style"); _sub(hst, "BackgroundColor", "#ffffff")
    _hbb = _sub(hst, "BottomBorder")
    _sub(_hbb, "Style", "Solid"); _sub(_hbb, "Color", "#444444"); _sub(_hbb, "Width", "1pt")
    hri = _sub(hrect, "ReportItems")
    for gi, (k, v, x, w) in enumerate(ghdr):
        val = _master_expr(v) if k == "field" else _label_expr(k, v)
        ta = "Right" if x > BODY_W * 0.6 else "Left"
        _build_textbox(hri, f"Tb_GH_{gi}", val, bold=True, font_size="11pt",
                       bg="#ffffff", fg="#111111", text_align=ta,
                       vertical_align="Middle", border_color="#ffffff",
                       padding="2pt", can_grow=False)
        _t = hri[-1]
        _sub(_t, "Top", "0in"); _sub(_t, "Left", f"{max(0.02, x):.2f}in")
        _sub(_t, "Width", f"{max(0.5, min(w or 2.0, BODY_W - x)):.2f}in")
        _sub(_t, "Height", f"{GLINE_H:.2f}in")
    col_xs = [c[0] for c in cols]
    for ci, (cx, label) in enumerate(cols):
        _build_textbox(hri, f"Tb_CH_{ci}", label, bold=True, font_size="9pt",
                       bg=HDR_BG, fg=HDR_FG, text_align="Left",
                       vertical_align="Middle", border_color=HDR_BG,
                       padding="2pt", can_grow=False)
        _t = hri[-1]
        _sub(_t, "Top", f"{GLINE_H:.2f}in"); _sub(_t, "Left", f"{max(0.02, cx):.2f}in")
        _sub(_t, "Width", f"{_col_w(ci, col_xs, BODY_W):.2f}in")
        _sub(_t, "Height", f"{GLINE_H:.2f}in")

    # ---- ROW 1: detail row (iterates per source row) ----
    drow = _sub(trows, "TablixRow"); _sub(drow, "Height", f"{det_h:.2f}in")
    dc = _sub(_sub(_sub(drow, "TablixCells"), "TablixCell"), "CellContents")
    drect = _sub(dc, "Rectangle"); drect.set("Name", "GTS_Detail")
    _sub(_sub(drect, "Style"), "BackgroundColor", "#ffffff")
    dri = _sub(drect, "ReportItems")
    det_xs = [c[0] for c in dcols]
    for di, (x, w, s) in enumerate(dcols):
        _build_textbox(dri, f"Tb_D_{di}", f"=Fields!{_safe(s)}.Value",
                       font_size="9pt", bg="#ffffff", fg="#111111",
                       text_align="Left", vertical_align="Top",
                       border_color="#ffffff", padding="2pt", can_grow=False)
        _t = dri[-1]
        _sub(_t, "Top", "0in"); _sub(_t, "Left", f"{max(0.02, x):.2f}in")
        _sub(_t, "Width", f"{_col_w(di, det_xs, BODY_W):.2f}in")
        _sub(_t, "Height", f"{det_h:.2f}in")

    # ---- ROW 2: group-footer totals stack ----
    frow = _sub(trows, "TablixRow"); _sub(frow, "Height", f"{ftr_h:.2f}in")
    fc = _sub(_sub(_sub(frow, "TablixCells"), "TablixCell"), "CellContents")
    frect = _sub(fc, "Rectangle"); frect.set("Name", "GTS_Footer")
    _sub(_sub(frect, "Style"), "BackgroundColor", "#ffffff")
    fri = _sub(frect, "ReportItems")
    for li, line in enumerate(footers):
        if not line:
            continue
        top = li * FLINE_H
        vk, vval, vx, _vw = line[-1]
        # The label column runs from the leftmost label up to the value column,
        # so a long staticized placeholder uses the full available run before it
        # would collide with the right-aligned total.
        lbl_left = min((x for _k, _v, x, _w in line[:-1]), default=max(0.02, vx - 1.5))
        for ji, (k, v, x, w) in enumerate(line[:-1]):
            _build_textbox(fri, f"Tb_FL_{li}_{ji}", _label_expr(k, v),
                           font_size="8pt", bg="#ffffff", fg="#111111",
                           text_align="Left", vertical_align="Middle",
                           border_color="#ffffff", padding="2pt", can_grow=False)
            _t = fri[-1]
            _sub(_t, "Top", f"{top:.2f}in"); _sub(_t, "Left", f"{max(0.02, x):.2f}in")
            _sub(_t, "Width", f"{max(0.6, vx - x - 0.04):.2f}in")
            _sub(_t, "Height", f"{FLINE_H:.2f}in")
        _build_textbox(fri, f"Tb_FV_{li}",
                       _value_expr(vval) if vk == "field" else _label_expr(vk, vval),
                       bold=True, font_size="8pt", bg="#ffffff", fg="#111111",
                       text_align="Right", vertical_align="Middle",
                       border_color="#ffffff", padding="2pt", can_grow=False)
        _t = fri[-1]
        _sub(_t, "Top", f"{top:.2f}in")
        _sub(_t, "Left", f"{max(0.02, vx):.2f}in")
        _sub(_t, "Width", f"{max(0.6, BODY_W - vx):.2f}in")
        _sub(_t, "Height", f"{FLINE_H:.2f}in")

    # column hierarchy (single static column)
    _sub(_sub(_sub(tablix, "TablixColumnHierarchy"), "TablixMembers"),
         "TablixMember")
    # row hierarchy: group(join_key) -> [header(static), detail(iter), footer(static)]
    rh = _sub(_sub(tablix, "TablixRowHierarchy"), "TablixMembers")
    gmem = _sub(rh, "TablixMember")
    grp = _sub(gmem, "Group"); grp.set("Name", "GTS_Group")
    gexprs = _sub(grp, "GroupExpressions")
    _grp_key = join_key or (dcols[0][2] if dcols else (total_col or "Group"))
    _sub(gexprs, "GroupExpression", f"=Fields!{_safe(_grp_key)}.Value")
    ginner = _sub(gmem, "TablixMembers")
    hmem = _sub(ginner, "TablixMember")
    _sub(hmem, "KeepWithGroup", "After"); _sub(hmem, "RepeatOnNewPage", "true")
    dmem = _sub(ginner, "TablixMember")
    _sub(dmem, "Group").set("Name", "GTS_DetailRows")
    fmem = _sub(ginner, "TablixMember")
    _sub(fmem, "KeepWithGroup", "Before")

    _sub(tablix, "DataSetName", _safe(main_ds))
    _sub(tablix, "Top", "0in"); _sub(tablix, "Left", "0in")
    _sub(tablix, "Height", f"{hdr_h + det_h + ftr_h:.2f}in")
    _sub(tablix, "Width", f"{BODY_W:.2f}in")
    _sub(_sub(tablix, "Style"), "PaddingTop", "2pt")
    return tablix


def _extract_title_lines(report, limit: int = 3):
    """Pull the centered title lines from the top of the report layout.
    Generic -- walks every layout group's text fields whose y-position
    is in the upper region of the page, sorts by y then x, returns the
    first `limit` non-noise strings."""
    seen = set()

    def _iter(group):
        yield group
        for ch in (group.children or []):
            yield from _iter(ch)

    # Display-constant parameter fields (a fixed Oracle initialValue, NOT a query
    # bind) positioned in the title band are SUBTITLE lines -- e.g. F_DIVISION
    # bound to &P_DIVISION = "DEQ Air Resources Management Bureau", or F_bureau ->
    # &P_BUREAU = "<...> Asbestos Control Program". A <text> title block can't hold
    # them (they're data fields), so without this the title loses its agency /
    # division sub-line. Resolve each to its literal default and let it sort into
    # the title block by its own y. Query-filter params (no initialValue, or used
    # as a :BIND) are excluded -- only fixed display constants become title lines.
    _binds = set()
    for q in (report.queries or []):
        for b in _detect_query_parameters(getattr(q, "tsql", "") or ""):
            _binds.add(b.upper())
        for b in _detect_oracle_bind_vars(getattr(q, "sql", "") or ""):
            _binds.add(b.upper())
    _pdefault = {}
    for p in (report.parameters or []):
        iv = (getattr(p, "initial_value", "") or "").strip()
        nm = (p.name or "").upper()
        if iv and nm not in _binds and ("P_" + nm) not in _binds:
            _pdefault[nm] = iv

    candidates = []  # (y, x, text, in_band)
    for top in (report.layout or []):
        for g in _iter(top):
            in_band = (getattr(g, "kind", "") or "").lower() in (
                "frame", "repeating_frame")
            for f in (g.fields or []):
                fk = getattr(f, "kind", "")
                if fk == "text":
                    text = (getattr(f, "text", "") or "").strip()
                    if not text:
                        continue
                    pieces = text.splitlines()
                elif fk == "field" and _pdefault:
                    src = (getattr(f, "source", "") or "").lstrip("&:").strip().upper()
                    iv = _pdefault.get(src)
                    if not iv:
                        continue
                    pieces = [iv]
                else:
                    continue
                for ln in pieces:
                    s = ln.strip()
                    if not s or s in seen:
                        continue
                    y = getattr(f, "y", 0) or 0
                    x = getattr(f, "x", 0) or 0
                    if y > 2.75:
                        continue
                    candidates.append((y, x, s, in_band))
                    seen.add(s)
    candidates.sort(key=lambda c: (c[0], c[1]))

    def _pick(cands):
        out = []
        for _y, _x, s, _b in cands:
            if s.endswith(":") or s.startswith("&") or s.startswith(":"):
                continue
            if len(s) > 120:
                continue
            up = s.upper()
            if up.startswith(("ERROR", "WARNING", "NOTE:")):
                continue
            # Skip Oracle lexical tokens (&P_TITLE, &<PageNumber>) -- an & that
            # is IMMEDIATELY followed by a letter/_/<. A standalone ampersand
            # used as "and" (e.g. "Permits & Notifications") is kept.
            if re.search(r"&[A-Za-z_<]", s):
                continue
            out.append(s)
            if len(out) >= limit:
                break
        return out

    # The real title lives at SECTION/MARGIN level. A repeating/header band
    # frame at the very top of the page holds the COLUMN-HEADER row
    # (e.g. "Permittee | Address | City", "Status: All Properties | Number")
    # which sits at a LOWER y than the centered title and would otherwise be
    # picked as the title. Prefer non-band text; fall back to all if none.
    lines = _pick([c for c in candidates if not c[3]])
    if not lines:
        lines = _pick(candidates)
    return lines


def _title_style(report):
    """Faithful title font + text color, read from the report's OWN title text.

    The converter must not impose a house theme (the historical Courier-New /
    navy ``#03047e`` look made every plain Oracle form read like a styled SSRS
    tablix). Instead, find the actual upper-region centered title field and
    return ITS font face and text color. Defaults: Arial / black -- a report
    whose title is plain Arial with no color renders plain black; one that
    titles in Times New Roman + darkblue keeps Times New Roman + darkblue.
    Name-agnostic and purely geometry/style driven."""
    from ..parsers.oracle_colors import resolve_color

    def _iter(group):
        yield group
        for ch in (group.children or []):
            yield from _iter(ch)

    best = None  # ((y, x), field)
    for top in (report.layout or []):
        for g in _iter(top):
            for f in (g.fields or []):
                if getattr(f, "kind", "") != "text":
                    continue
                text = (getattr(f, "text", "") or "").strip()
                if not text:
                    continue
                s0 = text.splitlines()[0].strip()
                if not s0 or s0.endswith(":") or s0.startswith(("&", ":")):
                    continue
                y = getattr(f, "y", 0) or 0
                if y > 2.75:
                    continue
                x = getattr(f, "x", 0) or 0
                key = (round(y, 3), round(x, 3))
                if best is None or key < best[0]:
                    best = (key, f)
    font = "Arial"
    color = "#000000"
    if best is not None:
        bf = best[1]
        if getattr(bf, "font_family", ""):
            font = bf.font_family
        hexc = resolve_color(getattr(bf, "color", "") or "")
        if hexc:
            color = hexc
    return font, color


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
                "SummHdr", counter, skip_repeating=True,
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
    _ct_font, TITLE_FG = _title_style(report)
    INK = "#282828"

    rect = ET.Element(_q("Rectangle"))
    rect.set("Name", "Rect_CoverPage")
    _sub(rect, "KeepTogether", "true")
    style = _sub(rect, "Style")
    # The real Oracle criteria cover is a plain label:value list on white paper --
    # NO border box (verified against the MVWF/CMVGY letter-cover truth). A drawn
    # box was an invented frame, so the cover border is None.
    border = _sub(style, "Border")
    _sub(border, "Style", "None")
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
            font_family=_ct_font,
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


def _cover_text_value(text: str, report: ParsedReport) -> str:
    """Scope-SAFE SSRS <Value> for a letter-cover TEXT element that may carry
    Oracle &TOKENs (e.g. "&SITE_NAME (&PERM_NAME)").

    The cover Rectangle sits at report-body scope (no row context) and the
    report usually has several datasets, so a BARE Fields! reference would throw
    an ambiguous-scope error at render time. _resolve_text_expression maps
    &PARAM -> Parameters!..Value (scope-free) and &FORMULA ->
    First(Fields!.., "ds") (scope-safe); only a plain DATA-field token yields a
    bare Fields!. In that (corpus-absent) case keep the raw literal rather than
    emit an expression that fails to render. Generic -- never a report name."""
    raw = text or ""
    val, is_expr = _resolve_text_expression(raw, report, "")
    if not is_expr:
        return '="' + raw.replace('"', '""') + '"'
    # Reject any Fields! NOT wrapped in First( -- unsafe at body scope.
    for m in re.finditer(r"Fields!", val):
        if val[max(0, m.start() - 6):m.start()] != "First(":
            return '="' + raw.replace('"', '""') + '"'
    return val


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
            raw_ct = (cont_text.text or "").strip()
            # Resolve any &TOKEN scope-safely (-> a quoted literal, or a
            # Parameters!/First(Fields!) expression) so a hyperlink note like
            # "... is a hyperlink to &CP_CHILD_REPORT" never prints raw.
            ct_val = _cover_text_value(raw_ct, report)
            if pairs and raw_ct:
                prev_lbl, prev_val, prev_vf = pairs[-1]
                # Chain into the previous value only when BOTH are plain static
                # literals; a resolved-token EXPRESSION can't be string-spliced
                # into a literal, so emit it as its own note row.
                if prev_val.startswith('="') and ct_val.startswith('="'):
                    pairs[-1] = (prev_lbl, prev_val[:-1] + ' ' + ct_val[2:],
                                 prev_vf)
                else:
                    pairs.append(("", ct_val, None))
            elif raw_ct:
                pairs.append(("", ct_val, None))
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
                # Resolve any &TOKEN scope-safely (a &PARAM/&FORMULA cover value)
                # so a raw "&SITE_NAME (&PERM_NAME)" never prints on the cover.
                value_expr = _cover_text_value(right.text or "", report)
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

    _ct_font, TITLE_FG = _title_style(report)
    INK = "#282828"

    rect = ET.Element(_q("Rectangle"))
    rect.set("Name", "Rect_CoverPage")
    _sub(rect, "KeepTogether", "true")
    style = _sub(rect, "Style")
    # Oracle's criteria cover is a borderless label:value list on white paper
    # (verified against the MVWF + CMVGY letter-cover truth — page 1 of each is
    # a plain list, no drawn box). A Solid border was an invented frame.
    border = _sub(style, "Border")
    _sub(border, "Style", "None")
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
            font_family=_ct_font,
        )
        tb = ri[-1]
        _sub(tb, "Top", f"{y:.2f}in"); _sub(tb, "Left", "0.15in")
        _sub(tb, "Width", "6.50in"); _sub(tb, "Height", f"{title_h:.2f}in")
        y += title_h + 0.40

    # NO fabricated "Run Date / Total of ALL Records" meta band here.
    # This is a LAYOUT-DRIVEN cover: the report's own header section already
    # carries its genuine "Run Date:" / "Selection Criteria:" / etc. labels,
    # which render as the `pairs` below. Injecting a CountRows() "Total of ALL
    # Records" line (and a second Run Date) duplicates and fabricates content
    # the real Oracle cover never prints -- verified against the real
    # letter/permit artifacts. Spacing only.
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


def _set_zindex(el, z):
    """Insert <ZIndex> at the RDL sequence position (immediately AFTER <Width>,
    else the last present geometry element) so a report item's overlap order is
    EXPLICIT. This ReportViewer build ignores document order for equal-ZIndex
    peers, so a seal/watermark image only renders behind text when given an
    explicit lower ZIndex."""
    if el.find(_q("ZIndex")) is not None:
        return
    kids = list(el)
    idx = len(kids)
    for tag in ("Width", "Height", "Left", "Top"):
        anchor = next((i for i, k in enumerate(kids) if k.tag == _q(tag)), None)
        if anchor is not None:
            idx = anchor + 1
            break
    zi = ET.Element(_q("ZIndex"))
    zi.text = str(z)
    el.insert(idx, zi)


def _layer_images_behind_text(container):
    """In every <ReportItems> that mixes an <Image> with other items, force the
    image(s) BEHIND via explicit ZIndex (Image=0, every sibling=1). A seal /
    watermark image positioned over the document body otherwise paints ON TOP of
    the prose / numbered conditions / signature it should sit behind, because the
    engine does not honor document order for equal-ZIndex overlap. General --
    fires wherever a positioned image shares a container with other items."""
    for ri in container.iter(_q("ReportItems")):
        kids = list(ri)
        if len(kids) < 2 or not any(k.tag == _q("Image") for k in kids):
            continue
        for k in kids:
            _set_zindex(k, "0" if k.tag == _q("Image") else "1")


def _emit_field_textbox(
    parent_items, name, value, lf, ox, oy, rect_w, rect_h, report,
    cover_title_lines, value_override=None,
):
    """Emit ONE LayoutField as a Textbox positioned relative to its
    containing frame's (ox, oy) origin. Used by _emit_frame_rect.
    Returns (emitted_bool, bottom_y_relative).

    value_override: when a caller has already resolved the field's value
    expression (e.g. a cross-query Lookup the dataset-local _field_value_for
    can't synthesize), pass it here to bypass value derivation while keeping
    all geometry / font / rotation / drillthrough handling."""
    kind = getattr(lf, "kind", "field") or "field"
    text = (getattr(lf, "text", "") or "").strip()
    source = (getattr(lf, "source", "") or "").strip()

    # Oracle visible="no" -> a computation-only field (a hidden CF_/CS_ statistic
    # that feeds a body-paragraph &token, e.g. CMVGY's CF_Avg_Haul); never draw
    # it. Mirrors the mockup's hidden-field skip so preview and RDL agree.
    if not getattr(lf, "visible", True):
        return (False, 0.0)
    # An Oracle *_ERROR / ERR_* formula/placeholder field is a conditional
    # error/empty-state message printed ONLY via a format trigger on the failure
    # path; the happy path hides it. We can't evaluate the trigger, so suppress
    # it (its formula text -- e.g. "...does NOT equal...<ERROR - ...>" -- would
    # otherwise overlap the real letter body). Mirrors html_mockup's
    # _is_conditional_error_source. Word-boundary keyed, so OPERATOR/TERMS/VENDOR
    # are never caught.
    if kind == "field" and re.search(r"(?i)(^|_)err(or)?($|_)", source):
        return (False, 0.0)

    # Oracle page-trailer boilerplate (a text field whose content is PURELY page
    # builtins -- &<PageNumber>, &<TotalPages>, &<PhysicalPageNumber>,
    # &<PanelNumber>, ... -- plus connectives like "Page"/"of"). SSRS
    # Globals!PageNumber is valid ONLY in page header/footer scope; emitting it in
    # the body both fails to evaluate and overprints body rows. Page numbering is
    # non-essential chrome here, so suppress the body copy. Word-residue gate keeps
    # real text that merely mentions a page token (e.g. "See page &<PageNumber> for
    # the appendix") from being dropped.
    if kind == "text" and re.search(
        r"&<\s*(?:total)?(?:page|panel|physical|logical)", text, re.I
    ):
        _resid = re.sub(r"&<[^>]*>", "", text)
        _resid = re.sub(r"(?i)\b(?:page|of)\b", "", _resid)
        _resid = re.sub(r"[\s/\-,.:#&]+", "", _resid)
        if not _resid:
            return (False, 0.0)
    # Oracle module-filename trailer (a text field whose entire content is the
    # source report's own file name, e.g. "CMVGY_GRANT_STATUS.rdf"). Oracle prints
    # this in a developer/margin trailer next to the page number; it is the source
    # artifact name, never report content, and would otherwise leak into the body.
    # Same skip the title path already applies (".rdf"-suffixed text is ignored
    # there). Anchored to a bare filename so prose mentioning a file is untouched.
    if kind == "text" and re.match(
        r"(?i)^[\w .#-]+\.(?:rdf|rep|rex|rdl|jsp)\.?\s*$", text
    ):
        return (False, 0.0)

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

    # A DRAWN graphic (<rectangle>/<box>/<line>): an empty bordered box around a
    # panel, or a horizontal/vertical rule. No data -- just an outline/bar at the
    # Oracle geometry. A box is a solid border with a transparent interior (so it
    # frames the text inside it); a rule is a thin filled bar = the line weight.
    if kind in ("rect", "line"):
        fx_g = float(getattr(lf, "x", 0.0) or 0.0)
        fy_g = float(getattr(lf, "y", 0.0) or 0.0)
        gw = float(getattr(lf, "width", 0.0) or 0.0)
        gh = float(getattr(lf, "height", 0.0) or 0.0)
        bw_pt = float(getattr(lf, "border_width", 0.0) or 0.0) or 1.0
        bcolor = (getattr(lf, "border_color", "") or "#000000")
        g_left = max(0.02, fx_g - ox)
        g_top = max(0.02, fy_g - oy)
        bw_in = min(0.06, max(0.01, bw_pt / 72.0))
        rg = _sub(parent_items, "Rectangle")
        rg.set("Name", name)
        rstyle = _sub(rg, "Style")
        rb = _sub(rstyle, "Border")
        # A <rectangle> with real width AND height is a BOX (outline); anything
        # with a near-zero dimension is a RULE drawn along its longer axis.
        if kind == "rect" and gw > 0.05 and gh > 0.05:
            # Box outline: solid border, transparent interior (frames its text).
            _sub(rb, "Style", "Solid")
            _sub(rb, "Color", bcolor)
            _sub(rb, "Width", f"{bw_pt:g}pt")
            g_w = max(0.02, min(gw, rect_w - g_left - 0.02))
            g_h = max(0.02, gh)
        else:
            # A rule: a thin filled bar along its LONGER axis. A horizontal rule
            # (width>height, e.g. a section underline) is full-width & line-thick;
            # a vertical rule (height>width, e.g. a table column separator) is
            # line-thin & full-height. Honoring orientation keeps a zero-width
            # vertical tick from becoming a spurious full-width horizontal bar.
            _sub(rstyle, "BackgroundColor", bcolor)
            _sub(rb, "Style", "None")
            if gh > gw:
                g_w = bw_in
                g_h = max(bw_in, gh)
            else:
                g_w = max(bw_in, min(gw if gw > 0 else rect_w,
                                     rect_w - g_left - 0.02))
                g_h = bw_in
        _sub(rg, "Top", f"{g_top:.2f}in")
        _sub(rg, "Left", f"{g_left:.2f}in")
        _sub(rg, "Height", f"{g_h:.2f}in")
        _sub(rg, "Width", f"{g_w:.2f}in")
        return (True, g_top + g_h)

    bold = bool(getattr(lf, "bold", False))
    italic = bool(getattr(lf, "italic", False))
    fs = getattr(lf, "font_size", 0) or 10
    # Oracle font FACE: parsed but (like the tablix path) was never passed
    # through here, so positioned per-record/document fields fell back to the
    # default Arial. Carry it (None -> SSRS default).
    fam = (getattr(lf, "font_family", "") or "").strip() or None
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
                # Oracle pads multi-line text with blank / whitespace-only SPACER
                # lines ("A \n\n   \n   B"); its renderer collapses each run to a
                # single break so the content prints CONSECUTIVELY. Keeping the
                # spacers here would emit ~12 lines for a 4-line address into a
                # fixed-height box (CanGrow stays False for positional fidelity --
                # see below), clipping the tail (signatory city/phone, the last
                # numbered clause). Drop the spacer lines to match Oracle.
                lines = [ln.strip() for ln in text.split(chr(10)) if ln.strip()]
                esc = [ln.replace('"', '""') for ln in lines]
                value_expr = ('=' + ' & vbCrLf & '.join(
                    '"' + ln + '"' for ln in esc)) if esc else '=""'
        elif value_override:
            value_expr = value_override
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

    # Oracle rotationAngle -> SSRS WritingMode. 270deg (a sideways window-envelope
    # address) maps to Rotate270 (text turned 90deg CCW, reading bottom-to-top);
    # 90deg maps to Vertical (90deg CW). Other angles stay upright (RDL has no
    # arbitrary-angle text rotation).
    _rot = float(getattr(lf, "rotation", 0.0) or 0.0)
    _wmode = None
    if 247.5 <= _rot < 292.5:
        _wmode = "Rotate270"
    elif 67.5 <= _rot < 112.5:
        _wmode = "Vertical"

    _build_textbox(
        parent_items, name, value_expr,
        bold=bold, italic=italic, font_family=fam,
        font_size=font_size, fg=fcolor,
        text_align=text_align, vertical_align="Top",
        border_color="#ffffff", padding="2pt",
        writing_mode=_wmode,
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
    skip_repeating=False,
):
    """Emit a LayoutGroup as a bordered Rectangle nested under
    parent_items. Coords inside the rect are relative to the group's
    own origin (mirrors html_mockup._render_frame).

    Generic - draws a 1pt border whenever the source XML's
    border_width > 0. Recurses into nested frames so child cards /
    sub-frames each get their own bordered Rectangle.
    """
    # A conditional ERROR/alert frame (format-trigger box whose content is a
    # not-equal / ERROR message, e.g. a totals-mismatch warning) is hidden on
    # the happy path -- never emit it into the RDL, where it would paint over
    # the normal form. Mirrors the mockup's _doc_collect_positioned skip so
    # preview and RDL agree.
    if _is_conditional_alert_frame(group):
        return parent_y
    # A conditional ERROR/empty-state frame named by the err/error convention
    # AND gated by a format trigger (e.g. M_PERMITEE_ERROR / M_CONTACT_ERROR,
    # holding a CP_*_ERROR field + a fallback "&CF_PARA_n" body that prints ONLY
    # when the master lookup fails). Oracle hides it via the trigger on the happy
    # path; a static convert can't evaluate the trigger, so emitting it stacks a
    # duplicate body paragraph at the top of the letter. The mockup drops these
    # at the FIELD level (_is_conditional_error_source on the field source); apply
    # the same convention at the FRAME level so RDL and preview agree. Gated on
    # BOTH a format trigger AND the name convention, so an UNCONDITIONAL frame
    # (no trigger -> always prints, e.g. CLP's M_Error_Contact_G) is never
    # dropped, and a frame merely named M_TERMS / M_VENDOR never matches.
    _gname = getattr(group, "name", "") or ""
    if (getattr(group, "format_trigger", "")
            and re.search(r"(?i)(^|_)err(or)?($|_)", _gname)):
        return parent_y
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
        # In a header-resident summary cover (CMVGY_GRANT_STATUS), the stat table
        # carries CONDITIONAL grantee/site LIST repeating frames (R_Budget_C,
        # R_Itemized_MI, R_Quarter_MQ3 -- "Permittee"/"&Permittee : &Site" values)
        # that Oracle hides when "Include Grantee/Site Lists on Summary Page" = NO.
        # Emitting them overprints the static stat cells (overlapping big glyphs).
        # Skip every repeating sub-frame so only the stat values remain. Gated by
        # skip_repeating (set ONLY on the header-summary cover path), so the
        # per-record bodies that legitimately tile repeating frames are unaffected.
        if skip_repeating and "repeating" in ckind:
            continue
        if "frame" in ckind or ckind == "repeating_frame":
            cy = _emit_frame_rect(
                inner, child, gx, gy, rect_w, report,
                cover_title_lines, name_prefix, counter, skip_repeating,
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


def _name_derived_title(report):
    """A clean report title derived from the report NAME, acronym-preserving:
    underscores -> spaces; a token with NO vowels stays UPPER (an acronym, e.g.
    CMVGY), every other token is Capitalized (GRANT -> Grant). "CMVGY_GRANT_
    STATUS" -> "CMVGY Grant Status". Used when the real title is a formula value
    the text-field title picker can't see."""
    nm = (getattr(report, "name", "") or "").strip()
    if not nm:
        return []
    words = []
    for tok in re.split(r"[_\s]+", nm):
        if not tok:
            continue
        words.append(tok if not re.search(r"[AEIOUaeiou]", tok) else tok.capitalize())
    return [" ".join(words)] if words else []


def _resolved_title_lines(report):
    """Title lines for the PageHeader, minus the generic 'Report
    Parameters' caption (which is never a real report title)."""
    # A HEADER-RESIDENT summary report's real title is a FORMULA value (CMVGY's
    # CP_CMVGY_GRANT_STATUS -> "CMVGY Grant Accounting Status"), a data field the
    # text-only title picker can't read; it instead grabs the section_main
    # asterisk-grid COLUMN-HEADER fragments ("Grant"/"Registered"/"Vehicles") and
    # leaks them into every page header. Derive a clean name-based title instead.
    # Gated on _is_header_summary_report -> True for ONLY CMVGY_GRANT_STATUS, so
    # no other report's title changes.
    if _is_header_summary_report(report):
        _nt = _name_derived_title(report)
        if _nt:
            return _nt
    # A grouped-tabular break report's real title (largest font, e.g. "Motor
    # Vehicle County Graveyard Logsheets for ...") carries an unresolved &TOKEN,
    # so the generic y-ranked picker skips it and grabs the lower "(continued)"
    # carry-over marker. Resolve it font-ranked instead (gated -> only this
    # archetype is affected; every other report's title is unchanged).
    if _is_grouped_tabular_subtotal(report):
        _gt = _grouped_tabular_title(report)
        if _gt:
            return [ln for ln in _gt
                    if not re.fullmatch(r"(report\s+)?parameters?", ln.strip(),
                                        re.IGNORECASE)]
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


def _build_packet_body(report, main):
    """Body for a positional document PACKET -- a memo cover, a data table, and
    a closing letter as sibling top-level frames in section_main, each its own
    page via Oracle pageBreakAfter. Each frame renders in its own KeepTogether
    Rectangle (prose frames via _emit_frame_rect; the data-table frame wraps a
    real _build_tablix), stacked vertically; a frame whose page_break_after is
    set gets PageBreak=End.

    Fixes the data loss where the generic tabular path emitted only a param
    cover + the table, DROPPING the memo and the closing letter. Mirrors the
    html_mockup packet preview.

    NOT render-verifiable in this environment (ReportViewer is WDAC-blocked).
    The structure is XSD-valid and the field/table emission reuses the proven
    letter + tablix paths; SSRS's absolute-Top page-break behaviour means the
    user should upload to confirm the exact pagination. The guaranteed win is
    content fidelity -- all three sections present + faithfully positioned.
    """
    body = ET.Element(_q("Body"))
    items = _sub(body, "ReportItems")
    main_sec = _section_by_kind(report, "section_main")
    frames = [c for c in (getattr(main_sec, "children", None) or [])
              if "frame" in (getattr(c, "kind", "") or "").lower()
              and "footer" not in (getattr(c, "name", "") or "").lower()]
    if not frames:
        _sub(body, "Height", "9in")
        _sub(body, "Style")
        return body

    _all_r = [float(getattr(c, "x", 0) or 0) + float(getattr(c, "width", 0) or 0)
              for c in frames]
    _MAX_BODY_W = _page_width_for(report) - 2 * _PAGE_HMARGIN_IN - 0.02
    BODY_W = min(_MAX_BODY_W, max(7.5, round(max(_all_r) + 0.15, 2)))

    counter = [0]
    cover_title_lines: set = set()
    top = 0.0
    GAP = 0.20
    last = len(frames) - 1
    for i, fr in enumerate(frames):
        is_table = main is not None and _frame_has_columnar_table(fr)
        if is_table:
            # The data-table frame: wrap the real data-bound grid in a Rectangle
            # so it stacks + can carry a PageBreak uniformly with the prose
            # sections (a plain _emit_frame_rect would print ONE static row).
            tbx = _build_tablix(report, main)
            for _t in tbx.findall(_q("Top")):
                tbx.remove(_t)
            for _l in tbx.findall(_q("Left")):
                tbx.remove(_l)
            rect = ET.Element(_q("Rectangle"))
            rect.set("Name", f"Pkt_Sect_{i}")
            _sub(rect, "KeepTogether", "true")
            inner = _sub(rect, "ReportItems")
            _sub(tbx, "Top", "0.02in")
            _sub(tbx, "Left", "0.02in")
            inner.append(tbx)
            _sub(_sub(_sub(rect, "Style"), "Border"), "Style", "None")
            sect_h = 2.5
            _sub(rect, "Top", f"{top:.2f}in")
            _sub(rect, "Left", "0.02in")
            _sub(rect, "Width", f"{BODY_W:.2f}in")
            _sub(rect, "Height", f"{sect_h:.2f}in")
            items.append(rect)
            sect_item = rect
        else:
            # Prose frame (memo / letter): positioned textboxes + images,
            # re-based to the frame origin (parent_x=parent_y=0 keeps each
            # field's absolute x; the rect's Top is overridden below to stack).
            _emit_frame_rect(items, fr, 0.0, 0.0, BODY_W,
                             report, cover_title_lines, "Pkt", counter)
            sect_item = list(items)[-1]
            _h_el = sect_item.find(_q("Height"))
            try:
                sect_h = float((_h_el.text or "1").replace("in", "")) if _h_el is not None else 1.0
            except Exception:
                sect_h = 1.0
            _top_el = sect_item.find(_q("Top"))
            if _top_el is not None:
                _top_el.text = f"{top:.2f}in"  # in place -> preserve XSD child order
            else:
                _sub(sect_item, "Top", f"{top:.2f}in")
        # Honour Oracle pageBreakAfter (never on the last section).
        if i != last and getattr(fr, "page_break_after", False):
            _sub(_sub(sect_item, "PageBreak"), "BreakLocation", "End")
        top += sect_h + GAP

    _sub(body, "Height", f"{max(9.0, top + 1.0):.2f}in")
    _sub(body, "Style")
    return body


def _is_summary_trailer_frame(fr) -> bool:
    """A section_main top-level frame that is a REPORT-WIDE summary TRAILER: it
    has NO repeating-frame descendant (so it is not per-record data) AND contains
    a field whose source is a report TOTAL (`..._TOTAL`, e.g. CF_APP_TOTAL). Such
    a frame -- MVWFR's M_REPORT_SUMMARY_FTR (Application-Status + MVWFR-Status
    count tables) -- prints ONCE at the report end, not on every per-record page.
    Tightly keyed (corpus scan: only MVWFR matches), so a per-record document with
    a plain page-footer frame (page#/date, no totals) is never pulled out."""
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


def _build_per_record_body(report, main, suppress_empty_cover=False):
    """Build a Body that renders ONE PAGE PER RECORD of the main dataset.

    ``suppress_empty_cover``: when True (the positional single-record FORM path),
    do NOT prepend the generic "Report Parameters" cover unless the report
    actually carries cover content (a real section_header criteria cover). A
    requisition/invoice form has no cover page -- page 1 is the form -- so the
    fabricated Run Date/Run By/Total cover would invent a page. Letters and
    certificates (default False) keep their cover, which IS faithful to their
    real Parameter-Form first page.

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
    if cover is None and _has_cover_page(report):
        # Fall back to the generic "Report Parameters" cover ONLY when the report
        # actually carries cover content (a real section_header criteria/parameter
        # display). A positional FORM or a single-page CERTIFICATE/LETTER whose
        # source has NO criteria section (VOLUNTARY registration, wallet card) must
        # NOT get a fabricated "Run Date / Run By / Total of ALL Records" page --
        # the real document never prints one. Letters that DO display criteria
        # (CMVGY letters, MVWF letter) build their faithful cover via
        # _build_letter_cover_page above, so they are unaffected. (suppress_empty_
        # cover is retained for API compatibility; the _has_cover_page gate now
        # governs the cover uniformly for the form and letter/cert paths.)
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
        # blank pages. PageWidth is 8.5 (portrait) for normal reports -> 7.98,
        # but WIDENS for landscape content so wide grids aren't compressed.
        _MAX_BODY_W = _page_width_for(report) - 2 * _PAGE_HMARGIN_IN - 0.02
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
    # Split off any REPORT-WIDE summary TRAILER frame (a totals frame with no
    # repeating descendant, e.g. MVWFR's Application/MVWFR-Status count tables).
    # It must print ONCE at the report end via a static Tablix trailer row, NOT on
    # every per-record page. Only split when a non-trailer (record) frame remains.
    _trailer_frames = [c for c in frame_children if _is_summary_trailer_frame(c)]
    if _trailer_frames and len(_trailer_frames) < len(frame_children):
        frame_children = [c for c in frame_children if c not in _trailer_frames]
    else:
        _trailer_frames = []
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
        # Skip the flat positional loop -- frames already handled it. The
        # section_main-direct top-of-page header band (a title subtitle bound to
        # &P_DIVISION, the run date, the heavy rule) is page-margin chrome that
        # OVERLAPS the repeating frame's coordinate space -- emitting it into the
        # body collides with the record content. Its title/subtitle lines flow
        # through the PageHeader instead (see _resolved_title_lines).
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
        italic = bool(getattr(f, "italic", False))
        fs = getattr(f, "font_size", 0) or 10
        fam = (getattr(f, "font_family", "") or "").strip() or None
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
            bold=bold, italic=italic, font_family=fam,
            font_size=font_size, fg=fcolor,
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

    # ---- Report-wide SUMMARY TRAILER row -------------------------------------
    # A static 2nd Tablix row that renders ONCE after the last record (the totals
    # frame split out above, e.g. MVWFR's Application/MVWFR-Status count tables).
    # Its own Group(=1) renders it exactly once; PageBreak=Start puts it on its
    # own final page -- matching the Oracle report trailer, instead of repeating
    # the summary on every per-record page.
    _trailer_h = 0.0
    if _trailer_frames:
        _tr_row = _sub(rows, "TablixRow")
        _tr_cells = _sub(_tr_row, "TablixCells")
        _tr_cell = _sub(_tr_cells, "TablixCell")
        _tr_contents = _sub(_tr_cell, "CellContents")
        _tr_rect = _sub(_tr_contents, "Rectangle")
        _tr_rect.set("Name", "Rect_ReportTrailer")
        _sub(_tr_rect, "KeepTogether", "true")
        _sub(_sub(_sub(_tr_rect, "Style"), "Border"), "Style", "None")
        _tr_items = _sub(_tr_rect, "ReportItems")
        _tr_base_y = min(float(getattr(c, "y", 0.0) or 0.0) for c in _trailer_frames)
        _tr_counter = [0]
        _tr_max = 0.0
        for _tc in _trailer_frames:
            _by = _emit_frame_rect(_tr_items, _tc, 0.0, _tr_base_y, BODY_W,
                                   report, set(), "Trl", _tr_counter)
            if _by is not None:
                _tr_max = max(_tr_max, _by)
        _trailer_h = max(0.5, _tr_max + 0.2)
        _sub(_tr_row, "Height", f"{_trailer_h:.2f}in")
        _tr_member = _sub(row_members, "TablixMember")
        _tr_group = _sub(_tr_member, "Group")
        _tr_group.set("Name", "ReportTrailer")
        _sub(_sub(_tr_group, "GroupExpressions"), "GroupExpression", "=1")
        _sub(_sub(_tr_group, "PageBreak"), "BreakLocation", "Start")

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
    _sub(tablix, "Height", f"{rect_h + _trailer_h:.2f}in")
    _sub(tablix, "Width", f"{BODY_W}in")
    _sub(tablix, "Style")

    items.append(tablix)

    # Body must end EXACTLY at the Tablix bottom. Any slack below the last
    # record (even 0.1in) is rendered as body whitespace AFTER the final
    # record and spills onto a TRAILING BLANK PAGE (measured with the real
    # MS engine: 0.6in slack -> one blank last page on every run).
    body_height_in = round(tablix_top + rect_h + _trailer_h, 2)
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
    # Seal / watermark images must sit BEHIND the body prose they overlap.
    _layer_images_behind_text(body)
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
    # Section frames usually sit directly under section_main, but an accounting
    # report wraps its ~9 count/fee sections INSIDE one body container frame, and
    # stacks one query-bound REPEATING group frame per section (not plain
    # frames). Mirror the preview's _detect_multi_section_preview: search each
    # container level (section_main + its child frames) and count repeating
    # group-frames as sections too (only when >=2 on DISTINCT queries, so a
    # genuine single-master nested-MD is never split). Pick the level with the
    # most section frames.
    def _frame_has_tables(frame):
        st = [frame]
        while st:
            g = st.pop()
            if ((g.kind or "").lower() == "repeating_frame"
                    and getattr(g, "source_query", None)
                    and any((f.kind or "") == "field" and (f.source or "")
                            for f in (g.fields or []))):
                return True
            st.extend(g.children or [])
        return False

    def _section_frames(container):
        kids = list(container.children or [])
        plain = [c for c in kids
                 if "frame" in (c.kind or "").lower()
                 and (c.kind or "").lower() != "repeating_frame"
                 and _frame_has_tables(c)]
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
                        # Keep the band's full caption (join non-blank lines) so a
                        # two-line Oracle band header -- "Historical Property
                        # Status:  Properties prior to Oct. 1, 2005" -- doesn't lose
                        # its subtitle. Single-line captions are unaffected.
                        _cap = " ".join(ln.strip() for ln in t.split("\n")
                                        if ln.strip())
                        cands.append((float(getattr(f, "y", 0.0) or 0.0),
                                      float(getattr(f, "x", 0.0) or 0.0), _cap))
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

    def _footer_totals(frame):
        # The Oracle group-footer sub-frame(s) carry the section's REAL total-row
        # LABELS ("Total Properties Closed", "Total Applications"). Return them
        # ordered by y so the section renders its true labeled totals instead of
        # one generic "Total". The total VALUE stays an =Sum() of the table's own
        # count column (already correct); only the label + presence change here.
        # A label is a static <text> mentioning "total" that is NOT inside a
        # repeating frame (those are data rows) and NOT a page/.rdf trailer.
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
        # De-dup while preserving order (a label can repeat across sub-frames).
        seen, out = set(), []
        for _y, lt in labels:
            if lt.lower() not in seen:
                seen.add(lt.lower())
                out.append(lt)
        return out

    sections = []
    distinct_q = set()
    def _has_aggregate(frame):
        # A section shows a total/subtotal ONLY if Oracle actually computes one --
        # i.e. it carries a SUMMARY field: a reset-at-group Sum* column or a CS_/CF_
        # summary formula. A plain list section (label + count, no aggregate, e.g.
        # METHQTRRPT's "Actions") has none, so it must NOT get a spurious total.
        st = [frame]
        while st:
            g = st.pop()
            for f in (g.fields or []):
                if (f.kind or "") == "field" and re.match(
                        r"(?i)^(sum|cs_|cf_)", (f.source or "").strip()):
                    return True
            st.extend(g.children or [])
        return False

    def _band_col_headers(frame):
        # The Oracle header sub-frame's column captions (the texts to the RIGHT of
        # the section title in the top band) -- "Number", or "Applications"/"Fees".
        # Mirrors html_mockup.band_col_headers; used here to spot a single-summary-
        # line section (a named band with no "Number" caption + just an aggregate).
        cands = []

        def walk(g, in_rep):
            ir = in_rep or (g.kind or "").lower() == "repeating_frame"
            for f in (g.fields or []):
                if (f.kind or "") == "text" and not ir:
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
        return [c[2] for c in band[1:]]

    def _band_col_headers_deep(frame):
        # Fallback for an Oracle break report whose value-column captions
        # ("Applications"/"Fees") live INSIDE the section's header REPEATING frame
        # -- so the repeating-frame-excluding scan above misses them. Collect TEXT
        # fields in the frame's TOP band (lowest y) that sit to the RIGHT (x>3),
        # i.e. the value-column captions; the leftmost title/label is a data field
        # here, not text, so it never appears. Used ONLY when the shallow scan is
        # empty -> a report that already has section-level captions is unchanged.
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
        band = sorted([c for c in cands
                       if abs(c[0] - y0) < 0.2 and c[1] > 3.0],
                      key=lambda z: z[1])
        return [c[2] for c in band]

    for fr in sorted(frames, key=lambda f: (f.y or 0.0)):
        tables = _tables_in_frame(fr)
        if not tables:
            continue
        for q, _ in tables:
            distinct_q.add(q.name.upper())
        _tot = _footer_totals(fr)
        _hdr = _header_text(fr)
        _ch = _band_col_headers(fr) or _band_col_headers_deep(fr)
        _agg = _has_aggregate(fr)
        sections.append({
            "header": _hdr,
            "y": fr.y or 0.0,
            "tables": tables,
            "totals": _tot,
            # The Oracle header band's value-column captions ("Applications"/"Fees",
            # or "Number") -- used by the section builder for the real column
            # headers instead of a humanized field-name dump.
            "col_headers": _ch,
            # No labeled total AND no aggregate field -> a plain list section that
            # the truth shows WITHOUT a footer row.
            "has_total": bool(_tot) or _agg,
            # A single-summary-line section (named band + ONE aggregate value, no
            # "Number" column caption, no per-row detail) -- e.g. "Complaints
            # received   35". Render as a one-line label|value band, not a table.
            "summary_line": bool(_hdr) and not _ch and _agg,
        })

    # Post-pass: surface a footer-only summary frame that the main detector missed
    # because it has NO group-frame wrapper of its own. ASBESTOS's "Enforcement
    # Cases Ongoing" (label + CS_9) sits LOOSE under section_main next to a single
    # loose repeating frame, so neither the plain nor the >=2-repeating path picks
    # it up, while its siblings (each wrapped in their own M_G_*_GRPFR) were caught.
    # Emit it as a single-summary-line section. Gated tight (label present, NOT a
    # "Total"/"Number" caption, carries a CS_/Sum/CF_ aggregate, NOT already a
    # section) so it fires ONLY on genuine orphan summary lines -- corpus-verified
    # to add nothing to any other report.
    _existing = {(s["header"] or "").strip().lower() for s in sections}

    def _partner_query(parent, fy):
        best = None
        for c in (parent.children or []):
            if ((c.kind or "").lower() == "repeating_frame"
                    and getattr(c, "source_query", None)):
                q = _query_for_group(c.source_query)
                if q is None:
                    continue
                col = next((f.source for f in (c.fields or [])
                            if (f.kind or "") == "field" and (f.source or "")),
                           None)
                cy = float(getattr(c, "y", 0.0) or 0.0)
                if col and (best is None or abs(cy - fy) < best[0]):
                    best = (abs(cy - fy), q, col)
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
                    q, col = _partner_query(g, fy)
                    if q is not None:
                        _existing.add(label.lower())
                        distinct_q.add(q.name.upper())
                        sections.append({
                            "header": label, "y": fy,
                            "tables": [(q, [col])], "totals": [],
                            "has_total": True, "summary_line": True,
                        })
            _scan_extra(c, ir)

    _scan_extra(sm, False)
    sections.sort(key=lambda s: s.get("y", 0.0))

    # Require genuine multi-section: >=2 sections AND >=2 distinct queries.
    # A single data table stays on the existing path.
    if len(sections) < 2 or len(distinct_q) < 2:
        return None
    return sections


def _build_section_tablix(report, name, query, columns, header_text, palette,
                          total_label=None, col_captions=None):
    """One stacked Tablix for a single dashboard section: an optional header
    band, a column-header row, and a detail row bound to ``query``. Mirrors the
    proven _build_tablix shape so it always uploads cleanly.

    ``total_label`` controls the bold footer total row:
      * None   -> generic "Total" (backward-compatible default).
      * "<str>"-> use the section's REAL Oracle total label (e.g. "Total
                  Properties Closed") -- the value stays =Sum() of the count col.
      * ""     -> emit NO total row (the Oracle section has no group footer)."""
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
        last = len(cols) - 1
        _caps = [c for c in (col_captions or []) if c]
        for i, _col in enumerate(cols):
            cell = _sub(hcells, "TablixCell")
            contents = _sub(cell, "CellContents")
            if i == 0:
                _btxt, _balign = header_text, "Left"
            elif _caps:
                # Real Oracle header-band column captions ("Applications"/"Fees",
                # or "Number"), mapped to the value columns (cols[1:]) in order.
                # A value column past the caption count gets none.
                _ci = i - 1
                _btxt = _caps[_ci] if _ci < len(_caps) else ""
                _balign = "Right"
            elif i == last and len(cols) > 1:
                # No detected captions -> the real stat report prints "Number" on
                # the band's right edge (not a separate header row).
                _btxt, _balign = "Number", "Right"
            else:
                _btxt, _balign = "", "Left"
            _build_textbox(
                contents, f"{_safe(name)}_Band_{i}",
                _btxt,
                bold=True, bg=band_bg, fg=band_fg,
                text_align=_balign, vertical_align="Middle",
                border_color=band_bg, padding="5pt",
            )

    # No separate column-header row: the real stat/accounting section goes
    # straight from the gray band (which carries the "Number" value caption on
    # its right edge) into data rows. A humanized field-name header row
    # ("Stat Meth Count ...") would be spurious noise, so it is omitted.
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

    # Bold per-section Total footer: SUM of each value column (every column after
    # the first/label column). Val() coerces text-or-numeric so the aggregate can
    # never break the render on a stray non-numeric cell -- the defining feature
    # of this accounting-summary archetype, previously dropped. The label is the
    # section's REAL Oracle total caption when known (e.g. "Total Properties
    # Closed"); total_label="" means the Oracle section has no group footer, so
    # emit no total row at all (matching e.g. METHQTRRPT's "Actions" section).
    have_total = total_label != ""
    if have_total:
        value_cols = cols[1:] if len(cols) > 1 else []
        frow = _sub(rows_el, "TablixRow")
        _sub(frow, "Height", "0.24in")
        fcells = _sub(frow, "TablixCells")
        for i, col in enumerate(cols):
            cell = _sub(fcells, "TablixCell")
            contents = _sub(cell, "CellContents")
            if i == 0:
                _fval, _falign = (total_label or "Total"), "Left"
            elif col in value_cols:
                _fval, _falign = f"=Sum(Val(Fields!{_safe(col)}.Value))", "Center"
            else:
                _fval, _falign = "", "Left"
            _build_textbox(
                contents, f"{_safe(name)}_Ftr_{_safe(col)}", _fval,
                bold=True, bg="#eef0f3", vertical_align="Middle",
                text_align=_falign, border_color="#d0d0d0", padding="3pt",
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
    dm = _sub(rmembers, "TablixMember")
    _sub(dm, "Group").set("Name", f"{_safe(name)}_Details")
    if have_total:
        fm = _sub(rmembers, "TablixMember")
        _sub(fm, "KeepWithGroup", "Before")

    _sub(tablix, "DataSetName", _safe(query.name))
    _sub(tablix, "Left", "0in")
    _sub(tablix, "Width", "7.5in")
    _sub(tablix, "Style")
    return tablix


def _build_summary_line_band(report, name, query, label, count_col, palette):
    """A SINGLE-summary-line section: one gray band carrying the label LEFT + its
    aggregate count RIGHT (e.g. "Complaints received   35"), with NO header row,
    NO detail rows, NO footer. A 1-row x 2-col Tablix bound to the section query
    so =Sum() evaluates. Mirrors the mockup's summary_line band."""
    band_bg = palette.get("band_bg", "#4a6a8a")
    band_fg = palette.get("band_fg", "#ffffff")
    tablix = ET.Element(_q("Tablix"))
    tablix.set("Name", _safe(name))
    body = _sub(tablix, "TablixBody")
    cols_el = _sub(body, "TablixColumns")
    for w in ("5.5in", "2.0in"):
        _sub(_sub(cols_el, "TablixColumn"), "Width", w)
    rows_el = _sub(body, "TablixRows")
    row = _sub(rows_el, "TablixRow")
    _sub(row, "Height", "0.30in")
    rcells = _sub(row, "TablixCells")
    # Left cell = label; right cell = the aggregate of the section's count column.
    _val = (f"=Sum(Val(Fields!{_safe(count_col)}.Value))"
            if count_col else "")
    for _txt, _align, _cn in ((label, "Left", "Lbl"), (_val, "Right", "Val")):
        contents = _sub(_sub(rcells, "TablixCell"), "CellContents")
        _build_textbox(
            contents, f"{_safe(name)}_{_cn}", _txt,
            bold=True, bg=band_bg, fg=band_fg,
            text_align=_align, vertical_align="Middle",
            border_color=band_bg, padding="5pt",
        )
    col_hier = _sub(tablix, "TablixColumnHierarchy")
    cmembers = _sub(col_hier, "TablixMembers")
    for _ in range(2):
        _sub(cmembers, "TablixMember")
    row_hier = _sub(tablix, "TablixRowHierarchy")
    _sub(_sub(row_hier, "TablixMembers"), "TablixMember")
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
        # A single-summary-line section renders as one label|value band (no
        # header/detail/total). Pick the count column = the section's numeric
        # column (else its last column).
        if sec.get("summary_line") and sec.get("tables"):
            query, cols = sec["tables"][0]
            _count = ""
            for c in cols:
                if re.search(r"(?i)(num|count|cnt|qty|total)", c or ""):
                    _count = c
            if not _count:
                _count = cols[-1] if cols else ""
            tx = _build_summary_line_band(
                report, f"Tbx_S{idx}", query,
                sec.get("header", ""), _count, palette)
            _sub(tx, "Top", f"{top:.2f}in")
            items.append(tx)
            top += 0.30 + SECTION_GAP
            idx += 1
            continue
        # Real Oracle group-footer total labels, paired to the section's tables in
        # order (e.g. a Status section's CLOSED table -> "Total Properties Closed",
        # its OPEN table -> "Total Active Properties"). A section with NO detected
        # footer totals keeps the generic single "Total" (label=None) for
        # backward-compatibility; a table beyond the label count gets none ("").
        _sec_totals = sec.get("totals") or []
        _has_total = sec.get("has_total", True)
        _col_caps = sec.get("col_headers") or []

        # COLLAPSE the group-title table into a header. An Oracle break report
        # whose SECTION TITLE is a data column (a `*_Group` / `CF_*_Group` formula
        # returning "Total <name> for <dates>" on the footer row) parses as a
        # section with NO static header text and TWO tables on the SAME query: a
        # single-column title table + the real data table. Rendering both stacks a
        # spurious mini-table above each section and sprawls the report over many
        # pages. Detect that exact shape and fold the title column into the header
        # band (=First(group_col)) so the section renders as ONE compact block.
        # Tightly gated (no header AND 2 same-query tables AND table[0] is a lone
        # *_GROUP column) -> fires only for this archetype; a normal multi-section
        # report (real header text + one table/section) is untouched.
        _tables = sec["tables"]
        _hdr_expr = None
        if (not sec.get("header") and len(_tables) == 2
                and _tables[0][0].name == _tables[1][0].name
                and len(_tables[0][1] or []) == 1
                and re.search(r"(?i)(_group$)", (_tables[0][1][0] or ""))):
            _grp_col = _tables[0][1][0]
            _q0 = _tables[0][0]
            _hdr_expr = (f'=First(Fields!{_safe(_grp_col)}.Value, '
                         f'"{_safe(_q0.name)}")')
            # Order the data columns the way Oracle's layout does: the
            # description/LABEL column leftmost, then the count ("Applications")
            # and fee ("Fees") value columns -- the parser returns them in query
            # order, which can put the count first. Keeps captions aligned.
            _dq, _dcols = _tables[1]
            _cnt = [c for c in _dcols if re.search(r"(?i)(count|cnt|num)", c or "")]
            _fee = [c for c in _dcols if re.search(r"(?i)(fee|amount)", c or "")]
            _lbl = [c for c in _dcols if c not in _cnt and c not in _fee]
            _reord = _lbl + _cnt + _fee
            if sorted(_reord) == sorted(_dcols):
                _tables = [(_dq, _reord)]
            else:
                _tables = [_tables[1]]
            if _has_total and (not _sec_totals):
                _sec_totals = [(f'="Total " & First(Fields!{_safe(_grp_col)}.Value, '
                                f'"{_safe(_q0.name)}")')]
        for _ti, (query, cols) in enumerate(_tables):
            name = f"Tbx_S{idx}"
            # Only the FIRST table in a section carries the section header band.
            _base_hdr = _hdr_expr if _hdr_expr is not None else sec.get("header", "")
            hdr = _base_hdr if not header_used else ""
            header_used = True
            if not _has_total:
                _tl = ""            # plain list section -> no total row at all
            elif _sec_totals:
                _tl = _sec_totals[_ti] if _ti < len(_sec_totals) else ""
            else:
                _tl = None          # has an aggregate but no label -> generic Total
            tx = _build_section_tablix(report, name, query, cols, hdr, palette,
                                       total_label=_tl, col_captions=_col_caps)
            _sub(tx, "Top", f"{top:.2f}in")
            items.append(tx)
            # band (0.30) + up to ~6 detail rows + Total footer (0.24)
            est = 0.30 + EST_ROW * 6 + 0.24
            top += est + SECTION_GAP
            idx += 1

    _sub(body, "Height", f"{max(9.0, top + 0.5):.2f}in")
    _sub(body, "Style")
    return body


def _build_chart_region(chart: dict, dataset_name: str,
                        top_in: float = 0.0) -> ET.Element:
    """Build a REAL minimal SSRS column/bar/pie Chart bound to the dataset --
    plots Sum(<dataValues>) grouped by <series/src> -- so a detected Oracle
    <graph>/<rw:graph> actually RENDERS instead of only being noted. Structure
    + element order are engine-verified (ReportViewer needs Labels the XSD
    doesn't). Caller must ensure both columns exist in the dataset."""
    cat = (chart.get("category") or "").strip()
    measure = (chart.get("plot_value") or "").strip()
    ctype = {"bar": "Bar", "pie": "Pie", "line": "Line", "area": "Area",
             "column": "Column", "graph": "Column", "chart": "Column"}.get(
        (chart.get("type") or "").lower(), "Column")
    ch = ET.Element(_q("Chart"))
    ch.set("Name", "Chart_" + _safe(measure or "M"))
    _sub(ch, "Style")
    _sub(ch, "Top", f"{top_in:.2f}in")
    _sub(ch, "Left", "0.25in")
    _sub(ch, "Height", "2.5in")
    _sub(ch, "Width", "6in")
    _sub(ch, "DataSetName", _safe(dataset_name))
    sm = _sub(_sub(_sub(ch, "ChartSeriesHierarchy"), "ChartMembers"),
              "ChartMember")
    _sub(sm, "Label", measure or "Value")
    cm = _sub(_sub(_sub(ch, "ChartCategoryHierarchy"), "ChartMembers"),
              "ChartMember")
    g = _sub(cm, "Group"); g.set("Name", "ChartCat")
    _sub(_sub(g, "GroupExpressions"), "GroupExpression",
         f"=Fields!{_safe(cat)}.Value")
    _sub(cm, "Label", f"=Fields!{_safe(cat)}.Value")
    cs = _sub(_sub(_sub(ch, "ChartData"), "ChartSeriesCollection"),
              "ChartSeries")
    cs.set("Name", "Series1")
    dpv = _sub(_sub(_sub(cs, "ChartDataPoints"), "ChartDataPoint"),
               "ChartDataPointValues")
    _sub(dpv, "Y", f"=Sum(Fields!{_safe(measure)}.Value)")
    _sub(cs, "Type", ctype)
    ca = _sub(_sub(ch, "ChartAreas"), "ChartArea"); ca.set("Name", "Area1")
    _sub(_sub(ca, "ChartCategoryAxes"), "ChartAxis").set("Name", "CatAxis")
    _sub(_sub(ca, "ChartValueAxes"), "ChartAxis").set("Name", "ValAxis")
    return ch


def _chart_for_report(report, main):
    """Return (chart_dict, dataset_name) for a renderable detected chart, or
    None. Renderable = its category + measure are BOTH columns of the main
    dataset (else the chart would bind to nothing)."""
    charts = list(getattr(report, "charts", None) or [])
    if not charts or main is None:
        return None
    cols = {(it.name or "").upper() for it in (main.items or []) if it.name}
    for ch in charts:
        cat = (ch.get("category") or "").strip().upper()
        meas = (ch.get("plot_value") or "").strip().upper()
        if cat and meas and cat in cols and meas in cols:
            return (ch, main.name)
    return None


def _find_label_spec(report) -> Optional[dict]:
    """Detect the mailing-label / multi-up archetype: a repeating frame whose
    printDirection tiles ACROSS (across / acrossDown) and whose cell is a
    small label box. Returns {frame, cell_w, cell_h, fields} or None.

    Guards (strict -- many shapes tile "across", e.g. a matrix's horizontal
    dimension frame): NO matrix anywhere in the report; EXACTLY ONE across
    repeating frame; that frame is NOT a matrix dimension frame; and its
    content is a LABEL cell -- a small box whose fields are predominantly a
    boilerplate TEXT block (the address template), not data columns."""
    # A report with a matrix is never a label report.
    for lg in (report.layout or []):
        stack = [lg]
        while stack:
            g = stack.pop()
            if (getattr(g, "kind", "") or "") in (
                    "matrix", "matrix_col", "matrix_row", "matrix_cell"):
                return None
            stack.extend(getattr(g, "children", None) or [])

    labels = []

    def walk(g):
        pd = (getattr(g, "print_direction", "") or "").lower()
        if getattr(g, "kind", "") == "repeating_frame" and "across" in pd:
            labels.append(g)
        for c in (getattr(g, "children", None) or []):
            walk(c)

    for lg in (report.layout or []):
        walk(lg)
    if len(labels) != 1:
        return None
    frame = labels[0]
    cell_w = float(getattr(frame, "width", 0.0) or 0.0)
    cell_h = float(getattr(frame, "height", 0.0) or 0.0)
    # A label cell is small (several fit across a page). Reject a full-width
    # "across" frame (a wide table) or a tall one.
    if not (0.5 <= cell_w <= 4.5 and 0.2 <= cell_h <= 3.0):
        return None

    def collect_fields(g, out):
        out.extend(getattr(g, "fields", None) or [])
        for c in (getattr(g, "children", None) or []):
            collect_fields(c, out)

    fields = []
    collect_fields(frame, fields)
    if not fields:
        return None
    # The defining trait of a mailing label / form-label: its cell is built
    # from a BOILERPLATE TEXT block (the merged address template), not a row
    # of data-bound column fields. Require at least one substantial text
    # field and that text dominate the cell.
    text_fields = [f for f in fields
                   if (getattr(f, "kind", "") == "text")
                   and len((getattr(f, "text", "") or "").strip()) >= 12]
    data_fields = [f for f in fields if getattr(f, "kind", "") == "field"]
    if not text_fields or len(data_fields) > len(text_fields):
        return None
    return {"frame": frame, "cell_w": cell_w, "cell_h": max(0.5, cell_h),
            "fields": fields}


def _build_label_body(report, main, spec):
    """A mailing-label body: a one-cell Tablix (RDL-2008 "list" = a Tablix
    with one column + a detail row group) whose single cell holds the label
    box. SSRS repeats it per record and, via the page's newspaper Columns,
    tiles the records ACROSS then DOWN. Returns (body, n_cols, col_gap)."""
    cell_w, cell_h = spec["cell_w"], spec["cell_h"]
    usable = 8.5 - 2 * 0.25  # page width minus default L/R margins
    col_gap = 0.12
    ncols = max(1, int((usable + col_gap) // (cell_w + col_gap)))

    body = ET.Element(_q("Body"))
    items = _sub(body, "ReportItems")
    t = _sub(items, "Tablix")
    t.set("Name", "Tablix_Labels")

    tbody = _sub(t, "TablixBody")
    cols = _sub(tbody, "TablixColumns")
    _sub(_sub(cols, "TablixColumn"), "Width", f"{cell_w:.3f}in")
    rows = _sub(tbody, "TablixRows")
    row = _sub(rows, "TablixRow")
    _sub(row, "Height", f"{cell_h:.3f}in")
    cell = _sub(_sub(_sub(row, "TablixCells"), "TablixCell"), "CellContents")

    rect = _sub(cell, "Rectangle")
    rect.set("Name", "Lbl_Cell")
    _sub(rect, "KeepTogether", "true")
    rect_items = _sub(rect, "ReportItems")
    cover_titles: set = set()
    counter = [0]
    for f in spec["fields"]:
        counter[0] += 1
        _emit_field_textbox(rect_items, f"Lbl_Tb_{counter[0]}", "", f,
                            0.0, 0.0, cell_w, cell_h, report, cover_titles)
    if len(rect_items) == 0:
        _build_textbox(rect_items, "Lbl_Tb_0", "=Nothing", font_size="10pt")
    _sub(_sub(_sub(rect, "Style"), "Border"), "Style", "None")

    # Column hierarchy: one static column.
    _sub(_sub(_sub(t, "TablixColumnHierarchy"), "TablixMembers"),
         "TablixMember")
    # Row hierarchy: a DETAIL group (a Group with NO GroupExpressions =
    # one instance per data row, the RDL-2008 "list" idiom).
    rhm = _sub(_sub(t, "TablixRowHierarchy"), "TablixMembers")
    det = _sub(rhm, "TablixMember")
    dg = _sub(det, "Group"); dg.set("Name", "Lbl_Detail")

    _sub(t, "DataSetName", _safe(main.name))
    _sub(t, "Top", "0in"); _sub(t, "Left", "0in")
    _sub(t, "Height", f"{cell_h:.3f}in")
    _sub(t, "Width", f"{cell_w:.3f}in")

    _sub(body, "Height", f"{cell_h:.3f}in")
    _sub(body, "Style")
    return body, ncols, col_gap


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
    row0, col0 = row_fields[0], col_fields[0]

    # CELL = the measure. The 6i inline form names it explicitly
    # (matrix_cell). The 9.0.2 frame-ref form does NOT, so pick a numeric
    # data column that is NOT the row/col dimension (a leave-balance /
    # amount / count) -- never a dimension's neighbor field like a name.
    cells = list(cell_fields)
    if not cells:
        _dims = {row0.upper(), col0.upper()}
        _NUMERIC = {"number", "integer", "float", "decimal", "long", "money"}
        for q in (report.queries or []):
            for it in (q.items or []):
                nm = (it.name or "").strip()
                if (nm and nm.upper() not in _dims
                        and (getattr(it, "datatype", "") or "").lower() in _NUMERIC):
                    cells.append(nm)
            if cells:
                break
        # Last resort: a non-dimension dimension-frame neighbor.
        if not cells:
            cells = [f for f in (col_fields[1:] + row_fields[1:])
                     if f.upper() not in _dims][:1]
    if not cells:
        return None

    # Dominance: is the matrix the section's primary content? A matrix
    # report ALWAYS has supporting frames -- the dimension frames, a measure
    # repeating frame, header-label frames -- all bound to the matrix's own
    # fields. Those are NOT competing content. Only a frame carrying a DATA
    # field OUTSIDE the matrix's {row, col, cells} set is an independent
    # region that should keep the tabular path (wild-corpus verified: the
    # HRMS leave-status matrix was wrongly judged mixed because its header
    # and measure frames counted as "others").
    _mx_fields = {row0.upper(), col0.upper()} | {c.upper() for c in cells}
    attrs = getattr(mx, "matrix_attrs", {}) or {}
    _own_frames = {(attrs.get("horizontalFrame") or "").strip(),
                   (attrs.get("verticalFrame") or "").strip(),
                   (mx.name or "").strip()}
    _own_frames.discard("")

    def count_outside(g, inside_mx):
        # A frame is the matrix's own if it's the matrix, nested in it, or is
        # named as a dimension frame (its extra fields are column/row header
        # labels, e.g. an employee NAME beside the EMP_NO column key).
        ins = inside_mx or (g is mx) or ((g.name or "").strip() in _own_frames)
        n = 0
        # Only an independent REPEATING data region (its own detail table)
        # competes with the matrix. A lone static field -- a company-name
        # title, a page heading -- is not competing content and must not
        # veto the pivot (wild-corpus verified: HRMS had a CO_NAME title).
        if not ins and (getattr(g, "kind", "") or "") == "repeating_frame":
            for f in (g.fields or []):
                src = (getattr(f, "source", "") or "").strip().upper()
                if src and src not in _mx_fields:
                    n += 1
                    break
        for c in (getattr(g, "children", None) or []):
            n += count_outside(c, ins)
        return n

    dominant = count_outside(sec_root, False) == 0

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


def _build_grantee_grid_tablix(report, main):
    """A per-grantee ASTERISK GRID for the header-summary report
    (CMVGY_GRANT_STATUS): one bordered box per grantee, repeated down the page.
    Each box = the section_main R_Org repeating frame painted at its real
    geometry (FY column-header texts + grantee name + the font-28 *_Ind asterisk
    fields under each FY column + quarter headers + site + contact). Replaces the
    generic nested-card the summary stat query would otherwise mangle. Gated to
    _is_header_summary_report (CMVGY_GS only), so baseline-safe."""
    ms = _section_by_kind(report, "section_main")

    def _find_rep(node):
        for c in (getattr(node, "children", None) or []):
            if "repeating" in (getattr(c, "kind", "") or "").lower():
                return c
            r = _find_rep(c)
            if r is not None:
                return r
        return None

    org = _find_rep(ms) if ms is not None else None
    if org is None:
        return _build_nested_group_tablix(report, main)

    BODY_W = 7.9
    tablix = ET.Element(_q("Tablix"))
    tablix.set("Name", "Tablix_GranteeGrid")
    tbody = _sub(tablix, "TablixBody")
    _sub(_sub(_sub(tbody, "TablixColumns"), "TablixColumn"), "Width", f"{BODY_W}in")
    rows = _sub(tbody, "TablixRows")
    row = _sub(rows, "TablixRow")
    gh = max(1.0, float(getattr(org, "height", 0.0) or 0.0) or 2.2)
    _sub(row, "Height", f"{gh:.2f}in")
    contents = _sub(_sub(_sub(row, "TablixCells"), "TablixCell"), "CellContents")
    rect = _sub(contents, "Rectangle")
    rect.set("Name", "Rect_GranteeBox")
    _sub(rect, "KeepTogether", "true")
    rstyle = _sub(rect, "Style")
    rb = _sub(rstyle, "Border")
    _sub(rb, "Style", "Solid")
    _sub(rb, "Color", "#000000")
    _sub(rb, "Width", "1pt")
    ri = _sub(rect, "ReportItems")
    counter = [0]
    _emit_frame_rect(ri, org, 0.0, 0.0, BODY_W, report, set(), "Grid", counter)

    # The per-grantee ASTERISK marks. Oracle's F_*_Ind fields (font 28, source
    # *_Ind whose formula returns "*"/"") live in the section_HEADER repeating
    # frames (R_Budget_C / R_Itemized_MI / R_Quarter_MQ3 -- suppressed there as a
    # stat-table overlap), positioned at the SAME x as this grid's FY column
    # headers (Grant 3.55 / Cars 4.29 / … / Complete 7.30; quarters at y1.49). They
    # belong HERE -- one "*" under each present form. Collect them from anywhere in
    # the layout and emit at their own geometry inside the grantee box.
    _seen_ind = set()
    _ind_fields = []

    def _collect_ind(node):
        for c in (getattr(node, "children", None) or []):
            for f in (getattr(c, "fields", None) or []):
                _s = (getattr(f, "source", "") or "")
                if _s.lower().endswith("_ind") and _s.upper() not in _seen_ind:
                    _seen_ind.add(_s.upper())
                    _ind_fields.append(f)
            _collect_ind(c)

    for _sec in (report.layout or []):
        _collect_ind(_sec)

    # The _Ind sources live in the SUB-queries (Cars_Ind/Grant_Ind/... in
    # Q_Grant; Itemized_Ind in Q_ACCOUNTING; Q1-4_Ind in Q_Logsheet), not in
    # the grid's own dataset (Q_CMVGY). _field_value_for, scoped to the grid
    # dataset, can only resolve same-dataset columns + report formulas (so just
    # CP_Complete_Ind, the DS_REPORT_FORMULAS field, resolved; the other nine
    # came back =Nothing). Bind each cross-query indicator with a SSRS Lookup on
    # the per-grantee join key shared between Q_CMVGY and the indicator's owning
    # query (Org_Id for grant/accounting, Site_Id for the quarterly logsheet) --
    # the same LookupSet/Join pattern already used for 1:many linked detail
    # elsewhere. Generic: the host query + join key are discovered from the
    # parsed query columns, never hardcoded.
    _main_cols = {(it.name or "").upper() for it in (main.items or [])}
    # source-col (upper) -> (host_query_name, join_key) for every non-main query
    _ind_host = {}
    for _qry in (report.queries or []):
        if _qry.name == main.name:
            continue
        _qcols = [(it.name or "") for it in (_qry.items or [])]
        _shared = next((c for c in _qcols if c.upper() in _main_cols), None)
        if not _shared:
            continue
        for c in _qcols:
            if c.upper().endswith("_IND") and c.upper() not in _ind_host:
                _ind_host[c.upper()] = (_qry.name, _shared)

    def _ind_value(src):
        host = _ind_host.get((src or "").upper())
        if host:
            qn, key = host
            return (f"=Lookup(Fields!{_safe(key)}.Value, "
                    f"Fields!{_safe(key)}.Value, "
                    f"Fields!{_safe(src)}.Value, \"{_safe(qn)}\")")
        return None  # not cross-query (e.g. a report-formula _Ind) -> default path

    for _indf in _ind_fields:
        counter[0] += 1
        _ov = _ind_value(getattr(_indf, "source", "") or "")
        _emit_field_textbox(ri, f"Grid_Ind_{counter[0]}", "", _indf,
                            0.0, 0.0, BODY_W, gh, report, set(),
                            value_override=_ov)

    # Body-direct field refs inside the box must be scoped aggregates? No -- the
    # rect IS a Tablix detail cell, so Fields!X are in the dataset row scope.
    _sub(_sub(_sub(tablix, "TablixColumnHierarchy"), "TablixMembers"),
         "TablixMember")
    rh = _sub(tablix, "TablixRowHierarchy")
    rm = _sub(rh, "TablixMembers")
    det = _sub(rm, "TablixMember")
    dg = _sub(det, "Group")
    dg.set("Name", "Grantee_Detail")
    # Group per grantee (Org_Id is the per-grantee key in the main dataset); the
    # PageBreak=Between renders one grantee box per page like the Oracle output.
    _ge = _sub(dg, "GroupExpressions")
    _gk = "Org_Id" if any((it.name or "").upper() == "ORG_ID"
                          for it in (main.items or [])) else None
    _sub(_ge, "GroupExpression",
         f"=Fields!{_gk}.Value" if _gk else "=RowNumber(Nothing)")
    _sub(_sub(dg, "PageBreak"), "BreakLocation", "Between")
    _sub(tablix, "DataSetName", _safe(main.name))
    return tablix


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

    # A "Run Date / Run By / Total of ALL Records + Report Parameters" cover is
    # prepended ONLY when the report actually carries cover content (a real
    # Parameter-Form criteria cover in section_header). A plain tabular list or a
    # master-detail grid has NO such page in the real Oracle output -- page 1 is
    # the data. _build_cover_page would otherwise fabricate one from the
    # parameter list (e.g. a report with a dozen-plus params), inventing a page
    # the report never prints. Gate it structurally, mirroring _has_cover_page
    # gate so preview and RDL agree.
    cover = _build_cover_page(report) if _has_cover_page(report) else None
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
        if _is_header_summary_report(report):
            # The summary cover Rectangle (above) already carries the criteria
            # cover + stat table; the BODY is the per-grantee asterisk grid.
            tablix = _build_grantee_grid_tablix(report, main)
        elif _is_matrix:
            tablix = _build_matrix_tablix(report, _mspec)
        elif _is_stacked_list_rdl(report, main):
            # A flat tabular LIST whose record occupies >=2 column-aligned
            # STACKED lines (Oracle 2-line list: Permit/Permit-Dates |
            # City/Type-of-Operation | ...) -> a single-column Tablix with a
            # stacked header band + a detail cell that stacks each column's
            # fields, instead of one flat column per field. Additive + gated, so
            # single-line lists keep the flat grid below.
            tablix = _build_stacked_list_tablix(report, main)
        elif _is_grouped_tabular_subtotal(report):
            # A 2-level GROUPED TABULAR break report with per-group SUBTOTALS:
            # a group-header band + column headers + detail rows + a group-footer
            # totals stack. Checked BEFORE the flat-list and card detectors (both
            # of which fire for it but render it wrong: flat drops the grouping +
            # totals; the card mashes the "(continued)"/column labels into one
            # garbled caption and drops the title). Tightly gated so it never
            # steals a flat list or a card report (see _grouped_tabular_spec).
            tablix = _build_grouped_tabular_subtotal_tablix(report, main)
        elif _is_flat_tabular_list_rdl(report, main):
            # A plain tabular LIST (incl. a grouped-but-flat-row roster) wrongly
            # caught by the nested-MD / card detectors -> flat column grid.
            tablix = _build_tablix(report, main)
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


def _content_span_in(report) -> float:
    """Widest right-edge (x + width) across the main section's frames and their
    fields = the Oracle CONTENT width. Drives portrait-vs-landscape page sizing:
    a span wider than portrait's usable area needs a wide (landscape) page, or
    the columns get compressed."""
    sec = _section_by_kind(report, "section_main")
    spans = [0.0]

    def _r(o) -> float:
        try:
            return float(getattr(o, "x", 0) or 0) + float(getattr(o, "width", 0) or 0)
        except Exception:  # noqa: BLE001
            return 0.0

    def walk(g) -> None:
        spans.append(_r(g))
        for f in (getattr(g, "fields", None) or []):
            spans.append(_r(f))
        for ch in (getattr(g, "children", None) or []):
            walk(ch)

    if sec:
        for c in (getattr(sec, "children", None) or []):
            walk(c)
    return max(spans)


def _page_width_for(report) -> float:
    """Resolve the report's PageWidth in inches (cached on the report). Portrait
    8.5in unless the Oracle content span exceeds portrait's usable width, in
    which case the page widens to fit (landscape). Gated so portrait reports are
    byte-identical -- only genuinely-wide reports change."""
    cached = getattr(report, "_page_width_in", None)
    if cached:
        return cached
    portrait_usable = 8.5 - 2 * _PAGE_HMARGIN_IN - 0.02  # 7.98
    try:
        span = _content_span_in(report)
    except Exception:  # noqa: BLE001 -- never let sizing break the RDL
        span = 0.0
    pw = (min(round(span + 2 * _PAGE_HMARGIN_IN + 0.1, 2), 17.0)
          if span > portrait_usable else 8.5)
    report._page_width_in = pw
    return pw


def _leading_param_echo(report) -> list:
    """Detect a *selection-criteria echo* in the report's leading margin: short
    boilerplate labels ending in ':' (e.g. "Start Date:", "Year of Emissions:")
    each paired with an adjacent value field whose source is a declared report
    PARAMETER. Oracle prints this block in the repeating top margin so the reader
    sees which criteria the run covers; the multi-section body builder drops every
    leading section_main field, so without this the whole block vanishes.

    Returns an ordered list of {label, lx, vx, ly, value} (empty when no such
    pair exists). General + self-gating: a report needs a colon-label sitting just
    left of a parameter-bound value in its top margin to qualify, so plain tables,
    letters and forms return []."""
    sm = _section_by_kind(report, "section_main")
    if sm is None:
        return []
    params = {(p.name or "").upper() for p in (report.parameters or [])}
    if not params:
        return []
    title_lines = {(t or "").strip().lower()
                   for t in (_resolved_title_lines(report) or [])}
    labels, values = [], []
    for f in (getattr(sm, "fields", []) or []):
        kind = getattr(f, "kind", "")
        text = (getattr(f, "text", "") or "").strip()
        src = (getattr(f, "source", "") or "").strip()
        y = getattr(f, "y", 0) or 0
        x = getattr(f, "x", 0) or 0
        if not (0 < y < 3.0):  # top margin only (skip body + footer page-num)
            continue
        if kind == "text" and not src and text.endswith(":") and len(text) <= 40:
            tl = text.lower().rstrip(":").strip()
            if tl in title_lines or "report run on" in tl:
                continue
            labels.append((x, y, text))
        elif kind == "field" and src.upper() in params:
            values.append((x, y, src))
    out = []
    for (lx, ly, lt) in labels:
        best = None
        for (vx, vy, vs) in values:
            if abs(vy - ly) < 0.25 and vx >= lx and (best is None or vx < best[0]):
                best = (vx, vy, vs)
        if best:
            out.append({"label": lt, "lx": lx, "vx": best[0],
                        "ly": ly, "value": best[2]})
    return sorted(out, key=lambda p: p["ly"])


def _section_header_banner(report):
    """AIR-style per-record FORM header banner: a section-DIRECT criteria row Oracle
    prints below the title -- a date (left) + a "<label> <param>" (centered) capped
    by a full-width heavy RULE. Returns {date_expr, label, value_expr} or None.

    TIGHTLY GATED on the structural signature (a full-width <line> rule AND a
    date/param field sitting section-DIRECTLY -- not frame-nested -- in the
    y 0.4..1.2 band). Corpus-probed to fire ONLY for the AIR summary/detail forms;
    every other report has no such section-direct banner, so its page header is
    untouched and byte-identical."""
    try:
        sm = _section_by_kind(report, "section_main")
        if sm is None:
            return None
        direct = list(getattr(sm, "fields", None) or [])
        rule = next((f for f in direct if (getattr(f, "kind", "") or "") == "line"
                     and 0.4 < float(getattr(f, "y", 0) or 0) < 1.2
                     and float(getattr(f, "width", 0) or 0) > 3.0), None)
        if rule is None:
            return None
        band = [f for f in direct
                if 0.4 < float(getattr(f, "y", 0) or 0) < 1.2
                and (getattr(f, "kind", "") or "") in ("field", "text")]
        date_f = next((f for f in band if (getattr(f, "kind", "") or "") == "field"
                       and re.search(r"(?i)(date|sysdate)", (getattr(f, "source", "") or ""))), None)
        label_f = next((f for f in band if (getattr(f, "kind", "") or "") == "text"
                        and (getattr(f, "text", "") or "").strip()), None)
        val_f = next((f for f in band if (getattr(f, "kind", "") or "") == "field"
                      and f is not date_f), None)
        if val_f is None and date_f is None:
            return None
        value_expr = None
        if val_f is not None:
            s = (getattr(val_f, "source", "") or "").strip()
            is_param = any((p.name or "").upper() == s.upper()
                           for p in (report.parameters or []))
            value_expr = (f'Parameters!{_safe(s)}.Value' if is_param
                          else f'First(Fields!{_safe(s)}.Value)')
        return {
            "date_expr": ('=Format(Globals!ExecutionTime, "MM/dd/yyyy")'
                          if date_f is not None else None),
            "label": (getattr(label_f, "text", "") or "").strip() if label_f else "",
            "value_expr": value_expr,
        }
    except Exception:  # noqa: BLE001 -- header chrome must never break the build
        return None


def _build_page(report: ParsedReport, page_height_in: float = 11.0,
                footer_on_first_page: bool = True,
                signature_in_footer: bool = True,
                columns: int = 1, column_spacing: float = 0.0,
                column_width_in: float = 0.0,
                param_echo: Optional[list] = None) -> ET.Element:
    """Page-level dimensions + optional header/footer. ``columns`` > 1 emits
    newspaper-style multi-column layout (the mailing-label tiling).

    ``param_echo`` (a list from ``_leading_param_echo``) renders a repeating
    selection-criteria block (label + parameter value) in the top-left margin --
    used by the multi-section path, whose body builder otherwise drops these
    leading fields. When present, "Report run on" moves to the top-right and the
    page number drops to the footer (matching the Oracle margin layout)."""
    page = ET.Element(_q("Page"))
    _echo = list(param_echo or [])

    # PageHeader: title block extracted from layout.
    title_lines = _resolved_title_lines(report)
    # AIR-style criteria banner (date + "<label> <param>" + heavy rule) printed
    # below the title on a per-record form. Gated -> None for every other report.
    _banner = None if _echo else _section_header_banner(report)
    if title_lines:
        header_h = 0.20 + 0.22 * len(title_lines) + 0.30
        if _echo:
            header_h = max(header_h, max(p["ly"] for p in _echo) + 0.32)
        if _banner:
            header_h = max(header_h, 0.10 + 0.22 * len(title_lines) + 0.42)
        ph = _sub(page, "PageHeader")
        _sub(ph, "Height", f"{header_h:.2f}in")
        _sub(ph, "PrintOnFirstPage", "true")
        _sub(ph, "PrintOnLastPage", "true")
        ph_items = _sub(ph, "ReportItems")
        # Resolve any Oracle lexical/&TOKEN in each title line to SSRS
        # expression atoms (a &PARAM -> Parameters!..Value, a &FORMULA -> its
        # placeholder) so a raw "&REPORT_VEHICLE_TYPE" never prints in the page
        # title. A line with no token stays a plain quoted literal (unchanged).
        def _title_atom(ln):
            val, is_expr = _resolve_text_expression(ln, report)
            if is_expr:
                return val[1:]  # strip the leading '=' -> the &-joined atoms
            return '"' + val.replace('"', '""') + '"'

        title_expr = "=" + " & vbCrLf & ".join(
            _title_atom(ln) for ln in title_lines
        )
        _t_font, _t_color = _title_style(report)
        _build_textbox(
            ph_items, "Tb_PageTitle", title_expr,
            bold=True, font_size="11pt",
            fg=_t_color,
            text_align="Center", vertical_align="Middle",
            border_color="#ffffff", padding="2pt",
            font_family=_t_font,
        )
        title_tb = ph_items[-1]
        _sub(title_tb, "Top", "0.05in")
        _sub(title_tb, "Left", "0.1in")
        _sub(title_tb, "Width", "7.3in")
        _sub(title_tb, "Height", f"{0.22 * len(title_lines):.2f}in")
        meta_y = 0.10 + 0.22 * len(title_lines)
        if _echo:
            # Selection-criteria echo (Oracle repeating top-left margin): one row
            # per label/parameter pair at its source geometry. "Report run on"
            # moves to the top-right and the page number drops to the footer to
            # match the Oracle margin layout.
            for i, pr in enumerate(_echo):
                _build_textbox(
                    ph_items, f"Tb_EchoL{i}", pr["label"],
                    font_size="9pt", fg="#444444", bold=True,
                    text_align="Left", vertical_align="Middle",
                    border_color="#ffffff", padding="2pt",
                )
                _lb = ph_items[-1]
                _sub(_lb, "Top", f"{pr['ly']:.2f}in")
                _sub(_lb, "Left", f"{max(0.1, pr['lx']):.2f}in")
                _sub(_lb, "Width", f"{max(0.6, pr['vx'] - pr['lx'] - 0.02):.2f}in")
                _sub(_lb, "Height", "0.20in")
                _build_textbox(
                    ph_items, f"Tb_EchoV{i}",
                    f"=Parameters!{_safe(pr['value'])}.Value",
                    font_size="9pt", fg="#444444",
                    text_align="Left", vertical_align="Middle",
                    border_color="#ffffff", padding="2pt",
                )
                _vb = ph_items[-1]
                _sub(_vb, "Top", f"{pr['ly']:.2f}in")
                _sub(_vb, "Left", f"{pr['vx']:.2f}in")
                _sub(_vb, "Width", "2.4in")
                _sub(_vb, "Height", "0.20in")
            _build_textbox(
                ph_items, "Tb_RunOn",
                '="Report run on: " & Format(Globals!ExecutionTime, "MM/dd/yyyy h:mm tt")',
                font_size="9pt", fg="#444444",
                text_align="Right", vertical_align="Middle",
                border_color="#ffffff", padding="2pt",
            )
            run_tb = ph_items[-1]
            _sub(run_tb, "Top", "0.05in")
            _sub(run_tb, "Left", "4.3in")
            _sub(run_tb, "Width", "3.2in")
            _sub(run_tb, "Height", "0.20in")
        elif _banner:
            # AIR criteria banner: date (left) + "<label> <value>" (centered)
            # capped by a heavy full-width rule, in place of the generic run-on /
            # page meta (the page number drops to the footer, below). Matches the
            # real Oracle form header.
            _bw = max(7.3, _page_width_for(report) - 2 * _PAGE_HMARGIN_IN)
            if _banner.get("date_expr"):
                _build_textbox(
                    ph_items, "Tb_BannerDate", _banner["date_expr"],
                    font_size="9pt", fg="#000000", text_align="Left",
                    vertical_align="Middle", border_color="#ffffff", padding="2pt")
                _bd = ph_items[-1]
                _sub(_bd, "Top", f"{meta_y:.2f}in"); _sub(_bd, "Left", "0.1in")
                _sub(_bd, "Width", "2.2in"); _sub(_bd, "Height", "0.20in")
            if _banner.get("value_expr") is not None:
                _lbl = _banner.get("label") or ""
                _yexpr = (('="' + _lbl.replace('"', '""') + '  " & ' + _banner["value_expr"])
                          if _lbl else ("=" + _banner["value_expr"]))
                _build_textbox(
                    ph_items, "Tb_BannerCriteria", _yexpr,
                    font_size="9pt", fg="#000000", text_align="Center",
                    vertical_align="Middle", border_color="#ffffff", padding="2pt")
                _bc = ph_items[-1]
                _sub(_bc, "Top", f"{meta_y:.2f}in"); _sub(_bc, "Left", "0.1in")
                _sub(_bc, "Width", f"{_bw:.2f}in"); _sub(_bc, "Height", "0.20in")
            # heavy full-width rule (a thin dark bar) capping the header band
            _rr = _sub(ph_items, "Rectangle"); _rr.set("Name", "Rect_BannerRule")
            _sub(_sub(_rr, "Style"), "BackgroundColor", "#000000")
            _sub(_rr, "Top", f"{meta_y + 0.24:.2f}in"); _sub(_rr, "Left", "0.1in")
            _sub(_rr, "Width", f"{_bw:.2f}in"); _sub(_rr, "Height", "0.03in")
        else:
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
    # When the page header carries a selection-criteria echo OR an AIR criteria
    # banner, the Oracle page number lives in the bottom margin (centered), not
    # the top-right -- emit it in the footer so the header band stays clean.
    if _echo or _banner:
        pf_items = _sub(pf, "ReportItems")
        _build_textbox(
            pf_items, "Tb_FootPageNum",
            '="Page " & Globals!PageNumber & " of " & Globals!TotalPages',
            font_size="9pt", fg="#444444",
            text_align="Center", vertical_align="Middle",
            border_color="#ffffff", padding="2pt",
        )
        _fp = pf_items[-1]
        _sub(_fp, "Top", "0.04in")
        _sub(_fp, "Left", "2.0in")
        _sub(_fp, "Width", "3.4in")
        _sub(_fp, "Height", "0.20in")
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
    _sub(page, "PageWidth", f"{_page_width_for(report):.2f}in")
    _sub(page, "LeftMargin", f"{_PAGE_HMARGIN_IN}in")
    _sub(page, "RightMargin", f"{_PAGE_HMARGIN_IN}in")
    _sub(page, "TopMargin", f"{_PAGE_MARGIN_IN}in")
    _sub(page, "BottomMargin", f"{_PAGE_MARGIN_IN}in")
    if columns and columns > 1:
        # Newspaper multi-up: SSRS tiles the body's single data region across
        # ``columns`` columns then down -- the mailing-label render.
        _sub(page, "Columns", str(columns))
        _sub(page, "ColumnSpacing", f"{column_spacing:.3f}in")
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
    # 1:many linked detail tables (Oracle <link> child rendered as a columnar
    # repeating frame, e.g. JV's vehicle list per tower): strip the child's
    # link WHERE filter so its dataset carries ALL rows (the per-master-row
    # join is done in SSRS by LookupSet on the key column the generator already
    # SELECTs), and stash the set so the field resolver emits Join(LookupSet(..))
    # -- a multi-row list -- instead of a scalar Lookup() for those columns.
    # Done FIRST so parameter augmentation + dataset build see the stripped SQL.
    try:
        report._one_to_many_children = _one_to_many_link_children(report)
        _strip_link_filter_predicates(report, report._one_to_many_children)
    except Exception:  # noqa: BLE001 -- never let detail-linking sink the RDL
        report._one_to_many_children = set()
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
    # Mailing-label / multi-up archetype: a repeating frame with
    # printDirection="across"/"acrossDown" tiles a small label cell across
    # the page then down. SSRS renders this natively with a List data
    # region + newspaper-style report Columns -- NOT a tall one-per-row
    # table (which leaves trailing blank pages, wild-corpus verified).
    _label = _find_label_spec(report) if main is not None else None
    if _label:
        body, ncols, colgap = _build_label_body(report, main, _label)
        root.append(body)
        _sub(root, "Width", f"{_label['cell_w']:.3f}in")
        root.append(_build_page(report, page_height_in=11.0,
                                footer_on_first_page=True,
                                signature_in_footer=False,
                                columns=ncols, column_spacing=colgap,
                                column_width_in=_label["cell_w"]))
        root.append(_build_code())
        _sub(root, "Language", "en-US")
        _rdsub(root, "DrawGrid", "true")
        _rdsub(root, "GridSpacing", "0.083333in")
        _strip_empty_required_containers(root)
        return root

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
    # A DEEP nested master-detail that is structurally a per-record positional
    # DOCUMENT (the mockup routes it via _is_per_record_document) must NOT take the
    # generic nested-group-Tablix branch below -- that emits navy field-name bands
    # ("Name Order: Name Order", "Appl Address | Appl Phones") instead of the
    # geometry-faithful document the truth shows. Route it through the per-record
    # positional body (the _is_form arm) so the RDL matches the mockup. Reuses the
    # mockup's exact predicate; METHACT/ASBINSPC (genuine nested-MD cards) score
    # False so they keep the Tablix path and the 13 baselines stay byte-identical.
    _prd = False
    if main is not None and kind not in ("certificate", "letter"):
        try:
            from converter.preview.html_mockup import (
                _is_per_record_document as _isprd)
            _prd = bool(_isprd(report))
        except Exception:
            _prd = False
    # A multi-SECTION dashboard (>=2 query-bound section frames -- an accounting
    # report's stacked count/fee sections) must reach _build_multi_section_body
    # below. translate_report populates the group chain so _is_nested_master_detail
    # flips True post-pipeline, which would otherwise divert it to the single-main
    # _build_body here and drop every section but the first. Detect it ONCE and
    # exclude it from the nested branch (and reuse it at the tabular_details arm).
    try:
        _multi_sections = (_detect_multi_section(report)
                           if kind == "tabular_details" else None)
    except Exception:  # noqa: BLE001 -- detection must never sink the build
        _multi_sections = None
    if (_nested and (kind == "tabular_details" or _deep) and not _multi_sections
            and not _prd):
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

    # Positional document PACKET (memo cover + data table + closing letter as
    # sibling frames, each its own page via pageBreakAfter). Checked before the
    # tabular/letter branches because such packets carry a columnar table (so
    # detect_report_kind calls them tabular) yet must NOT lose their prose
    # frames to the cover+tablix path. Gated tightly (_is_doc_packet fires for
    # 0/66 corpus reports) so only genuine packets enter here.
    try:
        _is_packet = main is not None and _is_doc_packet(report)
    except Exception:
        _is_packet = False
    if _is_packet:
        body = _build_packet_body(report, main)
        root.append(body)
        # Report Width is the printable content width (page width MINUS the two
        # side margins), never the full PageWidth: a report whose Width equals
        # PageWidth overflows the printable area by the margin total, and SSRS
        # paginates that overflow into a blank right-hand page after EVERY
        # content page (the [2,4,6] blank cadence seen on break-less packets).
        # _build_packet_body already budgets its frames to this same width.
        _sub(root, "Width",
             f"{_page_width_for(report) - 2 * _PAGE_HMARGIN_IN - 0.02:.2f}in")
        root.append(_build_page(report, page_height_in=11.0,
                                footer_on_first_page=True,
                                signature_in_footer=False))
        root.append(_build_code())
        _sub(root, "Language", "en-US")
        _rdsub(root, "DrawGrid", "true")
        _rdsub(root, "GridSpacing", "0.083333in")
        _strip_empty_required_containers(root)
        return root

    # Positional single-record FORM (invoice / requisition / order form): one
    # master record per page with a scattered vendor/bill-to/office block AND an
    # embedded line-item table. The generic tabular path fabricates a Run Date /
    # Run By / Total cover and drops the form entirely, so route it -- like a
    # letter -- through the per-record positional body (which now skips the cover
    # when there's none). Gated by _is_single_record_form (maxRec=1 + exactly one
    # columnar detail table + not nested-MD) OR _is_positional_form_rdl (the
    # broader multi-sub-table form: one record per page via pageBreakAfter +
    # scattered field blocks + >=1 sub-table -- e.g. an emissions-inventory form
    # whose Plant Location / Mailing blocks + SIC/NAIC/EMISSIONS boxes the generic
    # tabular path collapses into a navy nested-MD card with field-name dumps).
    # Both exclude flat tabular lists, so a list or deep master-detail never
    # enters here.
    try:
        _is_form = main is not None and (
            _is_single_record_form(report) or _is_positional_form_rdl(report))
    except Exception:
        _is_form = False
    # A per-record positional DOCUMENT (see _prd above) that fell through the
    # nested-Tablix branch -> route it through the same positional per-record body
    # the mockup uses so both views agree.
    if _prd and not _is_form:
        _is_form = True
    if _is_form:
        body = _build_per_record_body(report, main, suppress_empty_cover=True)
        try:
            req = float(body.attrib.pop("data-required-page-height-in", "0"))
        except Exception:
            req = 0.0
        _sig_in_body = body.attrib.pop("data-signature-in-body", "") == "1"
        chrome = (2 * _PAGE_MARGIN_IN
                  + _page_header_height(report)
                  + _PAGE_FOOTER_HEIGHT_IN)
        page_height = max(11.0, req + chrome + 0.2)
        root.append(body)
        _report_w = float(body.attrib.pop("data-body-width-in", "7.5"))
        _sub(root, "Width", f"{_report_w}in")
        root.append(_build_page(report, page_height_in=page_height,
                                footer_on_first_page=True,
                                signature_in_footer=not _sig_in_body))
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
        sections = _multi_sections
        if sections:
            body = _build_multi_section_body(report, sections)
        else:
            body = _build_body(report, main)
        page_height = 11.0
    else:
        body = _build_body(report, main)
        page_height = 11.0

    # A detected Oracle chart whose category + measure are real dataset
    # columns -> a REAL rendered SSRS Chart, placed below the body content
    # (engine-verified). Guarded: a chart must never sink the conversion.
    try:
        _chart = _chart_for_report(report, main)
        if _chart and body is not None:
            _ri = body.find(_q("ReportItems"))
            if _ri is None:
                _ri = ET.SubElement(body, _q("ReportItems"))
                body.insert(0, _ri)
            _bh = body.find(_q("Height"))
            _cur = 0.0
            if _bh is not None and _bh.text:
                try:
                    _cur = float(_bh.text.replace("in", "").strip())
                except ValueError:
                    _cur = 0.0
            _top = max(0.3, _cur + 0.3)
            _ri.append(_build_chart_region(_chart[0], _chart[1], top_in=_top))
            if _bh is not None:
                _bh.text = f"{_top + 2.5 + 0.5:.2f}in"
    except Exception:  # noqa: BLE001 -- a chart must never break the RDL
        pass

    root.append(body)
    # Use the body's computed width (derived from Oracle source layout) so the
    # content span isn't clipped. Falls back to 7.5in (portrait) -- but for a
    # LANDSCAPE report (a wide flat table) default to the full usable page width
    # so a 6-column table fits one page instead of spilling a column onto page 2.
    _default_bw = "7.5"
    if _page_width_for(report) > 8.5:
        _default_bw = f"{_page_width_for(report) - 2 * _PAGE_HMARGIN_IN - 0.04:.2f}"
    _report_w = float(body.attrib.pop("data-body-width-in", _default_bw))
    _sub(root, "Width", f"{_report_w}in")
    # Page 1 is a cover sheet for certificate / letter reports, so the
    # signature footer must not print on it. And when the signature blob
    # is rendered inside the body, drop the footer copy entirely.
    # Selection-criteria echo (e.g. "Start Date:" / "End Date:" param block) lives
    # in the repeating top margin. Only the multi-section path needs it injected:
    # its body builder drops all leading section_main fields. Other routes (forms,
    # letters, single-table) emit their leading fields positionally, so they pass
    # None and stay byte-identical.
    _param_echo = (_leading_param_echo(report)
                   if (kind == "tabular_details" and _multi_sections) else None)
    root.append(_build_page(
        report, page_height_in=page_height,
        footer_on_first_page=(kind not in ("certificate", "letter")),
        signature_in_footer=not signature_in_body,
        param_echo=_param_echo,
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


def _ensure_summary_totals_emitted(root: ET.Element, report) -> None:
    """Oracle report-level <summary compute="report"> GRAND TOTALS are placed in
    the layout as fields bound to the summary name, but flat / multi-section body
    builders render the data tablixes and skip these standalone total fields --
    so the grand totals vanish (wild-corpus banking reports: 6 of 8 summaries).
    Emit each not-yet-rendered report-scoped summary as a labeled textbox BELOW
    the body content, computing the REAL SSRS aggregate scoped to its source
    column's dataset (=Sum(Fields!src.Value,"Q")). Stacked below (not at the
    Oracle y) so it never overlaps a dynamically-grown tablix. Gated on
    report-scoped summaries existing -> non-summary reports are byte-identical."""
    # A HEADER-RESIDENT summary report (CMVGY_GRANT_STATUS) carries ALL its totals
    # inside the section_header stat table (the cover Rectangle); a separate
    # grand-total stack stacked below the per-grantee grid is a redundant
    # duplicate (it mis-renders the _Ind formulas as a "Miss Grant: Miss Grant"
    # label list). Skip. Gated -> only CMVGY_GRANT_STATUS, baseline-safe.
    if _is_header_summary_report(report):
        return
    formulas = getattr(report, "formulas", None) or []
    rep_summ = [f for f in formulas
                if getattr(f, "agg_function", "")
                and (getattr(f, "agg_scope", "") or "").lower() == "report"
                and getattr(f, "agg_source", "")]
    if not rep_summ:
        return
    body = root.find(_q("Body"))
    if body is None:
        return
    # Only emit summaries actually PLACED in the layout (a field bound to the
    # summary name) -- mirrors Oracle (an unplaced summary doesn't display).
    placed = set()

    def _collect(g):
        for f in (getattr(g, "fields", None) or []):
            s = (getattr(f, "source", "") or "").upper()
            if s:
                placed.add(s)
        for c in (getattr(g, "children", None) or []):
            _collect(c)
    for lg in (getattr(report, "layout", None) or []):
        _collect(lg)
    rep_summ = [f for f in rep_summ if (f.name or "").upper() in placed]
    if not rep_summ:
        return

    owner: dict = {}
    for q in (getattr(report, "queries", None) or []):
        for it in (getattr(q, "items", None) or []):
            if getattr(it, "name", ""):
                owner.setdefault(it.name.upper(), q.name)
    _SS = {"count": "Count", "sum": "Sum", "avg": "Avg", "average": "Avg",
           "min": "Min", "max": "Max", "stddev": "StDev", "variance": "Var",
           "% of total": "Sum"}

    # An aggregate OUTSIDE a data region needs an explicit dataset scope unless
    # the report has exactly one dataset (SSRS rule). So a grand total whose
    # source column has no resolvable owner dataset (e.g. it sums a FORMULA
    # column, not a base query column) can only be emitted unscoped -- valid in
    # a single-dataset report, INVALID (publish error) in a multi-dataset one.
    _multi_ds = len(getattr(report, "queries", None) or []) > 1

    def _expr(f):
        fn = _SS.get((f.agg_function or "").lower(), "Sum")
        src = (f.agg_source or "").strip()
        own = owner.get(src.upper())
        if not own and _multi_ds:
            return None        # would be an unscoped aggregate -> skip (flag)
        return (f'={fn}(Fields!{_safe(src)}.Value, "{_safe(own)}")' if own
                else f'={fn}(Fields!{_safe(src)}.Value)')

    existing = {(v.text or "") for v in root.iter(_q("Value"))}
    # De-dup grand totals that already render (e.g. resolved via a layout &token).
    todo = []
    for f in rep_summ:
        e = _expr(f)
        if e is None or e in existing or e in [t[1] for t in todo]:
            continue
        label = re.sub(r"^(CS|CF|CP|CN)_", "", f.name or "", flags=re.IGNORECASE)
        label = re.sub(r"[_]+", " ", label).strip().title() or (f.name or "Total")
        todo.append((label, e))
    if not todo:
        return

    ri = body.find(_q("ReportItems"))
    if ri is None:
        ri = _sub(body, "ReportItems")
    body_h_el = body.find(_q("Height"))
    try:
        body_h = float((body_h_el.text or "0").replace("in", "")) \
            if body_h_el is not None else 0.0
    except ValueError:
        body_h = 0.0

    y = body_h + 0.20
    for i, (label, e) in enumerate(todo):
        _build_textbox(
            ri, f"Tb_GrandTotal_{i}",
            f'="{_q_safe(label)}:  " & {e[1:]}',
            bold=True, font_size="9pt", fg="#111111",
            text_align="Left", vertical_align="Middle",
            border_color="#ffffff", padding="2pt", can_grow=False)
        tb = ri[-1]
        _sub(tb, "Top", f"{y:.2f}in")
        _sub(tb, "Left", "0.10in")
        _sub(tb, "Width", "5.0in")
        _sub(tb, "Height", "0.22in")
        y += 0.26
    if body_h_el is not None:
        body_h_el.text = f"{y + 0.1:.2f}in"


_SUMLOOKUP_CODE = (
    "Public Function SumLookup(ByVal items As Object()) As Decimal\n"
    "  If items Is Nothing Then Return CDec(0)\n"
    "  Dim t As Decimal = 0\n"
    "  For Each o As Object In items\n"
    "    If o IsNot Nothing AndAlso IsNumeric(o) Then t += CDec(o)\n"
    "  Next\n"
    "  Return t\n"
    "End Function"
)


def _crossquery_subtotal_keys(report, reset_group, src_query):
    """When a group subtotal resets at a group owned by master query M and its
    summed column lives in a DIRECT child query D of M (Oracle <link>), return
    (M.name, [(parent_col, child_col), ...]) from D's EXACT link_pairs (composite
    when D links on >1 column). Else None -> caller leaves it honestly flagged
    (indirect nesting / no explicit link keys are not safely resolvable)."""
    qs = getattr(report, "queries", None) or []
    owner = {}
    for q in qs:
        for gn in (getattr(q, "group_names", None) or []):
            owner[(gn or "").upper()] = q
    master = owner.get((reset_group or "").upper())
    child = next((q for q in qs
                  if (q.name or "").upper() == (src_query or "").upper()), None)
    if master is None or child is None:
        return None
    # DIRECT child only: child's parent_group must be a group OWNED BY master.
    if owner.get((getattr(child, "parent_group", "") or "").upper()) is not master:
        return None
    pairs = [(p, c) for (p, c) in (getattr(child, "link_pairs", None) or []) if p and c]
    if not pairs:
        return None
    return (master.name, pairs)


def _build_subtotal_tablix(name, dataset_name, key_expr, key_label, subtotals,
                           top_in):
    """Minimal flat Tablix bound to the MASTER dataset (one row per group key,
    e.g. per company): a header row + a detail row. Col 0 shows the group key;
    each subsequent col a cross-query subtotal expression. Returns (tablix,
    width_in). No row GROUP needed -- the master dataset already yields one row
    per key."""
    cols = [(key_label, key_expr)] + list(subtotals)
    colw = 1.7
    tablix = ET.Element(_q("Tablix"))
    tablix.set("Name", name)
    tbody = _sub(tablix, "TablixBody")
    tcols = _sub(tbody, "TablixColumns")
    for _ in cols:
        _sub(_sub(tcols, "TablixColumn"), "Width", f"{colw}in")
    trows = _sub(tbody, "TablixRows")
    hrow = _sub(trows, "TablixRow"); _sub(hrow, "Height", "0.24in")
    hcells = _sub(hrow, "TablixCells")
    for i, (label, _e) in enumerate(cols):
        cc = _sub(_sub(hcells, "TablixCell"), "CellContents")
        _build_textbox(cc, f"{name}_H{i}", '="' + _q_safe(label) + '"',
                       bold=True, font_size="9pt", bg="#eef2f6", fg="#282828",
                       text_align="Left", vertical_align="Middle",
                       border_color="#d0d0d0", padding="3pt", can_grow=False)
    drow = _sub(trows, "TablixRow"); _sub(drow, "Height", "0.22in")
    dcells = _sub(drow, "TablixCells")
    for i, (_l, expr) in enumerate(cols):
        cc = _sub(_sub(dcells, "TablixCell"), "CellContents")
        _build_textbox(cc, f"{name}_D{i}", expr,
                       font_size="9pt", fg="#282828",
                       text_align="Left" if i == 0 else "Right",
                       vertical_align="Middle", border_color="#d0d0d0",
                       padding="3pt", can_grow=True)
    chm = _sub(_sub(tablix, "TablixColumnHierarchy"), "TablixMembers")
    for _ in cols:
        _sub(chm, "TablixMember")
    rhm = _sub(_sub(tablix, "TablixRowHierarchy"), "TablixMembers")
    hmem = _sub(rhm, "TablixMember"); _sub(hmem, "KeepWithGroup", "After")
    dmem = _sub(rhm, "TablixMember")
    _sub(dmem, "Group").set("Name", f"{name}_Det")
    _sub(tablix, "DataSetName", _safe(dataset_name))
    _sub(tablix, "Top", f"{top_in:.2f}in")
    _sub(tablix, "Left", "0.10in")
    _sub(tablix, "Height", "0.46in")
    _sub(tablix, "Width", f"{colw * len(cols):.2f}in")
    return tablix, colw * len(cols)


def _ensure_group_subtotals_emitted(root: ET.Element, report) -> None:
    """Oracle GROUP <summary reset="G_x"> subtotals where the summed column is in
    a DIRECT child query of the reset group's master -> render a per-key subtotal
    Tablix (bound to the master dataset) below the body, each cell a CROSS-DATASET
    aggregate over Oracle's EXACT <link> keys:
        =Code.SumLookup(LookupSet(<masterKeys>, <childKeys>, Fields!src.Value, "D"))
    (=Sum(LookupSet(..)) is INVALID -> the VB reducer is mandatory; COUNT uses
    .Length). Only PLACED, direct-child, link-resolvable subtotals ship; indirect
    / unplaced ones are left honestly flagged. Gated -> non-subtotal reports stay
    byte-identical."""
    formulas = getattr(report, "formulas", None) or []
    grp = [f for f in formulas
           if getattr(f, "agg_function", "") and getattr(f, "agg_source", "")
           and (getattr(f, "agg_scope", "") or "").lower() not in ("", "report")]
    if not grp:
        return
    body = root.find(_q("Body"))
    if body is None:
        return
    placed = set()

    def _collect(g):
        for f in (getattr(g, "fields", None) or []):
            s = (getattr(f, "source", "") or "").upper()
            if s:
                placed.add(s)
        for c in (getattr(g, "children", None) or []):
            _collect(c)
    for lg in (getattr(report, "layout", None) or []):
        _collect(lg)
    grp = [f for f in grp if (f.name or "").upper() in placed]
    if not grp:
        return

    col_owner: dict = {}
    for q in (getattr(report, "queries", None) or []):
        for it in (getattr(q, "items", None) or []):
            if getattr(it, "name", ""):
                col_owner.setdefault(it.name.upper(), q.name)

    existing = {(v.text or "") for v in root.iter(_q("Value"))}
    # master_name -> (key_pair_for_display, [(label, expr)])
    by_master: dict = {}
    for f in grp:
        src_q = col_owner.get((f.agg_source or "").upper())
        if not src_q:
            continue
        info = _crossquery_subtotal_keys(report, f.agg_scope, src_q)
        if info is None:
            continue
        master_name, pairs = info
        fn_disp = (f.agg_function or "").lower()
        pkey = ' & "|" & '.join(f"Fields!{_safe(p)}.Value" for p, _c in pairs)
        ckey = ' & "|" & '.join(f"Fields!{_safe(c)}.Value" for _p, c in pairs)
        lset = (f"LookupSet({pkey}, {ckey}, "
                f'Fields!{_safe(f.agg_source)}.Value, "{_safe(src_q)}")')
        expr = (f"={lset}.Length" if fn_disp == "count"
                else f"=Code.SumLookup({lset})")
        if expr in existing:
            continue
        label = re.sub(r"(?i)^(CS|CF|CP|CN)_", "", f.name or "")
        label = re.sub(r"_+", " ", label).strip().title() or (f.name or "Sub")
        slot = by_master.setdefault(master_name, [pairs[0][0], []])
        slot[1].append((label, expr))
        existing.add(expr)
    by_master = {m: v for m, v in by_master.items() if v[1]}
    if not by_master:
        return

    ri = body.find(_q("ReportItems"))
    if ri is None:
        ri = _sub(body, "ReportItems")
    body_h_el = body.find(_q("Height"))
    try:
        body_h = float((body_h_el.text or "0").replace("in", "")) \
            if body_h_el is not None else 0.0
    except ValueError:
        body_h = 0.0
    top = body_h + 0.25
    emitted_sum = False
    for i, (master_name, (mkey, items)) in enumerate(by_master.items()):
        if any("Code.SumLookup" in e for _l, e in items):
            emitted_sum = True
        tbx, w = _build_subtotal_tablix(
            f"Subtot_{i}", master_name,
            f"=Fields!{_safe(mkey)}.Value", "Group", items, top)
        ri.append(tbx)
        top += 0.46 + 0.24 + 0.25
    if emitted_sum:
        code = root.find(_q("Code"))
        if code is not None and not (code.text or "").strip():
            code.text = _SUMLOOKUP_CODE
    if body_h_el is not None:
        body_h_el.text = f"{top + 0.1:.2f}in"


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
    # Report-level Oracle <summary> grand totals that the body builders skipped
    # (multi-section / flat) -> emit as labeled dataset-scoped aggregates below
    # the content. After the scope/repair passes so their explicit dataset scope
    # is preserved.
    _ensure_summary_totals_emitted(root, report)
    # Cross-query GROUP subtotals (placed, direct-child, link-resolvable) -> a
    # per-key master-bound subtotal Tablix using LookupSet over Oracle's exact
    # <link> keys. After the grand-total pass so the body-height baseline is set.
    _ensure_group_subtotals_emitted(root, report)
    # Fidelity: stamp <Format> onto field values that carried an Oracle
    # formatMask, so currency / dates / thousands render like the original.
    _apply_field_formats(root, report)
    try:
        ET.indent(root, space="  ")
    except AttributeError:
        pass
    body = ET.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="utf-8"?>\n' + body
