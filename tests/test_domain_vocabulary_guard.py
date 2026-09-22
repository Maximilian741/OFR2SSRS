"""
DOMAIN-VOCABULARY GUARD - a permanent rail against the "name list" class.

The converter is a DIALECT translator: every decision must come from what the
SOURCE declares (an element, an attribute, a reference, a program-unit
assignment), never from what a name happens to SPELL. A hardcoded roster of
uppercase identifiers is how a general translator quietly turns into a
corpus-shaped one -- it works on the reports it was written beside and does
the wrong thing on the next site's naming convention, silently.

This file scans the converter package for that construct -- three or more
uppercase, identifier-shaped strings gathered into one place -- and fails
unless the site is named in ALLOWLIST below with a one-line justification.

The scan reads every literal shape a roster is written in, not just the
obvious one: a tuple / list / set of strings, a DICT's keys, the left members
of a (name, value) pair list, and a single string that is ``.split()`` at
import. That breadth is not decorative -- while this scanner read only
tuple/list/set literals, a 37-key dict of report-domain tokens sat in the
preview package and the gate reported it clean. See roster_candidates.

WHAT BELONGS IN THE ALLOWLIST: dialect vocabulary, i.e. tokens fixed by a
LANGUAGE or a PRODUCT that a report author cannot rename -- SQL / PL/SQL
keywords, Oracle built-in functions and pseudo-columns, Oracle's own system
parameter names, Oracle format-mask tokens, Oracle graph types, SSRS built-in
fields. WHAT DOES NOT: report-domain vocabulary -- guesses about what some
site might call a column, a parameter, a formula or a table. Those must be
replaced by reading the source's own declarations.

Rosters that are still report-domain guesses are carried here with a
justification that STARTS WITH "RESIDUAL". They are visible debt, not
approval: test_residual_debt_does_not_grow ratchets their count down only.

Adding an entry is a deliberate act: each key is content-addressed, so editing
a listed roster invalidates its entry and brings the reviewer back here.

The mutation proofs at the bottom break the gate on purpose (an invented
ZZQX* roster) and assert it goes red, so a structurally blind scanner can
never certify the package. No real report / column / parameter name appears
in this file.
"""
from __future__ import annotations

import ast
import hashlib
import re
from pathlib import Path
from typing import Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
PKG_ROOT = REPO_ROOT / "backend" / "converter"
SELF_REL = "tests/test_domain_vocabulary_guard.py"

# An identifier-shaped token: ALL-CAPS words joined by underscores, optionally
# written as a bare prefix/suffix fragment ("CF_", "_PATH") -- the two shapes a
# vocabulary roster is actually written in. Two characters is enough to be a
# domain token ("ID", "DY"); one character is punctuation.
_IDENT = re.compile(r"^_?[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*_?$")

MIN_IDENTS = 3


def looks_like_identifier(value: object) -> bool:
    return isinstance(value, str) and len(value) >= 2 and bool(_IDENT.match(value))


def site_key(rel_path: str, values: Tuple[str, ...]) -> str:
    """Content-addressed key: the file plus a digest of the roster itself, so
    an edited roster no longer matches its old justification."""
    digest = hashlib.sha1("\x00".join(sorted(values)).encode("utf-8")).hexdigest()[:12]
    return rel_path + "::" + digest


def _string_elements(nodes) -> "List[str] | None":
    """The string constants of ``nodes``, or None if any element is not one."""
    out: List[str] = []
    for element in nodes:
        if isinstance(element, ast.Constant) and isinstance(element.value, str):
            out.append(element.value)
        else:
            return None
    return out


