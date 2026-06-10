"""
bundle_export.py
----------------
Build a single zip artifact containing every output the converter produced
for the most recent run, so coworkers can grab the whole package at once.

Public surface:
    build_bundle_zip(conversion_data: dict) -> bytes
"""
from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime
from typing import Any, Dict, Iterable, List


# ---------- markdown helpers -------------------------------------------------

def _md_escape(s: Any) -> str:
    if s is None:
        return ""
    return str(s).replace("\r\n", "\n").rstrip()


def _validation_md(tsql_issues: List[dict], rdl_issues: List[dict]) -> str:
    lines: List[str] = []
    lines.append("# Validation Report")
    lines.append("")
    lines.append("This file collects every issue the converter raised against the")
    lines.append("generated T-SQL queries and the produced RDL XML.  Severities are")
    lines.append("`error` (must fix), `warning` (should review), or `info` (nice-to-have).")
    lines.append("")

    # T-SQL block
    lines.append("## T-SQL validation issues")
    lines.append("")
    if not tsql_issues:
        lines.append("_No T-SQL issues were reported._")
    else:
        lines.append(f"Total: **{len(tsql_issues)}**")
        lines.append("")
        lines.append("| # | Severity | Rule | Scope | Line:Col | Message |")
        lines.append("|---|----------|------|-------|----------|---------|")
        for i, iss in enumerate(tsql_issues, 1):
            sev = iss.get("severity") or ""
            rule = iss.get("rule") or ""
            scope = iss.get("scope") or ""
            line = iss.get("line")
            col = iss.get("col")
            loc = f"{line}:{col}" if line is not None else ""
            msg = (iss.get("message") or "").replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {i} | {sev} | `{rule}` | {scope} | {loc} | {msg} |")
        # excerpt detail
        lines.append("")
        lines.append("### Excerpts")
        lines.append("")
        for i, iss in enumerate(tsql_issues, 1):
            ex = iss.get("excerpt")
            if ex:
                lines.append(f"**{i}. `{iss.get('rule','')}` in {iss.get('scope','')}**")
                lines.append("")
                lines.append("```sql")
                lines.append(_md_escape(ex))
                lines.append("```")
                lines.append("")

    lines.append("")
    lines.append("## RDL structural issues")
    lines.append("")
    if not rdl_issues:
        lines.append("_No RDL structural issues were reported._")
    else:
        lines.append(f"Total: **{len(rdl_issues)}**")
        lines.append("")
        lines.append("| # | Severity | Rule | Element | Message |")
        lines.append("|---|----------|------|---------|---------|")
        for i, iss in enumerate(rdl_issues, 1):
            sev = iss.get("severity") or ""
            rule = iss.get("rule") or ""
            elem = iss.get("element") or ""
            msg = (iss.get("message") or "").replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {i} | {sev} | `{rule}` | {elem} | {msg} |")

    lines.append("")
    return "\n".join(lines) + "\n"


def _checklist_md(checklist: List[Any]) -> str:
    lines: List[str] = []
    lines.append("# Deployment Checklist")
    lines.append("")
    lines.append("Numbered steps for getting the generated RDL deployed into SSRS.")
    lines.append("Each item shows whether it is automatic (`auto`) or needs a human")
    lines.append("hand (`manual`).")
    lines.append("")
    if not checklist:
        lines.append("_No checklist items were produced._")
        return "\n".join(lines) + "\n"

    for i, item in enumerate(checklist, 1):
        if isinstance(item, dict):
            title = item.get("title") or item.get("name") or f"Step {i}"
            status = item.get("status") or ""
            body = item.get("body_md") or item.get("body") or ""
            badge = f" _({status})_" if status else ""
            lines.append(f"## {i}. {title}{badge}")
            lines.append("")
            if body:
                lines.append(_md_escape(body))
                lines.append("")
        else:
            lines.append(f"{i}. {_md_escape(item)}")
    lines.append("")
    return "\n".join(lines) + "\n"


