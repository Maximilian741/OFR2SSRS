"""
UPLOAD FATAL GATE — corpus-wide.

The machine version of project-fatal error #1: the generated RDL failing at
UPLOAD (or first execution) on the real server. Every source in every
available corpus is converted and the resulting RDL must survive four legs:

  L-A  XSD      — schema-valid against Microsoft's REAL RDL 2008/01 schema
                  (the bundled tests/fixtures/schema copy; SSRS validates
                  exactly this at upload).
  L-B  SQL      — every DataSet's CommandText is SQL the server would accept:
                  parses under the repo's Oracle-grammar rail (judged
                  DIFFERENTIALLY against the report's own source query, the
                  sql_syntax rule), no star-aliased expansion
                  ("SELECT X.* AS X" — hard ORA-00923, shipped once), no
                  dangling lexical placeholder that would reach the server as
                  literal "&P_X" text, no unterminated string literal, and
                  never an expression-valued CommandText.
  L-C  ENGINE   — the RDL actually LOADS through Microsoft's LocalReport
                  engine: LoadReportDefinition + GetParameters(), the
                  definition compile that is the strongest local analog of
                  publish-time validation. GetParameters() is essential:
                  LoadReportDefinition alone defers validation and "loads"
                  garbage. Inputs are staticized (ms_layout) because a live
                  expression host cannot start under a PowerShell appbase /
                  this machine's Application Control policy; expression
                  compilation is L-D's job. Engine-gated: machines without
                  the harness SKIP this leg — never a silent pass.
  L-D  COMPILE  — every generated =expression (and the <Code> block) compiles
                  through the real VB.NET compiler (vb_expr_check rail), on a
                  DETERMINISTIC SHA-ROTATED SAMPLE per run: full corpus x
                  full compile is minutes, so each day a different sha bucket
                  of reports is compiled and coverage rotates through the
                  whole corpus. O2S_UPLOAD_GATE_COMPILE_ALL=1 compiles
                  everything (release mode).

Corpora are optional (CI machines without them skip cleanly); the synthetic
conftest report keeps every leg alive everywhere.

KNOWN-OPEN RATCHET: a corpus directory may carry an UPLOAD_GATE_BASELINE.json
(next to the sources, NEVER in the repo) mapping rel-path -> [violation
strings] that are known converter backlog on those shapes. A file whose
violations are ALL baselined xfails (visible, tracked); any NEW violation
anywhere is a hard failure.

Every leg is mutation-proved below: a known-good RDL is doctored into exactly
one failure class and the leg must go RED on it.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

import pytest

from converter import convert
from converter.parsers.oracle_xml import parse_oracle_xml
from converter.validators.preflight import preflight_audit
from converter.validators.sql_syntax import (
    _has_executable_sql,
    differential_issues,
    grammar_available,
    parse_check,
)
from converter.validators.vb_expr_check import check_rdl_expressions
from converter.validators.no_prompt_gate import _live_sql

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_NAME = "UPLOAD_GATE_BASELINE.json"
_LOAD_PS1 = Path(__file__).resolve().parent / "upload_gate_load_check.ps1"
_RENDERLAB = REPO_ROOT / "tools" / "renderlab"

# External corpora: entirely optional — absent directories simply contribute
# no cases. Paths are overridable so any machine can point the gate at its
# own harvest without touching repo code. (Same roster as the no-prompt gate.)
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
            params.append(pytest.param(root, p, id=f"{corpus}-{rel}"))
    return params


_PARAMS = _corpus_params()


# ---------------------------------------------------------------------------
# Shared conversion cache: one convert() + one source parse per corpus file,
# reused by every leg (the corpus is converted once, not four times).
# ---------------------------------------------------------------------------

_CACHE: dict = {}


def _converted(source_path: Path):
    key = str(source_path)
    if key not in _CACHE:
        data = source_path.read_bytes()
        res = convert(data, target_db="oracle")
        rdl = (res or {}).get("rdl_xml") or ""
        assert rdl.strip(), f"{source_path.name}: convert() returned no RDL"
        try:
            parsed = parse_oracle_xml(data)
        except Exception:  # noqa: BLE001 — differential leg degrades to
            parsed = None  # "no opinion"; the other legs still run
        _CACHE[key] = (rdl, parsed)
    return _CACHE[key]


# ---------------------------------------------------------------------------
# L-A: XSD schema validation (reuses the repo-bundled real 2008/01 schema)
# ---------------------------------------------------------------------------

_BUNDLED_XSD = REPO_ROOT / "tests" / "fixtures" / "schema" / "ReportDefinition_2008.xsd"
_SCHEMA = None


def _schema():
    """The real RDL 2008/01 XMLSchema, or None when unavailable."""
    global _SCHEMA
    if _SCHEMA is None:
        try:
            from lxml import etree
        except Exception:  # noqa: BLE001
            _SCHEMA = False
            return None
        env = os.environ.get("O2S_RDL_XSD")
        p = Path(env) if env and Path(env).exists() else _BUNDLED_XSD
        if not p.exists():
            _SCHEMA = False
            return None
        _SCHEMA = etree.XMLSchema(etree.parse(str(p)))
    return _SCHEMA or None


_NSURI_RE = re.compile(r"\{[^}]*\}")


def _xsd_violations(rdl_xml: str):
    """Stable violation strings (namespace URIs and line numbers stripped so
    a baseline entry survives unrelated layout edits)."""
    schema = _schema()
    assert schema is not None, "XSD leg called without a schema"
    from lxml import etree
    try:
        tree = etree.fromstring(rdl_xml.encode("utf-8"))
    except Exception as exc:  # noqa: BLE001 — unparsable XML IS a violation
        return [f"xsd:not-xml:{type(exc).__name__}"]
    if schema.validate(tree):
        return []
    out = set()
    for e in schema.error_log:
        out.add("xsd:" + _NSURI_RE.sub("", e.message).strip())
    return sorted(out)


# ---------------------------------------------------------------------------
# L-B: SQL the server would accept
# ---------------------------------------------------------------------------

# Preflight rules that are UPLOAD/EXECUTION-fatal SQL classes. (RED/AMBER
# lexical classes — dropped WHERE, NULL-stubbed operand — run but return
# wrong/zero rows; they are disclosure rules, not upload failures.)
_FATAL_RULES = {"sql.star_alias", "rdl.expression_commandtext"}
_FATAL_RULE_PREFIXES = (
    "sql.unparsable.",          # the differential grammar rail (via convert)
    "sql.lexical_identifier.",  # identifier fragment lost -> ORA-00903/936
    "sql.lexical_tablesource.", # whole FROM source lost -> invalid SQL
    "sql.arity",                # UNION branch arity -> ORA-01789
)

_RAW_LEXICAL_RE = re.compile(r"&+[A-Za-z_]\w*")


def _dataset_command_texts(rdl_xml: str):
    """[(dataset name, CommandText)] straight from the generated RDL."""
    root = ET.fromstring(rdl_xml)
    out = []
    for el in root.iter():
        if el.tag.split("}")[-1] != "DataSet":
            continue
        name = el.get("Name") or f"ds{len(out)}"
        for sub in el.iter():
            if sub.tag.split("}")[-1] == "CommandText":
                out.append((name, sub.text or ""))
                break
    return out


def _unterminated_string_literal(sql: str) -> bool:
    """True when a single-quoted literal opens and never closes (Oracle
    escapes an inner quote by doubling it). Comment spans are skipped."""
    n = len(sql)
    i = 0
    while i < n:
        c = sql[i]
        if c == "'":
            j = i + 1
            closed = False
            while j < n:
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":
                        j += 2
                        continue
                    closed = True
                    break
                j += 1
            if not closed:
                return True
            i = j + 1
        elif sql.startswith("--", i):
            j = sql.find("\n", i)
            i = n if j < 0 else j
        elif sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            if j < 0:
                return False  # unterminated comment swallows the rest
            i = j + 2
        else:
            i += 1
    return False


def _sql_violations(rdl_xml: str, source_report=None):
    """Upload-fatal SQL violations for one generated RDL.

    ``source_report`` (the parsed Oracle source) enables the repo's
    DIFFERENTIAL grammar rule — only queries WE broke are reported, never
    valid customer constructs the third-party grammar cannot model. Without
    a source (mutation proofs, synthetic doctoring) generated CommandTexts
    are judged on their own.
    """
    out = set()

    # The repo's preflight rail: star-alias, BLOCKER lexical classes,
    # expression-valued CommandText, literal &lexical refs.
    pre = preflight_audit(rdl_xml, target_db="oracle")
    for it in pre.get("issues") or []:
        rule = it.get("rule", "")
        if (rule in _FATAL_RULES
                or rule.startswith(_FATAL_RULE_PREFIXES)
                or rule.endswith(".lexical_ref")):
            out.add(f"sql:{rule}")

    ds = _dataset_command_texts(rdl_xml)

    # The repo's Oracle-grammar rail (sqlglot; "no opinion" when absent).
    if source_report is not None:
        for it in differential_issues(source_report, dict(ds)):
            out.add(f"sql:{it.get('rule', 'sql.unparsable')}")
    else:
        for name, sql in ds:
            if not _has_executable_sql(sql):
                continue  # deliberate comment-only placeholder, no SQL to judge
            ok, _err = parse_check(sql)
            if not ok:
                out.add(f"sql:parse.{name}")

    # Direct scans on every CommandText (defense in depth; each is a
    # zero-false-positive class on GENERATED text).
    for name, sql in ds:
        if sql.lstrip().startswith("="):
            out.add(f"sql:scan.expression_commandtext.{name}")
        if _unterminated_string_literal(sql):
            out.add(f"sql:scan.unterminated_quote.{name}")
        for amp in sorted({m.lstrip("&")
                           for m in _RAW_LEXICAL_RE.findall(_live_sql(sql))}):
            # live (non-comment, non-literal) '&P_X' would be sent to the
            # server verbatim — Oracle reads '&' as a syntax error mid-query.
            out.add(f"sql:scan.raw_lexical.{name}.{amp}")
    return sorted(out)


# ---------------------------------------------------------------------------
# Baseline plumbing (identical contract to the no-prompt gate)
# ---------------------------------------------------------------------------

def _gate_leg(violations, baselined, what, leg):
    base = set(baselined or ())
    new = [v for v in violations if v not in base]
    assert not new, (
        f"{what}: UPLOAD FATAL GATE ({leg}) — {len(new)} violation(s):\n  "
        + "\n  ".join(new))
    if violations:
        pytest.xfail(f"{what}: {len(violations)} baselined known-open "
                     f"violation(s) ({BASELINE_NAME})")


# ---------------------------------------------------------------------------
# The gate itself — synthetic first (alive on machines with zero corpora)
# ---------------------------------------------------------------------------

def test_synthetic_report_passes_upload_gate_xsd(synthetic_xml_bytes):
    if _schema() is None:
        pytest.skip("RDL 2008 XSD not available (set O2S_RDL_XSD or bundle it)")
    rdl = convert(synthetic_xml_bytes, target_db="oracle")["rdl_xml"]
    assert _xsd_violations(rdl) == []


def test_synthetic_report_passes_upload_gate_sql(synthetic_xml_bytes, parsed_report):
    rdl = convert(synthetic_xml_bytes, target_db="oracle")["rdl_xml"]
    assert _sql_violations(rdl, parsed_report) == []
    # And judged standalone (no differential shield) it is still clean SQL.
    assert _sql_violations(rdl) == []


@pytest.mark.skipif(not _PARAMS, reason="no corpus sources present")
@pytest.mark.parametrize("corpus_root,source_path", _PARAMS)
def test_upload_gate_xsd(corpus_root, source_path):
    if _schema() is None:
        pytest.skip("RDL 2008 XSD not available (set O2S_RDL_XSD or bundle it)")
    rel = source_path.relative_to(corpus_root).as_posix()
    rdl, _parsed = _converted(source_path)
    _gate_leg(_xsd_violations(rdl),
              _load_baseline(corpus_root).get(rel, ()), rel, "L-A xsd")


@pytest.mark.skipif(not _PARAMS, reason="no corpus sources present")
@pytest.mark.parametrize("corpus_root,source_path", _PARAMS)
def test_upload_gate_sql(corpus_root, source_path):
    rel = source_path.relative_to(corpus_root).as_posix()
    rdl, parsed = _converted(source_path)
    _gate_leg(_sql_violations(rdl, parsed),
              _load_baseline(corpus_root).get(rel, ()), rel, "L-B sql")


# ---------------------------------------------------------------------------
# L-C: the engine actually loads the RDL (one batched invocation — a PS
# process per report would take minutes; one process loads the whole corpus
# in seconds)
# ---------------------------------------------------------------------------

def _engine_harness():
    """(powershell, staticize, libdir) or a pytest.skip. Engine-gated cases
    SKIP on machines without the harness — they are never green by absence."""
    if sys.platform != "win32":
        pytest.skip("MS ReportViewer engine harness is Windows-only")
    ps = shutil.which("powershell") or shutil.which("pwsh")
    if ps is None:
        pytest.skip("PowerShell unavailable — engine leg skipped (not passed)")
    if not _LOAD_PS1.exists():
        pytest.skip("upload_gate_load_check.ps1 missing")
    sys.path.insert(0, str(_RENDERLAB))
    try:
        from render import lib_ready
        from ms_layout import staticize
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"renderlab import failed: {exc}")
    if not lib_ready():
        pytest.skip("ReportViewer DLLs missing — engine leg skipped (not passed)")
    return ps, staticize, str(_RENDERLAB / "lib")


def _engine_load(ps, libdir, rdl_paths, timeout=900):
    listing = "\n".join(str(p) for p in rdl_paths)
    lst = Path(rdl_paths[0]).parent / "_load_list.txt"
    lst.write_text(listing, encoding="utf-8")
    proc = subprocess.run(
        [ps, "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-File", str(_LOAD_PS1), "-ListFile", str(lst), "-LibDir", libdir],
        capture_output=True, text=True, timeout=timeout)
    data = None
    for line in reversed((proc.stdout or "").splitlines()):
        if line.strip().startswith("{"):
            try:
                data = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
    if data is None:
        pytest.skip("engine load harness produced no verdict "
                    f"(stderr={proc.stderr[:300]!r}) — skipped, not passed")
    return {r["path"]: r for r in data.get("results", [])}


@pytest.mark.skipif(not _PARAMS, reason="no corpus sources present")
def test_upload_gate_engine_load(tmp_path):
    """EVERY corpus RDL must survive the engine's definition compile."""
    ps, staticize, libdir = _engine_harness()
    ids = {}
    paths = []
    for param in _PARAMS:
        corpus_root, source_path = param.values
        rel = source_path.relative_to(corpus_root).as_posix()
        rdl, _parsed = _converted(source_path)
        h = hashlib.sha1(f"{corpus_root}|{rel}".encode()).hexdigest()[:16]
        f = tmp_path / f"{h}.rdl"
        f.write_text(staticize(rdl), encoding="utf-8")
        baselined = set(_load_baseline(corpus_root).get(rel, ()))
        ids[str(f)] = (rel, baselined)
        paths.append(str(f))
    results = _engine_load(ps, libdir, paths)
    new, known = [], []
    for p in paths:
        r = results.get(p)
        rel, baselined = ids[p]
        if r is None:
            new.append(f"{rel}: engine returned no verdict for this RDL")
        elif not r["ok"]:
            if "engine:load" in baselined:
                known.append(rel)
            else:
                new.append(f"{rel}: {r['error'][:400]}")
    assert not new, (
        "UPLOAD FATAL GATE (L-C engine load) — the MS engine REJECTED "
        f"{len(new)} generated RDL(s):\n  " + "\n  ".join(new))
    if known:
        pytest.xfail(f"{len(known)} baselined known-open engine-load "
                     f"failure(s) ({BASELINE_NAME}): {', '.join(known[:5])}")


