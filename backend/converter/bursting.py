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
# Sanitizers -- the report is UNTRUSTED. Report/column/parameter names and
# filename patterns flow into the GENERATED PowerShell + T-SQL burst artifacts.
# A hostile name must not be able to close a PS hashtable or break out of a SQL
# string/comment and inject code that later runs on the customer's SSRS host.
# ---------------------------------------------------------------------------

def _ps_ident(name: str) -> str:
    """Safe PowerShell variable identifier (letters / digits / underscore)."""
    s = re.sub(r"[^A-Za-z0-9_]", "", str(name or ""))
    if not s:
        return "Param"
    return ("P_" + s) if s[0].isdigit() else s


def _ps_text(text: str) -> str:
    """Text safe inside a PowerShell double-quoted string OR a # comment:
    newlines removed, and backtick / double-quote / $ escaped."""
    s = re.sub(r"[\r\n]+", " ", str(text or ""))
    return s.replace("`", "``").replace('"', '`"').replace("$", "`$")


def _sql_ident(name: str) -> str:
    """Safe T-SQL identifier fragment (schema.table / column): letters,
    digits, underscore and a single dot. Strips everything else."""
    s = re.sub(r"[^A-Za-z0-9_.]", "", str(name or ""))
    return s or "col"


def _sql_str(text: str) -> str:
    """Text safe inside a single-quoted T-SQL literal: quotes doubled,
    newlines removed."""
    s = re.sub(r"[\r\n]+", " ", str(text or ""))
    return s.replace("'", "''")


def _sql_comment(text: str) -> str:
    """Text safe on a -- comment line: no newlines."""
    return re.sub(r"[\r\n]+", " ", str(text or "")).strip()


def _safe_name(text: str) -> str:
    """Report/display name safe to splice into ANY generated artifact context
    (PowerShell string/comment, JSON config, SQL comment): newlines removed and
    every quote / backtick / $ / backslash / semicolon / brace stripped."""
    s = re.sub(r"[\r\n\t]+", " ", str(text or ""))
    s = re.sub(r"""[`"'$\\;{}]""", "", s)
    return s.strip() or "report"


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

