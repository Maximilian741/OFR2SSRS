"""Deterministic Oracle Reports formula (PL/SQL) -> SSRS VB.NET expression compiler.

This is the heart of the migration: an Oracle Reports CF_/CP_ formula is a small
PL/SQL function that returns a per-row value. SSRS computes per-row values with
VB.NET expressions (used in a calculated <Field> or directly in a textbox). The
two map cleanly for the constructs real reports use, so we TRANSLATE the formula
instead of leaving a placeholder.

Pipeline:
  1. strip comments, isolate the BEGIN..END body
  2. reduce the body to a single "effective return expression" -- handling
     IF/ELSIF/ELSE -> IIf(...) and simple `var := expr` local substitution
  3. translate that Oracle expression to VB.NET with a real tokenizer +
     precedence-climbing parser (|| -> &, NVL -> IIf(IsNothing..), DECODE ->
     nested IIf, SUBSTR -> Mid, :bind -> Fields!/Parameters! via a resolver...)

`translate_formula_to_vb(body, resolve)` returns {expr, ok, notes, unresolved}.
``ok`` is True only when the WHOLE thing translated with no unknown calls -- the
caller keeps its safe placeholder when ok is False, so a broken expression never
reaches SSRS. Generic: no per-report knowledge; the resolver supplies how a bind
name becomes a field/parameter reference.
"""
from __future__ import annotations

import re
from typing import Callable, Dict, List, Optional


# ---------------------------------------------------------------------------
# Default reference resolver (rdl.py passes its own, scope-aware one)
# ---------------------------------------------------------------------------
def _default_resolve(name: str) -> str:
    up = name.upper()
    if up.startswith(("P_", "PARM_")):
        return f"Parameters!{name}.Value"
    return f"Fields!{name}.Value"


# ---------------------------------------------------------------------------
# Comment / body extraction
# ---------------------------------------------------------------------------
def _strip_comments(s: str) -> str:
    s = re.sub(r"/\*.*?\*/", " ", s, flags=re.DOTALL)
    s = re.sub(r"--[^\n]*", " ", s)
    return s


def _body_between_begin_end(src: str) -> str:
    """Return the statements between the OUTER BEGIN and its matching END.
    Falls back to the whole string if no BEGIN is found."""
    m = re.search(r"\bBEGIN\b", src, re.IGNORECASE)
    if not m:
        return src.strip()
    inner = src[m.end():]
    # drop a trailing EXCEPTION ... END handler and the final END;
    ex = re.search(r"\bEXCEPTION\b", inner, re.IGNORECASE)
    if ex:
        inner = inner[:ex.start()]
    # cut at the LAST 'END' token
    last = None
    for mm in re.finditer(r"\bEND\b\s*;?", inner, re.IGNORECASE):
        last = mm
    if last:
        inner = inner[:last.start()]
    return inner.strip()


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------
_TOKEN_RE = re.compile(
    r"""
      (?P<ws>\s+)
    | (?P<str>'(?:''|[^'])*')
    | (?P<num>\d+\.\d+|\.\d+|\d+)
    | (?P<bind>:[A-Za-z_]\w*)
    | (?P<op><=|>=|<>|!=|\|\||\*\*|[-+*/=<>(),.])
    | (?P<id>[A-Za-z_]\w*)
    """,
    re.VERBOSE,
)


def _tokenize(expr: str) -> List[tuple]:
    out = []
    i = 0
    while i < len(expr):
        m = _TOKEN_RE.match(expr, i)
        if not m:
            # unknown char -> stop; caller treats as untranslatable
            raise ValueError(f"cannot tokenize at {expr[i:i+20]!r}")
        i = m.end()
        if m.lastgroup == "ws":
            continue
        out.append((m.lastgroup, m.group()))
    return out


# ---------------------------------------------------------------------------
# Expression parser  (Oracle expression -> VB.NET string)
# ---------------------------------------------------------------------------
_KEYWORDS = {"AND", "OR", "NOT", "IS", "NULL", "LIKE", "BETWEEN", "IN",
            "MOD", "CASE", "WHEN", "THEN", "ELSE", "END", "TRUE", "FALSE"}


