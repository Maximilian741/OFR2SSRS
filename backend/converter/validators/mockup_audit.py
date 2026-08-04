"""Visual-defect gates for the HTML preview.

WHY THIS LIVES IN THE REPO (and not in a throwaway script)
----------------------------------------------------------
The preview is a promise about what will print, so it needs the same
standard of proof as the RDL. For a long time the audit that certified
it lived outside the repo: it was not version-controlled, no test ran
it, and nothing forced any gate to demonstrate that it could actually
detect the defect it claimed to cover.

That is not a hypothetical weakness. A gate here once measured overlap
by selecting elements that carried text, which silently excluded
images -- so an emblem painting straight across a card's wording scored
zero overlaps and the preview was certified clean while it was visibly
broken.

The rule that follows, and the reason ``tests`` mutation-tests every
gate below: A GATE THAT HAS NEVER BEEN SHOWN TO FAIL ON A KNOWN-BROKEN
INPUT IS NOT EVIDENCE. Each defect class named here has a matching test
that deliberately corrupts a real preview and asserts this module
reports it. Add a gate, add its corruption -- otherwise the gate is
decoration and a clean score means nothing.
"""
from __future__ import annotations

import re

__all__ = ["mockup_defects", "defect_counts", "DEFECT_CLASSES"]

#: every class of visual defect these gates claim to detect
DEFECT_CLASSES = (
    "overlap",       # two painted text boxes interpenetrate
    "buried_text",   # text painted underneath an image
    "out_of_bounds",  # content extends past its page container -> clipped
    "unprotected_cell",  # %-width absolute cell with no overflow guard
    "multi_root",    # preview is not a single root -> host lays pages out
    "page_width",    # page sheets disagree on width -> read as documents
    "empty",         # preview carries no content at all
)

_PX_DIV = re.compile(
    r'<div[^>]*style="([^"]*position:\s*absolute[^"]*left:\s*([\d.]+)px'
    r'[^"]*top:\s*([\d.]+)px[^"]*width:\s*([\d.]+)px[^"]*'
    r'(?:height:\s*([\d.]+)px)?[^"]*)"[^>]*>(.*?)</div>', re.S)

# Each preview sheet is its own coordinate plane -- y restarts at 0 on a
# new page, so boxes must only ever be compared against boxes on the
# SAME page. Comparing across the whole document reports pairs that are
# nowhere near each other in print.
_PAGE_SPLIT = re.compile(r'box-shadow:0 4px 14px')

_ABS_IMG = re.compile(r'<img[^>]*style="([^"]*position:\s*absolute[^"]*)"')
_ABS_DIV_STYLE = re.compile(r'<div style="(position:\s*absolute;[^"]*)"')
_CONTAINER = re.compile(r'position:relative;width:(\d+(?:\.\d+)?)px[^"]*">')


def _z_of(style: str) -> float | None:
    m = re.search(r"z-index:\s*(-?[\d.]+)", style)
    return float(m.group(1)) if m else None


def _painted_width(style: str, declared: float, text: str) -> float:
    """Visible extent of a box.

    A nowrap/ellipsis box cannot paint past its glyphs, so a short value
    sitting in a wide clipped box must not be treated as covering the
    whole box -- that produced phantom overlaps for correct layouts.
    """
    if "ellipsis" not in style and "overflow:hidden" not in style:
        return declared
    fs = re.search(r"font-size:\s*([\d.]+)px", style)
    fpx = float(fs.group(1)) if fs else 11.0
    return min(declared, len(text) * fpx * 0.62 + 8)


def _boxes_on(page: str) -> list[dict]:
    out = []
    for m in _PX_DIV.finditer(page):
        style = m.group(1)
        text = re.sub(r"<[^>]+>", "", m.group(6)).strip()
        if not text:
            continue
        declared = float(m.group(4))
        clipped = "ellipsis" in style or "overflow:hidden" in style
        out.append({
            "x": float(m.group(2)), "y": float(m.group(3)),
            "w": _painted_width(style, declared, text),
            "h": float(m.group(5) or 14), "clipped": clipped,
            "z": _z_of(style), "text": text[:40],
        })
    return out


