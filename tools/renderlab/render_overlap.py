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

__all__ = ["pdf_overlaps", "rdl_overlaps", "stroke_through_text"]

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


def _buried_text(page, max_hits: int = 8):
    """Text spans painted BEFORE an opaque fill that covers them — the
    burial class the word-pair scan is structurally blind to (an
    adversarial sweep proved a requisition grid rendered EMPTY while this
    gate certified paint=0: each row's text was buried under the NEXT
    row frame's opaque white fill). A background legitimately paints
    BEFORE its text, so only HIGHER-seqno fills count."""
    fills = []
    for dr in page.get_drawings():
        f = dr.get("fill")
        r = dr.get("rect")
        if f is None or r is None or dr.get("fill_opacity", 1) in (0,):
            continue
        if r.width < 4 or r.height < 4:
            continue
        fills.append((dr.get("seqno", 0), r))
    if not fills:
        return
    hits = 0
    for span in page.get_texttrace():
        if hits >= max_hits:
            return
        s_seq = span.get("seqno", 0)
        bbox = span.get("bbox")
        if not bbox:
            continue
        import fitz
        sb = fitz.Rect(bbox)
        if sb.is_empty or sb.width <= 0 or sb.height <= 0:
            continue
        text = "".join(chr(c[0]) for c in span.get("chars", []))[:40]
        if not text.strip():
            continue
        for f_seq, fr in fills:
            if f_seq <= s_seq:
                continue
            inter = sb & fr
            if inter.is_empty:
                continue
            if (inter.width * inter.height) / (sb.width * sb.height) >= 0.9:
                yield {"buried": text.strip(), "seq": s_seq,
                       "fill_seq": f_seq}
                hits += 1
                break


#
# ---------------------------------------------------------------------------
# RULES THROUGH GLYPHS
#
# The word-pair scan above is text-vs-text by construction, so a DRAWN rule
# cutting through printed characters is invisible to it: a voucher grid came
# back paint=0 while every caption in it had the neighbouring value box's
# border sliced through the last letters (crop-verified). Underlines, cell
# borders and table rules are legitimate and common, so the discriminator is
# purely geometric + paint-order:
#
#   * the rule must be VISIBLE — a stroke an opaque fill covers afterwards
#     prints nothing (measured: a section band's own frame edge is stamped
#     first, then the band fill paints over it, then the caption; the pixels
#     show no rule at all);
#   * a HORIZONTAL rule must land inside the glyph CORE — below the cap line
#     and above the baseline. Underlines sit at or under the baseline and
#     overlines above the cap line, so neither can ever flag;
#   * a VERTICAL rule must land strictly INSIDE one character's ink box with
#     clearance on both sides, so a cell border abutting the text box edge,
#     or grazing the side bearing of the first/last glyph, does not flag;
#   * measurement is per CHARACTER (rawdict char boxes), never per word box:
#     a word box spans the inter-letter gaps, and a rule in a gap prints
#     between letters, not through them.
# ---------------------------------------------------------------------------

_RULE_MAX_THICK = 3.0     # pt; a filled band thicker than this is a fill
_RULE_AXIS_TOL = 0.6      # pt; deviation still counted as axis-aligned
_GLYPH_SIDE_CLEAR = 0.4   # pt; a vertical rule must be this far inside


def _drawn_rules(page):
    """Every axis-aligned rule the page PAINTS: stroked lines, the four
    edges of a stroked rect, and thin opaque filled bands (the engine draws
    hairline dividers as filled slivers). Each entry is
    ``(x0, y0, x1, y1, seqno)``."""
    segs = []
    try:
        drawings = page.get_drawings()
    except Exception:  # noqa: BLE001 - a scan must never sink on odd content
        return segs
    for dr in drawings:
        seq = dr.get("seqno", 0)
        stroked = (dr.get("color") is not None
                   and dr.get("stroke_opacity", 1) not in (0,))
        filled = (dr.get("fill") is not None
                  and dr.get("fill_opacity", 1) not in (0,))
        for item in dr.get("items", []):
            op = item[0]
            if op == "l" and stroked:
                p1, p2 = item[1], item[2]
                segs.append((p1.x, p1.y, p2.x, p2.y, seq))
            elif op == "re":
                r = item[1]
                if stroked:
                    segs += [(r.x0, r.y0, r.x1, r.y0, seq),
                             (r.x0, r.y1, r.x1, r.y1, seq),
                             (r.x0, r.y0, r.x0, r.y1, seq),
                             (r.x1, r.y0, r.x1, r.y1, seq)]
                elif filled:
                    if r.width <= _RULE_MAX_THICK < r.height:
                        x = (r.x0 + r.x1) / 2.0
                        segs.append((x, r.y0, x, r.y1, seq))
                    elif r.height <= _RULE_MAX_THICK < r.width:
                        y = (r.y0 + r.y1) / 2.0
                        segs.append((r.x0, y, r.x1, y, seq))
    return [s for s in segs
            if abs(s[3] - s[1]) <= _RULE_AXIS_TOL
            or abs(s[2] - s[0]) <= _RULE_AXIS_TOL]


