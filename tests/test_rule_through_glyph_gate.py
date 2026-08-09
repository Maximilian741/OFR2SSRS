"""RULES THROUGH GLYPHS — the rail, and the emitter defect it caught.

The render-overlap rail was text-vs-text by construction ("underlines and
rules are not words and never flag"), so a DRAWN rule slicing through printed
characters was invisible to it: a voucher grid came back paint=0 while it
carried 36 rule-through-glyph collisions, the divider between every caption
and its value printed straight through the last letters of the caption and
through a currency figure (crop-verified in the engine render).

Two things are guarded here.

A. THE DETECTOR (``render_overlap.stroke_through_text``). It must fire on a
   rule crossing glyph ink and stay silent on the three legitimate cases that
   look similar in a coordinate dump: an UNDERLINE (below the baseline), a
   cell border ABUTTING the text box with clearance from the glyphs, and a
   rule that a later opaque fill paints over (it never reaches the paper).
   The pages are drawn here directly, so the assertions are exact and need no
   render engine.

B. THE EMITTER. Two declaration-driven invariants whose violation produced
   those collisions:

   1. Every section the emitter prints into the body counts toward the
      sheet's width budget. Measuring only the main section under-sized the
      page for a record-bearing trailer section, and every container in that
      trailer was then squeezed by the fit clamps.

   2. A fit adjustment may not manufacture an overlap the declaration does
      not have. A right-anchored box in a squeezed container used to slide
      LEFT to keep its declared width — straight over the caption declared
      beside it (the source declares a +0.018in gap between the caption's
      right edge and the value's left edge; the slide turned that into a
      0.17in overlap). A box with a declared sibling to its left on the same
      band must pay for the squeeze out of its own width instead.

Synthetic fixtures only — no client data.
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "tools" / "renderlab"))

from converter import convert  # noqa: E402

NS = ("{http://schemas.microsoft.com/sqlserver/reporting/"
      "2008/01/reportdefinition}")


# --------------------------------------------------------------------------
# A. the detector
# --------------------------------------------------------------------------

def _draw_page(doc, *, rule=None, underline=False, border=False,
               buried=False, vrule=False):
    """One page carrying the word 'Ledger' at a known spot plus whichever
    decoration the case asks for. Coordinates are PDF points."""
    import fitz

    page = doc.new_page(width=300, height=120)
    x, baseline, size = 60.0, 60.0, 14.0
    if buried:
        # rule first, then an opaque fill over it, then the text: the rule
        # never reaches the paper.
        page.draw_line(fitz.Point(20, baseline - 5),
                       fitz.Point(280, baseline - 5),
                       color=(0, 0, 0), width=1)
        page.draw_rect(fitz.Rect(10, baseline - 20, 290, baseline + 10),
                       color=None, fill=(0.85, 0.85, 0.85))
    page.insert_text(fitz.Point(x, baseline), "Ledger", fontsize=size)
    right = x + page.get_text("words")[0][2] - page.get_text("words")[0][0]
    if underline:
        page.draw_line(fitz.Point(x, baseline + 2.0),
                       fitz.Point(right, baseline + 2.0),
                       color=(0, 0, 0), width=0.5)
    if border:
        # a cell box around the text with real clearance on every side
        page.draw_rect(fitz.Rect(x - 6, baseline - size - 4,
                                 right + 6, baseline + 5),
                       color=(0, 0, 0), width=0.5)
    if rule is not None:
        page.draw_line(fitz.Point(20, rule), fitz.Point(280, rule),
                       color=(0, 0, 0), width=0.5)
    if vrule:
        # straight down the middle of the word
        mid = (x + right) / 2.0
        page.draw_line(fitz.Point(mid, baseline - size),
                       fitz.Point(mid, baseline + 3),
                       color=(0, 0, 0), width=0.5)
    return page


def _hits(**kw):
    import tempfile

    fitz = pytest.importorskip("fitz")
    from render_overlap import stroke_through_text

    doc = fitz.open()
    _draw_page(doc, **kw)
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "p.pdf"
        doc.save(str(p))
        doc.close()
        return stroke_through_text(p, max_per_page=40)


def test_underline_border_and_buried_rules_never_flag():
    """The three legitimate shapes stay silent."""
    assert _hits(underline=True) == [], "an underline is not a strike-through"
    assert _hits(border=True) == [], "a cell border with clearance is not a strike"
    assert _hits(buried=True) == [], "a rule an opaque fill covers prints nothing"
    # a rule ABOVE the cap line is an overline, not a strike
    assert _hits(rule=44.0) == [], "an overline is not a strike-through"


def test_rule_crossing_glyph_ink_is_flagged():
    """PROVE THE GATE CAN FAIL: the same page, the rule moved into the glyph
    core, must go red on both axes. A detector that cannot fire certifies
    nothing — that is exactly how the earlier blindness shipped."""
    horiz = _hits(rule=54.0)          # mid x-height of a 14pt line on baseline 60
    assert horiz, "a horizontal rule through the glyph core must flag"
    assert horiz[0]["axis"] == "H"

    vert = _hits(vrule=True)
    assert vert, "a vertical rule through the middle of a word must flag"
    assert vert[0]["axis"] == "V"


def test_stroke_hits_reach_the_paint_gate():
    """The class must reach ``pdf_overlaps`` — the function every rail calls
    — not just the standalone helper."""
    import tempfile

    fitz = pytest.importorskip("fitz")
    from render_overlap import pdf_overlaps

    doc = fitz.open()
    _draw_page(doc, vrule=True)
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "p.pdf"
        doc.save(str(p))
        doc.close()
        hits = pdf_overlaps(p)
    assert any("rule through glyphs" in (h.get("b") or "") for h in hits), (
        "the paint gate still cannot see a rule painted through glyphs")


# --------------------------------------------------------------------------
# B. the emitter
# --------------------------------------------------------------------------

# A record-bearing trailer section declaring a WIDER body than the main
# section: 7.90in of content starting at x=0.30 on Letter paper. Both fit the
# sheet, so nothing has to be squeezed — but the sheet budget has to know
# the trailer exists.
WIDE_TRAILER_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<report name="STATEMENT_PACK" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q_MAIN">
      <select canParse="no"><![CDATA[SELECT ACCT_NO, ACCT_NAME FROM ACCOUNTS]]></select>
      <group name="G_MAIN">
        <dataItem name="ACCT_NO" datatype="vchar2" columnOrder="1" defaultLabel="Account"/>
        <dataItem name="ACCT_NAME" datatype="vchar2" columnOrder="2" defaultLabel="Name"/>
      </group>
    </dataSource>
  </data>
  <layout>
  <section name="trailer" repeatOn="G_MAIN">
    <body width="7.90000" height="9.00000">
      <location x="0.30000" y="0.30000"/>
      <frame name="T_PACK">
        <geometryInfo x="0.00000" y="0.00000" width="7.90000" height="3.00000"/>
        <text name="B_PACK_TITLE">
          <geometryInfo x="0.10000" y="0.10000" width="4.00000" height="0.22000"/>
          <textSegment><font face="Arial" size="12" bold="yes"/>
          <string><![CDATA[Remittance Slip]]></string></textSegment></text>
        <field name="F_PACK_ACCT" source="ACCT_NO">
          <font face="Arial" size="10"/>
          <geometryInfo x="6.40000" y="1.00000" width="1.50000" height="0.20000"/></field>
      </frame>
    </body>
  </section>
  <section name="main" repeatOn="G_MAIN">
    <body width="7.50000" height="9.00000">
      <location x="0.50000" y="0.50000"/>
      <frame name="M_DOC">
        <geometryInfo x="0.00000" y="0.00000" width="7.50000" height="9.00000"/>
        <text name="B_HEADING">
          <geometryInfo x="0.20000" y="0.30000" width="6.00000" height="0.22000"/>
          <textSegment><font face="Arial" size="12" bold="yes"/>
          <string><![CDATA[Statement of Account]]></string></textSegment></text>
        <field name="F_ACCT_NAME" source="ACCT_NAME">
          <font face="Arial" size="10"/>
          <geometryInfo x="0.20000" y="1.00000" width="3.00000" height="0.20000"/></field>
      </frame>
    </body>
  </section>
  </layout>
</report>"""


