"""
PUBLISH-SEMANTICS FATAL GATE — corpus-wide.

The third occurrence of project fatal error #1 was the one no local rail
could see: a generated RDL that was XSD-valid, carried no duplicate item
names and no undeclared references, LOADED through Microsoft's ReportViewer
engine and RENDERED — and the customer's Report Server still REJECTED it at
upload.

The blind spot is structural, not accidental:

  * the XSD encodes SHAPE only ("a Tablix may carry a DataSetName"), never
    MEANING ("...but a nested one is IGNORED, so the expressions inside it
    compile against the CONTAINER's dataset");
  * the upload gate's engine leg renders in LAYOUT mode — tools/renderlab/
    ms_layout.py STATICIZES every expression to a placeholder before the
    engine sees it, so expression SEMANTICS are never compiled locally (a
    live expression host cannot start under this machine's Application
    Control policy);
  * even in expression mode ReportViewer is MORE FORGIVING than the server:
    for a nested data region it silently ignores the declared DataSetName
    and renders, where the server compiles the expression against the
    container scope and refuses the report.

So this gate is a pure RULE ENGINE over the RDL tree
(converter.validators.publish_semantics) — no engine, no database, no
network. Every rule is a documented Report Server / RDL constraint, quoted
at its implementation site with the source it came from.

Corpora are optional (CI machines without them skip cleanly); the synthetic
conftest report keeps the gate alive everywhere.

KNOWN-OPEN RATCHET: a corpus directory may carry a PUBLISH_GATE_BASELINE.json
(next to the sources, NEVER in the repo) mapping rel-path -> [violation ids]
that are known converter backlog on those shapes. A file whose violations are
ALL baselined xfails (visible, tracked); any NEW violation anywhere is a hard
failure.

MEASURED AT PROMOTION (241 sources, all five corpora, report-only):
  6 violations in 2 reports, both triaged by eye against the artifact and
  both TRUE POSITIVES of documented publish-fatal classes:
    * 5x publish.lookup_nested  — a two-hop Lookup(Lookup(...), ...) chain.
      "Only one level of lookup is supported. A source, destination, or
      result expression can't include a reference to a lookup function."
    * 1x publish.field_not_in_scoped_dataset — a Lookup DESTINATION
      expression keyed on a column the looked-up dataset does not declare.
  Both are ratcheted as known-open converter defects, not silenced.

REACHABILITY CENSUS at promotion — a rule that examines zero sites reports
zero violations for free, so what each rule actually SAW was measured:
  WELL EXERCISED (a clean result here is a real result):
    * 4,698 Fields! references over 185 reports (3,470 bare, 1,228 scoped)
    * 1,534 aggregate calls, 1,020 of them carrying an explicit scope
    * 389 numeric aggregates whose operand is exactly one field, judged
      against a corpus where 1,896 of 3,355 fields are declared
      System.String — publish.aggregate_of_non_numeric is fully reachable
      and genuinely clean, NOT vacuous
    * 1,847 TablixMembers, 170 RepeatOnNewPage, 412 KeepWithGroup
    * 191 images (171 Embedded, 20 Database), 142 Lookup-family calls
  HONEST GAPS the corpus cannot exercise (mutation-proved capable below
  instead, so the gate is proven able to fail even here):
    * ReportItems! expression references: ZERO corpus-wide (the 884
      <ReportItems> hits are the container ELEMENT, not the collection)
    * Subreport elements: zero.  FixedData: zero.
    * Multilookup / RunningValue / Previous: zero.
    * Charts: one chart in one report.

Every rule class is mutation-proved: a known-good RDL is doctored into
exactly one failure class and the gate must go RED on it.
"""
from __future__ import annotations

