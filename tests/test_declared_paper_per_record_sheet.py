"""DECLARED-PAPER class: a per-record DOCUMENT prints on the paper its source
declares — never on one enormous sheet per record.

Root cause (truth-measured): the per-record FORM arm already kept the declared
paper and let an oversize record paginate, but the per-record
CERTIFICATE/LETTER arm budgeted ``max(11, record + chrome + slack)``
unconditionally. A letter-archetype source declaring a paper-sized body whose
record content ran long therefore emitted a single ~24in <PageHeight> and
rendered one gigantic sheet per record, while the Oracle-rendered truth
paginates the same document on ordinary 8.5x11 sheets.

Guarded here (all declaration-driven, synthetic fixtures only):

* the declared paper wins over the grow-the-sheet budget for a letter whose
  record outgrows one sheet, and the record's frames are released so the
  engine SPLITS them in place instead of pushing them whole to a fresh sheet
  (the blank-leader-page failure mode);
* the synthesized criteria COVER stays on ONE sheet, because the record data
  region is positioned flush beneath it with no page break of its own — a
  two-sheet cover strands the data region on the spill sheet and the detail
  break then wastes it;
* a record that FITS the declared sheet once the synthesized page furniture
  stops eating into it prints ONE per sheet (it does not flow), and
* the differential: with NO paper-sized body declared, the grow-the-page
  budget still holds (load-bearing for records that must stay whole).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402

_PARA_A = (
    "This notice is issued under the terms of the agreement referenced above "
    "and describes the obligations that apply to the recipient named on this "
    "page.\nEvery obligation listed below remains in force until the program "
    "office records a written release."
)
_PARA_B = (
    "Payment is due within thirty days of the issue date printed above. "
    "Remit the full balance to the address shown, and include the reference "
    "number so the receipt can be posted.\nQuestions about this notice should "
    "be directed to the program office at the telephone number listed."
)
_PARA_C = (
    "Records supporting this notice are retained by the program office for "
    "the period required by the applicable retention schedule.\nCopies may "
    "be requested in writing at any time during that period."
)


def _letter_xml(foot_y: float, body_h: str = '10.60000',
                cover_pairs: int = 6, cover_notes: int = 0) -> bytes:
    """A per-record LETTER: prose paragraphs (letter archetype), a criteria
    section_header (the cover), and a closing frame whose y decides whether
    one record outgrows the declared sheet.

    ``cover_pairs`` grows the criteria stack; ``cover_notes`` adds multi-line
    cover notes, each of which RESERVES one row per line while occupying only
    one — the empty vertical gap a long criteria sheet leaves behind."""
    def _row(i, y):
        return (
            f'<text name="H_L{i}"><geometryInfo x="0.50000" y="{y:.5f}" '
            f'width="2.00000" height="0.22000"/><textSegment>'
            f'<font face="Arial" size="10"/><string><![CDATA[Criteria {i}:]]>'
            f'</string></textSegment></text>'
            f'<field name="H_V{i}" source="P_KEY">'
            f'<geometryInfo x="2.80000" y="{y:.5f}" '
            f'width="3.00000" height="0.22000"/></field>')

    def _note(i, y):
        body = "\n".join(
            f"Note {i} line {n}: this cover note wraps across several "
            f"printed lines of the criteria sheet." for n in range(6))
        return (
            f'<text name="H_NOTE{i}"><geometryInfo x="0.50000" y="{y:.5f}" '
            f'width="6.00000" height="0.22000"/><textSegment>'
            f'<font face="Arial" size="10"/><string><![CDATA[{body}]]>'
            f'</string></textSegment></text>')

    cover_rows, y = "", 0.60
    per_block = max(1, cover_pairs // max(1, cover_notes + 1))
    made = 0
    for blk in range(cover_notes + 1):
        take = per_block if blk < cover_notes else cover_pairs - made
        for _ in range(max(0, take)):
            cover_rows += _row(made, y)
            made += 1
            y += 0.30
        if blk < cover_notes:
            cover_rows += _note(blk, y)
            y += 0.30
    return (
        '<?xml version="1.0"?>'
        '<report name="NOTICE_DOC" DTDVersion="9.0.2.0.10">'
        '<data><dataSource name="Q_DOC">'
        '<select><![CDATA[select acct_no, addressee from notices]]></select>'
        '<group name="G_DOC">'
        '<dataItem name="ACCT_NO" datatype="vchar2"/>'
        '<dataItem name="ADDRESSEE" datatype="vchar2"/>'
        '</group></dataSource></data>'
        '<parameters><parameter name="P_KEY" datatype="character">'
        '<initialValue><![CDATA[A]]></initialValue></parameter></parameters>'
        '<layout>'
        '<section name="header" width="8.50000">'
        '<body width="8.00000" height="10.75000">'
        f'{cover_rows}'
        '</body></section>'
        '<section name="main" width="8.50000">'
        f'<body width="7.50000" height="{body_h}">'
        '<repeatingFrame name="R_DOC" source="G_DOC" printDirection="down" '
        'maxRecordsPerPage="1" minWidowRecords="1" columnMode="no">'
        '<geometryInfo x="0.00000" y="0.00000" width="7.50000" '
        'height="9.00000"/>'
        '<generalLayout verticalElasticity="variable"/>'
        '<field name="F_ADDR" source="ADDRESSEE">'
        '<geometryInfo x="0.30000" y="0.30000" width="4.00000" '
        'height="0.20000"/></field>'
        '<field name="F_ACCT" source="ACCT_NO">'
        '<geometryInfo x="0.30000" y="0.70000" width="4.00000" '
        'height="0.20000"/></field>'
        '<text name="B_P1"><geometryInfo x="0.30000" y="1.40000" '
        'width="6.80000" height="1.20000"/><textSegment>'
        f'<font face="Arial" size="10"/><string><![CDATA[{_PARA_A}]]></string>'
        '</textSegment></text>'
        '<text name="B_P2"><geometryInfo x="0.30000" y="3.00000" '
        'width="6.80000" height="1.20000"/><textSegment>'
        f'<font face="Arial" size="10"/><string><![CDATA[{_PARA_B}]]></string>'
        '</textSegment></text>'
        '<frame name="M_CLOSE">'
        f'<geometryInfo x="0.00000" y="{foot_y:.5f}" width="7.50000" '
        'height="1.40000"/>'
        '<text name="B_P3">'
        f'<geometryInfo x="0.30000" y="{foot_y + 0.20:.5f}" '
        'width="6.80000" height="1.00000"/><textSegment>'
        f'<font face="Arial" size="10"/><string><![CDATA[{_PARA_C}]]></string>'
        '</textSegment></text></frame>'
        '</repeatingFrame>'
        '</body></section></layout></report>'
    ).encode()


def _page_height(rdl: str) -> float:
    m = re.search(r"<PageHeight>([0-9.]+)in</PageHeight>", rdl)
    assert m, "no PageHeight emitted"
    return float(m.group(1))


def _record_rect_keeptogether(rdl: str) -> str:
    m = re.search(r'<Rectangle Name="Rect_RecordPage">.{0,200}?'
                  r'<KeepTogether>(\w+)</KeepTogether>', rdl, re.S)
    assert m, "no record rect emitted"
    return m.group(1)


def test_oversize_record_keeps_declared_paper_and_paginates():
    """A per-record LETTER whose record outgrows the sheet its source declares
    keeps the DECLARED paper and paginates; it must never grow the sheet to
    swallow one record whole."""
    rdl = convert(_letter_xml(foot_y=18.0))["rdl_xml"]
    ph = _page_height(rdl)
    assert abs(ph - 11.0) < 0.01, (
        f"declared-paper letter must print on its declared sheet, got {ph}in")


def test_flowing_record_releases_every_inner_frame():
    """Inside a record that is being SPLIT by construction nothing may keep
    together: a descendant frame that still demands to stay whole is pushed to
    a fresh sheet and empties the sheet it left behind."""
    import xml.etree.ElementTree as ET
    rdl = convert(_letter_xml(foot_y=18.0))["rdl_xml"]
    assert _record_rect_keeptogether(rdl) == "false"
    ns = ("{http://schemas.microsoft.com/sqlserver/reporting/"
          "2008/01/reportdefinition}")
    root = ET.fromstring(rdl)
    rec = [e for e in root.iter() if e.get("Name") == "Rect_RecordPage"]
    assert rec, "no record rect emitted"
    frames = list(rec[0].iter(ns + "Rectangle"))
    assert len(frames) >= 3, "fixture must nest frames inside the record"
    for fr in frames:
        assert (fr.findtext(ns + "KeepTogether") or "false") == "false", (
            f"{fr.get('Name')}: a flowing record must not hold a frame that "
            f"demands to stay whole")


def _cover_body(gap: float, rows: int = 24, pitch: float = 0.30,
                row_h: float = 0.26, tablix_top: float = 0.0):
    """A body element shaped like the per-record one: a cover rect of stacked
    rows (with one tall empty gap partway down) and the record data region
    positioned flush beneath it."""
    import xml.etree.ElementTree as ET
    from converter.generators.rdl import _q, _sub
    body = ET.Element(_q("Body"))
    items = _sub(body, "ReportItems")
    cover = _sub(items, "Rectangle")
    cover.set("Name", "Rect_CoverPage")
    _sub(cover, "Top", "0in")
    cri = _sub(cover, "ReportItems")
    y = 0.60
    for i in range(rows):
        tb = _sub(cri, "Textbox")
        tb.set("Name", f"Row_{i}")
        _sub(tb, "Top", f"{y:.2f}in")
        _sub(tb, "Height", f"{row_h:.2f}in")
        y += pitch
        if i == rows // 2:
            y += gap
    cover_h = y - pitch + row_h + 0.40
    _sub(cover, "Height", f"{cover_h:.2f}in")
    tablix = _sub(items, "Tablix")
    tablix.set("Name", "Tablix_Record")
    _sub(tablix, "Top", f"{tablix_top or cover_h:.2f}in")
    _sub(tablix, "Height", "12.00in")
    _sub(body, "Height", f"{(tablix_top or cover_h) + 12.0:.2f}in")
    return body, cover, tablix


def test_cover_is_compressed_onto_one_sheet_when_the_record_flows():
    """The criteria cover carries no page break of its own — the record data
    region sits flush beneath it. A cover taller than one printable sheet
    therefore strands the data region on its spill sheet, which the detail
    break then wastes. Empty vertical gaps in the cover stack are reclaimed
    until it fits; nothing is clipped, dropped or overlapped."""
    from converter.generators.rdl import _fit_cover_to_one_page, _q
    printable = 9.15
    body, cover, tablix = _cover_body(gap=4.0)
    before_h = float(cover.findtext(_q("Height")).replace("in", ""))
    assert before_h > printable, "fixture must overflow one printable sheet"
    _fit_cover_to_one_page(body, printable)
    after_h = float(cover.findtext(_q("Height")).replace("in", ""))
    assert after_h <= printable, (
        f"cover must be brought onto one sheet, got {after_h}in")
    rows = list(cover.find(_q("ReportItems")))
    assert len(rows) == 24, "compression must reclaim whitespace, never rows"
    tops = [float(r.findtext(_q("Top")).replace("in", "")) for r in rows]
    hs = [float(r.findtext(_q("Height")).replace("in", "")) for r in rows]
    assert tops == sorted(tops), "compression must preserve row order"
    for i in range(1, len(tops)):
        assert tops[i] >= tops[i - 1] + hs[i - 1] - 1e-6, (
            "compression must never overlap two rows")
    # The record region and the body follow the cover up by the same delta.
    assert abs(float(tablix.findtext(_q("Top")).replace("in", ""))
               - after_h) < 0.02, "the data region must follow the cover up"
    assert abs(float(body.findtext(_q("Height")).replace("in", ""))
               - (after_h + 12.0)) < 0.02, "the body must shrink with it"


def test_cover_that_already_fits_is_left_alone():
    """Differential: a cover that fits one sheet is byte-identical — the
    compression is a last resort, not a reflow."""
    from converter.generators.rdl import _fit_cover_to_one_page, _q
    body, cover, tablix = _cover_body(gap=3.0, rows=6)
    before = (cover.findtext(_q("Height")),
              [r.findtext(_q("Top")) for r in cover.find(_q("ReportItems"))],
              tablix.findtext(_q("Top")))
    _fit_cover_to_one_page(body, 9.15)
    after = (cover.findtext(_q("Height")),
             [r.findtext(_q("Top")) for r in cover.find(_q("ReportItems"))],
             tablix.findtext(_q("Top")))
    assert before == after


def test_record_that_fits_the_declared_sheet_prints_one_per_sheet():
    """Synthesized page furniture — the fixed half-inch margins standing in
    for a smaller DECLARED inset and EMPTY header/footer bands — must not push
    a record that fits its declared sheet onto two. Such a record keeps the
    declared paper AND keeps together."""
    rdl = convert(_letter_xml(foot_y=8.60))["rdl_xml"]
    ph = _page_height(rdl)
    assert abs(ph - 11.0) < 0.01, f"expected the declared sheet, got {ph}in"
    assert _record_rect_keeptogether(rdl) == "true", (
        "a record that fits the declared sheet must stay whole on one sheet")
    for band in ("PageHeader", "PageFooter"):
        m = re.search(rf"<{band}>(.*?)</{band}>", rdl, re.S)
        assert not (m and "<ReportItems" not in m.group(1)), (
            f"an EMPTY {band} band must not eat into the declared sheet")


def test_without_a_paper_declaration_the_grow_budget_still_holds():
    """Differential: a source that declares NO paper-sized body keeps the
    grow-the-page budget, which is load-bearing for records that must stay
    whole on one (non-standard) sheet."""
    no_paper = _letter_xml(foot_y=18.0).replace(
        b'<body width="7.50000" height="10.60000">', b'<body width="7.50000">')
    rdl = convert(no_paper)["rdl_xml"]
    assert _page_height(rdl) > 11.05, (
        "without a paper-sized declaration the sheet must still grow to hold "
        "one whole record")
