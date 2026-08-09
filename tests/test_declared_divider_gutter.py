"""DIVIDER / SEPARATOR GEOMETRY comes from the DECLARATION, never from the
emitter's own convenience.

Two classes are guarded, both engine-render measured on a truth-paired
voucher-and-fee-table export whose truth PDF draws ZERO rules through
glyphs while ours drew 36:

1. PER-EDGE PAINT (``hideXBorder``). Oracle names a box with
   ``linePattern`` and then names WHICH of its four edges actually strokes
   with ``hideLeftBorder`` / ``hideRightBorder`` / ``hideTopBorder`` /
   ``hideBottomBorder``. Two cells stacked against each other hide the
   SHARED edge precisely so no divider stands inside the neighbour's text
   box. The emitter painted all four edges regardless, so every value
   box's suppressed LEFT edge printed as a vertical divider through the
   last glyphs of the caption beside it (4 hits per record instance).
   The declaration is the whole truth: emitted stroking edges must equal
   declared stroking edges, no more and no fewer.

2. A SIZE FLOOR MAY NOT MOVE A DECLARED STROKE. The 0.40in / 0.18in
   minimum box extents are text-clipping guards -- invisible on a box
   that draws no chrome. On a box whose right / bottom edge STROKES they
   relocate a printed rule: a fee-table sub-header declared 0.14587in
   tall was floored to 0.18in, dropping its bottom separator 0.034in
   (2.5pt) from the declared ascent gap ABOVE the caption below it to
   0.034in INSIDE that caption -- straight through its glyph core.

The third test is the general guard the two fixes have to satisfy: no
stroke this emitter writes may stand inside another text box's span.

Synthetic fixture only -- no client data.
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402

NS = ("{http://schemas.microsoft.com/sqlserver/reporting/"
      "2008/01/reportdefinition}")

# Every geometry below is a DECLARED number the assertions read back:
#
#   row 1  B_CAP_A  x=1.00 w=0.52 (right edge 1.52)  strokes TOP only
#          B_VAL_A  x=1.50 w=1.30                    strokes TOP + RIGHT
#          -> the value's left edge is declared 0.02in INSIDE the caption's
#             box, and is declared hidden for exactly that reason.
#
#   row 2  B_CAP_B  x=1.00 w=0.30 (right edge 1.30)  strokes BOTTOM + RIGHT
#          B_VAL_B  x=1.318 w=1.20                   strokes RIGHT only
#          -> a declared +0.018in gutter between the caption's right edge
#             and the value's left edge; the 0.40in width floor would eat
#             it and put the caption's stroking right edge 0.10in inside
#             the value box.
#
#   row 3  B_SUBHDR x=2.00 y=2.00     h=0.14587      strokes BOTTOM + LEFT
#          B_COLCAP x=2.00 y=2.14661  h=0.35339      strokes LEFT only
#          -> the sub-header's bottom separator is declared 0.00074in
#             ABOVE the caption box below it; the 0.18in height floor
#             would drop it 0.034in inside that caption.
GRID_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<report name="VOUCHER_GRID" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q_MAIN">
      <select canParse="no"><![CDATA[SELECT ACCT_NO, AMOUNT FROM LEDGER]]></select>
      <group name="G_MAIN">
        <dataItem name="ACCT_NO" datatype="vchar2" columnOrder="1" defaultLabel="Acct"/>
        <dataItem name="AMOUNT" oracleDatatype="number" columnOrder="2" defaultLabel="Amount"/>
      </group>
    </dataSource>
  </data>
  <layout>
  <section name="main" repeatOn="G_MAIN">
    <body width="7.50000" height="9.00000">
      <location x="0.50000" y="0.50000"/>
      <frame name="M_DOC">
        <geometryInfo x="0.00000" y="0.00000" width="7.50000" height="9.00000"/>

        <text name="B_CAP_A" minWidowLines="1">
          <textSettings justify="end" spacing="single"/>
          <geometryInfo x="1.00000" y="1.00000" width="0.52000" height="0.22000"/>
          <visualSettings fillPattern="transparent" linePattern="solid"
           hideLeftBorder="yes" hideRightBorder="yes" hideBottomBorder="yes"/>
          <textSegment><font face="Arial" size="8"/>
          <string><![CDATA[Alpha]]></string></textSegment></text>
        <text name="B_VAL_A" minWidowLines="1">
          <textSettings spacing="single"/>
          <geometryInfo x="1.50000" y="1.00000" width="1.30000" height="0.22000"/>
          <visualSettings fillPattern="transparent" linePattern="solid"
           hideLeftBorder="yes" hideBottomBorder="yes"/>
          <textSegment><font face="Arial" size="8"/>
          <string><![CDATA[Bravo]]></string></textSegment></text>

        <text name="B_CAP_B" minWidowLines="1">
          <textSettings justify="end" spacing="single"/>
          <geometryInfo x="1.00000" y="1.40000" width="0.30000" height="0.22000"/>
          <visualSettings fillPattern="transparent" linePattern="solid"
           hideLeftBorder="yes" hideTopBorder="yes"/>
          <textSegment><font face="Arial" size="8"/>
          <string><![CDATA[Cee]]></string></textSegment></text>
        <text name="B_VAL_B" minWidowLines="1">
          <textSettings spacing="single"/>
          <geometryInfo x="1.31800" y="1.40000" width="1.20000" height="0.22000"/>
          <visualSettings fillPattern="transparent" linePattern="solid"
           hideLeftBorder="yes" hideTopBorder="yes" hideBottomBorder="yes"/>
          <textSegment><font face="Arial" size="8"/>
          <string><![CDATA[Delta]]></string></textSegment></text>

        <text name="B_SUBHDR" minWidowLines="1">
          <textSettings justify="center" spacing="single"/>
          <geometryInfo x="2.00000" y="2.00000" width="2.00000" height="0.14587"/>
          <visualSettings fillPattern="transparent" linePattern="solid"
           hideRightBorder="yes" hideTopBorder="yes"/>
          <textSegment><font face="Arial" size="8"/>
          <string><![CDATA[Echo]]></string></textSegment></text>
        <text name="B_COLCAP" minWidowLines="1">
          <textSettings justify="center" spacing="single"/>
          <geometryInfo x="2.00000" y="2.14661" width="1.00000" height="0.35339"/>
          <visualSettings fillPattern="transparent" linePattern="solid"
           hideRightBorder="yes" hideTopBorder="yes" hideBottomBorder="yes"/>
          <textSegment><font face="Arial" size="8"/>
          <string><![CDATA[Foxtrot]]></string></textSegment></text>

        <field name="F_ACCT" source="ACCT_NO">
          <font face="Arial" size="10"/>
          <geometryInfo x="1.00000" y="3.00000" width="3.00000" height="0.20000"/></field>
      </frame>
    </body>
  </section>
  </layout>
</report>
"""

