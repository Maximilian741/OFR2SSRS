"""SSRS Oracle extension compatibility regression tests.

Guards against the class of bug that breaks a report at runtime even
when the RDL is well-formed and uploads cleanly. Specifically: SSRS's
Oracle data extension cannot bind a NULL DateTime parameter into
NVL(:P, <date>) without an explicit TO_DATE(:P, 'YYYY-MM-DD') wrapper.
Without the wrapper the SQL fails with a generic "Query execution
failed for dataset Q_X" message at run time -- a failure the
generator-side tests cannot catch because the RDL itself is
structurally valid.

These tests are NAME-AGNOSTIC: they exercise the converter using the
synthetic fixture and any source-of-truth cases discovered at
tests/fixtures/source_of_truth/. NO customer report names or column
names appear anywhere in this file.
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
    has no DateTime params the test is skipped. NOTHING is keyed off a
    specific report or bind name; this works for any conversion.
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
        "Double-wrapped TO_DATE detected -- the SQL rewrite is not idempotent."
