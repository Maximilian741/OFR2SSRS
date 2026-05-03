"""
Oracle SQL / PL-SQL  ->  T-SQL translator (rule-based, regex-driven).

Public API:
    translate_report(report: ParsedReport) -> None
        Mutate report in place. For each query, populate q.tsql and q.notes.
        For each formula, populate f.tsql_body and f.notes.

    translate_sql(oracle_sql: str) -> tuple[str, list[str]]
        Core single-query translator. Returns (tsql_text, list_of_warnings).

This module is intentionally pragmatic: it ships best-effort regex rewrites
for the patterns we know matter for the hackathon sample (MVWF_PERMIT.xml).
Every non-trivial rewrite emits a warning so the UI can flag risky rewrites.
"""

from __future__ import annotations

import re
from typing import List, Tuple

from ..models import ParsedReport, DataQuery, FormulaColumn
from . import udf_stubs


# ---------------------------------------------------------------------------
# Helper - paren-aware splitter
# ---------------------------------------------------------------------------

def _split_top_level_commas(s: str) -> List[str]:
    """Split a string on commas that sit at parenthesis depth 0.
    Respects single-quoted string literals."""
    out: List[str] = []
    depth = 0
    in_str = False
    buf: List[str] = []
    i = 0
    while i < len(s):
        c = s[i]
        if in_str:
            buf.append(c)
            if c == "'":
                # handle '' as escape
                if i + 1 < len(s) and s[i + 1] == "'":
                    buf.append("'")
                    i += 2
                    continue
                in_str = False
        else:
            if c == "'":
                in_str = True
                buf.append(c)
            elif c == '(':
                depth += 1
                buf.append(c)
            elif c == ')':
                depth -= 1
                buf.append(c)
            elif c == ',' and depth == 0:
                out.append(''.join(buf).strip())
                buf = []
            else:
                buf.append(c)
        i += 1
    tail = ''.join(buf).strip()
    if tail:
        out.append(tail)
    return out


def _find_matching_paren(s: str, open_idx: int) -> int:
    """Given index of '(' in s, return index of matching ')'. -1 if none."""
    assert s[open_idx] == '('
    depth = 0
    in_str = False
    i = open_idx
    while i < len(s):
        c = s[i]
        if in_str:
            if c == "'":
                if i + 1 < len(s) and s[i + 1] == "'":
                    i += 2
                    continue
                in_str = False
        else:
            if c == "'":
                in_str = True
            elif c == '(':
                depth += 1
            elif c == ')':
                depth -= 1
                if depth == 0:
                    return i
        i += 1
    return -1


def _find_function_call(text: str, fn_name: str, start: int = 0) -> Tuple[int, int, int]:
    """Find a case-insensitive call to `fn_name(...)` in text starting from `start`.

    Returns (name_start, paren_open, paren_close) or (-1,-1,-1) if not found.
    Requires the match to be a whole word (preceded by non-identifier).
    """
    pat = re.compile(r'(?<![A-Za-z0-9_$.])' + re.escape(fn_name) + r'\s*\(', re.IGNORECASE)
    m = pat.search(text, start)
    if not m:
        return (-1, -1, -1)
    name_start = m.start()
    paren_open = m.end() - 1   # the '('
    paren_close = _find_matching_paren(text, paren_open)
    if paren_close < 0:
        return (-1, -1, -1)
    return (name_start, paren_open, paren_close)


# ---------------------------------------------------------------------------
# Individual rewriters - each returns (new_text, warnings)
# ---------------------------------------------------------------------------

def _rewrite_decode(text: str) -> Tuple[str, List[str]]:
    """DECODE(x, a, b, c, d, ..., default) -> CASE x WHEN a THEN b WHEN c THEN d ... ELSE default END"""
    warnings: List[str] = []
    while True:
        ns, po, pc = _find_function_call(text, 'DECODE')
        if ns < 0:
            break
        inner = text[po + 1:pc]
        args = _split_top_level_commas(inner)
        if len(args) < 3:
            # malformed; bail
            warnings.append("DECODE call had fewer than 3 args; left as-is.")
            break
        expr = args[0]
        pairs: List[Tuple[str, str]] = []
        i = 1
        # last arg is default if odd number remaining after expr
        rest = args[1:]
        default = None
        if len(rest) % 2 == 1:
            default = rest[-1]
            rest = rest[:-1]
        for j in range(0, len(rest), 2):
            pairs.append((rest[j], rest[j + 1]))

        case_parts = ["CASE " + expr]
        for w, t in pairs:
            case_parts.append(f"WHEN {w} THEN {t}")
        if default is not None:
            case_parts.append(f"ELSE {default}")
        case_parts.append("END")
        replacement = " ".join(case_parts)

        text = text[:ns] + replacement + text[pc + 1:]
        warnings.append("DECODE rewritten as CASE expression.")
    return text, warnings


