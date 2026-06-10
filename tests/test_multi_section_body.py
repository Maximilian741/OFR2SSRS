"""
Multi-section dashboard regression: a report whose section_main stacks several
independent data tables (each bound to a different query) must render ALL of
them, not just the first. SAMPLE_MULTI_SECTION is the canonical case (6 datasets across
4 sections); it previously rendered a single Tablix bound to one query.

These tests build a synthetic multi-section report inline so they run on any
checkout (no dependency on the developer's private artifacts).
"""
import re

import xml.etree.ElementTree as ET

from converter.models import DataItem, DataQuery, LayoutField, LayoutGroup, ParsedReport
from converter.generators.rdl import (
    generate_rdl,
    _detect_multi_section,
    _query_matches_layout_ref,
)


def _two_section_report():
    """section_main with 2 sibling frames, each a header + a repeating frame
    bound to a DIFFERENT query (via the exact <group name> mapping)."""
    qa = DataQuery(name="Q_ALPHA", sql="SELECT 1",
                   items=[DataItem(name="A_DESC"), DataItem(name="A_COUNT")],
                   group_names=["G_ALPHA"])
    qb = DataQuery(name="Q_BETA", sql="SELECT 2",
                   items=[DataItem(name="B_DESC"), DataItem(name="B_COUNT")],
                   group_names=["G_BETA"])

    def section(frame_name, title, group_src, cols, y):
        title_txt = LayoutField(name="T", kind="text", text=title, x=0.0, y=y)
        rep = LayoutGroup(
            name="R_" + frame_name, kind="repeating_frame", source_query=group_src,
            fields=[LayoutField(name="F_" + c, kind="field", source=c, x=i * 1.5, y=y + 0.25)
                    for i, c in enumerate(cols)],
        )
        return LayoutGroup(name=frame_name, kind="frame", y=y,
                           fields=[title_txt], children=[rep])

    sm = LayoutGroup(name="section_main", kind="section_main", children=[
        section("M_A", "Alpha Section", "G_ALPHA", ["A_DESC", "A_COUNT"], 0.0),
        section("M_B", "Beta Section", "G_BETA", ["B_DESC", "B_COUNT"], 2.0),
    ])
    rep = ParsedReport(name="MULTI", dtd_version="9.0", queries=[qa, qb], layout=[sm])
    return rep


def test_group_name_mapping_is_exact():
    rep = _two_section_report()
    qa, qb = rep.queries
    # exact <group name> mapping (the foundational fix)
    assert _query_matches_layout_ref(qa, "G_ALPHA")
    assert _query_matches_layout_ref(qb, "G_BETA")
    # and they don't cross-match
    assert not _query_matches_layout_ref(qa, "G_BETA")


def test_detect_multi_section_finds_two_sections():
    rep = _two_section_report()
    sections = _detect_multi_section(rep)
    assert sections is not None
    assert len(sections) == 2
    headers = [s["header"] for s in sections]
    assert "Alpha Section" in headers
    assert "Beta Section" in headers


def test_multi_section_rdl_binds_all_queries():
    rep = _two_section_report()
    rdl = generate_rdl(rep)
    # well-formed
    ET.fromstring(rdl)
    bound = set(re.findall(r"<DataSetName>([^<]+)</DataSetName>", rdl))
    assert "Q_ALPHA" in bound
    assert "Q_BETA" in bound  # the second section must render, not be dropped
    # both sections' fields are referenced
    assert "Fields!A_DESC.Value" in rdl
    assert "Fields!B_DESC.Value" in rdl


def test_single_section_not_misdetected():
    """A report with ONE data table must NOT route to the multi-section path."""
    q = DataQuery(name="Q_ONLY", sql="SELECT 1",
                  items=[DataItem(name="X"), DataItem(name="Y")],
                  group_names=["G_ONLY"])
    rep_field = LayoutField(name="F_X", kind="field", source="X", x=0.0, y=0.25)
    rf = LayoutGroup(name="R_ONLY", kind="repeating_frame",
                     source_query="G_ONLY", fields=[rep_field])
    frame = LayoutGroup(name="M_ONLY", kind="frame", children=[rf])
    sm = LayoutGroup(name="section_main", kind="section_main", children=[frame])
    rep = ParsedReport(name="SOLO", dtd_version="9.0", queries=[q], layout=[sm])
    assert _detect_multi_section(rep) is None
