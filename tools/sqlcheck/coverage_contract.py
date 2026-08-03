"""CONTENT-COVERAGE CONTRACT — every visible source field either lands in
the output or is explicitly declined. No silent third category.

    python tools/sqlcheck/coverage_contract.py <report.xml> [more.xml ...]
    python tools/sqlcheck/coverage_contract.py --dir path/to/folder
    python tools/sqlcheck/coverage_contract.py --verbose <report.xml>

Exit code 0 = nothing vanished silently.

WHY: content has gone missing quietly more than once — a title segment, a
subtitle line, 21 margin fields, five detail columns. Each was invisible
because no rail asserted that source content REACHES the artifact. A
fidelity *score* can drift down a point and nobody notices; a contract
either holds or fails.

The contract, per visible layout field with a data source:

  ACCOUNTED  its source is referenced by the RDL (a field ref, a
             parameter ref, a lookup/aggregate over it, an image, or a
             formula-dataset column), OR
  DECLINED   preflight/audit says so in plain language (an honest
             limitation the user was told about), OTHERWISE
  SILENT     -> a contract violation.

Hidden fields, drawing primitives and pure-boilerplate text are out of
scope: they carry no data the reader can lose.
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert                                # noqa: E402
from converter.parsers.oracle_xml import parse_oracle_xml    # noqa: E402
# One source of truth: the SAME logic the converter uses to disclose its
# own content loss (backend/converter/validators/coverage.py). A rail that
# reimplements the rule can silently disagree with the product.
from converter.validators.coverage import (                  # noqa: E402
    visible_data_fields as _visible_data_fields_mod,
    is_accounted as _is_accounted_mod,
)


def _visible_data_fields(report):
    return _visible_data_fields_mod(report)


def _accounted(src: str, rdl: str, report) -> bool:
    return _is_accounted_mod(src, rdl, report)


def _declined(src: str, preflight) -> bool:
    """Did we TELL the user this could not be carried across?"""
    blob = " ".join(str(i.get("message", "") or "")
                    for i in (preflight.get("issues") or []))
    blob += " " + str(preflight.get("source_kind_message") or "")
    return src.lower() in blob.lower()


def check(path: pathlib.Path, verbose: bool = False):
    raw = path.read_bytes()
    report = parse_oracle_xml(raw)
    out = convert(raw)
    rdl, preflight = out["rdl_xml"], out["preflight"]
    silent = []
    total = 0
    for name, src in _visible_data_fields(report):
        total += 1
        if _accounted(src, rdl, report) or _declined(src, preflight):
            continue
        silent.append((name, src))
    if verbose:
        print(f"  {path.stem}: {total} visible data fields, "
              f"{len(silent)} silent")
    return total, silent


def main(argv) -> int:
    verbose = "--verbose" in argv
    files, i = [], 0
    args = [a for a in argv if a != "--verbose"]
    while i < len(args):
        if args[i] == "--dir":
            i += 1
            files += sorted(str(p) for p in
                            pathlib.Path(args[i]).rglob("*.xml"))
        else:
            files.append(args[i])
        i += 1
    if not files:
        print(__doc__)
        return 2

    total = violations = 0
    for f in files:
        p = pathlib.Path(f)
        try:
            n, silent = check(p, verbose)
        except Exception as exc:                             # noqa: BLE001
            print(f"CRASH {p.stem}: {type(exc).__name__}: {exc}")
            violations += 1
            continue
        total += n
        for name, src in silent:
            violations += 1
            print(f"SILENT-DROP  {p.stem[:30]:<30} field={name!r} "
                  f"source={src!r} — not in the RDL and not declined")
    print(f"\nvisible data fields checked: {total}   "
          f"silent drops: {violations}")
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
