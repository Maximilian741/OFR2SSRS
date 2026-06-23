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