_BURST_BODY_HINTS = ("P_AS_PATH", "P_DISTRIBUTE", "DESNAME")


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
        if "P_AS_PATH" in body_u:
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
            bu = b.upper()
            # Skip distribution-path params AND Oracle summary/formula/
            # placeholder binds (CS_/CF_/CP_) -- an aggregate is never a
            # per-recipient burst key.
            if bu in param_set or re.match(r"(?i)^(cs|cf|cp)_", b):
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

    # Derive a burst key without naming any specific report-domain columns.
    # Order of preference:
    #   1. Any bind reference from the bursting formula that is NOT one of
    #      the distribution-path params (already attempted above via the
    #      _bind_refs / param_set loop).
    #   2. The FIRST column of the report's main query — i.e. the first
    #      defined data item on whatever query has the most items. This is
    #      a structural fallback that works for any Oracle Report.
    #   3. None — the upstream caller can treat a missing burst key as
    #      "no per-row split detected" and proceed accordingly.
    if is_bursting and not burst_key_field:
        queries = getattr(report, "queries", []) or []
        if queries:
            main_q = max(queries, key=lambda q: len(getattr(q, "items", []) or []))
            cols = [getattr(it, "name", "") for it in (getattr(main_q, "items", []) or [])
                    if getattr(it, "name", "")]
            # A burst key is a per-recipient IDENTIFIER -- never an Oracle
            # summary/formula/placeholder column (CS_/CF_/CP_), which is an
            # aggregate, not a row key. Prefer an id/number/key column, then a
            # name/recipient column, else the first remaining real column.
            real = [c for c in cols if not re.match(r"(?i)^(cs|cf|cp)_", c)]

            def _first_with(cands, keys):
                for c in cands:
                    if any(k in c.upper() for k in keys):
                        return c
                return None

            burst_key_field = (
                _first_with(real, ("_ID", "_NUM", "_KEY", "_NO", "ID", "NUM", "KEY"))
                or _first_with(real, ("NAME", "RECIP", "CONTACT"))
                or (real[0] if real else (cols[0] if cols else None))
            )

    if is_bursting and not filename_pattern and burst_key_field:
        filename_pattern = "<" + burst_key_field + ">.pdf"

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
    # Derive recipient column from generic name conventions only — never
    # report-domain-specific terms. "RECIPIENT" stays as the canonical
    # neutral term; the rest are common cross-system header conventions
    # (CONTACT, NAME), and email is matched via EMAIL / TO_EMAIL / SEND_TO
    # / ADDRESS / ADDR.
    name_like = ("RECIPIENT", "CONTACT", "NAME")
    email_like = ("EMAIL", "TO_EMAIL", "SEND_TO", "ADDRESS", "ADDR")
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
    # All of these flow into generated T-SQL -- sanitize (the report is untrusted).
    key = _sql_ident(info.get("burst_key_field") or "Perm_Num")
    name_col, email_col = _pick_recipient_columns(report)
    name_col = _sql_ident(name_col)
    email_col = _sql_ident(email_col)
    pattern = info.get("filename_pattern") or ("<" + key + ">.pdf")
    rname = _sql_comment(getattr(report, "name", "REPORT") or "REPORT")

    first_q = ""
    qs = getattr(report, "queries", [])
    if qs:
        first_q = getattr(qs[0], "name", "") or ""

    evidence_str = _sql_comment(", ".join(info.get("evidence", []) or []) or "(none)")
    table = _sql_ident(first_q or "Permits")

    sql = (
        "-- Data-Driven Subscription / bursting source for " + rname + "\n"
        "-- One row per output file. Edit the FROM/JOIN to suit your environment.\n"
        "--\n"
        "-- Detected:\n"
        "--   burst_key_field  = " + key + "\n"
        "--   filename_pattern = " + _sql_comment(pattern) + "\n"
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
        "        '" + _sql_str(pattern) + "',\n"
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
        # $<name> must be a clean identifier or a hostile param name could close
        # the $ReportParameters hashtable and inject statements; the label sits
        # in a # comment.
        ps_param_lines.append("    $" + _ps_ident(p.name) + " = $null   # Oracle param: " + _ps_text(label))
    if not ps_param_lines:
        ps_param_lines.append("    # (no user-facing parameters)")
    ps_params = "\n".join(ps_param_lines)

    rdl_basename = rdl_path.replace("\\", "/").rsplit("/", 1)[-1]

    out = _PS_TEMPLATE
    out = out.replace("__RNAME__", _ps_text(rname))
    out = out.replace("__RDLBASE__", _ps_text(rdl_basename))
    out = out.replace("__PATTERN__", _ps_text(pattern))
    out = out.replace("__KEY__", _ps_text(key))
    out = out.replace("__EVIDENCE__", _ps_text(evidence_str))
    out = out.replace("__PSPARAMS__", ps_params)
    return out


__all__ = [
    "detect_bursting",
    "build_burst_query",
    "build_powershell_dds_script",
]


# ---------------------------------------------------------------------------
# Email-via-service-account distribution
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Production-grade email bursting helpers — ecosystem-specific (SSRS)
# These replace the earlier stub versions (later defs win in Python).
# ---------------------------------------------------------------------------

_EMAIL_COL_PATTERNS = (
    "RECIPIENT_EMAIL", "PRI_EMAIL", "PRIMARY_EMAIL",
    "EMAIL_ADDR", "EMAIL_ADDRESS", "EMAIL",
    "CONTACT_EMAIL", "MAIL_ADDR",
)


def _detect_main_table(report):
    """Inspect the parsed report's queries and try to pick the primary table
    the report binds against. Strategy: look at every dataset's tsql/sql,
    parse `FROM <ident>` and `JOIN <ident>`, and return the most-frequently
    referenced one. Falls back to None.
    """
    counts = {}
    pat = re.compile(
        r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_\.]*)",
        re.IGNORECASE,
    )
    for q in getattr(report, "queries", []):
        body = (getattr(q, "tsql", "") or getattr(q, "sql", "") or "")
        for m in pat.finditer(body):
            tok = m.group(1)
            # Strip schema-prefix for the comparison (dbo.X -> X)
            short = tok.split(".")[-1]
            if not short or short.upper() in (
                "DUAL", "SYS", "INFORMATION_SCHEMA", "SELECT",
            ):
                continue
            counts[short] = counts.get(short, 0) + 1
    if not counts:
        return None
    # Most-referenced wins; tie-broken by first-seen order (stable sort).
    return sorted(counts.items(), key=lambda kv: -kv[1])[0][0]


def _detect_email_column(report):
    """Walk every dataset's columns and return the first that looks like an
    email address (matches a pattern in _EMAIL_COL_PATTERNS, longest-first
    so 'EMAIL_ADDR' beats 'EMAIL'). Returns the original casing or None.
    """
    patterns = sorted(_EMAIL_COL_PATTERNS, key=len, reverse=True)
    for _q, c in _all_query_columns(report):
        cu = (c or "").upper()
        for p in patterns:
            if p in cu:
                return c
    return None


