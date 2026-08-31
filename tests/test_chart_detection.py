"""Oracle <graph>/<chart> objects must be TRANSLATED to real SSRS Charts from
their own declaration, and never silently dropped nor silently redrawn as a
different graph.

The XML in this file mirrors Oracle's documented structures: the web-source
rw:graph (src/series/dataValues + a <Graph> config in an HTML comment) and the
paper-layout <graph> with <geometryInfo> plus a <graphDefinition> CDATA. Both
shapes, and the graphType vocabulary the mapping decomposes, come from the
Oracle graph DTD; chart-bearing sources in the held-out wild corpora use
exactly these shapes. The fixtures here are synthetic (generic data), so they
are structural tests, not claimed real reports.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "tools" / "renderlab"))

from converter import convert  # noqa: E402
from converter.parsers.oracle_xml import parse_oracle_xml  # noqa: E402

_CHART_FIXTURES = ROOT / "tests" / "fixtures" / "chart"

# Web-layout chart: the <rw:graph> wraps a <graph> with a <Title>.
_CHART_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<report name="SALARY_CHART" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q_1">
      <select><![CDATA[SELECT department, SUM(salary) total FROM emp GROUP BY department]]></select>
      <group name="G_dept">
        <dataItem name="department" datatype="vchar2"/>
        <dataItem name="total" datatype="number"/>
      </group>
    </dataSource>
  </data>
  <layout>
  <section name="main">
    <body width="8.5" height="11.0">
      <graph name="G_chart" src="G_dept" series="department" dataValues="total"
             graphType="bar">
        <geometryInfo x="0.5" y="0.5" width="6.0" height="4.0"/>
        <Graph>
          <Title text="Total Salary by Department"/>
          <SeriesItems><Series id="0" color="#cc66cc"/></SeriesItems>
        </Graph>
      </graph>
      <field name="F_dept" source="department">
        <geometryInfo x="0.5" y="5.0" width="3.0" height="0.2"/>
      </field>
    </body>
  </section>
  </layout>
</report>
"""


def test_chart_is_parsed_with_title_and_plot_value():
    rep = parse_oracle_xml(_CHART_XML)
    assert len(rep.charts) == 1
    c = rep.charts[0]
    assert c["title"] == "Total Salary by Department"
    assert c["category"] == "department"
    assert c["plot_value"] == "total"
    assert c["type"] == "bar"


def test_chart_surfaced_in_fidelity_not_silently_dropped():
    out = convert(_CHART_XML)
    fr = out["fidelity_report"]
    assert fr["categories"]["charts"]["count"] == 1
    assert any("chart/graph" in n for n in fr["needs_attention"])
    # the rest of the report still converts (the field + dataset survive)
    assert (out.get("preflight") or {}).get("verdict") in ("READY", "AMBER", "RED")
    assert "<DataSet" in out["rdl_xml"]


def test_no_charts_means_no_chart_note():
    xml = _CHART_XML.replace(
        b'<graph name="G_chart" src="G_dept" series="department" dataValues="total"\n'
        b'             graphType="bar">', b"<!--")
    xml = xml.replace(b"</graph>", b"-->")
    out = convert(xml)
    assert out["fidelity_report"]["categories"]["charts"]["count"] == 0
    assert not any("chart/graph" in n for n in out["fidelity_report"]["needs_attention"])


# Web-source chart form: Oracle .jsp keeps the graph as <rw:graph> OUTSIDE the
# <report> block (the <Graph> config sits in an HTML comment). Real-artifact
# verified against Oracle's tutorial emprevb.jsp ("Employees by Salary").
_WEBSRC_CHART = b"""<html><body>
<report name="EMPREVB" DTDVersion="9.0.2.0.10"><data>
<dataSource name="Q_1"><select><![CDATA[SELECT employee_id, salary FROM emp]]></select>
<group name="G_EMPLOYEE_ID"><dataItem name="EMPLOYEE_ID" datatype="number"/>
<dataItem name="SALARY" datatype="number"/></group></dataSource></data>
<layout><section name="main"><body width="8" height="9">
<repeatingFrame name="R_G" source="G_EMPLOYEE_ID"><geometryInfo x="0" y="0" width="6" height="0.3"/>
<field name="f" source="EMPLOYEE_ID"><geometryInfo x="0" y="0" width="3" height="0.2"/></field>
</repeatingFrame></body></section></layout></report>
<rw:objects><rw:dataArea>
<rw:graph id="graph" src="G_EMPLOYEE_ID" series="EMPLOYEE_ID" dataValues="SALARY">
<!-- <?xml version="1.0" ?>
<Graph version="2.6.0.23"><Title text="Employees by Salary" visible="true"/></Graph>
-->
</rw:graph>
</rw:dataArea></rw:objects>
</body></html>"""


