"""PAGE 1 SHARES THE BODY ORIGIN WITH PAGES 2..N.

The <Body> origin is where page ONE begins its content, and the engine
restarts the body at that same origin on every continuation page. So any
lead the generator inserts above the body's first item is spent on page 1
alone: page 1 prints low by exactly the lead while pages 2..N sit at the
origin.

Engine-measured on the per-record family: a 0.10in cosmetic lead put every
span on page 1 exactly 7.2pt below its twin on pages 2 and 3 (record rect
y0 93.45pt on page 1 vs 86.25pt on 2/3) -- and 86.25pt is precisely what
the source declares as its body origin, so the pages WITHOUT the lead were
the correct ones. Two independent report shapes showed the same class.

The second rule guarded here is Oracle's ``templateSection`` attribute: a
<margin> object that names a section belongs to THAT section's pages. Each
section prints its own margin band, so a page-number stamp declared for the
main section must not appear on the criteria page the header section
produces -- and that criteria page is exactly the cover our RDL renders
first. Truth prints no footer there; we stamped one because the attribute
was parsed away.

Everything here is synthetic: no report, column or label from any real
source appears in this file.
"""
from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "tools" / "renderlab"))

from converter import convert  # noqa: E402
from converter.parsers.oracle_xml import parse_oracle_xml  # noqa: E402

NS = ("{http://schemas.microsoft.com/sqlserver/reporting/"
      "2008/01/reportdefinition}")

REC_BODY_Y = 1.10       # per-record fixture's declared <body><location y>
GRID_BODY_Y = 0.75      # column-grid fixture's declared <body><location y>


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def _record_xml(body_y: float = REC_BODY_Y) -> bytes:
    """A per-record document (one sheet per row) whose section declares a
    body origin and a real <margin> chrome band."""
    return (
        '<?xml version="1.0"?>'
        '<report name="RECDOC" DTDVersion="9.0.2.0.10"><data>'
        '<dataSource name="Q_MAIN">'
        '<select canParse="no">'
        '<![CDATA[SELECT ACCT_NO, ACCT_NAME FROM ACCOUNTS]]></select>'
        '<group name="G_MAIN">'
        '<dataItem name="ACCT_NO" datatype="vchar2" columnOrder="1"'
        ' defaultLabel="Account"/>'
        '<dataItem name="ACCT_NAME" datatype="vchar2" columnOrder="2"'
        ' defaultLabel="Name"/>'
        '</group></dataSource></data><layout>'
        '<section name="main" repeatOn="G_MAIN" width="8.50000"'
        ' height="11.00000">'
        '<body width="7.50000" height="8.00000">'
        f'<location x="0.50000" y="{body_y:.5f}"/>'
        '<frame name="M_DOC">'
        '<geometryInfo x="0.00000" y="0.00000" width="7.50000"'
        ' height="4.00000"/>'
        '<text name="B_HEADING">'
        '<geometryInfo x="0.20000" y="0.10000" width="6.00000"'
        ' height="0.22000"/><textSegment>'
        '<font face="Arial" size="12" bold="yes"/>'
        '<string><![CDATA[Statement of Account]]></string>'
        '</textSegment></text>'
        '<field name="F_ACCT_NO" source="ACCT_NO">'
        '<font face="Arial" size="10"/>'
        '<geometryInfo x="0.20000" y="1.00000" width="3.00000"'
        ' height="0.20000"/></field>'
        '<field name="F_ACCT_NAME" source="ACCT_NAME">'
        '<font face="Arial" size="10"/>'
        '<geometryInfo x="0.20000" y="1.40000" width="3.00000"'
        ' height="0.20000"/></field>'
        '</frame></body><margin>'
        '<text name="B_TITLE"><textSettings justify="center" spacing="0"/>'
        '<geometryInfo x="2.50000" y="0.25000" width="3.50000"'
        ' height="0.22000"/><textSegment>'
        '<font face="Arial" size="12" bold="yes"/>'
        '<string><![CDATA[Sample Record Document]]></string>'
        '</textSegment></text>'
        '<text name="B_PAGENO"><textSettings justify="center" spacing="0"/>'
        '<geometryInfo x="3.50000" y="10.40000" width="1.80000"'
        ' height="0.17000"/><textSegment><font face="Arial" size="8"/>'
        '<string><![CDATA[Page &PageNumber]]></string>'
        '</textSegment></text>'
        '</margin></section></layout></report>'
    ).encode()


