"""Grouped-subtotal band: DECLARED rules only, and group-SCOPE value fidelity.

Three defects this guards, all measured against a truth PDF of a grouped
break report:

1. The band-edge rules were re-emitted as the enclosing band's BORDER. A
   border spans the whole band whatever the declaration says, and -- the
   part that actually printed ink the source never draws -- it was emitted
   with a synthesized colour even when NO line is declared there at all.
   The truth page has ZERO drawings across that band.

2. A group-SCOPE lexical (``&MASTER_COL`` in a boilerplate text inside a
   detail band) resolved to ``=Nothing`` and printed blank, while its
   REPORT-scope twin over the same column resolved correctly.

3. A total placed over a column the source declares ``datatype="character"``
   emitted ``=Sum(Val(Fields!X.Value))`` -- a numeric guess about text.
"""
import re

import pytest

from converter import convert
from converter.generators import rdl as R


# --------------------------------------------------------------------------
# A synthetic 2-level break report: master query + linked detail query, a
# group-header rule, a >=3-label column strip, a detail row, and a group
# footer carrying a character-declared range formula, a numeric subtotal, a
# group-scope lexical and a SHORT declared underline. No customer data.
# --------------------------------------------------------------------------
_CHAR_RANGE_BODY = (
    "FUNCTION CF_Range_F RETURN VARCHAR2 IS BEGIN "
    "IF :CS_PER_MIN = :CS_PER_MAX THEN RETURN('P' || :CS_PER_MIN); "
    "ELSE RETURN('P' || :CS_PER_MIN || ' - P' || :CS_PER_MAX); "
    "END IF; END;"
)


_OPAQUE_RANGE_BODY = (
    "FUNCTION CF_Range_F RETURN VARCHAR2 IS BEGIN "
    "RETURN(Pkg_External.F_Label(piKey => :D_SITE_KEY)); END;"
)


