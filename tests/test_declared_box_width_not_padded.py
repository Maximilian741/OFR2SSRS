"""DECLARED WIDTH == EMITTED WIDTH.

One root cause sat behind a family of geometry defects: the emitters added
a metric cushion (~0.05-0.06in) to a box the source had pinned, and the
body-fit pass then grew the whole report to hold the oversized items.

Three consequences, all measured against Oracle-rendered truth PDFs:

* a page-chrome box is very often CENTRED or RIGHT-justified, so widening
  it moves its TEXT — padded chrome landed 2-3pt right of truth;
* the report <Width> outgrew the declared body and RightMargin was
  squeezed toward zero to pay for it, so right-aligned numeric columns
  ended past the truth's right edge;
* the sheet itself grew: a portrait report whose declared page furniture
  measures exactly the sheet width asked for a wider sheet.

Rules guarded here (all declaration-driven, no report-specific data):

1. A declared page-chrome box is emitted at its declared width, verbatim.
2. The report <Width> lands on the DECLARED body span, not on whatever a
   synthesized item happened to reach, and RightMargin is the residual.
3. Declared chrome never widens the paper, and the right margin never
   closes over it (a page band spans the PRINTABLE width - engine-measured
   fact, see test_page_chrome_paper_width).
4. Chrome declared left of the page margin is CLIPPED there, never slid
   inward — sliding moved every glyph a whole margin and forced the paper
   to grow by that margin. WHICH edge the clip preserves is set by the
   declared justification: a right-flush box keeps its declared paper RIGHT
   edge, and a CENTRED box keeps its declared paper CENTRE AXIS (the far
   edge is trimmed by the same sliver), because a one-sided clip on a
   centred box moves its glyphs by half the sliver — truth-measured.

Synthetic fixtures only.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402

# Declared geometry of the synthetic source, in inches.
BODY_ORIGIN_X = 0.25
BODY_SPAN = 7.50
DATE_X, DATE_W = 6.60000, 0.90000          # right-justified, ends at 7.50
TITLE_X, TITLE_W = 2.00000, 4.00000        # centred
BANNER_X, BANNER_W = 0.00000, 8.50000      # full-sheet band furniture


def _report_xml(banner: bool = False, banner_justify: str = "center") -> bytes:
    """A portrait report with a declared <margin> band.

    ``banner`` adds page furniture that starts at the paper's left edge —
    left of the page margin the RDL band begins at. ``banner_justify`` is the
    declared justification of that furniture; it decides WHICH edge/axis of
    the declared paper box has to survive the clip.
    """
    extra = ""
    if banner:
        extra = f"""
      <text name="B_BANNER" minWidowLines="1">
        <textSettings justify="{banner_justify}" spacing="0"/>
        <geometryInfo x="{BANNER_X:.5f}" y="0.20000"
         width="{BANNER_W:.5f}" height="0.22000"/>
        <textSegment><font face="Arial" size="9"/>
          <string><![CDATA[Division Banner]]></string></textSegment>
      </text>"""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<report name="WIDGET_LEDGER" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q_MAIN">
      <select canParse="no"><![CDATA[SELECT OWNER_NM, PARCEL_NO, DUE_AMT FROM WIDGET_LEDGER]]></select>
      <group name="G_MAIN">
        <dataItem name="OWNER_NM" datatype="vchar2" columnOrder="1" defaultLabel="Owner"/>
        <dataItem name="PARCEL_NO" datatype="vchar2" columnOrder="2" defaultLabel="Parcel"/>
        <dataItem name="DUE_AMT" datatype="number" columnOrder="3" defaultLabel="Due"/>
      </group>
    </dataSource>
  </data>
  <layout>
  <section name="main">
    <body height="8.90000">
      <location x="{BODY_ORIGIN_X:.5f}" y="0.50000"/>
      <frame name="M_G_MAIN_GRPFR">
        <geometryInfo x="0.00000" y="0.00000" width="{BODY_SPAN:.5f}"
         height="4.00000"/>
        <generalLayout verticalElasticity="variable"/>
        <repeatingFrame name="R_G_MAIN" source="G_MAIN" printDirection="down"
         minWidowRecords="1" columnMode="no">
          <geometryInfo x="0.00000" y="0.00000" width="{BODY_SPAN:.5f}"
           height="3.90000"/>
          <generalLayout verticalElasticity="variable"/>
          <text name="B_OWNER_LBL">
            <geometryInfo x="0.00000" y="0.00000" width="0.70000"
             height="0.18000"/>
            <textSegment><font face="Arial" size="10"/>
              <string><![CDATA[Owner]]></string></textSegment></text>
          <field name="F_OWNER_NM" source="OWNER_NM" minWidowLines="1"
           alignment="start"><font face="Arial" size="10"/>
            <geometryInfo x="0.75000" y="0.00000" width="4.00000"
             height="0.18000"/></field>
          <field name="F_PARCEL_NO" source="PARCEL_NO" minWidowLines="1"
           alignment="start"><font face="Arial" size="10"/>
            <geometryInfo x="0.75000" y="0.25000" width="4.00000"
             height="0.18000"/></field>
          <field name="F_DUE_AMT" source="DUE_AMT" minWidowLines="1"
           alignment="end"><font face="Arial" size="10"/>
            <geometryInfo x="5.50000" y="0.50000" width="2.00000"
             height="0.18000"/></field>
        </repeatingFrame>
      </frame>
    </body>
    <margin>
      <text name="B_TITLE" minWidowLines="1">
        <textSettings justify="center" spacing="0"/>
        <geometryInfo x="{TITLE_X:.5f}" y="0.30000" width="{TITLE_W:.5f}"
         height="0.25000"/>
        <textSegment><font face="Arial" size="14"/>
          <string><![CDATA[Widget Ledger]]></string></textSegment>
      </text>{extra}
      <field name="F_CURRENT_DATE" source="CurrentDate" minWidowLines="1"
       formatMask="MM/DD/RRRR" spacing="0" alignment="end">
        <font face="Arial" size="8"/>
        <geometryInfo x="{DATE_X:.5f}" y="10.36000" width="{DATE_W:.5f}"
         height="0.18700"/>
      </field>
    </margin>
  </section>
  </layout>
</report>""".encode()