# ---------------------------------------------------------------------------
# L-D: every expression compiles (sha-rotated deterministic sample per run)
# ---------------------------------------------------------------------------

_COMPILE_BUCKETS = 10  # ~10% of the corpus per run; full rotation in 10 days


def _in_todays_sample(case_id: str) -> bool:
    if os.environ.get("O2S_UPLOAD_GATE_COMPILE_ALL") == "1":
        return True
    bucket = int(hashlib.sha256(case_id.encode()).hexdigest(), 16) % _COMPILE_BUCKETS
    return bucket == date.today().toordinal() % _COMPILE_BUCKETS


def _compile_violations(rdl_xml: str):
    """(violations, bad) via the repo's real-VB-compiler rail, or a skip."""
    res = check_rdl_expressions(rdl_xml)
    if not res.get("available"):
        pytest.skip("VB compile harness unavailable "
                    f"({res.get('reason', '')}) — skipped, not passed")
    bad = res.get("bad") or []
    return sorted({f"compile:{b['location']}" for b in bad}), bad


def test_synthetic_report_passes_upload_gate_compile(synthetic_xml_bytes):
    rdl = convert(synthetic_xml_bytes, target_db="oracle")["rdl_xml"]
    violations, bad = _compile_violations(rdl)
    assert not violations, f"synthetic expressions failed VB compile: {bad[:3]}"


