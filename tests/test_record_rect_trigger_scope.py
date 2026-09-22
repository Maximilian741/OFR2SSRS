"""PER-RECORD RECTANGLE format triggers must survive the Report Server's
publish-time compile.

THE FAILURE (project fatal error #1, on an emitter path the earlier fixes
never covered). A per-record letter emits one Rectangle per declared Oracle
frame inside the record region, and a frame that declares a FORMAT TRIGGER
carries the translated trigger as that rectangle's ``<Visibility><Hidden>``.
The first gating of a newly harvested corpus found the server would refuse
24 of those reports at upload, on two independent shapes that every local
rail was blind to (the XSD encodes shape, not meaning; the render harness
staticizes expressions before the engine compiles one; ReportViewer
evaluates an unknown field as Nothing and paints a clean page):

  1. NAME DESYNC. A dataset's ``<Field Name>`` is a SANITISED derivation of
     the source column name — illegal identifier characters become ``_``, a
     name with no ASCII letter left becomes a positional ``Col<n>`` (the
     server rejects the rest: "Field names must be CLS-compliant
     identifiers"), and two raw names that sanitise to the same string get a
     uniquifier suffix. The trigger translator spelled its field references
     from the RAW name instead, so on any column that needed sanitising the
     published expression named a field the dataset does not declare:
     rsFieldReference, whole report refused.

  2. MIXED SCOPE. The scope net that makes a Hidden legal asked "does a
     dataset-scoped occurrence of this field appear anywhere in the
     expression?" — a question that answers YES for an expression carrying
     BOTH a scoped and a bare occurrence of the same column. The net then
     skipped the name entirely and shipped the bare one. The producing
     source shape is ordinary: ``IF :CS_SIDE_COUNT != 0 AND :SIDE_NOTE IS
     NOT NULL`` translates to a scoped Count for the declared <summary> and
     a bare reference for the column.

THE RULE NOW, and it is the one the variant-band path already follows (same
helper, one answer in the converter, not two): a rectangle NEVER changes
scope — it has no dataset of its own and always compiles against the
container region's. Every reference to another dataset rides inside a call
that carries THAT dataset's scope, and WHICH ROWS comes from the
DECLARATION: no <link> between the two queries -> the whole dataset
(``First(..., "D")``, the grain Oracle itself iterates for an unlinked
query); a declared <link> -> the correlated row through the DECLARED key
(``Lookup(<container key>, <other key>, ..., "D")``).

Fixtures are synthetic and English; the shapes are structural, so they are
the shapes the wild reports expose.
"""
from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert                                    # noqa: E402
from converter.generators.rdl import (                           # noqa: E402
    _dataset_field_names,
    _field_ref_is_dataset_scoped,
    _rewrite_unscoped_field_refs,
    _strict_trigger_resolve,
)
from converter.parsers.oracle_xml import parse_oracle_xml        # noqa: E402
from converter.validators.publish_semantics import (             # noqa: E402
    publish_violations,
)

NS = ("{http://schemas.microsoft.com/sqlserver/reporting/"
      "2008/01/reportdefinition}")
_REGIONS = {"Tablix", "List", "Matrix", "Table", "Chart", "CustomReportItem",
            "GaugePanel"}
_FIELD_REF = re.compile(r"Fields!(\w+)\.Value")
# an aggregate/Lookup call opening — used to prove no call ends up nested
# inside another one (the server refuses a nested aggregate in its own right)
_AGG_OPEN = re.compile(
    r"\b(?:First|Last|Sum|Avg|Min|Max|Count|CountDistinct|CountRows|StDev|"
    r"StDevP|Var|VarP|RunningValue|Aggregate|Lookup|LookupSet)\s*\(")


# ---------------------------------------------------------------------------
# Fixture: a per-record notice whose record frame declares a format trigger
# reading a SECOND query — the shape the wild reports carry.
# ---------------------------------------------------------------------------