import json
import os
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from converter import convert
from converter.validators.publish_semantics import (
    audit_publish_semantics,
    publish_violations,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_NAME = "PUBLISH_GATE_BASELINE.json"

# External corpora: entirely optional — absent directories simply contribute
# no cases. Paths are overridable so any machine can point the gate at its
# own harvest without touching repo code. (Same roster as the other gates.)
CORPORA = {
    "samples": REPO_ROOT / "samples" / "oracle",
    "i18n": REPO_ROOT / "tests" / "fixtures" / "i18n",
    "agency": Path(os.environ.get(
        "O2S_AGENCY_CORPUS",
        "C:/Users/maxca/Downloads/OneDrive_2026-08-06/Artifact Folders")),
    "wild": Path(os.environ.get(
        "O2S_WILD_CORPUS",
        "C:/Users/maxca/Downloads/_o2s_scratch11/wild_corpus")),
    "wild2": Path(os.environ.get(
        "O2S_WILD_CORPUS2",
        "C:/Users/maxca/Downloads/_o2s_scratch11/wild_corpus2")),
    "wild3": Path(os.environ.get(
        "O2S_WILD_CORPUS3",
        "C:/Users/maxca/Downloads/_o2s_scratch11/wild_corpus3")),
    "wild4": Path(os.environ.get(
        "O2S_WILD_CORPUS4",
        "C:/Users/maxca/Downloads/_o2s_scratch11/wild_corpus4")),
    "wild5": Path(os.environ.get(
        "O2S_WILD_CORPUS5",
        "C:/Users/maxca/Downloads/_o2s_scratch11/wild_corpus5")),
    "wild6": Path(os.environ.get(
        "O2S_WILD_CORPUS6",
        "C:/Users/maxca/Downloads/_o2s_scratch11/wild_corpus6")),
    "wild7": Path(os.environ.get(
        "O2S_WILD_CORPUS7",
        "C:/Users/maxca/Downloads/_o2s_scratch11/wild_corpus7")),
    "wild8": Path(os.environ.get(
        "O2S_WILD_CORPUS8",
        "C:/Users/maxca/Downloads/_o2s_scratch11/wild_corpus8")),
}


def _collect_corpus(root: Path):
    """Every *.xml under the corpus root, skipping tooling/output dirs
    (underscore-prefixed: _results, _generated_rdl, _meta, _dups...)."""
    if not root.is_dir():
        return []
    out = []
    for p in sorted(root.rglob("*.xml")):
        if any(part.startswith("_") for part in p.relative_to(root).parts[:-1]):
            continue
        out.append(p)
    return out


def _load_baseline(root: Path):
    bl = root / BASELINE_NAME
    if not bl.is_file():
        return {}
    try:
        return json.loads(bl.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — a corrupt baseline must never mask
        return {}      # violations; treat it as absent (everything fatal)


def _corpus_params():
    params = []
    for corpus, root in CORPORA.items():
        for p in _collect_corpus(root):
            rel = p.relative_to(root).as_posix()
            params.append(pytest.param(root, p, id=f"{corpus}-{rel}"))
    return params


_PARAMS = _corpus_params()

_CACHE: dict = {}


def _converted(source_path: Path) -> str:
    key = str(source_path)
    if key not in _CACHE:
        res = convert(source_path.read_bytes(), target_db="oracle")
        rdl = (res or {}).get("rdl_xml") or ""
        assert rdl.strip(), f"{source_path.name}: convert() returned no RDL"
        _CACHE[key] = rdl
    return _CACHE[key]


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------

def test_synthetic_report_passes_publish_semantics(synthetic_xml_bytes):
    rdl = convert(synthetic_xml_bytes, target_db="oracle")["rdl_xml"]
    assert publish_violations(rdl) == []


def _new_violations(violations, baselined):
    """The ratchet decision, isolated so it can be mutation-proved: only
    violations NOT in the baseline are fatal. A baselined file is an xfail,
    never a free pass — any NEW id on it still fails the gate."""
    base = set(baselined or ())
    return [v for v in violations if v not in base]


@pytest.mark.skipif(not _PARAMS, reason="no corpus sources present")
@pytest.mark.parametrize("corpus_root,source_path", _PARAMS)
def test_publish_semantics_gate(corpus_root, source_path):
    """Every generated RDL must survive the server's publish-time compile."""
    rel = source_path.relative_to(corpus_root).as_posix()
    violations = publish_violations(_converted(source_path))
    new = _new_violations(violations, _load_baseline(corpus_root).get(rel, ()))
    assert not new, (
        f"{rel}: PUBLISH FATAL GATE — {len(new)} violation(s) the Report "
        f"Server would reject at upload:\n  " + "\n  ".join(new))
    if violations:
        pytest.xfail(f"{rel}: {len(violations)} baselined known-open "
                     f"violation(s) ({BASELINE_NAME})")


@pytest.mark.skipif(not _PARAMS, reason="no corpus sources present")
@pytest.mark.parametrize("corpus_root,source_path", _PARAMS)
def test_publish_semantics_gate_on_the_deployed_form(corpus_root, source_path):
    """THE FILE THE OPERATOR UPLOADS is not the RDL convert() returns.

    Every download route runs the deploy transforms first (rdl_postprocess.
    deploy_transforms): each sub-report <Drillthrough> becomes a URL
    <Hyperlink> whose expression INLINES the drill-through's row
    expressions -- with or without a report-server URL. A field the
    drill-through could not legally reach therefore resurfaces as a
    Hyperlink expression the server refuses ("The Hyperlink expression for
    the text box ... refers to the field ..."). Measured on a customer's
    sub-report: the raw form audited clean, the download bounced at upload.
    So the gate audits the deployed form too, against the same ratchet."""
    from converter.rdl_postprocess import deploy_transforms
    rel = source_path.relative_to(corpus_root).as_posix()
    deployed = deploy_transforms(_converted(source_path))
    violations = publish_violations(deployed)
    new = _new_violations(violations, _load_baseline(corpus_root).get(rel, ()))
    assert not new, (
        f"{rel}: PUBLISH FATAL GATE (deployed form) — {len(new)} violation(s) "
        f"the Report Server would reject at upload of the DOWNLOADED file:\n  "
        + "\n  ".join(new))
    if violations:
        pytest.xfail(f"{rel}: {len(violations)} baselined known-open "
                     f"violation(s) ({BASELINE_NAME})")


def test_ratchet_never_becomes_a_free_pass():
    """A baselined file keeps its OLD violations as xfail but a NEW one is
    still fatal — otherwise the ratchet would silently absorb regressions."""
    known = ["publish.lookup_nested@A/Value"]
    # exactly the known violation -> nothing new -> xfail, not a hard fail
    assert _new_violations(known, known) == []
    # a NEW id on the SAME baselined file -> fatal
    assert _new_violations(known + ["publish.duplicate_name@B"], known) == \
        ["publish.duplicate_name@B"]
    # an empty / missing baseline makes everything fatal
    assert _new_violations(known, ()) == known
    assert _new_violations(known, None) == known


def test_corrupt_baseline_does_not_mask_violations(tmp_path):
    """A corrupt ratchet must be treated as ABSENT (everything fatal), never
    as 'baseline everything'."""
    (tmp_path / BASELINE_NAME).write_text("{not json", encoding="utf-8")
    assert _load_baseline(tmp_path) == {}


# ---------------------------------------------------------------------------
# Acceptance test: the REAL rejected artifact.
#
# The class is reconstructed synthetically below (machine-independent, runs
# everywhere). When a machine has the actual artifact on disk, point
# O2S_PUBLISH_GATE_ARTIFACT at it and the gate is proven on the real bytes
# that the customer's server refused.
# ---------------------------------------------------------------------------

def test_the_rejected_artifact_is_flagged():
    art = os.environ.get("O2S_PUBLISH_GATE_ARTIFACT")
    if not art or not Path(art).is_file():
        pytest.skip("O2S_PUBLISH_GATE_ARTIFACT not set to a rejected RDL")
    violations = publish_violations(Path(art).read_text(encoding="utf-8"))
    assert any(v.startswith("publish.nested_region_dataset") for v in violations), (
        "the gate did not flag the artifact the server actually rejected: "
        f"{violations}")


# ---------------------------------------------------------------------------
# Mutation proofs — one per rule class. Each doctors a clean RDL into
# EXACTLY one defect, proves the gate goes RED, and (restore) proves the
# undoctored RDL is GREEN.
# ---------------------------------------------------------------------------

def _local(tag):
    return tag.split("}", 1)[1] if "}" in tag else tag


@pytest.fixture(scope="module")
def good_rdl(synthetic_xml_bytes):
    rdl = convert(synthetic_xml_bytes, target_db="oracle")["rdl_xml"]
    assert publish_violations(rdl) == [], "synthetic RDL must start gate-clean"
    return rdl


def _doctor(rdl_xml: str, mutate) -> str:
    root = ET.fromstring(rdl_xml)
    ns = root.tag.split("}", 1)[0][1:] if root.tag.startswith("{") else ""
    if ns:
        ET.register_namespace("", ns)
    mutate(root)
    return ET.tostring(root, encoding="unicode")


def _ns(root):
    return root.tag.split("}", 1)[0] + "}" if root.tag.startswith("{") else ""


def _find_first(root, name):
    for el in root.iter():
        if _local(el.tag) == name:
            return el
    raise AssertionError(f"synthetic RDL has no <{name}> to doctor")


def _first_textbox_value(root):
    for tb in root.iter():
        if _local(tb.tag) != "Textbox":
            continue
        for v in tb.iter():
            if _local(v.tag) == "Value":
                return tb, v
    raise AssertionError("synthetic RDL has no Textbox/Value to doctor")


def _rules(rdl_xml):
    return {v["rule"] for v in audit_publish_semantics(rdl_xml)["violations"]}


def _assert_red(good, mutate, rule):
    """The doctored RDL trips exactly `rule`; the pristine one is clean."""
    doctored = _doctor(good, mutate)
    assert rule in _rules(doctored), (
        f"gate did NOT flag {rule} on the doctored RDL "
        f"(saw: {sorted(_rules(doctored))})")
    assert publish_violations(good) == [], \
        "restore check: the undoctored RDL must be clean"


# --- 3. the production class: a nested region declaring another dataset ----

def test_mutation_nested_region_dataset_goes_red(good_rdl):
    """THE class the customer's server rejected: a data region nested inside
    another declares a different DataSetName, and an expression inside it
    references a column of that declared dataset. The nested declaration is
    ignored; the expression compiles against the container's scope and the
    server refuses the report with rsFieldReference."""

    def mutate(root):
        ns = _ns(root)
        tablix = _find_first(root, "Tablix")
        # a second dataset whose column the nested region will reference
        datasets = _find_first(root, "DataSets")
        ds2 = ET.SubElement(datasets, ns + "DataSet")
        ds2.set("Name", "DS_OTHER")
        fields = ET.SubElement(ds2, ns + "Fields")
        f = ET.SubElement(fields, ns + "Field")
        f.set("Name", "OTHER_COL")
        ET.SubElement(f, ns + "DataField").text = "OTHER_COL"
        # a nested region inside the tablix, bound to that other dataset
        cell = _find_first(tablix, "TablixCell")
        contents = ET.SubElement(cell, ns + "CellContents")
        inner = ET.SubElement(contents, ns + "Tablix")
        inner.set("Name", "Nested_Region_Mutation_Proof")
        ET.SubElement(inner, ns + "DataSetName").text = "DS_OTHER"
        vis = ET.SubElement(inner, ns + "Visibility")
        ET.SubElement(vis, ns + "Hidden").text = \
            '=Not((Fields!OTHER_COL.Value = "X"))'

    _assert_red(good_rdl, mutate, "publish.nested_region_dataset")
    # ...and the field reference inside it is reported against the
    # CONTAINER's dataset, which is the server's actual complaint:
    doctored = _doctor(good_rdl, mutate)
    assert "publish.field_not_in_dataset" in _rules(doctored)


# --- 1. rsFieldInPageSectionExpression --------------------------------------

def test_mutation_field_in_page_section_goes_red(good_rdl):
    """[PGSEC] "For reports with more than one dataset, you cannot add
    fields or data-bound images directly to a header or footer." """

    def mutate(root):
        ns = _ns(root)
        datasets = _find_first(root, "DataSets")
        ds2 = ET.SubElement(datasets, ns + "DataSet")   # -> multi-dataset
        ds2.set("Name", "DS_SECOND")
        ET.SubElement(ds2, ns + "Fields")
        # RDL 2008 carries PageHeader inside <Page>; the synthetic report
        # declares none, so the page section itself is part of the fixture.
        header = ET.SubElement(_find_first(root, "Page"), ns + "PageHeader")
        tb = ET.SubElement(header, ns + "Textbox")
        tb.set("Name", "Page_Section_Field_Proof")
        paras = ET.SubElement(tb, ns + "Paragraphs")
        para = ET.SubElement(paras, ns + "Paragraph")
        runs = ET.SubElement(para, ns + "TextRuns")
        run = ET.SubElement(runs, ns + "TextRun")
        ET.SubElement(run, ns + "Value").text = "=Fields!emp_name.Value"

    _assert_red(good_rdl, mutate, "publish.field_in_page_section")


# --- 2. rsFieldReference: a field that is not in the current dataset -------

def test_mutation_field_not_in_dataset_goes_red(good_rdl):
    def mutate(root):
        _, val = _first_textbox_value(root)
        val.text = "=Fields!NoSuchColumnMutationProof.Value"

    _assert_red(good_rdl, mutate, "publish.field_not_in_dataset")


def test_mutation_field_in_scoped_aggregate_must_exist(good_rdl):
    """A dataset-scoped aggregate is the LEGAL way to reach another
    dataset — but the field must exist in the dataset it names."""
    def mutate(root):
        _, val = _first_textbox_value(root)
        val.text = '=First(Fields!NotThere.Value, "Q_MAIN")'

    _assert_red(good_rdl, mutate, "publish.field_not_in_scoped_dataset")


# --- 4. rsInvalidAggregateScope --------------------------------------------

def test_mutation_unknown_aggregate_scope_goes_red(good_rdl):
    def mutate(root):
        _, val = _first_textbox_value(root)
        val.text = '=Sum(Fields!salary.Value, "NoSuchScopeMutationProof")'

    _assert_red(good_rdl, mutate, "publish.aggregate_scope_unknown")


# --- 4b. Lookup restrictions (the class the corpus sweep actually found) ---

def _doctor_in_a_bad_drillthrough(root):
    """Give the first textbox a sub-report drill-through whose parameter
    value reaches for a field the scoped dataset does not declare -- the
    shape the deploy transforms turn into a refused <Hyperlink>."""
    ns = _ns(root)
    tb, value = _first_textbox_value(root)
    run = None
    for el in tb.iter():
        if _local(el.tag) == "TextRun":
            run = el
            break
    assert run is not None, "synthetic RDL has no TextRun to doctor"
    ai = ET.Element(ns + "ActionInfo")
    act = ET.SubElement(ET.SubElement(ai, ns + "Actions"), ns + "Action")
    dr = ET.SubElement(act, ns + "Drillthrough")
    ET.SubElement(dr, ns + "ReportName").text = "ZZQX_CHILD"
    prm = ET.SubElement(ET.SubElement(dr, ns + "Parameters"), ns + "Parameter")
    prm.set("Name", "P_ZZQX")
    # A per-record Lookup (NOT an aggregate: the deploy transform drops
    # aggregate-only parameters as generate-all links) whose DESTINATION key
    # names a field the destination dataset does not declare -- the exact
    # shape the customer's server refused.
    ET.SubElement(prm, ns + "Value").text = (
        '=Lookup(Fields!region_code.Value, Fields!zzqx_absent.Value, '
        'Fields!name.Value, "Q_MAIN")')
    # RDL TextRun order: Value, ActionInfo, Style
    kids = list(run)
    idx = next((i for i, k in enumerate(kids) if _local(k.tag) == "Value"), -1)
    run.insert(idx + 1, ai)


def test_mutation_deployed_form_hyperlink_goes_red(good_rdl):
    """THE CUSTOMER'S SECOND UPLOAD REJECTION, reproduced end to end.

    The deploy transforms rewrite a drill-through into a URL <Hyperlink>
    that inlines its parameter expressions. A parameter that reaches for
    an undeclared field is therefore a Hyperlink expression the server
    refuses. This proves (a) the deployed-form leg sees it AT THE HYPERLINK
    SITE, (b) the raw-form audit already sees the same class at the
    parameter, so the pre-download verdict cannot read clean while the
    download bounces, and (c) the pristine RDL's deployed form is clean --
    the transforms themselves introduce nothing."""
    from converter.rdl_postprocess import deploy_transforms
    doctored = _doctor(good_rdl, _doctor_in_a_bad_drillthrough)
    assert "<Drillthrough" in doctored, "the doctoring did not land"
    deployed = deploy_transforms(doctored)
    assert "<Hyperlink>" in deployed and "<Drillthrough" not in deployed, \
        "deploy_transforms must have rewritten the drill-through"
    dep_v = audit_publish_semantics(deployed)["violations"]
    assert any(str(v.get("where", "")).endswith("/Hyperlink")
               and v.get("rule") == "publish.field_not_in_scoped_dataset"
               for v in dep_v), (
        "the deployed-form leg did NOT flag the refused hyperlink: "
        f"{[(v.get('rule'), v.get('where')) for v in dep_v]}")
    raw_rules = _rules(doctored)
    assert "publish.field_not_in_scoped_dataset" in raw_rules, (
        "the raw-form audit must see the same class at the drill-through "
        f"parameter (saw {sorted(raw_rules)})")
    assert publish_violations(deploy_transforms(good_rdl)) == [], \
        "the pristine RDL's deployed form must be clean"


def test_convert_verdict_carries_the_deployed_form_violation(synthetic_xml_bytes,
                                                             monkeypatch):
    """The UI verdict is built inside convert(); it must audit the deployed
    form too, or the operator reads READY and the server bounces the file.
    Proven by making the deploy transforms inject a refused hyperlink and
    watching the verdict turn BLOCKER with that site named."""
    import converter as C
    from converter import rdl_postprocess as PP

    def _poisoned(rdl_xml, server_url="", gen_all_label=""):
        return _doctor(rdl_xml, _doctor_in_a_bad_drillthrough)
    real = PP.deploy_transforms
    monkeypatch.setattr(PP, "deploy_transforms",
                        lambda rdl, *a, **k: real(_poisoned(rdl)))
    res = C.convert(synthetic_xml_bytes, target_db="oracle")
    pf = res.get("preflight") or {}
    assert pf.get("verdict") == "BLOCKER", pf.get("verdict")
    assert any("/Hyperlink" in str(i.get("message", "")) for i in pf.get("issues", [])), (
        "the verdict does not name the refused hyperlink site: "
        f"{[i.get('message', '')[:80] for i in pf.get('issues', [])][:6]}")


def test_mutation_nested_lookup_goes_red(good_rdl):
    """[Lookup] "Only one level of lookup is supported. A source,
    destination, or result expression can't include a reference to a lookup
    function." This is the real defect the promotion sweep found on a
    customer report."""
    def mutate(root):
        _, val = _first_textbox_value(root)
        val.text = ('=Lookup(Lookup(Fields!emp_name.Value, '
                    'Fields!emp_name.Value, Fields!salary.Value, "Q_MAIN"), '
                    'Fields!emp_name.Value, Fields!salary.Value, "Q_MAIN")')

    _assert_red(good_rdl, mutate, "publish.lookup_nested")


def test_mutation_lookup_in_report_parameter_goes_red(good_rdl):
    """[Lookup] Lookup "can't be used as an expression for ... Report
    parameters." """
    def mutate(root):
        ns = _ns(root)
        param = _find_first(root, "ReportParameter")
        for dv in param.iter():
            if _local(dv.tag) == "Value":
                dv.text = ('=Lookup(Fields!a.Value, Fields!b.Value, '
                           'Fields!c.Value, "Q_MAIN")')
                return
        raise AssertionError("no parameter default Value to doctor")

    _assert_red(good_rdl, mutate, "publish.lookup_in_forbidden_location")


# --- 5. ReportItems! (zero corpus sites — proven capable here) ------------

def test_mutation_report_item_undefined_goes_red(good_rdl):
    def mutate(root):
        _, val = _first_textbox_value(root)
        val.text = "=ReportItems!NoSuchTextboxMutationProof.Value"

    _assert_red(good_rdl, mutate, "publish.report_item_undefined")


# --- 6. rsAggregateOfNonNumericData (corpus-reachable; see header census) --

_RD_NS = "{http://schemas.microsoft.com/SQLServer/reporting/reportdesigner}"


def test_mutation_aggregate_of_non_numeric_goes_red(good_rdl):
    """[ERRMSG] rsAggregateOfNonNumericData: "Numeric aggregate functions
    (Sum, Avg, StDev, Var, StDevP, and VarP) can only aggregate numeric
    data." The field type lives in the rd:TypeName CHILD ELEMENT, which the
    emitter writes on every field (1,896 of the corpus's fields are
    System.String), so this class is corpus-reachable, not theoretical."""
    def mutate(root):
        ns = _ns(root)
        ds = _find_first(root, "DataSet")
        fields = next(f for f in ds.iter() if _local(f.tag) == "Fields")
        fld = ET.SubElement(fields, ns + "Field")
        fld.set("Name", "TEXT_COL")
        ET.SubElement(fld, ns + "DataField").text = "TEXT_COL"
        ET.SubElement(fld, _RD_NS + "TypeName").text = "System.String"
        _, val = _first_textbox_value(root)
        val.text = "=Sum(Fields!TEXT_COL.Value)"

    _assert_red(good_rdl, mutate, "publish.aggregate_of_non_numeric")


def test_numeric_aggregate_rule_accepts_a_numeric_field(good_rdl):
    """The companion direction: the same shape over a DECIMAL field must
    stay GREEN, so the rule discriminates on type rather than on shape."""
    def mutate(root):
        ns = _ns(root)
        ds = _find_first(root, "DataSet")
        fields = next(f for f in ds.iter() if _local(f.tag) == "Fields")
        fld = ET.SubElement(fields, ns + "Field")
        fld.set("Name", "NUM_COL")
        ET.SubElement(fld, ns + "DataField").text = "NUM_COL"
        ET.SubElement(fld, _RD_NS + "TypeName").text = "System.Decimal"
        _, val = _first_textbox_value(root)
        val.text = "=Sum(Fields!NUM_COL.Value)"

    assert "publish.aggregate_of_non_numeric" not in _rules(
        _doctor(good_rdl, mutate))


# --- 7. restricted expression locations ------------------------------------

def test_mutation_aggregate_in_group_expression_goes_red(good_rdl):
    """[MS-RDL] "The value of the GroupExpressions.GroupExpression element
    MUST NOT include any aggregate functions other than the RowNumber
    aggregate function." """
    def mutate(root):
        ns = _ns(root)
        # the synthetic report's Group is static (no group expression), so
        # the grouping itself is part of the fixture.
        grp = _find_first(root, "Group")
        exprs = ET.SubElement(grp, ns + "GroupExpressions")
        ET.SubElement(exprs, ns + "GroupExpression").text = \
            "=Sum(Fields!salary.Value)"

    _assert_red(good_rdl, mutate, "publish.aggregate_in_group_expression")


def test_mutation_fields_in_report_parameter_goes_red(good_rdl):
    """[AGGREF] the Fields column reads "No" for Report Parameter."""
    def mutate(root):
        param = _find_first(root, "ReportParameter")
        for dv in param.iter():
            if _local(dv.tag) == "Value":
                dv.text = "=Fields!emp_name.Value"
                return
        raise AssertionError("no parameter default Value to doctor")

    _assert_red(good_rdl, mutate, "publish.fields_in_restricted_location")


# --- 9. names ---------------------------------------------------------------

def test_mutation_duplicate_name_goes_red(good_rdl):
    """[MS-RDL] a name "MUST be unique among all datasets, data regions, and
    groups in the report"."""
    def mutate(root):
        tablix = _find_first(root, "Tablix")
        ds = _find_first(root, "DataSet")
        tablix.set("Name", ds.get("Name"))

    _assert_red(good_rdl, mutate, "publish.duplicate_name")


def test_mutation_non_cls_name_goes_red(good_rdl):
    def mutate(root):
        _find_first(root, "Tablix").set("Name", "not a cls identifier!")

    _assert_red(good_rdl, mutate, "publish.name_not_cls_identifier")


# --- 10. parameters ---------------------------------------------------------

def test_mutation_parameter_forward_reference_goes_red(good_rdl):
    """[AGGREF] Report Parameter row, Parameters column: "Only parameters
    earlier in the list"."""
    def mutate(root):
        params = [p for p in root.iter()
                  if _local(p.tag) == "ReportParameter"]
        assert len(params) >= 2, "need two parameters to prove ordering"
        first, last = params[0], params[-1]
        for dv in first.iter():
            if _local(dv.tag) == "Value":
                dv.text = f"=Parameters!{last.get('Name')}.Value"
                return
        raise AssertionError("no parameter default Value to doctor")

    _assert_red(good_rdl, mutate, "publish.parameter_forward_reference")


def test_mutation_parameter_unknown_dataset_goes_red(good_rdl):
    def mutate(root):
        ns = _ns(root)
        param = _find_first(root, "ReportParameter")
        vv = ET.SubElement(param, ns + "ValidValues")
        dsr = ET.SubElement(vv, ns + "DataSetReference")
        ET.SubElement(dsr, ns + "DataSetName").text = "NoSuchDataSetProof"

    _assert_red(good_rdl, mutate, "publish.parameter_dataset_reference_unknown")


# --- 12. images -------------------------------------------------------------

def test_mutation_embedded_image_undeclared_goes_red(good_rdl):
    """[MS-RDL] Image.Value with Source 'Embedded' "MUST be ... the name of
    an EmbeddedImage in the report"."""
    def mutate(root):
        ns = _ns(root)
        body = _find_first(root, "Body")
        img = ET.SubElement(body, ns + "Image")
        img.set("Name", "Image_Mutation_Proof")
        ET.SubElement(img, ns + "Source").text = "Embedded"
        ET.SubElement(img, ns + "Value").text = "NoSuchEmbeddedImageProof"

    _assert_red(good_rdl, mutate, "publish.image_embedded_undeclared")


def test_mutation_database_image_without_mimetype_goes_red(good_rdl):
    """[MS-RDL] "if Image.Source is set to 'Database', the Image.MIMEType
    element MUST be specified"."""
    def mutate(root):
        ns = _ns(root)
        body = _find_first(root, "Body")
        img = ET.SubElement(body, ns + "Image")
        img.set("Name", "Image_Db_Proof")
        ET.SubElement(img, ns + "Source").text = "Database"
        ET.SubElement(img, ns + "Value").text = "=Fields!blob.Value"

    _assert_red(good_rdl, mutate, "publish.image_database_no_mimetype")


# --- 13. charts (one corpus chart — proven capable here) ------------------

def test_mutation_chart_series_without_data_points_goes_red(good_rdl):
    def mutate(root):
        ns = _ns(root)
        body = _find_first(root, "Body")
        chart = ET.SubElement(body, ns + "Chart")
        chart.set("Name", "Chart_Mutation_Proof")
        coll = ET.SubElement(chart, ns + "ChartData")
        sers = ET.SubElement(coll, ns + "ChartSeriesCollection")
        s = ET.SubElement(sers, ns + "ChartSeries")
        s.set("Name", "Series_Proof")

    _assert_red(good_rdl, mutate, "publish.chart_series_without_data_points")


# --- 14. subreports + tablix shape (zero corpus subreports) ---------------

def test_mutation_subreport_without_report_name_goes_red(good_rdl):
    def mutate(root):
        ns = _ns(root)
        body = _find_first(root, "Body")
        sub = ET.SubElement(body, ns + "Subreport")
        sub.set("Name", "Subreport_Mutation_Proof")

    _assert_red(good_rdl, mutate, "publish.subreport_no_report_name")


def test_mutation_tablix_cell_count_mismatch_goes_red(good_rdl):
    """rsInvalidTablix*: every row must carry exactly one cell per column."""
    def mutate(root):
        tablix = _find_first(root, "Tablix")
        for cells in tablix.iter():
            if _local(cells.tag) == "TablixCells":
                kids = [c for c in cells if _local(c.tag) == "TablixCell"]
                assert kids, "no TablixCell to drop"
                cells.remove(kids[0])
                return
        raise AssertionError("no TablixCells to doctor")

    _assert_red(good_rdl, mutate, "publish.tablix_cell_count_mismatch")


# --- 11. expression text the server rejects --------------------------------

def test_mutation_lone_equals_expression_goes_red(good_rdl):
    def mutate(root):
        _, val = _first_textbox_value(root)
        val.text = "="

    _assert_red(good_rdl, mutate, "publish.expression_empty")


def test_mutation_unbalanced_quote_goes_red(good_rdl):
    def mutate(root):
        _, val = _first_textbox_value(root)
        val.text = '=Fields!emp_name.Value & "unterminated'

    _assert_red(good_rdl, mutate, "publish.expression_unbalanced_quote")


# ---------------------------------------------------------------------------
# The gate's own contract
# ---------------------------------------------------------------------------

def test_audit_never_raises_on_hostile_input():
    """A validator that explodes is a validator that cannot gate."""
    for payload in ("", "   ", "<not-rdl/>", "<Report><Body/></Report>",
                    "not xml at all <<<", "<Report><Body><Textbox>"):
        res = audit_publish_semantics(payload)
        assert isinstance(res, dict) and "violations" in res


def test_baseline_files_never_live_in_the_repo():
    """The ratchet belongs NEXT TO the corpus it describes — a baseline
    committed to the repo would silently travel to other machines."""
    assert not list(REPO_ROOT.rglob(BASELINE_NAME)), (
        f"{BASELINE_NAME} must never be committed to the repo")