def test_websource_rw_graph_outside_report_is_detected():
    rep = parse_oracle_xml(_WEBSRC_CHART)
    assert len(rep.charts) == 1, rep.charts
    c = rep.charts[0]
    assert c["title"] == "Employees by Salary"
    assert c["category"] == "EMPLOYEE_ID"
    assert c["plot_value"] == "SALARY"
    out = convert(_WEBSRC_CHART)
    assert out["fidelity_report"]["categories"]["charts"]["count"] == 1
    assert any("chart/graph" in n for n in out["fidelity_report"]["needs_attention"])
    # the rest of the report still converts (data model survives)
    assert "<DataSet" in out["rdl_xml"]


def test_detected_chart_emits_a_real_xsd_valid_ssrs_chart():
    """A detected chart whose category + measure are real dataset columns must
    become a REAL <Chart> in the RDL (Sum(measure) by category), XSD-valid --
    not just a note. Real-artifact verified (emprevb 'Employees by Salary')."""
    import re
    out = convert(_WEBSRC_CHART)
    rdl = out["rdl_xml"]
    assert "<Chart Name=" in rdl
    assert re.search(r"<Y>\s*=Sum\(Fields!SALARY\.Value\)", rdl)
    assert "=Fields!EMPLOYEE_ID.Value" in rdl
    xsd = ROOT / "tests" / "fixtures" / "schema" / "ReportDefinition_2008.xsd"
    if xsd.exists():
        import pytest
        etree = pytest.importorskip("lxml.etree")
        schema = etree.XMLSchema(etree.parse(str(xsd)))
        assert schema.validate(etree.fromstring(rdl.encode())), \
            "\n".join(e.message for e in schema.error_log[:5])


def test_chart_not_emitted_when_columns_absent():
    """No <Chart> when the chart's category/measure aren't dataset columns
    (would bind to nothing) -- the fidelity note still covers it."""
    bad = _WEBSRC_CHART.replace(b'dataValues="SALARY"', b'dataValues="NOPE_COL"')
    out = convert(bad)
    assert "<Chart Name=" not in out["rdl_xml"]
    assert out["fidelity_report"]["categories"]["charts"]["count"] == 1


def test_chart_shows_in_mockup_so_preview_matches_rdl():
    """A detected chart must also appear in the HTML mockup (an SVG bar chart
    with the title), so the preview and the RDL <Chart> agree -- not a
    chart in the RDL but absent from the preview."""
    html = convert(_WEBSRC_CHART)["mockup_html"]
    assert "<svg" in html
    assert "Employees by Salary" in html
    # a plain report (no chart at all) must NOT get an SVG chart block
    plain = (b'<?xml version="1.0"?><report name="PLAIN" DTDVersion="9.0.2.0.10">'
             b'<data><dataSource name="Q_1"><select><![CDATA[SELECT a FROM t]]>'
             b'</select><group name="G"><dataItem name="a" datatype="vchar2"/>'
             b'</group></dataSource></data><layout><section name="main">'
             b'<body width="8" height="9"><repeatingFrame name="R" source="G">'
             b'<geometryInfo x="0" y="0" width="6" height="0.3"/>'
             b'<field name="f" source="a"><geometryInfo x="0" y="0" width="3" height="0.2"/>'
             b'</field></repeatingFrame></body></section></layout></report>')
    # The caption reads "(sample values)" since the preview draws the
    # DECLARED family (bar/line/pie), not always bars -- assert the live
    # marker, plus the chart sheet label, so this stays a real gate.
    _plain_html = convert(plain)["mockup_html"]
    assert "(sample values)" not in _plain_html
    assert "sample bars" not in _plain_html
    assert ">Chart<" not in _plain_html