def build_email_burst_query(report, info):
    """The burst query the PowerShell driver loops over.

    One row per email recipient. The driver reads each row, binds the report
    parameter, renders to PDF, sends via SMTP, and logs the outcome.

    The query is intentionally written to FAIL CLOSED (no spam): rows with
    no email are skipped, not sent to a fallback.

    Improvement: we now AUTO-DETECT the main table and (if possible) the
    recipient-email column from the parsed report's own datasets, and
    substitute them directly into the rendered SQL. Anything we can't infer
    is left as a clearly-labeled placeholder so the user knows where to
    edit. A header comment shows exactly what was substituted.
    """
    # Untrusted report-derived names flow into this generated T-SQL -- sanitize.
    burst_key = _sql_ident((info or {}).get("burst_key_field") or "Perm_Num")
    rname = _sql_comment((report.name if hasattr(report, "name") else "") or "report")

    detected_main = _detect_main_table(report)
    detected_email_col = _detect_email_column(report)

    main_table = _sql_ident(detected_main) if detected_main else "<MainTable>"
    email_table = "<EmailTable>"
    if detected_email_col:
        # We have an email column somewhere in the schema; for the rendered
        # SQL we assume it lives on the main table unless the user overrides.
        # That keeps the JOIN sane while still being copy-pastable.
        email_expr = "p." + _sql_ident(detected_email_col)
        email_join = ""  # no separate email-lookup table needed
    else:
        email_expr = "o.<RecipientEmail>"
        email_join = (
            "LEFT JOIN dbo." + email_table + " AS o\n"
            "    ON o." + burst_key + " = p." + burst_key + "\n"
        )

    subst_summary = (
        "--   <MainTable>      -> " + (main_table if detected_main else "<MainTable>  (NOT DETECTED — edit me)")
        + "\n--   <RecipientEmail> -> " + (detected_email_col if detected_email_col else "<RecipientEmail>  (NOT DETECTED — edit me)")
        + "\n--   burst_key        -> " + burst_key
    )

    return (
        "-- =============================================================\n"
        "-- " + rname + " — Email Burst Query\n"
        "-- One row per recipient. Driver loops this and emails each row.\n"
        "-- =============================================================\n"
        "-- Required columns (the driver references them by name):\n"
        "--   Burst_Key       value bound to the report's per-recipient parameter\n"
        "--   EmailTo         primary recipient address (REQUIRED — rows without this are skipped)\n"
        "--   EmailCc         optional cc list (semicolon-separated)\n"
        "--   Subject         email subject line\n"
        "--   Recipient_Name  for logging only (helps trace failures)\n"
        "--   Render_Format   PDF | EXCELOPENXML | WORDOPENXML  (default PDF)\n"
        "--\n"
        "-- Auto-substitutions (override any '<...>' that remains):\n"
        + subst_summary + "\n"
        "\n"
        "SELECT\n"
        "    CAST(p." + burst_key + " AS NVARCHAR(64))                         AS Burst_Key,\n"
        "    " + email_expr + "                                                AS EmailTo,\n"
        "    NULL                                                              AS EmailCc,\n"
        "    CONCAT('[" + rname + "] — ', CAST(p." + burst_key + " AS NVARCHAR(64)))  AS Subject,\n"
        "    CAST(p." + burst_key + " AS NVARCHAR(64))                         AS Recipient_Name,\n"
        "    'PDF'                                                             AS Render_Format\n"
        "FROM dbo." + main_table + " AS p\n"
        + email_join +
        "WHERE " + email_expr + " IS NOT NULL          -- fail-closed: never email '[unknown]'\n"
        "ORDER BY p." + burst_key + ";\n"
    )


