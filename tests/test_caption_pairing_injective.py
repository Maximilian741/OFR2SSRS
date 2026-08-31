"""Column-caption pairing is DECLARED-GEOMETRY-driven and injective.

Three wild-corpus render-measured defects, one root: the caption matcher
bucketed declared x to whole units and let two columns claim one label.

  1. A column midway between two label buckets (x=13.1 vs labels at 11.6
     and 13.75 -- bucket-key distance ties at 1) took its NEIGHBOUR's
     caption; the real caption 0.65in away never inked, and the neighbour
     caption painted TWICE at the identical spot (a caption self-pair on
     the engine PDF).
  2. A ``visible="no"`` field is computation-only -- Oracle never draws
     it -- yet it claimed a caption and a tablix column slot, shifting
     every later caption off its declared x.
  3. A bucket holding more labels than claiming columns (a group title
     sharing the data column's x) consumed labels by RANK, sliding every
     caption one slot left of its declaration.

The fixtures are synthetic; every assertion is driven by declared x/y.
"""
from __future__ import annotations

import pytest

from converter.parsers.oracle_xml import parse_oracle_xml
from converter.generators import rdl as R


def _build(caps, fields, width=18.0):
    """A one-band tabular source. ``caps`` = [(text, x, y, w)], ``fields`` =
    [(source, x, extra_attr)] on one detail row at y=0.5."""
    cap_xml = "".join(
        f'<text name="B_{i}"><geometryInfo x="{x:.5f}" y="{y:.5f}" '
        f'width="{w:.5f}" height="0.20000"/>'
        f'<textSegment><font face="Arial" size="8"/>'
        f'<string><![CDATA[{t}]]></string></textSegment></text>'
        for i, (t, x, y, w) in enumerate(caps))
    fld_xml = "".join(
        f'<field name="F_{i}" source="{s}"{extra}>'
        f'<geometryInfo x="{x:.5f}" y="0.50000" width="0.40000" '
        f'height="0.20000"/></field>'
        for i, (s, x, extra) in enumerate(fields))
    items = "".join(
        f'<dataItem name="{s}" datatype="character"/>'
        for s, _x, _e in fields)
    src = (
        '<?xml version="1.0"?><report name="CAPS_T" DTDVersion="9.0.2.0.10">'
        '<data><dataSource name="Q_1"><select><![CDATA[SELECT '
        + ", ".join(s for s, _x, _e in fields) + ' FROM T]]></select>'
        f'<group name="G_1">{items}</group></dataSource></data><layout>'
        f'<section name="main" width="{width:.5f}" height="11.00000">'
        f'<body width="{width - 1.0:.5f}" height="9.50000">'
        '<location x="0.5" y="0.5"/>'
        f'<frame name="M_1"><geometryInfo x="0" y="0" '
        f'width="{width - 1.0:.5f}" height="1.0"/>' + cap_xml +
        '<repeatingFrame name="R_1" source="G_1" printDirection="down">'
        f'<geometryInfo x="0" y="0.45000" width="{width - 1.0:.5f}" '
        'height="0.30000"/>' + fld_xml +
        '</repeatingFrame></frame></body></section></layout></report>'
    ).encode()
    return parse_oracle_xml(src)


def _caps_for(rep):
    cols = R._column_names_for_main(rep, rep.queries[0])
    return cols, R._column_captions_geo(rep, cols)


def test_bucket_tie_resolves_by_true_declared_distance():
    """Column at x=13.1 between label buckets 12 (label x=11.6) and 14
    (label x=13.75): the label 0.65 away is the caption, not the one 1.5
    away that whole-unit bucket distance tied it with."""
    rep = _build(
        caps=[("Alpha Cap", 11.6, 0.10, 1.3), ("Beta Cap", 13.75, 0.10, 1.8)],
        fields=[("C_ALPHA", 11.5, ""), ("C_BETA", 13.1, "")])
    _cols, caps = _caps_for(rep)
    assert caps.get("C_ALPHA") == "Alpha Cap", caps
    assert caps.get("C_BETA") == "Beta Cap", caps


def test_no_label_object_is_ever_assigned_twice():
    rep = _build(
        caps=[("Alpha Cap", 11.6, 0.10, 1.3), ("Beta Cap", 13.75, 0.10, 1.8)],
        fields=[("C_ALPHA", 11.5, ""), ("C_BETA", 13.1, "")])
    _cols, caps = _caps_for(rep)
    vals = list(caps.values())
    assert len(vals) == len(set(vals)), (
        "one caption text claimed by two columns -- the identical-spot "
        "double-draw class", caps)


