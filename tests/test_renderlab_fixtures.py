"""Render the SYNTHETIC fixtures through Microsoft's actual report engine.

This is the strongest regression net in the suite: a generated RDL is fed
to the same processing/rendering code SSRS runs, with synthetic rows, and
the produced PDF is opened and measured. Skips cleanly when the
ReportViewer DLLs haven't been fetched (tools/renderlab/README.md) so the
public repo's CI never breaks — on dev machines with the DLLs it proves:

  * the RDL renders AT ALL (the ultimate "upload will work" check),
  * no blank-page cadence,
  * no engine overlap warnings (the content-clipping bug class).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "tools" / "renderlab"))

from converter import convert  # noqa: E402

try:
    from render import render_rdl, lib_ready  # noqa: E402
    _LIB_OK = lib_ready()
except Exception:  # noqa: BLE001
    _LIB_OK = False

pytestmark = pytest.mark.skipif(
    not _LIB_OK or sys.platform != "win32",
    reason="ReportViewer DLLs not fetched (tools/renderlab) or non-Windows",
)

_FIXTURES = [
    ROOT / "tests" / "fixtures" / "source_of_truth" / "letter" / "source.xml",
    ROOT / "tests" / "fixtures" / "source_of_truth" / "master_detail" / "source.xml",
    ROOT / "tests" / "fixtures" / "subreports" / "SAMPLE_DRILLTHROUGH.xml",
    ROOT / "tests" / "fixtures" / "subreports" / "SAMPLE_LETTER_CHILD.xml",
    ROOT / "tests" / "fixtures" / "subreports" / "SAMPLE_MASTER_DETAIL.xml",
]


def _blank_pages(pdf_path: str) -> list[int]:
    from pypdf import PdfReader
    out = []
    r = PdfReader(pdf_path)
    for i, page in enumerate(r.pages):
        txt = (page.extract_text() or "").strip()
        residual = "".join(
            ln for ln in txt.splitlines()
            if not ln.strip().lower().startswith(("page ", "report run on"))
        ).strip()
        if len(residual) < 8:
            out.append(i + 1)
    return out


@pytest.mark.parametrize("src", [p for p in _FIXTURES if p.exists()],
                         ids=lambda p: p.parent.name + "/" + p.name)
def test_fixture_renders_clean_through_ms_engine(src, tmp_path):
    rdl = convert(src.read_bytes())["rdl_xml"]
    rdl_path = tmp_path / "r.rdl"
    rdl_path.write_text(rdl, encoding="utf-8")
    res = render_rdl(rdl_path, tmp_path / "r.pdf", rows=3)
    assert res["ok"], f"MS engine refused the RDL:\n{res['log'][-1500:]}"
    # No blank-page cadence.
    blanks = _blank_pages(res["pdf"])
    assert blanks == [], f"blank pages {blanks} in rendered PDF"
    # No overlap warnings (the clipping bug class).
    overlaps = [ln for ln in res["log"].splitlines()
                if "verlap" in ln and ln.startswith("WARN")]
    assert overlaps == [], f"engine overlap warnings: {overlaps}"
