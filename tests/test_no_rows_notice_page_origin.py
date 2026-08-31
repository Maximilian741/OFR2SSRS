"""THE NO-ROWS NOTICE MAY ONLY GO WHERE IT DISPLACES NOTHING.

``NoRowsMessage`` is not painted on one line. The engine reserves the data
region's WHOLE box for it, and it renders INSTEAD of the region rather than
alongside it. Those two measured facts pull in opposite directions and this
module pins the line between them:

  ROOM        the box has to fit the sheet the region RENDERS ON. Measuring
              it from the top of the BODY is wrong wherever the artifact
              DECLARES a page break above the region: past that break the
              region starts at the top of a fresh sheet, so a body-top
              reading asks about room that is not the room it renders in. A
              summary-header report was left with a trailing sheet carrying
              page furniture and nothing else because of exactly that.

  DISPLACEMENT a region with a STATIC row (a TablixMember with no <Group>)
              already prints its declaration when the query comes back empty.
              The notice would render in place of that declared caption band,
              trading it for one invented sentence. So the room relaxation is
              only ever granted to a region that prints NOTHING without rows.

Both legs get an A/B on the ONE declaration they key on, and both get a
mutation proof: with the leg disabled the gate here goes red, and (engine
permitting) the render loses either a sheet's only content or the declared
captions the notice replaced.
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from converter.generators.rdl import (
    _ensure_no_rows_message, _no_rows_region_prints_without_rows,
    _no_rows_notice_has_room, _no_rows_reserved_bottom, _page_origin_body_y,
    _printable_page_height, _q,
)

NS = "http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition"
REPO_ROOT = Path(__file__).resolve().parent.parent
_RENDERLAB = REPO_ROOT / "tools" / "renderlab"

# The measured shape, reduced to its arithmetic: a leading block that fills
# most of a body two sheets tall, a page break at its end, and a data region
# declared just below it. Body-top the region's box ends at 15.65in; on the
# sheet the break gives it, it ends at 2.23in of 8.82in of room.
_LEAD_TOP, _LEAD_HEIGHT = 0.0, 13.42
_REGION_TOP, _ROW_HEIGHT = 13.52, 2.13


def _textbox(name, value, top="0in"):
    """A textbox in the child order the RDL 2008 schema fixes."""
    return ('<Textbox Name="%s"><Paragraphs><Paragraph><TextRuns><TextRun>'
            '<Value>%s</Value><Style /></TextRun></TextRuns></Paragraph>'
            '</Paragraphs><Style /><CanGrow>true</CanGrow><Top>%s</Top>'
            '<Left>0.1in</Left><Width>6.5in</Width><Height>0.25in</Height>'
            '</Textbox>' % (name, value, top))


def _report(break_location="End", region_top=_REGION_TOP,
            row_height=_ROW_HEIGHT, static_row=False, lead_height=_LEAD_HEIGHT):
    """One leading block + one dataset-bound region, with every knob the rules
    key on exposed as a parameter so each test can flip exactly one.

    The leading block carries a static line of its own, because a report where
    NOTHING prints without rows takes the emitter's last-resort branch (a
    wholly blank sheet being worse than a spilling notice) and that branch
    would answer every question below before these rules were consulted.
    It carries a line on each of the two sheets its height spans, so a
    sheet of its own empty tail is never mistaken for the sheet under
    test."""
    brk = ("<PageBreak><BreakLocation>%s</BreakLocation></PageBreak>"
           % break_location) if break_location else ""
    members = ""
    rows = ""
    if static_row:
        members += "<TablixMember />"
        rows += ("<TablixRow><Height>0.47in</Height><TablixCells><TablixCell>"
                 "<CellContents>" + _textbox("Cap", "Column caption") +
                 "</CellContents></TablixCell></TablixCells></TablixRow>")
    members += '<TablixMember><Group Name="Details" /></TablixMember>'
    rows += ("<TablixRow><Height>%.3fin</Height><TablixCells><TablixCell>"
             "<CellContents>" % row_height + _textbox("Val", "=Fields!C1.Value")
             + "</CellContents></TablixCell></TablixCells></TablixRow>")
    return ET.fromstring(
        '<Report xmlns="%s"><Body><ReportItems>'
        '<Rectangle Name="Lead"><ReportItems>%s</ReportItems>'
        '<Top>%.3fin</Top><Left>0in</Left><Width>7in</Width>'
        '<Height>%.3fin</Height>%s</Rectangle>'
        '<Tablix Name="Region"><TablixBody><TablixColumns>'
        '<TablixColumn><Width>7in</Width></TablixColumn></TablixColumns>'
        '<TablixRows>%s</TablixRows></TablixBody>'
        '<TablixColumnHierarchy><TablixMembers><TablixMember />'
        '</TablixMembers></TablixColumnHierarchy>'
        '<TablixRowHierarchy><TablixMembers>%s</TablixMembers>'
        '</TablixRowHierarchy>'
        '<DataSetName>Q_Data</DataSetName>'
        '<Top>%.3fin</Top><Left>0in</Left><Width>7in</Width></Tablix>'
        '</ReportItems><Height>15.52in</Height></Body><Width>7.9in</Width>'
        '<Page><PageHeight>11in</PageHeight><PageWidth>8.6in</PageWidth>'
        '<TopMargin>0.75in</TopMargin><BottomMargin>0.5in</BottomMargin>'
        '<LeftMargin>0.25in</LeftMargin><RightMargin>0.31in</RightMargin>'
        '<PageHeader><Height>0.73in</Height></PageHeader>'
        '<PageFooter><Height>0.1974in</Height></PageFooter>'
        '</Page></Report>'
        % (NS, (_textbox("LeadLine1", "Leading block line one", top="0.1in")
                + _textbox("LeadLine2", "Leading block line two",
                           top="%.2fin" % (lead_height - 1.0))),
           _LEAD_TOP, lead_height, brk, rows, members, region_top))


def _region(root):
    return next(t for t in root.iter(_q("Tablix"))
                if t.get("Name") == "Region")


def _placed(root):
    _ensure_no_rows_message(root)
    return _region(root).find(_q("NoRowsMessage")) is not None


# ---------------------------------------------------------------------------
# the arithmetic the two legs are decided on
# ---------------------------------------------------------------------------

def test_fixture_arithmetic_is_the_measured_shape():
    """If this drifts, every assertion below is testing something else."""
    root = _report()
    fits = _printable_page_height(root)
    assert 8.80 < fits < 8.83, fits
    # body-top: past the sheet. On its own sheet: comfortably inside it.
    assert _no_rows_reserved_bottom(root, _region(root)) == pytest.approx(
        _REGION_TOP + _ROW_HEIGHT, abs=5e-4)
    assert _no_rows_reserved_bottom(root, _region(root)) > fits
    assert _REGION_TOP - _LEAD_HEIGHT + _ROW_HEIGHT < fits


# ---------------------------------------------------------------------------
# LEG 1 — a DECLARED page break puts the region at a page origin
# ---------------------------------------------------------------------------

def test_page_origin_comes_from_a_declared_break_and_nothing_else():
    """A/B on ONE declaration: the leading block's BreakLocation.

    ``End`` starts a fresh sheet after that block, so the region below it
    begins at the block's bottom. With no break declared the origin stays at
    the top of the body — where a page boundary merely FALLS is not a
    declaration and must not move it."""
    for loc, expect in (("End", _LEAD_HEIGHT), ("StartAndEnd", _LEAD_HEIGHT),
                        ("Start", 0.0), ("", 0.0)):
        root = _report(break_location=loc)
        assert _page_origin_body_y(root, _region(root)) == pytest.approx(
            expect, abs=5e-4), loc


def test_a_regions_own_start_break_is_its_own_origin():
    """The region declaring Start on itself begins a sheet the same way."""
    root = _report(break_location="")
    region = _region(root)
    region.append(ET.fromstring(
        '<PageBreak xmlns="%s"><BreakLocation>Start</BreakLocation>'
        '</PageBreak>' % NS))
    assert _page_origin_body_y(root, region) == pytest.approx(
        _REGION_TOP, abs=5e-4)


def test_an_ancestors_end_break_fires_after_the_region_not_before_it():
    """A container's own End break happens once the container — region and
    all — has rendered, so it can never be the region's page origin."""
    root = _report(break_location="")
    lead = next(r for r in root.iter(_q("Rectangle")))
    region = _region(root)
    root.find(_q("Body")).find(_q("ReportItems")).remove(region)
    lead.find(_q("ReportItems")).append(region)
    lead.append(ET.fromstring(
        '<PageBreak xmlns="%s"><BreakLocation>End</BreakLocation>'
        '</PageBreak>' % NS))
    assert _page_origin_body_y(root, region) == 0.0


