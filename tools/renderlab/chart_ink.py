"""PLOTTED-INK measure for engine-rendered charts.

A generated <Chart> can pass every structural gate -- XSD-valid, the right
Type and Subtype, the right value expression -- and still print an EMPTY AXIS
FRAME: title, axes, gridlines and tick labels with nothing plotted between
them. That is exactly what the render harness produced for every chart before
the staticizer learned to keep simple aggregate <Y> values, and no gate in the
suite could tell the two apart, because both are "a chart that rendered".

This measure separates them, from the rendered PDF itself.

  * An SSRS chart is rasterised into the PDF as ONE image placed at exactly
    the Width x Height its <Chart> declares, so chart images can be told from
    the rest by that box alone (measured: a 5.44in x 3.06in chart against a
    1.41in x 0.38in letterhead image in the same render). Charts print in the
    order the body declares them, so the k-th chart-sized image down the
    document is the k-th declared chart.
  * Axes, gridlines, tick labels and titles are BLACK or GRAY -- max(r,g,b)
    equals min(r,g,b) up to antialiasing. Plotted marks (bars, slices, areas,
    the line) carry the palette's COLOUR. Chromatic pixels are plotted ink.

Two things are measured about that ink, because one is not enough:

  SPREAD -- the fraction of image COLUMNS holding at least one chromatic
    pixel. The AMOUNT of colour does not separate plotted from empty: a thin
    line series covers 0.08% of its image while an emptied chart that still
    draws a LEGEND covers 1.2% (both measured). Spread does: plotted marks run
    across the plot area, a legend swatch stands in one narrow column.

  COLOURS -- the number of dominant chromatic colours (quantized to 8 levels
    per channel, each holding >=2% of the chromatic pixels), which must reach
    the number of ChartSeries the chart declares. Spread alone has one
    measured blind spot: emptied of its values, a PERCENT-STACKED AREA chart
    is painted by the engine as one solid full-width band, indistinguishable
    by spread from the real thing (0.73 coverage either way). Its colours give
    it away -- two declared series plot two colours, the empty band has one.

Measured over fixture charts covering every mapped graph family (column plain
/ stacked / percent, horizontal bar plain and stacked, line, area plain /
stacked / percent, pie, ring, and the neutral fallback), rendered through the
MS engine, against the same charts with their <Y> values emptied:

    plotted      coverage 0.483 .. 0.720   colours == declared series
    emptied      coverage 0.000 .. 0.053   except the percent-stacked area
                                           band (0.730) -- 1 colour, 2 series

so the spread gate sits at 0.25: about half the weakest real chart and about
five times the strongest empty one.

LIMITS, stated rather than hidden. (1) This measures COLOURED plotted ink, so
a graph rendered in an all-gray palette would read as empty; every stock SSRS
palette is chromatic, and the custom palette is built from the colours the
Oracle <Series color=..> declarations name. (2) A series whose values are all
zero plots no colour of its own; the render harness synthesises non-zero rows.

    from chart_ink import chart_plots, unplotted_charts
    bad = unplotted_charts("out.pdf", rdl_xml)   # -> [] when every chart plots
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

__all__ = ["declared_chart_boxes", "chart_plots", "unplotted_charts",
           "MIN_COLUMN_COVERAGE"]

_RDL_NS = ("{http://schemas.microsoft.com/sqlserver/reporting/2008/01/"
           "reportdefinition}")

#: A pixel counts as plotted ink when its channels differ by this much.
#: Antialiased black text and gray gridlines have a spread of 0.
_CHROMA = 32

#: A quantized colour is DOMINANT at this share of the chromatic pixels
#: (antialiasing fringes never reach it).
_DOMINANT = 0.02

#: Fraction of image columns that must carry plotted ink. See the module
#: docstring for the measurement the number comes from.
MIN_COLUMN_COVERAGE = 0.25

#: Inches of slack when matching a placed image to a declared chart box.
_BOX_TOL = 0.02


def _inches(text: str) -> float:
    try:
        return float((text or "0").strip().lower().replace("in", ""))
    except ValueError:
        return 0.0


def declared_chart_boxes(rdl_xml: str) -> list[dict]:
    """Every <Chart> the RDL declares, in document (print) order:
    ``{"chart", "width_in", "height_in", "series"}``."""
    try:
        root = ET.fromstring(rdl_xml)
    except ET.ParseError:
        return []
    out: list[dict] = []
    for ch in root.iter(_RDL_NS + "Chart"):
        out.append({
            "chart": ch.get("Name") or "",
            "width_in": _inches(ch.findtext(_RDL_NS + "Width")),
            "height_in": _inches(ch.findtext(_RDL_NS + "Height")),
            "series": max(1, len(list(ch.iter(_RDL_NS + "ChartSeries")))),
        })
    return out


def _ink(doc, xref: int) -> tuple[float, int]:
    """``(column coverage, dominant chromatic colours)`` of one image."""
    import fitz

    pix = fitz.Pixmap(doc, xref)
    if pix.n - pix.alpha > 3:            # CMYK / separation -> RGB
        pix = fitz.Pixmap(fitz.csRGB, pix)
    if pix.alpha:
        pix = fitz.Pixmap(pix, 0)
    if pix.n < 3 or not pix.width:       # a grayscale image plots nothing
        return (0.0, 0)
    s, n, w, h, stride = pix.samples, pix.n, pix.width, pix.height, pix.stride
    cols: set[int] = set()
    hues: Counter = Counter()
    for y in range(h):
        row = s[y * stride:y * stride + w * n]
        for x, (r, g, b) in enumerate(zip(row[0::n], row[1::n], row[2::n])):
            if max(r, g, b) - min(r, g, b) >= _CHROMA:
                cols.add(x)
                hues[(r >> 5, g >> 5, b >> 5)] += 1
    total = sum(hues.values())
    dominant = sum(1 for c in hues.values() if c / total >= _DOMINANT) \
        if total else 0
    return (len(cols) / w, dominant)


def chart_plots(pdf_path, rdl_xml: str) -> list[dict]:
    """One record per <Chart> the RDL declares, in declaration order:
    ``{"chart", "width_in", "height_in", "series", "page", "coverage",
    "colours", "plotted", "reason"}``.

    ``plotted`` is False -- with ``reason`` naming which measure said so --
    when the chart printed no image at its declared box, printed no spread of
    plotted ink, or printed fewer colours than it declares series.
    """
    import fitz

    boxes = declared_chart_boxes(rdl_xml)
    if not boxes:
        return []
    wanted = {(round(b["width_in"], 2), round(b["height_in"], 2))
              for b in boxes}

    def _is_chart_box(w: float, h: float) -> bool:
        return any(abs(w - cw) <= _BOX_TOL and abs(h - ch) <= _BOX_TOL
                   for cw, ch in wanted)

    out: list[dict] = []
    with fitz.open(str(Path(pdf_path))) as doc:
        placed = []
        for pno in range(doc.page_count):
            for info in doc[pno].get_image_info(xrefs=True):
                x0, y0, x1, y1 = info["bbox"]
                w, h = (x1 - x0) / 72.0, (y1 - y0) / 72.0
                if _is_chart_box(w, h):
                    placed.append((pno + 1, y0, x0, info["xref"], w, h))
        # Print order: down the document, then down the page.
        placed.sort(key=lambda p: (p[0], round(p[1], 1), p[2]))

        for i, box in enumerate(boxes):
            rec = dict(box, page=None, coverage=0.0, colours=0,
                       plotted=False, reason="no image at the declared box")
            if i >= len(placed):
                out.append(rec)
                continue
            pno, _y, _x, xref, w, h = placed[i]
            if abs(w - box["width_in"]) > _BOX_TOL or \
                    abs(h - box["height_in"]) > _BOX_TOL:
                rec["reason"] = (
                    "the image printed in this chart's place is %.2fin x "
                    "%.2fin, not its declared box" % (w, h))
                out.append(rec)
                continue
            rec["page"] = pno
            rec["coverage"], rec["colours"] = _ink(doc, xref)
            if rec["coverage"] < MIN_COLUMN_COVERAGE:
                rec["reason"] = ("plotted ink spans %.1f%% of the chart's "
                                 "width (an empty axis frame)"
                                 % (100 * rec["coverage"]))
            elif rec["colours"] < box["series"]:
                rec["reason"] = ("%d plotted colour(s) for %d declared series"
                                 % (rec["colours"], box["series"]))
            else:
                rec["plotted"], rec["reason"] = True, ""
            out.append(rec)
    return out


def unplotted_charts(pdf_path, rdl_xml: str) -> list[dict]:
    """Every declared chart that did NOT print plotted data (empty list = all
    charts plot). A chart missing from the render is reported too."""
    return [r for r in chart_plots(pdf_path, rdl_xml) if not r["plotted"]]


if __name__ == "__main__":  # pragma: no cover - manual tool
    import sys

    rdl = Path(sys.argv[2]).read_text(encoding="utf-8")
    for rec in chart_plots(sys.argv[1], rdl):
        print("%-10s %5.2fin x %5.2fin series=%d page=%-4s cover=%.4f "
              "colours=%d  %s" % (
                  rec["chart"], rec["width_in"], rec["height_in"],
                  rec["series"], rec["page"], rec["coverage"], rec["colours"],
                  "PLOTTED" if rec["plotted"] else "EMPTY: " + rec["reason"]))
