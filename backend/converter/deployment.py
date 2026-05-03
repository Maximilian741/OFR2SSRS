"""
Deployment checklist generator.

After convert() runs, we produce an ordered checklist the user can follow to
take the generated .rdl from "downloaded file" to "running on a real SSRS
server". Every step has a status:

    auto     -> the converter already did this for you
    todo     -> you have to do it; we tell you how
    manual   -> a human has to drive a UI we can't automate
    caution  -> there's a known footgun here; read carefully

Public API:
    build_checklist(report, rdl_xml: str, validation_issues: list[dict])
        -> list[dict]
"""
from __future__ import annotations

import re
from typing import Any, Dict, List


_PKG_RE = re.compile(r"\b(Pkg_[A-Za-z0-9_]+|Utl_URL)\s*\.\s*([A-Za-z_][A-Za-z0-9_]*)", re.I)


def _collect_package_calls(report) -> List[str]:
    seen: Dict[str, str] = {}
    for q in getattr(report, "queries", []) or []:
        for src in (getattr(q, "sql", "") or "", getattr(q, "tsql", "") or ""):
            for m in _PKG_RE.finditer(src):
                key = f"{m.group(1)}.{m.group(2)}"
                seen[key] = f"dbo.fn_{m.group(2)}"
    for f in getattr(report, "formulas", []) or []:
        for src in (getattr(f, "plsql_body", "") or "", getattr(f, "tsql_body", "") or ""):
            for m in _PKG_RE.finditer(src):
                key = f"{m.group(1)}.{m.group(2)}"
                seen[key] = f"dbo.fn_{m.group(2)}"
    return sorted(seen.keys())


def _collect_lex_refs(report) -> List[str]:
    refs = set()
    pat = re.compile(r"&[A-Za-z_][A-Za-z0-9_]*", re.I)
    for q in getattr(report, "queries", []) or []:
        for src in (getattr(q, "sql", "") or "", getattr(q, "tsql", "") or ""):
            for m in pat.finditer(src):
                refs.add(m.group(0))
    return sorted(refs)


def _collect_dataset_names(report) -> List[str]:
    return [getattr(q, "name", "") for q in (getattr(report, "queries", []) or []) if getattr(q, "name", None)]


def _has_dataset(report, name: str) -> bool:
    n = name.upper()
    return any((getattr(q, "name", "") or "").upper() == n for q in (getattr(report, "queries", []) or []))


def _format_param_list(report) -> str:
    rows = []
    for p in getattr(report, "parameters", []) or []:
        ssrs = getattr(p, "ssrs_datatype", "String")
        init = getattr(p, "initial_value", None)
        line = f"- **@{p.name}** ({ssrs})"
        if init not in (None, ""):
            line += f" — default `{init}`"
        if getattr(p, "label", ""):
            line += f"  *(label: {p.label})*"
        rows.append(line)
    return "\n".join(rows) if rows else "_No report parameters declared._"


def _summarize_issues(issues: List[Dict[str, Any]]) -> str:
    if not issues:
        return "**No T-SQL issues found.** The converter believes the generated SQL is portable. Still run a smoke query before deploying."
    by_sev: Dict[str, int] = {}
    for it in issues:
        by_sev[it["severity"]] = by_sev.get(it["severity"], 0) + 1
    parts = []
    for sev in ("error", "warning", "info"):
        if by_sev.get(sev):
            parts.append(f"**{by_sev[sev]} {sev}{'s' if by_sev[sev] != 1 else ''}**")
    head = "Validation found " + ", ".join(parts) + ". Top items:"
    bullets = []
    for it in issues[:8]:
        loc = f"L{it['line']}" if it.get("line") else "—"
        bullets.append(f"- _{it['severity']}_ `{it.get('rule','')}` ({it.get('scope','')} @ {loc}): {it['message']}")
    return head + "\n" + "\n".join(bullets)