_TRIGGER = """    <function name="f_side_ft">
      <textSource>
      <![CDATA[function F_SIDE_FT return boolean is
begin
  IF :CS_SIDE_COUNT != 0 AND :SIDE_NOTE IS NOT NULL THEN
    return (TRUE);
  ELSE
    RETURN (FALSE);
  END IF;
end;]]>
      </textSource>
    </function>
"""


def _notice_xml() -> bytes:
    """A per-record account notice (Q_MAIN, the heavier query, is the record
    dataset) with a side query whose three columns exercise EVERY step of
    the field-naming rule:

      ``1ST_QTR_TOTAL``  -> ``_1ST_QTR_TOTAL``    (leading-digit prefix)
      ``_1ST_QTR_TOTAL`` -> ``_1ST_QTR_TOTAL_2``  (uniquifier: both sanitise
                                                   to the same string)
      ``2026``           -> ``Col<n>``            (CLS fallback: no letter)

    The record frame's format trigger reads a declared <summary> over the
    first of those AND a plain column of the same query, so ONE Hidden
    carries both the sanitised-name reference and the mixed scoped/bare
    shape.
    """
    return ("""<?xml version="1.0" encoding="UTF-8"?>
<report name="BRANCH_NOTICE" DTDVersion="9.0.2.0.10">
  <data>
    <dataSource name="Q_MAIN">
      <select canParse="no"><![CDATA[SELECT ACCT_NO, REGION, HOLDER, STATUS, OPENED_ON, BALANCE FROM ACCOUNTS]]></select>
      <group name="G_MAIN">
        <dataItem name="ACCT_NO" datatype="vchar2" columnOrder="1" defaultLabel="Account"/>
        <dataItem name="REGION" datatype="vchar2" columnOrder="2" defaultLabel="Region"/>
        <dataItem name="HOLDER" datatype="vchar2" columnOrder="3" defaultLabel="Holder"/>
        <dataItem name="STATUS" datatype="vchar2" columnOrder="4" defaultLabel="Status"/>
        <dataItem name="OPENED_ON" datatype="date" columnOrder="5" defaultLabel="Opened"/>
        <dataItem name="BALANCE" datatype="number" columnOrder="6" defaultLabel="Balance"/>
      </group>
    </dataSource>
    <dataSource name="Q_SIDE">
      <select canParse="no"><![CDATA[SELECT Q1 AS "1ST_QTR_TOTAL", Q1B AS "_1ST_QTR_TOTAL", YR AS "2026", NOTE AS SIDE_NOTE FROM QUARTERS]]></select>
      <group name="G_SIDE">
        <dataItem name="1ST_QTR_TOTAL" datatype="number" columnOrder="1" defaultLabel="First Quarter"/>
        <dataItem name="_1ST_QTR_TOTAL" datatype="number" columnOrder="2" defaultLabel="First Quarter B"/>
        <dataItem name="2026" datatype="number" columnOrder="3" defaultLabel="Year"/>
        <dataItem name="SIDE_NOTE" datatype="vchar2" columnOrder="4" defaultLabel="Side Note"/>
        <summary name="CS_SIDE_COUNT" source="1ST_QTR_TOTAL" function="count"
                 width="20" precision="10" reset="report" compute="report"
                 defaultWidth="0" defaultHeight="0" columnFlags="8"/>
      </group>
    </dataSource>
  </data>
  <programUnits>
""" + _TRIGGER + """  </programUnits>
  <layout>
  <section name="main" repeatOn="G_MAIN">
    <body width="7.50000" height="9.00000">
      <location x="0.50000" y="0.50000"/>
      <frame name="M_SIDE_BLOCK">
        <geometryInfo x="0.00000" y="0.30000" width="7.50000" height="1.20000"/>
        <advancedLayout formatTrigger="f_side_ft"/>
        <text name="B_SIDE">
          <geometryInfo x="0.20000" y="0.40000" width="6.00000" height="0.22000"/>
          <textSegment><font face="Arial" size="10"/>
          <string><![CDATA[Quarterly summary enclosed]]></string></textSegment></text>
      </frame>
      <frame name="M_TAIL">
        <geometryInfo x="0.00000" y="2.00000" width="7.50000" height="1.00000"/>
        <text name="B_TAIL">
          <geometryInfo x="0.20000" y="2.20000" width="6.00000" height="0.40000"/>
          <textSegment><font face="Arial" size="10"/>
          <string><![CDATA[The stated balance remains payable on the schedule shown]]></string></textSegment></text>
        <field name="F_ACCT_NO" source="ACCT_NO">
          <font face="Arial" size="10"/>
          <geometryInfo x="0.20000" y="2.60000" width="3.00000" height="0.20000"/></field>
      </frame>
    </body>
  </section>
  </layout>
</report>""").encode("utf-8")


