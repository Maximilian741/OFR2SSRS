"""HOSTILE DATA-SHAPE MATRIX — render each report at row counts that
break real reports, not just the comfortable one.

    python tools/renderlab/shape_matrix.py <report.xml> [more.xml ...]
    python tools/renderlab/shape_matrix.py --dir folder --shapes 0,1,3,40

Exit code 0 = every report survived every shape.

WHY: everything here was verified at three synthetic rows. Three rows is
the shape that hides bugs. Zero rows is the single most common production
shape (a filter that matches nothing) and exercises a completely
different code path — empty data regions, aggregates over nothing,
NoRows handling. One row hides grouping errors. Many rows expose
pagination, repeat-on-new-page headers and group-footer placement.

A report is FAILED for a shape when the engine refuses it, or when the
PDF comes back with a blank page (measured with the shared strict rule in
blank_measure.py: strip the page furniture the ARTIFACT declares, then any
mark of ink LEFT is content — one is enough, in any script, and a sheet with
none of it is blank).

AT EVERY SHAPE, BY THE SAME RULE. A blank sheet used to be excused at
rows=0 whenever the extracted text carried an English phrase, so the
identical sheet failed at 1/3/25 rows and passed at 0 — the blank column
switched itself off at the shape this tool exists for, and in every script
whose glyphs do not decode. See ``shape_verdict``.
"""
from __future__ import annotations

import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "tools" / "renderlab"))

from converter import convert          # noqa: E402
from render import render_rdl          # noqa: E402
from blank_measure import (            # noqa: E402
    _squash, declared_no_rows_texts, measure_pdf)

DEFAULT_SHAPES = (0, 1, 3, 25)


def _prints_its_declared_notice(texts, rdl_xml) -> bool:
    """True when the render prints the wording the ARTIFACT declares as its
    no-rows notice.

    READ FROM THE REPORT, NOT FROM A WORDLIST. This used to be an English
    literal matched against extracted text, deciding a VERDICT. Two ways that
    was not a measurement: a report whose ``NoRowsMessage`` says anything else
    never matched, and the identical artifact in Greek, Cyrillic, Arabic or
    CJK could not match even when it printed its notice perfectly, because
    ReportViewer embeds those subsets with no ToUnicode CMap and not one glyph
    decodes. ``blank_measure.declared_no_rows_texts`` reads the sentence the
    region declares, in whatever language it is written.

    It now annotates a finding instead of cancelling one — see
    ``shape_verdict``."""
    declared = declared_no_rows_texts(rdl_xml or "")
    if not declared:
        return False
    return any(any(d in _squash(t) for d in declared) for t in texts)


def _strict_blanks(pdf, rdl_xml=None, mode=None) -> tuple:
    """Shared measure (blank_measure.measure_pdf): page furniture comes from
    the ARTIFACT — the RDL's own page-band wording in whatever language it is
    written, plus the lines the document repeats on every sheet — not from a
    hardcoded English wordlist."""
    m = measure_pdf(pdf, rdl_xml=rdl_xml, mode=mode)
    return m["pages"], m["blank"], m["texts"]


def shape_verdict(blanks, prints_notice) -> tuple:
    """(cell label, is_failure) for one rendered shape.

    THE ROW COUNT IS NOT AN ARGUMENT, and that is the whole point. This
    decision used to read "blank sheets, AND the shape is zero rows, AND the
    extracted text contains an English phrase -> not a failure", so one blank
    sheet was a FAILURE at one, three and twenty-five rows and a PASS at zero:
    the same sheet, the same ink, the verdict decided by how many rows the
    harness fed the report. Zero rows is the shape this tool exists for (a
    filter that matches nothing is the commonest production shape), so the
    exemption switched the blank column off exactly where it mattered — and it
    did so on a phrase match no non-Latin render could ever satisfy.

    A blank sheet is a blank sheet at every row count and in every script. The
    report's own declared notice is still REPORTED — it is the difference
    between a report that told the reader nothing came back and one that just
    printed an empty page — but it annotates the finding instead of
    cancelling it."""
    if not blanks:
        return "ok", False
    return ("BLANK+notice" if prints_notice else "BLANK"), True


def main(argv) -> int:
    shapes = list(DEFAULT_SHAPES)
    files, i = [], 0
    while i < len(argv):
        a = argv[i]
        if a == "--dir":
            i += 1
            files += sorted(str(p) for p in
                            pathlib.Path(argv[i]).rglob("*.xml"))
        elif a == "--shapes":
            i += 1
            shapes = [int(x) for x in argv[i].split(",") if x.strip()]
        else:
            files.append(a)
        i += 1
    if not files:
        print(__doc__)
        return 2

    out_dir = pathlib.Path(tempfile.mkdtemp(prefix="shapes_"))
    failures = 0
    empty_tails = []
    print(f"{'report':<30} " + " ".join(f"{('rows=' + str(s)):>12}"
                                        for s in shapes))
    print("-" * (30 + 13 * len(shapes)))
    for f in files:
        p = pathlib.Path(f)
        try:
            rdl = convert(p.read_bytes())["rdl_xml"]
        except Exception as exc:                       # noqa: BLE001
            print(f"{p.stem[:30]:<30} CONVERT-CRASH {type(exc).__name__}")
            failures += 1
            continue
        rp = out_dir / f"{p.stem[:40]}.rdl"
        rp.write_text(rdl, encoding="utf-8")
        cells = []
        for s in shapes:
            try:
                res = render_rdl(rp, out_dir / f"{p.stem[:40]}_{s}.pdf",
                                 rows=s)
            except Exception as exc:                   # noqa: BLE001
                cells.append("EXC")
                failures += 1
                continue
            if not res.get("ok"):
                cells.append("FAIL")
                failures += 1
                continue
            try:
                n, blanks, texts = _strict_blanks(res["pdf"], rdl,
                                                  res.get("mode"))
            except Exception:                          # noqa: BLE001
                cells.append("PDF?")
                failures += 1
                continue
            label, failed = shape_verdict(
                blanks, _prints_its_declared_notice(texts, rdl))
            cells.append(f"{n}p {label}")
            if failed:
                failures += 1
                if label.endswith("+notice"):
                    empty_tails.append((p.stem, s, n))
        print(f"{p.stem[:30]:<30} " + " ".join(f"{c:>12}" for c in cells))

    if empty_tails:
        print(f"\n{len(empty_tails)} shape(s) print the report's OWN declared "
              f"no-rows notice AND a blank sheet. The notice is why the sheet "
              f"is there; it is not a reason to pass it:")
        for stem, shape, n in empty_tails:
            print(f"    {stem}  rows={shape}  ({n} pages)")
    print(f"\nshape failures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
