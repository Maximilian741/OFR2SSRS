"""DECLARED page-scoped print rules — <advancedLayout printObjectOnPage=...>.

Oracle's ``printObjectOnPage`` states which of the pages an object's
ENCLOSING object spans the object itself prints on.  Most declared values are
already what a body flow does and need no special emission
(``firstPage``/``lastPage`` = print once where the flow puts it; ``allPage`` =
the page-repeating column header, carried by the tablix header machinery).

The ``allBut…`` values are the ones a BODY object cannot express: they ask for
the object to be SUPPRESSED on one page of the span and printed on every other
one — the continuation marker a report prints beside a group caption whenever
that group runs past a page.  SSRS expresses exactly that with a page-number
Hidden expression, and ``Globals!PageNumber`` is legal ONLY inside a page
band, so the object has to reach the band to be gated at all.

Measured before the fix (14 truth-paired reports + the wider export folder):
every declared ``allButFirstPage`` text was ABSENT from the generated RDL —
content loss on three reports — while every ``firstPage`` text (52 of them)
was already present, which is why this pass deliberately moves the
``allBut…`` family only.
"""

import re
import xml.etree.ElementTree as ET

from converter import convert

_NS = "{http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition}"

_MARKER = "(carried over)"


def _xml(print_rule: str = 'printObjectOnPage="allButFirstPage" ') -> bytes:
    """A grouped listing whose group caption carries a marker text declared
    with ``print_rule`` (pass "" for an undeclared object)."""
    return (
        '<?xml version="1.0"?>'
        '<report name="PGATE_T" DTDVersion="9.0.2.0.10">'
        '<data><dataSource name="Q_Main">'
        '<select><![CDATA[select grp_col, row_a, row_b from t]]></select>'
        '<group name="G_Grp"><dataItem name="GRP_COL" datatype="vchar2"/>'
        '</group>'
        '<group name="G_Row"><dataItem name="ROW_A" datatype="vchar2"/>'
        '<dataItem name="ROW_B" datatype="vchar2"/></group>'
        '</dataSource></data>'
        '<layout><section name="main" width="8.50000" height="11.00000">'
        '<body height="9.5">'
        '<repeatingFrame name="R_GRP" source="G_Grp" printDirection="down">'
        '<geometryInfo x="0.0" y="0.0" width="7.5" height="1.7"/>'
        '<field name="F_GRP" source="GRP_COL">'
        '<geometryInfo x="0.01" y="0.05" width="3.0" height="0.19"/></field>'
        '<text name="B_MORE"><textSettings spacing="single"/>'
        '<geometryInfo x="3.06" y="0.05" width="0.74" height="0.17"/>'
        + (f'<advancedLayout {print_rule}basePrintingOn="enclosingObject"/>'
           if print_rule else "")
        + '<textSegment><font face="Arial" size="10"/>'
        f'<string><![CDATA[{_MARKER}]]></string></textSegment></text>'
        '<repeatingFrame name="R_ROW" source="G_Row" printDirection="down">'
        '<geometryInfo x="0.14" y="0.28" width="7.3" height="0.4"/>'
        '<field name="F_A" source="ROW_A">'
        '<geometryInfo x="0.2" y="0.3" width="3.0" height="0.19"/></field>'
        '<field name="F_B" source="ROW_B">'
        '<geometryInfo x="4.0" y="0.3" width="3.0" height="0.19"/></field>'
        '</repeatingFrame></repeatingFrame>'
        '</body>'
        '<margin><text name="B_TTL">'
        '<geometryInfo x="2.5" y="0.2" width="3.0" height="0.25"/>'
        '<textSegment><font face="Arial" size="12" bold="yes"/>'
        '<string><![CDATA[Synthetic Title]]></string></textSegment></text>'
        '</margin>'
        '</section></layout></report>'
    ).encode()


def _marker_boxes(root, region):
    """Every textbox under ``region`` (a PageHeader/PageFooter/Body element)
    whose text is the marker."""
    out = []
    for tb in region.iter(_NS + "Textbox"):
        if any((v.text or "") == _MARKER for v in tb.iter(_NS + "Value")):
            out.append(tb)
    return out


def _geom(tb, tag):
    e = tb.find(_NS + tag)
    return float((e.text or "0in").replace("in", "")) if e is not None else None