# DECLARED stroking edges, read straight off the hideXBorder attributes.
DECLARED_EDGES = {
    "Alpha": {"top"},
    "Bravo": {"top", "right"},
    "Cee": {"bottom", "right"},
    "Delta": {"right"},
    "Echo": {"bottom", "left"},
    "Foxtrot": {"left"},
}

_SIDES = ("Top", "Bottom", "Left", "Right")


def _rdl():
    return ET.fromstring(convert(GRID_XML)["rdl_xml"])


def _literal(tb):
    """The static literal a Textbox prints, or ""."""
    v = tb.find(f"{NS}Paragraphs/{NS}Paragraph/{NS}TextRuns/{NS}TextRun/"
                f"{NS}Value")
    txt = (v.text or "") if v is not None else ""
    return txt[2:-1] if txt.startswith('="') and txt.endswith('"') else ""


def _stroking_edges(item):
    """The set of box edges an emitted report item actually strokes."""
    style = item.find(NS + "Style")
    if style is None:
        return set()
    base = style.find(NS + "Border")
    on = set()
    if base is not None and (base.findtext(NS + "Style") or "None") == "Solid":
        on |= {"top", "bottom", "left", "right"}
    for side in _SIDES:
        sb = style.find(NS + side + "Border")
        if sb is None:
            continue
        st = sb.findtext(NS + "Style")
        if st == "Solid":
            on.add(side.lower())
        elif st == "None":
            on.discard(side.lower())
    return on


def _inches(item, tag, default=0.0):
    raw = item.findtext(NS + tag)
    if not raw:
        return default
    try:
        return float(str(raw).replace("in", "").strip())
    except ValueError:
        return default


def _boxes_by_container(root):
    """[(container, [(item, literal, x0, y0, x1, y1, edges), ...]), ...].

    Grouped per ``<ReportItems>`` because that is where DECLARED siblings
    of one Oracle frame land, and their Top/Left share one origin there.
    """
    out = []
    for container in root.iter(NS + "ReportItems"):
        entries = []
        for item in container:
            if item.tag not in (NS + "Textbox", NS + "Rectangle",
                                NS + "Line", NS + "Image"):
                continue
            x0 = _inches(item, "Left")
            y0 = _inches(item, "Top")
            w = _inches(item, "Width")
            h = _inches(item, "Height")
            entries.append((item, _literal(item), x0, y0, x0 + w, y0 + h,
                            _stroking_edges(item)))
        if entries:
            out.append((container, entries))
    return out


