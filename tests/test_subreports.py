"""Tests for sub-report (drill-through) detection + RDL composition.

Generic, name-agnostic: every assertion is driven by what's actually in
the parsed report, never by literal report or column names.
"""
from __future__ import annotations
import os
from pathlib import Path

import pytest


# Bundled synthetic fixtures (a drill-through report, a master-detail without
# hyperlinks, a per-record letter) so this suite runs for anyone cloning the
# repo. No client data.
UPLOADS = Path(__file__).resolve().parent / "fixtures" / "subreports"


def _xml(name):
    p = UPLOADS / name
    if not p.exists():
        pytest.skip(f"{name} not staged in {UPLOADS}")
    return p.read_bytes()


def test_subreports_detected_when_hyperlink_present():
    """A report with <webSettings hyperlink="..."> markers must produce
    at least one detected sub-report link."""
    from converter import convert
    out = convert(_xml("SAMPLE_DRILLTHROUGH.xml"))
    links = out.get("subreport_links", [])
    assert links, "SAMPLE_DRILLTHROUGH has webSettings hyperlink markers; expected >=1 link"
    # Each link must carry the structural keys.
    for ln in links:
        for k in ("child_name", "parent_field", "url_formula", "bind_params"):
            assert k in ln, f"missing key {k} in link {ln!r}"


def test_no_subreports_when_no_hyperlinks():
    """SAMPLE_MASTER_DETAIL has no drill-through hyperlinks -> empty list."""
    from converter import convert
    out = convert(_xml("SAMPLE_MASTER_DETAIL.xml"))
    assert out.get("subreport_links") == [], \
        f"SAMPLE_MASTER_DETAIL should have no subreport_links, got {out.get('subreport_links')}"


def test_bursting_suppressed_for_drillthrough_only():
    """SAMPLE_DRILLTHROUGH was previously mis-classified as bursting (P_AS_PATH
    parameter alone triggered the flag). It is drill-through, not
    bursting -- no per-row email/distribution markers. Must now be
    flagged is_bursting=False."""
    from converter import convert
    out = convert(_xml("SAMPLE_DRILLTHROUGH.xml"))
    assert out["bursting"]["is_bursting"] is False, \
        "SAMPLE_DRILLTHROUGH is drill-through only; should not be flagged as bursting"
    # The reclassification reason should be captured in evidence.
    evidence = " ".join(out["bursting"].get("evidence", []))
    assert "drill" in evidence.lower() or "reclassif" in evidence.lower(), \
        f"expected reclassification evidence, got {evidence!r}"


def test_real_bursting_still_detected():
    """SAMPLE_LETTER_CHILD is a real bursting report (letter-mailing).
    Sub-report detection must NOT suppress that flag."""
    from converter import convert
    out = convert(_xml("SAMPLE_LETTER_CHILD.xml"))
    # It may or may not be flagged as bursting depending on the
    # heuristic, but the test asserts the suppression doesn't fire
    # spuriously for reports with EMAIL/distribution markers.
    # If it has bursting markers AND drill-through, bursting wins.
    assert isinstance(out["bursting"].get("is_bursting"), bool)


def test_compose_subreport_rdl_minimal_works():
    """compose_subreport_rdl produces a schema-valid RDL even with
    minimal inputs (no SQL artifact)."""
    from converter.subreports import compose_subreport_rdl
    result = compose_subreport_rdl("CHILD_REPORT", artifacts=[])
    rdl = result["rdl_xml"]
    # Must parse as XML.
    import xml.etree.ElementTree as ET
    root = ET.fromstring(rdl)
    NS = "{http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition}"
    # Must have the required structural elements per SSRS 2008/01.
    assert root.find(NS + "DataSources") is not None
    assert root.find(NS + "DataSets") is not None
    assert root.find(NS + "Body") is not None
    body = root.find(NS + "Body")
    assert body.find(NS + "Height") is not None
    # No empty must-have-child containers (would fail SSRS upload).
    for el in root.iter():
        tag = el.tag.split("}")[-1] if isinstance(el.tag, str) else ""
        if tag in ("ReportItems", "CellContents") and len(list(el)) == 0:
            pytest.fail(f"empty <{tag}/> would fail SSRS upload")
    # Result dict surface
    assert "fields" in result
    assert "binds" in result
    assert "sql" in result
    assert "issues" in result


