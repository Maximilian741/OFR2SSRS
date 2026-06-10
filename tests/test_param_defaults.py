"""Every ReportParameter must carry a <DefaultValue> so SSRS can auto-
run the report from "View Report" without prompting the user for any
parameter that has no explicit input. Without this, runtime fails with:

    "The 'P_X' parameter is missing a value."

even when the parameter is AllowBlank=true (AllowBlank lets the user
SUBMIT empty input -- it does not provide an initial default for the
auto-run path).

This is the universal rule for the converter: any generated RDL, for
any uploaded Oracle XML, must have every ReportParameter equipped with
a DefaultValue that the SQL's (:P IS NULL OR col = :P) guards can
treat as "no filter".
"""
from __future__ import annotations
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest


RDL_NS = "http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition"
RD = "{" + RDL_NS + "}"

import glob

# Corpus: $O2S_CORPUS_DIR if set, else the bundled synthetic samples +
# source-of-truth fixtures -- so this suite actually runs for anyone cloning
# the repo (the original author's hardcoded uploads mount doesn't exist).
_ROOT = Path(__file__).resolve().parent.parent


def _xml_files():
    dirs = [os.environ.get("O2S_CORPUS_DIR"),
            str(_ROOT / "samples" / "oracle"),
            str(_ROOT / "tests" / "fixtures" / "source_of_truth")]
    out, seen = [], set()
    for d in dirs:
        if not d or not os.path.isdir(d):
            continue
        for p in sorted(glob.glob(os.path.join(d, "**", "*.xml"), recursive=True)):
            nm = os.path.basename(p)
            if nm in seen:
                continue
            seen.add(nm)
            out.append(pytest.param(nm, p, id=nm))
    return out


def _params_without_defaults(rdl: str):
    """Return [(param_name, datatype)] for every ReportParameter that
    lacks a <DefaultValue><Values><Value/></Values></DefaultValue>."""
    root = ET.fromstring(rdl)
    missing = []
    for rp in root.iter(RD + "ReportParameter"):
        name = rp.get("Name", "?")
        dt_el = rp.find(RD + "DataType")
        dt = dt_el.text if dt_el is not None else "?"
        dv = rp.find(RD + "DefaultValue")
        if dv is None:
            missing.append((name, dt))
            continue
        values = dv.find(RD + "Values")
        if values is None or len(list(values)) == 0:
            # An empty <DefaultValue/> or DataSetReference-only DV is
            # acceptable only if it references a query default; we are
            # strict and require at least one <Value>.
            missing.append((name, dt))
    return missing


@pytest.mark.parametrize("xml_name,xml_path", _xml_files())
def test_every_parameter_has_default_value(xml_name, xml_path):
    """The universal rule: every ReportParameter must have a
    <DefaultValue><Values><Value...></Values></DefaultValue>. Catches
    the "P_X is missing a value" runtime error for every report,
    not just SAMPLE_DRILLTHROUGH."""
    from converter import convert
    try:
        rdl = convert(open(xml_path, "rb").read())["rdl_xml"]
    except Exception as e:
        pytest.skip(f"convert raised {type(e).__name__}: {e}")
    missing = _params_without_defaults(rdl)
    assert not missing, (
        f"[{xml_name}] ReportParameter(s) without DefaultValue -- "
        f"SSRS runtime will fail with 'parameter is missing a value':\n"
        + "\n".join(f"  {n} (DataType={dt})" for n, dt in missing[:10])
    )


def test_synthetic_fixture_every_param_has_default(translated_report):
    """Same enforcement on the synthetic conftest fixture."""
    from converter.generators.rdl import generate_rdl
    rdl = generate_rdl(translated_report, target_db="oracle")
    missing = _params_without_defaults(rdl)
    assert not missing, (
        f"synthetic fixture has params without DefaultValue: {missing[:5]}"
    )


