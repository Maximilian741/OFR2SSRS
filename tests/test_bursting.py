"""Tests for the plug-and-play bursting tab (auto-fill + Burst Pack zip)."""
import io
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter.models import (  # noqa: E402
    ParsedReport, DataQuery, DataItem, ReportParameter, FormulaColumn,
)
from converter import bursting as b  # noqa: E402


def _make_bursting_report(main_table="Permit", email_col="Recipient_Email"):
    r = ParsedReport(name="TEST_REPORT")
    r.parameters = [ReportParameter(name="P_AS_PATH"), ReportParameter(name="P_PERM_NUM")]
    q = DataQuery(name="Q_MAIN")
    q.tsql = "SELECT a.Perm_Num, a." + email_col + ", a.Site_Name FROM dbo." + main_table + " AS a"
    q.items = [DataItem(name="Perm_Num"), DataItem(name=email_col), DataItem(name="Site_Name")]
    r.queries = [q]
    r.formulas = [FormulaColumn(name="CF_File_F", plsql_body="RETURN(:P_AS_PATH || :Perm_Num || '.pdf');")]
    r.triggers = []
    return r


def _strip_header(sql):
    """Return only the live SQL body (after the leading '--' comments)."""
    out = []
    started = False
    for ln in sql.splitlines():
        s = ln.strip()
        if not started:
            if not s or s.startswith("--"):
                continue
            started = True
        out.append(ln)
    return "\n".join(out)


def test_email_burst_query_autofills_main_table():
    r = _make_bursting_report(main_table="Permit")
    info = b.detect_bursting(r)
    sql = b.build_email_burst_query(r, info)
    body = _strip_header(sql)
    assert "FROM dbo.Permit AS p" in body
    assert "<MainTable>" not in body
    assert "<MainTable>      -> Permit" in sql


def test_email_burst_query_autofills_email_column():
    r = _make_bursting_report(email_col="Recipient_Email")
    info = b.detect_bursting(r)
    sql = b.build_email_burst_query(r, info)
    body = _strip_header(sql)
    assert "Recipient_Email" in body
    assert "<RecipientEmail>" not in body


def test_email_burst_query_leaves_placeholder_when_no_email_col():
    r = ParsedReport(name="X")
    r.parameters = [ReportParameter(name="P_AS_PATH")]
    q = DataQuery(name="Q")
    q.tsql = "SELECT id FROM dbo.Things"
    q.items = [DataItem(name="Id")]
    r.queries = [q]
    r.formulas = [FormulaColumn(name="CF_File_F", plsql_body="RETURN(:P_AS_PATH || :Id);")]
    r.triggers = []
    info = b.detect_bursting(r)
    sql = b.build_email_burst_query(r, info)
    body = _strip_header(sql)
    assert "<RecipientEmail>" in body
    assert "NOT DETECTED" in sql


def test_build_burst_pack_zip_contents_and_overrides():
    r = _make_bursting_report()
    info = b.detect_bursting(r)
    overrides = {
        "SmtpServer":      "smtp.office365.com",
        "SmtpPort":        587,
        "AuthMode":        "Office365",
        "SmtpFrom":        "[email protected]",
        "SubjectTemplate": "{ReportName} - {BurstKey}",
        "BodyTemplate":    "Body for {BurstKey}",
    }
    blob = b.build_burst_pack_zip(r, "<Report/>", info, overrides)
    z = zipfile.ZipFile(io.BytesIO(blob))
    names = sorted(z.namelist())
    assert names == sorted([
        "TEST_REPORT.rdl", "burst.config.json", "Send-Reports.ps1",
        "README.md", "service-account-setup.md",
    ])
    cfg = json.loads(z.read("burst.config.json"))
    for k, v in overrides.items():
        assert cfg[k] == v
    ps = z.read("Send-Reports.ps1").decode("utf-8")
    assert "TEST_REPORT" in ps
    assert "Burst_Key" in ps
    readme = z.read("README.md").decode("utf-8")
    assert "smtp.office365.com" in readme


def test_burst_pack_uses_overridden_sql():
    r = _make_bursting_report()
    info = b.detect_bursting(r)
    custom_sql = "-- USER EDITED\nSELECT * FROM custom_view"
    overrides = {"EmailBurstSql": custom_sql}
    blob = b.build_burst_pack_zip(r, "<Report/>", info, overrides)
    z = zipfile.ZipFile(io.BytesIO(blob))
    ps = z.read("Send-Reports.ps1").decode("utf-8")
    assert "USER EDITED" in ps
    assert "custom_view" in ps
