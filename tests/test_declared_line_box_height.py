"""A single line of text always gets its font's whole line box.

Oracle's renderer lets a glyph OVERFLOW the box it was declared in; SSRS CLIPS
at the box interior. So an Oracle caption declared 0.18in tall at 10pt -- 12.96
points of box -- came out with its descenders sliced off, while the sibling
declared 0.22in printed the whole glyph. Measured at 600 dpi against the truth
pages: the truth's caption ink spans the full ~9.4pt at 10pt, ours spanned
~7.2pt.

DIALECT UPDATE (measured): an Oracle box carries NO inset, so the emitter now
writes 0pt padding on every declared box and the box's whole declared height is
its interior. Two consequences this file pins:

  * the interior is the DECLARED height, so a declared box may not be inflated
    to make room for an invented inset (a declared 0.2in box used to render
    0.22in, and a 0.1666in heading used to render 0.25in);
  * the clip class is only expressible at 12pt and up -- the emitter's own
    0.18in/0.19in minimum box already clears the line box of every size through
    11pt (11pt needs 12.29pt = 0.1707in), so no 8..11pt declaration can be
    short once the padding is gone.

The requirement is a real font metric, not a rule of thumb: ``_font_line_box_pt``
returns the face's own ascent+descent, and the brackets pinned below come from
rendering a font-size x box-height ladder through the actual report engine and
measuring the ink at 600 dpi -- at 10pt the engine starts slicing somewhere in
(10.80pt, 11.52pt] of interior, and the metric has to land inside that window.
A 1.18em rule of thumb overshoots every one of those windows, which is exactly
the kind of drift these brackets exist to catch.

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


# --------------------------------------------------------------------------
# (a) the metric itself
# --------------------------------------------------------------------------
#
# size -> (largest interior the engine still CLIPPED, smallest it printed
# whole), measured at 600 dpi through the real engine.  The metric must fall
# inside the half-open window (clipped, whole].
_ENGINE_BRACKETS = {
    8: (8.64, 9.36),
    9: (9.36, 10.08),
    10: (10.80, 11.52),
    11: (12.24, 12.96),
    12: (12.96, 13.68),
    14: (15.12, 15.84),
}


@pytest.mark.parametrize("size,bracket", sorted(_ENGINE_BRACKETS.items()))
def test_line_box_metric_matches_the_engine_measured_clip_threshold(size,
                                                                    bracket):
    lo, hi = bracket
    need = R._font_line_box_pt(size, sans=True)
    assert lo < need <= hi, (size, need, bracket)


def test_line_box_scales_with_the_declared_size():
    """It is a metric times the point size -- nothing size-specific."""
    assert R._font_line_box_pt(20) == pytest.approx(
        2 * R._font_line_box_pt(10))
    assert R._font_line_box_pt(0) == 0


def test_serif_and_sans_are_measured_from_their_own_faces():
    """The two width tables already model two faces; the vertical metric reads
    the same two, so a serif box is not sized with the sans face's numbers."""
    assert R._font_line_box_pt(10, sans=True) != \
        R._font_line_box_pt(10, sans=False)
    # an UNDECLARED family is the RDL default face
    assert R._family_is_sans("") is R._RDL_DEFAULT_FONT_IS_SANS
    assert R._family_is_sans("Arial") is True
    assert R._family_is_sans("Times New Roman") is False
    assert R._family_is_sans("Times New Roman, serif") is False


# --------------------------------------------------------------------------
# fixtures: layouts that declare boxes SHORTER than their own font's line box
# --------------------------------------------------------------------------

def _len(el, tag):
    c = el.find(_q(tag))
    if c is None or not (c.text or "").strip():
        return None
    try:
        return float(c.text.replace("in", "").strip())
    except ValueError:
        return None


def _pad_pt(style, tag):
    if style is None:
        return 0.0
    t = (style.findtext(_q(tag)) or "").strip().lower()
    for unit, per_pt in (("pt", 1.0), ("in", 72.0)):
        if t.endswith(unit):
            try:
                return float(t[:-len(unit)]) * per_pt
            except ValueError:
                return 0.0
    return 0.0


def _size_and_family(tb):
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


def _single_line(tb) -> bool:
    return len(list(tb.iter(_q("Paragraph")))) == 1 and \
        (tb.findtext(_q("CanGrow")) or "").strip().lower() != "true"


