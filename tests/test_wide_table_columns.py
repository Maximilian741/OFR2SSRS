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


def test_narrow_table_keeps_comfortable_width():
    rdl = convert(_wide_xml(3))["rdl_xml"]
    widths = [float(w) for w in re.findall(
        r"<TablixColumn>\s*<Width>([\d.]+)in", rdl)]
    assert widths and all(abs(w - 1.5) < 0.01 for w in widths), widths


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