def _linked_notice_xml() -> bytes:
    """The same notice with the side query declared a <link> CHILD of the
    record group on the account column: its rows belong to ONE record each,
    so a whole-dataset aggregate would read another record's rows."""
    base = _notice_xml().decode("utf-8")
    base = base.replace('NOTE AS SIDE_NOTE FROM QUARTERS',
                        'NOTE AS SIDE_NOTE, ACCT_NO FROM QUARTERS')
    base = base.replace(
        '<dataItem name="SIDE_NOTE" datatype="vchar2" columnOrder="4" '
        'defaultLabel="Side Note"/>',
        '<dataItem name="SIDE_NOTE" datatype="vchar2" columnOrder="4" '
        'defaultLabel="Side Note"/>\n'
        '        <dataItem name="ACCT_NO" datatype="vchar2" columnOrder="5" '
        'defaultLabel="Account"/>')
    assert 'columnOrder="5" defaultLabel="Account"' in base
    base = base.replace(
        "  </data>",
        '    <link parentGroup="G_MAIN" childQuery="Q_SIDE" '
        'parentColumn="ACCT_NO" childColumn="ACCT_NO" condition="eq" '
        'sqlClause="where"/>\n  </data>')
    return base.encode("utf-8")


def _local(tag):
    return tag.split("}", 1)[1] if "}" in tag else tag


def _rdl(xml_bytes) -> str:
    out = convert(xml_bytes, target_db="oracle")["rdl_xml"]
    assert out.strip()
    return out


def _dataset_fields(root):
    return {ds.get("Name") or "":
            {f.get("Name") for f in ds.iter(f"{NS}Field") if f.get("Name")}
            for ds in root.iter(f"{NS}DataSet")}


def _record_rect_hiddens(root):
    """(rectangle name, Hidden expression, container dataset) for every
    Rectangle carrying an expression Hidden. The container dataset is the
    OUTERMOST enclosing data region's — the only scope the engine gives a
    nested item, whatever anything in between declares."""
    out = []

    def walk(el, outer_ds):
        if _local(el.tag) in _REGIONS and outer_ds is None:
            outer_ds = el.findtext(f"{NS}DataSetName") or ""
        if _local(el.tag) == "Rectangle":
            vis = el.find(f"{NS}Visibility")
            hid = (vis.findtext(f"{NS}Hidden") or "") if vis is not None \
                else ""
            if hid.startswith("="):
                out.append((el.get("Name"), hid, outer_ds or ""))
        for c in el:
            walk(c, outer_ds)

    walk(root, None)
    return out


def _scoped_spans(expr):
    """Spans of calls whose LAST top-level argument is a ``"<scope>"``
    literal. A Lookup/LookupSet span starts AFTER its first top-level
    comma: its SOURCE key is evaluated in the CURRENT scope, so a foreign
    reference there is exactly the server's rejection."""
    n, spans = len(expr), []
    for m in _AGG_OPEN.finditer(expr):
        start, depth, i = m.end(), 1, m.end()
        first_comma = last_comma = -1
        while i < n and depth:
            c = expr[i]
            if c == '"':
                j = expr.find('"', i + 1)
                i = (j if j >= 0 else n) + 1
                continue
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    break
            elif c == "," and depth == 1:
                last_comma = i
                if first_comma < 0:
                    first_comma = i
            i += 1
        if depth or last_comma < 0 or i >= n:
            continue
        if not re.fullmatch(r"\"[^\"]+\"", expr[last_comma + 1:i].strip()):
            continue
        spans.append(((first_comma + 1) if m.group(0).lstrip().startswith(
            "Lookup") else start, i))
    return spans


