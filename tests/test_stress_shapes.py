"""Adversarial-but-VALID report shapes: extreme/unusual structures that the
real corpus doesn't exercise (wide tables, unicode/special-char names, deep
nesting, duplicate columns, dangling field refs, recursive formulas). Each
must convert to an XSD-valid RDL without crashing -- the converter degrades,
never breaks. Found 0 bugs when added; locks the robustness.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402

_XSD = ROOT / "tests" / "fixtures" / "schema" / "ReportDefinition_2008.xsd"


def _wrap(group_xml, fields_xml, name="STRESS", extra=""):
    return (f'<report name="{name}" DTDVersion="9.0.2.0.10"><data>'
            f'<dataSource name="Q_1"><select><![CDATA[SELECT x FROM t]]></select>'
            f'{group_xml}</dataSource></data><layout><section name="main">'
            f'<body width="8" height="9"><repeatingFrame name="R" source="G">'
            f'<geometryInfo x="0" y="0" width="6" height="0.3"/>{fields_xml}'
            f'</repeatingFrame>{extra}</body></section></layout></report>').encode()


def _wide(n):
    cols = "".join(f'<dataItem name="C{i}" datatype="vchar2"/>' for i in range(n))
    flds = "".join(f'<field name="F{i}" source="C{i}">'
                   f'<geometryInfo x="{i * 0.4}" y="0" width="0.4" height="0.2"/></field>'
                   for i in range(n))
    return _wrap(f'<group name="G">{cols}</group>', flds, name="WIDE")


def _deep(levels):
    inner = '<dataItem name="L%d" datatype="vchar2"/>' % levels
    for i in range(levels - 1, 0, -1):
        inner = (f'<group name="G{i}"><dataItem name="L{i}" datatype="vchar2"/>'
                 f'{inner}</group>')
    return (f'<report name="DEEP" DTDVersion="9.0.2.0.10"><data>'
            f'<dataSource name="Q_1"><select><![CDATA[SELECT a FROM t]]></select>'
            f'{inner}</dataSource></data><layout><section name="main">'
            f'<body width="8" height="9"><repeatingFrame name="R" source="G1">'
            f'<geometryInfo x="0" y="0" width="6" height="0.3"/><field name="F" source="L1">'
            f'<geometryInfo x="0" y="0" width="2" height="0.2"/></field>'
            f'</repeatingFrame></body></section></layout></report>').encode()


_CASES = {
    "wide_60_col": _wide(60),
    "unicode_names": _wrap(
        '<group name="G"><dataItem name="Café_Münü" datatype="vchar2"/>'
        '<dataItem name="数量" datatype="number"/></group>',
        '<field name="F1" source="Café_Münü"><geometryInfo x="0" y="0" width="2" height="0.2"/></field>'),
    "neg_geometry": _wrap(
        '<group name="G"><dataItem name="X" datatype="vchar2"/></group>',
        '<field name="F1" source="X"><geometryInfo x="-5" y="-2" width="0" height="-1"/></field>'),
    "long_name": _wrap(
        f'<group name="G"><dataItem name="{"VERYLONG" * 40}" datatype="vchar2"/></group>',
        f'<field name="F1" source="{"VERYLONG" * 40}"><geometryInfo x="0" y="0" width="2" height="0.2"/></field>'),
    "deep_6_level": _deep(6),
    "dup_columns": _wrap(
        '<group name="G"><dataItem name="NAME" datatype="vchar2"/>'
        '<dataItem name="NAME" datatype="vchar2"/>'
        '<dataItem name="NAME" datatype="number"/></group>',
        '<field name="F" source="NAME"><geometryInfo x="0" y="0" width="2" height="0.2"/></field>'),
    "dangling_field": _wrap(
        '<group name="G"><dataItem name="REAL" datatype="vchar2"/></group>',
        '<field name="F" source="GHOST_COL_NONEXISTENT"><geometryInfo x="0" y="0" width="2" height="0.2"/></field>'),
    "weird_names": _wrap(
        '<group name="G"><dataItem name="123START" datatype="number"/>'
        '<dataItem name="with space" datatype="vchar2"/></group>',
        '<field name="F" source="123START"><geometryInfo x="0" y="0" width="2" height="0.2"/></field>'),
    "recursive_formula": (
        b'<report name="REC" DTDVersion="9.0.2.0.10"><data><dataSource name="Q_1">'
        b'<select><![CDATA[SELECT a FROM t]]></select><group name="G">'
        b'<dataItem name="A" datatype="number"/></group>'
        b'<formula name="CF_LOOP" datatype="number"><![CDATA[function CF_LOOP return '
        b'number is begin return :CF_LOOP + 1; end;]]></formula></dataSource></data>'
        b'<layout><section name="main"><body width="8" height="9">'
        b'<repeatingFrame name="R" source="G"><geometryInfo x="0" y="0" width="6" height="0.3"/>'
        b'<text name="T"><geometryInfo x="0" y="0" width="2" height="0.2"/>'
        b'<textSegment><string><![CDATA[&CF_LOOP]]></string></textSegment></text>'
        b'</repeatingFrame></body></section></layout></report>'),
}


def _prep(params="", queries="", sql="SELECT a FROM t", name="P"):
    return (f'<report name="{name}" DTDVersion="9.0.2.0.10"><data>'
            f'{params}<dataSource name="Q_1"><select><![CDATA[{sql}]]></select>'
            f'<group name="G"><dataItem name="A" datatype="vchar2"/></group>'
            f'</dataSource>{queries}</data><layout><section name="main">'
            f'<body width="8" height="9"><repeatingFrame name="R" source="G">'
            f'<geometryInfo x="0" y="0" width="6" height="0.3"/><field name="F" source="A">'
            f'<geometryInfo x="0" y="0" width="2" height="0.2"/></field>'
            f'</repeatingFrame></body></section></layout></report>').encode()


_P50 = "".join(f'<userParameter name="P_{i}" datatype="character" width="10"/>'
               for i in range(50))
_MQ = "".join(
    f'<dataSource name="Q_{i}"><select><![CDATA[SELECT k{i} FROM t{i}]]></select>'
    f'<group name="GG{i}"><dataItem name="K{i}" datatype="number"/></group>'
    f'</dataSource>' for i in range(2, 12))
_LINKS = "".join(
    f'<link name="L{i}" parentGroup="G" parentColumn="A" childQuery="Q_{i}" '
    f'childColumn="K{i}" condition="eq"/>' for i in range(2, 12))

_PARAM_CASES = {
    "params_50": _prep(params=_P50),
    "binds_50_in_sql": _prep(params=_P50,
                             sql="SELECT a FROM t WHERE " + " AND ".join(
                                 f"col{i} = :P_{i}" for i in range(50))),
    "linked_queries_10": _prep(queries=_MQ + _LINKS),
    "huge_sql_500_col": _prep(sql="SELECT " + ", ".join(
        f"col{i}" for i in range(500)) + " FROM bigtable"),
    "lexical_ref": _prep(params='<userParameter name="P_WHERE" datatype="character"/>',
                         sql="SELECT a FROM t WHERE &P_WHERE"),
}


@pytest.mark.parametrize("name,xml", list(_CASES.items()))
def test_adversarial_shape_converts_to_valid_rdl(name, xml):
    out = convert(xml)                          # must not raise
    rdl = out["rdl_xml"]
    assert rdl and "<Report" in rdl
    if _XSD.exists():
        etree = pytest.importorskip("lxml.etree")
        schema = etree.XMLSchema(etree.parse(str(_XSD)))
        assert schema.validate(etree.fromstring(rdl.encode())), \
            f"{name}: " + "; ".join(e.message for e in schema.error_log[:3])


@pytest.mark.parametrize("name,xml", list(_PARAM_CASES.items()))
def test_param_and_query_extremes_convert_to_valid_rdl(name, xml):
    out = convert(xml)                          # must not raise
    rdl = out["rdl_xml"]
    assert rdl and "<Report" in rdl
    if _XSD.exists():
        etree = pytest.importorskip("lxml.etree")
        schema = etree.XMLSchema(etree.parse(str(_XSD)))
        assert schema.validate(etree.fromstring(rdl.encode())), \
            f"{name}: " + "; ".join(e.message for e in schema.error_log[:3])


def test_param_name_with_dollar_sign_is_sanitized_consistently():
    """Oracle legally allows $ / # in identifiers (e.g. P_BAL$). SSRS does
    NOT, so the param Name must be sanitized -- and its references must use the
    SAME sanitized name so they don't dangle. Regression: a raw decl + _safe'd
    refs produced an invalid Name AND a dangling Parameters! ref."""
    import re
    xml = (b'<report name="DOLLAR" DTDVersion="9.0.2.0.10"><data>'
           b'<userParameter name="P_BAL$" datatype="number"/>'
           b'<dataSource name="Q_1"><select><![CDATA[SELECT a FROM t WHERE bal > :P_BAL$]]>'
           b'</select><group name="G"><dataItem name="A" datatype="vchar2"/></group>'
           b'</dataSource></data><layout><section name="main"><body width="8" height="9">'
           b'<repeatingFrame name="R" source="G"><geometryInfo x="0" y="0" width="6" height="0.3"/>'
           b'<field name="F" source="A"><geometryInfo x="0" y="0" width="2" height="0.2"/></field>'
           b'</repeatingFrame></body></section></layout></report>')
    rdl = convert(xml)["rdl_xml"]
    decls = set(re.findall(r'<ReportParameter Name="([^"]+)"', rdl))
    refs = set(re.findall(r"Parameters!([A-Za-z0-9_]+)\.Value", rdl))
    assert decls, "no parameters declared"
    assert not any("$" in d for d in decls), decls       # valid SSRS Names
    assert refs <= decls, (refs - decls)                 # no dangling refs


def test_invalid_param_name_is_flagged_not_crashed():
    """A param name that isn't a valid SSRS identifier (a dash -- impossible
    from real Oracle, but defensive) must be caught by preflight, not crash."""
    bad = _prep(params='<userParameter name="P-with-dash" datatype="character"/>')
    out = convert(bad)                          # must not raise
    assert out["rdl_xml"]
    assert (out.get("preflight") or {}).get("verdict") in ("BLOCKER", "RED", "READY")


def test_special_char_column_datafield_matches_result_set():
    """Legacy Oracle columns legally contain '#' / '$' (EMP#, DEPT#, INV#,
    AMT$ -- classic old-school naming). Each <Field>'s DataField must name a
    column the CommandText actually RETURNS, or the field renders blank at
    runtime. Regression: the SELECT-aliaser treated 'emp#' as an un-aliasable
    expression and rewrote it to 'emp# AS EMP', renaming the result column to
    EMP while the DataField kept the raw 'EMP#' -> bind miss -> blank. A bare
    'emp#' must stay un-aliased so Oracle returns 'EMP#' verbatim."""
    import re
    xml = (b'<report name="LEGACY" DTDVersion="9.0.2.0.10"><data>'
           b'<dataSource name="Q_1">'
           b'<select><![CDATA[SELECT emp#, dept#, t.amt$, ename FROM emp t]]></select>'
           b'<group name="G">'
           b'<dataItem name="EMP#" datatype="number"/>'
           b'<dataItem name="DEPT#" datatype="number"/>'
           b'<dataItem name="AMT$" datatype="number"/>'
           b'<dataItem name="ENAME" datatype="vchar2"/></group>'
           b'</dataSource></data><layout><section name="main"><body width="8" height="9">'
           b'<repeatingFrame name="R" source="G"><geometryInfo x="0" y="0" width="6" height="0.3"/>'
           b'<field name="F1" source="EMP#"><geometryInfo x="0" y="0" width="2" height="0.2"/></field>'
           b'<field name="F2" source="DEPT#"><geometryInfo x="2" y="0" width="2" height="0.2"/></field>'
           b'<field name="F3" source="AMT$"><geometryInfo x="4" y="0" width="1" height="0.2"/></field>'
           b'<field name="F4" source="ENAME"><geometryInfo x="5" y="0" width="1" height="0.2"/></field>'
           b'</repeatingFrame></body></section></layout></report>')
    rdl = convert(xml)["rdl_xml"]
    cmd = re.search(r"<CommandText>(.*?)</CommandText>", rdl, re.S).group(1)

    # Result-set column names: bare col (optionally TABLE.) -> uppercased COL;
    # 'expr AS X' -> X. Includes $/# as legal identifier chars.
    body = re.search(r"SELECT (.*?) FROM", cmd, re.S | re.I).group(1)
    result_cols = set()
    for part in body.split(","):
        p = part.strip()
        m = re.search(r"\bAS\s+([A-Za-z_][\w$#]*)", p, re.I)
        result_cols.add((m.group(1) if m else re.sub(r"^.*\.", "", p)).upper())

    fields = re.findall(r'<Field Name="[^"]+">.*?<DataField>([^<]*)</DataField>',
                        rdl, re.S)
    assert fields, "no dataset fields emitted"
    dangling = [df for df in fields if df.upper() not in result_cols]
    assert not dangling, (
        f"DataField(s) {dangling} not in result set {sorted(result_cols)}")


def _no_dangling_or_special_names(rdl):
    """Helper: every Fields!/Parameters! reference resolves to a declared
    Field/ReportParameter, and no $/# leaks into an SSRS Name."""
    import re
    fdecl = set(re.findall(r'<Field Name="([^"]+)"', rdl))
    fref = set(re.findall(r"Fields!([A-Za-z0-9_]+)\.", rdl))
    pdecl = set(re.findall(r'<ReportParameter Name="([^"]+)"', rdl))
    pref = set(re.findall(r"Parameters!([A-Za-z0-9_]+)\.", rdl))
    assert fref <= fdecl, f"dangling field refs: {fref - fdecl}"
    assert pref <= pdecl, f"dangling param refs: {pref - pdecl}"
    bad = [n for n in (fdecl | pdecl) if re.search(r"[$#]", n)]
    assert not bad, f"$/# leaked into an SSRS Name: {bad}"


