"""Mailing-label / multi-up archetype: a repeating frame with
printDirection="acrossDown" + a boilerplate label cell must convert to a
ROW-MAJOR grouped Tablix (column group =(RowNumber(Nothing)-1) Mod N, row
group =Ceiling(RowNumber(Nothing)/N)) -- NOT newspaper Page Columns (SSRS
fills those DOWN-then-across and slices a body wider than one column at the
column edge: engine-measured, every record stacked left + address lines
split mid-word) and NOT a tall one-per-row table (trailing blank page).
Real-artifact verified (a 2-up mailing-label report; see
test_across_down_fill_order for the engine probes). Must NOT hijack
matrices/normal tables.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402
from converter.parsers.oracle_xml import parse_oracle_xml  # noqa: E402
from converter.generators.rdl import _find_label_spec  # noqa: E402

_LABEL_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<report name="MAIL_LABELS" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q_1">
      <select><![CDATA[SELECT first_name, last_name, city FROM contacts]]></select>
      <group name="G_first">
        <dataItem name="first_name" datatype="vchar2"/>
        <dataItem name="last_name" datatype="vchar2"/>
        <dataItem name="city" datatype="vchar2"/>
      </group>
    </dataSource>
  </data>
  <layout>
  <section name="main">
    <body width="8.5" height="11.0">
      <repeatingFrame name="R_G_first" source="G_first"
                      printDirection="acrossDown">
        <geometryInfo x="0" y="0" width="3.0" height="1.0"/>
        <text name="B_tbp">
          <geometryInfo x="0" y="0" width="3.0" height="1.0"/>
          <textSegment><font face="helvetica" size="10"/>
            <string><![CDATA[To
Mr./Ms &<first_name> &<last_name>
&<city>]]></string></textSegment>
        </text>
      </repeatingFrame>
    </body>
  </section>
  </layout>
</report>
"""

# A normal flat table (printDirection down, data columns) must NOT match.
_TABLE_XML = _LABEL_XML.replace(b'printDirection="acrossDown"',
                                b'printDirection="down"')


def test_label_spec_detected():
    spec = _find_label_spec(parse_oracle_xml(_LABEL_XML))
    assert spec is not None
    assert 2.5 <= spec["cell_w"] <= 3.5
    assert spec["fields"]


def test_label_emits_row_major_tiled_tablix():
    """STRICTER replacement for the old page-Columns assertion: newspaper
    Columns fill DOWN-then-across and slice the body at the column edge
    (engine-measured, see module docstring), so the label archetype must
    emit the ROW-MAJOR grouped Tablix and no page Columns at all."""
    rdl = convert(_LABEL_XML)["rdl_xml"]
    assert '<Tablix Name="Tablix_Labels">' in rdl
    mc = re.search(
        r"<GroupExpression>=\(RowNumber\(Nothing\) - 1\) Mod (\d+)"
        r"</GroupExpression>", rdl)
    mr = re.search(
        r"<GroupExpression>=Ceiling\(RowNumber\(Nothing\) / (\d+)\)"
        r"</GroupExpression>", rdl)
    assert mc and mr, "expected the row-major tile group expressions"
    # 3.0in tiles on the declared 8.5in body => 2 across, and BOTH groups
    # must agree on N or the transpose math breaks.
    assert int(mc.group(1)) == int(mr.group(1)) == 2
    # The down-fill construct must be GONE: page Columns transpose the fill
    # order AND slice the multi-tile body at the newspaper column edge.
    assert "<Columns>" not in rdl and "<ColumnSpacing>" not in rdl
    # The root Width must budget ALL tiles (>= 2 x 3.0in) -- the defective
    # construct emitted ONE tile's width (3.06in), which shrank the
    # printable area and the residual right margin sliced the grid (the
    # measured splice-gap defect). A source-declared body width wider than
    # the grid may win (declared body width IS the body width), so the
    # measurement is a floor, not equality.
    mw = re.search(r"<Width>([\d.]+)in</Width>\s*<Page>", rdl)
    assert mw and float(mw.group(1)) >= 6.0 - 0.05


def test_label_xsd_valid_and_ready():
    import pytest
    out = convert(_LABEL_XML)
    assert (out.get("preflight") or {}).get("verdict") == "READY"
    xsd = ROOT / "tests" / "fixtures" / "schema" / "ReportDefinition_2008.xsd"
    if not xsd.exists():
        pytest.skip("XSD not bundled")
    etree = pytest.importorskip("lxml.etree")
    schema = etree.XMLSchema(etree.parse(str(xsd)))
    assert schema.validate(etree.fromstring(out["rdl_xml"].encode())), \
        "\n".join(e.message for e in schema.error_log[:5])


def test_normal_table_is_not_misdetected_as_label():
    # printDirection=down with data columns (no boilerplate text) -> not a label
    assert _find_label_spec(parse_oracle_xml(_TABLE_XML)) is None
    rdl = convert(_TABLE_XML)["rdl_xml"]
    assert "<Tablix Name=\"Tablix_Labels\">" not in rdl


def test_mockup_tiles_labels_multi_up_not_one_per_page():
    """The HTML mockup must TILE the label cell multiple-up on one sheet
    (matching the RDL + Oracle print), not render one label per page."""
    html = convert(_LABEL_XML)["mockup_html"]
    assert "-up per row" in html            # the tiling note
    assert html.count("display:inline-block") >= 3   # several tiled cells
    # one sheet, not many
    assert html.count("Page 1 of 1") == 1 or "of 1" in html
    # a normal table must NOT use the label tiling path
    assert "-up per row" not in convert(_TABLE_XML)["mockup_html"]
