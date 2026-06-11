"""Header-resident summary reports pack several logical pages into one
<section> (a criteria cover + a stat table, split by Oracle's
pageBreakBefore). The HTML mockup must render each as its OWN page, not
overlap them on one sheet. Real-artifact verified (CMVGY_GRANT_STATUS).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402
from converter.parsers.oracle_xml import parse_oracle_xml  # noqa: E402

# A header section with TWO content frames: a criteria cover at y=0 and a
# stat table at y=5 carrying pageBreakBefore="yes". Mirrors the CMVGY shape.
_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<report name="HDR_SUMMARY" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q_MAIN">
      <select><![CDATA[SELECT Label, Cnt FROM Stats]]></select>
      <group name="G_MAIN">
        <dataItem name="Label" datatype="vchar2"/>
        <dataItem name="Cnt" datatype="number"/>
      </group>
    </dataSource>
  </data>
  <layout>
  <section name="header">
    <body width="8.5" height="11.0">
      <frame name="M_Cover_G">
        <geometryInfo x="0" y="0.0" width="7.5" height="4.0"/>
        <text name="B_Report"><geometryInfo x="0.25" y="0.25" width="1.5" height="0.2"/>
          <textSegment><string><![CDATA[Report:]]></string></textSegment></text>
        <text name="B_Crit"><geometryInfo x="0.25" y="1.25" width="1.8" height="0.2"/>
          <textSegment><string><![CDATA[Selection Criteria:]]></string></textSegment></text>
        <field name="F_Crit" source="P_SUBTITLE">
          <geometryInfo x="2.6" y="1.25" width="3.0" height="0.2"/></field>
      </frame>
      <frame name="M_Stats_G">
        <geometryInfo x="0" y="5.0" width="7.5" height="5.0"/>
        <generalLayout pageBreakBefore="yes"/>
        <text name="B_Title"><geometryInfo x="2.5" y="5.1" width="2.5" height="0.2"/>
          <textSegment><font bold="yes"/><string><![CDATA[Stat Title]]></string></textSegment></text>
        <repeatingFrame name="R_Stat" source="G_MAIN" printDirection="down">
          <geometryInfo x="0" y="5.5" width="7.5" height="0.3"/>
          <field name="F_Label" source="Label">
            <geometryInfo x="0.25" y="5.5" width="3.0" height="0.2"/></field>
          <field name="F_Cnt" source="Cnt">
            <geometryInfo x="4.0" y="5.5" width="1.0" height="0.2"/></field>
        </repeatingFrame>
      </frame>
    </body>
  </section>
  </layout>
</report>
"""


def test_pagebreak_before_is_parsed():
    rep = parse_oracle_xml(_XML)

    found = {}

    def walk(g):
        found[g.name] = getattr(g, "page_break_before", False)
        for c in (g.children or []):
            walk(c)

    for lg in rep.layout:
        walk(lg)
    assert found.get("M_Stats_G") is True
    assert found.get("M_Cover_G") is False


def test_header_summary_renders_separate_pages():
    html = convert(_XML)["mockup_html"]
    pages = re.findall(r"Page (\d+) of (\d+)", html)
    # at least the cover page and the stat page must be distinct
    assert pages, "no page labels emitted"
    totals = {int(b) for _a, b in pages}
    assert max(totals) >= 2, f"expected >=2 pages, got {pages}"


def test_section_page_groups_splits_by_content_frame():
    """The split returns one root per top-level content frame, so two frames
    at the same y (a cover + a detail) become separate pages -- the core fix
    (y-banding alone could not separate same-y frames)."""
    from converter.preview.html_mockup import _section_page_groups
    rep = parse_oracle_xml(_XML)
    roots = _section_page_groups(rep, "section_header")
    names = [r.name for r in roots]
    assert "M_Cover_G" in names and "M_Stats_G" in names