def _nested_aggregates(expr):
    """Every aggregate/Lookup call that OPENS inside another one's argument
    list. "Aggregate functions cannot be nested inside other aggregate
    functions" — the server refuses the report for this on its own, and the
    publish rule engine has no rule for it, so the measurement lives here.
    A blanket substitution over a field NAME produced exactly this shape:
    the already-scoped occurrence got a second wrapper."""
    out = []
    for m in _AGG_OPEN.finditer(expr):
        depth, i, n = 1, m.end(), len(expr)
        while i < n and depth:
            c = expr[i]
            if c == '"':
                j = expr.find('"', i + 1)
                i = (j if j >= 0 else n) + 1
                continue
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
            i += 1
        if depth:
            continue
        for inner in _AGG_OPEN.finditer(expr, m.end(), i):
            out.append((m.group(0).strip(), inner.group(0).strip()))
    return out


def _unscoped_foreign_refs(expr, container_fields):
    """Every field reference in ``expr`` that is NOT a column of the
    container's dataset and NOT inside a dataset-scoped call — the exact
    thing the server refuses (rsFieldReference)."""
    spans = _scoped_spans(expr)
    bad = []
    for m in _FIELD_REF.finditer(expr):
        if m.group(1) in container_fields:
            continue
        if any(a <= m.start() and m.end() <= b for a, b in spans):
            continue
        bad.append(m.group(1))
    return bad


# ---------------------------------------------------------------------------
# Guard 1 — the artifact the server accepts
# ---------------------------------------------------------------------------

def test_record_rect_trigger_hidden_is_publish_legal():
    """The generated notice carries the trigger on its record rectangle and
    the whole RDL survives the publish-semantics rule engine."""
    rdl = _rdl(_notice_xml())
    root = ET.fromstring(rdl)
    ds_fields = _dataset_fields(root)
    rects = _record_rect_hiddens(root)
    assert rects, "the record frame's format trigger never reached a Hidden"

    for name, hid, container in rects:
        assert container, f"{name}: no enclosing region dataset"
        bad = _unscoped_foreign_refs(hid, ds_fields.get(container, set()))
        assert not bad, (
            f"{name}: {bad} referenced outside dataset {container!r} with no "
            f"dataset scope — the server refuses this at upload: {hid}")

    assert publish_violations(rdl) == []


def test_every_field_reference_names_a_declared_field():
    """No expression anywhere may name a field its scoped dataset does not
    declare. This is the NAME-DESYNC half: the trigger's reference to a
    sanitised column must be spelled with the emitted ``<Field Name>``."""
    root = ET.fromstring(_rdl(_notice_xml()))
    ds_fields = _dataset_fields(root)
    declared = set().union(*ds_fields.values()) if ds_fields else set()
    for el in root.iter():
        txt = (el.text or "")
        if not txt.startswith("=") or "Fields!" not in txt:
            continue
        for ref in _FIELD_REF.findall(txt):
            assert ref in declared, (
                f"{_local(el.tag)}: Fields!{ref} is declared by NO dataset "
                f"in the generated RDL: {txt}")
    # and the specific spellings the naming rule produces
    side = ds_fields["Q_SIDE"]
    assert "_1ST_QTR_TOTAL" in side and "_1ST_QTR_TOTAL_2" in side, side
    assert any(n.startswith("Col") for n in side), side


def test_scoped_and_bare_occurrences_of_one_field_are_both_handled():
    """The MIXED-SCOPE half, on the artifact: the trigger's Hidden carries
    the summary's already-scoped aggregate AND a reference to a plain column
    of the same foreign query. The scoped one must survive untouched (a
    second wrap would nest aggregates, which the server also refuses) and
    the bare one must come out scoped."""
    root = ET.fromstring(_rdl(_notice_xml()))
    hid = [h for _n, h, _c in _record_rect_hiddens(root)
           if "Q_SIDE" in h]
    assert hid, "no Hidden referencing the side query"
    expr = hid[0]
    assert re.search(r'Count\(Fields!\w+\.Value, "Q_SIDE"\)', expr), expr
    assert re.search(r'(First|Lookup)\([^()]*Fields!SIDE_NOTE\.Value',
                     expr), expr
    assert _nested_aggregates(expr) == [], expr


