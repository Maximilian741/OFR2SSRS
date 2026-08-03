"""ONE COMMAND, EVERY RAIL — run the whole verification suite over a set
of reports and print a single scorecard.

    python tools/sqlcheck/run_all_rails.py <report.xml> [more.xml ...]
    python tools/sqlcheck/run_all_rails.py --dir ./reports
    python tools/sqlcheck/run_all_rails.py --dir ./reports --shapes 0,1,3

Exit code 0 = every rail passed.

The rails, and the blind spot each one closes:

  sql-grammar   Is the emitted SQL actually SQL? Nothing else here reads
                the SQL as SQL — the render harness supplies rows by
                field name, so an un-runnable query still makes a
                beautiful PDF. (tools/sqlcheck/check_corpus.py)

  coverage      Did any visible source field vanish without a word? A
                fidelity score can drift; a contract holds or fails.
                (backend/converter/validators/coverage.py)

  mutation      If the artifact were broken, would preflight notice?
                Everything else asks "is this good?" — this asks whether
                the safety net has holes. (mutation_test.py)

  shapes        Does it survive zero rows, one row, and many? Three rows
                is the shape that hides bugs.
                (tools/renderlab/shape_matrix.py)

  smoke-deploy  Does a REAL report server accept it? Only when an SSRS
                instance is configured; otherwise reported as skipped,
                never as a pass. (tools/ssrscheck/smoke_deploy.py)
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _collect(argv):
    files, shapes, i = [], "0,1,3", 0
    while i < len(argv):
        a = argv[i]
        if a == "--dir":
            i += 1
            files += sorted(str(p) for p in
                            pathlib.Path(argv[i]).rglob("*.xml"))
        elif a == "--shapes":
            i += 1
            shapes = argv[i]
        else:
            files.append(a)
        i += 1
    return files, shapes


def _run(label, script, args):
    proc = subprocess.run(
        [sys.executable, str(ROOT / script), *args],
        capture_output=True, text=True)
    tail = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
    summary = tail[-1] if tail else (proc.stderr or "").strip()[:160]
    return proc.returncode, summary, proc.stdout or ""


def main(argv) -> int:
    files, shapes = _collect(argv)
    if not files:
        print(__doc__)
        return 2

    rails = [
        ("sql-grammar", "tools/sqlcheck/check_corpus.py", files),
        ("coverage", "tools/sqlcheck/coverage_contract.py", files),
        ("mutation", "tools/sqlcheck/mutation_test.py", files[:4]),
        ("shapes", "tools/renderlab/shape_matrix.py",
         files + ["--shapes", shapes]),
        ("smoke-deploy", "tools/ssrscheck/smoke_deploy.py", files),
    ]

    failed = []
    details = {}
    print(f"running {len(rails)} rails over {len(files)} report(s)\n")
    for label, script, args in rails:
        code, summary, full = _run(label, script, args)
        details[label] = full
        if label == "smoke-deploy" and code == 2:
            status = "SKIP"          # no server configured — not a pass
        elif code == 0:
            status = "PASS"
        else:
            status = "FAIL"
            failed.append(label)
        print(f"  {status:<5} {label:<13} {summary[:96]}")

    if failed:
        print("\n--- detail for failing rails ---")
        for label in failed:
            print(f"\n===== {label} =====")
            print(details[label][-4000:])
    print(f"\nrails failed: {len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
