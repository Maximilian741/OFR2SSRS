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


def preflight_audit(rdl_xml: str, target_db: str = "oracle") -> Dict:
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

    # 3c) Empty required containers. The RDL 2008/01 schema marks several
    # collection elements with minOccurs=1 on their child sequence. Emitting
    # them empty triggers cryptic SSRS upload errors like:
    #   "The element 'ReportItems' has incomplete content. List of possible
    #    elements expected: 'Line, Rectangle, Textbox, Image, Subreport,
    #    Chart, GaugePanel, Tablix, CustomReportItem'."
    # Catch them all here so the generator can't accidentally regress.
    REQUIRED_NONEMPTY = (
        "ReportItems",
        "TablixCells",
        "TablixRows",
        "TablixColumns",
        "Fields",
        "EmbeddedImages",
        "ReportParameters",
        "DataSets",
        "DataSources",
        "TablixMembers",
        "GroupExpressions",
        "Paragraphs",
        "TextRuns",
    )
    empty_counts: Dict[str, int] = {}
    for tag in REQUIRED_NONEMPTY:
        for el in find_all(tree, tag):
            if len(list(el)) == 0:
                empty_counts[tag] = empty_counts.get(tag, 0) + 1
    for tag, count in empty_counts.items():
        issues.append((
            "BLOCKER",
            f"rdl.empty_required_container.{tag.lower()}",
            f"<{tag}> appears {count}x with zero children. RDL 2008/01 "
            f"requires at least one child element. Upload to SSRS will "
            f"fail with 'The element {tag!r} has incomplete content.' "
            f"Either populate the container or omit it entirely.",
        ))

    # 3d) Image element validity. Per RDL 2008/01 schema:
    #   <Image>
    #     <Source>External | Embedded | Database</Source>     (required)
    #     <Value>...</Value>                                   (required;
    #         meaning depends on Source: literal embedded-image name for
    #         Embedded, expression for Database/External)
    #     <Sizing>AutoSize | Fit | FitProportional | Clip</Sizing>  (optional)
    # If Source is Embedded, <Value> must match the Name of an
    # <EmbeddedImage> declared in the report-level <EmbeddedImages>
    # collection, or upload fails with:
    #   "The value of the Value property for the image '<X>' is '<Y>',
    #    which is not a valid Value."
    valid_image_source = {"External", "Embedded", "Database"}
    valid_image_sizing = {"AutoSize", "Fit", "FitProportional", "Clip"}
    embedded_names = set()
    for ei in find_all(tree, "EmbeddedImage"):
        nm = ei.get("Name") or ""
        if nm:
            embedded_names.add(nm)
    for img in find_all(tree, "Image"):
        img_name = img.get("Name", "?")
        src = img.find(NS + "Source") if NS else img.find("Source")
        val = img.find(NS + "Value") if NS else img.find("Value")
        sz = img.find(NS + "Sizing") if NS else img.find("Sizing")
        if src is None:
            issues.append((
                "BLOCKER",
                f"rdl.image.invalid.{img_name}",
                f"Image {img_name!r}: missing <Source>. Required values: "
                f"External, Embedded, Database.",
            ))
        elif (src.text or "").strip() not in valid_image_source:
            issues.append((
                "BLOCKER",
                f"rdl.image.invalid.{img_name}",
                f"Image {img_name!r}: <Source>{src.text!r}</Source> not a "
                f"valid value. Allowed: External, Embedded, Database.",
            ))
        if val is None:
            issues.append((
                "BLOCKER",
                f"rdl.image.invalid.{img_name}",
                f"Image {img_name!r}: missing <Value>.",
            ))
        elif src is not None and (src.text or "").strip() == "Embedded":
            v = (val.text or "").strip()
            if v and v not in embedded_names:
                issues.append((
                    "BLOCKER",
                    f"rdl.image.invalid.{img_name}",
                    f"Image {img_name!r}: Source=Embedded but Value={v!r} "
                    f"does not match any <EmbeddedImage> Name. Declared "
                    f"embedded names: {sorted(embedded_names)!r}. SSRS "
                    f"will reject upload with 'is not a valid Value'.",
                ))
        elif src is not None and (src.text or "").strip() == "Database":
            v = (val.text or "").strip()
            if v and not v.startswith("="):
                issues.append((
                    "BLOCKER",
                    f"rdl.image.invalid.{img_name}",
                    f"Image {img_name!r}: Source=Database but Value={v!r} "
                    f"is not an expression. Expected =Fields!X.Value.",
                ))
        if sz is not None:
            sv = (sz.text or "").strip()
            if sv and sv not in valid_image_sizing:
                issues.append((
                    "BLOCKER",
                    f"rdl.image.invalid.{img_name}",
                    f"Image {img_name!r}: <Sizing>{sv!r}</Sizing> not a "
                    f"valid value. Allowed: AutoSize, Fit, FitProportional, "
                    f"Clip.",
                ))

    # 3e) EmbeddedImage integrity. Each <EmbeddedImage> must carry both
    # <MIMEType> and <ImageData>; SSRS otherwise reports a deserialization
    # error at upload time.
    for ei in find_all(tree, "EmbeddedImage"):
        nm = ei.get("Name", "?")
        if (ei.find(NS + "MIMEType") if NS else ei.find("MIMEType")) is None:
            issues.append((
                "BLOCKER",
                f"rdl.embedded_image.invalid.{nm}",
                f"EmbeddedImage {nm!r}: missing <MIMEType>.",
            ))
        if (ei.find(NS + "ImageData") if NS else ei.find("ImageData")) is None:
            issues.append((
                "BLOCKER",
                f"rdl.embedded_image.invalid.{nm}",
                f"EmbeddedImage {nm!r}: missing <ImageData>.",
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
        # SSRS rejects the upload on a duplicate <DataSet Name>.
        if name in declared_ds_names:
            issues.append(("BLOCKER", "rdl.duplicate_dataset_name",
                           f"Duplicate DataSet name {name!r} — SSRS rejects the upload"))
        declared_ds_names.add(name)
        # ... and on a duplicate <Field Name> within a DataSet ("the dataset
        # has a duplicate field"). Two source columns that sanitize to the same
        # SSRS identifier hit this; one of them must be renamed at the source.
        _fseen: set = set()
        _fdups: set = set()
        for _f in find_all(ds, "Field"):
            _fn = _f.get("Name")
            (_fdups if _fn in _fseen else _fseen).add(_fn)
        if _fdups:
            issues.append(("BLOCKER", f"dataset.{name}.duplicate_field",
                           f"{name}: duplicate Field name(s) {sorted(_fdups)} — two "
                           "columns map to the same SSRS identifier; rename one"))

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

    # Cross-check QueryParameters against ReportParameters.
    # Strip both '@' (T-SQL) and ':' (Oracle bind) prefixes so the same
    # check applies regardless of the dataset's target provider.
    # Match is case-insensitive because the generator does a case-folded
    # canonical lookup when binding QueryParameter values.
    declared_bare = {p.lstrip("@:").upper() for p in declared_params}
    for ds in datasets:
        name = ds.get("Name", "")
        for qp in find_all(ds, "QueryParameter"):
            qname = qp.get("Name", "").lstrip("@:")
            if qname and qname.upper() not in declared_bare:
                issues.append(("RED", f"dataset.{name}.qp_undeclared",
                               f"DataSet {name}: QueryParameter {qname} has no matching ReportParameter"))

    # 6a) BLOCKER: every ReportParameter MUST carry a CONCRETE default value
    # (a non-empty <DefaultValue><Values><Value>, e.g. =Nothing or a literal),
    # or a dataset-driven <DataSetReference> default. An EMPTY/missing default
    # is THE cause of the "Define Query Parameters" dialog when the user
    # refreshes a dataset whose query binds to the parameter -- followed by a
    # runtime "parameter is missing a value" failure. The contract is: upload
    # -> repoint the shared data source -> Refresh Fields -> enter creds, with
    # NO parameter prompt, for EVERY report. So an empty default is a hard
    # upload-experience BLOCKER that must never ship silently. (Regression gate
    # for the load-bearing parameter-refresh bypass.)
    for rp in rps:
        pname = rp.get("Name", "")
        dv = rp.find(NS + "DefaultValue") if NS else rp.find("DefaultValue")
        dsr = None
        if dv is not None:
            dsr = dv.find(NS + "DataSetReference") if NS else dv.find("DataSetReference")
        if dsr is not None:
            continue  # dataset-driven default is fine
        dv_vals = find_all(dv, "Value") if dv is not None else []
        if dv is None or not dv_vals or all((v.text or "").strip() == "" for v in dv_vals):
            issues.append((
                "BLOCKER", "rdl.param_default_empty",
                f"ReportParameter {pname!r} has an empty/missing DefaultValue. SSRS "
                f"will pop the 'Define Query Parameters' dialog when a bound dataset "
                f"is refreshed, then fail at run time. Emit a concrete default "
                f"(=Nothing) so refresh + run proceed with no prompt."))

    # 6b) BLOCKER: every =Parameters!X.Value reference (anywhere in the RDL)
    # MUST match a declared <ReportParameter Name="X"> by EXACT case. SSRS is
    # case-sensitive on parameter names and rejects the upload with
    #   "...refers to a non-existing report parameter 'X'. Letters in the names
    #    of parameters must use the correct case."
    # This is the deterministic gate for the entire class -- it catches
    # undeclared params AND wrong-case references (e.g. a Drillthrough writing
    # P_Envelope when the report declares P_ENVELOPE) at convert time, so a
    # broken RDL can never reach the report server.
    declared_exact = set(declared_params)
    declared_ci = {d.upper(): d for d in declared_params}
    param_ref_re = re.compile(r"Parameters!([A-Za-z_][A-Za-z0-9_]*)\.Value")
    seen_bad = set()
    for pname in param_ref_re.findall(rdl_xml):
        if pname in declared_exact or pname in seen_bad:
            continue
        seen_bad.add(pname)
        ci = declared_ci.get(pname.upper())
        if ci:
            issues.append(("BLOCKER", "rdl.param_ref_wrong_case",
                           f"Expression references =Parameters!{pname}.Value but the "
                           f"declared parameter is {ci!r} (case differs). SSRS is "
                           f"case-sensitive and will reject the upload."))
        else:
            issues.append(("BLOCKER", "rdl.param_ref_undeclared",
                           f"Expression references =Parameters!{pname}.Value but no "
                           f"<ReportParameter Name=\"{pname}\"> is declared. SSRS "
                           f"will reject the upload."))

    # 6c) BLOCKER: a Tablix's TablixRows count MUST equal the number of LEAF
    # members in its TablixRowHierarchy (and likewise columns). SSRS rejects a
    # mismatch with "The Tablix 'X' has a row hierarchy member ... that does not
    # have a corresponding row" -- a structural upload failure. Catch it at
    # convert time so a hand-built or generated Tablix can never ship unbalanced.
    def _leaf_member_count(members_el):
        n = 0
        for m in members_el.findall(NS + "TablixMember") if NS else members_el.findall("TablixMember"):
            sub = m.find(NS + "TablixMembers") if NS else m.find("TablixMembers")
            n += _leaf_member_count(sub) if sub is not None else 1
        return n

    for tx in find_all(tree, "Tablix"):
        txname = tx.get("Name", "?")
        body = tx.find(NS + "TablixBody") if NS else tx.find("TablixBody")
        if body is None:
            continue
        rows_el = body.find(NS + "TablixRows") if NS else body.find("TablixRows")
        cols_el = body.find(NS + "TablixColumns") if NS else body.find("TablixColumns")
        rh = tx.find(NS + "TablixRowHierarchy") if NS else tx.find("TablixRowHierarchy")
        chh = tx.find(NS + "TablixColumnHierarchy") if NS else tx.find("TablixColumnHierarchy")
        if rows_el is not None and rh is not None:
            nrows = len(rows_el.findall(NS + "TablixRow") if NS else rows_el.findall("TablixRow"))
            rmembers = rh.find(NS + "TablixMembers") if NS else rh.find("TablixMembers")
            nleaf = _leaf_member_count(rmembers) if rmembers is not None else 0
            if nrows != nleaf:
                issues.append(("BLOCKER", f"rdl.tablix_row_mismatch.{txname}",
                               f"Tablix {txname!r}: {nrows} TablixRow(s) but "
                               f"{nleaf} row-hierarchy leaf member(s). SSRS "
                               f"requires them EQUAL or upload fails."))
        if cols_el is not None and chh is not None:
            ncols = len(cols_el.findall(NS + "TablixColumn") if NS else cols_el.findall("TablixColumn"))
            cmembers = chh.find(NS + "TablixMembers") if NS else chh.find("TablixMembers")
            nleafc = _leaf_member_count(cmembers) if cmembers is not None else 0
            if ncols != nleafc:
                issues.append(("BLOCKER", f"rdl.tablix_col_mismatch.{txname}",
                               f"Tablix {txname!r}: {ncols} TablixColumn(s) but "
                               f"{nleafc} column-hierarchy leaf member(s). SSRS "
                               f"requires them EQUAL or upload fails."))

    # 6d) RED: a Fields!X.Value reference whose field X exists in NO dataset is a
    # DANGLING reference -- SSRS renders a runtime error ("The Value expression
    # ... refers to the field 'X' which does not exist in the DataSet"). Scoped /
    # aggregate refs -- First/Sum/.../Lookup(Fields!X.Value, "DS") -- legitimately
    # reach another dataset, so strip those spans before scanning. Catch dangling
    # refs at convert time so a report can't silently render broken.
    all_field_names = set()
    for _ds in find_all(tree, "DataSet"):
        for _fld in find_all(_ds, "Field"):
            all_field_names.add((_fld.get("Name") or "").upper())
    if all_field_names:  # skip degenerate RDLs (no datasets/fields)
        _scoped = re.compile(
            r"(?:First|Last|Sum|Avg|Min|Max|Count|CountDistinct|StDev|StDevP|Var|"
            r"VarP|Aggregate|Lookup|LookupSet|Previous|RunningValue)\s*\([^()]*\)",
            re.IGNORECASE)
        _scan, _prev = rdl_xml, None
        while _prev != _scan:           # collapse nested scoped calls repeatedly
            _prev = _scan
            _scan = _scoped.sub("", _scan)
        _seen_dangle = set()
        for fx in re.findall(r"Fields!([A-Za-z_][A-Za-z0-9_]*)\.Value", _scan):
            if fx.upper() in all_field_names or fx in _seen_dangle:
                continue
            _seen_dangle.add(fx)
            issues.append((
                "RED", "rdl.dangling_field_ref",
                f"Expression references =Fields!{fx}.Value but no dataset declares a "
                f"Field named {fx!r}. SSRS renders a runtime error ('field does not "
                f"exist'). Bind it to a real dataset field, or scope it "
                f"(First(Fields!{fx}.Value, \"DataSetName\"))."))

    # 6e) RED: a SCOPED aggregate ref First|Sum|...(Fields!X.Value, "DS") whose
    # field X is not a Field of dataset "DS" -> runtime "field X does not exist
    # in the dataset DS". 6d can't catch this (it strips scoped spans, and X may
    # legitimately exist in a DIFFERENT dataset so the union check passes while
    # the SCOPED lookup fails). Also flag an Image Source="Database" bound to a
    # field no dataset declares (the image silently fails to render).
    ds_field_map = {}
    for _ds in find_all(tree, "DataSet"):
        ds_field_map[_ds.get("Name", "")] = {
            (f.get("Name") or "").upper() for f in find_all(_ds, "Field")}
    if ds_field_map:
        _agg = (r"(?:Sum|Avg|Min|Max|Count|CountDistinct|First|Last|StDev|StDevP|"
                r"Var|VarP|RunningValue|Aggregate)\s*\(\s*Fields!([A-Za-z_]\w*)"
                r"\.Value\s*,\s*\"([^\"]+)\"")
        _seen6e = set()
        for fld, scope in re.findall(_agg, rdl_xml):
            if (fld, scope) in _seen6e:
                continue
            _seen6e.add((fld, scope))
            # only validate when scope names a known dataset (could also be a
            # data-region name, which we can't validate statically -- skip those)
            if scope in ds_field_map and fld.upper() not in ds_field_map[scope]:
                issues.append((
                    "RED", "rdl.scoped_ref_field_missing",
                    f"Expression uses Fields!{fld}.Value scoped to dataset {scope!r}, "
                    f"but {scope!r} has no field {fld!r}. SSRS errors at run time."))
        _allf6e = set().union(*ds_field_map.values()) if ds_field_map else set()
        _seen_img = set()
        for img in find_all(tree, "Image"):
            src = ((img.findtext(NS + "Source") if NS else img.findtext("Source")) or "").strip()
            val = (img.findtext(NS + "Value") if NS else img.findtext("Value")) or ""
            if src == "Database":
                m = re.search(r"Fields!([A-Za-z_]\w*)\.Value", val)
                if m and m.group(1).upper() not in _allf6e and m.group(1) not in _seen_img:
                    _seen_img.add(m.group(1))
                    issues.append((
                        "RED", "rdl.image_field_missing",
                        f"Image is bound to Fields!{m.group(1)}.Value but no dataset "
                        f"declares that field; the image will fail to render."))

    # 6f) RED: body WIDTH + left margin + right margin must be STRICTLY less than
    # PageWidth. When they meet or exceed it, SSRS's PDF renderer emits a blank
    # page after every content page ("blank page after every page") -- the report
    # uploads fine but every other page is blank. Pure geometry; deterministic.
    def _inch(el, tag):
        t = (el.findtext(NS + tag) if (NS and el is not None) else
             (el.findtext(tag) if el is not None else None))
        m = re.match(r"([0-9.]+)", t or "")
        return float(m.group(1)) if m else 0.0
    _pg = tree.find(NS + "Page") if NS else tree.find("Page")
    _bw_el = tree.find(NS + "Width") if NS else tree.find("Width")
    if _pg is not None and _bw_el is not None:
        _bw = float(re.match(r"([0-9.]+)", _bw_el.text or "0").group(1)) if re.match(r"([0-9.]+)", _bw_el.text or "0") else 0.0
        _pw = _inch(_pg, "PageWidth")
        _lm = _inch(_pg, "LeftMargin"); _rm = _inch(_pg, "RightMargin")
        if _pw and (_bw + _lm + _rm) >= _pw - 0.005:
            issues.append((
                "RED", "rdl.page_width_overflow",
                f"Body width ({_bw}in) + margins ({_lm}+{_rm}) = {_bw + _lm + _rm:.2f}in "
                f">= PageWidth {_pw}in. SSRS will emit a blank page after every page. "
                f"Reduce the left/right margins or body width so it is strictly less."))

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
        issues.append(("BLOCKER", "rdl.no_page", "Missing <Page> - page sizing required"))


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

    # 9c) Expression scope validity. Every textbox <Value> expression that
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
    tablix_scope: Dict[str, str] = {}
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
        for v in find_all(tb, "Value"):
            txt = (v.text or "")
            if "Fields!" not in txt:
                continue
            # Lookup()/LookupSet() intentionally reference fields from a DIFFERENT
            # dataset (their 2nd/3rd args carry an explicit dataset name as the
            # 4th arg). Skip scope-checking those -- they are the sanctioned
            # cross-dataset mechanism (master-detail #3), not an out-of-scope bug.
            if re.search(r"Lookup(?:Set)?\s*\(", txt):
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

    # 9d) Unscoped aggregate Fields! reference. Every <Image>/<Textbox>
    # whose Value expression references Fields!X.Value MUST be either
    # inside a Tablix (so the data region supplies the scope) or wrap the
    # Fields! reference in an aggregate function with an explicit dataset
    # scope argument. Page header/footer items and body items not under a
    # Tablix all need explicit scope or SSRS rejects upload with:
    #   "The Value expression for the image 'Img_Sig' references a field
    #    in an aggregate expression without a scope. A scope is required
    #    for all aggregates in the page header or footer which reference
    #    fields."
    AGG_FUNCS = ("First", "Last", "Sum", "Avg", "Min", "Max", "Count",
                 "CountDistinct", "CountRows", "StDev", "StDevP",
                 "Var", "VarP", "RunningValue", "Aggregate")
    agg_alt = "|".join(AGG_FUNCS)

    def _is_scoped(expr: str, field_name: str) -> bool:
        # Strict match: <Agg>(... Fields!<X>.Value [...optional...] , "<DS>" )
        pat = (
            r"(?:" + agg_alt + r")\s*\(" +
            r"[^()]*?Fields!" + re.escape(field_name) + r"\.Value" +
            r"[^()]*?,\s*\"[^\"]+\"\s*\)"
        )
        return bool(re.search(pat, expr))

    # Pre-compute "is this element inside a Tablix?" by walking down
    # from each Tablix and collecting id()'s of descendants.
    in_tablix_ids: set = set()
    for tx in find_all(tree, "Tablix"):
        for desc in tx.iter():
            in_tablix_ids.add(id(desc))

    fields_ref_re = re.compile(r"Fields!([A-Za-z_][A-Za-z0-9_]*)\.Value")

    for tag_name in ("Image", "Textbox"):
        for el in find_all(tree, tag_name):
            if id(el) in in_tablix_ids:
                continue
            el_name = el.get("Name", "?")
            for v in find_all(el, "Value"):
                txt = (v.text or "")
                if "Fields!" not in txt:
                    continue
                seen: set = set()
                for m in fields_ref_re.finditer(txt):
                    fname = m.group(1)
                    if fname in seen:
                        continue
                    seen.add(fname)
                    if _is_scoped(txt, fname):
                        continue
                    issues.append((
                        "BLOCKER",
                        f"rdl.unscoped_aggregate.{tag_name.lower()}.{el_name}",
                        f"{tag_name} {el_name!r} is outside any data region "
                        f"but references Fields!{fname}.Value without an "
                        f"aggregate scope. Wrap as "
                        f'First(Fields!{fname}.Value, "<DataSetName>"). '
                        f"SSRS otherwise fails upload with 'references a "
                        f"field in an aggregate expression without a scope'.",
                    ))

    # 10) Size unit sanity
    size_re = re.compile(
        r"<(Width|Height|TopMargin|BottomMargin|LeftMargin|RightMargin|PageWidth|PageHeight)>([^<]+)</\1>"
    )
    for tag, val in size_re.findall(rdl_xml):
        if val.strip() and not re.match(r"^[\d.]+(in|cm|pt|mm|pc)\s*$", val.strip()):
            issues.append(("RED", f"size.{tag}",
                           f"<{tag}> value {val!r} doesn't end with a valid unit"))

    # Oracle constructs ((+), NVL, DECODE, TO_DATE, DUAL, ...) and Oracle
    # :binds are CORRECT for the shipped oracle target (OracleClient runs
    # the original SQL); flagging them turned every correct oracle-target
    # report RED. Drop those rules unless we are targeting SQL Server.
    _td = (target_db or "oracle").lower()
    if _td != "sqlserver":
        issues = [(_s, _r, _m) for (_s, _r, _m) in issues
                  if not (_r.endswith(".oracle_leftover")
                          or _r.endswith(".bind_var"))]

    # ---- Verdict ----
    sev_order = {"BLOCKER": 3, "RED": 2, "AMBER": 1}
    worst = max((sev_order[s] for s, _, _ in issues), default=0)
    verdict_label = {3: "BLOCKER", 2: "RED", 1: "AMBER", 0: "READY"}[worst]

    return {
        "verdict": verdict_label,
        "issues": [{"severity": s, "rule": r, "message": m} for s, r, m in issues],
        "stats": stats,
    }
