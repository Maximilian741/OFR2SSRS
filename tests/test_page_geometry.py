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


def test_record_report_without_cover_uses_between_break():
    """A per-record letter/cert whose source has NO criteria cover section must
    NOT emit a fabricated 'Report Parameters / Run Date / Run By' cover page, and
    its detail group must use PageBreak=Between → record 1 on page 1, one record
    per page thereafter, ZERO leading blank page. (A report that DOES display a
    criteria cover instead uses Start: cover on page 1, records from page 2 — that
    path is exercised by the real letter corpus, which carries a header criteria
    section.) This synthetic fixture has no header criteria section, so 'Between'
    is the correct blank-page-free geometry and no cover may be fabricated."""
    fix = (Path(__file__).resolve().parent / "fixtures" / "source_of_truth"
           / "letter" / "source.xml")
    if not fix.exists():
        pytest.skip("fixture missing")
    rdl = convert(fix.read_bytes())["rdl_xml"]
    if "OuterPageWrapper" not in rdl:
        pytest.skip("fixture is not a per-record tablix report")
    # No fabricated cover for a report with no criteria section.
    assert "Rect_CoverPage" not in rdl and "Cov_ParamsHdr" not in rdl, (
        "a no-criteria per-record report must not fabricate a cover page")
    breaks = re.findall(r"<BreakLocation>(\w+)</BreakLocation>", rdl)
    assert breaks == ["Between"], (
        f"no-cover per-record breaks must be exactly ['Between']; got {breaks}")
