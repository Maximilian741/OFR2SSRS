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
