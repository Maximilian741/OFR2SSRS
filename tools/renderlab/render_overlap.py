"""Painted-over text detector for ENGINE-RENDERED output.

The mockup gates measure the browser preview; the layout auditor measures
RDL geometry. Neither measures what Microsoft's engine actually PRINTS —
and that gap shipped a grant-status grid whose contact block was painted
straight over its column headers while every other rail reported clean.

This rail closes the gap at the last surface there is: the rendered PDF.
Words are extracted with their bounding boxes (PyMuPDF); two words from
DIFFERENT text blocks whose boxes materially intersect mean one piece of
text is printed on top of another — a defect no data shape can excuse.

    from render_overlap import pdf_overlaps, rdl_overlaps
    bad = pdf_overlaps("out.pdf")     # -> [ {page, a, b, area}, ... ]

Tuning notes, learned against real renders:
- The intersection must cover >=30% of the smaller word's box AND at
  least half the shorter word's height. Kerning, italic overhang, tight
  leading, and a section band touching a tiny one-letter word all graze;
  a painted-over word shares the line.
- There is deliberately NO same-block exemption. MuPDF groups text by
  REGION, so two textboxes stamped dead-centre on the same spot -- the
  worst possible defect -- land in one block; an exemption made exactly
  that case invisible (mutation-proofing caught it before it shipped).
- Underlines/rules are not words and never flag.
"""
from __future__ import annotations

from pathlib import Path

__all__ = ["pdf_overlaps", "rdl_overlaps"]

_MIN_COVER = 0.30  # of the smaller word's area


def _pairs(words):
    """Yield materially-overlapping word pairs."""
    # sort by x so the inner scan can stop early
    ws = sorted(words, key=lambda w: w[0])
    for i, a in enumerate(ws):
        ax0, ay0, ax1, ay1 = a[:4]
        for b in ws[i + 1:]:
            bx0, by0, bx1, by1 = b[:4]
            if bx0 >= ax1:          # no later word can overlap a either
                break
            ox = min(ax1, bx1) - max(ax0, bx0)
            oy = min(ay1, by1) - max(ay0, by0)
            if ox <= 0 or oy <= 0:
                continue
            # A graze is not a paint-over. A section band touching the tiny
            # word under it ("P", "-") clears 30% of the tiny word's AREA
            # while overlapping a sliver of its HEIGHT; a real paint-over
            # shares most of the line height. Require both.
            if oy < 0.5 * min(ay1 - ay0, by1 - by0):
                continue
            # A single-character SEPARATOR glyph ("-", "/", "&") sitting a
            # point or two into its snug neighbour is Oracle's own tight
            # label layout rendered faithfully (bisect-verified: a
            # centre-aligned dash between two labels), not painted-over
            # text. Words, not separators, still flag at any depth.
            if ox <= 4 and (a[4] in "-/&|·" or b[4] in "-/&|·"):
                continue
            smaller = min((ax1 - ax0) * (ay1 - ay0),
                          (bx1 - bx0) * (by1 - by0))
            if smaller > 0 and (ox * oy) / smaller >= _MIN_COVER:
                yield a, b, ox * oy


def pdf_overlaps(pdf_path: str | Path, max_per_page: int = 8) -> list[dict]:
    """Return painted-over word pairs found in a rendered PDF."""
    import fitz

    out: list[dict] = []
    with fitz.open(str(pdf_path)) as doc:
        for pno, page in enumerate(doc, start=1):
            n = 0
            for a, b, area in _pairs(page.get_text("words")):
                out.append({"page": pno, "a": a[4], "b": b[4],
                            "area": round(area, 1)})
                n += 1
                if n >= max_per_page:
                    break
    return out


def rdl_overlaps(rdl_xml: str, rows: int = 3) -> dict:
    """Render RDL text through the MS engine and scan the PDF for
    painted-over text. Returns {ok, overlaps, pages}; ok=False means the
    render itself failed (which is its own defect)."""
    import tempfile

    from rdl_preview import render_to_pdf

    with tempfile.TemporaryDirectory() as d:
        pdf = Path(d) / "r.pdf"
        res = render_to_pdf(rdl_xml, pdf, rows=rows)
        if not res["ok"]:
            return {"ok": False, "overlaps": [], "pages": 0,
                    "log": res["log"][-400:]}
        import fitz
        with fitz.open(str(pdf)) as doc:
            pages = len(doc)
        return {"ok": True, "overlaps": pdf_overlaps(pdf), "pages": pages}


if __name__ == "__main__":  # pragma: no cover - manual tool
    import sys

    hits = pdf_overlaps(sys.argv[1])
    for h in hits[:20]:
        print(f"p{h['page']}: {h['a']!r} over {h['b']!r} ({h['area']}pt²)")
    print(f"{len(hits)} painted-over word pair(s)")
    sys.exit(1 if hits else 0)
