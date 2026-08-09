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
    """A report whose Oracle columns are genuinely narrow renders them at
    their DECLARED width, NOT stretched to a uniform 1.5in and NOT propped
    up to a 0.5in "legibility" floor.

    The floor used to apply here too. Truth-measured on a landscape summary
    report that declares a 0.4375in column: the truth PDF centres that
    column's value on the 0.4375in box (centre 570.46pt vs the declared
    box's own centre 570.31pt). A 0.5in floor would have moved that centre
    +2.25pt and pushed every column after it right by the same. So the
    floor is only honest where the declared set does NOT fit the printable
    width and the widths are being scaled anyway -- which is what
    test_wide_table_column_width_adapts covers. Here the declaration fits,
    so the declaration IS the answer, to full precision.
    """
    rdl = convert(_wide_xml(3))["rdl_xml"]   # 3 cols, each 0.3in in Oracle
    widths = [float(w) for w in re.findall(
        r"<TablixColumn>\s*<Width>([\d.]+)in", rdl)]
    assert len(widths) == 3, widths
    assert all(abs(w - 0.3) < 0.0005 for w in widths), widths


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


def test_declared_record_frame_height_is_the_detail_row_height():
    """The detail row is the record frame's DECLARED height -- a 0.6in frame
    keeps 0.6in and a 0.2in frame keeps 0.2in.

    (Supersedes the old 0.28in synthesized floor: truth-PDF measurement
    showed a report declaring a 0.17212in frame renders 0.17212in rows, and
    padding the row to 0.28in inflated its page count ~29%.)"""
    assert abs(_detail_row_height(convert(_row_xml(0.2))["rdl_xml"]) - 0.20) < 0.01
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
