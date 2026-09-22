# -*- coding: utf-8 -*-
"""(A) BY PLACE — a page band's declared wording is furniture WHERE ITS INK
LIES, never because the wording turns up somewhere on the sheet.

THE HOLE. tools/renderlab/blank_measure.py judged one declaration by two
standards. Rule (D) — undecodable ink — asked WHERE the ink lies: inside the
strip the RDL reserves for a page band, or on a line the document repeats, it
is furniture; anywhere else it is content. Rule (A) — declared band wording —
asked nothing about place: it deleted the band's wording from ANY line on the
sheet, case-folded, and a line the deletion emptied was furniture wherever the
report had printed it. So a page whose BODY legitimately repeats a header
phrase — a section heading that restates the running title, a caption that
is a piece of it — had that content silently stripped, and a sheet carrying
nothing else read as blank.

That is script-dependent in effect, not only inconsistent: the wording test
only reaches ink that DECODES, and the ReportViewer PDF writer embeds
non-Latin subsets with no ToUnicode CMap. Measured on the engine, one
hand-built artifact whose only variable is the language of its wording, nine
scripts x four row counts, BEFORE the fix:

    the sheet whose body prints the declared title verbatim, and the sheet
    printing a trailing piece of it:
        ascii, accented Latin   chrome_only  -> reported BLANK
        Greek, Cyrillic, Hebrew, Arabic, CJK, Hangul, Thai   content
    at 0, 1, 3 and 25 rows alike.

The same visual state, opposite verdicts, decided by whether the glyphs
happened to decode — law L-A broken in the measure itself.

THE RULE NOW. A line whose ink the geometry LOCATED is judged by place
alone, the same test rule (D) makes: inside a reserved strip -> furniture; on
a line the document repeats -> furniture; anywhere else -> content, whatever
it says. The declared WORDING decides only a line no geometry located (a
caller with no PyMuPDF; an extracted line the PDF's own line boxes split
differently) — the one place it is the only evidence there is — and that
fallback is stated as weaker, not hidden. The same standard applies in the
second place the wording was consulted, ``_mark_is_furniture``, so a body
line that repeats the band's wording is a content MARK too: it carries ink
coverage and it is the sheet's own wording in every script.

WHAT THIS FILE PROVES, both laws, on the engine:

  L-A  nine scripts, one verdict — the title-repeating sheet and the
       piece-repeating sheet are content in all nine, and the sheet that
       carries ONLY the page bands is still blank in all nine (the furniture
       strip is not weakened; it is moved onto the declaration's strip).
  L-B  the same sheets at 0, 1, 3 and 25 rows — the shape never moves the
       verdict, so no arm of this gate is inert.
  MUTATION  put the wording-only judgement back (declare that no line was
       located) and the verdict SPLITS BY SCRIPT — blank exactly in the two
       scripts whose glyphs decode, content in the seven whose ink rule (D)
       owns — at every row count. A mutation that changed nothing, or
       changed every script the same way, would prove the place standard is
       not what is doing the work. The second site is mutated on its own:
       re-applying the wording test to located MARKS zeroes the sheet's ink
       coverage in the decoding scripts only.

The engine-free tests at the foot pin the rule on hand-built geometry —
the same line, first outside the reserved strip, then inside it, then with
no geometry at all — so the rule itself, and its documented fallback, are
tested on any machine.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_RENDERLAB = Path(__file__).resolve().parent.parent / "tools" / "renderlab"
if str(_RENDERLAB) not in sys.path:
    sys.path.insert(0, str(_RENDERLAB))

import blank_measure as bm  # noqa: E402

try:                                                       # noqa: SIM105
    from render import lib_ready, render_rdl  # noqa: E402
    _ENGINE = bool(lib_ready()) and sys.platform == "win32"
except Exception:                                          # noqa: BLE001
    _ENGINE = False

_needs_engine = pytest.mark.skipif(
    not _ENGINE, reason="ReportViewer engine not available on this machine")

_NS = "http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition"
SHAPES = (0, 1, 3, 25)

# script: (running title, footer stamp, cover line, a trailing PIECE of the
# title, the face its glyphs need). Invented wording, one bundle per script;
# the faces are the ones the East-Asian and Thai bundles need to print
# glyphs rather than tofu.
_BAND = {
    "ascii": ("Monthly Observation Report", "Printed automatically",
              "Total measured this period 41205", "Observation Report",
              "Arial"),
    "latin_accents": ("Informe Mensual de Observación",
                      "Impreso automáticamente",
                      "Total de mediciones del período 41205",
                      "de Observación", "Arial"),
    "greek": ("Μηνιαία Αναφορά Παρατηρήσεων", "Εκτυπώθηκε αυτόματα",
              "Σύνολο μετρήσεων περιόδου 41205", "Αναφορά Παρατηρήσεων",
              "Arial"),
    "cyrillic": ("Ежемесячный отчёт наблюдений", "Отчёт подготовлен",
                 "Итого измерений за период 41205", "отчёт наблюдений",
                 "Arial"),
    "hebrew": ("דוח ניטור חודשי", "הודפס אוטומטית",
               "סך המדידות לתקופה 41205", "ניטור חודשי", "Arial"),
    "arabic": ("تقرير الرصد الشهري", "طُبع تلقائيا",
               "مجموع القياسات لهذه الفترة 41205", "الرصد الشهري", "Arial"),
    "cjk": ("環境監視管理課月次観測報告書年報", "自動出力",
            "当期測定値合計 41205", "観測報告書年報", "MS Gothic"),
    "hangul": ("월간 관측 보고서 환경 관리과", "자동 출력",
               "당기 측정값 합계 41205", "환경 관리과", "Malgun Gothic"),
    "thai": ("รายงานการตรวจวัดรายเดือน", "พิมพ์อัตโนมัติ",
             "ยอดรวมการวัดในงวดนี้ 41205", "รายเดือน", "Leelawadee UI"),
}

# The scripts whose glyphs the PDF writer encodes in WinAnsi, so they DECODE
# and reach the text rules — the only scripts the wording test could ever
# touch. A measured property of the render, asserted below on every arm.
_DECODING_SCRIPTS = ("ascii", "latin_accents")


def _esc(v):
    return v.replace("&", "&amp;").replace("<", "&lt;")


def _tb(name, value, top, face, height="0.30in"):
    return (f'<Textbox Name="{name}"><Top>{top}</Top><Left>0.1in</Left>'
            f'<Height>{height}</Height><Width>6in</Width><CanGrow>true</CanGrow>'
            f'<Paragraphs><Paragraph><TextRuns><TextRun><Value>{_esc(value)}'
            f'</Value><Style><FontSize>11pt</FontSize><FontFamily>{face}'
            f'</FontFamily></Style></TextRun></TextRuns></Paragraph>'
            f'</Paragraphs></Textbox>')


def _sheet(name, inner, top):
    return (f'<Rectangle Name="{name}"><Top>{top}</Top><Left>0in</Left>'
            f'<Height>0.9in</Height><Width>6.5in</Width>'
            '<PageBreak><BreakLocation>Start</BreakLocation></PageBreak>'
            f'<ReportItems>{inner}</ReportItems></Rectangle>')


def _records(face, top):
    """One record to a sheet: a tablix grouped on the record with a page
    break at the START of each group, so the row count adds record sheets
    between the cover and the sheets under test without an empty sheet of
    its own (an End break followed by the next item's Start break is NOT
    collapsed by the engine — measured, it printed a furniture-only sheet
    between the records and the heading sheet)."""
    cell = _tb("R_VALUE", "=Fields!ITEM_CODE.Value", "0.1in", face)
    return (f'<Tablix Name="Tx"><TablixBody><TablixColumns><TablixColumn>'
            '<Width>6.5in</Width></TablixColumn></TablixColumns><TablixRows>'
            '<TablixRow><Height>0.6in</Height><TablixCells><TablixCell>'
            f'<CellContents><Rectangle Name="Tx_R"><ReportItems>{cell}'
            '</ReportItems><Top>0in</Top><Left>0in</Left><Width>6.5in</Width>'
            '<Height>0.6in</Height></Rectangle></CellContents></TablixCell>'
            '</TablixCells></TablixRow></TablixRows></TablixBody>'
            '<TablixColumnHierarchy><TablixMembers><TablixMember/>'
            '</TablixMembers></TablixColumnHierarchy><TablixRowHierarchy>'
            '<TablixMembers><TablixMember><Group Name="Tx_G">'
            '<GroupExpressions><GroupExpression>=Fields!ITEM_CODE.Value'
            '</GroupExpression></GroupExpressions><PageBreak><BreakLocation>'
            'Start</BreakLocation></PageBreak></Group><TablixMembers>'
            '<TablixMember/></TablixMembers></TablixMember></TablixMembers>'
            '</TablixRowHierarchy><DataSetName>Q_MAIN</DataSetName>'
            f'<Top>{top}</Top><Left>0in</Left><Width>6.5in</Width></Tablix>')


def _report(script):
    """A cover sheet, one sheet per record, then three sheets under test:

      HEADING  the body prints the declared running title verbatim
      PIECE    the body prints a trailing piece of it
      EMPTY    nothing but the page bands — the blank sheet the strip must
               still find, or the fix has weakened the gate

    The header declares PrintOnFirstPage=false (six of the shipping RDLs do),
    so the running title is not on every sheet and rule (B) cannot claim
    the body's copy by repetition: the ONLY evidence about it is the
    declaration, which is the rule under test."""
    title, stamp, cover, piece, face = _BAND[script]
    header = ('<PageHeader><Height>0.5in</Height>'
              '<PrintOnFirstPage>false</PrintOnFirstPage>'
              '<PrintOnLastPage>true</PrintOnLastPage><ReportItems>'
              + _tb("H_TITLE", title, "0.05in", face)
              + '</ReportItems></PageHeader>')
    footer = ('<PageFooter><Height>0.4in</Height>'
              '<PrintOnFirstPage>true</PrintOnFirstPage>'
              '<PrintOnLastPage>true</PrintOnLastPage><ReportItems>'
              + _tb("F_STAMP", stamp, "0.05in", face)
              + '</ReportItems></PageFooter>')
    body = (_tb("B_COVER", cover, "0.10in", face)
            + _records(face, "0.6in")
            + _sheet("S_HEADING", _tb("B_HEADING", title, "0.05in", face),
                     "1.4in")
            + _sheet("S_PIECE", _tb("B_PIECE", piece, "0.05in", face),
                     "2.4in")
            + _sheet("S_EMPTY", _tb("B_EMPTY", "", "0.05in", face), "3.4in"))
    return ('<?xml version="1.0" encoding="utf-8"?>'
            f'<Report xmlns="{_NS}">'
            '<DataSources><DataSource Name="DS"><ConnectionProperties>'
            '<DataProvider>SQL</DataProvider><ConnectString>x</ConnectString>'
            '</ConnectionProperties></DataSource></DataSources>'
            '<DataSets><DataSet Name="Q_MAIN"><Query><DataSourceName>DS'
            '</DataSourceName><CommandText>SELECT 1</CommandText></Query>'
            '<Fields><Field Name="ITEM_CODE"><DataField>ITEM_CODE</DataField>'
            '</Field></Fields></DataSet></DataSets>'
            f'<Body><ReportItems>{body}</ReportItems>'
            '<Height>4.4in</Height><Style/></Body><Width>6.5in</Width>'
            f'<Page>{header}{footer}'
            '<PageHeight>11in</PageHeight><PageWidth>8.5in</PageWidth>'
            '<LeftMargin>0.5in</LeftMargin><RightMargin>0.5in</RightMargin>'
            '<TopMargin>0.5in</TopMargin><BottomMargin>0.5in</BottomMargin>'
            '</Page></Report>')


# The three sheets under test are the LAST three of every render, whatever
# the row count put in front of them.
_UNDER_TEST = ("heading", "piece", "empty")
_EXPECTED = ("content", "content", "chrome_only")


@pytest.fixture(scope="module")
def engine_place(tmp_path_factory):
    """Nine scripts x four row counts, rendered once."""
    if not _ENGINE:
        pytest.skip("ReportViewer engine not available on this machine")
    work = tmp_path_factory.mktemp("declared_by_place")
    out = {}
    for script in _BAND:
        rdl = _report(script)
        path = work / f"{script}.rdl"
        path.write_text(rdl, encoding="utf-8")
        for rows in SHAPES:
            pdf = work / f"{script}_{rows}.pdf"
            res = render_rdl(path, pdf, rows=rows)
            assert res.get("ok"), (
                f"{script}/rows={rows} did not render: {res.get('log', '')[-300:]}")
            out[(script, rows)] = (pdf, rdl, res.get("mode"))
    return out


def _measure(engine_place, script, rows):
    pdf, rdl, mode = engine_place[(script, rows)]
    m = bm.measure_pdf(pdf, rdl_xml=rdl, mode=mode)
    assert m["pages"] == rows + 4, (
        f"{script}/rows={rows}: expected a cover, {rows} record sheet(s) and "
        f"three sheets under test — got {m['pages']} pages")
    return m


def _under_test(m):
    return dict(zip(_UNDER_TEST, m["classes"][-3:]))


# ---------------------------------------------------------------------------
# L-A and L-B: one verdict, nine scripts, four row counts
# ---------------------------------------------------------------------------

@_needs_engine
@pytest.mark.parametrize("rows", SHAPES)
@pytest.mark.parametrize("script", sorted(_BAND))
def test_engine_a_body_line_repeating_the_band_wording_is_content(
        engine_place, script, rows):
    """The sheet whose body restates the running title is a sheet with
    content on it, and so is the sheet printing a piece of the title — while
    the sheet that carries ONLY the page bands is still blank. Same
    artifact, same declaration; place is what separates them."""
    pdf, rdl, _mode = engine_place[(script, rows)]
    m = _measure(engine_place, script, rows)
    title = _BAND[script][0]
    assert bm._squash(title) in bm.declared_chrome_fragments(rdl), (
        f"{script}: the fixture's band must DECLARE the wording the body "
        "repeats, or this measures nothing")

    assert _under_test(m) == dict(zip(_UNDER_TEST, _EXPECTED)), (
        f"{script}/rows={rows}: {_under_test(m)} — residuals "
        f"{m['residuals'][-3:]!r}")
    assert m["blank"] == [m["pages"]], (
        f"{script}/rows={rows}: only the band-only sheet is blank — "
        f"{m['blank']}")


@_needs_engine
@pytest.mark.parametrize("script", sorted(_BAND))
def test_engine_the_wording_reaches_the_text_rules_only_where_it_decodes(
        engine_place, script):
    """The mechanism, pinned, so the tests above cannot pass for a reason
    that has nothing to do with the hole: in the two scripts whose glyphs
    decode the body's copy of the title IS on the extracted text the wording
    rule reads; in the other seven it is undecodable ink and rule (D)'s
    geometry owns it. Both halves are asserted so a fixture that stopped
    reproducing either would say so."""
    m = _measure(engine_place, script, 3)
    heading = m["pages"] - 3
    key = bm._squash(_BAND[script][0])
    raw = bm._squash(m["raw_texts"][heading - 1])
    undecodable = m["ink"][heading - 1]["undecodable"]
    if script in _DECODING_SCRIPTS:
        assert key in raw, (
            f"{script}: the body's copy of the title must reach the text "
            f"rules — {m['raw_texts'][heading - 1]!r}")
        assert not undecodable, f"{script}: this script's ink must decode"
    else:
        assert key not in raw, (
            f"{script}: the title DID survive extraction, so the wording "
            "rule can reach this arm and the script split it proves is gone")
        assert undecodable, f"{script}: no undecodable ink on the sheet"


@_needs_engine
@pytest.mark.parametrize("rows", SHAPES)
def test_engine_one_verdict_in_every_script(engine_place, rows):
    """L-A, asserted directly: the three sheets get one class triple across
    all nine scripts — and the RIGHT one, so a measure gone blind everywhere
    could not satisfy it."""
    triples = {s: tuple(_under_test(_measure(engine_place, s, rows)).values())
               for s in sorted(_BAND)}
    assert set(triples.values()) == {_EXPECTED}, (
        f"rows={rows}: the verdict depends on the SCRIPT — {triples}")


@_needs_engine
@pytest.mark.parametrize("script", sorted(_BAND))
def test_engine_the_verdict_does_not_move_with_the_row_count(engine_place,
                                                             script):
    """L-B, asserted directly: the same three sheets, four row counts, one
    class triple. The record sheets in front of them are the only thing the
    shape changes."""
    triples = {rows: tuple(_under_test(_measure(engine_place, script,
                                                 rows)).values())
               for rows in SHAPES}
    assert set(triples.values()) == {_EXPECTED}, (
        f"{script}: the verdict depends on the ROW COUNT — {triples}")


# ---------------------------------------------------------------------------
# MUTATION: put the wording-only judgement back
# ---------------------------------------------------------------------------

def _classes_by_script(engine_place, rows):
    return {s: _under_test(_measure(engine_place, s, rows))
            for s in sorted(_BAND)}


@_needs_engine
@pytest.mark.parametrize("rows", SHAPES)
def test_mutation_judging_located_ink_by_wording_splits_the_verdict_by_script(
        engine_place, rows, monkeypatch):
    """Declare that no line was located — ``seated_keys`` empty — and every
    line falls to the wording fallback, which is exactly the pre-fix rule.
    The heading and piece sheets must go BLANK in the two scripts whose
    glyphs decode and stay content in the seven whose ink rule (D) judges by
    place: a split along the decoding line, at every row count. The
    band-only sheet stays blank throughout — the mutation reaches nothing
    it should not."""
    honest = _classes_by_script(engine_place, rows)
    assert all(tuple(v.values()) == _EXPECTED for v in honest.values()), (
        f"rows={rows}: precondition — {honest}")

    monkeypatch.setattr(bm, "seated_keys", lambda *_a, **_k: frozenset())
    mutated = _classes_by_script(engine_place, rows)

    blanked = {s for s, v in mutated.items()
               if (v["heading"], v["piece"]) == ("chrome_only", "chrome_only")}
    kept = {s for s, v in mutated.items()
            if (v["heading"], v["piece"]) == ("content", "content")}
    assert blanked == set(_DECODING_SCRIPTS), (
        f"rows={rows}: judged by wording, the body's copy of the title must "
        f"read blank exactly where the glyphs decode — {mutated}")
    assert kept == set(_BAND) - set(_DECODING_SCRIPTS), (
        f"rows={rows}: the seven scripts whose ink does not decode are rule "
        f"(D)'s and must stay content under the mutation — {mutated}")
    assert all(v["empty"] == "chrome_only" for v in mutated.values()), (
        f"rows={rows}: the band-only sheet must stay blank either way")


def _wording_judged(real, fragments):
    """``_mark_is_furniture`` with the wording test PUT BACK — the pre-fix
    rule for located marks, re-applied over the shipped place test
    (``real``, captured once so the per-script mutants never chain)."""

    def mutant(key, repeated, in_band):
        if real(key, repeated, in_band):
            return True
        rest = bm._strip_all(key, fragments)
        if rest != key and not rest:
            return True
        return len(key) >= bm.LINE_FLOOR and any(key in f for f in fragments)
    return mutant


@_needs_engine
@pytest.mark.parametrize("rows", SHAPES)
def test_mutation_a_wording_test_on_located_marks_splits_the_coverage(
        engine_place, rows, monkeypatch):
    """The second site, mutated on its own. With the shipped rule the
    heading sheet carries a content MARK in every script — its ink coverage
    is positive. Re-apply the wording test to located marks and that
    coverage drops to zero in the two decoding scripts and nowhere else: the
    sheet would say nothing of its own in English and something in Greek."""
    # The heading sheet is the third from last: cover, ``rows`` record
    # sheets, HEADING, PIECE, EMPTY — so its 0-based index is pages - 3.
    heading = rows + 4 - 3
    honest = {s: _measure(engine_place, s, rows)["coverage"][heading]
              for s in sorted(_BAND)}
    assert all(c > 0 for c in honest.values()), (
        f"rows={rows}: precondition — the body's copy of the title is a "
        f"content mark in every script: {honest}")

    real = bm._mark_is_furniture
    mutated = {}
    for script in sorted(_BAND):
        _pdf, rdl, _mode = engine_place[(script, rows)]
        monkeypatch.setattr(
            bm, "_mark_is_furniture",
            _wording_judged(real, bm.declared_chrome_fragments(rdl)))
        mutated[script] = _measure(engine_place, script,
                                   rows)["coverage"][heading]
    zeroed = {s for s, c in mutated.items() if c == 0}
    assert zeroed == set(_DECODING_SCRIPTS), (
        f"rows={rows}: a wording test on located marks must zero the sheet's "
        f"coverage exactly where the glyphs decode — {mutated}")


# ---------------------------------------------------------------------------
# The rule itself, on hand-built geometry (engine-free)
# ---------------------------------------------------------------------------

_RDL = ("<Report xmlns='http://schemas.microsoft.com/sqlserver/reporting/"
        "2008/01/reportdefinition'><Page><PageHeader><Height>0.4in</Height>"
        "<ReportItems><Textbox Name='T'><Paragraphs><Paragraph><TextRuns>"
        "<TextRun><Value>Quarterly Balance Summary — Northern Region</Value>"
        "</TextRun></TextRuns></Paragraph></Paragraphs></Textbox>"
        "</ReportItems></PageHeader><TopMargin>0in</TopMargin></Page></Report>")
_TITLE = "Quarterly Balance Summary — Northern Region"


def _page(lines):
    """One sheet of measured geometry: 11in paper, the given located lines.
    The declared strip is 0.4in under a 0in top margin: 0..30.8pt."""
    return {"height": 792.0, "width": 612.0, "undecodable": [], "glyphs": 40,
            "decodable_text": None, "invisible": [], "images": [],
            "lines": [{"bbox": box, "key": bm._squash(text)}
                      for text, box in lines]}


_IN_STRIP = (72.0, 6.0, 400.0, 18.0)
_IN_BODY = (72.0, 300.0, 400.0, 312.0)


@pytest.mark.parametrize("wording", [_TITLE, "Northern Region"])
def test_declared_wording_is_judged_by_where_its_ink_lies(wording):
    """The same page text, the same declaration, one variable: where the ink
    lies. Verbatim title and a piece of it alike — the two forms the wording
    rule used to delete from anywhere."""
    pages = ["Opening balance 4,120.55", wording]
    cover = _page([("Opening balance 4,120.55", _IN_BODY)])

    body = bm.classify(pages, _RDL, (), None,
                       [cover, _page([(wording, _IN_BODY)])])
    assert body == ["content", "content"], (
        "located OUTSIDE the reserved strip the line is the body's own "
        f"content whatever it says — {body}")

    strip = bm.classify(pages, _RDL, (), None,
                        [cover, _page([(wording, _IN_STRIP)])])
    assert strip == ["content", "chrome_only"], (
        f"located INSIDE the reserved strip it is the band — {strip}")


@pytest.mark.parametrize("wording", [_TITLE, "Northern Region"])
def test_without_geometry_the_wording_is_the_only_evidence_and_it_is_weaker(
        wording):
    """The documented fallback: a caller that located nothing keeps the
    wording rule, which CAN call the body's copy furniture. This pins the
    fallback so its weakness is measured, not forgotten — and so a caller
    reading this knows which arm it is on."""
    pages = ["Opening balance 4,120.55", wording]
    assert bm.classify(pages, _RDL) == ["content", "chrome_only"]
    assert bm.classify(pages, _RDL, (), None, [None, None]) == \
        ["content", "chrome_only"]


def test_a_located_body_mark_is_a_content_mark_whatever_its_wording():
    """The second site: ``content_ink_marks`` keeps the body's copy of the
    title as a mark (it has coverage; it is the sheet's own wording) and
    drops the band's copy by its place."""
    pages = ["Opening balance 4,120.55", _TITLE]
    cover = _page([("Opening balance 4,120.55", _IN_BODY)])
    in_body = bm.content_ink_marks(pages, _RDL, (), [[], []],
                                   [cover, _page([(_TITLE, _IN_BODY)])])
    assert [m["sig"] for m in in_body[1]] == [("t", bm._squash(_TITLE))]
    in_strip = bm.content_ink_marks(pages, _RDL, (), [[], []],
                                    [cover, _page([(_TITLE, _IN_STRIP)])])
    assert in_strip[1] == []


def test_the_bands_own_copy_is_still_furniture_beside_the_bodys():
    """Both copies on one sheet: the band prints the title in its strip and
    the body restates it below. The sheet is content (the body's copy), and
    that is decided by the copy OUTSIDE the strip — ``band_resident_keys``
    never claims a key that also lies outside."""
    page = _page([(_TITLE, _IN_STRIP), (_TITLE, _IN_BODY)])
    assert bm.band_resident_keys(page, _RDL) == frozenset()
    assert bm.seated_keys(page, _RDL) == {bm._squash(_TITLE)}
    pages = ["Opening balance 4,120.55", f"{_TITLE}\n{_TITLE}"]
    cover = _page([("Opening balance 4,120.55", _IN_BODY)])
    assert bm.classify(pages, _RDL, (), None, [cover, page]) == \
        ["content", "content"]
    # ...and the band alone, in its strip, is still the blank sheet.
    alone = _page([(_TITLE, _IN_STRIP)])
    assert bm.classify(["Opening balance 4,120.55", _TITLE], _RDL, (), None,
                       [cover, alone]) == ["content", "chrome_only"]
