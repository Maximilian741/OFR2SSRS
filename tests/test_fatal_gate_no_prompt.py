"""
NO-PROMPT FATAL GATE — corpus-wide.

The machine version of the user's two Report Builder rituals. Two errors are
project-fatal and must be IMPOSSIBLE to ship:

  (1) the generated RDL failing at UPLOAD because the emitted query is broken;
  (2) Report Builder throwing the design-time "Define Query Parameters"
      prompt when the user refreshes datasets.

Every source in every available corpus is converted and the resulting RDL is
audited on five legs (see converter.validators.no_prompt_gate):

  L1  CommandText is STATIC SQL — never an expression (no leading '=')
  L2  every bind referenced by live SQL is declared as a QueryParameter of
      that DataSet, with a non-empty Value whose Parameters!X.Value refs name
      declared ReportParameters with exact case
  L3  every ReportParameter carries a usable default (Values/Value entry such
      as =Nothing, or a DataSetReference) and NEVER an empty <Value/> — the
      exact historical prompt trigger
  L4  DataSources use the shared-reference form (embedded connections are the
      deliberate rdl_postprocess path only, never the default convert path)
  L5  every Field's DataField matches a column the static SELECT list
      actually yields (the ORA-00904 / silent-Nothing data-loss class),
      reusing the repo's SELECT-alias extraction. Star-side leg: explicit
      aliases beside a star stay checkable (field = alias+numeric-suffix is
      a proven derivation desync), and a star over a single inline view
      resolves to the inner select list — full membership checking plus the
      ORA-00918 duplicate-column class that kills SELECT O.* at refresh

Corpora are optional (CI machines without them skip cleanly); the synthetic
conftest report and any repo-bundled samples keep the gate alive everywhere.

KNOWN-OPEN RATCHET: a corpus directory may carry a NO_PROMPT_GATE_BASELINE.json
(next to the sources, never in the repo) listing violations that are known
converter backlog on wild shapes. A file whose violations are ALL baselined
xfails (visible, tracked); any NEW violation anywhere is a hard failure.

Each of the five legs is mutation-proved below: a good RDL is doctored to
violate exactly that leg and the gate must go red on it.
"""
from __future__ import annotations

import json
import os
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from converter import convert
from converter.validators.no_prompt_gate import audit_no_prompt

REPO_ROOT = Path(__file__).resolve().parent.parent

BASELINE_NAME = "NO_PROMPT_GATE_BASELINE.json"

