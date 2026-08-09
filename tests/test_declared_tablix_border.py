"""The columnar Tablix must not ship chrome the source never declared.

The generator used to write an unconditional
``<Border><Style>Solid</Style><Color>LightGrey</Color></Border>`` on every
columnar Tablix, plus literal gray outlines on the header cells, the detail
cells and the synthesized grand-total row. None of that is declared by the
Oracle source: it rendered as four #D3D3D3 strokes framing the table and a
full gray cell grid on every deployed report.

These guards pin the dialect: NO declaration -> NO border; a declared
``linePattern`` on the frame that owns the detail region -> that frame's own
ink, width and (hideXBorder) edges.

Synthetic sources only -- no client report, field or label names.
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402

NS = "{http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition}"


def _flat_xml(frame_vs: str = "", rf_vs: str = "", summary: str = "") -> bytes:
    """A plain columnar report. ``frame_vs`` decorates the master frame that
    encloses the detail repeating frame; ``rf_vs`` decorates the repeating
    frame itself. Both empty == a source that declares no borders at all.
    ``summary`` adds a report-level aggregate so the synthesized grand-total
    footer row is emitted."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<report name="GRID_SRC" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q_ROWS">
      <select canParse="no"><![CDATA[SELECT COL_A, COL_B, COL_C FROM T_ROWS]]></select>
      <group name="G_ROWS">
        <dataItem name="COL_A" datatype="vchar2" columnOrder="1" defaultLabel="Col A">
          <dataDescriptor expression="COL_A" width="30"/>
        </dataItem>
        <dataItem name="COL_B" datatype="vchar2" columnOrder="2" defaultLabel="Col B" breakOrder="none">
          <dataDescriptor expression="COL_B" width="30"/>
        </dataItem>
        <dataItem name="COL_C" oracleDatatype="number" columnOrder="3" defaultLabel="Col C" breakOrder="none">
          <dataDescriptor expression="COL_C" oracleDatatype="number" precision="10"/>
        </dataItem>
      </group>
      {summary}
    </dataSource>
  </data>
  <layout>
  <section name="main" width="11.00000" height="8.50000" orientation="landscape">
    <body width="10.42627" height="7.14587">
      <location x="0.29248" y="0.76038"/>
      <frame name="M_G_ROWS_GRPFR">
        <geometryInfo x="0.02075" y="0.40000" width="10.37512" height="0.60000"/>
        {frame_vs}
        <repeatingFrame name="R_G_ROWS" source="G_ROWS" printDirection="down">
          <geometryInfo x="0.02075" y="0.44812" width="10.37512" height="0.20000"/>
          {rf_vs}
          <field name="F_COL_A" source="COL_A"><font face="Arial" size="10"/>
            <geometryInfo x="0.02075" y="0.44812" width="1.50000" height="0.18750"/></field>
          <field name="F_COL_B" source="COL_B"><font face="Arial" size="10"/>
            <geometryInfo x="1.60000" y="0.44812" width="4.00000" height="0.18750"/></field>
          <field name="F_COL_C" source="COL_C" alignment="end"><font face="Arial" size="10"/>
            <geometryInfo x="5.80000" y="0.44812" width="1.20000" height="0.18750"/></field>
        </repeatingFrame>
      </frame>
    </body>
  </section>
  </layout>
