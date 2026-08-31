"""SPARSE SHEETS — technically not blank, effectively empty.

THE HOLE. Every measure in tools/renderlab/blank_measure.py answers "is there
ink on this sheet?", and a sheet carrying one stray word answers YES. Six
sheets of a production letter were mailed-quality-broken while every rail
reported clean: the letter's closing word was re-emitted once per record, so
each extra record bought a sheet printing that word and nothing else. The
blank measure was RIGHT about those sheets — they carry content ink, they are
not blank. "Technically not blank" and "a letter your agency would mail" are
different standards, and section (G) of the shared measure is the second one.

THE RULE — three properties, each measured against what THIS DOCUMENT's own
sheets carry, and none of them a wordlist:

    STARVED           its DISTINCT content-ink coverage is at least an order
                      of magnitude below the fullest sheet's in the same
                      document.
    SAYS NOTHING      every piece of the REPORT'S OWN WORDING it paints is
    OF ITS OWN        wording a sheet that is NOT starved already carries.
    STARVED OF THE    it received an order of magnitude fewer DATA-BOUND
    DOCUMENT'S DATA   CELLS than the sheet that received most.

THE TWO LAWS THIS FILE EXISTS TO KEEP, and the failures that put them here:

  L-A SCRIPT INVARIANCE  the same visual state gets the same verdict in every
                         script. It did not: rendered in nine scripts, one
                         legitimate closing block measures 4.2x starved in
                         Greek and 16.5x in Arabic, so the decade condemned it
                         in ascii, Cyrillic, Arabic and CJK and kept it in the
                         other five — a verdict decided by the language the
                         wording is written in.
  L-B SHAPE INVARIANCE   the same sheet gets the same verdict at 0, 1, 3 and
                         25 rows. It did not: a sheet was excused when it
                         carried a mark no other sheet carried, which at ONE
                         record is every mark on every sheet, so the same
                         starved sheet was flagged at 3 and 25 rows and clean
                         at 1 in seven of the nine scripts.

WHAT THIS FILE PROVES, and why each fixture exists — a gate is only worth
having if it can be shown to fail, and only worth trusting if it can be shown
NOT to fire on the legitimate shape it most resembles:

  word-only letter      the defect. A closing word re-emitted onto sheets of
                        its own — flagged at every row count that has rows,
                        and the count SCALES with the data (none at zero, one
                        at one row, three at three, twenty-five at
                        twenty-five), which is exactly how the production
                        letter behaved.
  the same letter,      clean at every shape. One artifact, one element
  undoctored            changed between the arms.
  real signature block  closing line + signatory + office, printed on every
                        record's closing sheet and on no fuller one. NOT
                        flagged, in all nine scripts and at every factor from
                        2x to 40x — kept by SAYS NOTHING OF ITS OWN, which is
                        the only leg that can keep it in every language.
  signature LINE with   starved past the line, but it is the sheet that
  the name from data    received the most data cells in its document. NOT
                        flagged — the data leg keeps it.
  orphaned data cell    a record's cell alone on a sheet of its own: 41.6x
                        starved, one cell against eighteen. FLAGGED — the
                        shape an agency summary prints on every second sheet
                        and the rule used to excuse outright.
  a grid of data cells  the SAME ink (a placeholder repeated is one mark) with
                        ten cells against eighteen. NOT flagged — which is why
                        the data leg counts cells and not their ink.
  one-row last page     a ledger whose last sheet carries a single row. NOT
                        flagged — all three legs keep it.
  healthy per-record    twenty-five record sheets that are BYTE-IDENTICAL in a
  letter                layout render (every value is the same invented
                        placeholder). NOT flagged: they are not starved, and
                        placeholder ink is a data region's ink by declaration.

Each leg is mutation-proven load-bearing against the fixture the OTHERS pass,
so none is decoration; the data-ink floor, the expression-literal half of the
declared-wording read, and the containment match are proven the same way, and
the two script-shaped ones are proven to fail BY LANGUAGE, which is the
failure mode they exist for.

Structural assertions always run. The render proofs run wherever the
ReportViewer DLLs are present (tools/renderlab).
"""
from __future__ import annotations

import math
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "tools" / "renderlab"))

from converter import convert  # noqa: E402

import blank_measure as bm  # noqa: E402

try:
    from render import lib_ready, render_rdl  # noqa: E402
    _LIB_OK = bool(lib_ready()) and sys.platform == "win32"
except Exception:  # noqa: BLE001
    _LIB_OK = False

_needs_engine = pytest.mark.skipif(not _LIB_OK,
                                   reason="ReportViewer DLLs not present")

NS = "http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition"


def _q(tag):
    return f"{{{NS}}}{tag}"


# ---------------------------------------------------------------------------
# Synthetic sheets — the rule's own arithmetic, with no engine in the way
# ---------------------------------------------------------------------------

PAGE_W, PAGE_H = 612.0, 792.0


def _line(key, top, width, height=12.0, left=72.0):
    return {"key": key, "bbox": (left, top, left + width, top + height)}


def _sheet(lines):
    """One page of ink geometry in the shape ``page_ink`` returns."""
    return {"height": PAGE_H, "width": PAGE_W, "undecodable": [],
            "decodable_text": None, "lines": list(lines),
            "glyphs": sum(len(ln["key"]) for ln in lines),
            "ground": (255, 255, 255), "invisible": [], "images": []}


def _dense(tag, n=20, width=430.0):
    """A full sheet: many wide lines, each unique to it."""
    return [_line(f"{tag}line{i}ofthenoticetotheaccountholder", 60.0 + i * 22,
                  width) for i in range(n)]


def _measure(pages, classes=None, rdl_xml=None, invented=()):
    texts = ["\n".join(ln["key"] for ln in p["lines"]) for p in pages]
    ink = [_sheet(p["lines"]) for p in pages]
    return bm.sparse_sheets(texts, rdl_xml, invented, [[]] * len(pages), ink,
                            classes)


def _pages(*line_lists):
    return [{"lines": lines} for lines in line_lists]


_CLOSING = "closing"


def _letter_pages(*tails):
    """A two-sheet letter that prints its closing word once, where it belongs,
    followed by the sheets under test.

    TWO full sheets, not one, and the reason is a rule above this one: a line
    that appears on EVERY sheet of a render is page furniture by rule (B) and
    never becomes a content mark at all, so a three-sheet fixture whose
    closing word is on all three measures nothing and passes green. (The
    engine arms below do not need the second sheet because the report DECLARES
    that wording, which rule (B) reads; a hand-built page dict declares
    nothing.)"""
    return _pages(_dense("a") + [_line(_CLOSING, 500.0, 40.0)],
                  _dense("b"), *tails)


def test_a_sheet_that_only_reprints_a_fuller_sheets_wording_is_sparse():
    """The defect, reduced to its arithmetic: a full sheet that prints the
    letter's closing word where it belongs, then two sheets carrying that same
    word and nothing else.

    THE ECHO IS OF THE FULL SHEET, and that is the production shape — the
    letter printed "Sincerely," in its body and the defect re-emitted it once
    per record. It is also the only reading that survives nine scripts: this
    fixture used to echo between the two starved sheets alone, which is
    exactly the shape of a legitimate signature block reprinted on every
    record's closing sheet, and the two were told apart only by where their
    starvation ratios happened to land — 25.8x against 5.2x in ascii, and 13.9x
    against 16.5x once the same two artifacts are rendered in CJK and Arabic,
    which is the wrong way round. See
    ``test_a_sheet_echoing_only_other_starved_sheets_is_not_sparse``."""
    pages = _letter_pages([_line(_CLOSING, 300.0, 40.0)],
                          [_line(_CLOSING, 300.0, 40.0)])
    assert _measure(pages, ["content"] * 4) == [3, 4]


def test_a_sheet_echoing_only_other_starved_sheets_is_not_sparse():
    """...and the fixture the reading above exists to keep: three sheets
    carrying a closing block that NO fuller sheet carries.

    The reader is being handed something they have not had, however little of
    it, so the sheet is short and not empty. This is the legitimate signature
    block as arithmetic, and the leg that keeps it is the same one that
    condemns the reprint above — not a ratio, which measured the two in the
    wrong order across scripts."""
    block = [_line("yoursfaithfully,", 300.0, 60.0),
             _line("r.whitfielddeputyregistrar", 320.0, 90.0),
             _line("officeofassessmentsandrecords", 340.0, 95.0)]
    pages = _pages(_dense("a"), _dense("b"), block, block)
    assert _measure(pages, ["content"] * 4) == []


def test_a_sheet_carrying_one_mark_of_its_own_is_not_sparse():
    """The SAYS-SOMETHING-OF-ITS-OWN leg. Same starvation, same reprint, one
    mark no fuller sheet carries — a value, a caption, anything — and the
    sheet is saying something, however little."""
    pages = _letter_pages(
        [_line(_CLOSING, 300.0, 40.0), _line("41205", 320.0, 30.0)],
        [_line(_CLOSING, 300.0, 40.0)])
    assert _measure(pages, ["content"] * 4) == [4]


