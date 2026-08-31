"""
NO-PROMPT FATAL GATE — the machine version of the two Report Builder rituals
that must never fail in production:

  (1) the generated RDL must not fail at UPLOAD because the emitted query is
      broken, and
  (2) Report Builder must never throw the design-time "Define Query
      Parameters" prompt when the user refreshes datasets.

This module is a standalone auditor over a *generated* RDL string. It is
deliberately independent of the preflight validator so that a regression in
preflight cannot blind the gate (and vice versa). Generators must NOT import
this module — it is a verification surface, not a production dependency.

Five legs, each one a historically-proven failure class:

  L1  static_commandtext   — CommandText is STATIC SQL, never an expression
                             (leading '='). The fire-158 class: an expression
                             CommandText makes Report Builder prompt the END
                             USER for every query parameter at Refresh Fields.
  L2  binds_declared       — every bind (:P_X / @P_X) referenced by live SQL
                             code in a CommandText is declared as a
                             QueryParameter of that same DataSet, its Value is
                             non-empty (an empty <Value/> is itself the prompt
                             trigger), and any Parameters!X.Value it references
                             names a declared ReportParameter with EXACT case.
  L3  param_defaults       — every ReportParameter carries a usable default: a
                             <DefaultValue> with a DataSetReference or with
                             Values/Value entries whose text is non-empty
                             (=Nothing or a literal). An empty <Value/> is the
                             exact historical prompt trigger and never allowed.
  L4  shared_datasource    — DataSources use the shared-reference form
                             (<DataSourceReference>). The embedded
                             <ConnectionProperties> form is allowed only when
                             the caller deliberately supplied a connection
                             (allow_embedded=True): embedded connections force
                             design-time query evaluation and pop the prompt.
  L5  fields_match_sql     — every Field's DataField must be a column the
                             static SELECT list actually yields (the ORA-00904
                             "invalid identifier" class at upload/refresh).
                             Reuses the repo's SELECT-alias extraction and
                             stays deliberately lenient where static
                             extraction cannot be complete (opaque star
                             expansions, unparseable items) — a gate must
                             never cry wolf. STAR-SIDE LEG: a star does not
                             blind the dataset — explicit aliases beside it
                             stay checkable (a field named <alias>+numeric
                             suffix is a proven derivation desync), and a
                             star over a single inline view resolves to the
                             inner select list, enabling full membership
                             checking plus the ORA-00918 duplicate-column
                             class that kills SELECT O.* at Refresh Fields.

Each violation is returned as "leg:rule: message". An empty list means the
artifact passes the whole gate.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional


# ---------------------------------------------------------------------------
# XML helpers (namespace-agnostic — the gate must work across RDL namespaces)
# ---------------------------------------------------------------------------

def _local(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def _iter_local(root: ET.Element, name: str):
    for el in root.iter():
        if _local(el.tag) == name:
            yield el


def _child_local(parent: Optional[ET.Element], name: str) -> Optional[ET.Element]:
    if parent is None:
        return None
    for el in list(parent):
        if _local(el.tag) == name:
            return el
    return None


def _children_local(parent: Optional[ET.Element], name: str) -> List[ET.Element]:
    if parent is None:
        return []
    return [el for el in list(parent) if _local(el.tag) == name]


# ---------------------------------------------------------------------------
# SQL helpers
# ---------------------------------------------------------------------------

def _live_sql(sql: str) -> str:
    """Blank out string literals, quoted identifiers, and comments so scans
    only see live SQL *code*. Character positions are preserved (masked spans
    become spaces). Oracle escapes a quote inside a literal by doubling it."""
    if not sql:
        return ""
    n = len(sql)
    out = list(sql)
    i = 0
    while i < n:
        c = sql[i]
        if c == "'":                                   # string literal
            j = i + 1
            while j < n:
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            for k in range(i, min(j + 1, n)):
                out[k] = " "
            i = j + 1
        elif c == '"':                                 # quoted identifier
            j = sql.find('"', i + 1)
            j = n - 1 if j < 0 else j
            for k in range(i, j + 1):
                out[k] = " "
            i = j + 1
        elif sql.startswith("--", i):                  # line comment
            j = sql.find("\n", i)
            j = n if j < 0 else j
            for k in range(i, j):
                out[k] = " "
            i = j
        elif sql.startswith("/*", i):                  # block comment
            j = sql.find("*/", i + 2)
            j = n - 2 if j < 0 else j
            for k in range(i, min(j + 2, n)):
                out[k] = " "
            i = j + 2
        else:
            i += 1
    return "".join(out)


# Same shapes the generator itself detects (kept literal here on purpose:
# the gate must not import the generator it verifies).
_ORACLE_BIND_RE = re.compile(r"(?<![:\w]):([A-Za-z_]\w*)")
_TSQL_BIND_RE = re.compile(r"(?<![@\w])@([A-Za-z_]\w*)")
_PARAM_REF_RE = re.compile(r"Parameters!([A-Za-z_][A-Za-z0-9_]*)\.Value")
_SELECT_KW_RE = re.compile(r"\bSELECT\b", re.IGNORECASE)
_WORD_RE = re.compile(r"\b(SELECT|FROM)\b", re.IGNORECASE)


def _live_binds(live_sql: str) -> List[str]:
    """Unique bind names referenced by live SQL code, in order."""
    seen = set()
    out: List[str] = []
    for rx in (_ORACLE_BIND_RE, _TSQL_BIND_RE):
        for m in rx.finditer(live_sql):
            name = m.group(1)
            if name.upper() not in seen:
                seen.add(name.upper())
                out.append(name)
    return out


def _first_select_span(live: str):
    """Locate the first depth-0 ``SELECT ... FROM`` span in masked SQL and
    return ``(sel_end, frm_start, items, spans)`` where *items* are the
    top-level SELECT-list item strings and *spans* are each item's
    ``(start, end)`` character positions in ``live`` (masking preserves
    positions, so they index the RAW SQL identically). WITH-CTE bodies live
    inside parens, so the first depth-0 SELECT is the statement's OUTER one
    (the repo's lazy ``SELECT(.+?)FROM`` regex alone grabs the CTE's inner
    select — the exact false-alarm class this depth scan exists to
    prevent). Returns ``None`` when there is no complete depth-0 span."""
    depth = 0
    sel_end = -1
    frm_start = -1
    i = 0
    n = len(live)
    while i < n:
        c = live[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif depth == 0:
            m = _WORD_RE.match(live, i)
            if m:
                if m.group(1).upper() == "SELECT" and sel_end < 0:
                    sel_end = m.end()
                elif m.group(1).upper() == "FROM" and sel_end >= 0:
                    frm_start = m.start()
                    break
                i = m.end()
                continue
        i += 1
    if sel_end < 0 or frm_start < 0:
        return None
    # strip a leading DISTINCT / UNIQUE / ALL qualifier
    qm = re.match(r"\s*(?:DISTINCT|UNIQUE|ALL)\b", live[sel_end:frm_start],
                  flags=re.IGNORECASE)
    body_start = sel_end + (qm.end() if qm else 0)
    items: List[str] = []
    spans: List[tuple] = []
    d = 0
    cur_start = body_start
    j = body_start
    while j < frm_start:
        ch = live[j]
        if ch == "(":
            d += 1
        elif ch == ")":
            d -= 1
        if ch == "," and d == 0:
            items.append(live[cur_start:j].strip())
            spans.append((cur_start, j))
            cur_start = j + 1
        j += 1
    items.append(live[cur_start:frm_start].strip())
    spans.append((cur_start, frm_start))
    return sel_end, frm_start, items, spans


def _is_star_item(item: str) -> bool:
    """True for '*' or 'alias.*' (or a masked-identifier '.*') items."""
    return bool(re.match(r"^\s*(?:[\w$#]+\s*\.\s*|\.\s*)?\*\s*$", item))


# Depth-0 keywords that may legally follow a FROM clause's single source.
_AFTER_FROM_KW_RE = re.compile(
    r"^(?:WHERE|GROUP|HAVING|ORDER|CONNECT|START|MODEL|FETCH|OFFSET|FOR"
    r"|UNION|INTERSECT|MINUS)\b", re.IGNORECASE)


def _single_item_name(raw_item: str, select_columns) -> Optional[str]:
    """Output column name of ONE raw select-list item via the repo's
    extraction, or None when it is not cleanly derivable. Extracting item
    by item (instead of the whole list at once) keeps DUPLICATE names
    visible — ``_select_columns`` dedupes its result, which is correct for
    the callers it was built for but would blind the duplicate-column
    check below."""
    cols = select_columns("SELECT " + raw_item + " FROM DUAL")
    if len(cols) != 1:
        return None
    return cols[0].strip().strip('"').upper() or None


def _inline_view_star_contents(sql: str, live: str, frm_start: int,
                               qualifier: str, select_columns):
    """Column names (duplicates preserved, UPPER) that a star item provably
    expands to, or ``None`` when the star is opaque.

    Resolvable exactly when the outer FROM clause is a SINGLE parenthesized
    inline view — ``FROM ( <inner select> ) alias`` — whose alias matches
    the star's qualifier (an unqualified ``*`` needs no alias match), and
    whose inner SELECT list is fully enumerable with no nested star. This
    is the converter's own wrap shape (``SELECT O.*, ... FROM ( query ) O``),
    so the "star projection" is not opaque at all: it IS the inner select
    list. Anything else (a real table, a join, a CTE name, an inner star)
    returns None — the gate never guesses about star contents."""
    m = re.compile(r"FROM\s*\(", re.IGNORECASE).match(live, frm_start)
    if not m:
        return None
    open_p = m.end() - 1
    depth = 0
    close_p = -1
    for i in range(open_p, len(live)):
        c = live[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                close_p = i
                break
    if close_p < 0:
        return None
    tail = live[close_p + 1:]
    am = re.match(r"\s*([A-Za-z_][\w$#]*)?", tail)
    alias = am.group(1) or ""
    rest = tail[am.end():].strip()
    if alias and _AFTER_FROM_KW_RE.match(alias + " "):
        rest = (alias + " " + rest).strip()   # alias-less view; that word
        alias = ""                            # was the next clause keyword
    if rest and not _AFTER_FROM_KW_RE.match(rest):
        return None       # join / comma list / unknown tail: not the shape
    if qualifier and qualifier.upper() != alias.upper():
        return None
    inner_live = live[open_p + 1:close_p]
    span = _first_select_span(inner_live)
    if span is None:
        return None
    _, _, i_items, i_spans = span
    base = open_p + 1
    names: List[str] = []
    for (a, b), it in zip(i_spans, i_items):
        if not it.strip():
            return None
        if _is_star_item(it):
            return None   # nested star: unknowable
        nm = _single_item_name(sql[base + a:base + b], select_columns)
        if nm is None:
            return None
        names.append(nm)
    return names


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------

def audit_no_prompt(rdl_xml: str, allow_embedded: bool = False) -> List[str]:
    """Audit one generated RDL. Returns a list of violations (empty = pass).

    ``allow_embedded`` reflects the caller's deliberate choice to bake an
    embedded connection (rdl_postprocess datasource-config path). The default
    conversion path must always emit the shared-reference form.
    """
    violations: List[str] = []
    try:
        root = ET.fromstring(rdl_xml)
    except ET.ParseError as exc:
        return [f"L0:xml.parse: RDL is not well-formed XML: {exc}"]

    # -- declared report parameters -----------------------------------------
    report_params: List[ET.Element] = []
    for rps in _iter_local(root, "ReportParameters"):
        report_params.extend(_children_local(rps, "ReportParameter"))
    declared_exact = {rp.get("Name", "") for rp in report_params}

    # -- L3: every ReportParameter carries a usable default -----------------
    for rp in report_params:
        pname = rp.get("Name", "?")
        dv = _child_local(rp, "DefaultValue")
        if dv is not None and _child_local(dv, "DataSetReference") is not None:
            continue  # dataset-driven default is a usable default
        values_el = _child_local(dv, "Values")
        vals = _children_local(values_el, "Value")
        if dv is None or values_el is None or not vals:
            violations.append(
                f"L3:param_default_missing: ReportParameter {pname!r} has no "
                f"DefaultValue/Values/Value entry — Report Builder pops the "
                f"'Define Query Parameters' dialog for it at Refresh Fields.")
            continue
        for v in vals:
            if not (v.text or "").strip():
                violations.append(
                    f"L3:param_default_empty_value: ReportParameter {pname!r} "
                    f"carries an EMPTY <Value/> default — the exact historical "
                    f"prompt trigger. Emit =Nothing or a literal instead.")

    # -- L4: shared data source ---------------------------------------------
    datasets = list(_iter_local(root, "DataSet"))
    datasources = list(_iter_local(root, "DataSource"))
    if datasets and not datasources:
        violations.append(
            "L4:datasource_missing: the report has DataSets but declares no "
            "DataSource — the upload fails immediately.")
    for ds_el in datasources:
        dname = ds_el.get("Name", "?")
        ref = _child_local(ds_el, "DataSourceReference")
        conn = _child_local(ds_el, "ConnectionProperties")
        if ref is not None and (ref.text or "").strip():
            continue
        if conn is not None:
            if not allow_embedded:
                violations.append(
                    f"L4:embedded_connection: DataSource {dname!r} embeds "
                    f"<ConnectionProperties> on the default conversion path — "
                    f"embedded connections force design-time query evaluation "
                    f"and pop the parameter prompt. Emit a shared "
                    f"<DataSourceReference>.")
            continue
        violations.append(
            f"L4:datasource_no_reference: DataSource {dname!r} has neither a "
            f"non-empty <DataSourceReference> nor <ConnectionProperties>.")

    # -- per-DataSet legs ----------------------------------------------------
    try:
        # The repo's existing SELECT-alias extraction (the ORA-00904 class).
        from converter.subreports import _select_columns
    except Exception:                               # pragma: no cover
        from backend.converter.subreports import _select_columns  # type: ignore

    for ds in datasets:
        dsname = ds.get("Name", "?")
        query = _child_local(ds, "Query")
        ct = _child_local(query, "CommandText")
        sql = (ct.text or "") if ct is not None else ""

        # -- L1: static SQL, never an expression ---------------------------
        if sql.lstrip().startswith("="):
            violations.append(
                f"L1:expression_commandtext: DataSet {dsname!r} CommandText "
                f"is an EXPRESSION — Report Builder cannot evaluate it at "
                f"design time and prompts the end user for every query "
                f"parameter. CommandText must be static SQL.")
            continue  # the remaining SQL legs are meaningless on an expression

        live = _live_sql(sql)

        # -- L2: every referenced bind is declared + prompt-free -----------
        qp_map: Dict[str, str] = {}
        for qp in _iter_local(ds, "QueryParameter"):
            qname = (qp.get("Name") or "").lstrip("@:")
            val_el = _child_local(qp, "Value")
            vtext = (val_el.text or "") if val_el is not None else ""
            qp_map[qname.upper()] = vtext
            if not vtext.strip():
                violations.append(
                    f"L2:qp_empty_value: DataSet {dsname!r} QueryParameter "
                    f"{qname!r} has an empty <Value/> — the exact prompt "
                    f"trigger. Bind it to =Parameters!X.Value or =Nothing.")
                continue
            for pref in _PARAM_REF_RE.findall(vtext):
                if pref not in declared_exact:
                    violations.append(
                        f"L2:qp_param_undeclared: DataSet {dsname!r} "
                        f"QueryParameter {qname!r} binds to "
                        f"Parameters!{pref}.Value but no ReportParameter "
                        f"{pref!r} is declared with that exact case — SSRS "
                        f"rejects the upload.")
        for bind in _live_binds(live):
            if bind.upper() not in qp_map:
                violations.append(
                    f"L2:bind_undeclared: DataSet {dsname!r} SQL references "
                    f"bind {bind!r} but declares no matching QueryParameter — "
                    f"Report Builder prompts for it at Refresh Fields.")

        # -- L5: every DataField is a column the SELECT actually yields ----
        if not _SELECT_KW_RE.search(live):
            continue  # commented scaffold / empty-query placeholder
        if "lexical ref &" in sql:
            # The converter honestly declined a runtime-lexical splice and
            # left a marked stub — the SQL is by construction incomplete
            # (preflight already flags it), so a static column enumeration
            # is meaningless here.
            continue
        span = _first_select_span(live)
        if span is None:
            continue  # no complete depth-0 SELECT ... FROM span
        sel_end, frm_start, items, spans = span
        if not items:
            continue
        star_flags = [_is_star_item(it) for it in items]
        if any(star_flags):
            # -- STAR-SIDE LEG: a star projection does not blind the whole
            # dataset. Two things stay provable without ever guessing what
            # an opaque star contains:
            #   (1) the EXPLICIT items' output aliases are enumerable, and
            #   (2) when the star ranges over a single INLINE VIEW (the
            #       converter's own wrap shape, FROM ( query ) O), the
            #       star's contents ARE the inner select list — fully
            #       checkable, including the ORA-00918 duplicate-name
            #       class that kills SELECT O.* at Refresh Fields.
            explicit_aliases = set()
            complete = True
            for (a, b), star in zip(spans, star_flags):
                if star:
                    continue
                nm = _single_item_name(sql[a:b], _select_columns)
                if nm is None:
                    complete = False
                    break
                explicit_aliases.add(nm)
            if not complete:
                continue  # explicit side not enumerable — honest abstain
            resolved: set = set()
            all_resolved = True
            dup_reported = set()
            for (a, b), star in zip(spans, star_flags):
                if not star:
                    continue
                qm = re.match(r"^\s*(?:([A-Za-z_][\w$#]*)\s*\.\s*)?\*\s*$",
                              live[a:b])
                qualifier = (qm.group(1) or "") if qm else ""
                contents = _inline_view_star_contents(
                    sql, live, frm_start, qualifier, _select_columns)
                if contents is None:
                    all_resolved = False
                    continue
                for d in sorted({n for n in contents
                                 if contents.count(n) > 1}):
                    if d in dup_reported:
                        continue
                    dup_reported.add(d)
                    violations.append(
                        f"L5:star_view_duplicate_column: DataSet {dsname!r} "
                        f"expands a star over an inline view whose SELECT "
                        f"list yields the column name {d!r} more than once "
                        f"— ORA-00918 'column ambiguously defined' at "
                        f"upload/refresh (and even where tolerated, "
                        f"duplicate result names break SSRS field "
                        f"binding). Alias the duplicate occurrences.")
                resolved.update(contents)
            known = explicit_aliases | resolved
            for fld in _iter_local(ds, "Field"):
                df = _child_local(fld, "DataField")
                if df is None:
                    continue  # calculated field — no column
                dfname = (df.text or "").strip().strip('"')
                if not dfname:
                    continue
                dfu = dfname.upper()
                if dfu in known:
                    continue
                if all_resolved:
                    violations.append(
                        f"L5:field_not_in_select: DataSet {dsname!r} Field "
                        f"{fld.get('Name', '?')!r} binds DataField "
                        f"{dfname!r} but neither the explicit SELECT items "
                        f"nor the star's resolved inline-view projection "
                        f"yields such a column — the ORA-00904 class: the "
                        f"dataset fails at upload/refresh.")
                    continue
                # Opaque star: membership is unknowable, but a NEAR-MISS
                # of an explicit alias is still a proven desync — a field
                # named <alias>+<numeric suffix> was derived from that
                # explicit item's stem (the report tool's dedup-rename
                # pattern), yet the SELECT yields only the unsuffixed
                # alias. The star cannot be presumed to carry the
                # suffixed spelling. Direction matters: the field
                # carrying the suffix is the desync; the REVERSE shape
                # (field = stem, alias = stem+suffix) is the benign
                # covered-by-star pattern the wild corpus proved (a stub
                # alias deliberately suffixed AROUND a star column) and
                # is never flagged.
                for alias in sorted(explicit_aliases):
                    if (dfu != alias and dfu.startswith(alias)
                            and re.fullmatch(r"_?\d+", dfu[len(alias):])):
                        violations.append(
                            f"L5:star_alias_near_miss: DataSet {dsname!r} "
                            f"Field {fld.get('Name', '?')!r} binds "
                            f"DataField {dfname!r} — a numeric-suffix "
                            f"variant of the explicit SELECT alias "
                            f"{alias!r}. The SELECT yields the unsuffixed "
                            f"alias only, so the field/alias derivations "
                            f"desynced (the dedup-rename class): the "
                            f"field reads NULL or fails at refresh.")
                        break
            continue
        # Feed the repo's alias extraction EXACTLY the outer SELECT span.
        # (Positions in the masked text equal positions in the raw SQL, so
        # the fragment keeps the original literals/aliases; handing it the
        # whole statement instead would let the lazy SELECT..FROM regex
        # grab a WITH-CTE's inner select — a proven false-alarm class.)
        fragment = "SELECT " + sql[sel_end:frm_start] + " FROM DUAL"
        cols = _select_columns(fragment)
        if not cols or len(cols) != len(items):
            continue  # extraction incomplete — a lenient gate never guesses
        colset = {c.strip().strip('"').upper() for c in cols}
        for fld in _iter_local(ds, "Field"):
            df = _child_local(fld, "DataField")
            if df is None:
                continue  # calculated field (Value expression) — no column
            dfname = (df.text or "").strip().strip('"')
            if dfname and dfname.upper() not in colset:
                violations.append(
                    f"L5:field_not_in_select: DataSet {dsname!r} Field "
                    f"{fld.get('Name', '?')!r} binds DataField {dfname!r} but "
                    f"the SELECT list yields no such column — the ORA-00904 "
                    f"class: the dataset fails at upload/refresh.")

    return violations
