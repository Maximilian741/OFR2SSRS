"""Schema regression guard for the SSRS 2008 RDL rule:

    <PageBreak> is NOT a valid direct child of <TablixMember>.
    It MUST live inside a <Group> element (which can be a static
    group, i.e. a Group with only a Name attribute and no
    GroupExpressions).

Reporting Services rejects upload with:
    The element 'TablixMember' in namespace
    '...reportdefinition' has invalid child element 'PageBreak'.
    List of possible elements expected: 'Group, SortExpressions,
    TablixHeader, TablixMembers, CustomProperties, FixedData,
    Visibility, HideIfNoRows, RepeatOnNewPage, KeepWithGroup,
    DataElementName, DataElementOutput, KeepTogether'.

This test walks every Tablix in every generated RDL across every
fixture and asserts the constraint. Generic and name-agnostic.
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

# Set of element names schema-allowed as direct children of
# TablixMember per the SSRS 2008 RDL spec. See the error message
# quoted in the module docstring for the canonical list.
ALLOWED_TABLIXMEMBER_CHILDREN = {
    "Group", "SortExpressions", "TablixHeader", "TablixMembers",
    "CustomProperties", "FixedData", "Visibility", "HideIfNoRows",
    "RepeatOnNewPage", "KeepWithGroup", "DataElementName",
    "DataElementOutput", "KeepTogether",
}


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


@pytest.mark.parametrize("case_name,src_path", _cases())
def test_tablixmember_never_has_pagebreak_direct_child(case_name, src_path):
    """Walk every TablixMember; assert no PageBreak appears as a
    direct child. PageBreak goes inside Group, never directly
    inside TablixMember -- SSRS rejects upload otherwise."""
    rdl = _rdl_for(src_path)
    root = ET.fromstring(rdl)
    offenders = []
    for tm in root.iter(NS + "TablixMember"):
        for child in tm:
            local = child.tag.split("}", 1)[-1]
            if local == "PageBreak":
                offenders.append(tm.get("Name") or "<unnamed>")
                break
    assert not offenders, (
        f"[{case_name}] PageBreak appears as a DIRECT child of "
        f"TablixMember(s) {offenders}. SSRS will reject upload with "
        f"\"invalid child element 'PageBreak'\". Wrap PageBreak in "
        f"a <Group> instead."
    )


@pytest.mark.parametrize("case_name,src_path", _cases())
def test_tablixmember_children_all_schema_allowed(case_name, src_path):
    """Every direct child of TablixMember must be on the schema's
    allow-list. This is the broader version of the PageBreak guard
    above -- catches any future violation of the same shape."""
    rdl = _rdl_for(src_path)
    root = ET.fromstring(rdl)
    offenders = []
    for tm in root.iter(NS + "TablixMember"):
        for child in tm:
            local = child.tag.split("}", 1)[-1]
            if local not in ALLOWED_TABLIXMEMBER_CHILDREN:
                offenders.append((tm.get("Name") or "<unnamed>", local))
    assert not offenders, (
        f"[{case_name}] disallowed direct children of TablixMember:\n"
        + "\n".join(f"  TablixMember {nm!r}: <{tag}>"
                    for nm, tag in offenders[:6])
        + f"\nSchema allows only: {sorted(ALLOWED_TABLIXMEMBER_CHILDREN)}"
    )