def test_allbutfirstpage_marker_reaches_the_page_band_gated():
    """The continuation marker must exist in the RDL, live in the page band
    at its declared geometry, and be hidden on page one — with no second copy
    left in the body to print there anyway."""
    rdl = convert(_xml())["rdl_xml"]
    root = ET.fromstring(rdl)

    assert rdl.count(_MARKER) == 1, (
        "the declared marker must be emitted EXACTLY once — it was %d"
        % rdl.count(_MARKER))

    hdr = root.find(".//" + _NS + "PageHeader")
    body = root.find(".//" + _NS + "Body")
    assert hdr is not None
    band_copies = _marker_boxes(root, hdr)
    assert len(band_copies) == 1, (
        "the marker belongs in the page band (the only region where "
        "Globals!PageNumber is legal)")
    assert not _marker_boxes(root, body), (
        "a body copy would print on page one — the very page the declared "
        "rule suppresses")

    tb = band_copies[0]
    hidden = tb.find(".//" + _NS + "Hidden")
    assert hidden is not None and hidden.text == "=Globals!PageNumber = 1", (
        "allButFirstPage == hidden on page 1, visible on every later page; "
        "got %r" % (hidden.text if hidden is not None else None))

    # DECLARED geometry survives the move: a declared width is the width.
    assert abs(_geom(tb, "Left") - 3.06) < 0.001
    assert abs(_geom(tb, "Width") - 0.74) < 0.001
    assert abs(_geom(tb, "Height") - 0.17) < 0.001

    # …and the band is anchored to its FLOOR, i.e. as close to the body top
    # as the band reaches (Oracle prints the marker beside the caption that
    # restarts the page).
    band_h = float((hdr.find(_NS + "Height").text or "0in").replace("in", ""))
    assert abs(_geom(tb, "Top") - (band_h - _geom(tb, "Height"))) < 0.002


def test_page_gated_object_never_grows_the_band():
    """TopMargin + PageHeader height IS the declared body origin, so the
    marker may never make the band taller — that would push every body row
    down the page on EVERY page."""
    with_marker = ET.fromstring(convert(_xml())["rdl_xml"])
    without = ET.fromstring(convert(_xml(""))["rdl_xml"])

    def _band(root, tag):
        b = root.find(".//" + _NS + tag)
        return None if b is None else (b.find(_NS + "Height").text or "")

    assert _band(with_marker, "PageHeader") == _band(without, "PageHeader")
    assert _band(with_marker, "PageFooter") == _band(without, "PageFooter")
    for tag in ("TopMargin", "BottomMargin"):
        a = with_marker.find(".//" + _NS + tag)
        b = without.find(".//" + _NS + tag)
        assert (a is None) == (b is None)
        if a is not None:
            assert a.text == b.text, f"{tag} moved: {a.text} vs {b.text}"


def test_undeclared_object_is_not_page_gated():
    """PROVE-THE-GATE: strip the printObjectOnPage declaration and the very
    same text must NOT be hoisted or gated — the behaviour is driven by the
    declaration alone, never by the object's shape or position."""
    rdl = convert(_xml(""))["rdl_xml"]
    root = ET.fromstring(rdl)
    hdr = root.find(".//" + _NS + "PageHeader")
    assert hdr is None or not _marker_boxes(root, hdr)
    assert "Globals!PageNumber = 1" not in rdl
    assert "PgPrint_" not in rdl


def test_firstpage_objects_are_left_in_the_body_flow():
    """MEASURED DIALECT: ``firstPage`` means "the first page of the enclosing
    object", which is exactly what a body flow already does — 52 of 52
    declared firstPage texts across the export folder are present in the
    generated RDL today.  Hoisting them into a page band would move ordinary
    letter/invoice content out of the body, so the pass must ignore them."""
    rdl = convert(_xml('printObjectOnPage="firstPage" '))["rdl_xml"]
    root = ET.fromstring(rdl)
    hdr = root.find(".//" + _NS + "PageHeader")
    assert hdr is None or not _marker_boxes(root, hdr)
    assert "PgPrint_" not in rdl
    assert not re.search(r"<Hidden>=Globals!PageNumber", rdl)


def test_allbutlastpage_gets_the_complementary_expression():
    """The mirror-image declared value gates on the LAST page instead."""
    rdl = convert(_xml('printObjectOnPage="allButLastPage" '))["rdl_xml"]
    root = ET.fromstring(rdl)
    hdr = root.find(".//" + _NS + "PageHeader")
    assert hdr is not None
    tb = _marker_boxes(root, hdr)
    assert len(tb) == 1
    hidden = tb[0].find(".//" + _NS + "Hidden")
    assert hidden is not None
    assert hidden.text == "=Globals!PageNumber = Globals!TotalPages"


def test_page_gated_object_declared_low_in_the_body_uses_the_footer():
    """Band choice is the object's OWN declared position: one declared in the
    bottom half of the section body is footer furniture, not header."""
    xml = _xml().replace(b'<geometryInfo x="3.06" y="0.05" width="0.74" '
                         b'height="0.17"/>',
                         b'<geometryInfo x="3.06" y="8.90" width="0.74" '
                         b'height="0.17"/>')
    root = ET.fromstring(convert(xml)["rdl_xml"])
    ftr = root.find(".//" + _NS + "PageFooter")
    hdr = root.find(".//" + _NS + "PageHeader")
    assert ftr is not None and len(_marker_boxes(root, ftr)) == 1
    assert hdr is None or not _marker_boxes(root, hdr)
    tb = _marker_boxes(root, ftr)[0]
    # anchored to the footer's CEILING — the row just under the body
    assert abs(_geom(tb, "Top")) < 0.002