# Production PowerShell template. Reads its config from a sibling JSON file so
# the same script can drive every report. Has structured logging, retries on
# transient SMTP errors, send-history tracking to prevent duplicate emails on
# rerun, and a hard test-mode redirect that is impossible to forget about.
_EMAIL_PS_TEMPLATE = r"""<#
=================================================================================
__REPORT_NAME__ — Email Burst Driver
=================================================================================

Run AS the service account (Task Scheduler > Run whether user logged on / no).

Config:    burst.config.json (sibling file — see template below)
Log:       %ProgramData%\Oracle2SSRS\__REPORT_NAME__\YYYYMMDD.jsonl
History:   %ProgramData%\Oracle2SSRS\__REPORT_NAME__\sent_keys.txt
            (one Burst_Key per line; rerunning the script skips already-sent
             keys so a Task Scheduler retry is safe.)

The script never crashes the runbook on a single failed email. It logs the
error and moves on. End of run prints a summary and exits non-zero only if
ALL rows failed (so monitoring fires only on systemic problems, not on a
single bad email address).
=================================================================================
#>

#requires -Version 5.1
#requires -Modules ReportingServicesTools, SqlServer

[CmdletBinding()]
param(
    [string]$ConfigPath = (Join-Path $PSScriptRoot 'burst.config.json'),
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

# -------------------------------------------------------------------------
# Load config
# -------------------------------------------------------------------------
if (-not (Test-Path $ConfigPath)) {
    throw "Config not found: $ConfigPath. Copy burst.config.example.json to burst.config.json and fill it in."
}
$cfg = Get-Content -Raw $ConfigPath | ConvertFrom-Json

# Required keys
foreach ($k in 'ReportPath','ReportServer','DbServer','DbName','SmtpServer','SmtpFrom','SmtpCredentialTarget') {
    if (-not $cfg.$k) { throw "burst.config.json missing required key: $k" }
}

# -------------------------------------------------------------------------
# Logging — one JSON line per event (consumable by your SIEM)
# -------------------------------------------------------------------------
$LogRoot   = Join-Path $env:ProgramData ('Oracle2SSRS\' + $cfg.ReportName)
New-Item -ItemType Directory -Force -Path $LogRoot | Out-Null
$LogFile   = Join-Path $LogRoot ((Get-Date -Format 'yyyyMMdd') + '.jsonl')
$HistFile  = Join-Path $LogRoot 'sent_keys.txt'
if (-not (Test-Path $HistFile)) { New-Item -ItemType File -Path $HistFile | Out-Null }

function Write-Event {
    param([string]$Level, [string]$Event, [hashtable]$Extra = @{})
    $rec = @{
        ts    = (Get-Date).ToString('o')
        level = $Level
        event = $Event
        report= $cfg.ReportName
        host  = $env:COMPUTERNAME
        user  = $env:USERNAME
    }
    foreach ($k in $Extra.Keys) { $rec[$k] = $Extra[$k] }
    ($rec | ConvertTo-Json -Compress) | Add-Content -Path $LogFile
    Write-Host ("[{0}] {1} — {2}" -f $Level, $Event, ($Extra.Keys | ForEach-Object { "$_=$($Extra[$_])" } | Out-String))
}

# -------------------------------------------------------------------------
# Get SMTP credential from the service account's Credential Manager.
# (Stored once via: New-StoredCredential -Target $cfg.SmtpCredentialTarget …)
# -------------------------------------------------------------------------
$cred = $null
try {
    Import-Module CredentialManager -ErrorAction Stop
    $stored = Get-StoredCredential -Target $cfg.SmtpCredentialTarget -ErrorAction Stop
    if (-not $stored) { throw "No stored credential under target '$($cfg.SmtpCredentialTarget)'" }
    $cred = $stored
} catch {
    Write-Event 'ERROR' 'smtp_credential_missing' @{ msg = $_.Exception.Message }
    throw
}

# -------------------------------------------------------------------------
# 1. Run the burst query
# -------------------------------------------------------------------------
Write-Event 'INFO' 'run_start' @{ dry_run = $DryRun.IsPresent; config = $ConfigPath }

$BurstSql = @"
__BURST_SQL__
"@
$rows = Invoke-Sqlcmd -ServerInstance $cfg.DbServer -Database $cfg.DbName -Query $BurstSql `
                       -QueryTimeout 600 -ErrorAction Stop
$total = if ($rows -is [array]) { $rows.Count } else { if ($rows) { 1 } else { 0 } }
Write-Event 'INFO' 'burst_rows_loaded' @{ count = $total }

if ($total -eq 0) {
    Write-Event 'WARN' 'no_recipients' @{}
    return
}

# Skip keys we've already emailed (Task Scheduler retry-safety)
$alreadySent = @{}
Get-Content $HistFile | Where-Object { $_ } | ForEach-Object { $alreadySent[$_] = $true }

# -------------------------------------------------------------------------
# 2. Loop and send
# -------------------------------------------------------------------------
$ok = 0; $fail = 0; $skip = 0
foreach ($row in $rows) {
    $key = [string]$row.Burst_Key
    $to  = if ($cfg.TestRedirect) { $cfg.TestRedirect } else { [string]$row.EmailTo }
    $sub = [string]$row.Subject
    $fmt = if ($row.Render_Format) { [string]$row.Render_Format } else { 'PDF' }
    $cc  = [string]$row.EmailCc
    $rname = [string]$row.Recipient_Name

    if (-not $to) { $skip++; Write-Event 'WARN' 'skip_no_email' @{ key=$key }; continue }
    if (-not $cfg.TestRedirect -and $alreadySent.ContainsKey($key)) {
        $skip++; Write-Event 'INFO' 'skip_already_sent' @{ key=$key, to=$to }; continue
    }

    if ($DryRun) {
        Write-Event 'INFO' 'dry_run' @{ key=$key, to=$to, recipient=$rname, subject=$sub }
        $ok++; continue
    }

    # 2a. Render the SSRS report bound to $key
    $tempDir = Join-Path $env:TEMP ('o2s_burst_' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Force -Path $tempDir | Out-Null
    $rendered = $null
    try {
        # Render a tablix-bound parameter — name must match a ReportParameter on the .rdl.
        # cfg.BindParameter is e.g. "P___KEY__" (default) or "P_PERM_NUM" etc.
        $bindParam = if ($cfg.BindParameter) { $cfg.BindParameter } else { 'P___KEY__' }
        $params = @{ $bindParam = $key }

        # Retry transient errors up to 3x with backoff
        $attempt = 0; $renderOk = $false
        while (-not $renderOk -and $attempt -lt 3) {
            $attempt++
            try {
                Export-RsReport -ReportServerUri $cfg.ReportServer `
                                -ReportPath     $cfg.ReportPath `
                                -OutPath        $tempDir `
                                -Format         $fmt `
                                -Parameters     $params `
                                -ErrorAction    Stop | Out-Null
                $renderOk = $true
            } catch {
                if ($attempt -ge 3) { throw }
                Start-Sleep -Seconds (5 * $attempt)
            }
        }

        $rendered = Get-ChildItem $tempDir -File | Select-Object -First 1
        if (-not $rendered) { throw "Render produced no file" }
    } catch {
        $fail++
        Write-Event 'ERROR' 'render_failed' @{ key=$key, msg=$_.Exception.Message }
        Remove-Item $tempDir -Recurse -Force -ErrorAction SilentlyContinue
        continue
    }

    # 2b. Send the email
    try {
        $sendArgs = @{
            SmtpServer  = $cfg.SmtpServer
            Port        = if ($cfg.SmtpPort) { $cfg.SmtpPort } else { 587 }
            UseSsl      = $true
            Credential  = $cred
            From        = $cfg.SmtpFrom
            To          = $to
            Subject     = $sub
            Body        = $cfg.BodyTemplate -replace '__KEY__', $key -replace '__NAME__', $rname
            BodyAsHtml  = [bool]$cfg.BodyIsHtml
            Attachments = $rendered.FullName
            Encoding    = [System.Text.Encoding]::UTF8
        }
        if ($cc) { $sendArgs.Cc = ($cc -split ';' | Where-Object { $_ }) }

        Send-MailMessage @sendArgs -ErrorAction Stop
        $ok++
        Write-Event 'INFO' 'email_sent' @{ key=$key, to=$to, recipient=$rname, attempt=$attempt }
        if (-not $cfg.TestRedirect) { Add-Content $HistFile $key }
    } catch {
        $fail++
        Write-Event 'ERROR' 'smtp_failed' @{ key=$key, to=$to, msg=$_.Exception.Message }
    } finally {
        Remove-Item $tempDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# -------------------------------------------------------------------------
# 3. Summary
# -------------------------------------------------------------------------
Write-Event 'INFO' 'run_complete' @{ total=$total; ok=$ok; failed=$fail; skipped=$skip; dry_run=$DryRun.IsPresent }

if ($fail -gt 0 -and $ok -eq 0) {
    Write-Error "All $fail recipients failed. Check log: $LogFile"
    exit 1
}
exit 0
"""