def _page_geometry(rdl: str):
    """(PageWidth, LeftMargin, RightMargin, report Width) in inches."""
    def _one(tag):
        m = re.search(rf"<{tag}>([0-9.]+)in</{tag}>", rdl)
        return float(m.group(1)) if m else 0.0

    body_w = re.search(r"</Body>\s*<Width>([0-9.]+)in</Width>", rdl)
    return (_one("PageWidth"), _one("LeftMargin"), _one("RightMargin"),
            float(body_w.group(1)) if body_w else 0.0)


def _chrome_boxes(rdl: str) -> dict:
    """{name: (Left, Width)} for every emitted page-chrome item."""
    out = {}
    for name, block in re.findall(
            r"<(?:Textbox|Image|Line|Rectangle) Name=\"(MChrome_[^\"]*)\""
            r"[^>]*>(.*?)</(?:Textbox|Image|Line|Rectangle)>", rdl, re.S):
        left = re.search(r"<Left>([0-9.]+)in</Left>", block)
        width = re.search(r"<Width>([0-9.]+)in</Width>", block)
        if left and width:
            out[name] = (float(left.group(1)), float(width.group(1)))
    return out


def _box_ending_near(boxes: dict, right_edge: float, tol: float = 0.12):
    """The chrome box whose right edge is closest to ``right_edge``."""
    best, best_d = None, tol
    for _n, (left, width) in boxes.items():
        d = abs(left + width - right_edge)
        if d < best_d:
            best, best_d = (left, width), d
    return best


def test_declared_chrome_width_is_emitted_verbatim():
    """No metric cushion: every declared chrome width survives to the RDL."""
    rdl = convert(_report_xml())["rdl_xml"]
    _pw, lm, _rm, _w = _page_geometry(rdl)
    boxes = _chrome_boxes(rdl)
    assert boxes, "the declared <margin> band emitted no page chrome"
    declared = {DATE_W, TITLE_W}
    for name, (_left, width) in boxes.items():
        near = min(declared, key=lambda d: abs(d - width))
        assert abs(width - near) <= 0.005, (
            f"{name} emitted at {width}in against a declared {near}in — a "
            "declared page-chrome width must be emitted verbatim; padding it "
            "moves centred and right-justified chrome off its declared spot")
    # and the box positions still land at declared_x - LeftMargin
    date = _box_ending_near(boxes, DATE_X + DATE_W - lm)
    assert date is not None, "the right-justified chrome box was not emitted"
    assert abs(date[0] - (DATE_X - lm)) <= 0.005, (
        f"right-justified chrome left edge {date[0]}in, declared "
        f"{DATE_X - lm}in band-relative")
    assert abs(date[1] - DATE_W) <= 0.005, (
        f"right-justified chrome width {date[1]}in, declared {DATE_W}in — "
        "the extra width lands entirely on the right, i.e. on the text")