def _break_report(group_rule=True, sub_rule=True, band_rule_y=None,
                  range_body=_CHAR_RANGE_BODY):
    """Build the source. ``band_rule_y`` adds an EXTRA declared <line> at
    that y (used to prove a rule appears only where one is declared)."""
    extra = ""
    if band_rule_y is not None:
        extra = (f'<line name="B_BAND"><geometryInfo x="0.10" '
                 f'y="{band_rule_y}" width="7.20" height="0.0"/>'
                 f'<visualSettings linePattern="solid"/></line>')
    grule = (
        '<line name="B_GRPTOP"><geometryInfo x="0.02" y="0.01" width="7.40" '
        'height="0.0"/><visualSettings lineWidth="1" linePattern="solid"/>'
        '</line>') if group_rule else ""
    srule = (
        '<line name="B_SUBRULE"><geometryInfo x="6.55" y="1.18" width="0.90" '
        'height="0.0"/><visualSettings linePattern="solid"/></line>'
    ) if sub_rule else ""
    return (
        '<?xml version="1.0"?><report name="BRK_T" DTDVersion="9.0.2.0.10">'
        '<data>'
        '<dataSource name="Q_M">'
        '<select><![CDATA[select site_key, grp_label, grp_total from m]]>'
        '</select>'
        '<group name="G_M">'
        '<dataItem name="SITE_KEY" datatype="number"/>'
        '<dataItem name="GRP_LABEL" datatype="vchar2"/>'
        '<dataItem name="GRP_TOTAL" datatype="number"/>'
        '</group></dataSource>'
        '<dataSource name="Q_D">'
        '<select><![CDATA[select d_site_key, period_no, kind_txt, qty, amt '
        'from d where site_key = :SITE_KEY]]></select>'
        '<group name="G_DS">'
        '<dataItem name="D_SITE_KEY" datatype="number"/>'
        '<summary name="CS_PER_MIN" source="PERIOD_NO" function="minimum" '
        'reset="G_DS" compute="report"/>'
        '<summary name="CS_PER_MAX" source="PERIOD_NO" function="maximum" '
        'reset="G_DS" compute="report"/>'
        '<summary name="CS_QTY_TOTAL" source="QTY" function="sum" '
        'reset="G_DS" compute="report"/>'
        '</group>'
        '<group name="G_D">'
        '<dataItem name="PERIOD_NO" datatype="number"/>'
        '<dataItem name="KIND_TXT" datatype="vchar2"/>'
        '<dataItem name="QTY" datatype="number"/>'
        '<dataItem name="AMT" datatype="number"/>'
        '</group></dataSource>'
        '<link parentGroup="G_M" childQuery="Q_D" condition="eq" '
        'sqlClause="where"/>'
        '<formula name="CF_RANGE" source="cf_range_f" datatype="character" '
        'width="20"/>'
        '</data>'
        '<programUnits><function name="cf_range_f"><textSource><![CDATA['
        + range_body + ']]></textSource></function></programUnits>'
        '<layout><section name="main" width="8.50000" height="11.00000">'
        '<body>'
        '<frame name="M_BODY"><geometryInfo x="0" y="0" width="7.5" '
        'height="2.0"/>'
        '<repeatingFrame name="R_OUTER" source="G_M" printDirection="down">'
        '<geometryInfo x="0" y="0" width="7.5" height="1.9"/>'
        + grule +
        '<field name="F_GRP" source="GRP_LABEL">'
        '<geometryInfo x="0.05" y="0.06" width="4.0" height="0.19"/></field>'
        '<field name="F_KEY" source="SITE_KEY">'
        '<geometryInfo x="6.30" y="0.06" width="1.0" height="0.19"/></field>'
        + extra +
        '<text name="B_H1"><geometryInfo x="0.65" y="0.31" width="0.5" '
        'height="0.19"/><textSegment><font face="Arial" size="10"/>'
        '<string><![CDATA[Per]]></string></textSegment></text>'
        '<text name="B_H2"><geometryInfo x="1.26" y="0.31" width="1.4" '
        'height="0.19"/><textSegment><font face="Arial" size="10"/>'
        '<string><![CDATA[Kind]]></string></textSegment></text>'
        '<text name="B_H3"><geometryInfo x="3.00" y="0.31" width="0.8" '
        'height="0.19"/><textSegment><font face="Arial" size="10"/>'
        '<string><![CDATA[Qty]]></string></textSegment></text>'
        '<text name="B_H4"><geometryInfo x="6.60" y="0.31" width="0.9" '
        'height="0.19"/><textSegment><font face="Arial" size="10"/>'
        '<string><![CDATA[Amt]]></string></textSegment></text>'
        '<repeatingFrame name="R_DET" source="G_D" printDirection="down">'
        '<geometryInfo x="0.65" y="0.55" width="6.85" height="0.19"/>'
        '<field name="F_PER" source="PERIOD_NO">'
        '<geometryInfo x="0.65" y="0.55" width="0.5" height="0.19"/></field>'
        '<field name="F_KIND" source="KIND_TXT">'
        '<geometryInfo x="1.26" y="0.55" width="1.4" height="0.19"/></field>'
        '<field name="F_QTY" source="QTY">'
        '<geometryInfo x="3.00" y="0.55" width="0.8" height="0.19"/></field>'
        '<field name="F_AMT" source="AMT">'
        '<geometryInfo x="6.60" y="0.55" width="0.9" height="0.19"/></field>'
        '</repeatingFrame>'
        '<frame name="M_FTR"><geometryInfo x="0.03" y="1.18" width="7.46" '
        'height="0.60"/>'
        + srule +
        '<field name="F_RANGE" source="CF_RANGE">'
        '<geometryInfo x="5.25" y="1.22" width="1.24" height="0.19"/></field>'
        '<field name="F_TOT" source="CS_QTY_TOTAL">'
        '<geometryInfo x="6.60" y="1.22" width="0.90" height="0.19"/></field>'
        '<text name="B_LEX"><geometryInfo x="4.60" y="1.45" width="1.87" '
        'height="0.16"/><textSegment><font face="Arial" size="10"/>'
        '<string><![CDATA[&GRP_LABEL]]></string></textSegment></text>'
        '</frame>'
        '</repeatingFrame></frame>'
        '</body></section></layout></report>'
    )