def _grid_xml(template_section: bool = False, cover: bool = False,
              body_y: float = GRID_BODY_Y) -> bytes:
    """A flat column grid. ``cover`` adds a criteria section_header (which
    is what makes page 1 a cover sheet); ``template_section`` restricts the
    declared page-number stamp to the main section's pages."""
    tsa = ' templateSection="main"' if template_section else ''
    head = (
        '<section name="header" width="8.50000" height="11.00000">'
        '<body width="7.50000" height="9.00000">'
        '<location x="0.50000" y="0.50000"/>'
        '<frame name="M_CRIT">'
        '<geometryInfo x="0.00000" y="0.00000" width="7.00000"'
        ' height="2.00000"/>'
        '<text name="B_C1">'
        '<geometryInfo x="0.20000" y="0.30000" width="1.80000"'
        ' height="0.20000"/><textSegment><font face="Arial" size="10"/>'
        '<string><![CDATA[Start Date:]]></string></textSegment></text>'
        '<field name="F_C1" source="P_FROM"><font face="Arial" size="10"/>'
        '<geometryInfo x="2.20000" y="0.30000" width="2.00000"'
        ' height="0.20000"/></field>'
        '<text name="B_C2">'
        '<geometryInfo x="0.20000" y="0.60000" width="1.80000"'
        ' height="0.20000"/><textSegment><font face="Arial" size="10"/>'
        '<string><![CDATA[End Date:]]></string></textSegment></text>'
        '<field name="F_C2" source="P_TO"><font face="Arial" size="10"/>'
        '<geometryInfo x="2.20000" y="0.60000" width="2.00000"'
        ' height="0.20000"/></field>'
        '</frame></body></section>'
    ) if cover else ''
    return (
        '<?xml version="1.0"?>'
        '<report name="ORIGINGRID" DTDVersion="9.0.2.0.10"><data>'
        '<dataSource name="Q_1"><select canParse="no">'
        '<![CDATA[SELECT ALPHA, BETA FROM T]]></select>'
        '<group name="G_ROW">'
        '<dataItem name="ALPHA" datatype="vchar2" columnOrder="1"'
        ' defaultLabel="Alpha"/>'
        '<dataItem name="BETA" datatype="vchar2" columnOrder="2"'
        ' defaultLabel="Beta"/>'
        '</group></dataSource></data>'
        '<parameter name="P_FROM" datatype="vchar2"/>'
        '<parameter name="P_TO" datatype="vchar2"/>'
        '<layout>' + head +
        '<section name="main" width="8.50000" height="11.00000">'
        '<body width="7.50000" height="9.00000">'
        f'<location x="0.50000" y="{body_y:.5f}"/>'
        '<frame name="M_GRP">'
        '<geometryInfo x="0.00000" y="0.00000" width="7.00000"'
        ' height="0.60000"/>'
        '<frame name="M_HDR">'
        '<geometryInfo x="0.00000" y="0.00000" width="7.00000"'
        ' height="0.20000"/>'
        '<text name="B_ALPHA">'
        '<geometryInfo x="0.00000" y="0.00000" width="3.00000"'
        ' height="0.18000"/><textSegment><font face="Arial" size="9"/>'
        '<string><![CDATA[Alpha]]></string></textSegment></text>'
        '<text name="B_BETA">'
        '<geometryInfo x="3.20000" y="0.00000" width="3.00000"'
        ' height="0.18000"/><textSegment><font face="Arial" size="9"/>'
        '<string><![CDATA[Beta]]></string></textSegment></text>'
        '</frame>'
        '<repeatingFrame name="R_ROW" source="G_ROW" printDirection="down"'
        ' columnMode="no">'
        '<geometryInfo x="0.00000" y="0.25000" width="7.00000"'
        ' height="0.20000"/>'
        '<field name="F_ALPHA" source="ALPHA"><font face="Arial" size="9"/>'
        '<geometryInfo x="0.00000" y="0.25000" width="3.00000"'
        ' height="0.18000"/></field>'
        '<field name="F_BETA" source="BETA"><font face="Arial" size="9"/>'
        '<geometryInfo x="3.20000" y="0.25000" width="3.00000"'
        ' height="0.18000"/></field>'
        '</repeatingFrame></frame></body><margin>'
        '<text name="B_TITLE"><textSettings justify="center" spacing="0"/>'
        '<geometryInfo x="2.50000" y="0.20000" width="3.50000"'
        ' height="0.22000"/><textSegment>'
        '<font face="Arial" size="12" bold="yes"/>'
        '<string><![CDATA[Sample Origin Grid]]></string>'
        '</textSegment></text>'
        f'<text name="B_PAGENO" minWidowLines="1"{tsa}>'
        '<textSettings justify="center" spacing="0"/>'
        '<geometryInfo x="3.50000" y="10.40000" width="1.80000"'
        ' height="0.17000"/><textSegment><font face="Arial" size="8"/>'
        '<string><![CDATA[Page &PageNumber]]></string>'
        '</textSegment></text>'
        '</margin></section></layout></report>'
    ).encode()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _page(rdl: str) -> ET.Element:
    return ET.fromstring(rdl).find(".//" + NS + "Page")


