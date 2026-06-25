"""Compile every generated SSRS expression through the REAL VB.NET compiler.

The layout/geometry renderer staticizes expressions before it rasterizes, so it
never proves an expression actually *compiles*. This gate does: it feeds each
``=...`` expression to ``System.CodeDom`` ``VBCodeProvider`` -- the same
compilation SSRS performs when it publishes a report's expression host -- so a
syntactically-invalid VB expression (bad ``IIf`` arity, trailing comma,
unbalanced parens, an undefined function the translator invented, a leaked
Oracle ``||``) is caught HERE instead of rendering as ``#Error`` in the user's
live SSRS.

Skips cleanly on a host without the VB compiler (non-Windows CI), so the public
repo's pipeline never breaks; on a Windows dev box it runs for real.
"""
from __future__ import annotations

import sys
from pathlib import Path
from xml.sax.saxutils import escape

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402
from converter.validators.vb_expr_check import check_rdl_expressions  # noqa: E402

_NS = "http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition"


def _wrap(exprs, code=None):
    """Build a minimal but well-formed RDL carrying the given expressions as
    Textbox <Value> nodes, optionally with a report <Code> block."""
    cells = "".join(
        f'<Textbox Name="T{i}"><Paragraphs><Paragraph><TextRuns><TextRun>'
        f"<Value>{escape(e)}</Value></TextRun></TextRuns></Paragraph>"
        "</Paragraphs></Textbox>"
        for i, e in enumerate(exprs)
    )
    code_block = f"<Code>{escape(code)}</Code>" if code else ""
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<Report xmlns="{_NS}">{code_block}'
        f"<Body><ReportItems>{cells}</ReportItems>"
        "<Height>1in</Height></Body><Width>1in</Width>"
        "<Page><PageWidth>8.5in</PageWidth><PageHeight>11in</PageHeight></Page>"
        "</Report>"
    )


# Probe once: if the compiler/host is absent, skip the whole module.
_PROBE = check_rdl_expressions(_wrap(["=1"]))
_AVAILABLE = bool(_PROBE.get("available"))

pytestmark = pytest.mark.skipif(
    not _AVAILABLE or sys.platform != "win32",
    reason="VB.NET compiler (System.CodeDom VBCodeProvider) unavailable or non-Windows",
)


VALID = [
    '=Fields!Customer_Name.Value',
    '=IIf(Fields!Status.Value = "A", "Active", "Inactive")',
    '=First(Fields!Total.Value, "DS_MAIN")',
    '=Sum(Fields!Amount.Value) & " total"',
    '=Left(Fields!Name.Value, 10)',
    '=Format(Fields!D.Value, "yyyy")',
    '=Globals!PageNumber & " of " & Globals!TotalPages',
    '=Lookup(Fields!K.Value, Fields!K.Value, Fields!V.Value, "child")',
    '=Parameters!P_YEAR.Value',
    '=IIf(IsNothing(Parameters!P.Value), "all", CStr(Parameters!P.Value))',
]

# label -> (expr, substring expected somewhere in the VB error)
BROKEN = {
    "iif_two_args":   '=IIf(Fields!X.Value > 0, "pos")',
    "trailing_comma": '=Left(Fields!N.Value, )',
    "unbalanced":     '=(1 + Fields!A.Value',
    "invented_fn":    '=Greatest(Fields!A.Value, Fields!B.Value)',
    "oracle_concat":  '=Fields!A.Value || Fields!B.Value',
}


def test_valid_expressions_all_compile():
    res = check_rdl_expressions(_wrap(VALID))
    assert res["available"]
    bad = res["bad"]
    assert not bad, "false positives on valid SSRS expressions: " + "; ".join(
        f"{b['expr']} -> {b['errors']}" for b in bad
    )
    assert res["summary"]["total"] == len(VALID)


def test_each_broken_expression_is_flagged():
    exprs = list(BROKEN.values())
    res = check_rdl_expressions(_wrap(exprs))
    assert res["available"]
    flagged = {r["expr"] for r in res["bad"]}
    for label, e in BROKEN.items():
        assert e in flagged, f"compiler did NOT flag the broken expression: {label}: {e}"


def test_code_reference_resolves_when_declared_and_flags_when_not():
    # =Code.SumLookup(...) compiles when the report declares SumLookup in <Code>.
    decl = (
        "Public Function SumLookup(ByVal items As Object) As Decimal\n"
        "    Return 0\n"
        "End Function"
    )
    ok = check_rdl_expressions(_wrap(["=Code.SumLookup(Nothing)"], code=decl))
    assert ok["available"] and not ok["bad"], (
        "declared Code.SumLookup should compile: " + str(ok.get("bad"))
    )
    # Same reference with NO <Code> block -> undefined -> must be flagged.
    missing = check_rdl_expressions(_wrap(["=Code.SumLookup(Nothing)"]))
    assert missing["available"] and missing["bad"], (
        "Code.X with no <Code> block must be flagged as undeclared"
    )


@pytest.mark.parametrize(
    "fixture",
    [
        "tests/fixtures/source_of_truth/letter/source.xml",
        "tests/fixtures/source_of_truth/master_detail/source.xml",
        "tests/fixtures/subreports/SAMPLE_DRILLTHROUGH.xml",
        "tests/fixtures/subreports/SAMPLE_LETTER_CHILD.xml",
        "tests/fixtures/subreports/SAMPLE_MASTER_DETAIL.xml",
    ],
)
def test_synthetic_fixture_expressions_compile_clean(fixture):
    """Every expression the converter emits for the synthetic fixtures must
    compile in VB.NET. Locks the formula translator against regressions that
    would emit invalid expressions (which render as #Error in real SSRS)."""
    path = ROOT / fixture
    rdl = convert(path.read_bytes())
    rdl_xml = rdl["rdl_xml"] if isinstance(rdl, dict) else rdl
    res = check_rdl_expressions(rdl_xml)
    assert res["available"]
    assert not res["bad"], (
        f"{fixture}: {res['summary']['failed']} expression(s) do not compile -> "
        + "; ".join(f"<{b['location']}> {b['expr']} :: {b['errors'][:1]}"
                    for b in res["bad"][:6])
    )