# ---------------------------------------------------------------------------
# Guard 2 — PROVE THE GATE CAN FAIL: doctor the artifact back into each
# pre-fix shape and watch the publish gate go red.
# ---------------------------------------------------------------------------

def test_a_reference_to_an_undeclared_field_turns_the_gate_red():
    """PROVE THE GATE CAN FAIL on the NAME-DESYNC shape: doctor a reference
    in the finished artifact so it names a field the scoped dataset does not
    declare — which is precisely what spelling it from the raw column name
    produced on the reports the server refused — and the publish gate must
    report it.

    (The gate reads a reference with ``Fields!([A-Za-z_][A-Za-z0-9_]*)``, so
    a raw name whose FIRST character is already illegal — a digit — is not
    parsed as a reference at all and this rule cannot see it; such an
    expression does not compile either, and the compile leg owns it. What
    the rule does see, and what the refused reports carried, is a reference
    that parses and names nothing the dataset declares.)"""
    rdl = _rdl(_notice_xml())
    assert publish_violations(rdl) == []
    broken = rdl.replace("Fields!_1ST_QTR_TOTAL.Value",
                         "Fields!QTR_TOTAL_UNDECLARED.Value")
    assert broken != rdl
    viol = publish_violations(broken)
    assert any(v.startswith("publish.field_not_in_scoped_dataset")
               for v in viol), viol


def test_the_prefix_spellings_would_not_be_declared_fields():
    """The NAME-DESYNC defect itself, stated as a fact about the fixture:
    for a column that needs sanitising, the two spellings the converter used
    to produce — the RAW source name and a bare ``_safe()`` of it — are NOT
    names the emitted dataset declares, while the single derivation's answer
    is. Any builder that re-derives instead of asking ships a reference the
    server refuses."""
    from converter.generators.rdl import _safe

    report = parse_oracle_xml(_notice_xml())
    root = ET.fromstring(_rdl(_notice_xml()))
    declared = _dataset_fields(root)["Q_SIDE"]
    side = next(q for q in report.queries if q.name == "Q_SIDE")
    derived = _dataset_field_names(side)

    undeclared, wrong_field = 0, 0
    for it, fname in derived:
        assert fname in declared, (it.name, fname)
        for spelling in (it.name, _safe(it.name)):
            if spelling == fname:
                continue          # this step did not fire for this column
            # A pre-fix spelling either names NOTHING the dataset declares
            # (the publish rejection) or names ANOTHER column's field (a
            # silent wrong-column read) — never this item's field.
            if spelling in declared:
                wrong_field += 1
            else:
                undeclared += 1
    assert undeclared >= 2, (
        "the fixture must expose the publish-fatal spelling", derived)
    assert wrong_field >= 1, (
        "the fixture must expose the wrong-column spelling too", derived)


def test_a_bare_cross_dataset_reference_turns_the_gate_red():
    """PROVE THE GATE CAN FAIL on the MIXED-SCOPE shape: put the bare
    occurrence back — the one the "is it scoped anywhere?" question used to
    let through — and the gate must report it."""
    rdl = _rdl(_notice_xml())
    bare = re.sub(r'First\(Fields!SIDE_NOTE\.Value, "Q_SIDE"\)',
                  "Fields!SIDE_NOTE.Value", rdl)
    assert bare != rdl
    viol = publish_violations(bare)
    assert any(v.startswith("publish.field_not_in_dataset") for v in viol), \
        viol


# ---------------------------------------------------------------------------
# Guard 3 — the two scope helpers, per occurrence
# ---------------------------------------------------------------------------