def _in(el, tag: str, default: float = 0.0) -> float:
    if el is None:
        return default
    txt = el.findtext(NS + tag)
    if not txt:
        return default
    try:
        return float(txt.replace("in", "").strip())
    except ValueError:
        return default


def _first_body_item(rdl: str):
    body = ET.fromstring(rdl).find(".//" + NS + "Body")
    items = body.find(NS + "ReportItems") if body is not None else None
    kids = list(items) if items is not None else []
    assert kids, "body emitted no report items"
    return min(kids, key=lambda c: _in(c, "Top"))


def _paper_y_of_first_body_item(rdl: str) -> float:
    """Where the body's first item lands on the SHEET: the page chrome above
    the body (TopMargin + PageHeader height) plus the item's own body Top."""
    page = _page(rdl)
    return (_in(page, "TopMargin")
            + _in(page.find(NS + "PageHeader"), "Height")
            + _in(_first_body_item(rdl), "Top"))


# ---------------------------------------------------------------------------
# (a) the body's first item starts AT the declared body origin
# ---------------------------------------------------------------------------

def test_per_record_body_starts_at_the_declared_body_origin():
    rdl = convert(_record_xml())["rdl_xml"]
    assert abs(_paper_y_of_first_body_item(rdl) - REC_BODY_Y) < 0.005, (
        _paper_y_of_first_body_item(rdl), REC_BODY_Y)


def test_per_record_origin_follows_a_different_declaration():
    """The rule reads the source: move the declared origin and the whole
    chrome-plus-lead sum moves with it."""
    rdl = convert(_record_xml(body_y=1.45))["rdl_xml"]
    assert abs(_paper_y_of_first_body_item(rdl) - 1.45) < 0.005, (
        _paper_y_of_first_body_item(rdl))


def test_column_grid_body_starts_at_the_declared_body_origin():
    rdl = convert(_grid_xml())["rdl_xml"]
    assert abs(_paper_y_of_first_body_item(rdl) - GRID_BODY_Y) < 0.005, (
        _paper_y_of_first_body_item(rdl), GRID_BODY_Y)


def test_no_lead_is_inserted_above_the_first_body_item():
    """Directly: page 2..N restart the body at Top=0, so page 1's first item
    must be at Top=0 too. A nonzero Top here IS the page-1-only offset."""
    for xml in (_record_xml(), _grid_xml()):
        rdl = convert(xml)["rdl_xml"]
        top = _in(_first_body_item(rdl), "Top")
        assert abs(top) < 0.005, (_first_body_item(rdl).get("Name"), top)


# ---------------------------------------------------------------------------
# (b) rendered proof: page 1's spans sit exactly where pages 2..N's do
# ---------------------------------------------------------------------------

try:  # the MS ReportViewer DLLs are optional (public repo / CI)
    from render import lib_ready  # noqa: E402
    _LIB_OK = lib_ready()