class _Parser:
    def __init__(self, tokens: List[tuple], resolve: Callable[[str], str]):
        self.toks = tokens
        self.i = 0
        self.resolve = resolve
        self.unresolved: List[str] = []

    # -- token helpers --
    def _peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else (None, None)

    def _next(self):
        t = self.toks[self.i]
        self.i += 1
        return t

    def _eat_op(self, *vals):
        k, v = self._peek()
        if k == "op" and v in vals:
            self.i += 1
            return v
        return None

    def _eat_kw(self, *words):
        k, v = self._peek()
        if k == "id" and v.upper() in words:
            self.i += 1
            return v.upper()
        return None

    # -- grammar (low -> high precedence) --
    def parse(self) -> str:
        e = self._or()
        if self.i != len(self.toks):
            raise ValueError(f"trailing tokens at {self.toks[self.i:]}")
        return e

    def _or(self) -> str:
        left = self._and()
        while self._eat_kw("OR"):
            left = f"({left} Or {self._and()})"
        return left

    def _and(self) -> str:
        left = self._not()
        while self._eat_kw("AND"):
            left = f"({left} And {self._not()})"
        return left

    def _not(self) -> str:
        if self._eat_kw("NOT"):
            return f"(Not {self._not()})"
        return self._cmp()

    def _cmp(self) -> str:
        left = self._add()
        # IS [NOT] NULL
        if self._eat_kw("IS"):
            neg = self._eat_kw("NOT")
            if not self._eat_kw("NULL"):
                raise ValueError("expected NULL after IS")
            return f"(Not IsNothing({left}))" if neg else f"IsNothing({left})"
        if self._eat_kw("LIKE"):
            pat = self._add()
            return f"({left} Like {self._like_pattern(pat)})"
        op = self._eat_op("=", "<>", "!=", ">", "<", ">=", "<=")
        if op:
            vb = {"!=": "<>"}.get(op, op)
            return f"({left} {vb} {self._add()})"
        return left

    def _like_pattern(self, vb_literal: str) -> str:
        # translate Oracle wildcards % _ to VB Like * ? inside a string literal
        if vb_literal.startswith('"') and vb_literal.endswith('"'):
            inner = vb_literal[1:-1].replace("%", "*").replace("_", "?")
            return '"' + inner + '"'
        return vb_literal

    def _add(self) -> str:
        left = self._mul()
        while True:
            if self._eat_op("||"):
                left = f"({left} & {self._mul()})"
            elif self._eat_op("+"):
                left = f"({left} + {self._mul()})"
            elif self._eat_op("-"):
                left = f"({left} - {self._mul()})"
            else:
                break
        return left

    def _mul(self) -> str:
        left = self._unary()
        while True:
            if self._eat_op("*"):
                left = f"({left} * {self._unary()})"
            elif self._eat_op("/"):
                left = f"({left} / {self._unary()})"
            elif self._eat_kw("MOD"):
                left = f"({left} Mod {self._unary()})"
            else:
                break
        return left

    def _unary(self) -> str:
        if self._eat_op("-"):
            return f"(-{self._unary()})"
        if self._eat_op("+"):
            return self._unary()
        return self._primary()

    def _primary(self) -> str:
        k, v = self._peek()
        if k is None:
            raise ValueError("unexpected end of expression")
        if k == "op" and v == "(":
            self._next()
            e = self._or()
            if not self._eat_op(")"):
                raise ValueError("expected )")
            return f"({e})"
        if k == "num":
            self._next()
            return v
        if k == "str":
            self._next()
            # Oracle '' escape -> VB "" escape; wrap in double quotes
            inner = v[1:-1].replace("''", "\x00").replace('"', '""').replace("\x00", "'")
            return '"' + inner + '"'
        if k == "bind":
            self._next()
            return self.resolve(v[1:])
        if k == "id":
            up = v.upper()
            if up in ("NULL",):
                self._next()
                return "Nothing"
            if up in ("TRUE", "FALSE"):
                self._next()
                return up.capitalize()
            if up == "SYSDATE":
                self._next()
                return "Now()"
            if up == "CASE":
                return self._case()
            # function call?  NAME ( args )  -- possibly pkg.fn
            name = v
            self._next()
            # dotted name: Pkg.Fn
            while self._eat_op("."):
                k2, v2 = self._peek()
                if k2 == "id":
                    self._next()
                    name += "." + v2
                else:
                    break
            nk, nv = self._peek()
            if nk == "op" and nv == "(":
                self._next()
                args = self._arglist()
                if not self._eat_op(")"):
                    raise ValueError("expected ) in call")
                return self._func(name, args)
            # bare identifier (local var should have been substituted; column
            # without a colon is unusual) -> treat as a field reference but
            # flag it so the caller can decide.
            self.unresolved.append(name)
            return self.resolve(name)
        raise ValueError(f"unexpected token {v!r}")

    def _arglist(self) -> List[str]:
        args = []
        if self._peek() == ("op", ")"):
            return args
        args.append(self._named_or_pos())
        while self._eat_op(","):
            args.append(self._named_or_pos())
        return args

    def _named_or_pos(self) -> str:
        """One call argument, allowing Oracle named-parameter association
        ``name => value``. Named params appear ONLY in package/procedure
        calls (which have no VB equivalent and resolve to an unresolved
        placeholder anyway), so we consume the ``=>`` and keep just the
        value — without this the lone ``>`` crashed the whole formula
        parse, dropping otherwise-computable IF/CASE logic around the call
        (wild-corpus verified: CF_Permittees etc.)."""
        save = self.i
        k, v = self._peek()
        if k == "id":
            self._next()
            # Oracle named-param association is '=' immediately followed by
            # '>' (tokenized separately). Only treat as a name when both
            # appear back-to-back with the value following.
            if self._peek() == ("op", "=") and \
                    self.i + 1 < len(self.toks) and self.toks[self.i + 1] == ("op", ">"):
                self.i += 2  # consume '=' '>'
                return self._or()
            self.i = save  # not a named arg -> reparse as a normal expression
        return self._or()

    def _case(self) -> str:
        # CASE WHEN c THEN r ... [ELSE e] END   (searched CASE)
        self._eat_kw("CASE")
        whens = []
        while self._eat_kw("WHEN"):
            cond = self._or()
            if not self._eat_kw("THEN"):
                raise ValueError("expected THEN")
            whens.append((cond, self._or()))
        els = "Nothing"
        if self._eat_kw("ELSE"):
            els = self._or()
        if not self._eat_kw("END"):
            raise ValueError("expected END")
        out = els
        for c, r in reversed(whens):
            out = f"IIf({c}, {r}, {out})"
        return out

    # -- function translation --
    def _func(self, name: str, args: List[str]) -> str:
        n = name.upper()
        a = args

        def need(k):
            if len(a) < k:
                raise ValueError(f"{n} needs {k} args")

        if n == "NVL":
            need(2); return f"IIf(IsNothing({a[0]}), {a[1]}, {a[0]})"
        if n == "NVL2":
            need(3); return f"IIf(IsNothing({a[0]}), {a[2]}, {a[1]})"
        if n == "COALESCE":
            need(1)
            out = a[-1]
            for x in reversed(a[:-1]):
                out = f"IIf(IsNothing({x}), {out}, {x})"
            return out
        if n == "DECODE":
            need(3)
            e = a[0]
            rest = a[1:]
            default = "Nothing"
            pairs = []
            j = 0
            while j + 1 < len(rest):
                pairs.append((rest[j], rest[j + 1])); j += 2
            if j < len(rest):
                default = rest[j]
            out = default
            for s, r in reversed(pairs):
                out = f"IIf({e} = {s}, {r}, {out})"
            return out
        if n in ("TO_CHAR", "TO_NCHAR"):
            if len(a) >= 2:
                return f"Format({a[0]}, {_oracle_fmt_to_net(a[1])})"
            return f"CStr({a[0]})"
        if n in ("TO_NUMBER",):
            need(1); return f"CDbl({a[0]})"
        if n in ("TO_DATE",):
            need(1); return f"CDate({a[0]})"
        if n == "SUBSTR":
            need(2)
            return f"Mid({a[0]}, {a[1]}, {a[2]})" if len(a) >= 3 else f"Mid({a[0]}, {a[1]})"
        if n == "INSTR":
            need(2); return f"InStr({a[0]}, {a[1]})"
        if n in ("LENGTH", "LENGTHB"):
            need(1); return f"Len({a[0]})"
        if n == "UPPER":
            need(1); return f"UCase({a[0]})"
        if n == "LOWER":
            need(1); return f"LCase({a[0]})"
        if n == "INITCAP":
            need(1); return f"StrConv({a[0]}, VbStrConv.ProperCase)"
        if n == "TRIM":
            need(1); return f"Trim({a[0]})"
        if n == "LTRIM":
            need(1); return f"LTrim({a[0]})" if len(a) == 1 else f"{a[0]}.TrimStart()"
        if n == "RTRIM":
            need(1); return f"RTrim({a[0]})" if len(a) == 1 else f"{a[0]}.TrimEnd()"
        if n == "REPLACE":
            need(2)
            return f"Replace({a[0]}, {a[1]}, {a[2]})" if len(a) >= 3 else f"Replace({a[0]}, {a[1]}, \"\")"
        if n == "LPAD":
            need(2); return f"{a[0]}.PadLeft({a[1]})"
        if n == "RPAD":
            need(2); return f"{a[0]}.PadRight({a[1]})"
        if n == "CONCAT":
            need(2); return f"({a[0]} & {a[1]})"
        if n == "CHR":
            need(1)
            lit = {"10": "vbLf", "13": "vbCr", "9": "vbTab", "32": "\" \""}
            return lit.get(a[0].strip(), f"Chr({a[0]})")
        if n == "ROUND":
            need(1); return f"Math.Round({a[0]}, {a[1]})" if len(a) >= 2 else f"Math.Round({a[0]})"
        if n == "TRUNC" and len(a) == 1:
            return f"Int({a[0]})"
        if n == "FLOOR":
            need(1); return f"Math.Floor({a[0]})"
        if n in ("CEIL", "CEILING"):
            need(1); return f"Math.Ceiling({a[0]})"
        if n == "ABS":
            need(1); return f"Math.Abs({a[0]})"
        if n in ("POWER",):
            need(2); return f"({a[0]} ^ {a[1]})"
        if n == "MOD":
            need(2); return f"({a[0]} Mod {a[1]})"
        if n == "SIGN":
            need(1); return f"Math.Sign({a[0]})"
        if n == "GREATEST":
            need(2)
            out = a[0]
            for x in a[1:]:
                out = f"IIf({x} > {out}, {x}, {out})"
            return out
        if n == "LEAST":
            need(2)
            out = a[0]
            for x in a[1:]:
                out = f"IIf({x} < {out}, {x}, {out})"
            return out
        # Unknown function (e.g. an external package fn Pkg_X.F_Y) -- cannot
        # compute deterministically. Record it; the caller will keep a
        # placeholder rather than ship a broken expression.
        self.unresolved.append(name)
        return f"{name}({', '.join(a)})"