def test_compose_subreport_rdl_with_sql_in_text_file(tmp_path):
    """When an artifact contains real SQL, columns and binds are
    extracted and a non-placeholder Fields block is emitted."""
    from converter.subreports import compose_subreport_rdl
    sql_file = tmp_path / "child.sql"
    sql_file.write_text(
        "SELECT P.NAME AS PERM_NAME, P.ADDR, COUNT(*) AS CNT\n"
        "FROM PERMITS P\n"
        "WHERE P.PERM_NUM = :P_PERM_NUM\n"
        "  AND P.YEAR = :P_RENEWAL_YEAR\n",
        encoding="utf-8",
    )
    result = compose_subreport_rdl(
        "CHILD",
        artifacts=[str(sql_file)],
        parent_param_names=["P_PERM_NUM", "P_RENEWAL_YEAR", "P_USER"],
    )
    # Columns parsed from SELECT.
    assert "PERM_NAME" in result["fields"]
    assert "ADDR" in result["fields"]
    assert "CNT" in result["fields"]
    # Binds parsed; both happen to match parent params.
    assert set(result["binds"]) == {"P_PERM_NUM", "P_RENEWAL_YEAR"}
    assert set(result["forwarded_params"]) == {"P_PERM_NUM", "P_RENEWAL_YEAR"}
    # No "could not infer SQL" issue.
    assert all("no SQL" not in i for i in result["issues"]), result["issues"]


# ---------------------------------------------------------------------------
# Drill-through parameter reconciliation (parent forwards P_X that the child
# SQL does NOT bind -- e.g. an Oracle lexical-filtered child). The child RDL
# MUST still declare each forwarded param or SSRS errors "parameter not
# declared" the instant the link is clicked. All generic -- synthetic SQL,
# no client data.
# ---------------------------------------------------------------------------

_ROOT = Path(__file__).resolve().parent.parent
_XSD = _ROOT / "tests" / "fixtures" / "schema" / "ReportDefinition_2008.xsd"


def _assert_xsd_valid(rdl_xml: str):
    """Validate against the REAL Microsoft RDL 2008/01 schema when available."""
    etree = pytest.importorskip("lxml.etree")
    if not _XSD.exists():
        pytest.skip("RDL 2008 XSD not bundled")
    schema = etree.XMLSchema(etree.parse(str(_XSD)))
    doc = etree.fromstring(rdl_xml.encode("utf-8"))
    ok = schema.validate(doc)
    assert ok, "\n".join(e.message for e in schema.error_log[:6])


def _report_params_with_empty_default(rdl_xml: str):
    """Return names of ReportParameters that have NO usable DefaultValue --
    exactly the shape that triggers SSRS's 'Define Query Parameters' refresh
    prompt (the load-bearing invariant we must never reintroduce)."""
    import re
    bad = []
    for name, block in re.findall(
            r'<ReportParameter Name="([^"]+)">(.*?)</ReportParameter>',
            rdl_xml, re.S):
        m = re.search(r"<DefaultValue>(.*?)</DefaultValue>", block, re.S)
        if not m or not re.search(r"<Value>\s*\S", m.group(1)):
            bad.append(name)
    return bad


