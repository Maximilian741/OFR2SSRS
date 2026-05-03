"""
Top-level conversion pipeline.

Each module is implemented in its own file so the agents can build them
independently. This file just glues them together.
"""
from __future__ import annotations

from typing import Dict, Any

from .models import ParsedReport
from .parsers.oracle_xml import parse_oracle_xml
from .translators.plsql_to_tsql import translate_report
from .generators.rdl import generate_rdl
from .preview.html_mockup import render_mockup
from .preview.live_data import run_query
from .validators.tsql_check import validate_report
from .deployment import build_checklist


def convert(xml_bytes: bytes) -> Dict[str, Any]:
    """End-to-end conversion. Returns a dict ready to ship to the frontend."""
    parsed: ParsedReport = parse_oracle_xml(xml_bytes)
    translate_report(parsed)
    rdl_xml = generate_rdl(parsed)
    mockup_html = render_mockup(parsed)

    # SSRS deployment readiness: T-SQL static validation + post-download
    # checklist. Both run pure-Python with no network calls.
    validation_issues = validate_report(parsed)
    deployment_checklist = build_checklist(parsed, rdl_xml, validation_issues)

    return {
        "report": parsed.to_dict(),
        "rdl_xml": rdl_xml,
        "oracle_xml": parsed.raw_xml,
        "mockup_html": mockup_html,
        "validation_issues": validation_issues,
        "deployment_checklist": deployment_checklist,
    }


__all__ = ["convert", "run_query"]
