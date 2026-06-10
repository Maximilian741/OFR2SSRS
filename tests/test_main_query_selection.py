"""F8: _pick_main_query must not bind to an inflated <link> CHILD.

A flat link child (parent_group set, no nested group chain) can be inflated
past its master by join-key augmentation -- prefer the master so its fields
aren't dropped. But a child that carries its OWN nested group chain is the
genuine data-rich query and must be kept (don't regress nested reports).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402
from converter.parsers.oracle_xml import parse_oracle_xml  # noqa: E402
from converter.generators.rdl import _pick_main_query  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "source_of_truth"


class _G:
    def __init__(self, break_col="", children=None):
        self.break_col = break_col
        self.children = children or []


class _Q:
    def __init__(self, name, n_items, parent_group="", groups=None):
        self.name = name
        self.items = [type("I", (), {"name": f"{name}{i}"})() for i in range(n_items)]
        self.parent_group = parent_group
        self.groups = groups or []


class _R:
    def __init__(self, queries):
        self.queries = queries


def test_flat_inflated_child_yields_master():
    master = _Q("Q_MASTER", 3, groups=[_G(break_col="Q_MASTER0")])
    # heavier, FLAT child (single group) -- inflated past the master
    child = _Q("Q_DETAIL", 5, parent_group="G_MASTER",
               groups=[_G(break_col="Q_DETAIL0")])
    assert _pick_main_query(_R([master, child])).name == "Q_MASTER"


def test_nested_child_is_kept():
    master = _Q("Q_MASTER", 3, groups=[_G(break_col="Q_MASTER0")])
    # heavier child that is itself NESTED master-detail (2-level chain)
    child = _Q("Q_DETAIL", 5, parent_group="G_MASTER",
               groups=[_G(break_col="Q_DETAIL0", children=[_G(break_col="X")])])
    assert _pick_main_query(_R([master, child])).name == "Q_DETAIL"


def test_no_links_keeps_heaviest():
    a = _Q("Q_A", 3)
    b = _Q("Q_B", 7)
    assert _pick_main_query(_R([a, b])).name == "Q_B"


def test_master_detail_fixture_binds_master_fields():
    src = FIXTURES / "master_detail" / "source.xml"
    if not src.exists():
        import pytest
        pytest.skip("master_detail fixture not present")
    rep = parse_oracle_xml(src.read_bytes())
    assert _pick_main_query(rep).name == "Q_CUSTOMER"   # the <link> parent
    rdl = convert(src.read_bytes())["rdl_xml"]
    # the master's own column must be bound, not dropped in favor of the detail
    assert re.search(r"Fields!CUST_NAME\.Value", rdl)
