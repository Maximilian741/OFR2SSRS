"""
vb_expr_eval.py — prove every generated SSRS expression COMPUTES, not merely
that it compiles.

WHY THIS EXISTS
---------------
``vb_expr_check`` compiles each ``=...`` expression through the real VB.NET
compiler. That catches invalid syntax, but its object model answers ``Nothing``
to every ``Fields!``/``Parameters!``/aggregate reference, so an expression that
divides by zero, calls ``CDate`` on something that is not a date, or resolves to
a field the report never declared compiles perfectly — and then renders
``#Error`` or a silent blank the moment real SSRS runs it. When the ReportViewer
expression host is blocked on the dev box every render is layout-mode, so the
engine never runs it either. Nothing in the pipeline was proving that an
expression produces a VALUE.

This module closes that gap with the same compiler. It reads the RDL's own
declarations — ``<Field>``/``rd:TypeName`` per dataset, ``<ReportParameter>``
``<DataType>``/``<MultiValue>``, the report's textbox names — synthesises values
from them, compiles the expressions into a host seeded with those values,
INVOKES each one, and inspects what came back.

MULTI-WORLD EVALUATION
----------------------
A declaration can leave the runtime type ambiguous: an Oracle ``CHAR`` column
(and every no-prompt ``String`` report parameter) legitimately carries dates,
numbers and free text. Guessing one of those and demanding the expression cope
would flag correct work — ``CDate(Parameters!P_START.Value)`` is right and still
throws on ``"ALPHA"``. So every expression is evaluated once per WORLD:

    0 TEXT   ambiguous values are free text
    1 DATE   ambiguous values are an ISO date string
    2 NUM    ambiguous values are a numeric string
    3 NULL   every field/parameter is Nothing   (probe world, informational)
    4 MATCH  ambiguous values are the literal the report's OWN expressions
            compare that member against, so a conditional whose branches are
            keyed on a code value ("I"/"X"/"COMPLETE") actually reaches one

The rule is existential, never universal: an expression is sound if SOME
well-formed input makes it compute. It is a defect only when it throws in EVERY
populated world, or yields ``Nothing`` in every populated world at a position
that must print a value, or references a name the report never declared.

Graceful degradation: on a host without PowerShell / the VB compiler
:func:`evaluate_rdl_expressions` returns ``available=False`` and callers treat
it as skipped, mirroring ``vb_expr_check`` and the render rails.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_PS_SCRIPT = os.path.normpath(
    os.path.join(_HERE, "..", "..", "..", "tools", "renderlab", "vb_expr_eval.ps1")
)

# Worlds. Index order is a contract with the harness (World = list index).
WORLDS = ("TEXT", "DATE", "NUM", "NULL", "MATCH")
POPULATED_WORLDS = (0, 1, 2, 4)
NULL_WORLD = 3
MATCH_WORLD = 4

# The ambiguous-type witnesses, one per world. A declared String (Oracle CHAR)
# is exactly this ambiguous. The MATCH slot is filled per member from the
# report's own comparison literals; it falls back to the TEXT witness.
_AMBIGUOUS_TEXT = ("ALPHA", "2024-03-15", "1234.56", None, "ALPHA")

# Hard defect classes: an expression carrying any of these is broken for every
# input, not merely for one unlucky guess.
#
# ``always_nothing`` is a COMPUTED expression that still yields Nothing in every
# populated world — work was done and no value came out, which is exactly the
# silent blank the campaign keeps finding by eye. It is deliberately NOT the
# same as ``declared_blank`` (the literal ``=Nothing`` the emitter writes when it
# declines a construct it cannot translate): that one is a declaration of
# blankness carrying an audit note, not a failed computation.
# ``not_evaluated`` is the rail's own integrity class: the site was collected
# and submitted, and the harness returned NO record for it in ANY world. That
# happens when the report's <Code> block (or any other class-level source the
# harness assembles) fails to compile — the harness answers ``compiled=false``
# with an EMPTY result set, and a rail that only looks at ``defects`` would call
# a report whose expressions were never run once "clean". A gate that cannot
# fail is worse than no gate, so a shortfall between the sites EXPECTED and the
# sites EVALUATED is itself a hard defect.
#
# ``aggregate_over_nonnumeric`` is a numeric reducer (Sum/Avg/StDev/Var...) that
# actually received a non-numeric operand — text, a date, a boolean. SSRS raises
# rsAggregateOfNonNumericData on that at run time; the harness folds it to 0 so
# the SURROUNDING expression still exercises its own logic, which is exactly the
# silent-zero shape the campaign keeps finding by eye. It was an informational
# note; the note is the evidence, the defect is the finding.
DEFECT_CLASSES = (
    "compile_error",
    "not_evaluated",
    "always_throws",
    "always_nothing",
    "missing_reference",
    "aggregate_over_nonnumeric",
    "bad_boolean",
    "bad_color",
    "bad_enum",
)

# The harness's note prefix for a numeric reducer handed a non-numeric operand.
_AGG_NONNUMERIC_NOTE = "aggregate_nonnumeric"

# The emitter's declined-construct placeholder, in the forms it can take.
_LITERAL_NOTHING = re.compile(r"^=?\s*\(*\s*Nothing\s*\)*\s*$", re.I)

# Style properties whose value domain the RDL schema fixes.
_ENUMS: Dict[str, Tuple[str, ...]] = {
    "FontWeight": ("Lighter", "Normal", "Bold", "Bolder",
                   "100", "200", "300", "400", "500", "600", "700", "800", "900"),
    "FontStyle": ("Default", "Normal", "Italic"),
    "TextAlign": ("General", "Left", "Center", "Right", "Justify"),
    "VerticalAlign": ("Top", "Middle", "Bottom"),
    "TextDecoration": ("None", "Underline", "Overline", "LineThrough"),
    "BorderStyle": ("Default", "None", "Dotted", "Dashed", "Solid", "Double",
                    "Groove", "Ridge", "Inset", "WindowInset", "Outset"),
    "Direction": ("LTR", "RTL"),
    "WritingMode": ("lr-tb", "tb-rl", "tb-lr", "Horizontal", "Vertical", "Rotate270"),
}
_COLOR_TAGS = ("Color", "BackgroundColor", "BorderColor")
_BOOL_TAGS = ("Hidden", "RepeatOnNewPage", "KeepTogether", "Toggle")

# .NET named colours SSRS accepts, lower-cased. Not exhaustive by design: the
# check only fires on a value that is neither a hex triple nor a known name.
_KNOWN_COLORS = {
    "transparent", "black", "white", "red", "green", "blue", "yellow", "gray",
    "grey", "silver", "maroon", "olive", "lime", "aqua", "teal", "navy",
    "fuchsia", "purple", "orange", "brown", "pink", "gold", "beige", "ivory",
    "khaki", "lavender", "salmon", "tan", "violet", "wheat", "cyan", "magenta",
    "darkgray", "darkgrey", "lightgray", "lightgrey", "darkblue", "darkgreen",
    "darkred", "lightblue", "lightgreen", "lightyellow", "whitesmoke",
    "gainsboro", "dimgray", "dimgrey", "steelblue", "royalblue", "firebrick",
    "crimson", "indigo", "orchid", "plum", "sienna", "chocolate", "coral",
    "cornsilk", "linen", "mistyrose", "moccasin", "seashell", "snow", "azure",
    "honeydew", "aliceblue", "ghostwhite", "floralwhite", "oldlace",
}
_HEX_COLOR = re.compile(r"^#(?:[0-9A-Fa-f]{6}|[0-9A-Fa-f]{8}|[0-9A-Fa-f]{3})$")

# A Format string is "numeric" when it is built from the numeric placeholder
# alphabet only (SSRS custom numeric masks: 0 # . , % ; E+ and currency signs).
_NUMERIC_MASK = re.compile(r"^[0#.,%;$£€+\-() ]*[0#][0#.,%;$£€+\-() ]*$")
_NUMERIC_TYPES = (
    "System.Decimal", "System.Double", "System.Single", "System.Int16",
    "System.Int32", "System.Int64", "System.Byte", "System.SByte",
    "System.UInt16", "System.UInt32", "System.UInt64",
)


def _local(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


# --------------------------------------------------------------------------
# VB literal emission
# --------------------------------------------------------------------------
def _vb_string(s: str) -> str:
    """A VB.NET string literal for ``s``. Characters outside printable ASCII go
    through ``ChrW`` so the generated source survives any code page the compiler
    host happens to use (the corpus includes non-Latin scripts)."""
    parts: List[str] = []
    buf: List[str] = []
    for ch in s:
        o = ord(ch)
        if 32 <= o <= 126:
            buf.append('""' if ch == '"' else ch)
        else:
            if buf:
                parts.append('"' + "".join(buf) + '"')
                buf = []
            parts.append("ChrW(%d)" % o)
    if buf:
        parts.append('"' + "".join(buf) + '"')
    if not parts:
        return '""'
    return " & ".join(parts)


def _vb_literal(v: Any) -> str:
    """A VB.NET literal for a synthetic value."""
    if v is None:
        return "Nothing"
    if isinstance(v, _Typed):
        return v.vb
    if isinstance(v, bool):
        return "True" if v else "False"
    if isinstance(v, str):
        return _vb_string(v)
    if isinstance(v, (list, tuple)):
        return "New Object() { " + ", ".join(_vb_literal(x) for x in v) + " }"
    return _vb_string(str(v))


class _Typed:
    """A synthetic value that must carry a specific CLR type into the host."""

    __slots__ = ("vb",)

    def __init__(self, vb: str) -> None:
        self.vb = vb


_DATE = _Typed("New DateTime(2024, 3, 15, 9, 30, 0)")
_DEC = _Typed("CDec(1234.56)")
_DBL = _Typed("CDbl(1234.56)")
_INT = _Typed("CInt(42)")
_LNG = _Typed("CLng(42)")
_BOOL = _Typed("True")


def _witnesses_for_clr_type(type_name: str) -> List[Any]:
    """One synthetic value per world for a declared CLR type. Only a type the
    declaration leaves ambiguous (String / Object / unknown) varies across
    worlds; a declared DateTime is a DateTime in every populated world."""
    t = (type_name or "").strip()
    fixed: Optional[Any] = None
    if t in ("System.DateTime", "System.DateTimeOffset"):
        fixed = _DATE
    elif t == "System.Decimal":
        fixed = _DEC
    elif t in ("System.Double", "System.Single"):
        fixed = _DBL
    elif t in ("System.Int16", "System.Int32", "System.Byte", "System.SByte",
               "System.UInt16", "System.UInt32"):
        fixed = _INT
    elif t in ("System.Int64", "System.UInt64"):
        fixed = _LNG
    elif t == "System.Boolean":
        fixed = _BOOL
    if fixed is not None:
        return [fixed, fixed, fixed, None, fixed]
    return list(_AMBIGUOUS_TEXT)


def _witnesses_for_param_type(data_type: str) -> List[Any]:
    """Same, for an SSRS <ReportParameter><DataType>."""
    t = (data_type or "").strip().lower()
    if t == "datetime":
        return [_DATE, _DATE, _DATE, None, _DATE]
    if t == "integer":
        return [_INT, _INT, _INT, None, _INT]
    if t == "float":
        return [_DBL, _DBL, _DBL, None, _DBL]
    if t == "boolean":
        return [_BOOL, _BOOL, _BOOL, None, _BOOL]
    return list(_AMBIGUOUS_TEXT)


# Fields!X.Value / Parameters!X.Value compared against a string literal, with
# any number of closing parens between (UCase(Trim(Fields!X.Value)) = "Y").
_COMPARED_LITERAL = re.compile(
    r'(Fields|Parameters)!([A-Za-z_][A-Za-z0-9_]*)\.Value\s*\)*\s*'
    r'(?:=|<>|Like)\s*"((?:[^"]|"")*)"'
)


def comparison_literals(exprs: List[str]) -> Dict[Tuple[str, str], str]:
    """``{(collection, member): literal}`` for every member the report's own
    expressions compare against a string constant.

    A conditional keyed on a code value ("I"/"X"/"COMPLETE") returns Nothing for
    every synthetic value that is not one of those codes — which would read as a
    silent blank when the expression is in fact correct. The report DECLARES the
    values that matter, right there in the comparison, so the MATCH world uses
    them. First occurrence wins, so the mapping is deterministic."""
    out: Dict[Tuple[str, str], str] = {}
    for e in exprs:
        for coll, member, lit in _COMPARED_LITERAL.findall(e or ""):
            key = (coll, member)
            if key not in out:
                out[key] = lit.replace('""', '"')
    return out


# --------------------------------------------------------------------------
# Reading the report's declarations
# --------------------------------------------------------------------------
def declared_fields(root: ET.Element) -> Dict[str, str]:
    """Every ``<Field Name=...>`` the report declares, mapped to its
    ``rd:TypeName``. The union across datasets is deliberate: resolving a
    reference against the union never invents a false dangling-field report,
    while a name in NO dataset is dangling under any scope reading."""
    out: Dict[str, str] = {}
    for el in root.iter():
        if _local(el.tag) != "Field":
            continue
        name = el.get("Name")
        if not name:
            continue
        tname = ""
        for kid in el:
            if _local(kid.tag) == "TypeName":
                tname = (kid.text or "").strip()
        out.setdefault(name, tname or "System.String")
    return out


def declared_parameters(root: ET.Element) -> Dict[str, Tuple[str, bool]]:
    """``<ReportParameter Name=...>`` -> (DataType, MultiValue)."""
    out: Dict[str, Tuple[str, bool]] = {}
    for el in root.iter():
        if _local(el.tag) != "ReportParameter":
            continue
        name = el.get("Name")
        if not name:
            continue
        dtype = "String"
        multi = False
        for kid in el:
            lk = _local(kid.tag)
            if lk == "DataType":
                dtype = (kid.text or "String").strip()
            elif lk == "MultiValue":
                multi = (kid.text or "").strip().lower() == "true"
        out[name] = (dtype, multi)
    return out


def declared_report_items(root: ET.Element) -> List[str]:
    """Names addressable through ``ReportItems!X`` — the report's textboxes."""
    out: List[str] = []
    for el in root.iter():
        if _local(el.tag) == "Textbox":
            name = el.get("Name")
            if name:
                out.append(name)
    return out


