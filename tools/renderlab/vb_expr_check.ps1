<#
  vb_expr_check.ps1 — compile SSRS report expressions through the REAL VB.NET
  compiler (System.CodeDom VBCodeProvider), the same compilation SSRS performs
  when it publishes a report's expression host. This catches the class of bug
  the geometry/layout renderer is blind to: an expression that is syntactically
  invalid VB.NET (bad IIf arity, trailing comma, unbalanced parens, an undefined
  function the translator invented) renders as "#Error" / "The Value expression
  contains an error" in real SSRS but sails past a static Fields!-reference check.

  Input  : a UTF-8 JSON file = array of expression strings (WITH or without the
           leading '='; we strip it). Path passed as -InFile.
  Output : JSON to stdout = { "available": true, "results": [ {index, ok, errors:[...]} ] }
           On a host without the VB compiler, prints {"available": false} and exits 0
           so callers degrade gracefully (mirrors the render_rdl signed-DLL fallback).

  The expression host below mirrors the SSRS ReportObjectModel surface closely
  enough that every LEGITIMATE expression compiles and only genuine errors flag:
   - Imports Microsoft.VisualBasic / System / System.Math / System.Convert
     (the namespaces SSRS auto-imports; gives IIf, Left, Format, CStr, Math.*, ...)
   - Option Strict Off  (SSRS expressions are late-bound)
   - Fields!X.Value / Parameters!X.Value / Globals!X / ReportItems!X / User!X via
     a Default String-indexed property (VB's '!' dictionary-access operator)
   - the full SSRS aggregate + scope function surface as overloaded stubs
#>
param(
  [Parameter(Mandatory=$true)][string]$InFile
)
$ErrorActionPreference = "Stop"

function Emit-Unavailable($why) {
  Write-Output (@{ available = $false; reason = $why } | ConvertTo-Json -Compress)
  exit 0
}

try { Add-Type -AssemblyName "System" -ErrorAction SilentlyContinue } catch {}

# --- Load expressions -------------------------------------------------------
if (-not (Test-Path $InFile)) { Emit-Unavailable "input file not found: $InFile" }
$exprs = @()
$codeBody = ""
try {
  $raw = Get-Content -Raw -Encoding UTF8 $InFile
  # NB: assign-then-wrap. In Windows PowerShell 5.1, `@($raw | ConvertFrom-Json)`
  # wraps a top-level JSON array as a SINGLE element (the pipeline doesn't unroll
  # it); assigning to a variable first, then @(...), unrolls it correctly.
  $parsed = $raw | ConvertFrom-Json
  # Two input shapes: a bare array of expressions, OR an object
  # { "code": "<report <Code> block>", "exprs": [ ... ] }. The object form lets
  # us compile the report's own custom code so =Code.X(...) references resolve
  # (and a Code.X that the report never declared flags as BC30451 'not declared').
  if ($parsed -ne $null -and ($parsed.PSObject.Properties.Name -contains 'exprs')) {
    if ($parsed.PSObject.Properties.Name -contains 'code') { $codeBody = [string]$parsed.code }
    $exprs = @($parsed.exprs)
  } else {
    $exprs = @($parsed)
  }
} catch { Emit-Unavailable "could not parse input JSON: $($_.Exception.Message)" }
if ($exprs.Count -eq 0) {
  Write-Output (@{ available = $true; results = @() } | ConvertTo-Json -Compress)
  exit 0
}

# --- Build the VB expression-host source ------------------------------------
# Header: imports + the SSRS-like object model + aggregate/scope stubs.
$header = @'
Option Strict Off
Option Explicit Off
Imports System
Imports Microsoft.VisualBasic
Imports System.Math
Imports System.Convert

Public Class _Member
    Public ReadOnly Property Value As Object
        Get
            Return Nothing
        End Get
    End Property
    Public ReadOnly Property Label As Object
        Get
            Return Nothing
        End Get
    End Property
    Public ReadOnly Property IsMissing As Boolean
        Get
            Return False
        End Get
    End Property
    Public ReadOnly Property Count As Integer
        Get
            Return 0
        End Get
    End Property
    Public ReadOnly Property Color As Object
        Get
            Return Nothing
        End Get
    End Property
    Default Public ReadOnly Property Item(ByVal i As Integer) As Object
        Get
            Return Nothing
        End Get
    End Property
End Class

Public Class _Collection
    Default Public ReadOnly Property Item(ByVal name As String) As _Member
        Get
            Return New _Member()
        End Get
    End Property
    Public ReadOnly Property Count As Integer
        Get
            Return 0
        End Get
    End Property
End Class

Public Class _Globals
    Default Public ReadOnly Property Item(ByVal name As String) As Object
        Get
            Return Nothing
        End Get
    End Property
    Public ReadOnly Property PageNumber As Integer
        Get
            Return 0
        End Get
    End Property
    Public ReadOnly Property TotalPages As Integer
        Get
            Return 0
        End Get
    End Property
    Public ReadOnly Property OverallPageNumber As Integer
        Get
            Return 0
        End Get
    End Property
    Public ReadOnly Property OverallTotalPages As Integer
        Get
            Return 0
        End Get
    End Property
    Public ReadOnly Property ExecutionTime As DateTime
        Get
            Return DateTime.Now
        End Get
    End Property
    Public ReadOnly Property ReportName As String
        Get
            Return ""
        End Get
    End Property
    Public ReadOnly Property ReportServerUrl As String
        Get
            Return ""
        End Get
    End Property
    Public ReadOnly Property ReportFolder As String
        Get
            Return ""
        End Get
    End Property
    Public ReadOnly Property Language As String
        Get
            Return ""
        End Get
    End Property
End Class

Public Class _User
    Default Public ReadOnly Property Item(ByVal name As String) As Object
        Get
            Return Nothing
        End Get
    End Property
    Public ReadOnly Property UserID As String
        Get
            Return ""
        End Get
    End Property
    Public ReadOnly Property Language As String
        Get
            Return ""
        End Get
    End Property
End Class

Public Class ExprHost
    Public Fields As New _Collection()
    Public Parameters As New _Collection()
    Public ReportItems As New _Collection()
    Public Globals As New _Globals()
    Public User As New _User()

    Public Function Sum(ByVal o As Object) As Object
        Return Nothing
    End Function
    Public Function Sum(ByVal o As Object, ByVal scope As String) As Object
        Return Nothing
    End Function
    Public Function Avg(ByVal o As Object) As Object
        Return Nothing
    End Function
    Public Function Avg(ByVal o As Object, ByVal scope As String) As Object
        Return Nothing
    End Function
    Public Function Min(ByVal o As Object) As Object
        Return Nothing
    End Function
    Public Function Min(ByVal o As Object, ByVal scope As String) As Object
        Return Nothing
    End Function
    Public Function Max(ByVal o As Object) As Object
        Return Nothing
    End Function
    Public Function Max(ByVal o As Object, ByVal scope As String) As Object
        Return Nothing
    End Function
    Public Function Count(ByVal o As Object) As Object
        Return Nothing
    End Function
    Public Function Count(ByVal o As Object, ByVal scope As String) As Object
        Return Nothing
    End Function
    Public Function CountDistinct(ByVal o As Object) As Object
        Return Nothing
    End Function
    Public Function CountDistinct(ByVal o As Object, ByVal scope As String) As Object
        Return Nothing
    End Function
    Public Function CountRows() As Object
        Return Nothing
    End Function
    Public Function CountRows(ByVal scope As String) As Object
        Return Nothing
    End Function
    Public Function First(ByVal o As Object) As Object
        Return Nothing
    End Function
    Public Function First(ByVal o As Object, ByVal scope As String) As Object
        Return Nothing
    End Function
    Public Function Last(ByVal o As Object) As Object
        Return Nothing
    End Function
    Public Function Last(ByVal o As Object, ByVal scope As String) As Object
        Return Nothing
    End Function
    Public Function StDev(ByVal o As Object) As Object
        Return Nothing
    End Function
    Public Function StDev(ByVal o As Object, ByVal scope As String) As Object
        Return Nothing
    End Function
    Public Function StDevP(ByVal o As Object) As Object
        Return Nothing
    End Function
    Public Function StDevP(ByVal o As Object, ByVal scope As String) As Object
        Return Nothing
    End Function
    Public Function [Var](ByVal o As Object) As Object
        Return Nothing
    End Function
    Public Function [Var](ByVal o As Object, ByVal scope As String) As Object
        Return Nothing
    End Function
    Public Function VarP(ByVal o As Object) As Object
        Return Nothing
    End Function
    Public Function VarP(ByVal o As Object, ByVal scope As String) As Object
        Return Nothing
    End Function
    Public Function Aggregate(ByVal o As Object) As Object
        Return Nothing
    End Function
    Public Function Aggregate(ByVal o As Object, ByVal scope As String) As Object
        Return Nothing
    End Function
    Public Function RunningValue(ByVal o As Object, ByVal f As Object, ByVal scope As String) As Object
        Return Nothing
    End Function
    Public Function RunningValue(ByVal o As Object, ByVal f As Object) As Object
        Return Nothing
    End Function
    Public Function Previous(ByVal o As Object) As Object
        Return Nothing
    End Function
    Public Function Previous(ByVal o As Object, ByVal scope As String) As Object
        Return Nothing
    End Function
    Public Function RowNumber(ByVal scope As Object) As Object
        Return Nothing
    End Function
    Public Function Level() As Object
        Return Nothing
    End Function
    Public Function Level(ByVal scope As String) As Object
        Return Nothing
    End Function
    Public Function InScope(ByVal scope As String) As Object
        Return Nothing
    End Function
    Public Function Lookup(ByVal src As Object, ByVal dst As Object, ByVal res As Object, ByVal ds As String) As Object
        Return Nothing
    End Function
    Public Function MultiLookup(ByVal src As Object, ByVal dst As Object, ByVal res As Object, ByVal ds As String) As Object
        Return Nothing
    End Function
    Public Function LookupSet(ByVal src As Object, ByVal dst As Object, ByVal res As Object, ByVal ds As String) As Object
        Return Nothing
    End Function
    Public Function Join(ByVal o As Object, ByVal sep As String) As Object
        Return Nothing
    End Function

'@

$footer = @'
End Class
'@

# Inject the report's own <Code> block (if any) as a _CodeClass and expose it as
# ExprHost.Code, so =Code.MyFunc(...) compiles when the report declares MyFunc and
# flags as undeclared when it doesn't. Use literal .Replace (not -replace) so any
# '$' in the VB code is not treated as a regex substitution. Done before the line
# count is taken so error-line -> expression mapping stays accurate.
if ($codeBody.Trim().Length -gt 0) {
  $codeClass = "Public Class _CodeClass`n" + $codeBody + "`nEnd Class`n`n"
  $header = $header.Replace("Public Class ExprHost", $codeClass + "Public Class ExprHost")
  $header = $header.Replace("Public User As New _User()", "Public User As New _User()`n    Public Code As New _CodeClass()")
}

# Assemble the source, tracking the line where each expression's Return sits.
$sb = New-Object System.Text.StringBuilder
[void]$sb.Append($header)
$headerLines = ($header -split "`n").Count   # 1-based line of next appended line
$exprStartLine = @{}   # exprIndex -> first line of its function block
$lineCursor = $headerLines
for ($i = 0; $i -lt $exprs.Count; $i++) {
  $e = [string]$exprs[$i]
  if ($e -eq $null) { $e = "" }
  $e = $e.Trim()
  if ($e.StartsWith("=")) { $e = $e.Substring(1) }
  # Blank expression -> emit a trivially-valid body so indices stay aligned.
  if ($e.Trim().Length -eq 0) { $e = "Nothing" }
  $fnOpen  = "    Public Function Expr_$i() As Object"
  $fnRet   = "        Return ($e)"
  $fnClose = "    End Function"
  $exprStartLine[$i] = $lineCursor + 1   # the Function line
  [void]$sb.Append($fnOpen + "`n" + $fnRet + "`n" + $fnClose + "`n")
  $lineCursor += 3
}
[void]$sb.Append($footer)
$source = $sb.ToString()

# Map every source line to the expression whose block contains it.
$lineToExpr = @{}
$ordered = $exprStartLine.GetEnumerator() | Sort-Object Value
for ($k = 0; $k -lt $ordered.Count; $k++) {
  $idx   = $ordered[$k].Key
  $start = $ordered[$k].Value
  $end   = if ($k + 1 -lt $ordered.Count) { $ordered[$k+1].Value - 1 } else { $lineCursor + 5 }
  for ($ln = $start; $ln -le $end; $ln++) { $lineToExpr[$ln] = $idx }
}

# --- Compile ----------------------------------------------------------------
$prov = $null
try {
  $prov = New-Object Microsoft.VisualBasic.VBCodeProvider
} catch { Emit-Unavailable "VBCodeProvider unavailable: $($_.Exception.Message)" }

$params = New-Object System.CodeDom.Compiler.CompilerParameters
$params.GenerateInMemory = $true
$params.GenerateExecutable = $false
[void]$params.ReferencedAssemblies.Add("System.dll")
[void]$params.ReferencedAssemblies.Add("Microsoft.VisualBasic.dll")

$result = $null
try {
  $result = $prov.CompileAssemblyFromSource($params, $source)
} catch { Emit-Unavailable "compile invocation failed: $($_.Exception.Message)" }

# Initialise per-expression result records.
$res = @{}
for ($i = 0; $i -lt $exprs.Count; $i++) { $res[$i] = @{ index = $i; ok = $true; errors = @() } }

foreach ($err in $result.Errors) {
  if ($err.IsWarning) { continue }
  $ln = [int]$err.Line
  $idx = -1
  if ($lineToExpr.ContainsKey($ln)) { $idx = $lineToExpr[$ln] }
  $msg = "$($err.ErrorNumber): $($err.ErrorText)"
  if ($idx -ge 0) {
    $res[$idx].ok = $false
    $res[$idx].errors += $msg
  } else {
    # Header/structural error -> attach to a synthetic -1 bucket.
    if (-not $res.ContainsKey(-1)) { $res[-1] = @{ index = -1; ok = $false; errors = @() } }
    $res[-1].errors += "line $ln :: $msg"
  }
}

$out = @{ available = $true; exprCount = $exprs.Count; resCount = $res.Count; results = @() }
foreach ($k in ($res.Keys | Sort-Object)) { $out.results += $res[$k] }
Write-Output ($out | ConvertTo-Json -Depth 6 -Compress)
