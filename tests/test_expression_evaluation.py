"""Prove every generated SSRS expression COMPUTES, not merely that it compiles.

``test_vb_expr_check`` compiles each ``=...`` expression through the real VB.NET
compiler. Its object model answers ``Nothing`` to every ``Fields!`` /
``Parameters!`` / aggregate reference, so an expression that divides by zero,
calls ``CDate`` on something that is not a date, or resolves to a field the
report never declared compiles perfectly — and then renders ``#Error`` or a
silent blank the moment real SSRS runs it. With the ReportViewer expression host
blocked on this box every render is layout-mode, so the engine never runs it
either: nothing in the pipeline was proving that an expression produces a VALUE.

This rail closes that gap. It seeds a VB expression host from the RDL's OWN
declarations (``<Field>``/``rd:TypeName``, ``<ReportParameter>``/``<DataType>``,
the report's textbox names), INVOKES every expression once per synthetic world,
and asserts on what came back.

The rule is existential, never universal — an expression is sound when SOME
well-formed input makes it compute. ``CDate(Parameters!P_START.Value)`` is
correct SSRS and still throws on the text witness, so demanding that one
arbitrary guess work would flag correct work. See ``vb_expr_eval.WORLDS``.

Full-corpus mode: point ``O2S_EXPR_EVAL_CORPUS`` at a directory of Oracle
sources to sweep them all (optionally ``O2S_EXPR_EVAL_SAMPLE=N`` for a
deterministic N-file sample of it, chosen by sorted order so a rerun picks the
same files). Unset, the rail runs the repo's own synthetic fixtures.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from xml.sax.saxutils import escape

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402
from converter.validators.vb_expr_eval import (  # noqa: E402
    DEFECT_CLASSES,
    WORLDS,
    comparison_literals,
    declared_fields,
    declared_parameters,
    evaluate_rdl_expressions,
    expression_sites,
)

_NS = "http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition"
_RD = "http://schemas.microsoft.com/SQLServer/reporting/reportdesigner"


def _rdl(cells, fields=(), params=(), code=None):
    """A minimal well-formed RDL carrying the given expressions.

    ``cells`` is a list of (element-tag, expression). A "Value" cell becomes a
    Textbox TextRun value (a printed position); anything else becomes that style
    property on the same textbox, so the rail sees the real requirement class.
    """
    items = []
    for i, (tag, expr) in enumerate(cells):
        if tag == "Value":
            style = ""
            value = expr
        else:
            style = "<%s>%s</%s>" % (tag, escape(expr), tag)
            value = '="x"'
        items.append(
            '<Textbox Name="T%d"><Paragraphs><Paragraph><TextRuns><TextRun>'
            "<Value>%s</Value></TextRun></TextRuns></Paragraph></Paragraphs>"
            "<Style>%s</Style></Textbox>" % (i, escape(value), style)
        )
    fdecl = "".join(
        '<Field Name="%s"><DataField>%s</DataField>'
        "<rd:TypeName>%s</rd:TypeName></Field>" % (n, n, t)
        for n, t in fields
    )
    pdecl = "".join(
        '<ReportParameter Name="%s"><DataType>%s</DataType>'
        "<Nullable>true</Nullable></ReportParameter>" % (n, t)
        for n, t in params
    )
    code_block = "<Code>%s</Code>" % escape(code) if code else ""
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<Report xmlns="%s" xmlns:rd="%s">%s'
        "<DataSets><DataSet Name=\"D\"><Fields>%s</Fields></DataSet></DataSets>"
        "<ReportParameters>%s</ReportParameters>"
        "<Body><ReportItems>%s</ReportItems><Height>1in</Height></Body>"
        "<Width>1in</Width>"
        "<Page><PageWidth>8.5in</PageWidth><PageHeight>11in</PageHeight></Page>"
        "</Report>" % (_NS, _RD, code_block, fdecl, pdecl, "".join(items))
    )


FIELDS = (
    ("Name", "System.String"),
    ("Amount", "System.Decimal"),
    ("Started", "System.DateTime"),
    ("Qty", "System.Int32"),
    ("Code", "System.String"),
)
PARAMS = (("P_START", "String"), ("P_YEAR", "Integer"))


# Probe once: without the VB compiler there is nothing to invoke.
_PROBE = evaluate_rdl_expressions(_rdl([("Value", '="probe"')]))
_AVAILABLE = bool(_PROBE.get("available"))

pytestmark = pytest.mark.skipif(
    not _AVAILABLE or sys.platform != "win32",
    reason="VB.NET compiler (System.CodeDom VBCodeProvider) unavailable "
           "or non-Windows",
)


def _by_expr(res):
    return {r["expr"]: r for r in res["results"]}


# --------------------------------------------------------------------------
# The host really evaluates
# --------------------------------------------------------------------------
def test_host_computes_real_values_not_placeholders():
    """The whole point of this rail: values come BACK. A host that answered
    Nothing everywhere (what the compile-only rail does) would pass a compile
    check and prove nothing about what prints."""
    cells = [
        ("Value", '=Fields!Name.Value & "/" & Fields!Code.Value'),
        ("Value", "=Fields!Amount.Value * 2"),
        ("Value", '=Format(Fields!Started.Value, "yyyy")'),
        ("Value", '=Globals!PageNumber & " of " & Globals!TotalPages'),
        ("Value", "=Sum(Fields!Amount.Value)"),
        ("Value", "=Count(Fields!Qty.Value)"),
        ("Value", '=Lookup(Fields!Code.Value, Fields!Code.Value, '
                  'Fields!Name.Value, "D")'),
        ("Value", '=Join(LookupSet(Fields!Code.Value, Fields!Code.Value, '
                  'Fields!Name.Value, "D"), ", ")'),
        ("Value", "=UCase(Fields!Name.Value)"),
    ]
    res = evaluate_rdl_expressions(_rdl(cells, FIELDS, PARAMS))
    assert res["available"]
    got = _by_expr(res)
    text = got['=Fields!Name.Value & "/" & Fields!Code.Value']["values"][0]
    assert text["value"] == "ALPHA/ALPHA", text
    assert got["=Fields!Amount.Value * 2"]["values"][0]["value"] == "2469.12"
    assert got['=Format(Fields!Started.Value, "yyyy")']["values"][0]["value"] \
        == "2024"
    assert got['=Globals!PageNumber & " of " & Globals!TotalPages'][
        "values"][0]["value"] == "1 of 2"
    # Sum folds the numeric operand over the synthetic row count (3).
    assert got["=Sum(Fields!Amount.Value)"]["values"][0]["value"] == "3703.68"
    assert got["=Count(Fields!Qty.Value)"]["values"][0]["value"] == "3"
    assert got["=UCase(Fields!Name.Value)"]["values"][0]["value"] == "ALPHA"
    joined = got['=Join(LookupSet(Fields!Code.Value, Fields!Code.Value, '
                 'Fields!Name.Value, "D"), ", ")']["values"][0]["value"]
    assert joined == "ALPHA, ALPHA, ALPHA", joined
    assert not res["defects"], res["defects"]


def test_declared_types_reach_the_host():
    """A field's declared rd:TypeName decides the CLR type it evaluates as —
    the declaration is the only truth surface for the synthetic value."""
    cells = [("Value", "=Fields!Amount.Value"),
             ("Value", "=Fields!Started.Value"),
             ("Value", "=Fields!Qty.Value"),
             ("Value", "=Fields!Name.Value")]
    res = evaluate_rdl_expressions(_rdl(cells, FIELDS, PARAMS))
    got = _by_expr(res)
    assert got["=Fields!Amount.Value"]["values"][0]["type"] == "System.Decimal"
    assert got["=Fields!Started.Value"]["values"][0]["type"] == "System.DateTime"
    assert got["=Fields!Qty.Value"]["values"][0]["type"] == "System.Int32"
    assert got["=Fields!Name.Value"]["values"][0]["type"] == "System.String"


def test_multi_world_lets_an_ambiguous_string_be_a_date():
    """An Oracle CHAR column — and every no-prompt String parameter — carries
    dates, numbers and text alike. CDate on one is correct SSRS, so it must pass
    on the strength of the DATE world alone, and the harness must record that it
    threw in the others rather than hiding it."""
    e = '=Format(CDate(Parameters!P_START.Value), "yyyy-MM-dd")'
    res = evaluate_rdl_expressions(_rdl([("Value", e)], FIELDS, PARAMS))
    r = _by_expr(res)[e]
    assert not r["classes"], r
    worlds = {w["world"]: w for w in r["values"]}
    assert worlds["DATE"]["ok"] and worlds["DATE"]["value"] == "2024-03-15"
    assert not worlds["TEXT"]["ok"], "the text witness must genuinely throw"


def test_match_world_uses_the_reports_own_comparison_literals():
    """A conditional keyed on a code value reaches a branch because the report
    DECLARES the codes it compares against, right there in the expression."""
    e = ('=IIf(Fields!Code.Value = "I", "included", '
         'IIf(Fields!Code.Value = "X", "excluded", Nothing))')
    assert comparison_literals([e]) == {("Fields", "Code"): "I"}
    res = evaluate_rdl_expressions(_rdl([("Value", e)], FIELDS, PARAMS))
    r = _by_expr(res)[e]
    assert not r["classes"], r
    worlds = {w["world"]: w for w in r["values"]}
    assert worlds["MATCH"]["value"] == "included", worlds["MATCH"]
    assert worlds["TEXT"]["isNothing"], "no witness would have reached a branch"


# --------------------------------------------------------------------------
# MUTATION PROOF: expressions that COMPILE and still fail
# --------------------------------------------------------------------------
# Each of these is valid VB.NET — the compile rail passes every one of them.
BROKEN = {
    "divide_by_zero": ("Value", "=Fields!Qty.Value \\ 0", "always_throws"),
    "cast_that_cannot_work": ("Value", '=CDate("no such date")',
                              "always_throws"),
    "undeclared_field": ("Value", "=Fields!NOT_DECLARED.Value",
                         "missing_reference"),
    "undeclared_parameter": ("Value", "=Parameters!P_NOT_THERE.Value",
                             "missing_reference"),
    "undeclared_report_item": ("Value", "=ReportItems!NoSuchBox.Value",
                               "missing_reference"),
    "computed_blank": ("Value", '=IIf(Len(Fields!Name.Value) >= 0, Nothing, "x")',
                       "always_nothing"),
    "colour_that_is_not_a_colour": ("Color", '="not-a-colour"', "bad_color"),
    "hidden_that_is_not_boolean": ("Hidden", "=Fields!Name.Value",
                                   "bad_boolean"),
    "weight_outside_the_enum": ("FontWeight", '="Chunky"', "bad_enum"),
}


@pytest.mark.parametrize("label", sorted(BROKEN))
def test_each_runtime_defect_is_caught(label):
    """Break it on purpose, watch the rail go RED. Every one of these compiles
    cleanly, so only EVALUATION can catch it."""
    tag, expr, want = BROKEN[label]
    res = evaluate_rdl_expressions(_rdl([(tag, expr)], FIELDS, PARAMS))
    assert res["available"]
    r = _by_expr(res).get(expr)
    assert r is not None, "the site was not collected: %s" % expr
    assert want in r["classes"], (
        "%s: expected %s, got %s (%s)" % (label, want, r["classes"], r["detail"])
    )
    assert res["defects"], "%s must register as a hard defect" % label


VALID = [
    ("Value", '=Fields!Name.Value & " — " & Fields!Code.Value'),
    ("Value", "=IIf(IsNothing(Fields!Amount.Value), 0, Fields!Amount.Value)"),
    ("Value", '=First(Fields!Amount.Value, "D")'),
    ("Value", '=Left(Fields!Name.Value & "", 10)'),
    ("Value", "=Fields!Qty.Value + 1"),
    ("Value", '=Format(Globals!ExecutionTime, "MM/dd/yyyy")'),
    ("Value", "=Globals!ExecutionTime"),
    ("Value", '=CStr(Parameters!P_YEAR.Value) & " report"'),
    ("Color", '=IIf(Fields!Qty.Value > 0, "#008000", "Red")'),
    ("Hidden", '=Not(UCase(Fields!Code.Value) = "SHOW")'),
    ("FontWeight", '=IIf(Fields!Code.Value = "T", "Bold", "Normal")'),
    ("BackgroundColor", "=IIf(RowNumber(Nothing) Mod 2 = 0, \"#EAEAEA\", \"#FFFFFF\")"),
]


def test_no_false_positives_on_valid_expressions():
    """The counterweight to the mutation battery: correct SSRS in every one of
    these requirement classes must come back clean, or the rail is unusable."""
    res = evaluate_rdl_expressions(_rdl(VALID, FIELDS, PARAMS))
    assert res["available"]
    assert not res["defects"], "; ".join(
        "%s :: %s :: %s" % (d["expr"], d["classes"], d["detail"])
        for d in res["defects"]
    )
    # A style-property cell also carries the textbox's own printed value, so the
    # site count is the cells plus one extra per style cell.
    expected = len(VALID) + sum(1 for tag, _ in VALID if tag != "Value")
    assert res["summary"]["total"] == expected
    assert res["summary"]["evaluated"] == expected


def test_declared_blank_is_not_a_computed_blank():
    """The emitter writes a literal ``=Nothing`` when it declines a source
    construct it cannot translate — a DECLARATION of blankness carrying an audit
    note. An expression that does work and still yields Nothing is the silent
    blank we hunt. The two must never be conflated: collapsing them would either
    bury the real defect or turn every honest decline into a false alarm."""
    literal = "=Nothing"
    computed = '=IIf(Len(Fields!Name.Value) >= 0, Nothing, "x")'
    res = evaluate_rdl_expressions(
        _rdl([("Value", literal), ("Value", computed)], FIELDS, PARAMS))
    got = _by_expr(res)
    assert got[literal]["classes"] == ["declared_blank"], got[literal]
    assert "always_nothing" in got[computed]["classes"], got[computed]
    assert [d["expr"] for d in res["defects"]] == [computed]


def test_bind_positions_may_legitimately_be_nothing():
    """A query-parameter bind and a report-parameter default are BINDS, not
    printed text: ``=Nothing`` there is the deliberate 'no value supplied' state
    that keeps Report Builder from prompting. Flagging it would push the fix
    straight into the #1 fatal defect."""
    rdl = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<Report xmlns="%s" xmlns:rd="%s"><DataSets><DataSet Name="D">'
        '<Query><CommandText>SELECT 1 FROM DUAL</CommandText><QueryParameters>'
        '<QueryParameter Name="@P"><Value>=Nothing</Value></QueryParameter>'
        "</QueryParameters></Query><Fields/></DataSet></DataSets>"
        '<ReportParameters><ReportParameter Name="P"><DataType>String</DataType>'
        "<Nullable>true</Nullable><DefaultValue><Values>"
        "<Value>=Nothing</Value></Values></DefaultValue></ReportParameter>"
        "</ReportParameters>"
        "<Body><ReportItems/><Height>1in</Height></Body><Width>1in</Width>"
        "</Report>" % (_NS, _RD)
    )
    res = evaluate_rdl_expressions(rdl)
    assert res["available"]
    assert res["summary"]["total"] == 2, expression_sites
    assert all(r["requirement"] == "any" for r in res["results"])
    assert not res["defects"], res["defects"]


