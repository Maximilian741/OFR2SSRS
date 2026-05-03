"""End-to-end tests for converter.convert(...)."""
from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest


def test_convert_returns_dict(mvwf_xml_bytes):
    from converter import convert
    out = convert(mvwf_xml_bytes)
    assert isinstance(out, dict)


def test_convert_has_expected_keys(mvwf_xml_bytes):
    from converter import convert
    out = convert(mvwf_xml_bytes)
    expected = {"report", "rdl_xml", "oracle_xml", "mockup_html"}
    missing = expected - out.keys()
    assert not missing, f"missing keys in convert() output: {missing}"


def test_convert_report_dict_shape(mvwf_xml_bytes):
    from converter import convert
    out = convert(mvwf_xml_bytes)
    rep = out["report"]
    assert isinstance(rep, dict)
    for key in ("name", "parameters", "queries", "formulas", "triggers"):
        assert key in rep, f"missing report key {key}"
    assert len(rep["parameters"]) == 16
    assert len(rep["queries"]) == 3


def test_convert_rdl_is_valid_xml(mvwf_xml_bytes):
    from converter import convert
    out = convert(mvwf_xml_bytes)
    rdl = out["rdl_xml"]
    assert isinstance(rdl, str) and rdl.strip()
    # ET.fromstring should not raise.
    root = ET.fromstring(rdl)
    assert root is not None


def test_convert_oracle_xml_round_tripped(mvwf_xml_bytes):
    from converter import convert
    out = convert(mvwf_xml_bytes)
    oracle = out["oracle_xml"]
    assert isinstance(oracle, str) and oracle.strip()
    assert "<report" in oracle.lower()


def test_convert_mockup_html_present(mvwf_xml_bytes):
    from converter import convert
    out = convert(mvwf_xml_bytes)
    html = out["mockup_html"]
    assert isinstance(html, str)
    # Mockup should be non-trivial.
    assert len(html) > 50


def test_convert_size_bounds(mvwf_xml_bytes):
    """Sanity checks: pipeline output sizes are within reasonable bounds."""
    from converter import convert
    out = convert(mvwf_xml_bytes)
    # RDL should be at least a few KB and not absurdly large.
    rdl_len = len(out["rdl_xml"])
    assert 1000 < rdl_len < 5_000_000, f"unexpected RDL size {rdl_len}"
    # Mockup HTML is bounded too.
    html_len = len(out["mockup_html"])
    assert 50 < html_len < 5_000_000, f"unexpected mockup size {html_len}"


def test_convert_validation_issues_present_if_supported(mvwf_xml_bytes):
    """validation_issues key is part of the deployment-readiness work."""
    from converter import convert
    out = convert(mvwf_xml_bytes)
    if "validation_issues" in out:
        assert isinstance(out["validation_issues"], list)


def test_convert_deterministic(mvwf_xml_bytes):
    """Calling convert twice on the same input should produce identical reports."""
    from converter import convert
    a = convert(mvwf_xml_bytes)
    b = convert(mvwf_xml_bytes)
    assert a["report"]["name"] == b["report"]["name"]
    assert len(a["report"]["queries"]) == len(b["report"]["queries"])
