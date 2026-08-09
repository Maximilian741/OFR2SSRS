"""A declared cover section is a CANVAS: nothing on it may be dropped.

Oracle authors its criteria/parameter cover as real layout objects inside
``<section name="header">``.  Two things about that canvas are routinely
missed, and both cost declared content:

  * a CONDITIONAL branch (a mail variant beside the mail-less one, a debug
    parameter echo) is a normal frame carrying a ``formatTrigger`` -- its
    members are declared exactly like the default branch's and must be
    emitted exactly like them, each with its own translated ``<Hidden>``;

  * such a branch is routinely authored BELOW the first printable sheet
    (Oracle's form canvas has no page limit), and Oracle paginates it onto
    a CONTINUATION parameter-form page.

Before this was fixed the emitter budgeted a single sheet and silently
DROPPED every object declared past it -- measured on a production invoice
source: 43 declared cover objects, 24 emitted, 19 lost, taking a
"see memorandum..." note, a "generate memo" action, three envelope/mailing
instruction lines and six debug parameter echoes with them.

Pinned here on a synthetic source (no customer artifact needed):

  A. EVERY printable object the cover section declares is emitted, however
     far below the sheet it is declared and whichever branch owns it;
  B. the below-sheet objects land on a CONTINUATION sheet rectangle carried
     by a ROW of a single-column Tablix -- not squashed into sheet one, not
     clipped, and NOT positioned into reserved canvas;
  C. inside that sheet every DECLARED offset survives verbatim (the cut
     falls BETWEEN objects, never through one);
  D. each conditional object keeps its own translated ``<Hidden>``, and the
     sheet's ROW hides only when EVERY object on it hides;
  E. a cover that fits one sheet grows NO continuation sheet.

B WAS ONCE MEASURED WRONG. The continuation sheet used to be a Rectangle
placed at the absolute body-y of the next paper (``sheet_index * pitch``),
which reads right and prints wrong: SSRS reserves the space of a hidden
item, so the sheet printed a BLANK page whenever its branch was hidden --
under the shipped defaults, on a 307-page truth export that has no blank
page anywhere (engine-measured: 14 pages with page 2 blank; as a Tablix
row, 13 pages and no blank; forced visible, 14 pages with the sheet alone
on page 2 and all 17 of its declared objects at declared geometry). A
hidden Tablix ROW collapses, so the assertions below now pin the row
mechanism AND the container that must never reserve the sheet's paper.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402
from converter.generators.rdl import (  # noqa: E402
    _declared_cover_frame,
    _declared_cover_objects,
    _cover_sheet_pitch,
)
from converter.parsers.oracle_xml import parse_oracle_xml  # noqa: E402


# --- synthetic source -----------------------------------------------------
# Sheet one: a four-row criteria form (label column x=0.25, value column
# x=2.30, rows every 0.50in from y=0.60).
# Below the sheet: a conditional branch frame whose four rows are declared at
# y=9.60..11.10 -- past any letter-portrait printable height.
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

# The conditional branch, declared BELOW the first sheet.
_BRANCH_Y0 = 9.60
_BRANCH_PITCH = 0.50
_BRANCH_LINES = [
    "Branch note one, declared below the sheet.",
    "Branch note two, declared below the sheet.",
    "Branch note three, declared below the sheet.",
    "Branch note four, declared below the sheet.",
]
_TRIGGER = "f_branch_g_ft"


def _cover_xml(with_branch: bool = True) -> bytes:
    rows = []
    for i, (label, src) in enumerate(_ROWS):
        y = _Y0 + i * _PITCH
        rows.append(
            f'<text name="B_L{i}"><geometryInfo x="{_LBL_X}" y="{y}" '
            f'width="{_LBL_W}" height="{_BOX_H}"/><textSegment>'
            f'<font face="Arial" size="12"/>'
            f'<string><![CDATA[{label}]]></string></textSegment></text>'
            f'<field name="F_V{i}" source="{src}" alignment="start">'
            f'<font face="Arial" size="12"/>'
            f'<geometryInfo x="{_VAL_X}" y="{y}" width="{_VAL_W}" '
            f'height="{_BOX_H}"/></field>')
    branch = ""
    if with_branch:
        lines = []
        for i, txt in enumerate(_BRANCH_LINES):
            y = _BRANCH_Y0 + i * _BRANCH_PITCH
            lines.append(
                f'<text name="B_BR{i}"><geometryInfo x="{_LBL_X}" y="{y}" '
                f'width="6.00" height="{_BOX_H}"/><textSegment>'
                f'<font face="Arial" size="10"/>'
                f'<string><![CDATA[{txt}]]></string></textSegment></text>')
        branch = (
            f'<frame name="M_BRANCH"><geometryInfo x="0.25" y="{_BRANCH_Y0}" '
            f'width="7.00" height="2.00"/>'
            f'<advancedLayout formatTrigger="{_TRIGGER}"/>'
            + "".join(lines) + "</frame>")
    hdr = ('<section name="header"><body width="7.5" height="12.0">'
           '<frame name="M_COVER"><geometryInfo x="0.0" y="0.0" '
           'width="7.0" height="12.0"/>' + "".join(rows) + branch
           + "</frame></body></section>")
    units = (
        '<programUnits><function name="%s"><textSource><![CDATA['
        'FUNCTION F_Branch_G_FT RETURN BOOLEAN IS BEGIN '
        "IF :P_MODE = 'FULL' THEN RETURN(TRUE); "
        'ELSE RETURN(FALSE); END IF; END;]]></textSource></function>'
        '</programUnits>' % _TRIGGER)
    return (
        '<?xml version="1.0"?><report name="COVER_CONT_T" '
        'DTDVersion="9.0.2.0.10">'
        '<data><dataSource name="Q_Main">'
        '<select><![CDATA[select thing_no, thing_nm from t]]></select>'
        '<group name="G_THING"><dataItem name="THING_NO" datatype="vchar2"/>'
        '<dataItem name="THING_NM" datatype="vchar2"/></group>'
        '</dataSource>'
        '<userParameter name="P_MODE" datatype="character" width="10">'
        '<initialValue><![CDATA[BRIEF]]></initialValue></userParameter>'
        '</data>'
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
        '</body></section></layout>' + units + '</report>').encode()


def _cover_rect(rdl: str) -> str:
    """The whole <Rectangle Name="Rect_CoverPage"> element source."""
    i = rdl.index('<Rectangle Name="Rect_CoverPage">')
    depth, j = 0, i
    for m in re.finditer(r"<(/?)Rectangle\b[^>]*?(/?)>", rdl[i:]):
        if m.group(2) == "/":
            continue
        depth += -1 if m.group(1) else 1
        if depth == 0:
            j = i + m.end()
            break
    return rdl[i:j]


def _element(src: str, start: int, tag: str) -> str:
    """The whole ``<tag ...> ... </tag>`` element beginning at ``start``."""
    depth = 0
    for m in re.finditer(r"<(/?)%s\b[^>]*?(/?)>" % tag, src[start:]):
        if m.group(2) == "/":
            continue
        depth += -1 if m.group(1) else 1
        if depth == 0:
            return src[start:start + m.end()]
    return src[start:]


def _items(block: str):
    """[(name, top, left, width, height, value)] for every emitted item,
    EXCLUDING the container rectangles the cover builder wraps them in."""
    out = []
    for tag in ("Textbox", "Image", "Rectangle"):
        for m in re.finditer(r'<%s\s+Name="([^"]+)">' % tag, block):
            name = m.group(1)
            if re.fullmatch(r"Rect_CoverPage(_\d+)?", name):
                continue
            body = _element(block, m.start(), tag)
            g = {}
            for t in ("Top", "Left", "Width", "Height"):
                # the item's OWN geometry is the last one in its element
                mm = re.findall("<%s>([-\\d.]+)in</%s>" % (t, t), body)
                g[t] = float(mm[-1]) if mm else None
            v = re.search(r"<Value>(.*?)</Value>", body, re.S)
            out.append((name, g["Top"], g["Left"], g["Width"], g["Height"],
                        v.group(1) if v else ""))
    return out


def _sheet_tablix(block: str):
    """The whole <Tablix Name="Tablix_CoverSheets"> element, or None."""
    m = re.search(r'<Tablix\s+Name="Tablix_CoverSheets">', block)
    return None if m is None else _element(block, m.start(), "Tablix")


def _continuation(block: str):
    """(name, height, body-source) of the continuation sheet rectangle.

    The sheet carries NO Top of its own: it is the CellContents of a Tablix
    row, and the row is what places it (and what collapses it away when the
    branch hides). ``height`` is the rect's own declared height."""
    m = re.search(r'<Rectangle\s+Name="(Rect_CoverPage_\d+)">(.*?)'
                  r"</Rectangle>", block, re.S)
    if m is None:
        return None
    inner = m.group(2)
    height = float(re.findall(r"<Height>([-\d.]+)in</Height>", inner)[-1])
    return (m.group(1), height, inner)