def _oracle_fmt_to_net(vb_str_literal: str) -> str:
    """Translate a TO_CHAR format-mask string literal (already a VB "..." literal)
    to a .NET Format() mask. Conservative -- common date + number masks."""
    if not (vb_str_literal.startswith('"') and vb_str_literal.endswith('"')):
        return vb_str_literal
    f = vb_str_literal[1:-1]
    up = f.upper()
    # dates
    if any(t in up for t in ("YYYY", "YY", "MON", "DD", "HH", "MI", "SS")):
        repl = [("YYYY", "yyyy"), ("YY", "yy"), ("MONTH", "MMMM"), ("MON", "MMM"),
                ("DAY", "dddd"), ("DY", "ddd"), ("DD", "dd"), ("HH24", "HH"),
                ("HH", "hh"), ("MI", "mm"), ("SS", "ss"), ("AM", "tt"), ("PM", "tt")]
        out = up
        for o, nrep in repl:
            out = out.replace(o, "\x00" + nrep + "\x01")
        out = out.replace("\x00", "").replace("\x01", "")
        # MM (month) -- only the leftover MM not already consumed
        out = re.sub(r"MM", "MM", out)
        return '"' + out + '"'
    # numbers: 9/0 -> #/0 ; keep , . $ %
    if re.search(r"[90]", f):
        net = f.replace("9", "#")
        return '"' + net + '"'
    return vb_str_literal


def translate_expr(oracle_expr: str, resolve: Optional[Callable[[str], str]] = None) -> dict:
    """Translate a single Oracle expression to a VB.NET expression string."""
    resolve = resolve or _default_resolve
    try:
        toks = _tokenize(oracle_expr)
        p = _Parser(toks, resolve)
        vb = p.parse()
        return {"vb": vb, "ok": not p.unresolved, "unresolved": p.unresolved}
    except Exception as e:  # noqa: BLE001
        return {"vb": None, "ok": False, "unresolved": [], "error": str(e)}


# ---------------------------------------------------------------------------
# Statement layer: reduce a formula body to ONE effective return expression
# ---------------------------------------------------------------------------
def _split_statements(body: str) -> List[str]:
    """Split on ';' that are NOT inside parentheses or string literals."""
    out, buf, depth, i = [], [], 0, 0
    while i < len(body):
        c = body[i]
        if c == "'":
            j = i + 1
            while j < len(body):
                if body[j] == "'":
                    if j + 1 < len(body) and body[j + 1] == "'":
                        j += 2; continue
                    break
                j += 1
            buf.append(body[i:j + 1]); i = j + 1; continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        if c == ";" and depth == 0:
            out.append("".join(buf).strip()); buf = []; i += 1; continue
        buf.append(c); i += 1
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return [s for s in out if s]


