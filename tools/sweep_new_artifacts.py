"""Sweep specified Oracle XML artifacts through BOTH fidelity surfaces:
the HTML mockup (page count + overlap heuristics) and the MS-engine RDL
render (pages, blank pages, warnings). Used by the mockup-fidelity loop to
triage newly-hunted artifacts fast.

    python tools/sweep_new_artifacts.py <glob-or-dir> [...]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "tools" / "renderlab"))

from converter import convert  # noqa: E402


def _collect(args):
    out = []
    for a in args:
        p = Path(a)
        if p.is_dir():
            out += sorted(p.glob("*.xml"))
        elif p.is_file():
            out.append(p)
    seen, files = set(), []
    for p in out:
        try:
            head = p.read_bytes()[:4096].decode("utf-8", "replace").lower()
        except OSError:
            continue
        if "<report" not in head or "reportdefinition" in head:
            continue
        key = (p.name.lower(), p.stat().st_size)
        if key in seen:
            continue
        seen.add(key)
        files.append(p)
    return files


def main() -> int:
    files = _collect(sys.argv[1:])
    if not files:
        print("no Oracle XML artifacts found")
        return 2
    try:
        from render import render_rdl, lib_ready
        have_engine = lib_ready()
    except Exception:
        render_rdl, have_engine = None, False
    try:
        import pdfplumber
    except Exception:
        pdfplumber = None

    print(f"{'artifact':40} {'verdict':8} {'mock_pg':>7} {'render':6} "
          f"{'pdf_pg':>6} {'blank':>5}  notes")
    print("-" * 92)
    issues = 0
    for f in files:
        name = f.stem[:40]
        try:
            out = convert(f.read_bytes())
            rdl = out["rdl_xml"]
            verdict = (out.get("preflight") or {}).get("verdict", "?")
            mpages = len(re.findall(r"Page \d+ of \d+", out.get("mockup_html") or ""))
        except Exception as e:
            print(f"{name:40} {'CRASH':8} {'-':>7} {'-':6} {'-':>6} {'-':>5}  "
                  f"{type(e).__name__}: {e}")
            issues += 1
            continue
        rend, npdf, blanks = "-", "-", "-"
        if have_engine and render_rdl:
            import tempfile
            with tempfile.TemporaryDirectory() as td:
                rp = Path(td) / "r.rdl"
                rp.write_text(rdl, encoding="utf-8")
                res = render_rdl(rp, Path(td) / "r.pdf", rows=3)
                if res["ok"]:
                    rend = "ok"
                    if pdfplumber:
                        with pdfplumber.open(res["pdf"]) as pdf:
                            bl = [i + 1 for i, pg in enumerate(pdf.pages)
                                  if len([w for w in pg.extract_words()
                                          if 1.0 < w["top"] / 72]) < 3]
                            npdf, blanks = len(pdf.pages), (bl or "-")
                            if bl:
                                issues += 1
                else:
                    rend = "FAIL"
                    issues += 1
        print(f"{name:40} {verdict:8} {mpages:>7} {rend:6} {str(npdf):>6} "
              f"{str(blanks):>5}")
    print("-" * 92)
    print("ALL CLEAN" if issues == 0 else f"{issues} issue(s)")
    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main())
