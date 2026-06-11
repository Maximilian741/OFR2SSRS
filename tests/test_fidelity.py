"""Faithfulness gate: the fidelity report must show NO silent loss of columns
or parameters across every shape, and it must actually DETECT a drop (so a real
regression in the converter would be caught, not hidden).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "tests"))

from converter import convert  # noqa: E402
from converter.parsers.oracle_xml import parse_oracle_xml  # noqa: E402
from converter.fidelity import build_fidelity_report, RD  # noqa: E402


def _cases():
    out = []
    sot = ROOT / "tests" / "fixtures" / "source_of_truth"
    for d in sorted(sot.glob("*")):
        src = d / "source.xml"
        if src.exists():
            out.append(pytest.param(src.read_bytes(), id=f"fixture-{d.name}"))
    try:
        from test_synthetic_stress import build, CASES
        for cid, (cols, params, dup) in CASES.items():
            out.append(pytest.param(build(cid.upper(), cols, params, dup_query=dup),
                                    id=f"matrix-{cid}"))
    except Exception:
        pass
    return out


@pytest.mark.parametrize("xml_bytes", _cases())
def test_no_silent_column_or_param_loss(xml_bytes):
    rep = parse_oracle_xml(xml_bytes)
    fr = build_fidelity_report(rep, convert(xml_bytes).get("rdl_xml") or "")
    assert fr["score"] == 1.0, f"score {fr['score']}: {fr['needs_attention']}"
    assert not fr["categories"]["columns"]["dropped"], fr["categories"]["columns"]
    assert not fr["categories"]["parameters"]["dropped"], fr["categories"]["parameters"]


def test_fidelity_report_present_in_convert_output():
    fixture = (ROOT / "tests" / "fixtures" / "source_of_truth"
               / "master_detail" / "source.xml")
    if not fixture.exists():
        pytest.skip("fixture missing")
    fr = convert(fixture.read_bytes()).get("fidelity_report")
    assert isinstance(fr, dict)
    assert set(fr) >= {"score", "summary", "categories", "needs_attention"}
    assert fr["score"] == 1.0


def test_detector_catches_a_dropped_column():
    """If the converter ever drops a source column, the report MUST flag it
    (score < 1.0) -- otherwise the gate would be useless. Proven with a parsed
    report whose RDL is missing one of its columns."""
    class _I:
        def __init__(self, n): self.name = n
    class _Q:
        items = [_I("KEPT"), _I("DROPPED_COL")]
        groups: list = []
    class _R:
        parameters: list = []
        layout: list = []
        queries = [_Q()]

    rdl = (f'<Report xmlns="{RD[1:-1]}"><DataSets><DataSet Name="Q"><Fields>'
           f'<Field Name="KEPT"><DataField>KEPT</DataField></Field>'
           f'</Fields></DataSet></DataSets></Report>')
    fr = build_fidelity_report(_R(), rdl)
    assert fr["score"] < 1.0
    assert "DROPPED_COL" in fr["categories"]["columns"]["dropped"]
    assert fr["needs_attention"]


def test_dropped_subtotal_is_surfaced_but_formula_summaries_are_not():
    """A declared <summary> over a data column that the RDL never aggregates
    must be surfaced (a missing subtotal/grand total). A CF_/CP_ formula
    summary source must NOT be flagged -- those are wired separately."""
    class _Grp:
        def __init__(self, summaries):
            self.summaries = summaries
            self.children: list = []
            self.items: list = []

    class _Q:
        items: list = []
        def __init__(self, groups):
            self.groups = groups

    class _R:
        parameters: list = []
        layout: list = []
        def __init__(self, q):
            self.queries = [q]

    grp = _Grp([
        {"name": "SumSALARY", "source": "SALARY", "function": "sum"},
        {"name": "CntCF", "source": "CF_Thing", "function": "count"},
    ])
    rdl = (f'<Report xmlns="{RD[1:-1]}"><DataSets><DataSet Name="Q"><Fields>'
           f'<Field Name="SALARY"><DataField>SALARY</DataField></Field>'
           f'</Fields></DataSet></DataSets></Report>')  # no aggregate of SALARY
    fr = build_fidelity_report(_R(_Q([grp])), rdl)
    dt = fr["categories"]["summaries"]["dropped_totals"]
    assert "SALARY" in dt
    assert "CF_Thing" not in dt
    assert any("subtotal/grand-total" in n for n in fr["needs_attention"])