def _shortfalls(rdl: str):
    """Every single-line fixed textbox whose INTERIOR is under its line box.

    Reads the governing height the way the engine does: a positioned box uses
    its own <Height>, a data-region cell uses its row's."""
    root = ET.fromstring(rdl.encode("utf-8"))
    parent = {c: p for p in root.iter() for c in p}
    bad = []
    for tb in root.iter(_q("Textbox")):
        if not _single_line(tb):
            continue
        h = _len(tb, "Height")
        if h is None:
            row = parent.get(tb)
            while row is not None and row.tag != _q("TablixRow"):
                row = parent.get(row)
            h = _len(row, "Height") if row is not None else None
        if h is None:
            continue
        size, fam = _size_and_family(tb)
        style = tb.find(_q("Style"))
        interior = (h * 72.0 - _pad_pt(style, "PaddingTop")
                    - _pad_pt(style, "PaddingBottom"))
        need = R._font_line_box_pt(size, R._family_is_sans(fam))
        if interior + 0.02 < need:
            bad.append((tb.get("Name"), round(h, 4), size,
                        round(interior, 2), round(need, 2)))
    return bad


def _overlaps(rdl: str):
    """Sibling boxes in one coordinate space whose vertical spans collide.

    Only pairs that also share horizontal range count -- side-by-side boxes on
    one row legitimately share a band."""
    root = ET.fromstring(rdl.encode("utf-8"))
    hits = []
    for cont in root.iter():
        ri = cont.find(_q("ReportItems"))
        if ri is None:
            continue
        boxes = []
        for it in list(ri):
            L, T = _len(it, "Left"), _len(it, "Top")
            W, H = _len(it, "Width"), _len(it, "Height")
            if None in (L, T, W, H):
                continue
            boxes.append((it.get("Name") or it.tag, L, T, W, H))
        for i, (n1, l1, t1, w1, h1) in enumerate(boxes):
            for n2, l2, t2, w2, h2 in boxes[i + 1:]:
                if l1 < l2 + w2 - 0.01 and l2 < l1 + w1 - 0.01 \
                        and t1 < t2 + h2 - 0.005 and t2 < t1 + h1 - 0.005:
                    hits.append((n1, n2))
    return hits


_SHORT_H = 0.18       # declared box height, tighter than the sibling's 0.22
_TIGHT = 0.19         # declared pitch: growth would hit the box below
_ROOMY = 0.60         # declared pitch: there IS free space below

# Sizes whose line box exceeds the emitter's own minimum box (0.19in = 13.68pt),
# i.e. the only sizes at which a declared box can still be genuinely too short
# now that no padding is invented.  11pt needs 12.29pt and 12pt needs 13.41pt,
# so the boundary sits between them.
_CLIPPABLE_SIZES = [s for s in (8, 9, 10, 11, 12, 14)
                    if R._font_line_box_pt(s, True) > 0.19 * 72.0]


def _recoverable_h(size: int, sans: bool = True) -> float:
    """A box height the emitter will lay out verbatim, at exactly the font's
    own line box: the tightest declaration that is NOT short.

    Floored at the emitter's own minimum box height, so the fixture declares
    what the emitter will actually lay out -- otherwise the floor silently
    re-inflates the boxes past the pitch and the fixture stops being a trap."""
    return max(round(R._font_line_box_pt(size, sans) / 72.0 + 0.005, 4), 0.19)


def _too_short_h(size: int, sans: bool = True) -> float:
    """A declaration that is GENUINELY short: strictly under the font's own
    line box, with no invented padding to blame.  Only meaningful for
    ``_CLIPPABLE_SIZES`` -- below those the emitter's minimum box is already
    taller than the line box, so the shape cannot be declared at all."""
    return round(R._font_line_box_pt(size, sans) / 72.0 - 0.006, 4)