# ---------------------------------------------------------------------------
# A. nothing declared is dropped
# ---------------------------------------------------------------------------

def test_below_sheet_cover_objects_are_all_emitted():
    src = _cover_xml()
    rep = parse_oracle_xml(src)
    frame = _declared_cover_frame(rep)
    declared = [f for f, _ in _declared_cover_objects(rep) if f is not frame]
    assert len(declared) == 2 * len(_ROWS) + len(_BRANCH_LINES), (
        f"fixture parsed {len(declared)} cover objects")

    block = _cover_rect(convert(src)["rdl_xml"])
    names = [n for n, *_ in _items(block)]
    assert len(names) == len(declared), (
        f"declared {len(declared)} cover objects, emitted {len(names)}: "
        f"{names}")
    # ...and the conditional branch's own wording is among them, verbatim.
    for line in _BRANCH_LINES:
        assert line in block, (
            f"declared conditional-branch line {line!r} was dropped -- an "
            "object may not vanish because its branch is not the default")


# ---------------------------------------------------------------------------
# B/C. they land on a continuation sheet, at their declared offsets
# ---------------------------------------------------------------------------

def test_below_sheet_objects_land_on_a_continuation_sheet():
    src = _cover_xml()
    rep = parse_oracle_xml(src)
    pitch = _cover_sheet_pitch(rep)
    rdl = convert(src)["rdl_xml"]
    block = _cover_rect(rdl)

    cont = _continuation(block)
    assert cont is not None, (
        "objects declared below the printable sheet must get a CONTINUATION "
        "cover sheet, not be squashed into sheet one")
    name, height, inner = cont
    assert height <= pitch + 0.01, (
        f"continuation sheet is {height:.2f}in tall, past one {pitch:.2f}in "
        "sheet")

    # The sheet is a ROW of a one-column Tablix -- the only container whose
    # visibility genuinely collapses. A Rectangle placed at the next paper's
    # absolute y reserves that paper even when hidden, and printed a blank
    # page under the shipped defaults.
    tbx = _sheet_tablix(block)
    assert tbx is not None, (
        "the continuation sheet must ride in Tablix_CoverSheets; a hidden "
        "Rectangle reserves its page and prints it blank")
    assert inner in tbx, "the continuation sheet is not inside the row Tablix"
    assert "<CellContents>" in tbx, "the sheet must be a row CELL's contents"
    assert len(re.findall(r"<TablixColumn>", tbx)) == 1, (
        "the sheet carrier is a SINGLE-column Tablix")
    row_hs = [float(h) for h in
              re.findall(r"<TablixRow>\s*<Height>([\d.]+)in</Height>", tbx)]
    assert len(row_hs) == 1, f"expected one sheet row, found {row_hs}"
    assert abs(row_hs[0] - pitch) <= 0.05, (
        f"a continuation row is {row_hs[0]:.2f}in; a whole {pitch:.2f}in "
        "sheet keeps the next sheet off this one's paper")
    # ...and the sheet itself carries NO absolute Top: nothing places it into
    # canvas the cover would have to reserve.
    assert "<Top>" not in inner.split("</ReportItems>")[-1], (
        "the continuation sheet must not declare its own Top -- the row "
        "places it")

    # THE REGRESSION GUARD: the cover Rectangle's own declared height may
    # never reach past its FIRST sheet, or the space it reserves prints as a
    # blank page whenever the continuation hides.
    tail = block[block.rindex("</ReportItems>"):]
    cover_top = float(re.search(r"<Top>([-\d.]+)in</Top>", tail).group(1))
    cover_h = float(re.search(r"<Height>([-\d.]+)in</Height>", tail).group(1))
    assert cover_top + cover_h <= pitch + 0.01, (
        f"the cover rectangle reserves {cover_top + cover_h:.3f}in — past "
        f"its first {pitch:.2f}in sheet, which prints a blank page when the "
        "continuation hides")
    # The region below a cover is placed FLUSH at the cover's reported
    # bottom, written with 2 decimals. Land the bottom ON that grid, or the
    # rounding puts the next region a hair INSIDE this rectangle and SSRS
    # stops displacing it (measured: the sheet then shares paper with the
    # first record instead of getting its own page).
    assert abs((cover_top + cover_h) * 100
               - round((cover_top + cover_h) * 100)) <= 1e-6, (
        f"cover bottom {cover_top + cover_h:.4f}in is off the 2-decimal grid "
        "the region placer rounds to")

    # Every declared object of the branch is INSIDE that sheet...
    for line in _BRANCH_LINES:
        assert line in inner, f"{line!r} is not on the continuation sheet"
    # ...at its DECLARED offset from the sheet's first object: the declared
    # pitch survives the cut verbatim.
    tops = sorted(t for n, t, *_ in _items(inner) if t is not None)
    assert len(tops) == len(_BRANCH_LINES), (
        f"continuation sheet holds {len(tops)} items, expected "
        f"{len(_BRANCH_LINES)}")
    assert abs(tops[0]) <= 0.005, (
        f"the first continuation object starts at {tops[0]:.3f}in, not at the "
        "top of its sheet")
    gaps = [round(tops[i + 1] - tops[i], 4) for i in range(len(tops) - 1)]
    assert all(abs(g - _BRANCH_PITCH) <= 0.005 for g in gaps), (
        f"continuation pitch {gaps} != the declared {_BRANCH_PITCH}in")

    # Sheet one keeps ONLY what sheet one declares.
    sheet1 = block.replace(inner, "")
    for line in _BRANCH_LINES:
        assert line not in sheet1, (
            f"{line!r} was declared below the sheet but printed on sheet one")


