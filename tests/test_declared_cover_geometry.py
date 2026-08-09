"""The cover page is built from the DECLARATION, not synthesized.

Oracle authors its criteria/parameter cover as real layout objects inside
``<section name="header">``: each one carries its own ``<geometryInfo x y
width height>`` and its own ``<font>``.  The converter used to re-invent that
page from a template -- a fixed label column, a fixed value column, a fixed
row pitch -- which threw every declared coordinate away.  Measured against
the Oracle-rendered truth PDFs before this fix:

  * a letter cover's DECLARED 0.50in row pitch printed at 0.30in, so row 6
    landed a full inch above where the declaration (and the truth) put it;
  * its value column printed 1.31in right of the declaration;
  * a detail report's 15 cover rows drifted +0.45in cumulatively.

After the fix the same reports emit every declared object at its declared
box, and the whole page differs from the truth only by ONE constant offset
(the emitted page margin + page-header chrome).

Pinned here, all on a synthetic source so no customer artifact is needed:

  A. every printable object the cover section declares is emitted;
  B. each emitted box's Top/Left/Width/Height IS the declared one, measured
     relative to the cover Rectangle's own origin;
  C. the emitted ROW PITCH is the declared pitch (the defect that made this
     visible), not a template constant;
  D. declared styling (weight, colour, size) rides along;
  E. a cover section that declares NO positioned layout still falls back to
     the synthesized template -- the fix never removes a cover.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402
from converter.generators.rdl import (  # noqa: E402
    _declared_cover_objects,
    _declared_cover_frame,
)
from converter.parsers.oracle_xml import parse_oracle_xml  # noqa: E402


# --- synthetic source -----------------------------------------------------
# Label column x=0.25 (w 1.60), value column x=2.30 (w 4.00), rows every
# 0.50in starting at y=0.60 -- a classic Oracle criteria form.
_ROWS = [
    ("Report:", "CF_TITLE"),
    ("Run Date:", "CurrentDate"),
    ("Sort Order:", "CP_SORT"),
    ("Selection Criteria:", "CP_SUBTITLE"),
]
_PITCH = 0.50
_Y0 = 0.60
_LBL_X, _LBL_W = 0.25, 1.60
_VAL_X, _VAL_W = 2.30, 4.00
_BOX_H = 0.25


def _cover_xml(positioned: bool = True) -> bytes:
    rows = []
    for i, (label, src) in enumerate(_ROWS):
        y = _Y0 + i * _PITCH
        geom_l = (f'<geometryInfo x="{_LBL_X}" y="{y}" width="{_LBL_W}" '
                  f'height="{_BOX_H}"/>' if positioned
                  else '<geometryInfo x="0" y="0" width="0" height="0"/>')
        geom_v = (f'<geometryInfo x="{_VAL_X}" y="{y}" width="{_VAL_W}" '
                  f'height="{_BOX_H}"/>' if positioned
                  else '<geometryInfo x="0" y="0" width="0" height="0"/>')
        rows.append(
            f'<text name="B_L{i}">{geom_l}<textSegment>'
            f'<font face="Arial" size="12"/>'
            f'<string><![CDATA[{label}]]></string></textSegment></text>'
            f'<field name="F_V{i}" source="{src}" alignment="start">'
            f'<font face="Arial" size="12" bold="yes" textColor="r0g0b50"/>'
            f'{geom_v}</field>')
    hdr = ('<section name="header"><body>'
           '<frame name="M_COVER"><geometryInfo x="0.0" y="0.0" '
           'width="7.0" height="6.0"/>' + "".join(rows) + "</frame>"
           "</body></section>")
    return (
        '<?xml version="1.0"?><report name="COVER_GEOM_T" '
        'DTDVersion="9.0.2.0.10">'
        '<data><dataSource name="Q_Main">'
        '<select><![CDATA[select thing_no, thing_nm from t]]></select>'
        '<group name="G_THING"><dataItem name="THING_NO" datatype="vchar2"/>'
        '<dataItem name="THING_NM" datatype="vchar2"/></group>'
        '</dataSource></data>'
        '<layout>' + hdr +
        '<section name="main" repeatOn="G_THING">'
        '<body width="7.5" height="9.0">'
        '<frame name="M_BODY">'
        '<geometryInfo x="0.1" y="0.1" width="7.0" height="8.0"/>'
        '<repeatingFrame name="R_THING" source="G_THING" '
        'printDirection="down" maxRecordsPerPage="1">'
        '<geometryInfo x="0.1" y="0.1" width="7.0" height="7.5"/>'
        '<field name="F_NM" source="THING_NM">'
        '<geometryInfo x="0.5" y="0.5" width="4.0" height="0.25"/>'
        '</field></repeatingFrame></frame>'
        '</body></section></layout></report>').encode()


def _cover_block(rdl: str) -> str:
    i = rdl.index('<Rectangle Name="Rect_CoverPage">')
    j = rdl.index("</ReportItems>", i)
    return rdl[i:j]


def _cover_origin(rdl: str):
    j = rdl.index("</ReportItems>", rdl.index('<Rectangle Name="Rect_CoverPage">'))
    tail = rdl[j:j + 260]
    return (float(re.search(r"<Left>([-\d.]+)in</Left>", tail).group(1)),
            float(re.search(r"<Top>([-\d.]+)in</Top>", tail).group(1)))


def _boxes(block: str):
    """[(top, left, width, height, value_text, xml)] for every emitted cover
    item, in emitted order."""
    out = []
    for m in re.finditer(r'<(Textbox|Rectangle|Image) Name="((?:Lc)?Cov_[^"]+)">'
                         r".*?</\1>", block, re.S):
        b = m.group(0)
        v = re.search(r"<Value>(.*?)</Value>", b, re.S)
        g = {}
        for t in ("Top", "Left", "Width", "Height"):
            mm = re.search("<%s>([-\\d.]+)in</%s>" % (t, t), b)
            g[t] = float(mm.group(1)) if mm else None
        out.append((g["Top"], g["Left"], g["Width"], g["Height"],
                    (v.group(1) if v else ""), b))
    return out


def _row(boxes, want_top):
    """The (label, value) pair emitted on one declared row: the two boxes at
    that Top, ordered left to right."""
    on_row = sorted((b for b in boxes if abs(b[0] - want_top) <= 0.005),
                    key=lambda b: b[1])
    assert len(on_row) == 2, (
        f"expected a label+value pair at Top {want_top:.3f}in, got "
        f"{[(b[0], b[1], b[4][:24]) for b in on_row]}")
    return on_row[0], on_row[1]


# ---------------------------------------------------------------------------
# A. every declared cover object reaches the RDL
# ---------------------------------------------------------------------------

def test_every_declared_cover_object_is_emitted():
    src = _cover_xml()
    rep = parse_oracle_xml(src)
    frame = _declared_cover_frame(rep)
    declared = [f for f, _ in _declared_cover_objects(rep) if f is not frame]
    assert len(declared) == 2 * len(_ROWS), (
        f"fixture parsed {len(declared)} cover objects")
    block = _cover_block(convert(src)["rdl_xml"])
    emitted = re.findall(r'<(?:Textbox|Rectangle|Image) Name="((?:Lc)?Cov_[^"]+)"',
                         block)
    assert len(emitted) == len(declared), (
        f"declared {len(declared)} cover objects, emitted {len(emitted)}: "
        f"{emitted}")
    # ...and they are the DECLARED objects, not a template's stand-ins: every
    # emitted box size is a declared box size.
    want = {(round(float(f.width), 2), round(float(f.height), 2))
            for f in declared}
    got = {(round(b[2], 2), round(b[3], 2)) for b in _boxes(block)}
    assert got == want, (
        f"emitted cover box sizes {sorted(got)} are not the declared "
        f"{sorted(want)} -- the page is still being synthesized")


# ---------------------------------------------------------------------------
# B/C. declared geometry, declared pitch
# ---------------------------------------------------------------------------

def test_cover_rows_carry_their_declared_box_and_pitch():
    rdl = convert(_cover_xml())["rdl_xml"]
    ox, oy = _cover_origin(rdl)
    boxes = _boxes(_cover_block(rdl))

    lbl_tops, val_tops = [], []
    for i, (label, _src) in enumerate(_ROWS):
        want_y = _Y0 + i * _PITCH
        lbl, val = _row(boxes, want_y - oy)
        assert label in lbl[4], (
            f"row {i}: leftmost box is {lbl[4][:40]!r}, not {label!r}")
        assert abs(lbl[1] - (_LBL_X - ox)) <= 0.005, (
            f"{label}: Left {lbl[1]} != declared {_LBL_X} - cover left {ox}")
        assert abs(lbl[2] - _LBL_W) <= 0.005 and abs(lbl[3] - _BOX_H) <= 0.005, (
            f"{label}: box {lbl[2]}x{lbl[3]} != declared {_LBL_W}x{_BOX_H}")
        assert abs(val[1] - (_VAL_X - ox)) <= 0.005, (
            f"value {i}: Left {val[1]} != declared {_VAL_X} - {ox}")
        assert abs(val[2] - _VAL_W) <= 0.005, (
            f"value {i}: width {val[2]} != declared {_VAL_W}")
        assert abs((val[1] - lbl[1]) - (_VAL_X - _LBL_X)) <= 0.005, (
            f"row {i}: emitted column gap {val[1] - lbl[1]:.3f}in != "
            f"declared {_VAL_X - _LBL_X}in")
        lbl_tops.append(lbl[0])
        val_tops.append(val[0])

    for tops, what in ((lbl_tops, "label"), (val_tops, "value")):
        pitches = [round(tops[i + 1] - tops[i], 4)
                   for i in range(len(tops) - 1)]
        assert all(abs(p - _PITCH) <= 0.005 for p in pitches), (
            f"{what} column pitch {pitches} != the declared {_PITCH}in "
            "(a synthesized grid pitch drifts against the declaration)")


# ---------------------------------------------------------------------------
# D. declared styling survives the geometry-driven path
# ---------------------------------------------------------------------------

def test_cover_values_keep_their_declared_font_and_colour():
    rdl = convert(_cover_xml())["rdl_xml"]
    ox, oy = _cover_origin(rdl)
    boxes = _boxes(_cover_block(rdl))
    for i, (_label, src) in enumerate(_ROWS):
        _lbl, val = _row(boxes, (_Y0 + i * _PITCH) - oy)
        body = val[5]
        assert "<FontWeight>Bold</FontWeight>" in body, (
            f"{src}: declared bold dropped")
        assert "<FontSize>12pt</FontSize>" in body, (
            f"{src}: declared 12pt dropped")
        assert "#00007F" in body, f"{src}: declared navy dropped"


# ---------------------------------------------------------------------------
# E. no declared layout -> the synthesized template still builds a cover
# ---------------------------------------------------------------------------

def test_unpositioned_cover_section_keeps_the_synthesized_cover():
    rdl = convert(_cover_xml(positioned=False))["rdl_xml"]
    assert "Rect_CoverPage" in rdl, (
        "a cover section that declares no boxes must still get a cover -- "
        "the declaration-driven path is an upgrade, never a removal")
