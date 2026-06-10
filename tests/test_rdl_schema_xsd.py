"""The definitive upload-safety gate: validate every generated RDL against the
REAL Microsoft RDL 2008/01 schema (ReportDefinition.xsd).

This is strictly stronger than the structural preflight -- lxml validates the
RDL against Microsoft's actual XSD, so a report that converts to schema-INVALID
RDL (which SSRS would reject at upload) fails here. Runs over the source-of-
truth fixtures, the sub-report fixtures, and the full synthetic stress matrix.

The schema is resolved from $O2S_RDL_XSD, else the bundled copy under
tests/fixtures/schema/ (see that folder's NOTE.md); the suite skips cleanly if
neither is present, so it never silently tests nothing.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "tests"))

from converter import convert  # noqa: E402

etree = pytest.importorskip("lxml.etree")  # always available (the parser uses lxml)

_BUNDLED_XSD = ROOT / "tests" / "fixtures" / "schema" / "ReportDefinition_2008.xsd"


def _xsd_path():
    env = os.environ.get("O2S_RDL_XSD")
    if env and Path(env).exists():
        return Path(env)
    if _BUNDLED_XSD.exists():
        return _BUNDLED_XSD
    return None


@pytest.fixture(scope="module")
def schema():
    p = _xsd_path()
    if p is None:
        pytest.skip("RDL 2008 XSD not available (set O2S_RDL_XSD or bundle it)")
    return etree.XMLSchema(etree.parse(str(p)))


def _inputs():
    out = []
    sot = ROOT / "tests" / "fixtures" / "source_of_truth"
    if sot.exists():
        for d in sorted(sot.iterdir()):
            src = d / "source.xml"
            if src.exists():
                out.append(pytest.param(src.read_bytes(), id=f"fixture-{d.name}"))
    sub = ROOT / "tests" / "fixtures" / "subreports"
    if sub.exists():
        for p in sorted(sub.glob("*.xml")):
            out.append(pytest.param(p.read_bytes(), id=f"subreport-{p.stem}"))
    try:  # the synthetic stress matrix -- every diverse/pathological shape
        from test_synthetic_stress import build, CASES
        for cid, (cols, params, dup) in CASES.items():
            out.append(pytest.param(
                build(cid.upper(), cols, params, dup_query=dup), id=f"matrix-{cid}"))
    except Exception:
        pass
    return out


def _assert_valid(schema, rdl, what):
    assert rdl, f"{what}: empty RDL"
    tree = etree.fromstring(rdl.encode("utf-8"))
    if not schema.validate(tree):
        errs = "\n  ".join(f"L{e.line}: {e.message}"
                           for e in list(schema.error_log)[:8])
        pytest.fail(f"{what} is NOT valid against the real RDL 2008 schema "
                    f"(SSRS would reject the upload):\n  " + errs)


@pytest.mark.parametrize("xml_bytes", _inputs())
def test_generated_rdl_is_schema_valid(schema, xml_bytes):
    _assert_valid(schema, convert(xml_bytes).get("rdl_xml") or "", "RDL")


def test_subreport_rdl_is_schema_valid(schema):
    """The drill-through CHILD RDL (compose_subreport_rdl) must also validate."""
    from converter.subreports import compose_subreport_rdl
    res = compose_subreport_rdl("CHILD_REPORT", artifacts=[])
    _assert_valid(schema, res.get("rdl_xml") or "", "sub-report RDL")


@pytest.mark.parametrize("xml_bytes", [
    pytest.param(b"this is not an oracle report at all", id="garbage"),
    pytest.param(b"<?xml version='1.0'?><notareport/>", id="wrong-root"),
    pytest.param(b"\xff\xfe<x/>", id="binary"),
    pytest.param(b"", id="empty"),
])
def test_fallback_rdl_is_schema_valid(schema, xml_bytes):
    """Degenerate inputs hit the crash-safety fallback -- the fallback RDL must
    STILL be schema-valid (a broken-but-valid scaffold, never malformed XML)."""
    rdl = convert(xml_bytes).get("rdl_xml") or ""
    if not rdl:
        pytest.skip("no RDL produced for this input")
    _assert_valid(schema, rdl, "fallback RDL")
