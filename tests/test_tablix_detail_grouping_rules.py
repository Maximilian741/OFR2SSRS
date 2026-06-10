"""Schema regression guard for the SSRS grouping rule:

    A Tablix member whose Group has NO GroupExpressions is a
    "detail member" (it renders once per dataset row). Detail
    members can only contain STATIC inner members (TablixMembers
    with no Group element at all).

SSRS rejects upload with:
    The grouping '<name>' has a detail member with inner members.
    Detail members can only contain static inner members.

This test walks every Tablix row hierarchy in every generated RDL
and asserts: for every TablixMember whose Group has no
GroupExpressions, its nested TablixMembers (if any) must ALL be
static (no Group child).

Equivalently: if you want a grouped wrapper around a detail member
(so a PageBreak Start can fire before the data region), the wrapper
Group MUST have GroupExpressions -- e.g. a constant "=1" expression
that groups every row into one group.
"""
from __future__ import annotations
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402

FIXTURES = HERE / "fixtures" / "source_of_truth"
NS = "{http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition}"


def _cases():
    if not FIXTURES.exists():
        return []
    out = []
    for d in sorted(FIXTURES.iterdir()):
        src = d / "source.xml"
        if src.exists():
            out.append(pytest.param(d.name, src, id=d.name))
    return out


def _rdl_for(src_path: Path) -> str:
    return convert(src_path.read_bytes())["rdl_xml"]


def _is_detail_group(group_el):
    """A <Group> is 'detail' iff it has NO <GroupExpressions> child."""
    return group_el.find(NS + "GroupExpressions") is None


def _is_static_member(member_el):
    """A <TablixMember> is static iff it has NO <Group> child."""
    return member_el.find(NS + "Group") is None


def _inner_members(member_el):
    """Return the direct child <TablixMember>s nested inside this
    member's <TablixMembers>, or [] if none."""
    nested = member_el.find(NS + "TablixMembers")
    if nested is None:
        return []
    return list(nested.findall(NS + "TablixMember"))


@pytest.mark.parametrize("case_name,src_path", _cases())
def test_detail_member_only_has_static_inner_members(case_name, src_path):
    """For every TablixMember whose Group has no GroupExpressions,
    every nested TablixMember must be static (no Group)."""
    rdl = _rdl_for(src_path)
    root = ET.fromstring(rdl)
    offenders = []
    for tm in root.iter(NS + "TablixMember"):
        grp = tm.find(NS + "Group")
        if grp is None:
            continue  # static member is fine
        if not _is_detail_group(grp):
            continue  # grouped member can have grouped inner members
        # This IS a detail member. Inner members must all be static.
        for inner in _inner_members(tm):
            if not _is_static_member(inner):
                inner_grp = inner.find(NS + "Group")
                offenders.append((
                    grp.get("Name") or "<unnamed>",
                    inner_grp.get("Name") if inner_grp is not None else "?",
                ))
    assert not offenders, (
        f"[{case_name}] Detail member(s) contain non-static inner "
        f"member(s) -- SSRS rejects upload with \"detail members "
        f"can only contain static inner members\":\n"
        + "\n".join(
            f"  detail Group {outer!r} -> inner Group {inner!r}"
            for outer, inner in offenders[:6]
        )
        + "\nFix: add GroupExpressions to the outer Group (a "
        "constant '=1' works), or remove the outer wrapper."
    )


@pytest.mark.parametrize("case_name,src_path", _cases())
def test_outer_page_wrapper_has_group_expressions(case_name, src_path):
    """Specific positive guard for the OuterPageWrapper pattern
    used by per-record letter / certificate reports. If it exists,
    it must carry GroupExpressions so SSRS treats it as a grouped
    member (not detail)."""
    rdl = _rdl_for(src_path)
    if "OuterPageWrapper" not in rdl:
        pytest.skip(f"{case_name}: no OuterPageWrapper (not a per-record report)")
    root = ET.fromstring(rdl)
    for grp in root.iter(NS + "Group"):
        if grp.get("Name") == "OuterPageWrapper":
            ge = grp.find(NS + "GroupExpressions")
            assert ge is not None, (
                f"[{case_name}] OuterPageWrapper Group missing "
                f"GroupExpressions -- SSRS will treat it as a detail "
                f"member and reject Details_Record as an inner member."
            )
            # And it should have at least one GroupExpression child.
            exprs = ge.findall(NS + "GroupExpression")
            assert exprs, (
                f"[{case_name}] OuterPageWrapper GroupExpressions has "
                f"no GroupExpression child -- schema-invalid."
            )
            break
