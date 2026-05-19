"""Structural diff between our generated RDL and the perplexity-rebuilt
"source-of-truth" RDLs that the user has verified work in their SSRS.

This test is the regression anchor for the whole class of "RDL uploads
but breaks at runtime" bugs that pure XML-well-formedness tests can't
catch. It's fully generic: it walks tests/fixtures/source_of_truth/,
treats every subdirectory containing both ``source.xml`` and
``expected.rdl`` as a case, converts source.xml through our pipeline,
and asserts that the emitted RDL matches the structural shape of the
expected.rdl.

NO report names, table names, column names, bind variables, or other
report-specific tokens are hard-coded anywhere in this file. To add a
new case, drop the Oracle XML + the known-working RDL into a new
subdirectory and the test picks it up automatically.
"""
from __future__ import annotations
import html
import re
from pathlib import Path

import pytest


FIXTURES = Path(__file__).parent / "fixtures" / "source_of_truth"


def _cases():
    if not FIXTURES.exists():
        return []
    out = []
    for d in sorted(FIXTURES.iterdir()):
        src = d / "source.xml"
        exp = d / "expected.rdl"
        if src.exists() and exp.exists():
            out.append(pytest.param(d.name, src, exp, id=d.name))
    return out


def _extract_param_blocks(rdl: str):
    """Return {param_name: data_type} for every ReportParameter in the RDL."""
    out = {}
    for m in re.finditer(
        r'<ReportParameter Name="([^"]+)">(.*?)</ReportParameter>',
        rdl, re.DOTALL,
    ):
        name, body = m.group(1), m.group(2)
        dt = re.search(r"<DataType>([^<]+)</DataType>", body)
        out[name] = dt.group(1) if dt else "String"
    return out


def _extract_query_bind_names(rdl: str):
    """Return the set of QueryParameter names (the :NAME / @NAME forms)."""
    return set(re.findall(r'<QueryParameter Name="([^"]+)"', rdl))


def _extract_command_texts(rdl: str):
    return [html.unescape(c) for c in re.findall(
        r"<CommandText>(.*?)</CommandText>", rdl, re.DOTALL)]


def _empty_containers(rdl: str):
    """Return list of (element_name, line) for any empty must-have-child
    container. SSRS 2008/01 rejects these at upload."""
    bad = []
    for el in ("ReportItems", "CellContents", "DataSources",
               "DataSets", "ReportParameters"):
        for m in re.finditer(rf"<{el}\s*/>", rdl):
            bad.append((el, rdl[:m.start()].count("\n") + 1))
        for m in re.finditer(rf"<{el}>\s*</{el}>", rdl):
            bad.append((el, rdl[:m.start()].count("\n") + 1))
    return bad


@pytest.mark.parametrize("case_name,src_path,exp_path", _cases())
def test_no_empty_required_containers(case_name, src_path, exp_path):
    """SSRS rejects empty <ReportItems> / <CellContents> / etc.
    This is the upload-blocker class of bug ('has incomplete content')."""
    from converter import convert
    rdl = convert(src_path.read_bytes())["rdl_xml"]
    empties = _empty_containers(rdl)
    assert not empties, (
        f"[{case_name}] empty must-have-child container(s) -- SSRS upload "
        f"will fail with 'has incomplete content':\n"
        + "\n".join(f"  <{el}/> at line {ln}" for el, ln in empties[:10])
    )


@pytest.mark.parametrize("case_name,src_path,exp_path", _cases())
def test_param_set_matches_source_of_truth(case_name, src_path, exp_path):
    """Every parameter the SOURCE XML declares (as <userParameter>) must
    appear in our generated RDL.

    Scoped intentionally to the XML, not perplexity's RDL: perplexity
    sometimes adds hand-tweak parameters (e.g. drill-through sub-dataset
    binds) that don't exist in the original Oracle XML, and asking our
    generator to invent those would be hardcoding. We only fail if WE
    drop a param that the XML actually declares.
    """
    from converter import convert
    raw_xml = src_path.read_text(errors="replace")
    declared_in_xml = set(re.findall(
        r'<userParameter\s+name="([^"]+)"', raw_xml, re.IGNORECASE))
    if not declared_in_xml:
        pytest.skip(f"[{case_name}] source XML declares no <userParameter>")
    ours = set(_extract_param_blocks(convert(src_path.read_bytes())["rdl_xml"]))
    # Case-insensitive compare to absorb XML-vs-RDL case drift.
    ours_ci = {n.upper() for n in ours}
    missing = {n for n in declared_in_xml if n.upper() not in ours_ci}
    assert not missing, (
        f"[{case_name}] parameter(s) declared in source XML but missing "
        f"from our RDL: {sorted(missing)}"
    )