def _rewrite_nvl(text: str) -> Tuple[str, List[str]]:
    """NVL(a,b)  -> ISNULL(a,b);  NVL2(a,b,c) -> CASE WHEN a IS NOT NULL THEN b ELSE c END"""
    warnings: List[str] = []
    # NVL2 first to avoid the NVL prefix swallowing it
    while True:
        ns, po, pc = _find_function_call(text, 'NVL2')
        if ns < 0:
            break
        args = _split_top_level_commas(text[po + 1:pc])
        if len(args) != 3:
            warnings.append("NVL2 call had unexpected arg count; left as-is.")
            break
        replacement = f"CASE WHEN {args[0]} IS NOT NULL THEN {args[1]} ELSE {args[2]} END"
        text = text[:ns] + replacement + text[pc + 1:]

    while True:
        ns, po, pc = _find_function_call(text, 'NVL')
        if ns < 0:
            break
        args = _split_top_level_commas(text[po + 1:pc])
        if len(args) != 2:
            warnings.append("NVL call had unexpected arg count; left as-is.")
            break
        replacement = f"ISNULL({args[0]}, {args[1]})"
        text = text[:ns] + replacement + text[pc + 1:]
    return text, warnings


# Oracle to .NET / SQL Server FORMAT() format-string mapping.
# Order matters: longer tokens first so 'YYYY' isn't eaten by 'YY'.
_TO_CHAR_FORMAT_MAP = [
    ('YYYY', 'yyyy'),
    ('YY',   'yy'),
    ('MONTH', 'MMMM'),
    ('MON',   'MMM'),
    ('MM',   'MM'),
    ('DD',   'dd'),
    ('HH24', 'HH'),
    ('HH',   'hh'),
    ('MI',   'mm'),
    ('SS',   'ss'),
    ('AM',   'tt'),
    ('PM',   'tt'),
]


def _convert_oracle_format(fmt: str) -> Tuple[str, bool]:
    """Convert an Oracle format string like 'fmMONTH DD, YYYY' to a .NET format
    string like 'MMMM dd, yyyy'. Returns (converted, had_fm_modifier)."""
    had_fm = False
    f = fmt
    if f.lower().startswith('fm'):
        had_fm = True
        f = f[2:]
    # Replace tokens (case-insensitive) longest first
    out: List[str] = []
    i = 0
    while i < len(f):
        matched = False
        upper_rest = f[i:].upper()
        for tok, repl in _TO_CHAR_FORMAT_MAP:
            if upper_rest.startswith(tok):
                out.append(repl)
                i += len(tok)
                matched = True
                break
        if not matched:
            out.append(f[i])
            i += 1
    return (''.join(out), had_fm)


def _rewrite_to_char(text: str) -> Tuple[str, List[str]]:
    """TO_CHAR(d, 'fmt') -> FORMAT(d, '.netfmt')   (or UPPER(...) if 'fmMONTH ...')."""
    warnings: List[str] = []
    while True:
        ns, po, pc = _find_function_call(text, 'TO_CHAR')
        if ns < 0:
            break
        args = _split_top_level_commas(text[po + 1:pc])
        if len(args) == 1:
            # TO_CHAR(n) -> CAST(n AS NVARCHAR(50))
            replacement = f"CAST({args[0]} AS NVARCHAR(50))"
            warnings.append("TO_CHAR(n) rewritten as CAST AS NVARCHAR(50).")
        elif len(args) >= 2:
            d = args[0]
            fmt_arg = args[1].strip()
            if fmt_arg.startswith("'") and fmt_arg.endswith("'"):
                raw_fmt = fmt_arg[1:-1]
                net_fmt, had_fm = _convert_oracle_format(raw_fmt)
                # If MONTH (uppercase) was in the original, Oracle returns
                # uppercase month name; FORMAT(...,'MMMM') is title-case.
                if 'MONTH' in raw_fmt.upper() or 'DAY' in raw_fmt.upper():
                    replacement = f"UPPER(FORMAT({d}, '{net_fmt}'))"
                else:
                    replacement = f"FORMAT({d}, '{net_fmt}')"
                warnings.append("TO_CHAR rewritten as FORMAT(); review locale & case.")
            else:
                replacement = f"FORMAT({d}, {fmt_arg})"
                warnings.append("TO_CHAR with non-literal format kept as FORMAT().")
        else:
            warnings.append("TO_CHAR with no args left as-is.")
            break
        text = text[:ns] + replacement + text[pc + 1:]
    return text, warnings