def _lines(rdl):
    """{name: body} for every <Line> in the RDL."""
    return {m.group(1): m.group(0)
            for m in re.finditer(r'<Line Name="([^"]+)">.*?</Line>',
                                 rdl, re.S)}


def _geo_in(body, tag):
    """One geometry element of a report item, in inches.

    Read as a NUMBER, never as a formatted string: a declared quantity is
    emitted at full precision, and matching '<Left>0.02in</Left>' as text
    would pass for any value that merely rounds to 0.02.
    """
    m = re.search(rf"<{tag}>([\d.]+)in</{tag}>", body)
    assert m, (tag, body)
    return float(m.group(1))


def _routes():
    from converter.parsers.oracle_xml import parse_oracle_xml
    rep = parse_oracle_xml(_break_report().encode())
    return R._is_grouped_tabular_subtotal(rep)


def test_fixture_routes_through_the_grouped_subtotal_emitter():
    assert _routes(), (
        "the guards below only mean anything on the grouped-subtotal route")


def test_no_rule_is_painted_where_none_is_declared():
    """The source declares a rule at the group's TOP and a short underline
    in the footer -- and nothing between the column strip and the detail
    rows. The band there must carry no border and no rule: the truth page
    has zero drawings across it."""
    rdl = convert(_break_report().encode())["rdl_xml"]
    hdr = re.search(r'<Rectangle Name="GTS_Hdr">.*?<Rectangle '
                    r'Name="GTS_ColBand">', rdl, re.S)
    assert hdr, "grouped-subtotal route must emit the header band"
    assert "<BottomBorder>" not in hdr.group(0), (
        "no declared line under the band -> no synthesized band border")
    assert "<TopBorder>" not in rdl.split('Name="GTS_ColBand"')[1][:600], (
        "no declared line capping the column strip -> no synthesized border")
    # every rule in the body carries a DECLARED name
    for nm in _lines(rdl):
        assert nm.startswith(("Rule_", "GTS_RowRule", "MChrome_")), (
            f"rule {nm!r} traces to no declaration")


def test_short_subtotal_underline_keeps_its_declared_extent():
    """The footer underline is 0.90in wide over an ~7in detail band. A
    'wide enough to be a band edge' width gate dropped it from
    classification entirely; it must print, at its declared x and width."""
    rdl = convert(_break_report().encode())["rdl_xml"]
    ln = _lines(rdl).get("Rule_B_SUBRULE")
    assert ln, "the declared subtotal underline must print"
    assert abs(_geo_in(ln, "Left") - 6.55) < 1e-9, (
        "declared x, not the band's left edge", ln)
    assert abs(_geo_in(ln, "Width") - 0.90) < 1e-9, (
        "declared width, not the band's width", ln)
    # prove-the-gate: drop the declaration -> the rule disappears
    rdl2 = convert(_break_report(sub_rule=False).encode())["rdl_xml"]
    assert "Rule_B_SUBRULE" not in rdl2


def test_declared_group_rule_prints_at_its_declared_stroke():
    """The group's own heavy rule declares lineWidth=1 -> 1pt, at its
    declared endpoints; without the declaration nothing prints."""
    rdl = convert(_break_report().encode())["rdl_xml"]
    ln = _lines(rdl).get("Rule_B_GRPTOP")
    assert ln, "the declared group rule must print"
    assert "<Width>1pt</Width>" in ln, "DECLARED lineWidth maps 1:1 to points"
    assert abs(_geo_in(ln, "Left") - 0.02) < 1e-9, ln
    rdl2 = convert(_break_report(group_rule=False).encode())["rdl_xml"]
    assert "Rule_B_GRPTOP" not in rdl2, "no declaration -> no rule"


@pytest.mark.parametrize("band_y", ["0.44", "0.50"])
def test_a_declared_band_rule_does_print(band_y):
    """The mirror of the 'nothing declared -> nothing painted' guard: when
    a line IS declared in that band it must appear, so the fix cannot be a
    blanket suppression."""
    rdl = convert(_break_report(band_rule_y=band_y).encode())["rdl_xml"]
    ln = _lines(rdl).get("Rule_B_BAND")
    assert ln, "a DECLARED band rule must print"
    assert abs(_geo_in(ln, "Left") - 0.10) < 1e-9, ln


