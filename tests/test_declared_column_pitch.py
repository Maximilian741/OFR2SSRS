"""Tabular columns carry their DECLARED geometry, at full precision.

A tablix column is a SLOT, and the engine puts a cell's left edge at the
running SUM of the slots before it. Two things therefore have to be true or
every column after the first prints left of where the source declares it:

* the slot must be the declared PITCH (this box's left edge to the next
  one's), not the declared box width -- packing the boxes edge to edge
  swallows every declared inter-column gutter;
* nothing may be quantised. A declared width is the width (settled dialect
  rule), and 2dp rounding alone is worth real drift.

Both were measured on a landscape summary report whose truth PDF was
available. Emitted widths were rounded to 2dp AND packed edge to edge, and
the cumulative column lefts came out (declared -> emitted, in points):

    Address  -0.74   City  -1.64   State  -3.00   Zip  +0.76
    Phone    -2.13   ExpDate -8.80  Type -11.09   Name -11.84

...against a truth PDF that starts every one of those values exactly on
its declared x (measured on the rendered PDFs: Address 189.43 -> 190.15pt
against truth 190.12, City 403.27 -> 404.93 against 404.88, Phone
638.71 -> 640.87 against 640.80).

The same measurement disproves the old 0.5in "legibility floor": that
report declares a 0.4375in column and the truth centres its value on the
0.4375in box (ink centre 570.46pt, declared box centre 570.31pt). Floored
to 0.5in the centre would have moved +2.25pt and every column after it
with it. The floor is honest only where the declared set does NOT fit the
printable width and the widths are being scaled anyway.

No customer data: the fixture below is synthetic.
"""
from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402

NS = ("{http://schemas.microsoft.com/sqlserver/reporting/2008/01/"
      "reportdefinition}")

# Four declared columns with real inter-column GUTTERS. Column C is
# narrower than the old 0.5in floor and is declared CENTRE-anchored, so it
# exercises the floor and the gutter reserve at once.
_X = [0.02075, 2.20000, 5.30000, 5.75000]
_W = [2.09375, 2.97180, 0.43750, 0.78955]
_ALIGN = ["start", "start", "center", "start"]
_NAMES = ["ALPHA", "BRAVO", "CHARLIE", "DELTA"]
_PITCH = [_X[1] - _X[0], _X[2] - _X[1], _X[3] - _X[2], _W[3]]


def _source() -> bytes:
    caps = "".join(
        f'<text name="B_{n}"><textSettings justify="start"/>'
        f'<geometryInfo x="{_X[i]:.5f}" y="0.10000" width="{_W[i]:.5f}" '
        f'height="0.19000"/><textSegment><font face="Arial" size="9"/>'
        f'<string><![CDATA[Cap {n}]]></string></textSegment></text>'
        for i, n in enumerate(_NAMES))
    flds = "".join(
        f'<field name="F_{n}" source="{n}" alignment="{_ALIGN[i]}">'
        f'<font face="Arial" size="8"/>'
        f'<geometryInfo x="{_X[i]:.5f}" y="0.35000" width="{_W[i]:.5f}" '
        f'height="0.19000"/></field>'
        for i, n in enumerate(_NAMES))
    items = "".join(f'<dataItem name="{n}" datatype="vchar2"/>'
                    for n in _NAMES)
    return (
        '<?xml version="1.0"?><report name="PITCH_T" DTDVersion="9.0.2.0.10">'
        '<data><dataSource name="Q_1"><select><![CDATA[SELECT '
        + ",".join(_NAMES) + ' FROM T]]></select>'
        '<group name="G_1">' + items + '</group></dataSource></data><layout>'
        '<section name="main" width="8.50000" height="11.00000">'
        '<body width="7.50000" height="9.50000"><location x="0.5" y="0.5"/>'
        '<frame name="M_1"><geometryInfo x="0" y="0" width="7.5" '
        'height="1.2"/>' + caps +
        '<repeatingFrame name="R_1" source="G_1" printDirection="down">'
        '<geometryInfo x="0" y="0.35000" width="7.5" height="0.22000"/>'
        + flds +
        '</repeatingFrame></frame></body></section></layout></report>'
    ).encode()


