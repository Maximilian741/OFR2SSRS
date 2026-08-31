"""ONE Oracle bind must produce EXACTLY ONE QueryParameter.

Oracle bind variable names are ordinary unquoted identifiers, so they are
CASE-INSENSITIVE: ``:P_BEGIN_DATE`` and ``:P_Begin_Date`` are the SAME bind,
and a single statement may legally spell it both ways (Oracle Reports' own
exports do exactly that).

The generator used to deduplicate binds by exact SPELLING, so it declared one
``<QueryParameter>`` per spelling. That hands the provider MORE parameters
than the statement has binds, and Oracle answers with

    ORA-01036: illegal variable name/number

the moment the query is executed -- which is precisely what Report Builder
does when the user clicks **Refresh Fields** after repointing the data source.
Measured on the customer's own corpus before the fix:

    <report A> / Q_Site        3 distinct binds, 5 QueryParameters
    <report B> / Q_Permit      6 distinct binds, 8 QueryParameters
    <report C> / Q_SITE        6 distinct binds, 8 QueryParameters
    <report D> / Q_RENEWAL     7 distinct binds, 9 QueryParameters

THE CONTRACT this file holds, for every DataSet of every converted report:

  * ``|QueryParameters| == |distinct case-folded binds in the live SQL|``
  * the two NAME SETS are equal case-insensitively (no surplus parameter for
    a bind Oracle never sees; no bind left undeclared)
  * no two QueryParameters of one DataSet fold to the same name
  * no two ReportParameters of one report fold to the same name -- two
    spellings must not be able to create two report parameters either
  * the SQL TEXT is left alone: both spellings stay in the statement,
    because both are valid Oracle and both bind to the one parameter

The contract checker here is deliberately SELF-CONTAINED (its own SQL masker,
no import from the validators package). The failure this file exists for was
invisible to a checker that only asked "is every bind declared?" -- a question
a surplus duplicate answers YES to. A guard that shares its measuring
machinery with the thing it guards can go blind the same way twice, so this
one measures independently, and every leg below is mutation-proved against a
doctored RDL.

NOT PROVED HERE (and not provable on this machine): that Report Builder's
Refresh Fields now succeeds against the customer's server. Report Builder is
not installed here. What is proved is the SQL/parameter CONTRACT that the
data provider is handed.
"""
from __future__ import annotations