def _pair_heads(nodes) -> "List[str] | None":
    """The first members of a sequence of (name, value) pairs.

    ``[("ALPHA", 1), ("BETA", 2), ...]`` is a roster wearing a coat: the
    names on the left are exactly the vocabulary, and a scanner that only
    reads flat string sequences walks straight past it.
    """
    out: List[str] = []
    for element in nodes:
        if not isinstance(element, (ast.Tuple, ast.List)) or len(element.elts) < 2:
            return None
        head = element.elts[0]
        if isinstance(head, ast.Constant) and isinstance(head.value, str):
            out.append(head.value)
        else:
            return None
    return out


def roster_candidates(node) -> List[Tuple[str, List[str]]]:
    """Every (shape, names) group a single AST node expresses.

    A vocabulary roster is a SET OF NAMES, and Python spells that at least
    four ways. Reading only tuple/list/set literals certified the other three
    blind -- which was not theoretical: a 37-key DICT of report-domain tokens
    sat in the preview package while this gate reported it clean. The shapes:

      seq    ("ALPHA", "BETA", "GAMMA")        tuple / list / set literal
      dict   {"ALPHA": ..., "BETA": ...}       the KEYS are the roster
      pair   [("ALPHA", 1), ("BETA", 2)]       the LEFT members are the roster
      split  "ALPHA BETA GAMMA".split()        one string, split at import
    """
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        values = _string_elements(node.elts)
        if values is not None:
            return [("seq", values)]
        heads = _pair_heads(node.elts)
        return [("pair", heads)] if heads is not None else []
    if isinstance(node, ast.Dict):
        # ``**other`` contributes a None key; string keys among non-string
        # ones still form a roster, so collect what is there.
        keys = [k.value for k in node.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)]
        return [("dict", keys)] if keys else []
    if isinstance(node, ast.Call):
        fn = node.func
        if (isinstance(fn, ast.Attribute) and fn.attr == "split"
                and isinstance(fn.value, ast.Constant)
                and isinstance(fn.value.value, str)):
            sep = None
            if node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    sep = first.value
                else:
                    return []
            text = fn.value.value
            parts = text.split(sep) if sep is not None else text.split()
            return [("split", [p for p in parts if p])]
    return []
    # NOTE on comprehensions: a DictComp / SetComp / ListComp / GeneratorExp
    # carries its names in the iterable it walks, and ast.walk visits that
    # iterable as its own node -- so the roster inside one is already seen by
    # the seq / dict / split cases above. There is nothing extra to read here,
    # and test_gate_catches_a_roster_inside_a_comprehension proves it.


def scan_source(src: str, rel_path: str) -> List[Tuple[str, int, Tuple[str, ...]]]:
    """Every (key, lineno, values) roster in ``src``, in any literal shape."""
    out: List[Tuple[str, int, Tuple[str, ...]]] = []
    tree = ast.parse(src)
    for node in ast.walk(tree):
        for _shape, values in roster_candidates(node):
            if len(values) < MIN_IDENTS:
                continue
            idents = [v for v in values if looks_like_identifier(v)]
            if len(idents) < MIN_IDENTS:
                continue
            # A mostly-prose list that happens to carry three acronyms is not
            # a vocabulary roster; a roster is dominated by identifier-shaped
            # tokens.
            if len(idents) * 2 < len(values):
                continue
            out.append((site_key(rel_path, tuple(values)), node.lineno,
                        tuple(values)))
    return out


def package_files() -> List[Path]:
    return sorted(p for p in PKG_ROOT.rglob("*.py") if "__pycache__" not in p.parts)


def rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def live_sites() -> Dict[str, Tuple[str, int, Tuple[str, ...]]]:
    found: Dict[str, Tuple[str, int, Tuple[str, ...]]] = {}
    for path in package_files():
        rel_path = rel(path)
        src = path.read_text(encoding="utf-8", errors="replace")
        for key, lineno, values in scan_source(src, rel_path):
            found.setdefault(key, (rel_path, lineno, values))
    return found


# ---------------------------------------------------------------------------
# ALLOWLIST -- one line of justification each. A justification beginning with
# "RESIDUAL" marks a roster that is NOT dialect vocabulary and still owes a
# declaration-driven replacement.
# ---------------------------------------------------------------------------
ALLOWLIST: Dict[str, str] = {}