def test_group_scope_lexical_resolves_like_its_report_scope_twin():
    """``&MASTER_COL`` inside the group band reads the master row that owns
    the current detail row -- the same value its report-scope summary twin
    resolves to. It used to emit =Nothing and print blank."""
    rdl = convert(_break_report().encode())["rdl_xml"]
    # the lexical's textbox is the footer member at the declared x
    m = [b for b in re.findall(r'<Textbox Name="Tb_F_[0-9_]+">.*?</Textbox>',
                               rdl, re.S)
         if "<Left>4.60in</Left>" in b]
    assert m, "the group-band boilerplate text must be emitted"
    val = re.search(r"<Value>(.*?)</Value>", m[0], re.S).group(1)
    assert val != "=Nothing", (
        "a group-scope lexical must not blank out at run time")
    assert "Lookup(" in val and '"Q_M"' in val and "GRP_LABEL" in val, (
        "it must read the MASTER row correlated to the current detail row")


def test_declared_character_column_is_never_summed():
    """The footer's range field is declared datatype='character' -- its
    values are formatted text. It must reconstruct from the summaries it
    declares, never become a numeric guess over some other column."""
    rdl = convert(_break_report().encode())["rdl_xml"]
    m = [b for b in re.findall(r'<Textbox Name="Tb_F_[0-9_]+">.*?</Textbox>',
                               rdl, re.S)
         if "<Left>5.25in</Left>" in b]
    assert m, "the character-declared footer field must be emitted"
    val = re.search(r"<Value>(.*?)</Value>", m[0], re.S).group(1)
    assert "Sum(Val(" not in val, (
        "a declared-CHARACTER column must never be wrapped in Sum(Val())")
    assert "Min(Fields!PERIOD_NO.Value)" in val \
        and "Max(Fields!PERIOD_NO.Value)" in val, (
        "it must reconstruct from the group summaries it declares, at "
        "GROUP scope (unscoped aggregates = the innermost group)")
    # the sibling NUMERIC subtotal in the same band still aggregates
    n = [b for b in re.findall(r'<Textbox Name="Tb_F_[0-9_]+">.*?</Textbox>',
                               rdl, re.S)
         if "<Left>6.60in</Left>" in b]
    assert n and "Sum(Fields!QTY.Value)" in re.search(
        r"<Value>(.*?)</Value>", n[0], re.S).group(1), (
        "the declared numeric subtotal must still compute")


def test_uncompilable_character_column_stays_blank_not_summed():
    """When the character column's formula CANNOT be reconstructed (an
    external package call), the last-resort numeric guess must still be
    refused: blank is honest, an invented total over an unrelated numeric
    column is not."""
    rdl = convert(
        _break_report(range_body=_OPAQUE_RANGE_BODY).encode())["rdl_xml"]
    m = [b for b in re.findall(r'<Textbox Name="Tb_F_[0-9_]+">.*?</Textbox>',
                               rdl, re.S)
         if "<Left>5.25in</Left>" in b]
    assert m, "the character-declared footer field must still be emitted"
    val = re.search(r"<Value>(.*?)</Value>", m[0], re.S).group(1)
    assert "Sum(" not in val and "Val(" not in val, (
        "an unreconstructable CHARACTER column must never be aggregated")


def test_character_datatype_detection_is_declaration_driven():
    """The gate reads the SOURCE's own datatype declaration -- formula
    return type or dataItem datatype -- not a name heuristic."""
    from converter.parsers.oracle_xml import parse_oracle_xml
    rep = parse_oracle_xml(_break_report().encode())
    assert R._declares_character(rep, "CF_RANGE")
    assert R._declares_character(rep, "KIND_TXT")
    assert not R._declares_character(rep, "QTY")
    assert not R._declares_character(rep, "PERIOD_NO")
    assert not R._declares_character(rep, "NO_SUCH_COLUMN")
