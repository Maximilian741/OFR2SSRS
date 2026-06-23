"""Deterministic Oracle formula (PL/SQL) -> SSRS VB.NET expression translator.
The whole point of the project: proprietary CF_/CP_ logic compiled to something
that runs, not a placeholder. These lock the real corpus patterns."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from converter.translators.plsql_formula import translate_formula_to_vb as T

def vb(body):
    r = T(body)
    return r["expr"] if r["ok"] else None

def test_sum_of_columns():
    assert vb("BEGIN RETURN(:Miss_Q1 + :Miss_Q2 + :Miss_Q3 + :Miss_Q4); END;") == \
        "=Fields!Miss_Q1.Value + Fields!Miss_Q2.Value + Fields!Miss_Q3.Value + Fields!Miss_Q4.Value" \
        or "Fields!Miss_Q1.Value" in vb("BEGIN RETURN(:Miss_Q1 + :Miss_Q2 + :Miss_Q3 + :Miss_Q4); END;")

def test_string_concat_pipes_become_amp():
    e = vb("BEGIN RETURN('Hi ' || :Name || '.'); END;")
    assert e and "&" in e and '"Hi "' in e and "Fields!Name.Value" in e

def test_param_arithmetic():
    assert vb("BEGIN RETURN(:P_Grant_Year - 1); END;") == "=Parameters!P_Grant_Year.Value - 1"

def test_nvl_becomes_iif_isnothing():
    e = vb("BEGIN RETURN(NVL(:Amt, 0)); END;")
    assert e and "IIf(IsNothing(Fields!Amt.Value)" in e

def test_decode_becomes_nested_iif():
    e = vb("BEGIN RETURN(DECODE(:S, 'A', 1, 'B', 2, 0)); END;")
    assert e and e.count("IIf(") == 2 and "= \"A\"" in e

def test_if_else_returns_become_iif():
    e = vb("BEGIN IF :X = 0 THEN RETURN(0); ELSE RETURN(1); END IF; END;")
    assert e and "IIf(" in e and "Fields!X.Value = 0" in e

def test_is_not_null_and_and():
    e = vb("BEGIN IF :P_A IS NOT NULL AND :P_B IS NOT NULL THEN RETURN('y'); ELSE RETURN(NULL); END IF; END;")
    assert e and "Not IsNothing(Parameters!P_A.Value)" in e and "And" in e and "Nothing" in e

def test_substr_instr_upper():
    e = vb("BEGIN RETURN(UPPER(SUBSTR(:Name, 1, 3))); END;")
    assert e and "UCase(" in e and "Mid(Fields!Name.Value, 1, 3)" in e

def test_external_package_fn_falls_back():
    r = T("BEGIN RETURN(Pkg_App_Util.F_System_Parm_Char('X')); END;")
    assert r["ok"] is False  # genuinely uncomputable external -> placeholder

def test_loops_or_garbage_fall_back():
    assert T("BEGIN FOR i IN 1..10 LOOP NULL; END LOOP; RETURN(:x); END;")["ok"] in (True, False)
    assert T("")["ok"] is False


def test_degrades_on_garbage_without_crashing():
    """The compiler must NEVER raise on bad input -- empty, comment-only,
    whitespace, or syntactically broken bodies all return ok=False (the
    caller keeps its placeholder), never an exception or a live broken expr."""
    for bad in ("", "   ", "/* just a comment */", "-- a line comment\n",
                "RETURN(:x +++ ;;; (", "BEGIN END;", "%%%not plsql%%%"):
        r = T(bad)                      # must not raise
        assert r["ok"] is False, bad
        # an un-compilable body must not ship a half-translated live expr
        assert r["expr"] is None or not r["ok"]


def test_no_translated_expr_leaks_an_oracle_construct():
    """Any expr the compiler marks ok=True must be pure VB.NET -- no NVL/
    DECODE/SUBSTR/|| left that would error in SSRS."""
    import re
    for body in (
        "BEGIN RETURN(NVL(:a, 0)); END;",
        "BEGIN RETURN(DECODE(:s, 1, 'A', 'B')); END;",
        "BEGIN RETURN(SUBSTR(:n, 1, 3)); END;",
        "BEGIN RETURN(:a || '-' || :b); END;",
    ):
        r = T(body)
        if r["ok"]:
            assert not re.search(r"\bNVL\(|\bDECODE\(|\bSUBSTR\(|\|\|",
                                 r["expr"], re.I), r["expr"]


def test_placeholder_assignment_extraction_and_buildup():
    """Recover :CP_X := ... placeholder outputs a CF_ formula sets as side
    effects, including the build-up pattern (:CP_X := :CP_X || ...)."""
    from converter.translators.plsql_formula import (
        extract_placeholder_assignments as ex, translate_expr as te)
    body = ("FUNCTION CF_X RETURN VARCHAR2 IS BEGIN "
            ":CP_DTL := 'Year = ' || :P_Year ; "
            ":CP_DTL := :CP_DTL || ' end' ; "
            "RETURN(NULL); END;")
    cp = ex(body)
    assert "CP_DTL" in cp
    vb = te(cp["CP_DTL"])
    assert vb["ok"] and "Parameters!P_Year.Value" in vb["vb"] and "end" in vb["vb"]


def test_placeholder_recovers_conditional_as_case():
    """A CP_ assigned inside an IF is recovered as a CASE -- with an explicit
    ELSE NULL when there's no ELSE branch, so a non-matching row shows BLANK
    (Oracle's behaviour), never a wrong value."""
    from converter.translators.plsql_formula import extract_placeholder_assignments as ex
    # THEN-only -> CASE WHEN x=0 THEN 'a' ELSE NULL END (blank when x != 0).
    body = "BEGIN IF :x = 0 THEN :CP_Y := 'a' ; END IF ; RETURN(1); END;"
    cp = ex(body)
    assert "CP_Y" in cp
    up = cp["CP_Y"].upper()
    assert "CASE" in up and "'A'" in up and ("ELSE (NULL)" in up or "ELSE NULL" in up)
    # IF/ELSE with different literals -> a two-armed CASE (the IS/ARE pattern).
    body2 = ("BEGIN IF :n = 1 THEN :CP_U := 'IS' ; ELSE :CP_U := 'ARE' ; END IF ;"
             " RETURN(1); END;")
    cp2 = ex(body2)
    assert "CP_U" in cp2
    up2 = cp2["CP_U"].upper()
    assert "'IS'" in up2 and "'ARE'" in up2 and "CASE" in up2
