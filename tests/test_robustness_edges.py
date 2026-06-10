"""Works-every-time guarantee: convert() must NEVER crash and must ALWAYS
produce a schema-valid, uploadable RDL + a preview + a preflight verdict, for
ANY input -- including malformed, truncated, binary, or non-Oracle payloads.
The converter degrades to a clear fallback instead of failing."""
from __future__ import annotations
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402

RD_NS = "http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition"

EDGES = {
    "empty": b"",
    "whitespace": b"   \n\t ",
    "not_xml": b"}{>< not xml at all",
    "truncated": b"<?xml version='1.0'?><report><data><dataSource",
    "binary": bytes(range(256)) * 4,
    "html": b"<!DOCTYPE html><html><body><h1>hi</h1></body></html>",
    "empty_report": b"<?xml version='1.0'?><report name='X'></report>",
    "no_queries": b"<?xml version='1.0'?><report name='X'><data/></report>",
    "unicode": "<?xml version='1.0'?><report name='é中\U0001f600'><data/></report>".encode(),
    "huge_name": ("<?xml version='1.0'?><report name='" + "A" * 9000 + "'><data/></report>").encode(),
    "deep_nest": b"<?xml version='1.0'?><report>" + b"<g>" * 200 + b"</g>" * 200 + b"</report>",
}


def _schema():
    xsd = ROOT / "tests" / "fixtures" / "schema" / "ReportDefinition_2008.xsd"
    if not xsd.exists():
        return None
    from lxml import etree
    return etree.XMLSchema(etree.parse(str(xsd)))


@pytest.mark.parametrize("name", list(EDGES))
def test_convert_never_crashes_and_is_uploadable(name):
    res = convert(EDGES[name])                      # must not raise
    rdl = res.get("rdl_xml") or ""
    assert "<Report" in rdl, f"{name}: no RDL produced"
    assert res.get("mockup_html"), f"{name}: no preview produced"
    assert isinstance(res.get("preflight"), dict), f"{name}: no preflight verdict"
    schema = _schema()
    if schema is not None:
        from lxml import etree
        assert schema.validate(etree.fromstring(rdl.encode())), \
            f"{name}: RDL is not schema-valid -> would not upload"


def test_dataset_with_no_columns_omits_empty_fields():
    """A dataset/report with zero columns must NOT emit an empty <Fields/>
    (XSD requires >=1 Field) -- it should omit the element and stay valid."""
    res = convert(b"<?xml version='1.0'?><report name='X'><data><dataSource name='Q'>"
                  b"<select>SELECT 1 FROM DUAL</select></dataSource></data></report>")
    rdl = res.get("rdl_xml") or ""
    assert "<Fields></Fields>" not in rdl and "<Fields/>" not in rdl
    schema = _schema()
    if schema is not None:
        from lxml import etree
        assert schema.validate(etree.fromstring(rdl.encode()))
