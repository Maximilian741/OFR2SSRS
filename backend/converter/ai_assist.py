"""
AI-assist prompt builder.

For Oracle PL/SQL / SQL constructs the deterministic translator can't handle
cleanly, this module generates copy-paste prompts the user can drop into
Claude / Copilot / ChatGPT to obtain a working T-SQL translation.

We do NOT call any LLM here — we only structure the open work.

Public API:
    build_prompts(report) -> list[dict]

Each returned item has the shape:

    {
        "id": str,                    # stable id, e.g. "formula:CF_File_F"
        "scope": "formula" | "query" | "package_fn",
        "name": str,                  # e.g. "CF_File_F" or "PKG_UTIL.F_Get_Names"
        "difficulty": "easy" | "medium" | "hard",
        "deterministic_attempt": str, # what our translator produced (may be a TODO stub)
        "prompt_template": str,       # full prompt ready to paste
        "context_hint": str,          # one-paragraph description
    }
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# Trigger phrases in q.notes / formula notes that mark "needs human/AI help"
# ---------------------------------------------------------------------------

_NOTE_TRIGGERS: Tuple[str, ...] = (
    "TODO",
    "lexical ref",
    "outer-join rewrite",
    "outer-join '(+)'",
    "REGEXP",
    "CONNECT BY",
    "could not",
    "not confident",
    "uncertain",
    "manually",
)

# Body markers in formula tsql_body that mean "we punted"
_BODY_TRIGGERS: Tuple[str, ...] = (
    "/* TODO */",
    "/* TODO",
    "-- TODO",
)

# Pattern for Oracle package function calls (Pkg_*.F_*).
_PKG_CALL_RE = re.compile(
    r"\b(Pkg_[A-Za-z0-9_]+|Utl_URL)\s*\.\s*([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)

# These short function names get a known stub from the deterministic translator
# (translators/udf_stubs.py _KNOWN_STUBS). Anything outside this set is treated
# as a generic stub that benefits from an AI-assist prompt.
_KNOWN_STUB_FNS: Set[str] = {
    "F_Format_Address",
    "F_Format_Org_Name",
    "F_To_Phone",
    "F_Val",
    "F_Format_Url",
    "F_Get_Permittees",
    "F_Get_Rep_Description",
    "F_Get_Rep_URL",
    "F_Get_Rep_Distr_Abbr",
    "F_Get_Perm_Name",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _notes_match_any(notes: Iterable[str], triggers: Iterable[str]) -> List[str]:
    """Return the list of notes that match any trigger (case-insensitive)."""
    out: List[str] = []
    if not notes:
        return out
    for n in notes:
        if not n:
            continue
        low = n.lower()
        for t in triggers:
            if t.lower() in low:
                out.append(n)
                break
    return out


def _body_has_todo(body: str) -> bool:
    if not body:
        return False
    low = body.lower()
    for marker in _BODY_TRIGGERS:
        if marker.lower() in low:
            return True
    return False


def _classify_difficulty_from_notes(notes: Iterable[str]) -> str:
    """Pick a difficulty bucket based on what kind of trigger fired."""
    text = " ".join(notes or []).lower()
    # The hardest stuff: hierarchical, outer-join rewrites that didn't take,
    # regular expressions, lexical references that change query shape.
    if "connect by" in text or "regexp" in text:
        return "hard"
    if "outer-join" in text and ("not confident" in text or "todo" in text):
        return "hard"
    if "lexical ref" in text:
        return "medium"
    if "todo" in text:
        return "medium"
    return "easy"


def _schema_notes_from_queries(queries: List[Any], limit: int = 8) -> str:
    """Build a short bullet list of tables/columns referenced by the report's queries.

    This is shipped inside each prompt as 'Schema notes' so the AI has context
    on what tables/columns exist.
    """
    table_re = re.compile(
        r"\bFROM\s+([A-Za-z_][A-Za-z0-9_]*)|\bJOIN\s+([A-Za-z_][A-Za-z0-9_]*)",
        re.IGNORECASE,
    )
    tables: List[str] = []
    seen: Set[str] = set()
    for q in queries or []:
        sql = getattr(q, "sql", "") or ""
        for m in table_re.finditer(sql):
            t = (m.group(1) or m.group(2) or "").strip()
            if t and t.upper() not in seen:
                seen.add(t.upper())
                tables.append(t)
        if len(tables) >= limit:
            break

    if not tables:
        return "  (no schema info extracted from report.queries)"

    lines = [f"  - {t}" for t in tables[:limit]]
    return "\n".join(lines)


def _truncate(text: str, max_chars: int = 6000) -> str:
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n-- ... [truncated for prompt] ..."


# ---------------------------------------------------------------------------
# Prompt template builders
# ---------------------------------------------------------------------------

_FORMULA_TEMPLATE = """\
You are migrating an Oracle Reports application to SSRS / SQL Server.
Translate the following PL/SQL formula column to a T-SQL scalar UDF body.
Maintain identical behavior. Use SQL Server 2016+ features (TRY_CAST,
STRING_AGG, IIF, CONCAT_WS, etc.) where they make the translation cleaner.