def _bad_pads(rdl: str):
    """Every emitted padding that MOVES a glyph, plus every non-zero padding
    that the one-line ceiling does not account for.

    An Oracle box has no inset, so nothing the emitter writes may displace the
    text: the padding on the side the text is ANCHORED to must be 0 (both
    halves must match for centred text).  The one legal non-zero padding is the
    ceiling on the FREE side, which exists only to stop the engine reserving a
    second line inside a fixed single-line box -- and it is legal only when it
    leaves exactly one line box of interior."""
    root = ET.fromstring(rdl.encode("utf-8"))
    parent = {c: p for p in root.iter() for c in p}
    bad = []
    for tb in root.iter(_q("Textbox")):
        st = tb.find(_q("Style"))
        if st is None:
            continue
        p = {s: _pad_pt(st, f"Padding{s}")
             for s in ("Left", "Right", "Top", "Bottom")}
        name = tb.get("Name")
        ta = (next((t.text for t in tb.iter(_q("TextAlign"))
                    if (t.text or "").strip()), "") or "left").lower()
        va = (st.findtext(_q("VerticalAlign")) or "top").lower()
        # --- horizontal: nothing may push the anchored edge inward
        if ta in ("left", "general", "default") and p["Left"] > 0.001:
            bad.append((name, "PaddingLeft", p["Left"]))
        if ta == "right" and p["Right"] > 0.001:
            bad.append((name, "PaddingRight", p["Right"]))
        if ta == "center" and abs(p["Left"] - p["Right"]) > 0.001:
            bad.append((name, "asymmetric-centre", p["Left"], p["Right"]))
        # --- vertical: same rule, plus the ceiling's own justification
        if va in ("top", "") and p["Top"] > 0.001:
            bad.append((name, "PaddingTop", p["Top"]))
        if va == "bottom" and p["Bottom"] > 0.001:
            bad.append((name, "PaddingBottom", p["Bottom"]))
        if va == "middle" and abs(p["Top"] - p["Bottom"]) > 0.001:
            bad.append((name, "asymmetric-middle", p["Top"], p["Bottom"]))
        free = p["Bottom"] if va in ("top", "") else (
            p["Top"] if va == "bottom" else p["Top"] + p["Bottom"])
        if free <= 0.001:
            continue
        h = _len(tb, "Height")
        if h is None:
            row = parent.get(tb)
            while row is not None and row.tag != _q("TablixRow"):
                row = parent.get(row)
            h = _len(row, "Height") if row is not None else None
        if h is None:
            bad.append((name, "unjustified-pad", free))
            continue
        size, fam = _size_and_family(tb)
        need = R._font_line_box_pt(size, R._family_is_sans(fam))
        interior = h * 72.0 - p["Top"] - p["Bottom"]
        if abs(interior - need) > 0.05:
            bad.append((name, "pad-not-the-one-line-ceiling",
                        round(interior, 2), round(need, 2)))
    return bad


def _record_form_xml(box_h: float = _SHORT_H, pitch: float = _TIGHT,
                     size: int = 10, face: str = "Arial") -> bytes:
    """A per-record positional form: caption/value pairs stacked at a declared
    pitch, each in a box declared shorter than one line of its own font."""
    rows = "".join(
        f'<text name="B_{i}"><textSettings spacing="0"/>'
        f'<geometryInfo x="0.30000" y="{0.30 + i * pitch:.5f}"'
        f' width="2.20000" height="{box_h:.5f}"/><textSegment>'
        f'<font face="{face}" size="{size}"/>'
        f'<string><![CDATA[Deep Gypsy Quay {i}:]]></string>'
        f'</textSegment></text>'
        f'<field name="F_C{i}" source="COL{i}" alignment="start">'
        f'<font face="{face}" size="{size}"/>'
        f'<geometryInfo x="2.70000" y="{0.30 + i * pitch:.5f}"'
        f' width="3.40000" height="{box_h:.5f}"/></field>'
        for i in range(4))
    items = "".join(f'<dataItem name="COL{i}" datatype="vchar2"/>'
                    for i in range(4))
    return (
        '<?xml version="1.0"?>'
        '<report name="LINEBOX" DTDVersion="9.0.2.0.10"><data>'
        '<dataSource name="Q_1"><select><![CDATA[SELECT COL0, COL1, COL2,'
        ' COL3 FROM T]]></select>'
        f'<group name="G_REC">{items}</group></dataSource></data><layout>'
        '<section name="main" width="8.50000" height="11.00000">'
        '<body width="7.50000" height="9.50000">'
        '<location x="0.50000" y="0.50000"/>'
        '<frame name="M_REC">'
        '<geometryInfo x="0.00000" y="0.00000" width="7.50000"'
        ' height="3.00000"/>'
        '<repeatingFrame name="R_REC" source="G_REC" printDirection="down">'
        '<geometryInfo x="0.00000" y="0.00000" width="7.50000"'
        ' height="2.80000"/>'
        f'{rows}</repeatingFrame></frame></body>'
        '</section></layout></report>'
    ).encode()


# --------------------------------------------------------------------------
# (b) the invariant, end to end
# --------------------------------------------------------------------------