_EMAIL_CONFIG_TEMPLATE = """{
  "_comment": "burst.config.json — sits beside burst.ps1. Service account reads this at run.",

  "ReportName":            "__REPORT_NAME__",
  "ReportPath":            "/Reports/__REPORT_NAME__",
  "ReportServer":          "https://ssrs.example.com/ReportServer",

  "DbServer":              "sql-prod.example.com",
  "DbName":                "AppDb",

  "SmtpServer":            "smtp.example.com",
  "SmtpPort":              587,
  "SmtpFrom":              "[email protected]",
  "SmtpCredentialTarget":  "O2S_SMTP",

  "BindParameter":         "__BIND_PARAM__",
  "BodyIsHtml":            false,
  "BodyTemplate":          "Dear __NAME__,\\n\\nYour report is attached. Reference: __KEY__.\\n\\n— App Reporting",

  "_test_redirect_doc":    "Set this to YOUR address to send EVERY row to you. Leave blank in prod. Cannot be left set accidentally — sent_keys history is NOT updated when redirect is on.",
  "TestRedirect":          ""
}
"""


def build_email_powershell_script(report, info, rdl_path):
    """Production-grade email burst driver. Reads burst.config.json so the
    same script works for every report; only the SQL is per-report."""
    rname  = _safe_name((report.name if hasattr(report, "name") else "") or "report")
    sql    = build_email_burst_query(report, info).replace("\\", "\\\\")
    out    = _EMAIL_PS_TEMPLATE
    out    = out.replace("__REPORT_NAME__", rname)
    out    = out.replace("__BURST_SQL__", sql)
    out    = out.replace("__KEY__", "__KEY__")  # keep literal in PS script
    return out


def _bind_param_for_key(report, burst_key_field):
    """Best report PARAMETER to filter the report to one burst-key value -- the
    SSRS parameter the driver sets per row. Matches the burst-key column to a
    ReportParameter: an exact de-prefixed name match (burst key 'Perm_Name' ->
    'P_PERM_NAME') wins; else a clear report-specific placeholder 'P_<KEY>' the
    user edits (better than the old opaque 'P___KEY__'). Generic, name-based."""
    if not burst_key_field:
        return "P___KEY__"

    def _n(s):
        return re.sub(r"[^a-z0-9]", "", (s or "").lower())

    key_n = _n(burst_key_field)
    params = [getattr(p, "name", "") for p in getattr(report, "parameters", [])]
    # 1. exact de-prefixed match (P_PERM_NAME == key Perm_Name)
    for p in params:
        if _n(re.sub(r"(?i)^p_?", "", p)) == key_n and key_n:
            return p
    # 2. clear, report-specific fill-in placeholder (NOT the opaque P___KEY__).
    return "P_" + re.sub(r"[^A-Za-z0-9]", "_", burst_key_field).upper()