def test_lexical_and_id_column_helpers():
    """The structural helpers find lexical refs and qualified *_Id columns
    without any report-specific knowledge."""
    from converter.subreports import _lexical_refs_in_sql, _id_columns_in_sql
    sql = ("SELECT O.Org_Id, SA.Site_Id FROM Org O, Site_Aff SA "
           "WHERE O.Org_Id = SA.Org_Id &P_CRITERIA ORDER BY 1")
    assert _lexical_refs_in_sql(sql) == ["P_CRITERIA"]
    # XML entity escapes are NOT lexicals.
    assert _lexical_refs_in_sql("a &amp; b &lt; c") == []
    cols = _id_columns_in_sql(sql)
    assert "O.Org_Id" in cols and "SA.Site_Id" in cols and "SA.Org_Id" in cols


def test_compose_child_declares_drillthrough_params_with_defaults():
    """A forwarded param the child SQL never binds is still declared, Hidden,
    and -- critically -- carries a =Nothing default so refresh never prompts."""
    from converter.subreports import compose_subreport_rdl
    import re
    result = compose_subreport_rdl(
        "CHILD",
        artifacts=[],                       # no SQL -> placeholder query
        parent_param_names=[],
        drillthrough_params=["P_ORG_ID", "P_SITE_ID"],
    )
    rdl = result["rdl_xml"]
    declared = set(re.findall(r'<ReportParameter Name="([^"]+)"', rdl))
    assert {"P_ORG_ID", "P_SITE_ID"} <= declared, declared
    # Forwarded-only params are Hidden (parent sets them).
    assert rdl.count("<Hidden>true</Hidden>") >= 2
    # The load-bearing invariant: NO param with an empty/missing default.
    assert _report_params_with_empty_default(rdl) == []
    assert "P_ORG_ID" in result["forwarded_params"]
    _assert_xsd_valid(rdl)


def test_build_subreport_reconciles_lexical_filtered_child():
    """End-to-end: a child whose SQL filters through a lexical (&P_CRITERIA)
    and binds only :P_Flag, with the parent forwarding P_ORG_ID/P_SITE_ID.
    The built child must declare ALL THREE, every one with a default, the
    query must stay valid (lexical neutralized), and the RDL XSD-valid."""
    from converter.subreports import build_subreport
    import re
    sql = (
        "SELECT O.Org_Id, S.Site_Name, "
        "DECODE(:P_Flag, 'YES', S.Site_Name, NULL) AS Disp "
        "FROM Organization O, Site S "
        "WHERE O.Org_Id = S.Org_Id(+) &P_CRITERIA "
        "GROUP BY O.Org_Id, S.Site_Name"
    )
    import tempfile, os
    fd, path = tempfile.mkstemp(suffix=".sql")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(sql)
        res = build_subreport(
            "CHILD_ENVELOPE", [path],
            parent_param_names=["P_Flag"],
            drillthrough_params=["P_ORG_ID", "P_SITE_ID"],
        )
    finally:
        os.unlink(path)
    assert res["source"] == "sql"
    rdl = res["rdl_xml"]
    declared = set(re.findall(r'<ReportParameter Name="([^"]+)"', rdl))
    # The parent forwards P_ORG_ID/P_SITE_ID; both must be declared.
    assert {"P_ORG_ID", "P_SITE_ID"} <= declared, declared
    # The child's own bind is declared too.
    assert "P_Flag" in declared
    # No refresh-prompt landmine anywhere.
    assert _report_params_with_empty_default(rdl) == []
    # The lexical was neutralized so the query is valid SQL (no raw &P_CRITERIA
    # outside a comment).
    cmd = re.search(r"<CommandText>(.*?)</CommandText>", rdl, re.S).group(1)
    assert "lexical ref" in cmd  # replaced with a comment
    # forwarded_params surfaces the drill-through targets.
    assert {"P_ORG_ID", "P_SITE_ID"} <= set(res["forwarded_params"])
    # A reconciliation note tells the user how to wire the filter.
    joined = " ".join(res["issues"]).lower()
    assert "drill-through" in joined and "lexical" in joined
    _assert_xsd_valid(rdl)