# ---------------------------------------------------------------------------
# Chart ARCHETYPE: the declaration is the truth surface
# ---------------------------------------------------------------------------
# The paper-layout dialect (the shape the real corpus graphs use): a <graph>
# layout object with its own geometry, whose bindings and <Graph> config live
# inside a <graphDefinition> CDATA. Binding semantics are the Oracle graph
# model's: src picks the group/dataset, groups is the CATEGORY, series the
# SERIES, dataValues the VALUE columns.
_PAPER_CHART = b"""<?xml version="1.0" encoding="UTF-8"?>
<report name="PAPER_GRAPH" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q_1">
      <select><![CDATA[SELECT site_code, visits FROM site_visits]]></select>
      <group name="G_SITE">
        <dataItem name="SITE_CODE" datatype="vchar2"/>
        <dataItem name="VISITS" datatype="number"/>
        <summary name="CountVISITSPerSITE_CODE" source="VISITS"
                 function="count" label="Count:"/>
      </group>
    </dataSource>
  </data>
  <layout>
    <section name="main">
      <body width="7.5" height="9.0">
        <repeatingFrame name="R_G_SITE" source="G_SITE">
          <geometryInfo x="0" y="0" width="6.0" height="0.3"/>
          <field name="F_SITE" source="SITE_CODE">
            <geometryInfo x="0" y="0" width="3.0" height="0.2"/></field>
        </repeatingFrame>
      </body>
    </section>
    <section name="trailer">
      <body width="7.5" height="9.0">
        <graph name="CT_1">
          <geometryInfo x="0.375" y="0.5" width="5.4375" height="3.0625"/>
          <graphDefinition>
          <![CDATA[<rw:graph id="CT_1" src="G_SITE" groups="SITE_CODE"
                dataValues="CountVISITSPerSITE_CODE">
<!--
<Graph version="11.1.1.0" graphType="BAR_HORIZ_CLUST">
<Title text="Visits by Site" visible="true"/>
<O1Title text="Site" visible="true"/>
<Y1Title text="Visits" visible="true"/>
<LegendArea position="LAP_LEFT"/>
<SeriesItems><Series id="0" color="#3366cc"/></SeriesItems>
</Graph>
-->
</rw:graph>]]>
          </graphDefinition>
        </graph>
      </body>
    </section>
  </layout>
</report>
"""


def test_paper_graph_declaration_is_read_whole():
    """The paper dialect hides the real bindings + config inside the
    <graphDefinition> CDATA. Every declared part must come back: the
    CATEGORY from `groups` (NOT the `src` group name), the value column, the
    declared graph type, both axis titles, the legend side, the series
    colour, the declared box and the section that owns it."""
    rep = parse_oracle_xml(_PAPER_CHART)
    assert len(rep.charts) == 1, rep.charts
    c = rep.charts[0]
    assert c["category"] == "SITE_CODE"        # groups=, not src=
    assert c["plot_values"] == ["CountVISITSPerSITE_CODE"]
    assert c["type"] == "BAR_HORIZ_CLUST"
    assert c["title"] == "Visits by Site"
    assert c["cat_axis_title"] == "Site"
    assert c["val_axis_title"] == "Visits"
    assert c["legend_position"] == "LeftCenter"
    assert c["series_colors"] == {0: "#3366cc"}
    assert c["section"] == "trailer"
    g = c["geometry"]
    assert (round(g["x"], 4), round(g["width"], 4), round(g["height"], 4)) \
        == (0.375, 5.4375, 3.0625)


def test_one_declared_graph_is_exactly_one_chart():
    """ONE declaration is ONE chart. Both emit paths (the archetype body
    builder and the post-pass appender) used to fire for the same
    declaration, so a report declaring a single graph got TWO charts."""
    import re
    for src in (_CHART_XML, _WEBSRC_CHART, _PAPER_CHART):
        rdl = convert(src)["rdl_xml"]
        names = re.findall(r'<Chart Name="([^"]+)"', rdl)
        assert len(names) == 1, (names, src[:40])


def test_value_binding_to_a_group_summary_plots_its_declared_aggregate():
    """An Oracle graph may bind dataValues to a group SUMMARY column, which
    is report-computed and never a select-list column. It must plot THAT
    summary's declared function over its declared source column -- otherwise
    the chart resolves to nothing and is silently dropped (the real corpus
    graph does exactly this)."""
    rdl = convert(_PAPER_CHART)["rdl_xml"]
    assert "<Y>=Count(Fields!VISITS.Value)</Y>" in rdl
    assert "=Fields!SITE_CODE.Value" in rdl


def test_declared_graph_type_axis_titles_legend_and_colour_reach_the_rdl():
    """Declared graph features are translated, not dropped: the horizontal
    bar family becomes Type=Bar, both axis titles become ChartAxisTitles,
    the legend keeps its declared side, and the declared series colour is
    emitted through the custom PALETTE (engine-measured: a per-series
    Style/BackgroundColor is ignored by the renderer)."""
    rdl = convert(_PAPER_CHART)["rdl_xml"]
    assert "<Type>Bar</Type>" in rdl and "<Subtype>Plain</Subtype>" in rdl
    assert "<Caption>Site</Caption>" in rdl
    assert "<Caption>Visits</Caption>" in rdl
    assert "<Position>LeftCenter</Position>" in rdl
    assert "<Palette>Custom</Palette>" in rdl
    assert "<ChartCustomPaletteColor>#3366cc</ChartCustomPaletteColor>" in rdl


