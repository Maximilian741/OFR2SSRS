"""Oracle <graph>/<chart> objects must be DETECTED and surfaced (never
silently dropped), so a user with a chart report is told to recreate it as
an SSRS Chart. The XML mirrors Oracle's documented rw:graph structure
(src/series/dataValues + <Title text>). Synthetic — Oracle charts ship only
as binary .rdf publicly, so no real corpus sample exists; this is a
structural test, not a claimed real report.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402
from converter.parsers.oracle_xml import parse_oracle_xml  # noqa: E402

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
    assert "sample bars" not in convert(plain)["mockup_html"]