@pytest.mark.skipif(not _PARAMS, reason="no corpus sources present")
@pytest.mark.parametrize("corpus_root,source_path", _PARAMS)
def test_upload_gate_expressions_compile(corpus_root, source_path):
    rel = source_path.relative_to(corpus_root).as_posix()
    case_id = f"{corpus_root}|{rel}"
    if not _in_todays_sample(case_id):
        pytest.skip("not in today's sha-rotation compile sample "
                    "(O2S_UPLOAD_GATE_COMPILE_ALL=1 compiles everything)")
    rdl, _parsed = _converted(source_path)
    violations, bad = _compile_violations(rdl)
    if violations:
        detail = "\n  ".join(
            f"{b['location']}: {b['expr'][:80]!r} -> {'; '.join(b['errors'])[:200]}"
            for b in bad[:5])
        violations = [f"{v}  [{detail}]" if i == 0 else v
                      for i, v in enumerate(violations)]
    _gate_leg(violations, _load_baseline(corpus_root).get(rel, ()),
              rel, "L-D compile")


def test_compile_sample_is_deterministic_and_rotates():
    """The sample is a pure function of (case sha, day): same-day selection
    is stable, and across _COMPILE_BUCKETS consecutive days every case is
    selected at least once — coverage provably rotates through the corpus."""
    ids = [f"case-{i}" for i in range(200)]
    buckets = {i: int(hashlib.sha256(c.encode()).hexdigest(), 16) % _COMPILE_BUCKETS
               for i, c in enumerate(ids)}
    assert len(set(buckets.values())) == _COMPILE_BUCKETS  # spread, not clumped
    # every bucket value 0.._COMPILE_BUCKETS-1 is hit on some day:
    for b in range(_COMPILE_BUCKETS):
        assert any(v == b for v in buckets.values())