def test_a_break_declared_inside_the_region_is_not_its_origin():
    """A group that ends a page does so once each instance has printed, so
    it opens sheets AFTER the region starts, never before it."""
    root = _report(break_location="")
    region = _region(root)
    group = next(region.find(_q("TablixRowHierarchy")).iter(_q("Group")))
    group.append(ET.fromstring('<PageBreak xmlns="%s"><BreakLocation>End'
                               '</BreakLocation></PageBreak>' % NS))
    assert _page_origin_body_y(root, region) == 0.0
    assert not _placed(root)


def test_notice_reaches_a_region_that_starts_its_own_sheet():
    """The regression this closes: body-top says the box overruns the paper,
    the declaration says the region owns a fresh sheet, and withholding left
    that sheet carrying page furniture and nothing else."""
    assert _placed(_report(break_location="End"))
    # ...and the conservative reading still governs where nothing is declared
    assert not _placed(_report(break_location=""))


def test_the_relaxation_does_not_hand_out_room_that_is_not_there():
    """A region that outruns the sheet EVEN ON ITS OWN PAGE is still refused:
    the relaxation moves the origin, it does not skip the fit test."""
    assert not _placed(_report(break_location="End", row_height=9.5))


# ---------------------------------------------------------------------------
# LEG 2 — the notice may not replace a declared static row
# ---------------------------------------------------------------------------

