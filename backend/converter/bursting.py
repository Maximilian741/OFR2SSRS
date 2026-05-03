"""
Bursting / Data-Driven Subscription support for Oracle -> SSRS conversion.

Oracle Reports has a "distribution" mechanism: a single report run can emit
N output files (typically one PDF per group key) by reading a destination
parameter such as P_AS_PATH and a per-row filename built by a CF_File-style
formula. SSRS Standard edition has no native data-driven subscription, so we
generate a PowerShell script that loops a "burst query" and renders the RDL
once per row using the ReportingServicesTools module.

Public API (consumed by converter/__init__.py via the integration agent):

    detect_bursting(report) -> dict
        {
          "is_bursting": bool,
          "evidence": [str, ...],
          "burst_key_field": str | None,
          "filename_pattern": str | None,
        }

    build_burst_query(report, info) -> str
        T-SQL stub returning one row per delivery target.

    build_powershell_dds_script(report, info, rdl_path) -> str
        PowerShell driver script that emulates DDS on SSRS Standard.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Heuristic markers
# ---------------------------------------------------------------------------

_BURST_PARAM_NAMES = {
    "P_AS_PATH",
    "P_DISTRIBUTE",
    "P_DISTR_ABBR",
    "P_DESNAME",
    "P_DESTYPE",
    "P_DESFORMAT",
}

_BURST_FORMULA_NAME_HINTS = ("CF_FILE", "CF_FILENAME", "CF_PATH", "CF_OUTFILE")

_BURST_BODY_HINTS = ("P_AS_PATH", "P_DISTRIBUTE", "DEQ_IMAGE", "DESNAME")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _norm(s):
    return (s or "").upper()


def _all_param_names(report):
    return [_norm(getattr(p, "name", "")) for p in getattr(report, "parameters", [])]


def _all_query_columns(report):
    out = []
    for q in getattr(report, "queries", []):
        for it in getattr(q, "items", []):
            out.append((getattr(q, "name", ""), getattr(it, "name", "")))
    return out


def _bind_refs(plsql):
    if not plsql:
        return []
    return re.findall(r":([A-Za-z_][A-Za-z0-9_]*)", plsql)


# ---------------------------------------------------------------------------
# detect_bursting
# ---------------------------------------------------------------------------

def detect_bursting(report):
    """
    Decide whether ``report`` was using Oracle Reports distribution.

    Returns a dict with keys is_bursting, evidence, burst_key_field,
    filename_pattern.
    """
    evidence = []
    is_bursting = False

    # ---- 1. Parameter sniff -------------------------------------------------
    param_names = _all_param_names(report)
    for pname in param_names:
        if pname in _BURST_PARAM_NAMES:
            evidence.append("parameter " + pname + " present")
            if pname in ("P_AS_PATH", "P_DISTRIBUTE"):
                is_bursting = True

    # ---- 2. Formula sniff ---------------------------------------------------
    burst_formula = None
    for f in getattr(report, "formulas", []):
        fname = _norm(getattr(f, "name", ""))
        body = getattr(f, "plsql_body", "") or ""
        body_u = body.upper()

        name_hit = any(h in fname for h in _BURST_FORMULA_NAME_HINTS)
        body_hit = any(h in body_u for h in _BURST_BODY_HINTS)

        if body_hit:
            hit = [h for h in _BURST_BODY_HINTS if h in body_u][0]
            evidence.append("formula " + str(f.name) + " references " + hit)
            is_bursting = True
            if burst_formula is None:
                burst_formula = f
        elif name_hit:
            evidence.append("formula " + str(f.name) + " matches naming convention")
            if burst_formula is None:
                burst_formula = f

    # ---- 3. Triggers / hyperlink-style references ---------------------------
    for t in getattr(report, "triggers", []):
        body_u = (getattr(t, "body", "") or "").upper()
        if "P_AS_PATH" in body_u or "DEQ_IMAGE" in body_u:
            evidence.append("trigger " + str(t.name) + " references distribution path")
            is_bursting = True

    # ---- 4. Resolve burst key + filename pattern ----------------------------
    burst_key_field = None
    filename_pattern = None

    if burst_formula is not None:
        body = burst_formula.plsql_body or ""
        binds = _bind_refs(body)
        param_set = set(p.upper() for p in param_names)
        path_like = {"P_AS_PATH", "P_DESNAME", "P_DESTYPE", "P_DESFORMAT",
                     "P_DISTR_ABBR", "P_DISTRIBUTE"}
        for b in binds:
            if b.upper() in param_set:
                continue
            burst_key_field = b
            break

        m = re.search(r"RETURN\s*\((.+?)\)\s*;", body, re.DOTALL | re.IGNORECASE)
        ret_expr = m.group(1) if m else body
        pieces = []
        for tok in re.split(r"\|\|", ret_expr):
            tok = tok.strip()
            if not tok:
                continue
            mb = re.search(r":([A-Za-z_][A-Za-z0-9_]*)", tok)
            if mb:
                bname = mb.group(1)
                if bname.upper() in path_like:
                    continue
                pieces.append("<" + bname + ">")
                continue
            ml = re.search(r"'([^']*)'", tok)
            if ml:
                pieces.append(ml.group(1))
                continue
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", tok):
                continue
            pieces.append(tok[:24])
        joined = "".join(pieces)
        joined = re.sub(r"_{2,}", "_", joined).strip("_")
        if joined:
            filename_pattern = joined + ".pdf"

    if is_bursting and not burst_key_field:
        for _q, c in _all_query_columns(report):
            cu = c.upper()
            if cu in ("PERM_NUM", "PERMIT_NUM", "PERMITNUM", "PERMIT_ID",
                      "PERMIT", "DOC_ID", "INVOICE_NUM"):
                burst_key_field = c
                break

    if is_bursting and not filename_pattern:
        key = burst_key_field or "Id"
        filename_pattern = "<" + key + ">.pdf"

    return {
        "is_bursting": bool(is_bursting),
        "evidence": evidence,
        "burst_key_field": burst_key_field,
        "filename_pattern": filename_pattern,
    }


# ---------------------------------------------------------------------------
# build_burst_query
# ---------------------------------------------------------------------------

def _pick_recipient_columns(report):
    name_like = ("PERMITTEE", "RECIPIENT", "CONTACT", "NAME", "PERM_NAME")
    email_like = ("EMAIL", "MAIL", "ADDRESS", "ADDR")
    name_hit = None
    email_hit = None
    for _q, c in _all_query_columns(report):
        cu = c.upper()
        if not name_hit and any(k in cu for k in name_like):
            name_hit = c
        if not email_hit and any(k in cu for k in email_like):
            email_hit = c
    return [name_hit or "Recipient_Name", email_hit or "Email_Or_Path"]


def build_burst_query(report, info):
    """
    Returns a T-SQL stub that yields ONE row per delivery target.
    """
    key = info.get("burst_key_field") or "Perm_Num"
    name_col, email_col = _pick_recipient_columns(report)
    pattern = info.get("filename_pattern") or ("<" + key + ">.pdf")
    rname = getattr(report, "name", "REPORT") or "REPORT"

    first_q = ""
    qs = getattr(report, "queries", [])
    if qs:
        first_q = getattr(qs[0], "name", "") or ""

    evidence_str = ", ".join(info.get("evidence", []) or []) or "(none)"
    table = first_q or "Permits"

    sql = (
        "-- Data-Driven Subscription / bursting source for " + rname + "\n"
        "-- One row per output file. Edit the FROM/JOIN to suit your environment.\n"
        "--\n"
        "-- Detected:\n"
        "--   burst_key_field  = " + str(key) + "\n"
        "--   filename_pattern = " + str(pattern) + "\n"
        "--   evidence         = " + evidence_str + "\n"
        "--\n"
        "SELECT\n"
        "    p." + key + "                                   AS Burst_Key,\n"
        "    p." + name_col + "                              AS Recipient_Name,\n"
        "    COALESCE(r." + email_col + ", '\\\\fileshare\\reports\\out')\n"
        "                                              AS Email_Or_Path,\n"
        "    'PDF'                                     AS Render_Format,\n"
        "    -- Per-row filename built to match the original CF_File_F pattern.\n"
        "    REPLACE(REPLACE(REPLACE(\n"
        "        '" + pattern + "',\n"
        "        '<" + key + ">',         CAST(p." + key + " AS NVARCHAR(64))),\n"
        "        '<P_Distr_Abbr>',  ISNULL(@P_Distr_Abbr, '')),\n"
        "        '<Renewal_Year>',  ISNULL(CAST(@Renewal_Year AS NVARCHAR(8)), ''))\n"
        "                                              AS Output_File\n"
        "FROM dbo." + table + " AS p\n"
        "LEFT JOIN dbo.Distribution_Recipients AS r\n"
        "       ON r." + key + " = p." + key + "\n"
        "WHERE p." + key + " IS NOT NULL\n"
        "ORDER BY p." + key + ";\n"
    )
    return sql


# ---------------------------------------------------------------------------
# build_powershell_dds_script
# ---------------------------------------------------------------------------

_PS_TEMPLATE = r"""<#
    Data-Driven Subscription emulator for: __RNAME__
    Generated by Oracle2SSRS (bursting module).

    SSRS Standard edition does not support native DDS, so this script
    drives the equivalent behavior:

      1. Query the "burst" table once to get the list of output rows.
      2. For each row, render __RDLBASE__ via the SSRS URL access
         endpoint with that row's parameter values.
      3. Save the rendered PDF to the row's Output_File path or e-mail it.

    Detected pattern : __PATTERN__
    Burst key        : __KEY__
    Evidence         : __EVIDENCE__

    Prerequisites:
        Install-Module -Name ReportingServicesTools -Scope CurrentUser
        Install-Module -Name SqlServer            -Scope CurrentUser
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)] [string] $ReportServerUri,
    [Parameter(Mandatory=$true)] [string] $ReportPath,
    [Parameter(Mandatory=$true)] [string] $SqlInstance,
    [Parameter(Mandatory=$true)] [string] $Database,
    [Parameter(Mandatory=$false)][string] $OutputRoot = '\\fileshare\reports\out',
    [Parameter(Mandatory=$false)][string] $BurstQueryPath = './burst_query.sql'
)

