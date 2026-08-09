"""The report-end trailer must print only what the DECLARATION puts at the
report end -- and the cover must carry its own declared styling.

Three general defects, all render-measured on a production report:

1. ``_ensure_summary_totals_emitted`` walked the WHOLE layout looking for
   report-scoped summaries and for trailer boilerplate. Oracle's
   ``<section name="header">`` is the COVER -- it prints once, BEFORE the
   body -- so a report whose only grand-total placement is on the cover got
   that total SYNTHESIZED a second time at the bottom of the body, dragging
   the cover's own title and section heading down with it as extra "trailer"
   lines. Nothing declared in a leading section may reach the report end.

2. The cover's criteria rows echoed the DECLARED labels but emitted the
   VALUE boxes at a hardcoded regular weight, silently dropping a criteria
   form the source authors bold.

3. The cover Rectangle was pinned to a synthesized 0.35in/6.8in span even
   when the source draws its own frame around the criteria form, so the box
   printed at the wrong left edge and the wrong width.

Reports that DO declare a report-end trailer (a total placed in a
``section_main`` band at the bottom of the record flow) must keep printing
it -- guarded here as well as by the existing trailer wording/geometry tests.
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter.generators.rdl import (  # noqa: E402
    _build_cover_page,
    _ensure_summary_totals_emitted,
    _q,
    _sub,
)
from converter.models import (  # noqa: E402
    DataItem,
    DataQuery,
    FormulaColumn,
    LayoutField,
    LayoutGroup,
    ParsedReport,
    ReportParameter,
)

_COVER_TITLE = "Widget Maintenance Program Detail Report"
_COVER_HEADING = "Report Parameters"
_TOTAL_CAPTION = "Total of All Records:"


def _skeleton_root() -> ET.Element:
    """Minimal RDL with one body item for the trailer stack to sit under."""
    root = ET.Element(_q("Report"))
    page = _sub(root, "Page")
    _sub(page, "PageWidth", "8.50in")
    _sub(page, "PageHeight", "11.00in")
    _sub(page, "LeftMargin", "0.25in")
    _sub(page, "RightMargin", "0.25in")
    body = _sub(root, "Body")
    ri = _sub(body, "ReportItems")
    anchor = _sub(ri, "Tablix")
    anchor.set("Name", "Tablix_Body")
    _sub(anchor, "Top", "0.20in")
    _sub(anchor, "Left", "0.10in")
    _sub(anchor, "Width", "7.00in")
    _sub(anchor, "Height", "4.00in")
    _sub(body, "Height", "5.00in")
    _sub(root, "Width", "7.50in")
    return root


def _cover_section() -> LayoutGroup:
    """A leading section: title, heading, one criteria row, and the grand
    total placed BESIDE its caption -- the classic Oracle cover."""
    return LayoutGroup(name="section_header", kind="section_header", fields=[
        LayoutField(name="B_TITLE", kind="text", text=_COVER_TITLE,
                    bold=True, font_size=11, x=1.70, y=0.58,
                    width=4.33, height=0.58),
        LayoutField(name="B_TOTLBL", kind="text", text=_TOTAL_CAPTION,
                    bold=True, font_size=10, x=2.81, y=1.97,
                    width=1.62, height=0.18),
        LayoutField(name="F_TOTAL", kind="field", source="CountWIDPerReport",
                    bold=True, font_size=10, x=4.53, y=1.97,
                    width=0.71, height=0.19),
        LayoutField(name="B_HEADING", kind="text", text=_COVER_HEADING,
                    bold=True, italic=True, underline=True, font_size=12,
                    x=2.43, y=2.50, width=2.87, height=0.25),
        LayoutField(name="B_REGION", kind="text", text="Region:",
                    bold=True, font_size=10, align="right",
                    x=2.06, y=2.84, width=1.43, height=0.18),
        LayoutField(name="F_P_REGION", kind="field", source="PARM_REGION",
                    bold=True, font_size=10, font_family="Arial",
                    x=3.58, y=2.84, width=3.10, height=0.19),
    ])


def _main_section(with_trailer_band: bool) -> LayoutGroup:
    rec = LayoutGroup(name="R_MAIN", kind="repeating_frame",
                      source_query="G_MAIN", fields=[
                          LayoutField(name="F_WID", kind="field", source="WID",
                                      x=0.0, y=0.0, width=1.5, height=0.18),
                      ])
    kids = [rec]
    if with_trailer_band:
        kids.append(LayoutGroup(name="M_TRAILER", kind="frame", fields=[
            LayoutField(name="B_END_LBL", kind="text", text=_TOTAL_CAPTION,
                        bold=True, font_size=10, x=0.0, y=5.70,
                        width=1.62, height=0.18),
            LayoutField(name="F_END_TOT", kind="field",
                        source="CountWIDPerReport", bold=True, font_size=10,
                        x=1.80, y=5.70, width=1.50, height=0.19),
        ]))
    return LayoutGroup(name="section_main", kind="section_main", children=kids)


def _report(with_trailer_band: bool) -> ParsedReport:
    summary = FormulaColumn(name="CountWIDPerReport", agg_function="count",
                            agg_source="WID", agg_scope="report")
    query = DataQuery(name="Q_1", items=[DataItem(name="WID")])
    return ParsedReport(
        name="WIDGET_DETAIL", queries=[query], formulas=[summary],
        parameters=[ReportParameter(name="PARM_REGION")],
        layout=[_cover_section(), _main_section(with_trailer_band)])


def _trailer_values(root: ET.Element):
    return [next((v.text or "" for v in tb.iter(_q("Value"))), "")
            for tb in root.iter(_q("Textbox"))
            if (tb.get("Name") or "").startswith("Tb_GrandTotal_")]


# ---------------------------------------------------------------------------
# 1. a leading (cover) section never becomes a report-end trailer
# ---------------------------------------------------------------------------

def test_cover_only_total_is_not_re_emitted_at_report_end():
    root = _skeleton_root()
    _ensure_summary_totals_emitted(root, _report(with_trailer_band=False))
    assert _trailer_values(root) == [], (
        "the cover's grand total was synthesized a second time at the report "
        "end: " + repr(_trailer_values(root)))


def test_cover_boilerplate_never_reaches_the_report_end_trailer():
    root = _skeleton_root()
    _ensure_summary_totals_emitted(root, _report(with_trailer_band=True))
    joined = " || ".join(_trailer_values(root))
    assert _COVER_TITLE not in joined, (
        "the cover title was recycled into the body trailer: " + joined)
    assert _COVER_HEADING not in joined, (
        "the cover section heading was recycled into the body trailer: "
        + joined)


# ---------------------------------------------------------------------------
# 2. a DECLARED report-end trailer still prints (the fix stays targeted)
# ---------------------------------------------------------------------------

def test_declared_report_end_trailer_still_emits():
    root = _skeleton_root()
    _ensure_summary_totals_emitted(root, _report(with_trailer_band=True))
    joined = " || ".join(_trailer_values(root))
    assert "Count(" in joined, (
        "the declared report-end total stopped printing: " + joined)
    assert _TOTAL_CAPTION in joined, (
        "the declared trailer wording stopped printing: " + joined)


# ---------------------------------------------------------------------------
# 3. the cover carries its DECLARED styling and frame span
# ---------------------------------------------------------------------------

def _cover_frame_report() -> ParsedReport:
    rep = _report(with_trailer_band=False)
    hdr = rep.layout[0]
    hdr.fields.insert(0, LayoutField(
        name="B_FRAME", kind="rect", line_pattern="solid",
        x=0.6875, y=0.0625, width=6.1875, height=5.9375))
    return rep


def _boxes(rect: ET.Element, prefix: str):
    out = []
    for tb in rect.iter(_q("Textbox")):
        if not (tb.get("Name") or "").startswith(prefix):
            continue
        runs = [dict((c.tag.rsplit("}")[-1], c.text)
                     for c in st)
                for st in tb.iter(_q("Style"))]
        out.append((tb, runs))
    return out


def _fin(el: ET.Element, tag: str) -> float:
    return float((el.findtext(_q(tag)) or "0").replace("in", ""))


def test_declared_cover_criteria_values_keep_their_declared_weight():
    rect = _build_cover_page(_cover_frame_report())
    assert rect is not None
    vals = [tb for tb in rect.iter(_q("Textbox"))
            if (tb.get("Name") or "").startswith("Cov_ParmVal_")]
    assert vals, "no declared criteria rows emitted"
    for tb in vals:
        weights = [w.text for w in tb.iter(_q("FontWeight"))]
        assert "Bold" in weights, (
            f"{tb.get('Name')} dropped the declared bold: {weights}")


def test_cover_rect_takes_the_declared_frame_span():
    rect = _build_cover_page(_cover_frame_report())
    assert rect is not None
    left, width = _fin(rect, "Left"), _fin(rect, "Width")
    assert abs(left - 0.6875) <= 0.01, f"cover left {left} != declared 0.6875"
    assert abs(width - 6.1875) <= 0.01, (
        f"cover width {width} != declared 6.1875")
    # and the synthesized grid inside must still fit the declared span
    for tb in rect.iter(_q("Textbox")):
        right = _fin(tb, "Left") + _fin(tb, "Width")
        assert right <= width + 0.01, (
            f"{tb.get('Name')} runs {right:.2f}in past the {width:.2f}in "
            "declared frame")


def test_cover_rect_without_a_declared_frame_takes_the_declared_content_span():
    """Superseded measurement. The old assertion pinned the no-drawn-frame
    cover to a SYNTHESIZED 0.35in/6.8in span. Measured against the Oracle
    truth PDFs (three reports, both axes), the cover's objects print at
    their DECLARED coordinates -- so the box that holds them is the span
    those declarations describe, not a template constant. The replacement
    is strictly tighter: the exact declared content bounding box, and every
    child inside it.

    Declared here: leftmost x = 1.70 (B_TITLE), topmost y = 0.58, rightmost
    edge = 6.68 (F_P_REGION at 3.58 + 3.10). The box backs off the placer's
    0.02in left inset on both edges so the leftmost object keeps its
    declared x and the rightmost keeps its declared width: left 1.68, top
    0.58 (no vertical inset), width 6.68 - 1.68 + 0.02 = 5.02."""
    rect = _build_cover_page(_report(with_trailer_band=False))
    assert rect is not None
    assert abs(_fin(rect, "Left") - 1.68) <= 0.01, (
        f"cover left {_fin(rect, 'Left')} != declared 1.70 - 0.02 inset")
    assert abs(_fin(rect, "Top") - 0.58) <= 0.01, (
        f"cover top {_fin(rect, 'Top')} != declared 0.58")
    assert abs(_fin(rect, "Width") - 5.02) <= 0.01, (
        f"cover width {_fin(rect, 'Width')} != declared span 5.02")
    for tb in rect.iter(_q("Textbox")):
        assert _fin(tb, "Left") + _fin(tb, "Width") <= _fin(rect, "Width") + 0.01, (
            f"{tb.get('Name')} runs past the declared span")
