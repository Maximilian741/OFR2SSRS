"""
Declared body ``<line>`` objects must print as REAL rules at their own
declared endpoints.

Oracle draws a report's rules as ``<line>`` objects carrying their own
x / y / width. We used to drop the body ones and fake a rule with a
Rectangle border on whatever band happened to enclose them -- a border
spans the BAND, so every such rule started and ended in the wrong place
(measured on two production reports: one declares six body rules and
rendered a single one, the other declares three and rendered none; the
rules a viewer did see ran edge-to-edge instead of honouring the inset
the source declares).

The contract, in both directions:

  * a declared body line -> one ``<Line>`` at its declared x / width, its
    y translated into the containing region's coordinate space, in the
    declared ink and weight (a declared lineWidth maps 1:1 to points, an
    undeclared width is Oracle's device hairline);
  * no declared line (or one whose linePattern does not draw) -> no rule
    is invented in its place.
"""
import re

from converter import convert


def _ledger_xml(group_rule=True, footer_rule=True, end_rule=True,
                pattern='linePattern="solid"'):
    """A break report with subtotals: an outer group frame, a column-header
    band, a detail row, a per-group footer and a report-end total block.
    Each rule is declared with its OWN endpoints, deliberately narrower
    than (and inset from) the band that encloses it."""
    g = (f'<line name="B_GRP_RULE"><geometryInfo x="0.20000" y="0.01000" '
         f'width="6.00000" height="0.00000"/>'
         f'<visualSettings lineWidth="2" {pattern}/></line>') \
        if group_rule else ""
    f = (f'<line name="B_FTR_RULE"><geometryInfo x="5.00000" y="1.28000" '
         f'width="1.20000" height="0.00000"/>'
         f'<visualSettings {pattern}/></line>') if footer_rule else ""
    e = (f'<line name="B_END_RULE"><geometryInfo x="0.40000" y="2.10000" '
         f'width="5.50000" height="0.00000"/>'
         f'<visualSettings lineWidth="3" {pattern}/></line>') \
        if end_rule else ""
    return (
        '<?xml version="1.0"?><report name="BRK_T" DTDVersion="9.0.2.0.10">'
        '<data><dataSource name="Q_Main">'
        '<select><![CDATA[select brk_key, item_no, party, amt from t]]>'
        '</select>'
        '<group name="G_Main"><dataItem name="BRK_KEY" datatype="number"/>'
        '</group>'
        '<group name="G_Det"><dataItem name="ITEM_NO" datatype="number"/>'
        '<dataItem name="PARTY" datatype="vchar2"/>'
        '<dataItem name="AMT" datatype="number"/></group>'
        '<summary name="CS_GRP_TOTAL" function="sum" source="AMT"/>'
        '<summary name="CS_ALL_TOTAL" function="sum" source="AMT" '
        'reset="report"/>'
        '</dataSource></data>'
        '<layout><section name="main" width="8.50000" height="11.00000">'
        '<body>'
        '<frame name="M_G_GRP"><geometryInfo x="0" y="0" width="8.2" '
        'height="2.6"/>'
        '<repeatingFrame name="R_OUTER" source="G_Main" '
        'printDirection="down">'
        '<geometryInfo x="0" y="0" width="8.0" height="1.9"/>'
        + g +
        '<field name="F_KEY" source="BRK_KEY">'
        '<geometryInfo x="0.3" y="0.12" width="1.2" height="0.2"/></field>'
        '<text name="B_C1"><geometryInfo x="0.1" y="0.6" width="0.8" '
        'height="0.2"/><textSegment><font face="Arial" size="9" bold="yes"/>'
        '<string><![CDATA[Item]]></string></textSegment></text>'
        '<text name="B_C2"><geometryInfo x="1.2" y="0.6" width="1.2" '
        'height="0.2"/><textSegment><font face="Arial" size="9" bold="yes"/>'
        '<string><![CDATA[Party]]></string></textSegment></text>'
        '<text name="B_C3"><geometryInfo x="3.0" y="0.6" width="1.2" '
        'height="0.2"/><textSegment><font face="Arial" size="9" bold="yes"/>'
        '<string><![CDATA[Amount]]></string></textSegment></text>'
        '<repeatingFrame name="R_DET" source="G_Det" printDirection="down">'
        '<geometryInfo x="0.05" y="0.9" width="8.0" height="0.375"/>'
        '<field name="F_D1" source="ITEM_NO">'
        '<geometryInfo x="0.1" y="0.95" width="0.8" height="0.2"/></field>'
        '<field name="F_D2" source="PARTY">'
        '<geometryInfo x="1.2" y="0.95" width="1.6" height="0.2"/></field>'
        '<field name="F_D3" source="AMT">'
        '<geometryInfo x="3.0" y="0.95" width="1.0" height="0.2"/></field>'
        '</repeatingFrame>'
        + f +
        '<frame name="M_TOTALS">'
        '<geometryInfo x="1.5" y="1.5" width="3.0" height="0.4"/>'
        '<field name="F_TOT" source="CS_GRP_TOTAL">'
        '<geometryInfo x="3.0" y="1.55" width="1.0" height="0.2"/></field>'
        '<text name="B_TOTL"><geometryInfo x="1.8" y="1.55" width="1.1" '
        'height="0.2"/><textSegment><font face="Arial" size="9" bold="yes"/>'
        '<string><![CDATA[Total]]></string></textSegment></text>'
        '</frame>'
        '</repeatingFrame>'
        + e +
        '<frame name="M_REPORT_TOTAL">'
        '<geometryInfo x="1.5" y="2.2" width="3.0" height="0.4"/>'
        '<field name="F_ALL" source="CS_ALL_TOTAL">'
        '<geometryInfo x="3.0" y="2.25" width="1.0" height="0.2"/></field>'
        '<text name="B_ALLL"><geometryInfo x="1.6" y="2.25" width="1.3" '
        'height="0.2"/><textSegment><font face="Arial" size="9" bold="yes"/>'
        '<string><![CDATA[Report Total]]></string></textSegment></text>'
        '</frame>'
        '</frame>'
        '</body></section></layout></report>'
    )


