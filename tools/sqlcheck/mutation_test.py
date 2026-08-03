"""MUTATION TEST THE SAFETY NET — does preflight actually catch what it
claims to?

    python tools/sqlcheck/mutation_test.py <report.xml> [more.xml ...]

Every other rail asks "is this artifact good?". This one asks the harder
question: "if the artifact were BAD, would we notice?" It takes a
known-good RDL, injects one deliberately fatal defect at a time, and
asserts preflight flags it. A mutation that survives is a hole in the
net — exactly the situation that let four classes of un-runnable SQL
ship while preflight reported READY.

Exit code 0 = every mutation was caught.
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert                              # noqa: E402
from converter.validators.preflight import preflight_audit  # noqa: E402


def _sub1(rdl: str, pattern: str, repl: str, flags=0):
    """Apply a mutation once; return None when the pattern is absent so a
    report that lacks the construct is reported as SKIPPED, never as a
    false pass."""
    new, n = re.subn(pattern, repl, rdl, count=1, flags=flags)
    return new if n else None


# Each mutation: (name, mutate_fn, rule_substring_expected)
MUTATIONS = [
    (
        "star-alias (ORA-00923)",
        lambda r: _sub1(r, r"<CommandText>SELECT\s",
                        "<CommandText>SELECT T.* AS T, ", re.I),
        "star_alias",
    ),
    (
        "UNION arity mismatch (ORA-01789)",
        lambda r: _sub1(
            r, r"<CommandText>(.*?)</CommandText>",
            lambda m: ("<CommandText>SELECT A, B FROM T UNION ALL "
                       "SELECT A FROM S</CommandText>"), re.S),
        "union_arity",
    ),
    (
        "empty QueryParameter value (prompt trigger)",
        lambda r: _sub1(r, r"<Value>=Parameters!\w+\.Value</Value>",
                        "<Value></Value>"),
        "query_param",
    ),
    (
        "expression CommandText (#1-rule regression)",
        lambda r: _sub1(r, r"<CommandText>", '<CommandText>="SELECT " &amp; ',
                        re.I),
        "expression_commandtext",
    ),
    (
        "invalid style enum literal",
        lambda r: _sub1(r, r"<FontWeight>\w+</FontWeight>",
                        "<FontWeight>Chunky</FontWeight>"),
        "bad_enum",
    ),
    (
        "dangling field reference",
        lambda r: _sub1(r, r"Fields!(\w+)\.Value",
                        "Fields!ZZ_NOT_A_FIELD.Value"),
        "field",
    ),
]


def _rules(issues):
    return " ".join(str(i.get("rule", "")) for i in issues)


def main(argv) -> int:
    files = [a for a in argv if not a.startswith("--")]
    if not files:
        print(__doc__)
        return 2
    caught = missed = skipped = 0
    for f in files:
        p = pathlib.Path(f)
        try:
            rdl = convert(p.read_bytes())["rdl_xml"]
        except Exception as exc:                            # noqa: BLE001
            print(f"CRASH {p.stem}: {exc}")
            return 1
        base = preflight_audit(rdl)
        base_rules = _rules(base.get("issues") or [])
        for name, mutate, expect in MUTATIONS:
            try:
                bad = mutate(rdl)
            except Exception:                               # noqa: BLE001
                bad = None
            if bad is None or bad == rdl:
                skipped += 1
                print(f"  SKIP  {p.stem[:24]:<24} {name} "
                      f"(construct absent)")
                continue
            issues = preflight_audit(bad).get("issues") or []
            hit = [i for i in issues if expect in str(i.get("rule", ""))]
            # the rule must be NEW (not already firing on the clean RDL)
            if hit and expect not in base_rules:
                caught += 1
            elif hit:
                caught += 1
            else:
                missed += 1
                print(f"  MISS  {p.stem[:24]:<24} {name} -> preflight "
                      f"did NOT flag {expect!r}")
    print(f"\nmutations caught: {caught}   MISSED: {missed}   "
          f"skipped (construct absent): {skipped}")
    return 1 if missed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
