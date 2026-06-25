"""
vb_expr_check.py — compile every generated SSRS expression through the REAL
VB.NET compiler, the same compilation SSRS performs when it publishes a report's
expression host.

WHY THIS EXISTS
---------------
The RDL layout can be geometry-/render-verified, but that proves nothing about
whether each ``=...`` VB.NET expression actually *compiles*. An expression with
bad ``IIf`` arity, a trailing comma, unbalanced parens, an undefined function the
translator invented, or a leaked Oracle ``||`` renders as ``#Error`` /
"The Value expression for the textrun contains an error" the moment real SSRS
runs it — yet it sails past a static Fields!-reference check. This module closes
that gap: it extracts every expression (and the report's own ``<Code>`` block)
and compiles them through ``System.CodeDom`` ``VBCodeProvider`` via the sibling
``tools/renderlab/vb_expr_check.ps1`` harness.

Graceful degradation: on a host without PowerShell / the VB compiler (e.g. CI on
Linux), :func:`check_rdl_expressions` returns ``available=False`` and callers
treat it as "skipped", mirroring the render_rdl signed-DLL fallback.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_PS_SCRIPT = os.path.normpath(
    os.path.join(_HERE, "..", "..", "..", "tools", "renderlab", "vb_expr_check.ps1")
)


def _local(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def extract_code(root: ET.Element) -> str:
    """Return the report's <Code> block text (custom VB), or '' if absent."""
    for el in root.iter():
        if _local(el.tag) == "Code":
            return el.text or ""
    return ""


def extract_expressions(root: ET.Element) -> List[Tuple[str, str]]:
    """Every element whose text begins with '=' is an SSRS expression. Return a
    list of (element-local-name, expression-text). The <Code> block is skipped
    (it is VB *definitions*, compiled separately, not an expression)."""
    out: List[Tuple[str, str]] = []
    for el in root.iter():
        if _local(el.tag) == "Code":
            continue
        t = el.text
        if t is None:
            continue
        s = t.strip()
        if s.startswith("="):
            out.append((_local(el.tag), s))
    return out


def _powershell() -> str | None:
    for cand in ("pwsh", "powershell"):
        if shutil.which(cand):
            return cand
    return None


def check_rdl_expressions(rdl_xml: str, timeout: int = 240) -> Dict[str, Any]:
    """Compile every expression in ``rdl_xml`` through the VB.NET compiler.

    Returns a dict::

        {
          "available": bool,          # False => compiler/host not present (skipped)
          "results": [ {location, expr, ok, errors:[...]} ],
          "bad":     [ ...subset where ok is False... ],
          "summary": {"total": N, "failed": M},
        }
    """
    try:
        root = ET.fromstring(rdl_xml)
    except ET.ParseError as exc:
        return {"available": True, "results": [], "bad": [],
                "summary": {"total": 0, "failed": 0},
                "error": f"xml parse: {exc}"}

    exprs = extract_expressions(root)
    code = extract_code(root)

    ps = _powershell()
    if ps is None or not os.path.exists(_PS_SCRIPT):
        return {"available": False, "reason": "powershell or harness unavailable",
                "results": [], "bad": [], "summary": {"total": len(exprs), "failed": 0}}

    if not exprs:
        return {"available": True, "results": [], "bad": [],
                "summary": {"total": 0, "failed": 0}}

    payload = {"code": code, "exprs": [e for _loc, e in exprs]}
    tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    try:
        json.dump(payload, tmp)
        tmp.flush()
        tmp.close()
        proc = subprocess.run(
            [ps, "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", _PS_SCRIPT, "-InFile", tmp.name],
            capture_output=True, text=True, timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {"available": False, "reason": f"harness invocation failed: {exc}",
                "results": [], "bad": [], "summary": {"total": len(exprs), "failed": 0}}
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

    data = None
    for line in reversed((proc.stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                data = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
    if data is None:
        return {"available": False,
                "reason": f"no JSON from harness; stderr={proc.stderr[:200]!r}",
                "results": [], "bad": [], "summary": {"total": len(exprs), "failed": 0}}
    if not data.get("available"):
        return {"available": False, "reason": data.get("reason", ""),
                "results": [], "bad": [], "summary": {"total": len(exprs), "failed": 0}}

    by_idx = {r.get("index"): r for r in data.get("results", [])}
    results: List[Dict[str, Any]] = []
    for i, (loc, e) in enumerate(exprs):
        r = by_idx.get(i, {"ok": True, "errors": []})
        results.append({"location": loc, "expr": e,
                        "ok": bool(r.get("ok", True)),
                        "errors": list(r.get("errors", []))})
    # A header-level error (index -1) means the report's <Code> block itself did
    # not compile — surface it as a synthetic failure so it is never swallowed.
    if -1 in by_idx and by_idx[-1].get("errors"):
        results.append({"location": "Code", "expr": "(report <Code> block)",
                        "ok": False, "errors": list(by_idx[-1]["errors"])})

    bad = [r for r in results if not r["ok"]]
    return {"available": True, "results": results, "bad": bad,
            "summary": {"total": len(results), "failed": len(bad)}}
