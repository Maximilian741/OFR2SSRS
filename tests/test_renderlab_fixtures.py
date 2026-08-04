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
    # Chart archetype: a real <Chart> must render clean through the MS engine.
    # (Matrix is intentionally NOT here -- its LocalReport renders a documented
    # trailing-blank phantom that this blank-page gate would flag.)
    ROOT / "tests" / "fixtures" / "chart" / "source.xml",
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


def test_engine_render_has_no_painted_over_text():
    """Words from different text blocks must never materially intersect in
    the ENGINE-rendered PDF.

    This rail exists because every other gate missed the class: the mockup
    gates measure the browser preview, the layout auditor measures declared
    RDL geometry, and none of them measure what Microsoft's engine actually
    paints. A grant-status report shipped its run-date stamp straight
    across a stat-table label while the whole corpus scored SEAMLESS; the
    first sweep with this detector then found four more reports with
    painted-over text. Fixtures render here; the production corpus runs
    through the same detector in the audit rail.
    """
    import pathlib
    import sys as _sys

    root = pathlib.Path(__file__).resolve().parents[1]
    _sys.path.insert(0, str(root / "tools" / "renderlab"))
    try:
        import fitz  # noqa: F401
        from render import lib_ready
    except Exception:  # noqa: BLE001
        pytest.skip("renderlab not available")
    if not lib_ready():
        pytest.skip("ReportViewer DLLs not fetched")
    from converter import convert
    from render_overlap import pdf_overlaps, rdl_overlaps

    checked = 0
    for f in sorted((root / "samples" / "oracle").glob("*.xml")):
        try:
            rdl = convert(f.read_bytes())["rdl_xml"]
        except Exception:  # noqa: BLE001
            continue
        r = rdl_overlaps(rdl)
        if not r["ok"]:
            continue
        checked += 1
        assert not r["overlaps"], (
            f"{f.name}: text painted over text in the real render: "
            f"{r['overlaps'][:4]}")
    assert checked, "no fixture actually rendered"

    # PROVE THE GATE CAN FAIL, end to end through the SAME engine: a
    # minimal RDL with two DIFFERENT words stamped on the same coordinates
    # must be flagged -- a detector that cannot go red certifies nothing
    # (that is precisely how the previous blindness shipped). Duplicating
    # an existing box is NOT a valid corruption: identical glyphs at
    # identical positions merge into one MuPDF block, which the detector
    # rightly ignores.
    ns = "http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition"

    def _tb(name, word):
        return (
            f'<Textbox Name="{name}"><CanGrow>true</CanGrow>'
            "<Paragraphs><Paragraph><TextRuns><TextRun>"
            f"<Value>{word}</Value>"
            "<Style><FontSize>12pt</FontSize></Style>"
            "</TextRun></TextRuns></Paragraph></Paragraphs>"
            "<Top>1in</Top><Left>1in</Left>"
            "<Height>0.3in</Height><Width>2in</Width>"
            "<Style/></Textbox>")

    broken = (
        f'<Report xmlns="{ns}"><Body><ReportItems>'
        + _tb("TbA", "COLLIDING") + _tb("TbB", "PAINTOVER")
        + "</ReportItems><Height>2in</Height></Body>"
        "<Width>6in</Width><Page/></Report>")
    r_broken = rdl_overlaps(broken)
    assert r_broken["ok"], r_broken.get("log", "")[-300:]
    assert r_broken["overlaps"], (
        "detector failed to flag two different words rendered onto the "
        "same coordinates")