def test_invisible_field_neither_claims_a_caption_nor_a_column():
    """visible="no" = computation-only (never drawn). It must not enter
    the printed column set, and it must not consume a neighbour's caption
    (which then paints twice at one spot). The ghost sits EXACTLY on the
    caption's declared x -- nearest-distance matching alone would hand it
    the label and leave the real printed column uncaptioned, so the
    visibility gate itself is load-bearing here."""
    rep = _build(
        caps=[("Row Cap", 0.0, 0.10, 0.6), ("One Cap", 0.25, 0.10, 3.4)],
        fields=[("C_ROW", 0.0, ""), ("C_ONE", 0.32, ""),
                ("C_GHOST", 0.25, ' visible="no"')])
    cols, caps = _caps_for(rep)
    assert "C_GHOST" not in cols, (
        "a visible=no field became a printed tablix column", cols)
    assert "C_GHOST" not in caps, caps
    assert caps.get("C_ROW") == "Row Cap", caps
    assert caps.get("C_ONE") == "One Cap", caps
    vals = list(caps.values())
    assert len(vals) == len(set(vals)), caps


def test_group_title_sharing_a_bucket_does_not_shift_captions():
    """Warehouse shape: a group title ('Widget') rides at y=0 over the Qty
    column's own x; 'Qty'/'Gross' captions sit below at y=0.25. The Gross
    column must keep its declared 'Gross' (rank consumption slid it to
    'Qty')."""
    rep = _build(
        caps=[("Widget", 7.688, 0.05, 1.5), ("Qty", 7.688, 0.25, 0.6),
              ("Gross", 8.375, 0.25, 0.6)],
        fields=[("W_QTY", 7.688, ""), ("W_GROSS", 8.375, "")])
    _cols, caps = _caps_for(rep)
    assert caps.get("W_GROSS") == "Gross", caps
    assert caps.get("W_QTY") in ("Widget", "Qty"), caps


def test_stacked_two_row_header_pairs_top_label_to_top_field():
    """A 2-row-per-record band: two labels stacked at ONE x pair to the two
    stacked fields in the same top-to-bottom order (the old rank behavior,
    kept where it was right)."""
    rep = _build(
        caps=[("Top Cap", 2.0, 0.10, 1.0), ("Bottom Cap", 2.0, 0.28, 1.0)],
        fields=[("C_LEFT", 0.5, ""), ("C_TOP", 2.0, "")])
    # add a second-row field at the same x as C_TOP by rebuilding: the
    # helper puts all fields on one row, so append a custom one.
    src_fields = [("C_LEFT", 0.5, ""), ("C_TOP", 2.0, "")]
    cap_xml = (
        '<text name="B_0"><geometryInfo x="2.00000" y="0.10000" '
        'width="1.00000" height="0.15000"/><textSegment>'
        '<font face="Arial" size="8"/>'
        '<string><![CDATA[Top Cap]]></string></textSegment></text>'
        '<text name="B_1"><geometryInfo x="2.00000" y="0.28000" '
        'width="1.00000" height="0.15000"/><textSegment>'
        '<font face="Arial" size="8"/>'
        '<string><![CDATA[Bottom Cap]]></string></textSegment></text>')
    src = (
        '<?xml version="1.0"?><report name="CAPS_T2" DTDVersion="9.0.2.0.10">'
        '<data><dataSource name="Q_1"><select><![CDATA[SELECT C_LEFT, C_TOP, '
        'C_BOTTOM FROM T]]></select><group name="G_1">'
        '<dataItem name="C_LEFT" datatype="character"/>'
        '<dataItem name="C_TOP" datatype="character"/>'
        '<dataItem name="C_BOTTOM" datatype="character"/>'
        '</group></dataSource></data><layout>'
        '<section name="main" width="8.50000" height="11.00000">'
        '<body width="7.50000" height="9.50000"><location x="0.5" y="0.5"/>'
        '<frame name="M_1"><geometryInfo x="0" y="0" width="7.5" '
        'height="1.0"/>' + cap_xml +
        '<repeatingFrame name="R_1" source="G_1" printDirection="down">'
        '<geometryInfo x="0" y="0.45000" width="7.5" height="0.60000"/>'
        '<field name="F_0" source="C_LEFT">'
        '<geometryInfo x="0.50000" y="0.50000" width="0.40000" '
        'height="0.20000"/></field>'
        '<field name="F_1" source="C_TOP">'
        '<geometryInfo x="2.00000" y="0.50000" width="0.40000" '
        'height="0.20000"/></field>'
        '<field name="F_2" source="C_BOTTOM">'
        '<geometryInfo x="2.00000" y="0.75000" width="0.40000" '
        'height="0.20000"/></field>'
        '</repeatingFrame></frame></body></section></layout></report>'
    ).encode()
    rep = parse_oracle_xml(src)
    _cols, caps = _caps_for(rep)
    assert caps.get("C_TOP") == "Top Cap", caps
    assert caps.get("C_BOTTOM") == "Bottom Cap", caps


