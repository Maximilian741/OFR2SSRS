# RenderLab driver: render an RDL to PDF with Microsoft's LocalReport engine.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File render_rdl.ps1 `
#       -RdlPath report.rdl -DataJson data.json -OutPdf out.pdf [-LibDir lib]
#
# DataJson shape:
#   { "datasets": [ { "name": "Q_X",
#                     "columns": [ {"name":"Col","type":"System.String"}, ... ],
#                     "rows": [ [v1, v2, ...], ... ] }, ... ] }
#
# Emits "RENDER OK pages=<n>" on success; on failure prints the FULL inner
# exception chain from the processing engine (that's where the real reason
# lives) and exits 1.
param(
    [Parameter(Mandatory=$true)][string]$RdlPath,
    [Parameter(Mandatory=$true)][string]$DataJson,
    [Parameter(Mandatory=$true)][string]$OutPdf,
    [string]$LibDir = (Join-Path $PSScriptRoot "lib")
)

$ErrorActionPreference = "Stop"

# Resolve ReportViewer assemblies from LibDir (incl. ProcessingObjectModel,
# which the engine loads BY NAME at render time). MUST be a pure-.NET
# handler: a PowerShell scriptblock handler re-enters the PS engine during
# assembly resolution, which itself triggers resolution -> infinite
# recursion -> StackOverflowException.
$resolverSrc = @"
using System;
using System.IO;
using System.Reflection;
public static class RvResolver {
    public static string LibDir;
    public static void Install() {
        AppDomain.CurrentDomain.AssemblyResolve += Handler;
    }
    private static Assembly Handler(object sender, ResolveEventArgs e) {
        try {
            var name = new AssemblyName(e.Name).Name;
            if (name.EndsWith(".resources")) return null;
            var p = Path.Combine(LibDir, name + ".dll");
            return File.Exists(p) ? Assembly.LoadFrom(p) : null;
        } catch { return null; }
    }
}
"@
Add-Type -TypeDefinition $resolverSrc -Language CSharp
[RvResolver]::LibDir = (Resolve-Path $LibDir).Path
[RvResolver]::Install()

Add-Type -Path (Join-Path $LibDir "Microsoft.ReportViewer.WinForms.dll")

function Get-InnerChain([System.Exception]$ex) {
    $parts = @()
    $cur = $ex
    while ($null -ne $cur) {
        $parts += ("[" + $cur.GetType().Name + "] " + $cur.Message)
        if ($cur -is [Microsoft.Reporting.WinForms.LocalProcessingException]) {
            # surface aggregated processing messages too
        }
        $cur = $cur.InnerException
    }
    return ($parts -join "`n  inner: ")
}

try {
    Write-Output "STAGE create"
    $lr = New-Object Microsoft.Reporting.WinForms.LocalReport
    if ($env:RENDERLAB_DOMAIN -eq "current") {
        try {
            $lr.ExecuteReportInCurrentAppDomain([System.AppDomain]::CurrentDomain.Evidence)
            Write-Output "STAGE current-appdomain"
        } catch { Write-Output ("STAGE current-appdomain SKIP: " + $_.Exception.Message) }
    }

    Write-Output "STAGE load"
    $fs = [System.IO.File]::OpenRead((Resolve-Path $RdlPath))
    try { $lr.LoadReportDefinition($fs) } finally { $fs.Close() }
    Write-Output "STAGE loaded"

    $spec = Get-Content -LiteralPath $DataJson -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($ds in $spec.datasets) {
        $dt = New-Object System.Data.DataTable($ds.name)
        foreach ($c in $ds.columns) {
            $t = [Type]::GetType([string]$c.type)
            if ($null -eq $t) { $t = [string] }
            [void]$dt.Columns.Add([string]$c.name, $t)
        }
        foreach ($row in $ds.rows) {
            $dr = $dt.NewRow()
            for ($i = 0; $i -lt $ds.columns.Count; $i++) {
                $v = $row[$i]
                if ($null -eq $v) { $dr[$i] = [System.DBNull]::Value }
                else {
                    $colType = $dt.Columns[$i].DataType
                    if ($colType -eq [datetime]) { $dr[$i] = [datetime]::Parse([string]$v, [System.Globalization.CultureInfo]::InvariantCulture) }
                    elseif ($colType -eq [decimal]) { $dr[$i] = [decimal]$v }
                    elseif ($colType -eq [int]) { $dr[$i] = [int]$v }
                    else { $dr[$i] = $v }
                }
            }
            $dt.Rows.Add($dr)
        }
        $rds = New-Object Microsoft.Reporting.WinForms.ReportDataSource($ds.name, $dt)
        $lr.DataSources.Add($rds)
        Write-Output ("STAGE datasource " + $ds.name + " rows=" + $dt.Rows.Count)
    }

    Write-Output "STAGE render-start"
    $mime = $null; $enc = $null; $ext = $null; $ids = $null
    $warnings = $null
    $bytes = $lr.Render("PDF", $null, [ref]$mime, [ref]$enc, [ref]$ext,
                        [ref]$ids, [ref]$warnings)
    [System.IO.File]::WriteAllBytes($OutPdf, $bytes)

    if ($warnings) {
        foreach ($w in $warnings) {
            Write-Output ("WARN " + $w.Severity + " " + $w.Code + " " + $w.ObjectName + ": " + $w.Message)
        }
    }
    # Page count via the PDF trailer (count /Type /Page occurrences is fragile;
    # the python side recounts with pypdf — this is informational only).
    Write-Output ("RENDER OK bytes=" + $bytes.Length)
    exit 0
}
catch {
    $ex = $_.Exception
    Write-Output ("RENDER FAIL: " + (Get-InnerChain $ex))
    exit 1
}