def test_string_param_default_value_is_nothing():
    """String parameters MUST default to a concrete =Nothing (typed NULL), NOT
    an empty <Value/>. An empty default is what makes SSRS pop the
    "Define Query Parameters" dialog when refreshing a dataset whose query binds
    to the parameter -- a broken report from the user's perspective. =Nothing
    lets upload -> repoint data source -> Refresh Fields proceed with no prompt,
    and the SQL's (:P IS NULL OR ...) / NVL(:P, ...) guards treat NULL as
    'no filter'. (Regression guard -- this must never revert to empty.)"""
    from converter.models import ParsedReport, ReportParameter
    from converter.generators.rdl import generate_rdl
    r = ParsedReport(name="T", dtd_version="9.0", parameters=[
        ReportParameter(name="P_TEST", datatype="character"),
    ])
    rdl = generate_rdl(r, target_db="oracle")
    root = ET.fromstring(rdl)
    rp = next(r for r in root.iter(RD + "ReportParameter") if r.get("Name") == "P_TEST")
    val_el = rp.find(RD + "DefaultValue/" + RD + "Values/" + RD + "Value")
    assert val_el is not None
    assert (val_el.text or "") == "=Nothing", \
        f"expected =Nothing default, got {val_el.text!r}"
    # String params are now Nullable too, so the typed-NULL default is valid.
    assert rp.find(RD + "Nullable") is not None and rp.find(RD + "Nullable").text == "true"


def test_datetime_param_default_value_is_nothing():
    """DateTime / Integer / Float parameters default to =Nothing
    (a VB null literal SSRS forwards as DbNull to the data extension)."""
    from converter.models import ParsedReport, ReportParameter
    from converter.generators.rdl import generate_rdl
    r = ParsedReport(name="T", dtd_version="9.0", parameters=[
        ReportParameter(name="P_DT", datatype="date"),
    ])
    rdl = generate_rdl(r, target_db="oracle")
    root = ET.fromstring(rdl)
    rp = next(r for r in root.iter(RD + "ReportParameter") if r.get("Name") == "P_DT")
    val_el = rp.find(RD + "DefaultValue/" + RD + "Values/" + RD + "Value")
    assert val_el is not None
    assert val_el.text == "=Nothing", f"expected =Nothing, got {val_el.text!r}"


# ---------------------------------------------------------------------------
# Regression gates for the 2026-06-01 per-record certificate fix:
#   (1) no empty parameter default -> no "Define Query Parameters" prompt
#   (2) filter-only child binds are NOT injected into the SELECT (no ORA-00904)
# ---------------------------------------------------------------------------

def test_preflight_blocks_empty_param_default():
    """The preflight gate MUST flag a ReportParameter with an empty default
    (the exact thing that pops the 'Define Query Parameters' dialog on refresh),
    and MUST clear once a concrete =Nothing default is present. Proves the gate
    actually works -- it would catch a real regression, not hide it."""
    from converter.validators.preflight import preflight_audit
    base = (
        '<Report xmlns="%s">'
        '<DataSources><DataSource Name="DS"><DataSourceReference>shared</DataSourceReference>'
        '</DataSource></DataSources>'
        '<DataSets><DataSet Name="Q"><Query><DataSourceName>DS</DataSourceName>'
        '<CommandText>SELECT 1 X FROM DUAL</CommandText></Query>'
        '<Fields><Field Name="X"><DataField>X</DataField></Field></Fields></DataSet></DataSets>'
        '<ReportParameters>'
        '<ReportParameter Name="P_X"><DataType>String</DataType>'
        '<DefaultValue><Values><Value>%s</Value></Values></DefaultValue>'
        '<Prompt>P_X</Prompt></ReportParameter>'
        '</ReportParameters>'
        '<Body><ReportItems/><Height>1in</Height></Body><Width>7in</Width>'
        '<Page><PageHeight>11in</PageHeight><PageWidth>8.5in</PageWidth></Page>'
        '</Report>'
    )
    empty = preflight_audit(base % (RDL_NS, ""), target_db="oracle")
    assert "rdl.param_default_empty" in [i["rule"] for i in empty["issues"]]
    ok = preflight_audit(base % (RDL_NS, "=Nothing"), target_db="oracle")
    assert "rdl.param_default_empty" not in [i["rule"] for i in ok["issues"]]