def _pick_bind_candidates(report, burst_key_field):
    """Report parameters that plausibly filter to one burst key -- a parameter
    whose name shares the burst key's leading token (e.g. key 'Permit' ->
    P_PERM_NUM / P_PERM_NAME / P_PERMITTEE). For the README's guidance list."""
    ktoks = [t for t in re.split(r"[^a-z0-9]+", str(burst_key_field or "").lower()) if t]
    if not ktoks:
        return []
    pref = ktoks[0][:4]
    out = []
    for p in (getattr(report, "parameters", []) or []):
        pn = getattr(p, "name", "")
        ptoks = [t for t in re.split(r"[^a-z0-9]+", pn.lower()) if t and t != "p"]
        if any(t.startswith(pref) or pref.startswith(t) for t in ptoks):
            out.append(pn)
    return out[:6]


def build_email_config_template(report, info):
    """Sample burst.config.json for this specific report."""
    rname = _safe_name((report.name if hasattr(report, "name") else "") or "report")
    bind = _bind_param_for_key(report, (info or {}).get("burst_key_field"))
    return (_EMAIL_CONFIG_TEMPLATE
            .replace("__REPORT_NAME__", rname)
            .replace("__BIND_PARAM__", bind))


def build_service_account_checklist(report, info):
    """Concrete, verifiable steps. No filler."""
    return [
        {"step": 1, "title": "Confirm SSRS edition",
         "body": "Run on the SSRS host: <code>sqlcmd -S . -Q \"SELECT SERVERPROPERTY('Edition')\"</code>. "
                 "Native Data-Driven Subscriptions require <b>Enterprise</b> or <b>Developer</b>. "
                 "On <b>Standard</b>, the PowerShell driver below is the supported workaround. "
                 "There is no other option — don't waste time looking for a license workaround."},
        {"step": 2, "title": "Provision the service account",
         "body": "Request from AD team: a Group Managed Service Account (gMSA) named "
                 "<code>svc_o2s_burst$</code>, with logon-as-batch + logon-as-service rights "
                 "on the SSRS host. gMSA is preferred over a regular svc account because the "
                 "password rotates automatically and is never knowable. The Task Scheduler entry "
                 "uses <code>DOMAIN\\\\svc_o2s_burst$</code> — note the trailing dollar sign."},
        {"step": 3, "title": "Grant the SQL permissions (verbatim)",
         "body": "On the report DB, as a sysadmin, run:<br>"
                 "<pre><code>USE [AppDb];\n"
                 "CREATE USER [DOMAIN\\\\svc_o2s_burst$] FOR LOGIN [DOMAIN\\\\svc_o2s_burst$];\n"
                 "ALTER ROLE db_datareader ADD MEMBER [DOMAIN\\\\svc_o2s_burst$];\n"
                 "GRANT EXECUTE ON SCHEMA::dbo TO [DOMAIN\\\\svc_o2s_burst$];   -- the dbo.fn_F_* UDFs\n"
                 "</code></pre>"
                 "If the burst query runs against a view, also grant SELECT on that view."},
        {"step": 4, "title": "Grant SSRS catalog permissions",
         "body": "In Report Manager → folder containing the deployed report → "
                 "<i>Manage → Security</i>: add <code>DOMAIN\\\\svc_o2s_burst$</code> with role "
                 "<b>Browser</b> (lets it render). Do NOT give Content Manager — render-only is enough."},
        {"step": 5, "title": "Configure SMTP relay",
         "body": "Two paths. Pick one before writing the config:<br>"
                 "&nbsp;&nbsp;<b>Internal Exchange / relay</b>: contact mail admin to allow "
                 "<code>svc_o2s_burst$</code> to relay; SMTP server is your relay host on port 25 "
                 "(no TLS) or 587 (TLS). Set <code>SmtpCredentialTarget</code> to a bogus value -- "
                 "the Send-MailMessage call uses Windows auth via the service account.<br>"
                 "&nbsp;&nbsp;<b>O365 SMTP AUTH</b>: create an account with a license, generate an "
                 "app password (or OAuth2). Store with: <code>Install-Module CredentialManager; "
                 "New-StoredCredential -Target 'O2S_SMTP' -UserName "
                 "'[email protected]' -Password '...'</code>. Run that ONCE while logged "
                 "in AS the service account so the credential is encrypted to its profile."},
        {"step": 6, "title": "Drop the files on the SSRS host",
         "body": "Create <code>C:\\\\Oracle2SSRS\\\\&lt;ReportName&gt;\\\\</code> and put inside:<br>"
                 "&nbsp;&nbsp;burst.ps1 (from the Burst Pack zip)<br>"
                 "&nbsp;&nbsp;burst.config.json (fill in the values via the UI form, then download)<br>"
                 "ACL the folder so only the service account + admins can read/write."},
        {"step": 7, "title": "Test in DRY mode first",
         "body": "From an admin shell:<br>"
                 "<code>powershell.exe -ExecutionPolicy Bypass -File C:\\\\Oracle2SSRS\\\\&lt;ReportName&gt;\\\\burst.ps1 -DryRun</code><br>"
                 "It will pull the burst query, log every recipient it WOULD email, but never call SMTP."},
        {"step": 8, "title": "Test in REDIRECT mode",
         "body": "Edit <code>burst.config.json</code> and set <code>TestRedirect</code> to YOUR "
                 "address. Run without <code>-DryRun</code>. Every recipient's email goes to YOU "
                 "instead of the real address. Verify, then clear <code>TestRedirect</code>."},
        {"step": 9, "title": "Schedule via Task Scheduler",
         "body": "<code>schtasks /Create /TN \"O2S Burst &lt;ReportName&gt;\" "
                 "/TR \"powershell.exe -ExecutionPolicy Bypass -File "
                 "C:\\\\Oracle2SSRS\\\\&lt;ReportName&gt;\\\\burst.ps1\" "
                 "/SC WEEKLY /D MON /ST 06:00 "
                 "/RU \"DOMAIN\\\\svc_o2s_burst$\" /RP * /RL HIGHEST</code>"},
        {"step": 10, "title": "Wire monitoring",
         "body": "The driver writes <code>%ProgramData%\\\\Oracle2SSRS\\\\&lt;ReportName&gt;\\\\YYYYMMDD.jsonl</code> "
                 "-- one JSON event per row. Forward to your SIEM and alert on "
                 "<code>event = \"run_complete\" AND failed &gt; 0</code>."},
    ]