def _split_top_level(body: str) -> List[str]:
    """Split into top-level statements at ';' where paren-depth==0 AND if-depth
    ==0, so a whole ``IF .. END IF`` (with its inner ';'s) is ONE statement.
    String literals are matched and skipped so a ';' inside them never splits."""
    out, start, pd, ifd = [], 0, 0, 0
    for m in re.finditer(r"'(?:''|[^'])*'|\bEND\s+IF\b|\bELSIF\b|\bIF\b|\(|\)|;",
                         body, re.IGNORECASE):
        g = m.group()
        if g.startswith("'"):
            continue
        if g == "(":
            pd += 1
        elif g == ")":
            pd = max(0, pd - 1)
        elif re.match(r"(?i)END\s+IF", g):
            ifd = max(0, ifd - 1)
        elif g.upper() == "IF":
            ifd += 1
        elif g == ";" and pd == 0 and ifd == 0:
            out.append(body[start:m.start()].strip())
            start = m.end()
    tail = body[start:].strip()
    if tail:
        out.append(tail)
    return [s for s in out if s]


_RETURN_RE = re.compile(r"^\s*RETURN\s*(.*)$", re.IGNORECASE | re.DOTALL)
_ASSIGN_RE = re.compile(r"^\s*([A-Za-z_]\w*)\s*:=\s*(.*)$", re.DOTALL)
_IFBLOCK_RE = re.compile(r"(?is)^\s*IF\b(.*)\bEND\s+IF\s*$")


def _return_oracle_expr(body: str) -> Optional[str]:
    """Reduce the (comment-free) body to ONE Oracle return expression. Handles a
    direct RETURN, an IF/ELSIF/ELSE whose branches RETURN (-> nested CASE), and
    LOCAL variables assigned either by a plain ``:=`` or by an IF block (each
    folded into a CASE and substituted into the return, transitively). Returns
    None if not reducible (loops / cursors / multi-output)."""
    stmts = _split_top_level(body.strip())
    subs: Dict[str, str] = {}
    ret_expr: Optional[str] = None

    def _branch_single_local(branches) -> Optional[str]:
        names = set()
        parts = [st for _c, st in branches["cond"]]
        if branches["else"]:
            parts.append(branches["else"])
        for st in parts:
            for ss in _split_top_level(st):
                am = _ASSIGN_RE.match(ss)
                if am and not am.group(1).startswith(":"):
                    names.add(am.group(1).upper())
        return next(iter(names)) if len(names) == 1 else None

    for s in stmts:
        ifm = _IFBLOCK_RE.match(s)
        if not ifm:
            rm = _RETURN_RE.match(s)
            if rm:
                ret_expr = _strip_outer_parens(rm.group(1).strip().rstrip(";").strip())
                continue
            am = _ASSIGN_RE.match(s)
            if am and not am.group(1).startswith(":"):
                subs.setdefault(am.group(1).upper(), am.group(2).strip())
            continue
        branches = _parse_if(ifm.group(1))
        if branches is None:
            continue
        rets = [(c, _ret_of(st)) for c, st in branches["cond"]]
        else_ret = _ret_of(branches["else"]) if branches["else"] is not None else None
        if rets and all(r is not None for _, r in rets):
            ret_expr = _build_case(rets, else_ret if else_ret is not None else "NULL")
            continue
        var = _branch_single_local(branches)
        if var:
            rets2 = [(c, _assign_of(st, var)) for c, st in branches["cond"]]
            else2 = _assign_of(branches["else"], var) if branches["else"] is not None else None
            if all(r is not None for _, r in rets2):
                subs.setdefault(var, _build_case(rets2, else2 if else2 is not None else "NULL"))

    if ret_expr is None:
        return None
    for _ in range(5):  # substitute locals into the return, transitively
        nxt = ret_expr
        for var, val in subs.items():
            nxt = re.sub(rf"(?<![\w:])\b{re.escape(var)}\b(?!\w)",
                         "(" + val + ")", nxt, flags=re.IGNORECASE)
        if nxt == ret_expr:
            break
        ret_expr = nxt
    return ret_expr


def _parse_if(text: str):
    """Parse 'cond THEN stmts (ELSIF cond THEN stmts)* (ELSE stmts)?' (END IF
    already stripped). Returns {'cond': [(cond, stmts_str)...], 'else': str|None}."""
    parts = re.split(r"(?i)\bELSIF\b", text)
    cond_branches = []
    else_part = None
    first = parts[0]
    m = re.match(r"(?is)^\s*(.*?)\bTHEN\b(.*)$", first)
    if not m:
        return None
    head_cond = m.group(1).strip()
    rest = m.group(2)
    em = re.split(r"(?i)\bELSE\b", rest, maxsplit=1)
    cond_branches.append((head_cond, em[0].strip()))
    if len(em) > 1:
        else_part = em[1].strip()
    for pr in parts[1:]:
        mm = re.match(r"(?is)^\s*(.*?)\bTHEN\b(.*)$", pr)
        if not mm:
            return None
        c = mm.group(1).strip()
        body = mm.group(2)
        ee = re.split(r"(?i)\bELSE\b", body, maxsplit=1)
        cond_branches.append((c, ee[0].strip()))
        if len(ee) > 1:
            else_part = ee[1].strip()
    return {"cond": cond_branches, "else": else_part}


def _ret_of(stmts_str: str) -> Optional[str]:
    for s in _split_statements(stmts_str):
        rm = _RETURN_RE.match(s)
        if rm:
            return _strip_outer_parens(rm.group(1).strip().rstrip(";").strip())
    return None


def _assign_of(stmts_str: Optional[str], var: str) -> Optional[str]:
    if stmts_str is None:
        return None
    for s in _split_statements(stmts_str):
        am = _ASSIGN_RE.match(s)
        if am and am.group(1).upper() == var.upper():
            return am.group(2).strip()
    return None


def _trailing_return(trailing: str) -> Optional[str]:
    return _ret_of(trailing)


def _trailing_return_var(trailing: str) -> Optional[str]:
    r = _ret_of(trailing)
    if r and re.match(r"^[A-Za-z_]\w*$", r.strip()):
        return r.strip()
    return None


def _build_case(cond_rets, else_ret) -> str:
    """Build a nested Oracle CASE expression string from (cond, ret) pairs."""
    out = else_ret if else_ret is not None else "NULL"
    parts = "".join(f" WHEN ({c}) THEN ({r})" for c, r in cond_rets)
    return f"CASE{parts} ELSE ({out}) END"


