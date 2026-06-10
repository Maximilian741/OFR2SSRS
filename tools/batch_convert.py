"""Batch-convert a folder of Oracle Reports XMLs from the command line.

    python tools/batch_convert.py <xml-file-or-dir> [...more] -o out_dir
        [--render] [--target-db oracle|sqlserver]

Writes every generated RDL plus ASSESSMENT.html / assessment.json (the
Migration Assessment) into out_dir, prints the verdict table, and exits
non-zero if any report needs manual attention. ``--render`` additionally
verifies each RDL through Microsoft's local rendering engine (run
``python tools/renderlab/fetch_reportviewer.py`` once to enable).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter.batch import (  # noqa: E402
    batch_convert, build_assessment_html, build_batch_zip)


def _collect(paths):
    out = []
    for a in paths:
        p = Path(a)
        if p.is_dir():
            out.extend(sorted(p.rglob("*.xml")))
        elif p.is_file():
            out.append(p)
    items, seen = [], set()
    for p in out:
        try:
            blob = p.read_bytes()
        except OSError:
            continue
        head = blob[:4096].decode("utf-8", "replace").lower()
        if "<report" not in head or "reportdefinition" in head:
            continue
        # Dedupe identical copies living in multiple folders.
        key = (p.name.lower(), len(blob))
        if key in seen:
            continue
        seen.add(key)
        items.append((p.name, blob))
    return items


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("-o", "--out", default="o2s_out")
    ap.add_argument("--render", action="store_true",
                    help="verify each RDL through the local MS engine")
    ap.add_argument("--target-db", default="oracle",
                    choices=("oracle", "sqlserver"))
    args = ap.parse_args()

    items = _collect(args.inputs)
    if not items:
        print("No Oracle Reports XML files found.")
        return 2
    print(f"Converting {len(items)} report(s)...")
    batch = batch_convert(items, target_db=args.target_db,
                          render=args.render)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "ASSESSMENT.html").write_text(
        build_assessment_html(batch), encoding="utf-8")
    (out_dir / "migration_pack.zip").write_bytes(build_batch_zip(batch))
    rdl_dir = out_dir / "rdl"
    rdl_dir.mkdir(exist_ok=True)
    for r in batch["results"]:
        if r.get("rdl_xml"):
            (rdl_dir / (r["name"] + ".rdl")).write_text(
                r["rdl_xml"], encoding="utf-8")

    print(f"\n{'report':30} {'effort':12} {'check':7} {'fidelity':>8}"
          + ("  render" if batch.get("rendered") else ""))
    print("-" * 70)
    worst = 0
    for r in batch["results"]:
        fid = r.get("fidelity")
        fid_s = f"{fid:.2f}" if isinstance(fid, (int, float)) else "-"
        line = (f"{r['name'][:30]:30} {r.get('effort', '?'):12} "
                f"{str(r.get('verdict', '?')):7} {fid_s:>8}")
        if batch.get("rendered"):
            line += "  " + ("ok" if r.get("render_ok") else "FAIL")
        print(line)
        worst = max(worst, {"automatic": 0, "light-touch": 0,
                            "assisted": 1, "manual": 1}.get(r.get("effort"), 1))
    for name in batch.get("locked") or []:
        print(f"{name[:30]:30} {'(over batch limit)':12}")
    print(f"\nAssessment: {out_dir / 'ASSESSMENT.html'}")
    print(f"Pack:       {out_dir / 'migration_pack.zip'}")
    return worst


if __name__ == "__main__":
    sys.exit(main())
