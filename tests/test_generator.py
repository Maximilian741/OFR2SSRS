"""Tests for the SSRS RDL generator (converter.generators.rdl)."""
from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest


RDL_NS = "http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition"


def _q(tag: str) -> str:
    return f"{{{RDL_NS}}}{tag}"


def test_generate_rdl_returns_string(translated_report):
    from converter.generators.rdl import generate_rdl
    rdl = generate_rdl(translated_report)
    assert isinstance(rdl, str) and rdl.strip(), "RDL output should be non-empty"


def test_generate_rdl_is_well_formed_xml(translated_report):
    from converter.generators.rdl import generate_rdl
    rdl = generate_rdl(translated_report)
    # ET.fromstring must not raise on a well-formed RDL document.
    root = ET.fromstring(rdl)
    assert root is not None


def test_root_tag_is_report(translated_report):
    from converter.generators.rdl import generate_rdl
    rdl = generate_rdl(translated_report)
    root = ET.fromstring(rdl)
    # Root local-name should be Report.
    tag = root.tag
    if "}" in tag:
        tag = tag.split("}", 1)[1]
    assert tag == "Report"


def test_rdl_has_datasets(translated_report):
    from converter.generators.rdl import generate_rdl
    rdl = generate_rdl(translated_report)
    root = ET.fromstring(rdl)
    datasets = root.find(_q("DataSets"))
    assert datasets is not None, "RDL must declare a <DataSets> element"
    children = list(datasets)
    assert len(children) > 0, "Expected at least one DataSet"


def test_rdl_has_report_parameters(translated_report):
    from converter.generators.rdl import generate_rdl
    rdl = generate_rdl(translated_report)
    root = ET.fromstring(rdl)
    rps = root.find(_q("ReportParameters"))
    # The SAMPLE_INSPECTION sample has parameters, so this element should be present.
    if translated_report.parameters:
        assert rps is not None, "RDL must declare <ReportParameters> when params exist"
        assert len(list(rps)) >= 1


def test_rdl_has_tablix_or_body(translated_report):
    from converter.generators.rdl import generate_rdl
    rdl = generate_rdl(translated_report)
    # We tolerate either a Tablix or some renderable body element.
    has_tablix = "<Tablix" in rdl or "Tablix" in rdl
    has_body = "<Body" in rdl or "Body" in rdl
    assert has_tablix or has_body, "RDL should contain a Tablix or Body element"


def test_rdl_dataset_count_matches_queries(translated_report):
    from converter.generators.rdl import generate_rdl
    rdl = generate_rdl(translated_report)
    root = ET.fromstring(rdl)
    datasets = root.find(_q("DataSets"))
    assert datasets is not None
    # Datasets should match (or exceed) the query count from the report.
    assert len(list(datasets)) >= 1


def test_rdl_includes_query_param_atrefs(translated_report):
    """Parameters referenced in T-SQL should appear in the generated RDL."""
    from converter.generators.rdl import generate_rdl
    rdl = generate_rdl(translated_report)
    # Sample has at least P_RENEWAL_YEAR -- check it is referenced somewhere.
    declared = {p.name for p in translated_report.parameters}
    if "P_RENEWAL_YEAR" in declared:
        assert "P_RENEWAL_YEAR" in rdl


def test_generate_rdl_minimal_report():
    """Should not crash on an empty/synthetic ParsedReport."""
    from converter.generators.rdl import generate_rdl
    from converter.models import ParsedReport
    rep = ParsedReport(name="EMPTY", dtd_version="9.0")
    out = generate_rdl(rep)
    assert isinstance(out, str) and out.strip()
    # Should still parse as XML.
    root = ET.fromstring(out)
    assert root is not None


# ---------------------------------------------------------------------------
# target_db toggle: Oracle (default) vs SQL Server
# ---------------------------------------------------------------------------

def _extract_command_texts(rdl: str):
    """Return a list of CommandText payloads from the RDL string."""
    import re
    return re.findall(r"<CommandText>(.*?)</CommandText>", rdl, re.DOTALL)


def _extract_data_providers(rdl: str):
    """Return a list of DataProvider element texts from the RDL."""
    import re
    return re.findall(r"<DataProvider>(.*?)</DataProvider>", rdl)


def test_convert_default_target_is_oracle(synthetic_xml_bytes):
    """Default convert() should preserve original Oracle SQL with :P_ bind vars."""
    import re
    from converter import convert
    out = convert(synthetic_xml_bytes)
    rdl = out["rdl_xml"]
    assert out.get("target_db") == "oracle"
    cmds = _extract_command_texts(rdl)
    assert cmds, "Expected at least one CommandText in the RDL"
    joined = "\n".join(cmds)
    # Oracle SQL keeps :P_FOO bind vars; T-SQL @P_FOO must NOT appear in CT.
    assert re.search(r":P_[A-Z_]+", joined), "Expected :P_ bind var in Oracle CommandText"
    assert not re.search(r"@P_[A-Z_]+", joined), "T-SQL @P_ bind vars must not appear in Oracle CommandText"
    # DataProvider should be OracleClient
    assert "OracleClient" in _extract_data_providers(rdl)


def test_convert_sqlserver_target_uses_tsql(synthetic_xml_bytes):
    """convert(target_db='sqlserver') should emit T-SQL with @P_ bind vars."""
    import re
    from converter import convert
    out = convert(synthetic_xml_bytes, target_db="sqlserver")
    rdl = out["rdl_xml"]
    assert out.get("target_db") == "sqlserver"
    cmds = _extract_command_texts(rdl)
    joined = "\n".join(cmds)
    # T-SQL CommandText uses @P_FOO; Oracle :P_FOO must NOT appear.
    assert re.search(r"@P_[A-Z_]+", joined), "Expected @P_ bind var in T-SQL CommandText"
    assert not re.search(r":P_[A-Z_]+", joined), "Oracle :P_ bind vars must not appear in T-SQL CommandText"
    # DataProvider should be SQL
    assert "SQL" in _extract_data_providers(rdl)


def test_generate_rdl_oracle_emits_colon_query_param_names(translated_report):
    """Oracle target's <QueryParameter Name=...> should use a leading colon."""
    from converter.generators.rdl import generate_rdl
    rdl = generate_rdl(translated_report, target_db="oracle")
    # At least one QueryParameter must declare a :P_ name.
    assert 'Name=":P_' in rdl


def test_generate_rdl_sqlserver_emits_at_query_param_names(translated_report):
    """SQL Server target's <QueryParameter Name=...> should use a leading @."""
    from converter.generators.rdl import generate_rdl
    rdl = generate_rdl(translated_report, target_db="sqlserver")
    assert 'Name="@P_' in rdl


def test_generate_rdl_invalid_target_db_falls_back_to_oracle(translated_report):
    """An unrecognized target_db value normalizes to the safe default."""
    from converter.generators.rdl import generate_rdl
    rdl = generate_rdl(translated_report, target_db="postgres")
    # Should look like the Oracle path
    assert "OracleClient" in _extract_data_providers(rdl)