# A container declared 0.70in WIDER than the frame that owns it, holding a
# caption and a right-aligned value the source declares 0.02in apart. The
# container cannot be honoured in full, so something must give — and what
# gives may not be the declared gap.
SQUEEZED_GRID_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<report name="GRID_BLOCK" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q_MAIN">
      <select canParse="no"><![CDATA[SELECT ACCT_NO, ACCT_NAME FROM ACCOUNTS]]></select>
      <group name="G_MAIN">
        <dataItem name="ACCT_NO" datatype="vchar2" columnOrder="1" defaultLabel="Account"/>
        <dataItem name="ACCT_NAME" datatype="vchar2" columnOrder="2" defaultLabel="Name"/>
      </group>
    </dataSource>
  </data>
  <layout>
  <section name="main" repeatOn="G_MAIN">
    <body width="7.50000" height="9.00000">
      <location x="0.50000" y="0.50000"/>
      <frame name="M_DOC">
        <geometryInfo x="0.10000" y="0.00000" width="6.00000" height="9.00000"/>
        <text name="B_HEADING">
          <geometryInfo x="0.20000" y="0.30000" width="5.00000" height="0.22000"/>
          <textSegment><font face="Arial" size="12" bold="yes"/>
          <string><![CDATA[Statement of Account]]></string></textSegment></text>
        <frame name="M_GRID">
          <geometryInfo x="4.00000" y="2.00000" width="2.80000" height="0.60000"/>
          <text name="B_GRID_CAPTION">
            <textSettings justify="end" spacing="single"/>
            <geometryInfo x="4.02000" y="2.02000" width="1.30000" height="0.22000"/>
            <textSegment><font face="Arial" size="10"/>
            <string><![CDATA[Account Number]]></string></textSegment></text>
          <field name="F_GRID_VALUE" source="ACCT_NO" alignment="end">
            <font face="Arial" size="10" bold="yes"/>
            <geometryInfo x="5.34000" y="2.02000" width="1.40000" height="0.22000"/></field>
        </frame>
      </frame>
    </body>
  </section>
  </layout>
