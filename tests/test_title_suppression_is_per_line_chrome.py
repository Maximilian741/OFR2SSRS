"""A BODY TEXT IS ONLY A "TITLE DUPLICATE" IF THE BAND REALLY PRINTS IT.

The per-record builder suppresses body texts that duplicate the page title,
so a declared letterhead is not printed under the record it heads.  Which
lines the page title actually carries depends on how the header band gets
filled, and there are exactly two ways:

  * the source DECLARED header chrome (``_margin_page_chrome`` returns header
    objects) -- those objects ARE the band, and the synthesized centered
    title is never built;
  * no declared header chrome -- the synthesized title IS the band, and it
    carries the y-ranked picked lines verbatim.

The suppression was armed by a coarser question ("does this source declare a
margin band at all?"), which conflates the two.  A source with DECLARED
header chrome had its picked lines dropped from the body even though the
title that would have printed them was never built.

MEASURED on a wild indexed listing whose three column captions are declared
inside the outer repeating frame: the generated RDL contained 'Last Name' 0x,
'First Name' 0x, 'Phone Number' 0x, 'Email' 1x (saved only by the picker's
limit of 3), at verdict READY -- silent content loss.  After the fix all four
appear exactly once and the engine render carries 48 words where it carried
30.

The other half of the rule is load-bearing too, and cost a round to find: a
letter fixture whose header band IS the synthesized title must keep
suppressing, or the letterhead prints in the body, the record outgrows its
sheet, and every second page comes out strict-blank (measured: blank pages
[2, 4, 6] of 6).  Both directions are pinned below.

Corpus A/B over 351 sources: 3 RDLs change, 0 verdict deltas.  Two gain
declared text (48 vs 30 words, 36 vs 21 words) and one renders
byte-identical text.  The listing's restored caption row is grazed by the
20pt index letter declared directly above it -- SOURCE-DECLARED: the letter
is declared with font size 20 in a 0.1875in-tall box whose caption row is
declared 0.1875in below, and a 20pt line's ink measures 27.5pt.  Proved by
counterfactual: rewriting that box to its exact declared height renders the
identical ink and the identical pair count, so the collision is the
declaration's, not the emitter's.

Everything here is synthetic: no report, column or label from any real
source appears in this file.
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert                       # noqa: E402

NS = ("{http://schemas.microsoft.com/sqlserver/reporting/"
      "2008/01/reportdefinition}")

_MARGIN_LINE = "Synthetic Page Stamp"
_LOOSE_LINE = "Bureau Of Synthetic Records"
_BAND_CAPS = ("Alpha Column", "Beta Column", "Gamma Column")


def _xml(loose: bool = False, header_chrome: bool = True) -> bytes:
    """A one-record-per-page listing that declares page chrome (a <margin>
    stamp) AND, inside its outer repeating frame, a caption row above the
    detail band.

    With ``loose=False`` the chrome sits low on the sheet, so it is not a
    title candidate at all and the y-ranked picker falls back to the in-frame
    captions -- the shape that lost them.  With ``loose=True`` a text is
    authored directly on the section (the other chrome dialect) and wins the
    pick, which is the case the suppression exists for."""
    caps = "".join(
        '<text name="B_C%d"><textSettings spacing="single"/>'
        '<geometryInfo x="%.4f" y="0.30000" width="1.40000" '
        'height="0.18750"/>'
        '<textSegment><font face="Arial" size="10" bold="yes"/>'
        '<string><![CDATA[%s]]></string></textSegment></text>'
        % (i, 0.05 + 1.5 * i, cap)
        for i, cap in enumerate(_BAND_CAPS))
    loose_txt = (
        '<text name="B_LOOSE"><textSettings spacing="single"/>'
        '<geometryInfo x="2.00000" y="0.10000" width="4.00000"'
        ' height="0.20000"/>'
        '<textSegment><font face="Arial" size="12" bold="yes"/>'
        f'<string><![CDATA[{_LOOSE_LINE}]]></string></textSegment></text>'
    ) if loose else ""
    return (
        '<?xml version="1.0"?><report name="TITLESUP_T" '
        'DTDVersion="9.0.2.0.10">'
        '<data><dataSource name="Q_1">'
        '<select><![CDATA[SELECT KEY_COL, ALPHA, BETA, GAMMA FROM T]]>'
        '</select>'
        '<group name="G_KEY"><dataItem name="KEY_COL" datatype="vchar2"/>'
        '</group>'
        '<group name="G_ROW"><dataItem name="ALPHA" datatype="vchar2"/>'
        '<dataItem name="BETA" datatype="vchar2"/>'
        '<dataItem name="GAMMA" datatype="vchar2"/></group>'
        '</dataSource></data><layout>'
        '<section name="main" width="8.50000" height="11.00000">'
        '<body width="8.50000" height="9.50000"><location x="0.0" y="0.0"/>'
        + loose_txt +
        '<repeatingFrame name="R_KEY" source="G_KEY" printDirection="down"'
        ' maxRecordsPerPage="1">'
        '<geometryInfo x="0" y="0" width="6.5" height="1.2"/>'
        '<field name="F_KEY" source="KEY_COL" alignment="start">'
        '<font face="Arial" size="12" bold="yes"/>'
        '<geometryInfo x="0.05000" y="0.05000" width="4.00000"'
        ' height="0.20000"/></field>'
        + caps +
        '<repeatingFrame name="R_ROW" source="G_ROW" printDirection="down">'
        '<geometryInfo x="0.05" y="0.55" width="6.4" height="0.25"/>'
        '<field name="F_A" source="ALPHA" alignment="start">'
        '<font face="Arial" size="10"/>'
        '<geometryInfo x="0.05000" y="0.55000" width="1.40000"'
        ' height="0.18750"/></field>'
        '<field name="F_B" source="BETA" alignment="start">'
        '<font face="Arial" size="10"/>'
        '<geometryInfo x="1.55000" y="0.55000" width="1.40000"'
        ' height="0.18750"/></field>'
        '<field name="F_G" source="GAMMA" alignment="start">'
        '<font face="Arial" size="10"/>'
        '<geometryInfo x="3.05000" y="0.55000" width="1.40000"'
        ' height="0.18750"/></field>'
        '</repeatingFrame></repeatingFrame></body>'
        '<margin><text name="B_STAMP">'
        + ('<geometryInfo x="7.20000" y="0.15000" width="1.10000"'
           ' height="0.25000"/>' if header_chrome else
           '<geometryInfo x="7.20000" y="10.40000" width="1.10000"'
           ' height="0.25000"/>')
        + '<textSegment><font face="Arial" size="8"/>'
        f'<string><![CDATA[{_MARGIN_LINE}]]></string></textSegment></text>'
        '</margin>'
        '</section></layout></report>'
    ).encode()


def _counts(rdl: str, needle: str) -> int:
    return rdl.count(needle)


def _body_texts(rdl: str) -> str:
    """Everything the BODY prints. The captions are declared in the body, so
    that is where they have to survive — a copy that only exists inside a
    synthesized page title is not the declared caption row."""
    root = ET.fromstring(rdl.encode("utf-8"))
    body = root.find(".//" + NS + "Body")
    if body is None:
        return ""
    return " ".join("".join(v.text or "" for v in tb.iter(NS + "Value"))
                    for tb in body.iter(NS + "Textbox"))


@pytest.fixture(scope="module")
def rdl():
    return convert(_xml())["rdl_xml"]


def test_captions_declared_inside_a_frame_survive(rdl):
    """THE DEFECT: in-frame captions scavenged as title lines were dropped
    from the body and printed nowhere."""
    body = _body_texts(rdl)
    missing = [c for c in _BAND_CAPS if c not in body]
    assert not missing, (
        "every caption declared in the body must print in the body",
        missing, body)


def test_with_no_declared_header_chrome_the_title_still_owns_those_lines():
    """PROVE-THE-GATE: move the declared stamp to the foot of the sheet and
    the header band has no declared chrome, so the SYNTHESIZED title is what
    prints the picked lines — and the body copies must give way to it, or the
    letterhead prints twice. The rule swings entirely on what the band
    carries, which is the only thing that decides where the lines print."""
    rdl = convert(_xml(header_chrome=False))["rdl_xml"]
    body = _body_texts(rdl)
    dupes = [c for c in _BAND_CAPS if c in body]
    assert not dupes, (
        "with the synthesized title carrying them, the body copies go",
        dupes, body)
    root = ET.fromstring(rdl.encode("utf-8"))
    hdr = root.find(".//" + NS + "PageHeader")
    assert hdr is not None
    printed = " ".join("".join(v.text or "" for v in tb.iter(NS + "Value"))
                       for tb in hdr.iter(NS + "Textbox"))
    assert all(c in printed for c in _BAND_CAPS), (
        "…and the band is where they print instead", printed)


def test_chrome_membership_is_what_the_declared_band_prints():
    """The classifier the fix rests on, exercised directly: the set is built
    from the objects the page builder hands the header band, so it contains
    the declared stamp and none of the in-frame captions."""
    from converter.generators import rdl as _rdl
    from converter.parsers.oracle_xml import parse_oracle_xml

    report = parse_oracle_xml(_xml(loose=True))
    hdr, _ftr = _rdl._margin_page_chrome(report,
                                         _rdl._page_height_for(report))
    assert hdr, "the fixture must declare header chrome"
    lines = _rdl._declared_chrome_text_lines(hdr)
    assert _MARGIN_LINE.lower() in lines, (
        "the declared stamp is what the band prints", sorted(lines))
    assert _LOOSE_LINE.lower() not in lines, (
        "a text the band does not carry is not a chrome duplicate",
        sorted(lines))
    for cap in _BAND_CAPS:
        assert cap.lower() not in lines, (cap, sorted(lines))
