"""Static, data-INDEPENDENT layout auditor for generated RDL.

The render-and-eyeball loop has a blind spot: the offline render substitutes
short placeholder data, so a box that *clips* a real value can look fine, and a
human glance does not exhaustively check every element. This auditor mechanically
inspects EVERY textbox in a generated RDL and flags the failure modes that hide
behind placeholder renders:

* ``layout.height_overflow`` — a ``CanGrow=false`` textbox whose declared content
  (paragraph count + ``vbCrLf`` line breaks, at the box's largest font) needs more
  vertical space than the box provides, so every line past the fit clips. This is
  the class that dropped the "expires <date>" wallet-card date and stacked
  signature blocks: N fixed lines crammed into a one-line box.

The height estimate is deliberately calibrated to how the report engine lays text
out: the first ``n-1`` lines each consume a full leading-height
(``font_pt * LEADING / 72``) and the LAST line only needs its glyph height
(``font_pt / 72``) — a single tall line therefore does NOT false-positive in a box
sized to its own font (a 24pt line fits a 0.34in box), while a genuine multi-line
overflow still trips.

Returns a list of dicts ``{rule, severity, item, message, need_in, box_in}``.
Purely structural; no report names or data values are referenced.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import List, Dict

# Engine line leading as a multiple of the font point size. 1.2 matches the
# boundary cases observed in the MS-engine renders used to calibrate this.
_LEADING = 1.2
# Rendering slack: a box may be a hair shorter than the strict estimate and still
# not clip (region padding rounds in the box's favor). Below this it is noise.
_SLACK_IN = 0.04


def _localname(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def _ns(root) -> str:
    return "{" + root.tag.split("}")[0].strip("{") + "}" if "}" in root.tag else ""


def audit_layout(rdl_xml) -> List[Dict]:
    """Return a list of static layout issues for the given RDL (str or bytes)."""
    if isinstance(rdl_xml, str):
        rdl_xml = rdl_xml.encode("utf-8")
    try:
        root = ET.fromstring(rdl_xml)
    except ET.ParseError:
        return []
    NS = _ns(root)

    def q(t):
        return NS + t

    def child_text(el, tag):
        e = el.find(q(tag))
        return e.text if e is not None else None

    def child_num(el, tag):
        t = child_text(el, tag)
        if not t:
            return None
        try:
            return float(t.replace("in", "").strip())
        except ValueError:
            return None

    issues: List[Dict] = []
    for tb in root.iter(q("Textbox")):
        name = tb.get("Name") or "?"
        box_h = child_num(tb, "Height")
        can_grow = (child_text(tb, "CanGrow") or "").strip().lower() == "true"
        if box_h is None or can_grow:
            # Growable boxes expand to fit; boxes without a declared height are
            # inside a data region whose row height governs growth — skip both.
            continue

        n_lines = 0
        max_pt = 0.0
        for para in tb.iter(q("Paragraph")):
            run_vals = []
            for run in para.iter(q("TextRun")):
                run_vals.append(child_text(run, "Value") or "")
                style = run.find(q("Style"))
                fs = child_text(style, "FontSize") if style is not None else None
                if fs:
                    try:
                        max_pt = max(max_pt, float(fs.replace("pt", "").strip()))
                    except ValueError:
                        pass
            joined = " ".join(run_vals)
            # Each Paragraph is one line; an explicit vbCrLf inside a run adds one.
            n_lines += 1 + joined.count("vbCrLf")
        if n_lines <= 0:
            continue
        if max_pt <= 0:
            max_pt = 10.0

        leading_in = max_pt * _LEADING / 72.0
        glyph_in = max_pt / 72.0
        need_in = (n_lines - 1) * leading_in + glyph_in
        if need_in > box_h + _SLACK_IN:
            issues.append({
                "rule": "layout.height_overflow",
                "severity": "warning",
                "item": name,
                "message": (
                    f"{name}: {n_lines} text line(s) at {max_pt:.0f}pt need "
                    f"~{need_in:.2f}in but the CanGrow=false box is only "
                    f"{box_h:.2f}in tall — content past the fit clips."
                ),
                "need_in": round(need_in, 3),
                "box_in": round(box_h, 3),
            })
    return issues
