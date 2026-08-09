<#
  vb_invoke.ps1 — compile a report's <Code> block through the REAL VB.NET
  compiler (System.CodeDom VBCodeProvider) and CALL one of its functions.

  vb_expr_check.ps1 proves a report's custom VB *compiles*. It cannot prove it
  *computes*: a reducer that groups, pads or folds a LookupSet is pure logic,
  and on a host where the ReportViewer expression host is Application-Control
  blocked the engine will never run it. This harness closes that gap — same
  compiler, but the compiled function is invoked with real arguments and its
  return value comes back as text.

  Input  : UTF-8 JSON at -InFile
             { "code": "<VB function definitions>",
               "calls": [ { "func": "Name", "args": [ ... ] }, ... ] }
           An argument that is a JSON array becomes Object(); null becomes
           Nothing; a number becomes Double; anything else becomes String.
  Output : JSON to stdout
             { "available": true, "compiled": true,
               "results": [ { "index": 0, "ok": true, "value": "..." } ] }
           A host without the VB compiler prints {"available": false} and exits
           0 so callers skip cleanly (mirrors vb_expr_check.ps1 / render_rdl).
#>
param(
  [Parameter(Mandatory=$true)][string]$InFile
)
$ErrorActionPreference = "Stop"

function Emit-Unavailable($why) {
  Write-Output (@{ available = $false; reason = $why } | ConvertTo-Json -Compress)
  exit 0
}

if (-not (Test-Path $InFile)) { Emit-Unavailable "input file not found: $InFile" }
try {
  $spec = (Get-Content -Raw -Encoding UTF8 $InFile) | ConvertFrom-Json
} catch { Emit-Unavailable "could not parse input JSON: $($_.Exception.Message)" }

$src = @"
Option Strict Off
Option Explicit Off
Imports System
Imports Microsoft.VisualBasic

Public Class _CodeClass
$([string]$spec.code)
End Class
"@

try {
  $prov = New-Object Microsoft.VisualBasic.VBCodeProvider
} catch { Emit-Unavailable "VBCodeProvider unavailable: $($_.Exception.Message)" }

$p = New-Object System.CodeDom.Compiler.CompilerParameters
$p.GenerateInMemory = $true
$p.GenerateExecutable = $false
[void]$p.ReferencedAssemblies.Add("System.dll")
[void]$p.ReferencedAssemblies.Add("Microsoft.VisualBasic.dll")

try {
  $res = $prov.CompileAssemblyFromSource($p, $src)
} catch { Emit-Unavailable "compile invocation failed: $($_.Exception.Message)" }

$errs = @()
foreach ($e in $res.Errors) {
  if (-not $e.IsWarning) { $errs += "$($e.ErrorNumber) line $($e.Line): $($e.ErrorText)" }
}
if ($errs.Count -gt 0) {
  Write-Output (@{ available = $true; compiled = $false; errors = $errs } |
                ConvertTo-Json -Depth 5 -Compress)
  exit 0
}

$type = $res.CompiledAssembly.GetType("_CodeClass")
$inst = [Activator]::CreateInstance($type)
$out = @()
$i = 0
foreach ($call in @($spec.calls)) {
  $argv = @()
  foreach ($a in @($call.args)) {
    if ($a -eq $null) { $argv += , $null }
    elseif ($a -is [System.Object[]]) { $argv += , ([object[]]@($a)) }
    elseif ($a -is [int] -or $a -is [long] -or $a -is [double] -or $a -is [decimal]) {
      $argv += , ([double]$a)
    } else { $argv += , ([string]$a) }
  }
  $rec = @{ index = $i; ok = $true }
  try {
    $m = $type.GetMethod([string]$call.func)
    if ($m -eq $null) { throw "no such function: $($call.func)" }
    $rec.value = [string]$m.Invoke($inst, $argv)
  } catch {
    $rec.ok = $false
    $rec.error = $_.Exception.ToString()
  }
  $out += $rec
  $i = $i + 1
}
Write-Output (@{ available = $true; compiled = $true; results = $out } |
              ConvertTo-Json -Depth 8)