_MIXED = ('=Not(((Count(Fields!X.Value, "D") <> 0) '
          'And (Not IsNothing(Fields!X.Value))))')


def test_scope_predicate_is_per_occurrence_not_per_name():
    """The question that shipped the defect was "does a scoped occurrence
    appear?"; the question that has to be asked is "is EVERY occurrence
    scoped?". The mixed expression must answer NO."""
    assert _field_ref_is_dataset_scoped(_MIXED, "X") is False
    # every occurrence scoped -> yes
    assert _field_ref_is_dataset_scoped(
        '=(Count(Fields!X.Value, "D") <> 0) And '
        '(Not IsNothing(First(Fields!X.Value, "D")))', "X") is True
    # the flat form alone -> yes
    assert _field_ref_is_dataset_scoped('=First(Fields!X.Value, "D")',
                                        "X") is True
    # nothing to judge -> no
    assert _field_ref_is_dataset_scoped('="literal"', "X") is False


def test_rewrite_touches_only_the_unscoped_occurrence():
    out = _rewrite_unscoped_field_refs(
        _MIXED, "X", 'First(Fields!X.Value, "D")')
    assert out == ('=Not(((Count(Fields!X.Value, "D") <> 0) '
                   'And (Not IsNothing(First(Fields!X.Value, "D")))))'), out
    # idempotent: a second pass finds nothing left to wrap
    assert _rewrite_unscoped_field_refs(
        out, "X", 'First(Fields!X.Value, "D")') == out


# ---------------------------------------------------------------------------
# Guard 4 — SINGLE DERIVATION of the field name
# ---------------------------------------------------------------------------

def test_emitted_field_names_come_from_the_one_derivation():
    """Whatever the emitter wrote for a dataset's fields is exactly what
    _dataset_field_names says, and what the trigger resolver spells is one
    of those names — so no second derivation can drift from it."""
    xml = _notice_xml()
    report = parse_oracle_xml(xml)
    root = ET.fromstring(_rdl(xml))
    ds_fields = _dataset_fields(root)

    for q in report.queries:
        derived = [fname for _it, fname in _dataset_field_names(q)]
        emitted = [f.get("Name")
                   for ds in root.iter(f"{NS}DataSet")
                   if ds.get("Name") == q.name
                   for f in ds.iter(f"{NS}Field")]
        if emitted:            # a pruned dataset contributes nothing
            assert emitted == derived, (q.name, emitted, derived)
        assert len(set(derived)) == len(derived), (q.name, derived)

    resolve = _strict_trigger_resolve(report)
    for q in report.queries:
        for it in (q.items or []):
            if not (it.name or "").strip():
                continue
            expr = resolve(it.name)
            for ref in _FIELD_REF.findall(expr):
                assert ref in ds_fields.get(q.name, set()), (
                    f"{q.name}.{it.name}: resolver spelled Fields!{ref}, "
                    f"which that dataset does not declare")


def test_field_naming_rule_covers_its_three_steps():
    """Unit-level, so a future edit to one step cannot quietly drop it."""
    class _It:
        def __init__(self, name):
            self.name = name

    class _Q:
        def __init__(self, names):
            self.items = [_It(n) for n in names]

    got = [n for _it, n in _dataset_field_names(
        _Q(["PLAIN", "1ST_QTR", "_1ST_QTR", "2026", "", "TOTAL AMT"]))]
    assert got == ["PLAIN", "_1ST_QTR", "_1ST_QTR_2", "Col4", "TOTAL_AMT"], \
        got


# ---------------------------------------------------------------------------
# Guard 5 — the DECLARATION decides which rows, through the same helper the
# variant-band path uses
# ---------------------------------------------------------------------------