# External corpora: entirely optional — absent directories simply contribute
# no cases. Paths are overridable so any machine can point the gate at its
# own harvest without touching repo code.
CORPORA = {
    "samples": REPO_ROOT / "samples" / "oracle",
    # Synthesized non-Latin-script fixtures (Greek UTF-8, Cyrillic
    # WINDOWS-1251, Arabic WINDOWS-1256, CJK UTF-8) — repo-bundled like the
    # samples leg, so the gate holds over every script/encoding forever.
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
        rel_parts = p.relative_to(root).parts[:-1]
        if any(part.startswith("_") for part in rel_parts):
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
            params.append(pytest.param(
                root, p, id=f"{corpus}-{rel}"))
    return params


_PARAMS = _corpus_params()


def _gate(xml_bytes: bytes, source_name: str, baselined=()):
    """Convert one source and hold the five-leg gate on the result."""
    res = convert(xml_bytes, target_db="oracle")
    rdl = (res or {}).get("rdl_xml") or ""
    assert rdl.strip(), f"{source_name}: convert() returned no RDL"
    violations = audit_no_prompt(rdl)
    new = [v for v in violations if v not in set(baselined)]
    assert not new, (
        f"{source_name}: NO-PROMPT FATAL GATE — {len(new)} violation(s):\n  "
        + "\n  ".join(new))
    if violations:
        # Everything found is known converter backlog: keep it visible as an
        # expected failure instead of silently passing.
        pytest.xfail(
            f"{source_name}: {len(violations)} baselined known-open "
            f"violation(s) ({BASELINE_NAME})")


# ---------------------------------------------------------------------------
# The gate itself
# ---------------------------------------------------------------------------

def test_synthetic_report_passes_no_prompt_gate(synthetic_xml_bytes):
    """Always runs — the gate exists even with zero corpora present."""
    _gate(synthetic_xml_bytes, "conftest-synthetic")


@pytest.mark.skipif(not _PARAMS, reason="no corpus sources present")
@pytest.mark.parametrize("corpus_root,source_path", _PARAMS)
def test_no_prompt_fatal_gate(corpus_root, source_path):
    baseline = _load_baseline(corpus_root)
    rel = source_path.relative_to(corpus_root).as_posix()
    _gate(source_path.read_bytes(), rel, baselined=baseline.get(rel, ()))


# ---------------------------------------------------------------------------
# Mutation proofs — the gate must go RED when a leg is violated on purpose.
# Each doctors the known-good synthetic RDL into exactly one failure class.
# ---------------------------------------------------------------------------

def _local(tag):
    return tag.split("}", 1)[1] if "}" in tag else tag


@pytest.fixture(scope="module")
def good_rdl(synthetic_xml_bytes):
    res = convert(synthetic_xml_bytes, target_db="oracle")
    rdl = res["rdl_xml"]
    assert audit_no_prompt(rdl) == [], "synthetic RDL must start gate-clean"
    return rdl


def _doctor(rdl_xml: str, mutate) -> str:
    """Parse namespace-preserving, apply ``mutate(root)``, serialize."""
    root = ET.fromstring(rdl_xml)
    ns = root.tag.split("}", 1)[0][1:] if root.tag.startswith("{") else ""
    if ns:
        ET.register_namespace("", ns)
    mutate(root)
    return ET.tostring(root, encoding="unicode")


def _find_first(root, name):
    for el in root.iter():
        if _local(el.tag) == name:
            return el
    raise AssertionError(f"synthetic RDL has no <{name}> to doctor")


def _find_parent_of(root, child):
    for el in root.iter():
        if child in list(el):
            return el
    raise AssertionError("parent not found")


def test_mutation_L1_expression_commandtext_goes_red(good_rdl):
    def mutate(root):
        ct = _find_first(root, "CommandText")
        ct.text = '="SELECT 1 FROM DUAL WHERE X = " & Parameters!P_YEAR.Value'
    v = audit_no_prompt(_doctor(good_rdl, mutate))
    assert any(x.startswith("L1:expression_commandtext") for x in v), v


def test_mutation_L2_undeclared_bind_goes_red(good_rdl):
    def mutate(root):
        qp = _find_first(root, "QueryParameter")
        _find_parent_of(root, qp).remove(qp)
    v = audit_no_prompt(_doctor(good_rdl, mutate))
    assert any(x.startswith("L2:bind_undeclared") for x in v), v


def test_mutation_L2_empty_queryparam_value_goes_red(good_rdl):
    def mutate(root):
        qp = _find_first(root, "QueryParameter")
        for child in qp:
            if _local(child.tag) == "Value":
                child.text = None  # the exact historical <Value/> trigger
    v = audit_no_prompt(_doctor(good_rdl, mutate))
    assert any(x.startswith("L2:qp_empty_value") for x in v), v


def test_mutation_L2_wrong_case_param_ref_goes_red(good_rdl):
    def mutate(root):
        qp = _find_first(root, "QueryParameter")
        for child in qp:
            if _local(child.tag) == "Value":
                child.text = "=Parameters!p_ReGiOn_notdeclared.Value"
    v = audit_no_prompt(_doctor(good_rdl, mutate))
    assert any(x.startswith("L2:qp_param_undeclared") for x in v), v


def test_mutation_L3_empty_default_value_goes_red(good_rdl):
    def mutate(root):
        rp = _find_first(root, "ReportParameter")
        dv = _find_first(rp, "DefaultValue")
        val = _find_first(dv, "Value")
        val.text = None  # empty <Value/> — the exact historical trigger
    v = audit_no_prompt(_doctor(good_rdl, mutate))
    assert any(x.startswith("L3:param_default_empty_value") for x in v), v


def test_mutation_L3_missing_default_goes_red(good_rdl):
    def mutate(root):
        rp = _find_first(root, "ReportParameter")
        dv = _find_first(rp, "DefaultValue")
        rp.remove(dv)
    v = audit_no_prompt(_doctor(good_rdl, mutate))
    assert any(x.startswith("L3:param_default_missing") for x in v), v


def test_mutation_L4_embedded_connection_goes_red(good_rdl):
    def mutate(root):
        ds = _find_first(root, "DataSource")
        ref = _find_first(ds, "DataSourceReference")
        ds.remove(ref)
        ns = root.tag.split("}", 1)[0] + "}" if root.tag.startswith("{") else ""
        conn = ET.SubElement(ds, f"{ns}ConnectionProperties")
        ET.SubElement(conn, f"{ns}DataProvider").text = "ORACLE"
        ET.SubElement(conn, f"{ns}ConnectString").text = "Data Source=x"
    doctored = _doctor(good_rdl, mutate)
    v = audit_no_prompt(doctored)
    assert any(x.startswith("L4:embedded_connection") for x in v), v
    # ...and the DELIBERATE embedded path (caller supplied a connection)
    # is allowed when declared as such:
    assert not [x for x in audit_no_prompt(doctored, allow_embedded=True)
                if x.startswith("L4:")]


def test_mutation_L4_referenceless_datasource_goes_red(good_rdl):
    def mutate(root):
        ds = _find_first(root, "DataSource")
        ref = _find_first(ds, "DataSourceReference")
        ds.remove(ref)
    v = audit_no_prompt(_doctor(good_rdl, mutate))
    assert any(x.startswith("L4:datasource_no_reference") for x in v), v


def test_mutation_L5_dangling_datafield_goes_red(good_rdl):
    def mutate(root):
        fld = _find_first(root, "Field")
        for child in fld:
            if _local(child.tag) == "DataField":
                child.text = "NO_SUCH_COLUMN_MUTATION_PROOF"
    v = audit_no_prompt(_doctor(good_rdl, mutate))
    assert any(x.startswith("L5:field_not_in_select") for x in v), v


# ---------------------------------------------------------------------------
# L5 star-side leg — a star projection no longer blinds the whole dataset.
# Explicit aliases stay checkable next to an opaque star, and a star over a
# single inline view (the converter's own wrap shape) is fully resolvable.
# Each RED case is a mutation proof; the green cases pin the leg's honesty
# (benign shapes and full coverage must NOT cry wolf).
# ---------------------------------------------------------------------------

def test_mutation_L5_star_near_miss_goes_red(good_rdl):
    """Opaque star + explicit alias: a Field named <alias>+<numeric suffix>
    is a proven field/alias derivation desync even though the star's
    contents are unknowable — the SELECT yields only the unsuffixed
    alias."""
    def mutate(root):
        ct = _find_first(root, "CommandText")
        ct.text = ("SELECT e.*, TO_CHAR(hire_date, 'YYYY') AS hire_year "
                   "FROM employees e")
        fld = _find_first(root, "Field")
        for child in fld:
            if _local(child.tag) == "DataField":
                child.text = "HIRE_YEAR_2"
    v = audit_no_prompt(_doctor(good_rdl, mutate))
    assert any(x.startswith("L5:star_alias_near_miss") for x in v), v


def test_star_reverse_near_miss_is_never_flagged(good_rdl):
    """Field = stem while the explicit alias = stem+suffix is the benign
    covered-by-star shape (wild-corpus proven: a stub alias deliberately
    suffixed AROUND a star column). The leg must stay silent on it."""
    def mutate(root):
        ct = _find_first(root, "CommandText")
        ct.text = "SELECT e.*, NULL AS region_code1 FROM employees e"
    v = audit_no_prompt(_doctor(good_rdl, mutate))
    assert not [x for x in v if x.startswith("L5:")], v


def test_mutation_L5_star_view_duplicate_goes_red(good_rdl):
    """SELECT O.* over an inline view whose select list yields the same
    column name twice = ORA-00918 at Refresh Fields (the duplicate
    join-key class the wild corpus shipped 8 times)."""
    def mutate(root):
        ct = _find_first(root, "CommandText")
        ct.text = ("SELECT O.* FROM (SELECT 1 AS region_code, 2 AS name, "
                   "3 AS hire_year, 4 AS dup_col, 5 AS dup_col "
                   "FROM DUAL) O")
    v = audit_no_prompt(_doctor(good_rdl, mutate))
    assert any(x.startswith("L5:star_view_duplicate_column") for x in v), v


def test_mutation_L5_resolved_star_dangling_field_goes_red(good_rdl):
    """When the star resolves to a single inline view, membership is fully
    checkable: a Field the view + explicit items never yield goes red."""
    def mutate(root):
        ct = _find_first(root, "CommandText")
        ct.text = ("SELECT O.* FROM (SELECT 1 AS name, 2 AS hire_year "
                   "FROM DUAL) O")
    v = audit_no_prompt(_doctor(good_rdl, mutate))
    assert any(x.startswith("L5:field_not_in_select")
               and "region_code" in x for x in v), v


def test_resolved_star_with_full_coverage_stays_green(good_rdl):
    """The de-abstain must not cry wolf: a resolved star that yields every
    declared DataField passes the leg clean."""
    def mutate(root):
        ct = _find_first(root, "CommandText")
        ct.text = ("SELECT O.* FROM (SELECT 1 AS region_code, 2 AS name, "
                   "3 AS hire_year FROM DUAL) O")
    v = audit_no_prompt(_doctor(good_rdl, mutate))
    assert not [x for x in v if x.startswith("L5:")], v
