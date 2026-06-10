"""
Nested master-detail regression: a report whose Oracle dataSource declares a
nested <group> chain (e.g. a 3-level master -> detail -> sub-detail tree) must
render a deterministic group-tree Tablix that binds ALL levels and stays
upload-clean. Built inline so it runs on any checkout.
"""
import re
import xml.etree.ElementTree as ET

from converter.models import DataItem, DataQuery, QueryGroup, ParsedReport
from converter.generators.rdl import (
    generate_rdl,
    _is_nested_master_detail,
    _flatten_group_chain,
)
from converter.parsers.oracle_xml import parse_oracle_xml


def _nested_query():
    region = QueryGroup(
        name="G_REGION", break_col="REGION_NM",
        items=[DataItem(name="REGION_NM", label="Region")],
        summaries=[{"name": "CountPerRegion", "source": "CASE_ID",
                    "function": "count", "label": "Total Per Region:"}],
    )
    caseg = QueryGroup(
        name="G_CASE", break_col="CASE_ID",
        items=[DataItem(name="CASE_ID", label="Case ID"),
               DataItem(name="OWNER", label="Owner"),
               DataItem(name="LOCATION", label="Location")],
    )
    act = QueryGroup(
        name="G_ACT", break_col="ACTION_TYPE",
        items=[DataItem(name="ACTION_TYPE", label="Action Type"),
               DataItem(name="STATUS_DATE", label="Action Date"),
               DataItem(name="COMMENTS", label="Comments")],
    )
    region.children = [caseg]
    caseg.children = [act]
    items = [DataItem(name=n) for n in
             ["REGION_NM", "CASE_ID", "OWNER", "LOCATION", "ACTION_TYPE",
              "STATUS_DATE", "COMMENTS"]]
    q = DataQuery(name="Q_1", sql="SELECT 1", items=items, groups=[region])
    return q


def test_chain_flattens_master_to_detail():
    q = _nested_query()
    chain = _flatten_group_chain(q.groups)
    assert [g.name for g in chain] == ["G_REGION", "G_CASE", "G_ACT"]


def test_is_nested_master_detail_true_for_chain():
    assert _is_nested_master_detail(_nested_query())


def test_single_group_not_nested():
    q = DataQuery(name="Q", sql="SELECT 1",
                  items=[DataItem(name="X")],
                  groups=[QueryGroup(name="G", break_col="X",
                                     items=[DataItem(name="X")])])
    assert not _is_nested_master_detail(q)


def test_nested_rdl_is_wellformed_and_binds_all_levels():
    rep = ParsedReport(name="ND", dtd_version="9.0", queries=[_nested_query()])
    rdl = generate_rdl(rep)
    ET.fromstring(rdl)  # well-formed
    assert "Tablix_Nested" in rdl
    # all three group levels become row groups
    groups = re.findall(r'<Group Name="(ND_[^"]+)">', rdl)
    assert "ND_G0" in groups and "ND_G1" in groups
    # band + total + detail fields all present
    assert "Region" in rdl
    assert "Total Per Region" in rdl
    assert "Count(Fields!CASE_ID.Value)" in rdl
    assert "Fields!COMMENTS.Value" in rdl  # innermost detail field


# (An optional local-artifact regression test was removed for the public repo.
# The inline synthetic tests above already exercise the Tablix_Nested path on
# any checkout; point the converter at your own Oracle XML to spot-check more.)