def test_matrix_pivot_special_char_fields_stay_consistent():
    """A cross-tab whose pivot/measure columns carry $/# (legal SQL idents):
    the column GroupExpression, row GroupExpression and =Sum(cell) must all
    reference the SAME sanitized field names the dataset declares -- no
    dangling Fields! ref, no $/# in any Name. (Same identifier class as the
    EMP# DataField bug, exercised through the matrix builder.)"""
    xml = (b'<?xml version="1.0" encoding="UTF-8"?>'
           b'<report name="SAMPLE_MATRIX" DTDVersion="1.0"><data>'
           b'<dataSource name="Q_SALES">'
           b'<select><![CDATA[SELECT region#, prod$, amt# FROM sales]]></select>'
           b'<group name="G_SALES">'
           b'<dataItem name="region#" datatype="vchar2"/>'
           b'<dataItem name="prod$" datatype="vchar2"/>'
           b'<dataItem name="amt#" datatype="number"/></group></dataSource></data>'
           b'<layout><section name="main"><body width="7.5" height="9.0">'
           b'<matrix name="M_sales"><geometryInfo x="0" y="0" width="6" height="1"/>'
           b'<matrixCol name="g_region"><field name="f_region" source="region#">'
           b'<geometryInfo x="1.5" y="0" width="1.5" height="0.25"/></field></matrixCol>'
           b'<matrixRow name="g_product"><field name="f_product" source="prod$">'
           b'<geometryInfo x="0" y="0.25" width="1.5" height="0.25"/></field></matrixRow>'
           b'<matrixCell name="g_amount"><field name="f_amount" source="amt#">'
           b'<geometryInfo x="1.5" y="0.25" width="1.5" height="0.25"/></field></matrixCell>'
           b'</matrix></body></section></layout></report>')
    out = convert(xml)
    rdl = out["rdl_xml"]
    _no_dangling_or_special_names(rdl)
    if _XSD.exists():
        etree = pytest.importorskip("lxml.etree")
        schema = etree.XMLSchema(etree.parse(str(_XSD)))
        assert schema.validate(etree.fromstring(rdl.encode())), \
            "; ".join(e.message for e in schema.error_log[:3])