def test_trailer_section_graph_starts_its_own_page():
    """SECTION PRINT ORDER: Oracle finishes one section before starting the
    next, so a TRAILER-section graph prints on its own trailer page after
    every main page. Without the page break the chart is an absolute box a
    growing tablix paints straight through (render-measured)."""
    import re
    rdl = convert(_PAPER_CHART)["rdl_xml"]
    chart = re.search(r"<Chart Name=.*?</Chart>", rdl, re.S).group(0)
    assert "<BreakLocation>Start</BreakLocation>" in chart
    # a graph declared in any other section claims no page of its own
    other = _PAPER_CHART.replace(b'<section name="trailer">',
                                 b'<section name="extra">')
    chart2 = re.search(r"<Chart Name=.*?</Chart>",
                       convert(other)["rdl_xml"], re.S).group(0)
    assert "<BreakLocation>" not in chart2


def test_declared_box_is_the_charts_box():
    """A declared geometryInfo IS the chart's box -- its width/height/left
    are used verbatim rather than a stock 6.5x3.0in frame."""
    import re
    rdl = convert(_PAPER_CHART)["rdl_xml"]
    chart = re.search(r"<Chart Name=.*?</Chart>", rdl, re.S).group(0)
    assert "<Width>5.44in</Width>" in chart
    assert "<Height>3.06in</Height>" in chart
    assert "<Left>0.38in</Left>" in chart


def test_multi_type_fixture_maps_each_declared_family():
    """The synthetic dialect fixture declares four graph types at once. Each
    maps to the RDL family the Oracle token names -- vertical bar -> Column,
    line -> Line, pie -> Shape/Pie, stacked -> Stacked -- and a multi-value
    binding becomes one series per declared value column."""
    import re
    src = ROOT / "tests" / "fixtures" / "chart" / "types_source.xml"
    rdl = convert(src.read_bytes(), src.name)["rdl_xml"]
    assert len(re.findall(r'<Chart Name="([^"]+)"', rdl)) == 4
    pairs = re.findall(r"<Type>([^<]+)</Type>\s*<Subtype>([^<]+)</Subtype>",
                       rdl)
    assert ("Column", "Plain") in pairs
    assert ("Line", "Plain") in pairs
    assert ("Shape", "Pie") in pairs
    assert ("Column", "Stacked") in pairs
    # the dual-value chart plots BOTH declared value columns
    dual = [c for c in re.findall(r"<Chart Name=.*?</Chart>", rdl, re.S)
            if "Units and Amount" in c]
    assert dual and dual[0].count("<ChartSeries ") == 2


def test_declared_feature_without_an_rdl_analog_is_declined_by_name():
    """A declared graph feature with no faithful RDL equivalent is NAMED in
    preflight, never silently redrawn as something else. The fixture
    declares a dual-axis (2Y) graph; the RDL emits one value axis."""
    src = ROOT / "tests" / "fixtures" / "chart" / "types_source.xml"
    out = convert(src.read_bytes(), src.name)
    notes = [i for i in out["preflight"]["issues"]
             if i.get("rule") == "source.chart_element"]
    assert notes, out["preflight"]["issues"]
    assert "secondary value axis" in notes[0]["message"]
    assert notes[0]["severity"] == "AMBER"
    # a report whose every declared feature IS reproduced declines nothing
    n2 = [i for i in convert(_WEBSRC_CHART)["preflight"]["issues"]
          if i.get("rule") == "source.chart_element"]
    assert n2 and "NOT reproduced" not in n2[0]["message"]


def test_multi_type_fixture_is_xsd_valid():
    """Four charts of three families in one RDL must still validate."""
    import pytest
    xsd = ROOT / "tests" / "fixtures" / "schema" / "ReportDefinition_2008.xsd"
    if not xsd.exists():
        pytest.skip("RDL schema fixture not present")
    etree = pytest.importorskip("lxml.etree")
    src = ROOT / "tests" / "fixtures" / "chart" / "types_source.xml"
    rdl = convert(src.read_bytes(), src.name)["rdl_xml"]
    schema = etree.XMLSchema(etree.parse(str(xsd)))
    assert schema.validate(etree.fromstring(rdl.encode())), \
        "\n".join(e.message for e in schema.error_log[:5])


def test_every_declared_chart_reaches_the_preview_after_the_body():
    """Mockup<->RDL parity: EVERY declared chart gets a preview sheet, and
    the sheets come after the body pages (the RDL appends charts below the
    body / on the trailer page). The preview used to lead with a single
    chart sheet, so a second declared graph was invisible and a trailer
    graph appeared before the data it summarises."""
    src = ROOT / "tests" / "fixtures" / "chart" / "types_source.xml"
    html = convert(src.read_bytes(), src.name)["mockup_html"]
    assert html.count("(sample values)") == 4
    assert html.index("Units by Region") > html.index("Region")
    # the declared pie family draws slices, not bars
    assert "<path d=" in html


