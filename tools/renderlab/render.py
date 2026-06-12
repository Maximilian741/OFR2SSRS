"""Render an RDL to PDF through Microsoft's LocalReport engine.

Synthesizes type-correct sample rows for every dataset declared in the RDL
and invokes render_rdl.ps1. The value for a column is a deterministic
function of (column_name, row_index) ONLY — so a column that appears in two
datasets gets IDENTICAL values in both, which makes Lookup()/cross-dataset
joins resolve naturally with synthetic data.

Usage:
    python tools/renderlab/render.py report.rdl out.pdf [--rows N]

As a library:
    from render import render_rdl, synthesize_data
    result = render_rdl("report.rdl", "out.pdf", rows=3)
    # result = {"ok": bool, "log": str, "pdf": path or None}
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIB = HERE / "lib"
PS1 = HERE / "render_rdl.ps1"
EXE = HERE / "RenderLab.exe"
CS = HERE / "RenderLab.cs"

_CSC_CANDIDATES = [
    Path(r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"),
    Path(r"C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe"),
]


def ensure_exe() -> bool:
    """Compile RenderLab.exe from RenderLab.cs with the framework csc if it
    is missing or older than the source. Returns True when the exe exists."""
    if EXE.exists() and CS.exists() and EXE.stat().st_mtime >= CS.stat().st_mtime:
        return True
    csc = next((c for c in _CSC_CANDIDATES if c.exists()), None)
    if csc is None or not CS.exists():
        return EXE.exists()
    proc = subprocess.run(
        [str(csc), "/nologo", f"/out:{EXE}",
         f"/r:{LIB / 'Microsoft.ReportViewer.WinForms.dll'}",
         f"/r:{LIB / 'Microsoft.ReportViewer.Common.dll'}",
         "/r:System.Web.Extensions.dll",
         "/r:System.Data.dll",
         str(CS)],
        capture_output=True, text=True, cwd=str(HERE),
    )
    if proc.returncode != 0:
        print("csc failed:", (proc.stdout or "") + (proc.stderr or ""))
    return EXE.exists()

NS = "{http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition}"
RD = "{http://schemas.microsoft.com/SQLServer/reporting/reportdesigner}"


def lib_ready() -> bool:
    return (LIB / "Microsoft.ReportViewer.WinForms.dll").exists()


def _sample_value(col: str, typ: str, idx: int):
    """Deterministic synthetic value. Same (col, idx) -> same value, in every
    dataset — that's what makes Lookup() joins land."""
    t = (typ or "").lower()
    u = col.upper()
    # Columns that feed <Image Source="Database"> need byte[] — synthetic
    # strings make the engine warn rsInvalidImageData. NULL renders an
    # empty image box (same as a server with no blob), which is what a
    # layout-verification harness wants.
    if any(h in u for h in ("SIGNATURE", "IMAGE", "BLOB", "PHOTO", "LOGO", "SEAL")):
        return None
    if "datetime" in t:
        return f"2026-0{(idx % 8) + 1}-1{(idx % 9)}T00:00:00"
    if "decimal" in t or "int" in t or "double" in t:
        # A column whose name signals a signed/loss value gets a NEGATIVE
        # sample so negative-format masks (Oracle MI/PR -> .NET pos;neg
        # sections) can be render-verified. Opt-in by name only -- default
        # columns stay 1000+idx so Lookup() join keys still collide.
        if any(h in u for h in ("NEG", "LOSS", "ADJ", "VARIANCE")):
            return -(1000 + idx)
        # Join keys must collide across datasets; plain function of idx.
        return 1000 + idx
    if "byte[]" in t:
        return None  # blob (signature image) — render as empty
    # Strings: readable, distinct per row, schema-agnostic.
    short = re.sub(r"[^A-Za-z]", "", u)[:10] or "VAL"
    return f"{short}-{idx + 1:04d}"


def synthesize_data(rdl_xml: str, rows: int = 3) -> dict:
    root = ET.fromstring(rdl_xml)
    datasets = []
    for ds in root.iter(NS + "DataSet"):
        name = ds.get("Name") or "DataSet1"
        cols = []
        for f in ds.iter(NS + "Field"):
            fname = f.get("Name")
            t = f.find(RD + "TypeName")
            typ = (t.text if t is not None and t.text else "System.String").strip()
            cols.append({"name": fname, "type": typ})
        if not cols:
            continue
        n = rows
        ds_rows = []
        for i in range(n):
            ds_rows.append([_sample_value(c["name"], c["type"], i) for c in cols])
        datasets.append({"name": name, "columns": cols, "rows": ds_rows})
    return {"datasets": datasets}


def render_rdl(rdl_path: str | Path, out_pdf: str | Path,
               rows: int = 3, timeout: int = 240) -> dict:
    rdl_path = Path(rdl_path)
    out_pdf = Path(out_pdf)
    if not lib_ready():
        return {"ok": False, "pdf": None,
                "log": "ReportViewer DLLs missing — run fetch_reportviewer.py"}
    rdl_xml = rdl_path.read_text(encoding="utf-8")
    spec = synthesize_data(rdl_xml, rows=rows)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                     encoding="utf-8") as tf:
        json.dump(spec, tf)
        data_json = tf.name
    try:
        if ensure_exe():
            cmd = [str(EXE), str(rdl_path), data_json, str(out_pdf)]
        else:  # PowerShell fallback (no expression-host support in sandbox)
            cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                   "-File", str(PS1),
                   "-RdlPath", str(rdl_path),
                   "-DataJson", data_json,
                   "-OutPdf", str(out_pdf),
                   "-LibDir", str(LIB)]
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
        )
        log = (proc.stdout or "") + (proc.stderr or "")
        ok = proc.returncode == 0 and out_pdf.exists()
        return {"ok": ok, "pdf": str(out_pdf) if ok else None, "log": log}
    finally:
        try:
            Path(data_json).unlink()
        except OSError:
            pass


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    rows = 3
    if "--rows" in sys.argv:
        i = sys.argv.index("--rows")
        rows = int(sys.argv[i + 1])
    res = render_rdl(sys.argv[1], sys.argv[2], rows=rows)
    print(res["log"])
    print("OK" if res["ok"] else "FAILED")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