except Exception:  # noqa: BLE001
    _LIB_OK = False


@pytest.mark.skipif(not _LIB_OK or sys.platform != "win32",
                    reason="ReportViewer DLLs not fetched (tools/renderlab)")
def test_rendered_page_one_shares_the_body_origin_with_later_pages(tmp_path):
    import fitz  # PyMuPDF
    from rdl_preview import render_to_pdf

    rdl = convert(_record_xml())["rdl_xml"]
    out = tmp_path / "rec.pdf"
    res = render_to_pdf(rdl, out, rows=3)
    assert res.get("ok"), res.get("log", "")[-400:]
    doc = fitz.open(out)
    try:
        assert doc.page_count >= 3, doc.page_count

        def ys(pg):
            # Page-chrome BANDS carry their own first-page rules (a
            # per-record document suppresses the footer on page 1 so a
            # signature never lands on a cover), so the bottom band is out
            # of scope here -- this measures the BODY.
            floor = pg.rect.height * 0.90
            return sorted({round(s["bbox"][1], 2)
                           for b in pg.get_text("dict")["blocks"]
                           for ln in b.get("lines", [])
                           for s in ln["spans"]
                           if s["text"].strip() and s["bbox"][1] < floor})

        first, second, third = ys(doc[0]), ys(doc[1]), ys(doc[2])
    finally:
        doc.close()
    assert second == third, (second, third)      # steady state
    assert first == second, (first, second)      # page 1 is not offset


# ---------------------------------------------------------------------------
# (c) declared templateSection restricts an object to that section's pages
# ---------------------------------------------------------------------------

def test_parser_records_the_declared_template_section():
    report = parse_oracle_xml(_grid_xml(template_section=True))
    decl = {}

    def walk(g):
        for f in (g.fields or []):
            if getattr(f, "in_margin", False):
                decl[f.name] = getattr(f, "template_section", "")
        for c in (g.children or []):
            walk(c)
    for lg in (report.layout or []):
        walk(lg)
    assert decl, "no margin chrome parsed"
    restricted = {n for n, t in decl.items() if t == "main"}
    assert len(restricted) == 1, decl
    # and the objects that declare nothing stay unrestricted
    assert any(t == "" for t in decl.values()), decl


def _footer_prints_on_first_page(rdl: str) -> str:
    pf = _page(rdl).find(NS + "PageFooter")
    assert pf is not None, "no page footer emitted"
    return (pf.findtext(NS + "PrintOnFirstPage") or "").strip()


def test_restricted_footer_is_suppressed_on_the_cover_page():
    rdl = convert(_grid_xml(template_section=True, cover=True))["rdl_xml"]
    assert "Rect_CoverPage" in rdl, "fixture did not produce a cover page"
    assert _footer_prints_on_first_page(rdl) == "false"


def test_undeclared_footer_still_prints_on_the_cover_page():
    """Control: without the attribute the stamp is unrestricted furniture and
    keeps printing on every page -- the suppression is declaration-driven."""
    rdl = convert(_grid_xml(template_section=False, cover=True))["rdl_xml"]
    assert "Rect_CoverPage" in rdl, "fixture did not produce a cover page"
    assert _footer_prints_on_first_page(rdl) == "true"


def test_restricted_footer_still_prints_when_page_one_is_a_main_page():
    """Control: with no cover, page 1 IS a main-section page, so an object
    restricted to the main section belongs there."""
    rdl = convert(_grid_xml(template_section=True, cover=False))["rdl_xml"]
    assert "Rect_CoverPage" not in rdl
    assert _footer_prints_on_first_page(rdl) == "true"


def test_page_number_stamp_survives_on_the_content_pages():
    """The suppression is per-PAGE-ONE only: the declared stamp is still
    emitted into the footer band for pages 2..N."""
    rdl = convert(_grid_xml(template_section=True, cover=True))["rdl_xml"]
    pf = _page(rdl).find(NS + "PageFooter")
    items = pf.find(NS + "ReportItems")
    assert items is not None and len(list(items)) >= 1, ET.tostring(pf)
    assert re.search(r"Globals!PageNumber", ET.tostring(pf, encoding="unicode"))