_DATE_LITERAL_RE = re.compile(
    r"^'(?P<m>\d{1,2})/(?P<d>\d{1,2})/(?P<y>\d{4})'$"
)


def _rewrite_to_date(text: str) -> Tuple[str, List[str]]:
    """TO_DATE('01/01/2026','MM/DD/YYYY') -> TRY_CONVERT(date, '2026-01-01').
    With non-literals, fall back to CONVERT(date, x, 101)."""
    warnings: List[str] = []
    while True:
        ns, po, pc = _find_function_call(text, 'TO_DATE')
        if ns < 0:
            break
        args = _split_top_level_commas(text[po + 1:pc])
        if len(args) >= 1:
            v = args[0].strip()
            m = _DATE_LITERAL_RE.match(v)
            if m and len(args) >= 2:
                iso = f"{int(m['y']):04d}-{int(m['m']):02d}-{int(m['d']):02d}"
                replacement = f"TRY_CONVERT(date, '{iso}')"
            elif len(args) >= 2:
                replacement = f"CONVERT(date, {args[0]}, 101)"
                warnings.append("TO_DATE with non-literal rewritten as CONVERT(date,x,101); verify mask.")
            else:
                replacement = f"CONVERT(date, {args[0]})"
                warnings.append("TO_DATE without mask rewritten as CONVERT(date, x).")
        else:
            warnings.append("TO_DATE with no args left as-is.")
            break
        text = text[:ns] + replacement + text[pc + 1:]
    return text, warnings


def _rewrite_trunc(text: str) -> Tuple[str, List[str]]:
    """TRUNC(x) -> CAST(x AS DATE) for dates  (best-effort; we can't tell types,
    so we warn and pick DATE which is the common report case).
    TRUNC(SYSDATE) etc. -> CAST(SYSDATE_replaced_to_GETDATE() AS DATE)."""
    warnings: List[str] = []
    while True:
        ns, po, pc = _find_function_call(text, 'TRUNC')
        if ns < 0:
            break
        args = _split_top_level_commas(text[po + 1:pc])
        if len(args) == 1:
            replacement = f"CAST({args[0]} AS DATE)"
            warnings.append("TRUNC(x) rewritten as CAST(x AS DATE); use CAST(x AS INT) if numeric.")
        elif len(args) == 2:
            # TRUNC(date, 'YYYY')  -> DATEFROMPARTS(YEAR(d),1,1) etc.
            d = args[0]
            unit = args[1].strip().strip("'").upper()
            if unit in ('YYYY', 'YEAR', 'YY'):
                replacement = f"DATEFROMPARTS(YEAR({d}),1,1)"
            elif unit in ('MM', 'MONTH', 'MON'):
                replacement = f"DATEFROMPARTS(YEAR({d}),MONTH({d}),1)"
            elif unit in ('DD', 'DAY'):
                replacement = f"CAST({d} AS DATE)"
            else:
                replacement = f"CAST({d} AS DATE)"
            warnings.append(f"TRUNC(d, '{unit}') rewritten; verify period semantics.")
        else:
            warnings.append("TRUNC with unexpected args left as-is.")
            break
        text = text[:ns] + replacement + text[pc + 1:]
    return text, warnings


