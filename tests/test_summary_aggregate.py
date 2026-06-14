"""Oracle <summary> (count/sum/avg/...) referenced by a &TOKEN must resolve
to a REAL SSRS aggregate scoped to its source column's dataset -- so report
totals COMPUTE instead of shipping a NULL placeholder. Real-artifact driven
(a report-level count footer total whose <summary> sits under <data>).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter.parsers.oracle_xml import parse_oracle_xml  # noqa: E402
from converter.generators.rdl import _build_token_resolver  # noqa: E402

_XML = b"""<?xml version="1.0"?><report name="R" DTDVersion="9.0.2.0.10"><data>
<dataSource name="Q_1"><select><![CDATA[SELECT nume, oras, sal FROM t]]></select>
<group name="G"><dataItem name="nume" datatype="vchar2"/>
<dataItem name="oras" datatype="vchar2"/>
<dataItem name="sal" datatype="number"/></group>
<summary name="CountnumePerReport" source="nume" function="count" reset="report"/>
<summary name="SumsalPerReport" source="sal" function="sum" reset="report"/>
<summary name="AvgsalPerReport" source="sal" function="average" reset="report"/>
</dataSource></data><layout><section name="main"><body width="8" height="9">
<frame name="M_G_HDR"><geometryInfo x="0" y="0" width="6" height="0.25"/>
<text name="L_nume"><geometryInfo x="0" y="0" width="2" height="0.2"/>
<textSegment><string><![CDATA[Nume]]></string></textSegment></text>
<text name="L_oras"><geometryInfo x="2" y="0" width="2" height="0.2"/>
<textSegment><string><![CDATA[Oras]]></string></textSegment></text>
<text name="L_sal"><geometryInfo x="4" y="0" width="2" height="0.2"/>
<textSegment><string><![CDATA[Sal]]></string></textSegment></text></frame>
<repeatingFrame name="R_G" source="G"><geometryInfo x="0" y="0.25" width="6" height="0.3"/>
<field name="f_nume" source="nume"><geometryInfo x="0" y="0.25" width="2" height="0.2"/></field>
<field name="f_oras" source="oras"><geometryInfo x="2" y="0.25" width="2" height="0.2"/></field>
<field name="f_sal" source="sal"><geometryInfo x="4" y="0.25" width="2" height="0.2"/></field>
</repeatingFrame></body></section></layout></report>"""


def test_summary_parsed_with_function_and_source():
    rep = parse_oracle_xml(_XML)
    by = {f.name: f for f in rep.formulas}
    assert by["CountnumePerReport"].agg_function == "count"
    assert by["CountnumePerReport"].agg_source == "nume"
    assert by["SumsalPerReport"].agg_function == "sum"


def test_summary_token_resolves_to_real_aggregate():
    rep = parse_oracle_xml(_XML)
    resolve = _build_token_resolver(rep)
    k1, e1, _ = resolve("CountnumePerReport", "Q_1")
    assert k1 == "formula" and e1 == '=Count(Fields!nume.Value, "Q_1")'
    k2, e2, _ = resolve("SumsalPerReport", "Q_1")
    assert e2 == '=Sum(Fields!sal.Value, "Q_1")'
    # "average" maps to the SSRS Avg() function name.
    _k3, e3, _ = resolve("AvgsalPerReport", "Q_1")
    assert e3 == '=Avg(Fields!sal.Value, "Q_1")'


def test_summary_aggregate_scopes_to_owning_dataset():
    # The aggregate must name the dataset that OWNS the source column.
    rep = parse_oracle_xml(_XML)
    resolve = _build_token_resolver(rep)
    _k, expr, _ = resolve("CountnumePerReport", "Q_1")
    assert '"Q_1"' in expr


def test_flat_report_emits_footer_total_row():
    """A flat report with a report-level <summary> must render a static
    FOOTER total row carrying the real aggregate -- so the grand total
    actually prints (wild-corpus: a count/avg footer)."""
    from converter import convert as _convert
    rdl = _convert(_XML)["rdl_xml"]
    # a footer total cell over the summarized column, with the real aggregate
    assert 'Name="Foot_nume"' in rdl
    assert '=Count(Fields!nume.Value, "Q_1")' in rdl
    # XSD valid + renders, no crash
    out = _convert(_XML)
    assert (out.get("preflight") or {}).get("verdict") == "READY"


def test_no_summary_means_no_footer_row():
    import re as _re
    no_summ = _re.sub(rb"<summary[^>]*/>", b"", _XML)
    from converter import convert as _convert
    rdl = _convert(no_summ)["rdl_xml"]
    assert 'Name="Foot_' not in rdl


def test_summary_scope_is_captured_report_vs_group():
    """Oracle <summary compute/reset> scope must be parsed: "report" (grand
    total) vs a group name (subtotal). This routes WHERE the total renders and
    is the foundation for cross-query / group-footer subtotal rendering."""
    xml = (b'<?xml version="1.0"?><report name="S" DTDVersion="9.0.2.0.10"><data>'
           b'<dataSource name="Q_1"><select><![CDATA[SELECT g, v FROM t]]></select>'
           b'<group name="G_DEPT"><dataItem name="g" datatype="vchar2"/>'
           b'<dataItem name="v" datatype="number"/></group>'
           b'<summary name="GrandTot" source="v" function="sum" reset="report" compute="report"/>'
           b'<summary name="DeptSub" source="v" function="sum" reset="G_DEPT" compute="G_DEPT"/>'
           b'</dataSource></data><layout><section name="main"><body width="8" height="9">'
           b'<repeatingFrame name="R" source="G_DEPT"><geometryInfo x="0" y="0" width="6" height="0.3"/>'
           b'<field name="F" source="v"><geometryInfo x="0" y="0" width="2" height="0.2"/></field>'
           b'</repeatingFrame></body></section></layout></report>')
    rep = parse_oracle_xml(xml)
    scopes = {fc.name: fc.agg_scope for fc in (rep.formulas or [])
              if getattr(fc, "agg_function", "")}
    assert scopes.get("GrandTot") == "report"
    assert scopes.get("DeptSub") == "G_DEPT"


def test_report_grand_total_renders_as_dataset_scoped_aggregate():
    """A report-level <summary compute="report"> PLACED in the layout must
    render as a real dataset-scoped SSRS aggregate (=Sum(Fields!src.Value,"Q"))
    below the body, not vanish. Cross-query banking reports place grand totals
    as standalone fields the body builders skip -- this is the safety net."""
    from converter import convert as _convert
    xml = (b'<?xml version="1.0"?><report name="GT" DTDVersion="9.0.2.0.10"><data>'
           b'<dataSource name="Q_SALES"><select><![CDATA[SELECT region, amt FROM s]]></select>'
           b'<group name="G"><dataItem name="region" datatype="vchar2"/>'
           b'<dataItem name="amt" datatype="number"/></group>'
           b'<summary name="CS_grand" source="amt" function="sum" reset="report" compute="report"/>'
           b'</dataSource></data><layout><section name="main"><body width="8" height="9">'
           b'<repeatingFrame name="R" source="G"><geometryInfo x="0" y="0" width="6" height="0.3"/>'
           b'<field name="F_region" source="region"><geometryInfo x="0" y="0" width="2" height="0.2"/></field>'
           b'<field name="F_amt" source="amt"><geometryInfo x="2" y="0" width="2" height="0.2"/></field>'
           b'</repeatingFrame>'
           # the grand total PLACED as a standalone field at the bottom
           b'<field name="F_tot" source="CS_grand"><geometryInfo x="2" y="3" width="2" height="0.2"/></field>'
           b'</body></section></layout></report>')
    rdl = _convert(xml)["rdl_xml"]
    assert 'Sum(Fields!amt.Value, "Q_SALES")' in rdl, "grand total not a scoped Sum"


def test_no_grand_total_block_without_report_summary():
    """Gate: a report with no report-scoped summary emits no grand-total block
    (byte-identical to before)."""
    from converter import convert as _convert
    no_summ = _XML.replace(b'reset="report"', b'reset="G"')  # demote to group scope
    rdl = _convert(no_summ)["rdl_xml"]
    assert "Tb_GrandTotal_" not in rdl


def test_summary_scope_uses_reset_not_compute():
    """Regression (design-panel found): the subtotal scope is Oracle's RESET-at,
    NOT compute-at. Oracle defaults compute="report" on nearly every summary, so
    a compute-first read silently demoted every GROUP subtotal to a grand total
    (then summed report-wide -> wrong values). reset-first is correct; compute
    only governs %-of-total."""
    xml = (b'<?xml version="1.0"?><report name="P" DTDVersion="9.0.2.0.10"><data>'
           b'<dataSource name="Q"><select><![CDATA[SELECT g,v FROM t]]></select>'
           b'<group name="G_DEPT"><dataItem name="g" datatype="vchar2"/>'
           b'<dataItem name="v" datatype="number"/></group>'
           # reset=group but compute=report (the common Oracle default that broke it)
           b'<summary name="DeptSub" source="v" function="sum" reset="G_DEPT" compute="report"/>'
           b'</dataSource></data><layout><section name="main"><body width="8" height="9">'
           b'<repeatingFrame name="R" source="G_DEPT"><geometryInfo x="0" y="0" width="6" height="0.3"/>'
           b'<field name="F" source="v"><geometryInfo x="0" y="0" width="2" height="0.2"/></field>'
           b'</repeatingFrame></body></section></layout></report>')
    rep = parse_oracle_xml(xml)
    sub = next(fc for fc in rep.formulas if fc.name == "DeptSub")
    assert sub.agg_scope == "G_DEPT", f"scope should be reset-at, got {sub.agg_scope!r}"


def test_link_join_columns_are_captured_exactly():
    """Cross-query subtotals need Oracle's EXACT <link> join keys (parentColumn=
    childColumn), not a name-stem guess (real reports name the child key with a
    suffix, e.g. master cod_empresa -> child COD_EMPRESA_FL, which stem-matching
    misses). Capture them, incl. composite (>1 link per child)."""
    xml = (b'<?xml version="1.0"?><report name="MD" DTDVersion="9.0.2.0.10"><data>'
           b'<dataSource name="Q_M"><select><![CDATA[SELECT emp FROM m]]></select>'
           b'<group name="G_M"><dataItem name="emp" datatype="vchar2"/></group></dataSource>'
           b'<dataSource name="Q_D"><select><![CDATA[SELECT emp_d, amt FROM d]]></select>'
           b'<group name="G_D"><dataItem name="emp_d" datatype="vchar2"/>'
           b'<dataItem name="amt" datatype="number"/></group></dataSource>'
           b'<link name="L1" parentGroup="G_M" parentColumn="emp" childQuery="Q_D" '
           b'childColumn="emp_d" condition="eq" sqlClause="where"/>'
           b'</data><layout><section name="main"><body width="8" height="9">'
           b'<repeatingFrame name="R" source="G_M"><geometryInfo x="0" y="0" width="6" height="0.3"/>'
           b'<field name="F" source="emp"><geometryInfo x="0" y="0" width="2" height="0.2"/></field>'
           b'</repeatingFrame></body></section></layout></report>')
    rep = parse_oracle_xml(xml)
    qd = next(q for q in rep.queries if q.name == "Q_D")
    assert ("emp", "emp_d") in qd.link_pairs, qd.link_pairs