def test_chart_data_point_values_survive_the_layout_staticizer():
    """RENDER-HARNESS GUARD. The verification renderer replaces expressions
    with literals; it used to blank chart <Y> values too, so every chart
    rendered as an EMPTY axis frame and the harness could not tell a working
    chart from a broken one. A simple aggregate over one field binds
    natively without the expression host (engine-measured), so it must be
    kept verbatim."""
    import pytest
    sys.path.insert(0, str(ROOT / "tools" / "renderlab"))
    try:
        from ms_layout import staticize
    except Exception:  # noqa: BLE001
        pytest.skip("renderlab not importable")
    static = staticize(convert(_PAPER_CHART)["rdl_xml"])
    assert "<Y>=Count(Fields!VISITS.Value)</Y>" in static


def test_unmappable_graph_family_is_named_not_silently_swapped():
    """A declared graph FAMILY the RDL Chart cannot draw (pareto, 3-D, a
    stock graph without its four values) still plots its data in the neutral
    column form -- but the family is NAMED in preflight. Silently drawing a
    different graph is the failure this guards."""
    import re
    for token, expect_type, expect_note in (
            (b"PARETO", "Column", "graph type PARETO"),
            (b"THREED_BAR", "Column", "3-D graph rendering"),
            (b"LINE_VERT_STACK", "Line", "stacked line graph"),
            (b"STOCK_HILO_CLOSE", "Column", "stock graph type"),
    ):
        src = _PAPER_CHART.replace(b'graphType="BAR_HORIZ_CLUST"',
                                   b'graphType="' + token + b'"')
        out = convert(src)
        types = re.findall(r"<Type>([^<]+)</Type>", out["rdl_xml"])
        assert types == [expect_type], (token, types)
        notes = [i["message"] for i in out["preflight"]["issues"]
                 if i.get("rule") == "source.chart_element"]
        assert notes and expect_note in notes[0], (token, notes)
    # a family WITH a faithful analog is translated and declines nothing
    ring = convert(_PAPER_CHART.replace(b'graphType="BAR_HORIZ_CLUST"',
                                        b'graphType="RING"'))
    assert "<Subtype>Doughnut</Subtype>" in ring["rdl_xml"]
    rnotes = [i["message"] for i in ring["preflight"]["issues"]
              if i.get("rule") == "source.chart_element"]
    assert rnotes and "NOT reproduced" not in rnotes[0]


# ---------------------------------------------------------------------------
# The DEFAULT graph-type path -- the only shape a real corpus graph declares
# ---------------------------------------------------------------------------
# Every fixture above declares an explicit graphType. A census of the three
# corpora found the opposite: the chart-bearing source there declares a
# <Graph> block with NO graphType at all, so the branch the corpus actually
# rides had no fixture, while four branches it never uses did.

_DEFAULT_TYPE = _CHART_FIXTURES / "default_type_source.xml"
_MORE_TYPES = _CHART_FIXTURES / "types_more_source.xml"


def test_graph_declaring_no_type_maps_to_the_default_family():
    """A <Graph> config with no graphType is a COMPLETE declaration, not a
    broken one: Oracle's builder omits the attribute whenever the report keeps
    the default graph. Everything else it declares must still be read, and the
    graph must plot as the default family -- a vertical bar (RDL Column)."""
    rep = parse_oracle_xml(_DEFAULT_TYPE.read_bytes())
    assert len(rep.charts) == 1, rep.charts
    c = rep.charts[0]
    # no graph FAMILY is named anywhere in the declaration
    assert c["type"].strip().lower() in ("graph", "chart"), c["type"]
    assert c["category"] == "SITE_ID"                    # groups=, not src=
    assert c["plot_values"] == ["CountREADING_IDPerSITE_ID"]
    assert c["title"] == "Readings by Site"
    assert c["cat_axis_title"] == "Site"
    assert c["val_axis_title"] == "Number of Readings"
    assert c["declined"] == []          # nothing here lacks an RDL analog
    assert c["section"] == "trailer"

    rdl = convert(_DEFAULT_TYPE.read_bytes(), _DEFAULT_TYPE.name)["rdl_xml"]
    assert len(re.findall(r'<Chart Name="', rdl)) == 1
    assert "<Type>Column</Type>" in rdl and "<Subtype>Plain</Subtype>" in rdl
    # the value binding is a group SUMMARY -- it plots that summary's own
    # declared aggregate over its declared source column
    assert "<Y>=Count(Fields!READING_ID.Value)</Y>" in rdl
    assert "=Fields!SITE_ID.Value" in rdl
    assert "<Caption>Site</Caption>" in rdl
    assert "<Caption>Number of Readings</Caption>" in rdl


def test_the_default_family_is_a_branch_not_a_catch_all():
    """Mutation proof for the test above: the same declaration WITH a
    graphType maps somewhere else. If the default were a catch-all that
    swallowed every token, the assertions above would prove nothing."""
    typed = _DEFAULT_TYPE.read_bytes().replace(
        b'<Graph version="11.1.1.0" customLayout="">',
        b'<Graph version="11.1.1.0" graphType="PIE">')
    assert typed != _DEFAULT_TYPE.read_bytes()
    rdl = convert(typed, "typed.xml")["rdl_xml"]
    assert "<Subtype>Pie</Subtype>" in rdl
    assert "<Type>Column</Type>" not in rdl