def _allow(rel_path: str, values, why: str) -> None:
    ALLOWLIST[site_key(rel_path, tuple(values))] = why


_RDL = "backend/converter/generators/rdl.py"
_XML = "backend/converter/parsers/oracle_xml.py"
_MOCKUP = "backend/converter/preview/html_mockup.py"
_BURST = "backend/converter/bursting.py"
_SUB = "backend/converter/subreports.py"

# --- SSRS / RDL specification ---------------------------------------------
_allow(_RDL,
       ("PAGENUMBER", "PHYSICALPAGENUMBER", "LOGICALPAGENUMBER",
        "TOTALPHYSICALPAGES", "TOTALLOGICALPAGES", "TOTALPAGES",
        "PANELNUMBER", "TOTALPANELS"),
       "SSRS Globals! built-in field names - fixed by the RDL specification.")
_allow(_MOCKUP,
       ("PHYSICALPAGENUMBER", "PAGENUMBER", "TOTALPAGES", "TOTALPHYSICALPAGES",
        "TOTALLOGICALPAGES", "TOTALPANES", "PANENUMBER", "PAGE", "PAGES"),
       "SSRS Globals! built-in field names plus Oracle's page built-ins.")
_allow("backend/converter/validators/coverage.py",
       ("CURRENTDATE", "CURRENT_DATE", "PAGENUMBER", "TOTALPAGES",
        "PHYSICALPAGENUMBER", "LOGICALPAGENUMBER", "TOTALPHYSICALPAGES",
        "TOTALLOGICALPAGES", "PANELNUMBER", "TOTALPANELS"),
       "SSRS Globals! built-in field names - fixed by the RDL specification.")

# --- Oracle SQL / PL/SQL language -----------------------------------------
_allow(_RDL,
       ("SYSDATE", "SYSTIMESTAMP", "CURRENT_DATE", "CURRENT_TIMESTAMP",
        "LOCALTIMESTAMP", "USER", "UID", "SESSIONTIMEZONE", "DBTIMEZONE"),
       "Oracle SQL built-in functions / pseudo-columns - fixed by the dialect.")
_allow(_RDL,
       ("CURRENTDATE", "SYSDATE", "CURRENT_DATE"),
       "Oracle / SSRS current-date built-ins - fixed by both dialects.")
_allow(_RDL,
       ("FUNCTION", "PROCEDURE", "BEGIN", "DECLARE"),
       "PL/SQL program-unit keywords - fixed by the PL/SQL language.")
_allow(_XML,
       ("UNION ALL", "UNION", "INTERSECT", "MINUS", "EXCEPT"),
       "SQL set operators - fixed by the SQL language (MINUS is Oracle's).")
_allow(_RDL,
       ("UNION", "INTERSECT", "MINUS", "EXCEPT"),
       "SQL set operators - fixed by the SQL language; the chain-relay proof "
       "REFUSES any statement carrying one (a second branch proves nothing).")
_allow("backend/converter/translators/format_exception.py",
       ("AND", "OR", "NOT", "IS", "NULL", "LIKE", "BETWEEN"),
       "PL/SQL operator keywords - fixed by the PL/SQL language.")
_allow("backend/converter/translators/plsql_formula.py",
       ("AND", "OR", "NOT", "IS", "NULL", "LIKE", "BETWEEN", "IN", "MOD",
        "CASE", "WHEN", "THEN", "ELSE", "END", "TRUE", "FALSE"),
       "PL/SQL operator + control keywords - fixed by the PL/SQL language.")
_allow(_SUB,
       ("END", "NULL", "FROM", "DESC", "ASC", "DISTINCT"),
       "SQL keywords - fixed by the SQL language.")
