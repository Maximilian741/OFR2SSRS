"""Tests for the T-SQL validator (converter.validators.tsql_check)."""
from __future__ import annotations

import pytest


def test_validate_tsql_returns_list():
    from converter.validators.tsql_check import validate_tsql
    out = validate_tsql("SELECT 1")
    assert isinstance(out, list)


def test_validate_tsql_clean_query_has_no_errors():
    from converter.validators.tsql_check import validate_tsql
    issues = validate_tsql("SELECT a, b FROM dbo.foo WHERE x = @P_X")
    severities = {i.get("severity") for i in issues}
    assert "error" not in severities, f"Unexpected errors: {issues}"


def test_validate_tsql_flags_decode():
    from converter.validators.tsql_check import validate_tsql
    issues = validate_tsql("SELECT DECODE(x, 1, 'a') FROM t")
    rules = {i["rule"] for i in issues}
    assert "oracle.decode" in rules


def test_validate_tsql_flags_nvl():
    from converter.validators.tsql_check import validate_tsql
    issues = validate_tsql("SELECT NVL(a, 0) FROM t")
    rules = {i["rule"] for i in issues}
    assert "oracle.nvl" in rules


def test_validate_tsql_flags_sysdate():
    from converter.validators.tsql_check import validate_tsql
    issues = validate_tsql("SELECT SYSDATE FROM t")
    rules = {i["rule"] for i in issues}
    assert "oracle.sysdate" in rules


def test_validate_tsql_flags_dual():
    from converter.validators.tsql_check import validate_tsql
    issues = validate_tsql("SELECT 1 FROM DUAL")
    rules = {i["rule"] for i in issues}
    assert "oracle.dual" in rules


def test_validate_tsql_flags_outer_join_plus():
    from converter.validators.tsql_check import validate_tsql
    issues = validate_tsql("SELECT a.x FROM a, b WHERE a.id = b.id(+)")
    # Some rule must catch surviving (+) syntax (rule name varies but should
    # contain 'outer' or 'plus').
    msgs = " ".join(i["message"].lower() for i in issues)
    rules = " ".join(i["rule"].lower() for i in issues)
    assert "(+)" in msgs or "outer" in msgs or "outer" in rules or "plus" in rules


def test_validate_tsql_each_issue_has_required_keys():
    from converter.validators.tsql_check import validate_tsql
    issues = validate_tsql("SELECT NVL(a,0), DECODE(x,1,2) FROM DUAL")
    assert issues, "expected several issues"
    required = {"severity", "message", "rule", "scope"}
    for i in issues:
        missing = required - set(i.keys())
        assert not missing, f"issue missing keys {missing}: {i}"
        assert i["severity"] in ("error", "warning", "info")


def test_validate_report_returns_list(translated_report):
    from converter.validators.tsql_check import validate_report
    issues = validate_report(translated_report)
    assert isinstance(issues, list)


def test_validate_report_runs_clean_after_translation(translated_report):
    """validate_report runs without raising and returns a list."""
    from converter.validators.tsql_check import validate_report
    issues = validate_report(translated_report)
    assert isinstance(issues, list)
    # Each issue must have the documented shape
    for i in issues:
        assert "severity" in i
        assert "rule" in i


def test_validate_report_handles_empty_report():
    from converter.validators.tsql_check import validate_report
    from converter.models import ParsedReport
    rep = ParsedReport(name="EMPTY")
    issues = validate_report(rep)
    assert isinstance(issues, list)


# -- Preflight RDL audit: container child-element validity --

def _wrap_minimal_rdl(cell_contents_inner: str) -> str:
    """Build a minimal RDL string with the given inner XML inside a single
    <CellContents>. Used by preflight tests to exercise the schema-validation
    rules without spinning up a full report.
    """
    NS = "http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition"
    return f'''<?xml version="1.0" encoding="utf-8"?>
<Report xmlns="{NS}">
  <DataSources><DataSource Name="DS"><ConnectionProperties><DataProvider>SQL</DataProvider><ConnectString/></ConnectionProperties></DataSource></DataSources>
  <DataSets><DataSet Name="D1"><Query><DataSourceName>DS</DataSourceName><CommandText>SELECT 1</CommandText></Query></DataSet></DataSets>
  <Body>
    <ReportItems>
      <Tablix Name="T"><DataSetName>D1</DataSetName>
        <TablixBody>
          <TablixColumns><TablixColumn><Width>1in</Width></TablixColumn></TablixColumns>
          <TablixRows><TablixRow><Height>1in</Height>
            <TablixCells><TablixCell><CellContents>{cell_contents_inner}</CellContents></TablixCell></TablixCells>
          </TablixRow></TablixRows>
        </TablixBody>
        <TablixColumnHierarchy><TablixMembers><TablixMember/></TablixMembers></TablixColumnHierarchy>
        <TablixRowHierarchy><TablixMembers><TablixMember/></TablixMembers></TablixRowHierarchy>
      </Tablix>
    </ReportItems>
    <Height>1in</Height>
  </Body>
  <Page><PageHeight>11in</PageHeight><PageWidth>8.5in</PageWidth><LeftMargin>1in</LeftMargin><RightMargin>1in</RightMargin><TopMargin>1in</TopMargin><BottomMargin>1in</BottomMargin></Page>
  <Width>8.5in</Width>
</Report>'''


def test_preflight_flags_style_inside_cellcontents():
    """<CellContents><Style/></CellContents> is illegal in the SSRS 2008/01
    schema — preflight must flag it as a BLOCKER with rule
    rdl.invalid_cellcontents_child."""
    from converter.validators.preflight import preflight_audit
    rdl = _wrap_minimal_rdl('<Style><FontSize>10pt</FontSize></Style><Rectangle Name="R"/>')
    result = preflight_audit(rdl)
    rules = [i["rule"] for i in result["issues"]]
    assert "rdl.invalid_cellcontents_child" in rules, (
        f"expected rdl.invalid_cellcontents_child in issues; got {rules}"
    )
    assert result["verdict"] == "BLOCKER"
    # Message should name the disallowed child
    msgs = [i["message"] for i in result["issues"]
            if i["rule"] == "rdl.invalid_cellcontents_child"]
    assert any("Style" in m for m in msgs)


def test_preflight_passes_clean_cellcontents():
    """A CellContents whose only child is a Rectangle (ReportItem) is valid;
    the new rule must NOT fire."""
    from converter.validators.preflight import preflight_audit
    rdl = _wrap_minimal_rdl('<Rectangle Name="R"/>')
    result = preflight_audit(rdl)
    rules = [i["rule"] for i in result["issues"]]
    assert "rdl.invalid_cellcontents_child" not in rules
