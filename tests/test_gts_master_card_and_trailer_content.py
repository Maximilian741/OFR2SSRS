"""Master-detail break report: body-level and band content the grouped
route used to drop, measured on a held-out wild master/detail report.

Five general content-loss classes, all structural (no names):

1. A body-level PLAIN frame declared ABOVE the group frame (the master
   card: literal labels + report-formula values) was never walked -- the
   grouped-subtotal route rendered only the group tree, so the whole
   declared card vanished.
2. A SECTION TITLE text owned by a NON-repeating wrap frame, declared
   between the group band and the column strip, fell into a collector gap
   (not outer-direct, not in the strip window, not below the detail) and
   was dropped.
3. The OUTER GROUP FRAME'S OWN below-detail members (the group's closing
   totals caption + CF_ value boxes, declared directly on the group frame)
   were skipped by the footer scan's blanket repeating-frame filter.
4. A text DIRECTLY owned by a NESTED repeating frame within 0.5in above
   the detail row (a per-record card label) leaked into the column strip,
   squeezing the declared captions' neighbour-clamped widths (one caption
   wrapped into its neighbour -- the measured graze) and double-painting
   wording the per-row breakdown already carries.
5. A PLACED report-scope summary whose value cannot be decomposed into one
   SSRS aggregate dropped its DECLARED literal caption along with the
   value; and when two such value boxes share one declared caption, the
   caption must print once, not twice.

Plus the honest-value rule the closing-totals fix exposed: a FORMULA
column whose body will not compile binds its own formula-dataset stub,
never the last-detail-column Sum(Val()) guess.
"""
import re

from converter import convert
from converter.generators import rdl as R
from converter.parsers.oracle_xml import parse_oracle_xml


_OPAQUE_NUM = (
    "FUNCTION {name} RETURN NUMBER IS BEGIN "
    "RETURN(Pkg_Ext.F_Tot(piKey => :MKEY)); END;"
)
_OPAQUE_CHR = (
    "FUNCTION {name} RETURN VARCHAR2 IS BEGIN "
    "RETURN(Pkg_Ext.F_Lbl(piKey => :MKEY)); END;"
)