_allow(_SUB,
       ("WHERE", "AND", "OR"),
       "SQL keywords - fixed by the SQL language.")
_allow("backend/converter/ai_apply.py",
       ("DROP", "TRUNCATE", "EXEC", "EXECUTE", "xp_", "sp_executesql"),
       "SQL / T-SQL DDL + procedure keywords blocked by the injection guard.")
_allow(_BURST,
       ("DUAL", "SYS", "INFORMATION_SCHEMA", "SELECT"),
       "Oracle / ANSI dictionary + keyword names excluded from table detection.")

# --- Oracle format-mask grammar -------------------------------------------
_allow(_RDL,
       ("MONTH", "MON", "DAY", "DY", "RM"),
       "Oracle format-mask tokens - fixed by Oracle's TO_CHAR mask grammar.")
_allow(_RDL,
       ("YYYY", "RRRR", "MON", "MONTH", "DAY", "DY", "HH", "AM", "PM"),
       "Oracle format-mask tokens - fixed by Oracle's TO_CHAR mask grammar.")
_allow("backend/converter/translators/plsql_formula.py",
       ("YYYY", "YY", "MON", "DD", "HH", "MI", "SS"),
       "Oracle format-mask tokens - fixed by Oracle's TO_CHAR mask grammar.")
_allow("backend/converter/translators/plsql_to_tsql.py",
       ("YYYY", "YEAR", "YY"),
       "Oracle date-part tokens (TRUNC / format) - fixed by the Oracle dialect.")
_allow("backend/converter/translators/plsql_to_tsql.py",
       ("MM", "MONTH", "MON"),
       "Oracle date-part tokens (TRUNC / format) - fixed by the Oracle dialect.")

# --- Oracle Reports product vocabulary ------------------------------------
_allow(_XML,
       ("PARETO", "SPECTRAL", "PIE_BAR", "RING_BAR"),
       "Oracle Reports graph type names - fixed by the Oracle Reports dialect.")
_allow(_RDL,
       ("REPORT", "DESTYPE", "DESFORMAT", "DESNAME"),
       "Oracle Reports' OWN system parameter names (runtime destination slots).")
_allow(_BURST,
       ("P_AS_PATH", "P_DISTRIBUTE", "P_DISTR_ABBR", "P_DESNAME", "P_DESTYPE",
        "P_DESFORMAT"),
       "Oracle Reports' OWN distribution system parameter names.")
_allow(_BURST,
       ("P_AS_PATH", "P_DESNAME", "P_DESTYPE", "P_DESFORMAT", "P_DISTR_ABBR",
        "P_DISTRIBUTE"),
       "Oracle Reports' OWN distribution system parameter names.")
_allow(_BURST,
       ("P_AS_PATH", "P_DISTRIBUTE", "DESNAME"),
       "Oracle Reports' OWN distribution system parameter names.")

_allow("backend/converter/parsers/oracle_xml.py",
       ("TR_HORIZ", "TR_HORIZ_ROTATE_90", "TR_HORIZ_ROTATE_270"),
       "Oracle Reports textRotation enum values - fixed by the Oracle "
       "Reports dialect, mapped 1:1 to RDL TextOrientation.")
_allow("backend/converter/parsers/oracle_xml.py",
       ("LAP_TOP", "LAP_BOTTOM", "LAP_RIGHT", "LAP_LEFT"),
       "Oracle Reports LegendArea position enum values - fixed by the "
       "Oracle Reports dialect, mapped 1:1 to RDL ChartLegend Position.")
_allow("backend/converter/audit.py",
       ("DECODE", "NVL2", "NVL", "TO_CHAR", "TO_DATE", "SYSDATE", "INSTR",
        "SUBSTR", "CHR", "||", "'||'", "(+)", "outer-join", "OUTER JOIN",
        "LEFT JOIN", "bind variable", "lexical", "LISTAGG", "ROWNUM",
        "FROM DUAL", "DUAL", "TRUNC", "ROUND", "Package", "package function",
        "UDF", "EXISTS", "HAVING", "RTRIM", "LTRIM"),
       "Oracle SQL / PL/SQL construct names the translator's own notes use - "
       "fixed by the SQL dialect, keyed to reviewer-facing rule tags.")