def test_declared_link_child_reference_is_correlated_not_flattened():
    """With the side query declared a <link> child of the record group, a
    whole-dataset First() would read rows belonging to OTHER records. The
    reference must go through the declared key instead."""
    rdl = _rdl(_linked_notice_xml())
    assert publish_violations(rdl) == []
    root = ET.fromstring(rdl)
    hid = [h for _n, h, _c in _record_rect_hiddens(root) if "Q_SIDE" in h]
    assert hid, "no Hidden referencing the side query"
    expr = hid[0]
    m = re.search(r'Lookup\((Fields!\w+\.Value), (Fields!\w+\.Value), '
                  r'Fields!SIDE_NOTE\.Value, "Q_SIDE"\)', expr)
    assert m, f"declared link did not produce the correlated form: {expr}"
    src, dst = m.group(1), m.group(2)
    ds_fields = _dataset_fields(root)
    # the SOURCE key is evaluated in the container's scope, the DESTINATION
    # key in the looked-up dataset — each must be declared where it lands
    assert _FIELD_REF.findall(src)[0] in ds_fields["Q_MAIN"], src
    assert _FIELD_REF.findall(dst)[0] in ds_fields["Q_SIDE"], dst


def test_without_a_declared_link_the_reference_stays_dataset_grain():
    """The mirror measurement: remove the declaration and the same shape
    falls back to the whole-dataset form — the correlation is never
    guessed."""
    root = ET.fromstring(_rdl(_notice_xml()))
    hid = [h for _n, h, _c in _record_rect_hiddens(root) if "Q_SIDE" in h]
    assert hid
    assert 'First(Fields!SIDE_NOTE.Value, "Q_SIDE")' in hid[0], hid[0]
    assert "Lookup(" not in hid[0], hid[0]


# ---------------------------------------------------------------------------
# Guard 6 — corpus reach: the same two facts on every source available here
# ---------------------------------------------------------------------------

_CORPORA = [ROOT / "samples" / "oracle", ROOT / "tests" / "fixtures" / "i18n"]


def _corpus_sources():
    out = []
    for root in _CORPORA:
        if root.is_dir():
            out.extend(sorted(root.rglob("*.xml")))
    return out


@pytest.mark.parametrize("src", _corpus_sources(),
                         ids=lambda p: p.name)
def test_corpus_rect_hiddens_are_publish_legal(src):
    """Every Rectangle Hidden in every repo-bundled source keeps its
    references inside the container's dataset or inside a dataset-scoped
    call. (The external corpora are carried by the publish fatal gate;
    these run everywhere, CI included.)"""
    root = ET.fromstring(_rdl(src.read_bytes()))
    ds_fields = _dataset_fields(root)
    for name, hid, container in _record_rect_hiddens(root):
        bad = _unscoped_foreign_refs(hid, ds_fields.get(container, set()))
        assert not bad, f"{src.name}/{name}: {bad} unscoped in {hid}"
        nested = _nested_aggregates(hid)
        assert not nested, f"{src.name}/{name}: nested {nested} in {hid}"


def test_the_corpus_checker_can_fail():
    """PROVE THE MEASUREMENT: the checker behind guard 6 reports the exact
    rejected shape when it is handed one, and stays silent on the legal
    forms."""
    assert _unscoped_foreign_refs("=Fields!FOREIGN.Value = 1",
                                  {"HOME"}) == ["FOREIGN"]
    assert _unscoped_foreign_refs(_MIXED, {"HOME"}) == ["X"]
    assert _unscoped_foreign_refs('=First(Fields!FOREIGN.Value, "D") = 1',
                                  {"HOME"}) == []
    assert _unscoped_foreign_refs("=Fields!HOME.Value = 1", {"HOME"}) == []
    # a Lookup's SOURCE key is CURRENT scope, not the looked-up dataset
    assert _unscoped_foreign_refs(
        '=Lookup(Fields!FOREIGN.Value, Fields!K.Value, '
        'Fields!V.Value, "D")', {"HOME"}) == ["FOREIGN"]
    # and the nesting checker: the shape a blanket substitution produced
    assert _nested_aggregates(
        '=Count(First(Fields!X.Value, "D"), "D") <> 0') == [
        ("Count(", "First(")]
    assert _nested_aggregates(
        '=(Count(Fields!X.Value, "D") <> 0) And '
        '(Not IsNothing(First(Fields!X.Value, "D")))') == []
