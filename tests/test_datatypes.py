"""DataItem -> SSRS .NET type mapping (F2).

Oracle NUMBER must not blindly become Int32: a fixed scale > 0 is money/rate
data that Int32 truncates, and a >9-digit id overflows Int32. Only an explicit
small-precision scale-0 NUMBER is a genuine integer.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter.models import DataItem  # noqa: E402


def _t(**kw):
    return DataItem(name="X", **kw).ssrs_datatype


def test_number_with_scale_is_decimal_not_int32():
    assert _t(datatype="number", scale=2, precision=12) == "System.Decimal"
    assert _t(datatype="number", scale=4, precision=5) == "System.Decimal"


def test_large_or_unknown_precision_number_is_decimal():
    # 10-digit id overflows Int32 -> Decimal; unknown scale -> Decimal (safe)
    assert _t(datatype="number", scale=0, precision=10) == "System.Decimal"
    assert _t(datatype="number") == "System.Decimal"


def test_genuine_small_integer_is_int32():
    assert _t(datatype="number", scale=0, precision=4) == "System.Int32"
    assert _t(datatype="integer") == "System.Int32"


def test_float_and_currency():
    assert _t(datatype="float") == "System.Double"
    assert _t(datatype="currency") == "System.Decimal"  # NOT String


def test_text_and_date_unchanged():
    assert _t(datatype="vchar2") == "System.String"
    assert _t(datatype="date") == "System.DateTime"
