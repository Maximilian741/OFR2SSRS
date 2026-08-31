"""MANUFACTURED BLANK SHEETS — four emitter rules, each render-measured.

A blank sheet is never authored. It is manufactured by geometry the emitter
writes, and every rule below was found by rendering a real source at the
hostile data shapes (0/1/3/25 rows) and asking WHY a sheet came back carrying
nothing but page furniture. Each rule is stated as an invariant here, and each
test carries its own A/B: the artifact with the rule, and the same artifact
with the one declaration the rule keys on flipped back.

  1. BODY SLACK          <Body><Height> may not push the body past the room
                         the margins and page bands leave it, when the body's
                         own content ends inside that room.
  2. INDENT              a declared row indent is cell PADDING only while its
                         own column can hold it; past that it is the region's
                         Left, because padding wider than its box prints
                         nothing while still reserving the row's full height.
  3. MARGIN IMAGES       a <margin>-resident image is page chrome and goes in
                         a page band; the body-image safety net must not adopt
                         one at its PAPER y.

A FOURTH rule was measured and rejected in the same pass — see
``_no_rows_reserved_bottom``: relaxing the no-rows gate to a page-CROSSING
test is right as geometry and wrong as behaviour, because the notice renders
INSTEAD of a region's static rows rather than alongside them. The one
relaxation later admitted keys on a DECLARED page break and carries that
displacement test with it, so a region printing static rows is still refused
(tests/test_no_rows_notice_page_origin.py).
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import pytest

from converter.generators.rdl import (
    _build_multi_section_body, _detect_multi_section, _emit_orphan_margin_images,
    _indent_fits_column, _printable_page_height, _q, _trim_body_slack_to_page,
)
from converter.models import (DataItem, DataQuery, LayoutField, LayoutGroup,
                              ParsedReport)

NS = "{http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition}"


def _t(el, tag):
    return (el.findtext(f"{NS}{tag}") or "").strip()


def _inches(txt):
    txt = (txt or "0in").strip()
    return float(txt[:-2]) / 72.0 if txt.endswith("pt") else float(txt[:-2])


# ---------------------------------------------------------------------------
# 1. BODY SLACK
# ---------------------------------------------------------------------------

def _page_rdl(body_h, item_tops_heights, page_h=8.27, top_m=0.137,
              bot_m=1.270, hdr_h=1.0466, ftr_h=1.3625):
    """A report reduced to the arithmetic under test: one page, two bands and
    a body carrying absolutely-positioned boxes of known extent."""
    items = "".join(
        '<Textbox Name="Tb_%d"><Top>%.3fin</Top><Left>0in</Left>'
        '<Width>1in</Width><Height>%.3fin</Height></Textbox>' % (i, t, h)
        for i, (t, h) in enumerate(item_tops_heights))
    return ET.fromstring(
        '<Report xmlns="%s"><Body><ReportItems>%s</ReportItems>'
        '<Height>%.3fin</Height></Body><Width>7in</Width><Page>'
        '<PageHeight>%sin</PageHeight><PageWidth>11.69in</PageWidth>'
        '<TopMargin>%sin</TopMargin><BottomMargin>%sin</BottomMargin>'
        '<LeftMargin>0.5in</LeftMargin><RightMargin>0.46in</RightMargin>'
        '<PageHeader><Height>%sin</Height></PageHeader>'
        '<PageFooter><Height>%sin</Height></PageFooter>'
        '</Page></Report>'
        % (NS[1:-1], items, body_h, page_h, top_m, bot_m, hdr_h, ftr_h))


def test_body_slack_past_the_page_is_trimmed_to_the_content():
    """The measured case: 4.470in of body over 4.454in of room, with the
    body's own content ending at 4.310in. The 0.16in of slack is a whole
    extra SHEET, and it is the only thing this removes."""
    root = _page_rdl(4.470, [(0.0, 0.78), (3.938, 0.372)])
    fits = _printable_page_height(root)
    assert 4.45 < fits < 4.46, "fixture arithmetic drifted: %r" % fits

    # A/B arm 1 — the artifact BEFORE the rule: the body outruns the sheet.
    assert _inches(_t(root.find(_q("Body")), "Height")) > fits

    _trim_body_slack_to_page(root)
    trimmed = _inches(_t(root.find(_q("Body")), "Height"))
    assert trimmed == pytest.approx(4.310, abs=5e-4), trimmed
    assert trimmed < fits, "the trimmed body must clear the sheet, not sit on it"


def test_body_that_genuinely_outruns_the_sheet_is_left_alone():
    """A per-record letter drawn on a page-tall record really does flow across
    sheets. Shortening THAT body would clip declared content, so the rule only
    ever removes space nothing occupies."""
    root = _page_rdl(29.930, [(0.0, 9.5), (10.0, 19.8)])
    before = _t(root.find(_q("Body")), "Height")
    _trim_body_slack_to_page(root)
    assert _t(root.find(_q("Body")), "Height") == before

    # ...and a body already inside the sheet is not touched either.
    root2 = _page_rdl(3.000, [(0.0, 2.5)])
    _trim_body_slack_to_page(root2)
    assert _t(root2.find(_q("Body")), "Height") == "3.000in"


def test_body_trim_never_cuts_below_a_data_region_row_sum():
    """A data region reserves the SUM of its row heights, never its declared
    <Height> — so a region that will grow past its declaration must not be
    mistaken for slack and trimmed through."""
    rows = "".join("<TablixRow><Height>0.5in</Height></TablixRow>"
                   for _ in range(6))
    root = _page_rdl(4.470, [])
    ri = root.find(_q("Body")).find(_q("ReportItems"))
    ri.append(ET.fromstring(
        '<Tablix xmlns="%s" Name="Tbx"><TablixBody><TablixRows>%s'
        '</TablixRows></TablixBody><Top>0.5in</Top><Height>0.30in</Height>'
        '</Tablix>' % (NS[1:-1], rows)))
    _trim_body_slack_to_page(root)
    # the declared Height would say 0.80in of content; the ROW SUM says 3.50in
    assert _t(root.find(_q("Body")), "Height") == "3.500in"


def test_the_body_slack_trim_is_wired_into_every_generated_rdl(
        synthetic_xml_bytes):
    """A rule nothing applies is not a rule. This is the WIRING half of the
    proof: the pass has to run on the way out of generate_rdl, and the RDL it
    hands back has to obey the invariant — no body past the printable height
    while its own content ends inside it."""
    from converter import convert
    from converter.generators import rdl as R

    seen = []
    original = R._trim_body_slack_to_page

    def spy(root):
        seen.append(root)
        return original(root)

    R._trim_body_slack_to_page = spy
    try:
        out = convert(synthetic_xml_bytes, target_db="oracle")["rdl_xml"]
    finally:
        R._trim_body_slack_to_page = original
    assert seen, "generate_rdl no longer applies the body-slack trim"

    root = ET.fromstring(out)
    fits = _printable_page_height(root)
    body = root.find(_q("Body"))
    declared = _inches(_t(body, "Height"))
    ri = body.find(_q("ReportItems"))
    bottom = max(
        [_inches(el.findtext(_q("Top")) or "0in")
         + R._region_reflow_height_in(el)
         for el in (list(ri) if ri is not None else [])] or [0.0])
    assert not (fits > 0 and declared > fits and 0 < bottom <= fits - 0.04), (
        "the emitted body outruns the sheet on slack alone: "
        "%.3fin declared, content ends %.3fin, %.3fin of room"
        % (declared, bottom, fits))


# ---------------------------------------------------------------------------
# 2. INDENT
# ---------------------------------------------------------------------------

def test_indent_fits_column_predicate():
    assert _indent_fits_column(0.19, 3.00)
    assert _indent_fits_column(0.00, 0.40)
    # the measured defect: a 3.08in indent inside a 1.43in column
    assert not _indent_fits_column(3.08, 1.43)
    # at the edge: padding + right padding + the minimum printable strip
    # exactly fills the column, and a hair past it does not fit
    assert _indent_fits_column(1.43 - 3 / 72.0 - 0.25, 1.43)
    assert not _indent_fits_column(1.43 - 3 / 72.0 - 0.25 + 0.01, 1.43)
    # an unknown column can never be proven to hold anything
    assert not _indent_fits_column(0.19, 0.0)


def _indent_report(label_x, label_w):
    """One stat section whose detail label declares ``label_x`` inside a frame
    flush at x=0 — the shape that turns a declared x offset into a detail-row
    indent."""
    def _mkq(n):
        q = DataQuery(name="Q_%s" % n)
        q.items = [DataItem(name="%s_DESC" % n), DataItem(name="%s_CNT" % n)]
        q.sql = "SELECT %s_DESC, %s_CNT FROM x" % (n, n)
        return q

    def _sec(n, lx, lw):
        rf = LayoutGroup(
            name="R_G_%s" % n, kind="repeating_frame", source_query="G_%s" % n,
            x=0.0, y=0.25, width=6.0,
            fields=[
                LayoutField(name="F_%s_DESC" % n, source="%s_DESC" % n,
                            kind="field", x=lx, y=0.25, width=lw),
                LayoutField(name="F_%s_CNT" % n, source="%s_CNT" % n,
                            kind="field", x=lx + lw + 0.5, y=0.25, width=0.8),
            ])
        return LayoutGroup(name="M_G_%s" % n, kind="frame", x=0.0, y=0.0,
                           width=6.5, children=[rf])

    rep = ParsedReport(name="SYNTH_INDENT")
    rep.queries = [_mkq("S1"), _mkq("S2")]
    rep.layout = [LayoutGroup(name="main", kind="section_main", children=[
        _sec("S1", label_x, label_w), _sec("S2", 0.25, 3.0)])]
    return rep


def _first_section_xml(rep):
    sections = _detect_multi_section(rep)
    assert sections, "fixture did not produce a stat section"
    return ET.tostring(_build_multi_section_body(rep, sections),
                       encoding="unicode")


def test_indent_wider_than_its_column_moves_to_the_region_left():
    """A/B on ONE declaration — the label's declared x.

    Arm A (0.19in, a column can hold it): padding carries the indent and the
    region stays at Left 0, exactly as before this rule existed.
    Arm B (3.08in in a column the geometry makes narrower than that): padding
    would print NOTHING, so the region's own Left carries the offset and the
    cell keeps its stock padding and its full column for text."""
    a = _first_section_xml(_indent_report(0.1875, 3.0))
    m = re.search(r'<Textbox Name="Tbx_S0_Cell_S1_DESC">.*?'
                  r'<PaddingLeft>([^<]+)</PaddingLeft>', a, re.S)
    assert m and m.group(1) == "0.19in", "a holdable indent must stay padding"
    assert re.search(r'<Tablix Name="Tbx_S0">.*?<Left>0.00in</Left>', a, re.S)

    b = _first_section_xml(_indent_report(3.08, 1.31))
    m = re.search(r'<Textbox Name="Tbx_S0_Cell_S1_DESC">.*?'
                  r'<PaddingLeft>([^<]+)</PaddingLeft>', b, re.S)
    assert m and m.group(1) == "3pt", (
        "an indent its column cannot hold must not be written as padding — "
        "got %r" % (m.group(1) if m else None))
    lm = re.search(r'<Tablix Name="Tbx_S0">.*?<Left>([^<]+)</Left>', b, re.S)
    assert lm and lm.group(1) == "3.08in", (
        "the declared offset must survive as the region's Left: %r"
        % (lm.group(1) if lm else None))


def test_no_emitted_cell_is_padded_out_of_its_own_column():
    """The invariant, asserted over every cell the section builder emits: the
    left padding must leave a printable strip inside the column, or the row
    reserves its full height and paints nothing at all."""
    for label_x in (0.1875, 1.0, 3.08, 9.0):
        root = ET.fromstring(_first_section_xml(_indent_report(label_x, 1.31)))
        checked = 0
        for tx in root.iter(_q("Tablix")):
            tb = tx.find(_q("TablixBody"))
            if tb is None:
                continue
            widths = [_inches(c.findtext(_q("Width")))
                      for c in tb.find(_q("TablixColumns"))]
            for row in tb.find(_q("TablixRows")):
                for ci, cell in enumerate(row.find(_q("TablixCells"))):
                    if ci >= len(widths):
                        continue
                    for box in cell.iter(_q("Textbox")):
                        st = box.find(_q("Style"))
                        if st is None:
                            continue
                        checked += 1
                        pad = _inches(st.findtext(_q("PaddingLeft")))
                        assert pad < widths[ci], (
                            "%s padded %.2fin inside a %.2fin column "
                            "(declared x=%s)"
                            % (box.get("Name"), pad, widths[ci], label_x))
        assert checked, "fixture emitted no cells to check"


# ---------------------------------------------------------------------------
# 3. MARGIN IMAGES
# ---------------------------------------------------------------------------

def _banded_root(page_h=11.42, top_m=0.880, bot_m=0.492,
                 hdr_h=2.60, ftr_h=0.60):
    return ET.fromstring(
        '<Report xmlns="%s"><Body><ReportItems /><Height>3.4in</Height></Body>'
        '<Width>16.96in</Width><Page>'
        '<PageHeight>%sin</PageHeight><PageWidth>17in</PageWidth>'
        '<TopMargin>%sin</TopMargin><BottomMargin>%sin</BottomMargin>'
        '<LeftMargin>0in</LeftMargin><RightMargin>0in</RightMargin>'
        '<PageHeader><Height>%sin</Height><ReportItems /></PageHeader>'
        '<PageFooter><Height>%sin</Height><ReportItems /></PageFooter>'
        '</Page></Report>'
        % (NS[1:-1], page_h, top_m, bot_m, hdr_h, ftr_h))


def _margin_image_report():
    rep = ParsedReport(name="SYNTH_CHROME")
    rep.layout = [LayoutGroup(name="main", kind="section_main", fields=[
        LayoutField(name="B_TOP", kind="image", x=10.66, y=0.002,
                    width=6.97, height=0.75, in_margin=True),
        LayoutField(name="B_BOT", kind="image", x=5.98, y=11.046,
                    width=6.97, height=0.37, in_margin=True),
    ])]
    rep._image_assets = {"B_TOP": "B_TOP", "B_BOT": "B_BOT"}
    return rep


def test_margin_images_land_in_the_page_bands_not_the_body():
    """A declared <margin> image is page chrome. Its y is a PAPER y and the
    body has no such coordinate: a footer logo declared at 11.046in of an
    11.42in sheet became a body item 11.05in down a body with 6.85in of room,
    which is how a two-sheet dead gap and its blank pages were manufactured."""
    root = _banded_root()
    _emit_orphan_margin_images(root, _margin_image_report(), set())

    body_imgs = [i.get("Name") for i in root.find(_q("Body")).iter(_q("Image"))]
    assert body_imgs == [], "page chrome leaked into the body: %r" % body_imgs

    page = root.find(_q("Page"))
    hdr = [i.findtext(_q("Value"))
           for i in page.find(_q("PageHeader")).iter(_q("Image"))]
    ftr = [i.findtext(_q("Value"))
           for i in page.find(_q("PageFooter")).iter(_q("Image"))]
    assert hdr == ["B_TOP"], hdr
    assert ftr == ["B_BOT"], ftr

    # the band that had to grow did, and the body kept room to print
    assert _printable_page_height(root) >= 1.0

    # ...and an image a band ALREADY emitted is never emitted twice.
    root2 = _banded_root()
    _emit_orphan_margin_images(root2, _margin_image_report(),
                               {"B_TOP", "B_BOT"})
    assert not list(root2.iter(_q("Image")))


def test_margin_band_never_grows_past_the_bodys_room():
    """A band may grow to hold declared chrome, but not until the body it
    squeezes has nowhere left to print."""
    root = _banded_root(page_h=4.0, top_m=0.2, bot_m=0.2, hdr_h=0.4, ftr_h=0.4)
    before = _printable_page_height(root)
    rep = ParsedReport(name="SYNTH_CHROME2")
    rep.layout = [LayoutGroup(name="main", kind="section_main", fields=[
        LayoutField(name="B_BOT", kind="image", x=0.0, y=3.0,
                    width=2.0, height=3.0, in_margin=True)])]
    rep._image_assets = {"B_BOT": "B_BOT"}
    _emit_orphan_margin_images(root, rep, set())
    assert _printable_page_height(root) == before, \
        "the band grew until the body had no room left"
    assert [i.findtext(_q("Value")) for i
            in root.find(_q("Page")).find(_q("PageFooter")).iter(_q("Image"))
            ] == ["B_BOT"], "the declared chrome must still be emitted"