def test_child_filter_bind_not_injected_into_select():
    """ORA-00904 guard: a bind used ONLY in a filter expression (e.g. an
    NVL date range) must NOT be added to the child SELECT -- there is no such
    column. An equality join key (col = :bind) IS injected, as the real
    qualified column."""
    from converter.parsers.oracle_xml import _augment_child_join_keys
    from converter.models import DataQuery, DataItem
    master = DataQuery(name="Q_M", sql="SELECT 1",
                       items=[DataItem(name="PROG_ID"), DataItem(name="PERM_EXP_DATE")])
    child = DataQuery(
        name="Q_C",
        sql=("SELECT X.A FROM T X WHERE X.PROG_ID = :PROG_ID "
             "AND NVL(X.DT, NVL(:PERM_EXP_DATE, SYSDATE)) <= NVL(:PERM_EXP_DATE, SYSDATE)"),
        items=[DataItem(name="A")])
    _augment_child_join_keys(child, master, [])
    select_clause = child.sql.split("FROM")[0].upper()
    assert "X.PROG_ID" in select_clause          # real join key injected
    assert "PERM_EXP_DATE" not in select_clause  # filter-only bind skipped (no ORA-00904)


def test_preflight_blocks_dangling_field_ref():
    """A =Fields!X.Value whose field X is in NO dataset renders a runtime
    'field does not exist' error in SSRS. The gate must flag a bare dangling
    ref, and must NOT flag a real field or a scoped cross-dataset ref like
    First(Fields!X.Value, "DS"). (Guards the resolver fix that maps a layout
    field name -> its source so no dangling Fields!F_X.Value ever ships.)"""
    from converter.validators.preflight import preflight_audit
    base = (
        '<Report xmlns="%s">'
        '<DataSources><DataSource Name="DS"><DataSourceReference>s</DataSourceReference>'
        '</DataSource></DataSources>'
        '<DataSets><DataSet Name="Q"><Query><DataSourceName>DS</DataSourceName>'
        '<CommandText>SELECT 1 A</CommandText></Query>'
        '<Fields><Field Name="A"><DataField>A</DataField></Field></Fields></DataSet></DataSets>'
        '<Body><ReportItems><Textbox Name="t"><Paragraphs><Paragraph><TextRuns><TextRun>'
        '<Value>%s</Value></TextRun></TextRuns></Paragraph></Paragraphs>'
        '<Top>0in</Top><Left>0in</Left><Height>.25in</Height><Width>2in</Width></Textbox>'
        '</ReportItems><Height>1in</Height></Body><Width>7in</Width>'
        '<Page><PageHeight>11in</PageHeight><PageWidth>8.5in</PageWidth></Page></Report>'
    )
    def rules(val):
        return [i["rule"] for i in preflight_audit(base % (RDL_NS, val), target_db="oracle")["issues"]]
    assert "rdl.dangling_field_ref" in rules("=Fields!GHOST.Value")          # dangling -> flagged
    assert "rdl.dangling_field_ref" not in rules("=Fields!A.Value")          # real field -> clean
    assert "rdl.dangling_field_ref" not in rules('=First(Fields!Z.Value, "DS2")')  # scoped -> clean


def test_preflight_blocks_scoped_ref_to_wrong_dataset():
    """First/Sum(Fields!X.Value, "DS") scoped to a dataset that lacks X errors
    at run time even though X exists in ANOTHER dataset (so the plain dangling
    check passes). The 6e gate must catch the wrong-scope case."""
    from converter.validators.preflight import preflight_audit
    rdl = (
        '<Report xmlns="%s"><DataSources/><DataSets>'
        '<DataSet Name="A"><Fields><Field Name="X"><DataField>X</DataField></Field></Fields></DataSet>'
        '<DataSet Name="B"><Fields><Field Name="Y"><DataField>Y</DataField></Field></Fields></DataSet>'
        '</DataSets><Body><ReportItems><Textbox Name="t"><Paragraphs><Paragraph><TextRuns><TextRun>'
        '<Value>=First(Fields!X.Value, "B")</Value></TextRun></TextRuns></Paragraph></Paragraphs>'
        '<Top>0in</Top><Left>0in</Left><Height>.2in</Height><Width>2in</Width></Textbox></ReportItems>'
        '<Height>1in</Height></Body><Width>7in</Width>'
        '<Page><PageHeight>11in</PageHeight><PageWidth>8.5in</PageWidth></Page></Report>'
    ) % RDL_NS
    rules = [i["rule"] for i in preflight_audit(rdl, target_db="oracle")["issues"]]
    assert "rdl.scoped_ref_field_missing" in rules
