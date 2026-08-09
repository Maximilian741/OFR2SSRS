"""Group-FOOTER frames: every declared member prints, including one that
re-declares the group's break key.

DEFECT THIS GUARDS (content loss, truth-measured 2026-08-08)
------------------------------------------------------------
Oracle's classic break report closes a group by RE-PRINTING the group's
name on the closing line, beside that group's running total. It is
declared as a real ``<field>`` inside the group-FOOTER frame, with its own
x / width / weight -- e.g. x=0 w=5.09375 10pt bold, flush with the sheet's
left margin, on the same declared line as the last total.

The grouped-subtotal emitter used to DROP any footer field whose source
matched the break key ("keeps the totals stack clean/right-aligned"), so
one bold caption per group vanished from the output. The Oracle-rendered
truth PDF of a 70-page break report of exactly this shape prints that
caption at EVERY group close (page 1: y=438.14pt x=36.00pt
Helvetica-Bold 10, i.e. declared x=0 + the 0.5in section body origin, on
the same line as that group's yard total right-aligned at the declared
8.0in right edge) -- never once without it.

So a footer member is emitted because it is DECLARED, and for no other
reason; matching the break key is not a licence to drop a declaration.
Synthetic fixture only -- no customer report, field or label names.
"""
import re

import pytest

from converter import convert
from converter.generators import rdl as R
from converter.parsers.oracle_xml import parse_oracle_xml


# --------------------------------------------------------------------------
# A synthetic 2-level break report: master + linked detail, a >=3-label
# column strip, a 4-column detail row, and a group-footer frame carrying a
# summary total AND the closing break-key caption.
# --------------------------------------------------------------------------
def _break_report(closing_caption=True, caption_bold=True):
    """``closing_caption=False`` removes ONLY the footer's break-key field
    (the mutation that proves the guard is declaration-driven)."""
    cap = ""
    if closing_caption:
        bold = ' bold="yes"' if caption_bold else ""
        cap = (
            '<field name="F_CLOSE" source="GRP_LABEL" alignment="start">'
            f'<font face="Arial" size="10"{bold}/>'
            '<geometryInfo x="0.00000" y="1.45000" width="5.09375" '
            'height="0.18738"/></field>'
        )
    return (
        '<?xml version="1.0"?><report name="BRKFTR_T" DTDVersion="9.0.2.0.10">'
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
        '</data>'
        '<layout><section name="main" width="8.50000" height="11.00000">'
        '<body>'
        '<frame name="M_BODY"><geometryInfo x="0" y="0" width="7.5" '
        'height="2.0"/>'
        '<repeatingFrame name="R_OUTER" source="G_M" printDirection="down">'
        '<geometryInfo x="0" y="0" width="7.5" height="1.9"/>'
        '<field name="F_GRP" source="GRP_LABEL">'
        '<font face="Arial" size="10" bold="yes"/>'
        '<geometryInfo x="0.00000" y="0.06" width="5.09375" height="0.19"/>'
        '</field>'
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
        '<field name="F_TOT" source="CS_QTY_TOTAL" alignment="end">'
        '<font face="Arial" size="10" bold="yes"/>'
        '<geometryInfo x="6.60" y="1.45000" width="0.90" height="0.19"/>'
        '</field>'
        + cap +
        '</frame>'
        '</repeatingFrame></frame>'
        '</body></section></layout></report>'
    )


def _footer_band(rdl):
    """The group-footer cell's rectangle block, verbatim."""
    m = re.search(r'<Rectangle Name="GTS_Footer">(.*?)</Rectangle>\s*'
                  r'</CellContents>', rdl, re.S)
    if m is None:
        m = re.search(r'<Rectangle Name="GTS_Footer">(.*)', rdl, re.S)
    return m.group(1) if m else ""


def _textboxes(block):
    """[(name, body)] for every <Textbox> in an RDL fragment."""
    return [(m.group(1), m.group(0)) for m in
            re.finditer(r'<Textbox Name="([^"]+)">.*?</Textbox>',
                        block, re.S)]


def _child(body, tag):
    m = re.search(rf'<{tag}>(.*?)</{tag}>', body, re.S)
    return m.group(1) if m else ""