</report>"""

CAPTION_DECLARED_RIGHT_REL = 0.02 + 1.30      # relative to the 4.00in grid
VALUE_DECLARED_LEFT_REL = 5.34 - 4.00


def _inches(txt) -> float:
    try:
        return float((txt or "0in").replace("in", "").strip())
    except ValueError:
        return 0.0


def _boxes_by_value(rdl):
    """{textbox <Value> text: (Left, Width)} for every emitted Textbox."""
    root = ET.fromstring(rdl)
    out = {}
    for tb in root.iter(f"{NS}Textbox"):
        vals = [(v.text or "") for v in tb.iter(f"{NS}Value")]
        key = next((v for v in vals if v.strip()), "")
        if not key:
            continue
        out.setdefault(key.strip(), (_inches(tb.findtext(f"{NS}Left")),
                                     _inches(tb.findtext(f"{NS}Width"))))
    return out


def test_body_width_budget_covers_a_record_bearing_trailer_section():
    """The sheet must be sized for EVERY section it prints, not just main.

    The trailer declares 7.90in of content; a body sized from the main
    section's 7.50in squeezed every trailer container, which is what set the
    right-anchored value boxes sliding over their own captions."""
    rdl = convert(WIDE_TRAILER_XML)["rdl_xml"]
    root = ET.fromstring(rdl)
    width = _inches(root.findtext(f"{NS}Width"))
    assert width >= 7.88, (
        f"body <Width> {width}in ignores the trailer section's declared "
        "7.90in content span")
    page = root.find(f"{NS}Page")
    printable = (_inches(page.findtext(f"{NS}PageWidth"))
                 - _inches(page.findtext(f"{NS}LeftMargin"))
                 - _inches(page.findtext(f"{NS}RightMargin")))
    assert printable >= width - 0.001, (
        f"printable width {printable}in cannot hold the {width}in body")


def test_squeezed_container_never_slides_a_value_over_its_caption():
    """A fit adjustment may not manufacture a declared-disjoint overlap.

    The grid container is declared wider than its parent, so it IS squeezed.
    The right-anchored value must then lose WIDTH, never cross the caption:
    the source declares them 0.02in apart and Oracle prints that gap."""
    rdl = convert(SQUEEZED_GRID_XML)["rdl_xml"]
    boxes = _boxes_by_value(rdl)
    caption = next((v for k, v in boxes.items()
                    if "Account Number" in k), None)
    value = next((v for k, v in boxes.items()
                  if "ACCT_NO" in k and k.startswith("=")), None)
    assert caption is not None, f"caption box not emitted: {sorted(boxes)}"
    assert value is not None, f"value box not emitted: {sorted(boxes)}"

    cap_left, cap_w = caption
    val_left, _val_w = value
    # the squeeze must actually be happening, else this proves nothing
    assert val_left <= VALUE_DECLARED_LEFT_REL + 0.02
    assert cap_left + cap_w <= CAPTION_DECLARED_RIGHT_REL + 0.02
    assert val_left >= cap_left + cap_w - 0.001, (
        f"value box slid to Left={val_left}in, inside the caption that ends "
        f"at {cap_left + cap_w}in — the box border now prints through the "
        "caption glyphs")