def _build_wide(caps, fields):
    """Like ``_build`` but each field carries its own declared width:
    ``fields`` = [(source, x, width)]."""
    cap_xml = "".join(
        f'<text name="B_{i}"><geometryInfo x="{x:.5f}" y="{y:.5f}" '
        f'width="{w:.5f}" height="0.20000"/>'
        f'<textSegment><font face="Arial" size="8"/>'
        f'<string><![CDATA[{t}]]></string></textSegment></text>'
        for i, (t, x, y, w) in enumerate(caps))
    fld_xml = "".join(
        f'<field name="F_{i}" source="{s}">'
        f'<geometryInfo x="{x:.5f}" y="0.50000" width="{w:.5f}" '
        f'height="0.20000"/></field>'
        for i, (s, x, w) in enumerate(fields))
    items = "".join(
        f'<dataItem name="{s}" datatype="character"/>'
        for s, _x, _w in fields)
    src = (
        '<?xml version="1.0"?><report name="CAPS_W" DTDVersion="9.0.2.0.10">'
        '<data><dataSource name="Q_1"><select><![CDATA[SELECT '
        + ", ".join(s for s, _x, _w in fields) + ' FROM T]]></select>'
        f'<group name="G_1">{items}</group></dataSource></data><layout>'
        '<section name="main" width="8.50000" height="11.00000">'
        '<body width="7.50000" height="9.50000">'
        '<location x="0.5" y="0.5"/>'
        '<frame name="M_1"><geometryInfo x="0" y="0" '
        'width="7.50000" height="1.0"/>' + cap_xml +
        '<repeatingFrame name="R_1" source="G_1" printDirection="down">'
        '<geometryInfo x="0" y="0.45000" width="7.50000" '
        'height="0.30000"/>' + fld_xml +
        '</repeatingFrame></frame></body></section></layout></report>'
    ).encode()
    return parse_oracle_xml(src)


# ---------------------------------------------------------------------------
# Span-containment rescue: a caption CENTERED over a wide column sits far
# right of the column's left edge (outside the +/-1 bucket gate) -- it still
# captions that column. Wild-measured on two hospital listings whose 3.4in
# name column printed a humanized fallback while its true declared caption
# idled unconsumed 2.0in to the right.
# ---------------------------------------------------------------------------

_WIDE_CAPS = [("Alpha Cap", 0.40, 0.25, 0.9),
              ("Wide Middle Cap", 3.40, 0.25, 1.3),
              ("Delta Cap", 5.00, 0.25, 0.9)]
_WIDE_FIELDS = [("C_ALPHA", 0.40, 0.9),
                ("C_WIDE", 1.40, 3.40),
                ("C_DELTA", 5.00, 0.9)]


def test_wide_column_pairs_its_centered_caption_by_span():
    rep = _build_wide(caps=_WIDE_CAPS, fields=_WIDE_FIELDS)
    _cols, caps = _caps_for(rep)
    assert caps.get("C_WIDE") == "Wide Middle Cap", caps
    assert caps.get("C_ALPHA") == "Alpha Cap", caps
    assert caps.get("C_DELTA") == "Delta Cap", caps
    vals = list(caps.values())
    assert len(vals) == len(set(vals)), ("rescue double-used a label", caps)


def test_rescue_never_takes_a_title_floating_above_the_caption_row():
    # A page title also sits inside the wide column's span, HIGHER up and
    # closer to the column's centre -- it is not on the caption row, so it
    # must never displace (or stand in for) the declared caption.
    rep = _build_wide(
        caps=[("SOME PAGE TITLE", 2.90, 0.05, 2.0)] + _WIDE_CAPS,
        fields=_WIDE_FIELDS)
    _cols, caps = _caps_for(rep)
    assert caps.get("C_WIDE") == "Wide Middle Cap", caps
    assert "SOME PAGE TITLE" not in caps.values(), caps


def test_rescue_needs_an_established_caption_row():
    # No column pairs normally -> no caption band is established -> the
    # rescue must NOT scavenge the floating title for the wide column.
    rep = _build_wide(
        caps=[("SOME PAGE TITLE", 2.90, 0.05, 2.0)],
        fields=[("C_WIDE", 1.40, 3.40)])
    _cols, caps = _caps_for(rep)
    assert caps.get("C_WIDE") is None, caps


def test_rescue_only_reaches_into_the_columns_own_span():
    # An orphan label on the caption row, declared far OUTSIDE the capless
    # column's span, is not that column's caption -- the rescue must leave
    # the column on its humanized fallback rather than scavenge it.
    rep = _build_wide(
        caps=[("Alpha Cap", 0.40, 0.25, 0.9),
              ("Orphan Cap", 5.50, 0.25, 0.9)],
        fields=[("C_ALPHA", 0.40, 0.9),
                ("C_NARROW", 3.00, 0.4)])
    _cols, caps = _caps_for(rep)
    assert caps.get("C_ALPHA") == "Alpha Cap", caps
    assert caps.get("C_NARROW") is None, caps