# ---------------------------------------------------------------------------
# Mutation proofs — every leg must go RED when fed a doctored artifact.
# ---------------------------------------------------------------------------

def _local(tag):
    return tag.split("}", 1)[1] if "}" in tag else tag


@pytest.fixture(scope="module")
def good_rdl(synthetic_xml_bytes):
    rdl = convert(synthetic_xml_bytes, target_db="oracle")["rdl_xml"]
    assert _sql_violations(rdl) == [], "synthetic RDL must start gate-clean"
    return rdl


def _doctor(rdl_xml: str, mutate) -> str:
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


def _set_commandtext(root, sql):
    _find_first(root, "CommandText").text = sql


def test_mutation_invalid_identifier_star_alias_goes_red(good_rdl):
    """The exact production class: 'SELECT X.* AS X' shipped once and died
    on the real server while every local rail stayed green."""
    doctored = _doctor(good_rdl, lambda r: _set_commandtext(
        r, "SELECT E.* AS E FROM EMPLOYEES E WHERE E.DEPT_ID = :P_YEAR"))
    v = _sql_violations(doctored)
    assert any(x.startswith("sql:sql.star_alias") for x in v), v


@pytest.mark.skipif(not grammar_available(),
                    reason="sqlglot grammar backend not installed")
def test_mutation_unparsable_commandtext_goes_red(good_rdl):
    """An invalid identifier / broken grammar in a CommandText must trip the
    Oracle-grammar rail (judged standalone: the doctored SQL is entirely
    'ours', mirroring sql_syntax's own no-source rule)."""
    doctored = _doctor(good_rdl, lambda r: _set_commandtext(
        r, "SELECT 1FROM((BAD..IDENT WHERE"))
    v = _sql_violations(doctored)
    assert any(x.startswith("sql:parse.") for x in v), v


