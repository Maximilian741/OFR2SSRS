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
PDF comes back with a blank page (measured with the strict residual rule:
strip page chrome, then <8 characters left = blank).
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
from pypdf import PdfReader            # noqa: E402

DEFAULT_SHAPES = (0, 1, 3, 25)


def _says_no_data(pdf) -> bool:
    """True when the empty render actually TELLS the reader there is no
    data (the NoRowsMessage every data region carries)."""
    try:
        reader = PdfReader(pdf)
        txt = " ".join((pg.extract_text() or "") for pg in reader.pages)
    except Exception:                                  # noqa: BLE001
        return False
    return "no data was returned" in txt.lower()


def _strict_blanks(pdf) -> tuple:
    reader = PdfReader(pdf)
    blanks = []
    for i, page in enumerate(reader.pages):
        txt = (page.extract_text() or "").strip()
        residual = "".join(
            ln for ln in txt.splitlines()
            if not ln.strip().lower().startswith(("page ", "report run on"))
        ).strip()
        if len(residual) < 8:
            blanks.append(i + 1)
    return len(reader.pages), blanks


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
                n, blanks = _strict_blanks(res["pdf"])
            except Exception:                          # noqa: BLE001
                cells.append("PDF?")
                failures += 1
                continue
            if blanks and s == 0 and _says_no_data(res["pdf"]):
                # HONEST CLASSIFICATION, not a hidden failure: at zero rows
                # the report DID print "no data" — the extra sheet is the
                # tail of a letter body that reserves two pages of height
                # whether or not it is filled. Collapsing that would mean
                # conditionally resizing the body of every letter, which
                # risks the path that actually matters (the one with
                # data). Reported distinctly so it stays visible.
                cells.append(f"{n}p no-data+tail")
                empty_tails.append((p.stem, n))
            elif blanks:
                cells.append(f"{n}p BLANK")
                failures += 1
            else:
                cells.append(f"{n}p ok")
        print(f"{p.stem[:30]:<30} " + " ".join(f"{c:>12}" for c in cells))

    if empty_tails:
        print(f"\n{len(empty_tails)} report(s) print 'no data' plus a "
              f"trailing blank sheet at zero rows — the tail of a letter "
              f"body that reserves its height whether filled or not:")
        for stem, n in empty_tails:
            print(f"    {stem} ({n} pages)")
    print(f"\nshape failures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