def _md_report():
    """Synthetic 3-level master-detail break report shaped like the wild
    class: body lead-in card frame, outer group frame with a wrap-frame
    title, a nested per-record repeating frame carrying its own labels, a
    column strip, an inner detail table, outer-direct closing totals, and
    a body-direct report trailer whose two value boxes share one caption.
    All names/labels invented -- the guards are structural."""
    return (
        '<?xml version="1.0"?><report name="MDBRK_T" DTDVersion="9.0.2.0.10">'
        '<data>'
        '<dataSource name="Q_M"><select>'
        '<![CDATA[select mkey, mlabel from m]]></select>'
        '<group name="G_M">'
        '<dataItem name="MKEY" datatype="number"/>'
        '<dataItem name="MLABEL" datatype="vchar2"/>'
        '<summary name="CS_RTOT_A" source="CF_GTOT" function="sum" '
        'reset="report" compute="report"/>'
        '<summary name="CS_RTOT_B" source="CF_GTOT2" function="sum" '
        'reset="report" compute="report"/>'
        '</group></dataSource>'
        '<dataSource name="Q_MID"><select>'
        '<![CDATA[select midkey, midname from mid where mkey = :MKEY]]>'
        '</select>'
        '<group name="G_MID">'
        '<dataItem name="MIDKEY" datatype="number"/>'
        '<dataItem name="MIDNAME" datatype="vchar2"/>'
        '<summary name="CS_DTOT" source="DAMT" function="sum" '
        'reset="G_MID" compute="report"/>'
        '</group></dataSource>'
        '<dataSource name="Q_D"><select>'
        '<![CDATA[select dkey, dcol1, dcol2, dcol3, damt from d '
        'where midkey = :MIDKEY]]></select>'
        '<group name="G_D">'
        '<dataItem name="DKEY" datatype="number"/>'
        '<dataItem name="DCOL1" datatype="vchar2"/>'
        '<dataItem name="DCOL2" datatype="vchar2"/>'
        '<dataItem name="DCOL3" datatype="vchar2"/>'
        '<dataItem name="DAMT" datatype="number"/>'
        '</group></dataSource>'
        '<link parentGroup="G_M" childQuery="Q_MID" condition="eq" '
        'sqlClause="where"/>'
        '<link parentGroup="G_MID" childQuery="Q_D" condition="eq" '
        'sqlClause="where"/>'
        '<formula name="CF_HEADVAL" source="cf_headval_f" '
        'datatype="character" width="30"/>'
        '<formula name="CF_GTOT" source="cf_gtot_f" datatype="number"/>'
        '<formula name="CF_GTOT2" source="cf_gtot2_f" datatype="number"/>'
        '</data>'
        '<programUnits>'
        '<function name="cf_headval_f"><textSource><![CDATA['
        + _OPAQUE_CHR.format(name="cf_headval_f") + ']]></textSource>'
        '</function>'
        '<function name="cf_gtot_f"><textSource><![CDATA['
        + _OPAQUE_NUM.format(name="cf_gtot_f") + ']]></textSource>'
        '</function>'
        '<function name="cf_gtot2_f"><textSource><![CDATA['
        + _OPAQUE_NUM.format(name="cf_gtot2_f") + ']]></textSource>'
        '</function>'
        '</programUnits>'
        '<layout><section name="main" width="8.50000" height="11.00000">'
        '<body>'
        # 1) body-level MASTER CARD above the group frame
        '<frame name="M_CARD"><geometryInfo x="0" y="0" width="7.5" '
        'height="0.40"/>'
        '<text name="B_A"><geometryInfo x="0.05" y="0.05" width="0.70" '
        'height="0.15"/><textSegment><font face="Arial" size="9"/>'
        '<string><![CDATA[Alpha:]]></string></textSegment></text>'
        '<field name="F_HV" source="CF_HEADVAL">'
        '<geometryInfo x="0.80" y="0.05" width="2.5" height="0.15"/></field>'
        '</frame>'
        # the group host frame
        '<frame name="M_HOST"><geometryInfo x="0" y="0.40" width="7.5" '
        'height="3.20"/>'
        '<repeatingFrame name="R_OUT" source="G_M" printDirection="down">'
        '<geometryInfo x="0" y="0.40" width="7.4" height="3.10"/>'
        '<field name="F_MKEY" source="MKEY">'
        '<geometryInfo x="0.05" y="0.45" width="0.9" height="0.15"/></field>'
        '<field name="F_ML" source="MLABEL">'
        '<geometryInfo x="1.05" y="0.45" width="2.5" height="0.15"/></field>'
        # 2) wrap frame with a section title ABOVE the strip
        '<frame name="M_WRAP"><geometryInfo x="0.05" y="0.75" width="7.3" '
        'height="1.60"/>'
        '<text name="B_TITLE"><geometryInfo x="0.10" y="0.78" width="2.0" '
        'height="0.15"/><textSegment><font face="Arial" size="9"/>'
        '<string><![CDATA[SECTION ONE]]></string></textSegment></text>'
        '<repeatingFrame name="R_MID" source="G_MID" printDirection="down">'
        '<geometryInfo x="0.10" y="1.00" width="7.2" height="1.20"/>'
        # 4) per-record label of the NESTED repeating frame, inside the
        #    strip window (0 < drow_y 1.50 - 1.05 <= 0.5)
        '<text name="B_P1"><geometryInfo x="0.10" y="1.05" width="0.60" '
        'height="0.13"/><textSegment><font face="Arial" size="8"/>'
        '<string><![CDATA[Pair:]]></string></textSegment></text>'
        '<field name="F_MIDNAME" source="MIDNAME">'
        '<geometryInfo x="0.75" y="1.05" width="1.8" height="0.13"/></field>'
        '<frame name="M_STRIP"><geometryInfo x="0.10" y="1.30" width="7.2" '
        'height="0.15"/>'
        '<text name="B_C1"><geometryInfo x="0.10" y="1.30" width="1.3" '
        'height="0.13"/><textSegment><font face="Arial" size="8"/>'
        '<string><![CDATA[Col One]]></string></textSegment></text>'
        '<text name="B_C2"><geometryInfo x="1.50" y="1.30" width="1.4" '
        'height="0.13"/><textSegment><font face="Arial" size="8"/>'
        '<string><![CDATA[Col Two]]></string></textSegment></text>'
        '<text name="B_C3"><geometryInfo x="3.00" y="1.30" width="1.4" '
        'height="0.13"/><textSegment><font face="Arial" size="8"/>'
        '<string><![CDATA[Col Three]]></string></textSegment></text>'
        '<text name="B_C4"><geometryInfo x="4.50" y="1.30" width="1.4" '
        'height="0.13"/><textSegment><font face="Arial" size="8"/>'
        '<string><![CDATA[Col Amount]]></string></textSegment></text>'
        '</frame>'
        '<repeatingFrame name="R_DET" source="G_D" printDirection="down">'
        '<geometryInfo x="0.10" y="1.50" width="7.2" height="0.15"/>'
        '<field name="F_D1" source="DCOL1">'
        '<geometryInfo x="0.10" y="1.50" width="1.3" height="0.13"/></field>'
        '<field name="F_D2" source="DCOL2">'
        '<geometryInfo x="1.50" y="1.50" width="1.4" height="0.13"/></field>'
        '<field name="F_D3" source="DCOL3">'
        '<geometryInfo x="3.00" y="1.50" width="1.4" height="0.13"/></field>'
        '<field name="F_DAMT" source="DAMT">'
        '<geometryInfo x="4.50" y="1.50" width="1.4" height="0.13"/></field>'
        '</repeatingFrame>'
        '</repeatingFrame>'
        # group subtotal inside a footer sub-frame (keeps the route stable)
        '<text name="B_ST"><geometryInfo x="2.00" y="2.50" width="1.5" '
        'height="0.15"/><textSegment><font face="Arial" size="9"/>'
        '<string><![CDATA[Sub Pair]]></string></textSegment></text>'
        '<field name="F_SUBTOT" source="CS_DTOT">'
        '<geometryInfo x="4.50" y="2.50" width="1.4" height="0.15"/></field>'
        '</frame>'
        # 3) OUTER-DIRECT closing totals line, below the detail
        '<text name="B_GT"><geometryInfo x="2.00" y="3.20" width="2.2" '
        'height="0.15"/><textSegment><font face="Arial" size="9"/>'
        '<string><![CDATA[Grand Pair Total]]></string></textSegment></text>'
        '<field name="F_GTOT" source="CF_GTOT">'
        '<geometryInfo x="5.00" y="3.20" width="1.2" height="0.15"/></field>'
        '</repeatingFrame></frame>'
        # 5) body-direct report trailer: one caption, two summary boxes
        '<text name="B_RT"><geometryInfo x="1.00" y="3.80" width="3.0" '
        'height="0.15"/><textSegment><font face="Arial" size="9"/>'
        '<string><![CDATA[Report Total Caption]]></string></textSegment>'
        '</text>'
        '<field name="F_RA" source="CS_RTOT_A">'
        '<geometryInfo x="5.00" y="3.80" width="1.0" height="0.15"/></field>'
        '<field name="F_RB" source="CS_RTOT_B">'
        '<geometryInfo x="6.20" y="3.80" width="1.0" height="0.15"/></field>'
        '</body></section></layout></report>'
    )