def test_a_mark_too_short_to_identify_is_not_evidence_either_way():
    """A one- or two-token mark cannot be told from any other one- or two-token
    mark, so it neither proves nor disproves that a sheet says something new —
    the floor every containment test in the measure already stops at.

    Measured on the engine, in Hangul: the extractor hands back one printed
    closing line as a single run on the sheet where it belongs and as that run
    PLUS its comma, as a mark of its own, on the sheet the defect put it on.
    Counting that comma as something the sheet says of its own left the
    per-record defect unflagged in Hangul while flagging it in ascii."""
    pages = _letter_pages(
        [_line(_CLOSING, 300.0, 40.0), _line(",", 300.0, 3.0)],
        [_line(_CLOSING, 300.0, 40.0)])
    assert _measure(pages, ["content"] * 4) == [3, 4]


def test_a_sheet_that_is_not_starved_is_not_sparse():
    """The STARVED leg. Every mark an echo, but the sheet carries as much ink
    as its siblings — a legitimate reprint, not an empty page."""
    echo = _dense("shared", n=18)
    pages = _pages(_dense("a") + echo, _dense("b"), echo, echo)
    assert _measure(pages, ["content"] * 4) == []


def test_only_content_sheets_are_judged_and_blank_stays_the_blank_gates():
    """``sparse`` and ``blank`` are disjoint by construction: a sheet with no
    content ink is the blank gate's, and this rule never speaks about it."""
    pages = _letter_pages([_line(_CLOSING, 300.0, 40.0)],
                          [_line(_CLOSING, 300.0, 40.0)])
    assert _measure(pages, ["content", "content", "chrome_only",
                            "empty"]) == []


def test_a_single_sheet_document_is_never_sparse():
    """Nothing to echo and nothing to be starved beside."""
    assert _measure(_pages([_line("closing", 300.0, 40.0)]), ["content"]) == []


def test_the_verdict_is_the_same_when_the_glyphs_do_not_decode():
    """Script-agnostic BY CONSTRUCTION: swap every decodable line for an
    undecodable run carrying the same geometry and the same repetition, and
    the same sheets come back. A mark's signature is its glyph ids when its
    glyphs do not map (section (D)); nothing here counts a character."""
    def _undec(pages):
        out = []
        for p in pages:
            spans = [{"bbox": ln["bbox"], "sig": (hash(ln["key"]) % 9973,)}
                     for ln in p["lines"]]
            out.append({"height": PAGE_H, "width": PAGE_W,
                        "undecodable": spans, "decodable_text": "",
                        "lines": [], "glyphs": 40, "ground": (255, 255, 255),
                        "invisible": [], "images": []})
        return out

    pages = _letter_pages([_line(_CLOSING, 300.0, 40.0)],
                          [_line(_CLOSING, 300.0, 40.0)])
    latin = _measure(pages, ["content"] * 4)
    ink = _undec(pages)
    other = bm.sparse_sheets([""] * 4, None, (), [[]] * 4, ink,
                             ["content"] * 4)
    assert latin == other == [3, 4]


def test_no_geometry_means_no_verdict_not_a_clean_one():
    """With no ink pass there are no boxes, no coverage and no echo
    structure. The rule contributes nothing rather than guessing — the same
    direction every other geometric rule in the module fails in."""
    texts = ["a dense sheet of prose", "closing", "closing"]
    assert bm.sparse_sheets(texts, None, (), None, None,
                            ["content"] * 3) == []


# ---------------------------------------------------------------------------
# The artifact halves: what the report declares, and what a staticizer invents
# ---------------------------------------------------------------------------

def _mini_rdl(values):
    body = "".join(
        f'<Textbox Name="Tb{i}"><Paragraphs><Paragraph><TextRuns><TextRun>'
        f'<Value>{v}</Value><Style /></TextRun></TextRuns><Style /></Paragraph>'
        f'</Paragraphs><Style /><Top>{i}in</Top><Left>0in</Left>'
        f'<Width>3in</Width><Height>0.3in</Height></Textbox>'
        for i, v in enumerate(values))
    return (f'<?xml version="1.0"?><Report xmlns="{NS}"><Body><ReportItems>'
            f'{body}</ReportItems><Height>9in</Height></Body>'
            '<Width>7.5in</Width><Page><PageHeight>11in</PageHeight>'
            '<PageWidth>8.5in</PageWidth><TopMargin>0.5in</TopMargin>'
            '<BottomMargin>0.5in</BottomMargin><LeftMargin>0.5in</LeftMargin>'
            '<RightMargin>0.5in</RightMargin></Page></Report>')


@pytest.mark.parametrize("wording", [
    "Yours faithfully",                                          # ascii
    "Με εκτίμηση",                                               # greek
    "С уважением",                                               # cyrillic
    "مع فائق الاحترام",                                          # arabic
])
def test_declared_wording_is_read_through_an_expression_in_any_script(wording):
    """The emitter writes a great deal of static wording as ``="..."``. A rule
    that cannot see through that reads the report's own closing line as text
    somebody invented — and the wording is the report's, in whatever script it
    declares it."""
    rdl = _mini_rdl([f'="{wording}"'])
    assert bm._squash(wording) in bm.declared_literal_texts(rdl)


def test_a_literal_only_expression_is_not_data_region_ink():
    """``="Sincerely,"`` invents nothing: what it paints is the report's own
    literal. Counting it as a data region's ink exempts the production defect
    outright — every word-only sheet would carry 'data' and pass."""
    rdl = _mini_rdl(['="Sincerely,"', "=Fields!Holder.Value"])
    tokens = bm.data_region_texts(rdl, bm.invented_placeholder_texts(rdl))
    assert "sincerely," not in tokens
    assert tokens, "the field-bound cell must still count as a data region's"


def test_the_data_ink_floor_sees_a_one_character_placeholder(monkeypatch):
    """The staticizer writes ``*`` for a Lookup/Join, and a production summary
    report paints twenty-five sheets of nothing else. Read at the module's
    two-character fragment floor that placeholder DISAPPEARS, the sheets read
    as the report's own repeated ink, and every one of them was flagged. The
    ink is the harness's, not the report's, and the class that owns it is
    ``placeholder_only``."""
    rdl = _mini_rdl(['=First(Fields!CP_Complete_Ind.Value, "DS_FORMULAS")'])
    invented = bm.invented_placeholder_texts(rdl)
    fine = bm.data_region_texts(rdl, invented or {"x"})
    short = {t for t in fine if len(t) < bm.FRAGMENT_FLOOR}
    assert short, (
        f"the staticizer no longer writes a short placeholder: {fine} — this "
        "fixture no longer reproduces the sheets it exists for")
    bm._DATA_INK_CACHE.clear()
    monkeypatch.setattr(bm, "DATA_INK_FLOOR", bm.FRAGMENT_FLOOR)
    coarse = bm.data_region_texts(rdl, invented or {"x"})
    bm._DATA_INK_CACHE.clear()
    assert not (short & coarse), (
        "at the fragment floor the placeholder vanishes and its sheets read "
        "as the report's own repeated ink")


def test_a_caption_that_squashes_like_a_field_placeholder_stays_data_ink():
    """The reason the read is POSITIONAL. A report that captions a column
    "To Airport Id" over a field named TO_AIRPORT_ID paints two things that
    squash to one key; a set difference throws the placeholder away with the
    caption and the data sheet reads as the report's own repeated ink
    (measured on a wild source). Asked per expression, the caption is the
    caption and the field is the data."""
    rdl = _mini_rdl(['="To Airport Id"', "=Fields!To_Airport_Id.Value"])
    tokens = bm.data_region_texts(rdl, bm.invented_placeholder_texts(rdl))
    assert "toairportid" in tokens, (
        f"the field's own placeholder was subtracted by its caption: {tokens}")


def test_an_expression_render_exempts_nothing():
    """With real values on the page there is nothing to exempt: the engine
    painted the data, and the uniqueness half of the rule is what sees it."""
    rdl = _mini_rdl(["=Fields!Holder.Value"])
    assert bm.data_region_texts(rdl, ()) == frozenset()


# ---------------------------------------------------------------------------
# ENGINE — the discrimination suite, rendered through the real MS engine
# ---------------------------------------------------------------------------

_PARA = ("Thank you for submitting the annual package with the itemized "
         "accounting form; the budget and the breakdown have been reviewed "
         "and approved as submitted for this reporting period.")
_ENC = ("Enclosed with this notice is the schedule of fees that applies to "
        "the reporting period, together with the remittance advice and the "
        "filing instructions for the next cycle.")
_PROSE = ("The annual accounting package for the reporting period has been "
          "received and reviewed by this office, and the enclosed schedule "
          "sets out the amounts assessed for each category of activity.")
_SIGNOFF = "Sincerely,"


