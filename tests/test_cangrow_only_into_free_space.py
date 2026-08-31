"""A BOX MAY ONLY GROW INTO SPACE THE DECLARATION LEAVES EMPTY.

Two independent conditions grant a per-record box ``CanGrow=true``:

  * the source declares it elastic (``verticalElasticity`` expand/variable);
  * an AFM estimate says its STATIC text needs more lines than its declared
    box holds (put there because Oracle fits such a note in its fixed box
    while SSRS's GDI metrics need more room, so a fixed box clipped a
    multi-sentence note's last line).

The first was always gated on there being nothing declared below the box.
The second was not, and growth without that gate is growth onto a
neighbour's ink: SSRS wraps the tail INSIDE the report, so the extra lines
print exactly where the next declared object prints.

MEASURED (engine render of a character-mode statement, every page):
a rule of 105 declared dashes in its declared 8.45in box needed 9.0in at the
emitted size, so four dashes wrapped to a second line -- and the next object
is declared 0.0126in below the rule, so those dashes printed through the
detail row's values.  Paint gate: 192pt2 per page, plus two more wrapped
text-art rules landing in the box-border column beneath them (12 pairs over
3 pages).  With the gate applied the same render measures 3 pairs, all three
of them source-DECLARED overlaps (a border glyph declared inside a
full-width rule's own box, identical y band).

Trade, stated honestly: where something IS declared below, the declared box
wins and the engine clips inside it.  That is the fixed box Oracle prints
the line into, and it is never ink on the neighbour.  Measured over 351
corpus sources: 10 RDLs change, 9 of them render byte-identical TEXT (same
extracted-text md5, page count, blank set and paint count) -- i.e. nothing
that box ever actually grew -- and the tenth is the statement above.

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


def _q(tag: str) -> str:
    return NS + tag


def _num(el, tag):
    c = el.find(_q(tag))
    if c is None or not (c.text or "").strip():
        return None
    try:
        return float(c.text.replace("in", "").strip())
    except ValueError:
        return None


def _text_of(el) -> str:
    return "".join(v.text or "" for v in el.iter(_q("Value")))


def _box_with(rdl: str, needle: str):
    """The emitted textbox carrying ``needle`` (there is exactly one)."""
    root = ET.fromstring(rdl.encode("utf-8"))
    hits = [el for el in root.iter(_q("Textbox")) if needle in _text_of(el)]
    assert len(hits) == 1, (
        f"fixture expects exactly one box carrying {needle!r}, got "
        f"{len(hits)}")
    return hits[0]


def _cangrow(el) -> str:
    c = el.find(_q("CanGrow"))
    return (c.text or "").strip().lower() if c is not None else ""


# ---------------------------------------------------------------------------
# The fixture declares the SAME overflowing text twice, in two sibling
# frames, at the SAME box geometry.  The only difference is what the source
# declares underneath it.
# ---------------------------------------------------------------------------

# long enough to trip the >=60-char static-overflow estimate, and far too
# long for the declared 1.50in x 0.20in box at 10pt
_LONG = ("Crowded paragraph token " * 9).strip()
_LONG_FREE = ("Roomy paragraph token " * 9).strip()
_BOX_W = 1.50000
_BOX_H = 0.20000

_XML = (
    '<?xml version="1.0"?><report name="GROWSPACE_T" DTDVersion="9.0.2.0.10">'
    '<data><dataSource name="Q_1">'
    '<select><![CDATA[SELECT ALPHA FROM T]]></select>'
    '<group name="G_1"><dataItem name="ALPHA" datatype="vchar2"/></group>'
    '</dataSource></data><layout>'
    '<section name="main" width="8.50000" height="11.00000">'
    '<body width="8.50000" height="9.50000"><location x="0.0" y="0.0"/>'
    '<frame name="M_1"><geometryInfo x="0" y="0" width="8.5" height="6.0"/>'
    '<repeatingFrame name="R_1" source="G_1" printDirection="down">'
    '<geometryInfo x="0" y="0" width="8.5" height="5.8"/>'
    # --- frame A: the overflowing text with a declared object BELOW it
    '<frame name="M_A">'
    '<geometryInfo x="0" y="0.00000" width="8.5" height="1.00000"/>'
    '<text name="B_CROWDED"><textSettings justify="start"/>'
    f'<geometryInfo x="0.10000" y="0.20000" width="{_BOX_W:.5f}"'
    f' height="{_BOX_H:.5f}"/>'
    '<textSegment><font face="Arial" size="10"/>'
    f'<string><![CDATA[{_LONG}]]></string></textSegment></text>'
    '<text name="B_UNDER"><textSettings justify="start"/>'
    '<geometryInfo x="0.10000" y="0.42000" width="1.50000" height="0.18000"/>'
    '<textSegment><font face="Arial" size="10"/>'
    '<string><![CDATA[Under Marker Text]]></string></textSegment></text>'
    '</frame>'
    # --- frame B: the SAME text and box, with nothing declared below it
    '<frame name="M_B">'
    '<geometryInfo x="0" y="2.00000" width="8.5" height="2.00000"/>'
    '<text name="B_FREE"><textSettings justify="start"/>'
    f'<geometryInfo x="0.10000" y="2.20000" width="{_BOX_W:.5f}"'
    f' height="{_BOX_H:.5f}"/>'
    '<textSegment><font face="Arial" size="10"/>'
    f'<string><![CDATA[{_LONG_FREE}]]></string></textSegment></text>'
    '</frame>'
    # --- frame C: a DECLARED-ELASTIC field with a declared object below it
    '<frame name="M_C">'
    '<geometryInfo x="0" y="4.00000" width="8.5" height="1.50000"/>'
    '<field name="F_A" source="ALPHA" alignment="start">'
    '<font face="Arial" size="10"/>'
    '<geometryInfo x="0.10000" y="4.20000" width="1.50000" height="0.20000"/>'
    '<generalLayout verticalElasticity="variable"/></field>'
    '<text name="B_UNDER_2"><textSettings justify="start"/>'
    '<geometryInfo x="0.10000" y="4.42000" width="1.50000" height="0.18000"/>'
    '<textSegment><font face="Arial" size="10"/>'
    '<string><![CDATA[Second Under Marker]]></string></textSegment></text>'
    '</frame>'
    '</repeatingFrame></frame></body></section></layout></report>'
).encode()


@pytest.fixture(scope="module")
def rdl():
    return convert(_XML)["rdl_xml"]


def test_overflowing_text_with_something_below_stays_fixed(rdl):
    """THE DEFECT: the overflow grant ignored what the declaration puts
    beneath the box, so the wrapped tail printed on the neighbour."""
    tb = _box_with(rdl, "Crowded paragraph token")
    assert _cangrow(tb) == "false", (
        "a box with a declared object beneath it may not grow — its wrapped "
        "tail prints exactly where that object prints")
    assert _num(tb, "Height") == pytest.approx(_BOX_H, abs=1e-6), (
        "the declared height is the height", _num(tb, "Height"))


def test_the_same_text_still_grows_when_nothing_is_declared_below(rdl):
    """PROVE-THE-GATE: the overflow grant is not removed, only gated. The
    identical text in the identical box, with free space beneath it, still
    gets CanGrow — the clipped-note defect it was added for stays fixed."""
    tb = _box_with(rdl, "Roomy paragraph token")
    assert _cangrow(tb) == "true", (
        "growth into space the declaration leaves empty is still granted")


def test_declared_elastic_field_with_something_below_stays_fixed(rdl):
    """The elasticity grant carried this gate from the start; the two grants
    must agree, or the rule depends on which one happened to fire."""
    root = ET.fromstring(rdl.encode("utf-8"))
    hits = [el for el in root.iter(_q("Textbox"))
            if "ALPHA" in _text_of(el)]
    assert hits, "fixture must emit the elastic field"
    for tb in hits:
        assert _cangrow(tb) == "false", (
            "a declared-elastic box with a declared object beneath it does "
            "not grow either", tb.get("Name"))


def test_the_object_below_keeps_its_declared_top(rdl):
    """The neighbour is what the gate protects: it stays at its declared
    offset, so 'no growth' means the two boxes still print where the source
    declares them."""
    crowded = _box_with(rdl, "Crowded paragraph token")
    under = _box_with(rdl, "Under Marker Text")
    ct, ch = _num(crowded, "Top"), _num(crowded, "Height")
    ut = _num(under, "Top")
    assert ut is not None and ct is not None
    assert ut + 1e-6 >= ct + ch, (
        "the declaration puts the marker below the crowded box and the "
        "emission must keep it there", ct, ch, ut)