# ---------------------------------------------------------------------------
# Burst Pack zip — plug-and-play download
# ---------------------------------------------------------------------------

def build_burst_readme(report, info, config):
    """Step-by-step README that ships inside the Burst Pack zip."""
    rname = _safe_name(getattr(report, "name", "") or "report")
    bk = (info or {}).get("burst_key_field") or "Id"
    smtp_host = (config or {}).get("SmtpServer") or "smtp.office365.com"
    sender = (config or {}).get("SmtpFrom") or "[email protected]"

    return (
        "# " + rname + " — Burst Pack\n"
        "\n"
        "This zip contains everything you need to email-distribute the **"
        + rname + "** report on a per-recipient basis (one PDF, one email, per row of the burst query).\n"
        "\n"
        "## Contents\n"
        "\n"
        "| File | Purpose |\n"
        "|------|---------|\n"
        "| `" + rname + ".rdl` | The SSRS report definition. Deploy via Report Builder or rs.exe. |\n"
        "| `burst.config.json` | All env-specific knobs. Edit this, not the script. |\n"
        "| `Send-Reports.ps1` | The PowerShell driver. Runs as a service account. |\n"
        "| `README.md` | This file. |\n"
        "| `service-account-setup.md` | Step-by-step service-account provisioning checklist. |\n"
        "\n"
        "## Quick start\n"
        "\n"
        "1. **Install pre-reqs** on the SSRS host (as Administrator, once):\n"
        "   ```powershell\n"
        "   Install-Module ReportingServicesTools, SqlServer, CredentialManager -Scope AllUsers -Force\n"
        "   ```\n"
        "2. **Deploy the RDL** to SSRS (Report Manager > Upload File, or rs.exe). Confirm it renders for at least one burst key when run interactively.\n"
        "3. **Drop this folder on the SSRS host** at `C:\\Oracle2SSRS\\" + rname + "\\`.\n"
        "4. **Store the SMTP credential** while logged in AS the service account:\n"
        "   ```powershell\n"
        "   New-StoredCredential -Target 'O2S_SMTP' -UserName '" + sender + "' -Password '...'\n"
        "   ```\n"
        "5. **Edit `burst.config.json`** -- specifically the `SmtpServer` (currently `"
        + smtp_host + "`), `ReportServer`, `DbServer`, `DbName`, and the burst SQL if needed.\n"
        "   - **`BindParameter`** (currently `" + str((config or {}).get("BindParameter")
            or _bind_param_for_key(report, bk)) + "`) must be the report parameter "
        "that filters the report to ONE `" + bk + "`. Set it to the matching parameter "
        "from this report: " + (", ".join("`" + p + "`" for p in
            _pick_bind_candidates(report, bk)) or "`(none auto-detected)`") + ". "
        "The driver passes each row's `" + bk + "` as this parameter's value.\n"
        "6. **Dry-run first** -- no email actually leaves:\n"
        "   ```powershell\n"
        "   powershell -ExecutionPolicy Bypass -File .\\Send-Reports.ps1 -DryRun\n"
        "   ```\n"
        "   The output tells you exactly how many rows the burst query found and which address each would have gone to.\n"
        "7. **Redirect-test** -- set `TestRedirect` in the config to YOUR email, run for real once, confirm the rendered PDF and body look right, then clear `TestRedirect`.\n"
        "8. **Schedule** under Task Scheduler:\n"
        "   ```\n"
        "   schtasks /Create /TN \"O2S Burst " + rname + "\" /TR \"powershell -ExecutionPolicy Bypass -File C:\\Oracle2SSRS\\" + rname + "\\Send-Reports.ps1\" /SC WEEKLY /D MON /ST 06:00 /RU DOMAIN\\svc_o2s_burst$ /RL HIGHEST\n"
        "   ```\n"
        "\n"
        "## Burst key\n"
        "\n"
        "This report bursts on `" + bk + "`. The driver passes that value to the RDL as a parameter on every render; the RDL filters its main dataset on it.\n"
        "\n"
        "## Troubleshooting\n"
        "\n"
        "- **\"No recipients\" / 0 rows**: the burst SQL returned nothing. Run it interactively against the report DB. The most common cause is that `<MainTable>` / `<RecipientEmail>` placeholders were never filled in.\n"
        "- **\"render_failed\"**: the SSRS catalog path is wrong, or the service account lacks `Browser` on the report folder. See `service-account-setup.md` step 4.\n"
        "- **\"smtp_failed\" / 535 auth**: the stored credential target name in `burst.config.json` doesn't match what's in Credential Manager, OR the SMTP account requires modern auth (OAuth2) and you stored an app password.\n"
        "- **All emails went to my test address even after I cleared TestRedirect**: you edited the config under a different account than the one running the scheduled task. Check `%ProgramData%\\Oracle2SSRS\\" + rname + "\\sent_keys.txt` -- if it's empty after a real run, the script doesn't see the config you edited.\n"
        "\n"
        "## Where logs live\n"
        "\n"
        "`%ProgramData%\\Oracle2SSRS\\" + rname + "\\YYYYMMDD.jsonl` -- one JSON event per row. Tail with `Get-Content -Wait` while a run is in flight. Forward to your SIEM in production.\n"
    )