Formula name: {name}
Returns:      {return_type}

Original Oracle PL/SQL:
-----
{plsql}
-----

Our deterministic translator produced (verify / fix / replace):
-----
{attempt}
-----

Schema notes (tables visible to the report):
{schema_notes}

Return ONLY the T-SQL function body (the statements that go between BEGIN and END
of `CREATE FUNCTION dbo.fn_{name}(...) RETURNS {return_type} AS BEGIN ... END`),
no prose, no markdown fences.
"""

_QUERY_TEMPLATE = """\
You are migrating an Oracle Reports application to SSRS / SQL Server.
Rewrite the following Oracle SQL query as T-SQL for SQL Server 2016+.

Pay attention to:
  - Convert `(+)` outer-join syntax to LEFT/RIGHT JOIN.
  - Replace `CONNECT BY` hierarchies with recursive CTEs (WITH ... AS).
  - Replace `REGEXP_LIKE` / `REGEXP_REPLACE` with PATINDEX / LIKE / a CLR-free
    equivalent, or call out clearly that a CLR function is needed.
  - Replace bind variables `:P_NAME` with `@P_NAME`.
  - Replace lexical references `&P_NAME` (which substitute SQL fragments at parse
    time) with explicit `WHERE` predicates parameterised on `@P_NAME`.

Query name: {name}

Translator warnings that triggered this prompt:
{notes_block}

Original Oracle SQL:
-----
{oracle_sql}
-----

Deterministic best-effort T-SQL (verify / fix / replace):
-----
{attempt}
-----

Schema notes (tables seen elsewhere in the report):
{schema_notes}

Return ONLY the final T-SQL SELECT statement, no prose, no markdown fences.
The result set must have the same column names and order as the Oracle query.
"""

_PACKAGE_TEMPLATE = """\
You are migrating an Oracle Reports application to SSRS / SQL Server.
Implement a T-SQL scalar UDF that replaces calls to the Oracle package function
below. Match the original semantics. Use SQL Server 2016+ features.

Original Oracle reference: {qualified}
Target T-SQL name:         dbo.fn_{fn_short}
Observed call sites:       {call_count}
Argument arity (max seen): {arity}

Sample call sites in the report (first {sample_count} shown):
{call_samples}

Schema notes (tables visible to the report):
{schema_notes}

Our deterministic translator emitted a generic stub. Replace it with a real
implementation that returns the correct value. If you cannot derive the
behavior from the call sites, pick the most plausible interpretation and
clearly comment your assumptions inline.

