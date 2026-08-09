"""NESTING IS TRANSPARENT TO DECLARED GEOMETRY.

An Oracle frame is a grouping construct, not a box with padding: a child's
absolute position is the SUM of the declared offsets down the chain, and its
declared width is its width. Every Rectangle level the converter emits must
therefore contribute EXACTLY (declared coordinate - parent's declared
coordinate) -- no synthesized inset, no per-level width allowance, and no
coordinate rounding that accumulates down the chain.

Measured defect this guards (per-record invoice, truth-paired): a declared
<line> three Rectangle levels deep inherited a 0.02in inset at every level
and lost 0.02in of span at every level, so its rule printed 0.0750in short
and started 0.0400in right of the declaration. The truth PDF strokes that
cut rule at declared_x + the section's body origin (0.4790in) and 7.5210in
long against a declared 7.5209in. Same class in the other direction: a
frame's declared first-child inset (frame y -> first child y) may be
neither dropped nor padded.

The invariant asserted here is the exact one the defect violated:

    PAPER Left of a declared object = LeftMargin + sum(Left down the chain)
                                    = declared x + the section's body origin

    emitted extent = declared extent

Synthetic fixture only -- no client report, field or label names.
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402

NS = ("{http://schemas.microsoft.com/sqlserver/reporting/2008/01/"
      "reportdefinition}")

# --- the fixture's DECLARED geometry (one source of truth per assertion) ---
BODY_ORIGIN_X = 0.375         # <body><location x=...> == the sheet's margin
REC_X, REC_Y, REC_W = 0.0, 0.0, 7.50           # level 1 (per-record frame)
FIRST_CHILD_INSET = 0.09375   # declared frame y -> declared first child y
PANEL_X, PANEL_Y, PANEL_W = 0.0331, 1.0045, 7.4669   # level 2
INNER_X, INNER_Y, INNER_W = 0.0331, 1.5045, 7.4669   # level 3
FLUSH_CHILD_W = 3.00          # a member declared flush with its frame corner
RULE_X, RULE_W = 0.10, 7.40   # a rule declared INSIDE the innermost frame
EDGE_RULE_X, EDGE_RULE_W = 0.0331, 7.4669      # a rule declared FLUSH left

NESTED_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<report name="NESTED_FORM" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q_MAIN">
      <select canParse="no"><![CDATA[SELECT ACCT_NO, HOLDER, AMOUNT FROM LEDGER]]></select>
      <group name="G_MAIN">
        <dataItem name="ACCT_NO" datatype="vchar2" columnOrder="1" defaultLabel="Account"/>
        <dataItem name="HOLDER" datatype="vchar2" columnOrder="2" defaultLabel="Holder"/>
        <dataItem name="AMOUNT" datatype="number" columnOrder="3" defaultLabel="Amount"/>
      </group>
    </dataSource>
  </data>
  <layout>
  <section name="main" width="8.50000" height="11.00000">
    <body width="7.50000" height="10.00000">
      <location x="0.37500" y="0.50000"/>
      <repeatingFrame name="R_REC" source="G_MAIN" printDirection="down"
                      maxRecordsPerPage="1">
        <geometryInfo x="0.00000" y="0.00000" width="7.50000" height="9.00000"/>
        <field name="F_ACCT" source="ACCT_NO">
          <font face="Arial" size="10"/>
          <geometryInfo x="0.00000" y="0.09375" width="3.00000" height="0.18000"/></field>
        <frame name="M_PANEL">
          <geometryInfo x="0.03310" y="1.00450" width="7.46690" height="4.00000"/>
          <text name="B_PANEL_HDR">
            <geometryInfo x="0.03310" y="1.00450" width="3.00000" height="0.20000"/>
            <textSegment><font face="Arial" size="10"/><string><![CDATA[Panel]]></string></textSegment></text>
          <frame name="M_INNER">
            <geometryInfo x="0.03310" y="1.50450" width="7.46690" height="3.00000"/>
            <field name="F_HOLDER" source="HOLDER">
              <font face="Arial" size="10"/>
              <geometryInfo x="0.03310" y="1.50450" width="7.46690" height="0.18000"/></field>
            <line name="B_CUT">
              <geometryInfo x="0.10000" y="2.50000" width="7.40000" height="0.00000"/>
              <visualSettings linePattern="solid"/></line>
            <line name="B_EDGE">
              <geometryInfo x="0.03310" y="2.75000" width="7.46690" height="0.00000"/>
              <visualSettings linePattern="solid"/></line>
          </frame>
        </frame>
      </repeatingFrame>
    </body>
  </section>
  </layout>
</report>"""


def _q(tag: str) -> str:
    return NS + tag


def _in(el, tag):
    e = el.find(_q(tag))
    if e is None or not (e.text or "").strip():
        return 0.0
    return float((e.text or "").strip().replace("in", ""))


_ITEM_TAGS = ("Rectangle", "Textbox", "Image", "Line", "Tablix", "List",
              "Subreport", "Chart")


def _placed(items_el, ax=0.0, ay=0.0, out=None):
    """[(name, abs_left, abs_top, width, height, tag)] over the whole
    container tree -- absolute coordinates are the SUM down the chain."""
    if out is None:
        out = []
    for ch in list(items_el):
        tag = ch.tag.split("}")[-1]
        if tag not in _ITEM_TAGS:
            continue
        cx, cy = ax + _in(ch, "Left"), ay + _in(ch, "Top")
        out.append((ch.get("Name") or tag, cx, cy,
                    _in(ch, "Width"), _in(ch, "Height"), tag))
        sub = ch.find(_q("ReportItems"))
        if sub is not None:
            _placed(sub, cx, cy, out)
        for cc in ch.iter(_q("CellContents")):
            ri = cc.find(_q("ReportItems"))
            _placed(ri if ri is not None else cc, cx, cy, out)
    return out