def test_mutation_raw_lexical_placeholder_goes_red(good_rdl):
    """Literal '&P_X' in live SQL would be sent to the server verbatim."""
    doctored = _doctor(good_rdl, lambda r: _set_commandtext(
        r, "SELECT emp_name FROM employees WHERE &P_WHERE_CLAUSE ORDER BY 1"))
    v = _sql_violations(doctored)
    assert any(x.startswith("sql:scan.raw_lexical.") for x in v), v
    # ...but the SAME text inside a comment is the converter's honest
    # disclosure idiom, not live SQL — the scan must NOT fire on it:
    commented = _doctor(good_rdl, lambda r: _set_commandtext(
        r, "SELECT emp_name FROM employees /* lexical ref &P_WHERE_CLAUSE "
           "was here */ ORDER BY 1"))
    assert not any(x.startswith("sql:scan.raw_lexical.")
                   for x in _sql_violations(commented))


def test_mutation_unterminated_quote_goes_red(good_rdl):
    doctored = _doctor(good_rdl, lambda r: _set_commandtext(
        r, "SELECT emp_name FROM employees WHERE region = 'NORTH"))
    v = _sql_violations(doctored)
    assert any(x.startswith("sql:scan.unterminated_quote.") for x in v), v


def test_mutation_xsd_leg_goes_red(good_rdl):
    """A stray non-schema attribute (the exact 'internal marker leaked into
    the output' class) must fail schema validation."""
    if _schema() is None:
        pytest.skip("RDL 2008 XSD not available")
    assert _xsd_violations(good_rdl) == [], "doctor target must start clean"

    def mutate(root):
        _find_first(root, "Textbox").set("data-o2s-mutation-proof", "1")
    v = _xsd_violations(_doctor(good_rdl, mutate))
    assert v and all(x.startswith("xsd:") for x in v), v


