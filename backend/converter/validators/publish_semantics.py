"""
PUBLISH-SEMANTICS audit — the rules Report Server enforces when it COMPILES
a report definition at publish time, which neither the XSD nor a local
render can see.

Why this module exists (project fatal error #1, second occurrence): a
generated RDL was XSD-valid, carried no duplicate item names, no undeclared
references, and LOADED + RENDERED through Microsoft's ReportViewer engine —
and the customer's Report Server still REJECTED it at upload. The reason is
structural, not accidental:

  * the XSD encodes SHAPE only ("a Tablix may carry a DataSetName"), never
    MEANING ("...but a nested one is ignored, so the fields inside it must
    belong to the CONTAINER's dataset");
  * the local engine leg renders with expressions STATICIZED (this machine
    cannot start an expression host), so expression semantics are never
    compiled locally;
  * even in expression mode ReportViewer is more forgiving than the server:
    for a nested data region it simply ignores the declared DataSetName and
    renders, where the server compiles the expression against the container
    scope and refuses the report.

So this is a pure RULE ENGINE over the RDL tree. No engine, no database, no
network — every rule below is a documented Report Server / RDL constraint,
quoted at its implementation site with the source it came from.

Sources quoted throughout:
  [SCOPE]  "Expression scope for totals, aggregates, and built-in
           collections in a paginated report", Microsoft Learn
  [PGSEC]  "Page headers and footers in a paginated report", Microsoft Learn
  [RPTIT]  "ReportItems collection references in a paginated report",
           Microsoft Learn
  [AGGREF] "Aggregate functions reference for paginated reports",
           Microsoft Learn (the "Restrictions on Built-in Fields,
           Collections, and Aggregate Functions" table)
  [MS-RDL] "[MS-RDL]: Report Definition Language File Format", Microsoft
           Open Specifications (element-level normative text)
  [ERRMSG] Report Server error strings (rsFieldReference,
           rsFieldInPageSectionExpression, rsInvalidAggregateScope,
           rsAggregateOfNonNumericData, rsInvalidRepeatOnNewPage, ...)

Public API
----------
``audit_publish_semantics(rdl_xml)`` -> dict with "violations" (rich dicts)
``publish_violations(rdl_xml)``      -> sorted list of stable id strings,
                                        the form a ratcheted gate stores.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Expression scanning primitives
# ---------------------------------------------------------------------------

# Data-region element names. [MS-RDL] models Tablix/Chart/GaugePanel/Map/
# CustomReportItem as data regions; List/Table/Matrix are the pre-2008
# spellings and are kept so the rules also judge legacy input.
REGION_TAGS = frozenset({
    "Tablix", "Chart", "GaugePanel", "CustomReportItem", "Map",
    "List", "Table", "Matrix",
})

# Aggregate functions written operand-first with an OPTIONAL trailing scope
# argument:  Sum(<expr> [, "<scope>"]).
AGG_SCOPE_LAST = frozenset({
    "Sum", "Avg", "Count", "CountDistinct", "Min", "Max", "StDev", "StDevP",
    "Var", "VarP", "First", "Last", "Previous", "RunningValue", "Aggregate",
    "Union",
})

# Functions whose FIRST argument IS the scope:  RowNumber("<scope>").
AGG_SCOPE_FIRST = frozenset({"RowNumber", "CountRows", "InScope", "Level"})

# Lookup family:  Lookup(source, destination, result, "<dataset>"). Only the
# destination/result arguments resolve against the named dataset — the
# SOURCE argument is evaluated in the current scope. [AGGREF]
LOOKUP_FUNCS = frozenset({"Lookup", "LookupSet", "Multilookup"})

ALL_AGG_FUNCS = AGG_SCOPE_LAST | AGG_SCOPE_FIRST | LOOKUP_FUNCS

# [ERRMSG] rsAggregateOfNonNumericData: "The Value expression for the
# textbox 'X' uses a numeric aggregate function on data that is not
# numeric. Numeric aggregate functions (Sum, Avg, StDev, Var, StDevP, and
# VarP) can only aggregate numeric data."
NUMERIC_AGGS = frozenset({"Sum", "Avg", "StDev", "Var", "StDevP", "VarP"})

_CALL_OPEN_RE = re.compile(r"(?<![A-Za-z0-9_.!])([A-Za-z_][A-Za-z0-9_]*)\s*\(")
_FIELD_BANG_RE = re.compile(r"Fields!([A-Za-z_][A-Za-z0-9_]*)")
_FIELD_IDX_RE = re.compile(r"Fields\s*\(\s*\"([^\"]+)\"\s*\)")
_REPORTITEM_BANG_RE = re.compile(r"ReportItems!([A-Za-z_][A-Za-z0-9_]*)")
_REPORTITEM_IDX_RE = re.compile(r"ReportItems\s*\(\s*\"([^\"]+)\"\s*\)")
_PARAM_RE = re.compile(r"Parameters!([A-Za-z_][A-Za-z0-9_]*)")
_GLOBAL_PAGE_RE = re.compile(r"Globals!\s*(PageNumber|TotalPages)\b")
_STRING_SCOPE_RE = re.compile(r"^\"([^\"]*)\"$")

# A CLS-compliant identifier as [MS-RDL] defines names: "MUST be a
# case-sensitive CLS-compliant identifier" — begins with a letter or
# underscore, then letters/digits/underscores, no spaces or punctuation.
_CLS_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class Call:
    """One function call found in an expression, with argument spans."""

    __slots__ = ("func", "start", "open_paren", "close_paren", "args")

    def __init__(self, func, start, open_paren, close_paren, args):
        self.func = func
        self.start = start                # index of the function name
        self.open_paren = open_paren      # index just past '('
        self.close_paren = close_paren    # index of the matching ')'
        self.args = args                  # [(start, end)] half-open spans


def _scan_calls(expr: str) -> List[Call]:
    """Every function call in ``expr`` with its top-level argument spans.

    A balanced-paren scan, not a regex: an argument may itself be
    parenthesized arithmetic or a nested call, and string literals (which
    can hold parens and commas) are skipped so they never skew the depth.
    Unbalanced text yields no call rather than a bogus one — an expression
    that does not parse is reported by its own rule, not by guessing.
    """
    expr = expr or ""
    n = len(expr)
    out: List[Call] = []
    for m in _CALL_OPEN_RE.finditer(expr):
        open_paren = m.end()
        depth, i = 1, open_paren
        arg_start = open_paren
        args: List[Tuple[int, int]] = []
        while i < n and depth:
            c = expr[i]
            if c == '"':
                j = expr.find('"', i + 1)
                i = (j if j >= 0 else n) + 1
                continue
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    break
            elif c == "," and depth == 1:
                args.append((arg_start, i))
                arg_start = i + 1
            i += 1
        if depth:
            continue                      # unbalanced — not a judgeable call
        if i > open_paren or arg_start < i:
            args.append((arg_start, i))
        if len(args) == 1 and not expr[args[0][0]:args[0][1]].strip():
            args = []                     # zero-argument call: Now()
        out.append(Call(m.group(1), m.start(), open_paren, i, args))
    return out


def _literal_scope(text: str) -> Optional[str]:
    """The scope named by an argument, when it is a bare string constant.

    [SCOPE] "Named scope  The name of a dataset, a data region, or a data
    region group that is in scope for the expression." An argument that is
    not a string constant (a variable, a concatenation) cannot be judged
    statically and yields None.
    """
    m = _STRING_SCOPE_RE.match((text or "").strip())
    return m.group(1) if m else None


def _scope_of_call(call: Call, expr: str) -> Optional[str]:
    """The dataset/region/group scope a call names, or None."""
    if not call.args:
        return None
    if call.func in AGG_SCOPE_FIRST:
        return _literal_scope(expr[call.args[0][0]:call.args[0][1]])
    if call.func in LOOKUP_FUNCS or call.func in AGG_SCOPE_LAST:
        if len(call.args) < 2:
            return None
        a, b = call.args[-1]
        return _literal_scope(expr[a:b])
    return None


def _scoped_spans(expr: str) -> List[Tuple[int, int, str]]:
    """(start, end, scope) for every call whose operand region resolves
    against an explicitly named scope.

    For the Lookup family the covered region begins AFTER the first
    top-level comma, because [AGGREF] evaluates the SOURCE expression in the
    current scope: a foreign field there is exactly the server's rejection.
    """
    out: List[Tuple[int, int, str]] = []
    for call in _scan_calls(expr):
        scope = _scope_of_call(call, expr)
        if not scope:
            continue
        if call.func in LOOKUP_FUNCS:
            if len(call.args) < 2:
                continue
            out.append((call.args[1][0], call.close_paren, scope))
        elif call.func in AGG_SCOPE_FIRST:
            out.append((call.open_paren, call.close_paren, scope))
        else:
            out.append((call.open_paren, call.close_paren, scope))
    return out


def _innermost_scope_at(spans, pos: int) -> Optional[str]:
    """The scope of the tightest scoped call containing ``pos``."""
    best = None
    best_width = None
    for a, b, scope in spans:
        if a <= pos < b:
            w = b - a
            if best_width is None or w < best_width:
                best, best_width = scope, w
    return best


def _field_refs(expr: str):
    """(field name, position) for every Fields! / Fields("...") reference."""
    for m in _FIELD_BANG_RE.finditer(expr or ""):
        yield m.group(1), m.start()
    for m in _FIELD_IDX_RE.finditer(expr or ""):
        yield m.group(1), m.start()


def _reportitem_refs(expr: str):
    for m in _REPORTITEM_BANG_RE.finditer(expr or ""):
        yield m.group(1), m.start()
    for m in _REPORTITEM_IDX_RE.finditer(expr or ""):
        yield m.group(1), m.start()


def _has_agg(expr: str) -> Optional[str]:
    """The name of the first aggregate function called in ``expr``."""
    for call in _scan_calls(expr or ""):
        if call.func in ALL_AGG_FUNCS:
            return call.func
    return None


# ---------------------------------------------------------------------------
# Report model
# ---------------------------------------------------------------------------

def _local(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


class _Ctx:
    """Where an expression sits: page section, region chain, group chain."""

    __slots__ = ("section", "regions", "groups", "in_group_member")

    def __init__(self, section, regions, groups, in_group_member=False):
        self.section = section            # "Body" | "PageHeader" | "PageFooter"
        self.regions = regions            # [(region name, dataset or None)]
        self.groups = groups              # [group names, outermost first]
        self.in_group_member = in_group_member

    def dataset(self) -> Optional[str]:
        return self.regions[-1][1] if self.regions else None

    def child(self, **kw):
        return _Ctx(kw.get("section", self.section),
                    kw.get("regions", self.regions),
                    kw.get("groups", self.groups),
                    kw.get("in_group_member", self.in_group_member))


class _Model:
    """Everything the rules need to know about one report definition."""

    def __init__(self, root):
        self.root = root
        self.datasets: Dict[str, Dict[str, dict]] = {}   # name -> fields
        self.dataset_order: List[str] = []
        self.region_dataset: Dict[str, Optional[str]] = {}
        self.group_dataset: Dict[str, Optional[str]] = {}
        self.textbox_ctx: Dict[str, _Ctx] = {}
        self.embedded_images: List[str] = []
        self.parameters: List[str] = []
        self.parameter_index: Dict[str, int] = {}
        self.names: Dict[str, List[str]] = {}            # name -> [kinds]
        self._collect()

    # -- collection -------------------------------------------------------
    def _collect(self):
        for el in self.root.iter():
            t = _local(el.tag)
            if t == "DataSet":
                name = (el.get("Name") or "").strip()
                fields = {}
                for f in el.iter():
                    if _local(f.tag) != "Field":
                        continue
                    fname = (f.get("Name") or "").strip()
                    info = {"DataField": None, "TypeName": None}
                    for c in f:
                        ct = _local(c.tag)
                        if ct == "DataField":
                            info["DataField"] = (c.text or "").strip()
                        elif ct == "TypeName":
                            info["TypeName"] = (c.text or "").strip()
                    fields[fname] = info
                self.datasets[name] = fields
                self.dataset_order.append(name)
            elif t == "EmbeddedImage":
                self.embedded_images.append((el.get("Name") or "").strip())
            elif t == "ReportParameter":
                nm = (el.get("Name") or "").strip()
                self.parameter_index[nm] = len(self.parameters)
                self.parameters.append(nm)

        self._sole_dataset = (self.dataset_order[0]
                              if len(self.dataset_order) == 1 else None)
        self._walk(self.root, _Ctx("Body", [], []))

    def _name_kind(self, name, kind):
        if name:
            self.names.setdefault(name, []).append(kind)

    def _walk(self, el, ctx: _Ctx):
        t = _local(el.tag)
        if t in ("PageHeader", "PageFooter"):
            ctx = ctx.child(section=t, regions=[], groups=[])
        elif t == "Body":
            ctx = ctx.child(section="Body", regions=[], groups=[])
        elif t in REGION_TAGS:
            name = (el.get("Name") or "").strip()
            self._name_kind(name, "region")
            own = None
            for c in el:
                if _local(c.tag) == "DataSetName":
                    own = (c.text or "").strip() or None
            # [MS-RDL] Tablix.DataSetName: "If the Tablix has an ancestor,
            # the value of the Tablix.DataSetName element is interpreted as
            # the DataSet.Name for the containing scope." A nested region
            # therefore INHERITS; its own declaration is not its scope.
            if ctx.regions:
                effective = ctx.dataset()
            else:
                effective = own or self._sole_dataset
            self.region_dataset[name] = effective
            ctx = ctx.child(regions=ctx.regions + [(name, effective)])
        elif t == "Group":
            name = (el.get("Name") or "").strip()
            self._name_kind(name, "group")
            self.group_dataset[name] = ctx.dataset()
            ctx = ctx.child(groups=ctx.groups + [name])
        elif t in ("TablixMember", "ChartMember", "DataMember"):
            has_group = any(_local(c.tag) == "Group" for c in el)
            ctx = ctx.child(in_group_member=has_group or ctx.in_group_member)
        elif t in ("Textbox", "Rectangle", "Image", "Subreport", "Line"):
            name = (el.get("Name") or "").strip()
            self._name_kind(name, "item")
            if t == "Textbox":
                self.textbox_ctx[name] = ctx
        elif t == "DataSet":
            self._name_kind((el.get("Name") or "").strip(), "dataset")

        for c in el:
            self._walk(c, ctx)

    # -- queries ----------------------------------------------------------
    def dataset_of_scope(self, scope: str) -> Optional[str]:
        """The dataset a named scope resolves to, or None when unknown."""
        if scope in self.datasets:
            return scope
        if scope in self.region_dataset:
            return self.region_dataset[scope]
        if scope in self.group_dataset:
            return self.group_dataset[scope]
        return None

    def scope_exists(self, scope: str) -> bool:
        return (scope in self.datasets
                or scope in self.region_dataset
                or scope in self.group_dataset)


# ---------------------------------------------------------------------------
# Expression sites
# ---------------------------------------------------------------------------

# Elements whose text is an expression evaluated in a RESTRICTED location.
# [AGGREF] "Restrictions on Built-in Fields, Collections, and Aggregate
# Functions" gives one row per location; the kinds below name those rows.
_KIND_BY_TAG = {
    "GroupExpression": "GroupExpression",
    "FilterExpression": "FilterExpression",
}


def _expression_sites(model: _Model):
    """Yield (element, expression text, ctx, kind, owner name).

    ``kind`` names the [AGGREF] restriction row that governs the site;
    "Body" / "PageHeader" / "PageFooter" mean an ordinary report-item
    property expression in that section.
    """
    def walk(el, ctx: _Ctx, kind: Optional[str], owner: str):
        t = _local(el.tag)
        if t in ("PageHeader", "PageFooter"):
            ctx, kind = ctx.child(section=t, regions=[], groups=[]), None
        elif t == "Body":
            ctx, kind = ctx.child(section="Body", regions=[], groups=[]), None
        elif t in REGION_TAGS:
            name = (el.get("Name") or "").strip()
            owner = name or owner
            ctx = ctx.child(regions=ctx.regions
                            + [(name, model.region_dataset.get(name))])
        elif t == "Group":
            name = (el.get("Name") or "").strip()
            owner = name or owner
            ctx = ctx.child(groups=ctx.groups + [name])
        elif t in ("TablixMember", "ChartMember", "DataMember"):
            has_group = any(_local(c.tag) == "Group" for c in el)
            ctx = ctx.child(in_group_member=has_group or ctx.in_group_member)
        elif t in ("Textbox", "Image", "Subreport", "Rectangle", "Line"):
            owner = (el.get("Name") or "").strip() or owner
        elif t == "SortExpressions":
            kind = "SortExpression"
        elif t == "Filters":
            kind = "FilterExpression"
        elif t == "GroupExpressions":
            kind = "GroupExpression"
        elif t == "ReportParameters":
            kind = "ReportParameter"
        elif t == "QueryParameters":
            kind = "QueryParameter"
        elif t == "Code":
            kind = "Code"
        elif t == "Variables":
            kind = "Variables"

        text = (el.text or "").strip()
        if text.startswith("="):
            site_kind = kind or ctx.section
            if t in _KIND_BY_TAG:
                site_kind = _KIND_BY_TAG[t]
            yield el, text, ctx, site_kind, owner
        for c in el:
            for item in walk(c, ctx, kind, owner):
                yield item

    for item in walk(model.root, _Ctx("Body", [], []), None, "Report"):
        yield item


# ---------------------------------------------------------------------------
# The audit
# ---------------------------------------------------------------------------

def audit_publish_semantics(rdl_xml: str) -> Dict:
    """Rule-engine audit of publish-time semantics. Never raises."""
    violations: List[Dict] = []

    def add(rule, where, message, severity="BLOCKER"):
        violations.append({"rule": rule, "where": where,
                           "message": message, "severity": severity})

    try:
        root = ET.fromstring(rdl_xml)
    except ET.ParseError as exc:
        return {"violations": [{"rule": "publish.xml_parse", "where": "-",
                                "message": f"RDL is not well-formed XML: {exc}",
                                "severity": "BLOCKER"}]}

    model = _Model(root)

    _rule_expression_text(model, add)
    _rule_field_scope(model, add)
    _rule_nested_region_dataset(model, add)
    _rule_aggregate_scope(model, add)
    _rule_lookup(model, add)
    _rule_report_items(model, add)
    _rule_numeric_aggregate(model, add)
    _rule_restricted_locations(model, add)
    _rule_tablix_members(model, add)
    _rule_names(model, add)
    _rule_parameters(model, add)
    _rule_images(model, add)
    _rule_charts(model, add)
    _rule_subreports_and_tablix_shape(model, add)

    return {"violations": violations}


def publish_violations(rdl_xml: str) -> List[str]:
    """Stable "rule@where" ids — the form a ratcheted gate stores."""
    res = audit_publish_semantics(rdl_xml)
    return sorted({f"{v['rule']}@{v['where']}" for v in res["violations"]})


# --- 11. expression text the server rejects --------------------------------

def _rule_expression_text(model, add):
    """Text-level defects that make the server's expression compile fail.

    A lone "=" is an EMPTY expression; an odd number of double quotes leaves
    a string literal unterminated; a raw line break inside a string literal
    is not legal VB (a Visual Basic string constant cannot span lines).
    There is no documented maximum expression LENGTH for RDL — Microsoft
    publishes none, so this rule deliberately asserts no length limit
    rather than inventing one.
    """
    for el, expr, ctx, kind, owner in _expression_sites(model):
        tag = _local(el.tag)
        body = expr[1:].strip()
        if not body:
            add("publish.expression_empty", f"{owner}/{tag}",
                "expression is a lone '=' with no body; the server cannot "
                "compile an empty expression")
            continue
        if expr.count('"') % 2:
            add("publish.expression_unbalanced_quote", f"{owner}/{tag}",
                "expression has an odd number of double quotes — a string "
                "literal is never closed")
            continue
        for m in re.finditer(r'"([^"]*)"', expr):
            if "\n" in m.group(1) or "\r" in m.group(1):
                add("publish.expression_newline_in_literal", f"{owner}/{tag}",
                    "expression has a raw line break inside a string literal; "
                    "a Visual Basic string constant cannot span lines")
                break


# --- 1 + 2. rsFieldInPageSectionExpression / rsFieldReference ---------------

def _rule_field_scope(model, add):
    """Every bare Fields! reference must belong to the scope it sits in.

    [ERRMSG] rsFieldReference: "Report item expressions can only refer to
    fields within the current dataset scope or, if inside an aggregate, the
    specified dataset scope."

    [PGSEC] for page sections: "Expressions include dataset field references
    for reports with exactly one dataset and aggregate function calls that
    include the dataset as a scope." and, for multi-dataset reports, "you
    cannot add fields or data-bound images directly to a header or footer
    ... Do not include a direct reference to fields in a dataset."
    A bare field reference in a page section of a multi-dataset report is
    rsFieldInPageSectionExpression.
    """
    sole = model._sole_dataset
    for el, expr, ctx, kind, owner in _expression_sites(model):
        if kind in ("QueryParameter", "ReportParameter", "Code"):
            continue          # judged by their own rule below
        tag = _local(el.tag)
        spans = _scoped_spans(expr)
        for field, pos in _field_refs(expr):
            scope = _innermost_scope_at(spans, pos)
            if scope is not None:
                ds = model.dataset_of_scope(scope)
                if ds is None:
                    continue          # unknown scope: the scope rule reports it
                if ds in model.datasets and field not in model.datasets[ds]:
                    add("publish.field_not_in_scoped_dataset",
                        f"{owner}/{tag}",
                        f"aggregate scope {scope!r} resolves to dataset "
                        f"{ds!r}, which declares no field {field!r}")
                continue

            # A BARE reference: judged against the position's own scope.
            if ctx.section in ("PageHeader", "PageFooter"):
                if sole is None:
                    add("publish.field_in_page_section",
                        f"{owner}/{tag}",
                        f"bare Fields!{field} in the {ctx.section} of a "
                        f"{len(model.dataset_order)}-dataset report; page "
                        "sections allow a direct field reference only when "
                        "the report has exactly one dataset "
                        "(rsFieldInPageSectionExpression)")
                elif field not in model.datasets.get(sole, {}):
                    add("publish.field_not_in_dataset", f"{owner}/{tag}",
                        f"Fields!{field} is not a field of the report's only "
                        f"dataset {sole!r}")
                continue

            ds = ctx.dataset()
            if ds is None:
                if sole is None:
                    add("publish.field_outside_dataset_scope",
                        f"{owner}/{tag}",
                        f"bare Fields!{field} outside any data region in a "
                        f"{len(model.dataset_order)}-dataset report; there is "
                        "no current dataset scope, so the reference is legal "
                        "only inside an aggregate that names a dataset scope "
                        "(rsFieldReference)")
                    continue
                ds = sole
            if ds in model.datasets and field not in model.datasets[ds]:
                add("publish.field_not_in_dataset", f"{owner}/{tag}",
                    f"Fields!{field} is not a field of dataset {ds!r}, the "
                    "current dataset scope at this position "
                    "(rsFieldReference)")


# --- 3. nested data regions -------------------------------------------------

def _rule_nested_region_dataset(model, add):
    """A nested data region must not declare a different dataset.

    [MS-RDL] Tablix.DataSetName: "If the Tablix has an ancestor, the value
    of the Tablix.DataSetName element is interpreted as the DataSet.Name for
    the containing scope (DataRegion, Group, or Cell)." (The pre-2008
    Table.DataSetName wording is blunter: "This element is ignored for a
    table that is contained within another data region.")

    So a nested region's declaration is NOT its scope. Every expression
    inside it compiles against the CONTAINER's dataset, and the moment one
    of them names a column of the dataset the nested region declared, the
    server refuses the report with rsFieldReference. ReportViewer silently
    ignores the declaration and renders — which is exactly why a local
    render rail cannot see this class.
    """
    def walk(el, outer):
        t = _local(el.tag)
        if t in REGION_TAGS:
            own = None
            for c in el:
                if _local(c.tag) == "DataSetName":
                    own = (c.text or "").strip() or None
            name = (el.get("Name") or "").strip() or "?"
            if outer is not None and own and outer[1] and own != outer[1]:
                add("publish.nested_region_dataset", name,
                    f"data region {name!r} declares DataSetName {own!r} but "
                    f"is nested inside data region {outer[0]!r} bound to "
                    f"{outer[1]!r}; the nested declaration is ignored and "
                    "every expression inside compiles against the container's "
                    "dataset scope")
            effective = outer[1] if outer else (own or model._sole_dataset)
            outer = (name, effective)
        for c in el:
            walk(c, outer)

    walk(model.root, None)


# --- 4. aggregate scope validity -------------------------------------------

def _rule_aggregate_scope(model, add):
    """Every named scope must exist and be usable from where it is written.

    [ERRMSG] rsInvalidAggregateScope: "The scope parameter must be set to a
    string constant that is equal to either the name of a containing group,
    the name of a containing data region, or the name of a dataset."

    [SCOPE] "Named scope  The name of a dataset, a data region, or a data
    region group that is in scope for the expression. For aggregate
    calculations, you can specify a containing scope. You cannot specify a
    contained scope unless the expression is for an aggregate of an
    aggregate."
    """
    for el, expr, ctx, kind, owner in _expression_sites(model):
        tag = _local(el.tag)
        for call in _scan_calls(expr):
            if call.func not in ALL_AGG_FUNCS:
                continue
            scope = _scope_of_call(call, expr)
            if scope is None:
                continue
            if not model.scope_exists(scope):
                add("publish.aggregate_scope_unknown", f"{owner}/{tag}",
                    f"{call.func}(...) names scope {scope!r}, which is not a "
                    "dataset, data region or group in this report "
                    "(rsInvalidAggregateScope)")
                continue
            if scope in model.datasets:
                continue              # a dataset scope is legal anywhere
            if ctx.section in ("PageHeader", "PageFooter"):
                add("publish.aggregate_scope_in_page_section",
                    f"{owner}/{tag}",
                    f"{call.func}(...) names scope {scope!r} in the "
                    f"{ctx.section}; only a DATASET scope is available there "
                    "— a page section is outside every data region")
                continue
            containing = set(ctx.groups) | {n for n, _ in ctx.regions}
            if scope not in containing:
                add("publish.aggregate_scope_not_containing",
                    f"{owner}/{tag}",
                    f"{call.func}(...) names scope {scope!r}, which is not a "
                    "containing group or data region at this position "
                    "(rsInvalidAggregateScope)")


# --- 4b. Lookup family restrictions ----------------------------------------

# [Lookup] "Lookup can't be used as an expression for the following report
# items: Dynamic connection strings for a data source. Calculated fields in
# a dataset. Query parameters in a dataset. Filters in a dataset. Report
# parameters. The Report.Language property."
_LOOKUP_FORBIDDEN_KINDS = {
    "QueryParameter": "a query parameter in a dataset",
    "ReportParameter": "a report parameter",
    "FilterExpression": "a dataset/data-region filter",
}
_LOOKUP_FORBIDDEN_TAGS = {
    "ConnectionString": "a data source connection string",
    "Language": "the Report.Language property",
}


def _rule_lookup(model, add):
    """The documented Lookup/LookupSet/Multilookup restrictions.

    [Lookup] "Only one level of lookup is supported. A source, destination,
    or result expression can't include a reference to a lookup function."

    [Lookup] "Source, destination, and result expressions can't include
    references to report or group variables."

    [AGGREF] Note 1: "Aggregate functions are only allowed inside the Source
    expression of a Lookup function if the Lookup function is not contained
    in an aggregate. Aggregate functions are not allowed inside the
    Destination or Result expressions of a Lookup function."

    [Lookup] the forbidden-location list above.
    """
    for el, expr, ctx, kind, owner in _expression_sites(model):
        tag = _local(el.tag)
        where = f"{owner}/{tag}"
        calls = _scan_calls(expr)
        lookups = [c for c in calls if c.func in LOOKUP_FUNCS]
        if not lookups:
            continue

        forbidden = _LOOKUP_FORBIDDEN_KINDS.get(kind) \
            or _LOOKUP_FORBIDDEN_TAGS.get(tag)
        if forbidden:
            add("publish.lookup_in_forbidden_location", where,
                f"a Lookup-family call appears in {forbidden}; Lookup cannot "
                "be used as the expression there")

        for call in lookups:
            for idx, (a, b) in enumerate(call.args[:3]):
                arg = expr[a:b]
                inner = [c for c in calls
                         if a <= c.start < b and c.func in LOOKUP_FUNCS]
                if inner:
                    add("publish.lookup_nested", where,
                        f"argument {idx + 1} of {call.func}(...) contains a "
                        f"nested {inner[0].func}(...); only one level of "
                        "lookup is supported — a source, destination or "
                        "result expression cannot reference a lookup function")
                if "Variables!" in arg:
                    add("publish.lookup_variable_argument", where,
                        f"argument {idx + 1} of {call.func}(...) references a "
                        "report or group variable; Lookup source, destination "
                        "and result expressions cannot")
                if idx in (1, 2):
                    agg = next((c.func for c in calls
                                if a <= c.start < b
                                and c.func in ALL_AGG_FUNCS
                                and c.func not in LOOKUP_FUNCS), None)
                    if agg:
                        name = "destination" if idx == 1 else "result"
                        add("publish.aggregate_in_lookup_argument", where,
                            f"the {name} expression of {call.func}(...) calls "
                            f"{agg}(...); aggregate functions are not allowed "
                            "inside the destination or result expression of a "
                            "lookup")


# --- 5. ReportItems! --------------------------------------------------------

def _rule_report_items(model, add):
    """ReportItems references must name a real textbox in a reachable scope.

    [RPTIT] "The scope for a reference to the ReportItems collection is the
    current scope or any point higher than the current scope. For example, a
    text box in a row that is in a parent group must not contain an
    expression that refers to the name of a text box in a child group row."

    [AGGREF] restriction table: ReportItems is allowed in the Body only for
    "items in the current scope or a containing scope", and in a Page Header
    / Page Footer "At most one" per expression — which [RPTIT] states as "a
    text box in the page header can only refer to the ReportItems built-in
    collection once in an expression".
    """
    for el, expr, ctx, kind, owner in _expression_sites(model):
        tag = _local(el.tag)
        refs = list(_reportitem_refs(expr))
        if not refs:
            continue
        if ctx.section in ("PageHeader", "PageFooter") and len(refs) > 1:
            add("publish.report_items_multiple_in_page_section",
                f"{owner}/{tag}",
                f"{len(refs)} ReportItems references in one {ctx.section} "
                "expression; a page-section expression may refer to the "
                "ReportItems collection only once")
        for name, _pos in refs:
            if name not in model.textbox_ctx:
                add("publish.report_item_undefined", f"{owner}/{tag}",
                    f"ReportItems!{name} names no textbox in this report")
                continue
            if ctx.section != "Body":
                continue
            target = model.textbox_ctx[name]
            if target.section != "Body":
                add("publish.report_item_cross_section", f"{owner}/{tag}",
                    f"ReportItems!{name} refers to a textbox in the "
                    f"{target.section}; the body cannot see page-section items")
                continue
            here_groups = list(ctx.groups)
            there_groups = list(target.groups)
            if there_groups[:len(here_groups)] != here_groups \
                    or len(there_groups) > len(here_groups):
                add("publish.report_item_out_of_scope", f"{owner}/{tag}",
                    f"ReportItems!{name} lives in group scope "
                    f"{there_groups or ['<body>']}, which is not the current "
                    f"scope {here_groups or ['<body>']} nor a containing one")


# --- 6. rsAggregateOfNonNumericData ----------------------------------------

def _rule_numeric_aggregate(model, add):
    """A numeric aggregate over a declared-textual field is rejected.

    [ERRMSG] rsAggregateOfNonNumericData: "... uses a numeric aggregate
    function on data that is not numeric. Numeric aggregate functions (Sum,
    Avg, StDev, Var, StDevP, and VarP) can only aggregate numeric data."
    Count / CountDistinct / CountRows are not numeric aggregates and are
    always fine.

    Only a call whose operand is EXACTLY one field reference is judged: a
    computed operand (a CDbl(), an IIf that yields a number) is not textual
    just because a string-typed column appears somewhere inside it.
    """
    for el, expr, ctx, kind, owner in _expression_sites(model):
        tag = _local(el.tag)
        for call in _scan_calls(expr):
            if call.func not in NUMERIC_AGGS or not call.args:
                continue
            a, b = call.args[0]
            operand = expr[a:b].strip()
            m = re.fullmatch(r"Fields!([A-Za-z_][A-Za-z0-9_]*)\.Value",
                             operand)
            if not m:
                continue
            field = m.group(1)
            scope = _scope_of_call(call, expr)
            ds = (model.dataset_of_scope(scope) if scope else None) \
                or ctx.dataset() or model._sole_dataset
            info = (model.datasets.get(ds) or {}).get(field)
            if not info:
                continue
            tname = (info.get("TypeName") or "")
            if tname in ("System.String", "System.Char"):
                add("publish.aggregate_of_non_numeric", f"{owner}/{tag}",
                    f"{call.func}(Fields!{field}.Value) aggregates a field "
                    f"declared {tname} in dataset {ds!r} "
                    "(rsAggregateOfNonNumericData)")


# --- 7. restricted expression locations ------------------------------------

def _rule_restricted_locations(model, add):
    """Group / sort / filter / parameter / code expressions are restricted.

    [AGGREF] "Restrictions on Built-in Fields, Collections, and Aggregate
    Functions" — the ReportItems column reads "No" for Group Expression,
    Sort Expression, Filter Expression, Report Parameter, Field, Query
    Parameter and Code; the PageNumber/TotalPages column reads "No" for the
    Body and for all of those locations; the Fields column reads "No" for
    Report Parameter, Query Parameter and Code.

    [MS-RDL] GroupExpressions.GroupExpression: "The value of the
    GroupExpressions.GroupExpression element MUST NOT include any aggregate
    functions other than the RowNumber aggregate function. If the RowNumber
    aggregate function is used, it MUST reference the immediately containing
    scope."
    """
    for el, expr, ctx, kind, owner in _expression_sites(model):
        tag = _local(el.tag)
        where = f"{owner}/{tag}"

        if kind == "GroupExpression":
            for call in _scan_calls(expr):
                if call.func in ALL_AGG_FUNCS and call.func != "RowNumber":
                    add("publish.aggregate_in_group_expression", where,
                        f"group expression calls {call.func}(...); a group "
                        "expression must not include any aggregate function "
                        "other than RowNumber")
                    break

        if kind in ("GroupExpression", "SortExpression", "FilterExpression",
                    "ReportParameter", "QueryParameter", "Code"):
            if next(_reportitem_refs(expr), None):
                add("publish.report_items_in_restricted_location", where,
                    f"a {kind} may not reference the ReportItems collection")

        if kind in ("ReportParameter", "QueryParameter", "Code"):
            first = next(_field_refs(expr), None)
            if first:
                add("publish.fields_in_restricted_location", where,
                    f"a {kind} expression may not reference the Fields "
                    f"collection (Fields!{first[0]})")

        if _GLOBAL_PAGE_RE.search(expr) and (
                kind not in (None, "PageHeader", "PageFooter")
                or ctx.section == "Body"):
            add("publish.page_number_outside_page_section", where,
                "Globals!PageNumber / Globals!TotalPages are available only "
                "in a page header or page footer")


# --- 8. TablixMember paging properties -------------------------------------

def _rule_tablix_members(model, add):
    """RepeatOnNewPage / KeepWithGroup / FixedData consistency.

    [MS-RDL] TablixMember.RepeatOnNewPage: "The value of the
    TablixMember.RepeatOnNewPage element for all sibling TablixMember
    elements between the parent of the TablixMember.RepeatOnNewPage element
    and the associated dynamic member MUST be the same." and "If the parent
    element is a tablix column member, the value of this element MUST be
    false." The server reports a mismatch as rsInvalidRepeatOnNewPage: "The
    tablix 'X' has an invalid TablixMember. The TablixMember must have the
    same value set for the RepeatOnNewPage property as those following or
    preceding the dynamic TablixMember."

    [MS-RDL] TablixMember.KeepWithGroup: "The value of the
    TablixMember.KeepWithGroup element MUST be 'None' if the parent element
    is a dynamic member or has a dynamic member descendant. The value of
    this element MUST be 'None' if the parent element is a tablix column
    member." and, for "Before"/"After", every sibling between it and the
    dynamic member "MUST be" the same value.
    """
    def child_text(el, name, default=None):
        for c in el:
            if _local(c.tag) == name:
                return (c.text or "").strip()
        return default

    def is_dynamic(el):
        return any(_local(c.tag) == "Group" for c in el)

    def has_dynamic_descendant(el):
        for sub in el.iter():
            if sub is el:
                continue
            if _local(sub.tag) == "Group":
                return True
        return False

    for tablix in model.root.iter():
        if _local(tablix.tag) not in REGION_TAGS:
            continue
        tname = (tablix.get("Name") or "?").strip()
        for hier in tablix:
            htag = _local(hier.tag)
            if htag not in ("TablixRowHierarchy", "TablixColumnHierarchy"):
                continue
            is_column = htag == "TablixColumnHierarchy"
            _member_runs(hier, tname, is_column, add,
                         child_text, is_dynamic, has_dynamic_descendant)


def _member_runs(parent, tname, is_column, add, child_text, is_dynamic,
                 has_dynamic_descendant):
    """Check one TablixMembers list, then recurse into each member."""
    for members in parent:
        if _local(members.tag) != "TablixMembers":
            continue
        kids = [m for m in members if _local(m.tag) == "TablixMember"]

        # Column members may not repeat and may not keep-with-group.
        for m in kids:
            if is_column:
                if (child_text(m, "RepeatOnNewPage") or "").lower() == "true":
                    add("publish.repeat_on_new_page_on_column_member", tname,
                        "a tablix COLUMN member sets RepeatOnNewPage=true; "
                        "for a column member the value must be false")
                kwg = child_text(m, "KeepWithGroup")
                if kwg and kwg != "None":
                    add("publish.keep_with_group_on_column_member", tname,
                        f"a tablix COLUMN member sets KeepWithGroup={kwg!r}; "
                        "for a column member the value must be 'None'")
            if is_dynamic(m) or has_dynamic_descendant(m):
                kwg = child_text(m, "KeepWithGroup")
                if kwg and kwg != "None":
                    add("publish.keep_with_group_on_dynamic_member", tname,
                        f"TablixMember {m.get('Name') or '?'} is (or contains) "
                        f"a dynamic member and sets KeepWithGroup={kwg!r}; it "
                        "must be 'None'")

        # RepeatOnNewPage must agree across each run of STATIC members that
        # sits between the parent and a dynamic sibling.
        run: List = []
        for m in kids + [None]:
            if m is not None and not is_dynamic(m):
                run.append(m)
                continue
            if len(run) > 1:
                vals = {(child_text(s, "RepeatOnNewPage") or "false").lower()
                        for s in run}
                if len(vals) > 1 and m is not None:
                    add("publish.repeat_on_new_page_inconsistent", tname,
                        f"a run of {len(run)} static TablixMembers adjacent to "
                        f"a dynamic member sets RepeatOnNewPage to {sorted(vals)}"
                        "; every static member in the run must set the same "
                        "value (rsInvalidRepeatOnNewPage)")
            run = []

        for m in kids:
            _member_runs(m, tname, is_column, add, child_text, is_dynamic,
                         has_dynamic_descendant)


# --- 9. names ---------------------------------------------------------------

def _rule_names(model, add):
    """Name uniqueness and shape.

    [MS-RDL] DataSet.Name: "The value of this attribute MUST be a
    case-sensitive CLS-compliant identifier ... The value of the
    DataSet.Name attribute MUST be unique among all datasets, data regions,
    and groups in the report." The same CLS-identifier + uniqueness wording
    appears on DataSource.Name, Variable.Name and the report-item names; the
    server reports a violation as "... names must be CLS-compliant
    identifiers."
    """
    for name, kinds in sorted(model.names.items()):
        if not name:
            continue
        if not _CLS_IDENT_RE.match(name):
            add("publish.name_not_cls_identifier", name,
                f"{'/'.join(sorted(set(kinds)))} name {name!r} is not a "
                "CLS-compliant identifier (letter or underscore, then "
                "letters, digits or underscores)")
        if len(kinds) > 1:
            add("publish.duplicate_name", name,
                f"name {name!r} is used {len(kinds)} times "
                f"({', '.join(sorted(set(kinds)))}); dataset, data-region, "
                "group and report-item names share one namespace and must be "
                "unique")

    for ds, fields in sorted(model.datasets.items()):
        seen_df: Dict[str, str] = {}
        for fname, info in sorted(fields.items()):
            df = info.get("DataField")
            if not df:
                continue
            if df in seen_df:
                add("publish.duplicate_datafield", f"{ds}/{df}",
                    f"dataset {ds!r} maps two fields ({seen_df[df]!r} and "
                    f"{fname!r}) to the same DataField {df!r}")
            else:
                seen_df[df] = fname


# --- 10. parameters ---------------------------------------------------------

def _rule_parameters(model, add):
    """Parameter wiring the server validates at publish.

    [AGGREF] restriction table, Report Parameter row: the Parameters column
    reads "Only parameters earlier in the list", i.e. a parameter's default
    or valid-values expression may reference only parameters declared BEFORE
    it. A DataSetReference must name a dataset that exists in the report
    ([MS-RDL] DataSetReference.DataSetName).
    """
    for el in model.root.iter():
        if _local(el.tag) != "ReportParameter":
            continue
        name = (el.get("Name") or "").strip()
        my_index = model.parameter_index.get(name, 0)
        for sub in el.iter():
            if _local(sub.tag) == "DataSetName":
                ref = (sub.text or "").strip()
                if ref and ref not in model.datasets:
                    add("publish.parameter_dataset_reference_unknown", name,
                        f"parameter {name!r} draws from dataset {ref!r}, "
                        "which is not declared in this report")
            text = (sub.text or "").strip()
            if not text.startswith("="):
                continue
            for m in _PARAM_RE.finditer(text):
                other = m.group(1)
                if other not in model.parameter_index:
                    add("publish.parameter_reference_unknown", name,
                        f"parameter {name!r} references Parameters!{other}, "
                        "which is not declared in this report")
                elif model.parameter_index[other] >= my_index:
                    add("publish.parameter_forward_reference", name,
                        f"parameter {name!r} references Parameters!{other}, "
                        "which is declared later in the parameter list; a "
                        "parameter may reference only parameters earlier in "
                        "the list")


# --- 12. images -------------------------------------------------------------

_REPORT_MIME_TYPES = frozenset({
    "image/bmp", "image/jpeg", "image/gif", "image/png", "image/x-png",
})


def _rule_images(model, add):
    """[MS-RDL] Image.Value / Image.MIMEType.

    Image.Value: "If the peer Image.Source element is set to 'Embedded', the
    value of the Image.Value element MUST be a String constant or an
    expression that evaluates to the name of an EmbeddedImage in the
    report." / "...set to 'Database', the value ... MUST be a String
    constant or an expression that evaluates to the binary data for an
    image." / "...set to 'External', ... MUST be ... the location of an
    Image."
    Image.MIMEType: "if Image.Source is set to 'Database', the Image.MIMEType
    element MUST be specified", and its value MUST be a ReportMIMEType.
    """
    for el in model.root.iter():
        if _local(el.tag) not in ("Image", "BackgroundImage",
                                 "MapMarkerImage", "CapImage", "FrameImage"):
            continue
        name = (el.get("Name") or _local(el.tag)).strip()
        source = value = mime = None
        for c in el:
            t = _local(c.tag)
            if t == "Source":
                source = (c.text or "").strip()
            elif t == "Value":
                value = (c.text or "").strip()
            elif t == "MIMEType":
                mime = (c.text or "").strip()
        if source is None:
            continue
        if not value:
            add("publish.image_no_value", name,
                f"image source is {source!r} but <Value> is empty")
            continue
        if source == "Embedded":
            if not value.startswith("=") and value not in model.embedded_images:
                add("publish.image_embedded_undeclared", name,
                    f"image names embedded image {value!r}, which is not "
                    "declared in <EmbeddedImages>")
        elif source == "Database":
            if not value.startswith("="):
                add("publish.image_database_not_expression", name,
                    "image source is 'Database' but <Value> is a literal; it "
                    "must be an expression that evaluates to binary image data")
            if not mime:
                add("publish.image_database_no_mimetype", name,
                    "image source is 'Database' but no <MIMEType> is "
                    "specified; MIMEType is required for a database image")
        if mime and not mime.startswith("=") and mime not in _REPORT_MIME_TYPES:
            add("publish.image_mimetype_invalid", name,
                f"MIMEType {mime!r} is not one of the report MIME types "
                f"({', '.join(sorted(_REPORT_MIME_TYPES))})")


# --- 13. charts -------------------------------------------------------------

def _rule_charts(model, add):
    """[MS-RDL] Chart structure the server compiles.

    ChartSeries.ChartDataPoints "MUST be specified" for a series;
    ChartMember.Group requires GroupExpressions for a dynamic member. A
    series or category collection that carries a Group with no group
    expression cannot be compiled.
    """
    for chart in model.root.iter():
        if _local(chart.tag) != "Chart":
            continue
        cname = (chart.get("Name") or "?").strip()
        for series in chart.iter():
            if _local(series.tag) != "ChartSeries":
                continue
            pts = [c for c in series if _local(c.tag) == "ChartDataPoints"]
            if not pts or not any(len(list(p)) for p in pts):
                add("publish.chart_series_without_data_points",
                    f"{cname}/{series.get('Name') or '?'}",
                    "chart series declares no ChartDataPoints")
        for member in chart.iter():
            if _local(member.tag) != "ChartMember":
                continue
            grp = None
            for c in member:
                if _local(c.tag) == "Group":
                    grp = c
            if grp is None:
                continue
            exprs = [g for g in grp.iter()
                     if _local(g.tag) == "GroupExpression"
                     and (g.text or "").strip()]
            if not exprs:
                add("publish.chart_group_without_expression",
                    f"{cname}/{grp.get('Name') or '?'}",
                    "chart category/series group declares no non-empty "
                    "GroupExpression")


# --- 14. subreports + tablix grid shape ------------------------------------

def _rule_subreports_and_tablix_shape(model, add):
    """Subreport.ReportName and the tablix grid.

    [MS-RDL] Subreport.ReportName: the element "MUST be specified" and names
    the report to embed — an empty or expression-valued name cannot be
    resolved by the server's catalogue lookup.

    The tablix grid is rectangular: the number of TablixCell elements in
    each TablixRow must equal the number of TablixColumn elements
    (rsInvalidTablixCellCount / "The tablix has an invalid structure").
    """
    for sub in model.root.iter():
        if _local(sub.tag) != "Subreport":
            continue
        name = (sub.get("Name") or "?").strip()
        rn = None
        for c in sub:
            if _local(c.tag) == "ReportName":
                rn = (c.text or "").strip()
        if not rn:
            add("publish.subreport_no_report_name", name,
                "subreport declares no <ReportName>")
        elif rn.startswith("="):
            add("publish.subreport_report_name_expression", name,
                f"subreport ReportName is an expression ({rn[:40]!r}); the "
                "server resolves a subreport by a literal catalogue path")

    for tablix in model.root.iter():
        if _local(tablix.tag) != "Tablix":
            continue
        tname = (tablix.get("Name") or "?").strip()
        cols = 0
        rows = []
        for body in tablix:
            if _local(body.tag) != "TablixBody":
                continue
            for c in body:
                t = _local(c.tag)
                if t == "TablixColumns":
                    cols = sum(1 for x in c if _local(x.tag) == "TablixColumn")
                elif t == "TablixRows":
                    for r in c:
                        if _local(r.tag) != "TablixRow":
                            continue
                        cells = 0
                        for rc in r:
                            if _local(rc.tag) == "TablixCells":
                                cells = sum(1 for x in rc
                                            if _local(x.tag) == "TablixCell")
                        rows.append(cells)
        if cols and rows:
            bad = sorted({n for n in rows if n != cols})
            if bad:
                add("publish.tablix_cell_count_mismatch", tname,
                    f"tablix declares {cols} TablixColumn(s) but has row(s) "
                    f"with {bad} TablixCell(s); every row must have exactly "
                    "one cell per column")