def _txt(name, text, x, y, w, h, face=""):
    font = f'<font face="{face}" size="10"/>' if face else ""
    return (f'<text name="{name}"><geometryInfo x="{x}" y="{y}" width="{w}" '
            f'height="{h}"/><textSegment>{font}'
            f'<string><![CDATA[{text}]]></string>'
            f'</textSegment></text>')


def _fld(name, source, x, y, w, h, face=""):
    font = f'<font face="{face}" size="10"/>' if face else ""
    return (f'<field name="{name}" source="{source}"><geometryInfo x="{x}" '
            f'y="{y}" width="{w}" height="{h}"/>{font}</field>')


# A letter with a real prose sheet: the closing word is printed ONCE, where it
# belongs. The doctor below puts it on sheets of its own.
LETTER = (
    '<?xml version="1.0"?><report name="SPARSEX" DTDVersion="9.0.2.0.10">'
    '<data><dataSource name="Q_MAIN">'
    '<select><![CDATA[select code, nm from t]]></select>'
    '<group name="G_MAIN"><dataItem name="CODE" datatype="vchar2"/>'
    '<dataItem name="NM" datatype="vchar2"/></group></dataSource></data>'
    '<layout><section name="main"><body height="9.0">'
    '<frame name="M_LET"><geometryInfo x="0.05" y="0.2" width="7.4" '
    'height="8.0"/>'
    + _txt("B_BODY", _PARA, 0.1, 0.3, 6.0, 0.2)
    + _txt("B_SIGNOFF", _SIGNOFF, 0.1, 6.4, 0.7, 0.2)
    + _fld("F_NM", "NM", 0.1, 6.9, 4.2, 0.3)
    + '</frame><frame name="M_ENC"><geometryInfo x="0.05" y="8.3" width="7.4" '
      'height="0.6"/>'
    + _txt("B_ENC", _ENC, 0.1, 8.4, 4.0, 0.2)
    + '</frame></body></section></layout></report>')

# The same letter, one record to a sheet — the shape whose defect SCALES with
# the data, which is how the production letter behaved.
RECORD_LETTER = (
    '<?xml version="1.0"?><report name="SPARSEREC" DTDVersion="9.0.2.0.10">'
    '<data><dataSource name="Q_LET">'
    '<select><![CDATA[select acct_no, addressee from letter_queue]]></select>'
    '<group name="G_LET"><dataItem name="ACCT_NO" datatype="vchar2"/>'
    '<dataItem name="ADDRESSEE" datatype="vchar2"/></group>'
    '</dataSource></data>'
    '<layout><section name="main" width="8.50000">'
    '<body width="7.50000" height="9.40000">'
    '<repeatingFrame name="R_LET" source="G_LET" printDirection="down" '
    'maxRecordsPerPage="1" minWidowRecords="1" columnMode="no">'
    '<geometryInfo x="0.00000" y="0.00000" width="7.50000" height="9.00000"/>'
    '<generalLayout verticalElasticity="variable"/>'
    + _txt("B_BODY", _PARA, 0.3, 0.4, 6.0, 0.2)
    + _fld("F_ADDR", "ADDRESSEE", 0.3, 3.0, 4.0, 0.2)
    + _fld("F_ACCT", "ACCT_NO", 0.3, 3.4, 4.0, 0.2)
    + _txt("B_SIGNOFF", _SIGNOFF, 0.3, 6.4, 0.7, 0.2)
    + _txt("B_ENC", _ENC, 0.3, 7.2, 6.0, 0.2)
    + '</repeatingFrame></body></section></layout></report>')


def _closing_letter(closing_lines, signer_field=False):
    """A letter whose closing block falls on a sheet of its own.

    ``closing_lines`` is the block's own static wording; ``signer_field`` adds
    the signatory from the DATA. The two arms are the legitimate short final
    page in its two forms — a real signature block, and a signature line whose
    name the report looks up."""
    close = "".join(
        _txt(f"B_C{i}", text, 0.4, round(10.1 + i * 0.45, 2), 4.0, 0.25)
        for i, text in enumerate(closing_lines))
    if signer_field:
        close += _fld("F_SIGNER", "NM", 0.4, 10.55, 4.0, 0.25)
    return (
        '<?xml version="1.0"?><report name="CLOSING" DTDVersion="9.0.2.0.10">'
        '<data><dataSource name="Q_MAIN">'
        '<select><![CDATA[select code, nm from t]]></select>'
        '<group name="G_MAIN"><dataItem name="CODE" datatype="vchar2"/>'
        '<dataItem name="NM" datatype="vchar2"/></group></dataSource></data>'
        '<layout><section name="main"><body height="12.0">'
        '<frame name="M_LET"><geometryInfo x="0.05" y="0.2" width="7.4" '
        'height="8.6"/>'
        + "".join(_txt(f"B_P{i}", f"{_PROSE} Paragraph {i} of the notice.",
                       0.1, round(0.4 + i * 0.9, 2), 6.0, 0.8)
                  for i in range(9))
        + '</frame><frame name="M_SIGN"><geometryInfo x="0.05" y="10.0" '
          'width="7.4" height="1.6"/>' + close +
        '</frame></body></section></layout></report>')


# A ledger that paginates: its LAST sheet carries a single row.
LEDGER = (
    '<?xml version="1.0"?><report name="LEDGER" DTDVersion="9.0.2.0.10">'
    '<data><dataSource name="Q_ROWS">'
    '<select><![CDATA[select acct, holder, amt from ledger]]></select>'
    '<group name="G_ROWS"><dataItem name="ACCT" datatype="vchar2"/>'
    '<dataItem name="HOLDER" datatype="vchar2"/>'
    '<dataItem name="AMT" datatype="number"/></group></dataSource></data>'
    '<layout><section name="main"><body height="9.4">'
    + _txt("T_ACCT", "Account", 0.3, 0.3, 1.6, 0.2)
    + _txt("T_HOLD", "Account Holder", 2.0, 0.3, 3.0, 0.2)
    + _txt("T_AMT", "Amount Assessed", 5.2, 0.3, 2.0, 0.2)
    + '<repeatingFrame name="R_ROW" source="G_ROWS" printDirection="down" '
      'minWidowRecords="1" columnMode="no">'
      '<geometryInfo x="0.3" y="0.7" width="7.0" height="0.70"/>'
    + _fld("F_ACCT", "ACCT", 0.3, 0.7, 1.6, 0.2)
    + _fld("F_HOLD", "HOLDER", 2.0, 0.7, 3.0, 0.2)
    + _fld("F_AMT", "AMT", 5.2, 0.7, 2.0, 0.2)
    + '</repeatingFrame></body></section></layout></report>')


