"""SSRS Oracle extension compatibility regression tests.

Guards against the class of bug that breaks a report at runtime even
when the RDL is well-formed and uploads cleanly. Specifically: SSRS's
Oracle data extension cannot bind a NULL DateTime parameter into
NVL(:P, <date>) without an explicit TO_DATE(:P, 'YYYY-MM-DD') wrapper.
Without the wrapper the SQL fails with "Query execution failed for
dataset Q_1" at run time -- a failure the generator-side tests cannot
catch because the RDL itself is structurally valid.
"""
from __future__ import annotations
import re
import html
import pytest


def _command_texts(rdl: str):
    return [html.unescape(c)
            for c in re.findall(r"<CommandText>(.*?)</CommandText>", rdl, re.DOTALL)]


def _datetime_param_names(rdl: str):
    blocks = re.findall(
        r'<ReportParameter Name="([^"]+)">(.*?)</ReportParameter>',
        rdl, re.DOTALL,
    )
    return [name for name, body in blocks
            if "<DataType>DateTime</DataType>" in body]


def test_datetime_binds_are_to_date_wrapped(translated_report):
    """Every :DATE_PARAM bind in CommandText must be wrapped in TO_DATE(...).

    Driven purely by the param's declared SSRS DataType -- if the report
    has no DateTime params the test is vacuously true. NOTHING is keyed
    off a specific report or bind name; this works for any conversion.
    """
    from converter.generators.rdl import generate_rdl
    rdl = generate_rdl(translated_report, target_db="oracle")

    date_params = _datetime_param_names(rdl)
    if not date_params:
        pytest.skip("no DateTime params in this report")

    joined = "\n".join(_command_texts(rdl))
    unwrapped = []
    for name in date_params:
        for m in re.finditer(r":" + re.escape(name) + r"\b", joined):
            prefix = joined[max(0, m.start() - 10):m.start()].upper().rstrip()
            if not prefix.endswith("TO_DATE("):
                ctx = joined[max(0, m.start() - 30):m.end() + 5]
                unwrapped.append((name, ctx))
    assert not unwrapped, (
        "DateTime bind(s) referenced without TO_DATE wrapper -- SSRS Oracle "
        "extension will fail at runtime when the param is NULL.\n"
        + "\n".join(f"  {n}: ...{c}..." for n, c in unwrapped[:5])
    )


def test_to_date_wrap_is_idempotent(translated_report):
    """Regenerating an RDL must never produce TO_DATE(TO_DATE(...))."""
    from converter.generators.rdl import generate_rdl
    rdl = generate_rdl(translated_report, target_db="oracle")
    joined = "\n".join(_command_texts(rdl))
    flat = re.sub(r"\s+", "", joined).upper()
    assert "TO_DATE(TO_DATE(" not in flat, \
        "Double-wrapped TO_DATE detected -- _make_ssrs_oracle_compatible " \
        "is not idempotent."


def test_real_report_meth_details_has_no_bare_date_binds():
    """End-to-end check on the actual METH_DETAILS XML the user is deploying.

    This is the regression-anchor test for the specific bug the user hit:
    'Query execution failed for dataset Q_1' caused by un-wrapped date
    binds in the Oracle SQL. If THIS fails, the report will break in SSRS.
    """
    import os
    from converter import convert
    candidates = [
        "/sessions/peaceful-optimistic-fermi/mnt/uploads/METH_DETAILS.xml",
        os.path.join(os.path.dirname(__file__), "..", "backend", "samples",
                     "METH_DETAILS.xml"),
    ]
    path = next((p for p in candidates if os.path.exists(p)), None)
    if not path:
        pytest.skip("METH_DETAILS.xml not available in this environment")
    xml = open(path, "rb").read()
    out = convert(xml)
    date_params = _datetime_param_names(out["rdl_xml"])
    assert date_params, "expected METH_DETAILS to declare DateTime params"
    joined = "\n".join(_command_texts(out["rdl_xml"]))
    bare = []
    for name in date_params:
        for m in re.finditer(r":" + re.escape(name) + r"\b", joined):
            prefix = joined[max(0, m.start() - 10):m.start()].upper().rstrip()
            if not prefix.endswith("TO_DATE("):
                bare.append(name)
    assert not bare, f"METH_DETAILS has bare date binds: {bare}"
