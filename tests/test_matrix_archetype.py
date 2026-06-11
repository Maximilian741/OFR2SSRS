"""Matrix (cross-tab) archetype lock.

Oracle <matrix>/<matrixCol>/<matrixRow>/<matrixCell> must convert to a REAL
two-axis SSRS Tablix: a dynamic column group (across), a dynamic row group
(down), and Sum() measure cells — not a flat column dump.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402
from converter.parsers.oracle_xml import parse_oracle_xml  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "matrix" / "source.xml"


def test_matrix_parsed_into_dimension_groups():
    rep = parse_oracle_xml(FIX.read_bytes())

    kinds = []

    def walk(g):
        kinds.append(getattr(g, "kind", ""))
        for c in (g.children or []):
            walk(c)

    for lg in rep.layout:
        walk(lg)
    assert "matrix" in kinds
    assert "matrix_col" in kinds and "matrix_row" in kinds
    assert "matrix_cell" in kinds


def test_matrix_emits_two_axis_tablix():
    rdl = convert(FIX.read_bytes())["rdl_xml"]
    assert '<Tablix Name="Tablix_Matrix">' in rdl
    # dynamic column group (across) AND dynamic row group (down)
    assert '<Group Name="MxColG">' in rdl
    assert '<Group Name="MxRowG">' in rdl
    assert "=Fields!Region.Value" in rdl     # column dimension
    assert "=Fields!Product.Value" in rdl    # row dimension
    # measure aggregated, not dumped raw
    assert re.search(r"Sum\(Fields!Amount\.Value\)", rdl)


def test_matrix_dataset_declares_all_dimensions():
    rdl = convert(FIX.read_bytes())["rdl_xml"]
    for col in ("Region", "Product", "Amount"):
        assert f'<Field Name="{col}">' in rdl, col


def test_902_frameref_matrix_dominance_and_measure_cell():
    """A 9.0.2 frame-ref matrix (horizontalFrame/verticalFrame + crossProduct)
    must be detected as DOMINANT even though it has supporting dimension/
    header/measure frames, and the cell must be a NUMERIC measure, not a
    dimension's neighbor label. Real-artifact verified (HRMS leave status)."""
    from converter.parsers.oracle_xml import parse_oracle_xml as _p
    from converter.generators.rdl import _find_matrix_spec
    xml = (
        '<?xml version="1.0"?><report name="LV" DTDVersion="9.0.2.0.10"><data>'
        '<dataSource name="Q_1"><select><![CDATA[SELECT emp_no,name,remarks,avl '
        'FROM v]]></select>'
        '<group name="G_emp"><dataItem name="emp_no" datatype="number"/>'
        '<dataItem name="name" datatype="vchar2"/></group>'
        '<group name="G_rem"><dataItem name="remarks" datatype="vchar2"/></group>'
        '<group name="G_m"><dataItem name="avl" datatype="number"/></group>'
        '<crossProduct name="G_X"><dimension><group name="G_emp"/></dimension>'
        '<dimension><group name="G_rem"/></dimension></crossProduct>'
        '</dataSource></data><layout><section name="main"><body width="8" height="9">'
        '<text name="T"><geometryInfo x="0" y="0" width="3" height="0.2"/>'
        '<textSegment><string><![CDATA[Co Title]]></string></textSegment></text>'
        '<repeatingFrame name="R_emp" source="G_emp"><geometryInfo x="2" y="1" width="2" height="0.3"/>'
        '<field name="f_e" source="emp_no"><geometryInfo x="2" y="1" width="1" height="0.2"/></field>'
        '<field name="f_n" source="name"><geometryInfo x="3" y="1" width="1" height="0.2"/></field>'
        '</repeatingFrame>'
        '<repeatingFrame name="R_rem" source="G_rem"><geometryInfo x="0" y="2" width="2" height="0.3"/>'
        '<field name="f_r" source="remarks"><geometryInfo x="0" y="2" width="2" height="0.2"/></field>'
        '</repeatingFrame>'
        '<repeatingFrame name="R_m" source="G_m"><geometryInfo x="2" y="2" width="1" height="0.3"/>'
        '<field name="f_a" source="avl"><geometryInfo x="2" y="2" width="1" height="0.2"/></field>'
        '</repeatingFrame>'
        '<matrix name="X_G_X" horizontalFrame="R_emp" verticalFrame="R_rem" '
        'xProductGroup="G_X"><geometryInfo x="2" y="1" width="3" height="2"/></matrix>'
        '</body></section></layout></report>'
    ).encode()
    spec = _find_matrix_spec(_p(xml))
    assert spec is not None
    assert spec["dominant"] is True, spec
    assert spec["row"] == "remarks" and spec["col"] == "emp_no"
    assert "avl" in spec["cells"] and "name" not in spec["cells"], spec["cells"]
    rdl = convert(xml)["rdl_xml"]
    assert '<Tablix Name="Tablix_Matrix">' in rdl
    assert '<Group Name="MxColG">' in rdl and '<Group Name="MxRowG">' in rdl


def test_matrix_mockup_shows_a_pivot_grid_not_scattered_fields():
    """The HTML mockup of a matrix report must render a real cross-tab PIVOT
    (an HTML table with the row dim down the left, col dim across the top),
    matching the RDL's Tablix_Matrix -- not scatter the dimension fields."""
    out = convert(FIX.read_bytes())
    html = out["mockup_html"]
    assert "<table" in html.lower()
    assert "Cross-tab:" in html
    # both dimensions named in the caption
    assert "Region" in html and "Product" in html
    # and the RDL agrees it's a matrix (mockup ↔ RDL consistency)
    assert '<Tablix Name="Tablix_Matrix">' in out["rdl_xml"]


def test_matrix_renders_through_ms_engine():
    sys.path.insert(0, str(ROOT / "tools" / "renderlab"))
    try:
        from render import render_rdl, lib_ready  # type: ignore
    except Exception:
        pytest.skip("renderlab not importable")
    if not lib_ready():
        pytest.skip("renderlab DLLs not fetched")
    import tempfile
    rdl = convert(FIX.read_bytes())["rdl_xml"]
    with tempfile.TemporaryDirectory() as td:
        rp = Path(td) / "m.rdl"
        rp.write_text(rdl, encoding="utf-8")
        res = render_rdl(rp, Path(td) / "m.pdf", rows=3)
        assert res["ok"], res.get("log", "")[-400:]
