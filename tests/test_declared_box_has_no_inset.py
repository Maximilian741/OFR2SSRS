"""A DECLARED box has no inset: its declared width IS the usable text width
and its declared top IS where the first line starts.

Oracle's own boxes carry no padding. Every point the emitter invented cost
real geometry, measured against the truth PDFs:

  * a 1pt cell/caption inset put a whole header row +0.0139in right of the
    truth's glyph x (every left-anchored caption, one report-wide constant);
  * a 2pt inset on a top-anchored declared box dropped every line 2pt
    (0.0278in) below its declared top;
  * 2pt on each side of a 4.33in declared cover-title box left 4.28in of
    usable width -- and the title wrapped to a second line it does not have,
    which pushed the whole cover down by a line.

So: the emitter writes 0pt unless the DECLARATION states an inset (an Oracle
detail-frame indent does), and the only other padding allowed anywhere is the
one-line ceiling on the side the text is NOT anchored to -- which moves no
glyph and exists solely so the engine cannot reserve a second line inside a
fixed single-line box (a reserved-but-unprintable line steals a whole word
from the visible line).

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

NS = ("{http://schemas.microsoft.com/sqlserver/reporting/"
      "2008/01/reportdefinition}")


def _q(tag: str) -> str:
    return NS + tag


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _pt(style, tag: str) -> float:
    """A padding in POINTS (RDL sizes may be pt, in, cm or mm)."""
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


def _size_family(tb):
    sizes = []
    for fs in tb.iter(_q("FontSize")):
        t = (fs.text or "").strip()
        if t.endswith("pt"):
            try:
                sizes.append(float(t[:-2]))
            except ValueError:
                pass
    fam = next((f.text for f in tb.iter(_q("FontFamily"))
                if (f.text or "").strip()), "")
    return (max(sizes) if sizes else 10.0), fam


def _declared_boxes(rdl: str):
    """Every textbox that renders a DECLARED object.

    Scope note: the <Body> is where the declaration's own objects land, so the
    sweeps below run there. The page bands additionally carry SYNTHESIZED
    chrome (a run-date stamp, a page-number line) that no declaration places
    and whose house styling is not this rule's business; the DECLARED page
    furniture that does live there -- the criteria echo, emitted at the
    source's own label/value x -- is asserted by name instead."""
    root = ET.fromstring(rdl.encode("utf-8"))
    body = root.find(_q("Body"))
    return list(body.iter(_q("Textbox"))) if body is not None else []


def _displacing_insets(rdl: str, boxes=None):
    """Every emitted padding that MOVES a glyph off its declared anchor."""
    bad = []
    for tb in (_declared_boxes(rdl) if boxes is None else boxes):
        st = tb.find(_q("Style"))
        if st is None:
            continue
        name = tb.get("Name")
        ta = (next((t.text for t in tb.iter(_q("TextAlign"))
                    if (t.text or "").strip()), "") or "left").lower()
        va = (st.findtext(_q("VerticalAlign")) or "top").lower()
        pl, pr = _pt(st, "PaddingLeft"), _pt(st, "PaddingRight")
        ptop, pbot = _pt(st, "PaddingTop"), _pt(st, "PaddingBottom")
        if ta in ("left", "general", "default") and pl > 0.001:
            bad.append((name, "PaddingLeft", round(pl, 3)))
        elif ta == "right" and pr > 0.001:
            bad.append((name, "PaddingRight", round(pr, 3)))
        elif ta == "center" and abs(pl - pr) > 0.001:
            bad.append((name, "off-centre", round(pl, 3), round(pr, 3)))
        if va in ("top", "") and ptop > 0.001:
            bad.append((name, "PaddingTop", round(ptop, 3)))
        elif va == "bottom" and pbot > 0.001:
            bad.append((name, "PaddingBottom", round(pbot, 3)))
        elif va == "middle" and abs(ptop - pbot) > 0.001:
            bad.append((name, "off-middle", round(ptop, 3), round(pbot, 3)))
    return bad