def _rewrite_round_date(text: str) -> Tuple[str, List[str]]:
    """ROUND(d, 'YYYY') -> DATEFROMPARTS(YEAR(d),1,1). ROUND(n) left alone."""
    warnings: List[str] = []
    while True:
        ns, po, pc = _find_function_call(text, 'ROUND')
        if ns < 0:
            break
        args = _split_top_level_commas(text[po + 1:pc])
        if len(args) == 2 and args[1].strip().startswith("'"):
            d = args[0]
            unit = args[1].strip().strip("'").upper()
            if unit in ('YYYY', 'YEAR', 'YY'):
                replacement = f"DATEFROMPARTS(YEAR({d}),1,1)"
            elif unit in ('MM', 'MONTH'):
                replacement = f"DATEFROMPARTS(YEAR({d}),MONTH({d}),1)"
            else:
                replacement = f"CAST({d} AS DATE)"
            warnings.append(f"ROUND(date, '{unit}') rewritten; semantics differ slightly.")
            text = text[:ns] + replacement + text[pc + 1:]
            continue
        # numeric ROUND - leave as-is, T-SQL also has ROUND(n,d).
        # Move past this call.
        # To avoid infinite loop, we step over it by replacing in-place with
        # itself (no change) and continuing search after pc.
        # Simplest: break after one numeric ROUND - but multiple may exist;
        # We use a sentinel by temporarily replacing the function name.
        head = text[:ns] + 'RND__SENTINEL' + text[ns + len('ROUND'):]
        text = head
    text = text.replace('RND__SENTINEL', 'ROUND')
    return text, warnings


def _rewrite_sysdate(text: str) -> Tuple[str, List[str]]:
    """SYSDATE -> GETDATE(). Safe because SYSDATE has no args in Oracle."""
    new = re.sub(r'(?<![A-Za-z0-9_$])SYSDATE(?![A-Za-z0-9_$])',
                 'GETDATE()', text, flags=re.IGNORECASE)
    warns: List[str] = []
    if new != text:
        warns.append("SYSDATE replaced with GETDATE(); time component preserved.")
    return new, warns


def _rewrite_instr(text: str) -> Tuple[str, List[str]]:
    """INSTR(s, sub) -> CHARINDEX(sub, s)"""
    warnings: List[str] = []
    while True:
        ns, po, pc = _find_function_call(text, 'INSTR')
        if ns < 0:
            break
        args = _split_top_level_commas(text[po + 1:pc])
        if len(args) == 2:
            replacement = f"CHARINDEX({args[1]}, {args[0]})"
        elif len(args) == 3:
            replacement = f"CHARINDEX({args[1]}, {args[0]}, {args[2]})"
            warnings.append("INSTR(s,sub,start) rewritten; 4th 'occurrence' arg not supported in CHARINDEX.")
        elif len(args) >= 4:
            replacement = f"CHARINDEX({args[1]}, {args[0]}, {args[2]}) /* TODO: 4th 'occurrence' arg dropped */"
            warnings.append("INSTR 4-arg form: 'occurrence' parameter dropped (no T-SQL equivalent).")
        else:
            warnings.append("INSTR with unexpected arg count left as-is.")
            break
        text = text[:ns] + replacement + text[pc + 1:]
    return text, warnings


def _rewrite_substr(text: str) -> Tuple[str, List[str]]:
    """SUBSTR(s, p, l) -> SUBSTRING(s, p, l).  If l absent, use a big number."""
    warnings: List[str] = []
    while True:
        ns, po, pc = _find_function_call(text, 'SUBSTR')
        if ns < 0:
            break
        args = _split_top_level_commas(text[po + 1:pc])
        if len(args) == 2:
            replacement = f"SUBSTRING({args[0]}, {args[1]}, 8000)"
            warnings.append("SUBSTR(s,p) rewritten as SUBSTRING(s,p,8000) - T-SQL requires length.")
        elif len(args) >= 3:
            replacement = f"SUBSTRING({args[0]}, {args[1]}, {args[2]})"
        else:
            warnings.append("SUBSTR with unexpected args left as-is.")
            break
        text = text[:ns] + replacement + text[pc + 1:]
    return text, warnings


def _rewrite_chr(text: str) -> Tuple[str, List[str]]:
    """CHR(n) -> CHAR(n).  Safe rename."""
    new = re.sub(r'(?<![A-Za-z0-9_$])CHR\s*\(', 'CHAR(', text, flags=re.IGNORECASE)
    warns: List[str] = []
    if new != text:
        warns.append("CHR() replaced with CHAR().")
    return new, warns


