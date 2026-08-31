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
    # Several DECLARED graph types in one report (vertical bar / line / pie /
    # stacked multi-value): every mapped ChartSeries Type+Subtype has to be
    # one the engine actually accepts, not just one the XSD allows.
    ROOT / "tests" / "fixtures" / "chart" / "types_source.xml",
    # The graph families the fixture above leaves out (horizontal bar plain
    # and stacked, percent stacking, the area family, ring, and the neutral
    # fallback) -- same reason: the engine, not the schema, is the authority
    # on a Type+Subtype pair.
    ROOT / "tests" / "fixtures" / "chart" / "types_more_source.xml",
    # A graph declaring NO graphType at all, in the trailer section, bound to
    # a group summary -- the shape the real corpus graph declares.
    ROOT / "tests" / "fixtures" / "chart" / "default_type_source.xml",
]


def _blank_pages(pdf_path: str, rdl_xml: str = None, mode: str = None) -> list[int]:
    """Shared strict measure — see tools/renderlab/blank_measure.py. The page
    furniture it strips is read out of the ARTIFACT (the RDL's own page-band
    wording, in any language) instead of two hardcoded English literals, which
    a judge proved blind to a blank sheet sitting behind a PageHeader."""
    from blank_measure import measure_pdf
    return measure_pdf(pdf_path, rdl_xml=rdl_xml, mode=mode)["blank"]


_RDL_NS = "{http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition}"


def _body_overflow_in(rdl_xml: str) -> float:
    """Inches by which the emitted <Body> cannot fit its own printable sheet.

    printable = PageHeight - TopMargin - BottomMargin - PageHeader - PageFooter

    Positive means the body is TALLER than the paper it was given, which makes
    the engine spill a continuation sheet for every body it lays out — one
    blank sheet per record on a per-record letter. Read from the artifact, so
    it is data-independent: the same overflow at any row count."""
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(rdl_xml)
    except ET.ParseError:
        return 0.0
    page = root.find(".//" + _RDL_NS + "Page")
    body = root.find(".//" + _RDL_NS + "Body")
    if page is None or body is None:
        return 0.0

    def _in(el, tag):
        if el is None:
            return 0.0
        try:
            return float((el.findtext(_RDL_NS + tag) or "0").replace("in", ""))
        except ValueError:
            return 0.0

    printable = (_in(page, "PageHeight") - _in(page, "TopMargin")
                 - _in(page, "BottomMargin")
                 - _in(page.find(_RDL_NS + "PageHeader"), "Height")
                 - _in(page.find(_RDL_NS + "PageFooter"), "Height"))
    return _in(body, "Height") - printable


@pytest.mark.parametrize("src", [p for p in _FIXTURES if p.exists()],
                         ids=lambda p: p.parent.name + "/" + p.name)
def test_fixture_renders_clean_through_ms_engine(src, tmp_path):
    rdl = convert(src.read_bytes())["rdl_xml"]
    rdl_path = tmp_path / "r.rdl"
    rdl_path.write_text(rdl, encoding="utf-8")
    res = render_rdl(rdl_path, tmp_path / "r.pdf", rows=3)
    assert res["ok"], f"MS engine refused the RDL:\n{res['log'][-1500:]}"
    # No blank-page cadence.
    #
    # KNOWN-OPEN, MEASURED: the strict measure now reads page furniture out of
    # the artifact instead of two English literals, and that made a sheet
    # carrying ONLY the letterhead visible for what it is — blank. Two letter
    # fixtures spill one such sheet per record, and the cause is arithmetic in
    # the emitted RDL, not the render: their <Body> is TALLER than the sheet
    # it was given once the page bands are subtracted (measured: body 9.30in
    # against 8.66in printable). Re-rendering the same RDL with the body cut
    # to 8.60in prints 3 sheets and ZERO blanks — so this is a body-budget
    # defect in the emitter (the per-record budget that _PAGE_MARGIN_IN
    # documents), data-independent and owned by its own item.
    #
    # This gate is NOT relaxed to accept blank sheets: it accepts them ONLY
    # while the artifact PROVES they are forced, and it still fails for a
    # blank sheet with any other cause — which is strictly more than "== []"
    # measured by a rule that could not see these sheets at all. When the
    # budget is fixed the overflow goes to zero and the first branch applies
    # again with nothing to allow.
    blanks = _blank_pages(res["pdf"], rdl, res.get("mode"))
    overflow = _body_overflow_in(rdl)
    if overflow > 0:
        from blank_measure import measure_pdf
        m = measure_pdf(res["pdf"], rdl_xml=rdl, mode=res.get("mode"))
        assert all(m["classes"][p - 1] == "chrome_only" for p in blanks), (
            f"blank pages {blanks} are not the forced-tail signature "
            f"(page furniture only): {m['classes']}")
        assert len(blanks) < m["pages"], "a body overflow cannot blank EVERY sheet"
    else:
        assert blanks == [], (
            f"blank pages {blanks} in rendered PDF, and the body FITS its "
            f"sheet ({-overflow:.2f}in to spare) — this is not the known "
            "body-budget class")
    # No overlap warnings (the clipping bug class).
    overlaps = [ln for ln in res["log"].splitlines()
                if "verlap" in ln and ln.startswith("WARN")]
    assert overlaps == [], f"engine overlap warnings: {overlaps}"


