# upload_gate_load_check.ps1 — the ENGINE leg of the upload fatal gate.
#
# Loads every RDL named in -ListFile through Microsoft's real LocalReport
# engine (the signed ReportViewer DLLs under tools/renderlab/lib) and forces
# the definition compile with GetParameters(). LoadReportDefinition alone
# DEFERS validation — a garbage file "loads" fine; GetParameters() is what
# actually compiles the definition.
#
# WHAT THIS DOES NOT CHECK. The inputs are STATICIZED RDLs
# (tools/renderlab/ms_layout staticize): every =expression has been replaced
# by a literal placeholder before the engine sees the file, because a live
# =expression makes the engine build its expression-host assembly inside a
# sandbox AppDomain, which cannot resolve the ReportViewer DLLs from a
# PowerShell host (and crashes CLR outright under this machine's Application
# Control policy). So this leg validates the DOCUMENT STRUCTURE and nothing
# about EXPRESSION SEMANTICS — not dataset scope, not aggregate nesting, not
# field existence: by the time the engine reads the file there are no
# expressions left to be wrong.
#
# It is therefore NOT an analog of the SSRS server's publish-time
# validation, and calling it one here is exactly how a publish-fatal RDL
# passed every local gate and was refused by a customer's Report Server.
# Even a LIVE expression host would not close the gap: ReportViewer is more
# forgiving than the server (it ignores a nested data region's own
# DataSetName, evaluates an undeclared field as Nothing, and evaluates a
# Lookup nested inside another Lookup — all three publish-fatal).
#
# The server's publish rules are checked by the pure rule engine in
# backend/converter/validators/publish_semantics.py (gate:
# tests/test_fatal_gate_publish_semantics.py); VB.NET expression
# compilation is proven by the separate VB-compile leg.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File upload_gate_load_check.ps1 `
#       -ListFile rdl_paths.txt -LibDir ..\tools\renderlab\lib
#
# Output: ONE compact JSON line
#   {"available":true,"results":[{"path":...,"ok":bool,"error":str},...]}
param(
    [Parameter(Mandatory=$true)][string]$ListFile,
    [Parameter(Mandatory=$true)][string]$LibDir
)
$ErrorActionPreference = "Stop"

# Resolve ReportViewer assemblies from LibDir (incl. ProcessingObjectModel,
# which the engine loads BY NAME). Must be a pure-.NET handler: a PowerShell
# scriptblock handler re-enters the PS engine during assembly resolution,
# which itself triggers resolution -> infinite recursion. (Same pattern as
# tools/renderlab/render_rdl.ps1.)
$resolverSrc = @"
using System;
using System.IO;
using System.Reflection;
public static class O2SUploadGateResolver {
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
[O2SUploadGateResolver]::LibDir = (Resolve-Path $LibDir).Path
[O2SUploadGateResolver]::Install()

Add-Type -Path (Join-Path $LibDir "Microsoft.ReportViewer.WinForms.dll")

function Get-InnerChain([System.Exception]$ex) {
    $parts = @()
    $cur = $ex
    while ($null -ne $cur) {
        $parts += ("[" + $cur.GetType().Name + "] " + $cur.Message)
        if ($cur -is [Microsoft.Reporting.WinForms.LocalProcessingException]) {
            try {
                foreach ($m in $cur.Messages) { $parts += ("detail: " + $m) }
            } catch {}
        }
        $cur = $cur.InnerException
    }
    return ($parts -join " | inner: ")
}

$results = @()
foreach ($line in Get-Content $ListFile) {
    $p = $line.Trim()
    if (-not $p) { continue }
    $ok = $true; $err = ""
    try {
        $lr = New-Object Microsoft.Reporting.WinForms.LocalReport
        $fs = [System.IO.File]::OpenRead($p)
        try { $lr.LoadReportDefinition($fs) } finally { $fs.Close() }
        # LocalReport-only guardrails, NOT publish validations: the SSRS
        # server allows hyperlinks/external images by default, LocalReport
        # forbids them unless enabled. Verification harness setting only.
        try { $lr.EnableHyperlinks = $true } catch {}
        try { $lr.EnableExternalImages = $true } catch {}
        # THE definition compile. Without this call nothing is validated.
        $null = $lr.GetParameters()
        $lr.Dispose()
    } catch {
        $ok = $false
        $err = Get-InnerChain $_.Exception
        if ($err.Length -gt 2000) { $err = $err.Substring(0, 2000) }
    }
    $results += [pscustomobject]@{ path = $p; ok = $ok; error = $err }
}
[pscustomobject]@{ available = $true; results = $results } | ConvertTo-Json -Compress -Depth 4