def _rewrite_concat(text: str) -> Tuple[str, List[str]]:
    """Replace Oracle '||' concatenation with T-SQL '+'.

    Note: T-SQL '+' yields NULL when either side is NULL. Oracle's ||
    treats NULL as empty. We do not introduce ISNULL() blanket-wraps
    (too noisy); we just warn once.
    """
    warns: List[str] = []
    if '||' in text:
        # Replace || with +. Avoid touching '||' inside string literals.
        out: List[str] = []
        in_str = False
        i = 0
        while i < len(text):
            c = text[i]
            if c == "'" and not in_str:
                in_str = True
                out.append(c)
                i += 1
                continue
            if c == "'" and in_str:
                # handle '' escape
                if i + 1 < len(text) and text[i + 1] == "'":
                    out.append("''")
                    i += 2
                    continue
                in_str = False
                out.append(c)
                i += 1
                continue
            if not in_str and c == '|' and i + 1 < len(text) and text[i + 1] == '|':
                out.append(' + ')
                i += 2
                continue
            out.append(c)
            i += 1
        text = ''.join(out)
        warns.append("Oracle '||' rewritten as T-SQL '+'; NULL semantics differ "
                     "(T-SQL returns NULL on NULL operand). Wrap with ISNULL(x,'') if needed.")
    return text, warns


def _rewrite_bind_vars(text: str) -> Tuple[str, List[str]]:
    """:P_FOO -> @P_FOO (for SSRS / sp_executesql).

    Be careful: we don't want to munge things like ':=' (PL/SQL assignment)
    or substring-literals. We require the bind-var to start with an
    identifier-leading char immediately after the colon.
    """
    new, n = re.subn(r'(?<![A-Za-z0-9_$:]):([A-Za-z][A-Za-z0-9_$]*)', r'@\1', text)
    warns: List[str] = []
    if n > 0:
        warns.append(f"Replaced {n} Oracle bind variable(s) (:NAME) with T-SQL @NAME.")
    return new, warns


def _rewrite_lexicals(text: str) -> Tuple[str, List[str]]:
    """&P_NAME lexical refs -> /* &P_NAME */ comment placeholder + warning."""
    warns: List[str] = []
    matches = re.findall(r'&([A-Za-z][A-Za-z0-9_$]*)', text)
    if matches:
        text = re.sub(r'&([A-Za-z][A-Za-z0-9_$]*)', r'/* &\1 */', text)
        warns.append(
            "Oracle lexical reference(s) found ("
            + ", ".join(sorted(set('&' + m for m in matches)))
            + "). T-SQL has no lexical SQL-injection equivalent. "
            "Use a Tablix filter, dynamic SQL via sp_executesql, "
            "or pre-compose the WHERE clause in code-behind."
        )
    return text, warns


# ---------------------------------------------------------------------------
# Outer-join (+) rewrite
# ---------------------------------------------------------------------------

# Captures predicates of the form `A.col(+) = B.col` or `A.col = B.col(+)`.
_OUTER_PRED_RE = re.compile(
    r'(?P<lhs>[A-Za-z_][A-Za-z_0-9$#]*\.[A-Za-z_][A-Za-z_0-9$#]*)\s*'
    r'(?P<lp>\(\+\))?\s*'
    r'=\s*'
    r'(?P<rhs>[A-Za-z_][A-Za-z_0-9$#]*\.[A-Za-z_][A-Za-z_0-9$#]*)\s*'
    r'(?P<rp>\(\+\))?',
    re.IGNORECASE,
)


