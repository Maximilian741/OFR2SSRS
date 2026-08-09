"""A DECLARED box is TOP-anchored, never vertically centred.

Oracle puts the first line's ascent at the declared box top and leaves
whatever height is left over as slack BELOW the text. A taller box does not
push its own first line down -- which is why a declared title/subtitle pair
keeps its declared top-to-top distance however tall either box is.

Truth-measured on the page-chrome band of two reports: the declared
title-top -> subtitle-top distance of 0.2604in prints as 0.2607in. Emitting
``<VerticalAlign>Middle</VerticalAlign>`` centres the single line in the
box's slack instead, and the engine then drops the ink by (H - lineheight)/2.
Measured through the real ReportViewer engine on three reports, before the
fix, on declared subtitle boxes 0.515in / 0.427in / 0.396in tall holding one
line each:

    ink_top - declared_top   =  +0.167in / +0.113in / +0.097in
    after top-anchoring      =  -0.021in / -0.023in / -0.023in
      (the residual is the font's internal leading above the cap height,
       identical for every box of that size -- i.e. no displacement)

and the declared top-to-top distance came back: 0.401in -> 0.265in against a
declared 0.2604in on the report whose truth measures 0.2607in.

The export dialect declares NO vertical justification (a census of 314 real
exports found only ``verticalElasticity`` -- a GROWTH rule, not an anchor --
plus ``valign`` inside <webSource> HTML templates, which is a browser
rendering, not the paper layout). So the anchor is Top for every declared
object today; the parser still reads a declared justification so a dialect
that states one is honored rather than overridden.

The companion rule already locked elsewhere is the inset: a declared box has
zero padding (tests/test_declared_box_has_no_inset.py). The two combine into
the invariant this file gates -- A ZERO-INSET BOX IS A DECLARED BOX, AND A
DECLARED BOX ANCHORS TOP. Synthesized furniture that carries an invented
symmetric inset is out of scope here and keeps its own anchor.

Everything here is structural and synthetic: no report, column or label from
any real source appears in this file.
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert                      # noqa: E402
from converter.generators import rdl as R          # noqa: E402
from converter.parsers.oracle_xml import parse_oracle_xml   # noqa: E402

NS = ("{http://schemas.microsoft.com/sqlserver/reporting/"
      "2008/01/reportdefinition}")


def _q(tag: str) -> str:
    return NS + tag


# ---------------------------------------------------------------------------
# fixture: a declared page-chrome band with a SHORT title over a TALL subtitle
# ---------------------------------------------------------------------------

BAND_TOP = 0.26000          # declared title top (paper coordinates)
SUB_TOP = 0.56000           # declared subtitle top
TITLE_H = 0.20000           # box ~= one 12pt line
SUB_H = 0.60000             # box FAR taller than its one 10pt line
DECLARED_GAP = SUB_TOP - BAND_TOP


def _chrome_xml(vert_justify: str = "") -> str:
    """A report whose section <margin> declares a title and a much taller
    subtitle box. ``vert_justify`` optionally states an anchor on the
    subtitle, to prove a declaration is honored rather than overridden."""
    vj = f' vertJustify="{vert_justify}"' if vert_justify else ""
    return (
        '<?xml version="1.0"?><report name="VANCHOR_T" '
        'DTDVersion="9.0.2.0.10"><data><dataSource name="Q_Main">'
        '<select><![CDATA[select item_nm, amt from t]]></select>'
        '<group name="G_Main"><dataItem name="ITEM_NM" datatype="vchar2"/>'
        '<dataItem name="AMT" datatype="number"/></group>'
        '</dataSource></data>'
        '<layout><section name="main" width="8.50000" height="11.00000">'
        '<body><frame name="M_ALL">'
        '<geometryInfo x="0" y="0" width="7.5" height="3"/>'
        '<repeatingFrame name="R_Main" source="G_Main" printDirection="down">'
        '<geometryInfo x="0" y="0" width="7.5" height="0.4"/>'
        '<field name="F_ITEM" source="ITEM_NM">'
        '<font face="Arial" size="10"/>'
        '<geometryInfo x="0.1" y="0.05" width="2.0" height="0.2"/></field>'
        '<field name="F_AMT" source="AMT">'
        '<font face="Arial" size="10"/>'
        '<geometryInfo x="3.0" y="0.05" width="1.0" height="0.2"/></field>'
        '</repeatingFrame></frame></body>'
        '<margin>'
        '<text name="B_TITLE"><textSettings justify="center"/>'
        f'<geometryInfo x="1.00000" y="{BAND_TOP:.5f}" width="6.00000" '
        f'height="{TITLE_H:.5f}"/>'
        '<textSegment><font face="Arial" size="12"/>'
        '<string><![CDATA[Anchor Fixture Heading]]></string>'
        '</textSegment></text>'
        f'<text name="B_SUBTITLE"><textSettings justify="center"{vj}/>'
        f'<geometryInfo x="0.50000" y="{SUB_TOP:.5f}" width="7.50000" '
        f'height="{SUB_H:.5f}"/>'
        '<textSegment><font face="Arial" size="10"/>'
        '<string><![CDATA[Anchor fixture second line]]></string>'
        '</textSegment></text>'
        '</margin></section></layout></report>'
    )


# A declared tabular sheet: caption cells and detail cells are DECLARED
# column boxes, so they anchor the same way.
_TABULAR = (
    '<?xml version="1.0"?><report name="VANCHOR_TAB" '
    'DTDVersion="9.0.2.0.10"><data><dataSource name="Q_1">'
    '<select><![CDATA[SELECT ALPHA, BRAVO FROM T]]></select>'
    '<group name="G_1"><dataItem name="ALPHA" datatype="vchar2"/>'
    '<dataItem name="BRAVO" datatype="number"/></group>'
    '</dataSource></data><layout>'
    '<section name="main" width="8.50000" height="11.00000">'
    '<body width="7.50000" height="9.50000"><location x="0.5" y="0.5"/>'
    '<frame name="M_1"><geometryInfo x="0" y="0" width="7.5" height="1.2"/>'
    # a caption box FAR taller than its own line -- the shape that centres
    '<text name="B_H1"><textSettings justify="start"/>'
    '<geometryInfo x="0.10000" y="0.10000" width="1.80000" height="0.47000"/>'
    '<textSegment><font face="Arial" size="10"/>'
    '<string><![CDATA[Quay Alpha]]></string></textSegment></text>'
    '<text name="B_H2"><textSettings justify="start"/>'
    '<geometryInfo x="2.10000" y="0.10000" width="1.80000" height="0.47000"/>'
    '<textSegment><font face="Arial" size="10"/>'
    '<string><![CDATA[Quay Bravo]]></string></textSegment></text>'
    '<repeatingFrame name="R_1" source="G_1" printDirection="down">'
    '<geometryInfo x="0" y="0.65000" width="7.5" height="0.22000"/>'
    '<field name="F_A" source="ALPHA" alignment="start">'
    '<font face="Arial" size="10"/>'
    '<geometryInfo x="0.10000" y="0.65000" width="1.80000" height="0.19000"/>'
    '</field>'
    '<field name="F_B" source="BRAVO" alignment="start">'
    '<font face="Arial" size="10"/>'
    '<geometryInfo x="2.10000" y="0.65000" width="1.80000" height="0.19000"/>'
    '</field>'
    '</repeatingFrame></frame></body></section></layout></report>'
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _pt(style, tag: str) -> float:
    if style is None:
        return 0.0
    t = (style.findtext(_q(tag)) or "").strip().lower()
    for unit, per_pt in (("pt", 1.0), ("in", 72.0), ("cm", 72.0 / 2.54),
                         ("mm", 72.0 / 25.4)):
        if t.endswith(unit):
            try:
                return float(t[:-len(unit)]) * per_pt
            except ValueError:
                return 0.0
    return 0.0


def _in(el, tag):
    c = el.find(_q(tag))
    if c is None or not (c.text or "").strip():
        return None
    try:
        return float(c.text.replace("in", "").strip())
    except ValueError:
        return None


def _named(rdl: str, suffix: str):
    """The emitted Textbox whose Name ends with ``suffix``."""
    root = ET.fromstring(rdl.encode("utf-8"))
    for tb in root.iter(_q("Textbox")):
        if (tb.get("Name") or "").endswith(suffix):
            return tb
    return None


def _first_line_top(tb) -> float:
    """WHERE THE FIRST LINE'S TOP LANDS, in inches from the box's own Top --
    the engine's anchoring model, written out.

    Top anchor  -> box top + the top inset.
    Middle      -> the line is centred in the interior, so it starts
                   (interior - lineheight)/2 lower.
    Bottom      -> the line is flushed to the interior's floor.
    """
    st = tb.find(_q("Style"))
    top = _in(tb, "Top") or 0.0
    h = _in(tb, "Height")
    ptop, pbot = _pt(st, "PaddingTop"), _pt(st, "PaddingBottom")
    va = (st.findtext(_q("VerticalAlign")) or "Top").strip().lower()
    anchored = top + ptop / 72.0
    if va == "top" or h is None:
        return anchored
    sizes = [float((fs.text or "0pt")[:-2]) for fs in tb.iter(_q("FontSize"))
             if (fs.text or "").strip().endswith("pt")]
    fam = next((f.text for f in tb.iter(_q("FontFamily"))
                if (f.text or "").strip()), "")
    line = R._font_line_box_pt(max(sizes) if sizes else 10.0,
                               R._family_is_sans(fam)) / 72.0
    interior = h - (ptop + pbot) / 72.0
    slack = max(0.0, interior - line)
    return anchored + (slack / 2.0 if va == "middle" else slack)


def _centred_zero_inset_boxes(rdl: str):
    """Every emitted box that has NO inset -- i.e. a declared box -- yet
    carries a synthesized Middle/Bottom anchor."""
    root = ET.fromstring(rdl.encode("utf-8"))
    bad = []
    for tb in root.iter(_q("Textbox")):
        st = tb.find(_q("Style"))
        if st is None:
            continue
        va = (st.findtext(_q("VerticalAlign")) or "Top").strip().lower()
        if va == "top":
            continue
        if _pt(st, "PaddingTop") > 0.001 or _pt(st, "PaddingBottom") > 0.001:
            continue        # synthesized furniture with an invented inset
        bad.append((tb.get("Name"), va))
    return bad


# ---------------------------------------------------------------------------
# (a) the dialect helper itself
# ---------------------------------------------------------------------------

def test_the_anchor_helper_tops_whatever_declares_nothing():
    """The default IS the dialect: an object that declares no vertical
    justification -- which is every object in every real export -- anchors
    Top, and so does a synthesized box that has no declaration at all."""
    assert R._declared_vertical_align() == "Top"
    assert R._declared_vertical_align(None) == "Top"

    class _Blank:
        vertical_align = ""

    assert R._declared_vertical_align(_Blank()) == "Top"


@pytest.mark.parametrize("declared,expected", [
    ("top", "Top"), ("middle", "Middle"), ("center", "Middle"),
    ("bottom", "Bottom"), ("MIDDLE", "Middle"), ("  bottom ", "Bottom"),
    ("sideways", "Top"),          # unmappable -> the dialect default
])
def test_a_declared_vertical_justification_is_honored(declared, expected):
    class _Decl:
        vertical_align = declared

    assert R._declared_vertical_align(_Decl()) == expected


def test_the_parser_reads_a_declared_vertical_justification():
    """Honoring a declaration requires carrying it: the parser lifts a
    vertical-justify attribute onto the layout object, and leaves it empty
    when -- as in every real export -- none is declared."""
    plain = parse_oracle_xml(_chrome_xml().encode("utf-8"))
    declared = parse_oracle_xml(
        _chrome_xml(vert_justify="bottom").encode("utf-8"))

    def _sub_field(rep):
        def _walk(groups):
            for g in groups:
                for f in g.fields:
                    if (f.name or "").upper().endswith("SUBTITLE"):
                        return f
                hit = _walk(g.children)
                if hit is not None:
                    return hit
            return None
        return _walk(rep.layout)

    assert _sub_field(plain) is not None, "fixture must parse its subtitle"
    assert _sub_field(plain).vertical_align == ""
    assert _sub_field(declared).vertical_align == "bottom"


# ---------------------------------------------------------------------------
# (b) the emitter: a declared box anchors Top
# ---------------------------------------------------------------------------

def test_declared_page_chrome_is_top_anchored():
    rdl = convert(_chrome_xml().encode("utf-8"))["rdl_xml"]
    for suffix in ("B_TITLE", "B_SUBTITLE"):
        tb = _named(rdl, suffix)
        assert tb is not None, f"fixture must emit its declared {suffix} box"
        st = tb.find(_q("Style"))
        assert (st.findtext(_q("VerticalAlign")) or "Top") == "Top", suffix


def test_declared_chrome_keeps_its_declared_top_to_top_distance():
    """THE measured defect. Two declared boxes of very different heights:
    top-anchored, the distance between their first lines is the distance
    between their declared tops. Centred, the taller box swallows half its
    own slack and the gap grows by that much."""
    rdl = convert(_chrome_xml().encode("utf-8"))["rdl_xml"]
    title, sub = _named(rdl, "B_TITLE"), _named(rdl, "B_SUBTITLE")
    gap = _first_line_top(sub) - _first_line_top(title)
    assert gap == pytest.approx(DECLARED_GAP, abs=0.005), (
        "declared top-to-top distance lost", gap, DECLARED_GAP)
    # ...and the fixture is a real trap: the subtitle box is tall enough that
    # centring it would move the line by far more than the tolerance above.
    assert SUB_H - TITLE_H > 0.30, "fixture must declare a tall subtitle box"


def test_declared_column_boxes_are_top_anchored():
    """The body flavour: a declared caption box taller than its own line
    still starts its text at the declared top."""
    rdl = convert(_TABULAR.encode("utf-8"))["rdl_xml"]
    root = ET.fromstring(rdl.encode("utf-8"))
    seen = 0
    for tb in root.iter(_q("Textbox")):
        nm = tb.get("Name") or ""
        if not (nm.startswith("Hdr_") or nm.startswith("Cell_")):
            continue
        seen += 1
        st = tb.find(_q("Style"))
        assert (st.findtext(_q("VerticalAlign")) or "Top") == "Top", nm
    assert seen >= 4, "fixture must emit caption and data cells"


@pytest.mark.parametrize("src", [_chrome_xml(), _TABULAR],
                         ids=["page-chrome", "tabular"])
def test_no_zero_inset_box_is_vertically_centred(src):
    """The general invariant, swept over every emitted box: a box with no
    inset is a DECLARED box, and a declared box anchors Top. (A synthesized
    box that invents a symmetric inset is furniture, not a declaration, and
    is deliberately not measured here.)"""
    rdl = convert(src.encode("utf-8"))["rdl_xml"]
    assert _centred_zero_inset_boxes(rdl) == []


def test_no_emitter_hardcodes_a_vertical_anchor_on_a_zero_inset_box():
    """The source-level guard, so the next new declared-geometry emitter
    cannot re-invent the centring: every literal Middle/Bottom left in the
    generator belongs to a call that also invents an inset."""
    import re
    src = (ROOT / "backend" / "converter" / "generators"
           / "rdl.py").read_text(encoding="utf-8")
    lines = src.splitlines()
    offenders = []
    for i, line in enumerate(lines):
        if not re.search(r'vertical_align="(Middle|Bottom)"', line):
            continue
        start = i
        while start > 0 and "_build_textbox" not in lines[start]:
            start -= 1
        depth, blob = 0, []
        for j in range(start, min(len(lines), start + 40)):
            blob.append(lines[j])
            depth += lines[j].count("(") - lines[j].count(")")
            if j > start and depth <= 0:
                break
        m = re.search(r"padding=([^,)]+)", " ".join(blob))
        pad = m.group(1).strip() if m else ""
        if pad in ("", '"0pt"', "_cell_pad"):
            offenders.append((i + 1, line.strip()))
    assert offenders == [], (
        "a zero-inset (declared) box must anchor Top -- use "
        "_declared_vertical_align()", offenders)
