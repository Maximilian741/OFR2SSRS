"""A SIZE FLOOR MAY NOT MOVE A DECLARED BOX ONTO ITS NEIGHBOUR.

Every emitter carries readability floors -- a minimum left offset, a minimum
frame height, a minimum column width -- put there to keep a degenerate
declaration from collapsing to nothing. Each of them is also a way to *invent*
geometry, and where a declaration packs two boxes edge to edge the invention
lands inside the neighbour. The engine then prints one box's border, or one
box's value, through the other box's glyphs, and the paint gate reports a
collision the source does not have.

The four measured cases, all against Oracle-produced truth PDFs:

  * a 0.02in minimum on a member's LEFT turned a declaration that abuts two
    boxes ([0 .. 3.1875] and [3.1875 .. 6.6875]) into [0.02 .. 3.2075] and
    [3.19 .. 6.69]: the left box's right edge then stood 0.0175in inside the
    right box and stroked through its first glyph. The truth PDF draws those
    two frames sharing ONE edge, at 229.52pt, with nothing between them;
  * 2-decimal rounding of that same LEFT is worth up to 0.36pt and supplies
    the rest of the slack, so an edge the declaration strokes carries the
    same 4-decimal precision the far edges already do;
  * a 0.5in minimum on a FRAME rectangle floored a prose frame declared
    0.1875in tall, and because that frame's declaration strokes its edges,
    its left and bottom rules moved 0.31in down -- through the caption
    declared directly beneath it;
  * a 0.4in minimum column width floored a ledger index column declared
    0.1875in wide whose neighbour is declared 0.25in away, so the index value
    printed on top of the name. The truth prints the two boxes at
    27.0..40.5pt and 45.0..198.0pt -- exactly as declared.

A floor that guards something real is kept where it guards it: a BORDERLESS
grouping frame still gets its 0.5in minimum (nothing about it prints), and a
column with room still gets its 0.4in.

Companion rule for the harness, guarded at the bottom: a staticized
PLACEHOLDER may not be wider than the box the declaration sizes for the real
value. The staticizer prints a field's humanised NAME where the report prints
its VALUE, and a name is routinely several times longer -- measured, a payment
type declared 0.375in wide holds "CK" (13.9pt) in the truth while the
placeholder needs 27.8pt for its first word alone. Chasing that in the
converter means widening boxes the declaration deliberately made narrow.

Everything here is synthetic and structural: no report, column or label from
any real source appears in this file.
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "tools" / "renderlab"))

from converter import convert                       # noqa: E402

NS = ("{http://schemas.microsoft.com/sqlserver/reporting/"
      "2008/01/reportdefinition}")


def _q(tag: str) -> str:
    return NS + tag


def _num(el, tag):
    c = el.find(_q(tag))
    if c is None or not (c.text or "").strip():
        return None
    try:
        return float(c.text.replace("in", "").strip())
    except ValueError:
        return None


def _by_name(rdl: str) -> dict:
    root = ET.fromstring(rdl.encode("utf-8"))
    out = {}
    for tag in ("Rectangle", "Textbox"):
        for el in root.iter(_q(tag)):
            if el.get("Name"):
                out[el.get("Name")] = el
    return out


def _text_of(el) -> str:
    return "".join(v.text or "" for v in el.iter(_q("Value")))


def _find_by_text(rdl: str, needle: str):
    for name, el in _by_name(rdl).items():
        if el.tag == _q("Textbox") and needle in _text_of(el):
            return name, el
    return None, None


# ---------------------------------------------------------------------------
# fixture A -- a positional per-record form
#
# DECLARES:
#   two bordered boxes that ABUT exactly at 3.18750in;
#   a BORDERED frame 0.18750in tall (its edges print);
#   a BORDERLESS grouping frame of the same declared height (nothing prints).
# ---------------------------------------------------------------------------

_ABUT_L_W = 3.18750
_ABUT_R_X = 3.18750

_FORM = (
    '<?xml version="1.0"?><report name="FLOORS_F" DTDVersion="9.0.2.0.10">'
    '<data><dataSource name="Q_1">'
    '<select><![CDATA[SELECT ALPHA FROM T]]></select>'
    '<group name="G_1"><dataItem name="ALPHA" datatype="vchar2"/></group>'
    '</dataSource></data><layout>'
    '<section name="main" width="8.50000" height="11.00000">'
    '<body width="8.50000" height="9.50000"><location x="0.0" y="0.0"/>'
    '<frame name="M_1"><geometryInfo x="0" y="0" width="8.5" height="3.0"/>'
    '<repeatingFrame name="R_1" source="G_1" printDirection="down">'
    '<geometryInfo x="0" y="0" width="8.5" height="2.8"/>'
    '<text name="B_L"><textSettings justify="start"/>'
    f'<geometryInfo x="0.00000" y="0.20000" width="{_ABUT_L_W:.5f}"'
    ' height="0.18750"/><visualSettings linePattern="solid"/>'
    '<textSegment><font face="Arial" size="10"/>'
    '<string><![CDATA[Left Box Label]]></string></textSegment></text>'
    '<text name="B_R"><textSettings justify="start"/>'
    f'<geometryInfo x="{_ABUT_R_X:.5f}" y="0.20000" width="3.50000"'
    ' height="0.18750"/><visualSettings linePattern="solid"/>'
    '<textSegment><font face="Arial" size="10"/>'
    '<string><![CDATA[Right Box Label]]></string></textSegment></text>'
    '<frame name="M_BORDERED">'
    '<geometryInfo x="1.50000" y="1.00000" width="5.00000" height="0.18750"/>'
    '<visualSettings linePattern="solid"/>'
    '<field name="F_A" source="ALPHA" alignment="start">'
    '<font face="Arial" size="10"/>'
    '<geometryInfo x="1.50000" y="1.00000" width="5.00000"'
    ' height="0.18750"/></field></frame>'
    '<frame name="M_PLAIN">'
    '<geometryInfo x="1.50000" y="2.00000" width="5.00000" height="0.18750"/>'
    '<text name="B_P"><textSettings justify="start"/>'
    '<geometryInfo x="1.50000" y="2.00000" width="2.00000" height="0.18750"/>'
    '<textSegment><font face="Arial" size="10"/>'
    '<string><![CDATA[Plain Frame Text]]></string></textSegment></text>'
    '</frame>'
    '</repeatingFrame></frame></body></section></layout></report>'
).encode()


@pytest.fixture(scope="module")
def form_rdl():
    return convert(_FORM)["rdl_xml"]


def test_two_boxes_the_source_abuts_are_emitted_abutting(form_rdl):
    """The measured defect: a left minimum slid BOTH boxes right, and the
    left one's stroked right edge landed inside the right one's first
    glyph."""
    _n, left = _find_by_text(form_rdl, "Left Box Label")
    _n2, right = _find_by_text(form_rdl, "Right Box Label")
    assert left is not None and right is not None, "fixture must emit both"
    lx, lw = _num(left, "Left"), _num(left, "Width")
    rx = _num(right, "Left")
    assert lx == pytest.approx(0.0, abs=1e-6), (
        "a box declared at its frame's left edge belongs there", lx)
    assert rx == pytest.approx(_ABUT_R_X, abs=1e-6), (
        "the right box keeps its declared x at full precision", rx)
    assert lx + lw <= rx + 1e-6, (
        "the left box's stroked right edge may not enter its neighbour",
        lx + lw, rx)


def test_a_stroked_left_edge_keeps_full_declared_precision(form_rdl):
    """A box whose declaration strokes its left edge prints that edge as a
    rule; 2-decimal rounding moves the rule by up to 0.36pt, which is most of
    the slack the abutment has."""
    _n, right = _find_by_text(form_rdl, "Right Box Label")
    txt = (right.find(_q("Left")).text or "").strip()
    assert txt.endswith("in")
    decimals = len(txt[:-2].split(".")[1]) if "." in txt[:-2] else 0
    assert decimals >= 4, ("a stroked left edge is emitted at 4 decimals",
                           txt)


def test_a_bordered_frame_keeps_its_declared_height(form_rdl):
    """Its bottom edge is a printed rule, so a height floor would relocate
    that rule onto whatever the source declares underneath."""
    boxes = _by_name(form_rdl)
    bordered = [el for n, el in boxes.items()
                if el.tag == _q("Rectangle")
                and "Fields!ALPHA" in _text_of(el)
                and _num(el, "Left") == pytest.approx(1.50, abs=1e-6)]
    assert bordered, "fixture must emit the bordered frame rectangle"
    for el in bordered:
        assert _num(el, "Height") < 0.5, (
            "the 0.5in floor may not apply to a frame that strokes its "
            "edges", el.get("Name"), _num(el, "Height"))


def test_a_borderless_grouping_frame_still_gets_its_floor(form_rdl):
    """The fix is SCOPED: nothing about an invisible container prints, so its
    minimum height is free slack and stays."""
    plain = [el for n, el in _by_name(form_rdl).items()
             if el.tag == _q("Rectangle")
             and "Plain Frame Text" in _text_of(el)
             and _num(el, "Left") == pytest.approx(1.50, abs=1e-6)]
    assert plain, "fixture must emit the borderless frame rectangle"
    assert any(_num(el, "Height") == pytest.approx(0.5, abs=1e-6)
               for el in plain), [_num(el, "Height") for el in plain]


# ---------------------------------------------------------------------------
# fixture B -- the grouped-tabular break archetype
#
# DECLARES:
#   a band FILL (so a caption edge drawn in the band's own colour is a real
#   stroke in the emitted RDL, not the white the emitter already drops);
#   a group header whose lines jitter onto FOUR distinct y values, so a
#   synthesized one-line-per-stride stack overshoots the declaration;
#   column captions at their own y (0.30in below the last header line);
#   a detail index column 0.18750in wide whose neighbour starts 0.25in away.
# ---------------------------------------------------------------------------

_IDX_X, _IDX_W, _NAME_X = 0.06250, 0.18750, 0.31250
_GH_Y0, _CAP_Y = 0.40, 1.10          # -> declared strip top = 0.70in
_STRIDE_TOP = 0.88                   # what a 4-line synthesized stack gives

_GROUPED = f'''<?xml version="1.0" encoding="UTF-8"?>
<report name="FLOORS_G" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q_OUTER">
      <select><![CDATA[SELECT BREAK_KEY, G_CODE FROM T_BREAK]]></select>
      <group name="G_OUTER">
        <dataItem name="BREAK_KEY" datatype="vchar2" width="30">
          <dataDescriptor expression="BREAK_KEY" order="1" width="30"/>
        </dataItem>
        <dataItem name="G_CODE" datatype="vchar2" width="20">
          <dataDescriptor expression="G_CODE" order="2" width="20"/>
        </dataItem>
      </group>
    </dataSource>
    <dataSource name="Q_DETAIL">
      <select><![CDATA[SELECT D_IDX, D_NAME, D_KIND, CS_TALLY
       FROM T_DETAIL]]></select>
      <group name="G_DETAIL">
        <dataItem name="D_IDX" oracleDatatype="number" width="5">
          <dataDescriptor expression="D_IDX" order="1" width="5"/>
        </dataItem>
        <dataItem name="D_NAME" datatype="vchar2" width="60">
          <dataDescriptor expression="D_NAME" order="2" width="60"/>
        </dataItem>
        <dataItem name="D_KIND" datatype="vchar2" width="15">
          <dataDescriptor expression="D_KIND" order="3" width="15"/>
        </dataItem>
        <dataItem name="CS_TALLY" oracleDatatype="number" width="10">
          <dataDescriptor expression="CS_TALLY" order="4" width="10"/>
        </dataItem>
      </group>
    </dataSource>
  </data>
  <layout>
  <section name="main">
    <body height="9.6">
      <frame name="M_BODY">
        <geometryInfo x="0.00" y="0.00" width="8.40" height="3.00"/>
        <generalLayout verticalElasticity="variable"/>
      <repeatingFrame name="R_OUTER" source="G_OUTER" printDirection="down">
        <geometryInfo x="0.00" y="{_GH_Y0:.2f}" width="8.40" height="2.40"/>
        <generalLayout verticalElasticity="variable"/>
        <visualSettings fillPattern="solid" fillBackgroundColor="gray"/>
        <text name="B_K1"><textSettings spacing="single"/>
          <geometryInfo x="0.20" y="{_GH_Y0:.2f}" width="0.60" height="0.17"/>
          <textSegment><font face="Arial" size="10"/>
          <string><![CDATA[Key A]]></string></textSegment></text>
        <field name="F_KEY" source="BREAK_KEY" alignment="start">
          <font face="Arial" size="10"/>
          <geometryInfo x="0.90" y="0.55" width="1.50" height="0.17"/></field>
        <text name="B_K2"><textSettings spacing="single"/>
          <geometryInfo x="0.20" y="0.72" width="0.60" height="0.10"/>
          <textSegment><font face="Arial" size="10"/>
          <string><![CDATA[Key B]]></string></textSegment></text>
        <field name="F_CODE" source="G_CODE" alignment="start">
          <font face="Arial" size="10"/>
          <geometryInfo x="0.90" y="0.75" width="1.50" height="0.17"/></field>

        <text name="B_C1"><textSettings spacing="single"/>
          <geometryInfo x="{_IDX_X:.5f}" y="{_CAP_Y:.5f}" width="0.31250"
           height="0.18750"/>
          <textSegment><font face="Arial" size="9" bold="yes"/>
          <string><![CDATA[Idx]]></string></textSegment></text>
        <text name="B_C2"><textSettings spacing="single"/>
          <geometryInfo x="0.50000" y="{_CAP_Y:.5f}" width="1.31250"
           height="0.18750"/>
          <textSegment><font face="Arial" size="9" bold="yes"/>
          <string><![CDATA[Name Caption]]></string></textSegment></text>
        <text name="B_C3"><textSettings spacing="single"/>
          <geometryInfo x="2.60000" y="{_CAP_Y:.5f}" width="0.60000"
           height="0.18750"/>
          <textSegment><font face="Arial" size="9" bold="yes"/>
          <string><![CDATA[Kind]]></string></textSegment></text>

        <repeatingFrame name="R_DETAIL" source="G_DETAIL"
         printDirection="down">
          <geometryInfo x="{_IDX_X:.5f}" y="1.30000" width="8.30000"
           height="0.30000"/>
          <field name="F_IDX" source="D_IDX" alignment="end">
            <font face="Arial" size="9"/>
            <geometryInfo x="{_IDX_X:.5f}" y="1.30000" width="{_IDX_W:.5f}"
             height="0.22925"/></field>
          <field name="F_NAME" source="D_NAME" alignment="start">
            <font face="Arial" size="9"/>
            <geometryInfo x="{_NAME_X:.5f}" y="1.30000" width="2.12500"
             height="0.22925"/></field>
          <field name="F_KIND" source="D_KIND" alignment="center">
            <font face="Arial" size="9"/>
            <geometryInfo x="2.60000" y="1.30000" width="0.60000"
             height="0.22925"/></field>
        </repeatingFrame>

        <frame name="M_FOOT">
          <geometryInfo x="0.20" y="1.90" width="8.20" height="0.30"/>
          <text name="B_FOOT"><textSettings spacing="single"/>
            <geometryInfo x="4.60" y="1.95" width="1.00" height="0.19"/>
            <textSegment><font face="Arial" size="10" bold="yes"/>
            <string><![CDATA[Foot Label]]></string></textSegment></text>
          <field name="F_TALLY" source="CS_TALLY" alignment="start">
            <font face="Arial" size="10"/>
            <geometryInfo x="5.90" y="1.95" width="1.30"
             height="0.19"/></field>
        </frame>
      </repeatingFrame>
      </frame>
    </body>
  </section>
  </layout>
</report>'''.encode()


@pytest.fixture(scope="module")
def grouped_rdl():
    rdl = convert(_GROUPED)["rdl_xml"]
    assert "Tablix_GroupedSubtotal" in rdl, (
        "fixture must route through the grouped-tabular emitter")
    return rdl


def test_a_narrow_declared_column_stays_off_its_neighbour(grouped_rdl):
    boxes = _by_name(grouped_rdl)
    idx, name = boxes.get("Tb_D_0"), boxes.get("Tb_D_1")
    assert idx is not None and name is not None
    right = _num(idx, "Left") + _num(idx, "Width")
    assert right <= _num(name, "Left") + 1e-6, (
        "the width floor may not push a column over the next declared one",
        right, _num(name, "Left"))
    # ...and the fixture is a real trap: the declared gap is under the floor.
    assert _NAME_X - _IDX_X < 0.4


def _strip_top_in_header(rdl: str) -> float:
    """The column strip's top measured from the TOP OF THE HEADER BLOCK.

    The header block is one or more leading tablix rows (the group band, then
    the strip), so the strip's declared offset is the heights of every header
    row above it plus its own Top inside its row. Measuring it this way pins
    BOTH numbers -- the offset itself and the band row's height -- where the
    old assertion could only see a Top inside a single fused row."""
    root = ET.fromstring(rdl.encode("utf-8"))
    tablix = next(t for t in root.iter(_q("Tablix"))
                  if (t.get("Name") or "") == "Tablix_GroupedSubtotal")
    above = 0.0
    for row in tablix.iter(_q("TablixRow")):
        band = next((r for r in row.iter(_q("Rectangle"))
                     if (r.get("Name") or "") == "GTS_ColBand"), None)
        if band is not None:
            return above + (_num(band, "Top") or 0.0)
        above += _num(row, "Height") or 0.0
    raise AssertionError("fixture must emit the column strip")


def test_the_column_strip_starts_where_its_captions_are_declared(grouped_rdl):
    """The strip is not stacked one synthesized line-height per group-header
    sub-line: the captions carry their own y, and the source's own bracketing
    rules print at THEIRS, so a synthesized top puts band and rules on
    different geometry."""
    assert _by_name(grouped_rdl).get("GTS_ColBand") is not None, (
        "fixture must emit the column strip")
    got = _strip_top_in_header(grouped_rdl)
    assert got == pytest.approx(_CAP_Y - _GH_Y0, abs=0.005), (
        got, _CAP_Y - _GH_Y0)
    # the trap: a per-sub-line synthesized stack lands somewhere else
    assert abs(_STRIDE_TOP - (_CAP_Y - _GH_Y0)) > 0.1


def test_band_captions_carry_no_edge_in_their_own_band_colour(grouped_rdl):
    """An outline drawn in the colour already under it prints nothing, but it
    is still a stroke in the PDF -- and on a band whose declared sub-lines sit
    a hair apart it ran through the caption's own glyphs."""
    root = ET.fromstring(grouped_rdl.encode("utf-8"))
    band_bg = {}
    for rect in root.iter(_q("Rectangle")):
        st = rect.find(_q("Style"))
        if st is not None and st.findtext(_q("BackgroundColor")):
            band_bg[rect.get("Name")] = st.findtext(
                _q("BackgroundColor")).strip().lower()
    fills = set(band_bg.values())
    for name, tb in _by_name(grouped_rdl).items():
        if not (name.startswith("Tb_CH_") or name.startswith("Tb_GH_")):
            continue
        st = tb.find(_q("Style"))
        border = st.find(_q("Border")) if st is not None else None
        col = (border.findtext(_q("Color")) or "").strip().lower() \
            if border is not None else ""
        own = (st.findtext(_q("BackgroundColor")) or "").strip().lower() \
            if st is not None else ""
        if not col:
            continue
        assert col != own, (name, "an edge in the box's own fill is no edge")
        assert col not in fills, (
            name, "an edge in the band's own fill is no edge", col)


