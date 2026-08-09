"""Layout-only MS-engine render prep for an RDL.

The MS ReportViewer engine (render_rdl.ps1 + lib/) renders an RDL faithfully,
but under this machine's app-control policy it cannot JIT-compile the report's
=expressions (the expression-host sandbox AppDomain can't resolve
Microsoft.ReportViewer.Common). For LAYOUT verification we don't need live
expressions, so this swaps every =expression for a static placeholder (same
boxes/borders/tables/page-flow, placeholder text in cells) and writes the
synthetic dataset JSON. The caller then renders the *_static.rdl through
render_rdl.ps1 (no expression host -> renders cleanly to a real PDF).

Usage:
    python tools/renderlab/ms_layout.py <oracle.xml | report.rdl> <out_basename>
    -> writes <out_basename>_static.rdl and <out_basename>_static.data.json
       then PRINTS the powershell command to render it.
"""
from __future__ import annotations

import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from render import synthesize_data  # noqa: E402

NS = "{http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition}"
ET.register_namespace("", NS.strip("{}"))
_COLOR = {"Color", "BackgroundColor", "BorderColor"}


def _local(tag: str) -> str:
    return tag.split("}")[-1]


def _collapse_iif(expr: str) -> str:
    """Reduce IIf(cond, a, b) -> the VALUE branch for static layout verification.

    The staticizer can't evaluate the condition (no expression host), and its
    token loop concatenates EVERY Fields!/Parameters! token it sees. A
    value-or-fallback cover expression --
        IIf(Len(Trim(CStr(First(Fields!X)..)))=0, "fallback", CStr(First(Fields!X)..))
    -- references the same field in BOTH the condition AND the else branch, so
    the loop emitted the field's text TWICE ("CP ENVELOPECP ENVELOPE").
    Collapse each IIf to the branch that carries the field/param token (the value
    actually displayed on the happy path); tie / neither -> the else (3rd) arg.
    Repeats to handle nesting. Verification-only: never touches the deployed RDL,
    whose live IIf the real ReportViewer evaluates correctly. Generic, no names."""
    guard = 0
    low = expr.lower()
    idx = low.find("iif(")
    while idx != -1 and guard < 100:
        guard += 1
        if idx > 0 and (expr[idx - 1].isalnum() or expr[idx - 1] == "_"):
            idx = low.find("iif(", idx + 3)
            continue
        open_pos = idx + 3
        depth = 0
        args = []
        cur_start = open_pos + 1
        end = -1
        in_str = False
        i = open_pos
        while i < len(expr):
            ch = expr[i]
            if ch == '"':
                in_str = not in_str
            elif not in_str:
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        args.append(expr[cur_start:i])
                        end = i
                        break
                elif ch == "," and depth == 1:
                    args.append(expr[cur_start:i])
                    cur_start = i + 1
            i += 1
        if end != -1 and len(args) >= 3:
            a, b = args[1], args[2]
            def _has_tok(s):
                return ("Fields!" in s) or ("Parameters!" in s)
            if _has_tok(b) and not _has_tok(a):
                pick = b
            elif _has_tok(a) and not _has_tok(b):
                pick = a
            else:
                pick = b
            expr = expr[:idx] + "(" + pick.strip() + ")" + expr[end + 1:]
            low = expr.lower()
            idx = low.find("iif(")
        else:
            idx = low.find("iif(", idx + 3)
    return expr


def _len_in(text: str, size_pt: float, bold: bool, sans: bool) -> float:
    """Advance width of ``text`` in INCHES. Uses the converter's Standard-14
    AFM tables when they can be imported (same estimator the emitter measures
    with, so harness and emitter never disagree); falls back to a deliberately
    GENEROUS 0.62-em average, which under-reports width and therefore only
    ever shrinks a placeholder that is unambiguously too long."""
    try:
        from converter.generators.rdl import _afm_text_width  # noqa: PLC0415
        return _afm_text_width(text, size_pt, bold, sans)
    except Exception:  # noqa: BLE001 - the harness must not need the backend
        return len(text) * 0.62 * size_pt / 72.0


