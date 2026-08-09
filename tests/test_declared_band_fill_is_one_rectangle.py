"""A DECLARED band fill is ONE rectangle at the frame's extents.

Oracle declares a fill on the FRAME, and the frame paints a single
rectangle across its own declared extents behind everything it holds.
Emitting the fill once per child cell instead produces two measurable
defects, both caught against the truth PDF of a landscape summary report
by comparing the engine-rendered fill inventory (count / x0 / x1 /
gutters / colour) with the Oracle-rendered one:

  * TILING -- a declared 2.70483in tint frame printed as FOUR per-field
    swatches separated by three 0.72pt WHITE gutters, and a declared
    header band printed as TEN per-cell fills; the truth prints one
    continuous band in each case (navy 38.92 -> 916.44pt, tint
    720.92 -> 915.68pt, no gutters).
  * SPAN SHORTFALL -- the tiles stop at the LAST CHILD's right edge, so
    both bands ended 18.5pt (0.257in) short of the declared frame edge,
    and the tint's left edge started 8.77pt over (at the packed column
    boundary rather than the declared x).

Everything here is synthetic and structural -- no report, column, label
or colour from any client source appears in this file.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "tools" / "renderlab"))

from converter import convert  # noqa: E402

# --- the declaration under test (inches) ---------------------------------
BODY_X = 0.50000          # <body><location x> == the sheet margin
COLS = (("COL_A", 0.00000, 2.00000),
        ("COL_B", 2.50000, 2.00000),
        ("COL_C", 5.00000, 1.50000),
        ("COL_D", 7.00000, 1.50000))   # last child ends at 8.50000
BAND_X, BAND_W = 5.00000, 3.75000      # inner tint frame  -> ends 8.75000
HDR_X, HDR_W = 0.00000, 9.00000        # caption frame     -> ends 9.00000
ROW_H, ROW_GAP, HDR_H = 0.18000, 0.05000, 0.19000
BAND_RGB = (0.878, 0.878, 0.749)       # r88g88b75
HDR_RGB = (0.0, 0.0, 0.502)            # darkblue


def _src(fill: bool = True) -> bytes:
    """Four columns; the last TWO sit in a solid-fill inner frame that is
    declared WIDER than the fields it holds, under a solid-fill caption
    frame that is declared wider still."""
    _vs = ('<visualSettings fillPattern="solid" '
           'fillForegroundColor="r88g88b75"/>') if fill else ""
    _hvs = ('<visualSettings fillPattern="solid" '
            'fillForegroundColor="darkblue"/>') if fill else ""

    def _field(c, x, w):
        return (f'<field name="F_{c}" source="{c}" alignment="start">'
                f'<font face="Arial" size="8"/>'
                f'<geometryInfo x="{x:.5f}" y="0.30000" width="{w:.5f}"'
                f' height="{ROW_H:.5f}"/></field>')

    def _cap(c, x, w):
        return (f'<text name="B_{c}"><textSettings spacing="0"/>'
                f'<geometryInfo x="{x:.5f}" y="0.02000" width="{w:.5f}"'
                f' height="{HDR_H:.5f}"/><textSegment>'
                f'<font face="Arial" size="9" textColor="white"/>'
                f'<string><![CDATA[Cap{c[-1]}]]></string>'
                f'</textSegment></text>')

    outer = "".join(_field(c, x, w) for c, x, w in COLS[:2])
    inner = "".join(_field(c, x, w) for c, x, w in COLS[2:])
    caps = "".join(_cap(c, x, w) for c, x, w in COLS)
    items = "".join(f'<dataItem name="{c}" datatype="vchar2"'
                    f' columnOrder="{i + 1}"/>'
                    for i, (c, _x, _w) in enumerate(COLS))
    return (
        '<?xml version="1.0"?>'
        '<report name="BANDFILL" DTDVersion="9.0.2.0.10"><data>'
        '<dataSource name="Q_1">'
        '<select><![CDATA[SELECT COL_A, COL_B, COL_C, COL_D FROM T]]></select>'
        f'<group name="G_ROW">{items}</group>'
        '</dataSource></data><layout>'
        '<section name="main" width="14.00000" height="8.50000"'
        ' orientation="landscape">'
        '<body width="12.50000" height="7.00000">'
        f'<location x="{BODY_X:.5f}" y="0.50000"/>'
        '<frame name="M_GRP">'
        '<geometryInfo x="0.00000" y="0.02000" width="9.00000"'
        ' height="0.55000"/>'
        '<repeatingFrame name="R_ROW" source="G_ROW" printDirection="down"'
        f' minWidowRecords="1" columnMode="no"'
        f' vertSpaceBetweenFrames="{ROW_GAP:.4f}">'
        f'<geometryInfo x="0.00000" y="0.30000" width="9.00000"'
        f' height="{ROW_H:.5f}"/>'
        f'{outer}'
        '<repeatingFrame name="R_INNER" source="G_ROW">'
        f'<geometryInfo x="{BAND_X:.5f}" y="0.30000" width="{BAND_W:.5f}"'
        f' height="{ROW_H:.5f}"/>{_vs}{inner}</repeatingFrame>'
        '</repeatingFrame>'
        '<frame name="M_HDR">'
        f'<geometryInfo x="{HDR_X:.5f}" y="0.02000" width="{HDR_W:.5f}"'
        f' height="{HDR_H:.5f}"/>'
        '<advancedLayout printObjectOnPage="allPage"'
        ' basePrintingOn="anchoringObject"/>'
        f'{_hvs}{caps}</frame></frame></body>'
        '</section></layout></report>'
    ).encode()


def _el(rdl: str, tag: str, name: str):
    """The named report item as an ELEMENT, so its own geometry is read from
    its DIRECT children.

    A band rectangle CONTAINS the cells it paints (that nesting is what lets
    the fill grow with a row whose data wraps), so a blob regex would happily
    return the first nested child's <Left>/<Width> instead of the band's.
    """
    import xml.etree.ElementTree as ET
    root = ET.fromstring(rdl)
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] == tag and el.get("Name") == name:
            return el
    raise AssertionError(f"missing {tag} {name}")


def _own(el, prop: str) -> float:
    """One geometry property off the element ITSELF (never a descendant)."""
    for ch in el:
        if ch.tag.rsplit("}", 1)[-1] == prop:
            return float((ch.text or "").replace("in", ""))
    raise AssertionError(f"{el.get('Name')} declares no {prop}")


def _rect(rdl: str, name: str) -> str:
    m = re.search(rf'<Rectangle Name="{name}">.*?</Rectangle>', rdl, re.S)
    assert m, f"missing rectangle {name}"
    return m.group(0)


def _geo(rdl: str, name: str, tag: str = "Rectangle") -> tuple:
    el = _el(rdl, tag, name)
    return (_own(el, "Left"), _own(el, "Width"))


def _cols(rdl: str) -> list:
    seg = rdl.split("<TablixColumns>", 1)[1].split("</TablixColumns>", 1)[0]
    return [float(w) for w in re.findall(r"<Width>([\d.]+)in</Width>", seg)]


# --------------------------------------------------------------------------
# structure: one rectangle, declared extents, no per-cell tiles
# --------------------------------------------------------------------------

def test_row_band_is_one_rectangle_at_the_frames_declared_extents():
    rdl = convert(_src())["rdl_xml"]
    assert rdl.count('<Rectangle Name="Band_0">') == 1, (
        "a declared frame fill is ONE rectangle, never one tile per cell")
    left, width = _geo(rdl, "Band_0")
    org = sum(_cols(rdl)[:2])          # the two UNCOVERED columns
    assert abs((org + left) - BAND_X) < 0.002, (
        "the band starts at the frame's DECLARED x, not at the packed "
        "column boundary", org + left, BAND_X)
    assert abs(width - BAND_W) < 0.002, (
        "the band is the frame's DECLARED width", width, BAND_W)
    assert abs((org + left + width) - (BAND_X + BAND_W)) < 0.003, (
        "the band must reach the frame's declared right edge, not the last "
        "child's", org + left + width, BAND_X + BAND_W)
    # the covered children are INSIDE the band rectangle (that containment
    # is what makes the fill follow a grown row: an RDL rectangle's declared
    # height is a minimum and the engine grows it around its contents, which
    # a mere sibling fill can never do) and keep their own declared boxes
    # there -- so their offsets read from the BAND's left edge, not the
    # spanned cell's.
    band_el = _el(rdl, "Rectangle", "Band_0")
    _inside = {c.get("Name") for c in band_el.iter()
               if c.tag.rsplit("}", 1)[-1] == "Textbox"}
    for col, x, w in COLS[2:]:
        assert f"Cell_{col}" in _inside, (
            f"Cell_{col} must live INSIDE the band rectangle so the fill "
            "grows with it", sorted(_inside))
        c_left, c_width = _geo(rdl, f"Cell_{col}", "Textbox")
        assert abs((org + left + c_left) - x) < 0.002, (
            col, org + left + c_left, x)
        assert abs(c_width - w) < 0.002, (col, c_width, w)
    # ...and NO cell paints a tile of the frame's fill (that is what put
    # the 0.72pt white gutters between the swatches).
    for col, _x, _w in COLS:
        tb = re.search(rf'<Textbox Name="Cell_{col}">.*?</Textbox>',
                       rdl, re.S).group(0)
        assert "#E0E0BF" not in tb, f"Cell_{col} still tiles the band fill"


def test_header_band_is_one_rectangle_at_the_frames_declared_extents():
    rdl = convert(_src())["rdl_xml"]
    assert rdl.count('<Rectangle Name="HdrBand_0">') == 1, (
        "a declared caption-frame fill is ONE rectangle, not per-caption")
    left, width = _geo(rdl, "HdrBand_0")
    assert abs(left - HDR_X) < 0.002, (left, HDR_X)
    assert abs(width - HDR_W) < 0.002, (
        "the header band spans the caption frame's declared width -- the "
        "per-cell tiling stopped at the last caption", width, HDR_W)
    for col, _x, _w in COLS:
        tb = re.search(rf'<Textbox Name="Hdr_{col}">.*?</Textbox>',
                       rdl, re.S).group(0)
        assert "#000080" not in tb, f"Hdr_{col} still tiles the band fill"


def test_declared_caption_hairline_survives_the_band_move():
    """A caption's own declared white rule paints because it sits ON the
    band; moving the fill from the cell to the frame must not turn it into
    an invisible white-on-white border."""
    src = _src().replace(
        b'<textSegment><font face="Arial" size="9" textColor="white"/>',
        b'<visualSettings linePattern="solid" lineForegroundColor="white"/>'
        b'<textSegment><font face="Arial" size="9" textColor="white"/>')
    rdl = convert(src)["rdl_xml"]
    tb = re.search(r'<Textbox Name="Hdr_COL_A">.*?</Textbox>',
                   rdl, re.S).group(0)
    assert re.search(r"<Border>\s*<Style>Solid</Style>\s*<Color>#FFFFFF",
                     tb), tb


def test_no_declared_fill_invents_no_band():
    rdl = convert(_src(fill=False))["rdl_xml"]
    assert '<Rectangle Name="Band_0">' not in rdl
    assert '<Rectangle Name="HdrBand_0">' not in rdl
    assert "<ColSpan>" not in rdl


def test_a_child_that_declares_its_own_fill_still_paints_it():
    """Only the CHILD's own declared fill survives as a per-cell paint."""
    src = _src().replace(
        b'<field name="F_COL_C" source="COL_C" alignment="start">',
        b'<field name="F_COL_C" source="COL_C" alignment="start">'
        b'<visualSettings fillPattern="solid" fillForegroundColor="gray16"/>')
    rdl = convert(src)["rdl_xml"]
    tb = re.search(r'<Textbox Name="Cell_COL_C">.*?</Textbox>',
                   rdl, re.S).group(0)
    assert "<BackgroundColor>#D6D6D6</BackgroundColor>" in tb, tb
    tb_d = re.search(r'<Textbox Name="Cell_COL_D">.*?</Textbox>',
                     rdl, re.S).group(0)
    assert "BackgroundColor" not in tb_d, (
        "a sibling that declares NO fill of its own must stay unpainted")