# The remaining mapped families, in the order the fixture declares them.
_MORE_EXPECTED = [
    ("BAR_HORIZ_CLUST", "Bar", "Plain", 1),
    ("BAR_HORIZ_STACK", "Bar", "Stacked", 2),
    ("BAR_HORIZ_PERCENT", "Bar", "PercentStacked", 2),
    ("BAR_VERT_PERCENT", "Column", "PercentStacked", 2),
    ("AREA_VERT_ABS", "Area", "Plain", 1),
    ("AREA_VERT_STACK", "Area", "Stacked", 2),
    ("AREA_VERT_PERCENT", "Area", "PercentStacked", 2),
    ("RING", "Shape", "Doughnut", 1),
    ("PARETO", "Column", "Plain", 1),
]


def test_every_remaining_mapped_graph_family_reaches_the_rdl():
    """The graphType vocabulary is decomposed, not table-matched, so the
    branches are: the horizontal/vertical split, the three stacking values,
    the area family, the ring family and the no-analog fallback. Each must
    reach the RDL family the Oracle token names, with one series per declared
    value column."""
    rdl = convert(_MORE_TYPES.read_bytes(), _MORE_TYPES.name)["rdl_xml"]
    charts = re.findall(r"<Chart Name=.*?</Chart>", rdl, re.S)
    assert len(charts) == len(_MORE_EXPECTED), len(charts)
    for chart, (token, typ, sub, n) in zip(charts, _MORE_EXPECTED):
        pairs = set(re.findall(
            r"<Type>([^<]+)</Type>\s*<Subtype>([^<]+)</Subtype>", chart))
        assert pairs == {(typ, sub)}, (token, pairs)
        assert chart.count("<ChartSeries ") == n, token


def test_only_the_no_analog_family_is_declined_by_name():
    """The fallback family plots its data in the neutral column form and is
    NAMED; every family that DOES have an analog declines nothing."""
    out = convert(_MORE_TYPES.read_bytes(), _MORE_TYPES.name)
    notes = [i["message"] for i in out["preflight"]["issues"]
             if i.get("rule") == "source.chart_element"]
    assert notes, out["preflight"]["issues"]
    assert "graph type PARETO" in notes[0]
    for token, _t, _s, _n in _MORE_EXPECTED[:-1]:
        assert token not in notes[0], token


@pytest.mark.parametrize("fixture", ["default_type_source.xml",
                                     "types_more_source.xml"])
def test_new_type_fixtures_are_xsd_valid(fixture):
    xsd = ROOT / "tests" / "fixtures" / "schema" / "ReportDefinition_2008.xsd"
    if not xsd.exists():
        pytest.skip("RDL schema fixture not present")
    etree = pytest.importorskip("lxml.etree")
    src = _CHART_FIXTURES / fixture
    rdl = convert(src.read_bytes(), src.name)["rdl_xml"]
    schema = etree.XMLSchema(etree.parse(str(xsd)))
    assert schema.validate(etree.fromstring(rdl.encode())), \
        "\n".join(e.message for e in schema.error_log[:5])


# ---------------------------------------------------------------------------
# RENDERED proof: every mapped family PLOTS -- an empty axis frame is a defect
# ---------------------------------------------------------------------------
# Structural assertions cannot tell a chart from a chart-shaped hole: an RDL
# whose <Y> values are empty is XSD-valid, carries the right Type/Subtype and
# renders without error -- as a titled axis frame with nothing in it. The
# measure is tools/renderlab/chart_ink.py; its numbers and limits live there.

#: The fixtures the RENDER legs below put through the engine. The coverage
#: gate reads this same tuple, so a family whose fixture is never rendered
#: cannot count as covered.
_RENDER_FIXTURES = ("source.xml", "types_source.xml", "types_more_source.xml",
                    "default_type_source.xml")

try:  # the MS ReportViewer DLLs are optional (public repo / CI)
    from render import lib_ready  # noqa: E402
    _LIB_OK = lib_ready()
except Exception:  # noqa: BLE001
    _LIB_OK = False

_needs_engine = pytest.mark.skipif(
    not _LIB_OK or sys.platform != "win32",
    reason="ReportViewer DLLs not fetched (tools/renderlab) or non-Windows")


def _render(rdl: str, out_pdf: Path, rows: int = 5) -> Path:
    from rdl_preview import render_to_pdf
    res = render_to_pdf(rdl, out_pdf, rows=rows)
    assert res.get("ok"), (res.get("log") or "")[-800:]
    return out_pdf


