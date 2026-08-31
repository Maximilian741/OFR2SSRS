"""Declared coordinate UNITS are part of the declaration.

Oracle stamps the authoring unit on the report root
(``<report unitOfMeasurement="centimeter">``); every geometryInfo /
section / body / location number is authored in that unit. The whole
pipeline downstream is in inches, so an unhonored declaration inflates
every page 2.54x. Wild-corpus measured: a letter sheet declared
21.59 x 27.94 CENTIMETERS (exactly 8.5 x 11in) was emitted as a
21.59in-wide logical sheet, which tripped the physical-paper cap and its
oversized-sheet chrome remap into title/footer overlaps the source never
declared (three distinct paint signatures on one wild family).

Everything here is synthetic and structural -- no report names, no
customer wording. Stroke widths (lineWidth) are POINTS in every dialect
and must never be scaled with the geometry.
"""
from __future__ import annotations

import re

import pytest

from converter import convert
from converter.parsers.oracle_xml import parse_oracle_xml


def _source(unit_attr: str) -> bytes:
    """A letter sheet + one band, authored in the given unit (cm numbers).

    Declared (in centimeters): section 21.59 x 27.94, body 19.05 x 22.86 at
    location (1.27, 2.54); a frame at x=2.54 y=1.27 w=12.70 h=1.905; a
    repeating frame with vertSpaceBetweenFrames=0.127; a field at x=5.08.
    """
    return (
        '<?xml version="1.0"?><report name="UNIT_T" DTDVersion="9.0.2.0.10"'
        + unit_attr + '>'
        '<data><dataSource name="Q_1"><select><![CDATA[SELECT COL_A, COL_B '
        'FROM T]]></select>'
        '<group name="G_1">'
        '<dataItem name="COL_A" datatype="character"/>'
        '<dataItem name="COL_B" datatype="character"/>'
        '</group></dataSource></data><layout>'
        '<section name="main" width="21.59000" height="27.94000">'
        '<body width="19.05000" height="22.86000">'
        '<location x="1.27000" y="2.54000"/>'
        '<frame name="M_1"><geometryInfo x="2.54000" y="1.27000" '
        'width="12.70000" height="1.90500"/>'
        '<text name="B_A"><geometryInfo x="2.54000" y="1.27000" '
        'width="5.08000" height="0.63500"/>'
        '<textSegment><font face="Arial" size="8"/>'
        '<string><![CDATA[Alpha Cap]]></string></textSegment></text>'
        '<repeatingFrame name="R_1" source="G_1" printDirection="down" '
        'vertSpaceBetweenFrames="0.12700">'
        '<geometryInfo x="2.54000" y="2.03200" width="12.70000" '
        'height="0.63500"/>'
        '<field name="F_A" source="COL_A">'
        '<geometryInfo x="5.08000" y="2.03200" width="5.08000" '
        'height="0.63500"/></field>'
        '<field name="F_B" source="COL_B">'
        '<geometryInfo x="10.16000" y="2.03200" width="2.54000" '
        'height="0.63500"/></field>'
        '</repeatingFrame></frame></body></section></layout></report>'
    ).encode()


def _main_section(rep):
    for g in rep.layout or []:
        if (g.kind or "") == "section_main":
            return g
    raise AssertionError("no section_main parsed")


def _walk_fields(g):
    for f in g.fields or []:
        yield f
    for c in g.children or []:
        yield from _walk_fields(c)


def _field(rep, name):
    for f in _walk_fields(_main_section(rep)):
        if getattr(f, "name", "") == name:
            return f
    raise AssertionError(f"{name} not parsed")


def _group(rep, name):
    def walk(g):
        if getattr(g, "name", "") == name:
            return g
        for c in g.children or []:
            hit = walk(c)
            if hit is not None:
                return hit
        return None
    hit = walk(_main_section(rep))
    assert hit is not None, f"{name} not parsed"
    return hit


def test_centimeter_geometry_normalizes_to_inches():
    rep = parse_oracle_xml(_source(' unitOfMeasurement="centimeter"'))
    sec = _main_section(rep)
    assert abs(sec.width - 8.5) < 0.001, sec.width
    assert abs(sec.height - 11.0) < 0.001, sec.height
    assert abs(sec.body_width - 7.5) < 0.001, sec.body_width
    assert abs(sec.body_height - 9.0) < 0.001, sec.body_height
    assert sec.body_location and abs(sec.body_location[0] - 0.5) < 0.001 \
        and abs(sec.body_location[1] - 1.0) < 0.001, sec.body_location
    fa = _field(rep, "F_A")
    assert abs(fa.x - 2.0) < 0.001 and abs(fa.width - 2.0) < 0.001, \
        (fa.x, fa.width)
    rf = _group(rep, "R_1")
    assert abs(rf.vert_space - 0.05) < 0.001, rf.vert_space
    assert any("centimeter" in w for w in rep.warnings), rep.warnings


def test_point_geometry_normalizes_to_inches():
    rep = parse_oracle_xml(_source(' unitOfMeasurement="point"'))
    fa = _field(rep, "F_A")
    # 5.08 points = 5.08/72 inch
    assert abs(fa.x - 5.08 / 72.0) < 1e-6, fa.x


def test_inch_and_undeclared_geometry_stays_one_to_one():
    for attr in ('', ' unitOfMeasurement="inch"'):
        rep = parse_oracle_xml(_source(attr))
        fa = _field(rep, "F_A")
        assert abs(fa.x - 5.08) < 1e-9, (attr, fa.x)
        sec = _main_section(rep)
        assert abs(sec.width - 21.59) < 1e-9, (attr, sec.width)


def test_unknown_unit_is_kept_one_to_one_with_a_warning():
    rep = parse_oracle_xml(_source(' unitOfMeasurement="furlong"'))
    fa = _field(rep, "F_A")
    assert abs(fa.x - 5.08) < 1e-9, fa.x
    assert any("furlong" in w for w in rep.warnings), rep.warnings


def test_centimeter_letter_sheet_emits_a_letter_page():
    """End to end: the declared 21.59 x 27.94 cm sheet IS 8.5 x 11in --
    the emitted page must say so instead of a capped oversized sheet."""
    rdl = convert(_source(' unitOfMeasurement="centimeter"'))["rdl_xml"]
    m = re.search(r"<PageWidth>([\d.]+)in</PageWidth>", rdl)
    assert m, "no PageWidth emitted"
    assert abs(float(m.group(1)) - 8.5) < 0.01, m.group(1)
    m = re.search(r"<PageHeight>([\d.]+)in</PageHeight>", rdl)
    assert m and abs(float(m.group(1)) - 11.0) < 0.01, m and m.group(1)


def test_stroke_width_is_points_and_never_scales():
    """lineWidth is a POINT stroke weight in every dialect -- scaling it
    with a centimeter declaration would fatten every hairline 28x."""
    src = _source(' unitOfMeasurement="centimeter"').replace(
        b'<field name="F_A" source="COL_A">',
        b'<field name="F_A" source="COL_A">'
        b'<visualSettings linePattern="solid" lineWidth="1"/>')
    rep = parse_oracle_xml(src)
    fa = _field(rep, "F_A")
    assert abs(getattr(fa, "line_width", 0.0) - 1.0) < 1e-9, fa.line_width