def test_static_row_detector_reads_the_row_hierarchy():
    assert not _no_rows_region_prints_without_rows(_region(_report()))
    assert _no_rows_region_prints_without_rows(
        _region(_report(static_row=True)))


def test_notice_is_withheld_where_it_would_replace_a_declared_static_row():
    """A/B on ONE declaration: the group-less TablixMember.

    Same geometry, same declared break, same everything — but this region
    prints a caption band when the query comes back empty, and the notice
    renders INSTEAD of the region rather than under it. A declared band is
    worth more than an invented sentence, so the region keeps its band."""
    assert _placed(_report(break_location="End", static_row=False))
    assert not _placed(_report(break_location="End", static_row=True))


def test_regions_the_body_top_reading_already_admits_are_untouched():
    """The displacement test gates the RELAXATION only. A region whose box
    fits measured from the top of the body is admitted exactly as before,
    static row or not — that wider question is older than this rule and
    moves most of the corpus, so it is not silently answered here."""
    for static in (False, True):
        root = _report(break_location="", region_top=0.5, row_height=0.5,
                       lead_height=0.4, static_row=static)
        assert _no_rows_reserved_bottom(root, _region(root)) < \
            _printable_page_height(root)
        assert _placed(root), static


# ---------------------------------------------------------------------------
# MUTATION PROOFS — each leg removed, each gate watched going red
# ---------------------------------------------------------------------------

def test_mutation_body_top_only_leaves_the_region_without_its_notice():
    """Leg 1 removed (origin pinned to the top of the body): the region that
    owns a sheet loses its notice again, which is the chrome-only sheet."""
    import converter.generators.rdl as R
    real = R._page_origin_body_y
    R._page_origin_body_y = lambda *a, **k: 0.0
    try:
        assert not _placed(_report(break_location="End"))
    finally:
        R._page_origin_body_y = real
    assert _placed(_report(break_location="End")), "mutation was not restored"


def test_mutation_ungated_relaxation_eats_the_declared_caption_band():
    """Leg 2 removed (nothing is deemed to print without rows): the notice
    lands on the region whose declared band it replaces. This is the exact
    behaviour that got the wider page-CROSSING rule rejected, and the reason
    the relaxation carries the displacement test with it."""
    import converter.generators.rdl as R
    real = R._no_rows_region_prints_without_rows
    R._no_rows_region_prints_without_rows = lambda *a, **k: False
    try:
        assert _placed(_report(break_location="End", static_row=True))
    finally:
        R._no_rows_region_prints_without_rows = real
    assert not _placed(_report(break_location="End", static_row=True)), \
        "mutation was not restored"


# ---------------------------------------------------------------------------
# THE ENGINE LEG — what a reader actually gets on the sheet
# ---------------------------------------------------------------------------