# --- Oracle format-mask grammar (dict / pair shapes) -----------------------
_allow(_RDL,
       ("MONTH", "MON", "DAY", "DY", "YYYY", "RRRR", "YY", "RR", "HH24",
        "HH12", "HH", "MI", "SS", "A.M.", "P.M.", "AM", "PM", "MM", "DD"),
       "Oracle format-mask tokens - fixed by Oracle's TO_CHAR mask grammar.")
_allow(_RDL,
       ("HH24", "HH12", "HH", "MI", "SS", "MM", "DD"),
       "Oracle format-mask tokens - fixed by Oracle's TO_CHAR mask grammar.")
_allow(_MOCKUP,
       ("MONTH", "MON", "DAY", "DY", "YYYY", "RRRR", "YY", "RR", "HH24",
        "HH12", "HH", "SSSSS", "MI", "SS", "A.M.", "P.M.", "AM", "PM", "MM",
        "DDD", "DD", "J", "Q"),
       "Oracle format-mask tokens - fixed by Oracle's TO_CHAR mask grammar; "
       "the preview renders its sample instant THROUGH the declared mask.")
_allow("backend/converter/translators/plsql_to_tsql.py",
       ("YYYY", "YY", "MONTH", "MON", "MM", "DD", "HH24", "HH", "MI", "SS",
        "AM", "PM"),
       "Oracle format-mask tokens - fixed by Oracle's TO_CHAR mask grammar.")
_allow("backend/converter/translators/plsql_to_tsql.py",
       ("SUNDAY", "SUN", "MONDAY", "MON", "TUESDAY", "TUE", "TUES",
        "WEDNESDAY", "WED", "THURSDAY", "THU", "THUR", "THURS", "FRIDAY",
        "FRI", "SATURDAY", "SAT"),
       "English day names + Oracle's NEXT_DAY abbreviations - fixed by the "
       "Oracle dialect's own day-name vocabulary, not by any report.")
_allow("backend/converter/translators/plsql_formula.py",
       ("YYYY", "YY", "MONTH", "MON", "DAY", "DY", "DD", "HH24", "HH", "MI",
        "SS", "AM", "PM"),
       "Oracle format-mask tokens - fixed by Oracle's TO_CHAR mask grammar.")

# --- Internal tags (not source-derived at all) -----------------------------
_allow("backend/converter/validators/vb_expr_eval.py",
       ("TEXT", "DATE", "NUM", "NULL", "MATCH"),
       "The evaluator's own internal value-kind tags - not source-derived names.")
_allow(_MOCKUP,
       ("YEAR4", "YEAR2", "MONTHNUM", "DAYNUM", "MONTHFULL", "MONTHABBR",
        "DAYFULL", "DAYABBR", "HOUR24", "HOUR12", "MINUTE", "SECOND",
        "MERIDIEM", "QUARTER", "JULIAN"),
       "The preview sampler's OWN slot names for one neutral instant - "
       "invented here, never read from or matched against a report.")
for _sev_file in ("backend/converter/__init__.py",
                  "backend/converter/ingest.py",
                  "backend/converter/validators/preflight.py"):
    _allow(_sev_file, ("BLOCKER", "RED", "AMBER"),
           "This tool's OWN verdict severity levels - not source-derived names.")

# --- RESIDUAL report-domain guesses (visible debt, ratcheted below) --------
_allow(_RDL,
       ("REPORT_SERVER", "_PATH", "ENVELOPE", "DISTRIBUTE"),
       "RESIDUAL Oracle Reports DEPLOYMENT slots the source does not express "
       "structurally; measured as the only reason 25 parameters across 9 "
       "corpus reports stay off the end-user prompt - see "
       "_should_hide_parameter, which decides structurally first.")