def _unjustified_free_pads(rdl: str):
    """Non-zero padding on the FREE side that the one-line ceiling does not
    explain -- i.e. decoration, not the anti-wrap cap.

    A box the engine auto-sizes (CanGrow) or one declared tall enough for two
    WHOLE lines is out of the ceiling's remit, so it is not measured here."""
    root = ET.fromstring(rdl.encode("utf-8"))
    parent = {c: p for p in root.iter() for c in p}
    bad = []
    for tb in _declared_boxes(rdl):
        st = tb.find(_q("Style"))
        if st is None:
            continue
        if (tb.findtext(_q("CanGrow")) or "").strip().lower() == "true":
            continue
        ptop, pbot = _pt(st, "PaddingTop"), _pt(st, "PaddingBottom")
        if ptop + pbot <= 0.001:
            continue
        h = _in(tb, "Height")
        if h is None:
            row = parent.get(tb)
            while row is not None and row.tag != _q("TablixRow"):
                row = parent.get(row)
            h = _in(row, "Height") if row is not None else None
        if h is None:
            bad.append((tb.get("Name"), "no-governing-height"))
            continue
        size, fam = _size_family(tb)
        need = R._font_line_box_pt(size, R._family_is_sans(fam))
        interior = h * 72.0 - ptop - pbot
        if interior >= 2 * need - 0.05:
            continue                     # two whole lines really print here
        if abs(interior - need) > 0.05:
            bad.append((tb.get("Name"), round(interior, 2), round(need, 2)))
    return bad


# ---------------------------------------------------------------------------
# fixtures: one per emitter family, all structural
# ---------------------------------------------------------------------------

_TABULAR = (
    '<?xml version="1.0"?><report name="NOINSET_T" DTDVersion="9.0.2.0.10">'
    '<data><dataSource name="Q_1">'
    '<select><![CDATA[SELECT ALPHA, BRAVO, CHARLIE FROM T]]></select>'
    '<group name="G_1"><dataItem name="ALPHA" datatype="vchar2"/>'
    '<dataItem name="BRAVO" datatype="vchar2"/>'
    '<dataItem name="CHARLIE" datatype="number"/></group>'
    '</dataSource></data><layout>'
    '<section name="main" width="8.50000" height="11.00000">'
    '<body width="7.50000" height="9.50000"><location x="0.5" y="0.5"/>'
    '<frame name="M_1"><geometryInfo x="0" y="0" width="7.5" height="1.2"/>'
    '<text name="B_H1"><textSettings justify="start"/>'
    '<geometryInfo x="0.10000" y="0.10000" width="1.80000" height="0.19000"/>'
    '<textSegment><font face="Arial" size="10"/>'
    '<string><![CDATA[Quay Alpha]]></string></textSegment></text>'
    '<text name="B_H2"><textSettings justify="start"/>'
    '<geometryInfo x="2.10000" y="0.10000" width="1.80000" height="0.19000"/>'
    '<textSegment><font face="Arial" size="10"/>'
    '<string><![CDATA[Quay Bravo]]></string></textSegment></text>'
    '<text name="B_H3"><textSettings justify="start"/>'
    '<geometryInfo x="4.10000" y="0.10000" width="1.80000" height="0.19000"/>'
    '<textSegment><font face="Arial" size="10"/>'
    '<string><![CDATA[Quay Charlie]]></string></textSegment></text>'
    '<repeatingFrame name="R_1" source="G_1" printDirection="down">'
    '<geometryInfo x="0" y="0.35000" width="7.5" height="0.22000"/>'
    '<field name="F_A" source="ALPHA" alignment="start">'
    '<font face="Arial" size="10"/>'
    '<geometryInfo x="0.10000" y="0.35000" width="1.80000" height="0.19000"/>'
    '</field>'
    '<field name="F_B" source="BRAVO" alignment="start">'
    '<font face="Arial" size="10"/>'
    '<geometryInfo x="2.10000" y="0.35000" width="1.80000" height="0.19000"/>'
    '</field>'
    '<field name="F_C" source="CHARLIE" alignment="start">'
    '<font face="Arial" size="10"/>'
    '<geometryInfo x="4.10000" y="0.35000" width="1.80000" height="0.19000"/>'
    '</field>'
    '</repeatingFrame></frame></body></section></layout></report>'
).encode()