def _ai_prompts_md(prompts: List[dict]) -> str:
    lines: List[str] = []
    lines.append("# AI Assist Prompts")
    lines.append("")
    lines.append("The converter could not deterministically translate the items below.")
    lines.append("Each section gives you a paste-ready prompt for an LLM (Claude, GPT,")
    lines.append("Copilot, etc.) plus the deterministic stub the pipeline used as a")
    lines.append("placeholder.  Replace the stub with the LLM's output once you have")
    lines.append("verified it.")
    lines.append("")
    if not prompts:
        lines.append("_No AI-assist prompts were produced – everything translated cleanly._")
        return "\n".join(lines) + "\n"

    for i, p in enumerate(prompts, 1):
        name = p.get("name") or p.get("id") or f"prompt-{i}"
        scope = p.get("scope") or ""
        diff = p.get("difficulty") or ""
        lines.append(f"## {i}. {name}")
        lines.append("")
        meta = []
        if scope:
            meta.append(f"**scope**: `{scope}`")
        if diff:
            meta.append(f"**difficulty**: `{diff}`")
        pid = p.get("id")
        if pid and pid != name:
            meta.append(f"**id**: `{pid}`")
        if meta:
            lines.append(" - " + "  \n - ".join(meta))
            lines.append("")

        tmpl = p.get("prompt_template") or p.get("prompt") or ""
        if tmpl:
            lines.append("### Prompt (copy/paste this to your LLM)")
            lines.append("")
            lines.append("```text")
            lines.append(_md_escape(tmpl))
            lines.append("```")
            lines.append("")

        stub = p.get("deterministic_attempt") or p.get("stub") or ""
        if stub:
            lines.append("### Deterministic stub currently in the RDL")
            lines.append("")
            lines.append("```sql")
            lines.append(_md_escape(stub))
            lines.append("```")
            lines.append("")
    return "\n".join(lines) + "\n"


def _readme_md(data: Dict[str, Any], file_list: List[str]) -> str:
    report = data.get("report") or {}
    name = report.get("name") or "report"
    bursting = data.get("bursting") or {}
    is_bursting = bool(bursting.get("is_bursting"))

    counts = {
        "parameters": len(report.get("parameters") or []),
        "queries": len(report.get("queries") or []),
        "formulas": len(report.get("formulas") or []),
        "triggers": len(report.get("triggers") or []),
        "validation_issues": len(data.get("validation_issues") or []),
        "rdl_issues": len(data.get("rdl_issues") or []),
        "checklist_steps": len(data.get("deployment_checklist") or []),
        "ai_prompts": len(data.get("ai_prompts") or []),
        "audit_steps": len(data.get("audit_trail") or []),
    }

    lines: List[str] = []
    lines.append(f"# {name} – Conversion Bundle")
    lines.append("")
    lines.append(f"_Generated {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC by Oracle2SSRS._")
    lines.append("")
    lines.append("This zip contains every artifact the Oracle Reports -> SSRS converter")
    lines.append("produced for this run.  Hand it to the SSRS developer and they have")
    lines.append("everything they need to finish the migration.")
    lines.append("")

    lines.append("## What's in this zip")
    lines.append("")
    for fn in file_list:
        lines.append(f"- `{fn}`")
    lines.append("")

    lines.append("## Quick stats")
    lines.append("")
    lines.append("| Item | Count |")
    lines.append("|------|------:|")
    for k, v in counts.items():
        lines.append(f"| {k.replace('_',' ')} | {v} |")
    lines.append(f"| bursting report | {'yes' if is_bursting else 'no'} |")
    lines.append("")

    lines.append("## Recommended next steps")
    lines.append("")
    lines.append(f"1. Open `{name}.rdl` in **SQL Server Report Builder** or Visual Studio with the SSRS extension.")
    lines.append("2. Walk through `checklist.md` top-to-bottom – each step is either `auto`")
    lines.append("   (already done by the converter) or `manual` (you / your DBA must do it).")
    lines.append("3. Review `validation.md`.  Resolve every `error`, then triage `warning`s.")
    if counts["ai_prompts"]:
        lines.append("4. Open `ai_prompts.md`.  For each section, copy the **Prompt** block")
        lines.append("   into your favourite LLM, take its T-SQL output, drop it into your")
        lines.append("   database, and replace the matching deterministic stub.")
    if is_bursting:
        nxt = 5 if counts["ai_prompts"] else 4
        lines.append(f"{nxt}. This report is a **bursting** report.  See `bursting/` for the")
        lines.append("   `burst_query.sql` (lists every recipient) and `dds_emulator.ps1` (loops")
        lines.append("   the parameters and renders one PDF per burst key – the SSRS-side")
        lines.append("   replacement for Oracle's Data Distribution Service).")
    last = 6 if (is_bursting and counts["ai_prompts"]) else (5 if (is_bursting or counts["ai_prompts"]) else 4)
    lines.append(f"{last}. `audit_trail.json` is the full step-by-step record of every")
    lines.append("   transformation the converter applied – useful for code review and")
    lines.append("   for explaining the migration to auditors.")
    lines.append("")

    lines.append("## Support")
    lines.append("")
    lines.append("If something looks wrong, re-run the converter against the source")
    lines.append("Oracle XML and compare `audit_trail.json` between runs – the audit")
    lines.append("trail is deterministic, so any diff points straight at the change.")
    lines.append("")
    return "\n".join(lines) + "\n"