def _build_seed(root: ET.Element, exprs: Optional[List[str]] = None) -> str:
    """VB statements that populate the host's collections, one witness value per
    world, straight from the report's own declarations."""
    lits = comparison_literals(exprs or [])

    def _with_match(vals: List[Any], coll: str, name: str) -> List[Any]:
        lit = lits.get((coll, name))
        if lit is not None and isinstance(vals[MATCH_WORLD], str):
            vals = list(vals)
            vals[MATCH_WORLD] = lit
        return vals

    lines: List[str] = []
    for name, tname in sorted(declared_fields(root).items()):
        vals = _with_match(_witnesses_for_clr_type(tname), "Fields", name)
        lines.append("        Fields.Put(%s, %s)"
                     % (_vb_string(name), _vb_literal(tuple(vals))))
    for name, (dtype, multi) in sorted(declared_parameters(root).items()):
        vals = _with_match(_witnesses_for_param_type(dtype), "Parameters", name)
        if multi:
            vals = [None if v is None else (v, v) for v in vals]
        lines.append("        Parameters.Put(%s, %s)"
                     % (_vb_string(name), _vb_literal(tuple(vals))))
    for name in sorted(set(declared_report_items(root))):
        lines.append("        ReportItems.Put(%s, %s)"
                     % (_vb_string(name), _vb_literal(tuple(_AMBIGUOUS_TEXT))))
    return "\n".join(lines) if lines else "        ' (nothing declared)"