def test_declared_short_boxes_still_get_a_whole_line_box():
    """Tight pitch: there is nowhere to grow, so the interior has to be the
    DECLARED height itself -- which it is, because the box carries no inset."""
    rdl = convert(_record_form_xml())["rdl_xml"]
    assert _shortfalls(rdl) == []


def test_no_declared_box_is_emitted_with_an_invented_inset():
    """An Oracle box has no inset: the declared width is the usable text width
    and the declared height is the interior.  So no padding the emitter writes
    may displace a glyph -- the anchored side is 0pt, a centred axis is
    symmetric -- and the only non-zero padding allowed at all is the one-line
    ceiling on the free side, which must leave exactly one line box.

    (Strictly stronger than the old rule, which only demanded the two VERTICAL
    paddings be shed once a line was found to be clipping: a horizontal inset
    shifts every left-anchored glyph right of its declared x -- truth-measured
    at exactly the inset's own width.)"""
    for kwargs in ({}, {"pitch": _ROOMY}, {"box_h": 0.40, "pitch": _ROOMY},
                   {"size": 12}, {"face": "Times New Roman"}):
        rdl = convert(_record_form_xml(**kwargs))["rdl_xml"]
        assert list(ET.fromstring(rdl.encode("utf-8")).iter(_q("Textbox"))), \
            f"fixture must emit textboxes at all: {kwargs}"
        assert _bad_pads(rdl) == [], (kwargs, _bad_pads(rdl)[:4])


def test_short_boxes_get_a_whole_line_box_with_room_to_deepen():
    rdl = convert(_record_form_xml(pitch=_ROOMY))["rdl_xml"]
    assert _shortfalls(rdl) == []


@pytest.mark.parametrize("size", _CLIPPABLE_SIZES)
def test_genuinely_short_declarations_are_deepened_into_free_space(size):
    """The clip class that survives the no-inset dialect: a box DECLARED
    shorter than its own font's line box, with room below.  The rescue must
    close it without moving or overlapping anything."""
    h = _too_short_h(size)
    rdl = convert(_record_form_xml(box_h=h, pitch=_ROOMY, size=size))["rdl_xml"]
    assert _shortfalls(rdl) == []
    assert _overlaps(rdl) == []


@pytest.mark.parametrize("size", [8, 9, 10, 11, 12, 14])
def test_invariant_holds_across_declared_font_sizes(size):
    """Every declared size, each in a box shaped like the reported defect: tall
    enough for the bare glyph, too short once our padding is on top."""
    h = _recoverable_h(size)
    rdl = convert(_record_form_xml(box_h=h, pitch=h + 0.01,
                                   size=size))["rdl_xml"]
    assert _shortfalls(rdl) == []


@pytest.mark.parametrize("size", [10, 12])
def test_invariant_holds_for_a_serif_face(size):
    h = _recoverable_h(size, sans=False)
    rdl = convert(_record_form_xml(box_h=h, pitch=h + 0.01, size=size,
                                   face="Times New Roman"))["rdl_xml"]
    assert _shortfalls(rdl) == []


def test_data_region_rows_get_a_whole_line_box_too():
    """A cell textbox usually declares no geometry of its own -- the ROW height
    is what the engine clips against, so the rule has to reach it there too.

    Driven straight at the pass over a hand-built region: the tabular builders
    currently mark their cells CanGrow, which the engine auto-sizes, so no
    source fixture can express a fixed too-short cell today."""
    row_h, size = 0.15, 10           # 10.8pt of row for an 11.17pt line
    doc = ET.fromstring(f"""<Report xmlns="{NS[1:-1]}"><Body><ReportItems>
      <Tablix Name="TX"><TablixBody><TablixRows><TablixRow>
        <Height>{row_h}in</Height><TablixCells><TablixCell><CellContents>
          <Textbox Name="Cell"><Paragraphs><Paragraph><TextRuns><TextRun>
            <Value>Deep Gypsy Quay</Value>
            <Style><FontSize>{size}pt</FontSize></Style>
          </TextRun></TextRuns></Paragraph></Paragraphs>
          <Style><PaddingTop>2pt</PaddingTop>
                 <PaddingBottom>2pt</PaddingBottom></Style>
          <CanGrow>false</CanGrow></Textbox>
        </CellContents></TablixCell></TablixCells>
      </TablixRow></TablixRows></TablixBody><Height>{row_h}in</Height>
      </Tablix></ReportItems><Height>1in</Height></Body></Report>""")
    assert _shortfalls(ET.tostring(doc, encoding="unicode")), \
        "the hand-built region must start out clipping"
    R._unclip_short_line_boxes(doc)
    assert _shortfalls(ET.tostring(doc, encoding="unicode")) == []


