"""Compile a report's <Code> block and CALL one of its functions.

Thin wrapper over ``vb_invoke.ps1``. Use it to prove a generated VB reducer
COMPUTES the right thing -- ``vb_expr_check`` only proves it compiles, and on a
host where the ReportViewer expression host is blocked the engine never runs it
at all.

    from vb_invoke import invoke
    r = invoke(code, [("NDBreakBlock", [keys, once, each, 0.6, 0.0, 0.0,
                                        0.19, 0.155])])
    if r["available"]:
        assert r["results"][0]["value"] == ...

``available`` is False on a host without PowerShell / the VB compiler; callers
skip, exactly like the render and expression-compile rails.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_PS1 = os.path.join(_HERE, "vb_invoke.ps1")

__all__ = ["invoke"]


def invoke(code: str, calls, timeout: int = 180) -> dict:
    """Compile ``code`` and invoke ``calls`` = [(func_name, [args...]), ...].

    Arguments go over as JSON: a list becomes VB ``Object()``, ``None``
    becomes ``Nothing``, a number becomes ``Double``, anything else a
    ``String``. Returns the harness dict (``available`` / ``compiled`` /
    ``results``); never raises for an unavailable host.
    """
    ps = shutil.which("powershell") or shutil.which("pwsh")
    if ps is None or not os.path.exists(_PS1):
        return {"available": False, "reason": "powershell/harness not found"}
    spec = {"code": code,
            "calls": [{"func": f, "args": list(a)} for f, a in calls]}
    fd, path = tempfile.mkstemp(suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(spec, fh)
        try:
            proc = subprocess.run(
                [ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                 _PS1, "-InFile", path],
                capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"available": False, "reason": str(exc)}
        out = (proc.stdout or "").strip()
        if not out:
            return {"available": False, "reason": (proc.stderr or "")[-400:]}
        try:
            return json.loads(out)
        except ValueError:
            return {"available": False, "reason": out[-400:]}
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
