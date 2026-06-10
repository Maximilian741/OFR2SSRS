"""Deterministic page geometry. SSRS's PDF renderer emits "a blank page after
every page" when the report BODY WIDTH + left margin + right margin is >= the
PageWidth (the body doesn't fit the printable WIDTH). Height does NOT cause this
-- tall bodies paginate. So the invariant is purely horizontal:

    body_width + LeftMargin + RightMargin  <  PageWidth   (strictly, with slack)

Locks the horizontal-margin buffer so the blank-page bug can't recur."""
import re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
import pytest
from converter import convert
import xml.etree.ElementTree as ET
RD = "{http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition}"

def _in(s):
    m = re.match(r"([\d.]+)", s or ""); return float(m.group(1)) if m else 0.0

def _cases():
    out = []
    sot = Path(__file__).resolve().parent / "fixtures" / "source_of_truth"
    for d in sorted(sot.glob("*")):
        src = d / "source.xml"
        if src.exists():
            out.append(pytest.param(src.read_bytes(), id=d.name))
    tall = ("<report name='T'><data><dataSource name='Q'><select>SELECT a FROM t</select>"
            "</dataSource></data><layout><section name='main'>"
            + "".join(f"<text x='1' y='{y}' width='5' height='0.3'>l{y}</text>" for y in range(20))
            + "</section></layout></report>")
    out.append(pytest.param(tall.encode(), id="synthetic-tall"))
    return out

@pytest.mark.parametrize("xml", _cases())
def test_body_width_plus_margins_under_page_width(xml):
    r = ET.fromstring(convert(xml)["rdl_xml"])
    bw = _in(r.findtext(RD + "Width"))
    pw = _in(r.findtext(RD + "Page/" + RD + "PageWidth"))
    lm = _in(r.findtext(RD + "Page/" + RD + "LeftMargin"))
    rm = _in(r.findtext(RD + "Page/" + RD + "RightMargin"))
    assert bw + lm + rm < pw - 0.02, (
        f"body {bw} + margins {lm}+{rm} = {bw+lm+rm} not < PageWidth {pw} "
        f"-> SSRS blank-page-after-every-page")

def test_no_body_item_exceeds_body_width():
    """No body item's right edge (Left+Width) may exceed the body width, or it
    overflows horizontally and triggers the same blank-page behavior."""
    fix = (Path(__file__).resolve().parent / "fixtures" / "source_of_truth"
           / "letter" / "source.xml")
    if not fix.exists():
        pytest.skip("fixture missing")
    r = ET.fromstring(convert(fix.read_bytes())["rdl_xml"])
    bw = _in(r.findtext(RD + "Width")) or 7.5
    body = r.find(RD + "Body")
    worst = 0.0
    for it in (body.iter() if body is not None else []):
        if it.tag.split("}")[-1] in ("Textbox", "Rectangle", "Image", "Tablix"):
            worst = max(worst, _in(it.findtext(RD + "Left")) + _in(it.findtext(RD + "Width")))
    assert worst <= bw + 0.01, f"an item reaches {worst}in > body width {bw}in"


def test_cover_record_report_uses_detail_start_break():
    """A per-record report WITH a cover must use PageBreak=Start on the detail
    group (fires before EACH row, including the first → separates cover from
    cert 1 without a cover-side End break). The cover Rectangle must NOT carry
    its own PageBreak=End — when cover_h + TablixRow > printable area, SSRS
    positions the Tablix at its absolute Top on page 2, overflowing and
    creating a blank page. Verified: this pattern gives cover on page 1, one
    cert per page starting page 2, zero blank pages."""
    fix = (Path(__file__).resolve().parent / "fixtures" / "source_of_truth"
           / "letter" / "source.xml")
    if not fix.exists():
        pytest.skip("fixture missing")
    rdl = convert(fix.read_bytes())["rdl_xml"]
    if "OuterPageWrapper" not in rdl:
        pytest.skip("fixture is not a per-record tablix report")
    breaks = re.findall(r"<BreakLocation>(\w+)</BreakLocation>", rdl)
    # Cover reports: detail Start is the ONLY break. No End (cover-side), no
    # Between (would miss the cover→cert1 separation).
    assert breaks == ["Start"], (
        f"cover report breaks must be exactly ['Start']; got {breaks}")