def _rules(rdl):
    """{name: (top, left, width, ink, stroke_pt)} for every emitted rule."""
    out = {}
    for m in re.finditer(r'<Line Name="([^"]+)">(.*?)</Line>', rdl, re.S):
        body = m.group(2)
        ink = re.search(r"<Color>([^<]*)</Color>", body)
        pt = re.search(r"<Border>.*?<Width>([\d.]+)pt</Width>", body, re.S)
        tail = body.split("</Style>")[-1]

        def _f(tag):
            mm = re.search(r"<%s>([\d.]+)in</%s>" % (tag, tag), tail)
            return float(mm.group(1)) if mm else None
        out[m.group(1)] = (_f("Top"), _f("Left"), _f("Width"),
                           (ink.group(1) if ink else ""),
                           float(pt.group(1)) if pt else None)
    return out


def test_declared_body_lines_emit_at_their_declared_extents():
    rdl = convert(_ledger_xml().encode())["rdl_xml"]
    rules = _rules(rdl)
    # the group rule, the group-footer underline and the report-end rule
    # each print, each at its OWN declared x / width -- NOT at the width of
    # the band that encloses it (all three enclosing regions are wider).
    grp = rules.get("Rule_B_GRP_RULE")
    assert grp, f"declared group rule dropped; got {sorted(rules)}"
    assert abs(grp[1] - 0.20) < 0.02 and abs(grp[2] - 6.00) < 0.02, grp
    assert grp[4] == 2.0, grp          # declared lineWidth -> 1:1 points

    ftr = rules.get("Rule_B_FTR_RULE")
    assert ftr, f"declared footer underline dropped; got {sorted(rules)}"
    assert abs(ftr[1] - 5.00) < 0.02 and abs(ftr[2] - 1.20) < 0.02, ftr
    # no declared lineWidth -> Oracle's device hairline, in hairline ink
    assert ftr[4] == 0.25, ftr
    assert ftr[3].upper() == "#CCCCCC", ftr

    end = rules.get("Rule_B_END_RULE")
    assert end, f"declared report-end rule dropped; got {sorted(rules)}"
    assert abs(end[1] - 0.40) < 0.02 and abs(end[2] - 5.50) < 0.02, end
    assert end[4] == 3.0, end
    # the report-end rule prints ABOVE the report-total line it caps
    tot = re.search(r'<Textbox Name="Tb_GrandTotal_\d+">.*?'
                    r'<Top>([\d.]+)in</Top>', rdl, re.S)
    assert tot and end[0] <= float(tot.group(1)) + 1e-6, (end, tot.group(1))


def test_no_declared_line_invents_no_rule():
    """Negative twin: strip the declarations and the same layout must emit
    no body rule at all (the page-band chrome rule is a separate band)."""
    rdl = convert(_ledger_xml(group_rule=False, footer_rule=False,
                              end_rule=False).encode())["rdl_xml"]
    assert not [n for n in _rules(rdl) if n.startswith("Rule_")], _rules(rdl)


def test_line_pattern_gates_the_draw():
    """A <line> whose linePattern does not draw is declared-invisible --
    same dialect gate the page-band chrome rule uses."""
    rdl = convert(_ledger_xml(pattern='linePattern="transparent"')
                  .encode())["rdl_xml"]
    assert not [n for n in _rules(rdl) if n.startswith("Rule_")], _rules(rdl)


def test_declared_rule_is_not_stretched_to_the_band():
    """The defect in one line: every declared rule here is NARROWER than
    the region enclosing it, so a band-wide border would show up as a rule
    running the full body width."""
    rdl = convert(_ledger_xml().encode())["rdl_xml"]
    for name, geom in _rules(rdl).items():
        if not name.startswith("Rule_"):
            continue
        assert geom[2] <= 6.05, (name, geom)