def test_report_code_block_functions_are_invoked():
    """``=Code.X(...)`` must run the report's OWN VB, not a stub — a reducer
    that groups or folds is pure logic the engine would never get to run here."""
    code = ("Public Function Doubler(ByVal n As Object) As Object\n"
            "    Return CDbl(n) * 2\n"
            "End Function")
    e = "=Code.Doubler(Fields!Amount.Value)"
    res = evaluate_rdl_expressions(_rdl([("Value", e)], FIELDS, PARAMS,
                                        code=code))
    r = _by_expr(res)[e]
    assert not r["classes"], r
    assert r["values"][0]["value"] == "2469.12", r["values"][0]


# --------------------------------------------------------------------------
# The corpus rail
# --------------------------------------------------------------------------
def _corpus_sources():
    env = os.environ.get("O2S_EXPR_EVAL_CORPUS")
    if env:
        srcs = sorted(Path(env).rglob("*.xml"))
        n = os.environ.get("O2S_EXPR_EVAL_SAMPLE")
        if n:
            step = max(1, len(srcs) // int(n))
            srcs = srcs[::step][: int(n)]
        return srcs
    return sorted((ROOT / "tests" / "fixtures").rglob("*.xml"))


CORPUS = _corpus_sources()


@pytest.mark.parametrize(
    "source", CORPUS, ids=[p.parent.name + "/" + p.stem for p in CORPUS])
def test_every_generated_expression_computes(source):
    """No expression the converter emits may throw for every input, resolve to
    a name the report never declared, or compute its way to a blank."""
    rdl = convert(source.read_bytes(), source.name)
    rdl_xml = rdl["rdl_xml"] if isinstance(rdl, dict) else rdl
    res = evaluate_rdl_expressions(rdl_xml)
    assert res["available"]
    # The shortfall gate first: a harness that compiled nothing returns an
    # EMPTY result set, and "no defects" over no results is not a pass.
    assert res["summary"]["evaluated"] == res["summary"]["total"], (
        "%s: only %d of %d expressions were evaluated -> %s"
        % (source.name, res["summary"]["evaluated"], res["summary"]["total"],
           (res.get("error") or "")[:300]))
    assert not res["defects"], "%s: %d of %d expressions fail evaluation -> %s" % (
        source.name, len(res["defects"]), res["summary"]["total"],
        "; ".join("<%s> %s :: %s :: %s"
                  % (d["path"][-48:], d["expr"][:90], d["classes"], d["detail"])
                  for d in res["defects"][:6]),
    )


def test_mutation_a_real_generated_report_goes_red():
    """The gate must be able to FAIL on the artifacts it certifies. Take a real
    converted report, inject expressions that COMPILE but cannot compute, and
    confirm the corpus rail reports them; the unmutated report is clean."""
    source = ROOT / "tests/fixtures/source_of_truth/master_detail/source.xml"
    rdl_xml = convert(source.read_bytes(), source.name)["rdl_xml"]
    clean = evaluate_rdl_expressions(rdl_xml)
    assert clean["available"] and not clean["defects"], clean.get("defects")
    assert clean["summary"]["total"] > 0

    # Inject into the first printed value the converter emitted, so the mutation
    # rides the REAL artifact rather than a hand-built one.
    sites = [s for s in expression_sites(
        __import__("xml.etree.ElementTree", fromlist=["ElementTree"])
        .fromstring(rdl_xml)) if s["requirement"] == "value"]
    assert sites, "the fixture must emit at least one printed expression"
    victim = escape(sites[0]["expr"])

    # The divisor is built from a Global so the VB compiler cannot constant-fold
    # it: a literal `\ 0` is rejected at COMPILE time (BC30542) and would prove
    # nothing about evaluation.
    for mutant, want in (
        ("=CInt(Globals!TotalPages) \\ "
         "CInt(Globals!PageNumber - Globals!PageNumber)", "always_throws"),
        ("=Fields!NO_SUCH_COLUMN_AT_ALL.Value", "missing_reference"),
    ):
        broken = rdl_xml.replace("<Value>%s</Value>" % victim,
                                 "<Value>%s</Value>" % escape(mutant), 1)
        assert broken != rdl_xml, "mutation did not apply"
        red = evaluate_rdl_expressions(broken)
        assert red["available"]
        assert red["defects"], "rail stayed GREEN on an injected %s" % want
        assert any(want in d["classes"] for d in red["defects"]), (
            "expected %s, got %s"
            % (want, [d["classes"] for d in red["defects"]]))


def test_declarations_are_read_from_the_rdl_not_guessed():
    """The synthetic values come from the report's own <Field>/<ReportParameter>
    declarations; a rail that invented names would evaluate a different report
    than the one that ships."""
    source = ROOT / "tests/fixtures/source_of_truth/master_detail/source.xml"
    rdl_xml = convert(source.read_bytes(), source.name)["rdl_xml"]
    import xml.etree.ElementTree as ET

    root = ET.fromstring(rdl_xml)
    fields = declared_fields(root)
    params = declared_parameters(root)
    assert fields, "the fixture declares dataset fields"
    assert all(t.startswith("System.") for t in fields.values()), fields
    for name in fields:
        assert '<Field Name="%s"' % name in rdl_xml
    for name in params:
        assert '<ReportParameter Name="%s"' % name in rdl_xml
    assert len(WORLDS) >= 4 and "NULL" in WORLDS
    assert "always_nothing" in DEFECT_CLASSES
    assert "declared_blank" not in DEFECT_CLASSES


# --------------------------------------------------------------------------
# GATE INTEGRITY: the rail must be unable to pass a report it never evaluated
# --------------------------------------------------------------------------
# The harness assembles ONE VB source: its object model, the report's <Code>
# block, and one function per expression. An expression that will not compile is
# neutralised and retried, but a class-level failure (a <Code> block that is not
# valid VB) kills the whole assembly: the harness answers ``compiled=false`` with
# an EMPTY result set. Reading only ``defects`` then reported SUCCESS on a report
# whose expressions were never invoked once — a gate that cannot fail.
_UNCOMPILABLE_CODE = ("Public Function Broken(ByVal n As Object) As Object\n"
                      "    Return CDbl(n) *** 2 %%% Nothing\n")
_COMPILABLE_CODE = ("Public Function Fine(ByVal n As Object) As Object\n"
                    "    Return CDbl(n) * 2\n"
                    "End Function")
_TWO_CELLS = [("Value", '=Fields!Name.Value & "/" & Fields!Code.Value'),
              ("Value", "=Fields!Amount.Value * 2")]


def test_a_code_block_that_cannot_compile_fails_the_rail():
    """MUTATION PROOF. Break the report's own VB, watch the rail go RED. It
    must not go 'unavailable' either: that reads as skipped, and a skip on a
    report the pipeline actually ships is the same silent pass."""
    res = evaluate_rdl_expressions(
        _rdl(_TWO_CELLS, FIELDS, PARAMS, code=_UNCOMPILABLE_CODE))
    assert res["available"], "a harness failure is not an environment skip"
    assert res["summary"]["total"] == len(_TWO_CELLS)
    assert res["summary"]["evaluated"] == 0, res["summary"]
    assert res["summary"]["not_evaluated"] == len(_TWO_CELLS)
    assert len(res["defects"]) == len(_TWO_CELLS), res["summary"]
    assert all("not_evaluated" in d["classes"] for d in res["defects"]), \
        [d["classes"] for d in res["defects"]]
    assert "compiled=false" in (res.get("error") or ""), res.get("error")


def test_the_same_report_with_compilable_code_is_clean():
    """The counterweight: only the broken <Code> block may turn it red."""
    res = evaluate_rdl_expressions(
        _rdl(_TWO_CELLS, FIELDS, PARAMS, code=_COMPILABLE_CODE))
    assert res["available"]
    assert not res["defects"], res["defects"]
    assert res["summary"]["evaluated"] == res["summary"]["total"]
    assert "error" not in res


def test_every_collected_site_must_be_evaluated():
    """The shortfall IS the finding: sites expected vs sites that came back
    with a value. A real generated report may not lose a single one."""
    source = ROOT / "tests/fixtures/source_of_truth/master_detail/source.xml"
    rdl_xml = convert(source.read_bytes(), source.name)["rdl_xml"]
    res = evaluate_rdl_expressions(rdl_xml)
    assert res["available"]
    s = res["summary"]
    assert s["total"] > 0 and s["expected"] == s["total"]
    assert s["evaluated"] == s["expected"], (
        "%d of %d expressions were never invoked" % (s["evaluated"], s["expected"]))
    assert s["not_evaluated"] == 0


# --------------------------------------------------------------------------
# An aggregate over a column the report DECLARES non-numeric
# --------------------------------------------------------------------------
# SSRS answers rsAggregateOfNonNumericData; the harness folds the operand to 0
# so the surrounding logic still runs. Either way the number that prints is not
# a sum of anything — the silent-zero shape, and for years an informational note.
AGG_BROKEN = {
    "sum_over_declared_string": "=Sum(Fields!Name.Value)",
    "avg_over_declared_string": "=Avg(Fields!Code.Value)",
    "sum_over_declared_date": "=Sum(Fields!Started.Value)",
    "sum_inside_a_condition": ('=IIf(Sum(Fields!Code.Value) > 0, "over", '
                               '"under")'),
}


@pytest.mark.parametrize("label", sorted(AGG_BROKEN))
def test_aggregate_over_a_declared_nonnumeric_column_is_a_defect(label):
    expr = AGG_BROKEN[label]
    res = evaluate_rdl_expressions(_rdl([("Value", expr)], FIELDS, PARAMS))
    assert res["available"]
    r = _by_expr(res)[expr]
    assert "aggregate_over_nonnumeric" in r["classes"], (label, r)
    assert res["defects"], "%s must register as a hard defect" % label


# Correct SSRS in the same shape. A rail that flagged these would push the fix
# toward deleting real totals.
AGG_SOUND = [
    "=Sum(Fields!Amount.Value)",
    '=Sum(Fields!Amount.Value, "D")',
    "=Sum(Fields!Qty.Value)",
    "=Count(Fields!Name.Value)",           # Count counts rows, not values
    "=CountDistinct(Fields!Code.Value)",
    "=Min(Fields!Started.Value)",          # Min/Max order, they do not add
    "=Max(Fields!Name.Value)",
    "=First(Fields!Name.Value)",
    "=Sum(CDbl(Fields!Name.Value))",       # an explicit conversion IS the fix
    ("=Sum(IIf(IsNumeric(Fields!Name.Value), "
     "CDbl(Fields!Name.Value), 0))"),
    '=Sum(IIf(Fields!Code.Value = "I", Fields!Amount.Value, 0))',
]


def test_sound_aggregates_are_not_flagged():
    res = evaluate_rdl_expressions(
        _rdl([("Value", e) for e in AGG_SOUND], FIELDS, PARAMS))
    assert res["available"]
    flagged = [r["expr"] for r in res["results"]
               if "aggregate_over_nonnumeric" in r["classes"]]
    assert not flagged, flagged
    assert not res["defects"], [(d["expr"], d["classes"]) for d in res["defects"]]


def test_the_defect_classes_carry_the_two_integrity_rules():
    assert "not_evaluated" in DEFECT_CLASSES
    assert "aggregate_over_nonnumeric" in DEFECT_CLASSES