import collections
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from converter import convert

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Self-contained SQL masking + bind scan (no shared machinery with the code
# under test, and none with the other gates)
# ---------------------------------------------------------------------------

def _mask_sql(sql: str) -> str:
    """Blank string literals, quoted identifiers and comments, preserving
    length. A ``:NAME`` inside a date mask literal or a comment is not a
    bind."""
    if not sql:
        return ""
    n = len(sql)
    out = list(sql)
    i = 0
    while i < n:
        c = sql[i]
        if c == "'":
            j = i + 1
            while j < n:
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            for k in range(i, min(j + 1, n)):
                out[k] = " "
            i = j + 1
        elif c == '"':
            j = sql.find('"', i + 1)
            j = n - 1 if j < 0 else j
            for k in range(i, j + 1):
                out[k] = " "
            i = j + 1
        elif sql.startswith("--", i):
            j = sql.find("\n", i)
            j = n if j < 0 else j
            for k in range(i, j):
                out[k] = " "
            i = j
        elif sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            j = n - 2 if j < 0 else j
            for k in range(i, min(j + 2, n)):
                out[k] = " "
            i = j + 2
        else:
            i += 1
    return "".join(out)


_BIND_RE = re.compile(r"(?<![:\w]):([A-Za-z_]\w*)")
_TSQL_BIND_RE = re.compile(r"(?<![@\w])@([A-Za-z_]\w*)")


def _distinct_binds(sql: str):
    """UPPER-cased set of the binds the statement actually contains."""
    masked = _mask_sql(sql)
    found = set()
    for rx in (_BIND_RE, _TSQL_BIND_RE):
        found |= {m.group(1).upper() for m in rx.finditer(masked)}
    return found


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _iter_local(root, name):
    return [e for e in root.iter() if _local(e.tag) == name]


def _child(parent, name):
    if parent is None:
        return None
    for e in list(parent):
        if _local(e.tag) == name:
            return e
    return None


def bind_contract_violations(rdl_xml: str):
    """Every way one bind can fail to mean exactly one QueryParameter."""
    out = []
    root = ET.fromstring(rdl_xml)

    for ds in _iter_local(root, "DataSet"):
        dsname = ds.get("Name", "?")
        ct = _child(_child(ds, "Query"), "CommandText")
        sql = (ct.text or "") if ct is not None else ""
        if sql.lstrip().startswith("="):
            continue        # expression CommandText is a different gate's leg
        binds = _distinct_binds(sql)
        qnames = [(qp.get("Name") or "").lstrip("@:")
                  for qp in _iter_local(ds, "QueryParameter")]
        counts = collections.Counter(q.upper() for q in qnames)

        for name, n in sorted(counts.items()):
            if n > 1:
                out.append(
                    f"dup_case_query_parameter: DataSet {dsname!r} declares "
                    f"{n} QueryParameters for the single bind {name!r} "
                    f"(Oracle bind names are case-insensitive) - the provider "
                    f"is handed more parameters than the statement binds: "
                    f"ORA-01036 at Refresh Fields.")
        for name in sorted(set(counts) - binds):
            out.append(
                f"query_parameter_without_bind: DataSet {dsname!r} declares "
                f"QueryParameter {name!r} but the live SQL contains no such "
                f"bind - a parameter Oracle never asked for (ORA-01036).")
        for name in sorted(binds - set(counts)):
            out.append(
                f"bind_without_query_parameter: DataSet {dsname!r} SQL binds "
                f"{name!r} but declares no QueryParameter for it - "
                f"ORA-01008 'not all variables bound'.")
        if len(qnames) != len(binds):
            out.append(
                f"parameter_count_mismatch: DataSet {dsname!r} has "
                f"{len(binds)} distinct bind(s) but {len(qnames)} "
                f"QueryParameter(s).")

    # One report parameter per case-folded name: two spellings of one bind
    # must not be able to create two report parameters either.
    rp_counts = collections.Counter(
        (rp.get("Name") or "").upper()
        for rp in _iter_local(root, "ReportParameter"))
    for name, n in sorted(rp_counts.items()):
        if n > 1:
            out.append(
                f"dup_case_report_parameter: {n} ReportParameters fold to the "
                f"name {name!r} - one bind would prompt the user twice.")
    return out


# ---------------------------------------------------------------------------
# A source that spells the SAME bind two ways (the real Oracle idiom)
# ---------------------------------------------------------------------------

TWO_SPELLING_XML = b"""<?xml version="1.0" encoding="WINDOWS-1252" ?>
<report name="TWO_SPELLING_BIND" DTDVersion="9.0.2.0.10">
  <data>
    <userParameter name="P_BEGIN_DATE" datatype="date" width="10"
     precision="10" inputMask="MM/DD/YYYY" label="Begin Date"
     defaultWidth="0" defaultHeight="0"/>
    <userParameter name="P_SITE_NAME" datatype="character" width="50"
     precision="10" label="Site Name" defaultWidth="0" defaultHeight="0"/>
    <dataSource name="Q_MAIN">
      <select>
      <![CDATA[SELECT
    site_name,
    visit_date
FROM visits
WHERE (:P_BEGIN_DATE IS NULL OR visit_date >= :P_Begin_Date)
  AND (:P_SITE_NAME IS NULL OR site_name = :P_Site_Name)
ORDER BY 1]]>
      </select>
      <displayInfo x="0" y="0" width="1.5" height="0.5"/>
      <group name="G_MAIN">
        <displayInfo x="0" y="0" width="1.5" height="3"/>
        <dataItem name="site_name" datatype="vchar2" columnOrder="1" width="50"
         defaultWidth="100000" defaultHeight="10000" columnFlags="0"
         defaultLabel="Site Name">
          <dataDescriptor expression="site_name" descriptiveExpression="SITE_NAME"
           order="1" width="50"/>
          <dataItemPrivate adtName="" schemaName=""/>
        </dataItem>
        <dataItem name="visit_date" datatype="date" columnOrder="2" width="10"
         defaultWidth="100000" defaultHeight="10000" columnFlags="0"
         defaultLabel="Visit Date">
          <dataDescriptor expression="visit_date" descriptiveExpression="VISIT_DATE"
           order="2" width="10"/>
          <dataItemPrivate adtName="" schemaName=""/>
        </dataItem>
      </group>
    </dataSource>
  </data>
</report>"""


@pytest.fixture(scope="module")
def two_spelling_rdl():
    res = convert(TWO_SPELLING_XML, target_db="oracle")
    rdl = (res or {}).get("rdl_xml") or ""
    assert rdl.strip(), "convert() returned no RDL"
    return rdl


def _dataset(rdl_xml: str, name: str):
    for ds in _iter_local(ET.fromstring(rdl_xml), "DataSet"):
        if ds.get("Name") == name:
            return ds
    raise AssertionError(f"DataSet {name!r} not emitted")


# ---------------------------------------------------------------------------
# The emitter contract
# ---------------------------------------------------------------------------

def test_two_spellings_of_one_bind_yield_one_query_parameter(two_spelling_rdl):
    ds = _dataset(two_spelling_rdl, "Q_MAIN")
    sql = _child(_child(ds, "Query"), "CommandText").text or ""
    qnames = [(qp.get("Name") or "").lstrip(":@")
              for qp in _iter_local(ds, "QueryParameter")]

    assert _distinct_binds(sql) == {"P_BEGIN_DATE", "P_SITE_NAME"}
    assert len(qnames) == 2, (
        f"one bind must yield exactly one QueryParameter; got {qnames}")
    assert {q.upper() for q in qnames} == {"P_BEGIN_DATE", "P_SITE_NAME"}


def test_the_sql_text_keeps_both_spellings(two_spelling_rdl):
    """Both spellings are valid Oracle for the one bind, so the statement is
    never rewritten to make the parameter contract work."""
    sql = _child(_child(_dataset(two_spelling_rdl, "Q_MAIN"), "Query"),
                 "CommandText").text or ""
    masked = _mask_sql(sql)
    spellings = {m.group(1) for m in _BIND_RE.finditer(masked)
                 if m.group(1).upper() == "P_BEGIN_DATE"}
    assert len(spellings) == 2, (
        f"the source spelled the bind two ways; the emitted SQL must keep "
        f"both (got {sorted(spellings)})")


def test_both_spellings_resolve_to_the_one_report_parameter(two_spelling_rdl):
    root = ET.fromstring(two_spelling_rdl)
    declared = [(rp.get("Name") or "")
                for rp in _iter_local(root, "ReportParameter")]
    assert len(declared) == len({d.upper() for d in declared}), (
        f"two spellings must not create two report parameters: {declared}")
    ds = _dataset(two_spelling_rdl, "Q_MAIN")
    for qp in _iter_local(ds, "QueryParameter"):
        val = (_child(qp, "Value").text or "")
        assert val.strip(), "an empty <Value/> is the prompt trigger"
        for ref in re.findall(r"Parameters!([A-Za-z_]\w*)\.Value", val):
            assert ref in declared, (
                f"QueryParameter {qp.get('Name')!r} references "
                f"Parameters!{ref}.Value with no exactly-cased ReportParameter")


def test_date_bind_keeps_its_to_date_string_contract(two_spelling_rdl):
    """The one surviving QueryParameter must still see the TO_DATE wrap that
    ANY spelling of the bind carries. The wrap and the Format(CDate(...))
    value are two halves of one contract (a DateTime sent against a
    'YYYY-MM-DD' mask is ORA-01861), and collapsing the duplicate spellings
    must not drop it."""
    ds = _dataset(two_spelling_rdl, "Q_MAIN")
    sql = _child(_child(ds, "Query"), "CommandText").text or ""
    wrapped = {m.group(1).upper() for m in
               re.finditer(r"TO_DATE\(\s*:([A-Za-z_]\w*)", sql)}
    if not wrapped:
        pytest.skip("this build does not TO_DATE-wrap date binds")
    checked = 0
    for qp in _iter_local(ds, "QueryParameter"):
        name = (qp.get("Name") or "").lstrip(":@").upper()
        if name not in wrapped:
            continue
        val = (_child(qp, "Value").text or "")
        assert "Format(CDate(" in val, (
            f"bind {name!r} is wrapped in TO_DATE(...,'YYYY-MM-DD') in the "
            f"SQL but its QueryParameter sends a raw value: {val!r}")
        checked += 1
    assert checked, "no QueryParameter matched the TO_DATE-wrapped bind"


def test_two_spelling_report_satisfies_the_whole_contract(two_spelling_rdl):
    assert bind_contract_violations(two_spelling_rdl) == []


def test_detector_folds_bind_case_and_keeps_the_first_spelling():
    from converter.generators.rdl import _detect_oracle_bind_vars
    sql = ("SELECT 1 FROM t WHERE a BETWEEN :P_Begin_Date AND :P_END_DATE "
           "AND b >= :P_BEGIN_DATE AND c = :P_end_date AND d = :P_Other")
    assert _detect_oracle_bind_vars(sql) == [
        "P_Begin_Date", "P_END_DATE", "P_Other"]


def test_subreport_bind_scan_folds_case():
    from converter.subreports import _bind_params_in_sql
    sql = "SELECT 1 FROM t WHERE a = :P_Site AND b = :P_SITE AND c = :P_Other"
    assert _bind_params_in_sql(sql) == ["P_Site", "P_Other"]


def test_literal_and_comment_binds_are_still_not_binds():
    """The case-fold must not have widened what counts as a bind."""
    from converter.generators.rdl import _detect_oracle_bind_vars
    sql = ("SELECT TO_CHAR(d, 'HH24:MI') FROM t -- :P_COMMENTED\n"
           "WHERE x = :P_Real /* :P_BLOCKED */")
    assert _detect_oracle_bind_vars(sql) == ["P_Real"]


def test_todate_wrap_lookup_is_bind_case_insensitive():
    from converter.generators.rdl import _todate_wrapped
    sql = "WHERE d >= TO_DATE(:P_BEGIN_DATE, 'YYYY-MM-DD')"
    assert _todate_wrapped("P_Begin_Date", sql) is True
    assert _todate_wrapped("P_BEGIN_DATE", sql) is True
    assert _todate_wrapped("P_END_DATE", sql) is False
    assert _todate_wrapped("P_BEGIN", sql) is False   # prefix is not a match


# ---------------------------------------------------------------------------
# MUTATION PROOFS - the checker must go red on each class it claims to catch
# ---------------------------------------------------------------------------

def test_checker_catches_a_duplicate_case_query_parameter(two_spelling_rdl):
    doctored = two_spelling_rdl.replace(
        '<QueryParameter Name=":P_SITE_NAME">',
        '<QueryParameter Name=":P_Site_Name">\n'
        '          <Value>=Parameters!P_SITE_NAME.Value</Value>\n'
        '        </QueryParameter>\n'
        '        <QueryParameter Name=":P_SITE_NAME">', 1)
    assert doctored != two_spelling_rdl, "mutation did not apply"
    v = bind_contract_violations(doctored)
    assert any(x.startswith("dup_case_query_parameter") for x in v), v


def test_checker_catches_a_query_parameter_with_no_bind(two_spelling_rdl):
    doctored = two_spelling_rdl.replace(
        '<QueryParameter Name=":P_SITE_NAME">',
        '<QueryParameter Name=":P_NOT_IN_THE_SQL">\n'
        '          <Value>=Nothing</Value>\n'
        '        </QueryParameter>\n'
        '        <QueryParameter Name=":P_SITE_NAME">', 1)
    assert doctored != two_spelling_rdl, "mutation did not apply"
    v = bind_contract_violations(doctored)
    assert any(x.startswith("query_parameter_without_bind") for x in v), v


def test_checker_catches_an_undeclared_bind(two_spelling_rdl):
    doctored = re.sub(
        r'<QueryParameter Name=":P_SITE_NAME">.*?</QueryParameter>',
        "", two_spelling_rdl, flags=re.S | re.I)
    assert doctored != two_spelling_rdl, "mutation did not apply"
    v = bind_contract_violations(doctored)
    assert any(x.startswith("bind_without_query_parameter") for x in v), v


def test_checker_catches_a_duplicate_case_report_parameter(two_spelling_rdl):
    doctored = two_spelling_rdl.replace(
        '<ReportParameter Name="P_SITE_NAME">',
        '<ReportParameter Name="P_Site_Name">\n'
        '      <DataType>String</DataType>\n'
        '      <Nullable>true</Nullable>\n'
        '      <DefaultValue><Values><Value>=Nothing</Value></Values>'
        '</DefaultValue>\n'
        '    </ReportParameter>\n'
        '    <ReportParameter Name="P_SITE_NAME">', 1)
    assert doctored != two_spelling_rdl, "mutation did not apply"
    v = bind_contract_violations(doctored)
    assert any(x.startswith("dup_case_report_parameter") for x in v), v


# ---------------------------------------------------------------------------
# Corpus leg - every DataSet of every source on this machine
# ---------------------------------------------------------------------------

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
}