def _form_xml(label: str, width: float, size: int = 11,
              face: str = "Verdana", bold: str = ' bold="yes"') -> bytes:
    """A per-record positional form carrying ONE declared constant label in a
    box of the caller's width -- the cover-title shape."""
    return (
        '<?xml version="1.0"?><report name="NOINSET_F" '
        'DTDVersion="9.0.2.0.10"><data><dataSource name="Q_1">'
        '<select><![CDATA[SELECT ALPHA FROM T]]></select>'
        '<group name="G_1"><dataItem name="ALPHA" datatype="vchar2"/>'
        '</group></dataSource></data><layout>'
        '<section name="main" width="8.50000" height="11.00000">'
        '<body width="7.50000" height="9.50000"><location x="0.5" y="0.5"/>'
        '<frame name="M_1">'
        '<geometryInfo x="0" y="0" width="7.5" height="3.0"/>'
        '<repeatingFrame name="R_1" source="G_1" printDirection="down">'
        '<geometryInfo x="0" y="0" width="7.5" height="2.8"/>'
        '<text name="B_TITLE"><textSettings justify="center" spacing="0"/>'
        f'<geometryInfo x="0.20000" y="0.20000" width="{width:.5f}"'
        ' height="0.30000"/><textSegment>'
        f'<font face="{face}" size="{size}"{bold}/>'
        f'<string><![CDATA[{label}]]></string></textSegment></text>'
        '<field name="F_A" source="ALPHA" alignment="start">'
        '<font face="Arial" size="10"/>'
        '<geometryInfo x="0.20000" y="1.20000" width="3.00000"'
        ' height="0.19000"/></field>'
        '</repeatingFrame></frame></body></section></layout></report>'
    ).encode()


_TITLE = "Weekly Gypsy Quay Handover Digest Report"
_TITLE_PT = 11
# The declared box the title needs, plus a hair: wide enough for the label,
# too narrow once a 2pt+2pt inset is taken off it. Derived from the metric so
# the trap survives any future change to the estimator.
_TITLE_W = R._afm_text_width(_TITLE, _TITLE_PT, True, True) + 0.01
_ARCHETYPES = {
    "tabular": _TABULAR,
    "positional-form": _form_xml(_TITLE, _TITLE_W, size=_TITLE_PT),
    "wide-box": _form_xml(_TITLE, 6.00000, size=_TITLE_PT),
}


# ---------------------------------------------------------------------------
# (a) the shared emitter's own default
# ---------------------------------------------------------------------------

def test_the_shared_textbox_emitter_defaults_to_no_inset():
    """A call site that says nothing about padding must get NONE. The default
    is the trap that silently re-invents the inset on the next new emitter."""
    import inspect
    sig = inspect.signature(R._build_textbox)
    assert sig.parameters["padding"].default == "0pt"


def test_a_bare_emitted_textbox_carries_zero_padding_on_every_side():
    parent = ET.Element(_q("ReportItems"))
    tb = R._build_textbox(parent, "Tb_Probe", '="x"')
    st = tb.find(_q("Style"))
    assert [st.findtext(_q(f"Padding{s}"))
            for s in ("Left", "Right", "Top", "Bottom")] == \
        ["0pt", "0pt", "0pt", "0pt"]


# ---------------------------------------------------------------------------
# (b) no emitter displaces a glyph off its declared anchor
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(_ARCHETYPES))
def test_no_emitter_insets_text_off_its_declared_anchor(name):
    rdl = convert(_ARCHETYPES[name])["rdl_xml"]
    assert _displacing_insets(rdl) == []


@pytest.mark.parametrize("name", sorted(_ARCHETYPES))
def test_the_only_padding_left_anywhere_is_the_one_line_ceiling(name):
    rdl = convert(_ARCHETYPES[name])["rdl_xml"]
    assert _unjustified_free_pads(rdl) == []


def test_declared_caption_and_data_cells_are_flush_with_their_column():
    """The measured defect, at its own emitter: a caption or a data cell in a
    DECLARED column carries no inset, so its glyphs start at the declared x."""
    rdl = convert(_TABULAR)["rdl_xml"]
    root = ET.fromstring(rdl.encode("utf-8"))
    seen = 0
    for tb in root.iter(_q("Textbox")):
        nm = tb.get("Name") or ""
        if not (nm.startswith("Hdr_") or nm.startswith("Cell_")):
            continue
        seen += 1
        st = tb.find(_q("Style"))
        assert _pt(st, "PaddingLeft") == 0.0, nm
        assert _pt(st, "PaddingRight") == 0.0, nm
    assert seen >= 6, "fixture must emit caption and data cells"