def _opaque_fills(page):
    """(seqno, rect) for every opaque fill big enough to hide a rule."""
    out = []
    try:
        drawings = page.get_drawings()
    except Exception:  # noqa: BLE001
        return out
    for dr in drawings:
        f = dr.get("fill")
        r = dr.get("rect")
        if f is None or r is None or dr.get("fill_opacity", 1) in (0,):
            continue
        if r.width < 4 or r.height < 4:
            continue
        out.append((dr.get("seqno", 0), r))
    return out


def _rule_is_covered(seg, fills) -> bool:
    """True when a LATER opaque fill paints over (essentially all of) the
    rule — it never reaches the paper, so it cuts nothing."""
    import fitz

    x0, y0, x1, y1, seq = seg
    sr = fitz.Rect(min(x0, x1) - 0.1, min(y0, y1) - 0.1,
                   max(x0, x1) + 0.1, max(y0, y1) + 0.1)
    span = max(sr.width, sr.height)
    if span <= 0:
        return False
    for f_seq, fr in fills:
        if f_seq <= seq:
            continue
        inter = sr & fr
        if inter.is_empty:
            continue
        if max(inter.width, inter.height) >= 0.9 * span:
            return True
    return False


def _rule_through_glyphs(page, max_hits: int = 8):
    """Yield {text, axis, at} for each visible rule cutting glyph ink."""
    segs = _drawn_rules(page)
    if not segs:
        return
    fills = _opaque_fills(page)
    segs = [s for s in segs if not _rule_is_covered(s, fills)]
    if not segs:
        return
    hits = 0
    seen = set()
    try:
        raw = page.get_text("rawdict")
    except Exception:  # noqa: BLE001
        return
    for blk in raw.get("blocks", []):
        for line in blk.get("lines", []):
            for sp in line.get("spans", []):
                size = float(sp.get("size", 0) or 0)
                bbox = sp.get("bbox")
                if size <= 0 or not bbox:
                    continue
                # Baseline from the span's own font metrics: MuPDF puts the
                # span box at [ascender*size above, descender*size below]
                # the baseline (descender is negative).
                baseline = bbox[3] + float(sp.get("descender", -0.2)) * size
                core_top = baseline - 0.70 * size     # ~cap height
                core_bot = baseline - 0.08 * size     # just above baseline
                for ch in sp.get("chars", []):
                    if hits >= max_hits:
                        return
                    text = ch.get("c", "")
                    if not text.strip():
                        continue
                    cb = ch.get("bbox")
                    if not cb or (cb[2] - cb[0]) <= 2 * _GLYPH_SIDE_CLEAR:
                        continue
                    cx0, cx1 = cb[0], cb[2]
                    for (x0, y0, x1, y1, _s) in segs:
                        if abs(y1 - y0) <= _RULE_AXIS_TOL:
                            at = (y0 + y1) / 2.0
                            if not (core_top < at < core_bot):
                                continue
                            if (min(x0, x1) < cx1 - _GLYPH_SIDE_CLEAR
                                    and max(x0, x1) > cx0 + _GLYPH_SIDE_CLEAR):
                                key = ("H", round(at, 1), round(bbox[0], 1),
                                       round(bbox[1], 1))
                                axis = "H"
                            else:
                                continue
                        elif abs(x1 - x0) <= _RULE_AXIS_TOL:
                            at = (x0 + x1) / 2.0
                            if not (cx0 + _GLYPH_SIDE_CLEAR < at
                                    < cx1 - _GLYPH_SIDE_CLEAR):
                                continue
                            if not (min(y0, y1) < core_bot - 0.3
                                    and max(y0, y1) > core_top + 0.3):
                                continue
                            key = ("V", round(at, 1), round(bbox[1], 1))
                            axis = "V"
                        else:
                            continue
                        if key in seen:
                            break
                        seen.add(key)
                        yield {"text": "".join(
                            c.get("c", "") for c in sp.get("chars", [])
                        ).strip()[:40] or text,
                            "axis": axis, "at": round(at, 2)}
                        hits += 1
                        break


def stroke_through_text(pdf_path: str | Path,
                        max_per_page: int = 8) -> list[dict]:
    """Standalone view of the rule-through-glyph class (same hits the full
    ``pdf_overlaps`` gate reports, without the text-vs-text pairs)."""
    import fitz

    out: list[dict] = []
    with fitz.open(str(pdf_path)) as doc:
        for pno, page in enumerate(doc, start=1):
            for hit in _rule_through_glyphs(page, max_hits=max_per_page):
                out.append({"page": pno, "text": hit["text"],
                            "axis": hit["axis"], "at": hit["at"]})
    return out


def pdf_overlaps(pdf_path: str | Path, max_per_page: int = 8) -> list[dict]:
    """Return painted-over word pairs, buried-under-fill text AND drawn
    rules cutting through glyph ink, found in a rendered PDF."""
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
            for hit in _buried_text(page, max_hits=max_per_page):
                out.append({"page": pno, "a": hit["buried"],
                            "b": "<opaque fill painted after>",
                            "area": -1.0})
            for hit in _rule_through_glyphs(page, max_hits=max_per_page):
                out.append({"page": pno, "a": hit["text"],
                            "b": f"<{hit['axis']} rule through glyphs "
                                 f"@{hit['at']}>",
                            "area": -2.0})
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