def _images_on(page: str) -> list[dict]:
    out = []
    for m in _ABS_IMG.finditer(page):
        style = m.group(1)

        def _n(prop):
            g = re.search(prop + r":\s*([\d.]+)px", style)
            return float(g.group(1)) if g else None

        x, y, w, h = _n("left"), _n("top"), _n("width"), _n("height")
        if None in (x, y):
            continue
        out.append({"x": x, "y": y, "w": w or 0.0, "h": h or 0.0,
                    "z": _z_of(style)})
    return out


def mockup_defects(html: str) -> list[dict]:
    """Return every visual defect in ``html``; empty list means clean.

    Each entry is ``{"kind": <one of DEFECT_CLASSES>, "detail": str}``.
    """
    defects: list[dict] = []
    if not html or not html.strip():
        return defects

    if len(re.sub(r"<[^>]+>", "", html).strip()) < 40:
        defects.append({"kind": "empty", "detail": "preview has no content"})

    depth = roots = 0
    for m in re.finditer(r"<div\b|</div>", html):
        if m.group(0) == "<div":
            if depth == 0:
                roots += 1
            depth += 1
        else:
            depth -= 1
    if roots != 1:
        defects.append({"kind": "multi_root",
                        "detail": f"{roots} root elements, expected 1"})

    sheets = re.findall(r'class="o2s-page"[^>]*style="([^"]*)"', html)
    widths = {(re.search(r"max-width:\s*([\d.]+)in", s) or [None, ""])[1]
              for s in sheets}
    if len(widths) > 1:
        defects.append({"kind": "page_width",
                        "detail": f"page sheets differ in width: {widths}"})

    for c in re.findall(r'position:absolute[^"]*width:\s*[\d.]+%[^"]*', html):
        if "overflow" not in c and "white-space" not in c:
            defects.append({"kind": "unprotected_cell",
                            "detail": "%-width cell with no overflow guard"})

    for m in _CONTAINER.finditer(html):
        cw = float(m.group(1))
        seg = html[m.end():m.end() + 40000]
        for d in _PX_DIV.finditer(seg):
            x, w = float(d.group(2)), float(d.group(4))
            if x < -2 or x + w > cw + 30:
                defects.append({
                    "kind": "out_of_bounds",
                    "detail": f"box at x={x:.0f} w={w:.0f} exceeds {cw:.0f}px"})

    for page in _PAGE_SPLIT.split(html):
        boxes = _boxes_on(page)
        images = _images_on(page)

        # TEXT UNDER AN IMAGE. This is the class an earlier text-only
        # metric could not see at all. Geometry alone is not the defect --
        # Oracle legitimately lays wording across an emblem -- what makes
        # it a defect is the text losing the stacking contest.
        for t in boxes:
            for im in images:
                ox = min(t["x"] + t["w"], im["x"] + im["w"]) - \
                    max(t["x"], im["x"])
                oy = min(t["y"] + t["h"], im["y"] + im["h"]) - \
                    max(t["y"], im["y"])
                if ox <= 4 or oy < 10:
                    continue
                tz, iz = t["z"], im["z"]
                if tz is None or iz is None or tz <= iz:
                    defects.append({
                        "kind": "buried_text",
                        "detail": f"{t['text']!r} paints under an image "
                                  f"(text z={tz}, image z={iz})"})

        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                a, b = boxes[i], boxes[j]
                ox = min(a["x"] + a["w"], b["x"] + b["w"]) - \
                    max(a["x"], b["x"])
                oy = min(a["y"] + a["h"], b["y"] + b["h"]) - \
                    max(a["y"], b["y"])
                # a clipped box excuses a shallow edge touch; deep
                # interpenetration is a real defect either way
                if ox > 4 and oy >= 10 and not (
                        (a["clipped"] or b["clipped"]) and ox <= 12):
                    defects.append({
                        "kind": "overlap",
                        "detail": f"{a['text']!r} overlaps {b['text']!r} "
                                  f"by {ox:.0f}x{oy:.0f}px"})
    return defects


def defect_counts(html: str) -> dict:
    """``mockup_defects`` folded into ``{class: count}`` for scorecards."""
    counts = {k: 0 for k in DEFECT_CLASSES}
    for d in mockup_defects(html):
        counts[d["kind"]] = counts.get(d["kind"], 0) + 1
    return counts