# ---------------------------------------------------------------------------
# (c) the declared width IS the usable text width
# ---------------------------------------------------------------------------

def _title_box(rdl):
    root = ET.fromstring(rdl.encode("utf-8"))
    for tb in root.iter(_q("Textbox")):
        if any(_TITLE in (v.text or "") for v in tb.iter(_q("Value"))):
            return tb
    return None


def test_a_declared_box_keeps_its_whole_declared_width_for_text():
    """The cover-title class: the label fits the DECLARED box but not the box
    minus an inset, so any inset at all makes the engine wrap it -- and a wrap
    inside a one-line-tall box costs the whole wrapped word AND pushes every
    line under it down."""
    rdl = convert(_ARCHETYPES["positional-form"])["rdl_xml"]
    tb = _title_box(rdl)
    assert tb is not None, "fixture must emit the declared title box"
    st = tb.find(_q("Style"))
    w = _in(tb, "Width")
    usable = w - (_pt(st, "PaddingLeft") + _pt(st, "PaddingRight")) / 72.0
    assert usable == pytest.approx(w), "the declared width is the text width"
    # ...and the fixture is a real trap: the label fits the declared box but
    # NOT the box less the inset the emitter used to add (2pt + 2pt).
    need = R._afm_text_width(_TITLE, _TITLE_PT, True, True)
    assert need <= w + 1e-6, ("fixture label must fit the declared box",
                              need, w)
    assert need > w - 4 / 72.0, (
        "fixture must be tight enough that a 4pt inset would wrap it",
        need, w)


def test_the_declared_title_stays_one_line():
    """Structural companion to the render: one Paragraph, one TextRun, and a
    box tall enough for exactly one line of it -- so the engine has no second
    line to move a word onto."""
    rdl = convert(_ARCHETYPES["positional-form"])["rdl_xml"]
    tb = _title_box(rdl)
    assert len(list(tb.iter(_q("Paragraph")))) == 1
    st = tb.find(_q("Style"))
    h = _in(tb, "Height")
    interior = h * 72.0 - _pt(st, "PaddingTop") - _pt(st, "PaddingBottom")
    line = R._font_line_box_pt(_TITLE_PT, sans=True)
    assert interior == pytest.approx(line, abs=0.05), (interior, line)


# ---------------------------------------------------------------------------
# (d) the ceiling itself
# ---------------------------------------------------------------------------

def test_a_fixed_single_line_box_never_reserves_a_second_line():
    """A box tall enough to START a second line but too short to PRINT one
    makes the engine move a whole word onto the invisible line. The interior
    of every fixed single-line box is therefore exactly one line box."""
    rdl = convert(_ARCHETYPES["wide-box"])["rdl_xml"]
    root = ET.fromstring(rdl.encode("utf-8"))
    parent = {c: p for p in root.iter() for c in p}
    checked = 0
    for tb in _declared_boxes(rdl):
        if len(list(tb.iter(_q("Paragraph")))) != 1:
            continue
        if (tb.findtext(_q("CanGrow")) or "").lower() == "true":
            continue
        h = _in(tb, "Height")
        if h is None:
            row = parent.get(tb)
            while row is not None and row.tag != _q("TablixRow"):
                row = parent.get(row)
            h = _in(row, "Height") if row is not None else None
        if h is None:
            continue
        st = tb.find(_q("Style"))
        interior = h * 72.0 - _pt(st, "PaddingTop") - _pt(st, "PaddingBottom")
        size, fam = _size_family(tb)
        line = R._font_line_box_pt(size, R._family_is_sans(fam))
        if interior < 2 * line:
            checked += 1
            assert interior <= line + 0.05, (tb.get("Name"), interior, line)
    assert checked >= 2, "fixture must exercise the ceiling"


def test_the_ceiling_never_moves_a_glyph():
    """It is taken on the side the text is NOT anchored to, so the box, its
    fill, its border and its text all stay exactly where they were."""
    rdl = convert(_ARCHETYPES["wide-box"])["rdl_xml"]
    assert _displacing_insets(rdl) == []


