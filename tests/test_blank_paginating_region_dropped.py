"""NOTHING DECLARED CANNOT PAGINATE.

The data-model-only dialect declares queries, groups and parameters but no
<layout> objects at all.  It reaches the per-record builder with an empty
record rectangle -- and that region still carried its one-page-per-record
PageBreak, so the engine emitted one BLANK SHEET per data row.

MEASURED (engine render, rows=3, strict chrome-stripped blank rule) on six
wild data-model sources: 3 pages / 3 strict-blank each, ZERO extracted words
in either arm.  After the drop: 1 page / 1 strict-blank each, still zero
words -- 18 blank sheets across the corpus become 6, and no ink moves,
because there was never any ink.  (One blank sheet is the floor: an empty
body is one page, and that is what "nothing was declared" looks like.)

The refusal is untouched and must stay that way: a region with no content
items contributes nothing to the content-item count either, so preflight
still answers BLOCKER for exactly the same reason.  This is not a repair of
a broken report -- it is the artifact under an honest BLOCKER describing
itself accurately instead of padding itself out to N sheets.

Everything here is synthetic: no report, column or label from any real
source appears in this file.
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert                       # noqa: E402

NS = ("{http://schemas.microsoft.com/sqlserver/reporting/"
      "2008/01/reportdefinition}")


def _q(tag: str) -> str:
    return NS + tag


# The wild shape, reduced to its declaring parts: a query whose column list
# cannot be extracted (SELECT *), a runtime parameter, and NO <layout> at all.
# The parameter is what routes the report to the per-record builder, and the
# builder finds nothing to put in the record.
_NO_LAYOUT = (
    '<?xml version="1.0"?><report name="NOLAYOUT_T" DTDVersion="9.0.2.0.0">'
    '<data><dataSource name="Q_1" defaultGroupName="G_1">'
    '<select>select * from t</select></dataSource>'
    '<parameter name="P_X" datatype="character" initialValue="A"/>'
    '</data></report>'
).encode()

# the SAME shape with ONE declared field to print -- the control
_WITH_LAYOUT = (
    '<?xml version="1.0"?><report name="HASLAYOUT_T" DTDVersion="9.0.2.0.0">'
    '<data><dataSource name="Q_1" defaultGroupName="G_1">'
    '<select><![CDATA[select ALPHA from t]]></select>'
    '<group name="G_1"><dataItem name="ALPHA" datatype="vchar2"/></group>'
    '</dataSource>'
    '<parameter name="P_X" datatype="character" initialValue="A"/>'
    '</data>'
    '<layout><section name="main" width="8.50000" height="11.00000">'
    '<body width="8.50000" height="9.50000"><location x="0.0" y="0.0"/>'
    '<frame name="M_1"><geometryInfo x="0" y="0" width="8.5" height="3.0"/>'
    '<repeatingFrame name="R_1" source="G_1" printDirection="down">'
    '<geometryInfo x="0" y="0" width="8.5" height="2.8"/>'
    '<field name="F_A" source="ALPHA" alignment="start">'
    '<font face="Arial" size="10"/>'
    '<geometryInfo x="0.10000" y="0.20000" width="3.00000" height="0.20000"/>'
    '</field>'
    '</repeatingFrame></frame></body></section></layout></report>'
).encode()


def _regions_that_break_pages(rdl: str):
    """Every data region in the BODY carrying a PageBreak, with a flag for
    whether anything inside it can put ink on paper."""
    root = ET.fromstring(rdl.encode("utf-8"))
    body = root.find(".//" + _q("Body"))
    out = []
    if body is None:
        return out
    for region in body.iter(_q("Tablix")):
        if next(region.iter(_q("PageBreak")), None) is None:
            continue
        paints = any(next(region.iter(_q(t)), None) is not None
                     for t in ("Textbox", "Image", "Subreport", "Chart",
                               "Line"))
        out.append((region.get("Name"), paints))
    return out


@pytest.fixture(scope="module")
def no_layout():
    return convert(_NO_LAYOUT)


@pytest.fixture(scope="module")
def with_layout():
    return convert(_WITH_LAYOUT)


def test_a_region_that_prints_nothing_does_not_break_pages(no_layout):
    """THE DEFECT: one blank sheet per data row, off a region with no
    content item anywhere inside it."""
    regions = _regions_that_break_pages(no_layout["rdl_xml"])
    assert regions == [], (
        "a page-breaking region with nothing to print manufactures one blank "
        "sheet per row", regions)


def test_the_refusal_is_unchanged(no_layout):
    """The artifact is still refused, and for the same reason — this item
    makes the blank artifact honest, it does not make it usable."""
    pre = no_layout.get("preflight") or {}
    assert pre.get("verdict") == "BLOCKER", pre.get("verdict")
    rules = " ".join(str(i.get("rule", "")) for i in (pre.get("issues") or []))
    assert "rdl.no_content_items" in rules, (
        "the content-item blocker is what refuses this report", rules)
    assert pre.get("source_kind") == "data_model_only", (
        "the source is still classified by what it declares",
        pre.get("source_kind"))


def test_a_region_that_does_print_keeps_its_page_break(with_layout):
    """PROVE-THE-GATE: the same data model with ONE declared field keeps its
    per-record page break. The rule is 'prints nothing', never 'is a record
    region'."""
    regions = _regions_that_break_pages(with_layout["rdl_xml"])
    assert regions, "the control must still emit a page-breaking region"
    assert all(paints for _n, paints in regions), regions