def _rewrite_outer_join(text: str) -> Tuple[str, List[str]]:
    """Best-effort heuristic rewrite of Oracle's `(+)` outer-join syntax.

    Strategy:
      1. If no `(+)` present, do nothing.
      2. Try to detect a flat FROM-list with comma-separated tables.
         For each `A.col(+) = B.col` predicate, mark table A as the
         "optional" side of a LEFT JOIN to B (B LEFT JOIN A).
         (Yes, the marker `(+)` in Oracle goes on the *outer* side,
         which is the table whose columns may be NULL. So the table
         carrying `(+)` is the right operand of the LEFT JOIN.)
      3. If we can confidently rewrite, replace FROM/WHERE with the new
         JOIN syntax.  Otherwise, fall back to wrapping the original in
         a TODO comment and emit a warning.
    """
    if '(+)' not in text:
        return text, []

    warnings: List[str] = []

    # Locate FROM and WHERE positions (top-level only).
    from_match = re.search(r'\bFROM\b', text, flags=re.IGNORECASE)
    where_match = re.search(r'\bWHERE\b', text, flags=re.IGNORECASE)
    end_match = re.search(
        r'\b(GROUP\s+BY|HAVING|ORDER\s+BY|UNION|INTERSECT|MINUS|EXCEPT)\b',
        text,
        flags=re.IGNORECASE,
    )
    if not from_match or not where_match:
        warnings.append(
            "Oracle outer-join '(+)' detected but FROM/WHERE structure not "
            "recognized; left in place. Rewrite to LEFT JOIN by hand."
        )
        return text, warnings

    where_end = end_match.start() if end_match and end_match.start() > where_match.end() else len(text)

    from_clause = text[from_match.end():where_match.start()]
    where_clause = text[where_match.end():where_end]

    # Parse FROM list - simple comma split, ignoring parens.
    from_items = _split_top_level_commas(from_clause)

    # Map alias -> (table_name, alias)
    aliases: dict = {}
    table_order: List[str] = []
    for item in from_items:
        toks = item.strip().split()
        if not toks:
            continue
        if len(toks) >= 2:
            tbl = toks[0]
            alias = toks[-1]
        else:
            tbl = toks[0]
            alias = toks[0]
        aliases[alias.upper()] = (tbl, alias)
        table_order.append(alias.upper())

    # Find predicates with (+).  We split where_clause on AND at top level to
    # get individual predicates.
    preds = _split_on_and(where_clause)

    join_edges: List[Tuple[str, str, str]] = []   # (driving_alias, optional_alias, predicate)
    other_preds: List[str] = []
    confident = True

    for p in preds:
        sp = p.strip()
        if '(+)' not in sp:
            other_preds.append(sp)
            continue
        m = _OUTER_PRED_RE.search(sp)
        if not m:
            confident = False
            other_preds.append(sp)
            continue
        # Strip the (+) markers from the predicate text
        clean = sp.replace('(+)', '')
        lhs_alias = m.group('lhs').split('.')[0].upper()
        rhs_alias = m.group('rhs').split('.')[0].upper()
        if m.group('lp'):
            optional, driving = lhs_alias, rhs_alias
        elif m.group('rp'):
            optional, driving = rhs_alias, lhs_alias
        else:
            confident = False
            other_preds.append(sp)
            continue
        if optional not in aliases or driving not in aliases:
            confident = False
            other_preds.append(sp)
            continue
        join_edges.append((driving, optional, clean))

    if not confident or not join_edges:
        # Heuristic failed - just wrap and warn.
        new_text = (
            text[:from_match.start()]
            + "/* TODO outer-join rewrite: original '(+)' syntax retained; convert to LEFT JOIN manually */\n"
            + text[from_match.start():]
        )
        warnings.append(
            "Oracle '(+)' outer-join detected but heuristic rewrite was not "
            "confident; left as-is with TODO comment. Convert to LEFT JOIN manually."
        )
        return new_text, warnings

    # Build the new FROM clause.  We start with the first non-optional
    # alias, then chain in the optional aliases via LEFT JOIN.
    optional_aliases = {opt for _, opt, _ in join_edges}
    main_aliases = [a for a in table_order if a not in optional_aliases]
    if not main_aliases:
        # All-optional - shouldn't happen, fall back.
        warnings.append(
            "Oracle '(+)' rewrite: all tables marked optional; left as-is."
        )
        return text, warnings

    pieces = []
    seen_aliases: set = set()
    # First main table
    first = main_aliases[0]
    tbl, al = aliases[first]
    pieces.append(f"{tbl} {al}" if tbl != al else tbl)
    seen_aliases.add(first)

    # Remaining main tables - inner-join with TRUE for now (will be filtered by other_preds).
    for a in main_aliases[1:]:
        tbl, al = aliases[a]
        pieces.append(f"INNER JOIN {tbl} {al}" if tbl != al else f"INNER JOIN {tbl}")
        pieces.append("ON 1=1")
        seen_aliases.add(a)

    # Then LEFT JOIN every optional alias on its predicate(s).
    edges_by_optional: dict = {}
    for d, o, pred in join_edges:
        edges_by_optional.setdefault(o, []).append(pred)
    for opt_alias, preds_list in edges_by_optional.items():
        tbl, al = aliases[opt_alias]
        pieces.append(f"LEFT JOIN {tbl} {al}" if tbl != al else f"LEFT JOIN {tbl}")
        pieces.append("ON " + " AND ".join(preds_list))

    new_from = "\n  " + "\n  ".join(pieces) + "\n"
    new_where = ""
    if other_preds:
        new_where = "\nWHERE " + "\n  AND ".join(other_preds)

    new_text = (
        text[:from_match.start()]
        + "FROM" + new_from
        + new_where
        + text[where_end:]
    )
    warnings.append(
        f"Heuristic rewrite of Oracle '(+)' outer joins to LEFT JOIN "
        f"({len(edges_by_optional)} table(s)); review the JOIN order and "
        f"ON-clauses carefully."
    )
    return new_text, warnings