@_needs_engine
@pytest.mark.parametrize("fixture", _RENDER_FIXTURES)
def test_every_declared_graph_family_renders_as_a_plotted_chart(
        fixture, tmp_path):
    """Every chart of every mapped family prints PLOTTED DATA, not an empty
    frame. Together the four fixtures cover each branch of the type mapping
    -- pair by pair, proved by the coverage gate below: the default (no
    declared type), pie, ring, line, area x3, column plain / stacked /
    percent, horizontal bar plain / stacked / percent, and the fallback."""
    from chart_ink import chart_plots
    src = _CHART_FIXTURES / fixture
    rdl = convert(src.read_bytes(), src.name)["rdl_xml"]
    recs = chart_plots(_render(rdl, tmp_path / "r.pdf"), rdl)
    assert recs, fixture + " declares no chart"
    named = _emitted_pairs_by_chart(rdl)
    assert not [(r["chart"], named.get(r["chart"]), r["reason"])
                for r in recs if not r["plotted"]], \
        [(r["chart"], named.get(r["chart"]), r["coverage"], r["colours"],
          r["reason"]) for r in recs]


def _emitted_pairs_by_chart(rdl: str) -> dict:
    """``{chart name: (Type, Subtype)}`` for every <Chart> an RDL emits. One
    chart carries one pair per series; a chart carrying two different pairs
    would itself be a defect, so that case is kept visible (the value is then
    the sorted tuple of pairs) instead of being collapsed to the first."""
    out = {}
    for block in re.findall(r"<Chart Name=.*?</Chart>", rdl, re.S):
        nm = re.search(r'<Chart Name="([^"]+)"', block).group(1)
        pairs = sorted(set(re.findall(
            r"<Type>([^<]+)</Type>\s*<Subtype>([^<]+)</Subtype>", block)))
        out[nm] = pairs[0] if len(pairs) == 1 else tuple(pairs)
    return out


def _mapping_vocabulary() -> list:
    """The graph-type words the mapping itself reacts to.

    A hand-written word list is exactly the kind of instrument this campaign
    keeps finding blind: it can only enumerate the branches whoever wrote it
    remembered. So the DTD words below are UNIONED with every upper-case
    token the mapping's own source tests, which means a branch added for a
    family nobody here has heard of still enters the enumeration -- and the
    gate below then asks for a fixture for it."""
    import inspect

    from converter.generators.rdl import _ssrs_chart_type

    words = {"GRAPH", "BAR", "COLUMN", "LINE", "AREA", "PIE", "RING",
             "SCATTER", "PARETO", "RADAR", "STOCK", "THREED", "MULTI",
             "SPECTRAL", "HILO", "CLOSE"}
    try:
        words |= set(re.findall(r"[\"']([A-Z][A-Z0-9_]{1,24})[\"']",
                                inspect.getsource(_ssrs_chart_type)))
    except (OSError, TypeError):       # source not available -- DTD list only
        pass
    return sorted(words)


def _mapped_type_subtype_pairs() -> set:
    """Every (Type, Subtype) pair the mapping can EMIT, derived by exercising
    it over the graph DTD's compositional vocabulary -- FAMILY[_ORIENTATION]
    [_STACKING][_2Y], with families taken one and two words at a time, since
    the real tokens compose (THREED_BAR, STOCK_HILO_CLOSE, MULTI_PIE) and a
    branch can test for one word only in the ABSENCE of another. Deriving the
    pair set beats copying one out of the mapping: a new branch, or a new pair
    reached by an existing branch, appears here on its own, and the gate below
    then demands a rendered fixture for it instead of passing in silence."""
    from converter.generators.rdl import _ssrs_chart_type

    vocab = _mapping_vocabulary()
    families = [""] + vocab + ["%s_%s" % (a, b) for a in vocab for b in vocab
                               if a != b]
    orientations = ("", "VERT", "HORIZ")
    stackings = ("", "CLUST", "ABS", "STACK", "PERCENT")
    duals = ("", "2Y")
    pairs = set()
    for fam in families:
        for orient in orientations:
            for stack in stackings:
                for dual in duals:
                    token = "_".join(
                        part for part in (fam, orient, stack, dual) if part)
                    typ, sub, _dec = _ssrs_chart_type(token)
                    pairs.add((typ, sub))
    return pairs


