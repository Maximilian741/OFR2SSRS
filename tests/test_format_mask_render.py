"""Full-pipeline 1:1 proof for Oracle format masks through the REAL MS engine.

Unit tests (test_format_mask.py) lock the mask->.NET-format translation; this
goes one level further: it CONVERTS a report carrying the documented mask range
and RENDERS the generated RDL through Microsoft's actual ReportViewer, then
reads the PDF and asserts each value matches Oracle's documented TO_CHAR output.
That proves the whole converter wiring -- <Format> stamping, the percent escape,
the MI negative SECTION, and the date UCase value-wrap -- survives end to end,
not just the translator in isolation.

Skips cleanly when the ReportViewer DLLs aren't fetched (tools/renderlab) so the
public repo's CI never breaks; runs on dev machines that have them.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "tools" / "renderlab"))

from converter import convert  # noqa: E402

try:
    from render import render_rdl, lib_ready, expression_host_available  # noqa: E402
    # Asserts Oracle format masks ($1,000.00 / 1000% / 10-JAN-2026) appear in
    # the PDF — that needs RenderLab.exe's live expression+format evaluation;
    # the staticized layout path renders placeholders, not formatted values.
    _EXPR_OK = lib_ready() and expression_host_available()
except Exception:  # noqa: BLE001
    _EXPR_OK = False

pytestmark = pytest.mark.skipif(
    not _EXPR_OK or sys.platform != "win32",
    reason="RenderLab.exe expression host unavailable "
           "(DLLs unfetched / non-Windows / Application Control block)",
)

_FIXTURE = ROOT / "tests" / "fixtures" / "format_mask" / "source.xml"


def _pdf_text(pdf_path: str) -> str:
    from pypdf import PdfReader
    return "\n".join((p.extract_text() or "") for p in PdfReader(pdf_path).pages)


def test_format_masks_render_1to1_through_ms_engine(tmp_path):
    rdl = convert(_FIXTURE.read_bytes())["rdl_xml"]
    rdl_path = tmp_path / "r.rdl"
    rdl_path.write_text(rdl, encoding="utf-8")
    # rows=1 -> the harness feeds AMT/QTY/PCT/IDZ = 1000, NEG_ADJ = -1000,
    # D1/D2/D3 = 2026-01-10. Documented Oracle output for those values:
    res = render_rdl(rdl_path, tmp_path / "r.pdf", rows=1)
    assert res["ok"], f"MS engine refused the RDL:\n{res['log'][-1500:]}"
    txt = _pdf_text(res["pdf"])

    checks = {
        "currency $1,000.00":          "$1,000.00" in txt,
        "grouped 1,000":               re.search(r"(?<!\$)1,000\b", txt) is not None,
        # the percent fix: literal %, NOT a x100 scale (1000 -> "1000%")
        "percent literal 1000%":       "1000%" in txt and "100000%" not in txt,
        "zero-padded 001000":          "001000" in txt,
        # the MI negative SECTION: trailing minus on a negative value
        "MI trailing-minus 1000-":     "1000-" in txt,
        # the date UCase wrap: DD-MON-YYYY -> uppercase month
        "date uppercase 10-JAN-2026":  re.search(r"\d\d-JAN-2026", txt) is not None,
        # proper-case control unchanged
        "date proper 10-Jan-2026":     re.search(r"\d\d-Jan-2026", txt) is not None,
        "date digits 01/10/2026":      re.search(r"\d\d/\d\d/2026", txt) is not None,
    }
    failed = [k for k, ok in checks.items() if not ok]
    assert not failed, f"format mask(s) not 1:1 in rendered PDF: {failed}\n{txt[:400]}"
