"""Oracle field alignment -> SSRS <TextAlign> (1:1 fidelity).

Oracle Reports fields carry an explicit ``alignment`` (start/end/center). The
converter dropped it, so SSRS fell back to 'General' (numbers/dates right, text
left). That silently mismatches whenever the author EXPLICITLY chose an
alignment -- most visibly a CENTERED header/label, which 'General' renders
left. These lock that explicit alignment now maps through, while an UNSET
alignment still defers to 'General' (which matches Oracle's own default).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402
from converter.generators.rdl import _ssrs_text_align  # noqa: E402


@pytest.mark.parametrize("align,want", [
    ("end", "Right"), ("right", "Right"),
    ("center", "Center"), ("centre", "Center"),
    ("start", "Left"), ("left", "Left"),
    ("", None), (None, None), ("bogus", None),
])
def test_ssrs_text_align_mapping(align, want):
    assert _ssrs_text_align(align) == want


def _tabular(fields_xml: str) -> str:
    xml = (
        '<report name="AL" DTDVersion="9.0.2.0.10"><data>'
        '<dataSource name="Q_1"><select><![CDATA[SELECT amt,nm,ctr FROM t]]></select>'
        '<group name="G"><dataItem name="AMT" datatype="number"/>'
        '<dataItem name="NM" datatype="vchar2"/>'
        '<dataItem name="CTR" datatype="vchar2"/></group></dataSource></data>'
        '<layout><section name="main"><body width="8" height="9">'
        '<repeatingFrame name="R" source="G">'
        '<geometryInfo x="0" y="0" width="6" height="0.3"/>'
        f'{fields_xml}</repeatingFrame></body></section></layout></report>'
    ).encode()
    return convert(xml)["rdl_xml"]


def _cell_align(rdl: str, col: str):
    m = re.search(
        rf'<Textbox[^>]*Name="Cell_{col}"[^>]*>(.*?)</Textbox>', rdl, re.S)
    assert m, f"no Cell_{col} textbox"
    ta = re.search(r"<TextAlign>([^<]*)</TextAlign>", m.group(1))
    return ta.group(1) if ta else None


def test_explicit_alignment_maps_to_textalign():
    rdl = _tabular(
        '<field name="F1" source="AMT" alignment="end">'
        '<geometryInfo x="0" y="0" width="2" height="0.2"/></field>'
        '<field name="F2" source="NM">'  # no alignment -> General (None)
        '<geometryInfo x="2" y="0" width="2" height="0.2"/></field>'
        '<field name="F3" source="CTR" alignment="center">'
        '<geometryInfo x="4" y="0" width="2" height="0.2"/></field>')
    assert _cell_align(rdl, "AMT") == "Right"
    assert _cell_align(rdl, "NM") is None      # defers to SSRS General
    assert _cell_align(rdl, "CTR") == "Center"


def _cell_font(rdl: str, col: str):
    m = re.search(
        rf'<Textbox[^>]*Name="Cell_{col}"[^>]*>(.*?)</Textbox>', rdl, re.S)
    assert m, f"no Cell_{col} textbox"
    body = m.group(1)
    g = lambda tag: (re.search(rf"<{tag}>([^<]*)</{tag}>", body) or [None, None])[1]
    return {"family": g("FontFamily"), "size": g("FontSize"),
            "weight": g("FontWeight"), "style": g("FontStyle")}


def test_field_font_carries_into_data_cell():
    """Oracle <font face size bold italic> was parsed but never emitted on the
    data cell -- every cell fell back to default 10pt Arial. Regression: the
    face (esp. Courier New for fixed-width numerics), size, weight and style
    must reach the cell so the table renders in the original typeface."""
    rdl = _tabular(
        '<field name="F1" source="AMT">'
        '<font face="Courier New" size="12"/>'
        '<geometryInfo x="0" y="0" width="2" height="0.2"/></field>'
        '<field name="F2" source="NM">'
        '<font face="Times New Roman" size="11" bold="yes" italic="yes"/>'
        '<geometryInfo x="2" y="0" width="2" height="0.2"/></field>'
        '<field name="F3" source="CTR">'  # no font -> default
        '<geometryInfo x="4" y="0" width="2" height="0.2"/></field>')
    amt = _cell_font(rdl, "AMT")
    assert amt["family"] == "Courier New" and amt["size"] == "12pt"
    nm = _cell_font(rdl, "NM")
    assert nm["family"] == "Times New Roman" and nm["size"] == "11pt"
    assert nm["weight"] == "Bold" and nm["style"] == "Italic"
    ctr = _cell_font(rdl, "CTR")
    assert ctr["family"] is None       # no explicit face -> SSRS default


def test_per_record_field_font_carries_through():
    """The positional per-record/document builder (Tb_Rec_*) also dropped the
    font face -- a letter's Times New Roman body or Courier address block fell
    back to Arial. Lock that face + style now reach those textboxes too."""
    xml = (
        '<report name="LT" DTDVersion="9.0.2.0.10"><data>'
        '<dataSource name="Q_1"><select><![CDATA[SELECT recipient,addr FROM t]]></select>'
        '<group name="G"><dataItem name="RECIPIENT" datatype="vchar2"/>'
        '<dataItem name="ADDR" datatype="vchar2"/></group></dataSource></data>'
        '<layout><section name="main"><body width="8.5" height="11">'
        '<field name="F1" source="RECIPIENT">'
        '<font face="Times New Roman" size="14" italic="yes"/>'
        '<geometryInfo x="1.0" y="1.0" width="5" height="0.3"/></field>'
        '<field name="F2" source="ADDR"><font face="Courier New" size="10"/>'
        '<geometryInfo x="1.0" y="1.5" width="5" height="0.3"/></field>'
        '</body></section></layout></report>'
    ).encode()
    rdl = convert(xml)["rdl_xml"]
    fams = re.findall(r"<FontFamily>([^<]*)</FontFamily>", rdl)
    assert "Times New Roman" in fams
    assert "Courier New" in fams
    # the italic recipient line carries FontStyle=Italic
    assert "<FontStyle>Italic</FontStyle>" in rdl
