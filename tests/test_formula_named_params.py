"""Oracle named-parameter call syntax (`fn(p => v)`) must not crash formula
translation. Before this fix the lone `>` raised 'unexpected token', dropping
the computable IF/CASE logic around the package call (wild-corpus verified).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter.translators.plsql_formula import (  # noqa: E402
    translate_formula_to_vb, translate_expr)


def test_named_param_call_does_not_crash_parse():
    r = translate_expr("Pkg_Util.F_Get(pvName => :P_X, piId => 5)")
    # It resolves to an external placeholder (ok False) but MUST parse.
    assert "did not parse" not in str(r)
    assert r.get("vb") is not None


def test_if_logic_around_named_param_call_still_reduces():
    body = (
        "FUNCTION CF_T RETURN VARCHAR2 IS BEGIN "
        "IF :CS_Count = 1 THEN RETURN(:CS_First); "
        "ELSE RETURN(Pkg_Util.F_All(pvName => :P_X)); END IF; END;"
    )
    res = translate_formula_to_vb(body)
    # No leaked parser error; the IF becomes an IIf with the package call as
    # a placeholder in the else branch.
    assert "did not parse" not in str(res.get("notes"))
    assert res.get("expr") is not None
    assert "IIf(" in res["expr"]


def test_positional_args_unaffected():
    r = translate_expr("NVL(:A, 0) + SUBSTR(:B, 1, 3)")
    assert r.get("vb") is not None and "did not parse" not in str(r)