def _corpus_sources(root: Path):
    if not root.is_dir():
        return []
    return [p for p in sorted(root.rglob("*.xml"))
            if not any(part.startswith("_")
                       for part in p.relative_to(root).parts[:-1])]


@pytest.mark.parametrize("corpus", sorted(CORPORA))
@pytest.mark.parametrize("target_db", ["oracle", "sqlserver"])
def test_corpus_one_bind_one_query_parameter(corpus, target_db):
    """Corpus-wide: no report may hand the provider a parameter list that
    disagrees with its own statement. Absent corpora skip cleanly."""
    sources = _corpus_sources(CORPORA[corpus])
    if not sources:
        pytest.skip(f"corpus {corpus!r} not present on this machine")
    failures = []
    for src in sources:
        try:
            rdl = (convert(src.read_bytes(), target_db=target_db)
                   or {}).get("rdl_xml") or ""
        except Exception as exc:            # noqa: BLE001 - conversion health
            failures.append(f"{src.name}: convert() raised {exc!r}")
            continue
        if not rdl.strip():
            failures.append(f"{src.name}: convert() returned no RDL")
            continue
        for v in bind_contract_violations(rdl):
            failures.append(f"{src.name}: {v}")
    assert not failures, (
        f"{corpus}/{target_db}: {len(failures)} bind-contract violation(s):\n  "
        + "\n  ".join(failures[:40]))