def build_checklist(report, rdl_xml: str, validation_issues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Build the ordered post-download deployment checklist."""

    pkg_calls   = _collect_package_calls(report)
    lex_refs    = _collect_lex_refs(report)
    datasets    = _collect_dataset_names(report)
    has_q_org   = _has_dataset(report, "Q_ORG")
    has_q_sig   = _has_dataset(report, "Q_SIGNATURE") or _has_dataset(report, "Q_SIG")
    error_count = sum(1 for i in validation_issues if i.get("severity") == "error")
    warn_count  = sum(1 for i in validation_issues if i.get("severity") == "warning")

    steps: List[Dict[str, Any]] = []

    # 1. Open the .rdl
    steps.append({
        "step": 1,
        "status": "manual",
        "title": "Open the .rdl in SSRS Report Builder",
        "body_md": (
            "Download the generated `.rdl` (left sidebar) and open it with **SQL Server "
            "Report Builder** (free download from Microsoft) or **Visual Studio with the "
            "SSRS extension**.\n\n"
            "* If Report Builder complains the schema version is too new/old, re-save it "
            "from Report Builder once — that re-stamps it with your local namespace.\n"
            "* If you see *'The element 'Report' has invalid child element ...'* it's "
            "almost always a CodeModule reference; comment it out and re-open."
        ),
    })

    # 2. Configure DataSource
    steps.append({
        "step": 2,
        "status": "manual",
        "title": "Configure the DataSource against your DEQ database",
        "body_md": (
            "The RDL ships with a placeholder shared `DataSource` named **DEQ**. Point it "
            "at the migrated DEQ database:\n\n"
            "1. In Report Builder open **Data Sources** in the Report Data pane.\n"
            "2. Right-click **DEQ** -> **Data Source Properties**.\n"
            "3. Change connection string to `Data Source=YOUR_SQL_SERVER;Initial Catalog=DEQ;Integrated Security=SSPI;`\n"
            "4. Test connection. **Save** the report.\n\n"
            "If you want to use a shared data source, set **Use a shared connection** and "
            "pick `/Data Sources/DEQ` from the report server."
        ),
    })

    # 3. Run T-SQL validation report
    steps.append({
        "step": 3,
        "status": "auto" if not error_count else "caution",
        "title": f"Review T-SQL validation results ({error_count} errors, {warn_count} warnings)",
        "body_md": (
            "The converter ran a static T-SQL validator against every generated dataset. "
            "Open the **Validation** tab to see line-by-line issues. The checks include:\n\n"
            "* Oracle constructs that survived translation (DECODE, NVL, ROWNUM, (+), MINUS, ...)\n"
            "* Unbalanced parens / unterminated strings\n"
            "* Identifiers > 128 chars (SQL Server hard limit)\n"
            "* `SELECT *` (warning — Tablix bindings break if columns rename)\n"
            "* `@P_*` parameters referenced but not declared\n\n"
            + _summarize_issues(validation_issues)
        ),
    })

    # 4. Port dbo.fn_* UDFs
    udf_lines = []
    for full in pkg_calls:
        ora_name = full.split(".")[-1]
        udf_lines.append(f"* `{full}` -> `dbo.fn_{ora_name}`")
    if not udf_lines:
        udf_body = (
            "_No Oracle package functions detected in this report — nothing to port._"
        )
        udf_status = "auto"
    else:
        udf_body = (
            "The converter generated **stub** scalar UDFs in the live-data sandbox so the "
            "preview runs. Before going to prod, port each one against your real schema. "
            "Open `backend/converter/translators/udf_stubs.py` output to see the stubs and "
            "the embedded original PL/SQL hints; deploy them under `dbo.fn_*` in your "
            "DEQ database.\n\n"
            "**Functions referenced by this report:**\n\n"
            + "\n".join(udf_lines)
            + "\n\n"
            "Each generated stub includes a `/* PORTING NOTE */` block with the original "
            "Oracle name and parameter list, plus a `SESSION_CONTEXT(N'Oracle2SSRS_dev')` "
            "guard so calling the stub in production raises a `RAISERROR` instead of "
            "silently returning fake data."
        )
        udf_status = "todo"
    steps.append({
        "step": 4,
        "status": udf_status,
        "title": f"Port dbo.fn_* UDFs against the live DEQ schema ({len(pkg_calls)} found)",
        "body_md": udf_body,
    })

    # 5. Resolve lexical refs
    if lex_refs:
        lex_status = "caution"
        lex_body = (
            "The translator detected unresolved Oracle lexical references in this report:\n\n"
            + "\n".join(f"* `{r}`" for r in lex_refs)
            + "\n\n"
            "SSRS does **not** support `&P_*` substitution into a static SQL string. You "
            "have two patterns:\n\n"
            "**A. Tablix Filter (preferred when the criterion is a simple equality).** "
            "Leave the dataset SQL with no WHERE clause for that column, and add a Filter "
            "to the Tablix referencing the report parameter, e.g. `=Fields!Permit.Value` "
            "vs `=Parameters!P_Permit.Value`.\n\n"
            "**B. sp_executesql with parameter definitions.** Wrap the dataset SQL in:\n\n"
            "```sql\nDECLARE @sql NVARCHAR(MAX) = N'SELECT ... WHERE 1=1' \n"
            "  + CASE WHEN @P_Permit IS NOT NULL THEN N' AND p.Permit_Num = @P_Permit' ELSE N'' END;\n"
            "EXEC sp_executesql @sql, N'@P_Permit INT', @P_Permit = @P_Permit;\n```\n\n"
            "Pattern B is the only choice when the lex ref is itself a column list or "
            "ORDER BY clause."
        )
    else:
        lex_status = "auto"
        lex_body = (
            "_No `&P_*` lexical references in this report — nothing to do._"
        )
    steps.append({
        "step": 5,
        "status": lex_status,
        "title": f"Resolve Oracle lexical refs ({len(lex_refs)} found)",
        "body_md": lex_body,
    })

    # 6. Verify @P_* parameters
    steps.append({
        "step": 6,
        "status": "auto",
        "title": f"Verify @P_* parameter bindings ({len(getattr(report, 'parameters', []) or [])} declared)",
        "body_md": (
            "The converter declared the following `<ReportParameter>` blocks in the RDL "
            "and bound each one to its dataset(s) with the corresponding SSRS data type:\n\n"
            + _format_param_list(report)
            + "\n\nIn Report Builder, open **Parameters** and confirm that each parameter "
            "shows the right data type, prompt label, and default value. If you have "
            "available-values lists (dropdowns) defined in the original Oracle LOV, port "
            "those into a small lookup dataset and bind it to the parameter."
        ),
    })

    # 7. Sub-report and signature
    sr_parts = []
    if has_q_org:
        sr_parts.append("* **Q_ORG** — Add a Subreport item where the layout shows the agency block. The subreport should run a separate `.rdl` parameterised by `OrgId` and pull from `Q_ORG`.")
    if has_q_sig:
        sr_parts.append("* **Q_SIGNATURE** — Use an Image control with `Source = Database`, `MIMEType = image/png`, and bind `Value` to `=First(Fields!Sig_Image.Value, \"Q_SIGNATURE\")`. SSRS will not import Oracle-side BFILE refs; the column must be a `varbinary(max)` in T-SQL.")
    if not sr_parts:
        steps.append({
            "step": 7,
            "status": "auto",
            "title": "Add Q_ORG sub-report / Q_SIGNATURE image (skipped)",
            "body_md": "_This report contains neither a `Q_ORG` nor a `Q_SIGNATURE` dataset, so this step is skipped._",
        })
    else:
        steps.append({
            "step": 7,
            "status": "manual",
            "title": "Wire up sub-report (Q_ORG) and image control (Q_SIGNATURE)",
            "body_md": (
                "These artifacts can't be reproduced 1:1 from the Oracle source — they "
                "need to be added by hand in Report Builder:\n\n" + "\n".join(sr_parts)
            ),
        })

    # 8. Test render and deploy
    steps.append({
        "step": 8,
        "status": "manual",
        "title": "Test render in Report Builder, then deploy to SSRS catalog",
        "body_md": (
            "1. Click **Run** in Report Builder. Resolve any **#Error** cells (usually a "
            "missing UDF or a parameter type mismatch).\n"
            "2. Once it renders, **File -> Save As** and pick your SSRS site. Default path "
            "is something like `https://reports.example.com/ReportServer`.\n"
            "3. After upload, browse to the report on SSRS, click **Manage**, set "
            "permissions (read-only for end users, owner for your service account), and "
            "schedule any caching/snapshot policy.\n\n"
            "If the upload says *'The data source has been disabled'* you forgot step 2 — "
            "re-save the data source on the server with valid credentials."
        ),
    })

    # 9. Optional: data-driven subscription
    steps.append({
        "step": 9,
        "status": "manual",
        "title": "(Optional) Configure Data-Driven Subscription for bursting",
        "body_md": (
            "If the original Oracle Report ran a bursting loop (one PDF per permit, "
            "emailed to the permittee), recreate that as a SSRS **Data-Driven Subscription**:\n\n"
            "1. SSRS portal -> the report -> **Subscribe** -> **New Data-Driven Subscription**.\n"
            "2. Use a SQL query that returns one row per output (e.g. `SELECT Permit_Num, "
            "Email FROM v_Permit_Burst_List WHERE Active = 1`).\n"
            "3. Map the columns to the subscription's `To`, `CC`, `Subject`, parameter "
            "values, and rendering format (PDF / Excel / TIFF).\n"
            "4. Schedule it (daily, weekly, etc.). Errors land in the SSRS execution log; "
            "tail with `SELECT * FROM ReportServer.dbo.ExecutionLog3 ORDER BY TimeStart DESC`."
        ),
    })

    return steps


__all__ = ["build_checklist"]
