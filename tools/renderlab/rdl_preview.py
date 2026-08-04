"""TRUE PREVIEW: page images of what the generated RDL actually prints.

The hand-built HTML mockup re-implements Oracle's layout in the browser, so
it can only ever *approximate* the report and every discrepancy has to be
chased by hand. The RDL run through Microsoft's own ReportViewer engine is
not an approximation -- it is the thing SSRS will print. Rasterising that
render gives a preview that cannot disagree with the deliverable, and gives
a human (or Claude) an image to actually LOOK at instead of a metric to
trust.

    from rdl_preview import preview_pages
    pages = preview_pages(rdl_xml, out_dir)   # -> [Path, ...] one PNG/page

Layout mode is used (expressions staticized to placeholders): the live
expression host cannot run on this machine -- Smart App Control blocks the
unsigned RenderLab.exe, and driving LocalReport in-process crashes clr.dll
when it compiles the expression assembly. Geometry, pagination, and page
count are faithful; computed VALUES show as placeholders.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from render import LIB, PS1, synthesize_data

__all__ = ["preview_pages", "render_to_pdf"]

_DPI = 110  # legible for reading text without huge files


def render_to_pdf(rdl_xml: str, out_pdf: Path, rows: int = 3,
                  timeout: int = 300) -> dict:
    """Render RDL text to PDF through the signed ReportViewer DLLs."""
    from ms_layout import staticize

    out_pdf = Path(out_pdf)
    static = staticize(rdl_xml)
    with tempfile.TemporaryDirectory() as d:
        srdl = Path(d) / "r.rdl"
        srdl.write_text(static, encoding="utf-8")
        djs = Path(d) / "d.json"
        djs.write_text(json.dumps(synthesize_data(static, rows=rows)),
                       encoding="utf-8")
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(PS1), "-RdlPath", str(srdl), "-DataJson", str(djs),
             "-OutPdf", str(out_pdf), "-LibDir", str(LIB)],
            capture_output=True, text=True, timeout=timeout)
    log = (proc.stdout or "") + (proc.stderr or "")
    ok = proc.returncode == 0 and out_pdf.exists()
    return {"ok": ok, "pdf": str(out_pdf) if ok else None, "log": log}


def preview_pages(rdl_xml: str, out_dir: str | Path, rows: int = 3,
                  dpi: int = _DPI, max_pages: int = 12) -> list[Path]:
    """Render the RDL and return one PNG per page (empty list on failure)."""
    import fitz  # PyMuPDF

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf = out_dir / "report.pdf"
    res = render_to_pdf(rdl_xml, pdf, rows=rows)
    if not res["ok"]:
        return []

    pages: list[Path] = []
    zoom = dpi / 72.0
    with fitz.open(str(pdf)) as doc:
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
            p = out_dir / f"page-{i + 1:02d}.png"
            pix.save(str(p))
            pages.append(p)
    return pages


if __name__ == "__main__":  # pragma: no cover - manual tool
    import sys

    src = Path(sys.argv[1])
    dest = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("preview_out")
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
    from converter import convert

    xml = (convert(src.read_bytes())["rdl_xml"]
           if src.suffix.lower() == ".xml" else src.read_text(encoding="utf-8"))
    got = preview_pages(xml, dest)
    print(f"{len(got)} page image(s) -> {dest}")
    for g in got:
        print(" ", g)
