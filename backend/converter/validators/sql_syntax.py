"""Real-grammar SQL validation for the queries this converter emits.

WHY THIS EXISTS
---------------
Every other check in this repo reasons about the RDL. None of them ever
asked whether the SQL we generate is SQL Oracle would accept. A render
harness feeds synthetic rows in by field name, so a query the server
would reject with ORA-01789 still produces a beautiful local PDF. Four
classes of un-runnable SQL shipped that way to production — a report that
"rendered perfectly" locally and died on deploy.

THE DIFFERENTIAL RULE (the load-bearing idea)
---------------------------------------------
A generated query that fails to parse is only OUR defect when the
ORIGINAL source query parsed cleanly. Oracle has constructs no
third-party grammar fully models (legacy ``(+)`` outer joins in
non-equality comparisons, ``KEEP (DENSE_RANK ...)``, package calls,
optimizer hints). Flagging those would bury a real finding under noise
from valid customer SQL. Comparing against the source makes the signal
exact: we only ever report a query WE broke.

BACKENDS
--------
``sqlglot`` (optional import) supplies the Oracle grammar. When it is not
installed every check degrades to "no opinion" — never to a false alarm.

An optional LIVE backend validates against a real Oracle instance via
``EXPLAIN PLAN`` (syntax *and* object resolution — the strongest check
available, and the one to run against a customer's own schema before go
live). It is opt-in through environment variables and is never contacted
unless configured:

    O2S_ORACLE_DSN   e.g. host:1521/XEPDB1
    O2S_ORACLE_USER
    O2S_ORACLE_PASSWORD

See tools/sqlcheck/README.md.
"""
from __future__ import annotations

import os
import re
from typing import Dict, List, Optional, Tuple

try:                                    # optional: real Oracle grammar
    import sqlglot as _sqlglot
except Exception:                       # noqa: BLE001 - absence is not failure
    _sqlglot = None


def grammar_available() -> bool:
    """True when a real SQL grammar backend is importable."""
    return _sqlglot is not None


# A bind variable is not part of Oracle's *grammar* — it is a placeholder
# the driver substitutes. Swap binds for string literals so the parser
# judges STRUCTURE rather than tripping over ':P_FOO'.
_BIND_RE = re.compile(r"[:@]([A-Za-z_]\w*)")


def _probe_text(sql: str) -> str:
    return _BIND_RE.sub(r"'\1'", sql or "")


_COMMENT_RE = re.compile(r"--[^\n]*|/\*.*?\*/", re.S)


def _has_executable_sql(sql: str) -> bool:
    """True when the text contains an actual statement, not just comments.

    A comment-only CommandText is a deliberate placeholder (a report whose
    dataset read from a non-relational pluggable source), not a query we
    got wrong."""
    return bool(_COMMENT_RE.sub(" ", sql or "").strip())


def parse_check(sql: str, dialect: str = "oracle") -> Tuple[bool, str]:
    """``(parses_ok, error_message)``. Returns ``(True, "")`` when no
    grammar backend is installed or the text is empty — this validator
    never invents a failure it cannot substantiate."""
    if _sqlglot is None or not (sql or "").strip():
        return True, ""
    try:
        _sqlglot.parse_one(_probe_text(sql), dialect=dialect)
        return True, ""
    except Exception as exc:            # noqa: BLE001 - any parse error
        return False, " ".join(str(exc).split())[:300]


def source_query_map(report) -> Dict[str, str]:
    """``{UPPER dataset name: original Oracle SQL}`` from the parsed
    report, so a generated query can be judged against its own source."""
    out: Dict[str, str] = {}
    for q in (getattr(report, "queries", None) or []):
        name = (getattr(q, "name", "") or "").strip()
        if name:
            out[name.upper()] = getattr(q, "sql", "") or ""
    return out


def differential_issues(report, datasets: Dict[str, str],
                        dialect: str = "oracle") -> List[Dict[str, str]]:
    """Issues for queries the CONVERTER broke.

    ``datasets`` is ``{dataset name: generated CommandText}``. A dataset
    is reported only when its generated text fails to parse AND its
    source counterpart parsed cleanly. A dataset with no source
    counterpart (the synthetic formula-resolution stub) is judged on its
    own: it is entirely our construction, so a parse failure there is
    unambiguously ours.
    """
    if _sqlglot is None:
        return []
    sources = source_query_map(report)
    issues: List[Dict[str, str]] = []
    for name, sql in (datasets or {}).items():
        # A DELIBERATE placeholder is not a broken query. Some Oracle
        # reports read from a non-relational (pluggable text/CSV/XML)
        # source; we emit a comment-only CommandText telling the user how
        # to point the dataset at real data. There is no SQL to be wrong.
        if not _has_executable_sql(sql):
            continue
        ok, err = parse_check(sql, dialect)
        if ok:
            continue
        src = sources.get((name or "").upper())
        if src is not None:
            # An EMPTY source query means there was never a query to
            # break — the differential premise does not hold, so this
            # validator has no opinion (the pluggable-source case above
            # reaches here too when the stub carries real tokens).
            if not _has_executable_sql(src):
                continue
            src_ok, _ = parse_check(src, dialect)
            if not src_ok:
                # The source has the same construct: a grammar gap in the
                # checker, not a defect in the conversion.
                continue
        issues.append({
            "severity": "BLOCKER",
            "rule": f"sql.unparsable.{_safe_rule(name)}",
            "message": (
                f"The generated query for dataset {name!r} is not valid "
                f"Oracle SQL, but the report's ORIGINAL query for it "
                f"parsed cleanly — the conversion introduced the error. "
                f"Oracle will reject this dataset at execution. "
                f"Parser said: {err}"
            ),
        })
    return issues


def _safe_rule(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", (name or "ds"))[:40] or "ds"


# --------------------------------------------------------------------------
# Optional live-Oracle backend: syntax AND object resolution
# --------------------------------------------------------------------------

def live_backend_configured() -> bool:
    return bool(os.environ.get("O2S_ORACLE_DSN")
                and os.environ.get("O2S_ORACLE_USER"))


def explain_check(sql: str) -> Tuple[Optional[bool], str]:
    """Validate ``sql`` against a REAL Oracle instance with EXPLAIN PLAN.

    ``EXPLAIN PLAN FOR <query>`` compiles the statement — syntax, table
    and column resolution, privileges — without returning or modifying a
    single row. This is the strongest validation available and the one to
    point at a customer's own schema before go live.

    Returns ``(None, reason)`` when no backend is configured or the driver
    is missing, so callers can distinguish "no opinion" from "invalid".
    """
    if not live_backend_configured():
        return None, "live Oracle backend not configured"
    try:
        import oracledb                 # noqa: PLC0415 - optional dependency
    except Exception as exc:            # noqa: BLE001
        return None, f"oracledb driver unavailable: {exc}"
    if not (sql or "").strip():
        return True, ""
    # Binds stay BINDS here (unlike the grammar probe): the server accepts
    # them in EXPLAIN PLAN and this also proves the bind names are legal.
    try:
        with oracledb.connect(
            user=os.environ["O2S_ORACLE_USER"],
            password=os.environ.get("O2S_ORACLE_PASSWORD", ""),
            dsn=os.environ["O2S_ORACLE_DSN"],
        ) as conn:
            with conn.cursor() as cur:
                binds = {b: None for b in
                         sorted(set(_BIND_RE.findall(sql)))}
                cur.execute(f"EXPLAIN PLAN FOR {sql}", binds)
        return True, ""
    except Exception as exc:            # noqa: BLE001 - ORA-xxxxx surfaces here
        return False, " ".join(str(exc).split())[:300]