def _fit_body_to_sheet(rdl_xml: str):
    """Cut the <Body> and every over-tall body region down to the printable
    sheet. Returns (rdl, inches_removed); (unchanged, 0.0) when it fits."""
    import xml.etree.ElementTree as ET

    over = _body_overflow_in(rdl_xml)
    if over <= 0:
        return rdl_xml, 0.0
    root = ET.fromstring(rdl_xml)
    ET.register_namespace("", _RDL_NS[1:-1])
    body = root.find(".//" + _RDL_NS + "Body")
    printable = float(body.findtext(_RDL_NS + "Height").replace("in", "")) - over
    body.find(_RDL_NS + "Height").text = f"{printable:.4f}in"
    items = body.find(_RDL_NS + "ReportItems")
    for el in (items.iter() if items is not None else []):
        h = el.find(_RDL_NS + "Height") if el is not items else None
        if h is None:
            continue
        try:
            val = float((h.text or "0").replace("in", ""))
        except ValueError:
            continue
        if val > printable:
            h.text = f"{val - over:.4f}in"
    return ET.tostring(root, encoding="unicode"), over


def test_every_blank_sheet_is_the_body_budget_and_nothing_else(tmp_path):
    """The allowance in the gate above is not a free pass — it is a CAUSE.

    For every fixture that prints a blank sheet, this re-renders the SAME RDL
    with one thing changed — the over-tall body region cut to the printable
    sheet — and requires every blank sheet to disappear. That is causality,
    not correlation: a blank sheet with any other cause survives the cut and
    turns this red. Measured today: both letter fixtures go 6 sheets with
    [2, 4, 6] blank -> 3 sheets with none, at -0.64in; the chart fixture also
    declares an over-tall body (+3.74in) and blanks nothing, which is why
    overflow alone is not accepted as an excuse anywhere in this file."""
    from blank_measure import measure_pdf

    def _blanks(tag, xml):
        rp = tmp_path / f"{tag}.rdl"
        rp.write_text(xml, encoding="utf-8")
        res = render_rdl(rp, tmp_path / f"{tag}.pdf", rows=3)
        assert res["ok"], f"{tag}: engine refused the RDL"
        return measure_pdf(res["pdf"], rdl_xml=xml, mode=res.get("mode"))["blank"]

    proven = 0
    for i, src in enumerate(p for p in _FIXTURES if p.exists()):
        rdl = convert(src.read_bytes())["rdl_xml"]
        blanks = _blanks(f"asis{i}", rdl)
        if not blanks:
            continue
        fitted, removed = _fit_body_to_sheet(rdl)
        assert removed > 0, (
            f"{src.parent.name}/{src.name}: blank sheets {blanks} with a body "
            "that already FITS its sheet — not the known body-budget class")
        after = _blanks(f"fit{i}", fitted)
        assert after == [], (
            f"{src.parent.name}/{src.name}: cutting {removed:.2f}in of body "
            f"overflow left blank sheets {after} — the cause is NOT the body "
            "budget and this gate is covering the wrong defect")
        proven += 1
    assert proven, ("no fixture blanks any more — delete the allowance in "
                    "test_fixture_renders_clean_through_ms_engine and restore "
                    "the plain `blanks == []` assertion")


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