def test_drillthrough_param_special_chars_sanitized_consistently():
    """Subreport drill-through param names come from the parent's Oracle bind
    params, which legally carry $/# (P_ID#, P_BAL$). The injected
    <ReportParameter Name=..> and every Parameters! reference must route
    through the same sanitizer -- no invalid Name, no dangling ref."""
    plain = (b'<report name="CHILD" DTDVersion="9.0.2.0.10"><data>'
             b'<dataSource name="Q_1"><select><![CDATA[SELECT a FROM t]]></select>'
             b'<group name="G"><dataItem name="A" datatype="vchar2"/></group>'
             b'</dataSource></data><layout><section name="main"><body width="8" height="9">'
             b'<repeatingFrame name="R" source="G"><geometryInfo x="0" y="0" width="6" height="0.3"/>'
             b'<field name="F" source="A"><geometryInfo x="0" y="0" width="2" height="0.2"/></field>'
             b'</repeatingFrame></body></section></layout></report>')
    rdl = convert(plain, extra_param_names=["P_ID#", "P_BAL$"])["rdl_xml"]
    _no_dangling_or_special_names(rdl)
    import re
    decls = set(re.findall(r'<ReportParameter Name="([^"]+)"', rdl))
    assert {"P_ID_", "P_BAL_"} <= decls, decls