def _rdl() -> str:
    return convert(_source())["rdl_xml"]


def _widths(rdl: str):
    return [float(w) for w in re.findall(
        r"<TablixColumn>\s*<Width>([\d.]+)in</Width>", rdl)]


def test_column_widths_are_the_declared_pitch_at_full_precision():
    got = _widths(_rdl())
    assert len(got) == len(_PITCH), got
    for i, (g, want) in enumerate(zip(got, _PITCH)):
        assert abs(g - want) < 0.0005, (i, g, want, got)


def test_cumulative_column_lefts_land_on_the_declared_x():
    """The whole point of the pitch: cell i starts at declared x_i."""
    got = _widths(_rdl())
    cum = 0.0
    for i in range(len(_X)):
        drift_pt = (cum - (_X[i] - _X[0])) * 72.0
        assert abs(drift_pt) < 0.05, (
            f"column {i} starts {drift_pt:+.2f}pt off its declared x",
            cum, _X[i] - _X[0])
        cum += got[i]


def test_no_two_decimal_quantisation_of_a_declared_width():
    """A declared 2.09375in pitch must not collapse to 2.09in: the rounding
    is what accumulated into pointwise drift across the row."""
    got = _widths(_rdl())
    assert any(abs(g - round(g, 2)) > 0.0005 for g in got), (
        "every emitted column width is a clean 2dp value -- the declaration "
        "is being quantised", got)


def test_a_declared_column_narrower_than_the_old_floor_stays_narrow():
    got = _widths(_rdl())
    assert got[2] < 0.5, (
        "a declared 0.4375in column must not be propped up to a 0.5in "
        "legibility floor when the declared set fits the page", got)


def test_the_gutter_is_reserved_only_where_it_moves_the_ink():
    """A CENTRE-anchored value centres on its DECLARED box, so the slot's
    surplus over that box rides as a right inset. A LEFT-anchored value is
    already on its declared x, so it gets no inset at all (an Oracle box
    carries none)."""
    root = ET.fromstring(_rdl().encode("utf-8"))
    pads = {}
    for tb in root.iter(NS + "Textbox"):
        nm = tb.get("Name") or ""
        if not nm.startswith("Cell_"):
            continue
        st = tb.find(NS + "Style")
        pads[nm] = (st.findtext(NS + "PaddingLeft"),
                    st.findtext(NS + "PaddingRight"))
    assert pads, "no data cells emitted"
    for nm, (pl, pr) in pads.items():
        assert (pl or "").strip() in ("0pt", "0in"), (nm, pl)
    for n in ("ALPHA", "BRAVO", "DELTA"):
        pr = (pads[f"Cell_{n}"][1] or "").strip()
        assert pr in ("0pt", "0in"), (
            f"Cell_{n} is left-anchored: no inset may be invented", pr)
    pr = (pads["Cell_CHARLIE"][1] or "").strip()
    assert pr.endswith("pt"), pr
    want = (_PITCH[2] - _W[2]) * 72.0
    assert abs(float(pr[:-2]) - want) < 0.01, (
        "the centred column must reserve exactly its DECLARED gutter so the "
        "value centres on the declared box, not on the slot", pr, want)


def test_a_declared_rule_keeps_full_precision_geometry():
    """Same rule, other emitter: a declared <line>'s left/length are not
    2dp quantities either (2dp threw away up to 0.36pt of each)."""
    import xml.etree.ElementTree as _ET

    from converter.generators import rdl as R

    parent = _ET.Element("ReportItems")
    R._emit_rule_line(parent, "Probe", top=0.30000, left=0.02075,
                      width=7.19375, color="#CCCCCC")
    body = _ET.tostring(parent, encoding="unicode")
    left = float(re.search(r"<Left>([\d.]+)in</Left>", body).group(1))
    width = float(re.search(r"<Width>([\d.]+)in</Width>", body).group(1))
    assert abs(left - 0.02075) < 0.0005, (left, body)
    assert abs(width - 7.19375) < 0.0005, (width, body)