# --------------------------------------------------------------------------
# (c) the growth must not buy the line box with an overlap
# --------------------------------------------------------------------------

def test_tight_pitch_is_not_bought_with_an_overlap():
    """Boxes declared one 0.19in pitch apart must still not collide after the
    rescue: the declared geometry is preserved and the padding pays instead."""
    rdl = convert(_record_form_xml(pitch=_TIGHT))["rdl_xml"]
    assert _overlaps(rdl) == []
    root = ET.fromstring(rdl.encode("utf-8"))
    kept = [_len(tb, "Height") for tb in root.iter(_q("Textbox"))
            if _len(tb, "Height") is not None
            and abs(_len(tb, "Height") - _SHORT_H) < 0.008]
    assert kept, "the declared short boxes should still be near their declared height"
    assert max(kept) < _TIGHT, kept


@pytest.mark.parametrize("size", [10, 12, 14])
def test_flush_stacked_boxes_are_never_deepened_into_each_other(size):
    """The hard case for the deepen lever: boxes declared edge to edge, so ANY
    growth lands on the box below. The rescue must come from the padding and
    the declared geometry must survive untouched."""
    h = _recoverable_h(size)
    rdl = convert(_record_form_xml(box_h=h, pitch=h, size=size))["rdl_xml"]
    assert _overlaps(rdl) == []
    assert _shortfalls(rdl) == []


def test_roomy_pitch_may_deepen_but_never_past_the_neighbour():
    rdl = convert(_record_form_xml(pitch=_ROOMY))["rdl_xml"]
    assert _overlaps(rdl) == []


def test_boxes_that_already_fit_are_left_alone():
    """Grow-only: a box declared with room to spare keeps its exact geometry."""
    tall = 0.40
    rdl = convert(_record_form_xml(box_h=tall, pitch=_ROOMY))["rdl_xml"]
    root = ET.fromstring(rdl.encode("utf-8"))
    hs = [h for h in (_len(tb, "Height") for tb in root.iter(_q("Textbox")))
          if h is not None and abs(h - tall) < 0.05]
    assert hs, "fixture should emit the declared boxes"
    assert all(abs(h - tall) < 1e-6 for h in hs), hs


# --------------------------------------------------------------------------
# (d) the fixture really is a trap -- without the rule it fails
# --------------------------------------------------------------------------

def test_fixture_declares_boxes_that_are_genuinely_too_short(monkeypatch):
    """Mutation guard: with the rule switched off the very same fixtures must
    report shortfalls, so a green run above can only mean the rule ran.

    The fixtures are now short ON THEIR OWN DECLARATION -- no invented padding
    is subtracted from the interior any more, so a fixture that used to trap by
    losing 4pt to our own inset (0.18in at 10pt: 12.96pt of box, 8.96pt of
    interior) no longer traps at all.  That is the fix, not a hole: 12.96pt
    clears the 11.17pt line box 10pt needs.  Sizes 8..11 are left out for the
    same reason -- the emitter's own 0.19in minimum box (13.68pt) is taller
    than every line box through 11pt (12.29pt), so the shape is undeclarable
    there.  ``_CLIPPABLE_SIZES`` derives that boundary from the metric itself
    rather than hard-coding it."""
    monkeypatch.setattr(R, "_unclip_short_line_boxes", lambda root: None)
    assert _CLIPPABLE_SIZES, "the metric must still leave a clippable size"
    for size in _CLIPPABLE_SIZES:
        h = _too_short_h(size)
        assert _shortfalls(convert(
            _record_form_xml(box_h=h, pitch=_ROOMY, size=size))["rdl_xml"]), size
        assert _shortfalls(convert(
            _record_form_xml(box_h=h, pitch=h + 0.01,
                             size=size))["rdl_xml"]), size
    # ... and a SERIF declaration traps on its own face's metric too
    for size in _CLIPPABLE_SIZES:
        h = _too_short_h(size, sans=False)
        assert _shortfalls(convert(
            _record_form_xml(box_h=h, pitch=_ROOMY, size=size,
                             face="Times New Roman"))["rdl_xml"]), size
