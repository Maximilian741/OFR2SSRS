"""
ai_apply.py — Apply pasted AI translations back into the conversion bundle.

Companion to ai_assist.py. ai_assist emits prompts the user feeds to an LLM;
this module takes the LLM's response (a T-SQL UDF body or a SELECT) and
patches the in-memory conversion data so the next bundle download includes it.

Public API:
    validate_udf_body(body, expected_name=None) -> (ok, issues)
    apply_fix(rdl_xml, target, new_body)        -> (updated_rdl_xml, info)
    package_fixed_bundle(conversion_data, fixes) -> new_conversion_data

No new pip deps. Uses lxml + stdlib only.
"""
from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Optional, Tuple

from lxml import etree


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

# Tokens we never want to land in the RDL — they would either run unintended
# DDL/DML or open up SQL injection through sys/extended procs.
_BLOCKED_TOKENS: Tuple[str, ...] = (
    "DROP",
    "TRUNCATE",
    "EXEC",
    "EXECUTE",
    "xp_",
    "sp_executesql",
)

_PARAM_RE = re.compile(r"@[A-Za-z_][A-Za-z0-9_]*")
_FN_NAME_RE = re.compile(
    r"CREATE\s+(?:OR\s+ALTER\s+)?FUNCTION\s+([A-Za-z_][\w\.\[\]]*)",
    re.IGNORECASE,
)


def _strip_for_token_search(body: str) -> str:
    """Remove -- line comments and /* ... */ block comments before scanning
    for blocked tokens, so a comment like "-- DROP TABLE foo" doesn't
    falsely reject."""
    no_block = re.sub(r"/\*.*?\*/", " ", body, flags=re.DOTALL)
    no_line = re.sub(r"--[^\n]*", " ", no_block)
    return no_line


def validate_udf_body(body: str, expected_name: Optional[str] = None) -> Tuple[bool, List[str]]:
    """Sanity-check a pasted T-SQL UDF body.

    Returns (ok, issues). Reject conditions return ok=False; warn-only
    conditions still return ok=True with the warning appended to issues.
    """
    issues: List[str] = []
    if body is None:
        return False, ["empty body"]

    txt = body.strip()
    if not txt:
        return False, ["empty body"]

    # Length warnings (do not reject)
    if len(txt) < 30:
        issues.append("warning: body is very short (< 30 chars) — may be incomplete")
    elif len(txt) > 10000:
        issues.append("warning: body is very long (> 10000 chars)")

    upper = txt.upper()

    # Must look like a function body
    if "CREATE FUNCTION" not in upper and "CREATE OR ALTER FUNCTION" not in upper and "RETURN" not in upper:
        return False, issues + ["missing CREATE FUNCTION or RETURN — does not look like a T-SQL UDF body"]

    # Reject dangerous tokens (only in non-comment text)
    scan = _strip_for_token_search(txt)
    scan_upper = scan.upper()
    for tok in _BLOCKED_TOKENS:
        if tok.startswith("xp_") or tok.startswith("sp_"):
            if tok.lower() in scan.lower():
                return False, issues + [f"rejected: contains forbidden token '{tok}'"]
        else:
            if re.search(r"\b" + re.escape(tok) + r"\b", scan_upper):
                return False, issues + [f"rejected: contains forbidden token '{tok}'"]

    # Should reference @parameters — warn if none
    if not _PARAM_RE.search(txt):
        issues.append("warning: no @parameter references found (function takes no inputs?)")

    # If caller specified a function name, make sure the body declares it.
    if expected_name:
        m = _FN_NAME_RE.search(txt)
        if not m:
            issues.append("warning: could not parse function name from body")
        else:
            got = m.group(1).strip().strip("[]")
            want = expected_name.strip().strip("[]")
            if got.lower() != want.lower() and got.split(".")[-1].lower() != want.split(".")[-1].lower():
                return False, issues + [
                    f"rejected: function name in body ('{got}') does not match expected ('{expected_name}')"
                ]

    return True, issues


# ---------------------------------------------------------------------------
# RDL patching
# ---------------------------------------------------------------------------

def _parse_rdl(rdl_xml: str) -> etree._ElementTree:
    parser = etree.XMLParser(remove_blank_text=False, recover=False)
    if isinstance(rdl_xml, str):
        rdl_bytes = rdl_xml.encode("utf-8")
    else:
        rdl_bytes = rdl_xml
    root = etree.fromstring(rdl_bytes, parser=parser)
    return etree.ElementTree(root)


def _serialize_rdl(tree: etree._ElementTree) -> str:
    return etree.tostring(
        tree,
        xml_declaration=True,
        encoding="utf-8",
        pretty_print=False,
    ).decode("utf-8")


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _find_all_local(root: etree._Element, name: str) -> List[etree._Element]:
    out: List[etree._Element] = []
    for el in root.iter():
        tag = el.tag
        if not isinstance(tag, str):
            # comments and processing instructions have non-string tags
            continue
        if _localname(tag) == name:
            out.append(el)
    return out