def test_hidden_edges_never_stroke():
    """Emitted stroking edges == DECLARED stroking edges, per object.

    A hidden edge that paints anyway becomes a divider standing inside the
    neighbouring cell; a declared edge that stops painting loses a rule
    the source draws. Both directions are checked, so the assertion cannot
    be satisfied by simply drawing nothing.
    """
    root = _rdl()
    seen = {}
    for tb in root.iter(NS + "Textbox"):
        lit = _literal(tb)
        if lit in DECLARED_EDGES:
            seen[lit] = _stroking_edges(tb)
    assert set(seen) == set(DECLARED_EDGES), (
        f"fixture objects missing from the RDL: "
        f"{sorted(set(DECLARED_EDGES) - set(seen))}")
    assert seen == DECLARED_EDGES, (
        "emitted box edges disagree with the hideXBorder declaration: "
        + "; ".join(f"{k}: declared {sorted(DECLARED_EDGES[k])} "
                    f"-> emitted {sorted(seen[k])}"
                    for k in sorted(DECLARED_EDGES)
                    if seen[k] != DECLARED_EDGES[k]))


def test_size_floor_never_moves_a_declared_stroke():
    """A box whose right / bottom edge strokes keeps its DECLARED extent.

    The sub-header is declared 0.14587in tall and its bottom separator
    therefore sits 0.00074in ABOVE the caption box beneath it. Rounding it
    up to a 0.18in floor -- or even to 2 decimals -- pushes that separator
    into the caption.
    """
    root = _rdl()
    box = {}
    for tb in root.iter(NS + "Textbox"):
        lit = _literal(tb)
        if lit in DECLARED_EDGES:
            box[lit] = (_inches(tb, "Left"), _inches(tb, "Top"),
                        _inches(tb, "Width"), _inches(tb, "Height"))

    # BOTTOM stroke: declared height survives, and the separator lands
    # above the caption box it sits over.
    sub_x, sub_y, _sw, sub_h = box["Echo"]
    assert abs(sub_h - 0.14587) <= 0.0001, (
        f"sub-header height {sub_h} is not the declared 0.14587in")
    cap_top = box["Foxtrot"][1]
    assert sub_y + sub_h < cap_top, (
        f"sub-header separator at {sub_y + sub_h:.5f}in is not above the "
        f"caption box top {cap_top:.5f}in")

    # RIGHT stroke: declared width survives, and the declared 0.018in
    # gutter between the caption's right edge and the value's left edge
    # is still a gutter.
    cee_x, _cy, cee_w, _ch = box["Cee"]
    assert abs(cee_w - 0.30) <= 0.0001, (
        f"caption width {cee_w} is not the declared 0.30in")
    assert cee_x + cee_w <= box["Delta"][0] + 1e-9, (
        f"caption right edge {cee_x + cee_w:.5f}in has crossed into the "
        f"value box at {box['Delta'][0]:.5f}in")


def test_no_emitted_stroke_stands_inside_another_boxs_text():
    """GENERAL GUARD: every stroke this emitter writes sits on a declared
    box edge or in a declared gutter -- never inside another text box's
    span, where it would print through that box's glyphs.

    Checked per ``<ReportItems>`` container (one Oracle frame's declared
    siblings, sharing one origin) and only against boxes that actually
    carry text, so a background rectangle framing its own contents never
    flags. Clearance is a hairline: a stroke ON a neighbour's edge is the
    shared cell border Oracle draws, a stroke INSIDE it is not.
    """
    root = _rdl()
    clear = 0.005  # in; a stroke this far inside is not an edge graze
    bad = []
    for _container, entries in _boxes_by_container(root):
        texts = [e for e in entries if e[1]]
        for item, _lit, x0, y0, x1, y1, edges in entries:
            verticals = [("left", x0)] * ("left" in edges) \
                + [("right", x1)] * ("right" in edges)
            horizontals = [("top", y0)] * ("top" in edges) \
                + [("bottom", y1)] * ("bottom" in edges)
            for other, olit, ox0, oy0, ox1, oy1, _oe in texts:
                if other is item:
                    continue
                for name, at in verticals:
                    if (ox0 + clear < at < ox1 - clear
                            and y0 < oy1 - clear and y1 > oy0 + clear):
                        bad.append(
                            f"{item.get('Name')} {name} stroke at "
                            f"x={at:.4f}in stands inside {olit!r} "
                            f"({ox0:.4f}..{ox1:.4f}in)")
                for name, at in horizontals:
                    if (oy0 + clear < at < oy1 - clear
                            and x0 < ox1 - clear and x1 > ox0 + clear):
                        bad.append(
                            f"{item.get('Name')} {name} stroke at "
                            f"y={at:.4f}in stands inside {olit!r} "
                            f"({oy0:.4f}..{oy1:.4f}in)")
    assert not bad, "emitted strokes cut through text boxes:\n" + \
        "\n".join(sorted(set(bad)))