# --------------------------------------------------------------------------
# Expression sites: where each expression sits decides what it must return
# --------------------------------------------------------------------------
def _requirement(tag: str, ancestors: List[str]) -> str:
    if tag in _COLOR_TAGS:
        return "color"
    if tag in _ENUMS:
        return "enum:" + tag
    if tag in _BOOL_TAGS:
        return "bool"
    if tag == "Hyperlink":
        return "uri"
    if tag == "Value":
        # A <Value> under a query parameter, a report parameter default, or a
        # filter is a BIND, not printed text: Nothing there is the deliberate
        # "no value supplied" state and must never be flagged.
        for a in ("QueryParameter", "ReportParameter", "DefaultValue",
                  "ValidValues", "Filter", "FilterValues", "Sorting",
                  "SortExpression", "Group", "Variables", "Variable"):
            if a in ancestors:
                return "any"
        if "TextRun" in ancestors or "Textbox" in ancestors:
            return "value"
        return "any"
    return "any"


def expression_sites(root: ET.Element) -> List[Dict[str, Any]]:
    """Every element whose text is an SSRS expression, with the path that says
    what the engine will do with the result."""
    sites: List[Dict[str, Any]] = []

    def walk(el: ET.Element, ancestors: List[str], path: str,
             fmt: Optional[str]) -> None:
        tag = _local(el.tag)
        if tag == "Code":
            return
        here = path + "/" + tag
        name = el.get("Name")
        if name:
            here += "[%s]" % name
        # A TextRun's own <Style><Format> travels with it: the declared mask is
        # part of what the expression must satisfy.
        my_fmt = fmt
        if tag in ("TextRun", "Textbox"):
            my_fmt = _own_format(el) or fmt
        text = el.text
        if text is not None:
            s = text.strip()
            if s.startswith("="):
                sites.append({
                    "path": here,
                    "tag": tag,
                    "expr": s,
                    "requirement": _requirement(tag, ancestors),
                    "format": my_fmt or "",
                })
        child_anc = ancestors + [tag]
        for kid in el:
            walk(kid, child_anc, here, my_fmt)

    walk(root, [], "", None)
    return sites