def test_every_mapped_chart_type_has_a_rendered_fixture():
    """COVERAGE GATE for the render legs above. They prove every chart in the
    fixtures plots real marks; this proves the fixtures reach every
    (Type, Subtype) pair the mapping can emit. Together they say every mapped
    chart type plots -- which neither of them says alone.

    The pair that made this necessary had no fixture at all: the mapping
    emitted it, the suite rendered eleven of the twelve, and nothing was red.
    Deriving the pair set from the mapping and comparing it against what the
    RENDERED fixtures actually emit is what makes that state visible."""
    emitted = set()
    for fixture in _RENDER_FIXTURES:
        src = _CHART_FIXTURES / fixture
        rdl = convert(src.read_bytes(), src.name)["rdl_xml"]
        emitted |= {pair for pair in _emitted_pairs_by_chart(rdl).values()
                    if len(pair) == 2 and all(isinstance(x, str)
                                              for x in pair)}
    missing = _mapped_type_subtype_pairs() - emitted
    assert not missing, (
        "mapped chart types with no rendered fixture: %s" % sorted(missing))


@_needs_engine
@pytest.mark.parametrize("fixture", ["types_source.xml",
                                     "types_more_source.xml",
                                     "default_type_source.xml"])
def test_mutation_emptied_chart_values_read_as_unplotted(fixture, tmp_path):
    """MUTATION PROOF that the gate above can fail on the exact defect it
    claims to catch. Emptying each chart's <Y> is that defect (the layout
    staticizer used to do it to every chart): the report still converts, still
    validates, still renders -- every chart just comes out empty. Every chart
    of every family must then be reported unplotted."""
    from chart_ink import chart_plots
    src = _CHART_FIXTURES / fixture
    rdl = convert(src.read_bytes(), src.name)["rdl_xml"]
    empty = re.sub(r"<Y>[^<]*</Y>", "<Y></Y>", rdl)
    assert empty != rdl and "<Y></Y>" in empty
    recs = chart_plots(_render(empty, tmp_path / "empty.pdf"), empty)
    assert recs
    assert not [r["chart"] for r in recs if r["plotted"]], \
        [(r["chart"], r["coverage"], r["colours"]) for r in recs]


@_needs_engine
def test_mutation_a_chart_that_never_printed_is_reported_missing(tmp_path):
    """The other half of the gate: a declared chart the render DROPS (nothing
    at its box) must be reported too, not read as 'no charts to check'. The
    RDL is rendered with its <Chart> elements removed and measured against the
    declaration that still names them."""
    from chart_ink import chart_plots
    src = _CHART_FIXTURES / "types_more_source.xml"
    rdl = convert(src.read_bytes(), src.name)["rdl_xml"]
    dropped = re.sub(r"<Chart Name=.*?</Chart>", "", rdl, flags=re.S)
    assert "<Chart Name=" not in dropped
    recs = chart_plots(_render(dropped, tmp_path / "dropped.pdf"), rdl)
    assert len(recs) == len(_MORE_EXPECTED)
    assert all(not r["plotted"] and r["page"] is None for r in recs), recs
    assert all("no image" in r["reason"] for r in recs)


# ---------------------------------------------------------------------------
# The same proof on the REAL sources: every graph-bearing corpus file
# ---------------------------------------------------------------------------

_CORPORA = (
    Path(os.environ.get("O2S_AGENCY_CORPUS",
                        "C:/Users/maxca/Downloads/OneDrive_2026-08-06/"
                        "Artifact Folders")),
    Path(os.environ.get("O2S_WILD_CORPUS",
                        "C:/Users/maxca/Downloads/_o2s_scratch11/wild_corpus")),
    Path(os.environ.get(
        "O2S_WILD_CORPUS2",
        "C:/Users/maxca/Downloads/_o2s_scratch11/wild_corpus2")),
)


def _graph_bearing_sources():
    """Every corpus source that DECLARES a graph (optional corpora: an absent
    directory contributes nothing and the leg simply does not run)."""
    out = []
    for root in _CORPORA:
        if not root.is_dir():
            continue
        for p in sorted(root.rglob("*")):
            if p.suffix.lower() not in (".xml", ".jsp", ".htm", ".html"):
                continue
            try:
                raw = p.read_bytes()
            except OSError:
                continue
            if b"rw:graph" in raw or b"<graph " in raw or b"<graph\n" in raw:
                out.append(p)
    return out


@_needs_engine
@pytest.mark.parametrize("src", _graph_bearing_sources(),
                         ids=lambda p: p.name)
def test_corpus_graph_renders_as_a_plotted_chart(src, tmp_path):
    """The synthetic fixtures exist to cover the mapping; this is the source
    the mapping was built for. Its graph must still print real plotted data."""
    from chart_ink import chart_plots
    rdl = convert(src.read_bytes(), src.name)["rdl_xml"]
    if "<Chart Name=" not in rdl:
        pytest.skip("declared graph resolves to no dataset column")
    recs = chart_plots(_render(rdl, tmp_path / "r.pdf"), rdl)
    assert not [(r["chart"], r["reason"]) for r in recs if not r["plotted"]], \
        [(r["chart"], r["coverage"], r["colours"], r["reason"]) for r in recs]
