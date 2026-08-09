"""Oracle Reports declarative ``<conditionalFormat>`` -> SSRS conditional style.

WHY THIS EXISTS (measured, not assumed)
---------------------------------------
Reports Builder stores conditional formatting in TWO places, and they carry
DIFFERENT information:

  * ``<advancedLayout formatTrigger="f_x"/>`` -> a PL/SQL function that
    returns only TRUE/FALSE.  That is a VISIBILITY decision and nothing
    more -- ``plsql_formula.translate_format_trigger`` already handles it.
  * ``<generalLayout><conditionalFormat>`` -> the declarative block that
    holds the actual FORMAT to apply (bold, text colour, fill, font).

The bold/colour/fill is present ONLY in the declarative block.  A survey of
the wild corpus found 544 ``<conditionalFormat>`` blocks over 31 reports
carrying 432 conditional bolds, 235 conditional fills and 160 conditional
text colours -- none of which the PL/SQL path can ever recover, because the
trigger body does not mention them.  This module closes that gap.

WHICH FIELD IS THE SOURCE OF TRUTH
----------------------------------
Each ``<formatException>`` carries both a ``label`` attribute and exactly
one ``<cond>`` child.  The corpus settles which to trust:

  * ``<cond>`` appears exactly once per exception in all 712 observed
    exceptions -- but 206 of those labels reference TWO OR THREE columns.
    The single ``<cond>`` element simply cannot express those conditions;
    Oracle drops the extra terms when it serialises.
  * the ``label`` is Oracle's own rendering of the complete condition, in a
    small closed SQL-ish grammar (24 distinct shapes over the whole corpus).

So the label is parsed as PRIMARY, and ``<cond>`` is only a fallback for the
36 exceptions whose label is free text (the report author renamed the
exception, e.g. label="BACKGROUND", so it is a name and not a condition).

EXCEPTION-CODE MAP -- PROVEN ONLY
---------------------------------
The fallback needs ``exception="N"`` decoded.  The map below was derived
ONLY from exceptions whose label is a SINGLE atom, where the one ``<cond>``
unambiguously corresponds to the one operator in the label.  Codes seen in
the corpus but with no unambiguous evidence (2, 3, 4) are deliberately
absent: guessing them would invent formatting the source never asked for.
An unknown code DECLINES, and the object keeps today's behaviour.
"""
from __future__ import annotations

import re
from typing import Callable, Optional

from ..models import ORACLE_RENDERS_ITALIC
from ..parsers.oracle_colors import resolve_color


# exception code -> (operator, needs_low, needs_high).  See module docstring:
# every entry here is backed by single-atom evidence in the corpus.
_EXCEPTION_OPS = {
    "1":  ("=", True, False),
    "5":  ("<", True, False),
    "7":  ("BETWEEN", True, True),
    "9":  ("LIKE", True, False),
    "10": ("NOT LIKE", True, False),
    "11": ("IS NULL", False, False),
    "12": ("IS NOT NULL", False, False),
}


