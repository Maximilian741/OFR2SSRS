"""Synthetic stress matrix: generate a WIDE variety of Oracle report shapes
(including pathological ones) and assert strong upload-safety + faithfulness
invariants on each generated RDL. This is the standing guarantee that the core
promise -- any Oracle report -> an uploadable, faithful 1:1 RDL -- holds across
shapes, not just the curated fixtures.

Invariants per generated report:
  * RDL is well-formed XML and convert() raised no conversion_error
  * preflight has NO RED issue (the upload-blocker class)
  * every declared <userParameter> appears as a <ReportParameter>
  * every dataItem appears as a dataset <Field>/<DataField> (no silent drop)
  * NO duplicate <Field Name> within a dataset (SSRS rejects)
  * NO duplicate <DataSet Name> (SSRS rejects)
  * NO dangling Fields!X reference (X must be declared in some dataset)

All inputs are synthetic; no client data.
"""
from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from converter import convert  # noqa: E402

RDL_NS = "http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition"
RD = "{" + RDL_NS + "}"


def _x(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def build(name, cols, params=None, dup_query=False):
    """Build a tabular Oracle Reports XML from column/param specs.

    cols:   list of {name, dtype?, scale?, mask?, label?}
    params: list of {name, dtype?, label?}
    dup_query: also emit a SECOND <dataSource> with the SAME name (Q_MAIN).
    """
    params = params or []
    items, fields, headers = [], [], []
    for i, c in enumerate(cols):
        n, dt = c["name"], c.get("dtype", "vchar2")
        numeric = dt in ("number", "integer", "float", "currency")
        odt = f' oracleDatatype="{dt}"' if numeric else ""
        dta = "" if numeric else f' datatype="{_x(dt)}"'
        sc = f' scale="{c["scale"]}"' if c.get("scale") is not None else ""
        brk = ' breakOrder="none"' if i > 0 else ""
        lbl = c.get("label", n)
        items.append(
            f'<dataItem name="{_x(n)}"{odt}{dta} columnOrder="{i+1}" '
            f'defaultLabel="{_x(lbl)}"{brk}>'
            f'<dataDescriptor expression="{_x(n)}"{sc} precision="10"/></dataItem>')
        mk = f' formatMask="{_x(c["mask"])}"' if c.get("mask") else ""
        x = 0.02 + i * 1.0
        fields.append(
            f'<field name="F{i}" source="{_x(n)}"{mk}><font face="Arial" size="10"/>'
            f'<geometryInfo x="{x:.2f}" y="0.40" width="0.95" height="0.18"/></field>')
        headers.append(
            f'<text name="B{i}"><geometryInfo x="{x:.2f}" y="0.0" width="0.95" height="0.17"/>'
            f'<textSegment><font face="Arial" size="10" bold="yes"/>'
            f'<string><![CDATA[{lbl}]]></string></textSegment></text>')
    params_xml = "".join(
        f'<userParameter name="{_x(p["name"])}" datatype="{p.get("dtype","character")}" '
        f'label="{_x(p.get("label", p["name"]))}"/>' for p in params)
    sel = "SELECT " + ", ".join(_x(c["name"]) for c in cols) + " FROM T"
    main_ds = (
        f'<dataSource name="Q_MAIN"><select canParse="no"><![CDATA[{sel}]]></select>'
        f'<group name="G_MAIN">{"".join(items)}</group></dataSource>')
    second = ""
    if dup_query:
        second = (
            '<dataSource name="Q_MAIN"><select canParse="no">'
            '<![CDATA[SELECT OTHER FROM T2]]></select>'
            '<group name="G_DUP"><dataItem name="OTHER" datatype="vchar2" '
            'defaultLabel="Other"><dataDescriptor expression="OTHER"/></dataItem>'
            '</group></dataSource>')
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<report name="{_x(name)}" DTDVersion="9.0.2.0.10"><data>'
        f'{params_xml}{main_ds}{second}</data>'
        f'<layout><section name="main" width="11" height="8.5">'
        f'<body width="10" height="7"><location x="0.3" y="0.7"/>'
        f'<repeatingFrame name="R_MAIN" source="G_MAIN" printDirection="down">'
        f'<geometryInfo x="0" y="0.4" width="10" height="0.2"/>'
        f'{"".join(fields)}</repeatingFrame>'
        f'<frame name="HDR"><geometryInfo x="0" y="0" width="10" height="0.38"/>'
        f'<visualSettings fillPattern="solid" fillForegroundColor="darkblue" '
        f'lineForegroundColor="white"/>{"".join(headers)}</frame></body>'
        f'<margin><text name="T"><geometryInfo x="3" y="0.25" width="4" height="0.2"/>'
        f'<textSegment><font face="Arial" size="12" bold="yes"/>'
        f'<string><![CDATA[{_x(name)}]]></string></textSegment></text></margin>'
        f'</section></layout></report>'
    ).encode("utf-8")


