"""Cross-query GROUP subtotals: an Oracle <summary reset="G_x"> whose source
column lives in a DIRECT child query of the reset group's master renders as a
per-key master-bound Tablix using LookupSet over Oracle's EXACT <link> keys,
reduced by a VB Code.SumLookup (=Sum(LookupSet(..)) is invalid SSRS).
"""
from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "tools" / "renderlab"))

from converter import convert  # noqa: E402

# A master Q_M (emp) -- one row per company -- and a linked detail Q_D (emp, amt).
# Numeric emp so RenderLab's synthetic data (1000+idx, same per dataset) makes
# the cross-dataset lookup actually MATCH -> the subtotal is verifiably non-zero.
_MD = (
    b'<?xml version="1.0"?><report name="GS" DTDVersion="9.0.2.0.10"><data>'
    b'<dataSource name="Q_M"><select><![CDATA[SELECT emp FROM m]]></select>'
    b'<group name="G_M"><dataItem name="emp" datatype="number"/></group></dataSource>'
    b'<dataSource name="Q_D"><select><![CDATA[SELECT emp, amt FROM d WHERE emp=:emp]]></select>'
    b'<group name="G_D"><dataItem name="emp" datatype="number"/>'
    b'<dataItem name="amt" datatype="number"/></group></dataSource>'
    b'<link name="L" parentGroup="G_M" parentColumn="emp" childQuery="Q_D" '
    b'childColumn="emp" condition="eq" sqlClause="where"/>'
    b'<summary name="CS_dsub" source="amt" function="sum" reset="G_M" compute="report"/>'
    b'</data><layout><section name="main"><body width="8" height="9">'
    b'<repeatingFrame name="R" source="G_M"><geometryInfo x="0" y="0" width="6" height="0.3"/>'
    b'<field name="F_emp" source="emp"><geometryInfo x="0" y="0" width="2" height="0.2"/></field>'
    b'</repeatingFrame>'
    b'<field name="F_sub" source="CS_dsub"><geometryInfo x="0" y="3" width="2" height="0.2"/></field>'
    b'</body></section></layout></report>')


def test_crossquery_subtotal_emits_lookupset_with_exact_keys():
    rdl = convert(_MD)["rdl_xml"]
    assert 'Code.SumLookup(LookupSet(Fields!emp.Value, Fields!emp.Value, ' \
           'Fields!amt.Value, "Q_D"))' in rdl
    assert "Public Function SumLookup" in rdl          # VB reducer injected


def test_no_subtotal_tablix_without_group_summary():
    """Gate: a report with no cross-query group subtotal emits no Subtot tablix
    (corpus stays byte-identical)."""
    plain = _MD.replace(b'reset="G_M"', b'reset="report"')  # demote to grand total
    rdl = convert(plain)["rdl_xml"]
    assert 'Name="Subtot_' not in rdl


try:
    from render import render_rdl, lib_ready, expression_host_available  # noqa: E402
    # This test asserts a COMPUTED subtotal value appears in the PDF, which
    # needs RenderLab.exe's live expression host — the staticized layout path
    # can't evaluate Code.SumLookup. Skip when that host is unavailable.
    _EXPR_OK = lib_ready() and expression_host_available()
except Exception:  # noqa: BLE001
    _EXPR_OK = False


@pytest.mark.skipif(not _EXPR_OK or sys.platform != "win32",
                    reason="RenderLab.exe expression host unavailable "
                           "(DLLs unfetched / non-Windows / Application Control block)")
def test_crossquery_subtotal_value_is_correct_through_ms_engine():
    """The one check a publish-test can't give: the cross-dataset lookup finds
    rows and sums them (a WRONG join key -> empty -> 0). RenderLab feeds emp=
    1000+idx identically to Q_M and Q_D, so each company's subtotal == that
    company's single matching amt (also 1000+idx) -- non-zero proves the key."""
    rdl = convert(_MD)["rdl_xml"]
    d = Path(tempfile.mkdtemp())
    (d / "r.rdl").write_text(rdl, encoding="utf-8")
    res = render_rdl(d / "r.rdl", d / "r.pdf", rows=3)
    assert res["ok"], res["log"][-1200:]
    assert not [l for l in res["log"].splitlines()
                if "rsCompilerError" in l or "rsRuntimeError" in l or "#Error" in l]
    from pypdf import PdfReader
    txt = "\n".join((p.extract_text() or "") for p in PdfReader(res["pdf"]).pages)
    # the subtotal column must contain the matched 1000-range values (non-zero)
    assert re.search(r"\b1000\b", txt), txt[:400]