def test_root_scoping_has_no_negative_y():
    """Re-basing must use the real min-y of the frame's content; nested
    children sitting above the frame's declared y must NOT produce negative
    y (which bled page-2 content upward onto page 1)."""
    from converter.preview.html_mockup import (
        _section_page_groups, _doc_collect_positioned)
    rep = parse_oracle_xml(_XML)
    for r in _section_page_groups(rep, "section_header"):
        elems, _w, _h = _doc_collect_positioned(rep, "section_header", root=r)
        assert all(e["y"] >= 0.0 for e in elems), \
            [(e["y"], e.get("source") or e.get("text")) for e in elems if e["y"] < 0]


def test_indicator_fields_render_as_yes_no_not_sample_text():
    """Boolean indicator/flag columns (*_Ind, *_Flag, *_YN) must read as a
    Y/N marker, never a generic 'Sample Value A' block. No false positives
    on words that merely contain the substring (binding/finding/index)."""
    from converter.preview.html_mockup import _sample_for_source
    for s in ("Grant_Ind", "Cars_Ind", "Q3_Ind", "Status_Flag", "Active_YN"):
        assert _sample_for_source(s, 0) in ("Y", "N"), s
    for s in ("binding", "finding", "index", "kind_of_thing"):
        assert _sample_for_source(s, 0) not in ("Y", "N"), s


def test_large_vertical_gap_is_collapsed_on_summary_pages():
    """A root-scoped summary page collapses a >1.5in empty vertical band
    (Oracle container frames shrink-to-fit) -- no giant blank stripe."""
    from converter.preview.html_mockup import (
        _section_page_groups, _doc_collect_positioned)
    rep = parse_oracle_xml(_XML)
    roots = _section_page_groups(rep, "section_header")
    for r in roots:
        elems, _w, _h = _doc_collect_positioned(rep, "section_header", root=r)
        ys = sorted(e["y"] for e in elems)
        # no adjacent pair of element tops should be separated by a > ~1.5in
        # void (allowing for element heights, check raw top gaps modestly)
        big = [b - a for a, b in zip(ys, ys[1:]) if b - a > 1.5]
        assert not big, f"uncollapsed gap(s) {big} in {r.name}"


def test_title_formula_resolves_to_report_name_not_keyword_sample():
    """A formula field named after the report (CP_<REPORTNAME>) is the title
    formula -- it must show the report's own name, never a keyword-matched
    sample like 'Active' from a STATUS substring."""
    from converter.preview.html_mockup import (
        _doc_field_caption_and_value, _humanize_report_title)
    rep = parse_oracle_xml(_XML)
    # synthetic report is named HDR_SUMMARY -> CP_HDR_SUMMARY is its title
    val = _doc_field_caption_and_value("CP_HDR_SUMMARY", rep, {}, 0)
    assert "Hdr" in val or "HDR" in val, val
    assert val not in ("Active", "Pending")
    assert _humanize_report_title("CMVGY_GRANT_STATUS") == "CMVGY Grant Status"


def test_root_scoping_isolates_one_frame_and_rebases_y():
    """Collecting with a frame root yields ONLY that frame's elements, with y
    re-based so the page starts at the top of its own sheet."""
    from converter.preview.html_mockup import (
        _section_page_groups, _doc_collect_positioned)
    rep = parse_oracle_xml(_XML)
    roots = {r.name: r for r in _section_page_groups(rep, "section_header")}
    cover_elems, _w, _h = _doc_collect_positioned(rep, "section_header",
                                                  root=roots["M_Cover_G"])
    stat_elems, _w2, _h2 = _doc_collect_positioned(rep, "section_header",
                                                   root=roots["M_Stats_G"])
    cover_text = " ".join(str(e.get("text", "")) for e in cover_elems)
    stat_text = " ".join(str(e.get("text", "")) for e in stat_elems)
    # cover has the criteria label, NOT the stat title; stat has the title.
    assert "Selection Criteria" in cover_text
    assert "Stat Title" not in cover_text
    assert "Stat Title" in stat_text
    # the stat frame (Oracle y=5.0) is re-based so its title sits near y=0.
    title = next(e for e in stat_elems if "Stat Title" in str(e.get("text", "")))
    assert title["y"] < 1.0, title["y"]