</report>""".encode("utf-8")


def _tablix(rdl_xml: str) -> ET.Element:
    root = ET.fromstring(rdl_xml)
    for tbx in root.iter(f"{NS}Tablix"):
        if tbx.get("Name") == "Tablix_Main":
            return tbx
    raise AssertionError("no columnar Tablix_Main in the generated RDL")


def _drawn_borders(el: ET.Element):
    """Every border element under ``el`` whose Style actually paints."""
    out = []
    for tag in ("Border", "TopBorder", "BottomBorder",
                "LeftBorder", "RightBorder"):
        for b in el.iter(f"{NS}{tag}"):
            st = b.find(f"{NS}Style")
            if st is not None and (st.text or "").strip() not in ("", "None"):
                col = b.find(f"{NS}Color")
                out.append((tag, (col.text or "").strip()
                            if col is not None else ""))
    return out


def test_no_declared_border_means_no_border_anywhere_on_the_tablix():
    rdl_xml = convert(_flat_xml())["rdl_xml"]
    tbx = _tablix(rdl_xml)
    drawn = _drawn_borders(tbx)
    assert drawn == [], (
        "a source declaring no linePattern must produce a columnar Tablix "
        f"with no painted border; got {drawn}"
    )
    # ... and the Tablix must not carry a Style whose only content is chrome
    style = tbx.find(f"{NS}Style")
    if style is not None:
        assert list(style) == [], (
            "the Tablix Style must stay empty when nothing is declared; "
            f"got {[c.tag.split('}')[-1] for c in style]}"
        )


def test_no_invented_light_grey_frame_literal_in_the_whole_rdl():
    rdl_xml = convert(_flat_xml())["rdl_xml"]
    for literal in ("LightGrey", "LightGray", "#d0d0d0", "#D0D0D0",
                    "#a0a0a0", "#A0A0A0", "#eef2f6", "#EEF2F6"):
        assert literal not in rdl_xml, (
            f"house chrome literal {literal!r} shipped in a report that "
            "declares no borders"
        )


def test_synthesized_grand_total_row_carries_no_invented_chrome():
    """The grand-total row is the converter's own addition (it re-implements a
    declared <summary>), so it must not paint a tinted band or a gray grid the
    layout never declared."""
    summary = ('<summary name="CS_TOTAL" function="sum" source="COL_C" '
               'reset="report" datatype="NUMBER" width="20"/>')
    rdl_xml = convert(_flat_xml(summary=summary))["rdl_xml"]
    tbx = _tablix(rdl_xml)
    foot = [tb for tb in tbx.iter(f"{NS}Textbox")
            if (tb.get("Name") or "").startswith("Foot_")]
    assert foot, "the declared report-level <summary> must emit a total row"
    for tb in foot:
        assert _drawn_borders(tb) == [], (
            f"total-row cell {tb.get('Name')} painted an undeclared border")
        style = tb.find(f"{NS}Style")
        bg = style.find(f"{NS}BackgroundColor") if style is not None else None
        assert bg is None, (
            f"total-row cell {tb.get('Name')} painted an undeclared fill "
            f"{bg.text if bg is not None else ''}")


def test_declared_region_frame_border_reaches_the_tablix():
    vs = '<visualSettings linePattern="solid" lineWidth="2" lineColor="black"/>'
    rdl_xml = convert(_flat_xml(frame_vs=vs))["rdl_xml"]
    tbx = _tablix(rdl_xml)
    style = tbx.find(f"{NS}Style")
    assert style is not None, "a declared frame border must reach the Tablix"
    border = style.find(f"{NS}Border")
    assert border is not None, "declared solid frame -> a Tablix <Border>"
    assert (border.find(f"{NS}Style").text or "").strip() == "Solid"
    assert (border.find(f"{NS}Color").text or "").strip() == "#000000"
    assert (border.find(f"{NS}Width").text or "").strip() == "2pt"


def test_declared_hidden_edges_paint_only_the_surviving_edges():
    vs = ('<visualSettings linePattern="solid" lineColor="black" '
          'hideLeftBorder="yes" hideRightBorder="yes" hideTopBorder="yes"/>')
    rdl_xml = convert(_flat_xml(frame_vs=vs))["rdl_xml"]
    tbx = _tablix(rdl_xml)
    style = tbx.find(f"{NS}Style")
    assert style is not None
    tags = [c.tag.split("}")[-1] for c in style]
    assert tags == ["BottomBorder"], (
        "hideLeft/Right/Top on the region frame must leave ONLY the declared "
        f"bottom edge; got {tags}"
    )


def test_textbox_default_is_no_border():
    """The shared textbox emitter defaulted to a house gray, so any caller
    that had nothing to declare silently shipped a grid. Asking for nothing
    must produce nothing."""
    from converter.generators.rdl import _build_textbox  # noqa: PLC0415

    parent = ET.Element("Parent")
    tb = _build_textbox(parent, "Tb_Default", "=Nothing")
    assert _drawn_borders(tb) == [], (
        f"_build_textbox default painted {_drawn_borders(tb)}")


MATRIX_FIX = ROOT / "tests" / "fixtures" / "matrix" / "source.xml"


def _matrix_tablix(rdl_xml: str) -> ET.Element:
    root = ET.fromstring(rdl_xml)
    for tbx in root.iter(f"{NS}Tablix"):
        if (tbx.get("Name") or "").startswith("Tablix_Matrix"):
            return tbx
    raise AssertionError("no cross-tab Tablix in the generated RDL")


def test_matrix_declaring_no_box_gets_no_grid():
    """The cross-tab builder shipped a literal gray grid (and a slate-blue
    band) on every matrix. A matrix object that declares no linePattern must
    reach the RDL with no painted border anywhere."""
    src = MATRIX_FIX.read_bytes()
    assert b"linePattern" not in src, "fixture must declare no borders"
    tbx = _matrix_tablix(convert(src)["rdl_xml"])
    assert _drawn_borders(tbx) == [], (
        f"undeclared cross-tab grid shipped: {_drawn_borders(tbx)}")


def test_matrix_declared_box_reaches_the_cross_tab():
    src = MATRIX_FIX.read_bytes().replace(
        b'<matrix name="M_sales">',
        b'<matrix name="M_sales">'
        b'<visualSettings linePattern="solid" lineWidth="1" lineColor="black"/>',
        1)
    tbx = _matrix_tablix(convert(src)["rdl_xml"])
    drawn = _drawn_borders(tbx)
    assert drawn, "a declared matrix box must reach the cross-tab Tablix"
    assert all(c == "#000000" for _t, c in drawn), drawn
    # ... and the Tablix's OWN Style must carry the declared outer box, not
    # just the cells inside it
    style = tbx.find(f"{NS}Style")
    assert style is not None and _drawn_borders(style), (
        "the matrix object's declared box must land on the Tablix itself")


def test_transparent_line_pattern_still_draws_nothing():
    vs = '<visualSettings linePattern="transparent" lineColor="black"/>'
    rdl_xml = convert(_flat_xml(frame_vs=vs))["rdl_xml"]
    assert _drawn_borders(_tablix(rdl_xml)) == [], (
        "linePattern=transparent is Oracle's 'do not paint' -- it must not "
        "become a solid RDL border"
    )
