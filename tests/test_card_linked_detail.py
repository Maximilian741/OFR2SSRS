"""Link-based master-detail: the card must surface the SEPARATE <link> child
dataset, not drop it.

A Tablix binds only one dataset, so the linked detail query's columns are
surfaced via =Join(LookupSet(masterKey, childKey, col, "childDS"), vbCrLf) --
all child rows, newline-aligned per column. Both the master fields and the
detail columns must appear, and it must stay upload-clean.
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
from converter.generators.rdl import _child_join_keys  # noqa: E402

SRC = ROOT / "tests" / "fixtures" / "source_of_truth" / "master_detail" / "source.xml"


def test_child_join_keys_detects_link():
    if not SRC.exists():
        pytest.skip("master_detail fixture not present")
    rep = parse_oracle_xml(SRC.read_bytes())
    by = {q.name: q for q in rep.queries}
    keys = _child_join_keys(by["Q_CUSTOMER"], by["Q_ORDER"])
    assert keys is not None
    master_col, child_col = keys
    assert master_col.upper() == "CUST_ID" and child_col.upper() == "CUST_ID"


def test_card_shows_master_and_linked_detail():
    if not SRC.exists():
        pytest.skip("master_detail fixture not present")
    res = convert(SRC.read_bytes())
    rdl = res["rdl_xml"]
    # master fields present (F8) ...
    assert re.search(r"Fields!CUST_NAME\.Value", rdl)
    # ... AND the linked detail columns via LookupSet (all child rows)
    for col in ("ORDER_DT", "AMOUNT", "STATUS"):
        assert re.search(rf"LookupSet\([^)]*Fields!{col}\.Value", rdl), \
            f"linked detail column {col} not surfaced"


def test_card_linked_detail_is_upload_clean():
    if not SRC.exists():
        pytest.skip("master_detail fixture not present")
    res = convert(SRC.read_bytes())
    assert res.get("conversion_error") in (None, "")
    pf = res.get("preflight") or {}
    assert not pf.get("issues"), f"preflight issues: {pf.get('issues')}"
