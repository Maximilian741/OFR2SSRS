"""Corpus verifier: convert Oracle XMLs with the project pipeline, render
each generated RDL through Microsoft's LocalReport engine, and measure the
PDFs. Prints a verdict table. Inputs come from the command line — nothing
client-specific lives in this file.

    python tools/renderlab/run_corpus.py <xml-file-or-dir> [...more] \
        [--out DIR] [--rows N]

Measurements per report:
  rendered      engine produced a PDF (the ultimate upload-will-render proof)
  pages         page count
  blank         interior pages with no content text (blank-page bug class)
  warns         engine warnings (overlap/overflow show up here)
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(HERE))

from render import render_rdl, lib_ready  # noqa: E402


def _is_oracle_xml(p: Path) -> bool:
    try:
        head = p.read_bytes()[:4096].decode("utf-8", "replace").lower()
    except OSError:
        return False
    return "<report" in head and "reportdefinition" not in head


def _collect(args: list[str]) -> list[Path]:
    out: list[Path] = []
    for a in args:
        p = Path(a)
        if p.is_dir():
            out.extend(sorted(x for x in p.rglob("*.xml") if _is_oracle_xml(x)))
        elif p.is_file():
            out.append(p)
    return out


def _measure_pdf(pdf_path: str, rdl_xml: str = None, mode: str = None) -> dict:
    """Shared strict blank measure (blank_measure.measure_pdf).

    Page furniture is derived from the ARTIFACT — the wording the RDL declares
    in its PageHeader/PageFooter, in whatever language, the strip that band
    RESERVES on the sheet, and the lines the document repeats on every page —
    and a page is blank when NO mark of content ink is left. This used to
    strip two hardcoded English literals and call a page with under 8
    residual characters blank; a judge proved the literals blind (a blank
    sheet behind an intact PageHeader read as inked) and two more proved the
    count blind (a sheet printing a short value read blank, and the same
    sheet got opposite verdicts in different scripts)."""
    from blank_measure import measure_pdf
    m = measure_pdf(pdf_path, rdl_xml=rdl_xml, mode=mode)
    return {"pages": m["pages"], "blank": m["blank"],
            "placeholder_only": m["placeholder_only"]}


def main() -> int:
    flags = {"--out": None, "--rows": "3"}
    args = []
    it = iter(sys.argv[1:])
    for a in it:
        if a in flags:
            flags[a] = next(it, None)
        else:
            args.append(a)
    if not args:
        print(__doc__)
        return 2
    if not lib_ready():
        print("ReportViewer DLLs missing — run fetch_reportviewer.py first")
        return 1

    out_dir = Path(flags["--out"] or tempfile.mkdtemp(prefix="renderlab_"))
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = int(flags["--rows"] or 3)

    from converter import convert  # noqa: E402  (after sys.path insert)

    files = _collect(args)
    print(f"{len(files)} report(s); output -> {out_dir}\n")
    print(f"{'report':28} {'convert':8} {'render':7} {'pages':>5} "
          f"{'blank':>6}  notes")
    print("-" * 84)
    failures = 0
    for f in files:
        name = f.stem[:28]
        try:
            data = convert(f.read_bytes())
            rdl = data["rdl_xml"]
            verdict = (data.get("preflight") or {}).get("verdict", "?")
        except Exception as e:  # noqa: BLE001
            print(f"{name:28} {'CRASH':8} {'-':7} {'-':>5} {'-':>6}  {type(e).__name__}: {e}")
            failures += 1
            continue
        rdl_path = out_dir / (f.stem + ".rdl")
        rdl_path.write_text(rdl, encoding="utf-8")
        pdf_path = out_dir / (f.stem + ".pdf")
        res = render_rdl(rdl_path, pdf_path, rows=rows)
        warns = sum(1 for ln in res["log"].splitlines() if ln.startswith("WARN"))
        if not res["ok"]:
            reason = next((ln for ln in res["log"].splitlines()
                           if "RENDER FAIL" in ln), res["log"][-200:])
            print(f"{name:28} {verdict:8} {'FAIL':7} {'-':>5} {'-':>6}  {reason[:90]}")
            failures += 1
            continue
        m = _measure_pdf(res["pdf"], rdl, res.get("mode"))
        blank_s = ",".join(map(str, m["blank"])) or "-"
        note = f"{warns} warn(s)" if warns else ""
        if m["blank"]:
            failures += 1
            note = (note + " BLANK PAGES").strip()
        print(f"{name:28} {verdict:8} {'ok':7} {m['pages']:>5} {blank_s:>6}  {note}")
    print("-" * 84)
    print(("ALL CLEAN" if failures == 0 else f"{failures} ISSUE(S)") +
          f" — artifacts in {out_dir}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