# ---- Original Oracle parameters (edit as needed) ---------------------------
$ReportParameters = @{
__PSPARAMS__
}

Import-Module ReportingServicesTools -ErrorAction Stop
Import-Module SqlServer              -ErrorAction Stop

Write-Host "Bursting __RNAME__ via $ReportServerUri$ReportPath" -ForegroundColor Cyan

# ---- 1. Pull the row-per-delivery list -------------------------------------
$burstSql = Get-Content -Raw -Path $BurstQueryPath
$rows = Invoke-Sqlcmd -ServerInstance $SqlInstance -Database $Database -Query $burstSql

if (-not $rows) {
    Write-Warning "Burst query returned 0 rows; nothing to render."
    return
}

Write-Host ("Rendering {0} bursts..." -f $rows.Count)

# ---- 2. Render once per row -------------------------------------------------
$session = New-RsRestSession -ReportPortalUri $ReportServerUri -ErrorAction Stop

foreach ($row in $rows) {
    $burstKey   = $row.Burst_Key
    if ([string]::IsNullOrWhiteSpace($row.Output_File)) {
        $outputFile = Join-Path $OutputRoot ("{0}.pdf" -f $burstKey)
    } else {
        $outputFile = Join-Path $OutputRoot $row.Output_File
    }
    $outputDir  = Split-Path -Parent $outputFile
    if (-not (Test-Path $outputDir)) {
        New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
    }

    # Per-row override of the burst-key parameter (the DDS substitute).
    $perRowParams = @{}
    foreach ($k in $ReportParameters.Keys) { $perRowParams[$k] = $ReportParameters[$k] }
    $perRowParams['__KEY__'] = $burstKey

    $renderFormat = if ($row.Render_Format) { $row.Render_Format } else { 'PDF' }

    try {
        Export-RsReport `
            -ReportServerUri  $ReportServerUri `
            -ReportPath       $ReportPath `
            -RenderFormat     $renderFormat `
            -Destination      $outputFile `
            -Parameters       $perRowParams `
            -Credential       (Get-Credential -Message "SSRS account") `
            -ErrorAction      Stop
        Write-Host ("  [OK]  {0} -> {1}" -f $burstKey, $outputFile)
    }
    catch {
        Write-Warning ("  [FAIL] {0}: {1}" -f $burstKey, $_.Exception.Message)
    }

    # ---- 3. (Optional) e-mail the rendered file -----------------------------
    if ($row.Email_Or_Path -match '^[^@\s]+@[^@\s]+$') {
        try {
            Send-MailMessage `
                -To          $row.Email_Or_Path `
                -From        'reports@yourdomain.local' `
                -Subject     ("__RNAME__ for {0}" -f $burstKey) `
                -Body        'Attached is your distribution copy.' `
                -Attachments $outputFile `
                -SmtpServer  'smtp.yourdomain.local' `
                -ErrorAction Stop
            Write-Host ("        emailed -> {0}" -f $row.Email_Or_Path)
        } catch {
            Write-Warning ("        email failed: {0}" -f $_.Exception.Message)
        }
    }
}