def _split_on_and(clause: str) -> List[str]:
    """Split a WHERE clause on top-level AND tokens.

    Treats any whitespace (space, tab, newline) on either side of AND as
    a valid separator. (sentinel-v2)
    """
    out: List[str] = []
    depth = 0
    in_str = False
    buf: List[str] = []
    i = 0
    upper = clause.upper()
    n = len(clause)

    def _is_word_boundary_left(idx: int) -> bool:
        if idx <= 0:
            return True
        c = clause[idx - 1]
        return not (c.isalnum() or c == '_')

    def _is_word_boundary_right(idx: int) -> bool:
        if idx >= n:
            return True
        c = clause[idx]
        return not (c.isalnum() or c == '_')

    while i < n:
        c = clause[i]
        if in_str:
            buf.append(c)
            if c == "'":
                if i + 1 < n and clause[i + 1] == "'":
                    buf.append("'")
                    i += 2
                    continue
                in_str = False
            i += 1
            continue
        if c == "'":
            in_str = True
            buf.append(c)
            i += 1
            continue
        if c == '(':
            depth += 1
            buf.append(c)
            i += 1
            continue
        if c == ')':
            depth -= 1
            buf.append(c)
            i += 1
            continue
        # Look for AND as a whole word at depth 0, surrounded by whitespace
        if (depth == 0
                and upper[i:i + 3] == 'AND'
                and _is_word_boundary_left(i)
                and _is_word_boundary_right(i + 3)):
            out.append(''.join(buf).strip())
            buf = []
            i += 3
            # Eat trailing whitespace so the next predicate starts cleanly.
            while i < n and clause[i] in ' \t\r\n':
                i += 1
            continue
        buf.append(c)
        i += 1
    tail = ''.join(buf).strip()
    if tail:
        out.append(tail)
    return [p for p in out if p]


# ---------------------------------------------------------------------------
# EXISTS-with-HAVING  (just emit a recommendation warning)
# ---------------------------------------------------------------------------

_EXISTS_HAVING_RE = re.compile(
    r'EXISTS\s*\(\s*SELECT[^)]*?HAVING\s+MAX\s*\(',
    re.IGNORECASE | re.DOTALL,
)


def _check_exists_having(text: str) -> List[str]:
    if _EXISTS_HAVING_RE.search(text):
        return [
            "EXISTS (... HAVING MAX(...)) preserved verbatim. "
            "Consider rewriting as a CTE with ROW_NUMBER() / window-MAX for clarity."
        ]
    return []


# ---------------------------------------------------------------------------
# RTRIM / LTRIM with character-class - just warn
# ---------------------------------------------------------------------------

def _check_rtrim_ltrim(text: str) -> List[str]:
    warns: List[str] = []
    for fn in ('RTRIM', 'LTRIM'):
        ns, po, pc = _find_function_call(text, fn)
        while ns >= 0:
            args = _split_top_level_commas(text[po + 1:pc])
            if len(args) >= 2:
                warns.append(
                    f"{fn}(s, chars) detected. T-SQL TRIM only accepts a "
                    "literal char-set in 2017+. Verify or wrap with a UDF."
                )
                break
            ns, po, pc = _find_function_call(text, fn, pc + 1)
    return warns


# ---------------------------------------------------------------------------
# Package-function rewrites (Pkg_X.F_Y -> dbo.fn_F_Y)
# ---------------------------------------------------------------------------

