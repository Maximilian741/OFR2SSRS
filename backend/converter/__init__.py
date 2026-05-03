"""
Top-level conversion pipeline.

Each module is implemented in its own file so the agents can build them
independently. This file just glues them together and returns a single dict
that the frontend can consume.
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
from .validators.rdl_check import validate_rdl
from .deployment import build_checklist
from .audit import build_audit_trail
from .ai_assist import build_prompts
from .bursting import detect_bursting, build_burst_query, build_powershell_dds_script


def convert(xml_bytes: bytes) -> Dict[str, Any]:
    """End-to-end conversion. Returns a dict ready to ship to the frontend."""
    parsed: ParsedReport = parse_oracle_xml(xml_bytes)
    translate_report(parsed)
    rdl_xml = generate_rdl(parsed)
    mockup_html = render_mockup(parsed)

    # Validation: T-SQL static + RDL structural
    validation_issues = validate_report(parsed)
    rdl_issues = []
    try:
        rdl_issues = validate_rdl(rdl_xml)
    except Exception as e:  # noqa: BLE001
        rdl_issues = [{"severity": "warning", "rule": "rdl.check_failed",
                       "message": f"RDL validator raised {type(e).__name__}: {e}",
                       "element": None}]

    # Deployment checklist
    deployment_checklist = build_checklist(parsed, rdl_xml, validation_issues + rdl_issues)

    # Audit trail (every translation decision)
    audit_trail = []
    try:
        audit_trail = build_audit_trail(parsed)
    except Exception as e:  # noqa: BLE001
        audit_trail = [{"step": 0, "stage": "audit", "scope": "audit",
                        "rule": "audit.failed",
                        "before": "", "after": "",
                        "rationale": f"audit raised {type(e).__name__}: {e}"}]

    # AI-assist prompts for tricky bits
    ai_prompts = []
    try:
        ai_prompts = build_prompts(parsed)
    except Exception as e:  # noqa: BLE001
        ai_prompts = []

    # Bursting / DDS detection
    bursting_info = {"is_bursting": False}
    try:
        bursting_info = detect_bursting(parsed)
        if bursting_info.get("is_bursting"):
            bursting_info["burst_query"] = build_burst_query(parsed, bursting_info)
            bursting_info["powershell_script"] = build_powershell_dds_script(
                parsed, bursting_info, f"{parsed.name or 'report'}.rdl"
            )
    except Exception as e:  # noqa: BLE001
        bursting_info = {"is_bursting": False, "error": f"{type(e).__name__}: {e}"}

    return {
        "report": parsed.to_dict(),
        "rdl_xml": rdl_xml,
        "oracle_xml": parsed.raw_xml,
        "mockup_html": mockup_html,
        "validation_issues": validation_issues,
        "rdl_issues": rdl_issues,
        "deployment_checklist": deployment_checklist,
        "audit_trail": audit_trail,
        "ai_prompts": ai_prompts,
        "bursting": bursting_info,
    }


__all__ = ["convert", "run_query"]