def test_report_width_lands_on_the_declared_body():
    """<Width> is the declared body span and RightMargin is the residual."""
    rdl = convert(_report_xml())["rdl_xml"]
    pw, lm, rm, width = _page_geometry(rdl)
    assert abs(width - BODY_SPAN) <= 0.02, (
        f"report Width {width}in against a declared body span of "
        f"{BODY_SPAN}in — a synthesized item that outgrew the declaration "
        "must give the slack back, not drag the report wider")
    assert rm > 0.05, (
        f"RightMargin {rm}in: with the declared body honored the right "
        "margin is the real residual, not zero")
    assert lm + width + rm <= pw + 1e-9, (
        f"Width {width}in + margins {lm}/{rm} overflows PageWidth {pw}in")


def test_declared_chrome_neither_widens_the_paper_nor_is_clipped_by_it():
    """Full-sheet CENTRED page furniture keeps the declared sheet AND its
    declared centre AXIS.

    This assertion used to demand the declared paper RIGHT EDGE for every
    left-clipped box, centred ones included. An Oracle-rendered truth page
    disproves that for the centred case: a 3-line title declared
    x=0.00000 width=8.50000 justify="center" on a report whose body origin
    (== the emitted LeftMargin) is 0.25in prints its ink centred on 4.2500in
    — the paper's own axis — measured to 0.0000in over three separate ink
    lines. Keeping the right edge and clipping only the left puts the box at
    0.25..8.50in, i.e. the glyphs at 4.3750in: 0.125in right of truth, which
    is exactly half the clipped sliver.

    So the replacement is STRICTER than the edge rule it replaces: a centred
    box must land on its declared paper CENTRE (one number, not a bound),
    which forces the symmetric trim. The right-edge rule still binds for
    non-centred furniture — see the companion test below.
    """
    rdl = convert(_report_xml(banner=True))["rdl_xml"]
    pw, lm, rm, _width = _page_geometry(rdl)
    assert abs(pw - BANNER_W) <= 0.02, (
        f"PageWidth {pw}in: page furniture declared exactly {BANNER_W}in "
        "wide is the sheet — reserving a side margin on top of it grows the "
        "paper past the paper the source draws on")
    boxes = _chrome_boxes(rdl)
    banner = max(boxes.items(), key=lambda kv: kv[1][0] + kv[1][1])[1]
    # clipped on the left by the page margin, centre axis preserved
    assert abs(banner[0]) <= 0.005, (
        f"banner left {banner[0]}in: chrome declared at the paper edge "
        "starts at the band origin")
    _centre = lm + banner[0] + banner[1] / 2.0
    assert abs(_centre - (BANNER_X + BANNER_W / 2.0)) <= 0.005, (
        f"centred banner spans paper {lm + banner[0]}.."
        f"{lm + banner[0] + banner[1]}in, centre {_centre}in against a "
        f"declared centre of {BANNER_X + BANNER_W / 2.0}in — a centred box "
        "is its AXIS, so a one-sided clip slides every glyph by half the "
        "clipped sliver")
    assert pw - lm - rm >= banner[0] + banner[1] - 1e-9, (
        f"printable width {pw - lm - rm}in does not reach the chrome's "
        f"{banner[0] + banner[1]}in right edge — the band spans the "
        "printable width, so the right margin may never close over it")


def test_left_clipped_right_justified_chrome_keeps_its_paper_right_edge():
    """The edge rule, still binding wherever the declaration anchors an edge.

    A RIGHT-justified box flushes its glyphs against its right edge, so that
    edge is what the clip must preserve — trimming it (or sliding the box
    inward) moves every glyph. Same full-sheet furniture as above, declared
    justify="end" instead of "center".
    """
    rdl = convert(_report_xml(banner=True,
                              banner_justify="end"))["rdl_xml"]
    _pw, lm, _rm, _width = _page_geometry(rdl)
    boxes = _chrome_boxes(rdl)
    banner = max(boxes.items(), key=lambda kv: kv[1][0] + kv[1][1])[1]
    assert abs(banner[0]) <= 0.005, (
        f"banner left {banner[0]}in: chrome declared at the paper edge "
        "starts at the band origin")
    assert abs(banner[0] + banner[1] - (BANNER_X + BANNER_W - lm)) <= 0.01, (
        f"banner spans {banner[0]}..{banner[0] + banner[1]}in band-relative; "
        f"its declared paper right edge {BANNER_X + BANNER_W}in maps to "
        f"{BANNER_X + BANNER_W - lm}in — sliding the box inward instead of "
        "clipping it moves every glyph a whole margin right")