def _shrink_placeholder_to_box(out, tok_at, el, parent) -> None:
    """Trim INVENTED placeholder text so it cannot be wider than the box the
    DECLARATION sizes for the real value.

    Why the harness owes this: the staticizer replaces a field reference with
    the field's HUMANISED NAME, and a name is routinely several times longer
    than the value it names. Measured on a receipts ledger: a payment-type
    column is declared 0.375in wide, the truth PDF prints "CK" in it (13.9pt
    of ink, comfortably inside the 27pt box) — the placeholder "PMNT TYPE"
    needs 27.8pt for its FIRST WORD alone, so it wrapped, spilled out of the
    row and was buried under the next row's opaque fill. That is a property of
    the placeholder, not of the emitted geometry, and chasing it in the
    converter means widening boxes the declaration deliberately made narrow.

    What it deliberately does NOT touch: DECLARED string literals. Whether the
    report's own boilerplate fits its own box is a real fidelity question, so
    those pieces keep their full length and still overflow when they must.
    A box that is genuinely too WIDE also still shows: the shrunken value is
    laid out at the box's own anchor, so a box overhanging its neighbour still
    prints its value over the neighbour's.
    """
    if not tok_at:
        return
    if any("\n" in (p or "") for p in out):
        return                      # multi-line value: wrapping is the point
    tb = parent.get(el)
    while tb is not None and _local(tb.tag) != "Textbox":
        tb = parent.get(tb)
    if tb is None:
        return
    w = next((c.text for c in tb if _local(c.tag) == "Width"), "") or ""
    w = w.strip()
    if not w.endswith("in"):
        return
    try:
        box_in = float(w[:-2])
    except ValueError:
        return
    if box_in <= 0:
        return
    sizes, bold, sans = [], False, True
    for st in tb.iter():
        tag = _local(st.tag)
        if tag == "FontSize" and (st.text or "").strip().endswith("pt"):
            try:
                sizes.append(float((st.text or "").strip()[:-2]))
            except ValueError:
                pass
        elif tag == "FontWeight" and "bold" in (st.text or "").lower():
            bold = True
        elif tag == "FontFamily":
            sans = "times" not in (st.text or "").lower()
    size_pt = max(sizes) if sizes else 10.0

    def _too_wide():
        return _len_in("".join(out), size_pt, bold, sans) > box_in

    guard = 0
    while _too_wide() and guard < 400:
        guard += 1
        i = max(tok_at, key=lambda k: len(out[k]))
        if len(out[i]) <= 1:
            break               # nothing invented left to give
        out[i] = out[i][:-1].rstrip() or out[i][:1]


