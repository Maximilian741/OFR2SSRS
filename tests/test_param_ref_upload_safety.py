"""
Upload-safety regression: every =Parameters!X.Value reference in a generated
RDL must match a declared <ReportParameter Name="X"> by EXACT case.

SSRS is case-sensitive on parameter names and rejects the upload with
"...refers to a non-existing report parameter 'X'. Letters in the names of
parameters must use the correct case." A drill-through that forwarded a bind
with the wrong case (P_Envelope vs the declared P_ENVELOPE) once shipped a
non-uploadable SAMPLE_DRILLTHROUGH RDL. These tests are the deterministic gate so that
whole class can never reach the report server again.
"""
import re

import pytest

from converter.validators.preflight import preflight_audit


_PARAM_REF = re.compile(r"Parameters!([A-Za-z_][A-Za-z0-9_]*)\.Value")
_DECL = re.compile(r'<ReportParameter Name="([^"]+)">')


def _broken_param_refs(rdl_xml):
    """Return param refs that don't match a declared parameter by exact case."""
    declared = set(_DECL.findall(rdl_xml))
    refs = set(_PARAM_REF.findall(rdl_xml))
    return sorted(refs - declared)


# ---------------------------------------------------------------------------
# 1) The gate fires on broken refs and stays silent on correct ones.
# ---------------------------------------------------------------------------
_MINIMAL_RDL = """<?xml version="1.0" encoding="utf-8"?>
<Report xmlns="http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition">
  <DataSources><DataSource Name="D"><DataSourceReference>D</DataSourceReference></DataSource></DataSources>
  <DataSets><DataSet Name="DS"><Query><DataSourceName>D</DataSourceName><CommandText>SELECT 1 X</CommandText></Query><Fields><Field Name="X"><DataField>X</DataField></Field></Fields></DataSet></DataSets>
  <ReportParameters>
    <ReportParameter Name="P_ENVELOPE"><DataType>String</DataType><Prompt>e</Prompt></ReportParameter>
  </ReportParameters>
  <Body><ReportItems>
    {textboxes}
  </ReportItems><Height>2in</Height></Body>
  <Width>7in</Width><Page><PageHeight>11in</PageHeight><PageWidth>8.5in</PageWidth></Page>
</Report>"""


def _tb(name, value):
    return (f'<Textbox Name="{name}"><Paragraphs><Paragraph><TextRuns><TextRun>'
            f'<Value>{value}</Value></TextRun></TextRuns></Paragraph></Paragraphs></Textbox>')


def test_gate_flags_wrong_case_param_ref():
    rdl = _MINIMAL_RDL.format(textboxes=_tb("T", "=Parameters!P_Envelope.Value"))
    pf = preflight_audit(rdl, target_db="oracle")
    rules = [i["rule"] for i in pf["issues"]]
    assert "rdl.param_ref_wrong_case" in rules
    assert pf["verdict"] == "BLOCKER"


def test_gate_flags_undeclared_param_ref():
    rdl = _MINIMAL_RDL.format(textboxes=_tb("T", "=Parameters!P_NOPE.Value"))
    pf = preflight_audit(rdl, target_db="oracle")
    rules = [i["rule"] for i in pf["issues"]]
    assert "rdl.param_ref_undeclared" in rules
    assert pf["verdict"] == "BLOCKER"


def test_gate_silent_on_exact_case_param_ref():
    rdl = _MINIMAL_RDL.format(textboxes=_tb("T", "=Parameters!P_ENVELOPE.Value"))
    pf = preflight_audit(rdl, target_db="oracle")
    rules = [i["rule"] for i in pf["issues"]]
    assert "rdl.param_ref_wrong_case" not in rules
    assert "rdl.param_ref_undeclared" not in rules


# ---------------------------------------------------------------------------
# 2) Real reports: every converted RDL must have 0 broken param refs and 0
#    param-ref blockers. SAMPLE_DRILLTHROUGH is the canary (it exercises both the
#    master-detail Lookup and the drill-through Action that broke before).
# ---------------------------------------------------------------------------
def _convert(path):
    from converter import convert
    with open(path, "rb") as fh:
        return convert(fh.read())


# (Optional local-artifact regression tests were removed for the public repo;
# the synthetic gate tests above provide the same param-ref coverage on any
# checkout. To spot-check your own Oracle reports, run them through the app.)


# ---------------------------------------------------------------------------
# 3) Tablix row/column balance gate -- TablixRows must equal row-hierarchy
#    leaf members, else SSRS rejects the upload.
# ---------------------------------------------------------------------------
def test_tablix_balance_gate_fires_on_mismatch():
    import re as _re
    # A Tablix with 2 rows but 1 row-hierarchy leaf member is unbalanced.
    rdl = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<Report xmlns="http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition">'
        '<DataSources><DataSource Name="D"><DataSourceReference>D</DataSourceReference></DataSource></DataSources>'
        '<DataSets><DataSet Name="DS"><Query><DataSourceName>D</DataSourceName><CommandText>SELECT 1 X</CommandText></Query><Fields><Field Name="X"><DataField>X</DataField></Field></Fields></DataSet></DataSets>'
        '<Body><ReportItems><Tablix Name="T">'
        '<TablixBody><TablixColumns><TablixColumn><Width>1in</Width></TablixColumn></TablixColumns>'
        '<TablixRows>'
        '<TablixRow><Height>0.2in</Height><TablixCells><TablixCell><CellContents>'
        '<Textbox Name="a"><Paragraphs><Paragraph><TextRuns><TextRun><Value>a</Value></TextRun></TextRuns></Paragraph></Paragraphs></Textbox>'
        '</CellContents></TablixCell></TablixCells></TablixRow>'
        '<TablixRow><Height>0.2in</Height><TablixCells><TablixCell><CellContents>'
        '<Textbox Name="b"><Paragraphs><Paragraph><TextRuns><TextRun><Value>b</Value></TextRun></TextRuns></Paragraph></Paragraphs></Textbox>'
        '</CellContents></TablixCell></TablixCells></TablixRow>'
        '</TablixRows></TablixBody>'
        '<TablixColumnHierarchy><TablixMembers><TablixMember/></TablixMembers></TablixColumnHierarchy>'
        '<TablixRowHierarchy><TablixMembers><TablixMember><Group Name="g"/></TablixMember></TablixMembers></TablixRowHierarchy>'
        '<DataSetName>DS</DataSetName><Height>0.4in</Height><Width>1in</Width></Tablix>'
        '</ReportItems><Height>2in</Height></Body>'
        '<Width>7in</Width><Page><PageHeight>11in</PageHeight><PageWidth>8.5in</PageWidth></Page></Report>'
    )
    pf = preflight_audit(rdl, target_db="oracle")
    rules = [i["rule"] for i in pf["issues"]]
    assert any("tablix_row_mismatch" in r for r in rules)
    assert pf["verdict"] == "BLOCKER"
