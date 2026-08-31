<#
  vb_expr_eval.ps1 — EVALUATE SSRS report expressions, not merely compile them.

  vb_expr_check.ps1 proves an expression is valid VB.NET. It cannot prove the
  expression COMPUTES: its object model returns Nothing for every Fields!/
  Parameters!/aggregate reference, so an expression that divides by zero, calls
  CDate on something that is not a date, or resolves to a field the report never
  declared compiles perfectly and then renders "#Error" — or a silent blank — in
  real SSRS. On a host where the ReportViewer expression host is Application-
  Control blocked the engine never runs it either, so nothing catches it.

  This harness closes that gap with the SAME compiler: it builds an expression
  host seeded with SYNTHETIC values (supplied by the caller, derived from the
  RDL's own <Field>/<ReportParameter> declarations), invokes every expression,
  and reports the returned value, its CLR type, and any exception.

  Multi-world evaluation: the caller supplies N "worlds" — alternative synthetic
  value assignments for the types the declaration leaves ambiguous (an Oracle
  CHAR column carries dates, numbers and text alike). Each expression is invoked
  once per world so the caller can require only that SOME well-formed input
  makes it compute, never that one arbitrary guess does.

  Input  : UTF-8 JSON at -InFile
             { "decls":  "<extra VB class-level declarations>",  (optional)
               "seed":   "<VB statements run in ExprHost.Seed()>",
               "code":   "<the report's <Code> block>",          (optional)
               "worlds": 4,
               "exprs":  [ "=...", ... ] }
  Output : JSON to stdout
             { "available": true, "compiled": true,
               "compileErrors": { "<index>": ["..."] },
               "results": [ {index, world, ok, isNothing, value, type,
                             error, misses:[...], notes:[...] } ] }
           A host without the VB compiler prints {"available": false} and exits
           0 so callers skip cleanly (mirrors vb_expr_check.ps1 / render_rdl).
#>
param(
  [Parameter(Mandatory=$true)][string]$InFile
)
$ErrorActionPreference = "Stop"

# Windows PowerShell 5.1 writes redirected stdout in the console OEM code page,
# which turns any non-ASCII character an expression returns (a section sign, an
# accented name, a non-Latin script) into a raw byte the caller cannot decode --
# and, once it lands inside a JSON string, into an invalid-control-character
# parse failure that silently costs the whole report. Pin UTF-8.
try {
  [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
} catch {}

# ConvertTo-Json in 5.1 does not escape the C0 controls outside \b\f\n\r\t, so
# scrub them out of every free-text field before they reach the JSON.
function Clean-Text([string]$s) {
  if ($null -eq $s) { return "" }
  return [System.Text.RegularExpressions.Regex]::Replace(
    $s, "[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", " ")
}

function Emit-Unavailable($why) {
  Write-Output (@{ available = $false; reason = $why } | ConvertTo-Json -Compress)
  exit 0
}

if (-not (Test-Path $InFile)) { Emit-Unavailable "input file not found: $InFile" }
$exprs = @(); $seed = ""; $decls = ""; $codeBody = ""; $worlds = 1
try {
  # NB: assign-then-wrap. In Windows PowerShell 5.1 `@($raw | ConvertFrom-Json)`
  # wraps a top-level JSON array as a SINGLE element; assign first, then @().
  $raw = Get-Content -Raw -Encoding UTF8 $InFile
  $spec = $raw | ConvertFrom-Json
  $names = $spec.PSObject.Properties.Name
  if ($names -contains 'seed')   { $seed     = [string]$spec.seed }
  if ($names -contains 'decls')  { $decls    = [string]$spec.decls }
  if ($names -contains 'code')   { $codeBody = [string]$spec.code }
  if ($names -contains 'worlds') { $worlds   = [int]$spec.worlds }
  $exprs = @($spec.exprs)
} catch { Emit-Unavailable "could not parse input JSON: $($_.Exception.Message)" }
if ($worlds -lt 1) { $worlds = 1 }
if ($exprs.Count -eq 0) {
  Write-Output (@{ available = $true; compiled = $true; results = @();
                   compileErrors = @{} } | ConvertTo-Json -Compress)
  exit 0
}

# --- The expression host -----------------------------------------------------
# Mirrors the SSRS ReportObjectModel surface (same shape as vb_expr_check.ps1)
# but every member carries REAL synthetic values, one per world, and the
# aggregate stubs propagate type instead of returning a blanket Nothing.
$hostSrc = @'
Option Strict Off
Option Explicit Off
Imports System
Imports System.Collections.Generic
Imports Microsoft.VisualBasic
Imports System.Math
Imports System.Convert

Public Class _Env
    Public Shared World As Integer = 0
    Public Shared Rows As Integer = 3
    Public Shared Misses As New List(Of String)()
    Public Shared Notes As New List(Of String)()
    Public Shared Sub Miss(ByVal what As String)
        If Not _Env.Misses.Contains(what) Then
            _Env.Misses.Add(what)
        End If
    End Sub
    Public Shared Sub Note(ByVal what As String)
        If Not _Env.Notes.Contains(what) Then
            _Env.Notes.Add(what)
        End If
    End Sub
End Class

Public Class _Member
    Private _vals As Object()
    Private _known As Boolean
    Private _name As String
    Public Sub New(ByVal name As String, ByVal vals As Object(), ByVal known As Boolean)
        _name = name
        _vals = vals
        _known = known
    End Sub
    Public ReadOnly Property Value As Object
        Get
            If Not _known Then
                _Env.Miss(_name)
                Return Nothing
            End If
            If _vals Is Nothing Then
                Return Nothing
            End If
            If _vals.Length = 0 Then
                Return Nothing
            End If
            Dim w As Integer = _Env.World
            If w < 0 Then
                w = 0
            End If
            If w >= _vals.Length Then
                w = _vals.Length - 1
            End If
            Return _vals(w)
        End Get
    End Property
    Public ReadOnly Property Label As Object
        Get
            Return Me.Value
        End Get
    End Property
    Public ReadOnly Property IsMissing As Boolean
        Get
            Return Not _known
        End Get
    End Property
    Public ReadOnly Property Count As Integer
        Get
            Return _Env.Rows
        End Get
    End Property
    Public ReadOnly Property Color As Object
        Get
            Return "#000000"
        End Get
    End Property
    Public ReadOnly Property UniqueName As Object
        Get
            Return _name
        End Get
    End Property
    Default Public ReadOnly Property Item(ByVal i As Integer) As Object
        Get
            Return Me.Value
        End Get
    End Property
End Class

Public Class _Collection
    Private _d As Dictionary(Of String, _Member)
    Private _kind As String
    Public Sub New(ByVal kind As String)
        _kind = kind
        _d = New Dictionary(Of String, _Member)(StringComparer.OrdinalIgnoreCase)
    End Sub
    Public Sub Put(ByVal name As String, ByVal vals As Object())
        _d(name) = New _Member(_kind & "!" & name, vals, True)
    End Sub
    Default Public ReadOnly Property Item(ByVal name As String) As _Member
        Get
            If _d.ContainsKey(name) Then
                Return _d(name)
            End If
            Return New _Member(_kind & "!" & name, New Object() { Nothing }, False)
        End Get
    End Property
    Public ReadOnly Property Count As Integer
        Get
            Return _d.Count
        End Get
    End Property
End Class

Public Class _Globals
    ' VB's '!' operator is DEFAULT-PROPERTY access: Globals!ExecutionTime
    ' compiles to Globals("ExecutionTime"), never to the named property. An
    ' indexer that answers Nothing therefore makes every correct Globals!
    ' expression look like a silent blank, so the indexer answers by name.
    Default Public ReadOnly Property Item(ByVal name As String) As Object
        Get
            Select Case LCase(name)
                Case "pagenumber"
                    Return Me.PageNumber
                Case "totalpages"
                    Return Me.TotalPages
                Case "overallpagenumber"
                    Return Me.OverallPageNumber
                Case "overalltotalpages"
                    Return Me.OverallTotalPages
                Case "executiontime"
                    Return Me.ExecutionTime
                Case "reportname"
                    Return Me.ReportName
                Case "reportserverurl"
                    Return Me.ReportServerUrl
                Case "reportfolder"
                    Return Me.ReportFolder
                Case "language"
                    Return Me.Language
                Case "renderformat"
                    Return "PDF"
            End Select
            _Env.Miss("Globals!" & name)
            Return Nothing
        End Get
    End Property
    Public ReadOnly Property PageNumber As Integer
        Get
            Return 1
        End Get
    End Property
    Public ReadOnly Property TotalPages As Integer
        Get
            Return 2
        End Get
    End Property
    Public ReadOnly Property OverallPageNumber As Integer
        Get
            Return 1
        End Get
    End Property
    Public ReadOnly Property OverallTotalPages As Integer
        Get
            Return 2
        End Get
    End Property
    Public ReadOnly Property ExecutionTime As DateTime
        Get
            Return New DateTime(2024, 3, 15, 9, 30, 0)
        End Get
    End Property
    Public ReadOnly Property ReportName As String
        Get
            Return "R"
        End Get
    End Property
    Public ReadOnly Property ReportServerUrl As String
        Get
            Return "http://localhost/ReportServer"
        End Get
    End Property
    Public ReadOnly Property ReportFolder As String
        Get
            Return "/Reports"
        End Get
    End Property
    Public ReadOnly Property Language As String
        Get
            Return "en-US"
        End Get
    End Property
End Class

Public Class _User
    ' Same '!' default-property rule as _Globals.
    Default Public ReadOnly Property Item(ByVal name As String) As Object
        Get
            Select Case LCase(name)
                Case "userid"
                    Return Me.UserID
                Case "language"
                    Return Me.Language
            End Select
            _Env.Miss("User!" & name)
            Return Nothing
        End Get
    End Property
    Public ReadOnly Property UserID As String
        Get
            Return "TESTDOMAIN\tester"
        End Get
    End Property
    Public ReadOnly Property Language As String
        Get
            Return "en-US"
        End Get
    End Property
End Class

Public Class ExprHost
    Public Fields As _Collection
    Public Parameters As _Collection
    Public ReportItems As _Collection
    Public Globals As New _Globals()
    Public User As New _User()

    Public Sub New()
        Fields = New _Collection("Fields")
        Parameters = New _Collection("Parameters")
        ReportItems = New _Collection("ReportItems")
        Seed()
    End Sub

    ' ---- aggregate + scope surface: type-propagating, never blanket-Nothing --
    Private Function _AsNum(ByVal o As Object, ByVal who As String) As Object
        ' A NULL operand is not a non-numeric operand: SSRS aggregates skip
        ' nulls, so Nothing here is the empty set, not a type error.
        If o Is Nothing Then
            Return Nothing
        End If
        ' Boolean and DateTime are declared types that can never be summed.
        ' They used to return Nothing SILENTLY, so Sum(a date column) folded to
        ' 0 with nothing recorded -- the same silent zero the text case makes,
        ' and invisible to the caller. Every non-numeric operand is reported.
        If TypeOf o Is Boolean Then
            _Env.Note("aggregate_nonnumeric:" & who & ":Boolean")
            Return Nothing
        End If
        If TypeOf o Is DateTime Then
            _Env.Note("aggregate_nonnumeric:" & who & ":DateTime")
            Return Nothing
        End If
        Try
            If IsNumeric(o) Then
                Return CDbl(o)
            End If
        Catch ex As Exception
        End Try
        Dim tn As String = "Unknown"
        Try
            tn = o.GetType().Name
        Catch ex2 As Exception
        End Try
        _Env.Note("aggregate_nonnumeric:" & who & ":" & tn)
        Return Nothing
    End Function

    Private Function _Fold(ByVal o As Object, ByVal who As String) As Object
        ' Sum/Avg-shaped reducers: numeric in -> numeric out. A non-numeric
        ' operand folds to 0 so the SURROUNDING expression still exercises its
        ' own logic; the operand itself is reported as a note, never swallowed.
        Dim n As Object = _AsNum(o, who)
        If n Is Nothing Then
            Return CDbl(0)
        End If
        Return n
    End Function

    Public Function Sum(ByVal o As Object) As Object
        Return CDbl(_Fold(o, "Sum")) * _Env.Rows
    End Function
    Public Function Sum(ByVal o As Object, ByVal scope As String) As Object
        Return Me.Sum(o)
    End Function
    Public Function Avg(ByVal o As Object) As Object
        Return _Fold(o, "Avg")
    End Function
    Public Function Avg(ByVal o As Object, ByVal scope As String) As Object
        Return Me.Avg(o)
    End Function
    Public Function Min(ByVal o As Object) As Object
        Return o
    End Function
    Public Function Min(ByVal o As Object, ByVal scope As String) As Object
        Return o
    End Function
    Public Function Max(ByVal o As Object) As Object
        Return o
    End Function
    Public Function Max(ByVal o As Object, ByVal scope As String) As Object
        Return o
    End Function
    Public Function Count(ByVal o As Object) As Object
        Return _Env.Rows
    End Function
    Public Function Count(ByVal o As Object, ByVal scope As String) As Object
        Return _Env.Rows
    End Function
    Public Function CountDistinct(ByVal o As Object) As Object
        Return _Env.Rows
    End Function
    Public Function CountDistinct(ByVal o As Object, ByVal scope As String) As Object
        Return _Env.Rows
    End Function
    Public Function CountRows() As Object
        Return _Env.Rows
    End Function
    Public Function CountRows(ByVal scope As String) As Object
        Return _Env.Rows
    End Function
    Public Function First(ByVal o As Object) As Object
        Return o
    End Function
    Public Function First(ByVal o As Object, ByVal scope As String) As Object
        Return o
    End Function
    Public Function Last(ByVal o As Object) As Object
        Return o
    End Function
    Public Function Last(ByVal o As Object, ByVal scope As String) As Object
        Return o
    End Function
    Public Function StDev(ByVal o As Object) As Object
        Return _Fold(o, "StDev")
    End Function
    Public Function StDev(ByVal o As Object, ByVal scope As String) As Object
        Return _Fold(o, "StDev")
    End Function
    Public Function StDevP(ByVal o As Object) As Object
        Return _Fold(o, "StDevP")
    End Function
    Public Function StDevP(ByVal o As Object, ByVal scope As String) As Object
        Return _Fold(o, "StDevP")
    End Function
    Public Function [Var](ByVal o As Object) As Object
        Return _Fold(o, "Var")
    End Function
    Public Function [Var](ByVal o As Object, ByVal scope As String) As Object
        Return _Fold(o, "Var")
    End Function
    Public Function VarP(ByVal o As Object) As Object
        Return _Fold(o, "VarP")
    End Function
    Public Function VarP(ByVal o As Object, ByVal scope As String) As Object
        Return _Fold(o, "VarP")
    End Function
    Public Function Aggregate(ByVal o As Object) As Object
        Return o
    End Function
    Public Function Aggregate(ByVal o As Object, ByVal scope As String) As Object
        Return o
    End Function
    Public Function RunningValue(ByVal o As Object, ByVal f As Object, ByVal scope As String) As Object
        Return o
    End Function
    Public Function RunningValue(ByVal o As Object, ByVal f As Object) As Object
        Return o
    End Function
    Public Function Previous(ByVal o As Object) As Object
        Return o
    End Function
    Public Function Previous(ByVal o As Object, ByVal scope As String) As Object
        Return o
    End Function
    Public Function RowNumber(ByVal scope As Object) As Object
        Return 1
    End Function
    Public Function Level() As Object
        Return 0
    End Function
    Public Function Level(ByVal scope As String) As Object
        Return 0
    End Function
    Public Function InScope(ByVal scope As String) As Object
        Return True
    End Function
    Public Function Lookup(ByVal src As Object, ByVal dst As Object, ByVal res As Object, ByVal ds As String) As Object
        ' The engine matches src against dst inside ds and returns res for the
        ' hit. With one synthetic row per dataset the hit IS that row, so res is
        ' the type-correct answer.
        Return res
    End Function
    Public Function MultiLookup(ByVal src As Object, ByVal dst As Object, ByVal res As Object, ByVal ds As String) As Object
        Dim a(_Env.Rows - 1) As Object
        Dim i As Integer
        For i = 0 To _Env.Rows - 1
            a(i) = res
        Next
        Return a
    End Function
    Public Function LookupSet(ByVal src As Object, ByVal dst As Object, ByVal res As Object, ByVal ds As String) As Object
        Return Me.MultiLookup(src, dst, res, ds)
    End Function
    Public Function Join(ByVal o As Object, ByVal sep As String) As Object
        If o Is Nothing Then
            Return ""
        End If
        If TypeOf o Is Object() Then
            Dim parts As New List(Of String)()
            Dim x As Object
            For Each x In CType(o, Object())
                If x Is Nothing Then
                    parts.Add("")
                Else
                    parts.Add(Convert.ToString(x))
                End If
            Next
            Return String.Join(sep, parts.ToArray())
        End If
        Return Convert.ToString(o)
    End Function
    Public Function Join(ByVal o As Object) As Object
        Return Me.Join(o, ",")
    End Function
'@

$footerSrc = @'
End Class
'@

# --- Assemble ---------------------------------------------------------------
$seedBody = $seed
if ($seedBody.Trim().Length -eq 0) { $seedBody = "        ' (no seed)" }
$hostSrc = $hostSrc + "`n    Public Sub Seed()`n" + $seedBody + "`n    End Sub`n"
if ($decls.Trim().Length -gt 0) { $hostSrc = $hostSrc + "`n" + $decls + "`n" }

if ($codeBody.Trim().Length -gt 0) {
  # Literal .Replace (not -replace) so a '$' inside the report's own VB is not
  # read as a regex substitution.
  $codeClass = "Public Class _CodeClass`n" + $codeBody + "`nEnd Class`n`n"
  $hostSrc = $hostSrc.Replace("Public Class ExprHost", $codeClass + "Public Class ExprHost")
  $hostSrc = $hostSrc.Replace("Public User As New _User()", "Public User As New _User()`n    Public Code As New _CodeClass()")
}

function Build-Source($skip) {
  $sb = New-Object System.Text.StringBuilder
  [void]$sb.Append($hostSrc)
  $headerLines = ($hostSrc -split "`n").Count
  $starts = @{}
  $cursor = $headerLines
  for ($i = 0; $i -lt $exprs.Count; $i++) {
    $e = [string]$exprs[$i]
    if ($null -eq $e) { $e = "" }
    $e = $e.Trim()
    if ($e.StartsWith("=")) { $e = $e.Substring(1) }
    if ($e.Trim().Length -eq 0) { $e = "Nothing" }
    if ($skip.Contains($i)) { $e = "Nothing" }
    $starts[$i] = $cursor + 1
    [void]$sb.Append("    Public Function Expr_$i() As Object`n        Return ($e)`n    End Function`n")
    $cursor += 3
  }
  [void]$sb.Append($footerSrc)
  return @{ src = $sb.ToString(); starts = $starts; last = $cursor }
}

function Map-Errors($built, $result) {
  $lineToExpr = @{}
  $ordered = @($built.starts.GetEnumerator() | Sort-Object Value)
  for ($k = 0; $k -lt $ordered.Count; $k++) {
    $idx = $ordered[$k].Key
    $start = $ordered[$k].Value
    if ($k + 1 -lt $ordered.Count) { $end = $ordered[$k+1].Value - 1 } else { $end = $built.last + 5 }
    for ($ln = $start; $ln -le $end; $ln++) { $lineToExpr[$ln] = $idx }
  }
  $map = @{}
  $structural = @()
  foreach ($err in $result.Errors) {
    if ($err.IsWarning) { continue }
    $ln = [int]$err.Line
    $msg = "$($err.ErrorNumber): $($err.ErrorText)"
    if ($lineToExpr.ContainsKey($ln)) {
      $idx = $lineToExpr[$ln]
      if (-not $map.ContainsKey($idx)) { $map[$idx] = @() }
      $map[$idx] += $msg
    } else {
      $structural += "line ${ln} :: $msg"
    }
  }
  return @{ map = $map; structural = $structural }
}

try {
  $prov = New-Object Microsoft.VisualBasic.VBCodeProvider
} catch { Emit-Unavailable "VBCodeProvider unavailable: $($_.Exception.Message)" }

$cp = New-Object System.CodeDom.Compiler.CompilerParameters
$cp.GenerateInMemory = $true
$cp.GenerateExecutable = $false
[void]$cp.ReferencedAssemblies.Add("System.dll")
[void]$cp.ReferencedAssemblies.Add("Microsoft.VisualBasic.dll")

# Compile. Any expression that does not compile is neutralised to `Nothing` and
# recorded, then we retry — ONE bad expression must not blind the whole report.
$skip = New-Object 'System.Collections.Generic.HashSet[int]'
$compileErrors = @{}
$structural = @()
$compiled = $null
for ($attempt = 0; $attempt -lt 4; $attempt++) {
  $built = Build-Source $skip
  try {
    $r = $prov.CompileAssemblyFromSource($cp, $built.src)
  } catch { Emit-Unavailable "compile invocation failed: $($_.Exception.Message)" }
  $mapped = Map-Errors $built $r
  if ($mapped.map.Count -eq 0 -and $mapped.structural.Count -eq 0) { $compiled = $r; break }
  if ($mapped.structural.Count -gt 0) { $structural = $mapped.structural }
  if ($mapped.map.Count -eq 0) { break }
  foreach ($k in @($mapped.map.Keys)) {
    $compileErrors["$k"] = $mapped.map[$k]
    [void]$skip.Add([int]$k)
  }
}

if ($null -eq $compiled) {
  Write-Output (@{ available = $true; compiled = $false;
                   compileErrors = $compileErrors; structural = $structural;
                   results = @() } | ConvertTo-Json -Depth 6 -Compress)
  exit 0
}

$asm = $compiled.CompiledAssembly
$envType = $asm.GetType("_Env")
$hostType = $asm.GetType("ExprHost")
$worldFld = $envType.GetField("World")
$missFld = $envType.GetField("Misses")
$noteFld = $envType.GetField("Notes")

$results = @()
for ($w = 0; $w -lt $worlds; $w++) {
  $worldFld.SetValue($null, [int]$w)
  $inst = $null
  try {
    $inst = [Activator]::CreateInstance($hostType)
  } catch {
    Write-Output (@{ available = $true; compiled = $true; seedFailed = $true;
                     reason = $_.Exception.ToString();
                     compileErrors = $compileErrors; results = @() } |
                  ConvertTo-Json -Depth 6 -Compress)
    exit 0
  }
  for ($i = 0; $i -lt $exprs.Count; $i++) {
    if ($skip.Contains($i)) { continue }
    $missList = $missFld.GetValue($null)
    $missList.Clear()
    $noteList = $noteFld.GetValue($null)
    $noteList.Clear()
    $rec = @{ index = $i; world = $w; ok = $true; isNothing = $true; value = "";
              type = ""; truncated = $false }
    try {
      $m = $hostType.GetMethod("Expr_$i")
      $v = $m.Invoke($inst, @())
      if ($null -eq $v) {
        $rec.isNothing = $true
      } else {
        $rec.isNothing = $false
        $rec.type = $v.GetType().FullName
        $s = ""
        try {
          $s = [System.Convert]::ToString($v, [System.Globalization.CultureInfo]::InvariantCulture)
        } catch {
          $s = "$v"
        }
        if ($null -eq $s) { $s = "" }
        if ($s.Length -gt 300) {
          $s = $s.Substring(0, 300)
          $rec.truncated = $true
        }
        $rec.value = (Clean-Text $s)
      }
    } catch {
      $rec.ok = $false
      $ex = $_.Exception
      if ($null -ne $ex.InnerException) { $ex = $ex.InnerException }
      $rec.error = (Clean-Text ($ex.GetType().Name + ": " + $ex.Message))
    }
    $rec.misses = @(@($missFld.GetValue($null)) | ForEach-Object { [string]$_ })
    $rec.notes = @(@($noteFld.GetValue($null)) | ForEach-Object { [string]$_ })
    $results += $rec
  }
}

Write-Output (@{ available = $true; compiled = $true; worlds = $worlds;
                 compileErrors = $compileErrors; structural = $structural;
                 results = $results } | ConvertTo-Json -Depth 8 -Compress)