def staticize(rdl_xml: str) -> str:
    """Replace every =expression with a static value so the report needs no
    expression host. <Value> -> a readable placeholder derived from the first
    Fields!/Parameters! token; <Hidden> -> false; color tags -> a literal;
    anything else -> empty. The <ReportParameters> subtree is left intact:
    parameter DefaultValues (=Nothing / typed literals) must keep their
    declared type, and the engine evaluates those constants without the
    expression-host sandbox."""
    root = ET.fromstring(rdl_xml)
    parent = {c: p for p in root.iter() for c in p}

    def under_params(el):
        cur = parent.get(el)
        while cur is not None:
            if _local(cur.tag) == "ReportParameters":
                return True
            cur = parent.get(cur)
        return False

    # Parameter DefaultValues: rewrite any =expression (=Nothing, etc.) to a
    # type-valid literal so the param needs no expression host AND keeps its
    # declared type (a DateTime param can't default to "Sample").
    _TYPE_LIT = {"String": "x", "Boolean": "false",
                 "DateTime": "2020-01-01T00:00:00", "Integer": "0", "Float": "0"}
    # A parameter whose DefaultValue is a CONCRETE LITERAL (no leading '=', e.g.
    # a title's bureau/division display constant) is a display constant: a
    # =Parameters!X.Value reference to it should render that literal, not the
    # humanised token. Capture these BEFORE the =expr defaults get type-rewritten.
    param_defaults = {}
    for rp in root.iter():
        if _local(rp.tag) != "ReportParameter":
            continue
        nm = rp.get("Name") or ""
        v0 = next((c for c in rp.iter() if _local(c.tag) == "Value"), None)
        if nm and v0 is not None and (v0.text or "") and not (v0.text or "").startswith("="):
            param_defaults[nm] = v0.text
    for rp in root.iter():
        if _local(rp.tag) != "ReportParameter":
            continue
        dt = next((c for c in rp.iter() if _local(c.tag) == "DataType"), None)
        lit = _TYPE_LIT.get((dt.text or "String").strip() if dt is not None else "String", "x")
        for v in rp.iter():
            if _local(v.tag) == "Value" and (v.text or "").startswith("="):
                v.text = lit

    # Database/External images feed a BINARY value via an =expression; a static
    # constant string can't satisfy that ("Value requires a binary value, so it
    # cannot be a constant"). Repoint such images at a 1x1 EMBEDDED png so the
    # report PUBLISHES and renders (the image box/layout is preserved, content is
    # a dot). Embedded-image refs (Value is a constant name, no leading '=') are
    # left untouched. Self-contained: no external file, no EnableExternalImages.
    _imgs = [img for img in root.iter() if _local(img.tag) == "Image"
             and any(_local(c.tag) == "Value" and (c.text or "").startswith("=")
                     for c in img)]
    if _imgs:
        for img in _imgs:
            src = next((c for c in img if _local(c.tag) == "Source"), None)
            val = next((c for c in img if _local(c.tag) == "Value"), None)
            if src is not None:
                src.text = "Embedded"
            if val is not None:
                val.text = "o2s_ph"
        ei = root.find(NS + "EmbeddedImages")
        if ei is None:
            ei = ET.Element(NS + "EmbeddedImages")
            kids = list(root)
            pos = next((i for i, c in enumerate(kids)
                        if _local(c.tag) == "Page"), len(kids) - 1)
            root.insert(pos + 1, ei)
        # add the placeholder image to the (possibly pre-existing) block once
        if not any(e.get("Name") == "o2s_ph" for e in ei):
            one = ET.SubElement(ei, NS + "EmbeddedImage")
            one.set("Name", "o2s_ph")
            ET.SubElement(one, NS + "MIMEType").text = "image/png"
            ET.SubElement(one, NS + "ImageData").text = (
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYPhf"
                "DwAChwGA60e6kgAAAABJRU5ErkJggg==")

    # Fields the RDL DECLARES numeric (rd:TypeName) — used to staticize
    # single-token numeric refs as numbers instead of wide humanised names.
    _NUM_T = ("Decimal", "Double", "Int", "Single", "Byte")
    _numeric_fields = set()
    for _fe in root.iter():
        if _local(_fe.tag) != "Field":
            continue
        _tn = next((c.text or "" for c in _fe.iter()
                    if _local(c.tag) == "TypeName"), "")
        if any(t in _tn for t in _NUM_T) and _fe.get("Name"):
            _numeric_fields.add(_fe.get("Name"))

    def _eval_hidden(expr):
        """Evaluate a PARAMETER-ONLY Hidden expression against the report's
        staticized parameter defaults, so the layout render shows the same
        conditional blocks a default-parameter server run would. Returns
        "true"/"false", or None for data-dependent / unsupported
        expressions (caller falls back to hidden — the variant-overlap
        rule)."""
        e = (expr or "").strip()
        if not e.startswith("="):
            return None
        e = e[1:]
        if any(k in e for k in ("Fields!", "Globals!", "ReportItems!",
                                "Code.")):
            return None
        e = re.sub(r"Parameters!(\w+)\.Value",
                   lambda m: '"%s"' % param_defaults.get(m.group(1), "x")
                   .replace('"', ""), e)
        # VB string literals -> Python literals FIRST (so operator rewrites
        # never touch text inside quotes)
        e = re.sub(r'"((?:[^"]|"")*)"',
                   lambda m: repr(m.group(1).replace('""', '"')), e)
        e = re.sub(r"<>", "!=", e)
        e = re.sub(r"(?<![<>!=])=(?!=)", "==", e)
        e = re.sub(r"\bNot\b", " not ", e)
        e = re.sub(r"\bAnd\b", " and ", e)
        e = re.sub(r"\bOr\b", " or ", e)
        e = re.sub(r"\bTrue\b", "True", e, flags=re.I)
        e = re.sub(r"\bFalse\b", "False", e, flags=re.I)
        # anything beyond the safe subset (unknown identifiers) -> bail.
        # Scan with string-literal CONTENTS removed — the values inside
        # quotes are data, not identifiers.
        bare = re.sub(r"'(?:[^'\\]|\\.)*'", "''", e)
        idents = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", bare))
        if idents - {"IIf", "True", "False", "not", "and", "or"}:
            return None
        try:
            r = eval(e, {"__builtins__": {}},  # noqa: S307 - whitelisted tokens only
                     {"IIf": lambda c, a, b: a if c else b})
        except Exception:  # noqa: BLE001
            return None
        if isinstance(r, bool):
            return "true" if r else "false"
        return None

    for el in root.iter():
        txt = el.text or ""
        if not txt.startswith("="):
            continue
        if under_params(el):
            continue
        t = _local(el.tag)
        if t == "Value":
            # Render the expression READABLY so layout verification is faithful:
            # keep string literals verbatim (boilerplate like ="Plant Location"),
            # replace each Fields!/Parameters! token with its humanised name, and
            # drop the VB glue (&, IIf, etc.). A bare =Nothing/empty -> "Sample".
            expr = txt[1:]
            # A PURE numeric aggregate (=Sum(...), =Count(...) — no string
            # literals mixed in) staticizes to a plausible NUMBER: the box
            # is sized for "1,457", and the humanised token ("XYZ TOTAL")
            # wraps/clips out of it, which the paint gate then flags as an
            # overlap the deployed report doesn't have.
            if (re.match(r"^\s*(Sum|Count|CountDistinct|Avg|Min|Max|"
                         r"RunningValue)\s*\(", expr, re.I)
                    and "&" not in expr):
                el.text = "1,234"
                continue
            # Same for a bare reference to an ORACLE SUMMARY COLUMN — the
            # generated "<fn>X..Per<group>" names (SumFeePerSite,
            # CountIdPerYear) are numeric by construction, and their
            # humanised token wraps out of the number-sized box.
            if (re.match(r"^\s*(?:First\s*\(\s*)?Fields!(?:Sum|Count|Avg|"
                         r"Min|Max)\w*Per\w+\.Value", expr)
                    and "&" not in expr):
                el.text = "1,234"
                continue
            # And for a single-token ref to a field the RDL itself DECLARES
            # numeric (rd:TypeName Decimal/Double/Int...) — the box is
            # sized for a number; the humanised name wraps 4 lines tall.
            _m1 = re.match(r"^\s*(?:First\s*\(\s*)?Fields!(\w+)\.Value"
                           r"\s*(?:,\s*\"[^\"]*\"\s*\))?\s*$", expr)
            if _m1 and _m1.group(1) in _numeric_fields:
                el.text = "1,234"
                continue
            # Collapse value-or-fallback IIf()s to the displayed branch BEFORE
            # token extraction, so a field referenced in both the condition and
            # the else branch isn't humanised + concatenated twice.
            expr = _collapse_iif(expr)
            # Lookup(src_key, dest_key, RESULT, "dataset") displays only RESULT;
            # the two key args + the dataset name are plumbing, not output. Left
            # as-is, every Fields! token (incl. the keys) would humanise and
            # concatenate into a garbled band ("SITE IDSITE IDORG"). Collapse to
            # the result field so the verification render shows the real column.
            expr = re.sub(
                r'\bLookup(?:Set)?\s*\(\s*[^,()]+,\s*[^,()]+,\s*'
                r'(Fields!\s*[A-Za-z0-9_]+\s*\.\s*Value)\s*,\s*"[^"]*"\s*\)',
                r'\1', expr)
            out = []
            _tok_at = []   # indices in `out` that are INVENTED placeholder text
            for m in re.finditer(
                    r'"((?:[^"]|"")*)"'                       # string literal
                    r'|(?:Fields!|Parameters!)([A-Za-z0-9_]+)'  # field/param token
                    r'|(vbCrLf|vbCr|vbLf|vbNewLine|Environment\.NewLine'
                    r'|Chr\(\s*1[03]\s*\)|Chr\$\(\s*1[03]\s*\))',  # VB newline
                    expr):
                lit, tok, nl = m.group(1), m.group(2), m.group(3)
                if nl is not None:
                    out.append("\n")  # honor multi-line values (vbCrLf etc.)
                elif tok is not None:
                    _tok_at.append(len(out))
                    _tu = tok.upper()
                    # An *_Ind boolean indicator field renders the Oracle ASTERISK
                    # it drives (the _Ind formula returns "*"/""); show "*" so the
                    # verification render shows the grid marks instead of a blank.
                    if _tu.endswith("_IND") or _tu == "IND":
                        out.append("*")
                    # A YEAR / fiscal-year param renders a sample 4-digit year, not
                    # its humanised name, so 'FY" & P_GRANT_YEAR' -> "FY2024" (the
                    # long "P GRANT YEAR" wrapped 3 lines + clipped the col label).
                    elif "YEAR" in _tu:
                        out.append("2024")
                    else:
                        # A param with a concrete literal default (display
                        # constant) renders that default; else the humanised name.
                        out.append(param_defaults.get(tok, tok.replace("_", " ")))
                else:
                    # Skip function-argument literals (aggregate scope names,
                    # Format() patterns): those follow a comma. Keep boilerplate
                    # and concatenation separators (=, &, ( before them).
                    if expr[:m.start()].rstrip().endswith(","):
                        continue
                    out.append((lit or "").replace('""', '"'))
            _shrink_placeholder_to_box(out, _tok_at, el, parent)
            _joined = "".join(out)
            rendered = _joined.strip()
            if not rendered:
                rendered = "Sample"
            else:
                # Keep the declared edge whitespace RUNS verbatim. An inline
                # styled TextRun ('Select ' before a bold 'Site' run) carries
                # its run-boundary space at the literal's edge, and a prose
                # clause carries its indentation there ('     a)  ...') — the
                # engine advances by every one of those spaces, so collapsing
                # the run to a single space made the verification render print
                # the clause 4 space-widths left of where the report does.
                # Only HORIZONTAL whitespace is restored; a leading/trailing
                # vbCrLf still goes (it would grow the box a blank line).
                _lead = re.match(r"[^\S\n]*", _joined).group(0)
                _tail = re.search(r"[^\S\n]*\Z", _joined).group(0)
                rendered = _lead + rendered + _tail
            # A staticized literal that itself begins with '=' (Oracle's
            # "= Vehicles in Yards" running-total label) would be re-parsed by
            # ReportViewer AS an expression -> "'Vehicles' is not declared".
            # Re-quoting as ="..." is also wrong (that's an expression; this
            # machine's sandbox can't load the expression host), and a leading
            # SPACE is trimmed before the '=' check. A zero-width space (U+200B,
            # not Unicode whitespace, so Trim keeps it) makes the first char
            # not '=' -> a plain literal that renders the label verbatim.
            el.text = (("​" + rendered) if rendered.lstrip().startswith("=")
                       else rendered)
        elif t == "Hidden":
            # An EXPRESSION Hidden marks a CONDITIONAL item (a format-trigger
            # variant frame, an optional enclosure line). In PAGE BANDS and
            # COVER rects, PARAMETER-ONLY conditions evaluate against the
            # staticized defaults so a fully-conditional cover page renders
            # like a default-parameter server run instead of BLANK. BODY
            # items keep the always-hide rule: un-hiding body variant
            # frames inflated per-record letters past their tuned page
            # budget (an ARCHIVED known-good grant letter re-rendered with
            # alternating blank pages and a mid-letter page 1 — harness
            # drift, caught by rendering the archive first).
            _cur = parent.get(el)
            _in_band = False
            while _cur is not None:
                _t2 = _local(_cur.tag)
                if _t2 in ("PageHeader", "PageFooter") or (
                        _t2 == "Rectangle" and (_cur.get("Name") or "")
                        in ("Rect_CoverPage", "Rect_SummaryHeader")):
                    _in_band = True
                    break
                _cur = parent.get(_cur)
            el.text = (_eval_hidden(txt) if _in_band else None) or "true"
        elif t in _COLOR:
            el.text = "White" if t == "BackgroundColor" else "Black"
        elif t in ("Hyperlink", "BookmarkLink"):
            # A URL/bookmark action value is an =expression on the real server;
            # the expression-host-less render can't evaluate it, and a BLANK
            # action element is INVALID ("Action must have exactly one of
            # Hyperlink/Drillthrough/BookmarkLink"). Swap in a static literal
            # URL so the report still PUBLISHES + renders for layout checks.
            el.text = "http://localhost/o2s_static_link"
        elif t in ("FontWeight", "FontStyle", "FontFamily", "FontSize",
                   "TextDecoration", "Format", "TextAlign", "VerticalAlign"):
            # Conditional STYLE expressions (=IIf(cond, "Bold", "Normal") —
            # the bold-the-subtotal trigger pattern): collapse to the IIf's
            # DEFAULT (last string literal) branch; a BLANKED style element
            # is " is not a valid value" at publish. The live ReportViewer
            # evaluates these natively in expression mode.
            _lits = re.findall(r'"((?:[^"]|"")*)"', el.text or "")
            _safe_const = {"FontWeight": "Normal", "FontStyle": "Normal",
                           "FontFamily": "Arial", "FontSize": "10pt",
                           "TextDecoration": "None", "TextAlign": "Left",
                           "VerticalAlign": "Top"}
            el.text = ((_lits[-1] if _lits else "")
                       or _safe_const.get(t, ""))
        else:
            el.text = ""
    # A non-empty <Code> block makes the engine build the EXPRESSION HOST
    # assembly even when every expression is already a literal -- and that
    # is exactly what this mode exists to avoid (measured: the signed-DLL
    # path then dies with "Failed to load expression host assembly ...
    # Microsoft.ReportViewer.Common"). Nothing can still call into it after
    # the pass above, so empty it. The real VB in that block is compiled by
    # vb_expr_check.ps1 (System.CodeDom), which is the rail that proves it.
    for _c in root.iter():
        if _local(_c.tag) == "Code":
            _c.text = ""
    return '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(root, encoding="unicode")