# ---------------------------------------------------------------------------
# the harness side: a placeholder is a stand-in VALUE, not a wide NAME
# ---------------------------------------------------------------------------

def _mini_rdl(width_in: str, value: str, size: str = "9pt") -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<Report xmlns="http://schemas.microsoft.com/sqlserver/reporting/'
        '2008/01/reportdefinition"><Body><ReportItems>'
        '<Textbox Name="Tb_Probe"><Paragraphs><Paragraph><TextRuns><TextRun>'
        f'<Value>{value}</Value>'
        f'<Style><FontSize>{size}</FontSize><FontFamily>Arial</FontFamily>'
        '</Style></TextRun></TextRuns></Paragraph></Paragraphs>'
        f'<Left>0in</Left><Top>0in</Top><Width>{width_in}</Width>'
        '<Height>0.19in</Height></Textbox>'
        '</ReportItems><Height>1in</Height></Body>'
        '<Width>8.5in</Width></Report>'
    )


def _probe_text(rdl: str) -> str:
    from ms_layout import staticize                # noqa: PLC0415
    out = ET.fromstring(staticize(rdl).encode("utf-8"))
    tb = next(t for t in out.iter(_q("Textbox"))
              if t.get("Name") == "Tb_Probe")
    return next(v.text or "" for v in tb.iter(_q("Value")))


