"""
Static structural validator for RDL output.

We don't have the real RDL XSD locally, so we hand-code rules based on common
SSRS gotchas that prevent the report from opening in Report Builder or running
against a live database.

Public API:
    validate_rdl(rdl_xml: str) -> list[dict]

Each issue dict has the shape:
    {
        "severity": "error" | "warning" | "info",
        "rule":     str,
        "message":  str,
        "element":  str | None,   # local name (or path) of the offending element
    }
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Namespaces
# ---------------------------------------------------------------------------

RDL_NS_2008 = "http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition"
RDL_NS_2010 = "http://schemas.microsoft.com/sqlserver/reporting/2010/01/reportdefinition"
RDL_NS_2016 = "http://schemas.microsoft.com/sqlserver/reporting/2016/01/reportdefinition"
RD_NS       = "http://schemas.microsoft.com/SQLServer/reporting/reportdesigner"

_ANY_RDL_NS = (RDL_NS_2008, RDL_NS_2010, RDL_NS_2016)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _local(tag: str) -> str:
    """Strip XML namespace from a tag, returning just the local name."""
    if not tag:
        return tag
    if tag.startswith("{"):
        return tag.split("}", 1)[1]
    return tag


def _make(severity: str, rule: str, message: str,
          element: Optional[str] = None) -> Dict[str, Any]:
    return {
        "severity": severity,
        "rule":     rule,
        "message":  message,
        "element":  element,
    }


def _findall_local(root: ET.Element, name: str) -> List[ET.Element]:
    """Find every descendant whose local name matches `name`, namespace-agnostic."""
    out: List[ET.Element] = []
    for el in root.iter():
        if _local(el.tag) == name:
            out.append(el)
    return out


def _child_local(parent: ET.Element, name: str) -> Optional[ET.Element]:
    """Find first direct child by local name."""
    for child in parent:
        if _local(child.tag) == name:
            return child
    return None


def _children_local(parent: ET.Element, name: str) -> List[ET.Element]:
    return [c for c in parent if _local(c.tag) == name]


def _text(el: Optional[ET.Element]) -> str:
    if el is None or el.text is None:
        return ""
    return el.text


# ---------------------------------------------------------------------------
# Individual rule checks
# ---------------------------------------------------------------------------

def _check_parse(rdl_xml: str) -> (Optional[ET.Element], List[Dict[str, Any]]):
    """Parse the RDL XML. Returns (root, issues)."""
    issues: List[Dict[str, Any]] = []
    if not rdl_xml or not rdl_xml.strip():
        issues.append(_make(
            "error", "rdl.parse",
            "RDL XML is empty; nothing to validate.",
            None,
        ))
        return None, issues
    try:
        root = ET.fromstring(rdl_xml)
        return root, issues
    except ET.ParseError as exc:
        issues.append(_make(
            "error", "rdl.parse",
            f"RDL XML is not well-formed: {exc}",
            None,
        ))
        return None, issues


def _check_namespace(rdl_xml: str, root: ET.Element) -> List[Dict[str, Any]]:
    """Root must declare an RDL default namespace AND the rd designer namespace."""
    issues: List[Dict[str, Any]] = []
    tag = root.tag
    has_rdl_ns = tag.startswith("{") and any(
        tag.startswith("{" + ns + "}") for ns in _ANY_RDL_NS
    )
    if not has_rdl_ns:
        issues.append(_make(
            "error", "rdl.namespace",
            "Root element is not in any recognised RDL namespace "
            "(2008/2010/2016 schemas.microsoft.com/sqlserver/reporting). "
            "Report Builder will refuse to open it.",
            _local(tag),
        ))
    # The rd designer namespace usually appears as a declared xmlns:rd.
    # ElementTree drops xmlns declarations on parse, so look at the raw text.
    if RD_NS not in (rdl_xml or ""):
        issues.append(_make(
            "warning", "rdl.namespace",
            "Designer namespace 'schemas.microsoft.com/SQLServer/reporting/"
            "reportdesigner' is not declared on the root; some rd:* hints "
            "(rd:DataSourceID, rd:DefaultName) will be lost.",
            _local(tag),
        ))
    return issues


def _check_report_root(root: ET.Element) -> List[Dict[str, Any]]:
    if _local(root.tag) != "Report":
        return [_make(
            "error", "rdl.report_root",
            f"Root element is <{_local(root.tag)}>; RDL files must be rooted "
            "at <Report>.",
            _local(root.tag),
        )]
    return []


def _check_has_datasource(root: ET.Element) -> List[Dict[str, Any]]:
    sources = _findall_local(root, "DataSource")
    if not sources:
        return [_make(
            "error", "rdl.has_datasource",
            "No <DataSource> declared; the report cannot connect to a "
            "database. Add at least one DataSource (shared or embedded).",
            "Report",
        )]
    return []


def _check_has_dataset(root: ET.Element) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    datasets = _findall_local(root, "DataSet")
    if not datasets:
        return [_make(
            "error", "rdl.has_dataset",
            "No <DataSet> declared; report has nothing to render.",
            "Report",
        )]
    has_query = False
    for ds in datasets:
        ds_name = ds.get("Name") or "(unnamed)"
        cmd = None
        for el in ds.iter():
            if _local(el.tag) == "CommandText":
                cmd = el
                break
        if cmd is None or not _text(cmd).strip():
            issues.append(_make(
                "error", "rdl.has_dataset",
                f"DataSet '{ds_name}' has empty <CommandText>; SSRS will "
                "fail at runtime trying to execute an empty query.",
                f"DataSet[{ds_name}]",
            ))
        else:
            has_query = True
    if not has_query and not issues:
        issues.append(_make(
            "error", "rdl.has_dataset",
            "No DataSet contains a non-empty <CommandText>.",
            "Report",
        ))
    return issues


def _check_dataset_fields(root: ET.Element) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    for ds in _findall_local(root, "DataSet"):
        ds_name = ds.get("Name") or "(unnamed)"
        fields_parent = _child_local(ds, "Fields")
        if fields_parent is None:
            issues.append(_make(
                "error", "rdl.dataset_fields",
                f"DataSet '{ds_name}' has no <Fields> element; Tablix "
                "bindings won't be able to reference any columns.",
                f"DataSet[{ds_name}]",
            ))
            continue
        fields = _children_local(fields_parent, "Field")
        if not fields:
            issues.append(_make(
                "error", "rdl.dataset_fields",
                f"DataSet '{ds_name}' has <Fields> but contains zero "
                "<Field> children; Report Builder will reject it.",
                f"DataSet[{ds_name}]",
            ))
    return issues


def _check_parameter_consistency(root: ET.Element) -> List[Dict[str, Any]]:
    """Every <QueryParameter Name='@P_X'> must have a matching <ReportParameter Name='P_X'>."""
    issues: List[Dict[str, Any]] = []

    # Collect declared report parameters.
    declared: set = set()
    for rp in _findall_local(root, "ReportParameter"):
        nm = rp.get("Name")
        if nm:
            declared.add(nm.lstrip("@"))

    seen_query_params: Dict[str, str] = {}  # bare name -> source dataset
    for qp in _findall_local(root, "QueryParameter"):
        raw = qp.get("Name") or ""
        bare = raw.lstrip("@")
        if not bare:
            continue
        # Walk up to find the enclosing DataSet name for nicer error messages.
        ds_name = "(unknown)"
        # Without parent map we can search from root; for now mark by bare name.
        seen_query_params.setdefault(bare, ds_name)

    for bare, ds_name in sorted(seen_query_params.items()):
        if bare not in declared:
            issues.append(_make(
                "error", "rdl.parameter_consistency",
                f"<QueryParameter> @{bare} has no matching <ReportParameter> "
                f"named '{bare}'. Report Builder will fail to bind the "
                "dataset query.",
                f"QueryParameter[@{bare}]",
            ))

    # Info: ReportParameters declared but never used as a QueryParameter.
    used = set(seen_query_params.keys())
    for p in sorted(declared - used):
        issues.append(_make(
            "info", "rdl.parameter_consistency",
            f"<ReportParameter> '{p}' is declared but never referenced by "
            "any <QueryParameter>; the prompt will appear with no effect.",
            f"ReportParameter[{p}]",
        ))
    return issues


def _check_body_present(root: ET.Element) -> List[Dict[str, Any]]:
    body = _child_local(root, "Body")
    if body is None:
        return [_make(
            "error", "rdl.body_present",
            "<Report> has no <Body> child; the report has nothing to render.",
            "Report",
        )]
    items = _child_local(body, "ReportItems")
    if items is None:
        return [_make(
            "error", "rdl.body_present",
            "<Body> has no <ReportItems> child; the report body is empty.",
            "Body",
        )]
    if len(list(items)) == 0:
        return [_make(
            "warning", "rdl.body_present",
            "<ReportItems> contains no children; the report will render "
            "as a blank page.",
            "ReportItems",
        )]
    return []


def _check_tablix_dataset_bound(root: ET.Element) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    dataset_names = {
        ds.get("Name") for ds in _findall_local(root, "DataSet") if ds.get("Name")
    }
    for tx in _findall_local(root, "Tablix"):
        tx_name = tx.get("Name") or "(unnamed)"
        ds_ref_el = None
        for el in tx.iter():
            if _local(el.tag) == "DataSetName":
                ds_ref_el = el
                break
        if ds_ref_el is None:
            issues.append(_make(
                "error", "rdl.tablix_dataset_bound",
                f"<Tablix> '{tx_name}' has no <DataSetName>; SSRS won't "
                "know which dataset to render.",
                f"Tablix[{tx_name}]",
            ))
            continue
        ref = _text(ds_ref_el).strip()
        if not ref:
            issues.append(_make(
                "error", "rdl.tablix_dataset_bound",
                f"<Tablix> '{tx_name}' has an empty <DataSetName>.",
                f"Tablix[{tx_name}]",
            ))
            continue
        if ref not in dataset_names:
            issues.append(_make(
                "error", "rdl.tablix_dataset_bound",
                f"<Tablix> '{tx_name}' references DataSet '{ref}' which "
                f"is not defined. Known datasets: "
                f"{', '.join(sorted(n for n in dataset_names if n)) or '(none)'}.",
                f"Tablix[{tx_name}]",
            ))
    return issues


# Pattern recognising allowed expression token prefixes inside =... text.
_ALLOWED_EXPR_TOKEN = re.compile(
    r"\b(?:Fields!\w+\.Value|Parameters!\w+\.(?:Value|Label|Count)|"
    r"Globals!\w+|User!\w+|ReportItems!\w+\.Value|Variables!\w+\.Value|"
    r"DataSets!\w+|DataSources!\w+|Aggregates!\w+|Cstr|CDate|CDbl|CInt|"
    r"CBool|First|Last|Sum|Avg|Min|Max|Count|CountDistinct|"
    r"IIf|Switch|Choose|Format|FormatDateTime|FormatNumber|FormatCurrency|"
    r"FormatPercent|Now|Today|Year|Month|Day|DateAdd|DateDiff|DatePart|"
    r"Trim|LTrim|RTrim|Len|Left|Right|Mid|InStr|Replace|UCase|LCase|"
    r"Round|Floor|Ceiling|Abs|Sign|Coalesce|IsNothing|IsNumeric|IsDate)\b"
)
_BARE_FIELD = re.compile(r"=\s*([A-Za-z_]\w*)\b")


def _check_expression_syntax(root: ET.Element) -> List[Dict[str, Any]]:
    """Walk every text node beginning with '=' and warn if it doesn't reference
    any Fields!/Parameters!/Globals! collection."""
    issues: List[Dict[str, Any]] = []
    for el in root.iter():
        txt = el.text
        if not txt:
            continue
        s = txt.strip()
        if not s.startswith("="):
            continue
        # Strip the leading '=' to inspect the expression.
        body = s[1:].strip()
        if not body:
            issues.append(_make(
                "warning", "rdl.expression_syntax",
                f"<{_local(el.tag)}> contains a bare '=' with no expression "
                "body; Report Builder will flag it as #Error.",
                _local(el.tag),
            ))
            continue
        # Numeric / quoted string / parenthesised expressions are fine.
        if _ALLOWED_EXPR_TOKEN.search(body):
            continue
        if body.startswith('"') or body.startswith("'"):
            continue
        # Pure number / numeric expression.
        if re.fullmatch(r"[\-+0-9.\s%*/+()-]+", body):
            continue
        # Bare =Field looks like the user forgot Fields!X.Value
        m = _BARE_FIELD.match(s)
        if m:
            issues.append(_make(
                "warning", "rdl.expression_syntax",
                f"<{_local(el.tag)}> expression '{s[:60]}' looks like a "
                f"bare field reference '={m.group(1)}'; SSRS expressions "
                "must use Fields!"
                f"{m.group(1)}.Value, Parameters!X.Value or Globals!X.",
                _local(el.tag),
            ))
            continue
        # Anything else: weakly warn that we don't recognise an expression token.
        issues.append(_make(
            "info", "rdl.expression_syntax",
            f"<{_local(el.tag)}> expression '{s[:60]}' does not reference "
            "Fields!/Parameters!/Globals!; double-check it's a valid "
            "VB-style RDL expression.",
            _local(el.tag),
        ))
    return issues


_ORACLE_RX = [
    (re.compile(r"\bDECODE\s*\(", re.I), "DECODE"),
    (re.compile(r"\bNVL2?\s*\(",  re.I), "NVL/NVL2"),
    (re.compile(r"\bTO_CHAR\s*\(", re.I), "TO_CHAR"),
    (re.compile(r"\bTO_DATE\s*\(", re.I), "TO_DATE"),
    (re.compile(r"\bTO_NUMBER\s*\(", re.I), "TO_NUMBER"),
    (re.compile(r"\bSYSDATE\b", re.I), "SYSDATE"),
    (re.compile(r"\bSYSTIMESTAMP\b", re.I), "SYSTIMESTAMP"),
    (re.compile(r"\bSUBSTR\s*\(", re.I), "SUBSTR"),
    (re.compile(r"\bINSTR\s*\(", re.I), "INSTR"),
    (re.compile(r"\bROWNUM\b", re.I), "ROWNUM"),
    (re.compile(r"\bMINUS\b", re.I), "MINUS"),
    (re.compile(r"\bDUAL\b", re.I), "DUAL"),
    (re.compile(r"\(\s*\+\s*\)"), "(+) outer-join hint"),
]


def _check_no_oracle_leftovers(root: ET.Element) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    for ds in _findall_local(root, "DataSet"):
        ds_name = ds.get("Name") or "(unnamed)"
        cmd = None
        for el in ds.iter():
            if _local(el.tag) == "CommandText":
                cmd = el
                break
        if cmd is None:
            continue
        sql = _text(cmd)
        if not sql:
            continue
        seen: set = set()
        for rx, label in _ORACLE_RX:
            if rx.search(sql) and label not in seen:
                seen.add(label)
                issues.append(_make(
                    "error", "rdl.no_oracle_leftovers",
                    f"DataSet '{ds_name}' <CommandText> still contains "
                    f"Oracle-only token '{label}'; the translator did not "
                    "rewrite it. Report will fail when SSRS executes the "
                    "query against SQL Server.",
                    f"DataSet[{ds_name}]/CommandText",
                ))
    return issues


def _check_image_source(root: ET.Element) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    for img in _findall_local(root, "Image"):
        name = img.get("Name") or "(unnamed)"
        src_attr = img.get("Source")
        src_child = _child_local(img, "Source")
        if not src_attr and src_child is None:
            issues.append(_make(
                "error", "rdl.image_source",
                f"<Image> '{name}' has no Source attribute or child; SSRS "
                "requires Source='External' | 'Embedded' | 'Database'.",
                f"Image[{name}]",
            ))
    return issues


_VALID_UNIT_RX = re.compile(r"^\s*-?\d+(?:\.\d+)?\s*(in|cm|pt|mm|pc)\s*$", re.I)


def _check_size_units(root: ET.Element) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    targets = ("Width", "Height", "Top", "Left",
               "PageWidth", "PageHeight",
               "LeftMargin", "RightMargin", "TopMargin", "BottomMargin")
    for el in root.iter():
        ln = _local(el.tag)
        if ln not in targets:
            continue
        val = (el.text or "").strip()
        if not val:
            continue
        if not _VALID_UNIT_RX.match(val):
            issues.append(_make(
                "error", "rdl.size_units",
                f"<{ln}> value '{val}' has no valid unit suffix; SSRS "
                "requires one of in / cm / mm / pt / pc.",
                ln,
            ))
    return issues


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_rdl(rdl_xml: str) -> List[Dict[str, Any]]:
    """Run every static check against the RDL XML and return a flat issue list."""
    root, parse_issues = _check_parse(rdl_xml)
    issues: List[Dict[str, Any]] = list(parse_issues)
    if root is None:
        return issues

    issues += _check_namespace(rdl_xml, root)
    issues += _check_report_root(root)
    # If the root isn't <Report> the rest of the rules don't apply meaningfully,
    # but we still attempt them so the user gets all the feedback in one pass.
    issues += _check_has_datasource(root)
    issues += _check_has_dataset(root)
    issues += _check_dataset_fields(root)
    issues += _check_parameter_consistency(root)
    issues += _check_body_present(root)
    issues += _check_tablix_dataset_bound(root)
    issues += _check_expression_syntax(root)
    issues += _check_no_oracle_leftovers(root)
    issues += _check_image_source(root)
    issues += _check_size_units(root)
    return issues


__all__ = ["validate_rdl"]