def prep(src_path: str, out_base: str, rows: int = 3) -> tuple[str, str]:
    """src_path is an Oracle XML or an RDL. Returns (static_rdl_path, data_json_path)."""
    src = Path(src_path)
    raw = src.read_bytes()
    if src.suffix.lower() == ".rdl":
        rdl_xml = raw.decode("utf-8")
    else:
        repo = HERE.parent.parent
        sys.path.insert(0, str(repo / "backend"))
        from converter import convert  # noqa: E402
        rdl_xml = convert(raw)["rdl_xml"]
    static = staticize(rdl_xml)
    rdl_out = out_base + "_static.rdl"
    json_out = out_base + "_static.data.json"
    Path(rdl_out).write_text(static, encoding="utf-8")
    Path(json_out).write_text(json.dumps(synthesize_data(static, rows=rows)), encoding="utf-8")
    return rdl_out, json_out


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(2)
    out_base = sys.argv[2]
    rdl_out, json_out = prep(sys.argv[1], out_base)
    ps1 = HERE / "render_rdl.ps1"
    lib = HERE / "lib"
    print("STATIC_RDL:", rdl_out)
    print("DATA_JSON:", json_out)
    print("RENDER_CMD: powershell -NoProfile -ExecutionPolicy Bypass -File \"%s\" "
          "-RdlPath \"%s\" -DataJson \"%s\" -OutPdf \"%s\" -LibDir \"%s\""
          % (ps1, rdl_out, json_out, out_base + "_ms.pdf", lib))