def _engine():
    if sys.platform != "win32":
        pytest.skip("MS ReportViewer engine harness is Windows-only")
    if str(_RENDERLAB) not in sys.path:
        sys.path.insert(0, str(_RENDERLAB))
    try:
        from blank_measure import measure_pdf
        from render import lib_ready, render_rdl
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"renderlab import failed: {exc}")
    if not lib_ready():
        pytest.skip("ReportViewer DLLs missing — engine leg skipped, not passed")
    return render_rdl, measure_pdf


_SCAFFOLD = (
    '<DataSources><DataSource Name="SharedDataSource">'
    '<DataSourceReference>SharedDataSource</DataSourceReference>'
    '</DataSource></DataSources>'
    '<DataSets><DataSet Name="Q_Data"><Query>'
    '<DataSourceName>SharedDataSource</DataSourceName>'
    '<CommandText>select c1 from t</CommandText></Query>'
    '<Fields><Field Name="C1"><DataField>C1</DataField>'
    '<rd:TypeName xmlns:rd="http://schemas.microsoft.com/SQLServer/reporting/'
    'reportdesigner">System.String</rd:TypeName></Field></Fields>'
    '</DataSet></DataSets>')


def _renderable(root):
    """The fixture plus the minimum SSRS scaffolding a render needs."""
    xml = ET.tostring(root, encoding="unicode")
    return xml.replace("<Body>", _SCAFFOLD + "<Body>", 1)


def _render_zero_rows(tmp_path, root, tag):
    render_rdl, measure_pdf = _engine()
    rp = tmp_path / f"{tag}.rdl"
    rp.write_text(_renderable(root), encoding="utf-8")
    res = render_rdl(rp, tmp_path / f"{tag}.pdf", rows=0)
    assert res.get("ok"), f"{tag}: engine refused the fixture: {str(res)[:300]}"
    m = measure_pdf(res["pdf"], rdl_xml=rp.read_text(encoding="utf-8"),
                    mode=res.get("mode"))
    text = " ".join(m["texts"])
    return m, text


def test_engine_the_notice_lands_on_the_sheet_the_break_gave_it(tmp_path):
    """The measured claim, on the engine: the notice fits the sheet the
    declared break opens. It does NOT spill to a further sheet (which is what
    withholding was protecting against), and the sheet that was furniture-only
    now carries the notice."""
    root = _report(break_location="End")
    assert _placed(root)
    m, text = _render_zero_rows(tmp_path, root, "own_sheet")
    assert "No data was returned" in text, text[:400]
    assert not m["blank"], f"the notice manufactured a blank sheet: {m['blank']}"


def test_engine_a_notice_renders_instead_of_a_declared_caption_band(tmp_path):
    """The dialect fact leg 2 exists for, measured rather than asserted: with
    the notice present the declared caption band is GONE from the sheet. The
    blank-page gate cannot see this loss — both arms are 'content' — so it is
    checked here on the words the reader actually gets."""
    kept = _report(break_location="End", static_row=True)
    assert not _placed(kept), "fixture precondition: the band must be kept"
    _, kept_text = _render_zero_rows(tmp_path, kept, "band_kept")
    assert "Column caption" in kept_text, kept_text[:400]
    assert "No data was returned" not in kept_text

    import converter.generators.rdl as R
    real = R._no_rows_region_prints_without_rows
    R._no_rows_region_prints_without_rows = lambda *a, **k: False
    try:
        lost = _report(break_location="End", static_row=True)
        assert _placed(lost)
    finally:
        R._no_rows_region_prints_without_rows = real
    _, lost_text = _render_zero_rows(tmp_path, lost, "band_lost")
    assert "No data was returned" in lost_text, lost_text[:400]
    assert "Column caption" not in lost_text, (
        "the mutation proof is dead: the notice no longer displaces the "
        "declared band, so leg 2 is guarding nothing")


# ---------------------------------------------------------------------------
# WIRING — the emitter obeys its own predicate on every artifact it ships
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("src", sorted(
    (REPO_ROOT / "samples" / "oracle").glob("*.xml")),
    ids=lambda p: p.name)
def test_every_emitted_notice_has_room_on_the_sheet_it_renders_on(src):
    """A rule the emitter does not apply is not a rule."""
    from converter import convert

    root = ET.fromstring(convert(src.read_bytes(), src.name)["rdl_xml"])
    fits = _printable_page_height(root)
    for tx in root.iter(_q("Tablix")):
        if tx.find(_q("NoRowsMessage")) is None:
            continue
        assert _no_rows_notice_has_room(root, tx, None, fits), (
            f"{tx.get('Name')} carries a notice its sheet has no room for")