@pytest.mark.parametrize("case_name,src_path,exp_path", _cases())
def test_query_binds_match_source_of_truth(case_name, src_path, exp_path):
    """Every bind variable that appears in the SOURCE XML's SQL must also
    appear as a QueryParameter in our generated RDL.

    Scoped to the XML so we don't fail because perplexity added extra
    bind references via hand-tweaks (e.g. drill-through sub-dataset
    binds). We only fail if WE drop a bind that's actually in the XML's
    SQL payload.
    """
    from converter import convert
    raw_xml = src_path.read_text(errors="replace")
    # Pull every <select> CommandText payload out of the XML, then find
    # any :BIND_NAME refs inside.
    sql_blobs = re.findall(r"<select[^>]*>(.*?)</select>",
                           raw_xml, re.DOTALL | re.IGNORECASE)
    xml_binds = set()
    for blob in sql_blobs:
        for m in re.finditer(r":([A-Za-z_][A-Za-z0-9_]*)", blob):
            xml_binds.add(m.group(1).upper())
    if not xml_binds:
        pytest.skip(f"[{case_name}] source XML has no :BIND references")
    ours = {n.lstrip(":@").upper()
            for n in _extract_query_bind_names(convert(src_path.read_bytes())["rdl_xml"])}
    missing = xml_binds - ours
    assert not missing, (
        f"[{case_name}] bind(s) referenced in source XML SQL but missing "
        f"from our RDL\'s QueryParameters: {sorted(missing)}"
    )


@pytest.mark.parametrize("case_name,src_path,exp_path", _cases())
def test_datetime_binds_to_date_wrapped(case_name, src_path, exp_path):
    """If a parameter is DateTime, every reference in our CommandText
    must be inside a TO_DATE(...). This catches the actual runtime
    failure the user reported ("Query execution failed for dataset Q_1").
    """
    from converter import convert
    rdl = convert(src_path.read_bytes())["rdl_xml"]
    date_params = [n for n, t in _extract_param_blocks(rdl).items()
                   if t == "DateTime"]
    if not date_params:
        pytest.skip(f"[{case_name}] no DateTime params")
    joined = "\n".join(_extract_command_texts(rdl))
    bare = []
    for n in date_params:
        for m in re.finditer(r":" + re.escape(n) + r"\b", joined):
            prefix = joined[max(0, m.start() - 10):m.start()].upper().rstrip()
            if not prefix.endswith("TO_DATE("):
                bare.append(n)
    assert not bare, (
        f"[{case_name}] DateTime binds not TO_DATE-wrapped: {sorted(set(bare))} "
        f"-- SSRS Oracle extension fails with NULL DateTime binds otherwise"
    )


@pytest.mark.parametrize("case_name,src_path,exp_path", _cases())
def test_uses_data_source_reference_like_truth(case_name, src_path, exp_path):
    """If the source-of-truth uses <DataSourceReference> (the pattern
    that suppresses Refresh-Fields prompt), ours must too."""
    from converter import convert
    if "<DataSourceReference>" not in exp_path.read_text(errors="replace"):
        pytest.skip(f"[{case_name}] truth RDL doesn't use DataSourceReference")
    ours = convert(src_path.read_bytes())["rdl_xml"]
    assert "<DataSourceReference>" in ours, (
        f"[{case_name}] source-of-truth uses <DataSourceReference> but ours "
        f"emits an embedded DataSource -- Refresh Fields dialog will appear"
    )


@pytest.mark.parametrize("case_name,src_path,exp_path", _cases())
def test_param_optionality_matches_truth(case_name, src_path, exp_path):
    """For every param present in BOTH our RDL and the truth RDL, if the
    truth marks it AllowBlank or Nullable, ours must too -- otherwise
    the user can't leave that field blank at runtime."""
    from converter import convert
    ours_rdl = convert(src_path.read_bytes())["rdl_xml"]
    truth_rdl = exp_path.read_text(errors="replace")

    def _flags(rdl: str):
        out = {}
        for m in re.finditer(
            r'<ReportParameter Name="([^"]+)">(.*?)</ReportParameter>',
            rdl, re.DOTALL,
        ):
            name, body = m.group(1), m.group(2)
            out[name] = {
                "allow_blank": "<AllowBlank>true</AllowBlank>" in body,
                "nullable": "<Nullable>true</Nullable>" in body,
            }
        return out

    ours_f = _flags(ours_rdl)
    truth_f = _flags(truth_rdl)
    fails = []
    for n in set(ours_f) & set(truth_f):
        if truth_f[n]["allow_blank"] and not ours_f[n]["allow_blank"]:
            fails.append((n, "missing AllowBlank=true"))
        if truth_f[n]["nullable"] and not ours_f[n]["nullable"]:
            fails.append((n, "missing Nullable=true"))
    assert not fails, (
        f"[{case_name}] param optionality drift from source-of-truth: " +
        "; ".join(f"{n} {why}" for n, why in fails[:10])
    )