# --------------------------------------------------------------------------
# Tokenizer / parser for the label grammar
# --------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"""
      (?P<ws>\s+)
    | (?P<lparen>\()
    | (?P<rparen>\))
    | (?P<colref>:[A-Za-z_][A-Za-z0-9_$#]*)
    | (?P<str>'(?:[^']|'')*')
    | (?P<op><=|>=|<>|!=|=|<|>)
    | (?P<word>[A-Za-z_][A-Za-z0-9_]*)
""", re.X)

_KEYWORDS = {"AND", "OR", "NOT", "IS", "NULL", "LIKE", "BETWEEN"}


class _Decline(Exception):
    """Raised whenever the label cannot be translated with certainty."""


def _tokenize(text: str):
    toks, pos = [], 0
    while pos < len(text):
        m = _TOKEN_RE.match(text, pos)
        if not m:
            raise _Decline(f"unlexable at {pos}: {text[pos:pos + 12]!r}")
        pos = m.end()
        kind = m.lastgroup
        if kind == "ws":
            continue
        val = m.group()
        if kind == "word":
            up = val.upper()
            if up not in _KEYWORDS:
                # A bare word that is not a keyword means the label is a
                # free-text exception NAME, not a condition.
                raise _Decline(f"free-text label token {val!r}")
            toks.append((up, up))
        elif kind == "str":
            toks.append(("STR", val[1:-1].replace("''", "'")))
        elif kind == "colref":
            toks.append(("COL", val[1:]))
        else:
            toks.append((kind.upper(), val))
    return toks


def _vb_str(s: str) -> str:
    return '"' + s.replace('"', '""') + '"'


_NUM_RE = re.compile(r"^[+-]?\d+(\.\d+)?$")


def _is_num(lit: str) -> bool:
    return bool(_NUM_RE.match(lit.strip()))


def _vb_num(lit: str) -> str:
    """Numeric literal as VB source that always COMPILES.

    Oracle's criteria builders emit sentinel bounds like
    ``BETWEEN '0' and '99999999999999999999'`` (20 digits) to mean "no upper
    limit". Emitted as a bare VB literal that is ``BC30036: Overflow`` at
    JIT time -- which the engine reports only as the generic "An error
    occurred during local report processing", so it fails the whole report
    rather than the one cell. Anything past 15 significant digits (safe for
    both Long and exact Double) goes through ``Val()``, a runtime parse with
    no compile-time range limit.
    """
    s = lit.strip()
    digits = s.lstrip("+-").replace(".", "")
    if len(digits) <= 15:
        return s
    return f'Val("{s}")'


def _like_pattern(lit: str) -> Optional[str]:
    """Oracle LIKE literal -> VB ``Like`` pattern, or None when the literal
    holds no wildcard (caller emits a plain equality, which is both simpler
    and provably equivalent)."""
    if "%" not in lit and "_" not in lit:
        return None
    out = []
    for ch in lit:
        if ch == "%":
            out.append("*")
        elif ch == "_":
            out.append("?")
        elif ch in "*?#[]":
            out.append(f"[{ch}]")        # VB pattern metacharacters
        else:
            out.append(ch)
    return "".join(out)


class _Parser:
    """Recursive-descent over the closed label grammar.

    or_expr  := and_expr (OR and_expr)*
    and_expr := primary (AND primary)*
    primary  := '(' or_expr ')' | atom
    atom     := COL relation
    """

    def __init__(self, toks, resolve: Callable[[str], str]):
        self.toks = toks
        self.i = 0
        self.resolve = resolve

    # -- token helpers ----------------------------------------------------
    def _peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else (None, None)

    def _next(self):
        t = self._peek()
        self.i += 1
        return t

    def _accept(self, kind):
        if self._peek()[0] == kind:
            self.i += 1
            return True
        return False

    def _expect(self, kind):
        k, v = self._next()
        if k != kind:
            raise _Decline(f"expected {kind}, got {k}")
        return v

    # -- grammar ----------------------------------------------------------
    def parse(self) -> str:
        vb = self.or_expr()
        if self.i != len(self.toks):
            raise _Decline("trailing tokens")
        return vb

    def or_expr(self) -> str:
        left = self.and_expr()
        while self._accept("OR"):
            left = f"({left} Or {self.and_expr()})"
        return left

    def and_expr(self) -> str:
        left = self.primary()
        while self._accept("AND"):
            left = f"({left} And {self.primary()})"
        return left

    def primary(self) -> str:
        if self._accept("LPAREN"):
            vb = self.or_expr()
            self._expect("RPAREN")
            return vb
        return self.atom()

    def atom(self) -> str:
        col = self._expect("COL")
        try:
            ref = self.resolve(col)
        except KeyError as exc:                     # unknown name -> decline
            raise _Decline(f"unresolvable column {col!r}") from exc
        return self._relation(ref)

    def _relation(self, ref: str) -> str:
        k, v = self._next()

        if k == "IS":
            negated = self._accept("NOT")
            self._expect("NULL")
            return f"Not IsNothing({ref})" if negated else f"IsNothing({ref})"

        negated = False
        if k == "NOT":
            negated = True
            k, v = self._next()

        if k == "LIKE":
            lit = self._expect("STR")
            pat = _like_pattern(lit)
            cmp_ = (f"CStr({ref}) Like {_vb_str(pat)}" if pat is not None
                    else f"CStr({ref}) = {_vb_str(lit)}")
        elif k == "BETWEEN":
            lo = self._expect("STR")
            self._expect("AND")
            hi = self._expect("STR")
            if _is_num(lo) and _is_num(hi):
                n = f"Val(CStr({ref}))"
                cmp_ = (f"({n} >= {_vb_num(lo)} And {n} <= {_vb_num(hi)})")
            else:
                s = f"CStr({ref})"
                cmp_ = (f"({s} >= {_vb_str(lo)} And {s} <= {_vb_str(hi)})")
        elif k == "OP":
            lit = self._expect("STR")
            op = {"!=": "<>"}.get(v, v)
            if op in ("<", ">", "<=", ">=") and _is_num(lit):
                # Ordering comparison against a number: compare numerically.
                # Val() is total (returns 0 for non-numeric text) so this can
                # never raise #Error at render time the way CDbl would.
                cmp_ = f"Val(CStr({ref})) {op} {_vb_num(lit)}"
            else:
                cmp_ = f"CStr({ref}) {op} {_vb_str(lit)}"
        else:
            raise _Decline(f"unsupported relation {k!r}")

        if negated:
            cmp_ = f"Not ({cmp_})"
        # Oracle: ANY comparison with a NULL operand yields NULL, which an IF
        # treats as false. VB would disagree for the empty-literal case
        # (CStr(Nothing) = ""), so guard every non-IS-NULL atom.
        return f"(Not IsNothing({ref}) AndAlso {cmp_})"


def translate_condition_label(label: str,
                              resolve: Callable[[str], str]) -> Optional[str]:
    """Oracle ``formatException@label`` -> VB.NET boolean, or None to decline.

    Declines (rather than guesses) on a free-text label, an unresolvable
    column, or any construct outside the observed grammar.
    """
    if not (label or "").strip():
        return None
    try:
        return _Parser(_tokenize(label.strip()), resolve).parse()
    except _Decline:
        return None
    except Exception:  # noqa: BLE001 - never let a malformed label escape
        return None


def translate_cond_element(cond: dict,
                           resolve: Callable[[str], str]) -> Optional[str]:
    """Fallback path: rebuild the condition from ``<cond>`` attributes.

    Used only when the label is free text.  Deliberately restricted to the
    OPERAND-FREE codes (IS NULL / IS NOT NULL).

    Why so narrow: ``lowValue`` is not reliably a literal.  The corpus has
    ``column="CS_suppl_count" exception="1" lowValue="ac_area_code"`` -- a
    row COUNT compared against something that reads as another column's
    name, not as the string "ac_area_code".  Since nothing in the file
    distinguishes a literal operand from a column reference, any operand
    -bearing fallback would be a guess, and a wrong guess here silently
    paints the wrong rows.  The operand-free codes carry no such ambiguity.
    This costs ~4 exceptions corpus-wide; the label path already covers 95%.
    """
    code = str(cond.get("exception", "")).strip()
    spec = _EXCEPTION_OPS.get(code)
    if not spec:
        return None
    op, needs_low, needs_high = spec
    if needs_low or needs_high:
        return None
    col = (cond.get("column") or "").strip()
    if not col:
        return None
    return translate_condition_label(f"(:{col} {op})", resolve)


# --------------------------------------------------------------------------
# Format payload -> SSRS style properties
# --------------------------------------------------------------------------

def format_exception_styles(font: dict, visual: dict) -> dict:
    """``<font>`` + ``<formatVisualSettings>`` -> {ssrs_style_prop: value}.

    Only properties the source actually sets are returned.  Colours that do
    not resolve (Oracle symbolic names such as textColor="TextColor") are
    dropped rather than emitted as garbage.  The caller is responsible for
    discarding properties whose value equals the element's own base style --
    it is the side that knows the base.
    """
    styles: dict = {}
    font = font or {}
    visual = visual or {}

    def _yes(d, k):
        return str(d.get(k, "")).strip().lower() in ("yes", "true", "1")

    if "bold" in font:
        styles["FontWeight"] = "Bold" if _yes(font, "bold") else "Normal"
    if "italic" in font and (ORACLE_RENDERS_ITALIC or not _yes(font, "italic")):
        # A conditional italic is DROPPED, not emitted as an always-Normal
        # expression: the Oracle export dialect never paints an oblique face
        # (see models.ORACLE_RENDERS_ITALIC -- 16 truth PDFs, 142,831 spans,
        # zero italic). A conditional italic="no" still emits, because that
        # one really does mean "upright here".
        styles["FontStyle"] = "Italic" if _yes(font, "italic") else "Normal"
    if "underline" in font:
        styles["TextDecoration"] = ("Underline" if _yes(font, "underline")
                                    else "None")
    face = (font.get("face") or "").strip()
    if face:
        styles["FontFamily"] = face
    size = str(font.get("size") or "").strip()
    if re.fullmatch(r"\d+(\.\d+)?", size):
        styles["FontSize"] = f"{size}pt"
    tc = resolve_color(font.get("textColor"))
    if tc:
        styles["Color"] = tc

    fill_pattern = (visual.get("fillPattern") or "").strip().lower()
    fg = resolve_color(visual.get("fillForegroundColor"))
    if fill_pattern in ("transparent", "no_fill", "nofill", "none"):
        styles["BackgroundColor"] = "Transparent"
    elif fg:
        styles["BackgroundColor"] = fg

    return styles


def translate_conditional_format(entries, resolve: Callable[[str], str]):
    """``LayoutField.conditional_formats`` -> ``[(cond_vb, {prop: value})]``.

    Entries are returned in document order; Oracle evaluates format
    exceptions in that order and the first match wins, so the caller must
    nest the resulting IIfs in the SAME order.  Untranslatable exceptions
    are skipped (never guessed), and an exception that carries no usable
    format payload is skipped too.
    """
    out = []
    for e in (entries or []):
        vb = translate_condition_label(e.get("label", ""), resolve)
        if vb is None:
            cond = e.get("cond") or {}
            vb = translate_cond_element(cond, resolve)
        if not vb:
            continue
        styles = format_exception_styles(e.get("font"), e.get("visual"))
        if not styles:
            continue
        out.append((vb, styles))
    return out