Write-Host "Bursting complete." -ForegroundColor Green
"""


def build_powershell_dds_script(report, info, rdl_path):
    """
    Returns a PowerShell driver script emulating a Data-Driven Subscription
    on SSRS Standard edition.
    """
    rname = getattr(report, "name", "REPORT") or "REPORT"
    key = info.get("burst_key_field") or "Perm_Num"
    pattern = info.get("filename_pattern") or ("<" + key + ">.pdf")
    evidence_str = ", ".join(info.get("evidence", []) or []) or "(none)"

    ps_param_lines = []
    for p in getattr(report, "parameters", []):
        if not getattr(p, "display", True):
            continue
        label = getattr(p, "label", "") or p.name
        ps_param_lines.append("    $" + p.name + " = $null   # Oracle param: " + label)
    if not ps_param_lines:
        ps_param_lines.append("    # (no user-facing parameters)")
    ps_params = "\n".join(ps_param_lines)

    rdl_basename = rdl_path.replace("\\", "/").rsplit("/", 1)[-1]

    out = _PS_TEMPLATE
    out = out.replace("__RNAME__", rname)
    out = out.replace("__RDLBASE__", rdl_basename)
    out = out.replace("__PATTERN__", pattern)
    out = out.replace("__KEY__", key)
    out = out.replace("__EVIDENCE__", evidence_str)
    out = out.replace("__PSPARAMS__", ps_params)
    return out


__all__ = [
    "detect_bursting",
    "build_burst_query",
    "build_powershell_dds_script",
]
