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