def _rewrite_package_calls(text: str) -> Tuple[str, List[str]]:
    """Replace 'Pkg_*.F_xxx' with 'dbo.fn_F_xxx'."""
    warns: List[str] = []
    pat = re.compile(r'\b(Pkg_[A-Za-z0-9_]+|Utl_URL)\s*\.\s*([A-Za-z_][A-Za-z0-9_]*)',
                     re.IGNORECASE)
    found = set()

    def _sub(m: re.Match) -> str:
        pkg, fn = m.group(1), m.group(2)
        found.add((pkg, fn))
        return f"dbo.fn_{fn}"

    new = pat.sub(_sub, text)
    if found:
        names = sorted({f"{p}.{f}" for p, f in found})
        warns.append(
            "Replaced Oracle package function call(s) with T-SQL UDF stubs: "
            + ", ".join(names)
            + ". See generated dbo.fn_* CREATE FUNCTION script."
        )
    return new, warns


# ---------------------------------------------------------------------------
# Top-level translate_sql
# ---------------------------------------------------------------------------

def translate_sql(oracle_sql: str) -> Tuple[str, List[str]]:
    """Translate one Oracle SQL statement to (best-effort) T-SQL.
    Returns the translated SQL and a list of human-readable warnings.
    """
    if not oracle_sql or not oracle_sql.strip():
        return "", []

    text = oracle_sql
    warnings: List[str] = []

    # Step 1 - outer-join rewrite (do this BEFORE bind-var conversion so the
    # alias detection isn't confused by `@`).
    text, w = _rewrite_outer_join(text)
    warnings.extend(w)

    # Step 2 - function rewrites.  Order matters: NVL2 before NVL.
    for fn in (_rewrite_decode,
               _rewrite_nvl,
               _rewrite_to_char,
               _rewrite_to_date,
               _rewrite_round_date,   # before TRUNC, harmless
               _rewrite_trunc,
               _rewrite_sysdate,
               _rewrite_instr,
               _rewrite_substr,
               _rewrite_chr,
               _rewrite_concat,
               _rewrite_lexicals,
               _rewrite_bind_vars,
               _rewrite_package_calls,
               ):
        text, w = fn(text)
        warnings.extend(w)

    # Step 3 - non-rewriting checks
    warnings.extend(_check_exists_having(text))
    warnings.extend(_check_rtrim_ltrim(text))

    # Deduplicate warnings while preserving order
    seen = set()
    unique: List[str] = []
    for w in warnings:
        if w not in seen:
            unique.append(w)
            seen.add(w)
    return text, unique


# ---------------------------------------------------------------------------
# PL/SQL formula body translation (best-effort)
# ---------------------------------------------------------------------------

def translate_plsql_body(plsql: str) -> Tuple[str, List[str]]:
    """Translate an Oracle PL/SQL function body to T-SQL (very best-effort)."""
    if not plsql or not plsql.strip():
        return "", []
    text = plsql
    warnings: List[str] = ["PL/SQL formula body translated heuristically; review carefully."]

    # Reuse SQL-level rewrites for expressions inside the body.
    text, w = translate_sql(text)
    # translate_sql adds its own warnings - keep them.
    warnings.extend(w)

    # PL/SQL keywords.
    text = re.sub(r'\bRETURN\b', 'RETURN', text, flags=re.IGNORECASE)
    text = re.sub(r'\bELSIF\b', 'ELSE IF', text, flags=re.IGNORECASE)
    text = re.sub(r'\bEND\s+IF\b', 'END', text, flags=re.IGNORECASE)
    text = re.sub(r'\bIS NULL\b', 'IS NULL', text, flags=re.IGNORECASE)
    text = re.sub(r'\bIS NOT NULL\b', 'IS NOT NULL', text, flags=re.IGNORECASE)

    # Dedup warnings.
    seen, unique = set(), []
    for w2 in warnings:
        if w2 not in seen:
            unique.append(w2)
            seen.add(w2)
    return text, unique


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def translate_report(report: ParsedReport) -> None:
    """Translate every query and formula in `report` in place."""
    for q in report.queries:
        if not q.sql:
            continue
        try:
            tsql, warns = translate_sql(q.sql)
        except Exception as e:  # noqa: BLE001
            tsql = q.sql
            warns = [f"translate_sql raised {type(e).__name__}: {e}"]
        q.tsql = tsql
        for w in warns:
            q.add_warning(w)

    for f in report.formulas:
        if not f.plsql_body:
            continue
        try:
            body, warns = translate_plsql_body(f.plsql_body)
        except Exception as e:  # noqa: BLE001
            body = f.plsql_body
            warns = [f"translate_plsql_body raised {type(e).__name__}: {e}"]
        f.tsql_body = body
        for w in warns:
            if w and w not in f.notes:
                f.notes.append(w)
