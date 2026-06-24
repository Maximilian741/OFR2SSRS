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
            rendered = "".join(out).strip()
            if not rendered:
                rendered = "Sample"
            # A staticized literal that itself begins with '=' (Oracle's
            # "= Vehicles in Yards" running-total label) would be re-parsed by
            # ReportViewer AS an expression -> "'Vehicles' is not declared".
            # Re-quoting as ="..." is also wrong (that's an expression; this
            # machine's sandbox can't load the expression host), and a leading
            # SPACE is trimmed before the '=' check. A zero-width space (U+200B,
            # not Unicode whitespace, so Trim keeps it) makes the first char
            # not '=' -> a plain literal that renders the label verbatim.
            el.text = ("​" + rendered) if rendered.startswith("=") else rendered
        elif t == "Hidden":
            el.text = "false"
        elif t in _COLOR:
            el.text = "White" if t == "BackgroundColor" else "Black"
        elif t in ("Hyperlink", "BookmarkLink"):
            # A URL/bookmark action value is an =expression on the real server;
            # the expression-host-less render can't evaluate it, and a BLANK
            # action element is INVALID ("Action must have exactly one of
            # Hyperlink/Drillthrough/BookmarkLink"). Swap in a static literal
            # URL so the report still PUBLISHES + renders for layout checks.
            el.text = "http://localhost/o2s_static_link"
        else:
            el.text = ""
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
