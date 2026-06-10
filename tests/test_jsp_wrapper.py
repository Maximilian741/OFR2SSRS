"""Oracle Reports stored as a .jsp 'Reports Web Source' (the <report> block
embedded in an HTML comment between <rw:report>/<rw:objects>, followed by the
paper-layout HTML) must be unwrapped to the real report before parsing.
Wild-corpus verified — this is a very common storage format.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402
from converter.parsers.oracle_xml import (  # noqa: E402
    parse_oracle_xml, _extract_embedded_report)

_REPORT = (
    '<report name="WrappedReport" DTDVersion="9.0.2.0.10">'
    "<data><dataSource name=\"Q1\"><select><![CDATA[SELECT A FROM T]]></select>"
    '<group name="G1"><dataItem name="A" datatype="vchar2"/></group>'
    "</dataSource></data>"
    '<layout><section name="main"><body width="7" height="9">'
    '<field name="F_A" source="A"><geometryInfo x="0" y="0" width="3" height="0.2"/></field>'
    "</body></section></layout></report>"
)

_JSP = (
    '<%@ taglib uri="/WEB-INF/lib/reports_tld.jar" prefix="rw" %>\n'
    '<%@ page language="java" %>\n'
    "<!--\n<rw:report id=\"report\">\n<rw:objects id=\"objects\">\n"
    '<?xml version="1.0" encoding="WINDOWS-1252" ?>\n'
    + _REPORT +
    "\n</rw:objects>\n-->\n\n"
    '<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01//EN">\n'
    "<html><body>paper layout template here</body></html>\n"
).encode("utf-8")


def test_extract_helper_pulls_report_from_jsp():
    raw = _JSP.decode("utf-8")
    got = _extract_embedded_report(raw)
    assert got is not None
    assert got.startswith("<report")
    assert got.rstrip().endswith("</report>")
    assert "<html>" not in got and "<%@" not in got


def test_clean_report_is_not_rewrapped():
    # A normal report document (no wrapper) must pass through untouched.
    assert _extract_embedded_report(_REPORT) is None
    assert _extract_embedded_report('<?xml version="1.0"?>\n' + _REPORT) is None


def test_jsp_parses_and_converts():
    rep = parse_oracle_xml(_JSP)
    assert rep.name == "WrappedReport"
    assert any(it.name == "A" for q in rep.queries for it in (q.items or []))
    out = convert(_JSP)
    assert out["report"]["name"] == "WrappedReport"
    assert (out.get("preflight") or {}).get("verdict") == "READY"
    assert "<DataSet" in out["rdl_xml"]