def _strip_outer_parens(s: str) -> str:
    s = s.strip()
    while s.startswith("(") and s.endswith(")"):
        depth = 0
        ok = True
        for idx, c in enumerate(s):
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0 and idx != len(s) - 1:
                    ok = False
                    break
        if ok:
            s = s[1:-1].strip()
        else:
            break
    return s


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def extract_placeholder_assignments(plsql_body: str) -> Dict[str, str]:
    """Recover Oracle PLACEHOLDER-column outputs (``:CP_X := expr``) that a CF_
    formula computes as side-effects. One CF_ function often sets several CP_
    columns; those are referenced elsewhere in the layout but would otherwise
    stay blank. Returns ``{CP_NAME_UPPER: oracle_expr}``.

    Handles the build-up pattern ``:CP_X := :CP_X || '...'`` by folding the prior
    value in, and CONDITIONAL assignments inside an ``IF/ELSIF/ELSE`` by folding
    the branch values into a CASE (so e.g. ``IF :n=1 THEN :CP_U:='IS ...' ELSE
    :CP_U:='ARE ...'`` recovers as ``CASE WHEN :n=1 THEN 'IS ...' ELSE 'ARE ...'
    END``). Cross-references between placeholders (``:CP_L := LOWER(:CP_U)``) are
    folded transitively. The caller still translates + scope-checks every
    expression, so any value whose condition/refs don't fully resolve falls back
    to the placeholder -- a broken or out-of-scope expression never ships."""
    src = _strip_comments(plsql_body or "")
    body = _body_between_begin_end(src)
    out: Dict[str, str] = {}

    def _ph_assign_in(stmts_str, cpname):
        if stmts_str is None:
            return None
        for ss in _split_statements(stmts_str):
            m = re.match(r"(?is)^\s*:([A-Za-z_]\w*)\s*:=\s*(.*)$", ss)
            if m and m.group(1).upper() == cpname.upper():
                return m.group(2).strip().rstrip(";").strip()
        return None

    for s in _split_top_level(body):
        ifm = _IFBLOCK_RE.match(s)
        if ifm:
            branches = _parse_if(ifm.group(1))
            if not branches:
                continue
            parts = [st for _c, st in branches["cond"]]
            if branches["else"] is not None:
                parts.append(branches["else"])
            cpnames = set()
            for st in parts:
                for ss in _split_statements(st or ""):
                    m = re.match(r"(?is)^\s*:(CP_[A-Za-z0-9_]*)\s*:=", ss)
                    if m:
                        cpnames.add(m.group(1).upper())
            for cp in cpnames:
                cond_vals = [(c, _ph_assign_in(st, cp)) for c, st in branches["cond"]]
                else_val = _ph_assign_in(branches["else"], cp) if branches["else"] is not None else None
                # every conditional branch must set it, else the value is ambiguous
                if not all(v is not None for _, v in cond_vals):
                    continue
                vals = [v for _, v in cond_vals] + [else_val]
                if else_val is not None and len(set(vals)) == 1:
                    # set to the SAME value in every branch incl. ELSE -> the
                    # condition is irrelevant; emit it unconditionally.
                    out[cp] = else_val
                else:
                    # Genuinely conditional (different per branch, or no ELSE so
                    # it's blank when the condition is false). Build a CASE with
                    # an explicit ELSE NULL so a non-matching row shows blank --
                    # exactly Oracle's behaviour, never a wrong value.
                    out[cp] = _build_case(cond_vals,
                                          else_val if else_val is not None else "NULL")
            continue
        am = re.match(r"(?is)^\s*:([A-Za-z_]\w*)\s*:=\s*(.*)$", s)
        if not am:
            continue
        name = am.group(1)
        if not name.upper().startswith("CP_"):
            continue
        expr = am.group(2).strip().rstrip(";").strip()
        prior = out.get(name.upper())
        if prior is not None:  # build-up: :CP_X := :CP_X || ... -> fold prior in
            expr = re.sub(rf":{re.escape(name)}\b", "(" + prior + ")",
                          expr, flags=re.IGNORECASE)
        out[name.upper()] = expr

    # Fold cross-placeholder references (:CP_L := LOWER(:CP_U)) transitively so
    # every value stands on its own (references only real binds/fields/summaries).
    for _ in range(6):
        changed = False
        for k in list(out.keys()):
            def _sub(m, _k=k):
                ref = m.group(1).upper()
                if ref in out and ref != _k:
                    return "(" + out[ref] + ")"
                return m.group(0)
            new = re.sub(r":([A-Za-z_]\w*)", _sub, out[k])
            if new != out[k]:
                out[k] = new
                changed = True
        if not changed:
            break
    return out


def translate_formula_to_vb(plsql_body: str,
                            resolve: Optional[Callable[[str], str]] = None) -> dict:
    """Translate a full Oracle formula function body to an SSRS VB.NET expression.

    Returns {expr, ok, notes, unresolved}:
      expr        -- '=...' VB expression (or None)
      ok          -- True only if fully translated with no unknown calls; the
                     caller keeps its placeholder otherwise (never ships broken)
      notes       -- human-readable explanation
      unresolved  -- external functions / names that blocked a clean translate
    """
    resolve = resolve or _default_resolve
    notes: List[str] = []
    if not plsql_body or not plsql_body.strip():
        return {"expr": None, "ok": False, "notes": ["empty body"], "unresolved": []}

    src = _strip_comments(plsql_body)
    body = _body_between_begin_end(src)
    oracle_expr = _return_oracle_expr(body)
    if not oracle_expr:
        return {"expr": None, "ok": False,
                "notes": ["return logic not reducible (loops/cursors/multi-output)"],
                "unresolved": []}

    res = translate_expr(oracle_expr, resolve)
    if res.get("vb") is None:
        return {"expr": None, "ok": False,
                "notes": ["expression did not parse: " + str(res.get("error", ""))],
                "unresolved": res.get("unresolved", [])}

    vb = res["vb"]
    vb = _strip_redundant_outer(vb)
    ok = res["ok"]
    if not ok:
        notes.append("contains an external/unknown call that cannot be computed: "
                     + ", ".join(sorted(set(res["unresolved"]))))
    return {"expr": "=" + vb, "ok": ok, "notes": notes,
            "unresolved": res.get("unresolved", [])}