_allow(_RDL,
       ("_NAME", "_CODE", "_ID"),
       "RESIDUAL column-name suffix guess (code/description pair collapsing) "
       "- owes a declaration-driven replacement.")
_allow(_RDL,
       ("_DESC", "_DESCRIPTION", "_TEXT"),
       "RESIDUAL column-name suffix guess (code/description pair collapsing) "
       "- owes a declaration-driven replacement.")
_allow(_RDL,
       ("_ID", "_CODE", "_NO", "_NUM", "_KEY"),
       "RESIDUAL column-name suffix guess (parameter/column key pairing) "
       "- owes a declaration-driven replacement.")
_allow(_RDL,
       ("_ID", "_DATE", "_DT"),
       "RESIDUAL column-name suffix guess (non-descriptive card fields) "
       "- owes a declaration-driven replacement.")
_allow(_MOCKUP,
       ("_ID", "_DATE", "_DT"),
       "RESIDUAL column-name suffix guess (preview mirror of the RDL rule) "
       "- owes a declaration-driven replacement.")
_allow(_MOCKUP,
       ("F_", "P_", "PARM_"),
       "RESIDUAL object-name prefix guess (preview token resolution); F_/P_ "
       "are Oracle object prefixes but PARM_ is a site convention.")
_allow(_MOCKUP,
       ("FY", "YEAR", "YR"),
       "RESIDUAL report-domain guess (preview-only sample value) - affects "
       "the mockup preview only, never the RDL.")
_allow(_SUB,
       ("P_AS_PATH", "P_ENVELOPE", "P_DRILLDOWN", "P_DRILL", "P_SUBREPORT",
        "P_CHILD_REPORT", "P_NESTED"),
       "RESIDUAL report-domain guess (sub-report parameter detection) "
       "- owes a declaration-driven replacement.")
_allow(_SUB,
       ("P_URL_", "P_REPORT_SERVER", "P_URL", "P_REP_URL"),
       "RESIDUAL report-domain guess (sub-report URL parameter detection) "
       "- owes a declaration-driven replacement.")

# The number of RESIDUAL entries at the time this gate was installed. It may
# shrink (replace a guess with a declaration-driven rule and delete its entry);
# it may never grow.
RESIDUAL_BUDGET = 10


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------

def test_no_unjustified_domain_vocabulary_roster():
    """Every uppercase-identifier roster in the converter package is either
    dialect vocabulary (allowlisted with a justification) or a bug."""
    offenders: List[str] = []
    for key, (rel_path, lineno, values) in sorted(live_sites().items()):
        if key in ALLOWLIST:
            continue
        offenders.append(
            "{p}:{ln}  {n} tokens\n"
            "      values : {v}\n"
            "      key    : {k}".format(
                p=rel_path, ln=lineno, n=len(values),
                v=", ".join(values[:10]) + (" ..." if len(values) > 10 else ""),
                k=key))
    assert not offenders, (
        "Unjustified domain-vocabulary roster(s) found.\n\n"
        + "\n".join(offenders)
        + "\n\nA roster of uppercase identifiers makes the converter guess at "
          "a site's naming convention. Read what the SOURCE declares instead "
          "(an element, an attribute, a reference, an assignment). If the "
          "tokens really are DIALECT vocabulary - fixed by SQL, PL/SQL, "
          "Oracle Reports or the RDL spec, and unrenameable by a report "
          "author - add the printed key to ALLOWLIST in " + SELF_REL
        + " with a one-line justification.")


def test_allowlist_entries_all_carry_a_justification():
    for key, why in ALLOWLIST.items():
        assert isinstance(why, str) and len(why.strip()) >= 20, key