def _fits(text: str, size_pt: float, box_in: float) -> bool:
    from converter.generators.rdl import _afm_text_width   # noqa: PLC0415
    return _afm_text_width(text, size_pt, False, True) <= box_in + 1e-9


def test_a_field_placeholder_is_trimmed_to_the_declared_box():
    """The humanised name of a narrow column is far wider than the value the
    column holds; left alone it wraps out of the row and the next row's fill
    buries it, which reads as a converter defect it is not."""
    rdl = _mini_rdl("0.375in", "=Fields!SOME_RATHER_LONG_COLUMN.Value")
    got = _probe_text(rdl)
    assert got, "a placeholder still prints"
    assert _fits(got, 9.0, 0.375), (got, "must fit the declared box")


def test_a_roomy_box_keeps_the_whole_placeholder():
    """Trimming is not a blanket truncation: where the declared box holds the
    humanised name, the harness prints all of it."""
    rdl = _mini_rdl("4.0in", "=Fields!SOME_RATHER_LONG_COLUMN.Value")
    assert _probe_text(rdl).strip() == "SOME RATHER LONG COLUMN"


def test_a_declared_literal_is_never_trimmed_to_fit():
    """Whether the REPORT'S OWN text fits the box it declares is a real
    fidelity question; only the invented part of the string gives way."""
    literal = "Declared Boilerplate Sentence That Does Not Fit"
    rdl = _mini_rdl("0.5in", f'="{literal}" &amp; Fields!SOME_COLUMN.Value')
    got = _probe_text(rdl)
    assert literal in got, (got, "the declared literal survives verbatim")
    assert "SOME COLUMN" not in got, (
        got, "the invented placeholder is what gave way")
