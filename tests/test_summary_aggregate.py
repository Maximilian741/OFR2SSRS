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