def test_allowlist_has_no_dead_entries():
    """An allowlist entry that matches nothing is stale - it stops documenting
    a real site and starts hiding a future one behind a familiar-looking key."""
    live = set(live_sites())
    dead = sorted(k for k in ALLOWLIST if k not in live)
    assert not dead, (
        "Stale ALLOWLIST entries (no matching roster in the package any more) "
        "- delete them:\n  " + "\n  ".join(dead))


def test_residual_debt_does_not_grow():
    """RESIDUAL entries are guesses awaiting a declaration-driven rule. The
    count may shrink; a new one must be a real fix, not a new entry."""
    residual = [k for k, why in ALLOWLIST.items()
                if why.strip().upper().startswith("RESIDUAL")]
    assert len(residual) <= RESIDUAL_BUDGET, (
        "RESIDUAL vocabulary debt grew from {b} to {n}. Replace the guess with "
        "a rule that reads the source's declarations instead of adding an "
        "entry.".format(b=RESIDUAL_BUDGET, n=len(residual)))


# ---------------------------------------------------------------------------
# Mutation proofs - break it on purpose, confirm the gate goes red
# ---------------------------------------------------------------------------

_FAKE_ROSTER = ("ZZQX_ALPHA", "ZZQX_BETA", "ZZQX_GAMMA", "ZZQX_DELTA")


def test_gate_catches_a_planted_roster():
    """A fresh name list must be flagged - in a tuple, a list and a set, and
    whether it is a module constant or an inline literal."""
    for opener, closer in (("(", ")"), ("[", "]"), ("{", "}")):
        body = ", ".join(repr(v) for v in _FAKE_ROSTER)
        src = "_ZZQX_HINTS = " + opener + body + closer + "\n"
        found = scan_source(src, "backend/converter/zzqx_planted.py")
        assert len(found) == 1, (opener, found)
        assert found[0][0] not in ALLOWLIST
        assert found[0][2] == _FAKE_ROSTER

    inline = (
        "def f(name):\n"
        "    return any(p in name for p in ("
        + ", ".join(repr(v) for v in _FAKE_ROSTER) + "))\n"
    )
    found = scan_source(inline, "backend/converter/zzqx_planted.py")
    assert len(found) == 1 and found[0][2] == _FAKE_ROSTER


def test_gate_catches_a_roster_written_as_dict_keys():
    """THE BLIND SPOT THIS SCANNER WAS WIDENED FOR.

    A mapping's KEYS are a roster. While the scanner read only
    tuple/list/set literals, a 37-key dict of report-domain tokens sat in the
    preview package and this gate reported the package clean.
    """
    body = ", ".join("%s: 'x'" % repr(v) for v in _FAKE_ROSTER)
    found = scan_source("_ZZQX = {" + body + "}\n",
                        "backend/converter/zzqx_planted.py")
    assert len(found) == 1, found
    assert found[0][2] == _FAKE_ROSTER
    assert found[0][0] not in ALLOWLIST


def test_gate_catches_a_roster_written_as_pairs():
    """``[("ALPHA", 1), ("BETA", 2), ...]`` - the names are on the left."""
    body = ", ".join("(%s, %d)" % (repr(v), i)
                     for i, v in enumerate(_FAKE_ROSTER))
    for opener, closer in (("[", "]"), ("(", ")")):
        found = scan_source("_ZZQX = " + opener + body + closer + "\n",
                            "backend/converter/zzqx_planted.py")
        assert len(found) == 1, (opener, found)
        assert found[0][2] == _FAKE_ROSTER


def test_gate_catches_a_roster_written_as_a_split_string():
    """``"ALPHA BETA GAMMA".split()`` is a roster that never appears as a
    sequence of string nodes at all."""
    for call in ('"%s".split()' % " ".join(_FAKE_ROSTER),
                 '"%s".split(",")' % ",".join(_FAKE_ROSTER)):
        found = scan_source("_ZZQX = " + call + "\n",
                            "backend/converter/zzqx_planted.py")
        assert len(found) == 1, (call, found)
        assert found[0][2] == _FAKE_ROSTER