def _build():
    rdl = convert(NESTED_XML, "NESTED_FORM.xml")["rdl_xml"]
    root = ET.fromstring(rdl.encode("utf-8") if isinstance(rdl, str) else rdl)
    page = root.find(".//" + _q("Page"))
    body = root.find(".//" + _q("Body"))
    assert body is not None and body.find(_q("ReportItems")) is not None
    return root, page, _placed(body.find(_q("ReportItems")))


def _rules(items):
    """The emitted hairline bars, keyed by their declared width."""
    return sorted((it for it in items if it[4] < 0.02 and it[3] > 1.0),
                  key=lambda it: it[3])


def test_declared_rule_three_levels_deep_keeps_its_declared_left_and_span():
    """THE invariant: a declared <line>'s rule prints at declared_x + the
    section's body origin, at its declared length -- however deep it is
    nested. Every synthesized per-level inset shows up here."""
    _root, page, items = _build()
    rules = _rules(items)
    assert len(rules) == 2, [it[0] for it in rules]
    margin = _in(page, "LeftMargin")
    for (name, left, _top, width, _h, _tag), (dx, dw) in zip(
            rules, ((RULE_X, RULE_W), (EDGE_RULE_X, EDGE_RULE_W))):
        paper_left = margin + left
        assert abs(paper_left - (dx + BODY_ORIGIN_X)) < 0.0005, (
            f"{name} prints at {paper_left:.4f}in; the declaration puts it "
            f"at {dx:.4f} + the body origin {BODY_ORIGIN_X:.4f} = "
            f"{dx + BODY_ORIGIN_X:.4f}in (per-level inset leaked in)")
        assert abs(width - dw) < 0.0006, (
            f"{name} is {width:.4f}in long against a declared {dw:.4f}in "
            f"({dw - width:+.4f}in of per-level width loss)")


def test_every_nested_rectangle_level_lands_on_its_declared_origin():
    """Each Rectangle level contributes exactly its declared offset -- the
    absolute (Left, Top, Width) of the three declared frames is what the
    source declares, not a per-level accumulation of 0.02in insets nor a
    2-decimal rounding of it."""
    _root, _page, items = _build()
    declared = {(REC_X, REC_Y, REC_W), (PANEL_X, PANEL_Y, PANEL_W),
                (INNER_X, INNER_Y, INNER_W)}
    rects = [it for it in items if it[5] == "Rectangle" and it[4] > 0.5]
    got = {(round(it[1], 4), round(it[2], 4), round(it[3], 4))
           for it in rects}
    for dx, dy, dw in sorted(declared):
        near = [g for g in got
                if abs(g[0] - dx) < 0.0005 and abs(g[1] - dy) < 0.0005
                and abs(g[2] - dw) < 0.0005]
        assert near, (
            f"no emitted frame at the declared ({dx:.4f}, {dy:.4f}, "
            f"w={dw:.4f}); emitted frames: "
            f"{sorted((it[0], round(it[1], 4), round(it[2], 4), round(it[3], 4)) for it in rects)}")


def test_declared_first_child_inset_is_neither_dropped_nor_invented():
    """The gap a frame declares above its first member is content: it is
    reproduced verbatim (0.09375in here) -- and where the declaration
    leaves NO gap (the panel's caption is flush with its frame corner),
    none is invented."""
    _root, _page, items = _build()
    inset_child = [it for it in items
                   if it[5] == "Textbox" and abs(it[3] - 3.00) < 0.01
                   and abs(it[4] - 0.18) < 0.01]
    assert len(inset_child) == 1, [it[0] for it in items]
    assert abs(inset_child[0][2] - (REC_Y + FIRST_CHILD_INSET)) < 0.0005, (
        f"first child rides at {inset_child[0][2]:.4f}in; the declaration "
        f"insets it {FIRST_CHILD_INSET:.5f}in below the frame's y={REC_Y}")

    flush = [it for it in items
             if it[5] == "Textbox" and abs(it[3] - FLUSH_CHILD_W) < 0.01
             and abs(it[4] - 0.20) < 0.01]
    assert len(flush) == 1, [it[0] for it in items]
    assert abs(flush[0][1] - PANEL_X) < 0.0005, (
        f"a member declared flush with its frame's left edge "
        f"(x={PANEL_X}) was pushed to {flush[0][1]:.4f}in")
    assert abs(flush[0][2] - PANEL_Y) < 0.0005, (
        f"a member declared flush with its frame's top edge "
        f"(y={PANEL_Y}) was pushed to {flush[0][2]:.4f}in")


def test_full_width_member_keeps_its_declared_width_at_every_level():
    """A box declared as wide as its frame stays that wide: the container
    span is the only limit, and it ends at the frame's declared right edge.
    (Textbox extents are emitted at 2 decimals, so the tolerance here is
    that rounding -- far below the 0.02in-per-level loss it guards.)"""
    _root, _page, items = _build()
    wide = [it for it in items
            if it[5] == "Textbox" and abs(it[3] - INNER_W) < 0.06]
    assert wide, [(it[0], it[3]) for it in items if it[5] == "Textbox"]
    for name, _left, _top, width, _h, _tag in wide:
        assert abs(width - INNER_W) < 0.006, (
            f"{name} is {width:.4f}in wide against a declared "
            f"{INNER_W:.4f}in ({INNER_W - width:+.4f}in lost to nesting)")
