"""
Pre-flight RDL audit — answers the question:
"If I open this .rdl in SSRS Report Builder right now, will it work?"

Stricter than the structural rdl_check because it cross-references the
generated SQL, parameter wiring, and data-bound report items.

Severities:
  BLOCKER  — Report Builder will refuse to open it, OR it'll throw at runtime
             before showing any data
  RED      — opens, but SOME query/section will fail at runtime
  AMBER    — works but has rough edges (cosmetic, missing widths, etc.)

The verdict is the worst severity found. No issues = "ready to deploy".
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import List, Dict, Tuple


RDL_NS_2008 = "http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition"
RDL_NS_2010 = "http://schemas.microsoft.com/sqlserver/reporting/2010/01/reportdefinition"
RDL_NS_2016 = "http://schemas.microsoft.com/sqlserver/reporting/2016/01/reportdefinition"


def _strip_sql_comments(sql: str) -> str:
    """Remove /* ... */ block comments and -- line comments. Used so we don't
    flag Oracle constructs that were intentionally commented out by the
    translator (e.g. lexical refs)."""
    if not sql:
        return ""
    # Block comments
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    # Line comments
    sql = re.sub(r"--[^\n]*", "", sql)
    return sql


def preflight_audit(rdl_xml: str) -> Dict:
    """Run the audit on an RDL XML string. Returns a structured result."""
    issues: List[Tuple[str, str, str]] = []  # (severity, rule, msg)
    stats: Dict[str, int] = {}

    # 1) Parse
    try:
        tree = ET.fromstring(rdl_xml)
    except ET.ParseError as e:
        return {
            "verdict": "BLOCKER",
            "issues": [{"severity": "BLOCKER", "rule": "xml.parse",
                        "message": f"RDL is not well-formed XML: {e}"}],
            "stats": {},
        }

    # Detect namespace and use it for all lookups
    root_tag_full = tree.tag
    if root_tag_full.startswith("{"):
        ns = root_tag_full.split("}", 1)[0][1:]
    else:
        ns = ""
    NS = "{" + ns + "}" if ns else ""

    def find_all(elem, tag):
        return list(elem.iter(NS + tag)) if NS else list(elem.iter(tag))

    def stripns(t):
        return t.split("}", 1)[1] if "}" in t else t

    # 2) Root must be <Report>
    if stripns(root_tag_full) != "Report":
        issues.append(("BLOCKER", "rdl.root",
                       f"Root element is <{stripns(root_tag_full)}>, expected <Report>"))

    # 3) Namespace must be a known RDL namespace
    if ns not in (RDL_NS_2008, RDL_NS_2010, RDL_NS_2016):
        issues.append(("BLOCKER", "rdl.namespace",
                       f"Unknown RDL namespace: {ns!r}"))


    # 3a) Schema-deprecated elements. SSRS 2008+ folded <List>, <Table>, and
    # <Matrix> into a unified <Tablix>. Emitting any of these under the
    # 2008/2010/2016 namespace produces a deserialization error at upload:
    #   "ReportItems has invalid child element 'List'..."
    # This catches the entire class of bug at convert-time so the user never
    # gets the cryptic error from the report server.
    DEPRECATED_2008 = {
        "List":          "removed - use <Tablix> with one column and a single detail row group",
        "Table":         "removed - use <Tablix> with TablixColumnHierarchy/TablixRowHierarchy",
        "Matrix":        "removed - use <Tablix> with column AND row hierarchy groups",
        "RowGrouping":   "removed - use <TablixRowHierarchy>",
        "ColumnGrouping": "removed - use <TablixColumnHierarchy>",
    }
    found_deprecated: Dict[str, int] = {}
    for el in tree.iter():
        local = stripns(el.tag)
        if local in DEPRECATED_2008:
            found_deprecated[local] = found_deprecated.get(local, 0) + 1
    for elem_name, count in found_deprecated.items():
        issues.append((
            "BLOCKER",
            f"rdl.deprecated_element.{elem_name.lower()}",
            f"<{elem_name}> appears {count}x - {DEPRECATED_2008[elem_name]}. "
            f"Upload to SSRS will fail with 'invalid child element {elem_name!r}'.",
        ))

    # 3b) Container child-element validity. The SSRS 2008/01 schema enforces
    # strict allowed-child sets on certain layout containers; emitting a
    # disallowed direct child triggers cryptic deserialization errors at
    # upload time, e.g.:
    #   "CellContents has invalid child element 'Style' in namespace ..."
    # Catch the entire class of bug at convert-time so the user never sees
    # the report-server error.
    ALLOWED_CHILDREN = {
        "CellContents": {
            "ColSpan", "RowSpan",
            "Line", "Rectangle", "Textbox", "Image", "Subreport",
            "Chart", "GaugePanel", "CustomReportItem", "Tablix",
        },
        "ReportItems": {
            "Line", "Rectangle", "Textbox", "Image", "Subreport",
            "Chart", "GaugePanel", "CustomReportItem", "Tablix",
        },
    }
    for container_tag, allowed in ALLOWED_CHILDREN.items():
        bad_children: Dict[str, int] = {}
        for el in find_all(tree, container_tag):
            for child in list(el):
                local = stripns(child.tag)
                if local not in allowed:
                    bad_children[local] = bad_children.get(local, 0) + 1
        for child_name, count in bad_children.items():
            allowed_list = ", ".join(sorted(allowed))
            rule_suffix = (
                "invalid_cellcontents_child"
                if container_tag == "CellContents"
                else "invalid_reportitems_child"
            )
            issues.append((
                "BLOCKER",
                f"rdl.{rule_suffix}",
                f"<{container_tag}> has invalid direct child <{child_name}> "
                f"({count}x). Allowed direct children: {allowed_list}. "
                f"Upload to SSRS will fail with 'invalid child element "
                f"{child_name!r}'.",
            ))

    # 4) DataSources
    ds_list = find_all(tree, "DataSource")
    stats["datasources"] = len(ds_list)
    if not ds_list:
        issues.append(("BLOCKER", "rdl.no_datasource",
                       "No <DataSource> — Report Builder will refuse to render"))

    # 5) DataSets — analyze each CommandText
    datasets = find_all(tree, "DataSet")
    stats["datasets"] = len(datasets)
    if not datasets:
        issues.append(("BLOCKER", "rdl.no_dataset",
                       "No <DataSet> — no data will load"))
    declared_ds_names = set()

    # Oracle leftovers we can be CERTAIN will fail in T-SQL
    oracle_constructs = [
        (r"\bDECODE\s*\(",     "DECODE — T-SQL has no DECODE; should be CASE WHEN"),
        (r"\bNVL\s*\(",        "NVL — T-SQL uses ISNULL or COALESCE"),
        (r"\bNVL2\s*\(",       "NVL2 — T-SQL has no NVL2"),
        (r"\bTO_CHAR\s*\(",    "TO_CHAR — T-SQL uses FORMAT() or CONVERT()"),
        (r"\bTO_DATE\s*\(",    "TO_DATE — T-SQL uses TRY_CONVERT or CAST"),
        (r"\bSYSDATE\b",       "SYSDATE — T-SQL uses GETDATE() or SYSDATETIME()"),
        (r"\bSUBSTR\s*\(",     "SUBSTR — T-SQL uses SUBSTRING"),
        (r"\bINSTR\s*\(",      "INSTR — T-SQL uses CHARINDEX"),
        (r"\bROWNUM\b",        "ROWNUM — T-SQL uses TOP n / OFFSET FETCH / ROW_NUMBER()"),
        (r"\bMINUS\b(?=\s)",   "MINUS — T-SQL uses EXCEPT"),
        (r"\bCONNECT\s+BY\b",  "CONNECT BY — T-SQL uses recursive CTE"),
        (r"\(\+\)",            "(+) outer-join syntax — T-SQL uses LEFT/RIGHT JOIN"),
        (r"\bDUAL\b",          "DUAL — remove (T-SQL has no DUAL)"),
    ]

    for ds in datasets:
        name = ds.get("Name", "")
        declared_ds_names.add(name)

        cmd = ds.find(NS + "Query/" + NS + "CommandText") if NS else ds.find("Query/CommandText")
        if cmd is None or not (cmd.text or "").strip():
            issues.append(("BLOCKER", f"dataset.{name}.empty_command",
                           f"DataSet {name!r} has no CommandText — will fail at runtime"))
            continue

        sql_raw = cmd.text or ""
        sql_stripped = _strip_sql_comments(sql_raw)

        for pat, msg in oracle_constructs:
            if re.search(pat, sql_stripped, flags=re.IGNORECASE):
                issues.append(("RED", f"dataset.{name}.oracle_leftover",
                               f"{name}: {msg}"))

        # Lexical refs (Oracle Reports only). Already stripped of /*comments*/.
        for m in re.finditer(r"&([A-Z_][A-Z0-9_]+)", sql_stripped):
            issues.append(("RED", f"dataset.{name}.lexical_ref",
                           f"{name}: still uses Oracle Reports lexical reference &{m.group(1)} — not valid in SSRS"))

        # Oracle bind ":NAME" left in (T-SQL uses @NAME)
        for m in re.finditer(r"(?<![:\w]):([A-Z_][A-Z0-9_]+)", sql_stripped):
            issues.append(("RED", f"dataset.{name}.bind_var",
                           f"{name}: Oracle bind :{m.group(1)} — should be @{m.group(1)}"))

        # Identifiers > 128 chars (SQL Server limit)
        for m in re.finditer(r"[A-Za-z_][A-Za-z0-9_]{129,}", sql_stripped):
            issues.append(("AMBER", f"dataset.{name}.long_ident",
                           f"{name}: identifier > 128 chars — SQL Server limit"))

    # 6) ReportParameters
    rps = find_all(tree, "ReportParameter")
    stats["report_parameters"] = len(rps)
    declared_params = {rp.get("Name", "") for rp in rps}

    # Cross-check QueryParameters against ReportParameters
    for ds in datasets:
        name = ds.get("Name", "")
        for qp in find_all(ds, "QueryParameter"):
            qname = qp.get("Name", "").lstrip("@")
            if qname and qname not in declared_params:
                issues.append(("RED", f"dataset.{name}.qp_undeclared",
                               f"DataSet {name}: QueryParameter @{qname} has no matching ReportParameter"))

    # 7) Data-bound report items: Tablix, List, Subreport, Chart, Map, Gauge
    tablices    = find_all(tree, "Tablix")
    lists       = find_all(tree, "List")
    subreports  = find_all(tree, "Subreport")
    charts      = find_all(tree, "Chart")
    bound_items = len(tablices) + len(lists) + len(subreports) + len(charts)
    stats["tablices"] = len(tablices)
    stats["lists"] = len(lists)
    stats["subreports"] = len(subreports)
    stats["charts"] = len(charts)
    stats["bound_items_total"] = bound_items

    if bound_items == 0:
        issues.append(("RED", "rdl.no_bound_items",
                       "No <Tablix>, <List>, <Subreport>, or <Chart> — datasets will load but never display"))

    # 8) Every data-bound item should reference an existing dataset
    for tag in ("Tablix", "List", "Chart"):
        for el in find_all(tree, tag):
            elname = el.get("Name", "?")
            dsn = el.find(NS + "DataSetName") if NS else el.find("DataSetName")
            if dsn is None or not (dsn.text or "").strip():
                # Lists may inherit from outer scope, so this is just AMBER
                issues.append(("AMBER", f"{tag.lower()}.{elname}.no_dataset",
                               f"{tag} {elname} has no DataSetName"))
            elif dsn.text not in declared_ds_names:
                issues.append(("BLOCKER", f"{tag.lower()}.{elname}.bad_dataset",
                               f"{tag} {elname} binds to DataSet {dsn.text!r} which doesn't exist"))

    # 9) Body / Page presence
    if not find_all(tree, "Body"):
        issues.append(("BLOCKER", "rdl.no_body", "Missing <Body>"))
    if not find_all(tree, "Page"):
        issues.append(("BLOCKER", "rdl.no_page", "Missing <Page> — page sizing required"))


    # 9b) Enumerated-value validity. Several RDL elements have strict enum
    # value sets per the 2008/01 schema; emitting an out-of-schema value
    # causes deserialization failures like:
    #   "Start is not a valid value. Line X, position Y."
    # Catch them all here so the user never sees the cryptic upload error.
    ENUM_RULES = {
        "TextAlign":      {"Default", "Left", "Center", "Right", "General"},
        "VerticalAlign":  {"Default", "Top", "Middle", "Bottom"},
        "TextDecoration": {"Default", "Underline", "Overline", "LineThrough", "None"},
        "FontStyle":      {"Default", "Normal", "Italic"},
        "Direction":      {"LTR", "RTL"},
        "WritingMode":    {"Horizontal", "Vertical", "Rotate270"},
        "BreakLocation":  {"Start", "End", "StartAndEnd", "Between", "EndOfGroup"},
        "KeepWithGroup":  {"None", "Before", "After"},
    }
    enum_re = re.compile(
        r"<(" + "|".join(ENUM_RULES.keys()) + r")>([^<]+)</\1>"
    )
    bad_values: Dict[str, int] = {}
    for tag, val in enum_re.findall(rdl_xml):
        v = val.strip()
        if v and v not in ENUM_RULES[tag]:
            key = f"{tag}={v}"
            bad_values[key] = bad_values.get(key, 0) + 1
    for key, count in bad_values.items():
        tag, val = key.split("=", 1)
        valid = ", ".join(sorted(ENUM_RULES[tag]))
        issues.append((
            "BLOCKER",
            f"rdl.bad_enum.{tag.lower()}",
            f"<{tag}>{val}</{tag}> appears {count}x - not a valid value. "
            f"Allowed: {valid}. Upload to SSRS will fail with "
            f"'{val} is not a valid value'.",
        ))

    # 3c) Expression scope validity. Every textbox <Value> expression that
    # references =Fields!X.Value must live inside a data region (Tablix) and
    # X must be in that Tablix's bound DataSet <Fields> list. References to
    # parameter values must use =Parameters!P_X.Value. This catches the
    # exact upload error:
    #   "The Value expression for the text box 'Tb_X' refers to the field
    #    'Y'. Report item expressions can only refer to fields within the
    #    current dataset scope ..."
    fields_re = re.compile(r"=\s*Fields!([A-Za-z_][A-Za-z0-9_]*)\.Value", re.IGNORECASE)

    # Build dataset -> set(field names) map for scope checks.
    ds_fields: Dict[str, set] = {}
    for ds in datasets:
        ds_name = ds.get("Name", "")
        names: set = set()
        for f in find_all(ds, "Field"):
            n = f.get("Name", "")
            if n:
                names.add(n)
        ds_fields[ds_name] = names

    # Walk each Tablix and collect its DataSetName + the set of textbox
    # names directly under that tablix's CellContents (so we know which
    # textboxes are "in scope").
    tablix_scope: Dict[str, str] = {}  # textbox name -> dataset name
    for tx in find_all(tree, "Tablix"):
        dsn_el = tx.find(NS + "DataSetName") if NS else tx.find("DataSetName")
        dsn = (dsn_el.text or "").strip() if dsn_el is not None else ""
        if not dsn:
            continue
        for tb in find_all(tx, "Textbox"):
            tb_name = tb.get("Name", "")
            if tb_name:
                tablix_scope[tb_name] = dsn

    # Now check every textbox in the tree.
    for tb in find_all(tree, "Textbox"):
        tb_name = tb.get("Name", "?")
        # Read every <Value> text inside this textbox.
        for v in find_all(tb, "Value"):
            txt = (v.text or "")
            if "Fields!" not in txt:
                continue
            for m in fields_re.finditer(txt):
                field_name = m.group(1)
                dsn = tablix_scope.get(tb_name, "")
                if not dsn:
                    issues.append((
                        "BLOCKER",
                        f"rdl.expr_scope.outside_dataset.{tb_name}",
                        f"Textbox {tb_name!r} uses =Fields!{field_name}.Value "
                        f"but is not inside any data region (no dataset scope). "
                        f"Maybe meant =Parameters!P_{field_name}.Value.",
                    ))
                    continue
                allowed = ds_fields.get(dsn, set())
                if field_name not in allowed:
                    issues.append((
                        "BLOCKER",
                        f"rdl.expr_scope.field_missing.{tb_name}",
                        f"Textbox {tb_name!r} (dataset {dsn!r}) refers to field "
                        f"'{field_name}', which is not in that dataset's "
                        f"<Fields> list. Maybe meant "
                        f"=Parameters!P_{field_name}.Value.",
                    ))

        # 10) Size unit sanity
    size_re = re.compile(
        r"<(Width|Height|TopMargin|BottomMargin|LeftMargin|RightMargin|PageWidth|PageHeight)>([^<]+)</\1>"
    )
    for tag, val in size_re.findall(rdl_xml):
        if val.strip() and not re.match(r"^[\d.]+(in|cm|pt|mm|pc)\s*$", val.strip()):
            issues.append(("RED", f"size.{tag}",
                           f"<{tag}> value {val!r} doesn't end with a valid unit"))

    # ---- Verdict ----
    sev_order = {"BLOCKER": 3, "RED": 2, "AMBER": 1}
    worst = max((sev_order[s] for s, _, _ in issues), default=0)
    verdict_label = {3: "BLOCKER", 2: "RED", 1: "AMBER", 0: "READY"}[worst]

    return {
        "verdict": verdict_label,
        "issues": [{"severity": s, "rule": r, "message": m} for s, r, m in issues],
        "stats": stats,
    }