def test_gate_catches_a_roster_inside_a_comprehension():
    """A comprehension hides nothing: its source iterable is its own node."""
    body = ", ".join(repr(v) for v in _FAKE_ROSTER)
    for src in ("_ZZQX = {k: 1 for k in (" + body + ")}\n",
                "_ZZQX = {k.lower() for k in [" + body + "]}\n",
                "_ZZQX = [k for k in (" + body + ") if k]\n",
                "_ZZQX = tuple(k for k in (" + body + "))\n"):
        found = scan_source(src, "backend/converter/zzqx_planted.py")
        assert any(f[2] == _FAKE_ROSTER for f in found), (src, found)


def test_widened_shapes_do_not_flag_ordinary_code():
    """No false positives from the new shapes: a short mapping, a mapping of
    prose, a pair list keyed by non-strings, and a split of a sentence."""
    assert scan_source("_X = {'ZZQX_ALPHA': 1, 'ZZQX_BETA': 2}\n", "p.py") == []
    assert scan_source("_X = {'one': 1, 'two': 2, 'three': 3}\n", "p.py") == []
    assert scan_source("_X = [(1, 'ZZQX_A'), (2, 'ZZQX_B'), (3, 'ZZQX_C')]\n",
                       "p.py") == []
    assert scan_source('_X = "the quick brown fox jumps".split()\n', "p.py") == []
    assert scan_source("_X = some_text.split(',')\n", "p.py") == []


def test_gate_catches_a_suffix_fragment_roster():
    """Suffix/prefix fragments ('_ALPHA') are how these rosters are usually
    written - the scanner must not be blind to them."""
    src = "_ZZQX = ('_ALPHA', '_BETA', '_GAMMA')\n"
    found = scan_source(src, "backend/converter/zzqx_planted.py")
    assert len(found) == 1 and len(found[0][2]) == 3


def test_gate_ignores_prose_and_short_rosters():
    """No false positives on ordinary code: prose lists, two-token rosters,
    and lists that merely mention an acronym."""
    assert scan_source("_X = ('ZZQX_ALPHA', 'ZZQX_BETA')\n", "p.py") == []
    prose = ("_MSGS = ('the ZZQX_ALPHA slot', 'a ZZQX_BETA value', "
             "'some ZZQX_GAMMA text', 'plain words here', 'more plain words')\n")
    assert scan_source(prose, "p.py") == []
    assert scan_source("_X = ('a', 'b', 'c')\n", "p.py") == []


def test_edited_roster_loses_its_justification():
    """The key is content-addressed on purpose: adding one token to an
    allowlisted roster must invalidate its entry, not inherit it."""
    approved = ("REPORT", "DESTYPE", "DESFORMAT", "DESNAME")
    assert site_key(_RDL, approved) in ALLOWLIST
    assert site_key(_RDL, approved + ("ZZQX_EXTRA",)) not in ALLOWLIST


def test_planted_roster_would_fail_the_real_gate(tmp_path, monkeypatch):
    """End-to-end mutation proof: plant the roster in a throwaway package,
    point the gate at it, and confirm the assertion fires."""
    pkg = tmp_path / "backend" / "converter"
    pkg.mkdir(parents=True)
    (pkg / "zzqx_planted.py").write_text(
        "_ZZQX_HINTS = " + repr(_FAKE_ROSTER) + "\n", encoding="utf-8")

    import tests.test_domain_vocabulary_guard as mod
    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(mod, "PKG_ROOT", pkg)
    try:
        mod.test_no_unjustified_domain_vocabulary_roster()
    except AssertionError as exc:
        assert "ZZQX_ALPHA" in str(exc)
    else:  # pragma: no cover - the gate must be able to fail
        raise AssertionError("gate did not fire on a planted roster")