def test_fixture_routes_through_the_grouped_subtotal_emitter():
    """The guards below only mean anything on that route."""
    rep = parse_oracle_xml(_break_report().encode())
    assert R._is_grouped_tabular_subtotal(rep)
    assert R._grouped_tabular_spec(rep)["grp_key"] == "GRP_LABEL", (
        "the closing caption must genuinely re-declare the BREAK KEY -- "
        "otherwise this fixture guards nothing")


def test_declared_footer_break_key_caption_is_emitted():
    """The footer's break-key field prints in the group-footer band."""
    rdl = convert(_break_report().encode())["rdl_xml"]
    band = _footer_band(rdl)
    assert band, "grouped-subtotal route must emit the group-footer band"
    hits = [(n, b) for n, b in _textboxes(band)
            if "GRP_LABEL" in _child(b, "Value")]
    assert hits, (
        "the DECLARED closing caption is missing from the group footer -- "
        "one caption per group of pure content loss.\nfooter band was:\n"
        + band[:1500])


def test_footer_break_key_caption_keeps_its_declared_geometry_and_weight():
    """Declared x=0 / width=5.09375 / 10pt bold, not a right-stacked
    synthesized total."""
    rdl = convert(_break_report().encode())["rdl_xml"]
    band = _footer_band(rdl)
    hits = [b for _n, b in _textboxes(band)
            if "GRP_LABEL" in _child(b, "Value")]
    assert hits
    body = hits[0]
    left = _child(body, "Left")
    width = _child(body, "Width")
    assert float(left.rstrip("in")) <= 0.03, (
        f"declared x=0 must anchor the caption at the band's left edge, "
        f"got Left={left!r}")
    assert abs(float(width.rstrip("in")) - 5.09375) <= 0.02, (
        f"declared width 5.09375in must survive, got Width={width!r}")
    assert "<FontWeight>Bold</FontWeight>" in body, (
        "the caption declares bold=yes")
    assert "<TextAlign>Left</TextAlign>" in body, (
        'the caption declares alignment="start"')


def test_the_caption_shares_the_footer_line_with_that_group_s_total():
    """Truth prints caption and total on ONE line (same declared y). They
    must land on the same Top inside the footer band, and must not overlap
    horizontally."""
    rdl = convert(_break_report().encode())["rdl_xml"]
    band = _footer_band(rdl)
    boxes = {}
    for _n, b in _textboxes(band):
        val = _child(b, "Value")
        key = ("caption" if "GRP_LABEL" in val
               else "total" if "QTY" in val else None)
        if key:
            boxes[key] = (float(_child(b, "Top").rstrip("in")),
                          float(_child(b, "Left").rstrip("in")),
                          float(_child(b, "Width").rstrip("in")))
    assert set(boxes) == {"caption", "total"}, (
        f"expected both footer members, got {sorted(boxes)}")
    assert abs(boxes["caption"][0] - boxes["total"][0]) <= 0.01, (
        "both declare y=1.45 -> one printed line")
    c_right = boxes["caption"][1] + boxes["caption"][2]
    assert c_right <= boxes["total"][1] + 0.001, (
        "the caption's declared box ends before the total's begins; "
        "emitting it must not paint over the totals stack")


def test_no_declaration_no_caption():
    """PROVE THE GATE: with the footer field removed the caption is gone,
    so the emitter is reading the declaration and not synthesizing a
    closing line of its own."""
    rdl = convert(_break_report(closing_caption=False).encode())["rdl_xml"]
    band = _footer_band(rdl)
    assert band, "the footer band itself must still be emitted"
    hits = [n for n, b in _textboxes(band)
            if "GRP_LABEL" in _child(b, "Value")]
    assert not hits, (
        f"no footer declaration -> no closing caption, got {hits}")


@pytest.mark.parametrize("bold", [True, False])
def test_caption_weight_follows_the_declaration(bold):
    """Weight is read off the declaration, never assumed from position."""
    rdl = convert(_break_report(caption_bold=bold).encode())["rdl_xml"]
    band = _footer_band(rdl)
    body = [b for _n, b in _textboxes(band)
            if "GRP_LABEL" in _child(b, "Value")][0]
    assert ("<FontWeight>Bold</FontWeight>" in body) is bold