def test_a_box_with_room_for_two_whole_lines_is_left_alone():
    """The ceiling only removes space that could never show a COMPLETE second
    line -- where two lines really print, wrapping is the faithful behaviour
    and the declared height must survive untouched."""
    tall = (
        '<?xml version="1.0"?><report name="NOINSET_2L" '
        'DTDVersion="9.0.2.0.10"><data><dataSource name="Q_1">'
        '<select><![CDATA[SELECT ALPHA FROM T]]></select>'
        '<group name="G_1"><dataItem name="ALPHA" datatype="vchar2"/>'
        '</group></dataSource></data><layout>'
        '<section name="main" width="8.50000" height="11.00000">'
        '<body width="7.50000" height="9.50000"><location x="0.5" y="0.5"/>'
        '<frame name="M_1">'
        '<geometryInfo x="0" y="0" width="7.5" height="3.0"/>'
        '<repeatingFrame name="R_1" source="G_1" printDirection="down">'
        '<geometryInfo x="0" y="0" width="7.5" height="2.8"/>'
        '<text name="B_TALL"><textSettings justify="start" spacing="0"/>'
        '<geometryInfo x="0.20000" y="0.20000" width="2.00000"'
        ' height="0.60000"/><textSegment>'
        '<font face="Arial" size="10"/>'
        '<string><![CDATA[Deep Gypsy Quay]]></string></textSegment></text>'
        '<field name="F_A" source="ALPHA" alignment="start">'
        '<font face="Arial" size="10"/>'
        '<geometryInfo x="0.20000" y="1.40000" width="3.00000"'
        ' height="0.19000"/></field>'
        '</repeatingFrame></frame></body></section></layout></report>'
    ).encode()
    rdl = convert(tall)["rdl_xml"]
    root = ET.fromstring(rdl.encode("utf-8"))
    tb = next(t for t in root.iter(_q("Textbox"))
              if any("Deep Gypsy Quay" in (v.text or "")
                     for v in t.iter(_q("Value"))))
    st = tb.find(_q("Style"))
    line = R._font_line_box_pt(10, sans=True)
    interior = (_in(tb, "Height") * 72.0
                - _pt(st, "PaddingTop") - _pt(st, "PaddingBottom"))
    assert interior >= 2 * line - 0.05, (
        "a box declared tall enough for two whole lines keeps them", interior)


# ---------------------------------------------------------------------------
# (e) a DECLARED inset still travels
# ---------------------------------------------------------------------------

def test_a_declared_indent_is_still_emitted_as_padding():
    """0pt is the default, not a ban: where the SOURCE declares an inset (a
    detail frame offset under a full-width band) it must still reach the
    emitted style."""
    from converter.generators.rdl import (_detect_multi_section,
                                          _build_multi_section_body)
    from converter.models import (DataQuery, DataItem, LayoutField,
                                  LayoutGroup, ParsedReport)

    def _mkq(n):
        q = DataQuery(name=f"Q_{n}")
        q.items = [DataItem(name=f"{n}_DESC"), DataItem(name=f"{n}_CNT")]
        q.sql = f"SELECT {n}_DESC, {n}_CNT FROM x"
        return q

    def _sec(n, frame_x, label_x, count_x):
        rf = LayoutGroup(
            name=f"R_G_{n}", kind="repeating_frame", source_query=f"G_{n}",
            x=frame_x, y=0.25, width=4.3,
            fields=[
                LayoutField(name=f"F_{n}_DESC", source=f"{n}_DESC",
                            kind="field", x=label_x, y=0.25, width=3.0),
                LayoutField(name=f"F_{n}_CNT", source=f"{n}_CNT",
                            kind="field", x=count_x, y=0.25, width=0.8),
            ])
        return LayoutGroup(name=f"M_G_{n}", kind="frame", x=0.0, y=0.0,
                           width=4.5, children=[rf])

    rep = ParsedReport(name="NOINSET_D")
    rep.queries = [_mkq("S1"), _mkq("S2")]
    rep.layout = [LayoutGroup(name="main", kind="section_main", children=[
        _sec("S1", 0.0, 0.1875, 3.6875),
        _sec("S2", 0.25, 0.25, 3.75),
    ])]
    xml = ET.tostring(
        _build_multi_section_body(rep, _detect_multi_section(rep)),
        encoding="unicode")
    assert "<PaddingLeft>0.19in</PaddingLeft>" in xml
    assert "<PaddingLeft>0.25in</PaddingLeft>" in xml