def _strip_redundant_outer(vb: str) -> str:
    if vb.startswith("(") and vb.endswith(")"):
        depth = 0
        for idx, c in enumerate(vb):
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0 and idx != len(vb) - 1:
                    return vb
        return vb[1:-1]
    return vb


def translate_format_trigger(body: str,
                             resolve: Optional[Callable[[str], str]] = None
                             ) -> Optional[str]:
    """Reduce an Oracle FORMAT TRIGGER (a boolean show/hide function) to an
    SSRS ``<Hidden>`` VB expression, or None when the body is beyond the
    supported patterns (the caller keeps today's behavior + an honest note).

    Truth table: Oracle ``RETURN TRUE`` = PRINT the object, ``FALSE`` =
    suppress it; SSRS ``Hidden=True`` = suppress. So Hidden = NOT(result).
    Supported shapes (the overwhelming wild-corpus majority):
      RETURN <boolean-expr>;
      IF <cond> THEN RETURN FALSE|TRUE; [ELSE RETURN ...;] END IF;
      [RETURN TRUE|FALSE;]
    """
    if not (body or "").strip():
        return None
    # LOCAL BOOLEAN CONSTANTS fold before matching. A debug switch is the
    # classic shape: `cbDEBUG CONSTANT BOOLEAN := FALSE;` then
    # `ELSIF cbDEBUG THEN RETURN TRUE` — after substitution the branch is a
    # literal and the chain collapses like any other (wild-corpus: an
    # invoice's debug frame otherwise printed unconditionally over the
    # letter, because its trigger declined and got no Hidden at all).
    consts = {}
    for cm in re.finditer(
            r"(?is)\b([A-Za-z_]\w*)\s+CONSTANT\s+BOOLEAN\s*:=\s*"
            r"(TRUE|FALSE)\s*;", body):
        consts[cm.group(1).upper()] = cm.group(2).upper()
    src = _strip_comments(_body_between_begin_end(body))
    up = re.sub(r"\s+", " ", src).strip().rstrip(";").strip()
    if consts:
        for cname, cval in consts.items():
            up = re.sub(rf"(?i)\b{re.escape(cname)}\b", cval, up)

    # IF/ELSIF/ELSE chains where every branch is RETURN TRUE|FALSE reduce
    # to nested IIfs. The single-IF pattern below misses them, and a
    # declined multi-branch trigger means NO Hidden — the conditional
    # email-header variant then prints straight over the print-letter
    # title (engine-render verified on a production invoice).
    chain = re.fullmatch(
        r"(?is)IF\s+(.+?)\s+THEN\s+RETURN\s*\(?\s*(TRUE|FALSE)\s*\)?\s*;"
        r"((?:\s*ELSIF\s+.+?\s+THEN\s+RETURN\s*\(?\s*(?:TRUE|FALSE)\s*\)?\s*;)+)"
        r"(?:\s*ELSE\s+RETURN\s*\(?\s*(TRUE|FALSE)\s*\)?\s*;?)?\s*END\s*IF"
        r"\s*;?(?:\s*RETURN\s*\(?\s*(TRUE|FALSE)\s*\)?)?", up)
    if chain:
        branches = [(chain.group(1), chain.group(2).upper())]
        for bm in re.finditer(
                r"(?is)ELSIF\s+(.+?)\s+THEN\s+RETURN\s*\(?\s*(TRUE|FALSE)"
                r"\s*\)?\s*;", chain.group(3)):
            branches.append((bm.group(1), bm.group(2).upper()))
        default = (chain.group(4) or chain.group(5) or "FALSE").upper()
        # fold literal-TRUE/FALSE conditions (post constant substitution)
        vb = "True" if default == "TRUE" else "False"
        ok = True
        for cond, ret in reversed(branches):
            cu = cond.strip().upper()
            if cu in ("TRUE", "(TRUE)"):
                vb = "True" if ret == "TRUE" else "False"
                continue
            if cu in ("FALSE", "(FALSE)"):
                continue
            r = translate_expr(cond, resolve)
            if not r.get("ok") or not r.get("vb"):
                ok = False
                break
            ret_vb = "True" if ret == "TRUE" else "False"
            vb = f"IIf({r['vb']}, {ret_vb}, {vb})"
        if ok:
            if vb == "True":
                return None       # unconditionally shown
            if vb == "False":
                return "=True"    # unconditionally suppressed
            return f"=Not({vb})"
        return None
    m = re.fullmatch(
        r"(?is)IF\s+(.+?)\s+THEN\s+RETURN\s*\(?\s*(TRUE|FALSE)\s*\)?\s*;?"
        r"(?:\s*ELSE\s+RETURN\s*\(?\s*(TRUE|FALSE)\s*\)?\s*;?)?\s*END\s*IF"
        r"\s*;?(?:\s*RETURN\s*\(?\s*(TRUE|FALSE)\s*\)?)?", up)
    if m:
        cond = m.group(1)
        then_v = m.group(2).upper()
        other = (m.group(3) or m.group(4) or
                 ("TRUE" if then_v == "FALSE" else "FALSE")).upper()
        if then_v == other:
            return None if then_v == "TRUE" else "=True"
        r = translate_expr(cond, resolve)
        if not r.get("ok") or not r.get("vb"):
            return None
        return (f"=({r['vb']})" if then_v == "FALSE"
                else f"=Not({r['vb']})")
    m2 = re.fullmatch(r"(?is)RETURN\s+(.+)", up)
    if m2:
        val = m2.group(1).strip().rstrip(";").strip()
        if val.upper() in ("TRUE", "(TRUE)"):
            return None            # unconditionally shown: no Hidden needed
        if val.upper() in ("FALSE", "(FALSE)"):
            return "=True"
        r = translate_expr(val, resolve)
        if r.get("ok") and r.get("vb"):
            return f"=Not({r['vb']})"
    return None