# ---------- main entry -------------------------------------------------------

def build_bundle_zip(conversion_data: Dict[str, Any]) -> bytes:
    """Build an in-memory zip containing every converter output.

    Required keys in conversion_data:
        rdl_xml, report, validation_issues, rdl_issues,
        deployment_checklist, audit_trail, ai_prompts, bursting
    """
    data = conversion_data or {}
    report = data.get("report") or {}
    name = report.get("name") or "report"
    rdl_xml = data.get("rdl_xml") or ""

    tsql_issues = data.get("validation_issues") or []
    rdl_issues = data.get("rdl_issues") or []
    checklist = data.get("deployment_checklist") or []
    audit_trail = data.get("audit_trail") or []
    ai_prompts = data.get("ai_prompts") or []
    bursting = data.get("bursting") or {}
    is_bursting = bool(bursting.get("is_bursting"))

    # Pre-build the file list so README can mention it.
    file_list: List[str] = [
        f"{name}.rdl",
        "validation.md",
        "checklist.md",
        "audit_trail.json",
        "ai_prompts.md",
    ]
    if is_bursting:
        file_list.append("bursting/burst_query.sql")
        file_list.append("bursting/dds_emulator.ps1")
    file_list.append("README.md")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{name}.rdl", rdl_xml)
        zf.writestr("validation.md", _validation_md(tsql_issues, rdl_issues))
        zf.writestr("checklist.md", _checklist_md(checklist))
        zf.writestr(
            "audit_trail.json",
            json.dumps(audit_trail, indent=2, default=str, ensure_ascii=False),
        )
        zf.writestr("ai_prompts.md", _ai_prompts_md(ai_prompts))

        if is_bursting:
            burst_sql = bursting.get("burst_query") or "-- no burst query produced\n"
            burst_ps1 = bursting.get("powershell_script") or "# no DDS emulator produced\n"
            header = (
                "-- Burst recipient query\n"
                f"-- Burst key field : {bursting.get('burst_key_field') or ''}\n"
                f"-- Filename pattern: {bursting.get('filename_pattern') or ''}\n\n"
            )
            zf.writestr("bursting/burst_query.sql", header + burst_sql)
            zf.writestr("bursting/dds_emulator.ps1", burst_ps1)

        zf.writestr("README.md", _readme_md(data, file_list))

    return buf.getvalue()