def _apply_query_fix(root: etree._Element, dataset_name: str, new_body: str) -> Tuple[bool, str]:
    """Replace the <CommandText> body of the named DataSet (case-insensitive)."""
    target = dataset_name.lower()
    for ds in _find_all_local(root, "DataSet"):
        name = (ds.get("Name") or "").lower()
        if name != target:
            continue
        for cmd in _find_all_local(ds, "CommandText"):
            cmd.text = new_body
            return True, f"DataSet[{ds.get('Name')}]/Query/CommandText"
    return False, f"DataSet '{dataset_name}' not found"


def _apply_udf_fix(root: etree._Element, fn_name: str, new_body: str) -> Tuple[bool, str]:
    """Store the UDF in a sidecar processing-instruction at the top of the RDL.

    XML comments cannot contain '--', so we use a <?O2S_AI_APPLIED_UDF ... ?>
    processing instruction instead. PIs accept arbitrary payloads, are
    tolerated by SSRS's RDL reader, and round-trip cleanly through lxml.
    """
    bare = fn_name.split(".")[-1].strip("[]")
    pi_target = "O2S_AI_APPLIED_UDF"

    # PIs may not contain '?>' — defensively neutralize that sequence.
    sanitized = new_body.strip().replace("?>", "?_>")
    payload = f'name="{bare}"\n{sanitized}'

    # If a PI for this function already exists, replace its content.
    for child in list(root):
        if isinstance(child, etree._ProcessingInstruction) and child.target == pi_target:
            existing = child.text or ""
            if f'name="{bare}"' in existing:
                child.text = payload
                return True, f'<?{pi_target} name="{bare}"?> (replaced)'

    pi = etree.ProcessingInstruction(pi_target, payload)
    root.insert(0, pi)
    return True, f'<?{pi_target} name="{bare}"?> (inserted)'


def apply_fix(rdl_xml: str, target: Dict[str, Any], new_body: str) -> Tuple[str, Dict[str, Any]]:
    """Apply one AI fix to the RDL.

    target = {"kind": "udf"|"formula"|"query", "name": str}
    Returns (updated_rdl_xml, info_dict).
    """
    if not rdl_xml:
        raise ValueError("apply_fix: empty rdl_xml")
    kind = (target or {}).get("kind", "").lower()
    name = (target or {}).get("name", "")
    if not kind or not name:
        raise ValueError("apply_fix: target must include 'kind' and 'name'")

    tree = _parse_rdl(rdl_xml)
    root = tree.getroot()

    preview = (new_body or "").strip()[:200]
    info: Dict[str, Any] = {
        "kind": kind,
        "name": name,
        "changed": False,
        "where": "",
        "preview": preview,
    }

    if kind == "udf":
        ok, where = _apply_udf_fix(root, name, new_body)
        info["changed"] = ok
        info["where"] = where
    elif kind == "query":
        ok, where = _apply_query_fix(root, name, new_body)
        info["changed"] = ok
        info["where"] = where
        if not ok:
            info["hint"] = (
                "DataSet name not found in the RDL. Check that 'name' matches "
                "the DataSet Name attribute exactly (case-insensitive)."
            )
    elif kind == "formula":
        info["changed"] = False
        info["where"] = "formula (not applied)"
        info["hint"] = (
            "RDL doesn't carry a separate 'formula' element — formulas live "
            "inside <Value> expressions of textboxes. Update the originating "
            "Oracle CF_* PL/SQL function in source and re-run the converter, "
            "or paste the expression directly into the relevant cell."
        )
    else:
        raise ValueError(f"apply_fix: unknown kind '{kind}'")

    updated_xml = _serialize_rdl(tree)
    return updated_xml, info


# ---------------------------------------------------------------------------
# Bundle packaging
# ---------------------------------------------------------------------------

def package_fixed_bundle(conversion_data: Dict[str, Any], fixes: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Return a NEW conversion_data dict with all the supplied fixes applied
    against its rdl_xml. The original dict is not mutated."""
    if not isinstance(conversion_data, dict):
        raise TypeError("conversion_data must be a dict")
    out = copy.deepcopy(conversion_data)
    rdl = out.get("rdl_xml") or ""
    applied: List[Dict[str, Any]] = list(out.get("applied_fixes") or [])

    for fix in fixes or []:
        target = fix.get("target") or {}
        body = fix.get("new_body") or ""
        try:
            ok, issues = validate_udf_body(body, target.get("name"))
            if not ok:
                applied.append({
                    "target": target,
                    "applied": False,
                    "info": {"changed": False, "issues": issues, "where": "validation_failed"},
                })
                continue
            rdl, info = apply_fix(rdl, target, body)
            applied.append({"target": target, "applied": bool(info.get("changed")), "info": info})
        except Exception as exc:  # noqa: BLE001
            applied.append({
                      "target": target,
                "applied": False,
                "info": {"changed": False, "where": f"error: {exc}"},
            })

    out["rdl_xml"] = rdl
    out["applied_fixes"] = applied
    return out