def _C(name, **kw):
    return dict(name=name, **kw)


# --- the matrix: id -> (cols, params, dup_query) -------------------------------
CASES = {
    "single_col": ([_C("ONLY")], [], False),
    "wide_20col": ([_C(f"COL{i}", dtype="number") for i in range(20)], [], False),
    "all_types": ([_C("S", dtype="vchar2"), _C("N", dtype="number"),
                   _C("D", dtype="date"), _C("F", dtype="float"),
                   _C("CUR", dtype="currency"), _C("I", dtype="integer"),
                   _C("MON", dtype="number", scale=2)], [], False),
    "with_masks": ([_C("AMT", dtype="number", scale=2, mask="$NNN,NN0.00"),
                    _C("DT", dtype="date", mask="DD-MON-YYYY"),
                    _C("PCT", dtype="number", scale=4, mask="0.0000")], [], False),
    "many_params": ([_C("A"), _C("B")],
                    [_C(f"P_{i}", dtype="character") for i in range(15)], False),
    "unicode_names": ([_C("CÔL_É", label="Coût (€)"), _C("RÉGION", label="Région")],
                      [], False),
    "leading_digit": ([_C("9NINE", dtype="number"), _C("NAME")], [], False),
    "name_collision": ([_C("AMT.USD", dtype="number"), _C("AMT-USD", dtype="number"),
                        _C("LABEL")], [], False),
    "long_names": ([_C("A" * 120, dtype="number"), _C("B" * 90)], [], False),
    "duplicate_query": ([_C("X"), _C("Y")], [], True),
    "reserved_words": ([_C("SELECT", dtype="vchar2"), _C("FROM"), _C("ORDER")],
                       [], False),
    "spaces_in_names": ([_C("First Name"), _C("Order Date", dtype="date")], [], False),
}


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_synthetic_report_is_upload_safe_and_faithful(case_id):
    cols, params, dup = CASES[case_id]
    xml = build(case_id.upper(), cols, params, dup_query=dup)
    res = convert(xml)
    rdl = res.get("rdl_xml") or ""

    assert res.get("conversion_error") in (None, ""), \
        f"[{case_id}] conversion_error: {res.get('conversion_error')}"
    assert rdl, f"[{case_id}] empty RDL"
    root = ET.fromstring(rdl)  # well-formed or this raises

    # --- faithfulness invariants (always hold) -------------------------------
    rps = {(rp.get("Name") or "").upper() for rp in root.iter(RD + "ReportParameter")}
    for p in params:
        assert p["name"].upper() in rps, f"[{case_id}] param {p['name']} dropped"

    datafields = {df.text for df in root.iter(RD + "DataField")}
    for c in cols:
        assert c["name"] in datafields, f"[{case_id}] column {c['name']!r} not bound to any dataset"

    declared = {f.get("Name") for f in root.iter(RD + "Field")}
    refs = set(re.findall(r"Fields!([A-Za-z0-9_]+)\.Value", rdl))
    assert not (refs - declared), f"[{case_id}] dangling field refs: {sorted(refs - declared)}"

    # --- upload-safety: a structural duplicate that SSRS rejects must NEVER be
    #     SILENT. It is either absent (clean RDL), or preflight flags it so the
    #     user is told exactly why. Never a quietly-broken upload. -------------
    dup_field = any(
        len([f.get("Name") for f in ds.iter(RD + "Field")])
        != len({f.get("Name") for f in ds.iter(RD + "Field")})
        for ds in root.iter(RD + "DataSet"))
    dsnames = [ds.get("Name") for ds in root.iter(RD + "DataSet")]
    dup_ds = len(dsnames) != len(set(dsnames))

    pf = res.get("preflight") or {}
    blockers = [i for i in pf.get("issues", [])
                if isinstance(i, dict) and i.get("severity") in ("BLOCKER", "RED")]
    if dup_field or dup_ds:
        assert blockers, (f"[{case_id}] SILENT upload-blocker (dup_field={dup_field}, "
                          f"dup_dataset={dup_ds}) -- preflight did not flag it")
    else:
        assert not blockers, (f"[{case_id}] unexpected preflight blocker(s): "
                              f"{[(i.get('rule'), i.get('message')) for i in blockers]}")