# A per-record data CARD — a dozen cells to a record, the shape a summary
# report prints one record to a sheet. The two doctors below put its CELLS on
# sheets of their own, which is the shape that separates the two readings of
# "this sheet carries the document's data": an agency summary orphans ONE cell
# (its record comment) onto every second sheet, and a summary grid puts a
# table's worth of them on its last one. In a layout render both are almost no
# INK — a placeholder is as wide as its field name — and only one of them is
# an empty sheet.
CARD_LETTER = (
    '<?xml version="1.0"?><report name="CARDREC" DTDVersion="9.0.2.0.10">'
    '<data><dataSource name="Q_CARD">'
    '<select><![CDATA[select * from card_queue]]></select>'
    '<group name="G_CARD">'
    + "".join(f'<dataItem name="C{i:02d}" datatype="vchar2"/>'
              for i in range(1, 19))
    + '</group></dataSource></data>'
    '<layout><section name="main" width="8.50000">'
    '<body width="7.50000" height="9.40000">'
    '<repeatingFrame name="R_CARD" source="G_CARD" printDirection="down" '
    'maxRecordsPerPage="1" minWidowRecords="1" columnMode="no">'
    '<geometryInfo x="0.00000" y="0.00000" width="7.50000" '
    'height="9.00000"/>'
    '<generalLayout verticalElasticity="variable"/>'
    + "".join(_txt(f"B_L{i}", f"{_PROSE} Paragraph {i} of the record card.",
                   0.3, round(0.4 + i * 0.4, 2), 6.0, 0.3)
              for i in range(3))
    + "".join(_fld(f"F_C{i:02d}", f"C{i:02d}",
                   round(0.3 + 1.2 * ((i - 1) % 6), 2),
                   round(2.2 + 0.4 * ((i - 1) // 6), 2), 1.1, 0.2)
              for i in range(1, 19))
    + '</repeatingFrame></body></section></layout></report>')


def _cell_block_per_record(rdl_xml, copies=1):
    """A SECOND per-record block whose row is nothing but data cells.

    The same doctor as ``_word_block_per_record``, with one difference that is
    the whole point: what lands on the sheet of its own is a data-bound CELL,
    not a word the report already prints. One copy is the agency summary's
    orphaned comment; several are a grid's worth of cells."""
    import copy as _copy

    ET.register_namespace("", NS)
    root = ET.fromstring(rdl_xml)
    body = root.find(_q("Body"))
    items = body.find(_q("ReportItems"))
    sheet = _sheet_height(root)
    body_h = _inches(body.findtext(_q("Height")), 9.0)
    record = next((tx for tx in items.iter(_q("Tablix"))
                   if tx.findtext(_q("DataSetName"))), None)
    assert record is not None, "the card declares no per-record region"
    src = next((tb for tb in root.iter(_q("Textbox"))
                if any("Fields!" in (v.text or "")
                       for v in tb.iter(_q("Value")))), None)
    assert src is not None, "the card prints no data cell"

    clone = _copy.deepcopy(record)
    clone.set("Name", "Tablix_CellBlock")
    for el in clone.iter():
        if el.get("Name"):
            el.set("Name", el.get("Name") + "_CB")
    for n, cell in enumerate(clone.iter(_q("CellContents"))):
        for child in list(cell):
            cell.remove(child)
        # One child per cell is all the schema allows, so several cells ride
        # in a rectangle of their own.
        holder = ET.SubElement(cell, _q("Rectangle"))
        holder.set("Name", f"Rect_Cells_{n}")
        inner = ET.SubElement(holder, _q("ReportItems"))
        for k in range(copies):
            box = _copy.deepcopy(src)
            box.set("Name", f"Tb_Cell_{n}_{k}")
            for tag, value in (("Top", f"{0.3 * k:.3f}in"),
                               ("Left", "0.000in")):
                el = box.find(_q(tag))
                if el is not None:
                    el.text = value
            inner.append(box)
        for tag, value in (("Top", "0.000in"), ("Left", "0.000in"),
                           ("Width", "3.000in"),
                           ("Height", f"{0.3 * copies + 0.2:.3f}in")):
            ET.SubElement(holder, _q(tag)).text = value
    for row in clone.iter(_q("TablixRow")):
        height = row.find(_q("Height"))
        if height is not None:
            height.text = f"{sheet - 0.2:.3f}in"
    top = clone.find(_q("Top"))
    if top is None:
        top = ET.SubElement(clone, _q("Top"))
    start = math.ceil(body_h / sheet) * sheet
    top.text = f"{start:.3f}in"
    height = clone.find(_q("Height"))
    if height is not None:
        height.text = f"{sheet - 0.2:.3f}in"
    items.append(clone)
    body.find(_q("Height")).text = f"{start + sheet - 0.2:.3f}in"
    return ET.tostring(root, encoding="unicode")


def _inches(text, default=0.0):
    try:
        return float((text or "").replace("in", "").strip())
    except ValueError:
        return default


def _sheet_height(root):
    page = root.find(_q("Page"))
    return (_inches(page.findtext(_q("PageHeight")), 11.0)
            - _inches(page.findtext(_q("TopMargin")))
            - _inches(page.findtext(_q("BottomMargin"))))


def _word_textbox(root, word):
    return next((tb for tb in root.iter(_q("Textbox"))
                 if any(word in (v.text or "") for v in tb.iter(_q("Value")))),
                None)


def _word_onto_sheets_of_its_own(rdl_xml, word=_SIGNOFF, sheets=3):
    """THE DEFECT, as an artifact mutation: the letter's own closing word,
    cloned onto sheets of its own.

    A clone, not an invention — the box, the font and the wording are the
    report's, so the two arms differ in nothing but WHERE the word is
    printed."""
    import copy

    ET.register_namespace("", NS)
    root = ET.fromstring(rdl_xml)
    body = root.find(_q("Body"))
    items = body.find(_q("ReportItems"))
    sheet = _sheet_height(root)
    body_h = _inches(body.findtext(_q("Height")), 9.0)
    src = _word_textbox(root, word)
    assert src is not None, "the letter does not print that word"
    first, last = math.ceil(body_h / sheet) * sheet, 0.0
    for k in range(sheets):
        clone = copy.deepcopy(src)
        clone.set("Name", f"Tb_Orphan_{k}")
        last = first + k * sheet + sheet / 2.0
        top = clone.find(_q("Top"))
        if top is None:
            top = ET.SubElement(clone, _q("Top"))
        top.text = f"{last:.3f}in"
        items.append(clone)
    body.find(_q("Height")).text = f"{last + 0.4:.3f}in"
    return ET.tostring(root, encoding="unicode")


def _word_block_per_record(rdl_xml, word=_SIGNOFF):
    """The same defect where it actually came from: a SECOND per-record block
    whose row resolves nothing but a word the letter already prints. One sheet
    per record, so the defect scales with the data."""
    import copy

    ET.register_namespace("", NS)
    root = ET.fromstring(rdl_xml)
    body = root.find(_q("Body"))
    items = body.find(_q("ReportItems"))
    sheet = _sheet_height(root)
    body_h = _inches(body.findtext(_q("Height")), 9.0)
    record = next((tx for tx in items.iter(_q("Tablix"))
                   if tx.findtext(_q("DataSetName"))), None)
    assert record is not None, "the letter declares no per-record region"
    src = _word_textbox(root, word)
    assert src is not None, "the letter does not print that word"

    clone = copy.deepcopy(record)
    clone.set("Name", "Tablix_WordBlock")
    for el in clone.iter():
        if el.get("Name"):
            el.set("Name", el.get("Name") + "_WB")
    for n, cell in enumerate(clone.iter(_q("CellContents"))):
        for child in list(cell):
            cell.remove(child)
        box = copy.deepcopy(src)
        box.set("Name", f"Tb_Word_{n}")
        for tag in ("Top", "Left"):
            el = box.find(_q(tag))
            if el is not None:
                el.text = "0.000in"
        cell.append(box)
    for row in clone.iter(_q("TablixRow")):
        height = row.find(_q("Height"))
        if height is not None:
            height.text = f"{sheet - 0.2:.3f}in"
    top = clone.find(_q("Top"))
    if top is None:
        top = ET.SubElement(clone, _q("Top"))
    start = math.ceil(body_h / sheet) * sheet
    top.text = f"{start:.3f}in"
    height = clone.find(_q("Height"))
    if height is not None:
        height.text = f"{sheet - 0.2:.3f}in"
    items.append(clone)
    body.find(_q("Height")).text = f"{start + sheet - 0.2:.3f}in"
    return ET.tostring(root, encoding="unicode")


SHAPES = (0, 1, 3, 25)


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    """Render every arm once: {name: {rows: measure}} plus the RDL of each."""
    if not _LIB_OK:
        pytest.skip("ReportViewer DLLs not present")
    out = tmp_path_factory.mktemp("sparse")
    letter = convert(LETTER.encode(), "sparsex.xml")["rdl_xml"]
    record = convert(RECORD_LETTER.encode(), "sparserec.xml")["rdl_xml"]
    card = convert(CARD_LETTER.encode(), "card.xml")["rdl_xml"]
    arms = {
        "letter": (letter, SHAPES),
        "letter_doctored": (_word_onto_sheets_of_its_own(letter), SHAPES),
        "record": (record, SHAPES),
        "record_doctored": (_word_block_per_record(record), SHAPES),
        "signature_block": (convert(_closing_letter(
            ["Yours faithfully,", "R. Whitfield, Deputy Registrar",
             "Office of Assessments and Records"]).encode(),
            "sig.xml")["rdl_xml"], (3,)),
        "signature_line": (convert(_closing_letter(
            ["Yours faithfully,"], signer_field=True).encode(),
            "sigline.xml")["rdl_xml"], (3,)),
        "ledger": (convert(LEDGER.encode(), "ledger.xml")["rdl_xml"], (25,)),
        "card": (card, SHAPES),
        "card_orphan": (_cell_block_per_record(card, copies=1), SHAPES),
        "card_grid": (_cell_block_per_record(card, copies=10), SHAPES),
    }
    built = {}
    for name, (rdl, shapes) in arms.items():
        path = out / f"{name}.rdl"
        path.write_text(rdl, encoding="utf-8")
        built[name] = {"rdl": rdl, "pdf": {}}
        for rows in shapes:
            pdf = out / f"{name}-{rows}.pdf"
            res = render_rdl(path, pdf, rows=rows)
            assert res.get("ok"), f"{name} rows={rows}: {res.get('log','')[-400:]}"
            built[name]["pdf"][rows] = (pdf, res.get("mode"))
    return built


def _measured(built, name, rows):
    pdf, mode = built[name]["pdf"][rows]
    return bm.measure_pdf(pdf, rdl_xml=built[name]["rdl"], mode=mode)


@_needs_engine
@pytest.mark.parametrize("rows", SHAPES)
def test_engine_the_shipped_letter_is_clean_at_every_shape(engine, rows):
    """The arm the defect is measured against. One sheet, nothing flagged —
    and no blank sheet either, so the doctored arm's verdict cannot be an
    inherited one."""
    m = _measured(engine, "letter", rows)
    assert m["sparse"] == [] and m["blank"] == [], (
        f"rows={rows}: the shipped letter must start clean — "
        f"sparse={m['sparse']} blank={m['blank']}")


@_needs_engine
@pytest.mark.parametrize("rows", SHAPES)
def test_engine_word_only_sheets_are_flagged_at_every_shape(engine, rows):
    """PROVE THE GATE CAN FAIL. The same letter with its own closing word
    cloned onto three sheets of its own: every one of them flagged, at every
    row count, because the defect does not depend on the data."""
    m = _measured(engine, "letter_doctored", rows)
    assert m["pages"] == 4, (
        f"rows={rows}: the mutation did not produce the sheets: {m['pages']}p")
    assert m["sparse"] == [2, 3, 4], (
        f"rows={rows}: word-only sheets went unflagged: sparse={m['sparse']} "
        f"coverage={[round(c * 100, 3) for c in m['coverage']]}")
    # ...and the blank column is silent on them, which is the whole point:
    # they carry ink, they are not blank, and no measure above section (G)
    # could ever have named them.
    assert m["blank"] == [], (
        "the blank gate now flags these sheets, so this fixture no longer "
        f"reproduces the hole it exists for: {m['blank']}")
    assert all(m["classes"][p - 1] == "content" for p in m["sparse"])


@_needs_engine
@pytest.mark.parametrize("rows,expect", [(0, 0), (1, 1), (3, 3), (25, 25)])
def test_engine_the_defect_scales_with_the_row_count(engine, rows, expect):
    """The production shape: a per-record block whose row is nothing but the
    closing word. The count of ruined sheets TRACKS THE DATA — none at zero
    rows, one per record after that — which is why this is measured at every
    shape and not at one."""
    m = _measured(engine, "record_doctored", rows)
    assert len(m["sparse"]) == expect, (
        f"rows={rows}: expected {expect} word-only sheets, got {m['sparse']}")


@_needs_engine
@pytest.mark.parametrize("rows", SHAPES)
def test_engine_a_healthy_per_record_letter_is_never_flagged(engine, rows):
    """The shape this rule must not break. In a LAYOUT render every record
    sheet is byte-identical — the same invented placeholder in every field —
    so the echo half sees twenty-five identical sheets. They survive because
    placeholder ink is a data region's ink BY DECLARATION, which is what stops
    this rule doing to per-record letters what the repeat rule once did."""
    m = _measured(engine, "record", rows)
    assert m["sparse"] == [], (
        f"rows={rows}: a healthy per-record letter was called sparse: "
        f"{m['sparse']} of {m['pages']} sheets")


@_needs_engine
def test_engine_a_real_signature_block_is_not_flagged(engine):
    """A legitimately short final page. Closing line, signatory and office —
    all of it the report's own wording, reprinted on every record's closing
    sheet, so it carries NO original ink. It is kept by the STARVED leg: at
    5.2x it is not orders of magnitude below the letter's own sheet."""
    m = _measured(engine, "signature_block", 3)
    assert m["sparse"] == [], f"a real signature block was flagged: {m['sparse']}"
    cover = m["coverage"]
    ratio = max(cover) / min(c for c in cover if c > 0)
    assert 3.0 < ratio < 10.0 ** bm.SPARSE_DECADES, (
        "the fixture must sit BELOW the starvation line while still being a "
        f"short page — measured {ratio:.1f}x")


@_needs_engine
def test_engine_a_short_final_page_that_carries_a_datum_is_not_flagged(engine):
    """The other legitimately short final page, and the one that isolates the
    ORIGINALITY leg: a signature LINE with the signatory from the data. It is
    starved — far past the line the signature block sits under — and it is
    kept because one of its two marks is a data region's."""
    m = _measured(engine, "signature_line", 3)
    assert m["sparse"] == [], f"a signed short page was flagged: {m['sparse']}"
    cover = m["coverage"]
    ratio = max(cover) / min(c for c in cover if c > 0)
    assert ratio > 10.0 ** bm.SPARSE_DECADES, (
        f"the fixture is no longer starved ({ratio:.1f}x), so it stops "
        "proving that the originality leg is what keeps it")


@_needs_engine
def test_engine_a_one_row_last_page_is_not_flagged(engine):
    """The third legitimate shape: a ledger whose last sheet carries a single
    row."""
    m = _measured(engine, "ledger", 25)
    assert m["pages"] >= 3, "the ledger no longer paginates"
    assert m["sparse"] == [], f"a one-row last page was flagged: {m['sparse']}"


@_needs_engine
@pytest.mark.parametrize("rows", SHAPES)
def test_engine_the_card_is_clean_at_every_shape(engine, rows):
    """The arm the two below are measured against."""
    m = _measured(engine, "card", rows)
    assert m["sparse"] == [] and m["blank"] == [], (
        f"rows={rows}: the record card must start clean — "
        f"sparse={m['sparse']} blank={m['blank']}")


@_needs_engine
@pytest.mark.parametrize("rows", SHAPES)
def test_engine_one_orphaned_data_cell_does_not_excuse_a_sheet(engine, rows):
    """THE 29x SHEET. A record's cell, orphaned onto a sheet of its own —
    which is what an agency summary does with its comment field on every
    second sheet: coverage 0.00141 against the record sheet's 0.04137, and one
    data cell against eighteen.

    The rule this replaces excused it outright, at every row count, because
    ONE mark on it came from a data region. A sheet can be twenty-nine times
    emptier than its siblings and it is not made full by a single value
    landing on it."""
    m = _measured(engine, "card_orphan", rows)
    assert len(m["sparse"]) == rows, (
        f"rows={rows}: expected {rows} orphaned-cell sheets, got "
        f"{m['sparse']} of {m['pages']} — coverage="
        f"{[round(c, 5) for c in m['coverage'][:4]]} "
        f"cells={m['data_cells'][:4]}")


@_needs_engine
@pytest.mark.parametrize("rows", SHAPES)
def test_engine_a_sheet_that_carries_the_documents_data_is_not_flagged(engine,
                                                                       rows):
    """...and the sheet the same leg has to keep, which is why it counts CELLS
    and not their ink: a grid of them on a sheet of its own.

    In a layout render those cells are a placeholder each — the whole sheet is
    a thousandth of the paper, far past the starvation line — and what it
    actually carries is a table's worth of the report's data. Measured on a
    production summary whose grid staticizes to a column of ``*``: read as
    ink, every one of its sheets reads ninety times starved; read as cells, it
    carries ten against the stat table's eighteen and is a short page, not an
    empty one."""
    m = _measured(engine, "card_grid", rows)
    assert m["sparse"] == [], (
        f"rows={rows}: a sheet carrying the document's own data cells was "
        f"called empty: {m['sparse']} — coverage="
        f"{[round(c, 5) for c in m['coverage'][:4]]} "
        f"cells={m['data_cells'][:4]}")


# ---------------------------------------------------------------------------
# NINE SCRIPTS x FOUR ROW COUNTS — the two laws, measured on the engine
# ---------------------------------------------------------------------------
#
# L-A SCRIPT INVARIANCE: the same visual state must get the same verdict in
# every script. L-B SHAPE INVARIANCE: the same sheet must get the same verdict
# at 0, 1, 3 and 25 rows.
#
# This rule failed both, and the failures were each other's mirror: the
# legitimate signature block was condemned in four of nine scripts and kept in
# five, and the per-record defect was named at 3 and 25 rows and excused at 1.
# Both came from quantities that are not the report's — the ink WIDTH a script
# gives a short line, and whether a document with one record repeats anything.
#
# The wording below is invented for this repo, one bundle per script, and each
# bundle carries the face its glyphs need (the ReportViewer default has no
# East-Asian coverage and prints tofu, which is ink a reader cannot read).

SCRIPT_WORDING = {
    "ascii": {
        "face": "Arial",
        "prose": ("The annual accounting package for the reporting period "
                  "has been received and reviewed by this office, and the "
                  "enclosed schedule sets out the amounts assessed for each "
                  "category of activity."),
        "sig": ["Yours faithfully,", "R. Whitfield, Deputy Registrar",
                "Office of Assessments and Records"],
        "signoff": "Sincerely,",
        "enc": ("Enclosed with this notice is the schedule of fees that "
                "applies to the reporting period."),
    },
    "latin_accented": {
        "face": "Arial",
        "prose": ("Le dossier comptable annuel de la période visée a été reçu "
                  "et examiné par ce bureau, et le barème ci-joint présente "
                  "les montants établis pour chaque catégorie d'activité."),
        "sig": ["Veuillez agréer nos salutations,",
                "R. Deschênes, greffier adjoint",
                "Bureau des évaluations et des registres"],
        "signoff": "Cordialement,",
        "enc": ("Vous trouverez ci-joint le barème des droits applicable à "
                "la période visée."),
    },
    "greek": {
        "face": "Arial",
        "prose": ("Ο ετήσιος λογιστικός φάκελος της περιόδου αναφοράς "
                  "παραλήφθηκε και εξετάστηκε από την υπηρεσία μας, και ο "
                  "συνημμένος πίνακας παραθέτει τα ποσά που βεβαιώθηκαν ανά "
                  "κατηγορία δραστηριότητας."),
        "sig": ["Με τιμή,", "Ρ. Βλαχάκης, αναπληρωτής γραμματέας",
                "Γραφείο Βεβαιώσεων και Μητρώων"],
        "signoff": "Με εκτίμηση,",
        "enc": ("Συνημμένα με την παρούσα ειδοποίηση θα βρείτε τον πίνακα "
                "τελών που ισχύει για την περίοδο αναφοράς."),
    },
    "cyrillic": {
        "face": "Arial",
        "prose": ("Годовой учётный пакет за отчётный период получен и "
                  "рассмотрен нашим управлением, а в прилагаемом перечне "
                  "указаны суммы, начисленные по каждой категории "
                  "деятельности."),
        "sig": ["С почтением,", "Р. Ветлицкий, заместитель регистратора",
                "Управление начислений и учёта"],
        "signoff": "С уважением,",
        "enc": ("К настоящему уведомлению прилагается перечень сборов, "
                "действующий в отчётном периоде."),
    },
    "hebrew": {
        "face": "Arial",
        "prose": ("החבילה החשבונאית השנתית לתקופת הדיווח התקבלה ונבדקה על ידי "
                  "משרד זה, והלוח המצורף מפרט את הסכומים שנקבעו לכל קטגוריית "
                  "פעילות."),
        "sig": ["בכבוד רב,", "ר. ולדמן, סגן הרשם", "לשכת השומות והרישומים"],
        "signoff": "בברכה,",
        "enc": "מצורף להודעה זו לוח האגרות החל על תקופת הדיווח.",
    },
    "arabic": {
        "face": "Arial",
        "prose": ("استلم هذا المكتب الحزمة المحاسبية السنوية لفترة التقرير "
                  "وراجعها، ويبين الجدول المرفق المبالغ المقررة لكل فئة من "
                  "فئات النشاط."),
        "sig": ["مع فائق الاحترام،", "ر. الوصيفي، مسجل مساعد",
                "مكتب التقديرات والسجلات"],
        "signoff": "مع التحية،",
        "enc": "مرفق بهذا الإشعار جدول الرسوم المطبق على فترة التقرير.",
    },
    "thai": {
        "face": "Leelawadee UI",
        "prose": ("สำนักงานนี้ได้รับและตรวจสอบชุดเอกสารบัญชีประจำปีสำหรับรอบ"
                  "รายงานแล้ว และตารางที่แนบมาแสดงจำนวนเงินที่ประเมินไว้สำหรับ"
                  "กิจกรรมแต่ละประเภท"),
        "sig": ["ขอแสดงความนับถืออย่างสูง", "ร. วัฒนกุล รองนายทะเบียน",
                "สำนักงานประเมินและทะเบียน"],
        "signoff": "ด้วยความนับถือ",
        "enc": "แนบมาพร้อมหนังสือฉบับนี้คือตารางค่าธรรมเนียมที่ใช้กับรอบรายงาน",
    },
    "hangul": {
        "face": "Malgun Gothic",
        "prose": ("보고 기간의 연간 회계 서류 묶음은 본 사무소가 접수하여 "
                  "검토하였으며, 동봉된 표에는 활동 범주별로 산정된 금액이 "
                  "기재되어 있습니다."),
        "sig": ["삼가 아룁니다,", "박 원식, 부등기관", "산정 및 기록 사무소"],
        "signoff": "감사합니다,",
        "enc": "이 통지서에는 보고 기간에 적용되는 수수료 표가 동봉되어 있습니다.",
    },
    "cjk": {
        "face": "MS Gothic",
        "prose": ("報告期間に係る年次会計書類一式は当事務所が受領し審査いた"
                  "しました。同封の表には、活動の区分ごとに算定された金額を"
                  "記載しております。"),
        "sig": ["謹白", "白鷺 律夫、副登記官", "査定記録事務所"],
        "signoff": "敬具",
        "enc": "本通知には、報告期間に適用される手数料表を同封しております。",
    },
}


def _closing_letter_in(script):
    """The signature-block arm in one script: nine prose sheets' worth of body
    and a three-line closing block that falls on a sheet of its own."""
    w = SCRIPT_WORDING[script]
    face = w["face"]
    close = "".join(
        _txt(f"B_C{i}", text, 0.4, round(10.1 + i * 0.45, 2), 4.0, 0.25, face)
        for i, text in enumerate(w["sig"]))
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<report name="CLOSINGX" DTDVersion="9.0.2.0.10">'
        '<data><dataSource name="Q_MAIN">'
        '<select><![CDATA[select code, nm from t]]></select>'
        '<group name="G_MAIN"><dataItem name="CODE" datatype="vchar2"/>'
        '<dataItem name="NM" datatype="vchar2"/></group></dataSource></data>'
        '<layout><section name="main"><body height="12.0">'
        '<frame name="M_LET"><geometryInfo x="0.05" y="0.2" width="7.4" '
        'height="8.6"/>'
        + "".join(_txt(f"B_P{i}", f"{w['prose']} {i}",
                       0.1, round(0.4 + i * 0.9, 2), 6.0, 0.8, face)
                  for i in range(9))
        + '</frame><frame name="M_SIGN"><geometryInfo x="0.05" y="10.0" '
          'width="7.4" height="1.6"/>' + close +
        '</frame></body></section></layout></report>')


def _record_letter_in(script):
    """The per-record letter in one script — one record to a sheet, the
    closing word printed once inside the record where it belongs."""
    w = SCRIPT_WORDING[script]
    face = w["face"]
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<report name="SPARSERECX" DTDVersion="9.0.2.0.10">'
        '<data><dataSource name="Q_LET">'
        '<select><![CDATA[select acct_no, addressee from letter_queue]]>'
        '</select>'
        '<group name="G_LET"><dataItem name="ACCT_NO" datatype="vchar2"/>'
        '<dataItem name="ADDRESSEE" datatype="vchar2"/></group>'
        '</dataSource></data>'
        '<layout><section name="main" width="8.50000">'
        '<body width="7.50000" height="9.40000">'
        '<repeatingFrame name="R_LET" source="G_LET" printDirection="down" '
        'maxRecordsPerPage="1" minWidowRecords="1" columnMode="no">'
        '<geometryInfo x="0.00000" y="0.00000" width="7.50000" '
        'height="9.00000"/>'
        '<generalLayout verticalElasticity="variable"/>'
        + _txt("B_BODY", w["prose"], 0.3, 0.4, 6.0, 0.2, face)
        + _fld("F_ADDR", "ADDRESSEE", 0.3, 3.0, 4.0, 0.2, face)
        + _fld("F_ACCT", "ACCT_NO", 0.3, 3.4, 4.0, 0.2, face)
        + _txt("B_SIGNOFF", w["signoff"], 0.3, 6.4, 0.7, 0.2, face)
        + _txt("B_ENC", w["enc"], 0.3, 7.2, 6.0, 0.2, face)
        + '</repeatingFrame></body></section></layout></report>')


@pytest.fixture(scope="module")
def scripts(tmp_path_factory):
    """Both arms, nine scripts, four row counts, rendered once."""
    if not _LIB_OK:
        pytest.skip("ReportViewer DLLs not present")
    out = tmp_path_factory.mktemp("sparse-scripts")
    built = {}
    for name in SCRIPT_WORDING:
        block = convert(_closing_letter_in(name).encode(),
                        f"sig-{name}.xml")["rdl_xml"]
        record = convert(_record_letter_in(name).encode(),
                         f"rec-{name}.xml")["rdl_xml"]
        arms = {
            "signature_block": block,
            "record_doctored": _word_block_per_record(
                record, SCRIPT_WORDING[name]["signoff"]),
        }
        for arm, rdl in arms.items():
            path = out / f"{name}-{arm}.rdl"
            path.write_text(rdl, encoding="utf-8")
            for rows in SHAPES:
                pdf = out / f"{name}-{arm}-{rows}.pdf"
                res = render_rdl(path, pdf, rows=rows)
                assert res.get("ok"), (
                    f"{name}/{arm} rows={rows}: {res.get('log', '')[-300:]}")
                built[(name, arm, rows)] = {
                    "m": bm.measure_pdf(pdf, rdl_xml=rdl,
                                        mode=res.get("mode")),
                    "pdf": pdf, "rdl": rdl, "mode": res.get("mode")}
    return built


def _ratio(measure):
    cover = [c for c in measure["coverage"] if c > 0]
    return max(cover) / min(cover) if len(cover) > 1 else 1.0


@_needs_engine
@pytest.mark.parametrize("script", sorted(SCRIPT_WORDING))
@pytest.mark.parametrize("rows", SHAPES)
def test_engine_a_signature_block_is_kept_in_every_script(scripts, script,
                                                          rows):
    """L-A, the false-positive direction. One artifact, one closing block,
    nine languages: the sheet is legitimate in all of them, so it is flagged in
    none of them."""
    m = scripts[(script, "signature_block", rows)]["m"]
    assert m["sparse"] == [], (
        f"{script} rows={rows}: a legitimate closing block was called a "
        f"ruined sheet — sparse={m['sparse']} "
        f"coverage={[round(c, 5) for c in m['coverage'][:4]]}")


@_needs_engine
def test_engine_the_starvation_ratio_alone_decides_it_by_language(scripts):
    """...and this is what the leg that keeps it had to replace.

    The SAME closing block, measured as ink, is a different distance from its
    own document's fullest sheet in every language — because how wide a script
    sets three short lines is a fact about the language, while the body prose
    beside it is pinned to its box in all of them. The two populations this
    rule has to separate overlap and INVERT: the legitimate block reaches
    further from the line in Arabic than the word-only defect does in CJK.

    So a decade on that ratio condemns the block in some languages and keeps it
    in others, which is the failure this file exists to prevent — and it is why
    the verdict is settled by what the document's own fuller sheets carry."""
    ratios = {s: _ratio(scripts[(s, "signature_block", 3)]["m"])
              for s in SCRIPT_WORDING}
    assert max(ratios.values()) > 10.0 > min(ratios.values()), (
        "the fixture no longer straddles the decade across scripts, so it "
        f"stops proving that a ratio cannot decide it: {ratios}")
    assert all(scripts[(s, "signature_block", 3)]["m"]["sparse"] == []
               for s in SCRIPT_WORDING), "the shipped rule must keep them all"


@_needs_engine
@pytest.mark.parametrize("script", sorted(SCRIPT_WORDING))
@pytest.mark.parametrize("rows", SHAPES)
def test_engine_the_defect_is_named_at_every_row_count_in_every_script(
        scripts, script, rows):
    """L-B, and L-A for the miss direction: the per-record defect — a sheet
    per record carrying nothing but the letter's own closing word — is NAMED
    at 0, 1, 3 and 25 rows, in every script, once per record.

    Named, not necessarily by this rule: where the closing word's glyphs do
    not decode, the repeat rule above (section (B)) reads it as page furniture
    and the sheets come back ``chrome_only``, so the BLANK gate names them
    instead. Both are defect verdicts on the same sheets and the count is the
    same; which column speaks is a fact about rule (B)'s text matching, not
    about this one. What may never happen — and is what this asserts — is that
    the sheets are named in one script and not in another, or at one row count
    and not another."""
    m = scripts[(script, "record_doctored", rows)]["m"]
    named = sorted(set(m["sparse"]) | set(m["blank"]))
    assert len(named) == rows, (
        f"{script} rows={rows}: expected {rows} ruined sheets, named "
        f"{len(named)} — sparse={m['sparse'][:6]} blank={m['blank'][:6]} "
        f"of {m['pages']} sheets")


@_needs_engine
@pytest.mark.parametrize("script", sorted(SCRIPT_WORDING))
def test_engine_the_sparse_verdict_does_not_move_with_the_row_count(scripts,
                                                                    script):
    """L-B stated directly: for one artifact in one script, whether a sheet of
    a given kind is sparse is settled by the ARTIFACT, so the per-record
    defect's count is exactly the record count and the legitimate block's is
    zero at every shape.

    The reading this replaces excused a sheet that carried any mark no other
    sheet carried, which at one record is every mark on every sheet: the same
    starved sheet came back flagged at 3 and 25 rows and clean at 1."""
    defect = {rows: len(
        scripts[(script, "record_doctored", rows)]["m"]["sparse"])
        for rows in SHAPES}
    block = {rows: len(
        scripts[(script, "signature_block", rows)]["m"]["sparse"])
        for rows in SHAPES}
    reached = {rows: n for rows, n in defect.items() if n}
    assert not reached or all(n == rows for rows, n in reached.items()), (
        f"{script}: the defect's count does not track the records: {defect}")
    assert set(block.values()) == {0}, (
        f"{script}: the legitimate block's verdict moved with the row "
        f"count: {block}")


# ---------------------------------------------------------------------------
# MUTATION PROOFS — each leg shown load-bearing against the fixture the OTHER
# one passes, so neither is decoration
# ---------------------------------------------------------------------------

def _marks_of(m, rdl_xml, mode):
    invented = (bm.invented_placeholder_texts(rdl_xml)
                if mode in (None, "layout") else set())
    return bm.content_ink_marks(m["texts"], rdl_xml, invented,
                                bm.page_image_marks(None, m["ink"]), m["ink"])


def _legs(m, rdl_xml, mode, starve=True, data="cells"):
    """The shipped rule with ONE leg swapped for the reading it replaced.

    ``starve=False``  deletes the starvation comparison.
    ``data="binary"`` restores the exemption the cell count replaced: any
                      mark a data region supplied excuses the sheet.
    ``data="ink"``    measures the document's data as INK instead of cells.
    ``data=None``     deletes the data leg entirely."""
    marks = _marks_of(m, rdl_xml, mode)
    pages = m["ink"] or [None] * len(marks)
    factor = 10.0 ** bm.SPARSE_DECADES
    covers = m["coverage"]
    top = max(covers, default=0.0)
    starved = [top > 0 and c * factor <= top for c in covers]
    elsewhere = {mk["sig"] for j, page in enumerate(marks)
                 if not starved[j] for mk in page}
    if data == "ink":
        amount = [bm.sheet_ink_coverage([mk for mk in page if mk["data"]], pg)
                  for page, pg in zip(marks, pages)]
    else:
        amount = [bm.sheet_data_cells(page) for page in marks]
    most = max(amount, default=0)
    out = []
    for i, page in enumerate(marks):
        if m["classes"][i] != "content" or not page:
            continue
        if starve and not starved[i]:
            continue
        if bm._says_something_of_its_own(page, elsewhere):
            continue
        if data == "binary":
            if any(mk["data"] for mk in page):
                continue
        elif data is not None and amount[i] * factor > most:
            continue
        out.append(i + 1)
    return out


def _sparse_without_the_starved_leg(m, rdl_xml, mode):
    """The rule with the STARVED leg deleted."""
    return _legs(m, rdl_xml, mode, starve=False)


@_needs_engine
def test_mutation_dropping_the_starved_leg_flags_a_real_signature_block(engine):
    """Without the starvation comparison, a real signature block — the report's
    own closing, reprinted on every record's sheet — reads as a ruined sheet.
    The leg is what tells "short" from "empty"."""
    built = engine["signature_block"]
    m = _measured(engine, "signature_block", 3)
    pdf, mode = built["pdf"][3]
    without = _sparse_without_the_starved_leg(m, built["rdl"], mode)
    assert without, (
        "the signature block says something of its own after all, so this "
        "fixture cannot show the starved leg is load-bearing")
    assert m["sparse"] == [], "the shipped rule must keep it"


@_needs_engine
def test_mutation_dropping_the_originality_leg_flags_a_signed_short_page(engine):
    """Without the originality half, the starvation comparison alone condemns
    a signature line that carries the signatory from the data — a legitimately
    short final page, and one a rail that flagged it would teach people to
    ignore."""
    m = _measured(engine, "signature_line", 3)
    cover = m["coverage"]
    starved = [i + 1 for i, c in enumerate(cover)
               if m["classes"][i] == "content" and c > 0
               and c * (10.0 ** bm.SPARSE_DECADES)
               <= max(x for j, x in enumerate(cover) if j != i)]
    assert starved, (
        "the signed short page is not starved, so this fixture cannot show "
        "the originality leg is load-bearing")
    assert m["sparse"] == [], "the shipped rule must keep it"


@_needs_engine
@pytest.mark.parametrize("rows", SHAPES)
def test_mutation_the_binary_data_exemption_lets_the_orphaned_cell_walk(
        engine, rows):
    """THE 29x HOLE, reproduced. Swap the cell count back for the exemption it
    replaced — one mark a data region supplied excuses the sheet — and every
    orphaned-cell sheet passes, at every row count."""
    built = engine["card_orphan"]
    m = _measured(engine, "card_orphan", rows)
    _pdf, mode = built["pdf"][rows]
    walked = _legs(m, built["rdl"], mode, data="binary")
    assert walked == [], (
        f"rows={rows}: the exemption no longer excuses these sheets, so this "
        f"fixture stops reproducing the hole: {walked}")
    assert len(m["sparse"]) == rows, "the shipped rule must name them"


@_needs_engine
@pytest.mark.parametrize("rows", SHAPES)
def test_mutation_measuring_the_data_as_ink_condemns_a_sheet_of_cells(
        engine, rows):
    """...and the other half of the same leg. Measure the document's data as
    INK rather than as cells and the grid sheet — the same ink as the orphan
    sheet, three times the cells — is condemned with it.

    The two fixtures paint IDENTICAL ink (a placeholder repeated is one
    distinct mark), so no measure of ink can tell them apart, and only one of
    them is an empty sheet."""
    built = engine["card_grid"]
    m = _measured(engine, "card_grid", rows)
    _pdf, mode = built["pdf"][rows]
    condemned = _legs(m, built["rdl"], mode, data="ink")
    if rows:
        assert condemned, (
            f"rows={rows}: reading the data as ink no longer condemns the "
            "grid, so this fixture stops proving the leg counts cells")
    assert m["sparse"] == [], "the shipped rule must keep it"


@_needs_engine
@pytest.mark.parametrize("rows", SHAPES)
def test_mutation_counting_every_expression_as_data_ink_hides_the_defect(
        engine, rows, monkeypatch):
    """The emitter writes the letter's closing as ``="Sincerely,"``. Treat
    every expression as data-bound — the reading that skips
    ``_paints_only_its_own_literals`` — and that literal becomes 'ink a data
    region supplied', so every word-only sheet carries data and passes.
    Measured at every row count, because the defect is at every row count."""
    built = engine["letter_doctored"]
    pdf, mode = built["pdf"][rows]
    seeing = bm.measure_pdf(pdf, rdl_xml=built["rdl"], mode=mode)["sparse"]
    bm._DATA_INK_CACHE.clear()
    monkeypatch.setattr(bm, "_paints_only_its_own_literals",
                        lambda expr, painted: False)
    blind = bm.measure_pdf(pdf, rdl_xml=built["rdl"], mode=mode)["sparse"]
    bm._DATA_INK_CACHE.clear()
    assert blind == [] and seeing == [2, 3, 4], (
        f"rows={rows}: the literal-only test is what finds these sheets — "
        f"blind={blind} seeing={seeing}")


@_needs_engine
def test_mutation_dropping_the_wording_leg_condemns_the_block_by_language(
        scripts, monkeypatch):
    """The leg that keeps the legitimate closing block, and the proof that
    what remains without it is a LANGUAGE-DEPENDENT verdict.

    Delete "does this sheet say anything of its own" and the decision falls
    back to the starvation ratio — which condemns the same three lines in some
    scripts and keeps them in others, on one artifact whose only difference is
    the language its wording is written in. That split is the failure this leg
    exists to remove, so the mutation must show BOTH: some scripts condemned
    and some not."""
    monkeypatch.setattr(bm, "_says_something_of_its_own",
                        lambda page_marks, elsewhere: False)
    verdicts = {}
    for name in SCRIPT_WORDING:
        cell = scripts[(name, "signature_block", 3)]
        verdicts[name] = bm.measure_pdf(cell["pdf"], rdl_xml=cell["rdl"],
                                        mode=cell["mode"])["sparse"]
    condemned = {k for k, v in verdicts.items() if v}
    assert condemned, (
        "without the leg nothing is condemned, so this fixture cannot show it "
        f"is load-bearing: {verdicts}")
    assert condemned != set(SCRIPT_WORDING), (
        "without the leg EVERY script is condemned, so the fixture no longer "
        f"shows the verdict splitting by language: {sorted(condemned)}")


@_needs_engine
def test_mutation_matching_marks_by_equality_loses_the_defect_by_language(
        scripts, monkeypatch):
    """The other script-shaped extractor fact. One printed closing line comes
    back as one run on the sheet it belongs to and as that run plus its decoded
    punctuation on the sheet the defect put it on — so an equality-only match
    reads the reprint as something the sheet says of its own, and only in the
    scripts where that happens.

    Measured: with equality alone the per-record defect goes unnamed in Greek,
    Cyrillic, Thai and Hangul while staying named in ascii, on the same
    artifact."""
    monkeypatch.setattr(bm, "_same_ink", lambda a, b: a == b)
    lost = []
    for name in SCRIPT_WORDING:
        for rows in (1, 3, 25):
            cell = scripts[(name, "record_doctored", rows)]
            m = bm.measure_pdf(cell["pdf"], rdl_xml=cell["rdl"],
                               mode=cell["mode"])
            shipped = cell["m"]
            if (len(set(m["sparse"]) | set(m["blank"]))
                    < len(set(shipped["sparse"]) | set(shipped["blank"]))):
                lost.append((name, rows))
    assert lost, ("equality alone no longer loses the defect anywhere, so "
                  "containment is not shown load-bearing")
    assert len(lost) < len(SCRIPT_WORDING) * 3, (
        "equality loses the defect EVERYWHERE, so the fixture no longer shows "
        f"the loss splitting by language: {lost}")


# ---------------------------------------------------------------------------
# The decade: a unit, checked for stability, not a threshold tuned to a case
# ---------------------------------------------------------------------------

_ARMS = [("letter", 0), ("letter", 1), ("letter", 3), ("letter", 25),
         ("letter_doctored", 0), ("letter_doctored", 1),
         ("letter_doctored", 3), ("letter_doctored", 25),
         ("record", 3), ("record", 25),
         ("record_doctored", 3), ("record_doctored", 25),
         ("signature_block", 3), ("signature_line", 3), ("ledger", 25),
         ("card", 3), ("card", 25), ("card_orphan", 1), ("card_orphan", 3),
         ("card_orphan", 25), ("card_grid", 3), ("card_grid", 25)]


@_needs_engine
@pytest.mark.parametrize("factor", [2, 3, 4, 6, 8, 10, 12, 16])
def test_the_decade_is_a_unit_not_a_tuned_threshold(engine, factor,
                                                    monkeypatch):
    """Every factor across the window this class has room in gives the SAME
    verdicts on the whole discrimination suite.

    THE WINDOW IS NOW ONE-SIDED, and that is the point of the change this
    file records. It used to run 6x to 25x: below 6 the ratio started
    condemning the legitimate signature block, above 25 it started missing the
    defect, and the decade was the round number between two verdicts a
    threshold was making. The legitimate cases are no longer decided by the
    ratio at all — see
    ``test_no_factor_at_all_decides_the_signature_block``, which keeps them at
    every factor from 2x to 40x in all nine scripts — so the bottom is gone
    and every factor from 2x up gives identical verdicts. What remains is the
    top, where the defect itself stops being starved: measured just past 25x,
    and shown below."""
    baseline = {(n, r): _measured(engine, n, r)["sparse"] for n, r in _ARMS}
    monkeypatch.setattr(bm, "SPARSE_DECADES", math.log10(factor))
    moved = {k: v for k, v in baseline.items()
             if _measured(engine, *k)["sparse"] != v}
    assert not moved, f"factor {factor} moved verdicts: {moved}"


@_needs_engine
@pytest.mark.parametrize("factor", [2, 4, 6, 10, 16, 25, 40])
def test_no_factor_at_all_decides_the_signature_block(scripts, factor,
                                                      monkeypatch):
    """THE PROOF THAT REPLACED A DEAD ONE, and it is stricter.

    This file used to assert that at 4x — under the measured window — the
    starvation ratio starts condemning a real signature block, which was
    offered as evidence that the decade is bounded by measurement. It was
    evidence of something else: that the ratio was DECIDING that fixture, in
    ascii, where it happened to land at 5.2x. Rendered in nine scripts the
    same three lines land anywhere from 4.2x to 16.5x, so the old proof's
    "bottom of the window" is a different number in every language and in four
    of them the shipped decade was already past it — the block was condemned
    in ascii, Cyrillic, Arabic and CJK and kept in the other five.

    The rule no longer asks a ratio which KIND of short sheet this is, so
    there is no bottom to find: at every factor from 2x to 40x, in every one
    of the nine scripts, the legitimate closing block is kept. The window
    proof that remains is the one that still means something — that the
    factor does not move any verdict across the measured window (below) — and
    the top of it, where the defect walks.

    This also settles the OTHER way a ratio verdict used to move: with how
    full the sheet's siblings happen to be. Scaling the factor is the same
    arithmetic as scaling the reference, so a verdict that holds across a
    twentyfold sweep of the factor cannot be moved by a document whose fuller
    sheets carry more or less. (Measured directly too: the same closing block
    behind letters of 2, 4, 6, 9, 12 and 16 paragraphs moves the ratio from
    3.0x to 5.2x and back to 1.0x and is kept at every one of them.)"""
    monkeypatch.setattr(bm, "SPARSE_DECADES", math.log10(factor))
    flagged = {}
    for name in SCRIPT_WORDING:
        cell = scripts[(name, "signature_block", 3)]
        verdict = bm.measure_pdf(cell["pdf"], rdl_xml=cell["rdl"],
                                 mode=cell["mode"])["sparse"]
        if verdict:
            flagged[name] = verdict
    assert not flagged, (
        f"factor {factor}x condemned a legitimate closing block: {flagged}")


@_needs_engine
@pytest.mark.parametrize("factor", [30, 40])
def test_a_factor_above_the_window_lets_the_defect_through(engine, factor,
                                                           monkeypatch):
    """...and the top is real and measured: the word-only sheets sit 25.8x
    below their letter's own sheet, so at 30x they are no longer starved and
    the rule stops naming them. A factor is a rounding choice below that and a
    verdict above it."""
    monkeypatch.setattr(bm, "SPARSE_DECADES", math.log10(factor))
    assert _measured(engine, "letter_doctored", 3)["sparse"] == [], (
        f"a factor above the measured window ({factor}x) must start missing "
        "the defect; if it does not, the window is wider than recorded")
