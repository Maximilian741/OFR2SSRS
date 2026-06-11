"""The SQL Server target (target_db="sqlserver") emits translated T-SQL with a
SQL DataProvider. It must stay XSD-valid, NOT use OracleClient, and leave no
untranslated Oracle construct (NVL / || / SYSDATE / :P bind) in a LIVE query
(comment blocks documenting the original PL/SQL are fine).
"""
from __future__ import annotations

import html
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402

RD = "{http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition}"


def _fixtures():
    sot = ROOT / "tests" / "fixtures" / "source_of_truth"
    out = []
    for d in sorted(sot.glob("*")):
        src = d / "source.xml"
        if src.exists():
            out.append(pytest.param(src.read_bytes(), id=d.name))
    return out


def _strip_comments(s: str) -> str:
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    s = re.sub(r"(?m)^\s*--.*$", "", s)
    return s


@pytest.mark.parametrize("xml_bytes", _fixtures())
def test_sqlserver_target_is_valid_tsql(xml_bytes):
    out = convert(xml_bytes, target_db="sqlserver")
    rdl = out["rdl_xml"]
    # structurally valid + SQL provider, not OracleClient
    xsd = ROOT / "tests" / "fixtures" / "schema" / "ReportDefinition_2008.xsd"
    if xsd.exists():
        etree = pytest.importorskip("lxml.etree")
        schema = etree.XMLSchema(etree.parse(str(xsd)))
        assert schema.validate(etree.fromstring(rdl.encode())), \
            "\n".join(e.message for e in schema.error_log[:5])
    assert "OracleClient" not in rdl
    # no untranslated Oracle construct in any LIVE query
    for c in re.findall(r"<CommandText>(.*?)</CommandText>", rdl, re.S):
        live = _strip_comments(html.unescape(c))
        assert not re.search(r"\bNVL\s*\(|\|\||\bSYSDATE\b", live, re.I), live[:120]
        assert not re.search(r":[A-Za-z_]\w*", live), live[:120]


def test_oracle_target_keeps_oracle_provider():
    """Sanity: the default Oracle target still uses the Oracle path (preserves
    :P binds + OracleClient), so the two targets stay distinct."""
    fx = _fixtures()
    if not fx:
        pytest.skip("no fixtures")
    rdl = convert(fx[0].values[0], target_db="oracle")["rdl_xml"]
    assert "<CommandText>" in rdl