Return the COMPLETE T-SQL CREATE FUNCTION statement (with CREATE OR ALTER
FUNCTION ...), including parameter list, RETURNS clause, and BEGIN ... END.
No prose, no markdown fences.
"""


def _format_notes_block(notes: Iterable[str]) -> str:
    notes = [n for n in (notes or []) if n]
    if not notes:
        return "  (none captured)"
    return "\n".join(f"  - {n}" for n in notes)


# ---------------------------------------------------------------------------
# Per-scope prompt extractors
# ---------------------------------------------------------------------------

def _formula_context_hint(f: Any) -> str:
    name = getattr(f, "name", "?")
    rt = getattr(f, "return_type", "VARCHAR2")
    return (
        f"Formula column '{name}' returns an Oracle {rt}. It is used by the "
        f"report to compute a derived value per row. The deterministic "
        f"translator left a TODO marker, so the body needs human/AI completion."
    )


def _query_context_hint(q: Any) -> str:
    name = getattr(q, "name", "?")
    return (
        f"Data query '{name}' contains Oracle-specific syntax (lexical refs, "
        f"outer-join '(+)', CONNECT BY hierarchies, or REGEXP_*) that the "
        f"deterministic translator could not rewrite confidently. The result "
        f"set columns must match what the RDL dataset expects."
    )


def _package_context_hint(qualified: str) -> str:
    return (
        f"Oracle package function '{qualified}' is referenced in the report's "
        f"SQL/PL/SQL but no specific stub matches it. The deterministic "
        f"translator wired up a generic '<placeholder>' UDF body. Replace "
        f"that body with a real implementation matching the original."
    )


def _build_formula_prompt(f: Any, schema_notes: str) -> Dict[str, Any]:
    name = getattr(f, "name", "")
    plsql = getattr(f, "plsql_body", "") or ""
    attempt = getattr(f, "tsql_body", "") or "(translator produced no body)"
    notes = list(getattr(f, "notes", []) or [])
    return_type = getattr(f, "return_type", "VARCHAR2") or "VARCHAR2"

    difficulty = _classify_difficulty_from_notes(notes)
    if not notes and _body_has_todo(attempt):
        difficulty = "medium"

    template = _FORMULA_TEMPLATE.format(
        name=name,
        return_type=return_type,
        plsql=_truncate(plsql).rstrip(),
        attempt=_truncate(attempt).rstrip(),
        schema_notes=schema_notes,
    )

    return {
        "id": f"formula:{name}",
        "scope": "formula",
        "name": name,
        "difficulty": difficulty,
        "deterministic_attempt": attempt,
        "prompt_template": template,
        "context_hint": _formula_context_hint(f),
    }


def _build_query_prompt(q: Any, schema_notes: str) -> Dict[str, Any]:
    name = getattr(q, "name", "")
    oracle_sql = getattr(q, "sql", "") or ""
    attempt = getattr(q, "tsql", "") or "(translator produced no T-SQL)"
    notes = list(getattr(q, "notes", []) or [])

    difficulty = _classify_difficulty_from_notes(notes)
    notes_block = _format_notes_block(notes)

    template = _QUERY_TEMPLATE.format(
        name=name,
        notes_block=notes_block,
        oracle_sql=_truncate(oracle_sql).rstrip(),
        attempt=_truncate(attempt).rstrip(),
        schema_notes=schema_notes,
    )

    return {
        "id": f"query:{name}",
        "scope": "query",
        "name": name,
        "difficulty": difficulty,
        "deterministic_attempt": attempt,
        "prompt_template": template,
        "context_hint": _query_context_hint(q),
    }


def _scan_pkg_calls(report: Any) -> Dict[str, Dict[str, Any]]:
    """Scan all SQL / PL/SQL bodies for Pkg_*.F_* references.

    Returns: { qualified_name -> {fn_short, pkg, arity, call_sites: [snippet,...]} }
    """
    sources: List[Tuple[str, str]] = []   # (origin_label, text)
    for q in getattr(report, "queries", []) or []:
        if getattr(q, "sql", ""):
            sources.append((f"query {q.name}", q.sql))
    for f in getattr(report, "formulas", []) or []:
        if getattr(f, "plsql_body", ""):
            sources.append((f"formula {f.name}", f.plsql_body))
    for t in getattr(report, "triggers", []) or []:
        if getattr(t, "body", ""):
            sources.append((f"trigger {t.name}", t.body))

    found: Dict[str, Dict[str, Any]] = {}
    for origin, text in sources:
        for m in _PKG_CALL_RE.finditer(text):
            pkg = m.group(1)
            fn_short = m.group(2)
            qualified = f"{pkg}.{fn_short}"
            entry = found.setdefault(qualified, {
                "pkg": pkg,
                "fn_short": fn_short,
                "qualified": qualified,
                "arity": 0,
                "call_count": 0,
                "call_sites": [],
            })
            # Count this call site, capture a small snippet around the match.
            entry["call_count"] += 1
            start = max(0, m.start() - 40)
            end = min(len(text), m.end() + 80)
            snippet = text[start:end].replace("\n", " ").strip()
            label = f"({origin}) ...{snippet}..."
            if len(entry["call_sites"]) < 3:
                entry["call_sites"].append(label)
            # Compute arity by counting commas at depth 1 after the '('.
            i = m.end()
            if i < len(text) and text[i] == "(":
                depth = 0
                j = i
                in_str = False
                commas = 0
                had_content = False
                while j < len(text):
                    c = text[j]
                    if in_str:
                        if c == "'":
                            if j + 1 < len(text) and text[j + 1] == "'":
                                j += 2
                                continue
                            in_str = False
                    else:
                        if c == "'":
                            in_str = True
                        elif c == "(":
                            depth += 1
                        elif c == ")":
                            depth -= 1
                            if depth == 0:
                                break
                        elif c == "," and depth == 1:
                            commas += 1
                        elif depth == 1 and not c.isspace():
                            had_content = True
                    j += 1
                arity = (commas + 1) if had_content else 0
                if arity > entry["arity"]:
                    entry["arity"] = arity
    return found


def _build_package_prompt(entry: Dict[str, Any], schema_notes: str) -> Dict[str, Any]:
    qualified = entry["qualified"]
    fn_short = entry["fn_short"]
    arity = entry["arity"]
    call_count = entry["call_count"]
    samples = entry["call_sites"]

    if not samples:
        sample_block = "  (no call sites captured)"
    else:
        sample_block = "\n".join(f"  - {s}" for s in samples)

    # Hard if no call sites were captured (we know nothing). Otherwise medium.
    difficulty = "hard" if call_count == 0 or arity == 0 else "medium"

    attempt = (
        f"-- Generic auto-generated stub from translators/udf_stubs.py\n"
        f"-- dbo.fn_{fn_short} returns N'<{qualified}>' as a placeholder.\n"
        f"-- Replace with real implementation."
    )

    template = _PACKAGE_TEMPLATE.format(
        qualified=qualified,
        fn_short=fn_short,
        call_count=call_count,
        arity=arity if arity else "(unknown)",
        sample_count=min(3, len(samples)),
        call_samples=sample_block,
        schema_notes=schema_notes,
    )

    return {
        "id": f"package_fn:{qualified}",
        "scope": "package_fn",
        "name": qualified,
        "difficulty": difficulty,
        "deterministic_attempt": attempt,
        "prompt_template": template,
        "context_hint": _package_context_hint(qualified),
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_prompts(report: Any) -> List[Dict[str, Any]]:
    """Return a list of paste-ready prompts for the tricky parts of `report`.

    See module docstring for the item shape.
    """
    if report is None:
        return []

    schema_notes = _schema_notes_from_queries(getattr(report, "queries", []) or [])

    out: List[Dict[str, Any]] = []
    seen_ids: Set[str] = set()

    # 1) Formulas with TODO bodies or trigger-matching notes.
    for f in getattr(report, "formulas", []) or []:
        notes = list(getattr(f, "notes", []) or [])
        body = getattr(f, "tsql_body", "") or ""
        if _notes_match_any(notes, _NOTE_TRIGGERS) or _body_has_todo(body):
            item = _build_formula_prompt(f, schema_notes)
            if item["id"] not in seen_ids:
                seen_ids.add(item["id"])
                out.append(item)

    # 2) Queries whose notes mention any tricky construct.
    for q in getattr(report, "queries", []) or []:
        notes = list(getattr(q, "notes", []) or [])
        matches = _notes_match_any(notes, _NOTE_TRIGGERS)
        if matches:
            item = _build_query_prompt(q, schema_notes)
            if item["id"] not in seen_ids:
                seen_ids.add(item["id"])
                out.append(item)

    # 3) Package function references that don't have a known stub spec.
    pkg_calls = _scan_pkg_calls(report)
    for qualified in sorted(pkg_calls.keys()):
        entry = pkg_calls[qualified]
        if entry["fn_short"] in _KNOWN_STUB_FNS:
            continue
        item = _build_package_prompt(entry, schema_notes)
        if item["id"] not in seen_ids:
            seen_ids.add(item["id"])
            out.append(item)

    # Stable sort: hard first, then medium, then easy; ties by name.
    rank = {"hard": 0, "medium": 1, "easy": 2}
    out.sort(key=lambda d: (rank.get(d.get("difficulty", "easy"), 3), d.get("name", "")))
    return out


__all__ = ["build_prompts"]