def _service_account_md(report, info):
    """Flat-markdown rendering of the service-account checklist."""
    items = build_service_account_checklist(report, info) or []
    lines = ["# Service-account setup\n"]
    for s in items:
        # Strip HTML tags from the body for plain-text MD readability.
        body = re.sub(r"<[^>]+>", "", str(s.get("body", "")))
        body = body.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").replace("&nbsp;", " ")
        lines.append("## " + str(s.get("step", "?")) + ". " + str(s.get("title", "")))
        lines.append("")
        lines.append(body)
        lines.append("")
    return "\n".join(lines)


def _apply_config_overrides(template_json, overrides):
    """Merge UI form overrides on top of the JSON template. Returns the
    final JSON string. Unknown keys from the UI are preserved (e.g. AuthMode)
    so the PowerShell driver can read them if it grows new knobs."""
    import json as _json
    try:
        cfg = _json.loads(template_json)
    except Exception:
        cfg = {}
    if not isinstance(overrides, dict):
        overrides = {}
    # Only let through string/number/bool/null overrides. No dict/list nesting
    # from the UI form (avoids injection of weird structures).
    for k, v in overrides.items():
        if v is None or isinstance(v, (str, int, float, bool)):
            cfg[k] = v
    return _json.dumps(cfg, indent=2)


def build_burst_pack_zip(report, rdl_xml, bursting_info, config_overrides=None):
    """Build an in-memory zip with the full plug-and-play Burst Pack.

    Contents:
      <report>.rdl
      burst.config.json   (template + overrides merged)
      Send-Reports.ps1    (the production PS driver)
      README.md           (step-by-step quick start)
      service-account-setup.md
    """
    import io as _io
    import zipfile as _zip

    info = bursting_info or {}
    overrides = config_overrides or {}

    rname = _safe_name(getattr(report, "name", "") or "report")
    rdl_filename = rname + ".rdl"

    # Build each artifact. If the UI passed an EmailBurstSql override use it
    # verbatim (the user may have hand-tweaked the auto-substituted query);
    # otherwise rebuild from the parsed report.
    email_sql = overrides.pop("EmailBurstSql", None) if isinstance(overrides, dict) else None
    if not email_sql:
        email_sql = build_email_burst_query(report, info)

    # Build the PS driver. We rebuild fresh so a per-call SQL override is honored.
    ps_src = _EMAIL_PS_TEMPLATE
    ps_src = ps_src.replace("__REPORT_NAME__", rname)
    ps_src = ps_src.replace("__BURST_SQL__", email_sql.replace("\\", "\\\\"))

    # Build the config: start with template, merge overrides.
    cfg_template = build_email_config_template(report, info)
    cfg_json = _apply_config_overrides(cfg_template, overrides)

    readme = build_burst_readme(report, info, overrides)
    sa_md = _service_account_md(report, info)

    buf = _io.BytesIO()
    with _zip.ZipFile(buf, "w", _zip.ZIP_DEFLATED) as z:
        if rdl_xml:
            z.writestr(rdl_filename, rdl_xml)
        z.writestr("burst.config.json", cfg_json)
        z.writestr("Send-Reports.ps1", ps_src)
        z.writestr("README.md", readme)
        z.writestr("service-account-setup.md", sa_md)
    return buf.getvalue()


__all__ = [
    "detect_bursting",
    "build_burst_query",
    "build_powershell_dds_script",
    "build_email_burst_query",
    "build_email_powershell_script",
    "build_email_config_template",
    "build_service_account_checklist",
    "build_burst_pack_zip",
    "build_burst_readme",
]