# --------------------------------------------------------------------------
# ROW-ALTERNATION (banded record) conditional fill
# --------------------------------------------------------------------------
#
# Oracle bands a repeating record frame by counting records into a summary
# column and testing its parity in the frame's format trigger.  The SHAPE is
# what identifies it -- a ``MOD 2`` comparison guarding SRW fill calls -- so
# the detector below matches structure only and never a counter, column or
# report name.  Everything outside the shape declines, which keeps a report
# that declares no such trigger completely unbanded.

# ``<counter> MOD 2 <op> <0|1>``.  The counter may be a bind (``:X``), a
# plain identifier, or a qualified one; only its PARITY test matters.
_MOD2_COND_RE = re.compile(
    r"(?is)^\(?\s*:?([A-Za-z_][\w.$#]*)\s+MOD\s+2\s*(=|<>|!=)\s*([01])\s*\)?$")

_SRW_FILL_CALLS = ("set_foreground_fill_color", "set_background_fill_color",
                   "set_fill_pattern")

_TRANSPARENT_PATTERNS = ("transparent", "no_fill", "nofill", "none")


class _NotAFillBranch(Exception):
    """The branch does something other than set a fill."""


def _alternation_branch_fill(calls_src: str):
    """One IF branch's SRW calls -> the colour token that branch paints.

    ``""`` = explicitly transparent, ``None`` = the branch declares no
    fill decision at all.  Raises when the branch contains anything that
    is not a fill call, so a trigger that also hides/bolds never turns
    into a band.
    """
    fg = bg = None
    pattern = None
    seen = 0
    for cm in re.finditer(r"(?is)srw\s*\.\s*(\w+)\s*\(\s*([^)]*)\s*\)\s*;?",
                          calls_src):
        fn = cm.group(1).lower()
        arg = cm.group(2).strip().strip("'\"").strip()
        if fn not in _SRW_FILL_CALLS:
            raise _NotAFillBranch(fn)
        seen += 1
        if fn == "set_fill_pattern":
            pattern = arg.lower()
        elif fn == "set_foreground_fill_color":
            fg = arg
        else:
            bg = arg
    residue = re.sub(r"(?is)srw\s*\.\s*\w+\s*\([^)]*\)\s*;?", "", calls_src)
    if residue.strip():
        raise _NotAFillBranch("non-call residue")
    if not seen:
        return None
    if pattern in _TRANSPARENT_PATTERNS:
        return ""
    if pattern != "solid":
        # DIALECT: the fill pattern is what gates the paint.  With no solid
        # pattern declared in the branch there is no paint decision to make.
        return None
    # DIALECT: under a SOLID pattern the FOREGROUND fill colour is the ink
    # that covers the object (the background colour only shows through a
    # patterned fill).
    return fg if fg is not None else bg


def translate_row_alternation_fill(body: str) -> Optional[dict]:
    """Oracle's banded-record format trigger -> its two alternating fills.

    Recognised STRUCTURALLY::

        IF <counter> MOD 2 = 0 THEN
          SRW.SET_FOREGROUND_FILL_COLOR('<tint>');
          SRW.SET_FILL_PATTERN('solid');
        ELSE
          SRW.SET_FOREGROUND_FILL_COLOR('<base>');
          SRW.SET_FILL_PATTERN('solid');
        END IF;
        RETURN(TRUE);

    Returns ``{"even": <token>, "odd": <token>}`` -- ``even`` is the fill
    for a counter whose value satisfies ``MOD 2 = 0`` (Oracle's counters
    are 1-based running counts, so ``even`` bands the SECOND record and
    every other one after it).  Each value is an Oracle colour token,
    ``""`` for an explicitly transparent branch, or None where that branch
    declares no fill.  Returns None for every other trigger body.
    """
    if not (body or "").strip():
        return None
    src = _strip_comments(_body_between_begin_end(body))
    up = re.sub(r"\s+", " ", src).strip()
    tail = r"\s*END\s*IF\s*;?\s*RETURN\s*\(?\s*TRUE\s*\)?\s*;?"
    m = re.fullmatch(
        r"(?is)IF\s+(.+?)\s+THEN\s+(.*?)\s*ELSE\s+(.*?)" + tail, up)
    if m:
        cond, then_src, else_src = m.group(1), m.group(2), m.group(3)
    else:
        m = re.fullmatch(r"(?is)IF\s+(.+?)\s+THEN\s+(.*?)" + tail, up)
        if not m:
            return None
        cond, then_src, else_src = m.group(1), m.group(2), ""
    cm = _MOD2_COND_RE.match(cond.strip())
    if not cm:
        return None
    op, rhs = cm.group(2), cm.group(3)
    # THEN fires on the EVEN parity when the test is "= 0" or "<> 1".
    then_is_even = ((op == "=" and rhs == "0")
                    or (op in ("<>", "!=") and rhs == "1"))
    try:
        then_fill = _alternation_branch_fill(then_src)
        else_fill = _alternation_branch_fill(else_src) if else_src else None
    except _NotAFillBranch:
        return None
    if then_fill is None and else_fill is None:
        return None
    if then_is_even:
        return {"even": then_fill, "odd": else_fill}
    return {"even": else_fill, "odd": then_fill}


_SRW_STYLE_MAP = {
    "set_font_face": ("FontFamily", lambda a: a.strip("'\" ")),
    "set_font_size": ("FontSize", lambda a: f"{a.strip()}pt"),
    "set_font_weight": ("FontWeight",
                        lambda a: "Bold" if "BOLD" in a.upper() else "Normal"),
    # RECOGNIZED, NO SSRS MAPPING. srw.set_font_style('ITALIC') asks for an
    # oblique face the Oracle export dialect never paints: 16 truth PDFs /
    # 142,831 spans carry ZERO italic-flagged spans and no *-Oblique font
    # resource, while bold IS honoured (see models.ORACLE_RENDERS_ITALIC).
    # Emitting FontStyle here would make the SSRS render diverge from the
    # truth exactly where a trigger fires. 'PLAIN' is a no-op for the same
    # reason. Other srw style calls in the same trigger still translate.
    "set_font_style": (None, None),
    "set_text_color": ("Color", lambda a: a.strip("'\" ").title()),
    "set_foreground_fill_color": ("BackgroundColor",
                                  lambda a: a.strip("'\" ").title()),
    "set_fill_pattern": (None, None),        # recognized, no SSRS mapping
    "set_charmode_text": (None, None),
}