# ---------------------------------------------------------------------------
# D. the branch's condition rides along, per object and per sheet
# ---------------------------------------------------------------------------

def test_conditional_branch_keeps_its_hidden_on_object_and_sheet():
    block = _cover_rect(convert(_cover_xml())["rdl_xml"])
    cont = _continuation(block)
    assert cont is not None
    _name, _h, inner = cont
    hiddens = re.findall(r"<Hidden>(.*?)</Hidden>", inner, re.S)
    assert len(hiddens) >= len(_BRANCH_LINES), (
        "every conditional cover object needs its own <Hidden>; found "
        f"{len(hiddens)}")
    for h in hiddens:
        assert "P_MODE" in h, (
            f"declared trigger condition lost: <Hidden>{h}</Hidden>")

    # The SHEET's own condition rides on the ROW member -- that is what makes
    # a fully hidden sheet consume no paper. On the Rectangle it only hid the
    # ink and still printed the page.
    tbx = _sheet_tablix(block)
    assert tbx is not None
    member = tbx[tbx.index("<TablixRowHierarchy>"):]
    sheet_hidden = re.findall(
        r"<TablixMember>\s*<Visibility>\s*<Hidden>(.*?)</Hidden>",
        member, re.S)
    assert len(sheet_hidden) == 1, (
        "the sheet's row must carry the sheet's <Hidden>; found "
        f"{len(sheet_hidden)}")
    assert "P_MODE" in sheet_hidden[0], (
        f"declared trigger condition lost on the row: {sheet_hidden[0]!r}")
    tail = inner.split("</ReportItems>")[-1]
    assert "<Visibility>" not in tail, (
        "the sheet Rectangle must NOT carry the visibility itself — a hidden "
        "Rectangle still reserves (and prints) its page")


# ---------------------------------------------------------------------------
# E. a cover that fits one sheet grows no continuation sheet
# ---------------------------------------------------------------------------

def test_cover_that_fits_one_sheet_has_no_continuation():
    block = _cover_rect(convert(_cover_xml(with_branch=False))["rdl_xml"])
    assert _continuation(block) is None, (
        "a cover whose declared objects all fit one printable sheet must not "
        "grow a second one")
    assert "<KeepTogether>true</KeepTogether>" in block, (
        "a single-sheet cover must still be kept together")