def _own_format(el: ET.Element) -> Optional[str]:
    """The literal ``<Style><Format>`` declared on this item, if any (an
    expression-valued Format is not a literal mask and is skipped)."""
    for kid in el:
        if _local(kid.tag) != "Style":
            continue
        for sk in kid:
            if _local(sk.tag) == "Format":
                t = (sk.text or "").strip()
                if t and not t.startswith("="):
                    return t
    return None


# --------------------------------------------------------------------------
# The harness call
# --------------------------------------------------------------------------
def _powershell() -> Optional[str]:
    for cand in ("pwsh", "powershell"):
        if shutil.which(cand):
            return cand
    return None


def _unavailable(reason: str, total: int) -> Dict[str, Any]:
    return {"available": False, "reason": reason, "sites": [], "results": [],
            "defects": [],
            "summary": {"total": total, "expected": total, "evaluated": 0,
                        "not_evaluated": total, "defects": 0, "by_class": {}}}


def _run_harness(payload: Dict[str, Any], timeout: int) -> Optional[Dict[str, Any]]:
    ps = _powershell()
    if ps is None or not os.path.exists(_PS_SCRIPT):
        return None
    tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                      encoding="utf-8")
    try:
        json.dump(payload, tmp)
        tmp.flush()
        tmp.close()
        proc = subprocess.run(
            [ps, "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", _PS_SCRIPT, "-InFile", tmp.name],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {"available": False, "reason": "harness invocation failed: %s" % exc}
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
    out = proc.stdout or ""
    for line in reversed(out.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    # A very large report can make PowerShell's host wrap the single JSON line;
    # fall back to decoding from the first brace across the whole stream.
    brace = out.find("{")
    if brace >= 0:
        try:
            return json.loads(out[brace:].replace("\r\n", "").replace("\n", ""))
        except json.JSONDecodeError:
            pass
    return {"available": False,
            "reason": ("no JSON from harness; rc=%s stdout=%dB head=%r "
                       "stderr=%r"
                       % (proc.returncode, len(out), out[:160],
                          (proc.stderr or "")[:300]))}


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------
def _is_color(value: str) -> bool:
    v = (value or "").strip()
    if not v:
        return False
    if _HEX_COLOR.match(v):
        return True
    return v.lower() in _KNOWN_COLORS


def _top_level_literals(expr: str) -> List[str]:
    """String literals in an expression that is a plain top-level concatenation
    (no conditional operator anywhere). Only then must every literal survive
    into the result."""
    body = expr[1:] if expr.startswith("=") else expr
    if re.search(r"\b(IIf|Switch|Choose|IsNothing|Iif)\s*\(", body, re.I):
        return []
    lits = re.findall(r'"((?:[^"]|"")*)"', body)
    stripped = re.sub(r'"(?:[^"]|"")*"', "", body)
    # A literal that is an ARGUMENT (a mask, a scope name, a separator) does not
    # have to appear in the output; only a &-joined operand does.
    if "(" in stripped:
        return []
    return [l.replace('""', '"') for l in lits if len(l.strip()) >= 2]


def _classify(site: Dict[str, Any], worlds: List[Optional[Dict[str, Any]]],
              compile_errors: List[str]) -> Dict[str, Any]:
    classes: List[str] = []
    detail: List[str] = []

    if compile_errors:
        classes.append("compile_error")
        detail.append("; ".join(compile_errors[:2]))

    populated = [worlds[i] for i in POPULATED_WORLDS
                 if i < len(worlds) and worlds[i] is not None]
    null_world = worlds[NULL_WORLD] if len(worlds) > NULL_WORLD else None

    misses: List[str] = []
    for w in worlds:
        if not w:
            continue
        for m in w.get("misses") or []:
            if m not in misses:
                misses.append(m)
    if misses:
        classes.append("missing_reference")
        detail.append("undeclared: " + ", ".join(sorted(misses)[:4]))

    notes: List[str] = []
    for w in worlds:
        if not w:
            continue
        for n in w.get("notes") or []:
            if n not in notes:
                notes.append(n)

    # THE RAIL'S OWN INTEGRITY CHECK. No record in any world means the site was
    # never invoked: the harness compiled nothing (a broken <Code> block makes
    # it answer compiled=false with results=[]) or dropped the site. Reporting
    # "no defects" on an expression that was never run is the gate-that-cannot-
    # fail shape, so the shortfall IS the defect.
    if not any(w is not None for w in worlds):
        classes.append("not_evaluated")
        detail.append("no result in any world: the expression was "
                      "never invoked")

    # A numeric reducer that actually received text / a date / a boolean. The
    # host folds it to 0 so the surrounding expression still runs; SSRS raises
    # rsAggregateOfNonNumericData instead. Either way the number that prints is
    # not a sum of anything.
    agg = [n for n in notes if n.startswith(_AGG_NONNUMERIC_NOTE)]
    if agg:
        classes.append("aggregate_over_nonnumeric")
        detail.append("aggregate over a non-numeric operand: "
                      + ", ".join(sorted(agg)[:4]))

    if populated and not compile_errors:
        clean = [w for w in populated if w.get("ok")]
        if not clean:
            classes.append("always_throws")
            detail.append(populated[0].get("error", "")[:160])
        else:
            valued = [w for w in clean if not w.get("isNothing")]
            if site["requirement"] == "value" and not valued:
                if _LITERAL_NOTHING.match(site["expr"] or ""):
                    classes.append("declared_blank")
                    detail.append("declared placeholder (emitter declined the "
                                  "source construct)")
                else:
                    classes.append("always_nothing")
                    detail.append("computes, returns Nothing in every "
                                  "populated world")
            best = valued[0] if valued else None
            if best is not None:
                classes.extend(_domain_classes(site, best, detail))
                got = best.get("value") or ""
                # The harness caps the returned text; a capped value cannot
                # witness the tail literals, so the concatenation check is only
                # meaningful on an intact result.
                if not best.get("truncated"):
                    lits = _top_level_literals(site["expr"])
                    lost = [l for l in lits if l not in got]
                    if lost:
                        classes.append("lost_literal")
                        detail.append("literal(s) absent from result: %r"
                                      % [l[:60] for l in lost[:3]])
                # A declared mask governs the PRINTED value only. A style
                # expression (Color/FontWeight/...) inherits its textbox's
                # <Format> through the tree but is never formatted by it.
                fmt = site.get("format") or ""
                if (site["requirement"] == "value" and fmt
                        and _NUMERIC_MASK.match(fmt)
                        and best.get("type") not in _NUMERIC_TYPES):
                    classes.append("mask_type_mismatch")
                    detail.append("numeric mask %r on %s"
                                  % (fmt, best.get("type") or "Nothing"))
        if null_world is not None and clean and not null_world.get("ok"):
            classes.append("null_fragile")
            detail.append("null world: " + (null_world.get("error", "")[:120]))

    return {
        "path": site["path"],
        "tag": site["tag"],
        "expr": site["expr"],
        "requirement": site["requirement"],
        "format": site.get("format", ""),
        "classes": classes,
        "notes": notes,
        "detail": " | ".join(d for d in detail if d),
        "values": [None if w is None else
                   {"world": WORLDS[w["world"]], "ok": w.get("ok"),
                    "isNothing": w.get("isNothing"),
                    "value": w.get("value"), "type": w.get("type"),
                    "truncated": bool(w.get("truncated")),
                    "error": w.get("error", "")}
                   for w in worlds],
    }


def _domain_classes(site: Dict[str, Any], best: Dict[str, Any],
                    detail: List[str]) -> List[str]:
    req = site["requirement"]
    val = best.get("value") or ""
    out: List[str] = []
    if req == "color":
        if not _is_color(val):
            out.append("bad_color")
            detail.append("not a colour: %r" % val[:40])
    elif req == "bool":
        if best.get("type") != "System.Boolean":
            out.append("bad_boolean")
            detail.append("not Boolean: %s=%r" % (best.get("type"), val[:40]))
    elif req.startswith("enum:"):
        allowed = _ENUMS.get(req.split(":", 1)[1], ())
        if allowed and val.strip().lower() not in {a.lower() for a in allowed}:
            out.append("bad_enum")
            detail.append("not a %s: %r" % (req.split(":", 1)[1], val[:40]))
    elif req == "uri":
        if not val.strip():
            out.append("bad_uri")
            detail.append("empty hyperlink target")
    return out


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------
def evaluate_rdl_expressions(rdl_xml: str, timeout: int = 600) -> Dict[str, Any]:
    """Compile AND INVOKE every expression in ``rdl_xml``.

    Returns::

        {
          "available": bool,        # False => VB compiler/host absent (skipped)
          "results":  [ {path, expr, requirement, classes, detail, values} ],
          "defects":  [ ...subset whose classes intersect DEFECT_CLASSES... ],
          "summary":  {"total", "evaluated", "defects", "by_class"},
        }
    """
    try:
        root = ET.fromstring(rdl_xml)
    except ET.ParseError as exc:
        return {"available": True, "results": [], "defects": [],
                "summary": {"total": 0, "expected": 0, "evaluated": 0,
                            "not_evaluated": 0, "defects": 0, "by_class": {}},
                "error": "xml parse: %s" % exc}

    sites = expression_sites(root)
    if not sites:
        return {"available": True, "results": [], "defects": [],
                "summary": {"total": 0, "expected": 0, "evaluated": 0,
                            "not_evaluated": 0, "defects": 0, "by_class": {}}}

    code = ""
    for el in root.iter():
        if _local(el.tag) == "Code":
            code = el.text or ""
            break

    all_exprs = [s["expr"] for s in sites]
    payload = {
        "seed": _build_seed(root, all_exprs),
        "code": code,
        "worlds": len(WORLDS),
        "exprs": all_exprs,
    }
    data = _run_harness(payload, timeout)
    if data is None:
        return _unavailable("powershell or harness unavailable", len(sites))
    if not data.get("available"):
        return _unavailable(data.get("reason", ""), len(sites))

    # A HARNESS-LEVEL FAILURE IS A FAILURE OF THIS REPORT, NEVER A SKIP.
    # ``compiled=false`` (the assembled source — the report's own <Code> block
    # included — would not compile) and ``seedFailed`` (the host could not be
    # built from the report's declarations) both come back with an EMPTY result
    # set. Returning "unavailable" would make callers skip, and classifying an
    # empty result set the old way reported ZERO DEFECTS on a report whose
    # expressions were never run once. Fall through instead: every site then
    # carries ``not_evaluated`` and the rail is red, with the reason attached.
    failure = ""
    if not data.get("compiled"):
        bits = [str(x) for x in (data.get("structural") or [])]
        for msgs in (data.get("compileErrors") or {}).values():
            bits.extend(str(m) for m in list(msgs)[:1])
        failure = ("the report's expressions were NEVER COMPILED (harness "
                   "compiled=false): %s"
                   % ("; ".join(bits)[:400] or "no compiler detail"))
    elif data.get("seedFailed"):
        failure = ("the expression host could not be seeded from the report's "
                   "own declarations: %s" % str(data.get("reason") or "")[:300])

    by_expr: Dict[int, List[Optional[Dict[str, Any]]]] = {
        i: [None] * len(WORLDS) for i in range(len(sites))
    }
    for r in data.get("results", []):
        i = int(r.get("index", -1))
        w = int(r.get("world", -1))
        if 0 <= i < len(sites) and 0 <= w < len(WORLDS):
            by_expr[i][w] = r
    cerrs = {int(k): list(v) for k, v in (data.get("compileErrors") or {}).items()}

    results = [_classify(sites[i], by_expr[i], cerrs.get(i, []))
               for i in range(len(sites))]
    if failure:
        for r in results:
            if "not_evaluated" in r["classes"]:
                r["detail"] = (r["detail"] + " | " if r["detail"] else "") + failure
    by_class: Dict[str, int] = {}
    for r in results:
        for c in r["classes"]:
            by_class[c] = by_class.get(c, 0) + 1
    defects = [r for r in results
               if any(c in DEFECT_CLASSES for c in r["classes"])]
    evaluated = sum(1 for r in results
                    if any(v and v.get("ok") is not None for v in r["values"]))
    # ``expected`` vs ``evaluated`` is the shortfall the caller asserts on: a
    # rail that only counted defects called an EMPTY result set clean.
    out = {"available": True, "results": results, "defects": defects,
           "structural": data.get("structural") or [],
           "summary": {"total": len(sites), "expected": len(sites),
                       "evaluated": evaluated,
                       "not_evaluated": len(sites) - evaluated,
                       "defects": len(defects), "by_class": by_class}}
    if failure:
        out["error"] = failure
    return out