def translate_format_trigger_style(body: str,
                                   resolve: Optional[
                                       Callable[[str], str]] = None
                                   ) -> Optional[tuple]:
    """Reduce a conditional-STYLING format trigger — the dominant wild
    pattern ``if (<cond>) then srw.set_font_weight(...); ... end if;
    return (true);`` — to ``(cond_vb, {ssrs_style_prop: value})``.
    Returns None for anything else (incl. visibility triggers, which
    translate_format_trigger handles)."""
    if not (body or "").strip():
        return None
    src = _strip_comments(_body_between_begin_end(body))
    up = re.sub(r"\s+", " ", src).strip()
    m = re.fullmatch(
        r"(?is)IF\s*\(?\s*(.+?)\s*\)?\s*THEN\s+(.*?)\s*END\s*IF\s*;?"
        r"\s*RETURN\s*\(?\s*TRUE\s*\)?\s*;?", up)
    if not m:
        return None
    cond, calls_src = m.group(1), m.group(2)
    styles = {}
    ok_calls = 0
    for cm in re.finditer(r"(?is)srw\s*\.\s*(\w+)\s*\(\s*([^)]*)\s*\)\s*;?",
                          calls_src):
        fn, arg = cm.group(1).lower(), cm.group(2)
        if fn not in _SRW_STYLE_MAP:
            return None            # unknown srw call: stay conservative
        prop, conv = _SRW_STYLE_MAP[fn]
        ok_calls += 1
        if prop:
            try:
                styles[prop] = conv(arg)
            except Exception:  # noqa: BLE001
                return None
    # The THEN block must be srw calls ONLY (no assignments/returns hiding).
    residue = re.sub(r"(?is)srw\s*\.\s*\w+\s*\([^)]*\)\s*;?", "", calls_src)
    if residue.strip() or not ok_calls or not styles:
        return None
    r = translate_expr(cond, resolve)
    if not r.get("ok") or not r.get("vb"):
        return None
    return (r["vb"], styles)


def cursor_formula_to_subquery(body: str, outer_cols, param_names
                               ) -> Optional[str]:
    """Translate a SINGLE-FETCH cursor formula (the dominant Oracle CF_
    pattern: CURSOR c IS SELECT ...; OPEN c; FETCH c INTO rec; RETURN ...)
    into a scalar correlated subquery against the wrapper alias ``O``:

        (SELECT MAX(<expr>) [KEEP (DENSE_RANK FIRST ORDER BY <order>)]
           FROM <from> WHERE <where, :col -> O.col>)

    ``MAX(...) KEEP (DENSE_RANK FIRST ...)`` reproduces first-row-by-order
    at correlation depth 1 (a nested ROWNUM=1 subquery cannot see O).
    Conservative: multi-column cursors decline unless RETURN names the
    column; LOOP/concat bodies decline (kept as honest NULL stubs)."""
    src = _strip_comments(body or "")
    if re.search(r"(?i)\b(LOOP|WHILE|FOR\s+\w+\s+IN)\b", src):
        return None
    m = re.search(r"(?is)\bCURSOR\s+(\w+)\s+IS\s+(SELECT\b.*?);", src)
    if not m:
        return None
    if len(re.findall(r"(?i)\bCURSOR\b", src)) != 1:
        return None
    sel = m.group(2).strip()
    if not re.search(r"(?i)\bFETCH\b", src):
        return None
    # Split the cursor SELECT: list / FROM / WHERE / ORDER BY (top level).
    ms = re.match(r"(?is)SELECT\s+(.*?)\s+FROM\s+(.*)$", sel)
    if not ms:
        return None
    sel_list, rest = ms.group(1), ms.group(2)
    mo = re.search(r"(?is)\bORDER\s+BY\s+(.*)$", rest)
    order = mo.group(1).strip().rstrip(";") if mo else ""
    if mo:
        rest = rest[:mo.start()].strip()
    # Multiple select items? Need RETURN rec.<col> to pick one; otherwise
    # decline. (Top-level comma split.)
    depth = 0
    items = []
    cur = []
    for ch in sel_list:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            items.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    items.append("".join(cur))
    expr = items[0].strip()
    if len(items) > 1:
        mr = re.search(r"(?is)\bRETURN\s+\w+\.(\w+)", src)
        if not mr:
            return None
        want = mr.group(1).upper()
        pick = None
        for it in items:
            it = it.strip()
            alias = re.search(r"([A-Za-z_][A-Za-z0-9_]*)\s*$", it)
            base = re.search(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*$",
                             it.split(".")[-1])
            if (alias and alias.group(1).upper() == want) or \
                    (base and base.group(1).upper() == want):
                pick = it
                break
        if pick is None:
            return None
        expr = pick
    # Strip a trailing alias from the picked expression (invalid inside MAX).
    expr = re.sub(r"\s+[A-Za-z_][A-Za-z0-9_]*\s*$",
                  "", expr) if re.search(
        r"(?:\)|'|[A-Za-z0-9_])\s+[A-Za-z_][A-Za-z0-9_]*\s*$", expr) \
        and not re.search(r"(?i)\b(END|NULL)\s*$", expr) else expr
    # Rewrite :BIND -> O.BIND when BIND is an outer column; parameter binds
    # stay binds; any OTHER bind (unknown local) -> decline.
    ocols = {c.upper() for c in (outer_cols or [])}
    pnames = {p.upper() for p in (param_names or [])}
    bad = []

    def _bind(mm):
        nm = mm.group(1)
        if nm.upper() in ocols:
            return f"O.{nm}"
        if nm.upper() in pnames:
            return mm.group(0)
        bad.append(nm)
        return mm.group(0)
    rest2 = re.sub(r":([A-Za-z_][A-Za-z0-9_]*)", _bind, rest)
    if bad:
        return None
    agg = (f"MAX({expr}) KEEP (DENSE_RANK FIRST ORDER BY {order})"
           if order else f"MAX({expr})")
    return f"(SELECT {agg}\n   FROM {rest2})"