def _rdl():
    return convert(_md_report().encode())["rdl_xml"]


def _boxes(rdl, prefix):
    """{name: full <Textbox> body} for every textbox named ``prefix``*."""
    return {m.group(1): m.group(0)
            for m in re.finditer(
                rf'<Textbox Name="({re.escape(prefix)}[^"]*)">.*?</Textbox>',
                rdl, re.S)}


def test_fixture_routes_through_the_grouped_subtotal_emitter():
    rep = parse_oracle_xml(_md_report().encode())
    assert R._is_grouped_tabular_subtotal(rep), (
        "the guards below only mean anything on the grouped-subtotal route")


def test_body_level_master_card_emits_above_the_tablix():
    """Class 1: the lead-in card's literal label and its formula value box
    both reach the RDL, and the group tablix starts BELOW the card's
    declared span (the card prints once at the head of the flow)."""
    rdl = _rdl()
    assert "Alpha:" in rdl, "master-card literal label lost"
    lead = re.search(r'<Rectangle Name="GTSLead_Rect_\d+">.*?</Rectangle>',
                     rdl, re.S)
    assert lead, "master-card frame must emit as a lead-in Rectangle"
    assert "CF_HEADVAL" in lead.group(0), (
        "the card's report-formula value box must emit with the card")
    import xml.etree.ElementTree as ET
    root = ET.fromstring(re.sub(r'xmlns="[^"]+"', "", rdl, count=1))
    tb = next(t for t in root.iter("Tablix")
              if t.get("Name") == "Tablix_GroupedSubtotal")
    top = float((tb.findtext("Top") or "0").replace("in", ""))
    assert top >= 0.39, (
        f"the group tablix must sit below the declared card span, Top={top}")


