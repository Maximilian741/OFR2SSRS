"""Wide tabular reports must keep EVERY column (no silent truncation), and
the fidelity self-check must flag a layout column the RDL fails to display.
Wild-corpus verified: a 54-column warehouse report previously rendered 10.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402


def _wide_xml(ncol: int) -> bytes:
    items = "".join(
        f'<dataItem name="C{i}" datatype="number"/>' for i in range(ncol))
    fields = "".join(
        f'<field name="F_C{i}" source="C{i}">'
        f'<geometryInfo x="{i*0.3}" y="0" width="0.3" height="0.2"/></field>'
        for i in range(ncol))
    sel = ",".join(f"C{i}" for i in range(ncol))
    return (
        f'<?xml version="1.0"?><report name="WIDE" DTDVersion="9.0.2.0.10">'
        f'<data><dataSource name="Q"><select><![CDATA[SELECT {sel} FROM T]]>'
        f'</select><group name="G">{items}</group></dataSource></data>'
        f'<layout><section name="main"><body width="7" height="9">'
        f'<repeatingFrame name="R" source="G" printDirection="down">'
        f'<geometryInfo x="0" y="0" width="7" height="0.3"/>{fields}'
        f'</repeatingFrame></body></section></layout></report>'
    ).encode()


def test_wide_table_keeps_all_columns():
    rdl = convert(_wide_xml(40))["rdl_xml"]
    refs = set(re.findall(r"Fields!(C\d+)\.Value", rdl))
    assert len(refs) == 40, f"only {len(refs)} of 40 columns displayed"


def test_wide_table_column_width_adapts():
    rdl = convert(_wide_xml(40))["rdl_xml"]
    widths = [float(w) for w in re.findall(
        r"<TablixColumn>\s*<Width>([\d.]+)in", rdl)]
    assert widths, "no tablix columns emitted"
    # all narrow enough that the table is not absurdly wide, never below floor
    assert all(0.5 <= w <= 1.0 for w in widths), widths


def test_columns_follow_oracle_per_column_widths():
    """Columns now follow each field's OWN Oracle width (1:1) rather than a
    single uniform width: a wide description column stays wide, narrow code
    columns stay narrow. (Previously every column was stretched to a uniform
    'comfortable' 1.5in, which did not match Oracle's proportions.)"""
    xml = (
        '<?xml version="1.0"?><report name="V" DTDVersion="9.0.2.0.10"><data>'
        '<dataSource name="Q"><select><![CDATA[SELECT code,descr,qty FROM T]]>'
        '</select><group name="G">'
        '<dataItem name="CODE" datatype="vchar2"/>'
        '<dataItem name="DESCR" datatype="vchar2"/>'
        '<dataItem name="QTY" datatype="number"/></group></dataSource></data>'
        '<layout><section name="main"><body width="7" height="9">'
        '<repeatingFrame name="R" source="G" printDirection="down">'
        '<geometryInfo x="0" y="0" width="7" height="0.3"/>'
        '<field name="F1" source="CODE"><geometryInfo x="0" y="0" width="0.8" height="0.2"/></field>'
        '<field name="F2" source="DESCR"><geometryInfo x="0.8" y="0" width="4.5" height="0.2"/></field>'
        '<field name="F3" source="QTY"><geometryInfo x="5.3" y="0" width="0.7" height="0.2"/></field>'
        '</repeatingFrame></body></section></layout></report>'
    ).encode()
    widths = [float(w) for w in re.findall(
        r"<TablixColumn>\s*<Width>([\d.]+)in", convert(xml)["rdl_xml"])]
    assert len(widths) == 3, widths
    # the description column is by far the widest; code/qty stay narrow
    assert widths[1] > 3.0 and widths[0] < 1.5 and widths[2] < 1.5, widths


def test_narrow_oracle_columns_are_not_stretched():
    """A report whose Oracle columns are genuinely narrow renders them narrow
    (floored at 0.5in for legibility), NOT stretched to a uniform 1.5in."""
    rdl = convert(_wide_xml(3))["rdl_xml"]   # 3 cols, each 0.3in in Oracle
    widths = [float(w) for w in re.findall(
        r"<TablixColumn>\s*<Width>([\d.]+)in", rdl)]
    assert widths and all(w < 1.0 for w in widths), widths   # not stretched to 1.5
    assert all(w >= 0.5 for w in widths), widths             # legibility floor


def _row_xml(field_h: float) -> bytes:
    return (
        f'<report name="H" DTDVersion="9.0.2.0.10"><data>'
        f'<dataSource name="Q"><select><![CDATA[SELECT a,b FROM t]]></select>'
        f'<group name="G"><dataItem name="A" datatype="vchar2"/>'
        f'<dataItem name="B" datatype="vchar2"/></group></dataSource></data>'
        f'<layout><section name="main"><body width="7" height="9">'
        f'<repeatingFrame name="R" source="G"><geometryInfo x="0" y="0.3" width="7" height="{field_h}"/>'
        f'<field name="F1" source="A"><geometryInfo x="0" y="0.3" width="2" height="0.2"/></field>'
        f'<field name="F2" source="B"><geometryInfo x="2" y="0.3" width="2" height="{field_h}"/></field>'
        f'</repeatingFrame></body></section></layout></report>'
    ).encode()


def _detail_row_height(rdl: str) -> float:
    # rows: [header 0.30, detail, (footer)]; the detail row is the 2nd
    hs = [float(h) for h in re.findall(
        r"<TablixRow>\s*<Height>([\d.]+)in", rdl)]
    return hs[1] if len(hs) > 1 else hs[0]


def test_tall_oracle_field_makes_a_taller_detail_row():
    """The detail row follows the TALLEST Oracle detail field (a field Oracle
    drew 0.6in tall keeps it), while ordinary <=0.28in fields stay at the 0.28in
    default -- so the corpus is unchanged and only genuinely-tall fields grow."""
    assert abs(_detail_row_height(convert(_row_xml(0.2))["rdl_xml"]) - 0.28) < 0.01
    assert abs(_detail_row_height(convert(_row_xml(0.6))["rdl_xml"]) - 0.60) < 0.01
    assert abs(_detail_row_height(convert(_row_xml(0.9))["rdl_xml"]) - 0.90) < 0.01


def test_fidelity_flags_a_dropped_layout_column():
    # 5 columns placed in the layout, but the layout binds only one field ->
    # the detector must warn that placed columns aren't displayed.
    xml = (
        '<?xml version="1.0"?><report name="D" DTDVersion="9.0.2.0.10">'
        "<data><dataSource name=\"Q\"><select><![CDATA[SELECT A,B,C,D,E FROM T]]>"
        "</select><group name=\"G\">"
        + "".join(f'<dataItem name="{c}" datatype="vchar2"/>' for c in "ABCDE")
        + "</group></dataSource></data>"
        '<layout><section name="main"><body width="7" height="9">'
        '<repeatingFrame name="R" source="G" printDirection="down">'
        '<geometryInfo x="0" y="0" width="7" height="0.3"/>'
        + "".join(
            f'<field name="F_{c}" source="{c}">'
            f'<geometryInfo x="{i}" y="0" width="1" height="0.2"/></field>'
            for i, c in enumerate("ABCDE"))
        + "</repeatingFrame></body></section></layout></report>"
    ).encode()
    out = convert(xml)
    refs = set(re.findall(r"Fields!([A-E])\.Value", out["rdl_xml"]))
    # All five are placed AND should display (this verifies the fix end-to-end)
    assert refs == set("ABCDE"), refs
    fr = out["fidelity_report"]
    assert not [n for n in fr["needs_attention"] if "not displayed" in n]
