"""A cover sheet whose whole content is hidden must print NO page.

Oracle's parameter form is a canvas: a conditional branch (a mail variant,
a debug parameter echo) is routinely authored BELOW the first printable
sheet and paginates onto a continuation parameter-form page. Emitting that
sheet as a Rectangle at the next paper's absolute y reads right and prints
wrong -- SSRS reserves the space of a hidden item, so under the shipped
default parameters the sheet printed a BLANK page. The truth export it was
measured against (307 pages) has no blank page anywhere.

The general mechanism, pinned here: a Tablix ROW's visibility genuinely
collapses where a Rectangle's does not, so each continuation sheet is a row
of a single-column Tablix carrying that sheet's own <Hidden>, and the cover
Rectangle's declared box never reaches past its FIRST sheet.

Structural assertions always run; the render proof runs wherever the
ReportViewer DLLs are present (tools/renderlab) and measures Microsoft's
own engine output both ways: shipped defaults -> no blank page; the branch
forced visible -> the sheet prints complete, on a page of its own.
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
from converter.generators.rdl import _cover_sheet_pitch  # noqa: E402
from converter.parsers.oracle_xml import parse_oracle_xml  # noqa: E402

try:
    from render import render_rdl, lib_ready  # noqa: E402
    _LIB_OK = bool(lib_ready()) and sys.platform == "win32"
except Exception:  # noqa: BLE001
    _LIB_OK = False


# --- synthetic source -----------------------------------------------------
# Sheet one: a small criteria form. Below it: a conditional block the
# SHIPPED DEFAULT parameter value hides (default "BRIEF", the trigger wants
# "FULL") -- the exact production shape that printed the blank page.
_ROWS = [("Report:", "CF_TITLE"), ("Run Date:", "CurrentDate"),
         ("Sort Order:", "CP_SORT"), ("Criteria:", "CP_SUBTITLE")]
_BRANCH_LINES = ["Branch note one, below the sheet.",
                 "Branch note two, below the sheet.",
                 "Branch note three, below the sheet."]
_TRIGGER = "f_branch_g_ft"


def _source(with_branch: bool = True, y0: float = 0.60,
            box_h: float = 0.25) -> bytes:
    rows = []
    for i, (label, src) in enumerate(_ROWS):
        y = round(y0 + i * 0.50, 4)
        rows.append(
            f'<text name="B_L{i}"><geometryInfo x="0.25" y="{y}" '
            f'width="1.60" height="{box_h}"/><textSegment>'
            f'<font face="Arial" size="12"/>'
            f'<string><![CDATA[{label}]]></string></textSegment></text>'
            f'<field name="F_V{i}" source="{src}" alignment="start">'
            f'<font face="Arial" size="12"/>'
            f'<geometryInfo x="2.30" y="{y}" width="4.00" '
            f'height="{box_h}"/></field>')
    lines = []
    for i, txt in enumerate(_BRANCH_LINES):
        y = 9.60 + i * 0.50
        lines.append(
            f'<text name="B_BR{i}"><geometryInfo x="0.25" y="{y}" '
            f'width="6.00" height="0.25"/><textSegment>'
            f'<font face="Arial" size="10"/>'
            f'<string><![CDATA[{txt}]]></string></textSegment></text>')
    branch = ('<frame name="M_BRANCH"><geometryInfo x="0.25" y="9.60" '
              'width="7.00" height="2.00"/>'
              f'<advancedLayout formatTrigger="{_TRIGGER}"/>'
              + "".join(lines) + "</frame>") if with_branch else ""
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
        '<?xml version="1.0"?><report name="COVER_BLANK_T" '
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


def _element(src: str, start: int, tag: str) -> str:
    depth = 0
    for m in re.finditer(r"<(/?)%s\b[^>]*?(/?)>" % tag, src[start:]):
        if m.group(2) == "/":
            continue
        depth += -1 if m.group(1) else 1
        if depth == 0:
            return src[start:start + m.end()]
    return src[start:]


def _cover_rect(rdl: str) -> str:
    return _element(rdl, rdl.index('<Rectangle Name="Rect_CoverPage">'),
                    "Rectangle")


def _blank_pages(pdf_path: str) -> list:
    """Pages with no residual text once page chrome is stripped."""
    from pypdf import PdfReader
    out = []
    for i, page in enumerate(PdfReader(pdf_path).pages):
        txt = (page.extract_text() or "").strip()
        residual = "".join(
            ln for ln in txt.splitlines()
            if not ln.strip().lower().startswith(("page ", "report run on"))
        ).strip()
        if len(residual) < 8:
            out.append(i + 1)
    return out


def _force_sheets_visible(rdl: str) -> str:
    """Stub every <Hidden> of the sheet carrier so the branch prints."""
    i = rdl.index('<Tablix Name="Tablix_CoverSheets">')
    tbx = _element(rdl, i, "Tablix")
    return rdl[:i] + re.sub(r"<Hidden>.*?</Hidden>", "<Hidden>false</Hidden>",
                            tbx, flags=re.S) + rdl[i + len(tbx):]


# ---------------------------------------------------------------------------
# structural: the cover never RESERVES the continuation sheet's paper
# ---------------------------------------------------------------------------

def _cover_box(with_branch: bool):
    block = _cover_rect(convert(_source(with_branch))["rdl_xml"])
    tail = block[block.rindex("</ReportItems>"):]
    return (float(re.search(r"<Top>([-\d.]+)in</Top>", tail).group(1)),
            float(re.search(r"<Height>([-\d.]+)in</Height>", tail).group(1)))


def test_cover_box_never_reserves_the_continuation_sheet():
    src = _source()
    pitch = _cover_sheet_pitch(parse_oracle_xml(src))
    top, height = _cover_box(True)
    assert top + height <= pitch + 0.01, (
        f"the cover rectangle reserves down to {top + height:.3f}in, past its "
        f"first {pitch:.2f}in sheet: SSRS prints that reserved space as a "
        "BLANK page whenever the continuation sheet hides")

    # DIFFERENTIAL: a below-fold branch may cost the cover box NOTHING. Its
    # sheet is carried by a Tablix row that reserves only its stub until the
    # row really renders, so the declared box is the same box a cover with no
    # branch at all would print (within the 2-decimal bottom-edge quantum).
    _btop, _bheight = _cover_box(False)
    assert abs((top + height) - (_btop + _bheight)) <= 0.011, (
        f"the cover box grows from {_btop + _bheight:.3f}in to "
        f"{top + height:.3f}in just because a hidden continuation sheet is "
        "declared — that growth is the blank page")


def test_cover_bottom_lands_on_the_region_placer_grid():
    """A cover carrying continuation sheets must end ON the 2-decimal grid.

    The region below a cover is placed FLUSH at the cover's reported bottom,
    written with 2 decimals. A bottom of 8.9718in therefore becomes a Top of
    8.97in — 0.0018in INSIDE the cover rectangle — and SSRS never displaces
    an item that overlaps the container that grows: the continuation sheet
    then printed on the same paper as the first record instead of getting
    its own page (engine-measured both ways).

    Declared geometry that lands off the grid (an odd y and an odd box
    height, as real Oracle exports carry) is the case that exposes it."""
    src = _source(True, y0=0.6013, box_h=0.2537)
    block = _cover_rect(convert(src)["rdl_xml"])
    assert "Tablix_CoverSheets" in block, "fixture grew no continuation sheet"
    tail = block[block.rindex("</ReportItems>"):]
    top = float(re.search(r"<Top>([-\d.]+)in</Top>", tail).group(1))
    height = float(re.search(r"<Height>([-\d.]+)in</Height>", tail).group(1))
    bottom = top + height
    assert abs(bottom * 100 - round(bottom * 100)) <= 1e-6, (
        f"cover bottom {bottom:.4f}in is off the 2-decimal grid the region "
        "placer rounds to, so the next region lands inside this rectangle "
        "and the sheet loses its own page")


def test_continuation_sheet_is_a_collapsing_tablix_row():
    rdl = convert(_source())["rdl_xml"]
    block = _cover_rect(rdl)
    i = block.find('<Tablix Name="Tablix_CoverSheets">')
    assert i >= 0, (
        "the continuation sheet must ride in a Tablix row — a Rectangle's "
        "hidden space is still reserved and printed")
    tbx = _element(block, i, "Tablix")
    assert re.search(r'<Rectangle Name="Rect_CoverPage_\d+">', tbx), (
        "the sheet rectangle belongs inside the row's CellContents")
    hidden = re.findall(
        r"<TablixMember>\s*<Visibility>\s*<Hidden>(.*?)</Hidden>",
        tbx[tbx.index("<TablixRowHierarchy>"):], re.S)
    assert len(hidden) == 1 and "P_MODE" in hidden[0], (
        f"the sheet's row must carry the sheet's declared condition: {hidden}")
    # No fabricated "no data" notice on a declared parameter-form sheet.
    assert "<NoRowsMessage>" not in tbx, (
        "the sheet carrier binds a dataset only to satisfy SSRS; a "
        "NoRowsMessage would replace a declared sheet with a notice")


# ---------------------------------------------------------------------------
# render: Microsoft's engine, both ways
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _LIB_OK,
                    reason="ReportViewer DLLs not fetched (tools/renderlab)")
def test_hidden_cover_sheet_prints_no_page_and_visible_one_prints_whole(
        tmp_path):
    rdl = convert(_source())["rdl_xml"]

    # (1) shipped defaults: the branch hides -> no paper at all for it.
    p1 = tmp_path / "default.rdl"
    p1.write_text(rdl, encoding="utf-8")
    res = render_rdl(p1, tmp_path / "default.pdf", rows=3)
    assert res["ok"], f"MS engine refused the RDL:\n{res['log'][-1200:]}"
    blanks = _blank_pages(res["pdf"])
    assert blanks == [], (
        f"blank page(s) {blanks} under the shipped defaults — a sheet whose "
        "every object is hidden must consume no page")
    from pypdf import PdfReader
    n_default = len(PdfReader(res["pdf"]).pages)

    # (2) branch forced visible: the sheet prints, whole, on its own page.
    p2 = tmp_path / "visible.rdl"
    p2.write_text(_force_sheets_visible(rdl), encoding="utf-8")
    res2 = render_rdl(p2, tmp_path / "visible.pdf", rows=3)
    assert res2["ok"], f"MS engine refused the RDL:\n{res2['log'][-1200:]}"
    assert _blank_pages(res2["pdf"]) == [], (
        "the visible continuation sheet must not drag a blank page with it")
    pages = [(pg.extract_text() or "") for pg in PdfReader(res2["pdf"]).pages]
    assert len(pages) == n_default + 1, (
        f"the visible sheet must cost exactly one page: {n_default} -> "
        f"{len(pages)}")
    sheet = [p for p in pages if _BRANCH_LINES[0] in " ".join(p.split())]
    assert len(sheet) == 1, "the continuation sheet prints on exactly one page"
    body = " ".join(sheet[0].split())
    for line in _BRANCH_LINES:
        assert line in body, (
            f"declared continuation line {line!r} did not print — the sheet "
            "must arrive complete")