def test_wrap_frame_section_title_joins_the_group_band():
    """Class 2: the wrap-frame title above the column strip prints in the
    group header band instead of vanishing."""
    rdl = _rdl()
    hdr = re.search(r'<Rectangle Name="GTS_Hdr">.*?'
                    r'<Rectangle Name="GTS_ColBand">', rdl, re.S)
    assert hdr, "grouped-subtotal route must emit the header band"
    assert "SECTION ONE" in hdr.group(0), (
        "the declared wrap-frame section title must print in the band")


def test_outer_direct_closing_totals_join_the_group_footer():
    """Class 3: the group frame's OWN below-detail caption + CF_ value box
    print in the group footer."""
    rdl = _rdl()
    ftr = re.search(r'<Rectangle Name="GTS_Footer">.*?</TablixRow>',
                    rdl, re.S)
    assert ftr, "grouped-subtotal route must emit the footer band"
    assert "Grand Pair Total" in ftr.group(0), (
        "the outer-frame-direct closing caption must print in the footer")
    assert "CF_GTOT" in ftr.group(0), (
        "the outer-frame-direct closing value box must print in the footer")


def test_nested_per_record_label_stays_out_of_the_column_strip():
    """Class 4: the nested repeating frame's own label never joins the
    column strip (it repeats per record and its wording lives in the
    per-row breakdown); the strip carries exactly the declared captions."""
    rdl = _rdl()
    ch = _boxes(rdl, "Tb_CH_")
    vals = {re.search(r"<Value>(.*?)</Value>", b, re.S).group(1)
            for b in ch.values()}
    assert vals == {"Col One", "Col Two", "Col Three", "Col Amount"}, (
        f"column strip must carry exactly the declared captions, got {vals}")


def test_unresolvable_report_summary_keeps_its_declared_caption():
    """Class 5: the report trailer's declared caption prints (once) even
    though neither summary's value is decomposable into one SSRS
    aggregate; the value boxes stay HONESTLY blank (=Nothing), and the
    shared caption is never painted twice."""
    rdl = _rdl()
    gt = _boxes(rdl, "Tb_GrandTotal_")
    cap = [b for b in gt.values() if "Report Total Caption" in b]
    assert len(cap) == 1, (
        f"the declared trailer caption must print exactly once, "
        f"found {len(cap)}")
    blanks = [b for b in gt.values()
              if re.search(r"<Value>[^<]*Nothing</Value>", b)]
    assert blanks, "the unresolvable values must emit as honest blanks"
    # never an invented number for an unresolvable report summary
    for b in gt.values():
        v = re.search(r"<Value>(.*?)</Value>", b, re.S).group(1)
        assert "Sum(Val(" not in v, (
            "an unresolvable report summary must never print a guessed "
            "unrelated-column total")


def test_uncompilable_formula_footer_binds_its_stub_not_a_guess():
    """The closing-totals value box binds the formula dataset's own stub
    column (honestly blank until the user fills the stub SQL), never the
    last-detail-column Sum(Val()) guess."""
    rdl = _rdl()
    ftr = re.search(r'<Rectangle Name="GTS_Footer">.*?</TablixRow>',
                    rdl, re.S).group(0)
    m = re.search(r"<Value>([^<]*CF_GTOT[^<]*)</Value>", ftr)
    assert m, "the closing-totals value box must bind its formula column"
    assert "DS_REPORT_FORMULAS" in m.group(1), (
        "an uncompilable formula binds its formula-dataset stub")
    assert "Sum(Val(" not in m.group(1), (
        "never the unrelated last-detail-column guess")
