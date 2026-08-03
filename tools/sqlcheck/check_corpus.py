"""Validate the SQL this converter emits against a REAL SQL grammar —
and, when configured, against a REAL Oracle instance.

    python tools/sqlcheck/check_corpus.py <report.xml> [more.xml ...]
    python tools/sqlcheck/check_corpus.py --dir path/to/folder
    python tools/sqlcheck/check_corpus.py --live <report.xml>

Exit code 0 = no converter-introduced SQL defects.

WHY: the render harness feeds synthetic rows in BY FIELD NAME, so a query
Oracle would reject still produces a perfect local PDF. This is the only
check that reads the SQL as SQL.

Two backends:

  grammar (default)  sqlglot's Oracle dialect. Judged DIFFERENTIALLY —
                     a generated query is only reported when the report's
                     ORIGINAL query parsed cleanly, so Oracle constructs
                     the grammar cannot model never raise false alarms.

  --live             EXPLAIN PLAN against a real Oracle instance. Proves
                     syntax AND that every table/column/privilege
                     resolves. Point it at the CUSTOMER's schema before go
                     live; nothing is read or written, EXPLAIN PLAN only
                     compiles the statement. Configure with:

                         set O2S_ORACLE_DSN=host:1521/XEPDB1
                         set O2S_ORACLE_USER=reporting_user
                         set O2S_ORACLE_PASSWORD=...

                     and install the driver:  pip install oracledb
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert, _dataset_command_texts   # noqa: E402
from converter.parsers.oracle_xml import parse_oracle_xml  # noqa: E402
from converter.validators import sql_syntax as S        # noqa: E402


def _targets(argv):
    files, live = [], False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--live":
            live = True
        elif a == "--dir":
            i += 1
            files += sorted(str(p) for p in
                            pathlib.Path(argv[i]).rglob("*.xml"))
        else:
            files.append(a)
        i += 1
    return files, live


def main(argv) -> int:
    files, live = _targets(argv)
    if not files:
        print(__doc__)
        return 2
    if not S.grammar_available():
        print("NOTE: no grammar backend (pip install sqlglot) — "
              "grammar checks will be skipped.")
    if live and not S.live_backend_configured():
        print("NOTE: --live requested but O2S_ORACLE_DSN/USER are unset; "
              "falling back to grammar only.")
        live = False

    total = introduced = live_bad = 0
    for f in files:
        p = pathlib.Path(f)
        try:
            raw = p.read_bytes()
            report = parse_oracle_xml(raw)
            rdl = convert(raw)["rdl_xml"]
        except Exception as exc:                        # noqa: BLE001
            print(f"CRASH  {p.stem}: {type(exc).__name__}: {exc}")
            introduced += 1
            continue
        datasets = _dataset_command_texts(rdl)
        total += len(datasets)
        for issue in S.differential_issues(report, datasets):
            introduced += 1
            print(f"BLOCKER  {p.stem}: {issue['message']}")
        if live:
            for name, sql in datasets.items():
                ok, err = S.explain_check(sql)
                if ok is False:
                    live_bad += 1
                    print(f"ORACLE-REJECTED  {p.stem} / {name}: {err}")

    print(f"\ndatasets checked: {total}   "
          f"converter-introduced parse defects: {introduced}"
          + (f"   Oracle-rejected: {live_bad}" if live else ""))
    return 1 if (introduced or live_bad) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