def test_mutation_bad_expression_in_textbox_goes_red(good_rdl):
    """A textbox expression with broken VB (unbalanced IIf) must fail the
    real-compiler leg."""

    def mutate(root):
        tb = _find_first(root, "Textbox")
        val = _find_first(tb, "Value")
        val.text = '=IIf(Fields!name.Value, "a"'   # unbalanced — cannot compile
    violations, bad = _compile_violations(_doctor(good_rdl, mutate))
    assert violations, "VB compile leg passed a syntactically broken expression"
    assert bad and any(b["errors"] for b in bad)


def test_mutation_engine_load_goes_red(good_rdl, tmp_path):
    """The engine leg must reject an invalid definition — and accept the
    good one in the same batch (both directions proven in one invocation)."""
    ps, staticize, libdir = _engine_harness()
    good = tmp_path / "good.rdl"
    good.write_text(staticize(good_rdl), encoding="utf-8")
    bad_xml = _doctor(good_rdl, lambda root: ET.SubElement(
        root, root.tag.split("}")[0] + "}NotARdlElementMutationProof"
        if root.tag.startswith("{") else "NotARdlElementMutationProof"))
    bad = tmp_path / "bad.rdl"
    bad.write_text(staticize(bad_xml), encoding="utf-8")
    results = _engine_load(ps, libdir, [str(good), str(bad)])
    assert results[str(good)]["ok"], results[str(good)]["error"]
    assert not results[str(bad)]["ok"], \
        "engine leg accepted a definition-invalid RDL — the gate cannot fail"