# --------------------------------------------------------------------------
# the real proof: measure the ENGINE-rendered fill inventory
# --------------------------------------------------------------------------

def _fill_inventory(pdf_path, rgb, tol=0.01):
    """Every filled rect of one colour: (x0, y0, x1, y1), page 1."""
    import fitz
    doc = fitz.open(str(pdf_path))
    out = []
    for d in doc[0].get_drawings():
        f = d.get("fill")
        if not f or len(f) < 3:
            continue
        if max(abs(a - b) for a, b in zip(f[:3], rgb)) > tol:
            continue
        r = d["rect"]
        if r.width < 2 or r.height < 1:
            continue
        out.append((round(r.x0, 2), round(r.y0, 2),
                    round(r.x1, 2), round(r.y1, 2)))
    doc.close()
    return sorted(out, key=lambda t: (t[1], t[0]))


def test_engine_paints_one_band_per_row_at_the_declared_extents(tmp_path):
    try:
        import fitz  # noqa: F401
        from render import render_rdl, lib_ready
    except Exception:  # noqa: BLE001
        pytest.skip("renderlab not available")
    if not lib_ready() or sys.platform != "win32":
        pytest.skip("ReportViewer DLLs not fetched")

    rdl = convert(_src())["rdl_xml"]
    p = tmp_path / "band.rdl"
    p.write_text(rdl, encoding="utf-8")
    res = render_rdl(p, tmp_path / "band.pdf", rows=3)
    assert res["ok"], res["log"][-1200:]

    hdr = _fill_inventory(res["pdf"], HDR_RGB)
    assert len(hdr) == 1, (
        f"the declared header band must paint ONE rectangle, got {len(hdr)}"
        f" tiles: {hdr}")
    x0, _y0, x1, _y1 = hdr[0]
    assert abs(x0 - (BODY_X + HDR_X) * 72.0) < 1.2, (x0, hdr)
    assert abs(x1 - (BODY_X + HDR_X + HDR_W) * 72.0) < 1.2, (x1, hdr)

    rows = _fill_inventory(res["pdf"], BAND_RGB)
    assert rows, "the declared row band painted nothing"
    for x0, _y0, x1, _y1 in rows:
        assert abs(x0 - (BODY_X + BAND_X) * 72.0) < 1.2, (x0, rows)
        assert abs(x1 - (BODY_X + BAND_X + BAND_W) * 72.0) < 1.2, (x1, rows)
    # one band per record: no two swatches share a row (that is what the
    # per-cell tiling produced, split by white gutters).
    for a, b in zip(rows, rows[1:]):
        assert b[1] >= a[3] - 0.5, f"two tiles on one row: {a} {b}"

    # The band paints BEHIND its children. Document order is not paint
    # order for overlapping items in a Rectangle: without an explicit
    # ZIndex the engine painted the opaque band on top and buried every
    # caption and value it covers.
    from render_overlap import pdf_overlaps
    buried = pdf_overlaps(res["pdf"])
    assert not buried, f"the band paints over its own content: {buried[:6]}"
