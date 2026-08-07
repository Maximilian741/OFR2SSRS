# BATCH SMOKE-DEPLOY — ask the REAL report server to validate every RDL.
#
#   powershell -ExecutionPolicy Bypass -File tools\ssrscheck\smoke_deploy.ps1 `
#       -Dir C:\path\to\rdls -ServerUrl http://yourserver/ReportServer
#
# WHY: every local rail reasons about the file; only the SERVER runs the
# full publish validation. One rejection class (Hidden dataset scope) was
# discovered at the Report Manager dialog after every local rail passed —
# this tool finds ANY remaining class in one pass, before a human does.
#
# WHAT IT DOES per *.rdl in -Dir:
#   upload to a dedicated scratch folder (default /O2S_SmokeTest, never a
#   production path) -> record the server's own verdict/message -> DELETE
#   the uploaded item. The report is never executed; no data source binds;
#   no query runs.
#
# ZERO INSTALL: pure PowerShell (ships with Windows), SOAP over
# -UseDefaultCredentials (your logged-in domain account). Nothing added
# to the machine.
param(
    [Parameter(Mandatory = $true)][string]$Dir,
    [Parameter(Mandatory = $true)][string]$ServerUrl,
    [string]$Folder = "/O2S_SmokeTest"
)

$ErrorActionPreference = "Stop"
$ServerUrl = $ServerUrl.TrimEnd("/")
$soapUrl = "$ServerUrl/ReportService2010.asmx"

function Invoke-Soap([string]$action, [string]$body) {
    $envelope = @"
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"
               xmlns:rs="http://schemas.microsoft.com/sqlserver/reporting/2010/03/01/ReportServer">
  <soap:Body>$body</soap:Body>
</soap:Envelope>
"@
    return Invoke-WebRequest -Uri $soapUrl -Method Post -UseDefaultCredentials `
        -ContentType 'text/xml; charset=utf-8' `
        -Headers @{ SOAPAction = "http://schemas.microsoft.com/sqlserver/reporting/2010/03/01/ReportServer/$action" } `
        -Body $envelope -UseBasicParsing
}

function Get-Fault($ex) {
    try {
        $reader = New-Object System.IO.StreamReader($ex.Exception.Response.GetResponseStream())
        $xmlText = $reader.ReadToEnd()
        $m = [regex]::Match($xmlText, "<faultstring[^>]*>(.*?)</faultstring>",
                            [System.Text.RegularExpressions.RegexOptions]::Singleline)
        if ($m.Success) { return $m.Groups[1].Value.Trim() }
        return $xmlText.Substring(0, [Math]::Min(300, $xmlText.Length))
    } catch { return $ex.Exception.Message }
}

# scratch folder (AlreadyExists is fine)
try {
    [void](Invoke-Soap "CreateFolder" "<rs:CreateFolder><rs:Folder>$($Folder.Trim('/'))</rs:Folder><rs:Parent>/</rs:Parent></rs:CreateFolder>")
} catch {
    $msg = Get-Fault $_
    if ($msg -notmatch "AlreadyExists|already exists") {
        Write-Output "CANNOT CREATE $Folder : $msg"; exit 1
    }
}

$files = Get-ChildItem -Path $Dir -Filter *.rdl
$accepted = 0; $warned = 0; $rejected = 0
foreach ($f in $files) {
    $name = "o2s_smoke_" + ($f.BaseName -replace "[^A-Za-z0-9_]", "_")
    $b64 = [Convert]::ToBase64String([System.IO.File]::ReadAllBytes($f.FullName))
    try {
        $resp = Invoke-Soap "CreateCatalogItem" @"
<rs:CreateCatalogItem>
  <rs:ItemType>Report</rs:ItemType>
  <rs:Name>$name</rs:Name>
  <rs:Parent>$Folder</rs:Parent>
  <rs:Overwrite>true</rs:Overwrite>
  <rs:Definition>$b64</rs:Definition>
</rs:CreateCatalogItem>
"@
        $warnings = [regex]::Matches($resp.Content, "<Message>(.*?)</Message>")
        if ($warnings.Count -gt 0) {
            $warned++
            Write-Output ("WARNINGS  {0}" -f $f.BaseName)
            foreach ($w in $warnings | Select-Object -First 3) {
                Write-Output ("          {0}" -f $w.Groups[1].Value.Substring(0, [Math]::Min(110, $w.Groups[1].Value.Length)))
            }
        } else {
            $accepted++
            Write-Output ("ACCEPTED  {0}" -f $f.BaseName)
        }
        try {
            [void](Invoke-Soap "DeleteItem" "<rs:DeleteItem><rs:ItemPath>$Folder/$name</rs:ItemPath></rs:DeleteItem>")
        } catch {}
    } catch {
        $rejected++
        Write-Output ("REJECTED  {0}" -f $f.BaseName)
        Write-Output ("          {0}" -f (Get-Fault $_))
    }
}
Write-Output ""
Write-Output ("TOTAL: {0} accepted, {1} with warnings, {2} REJECTED of {3}" -f $accepted, $warned, $rejected, $files.Count)
if ($rejected -gt 0) { exit 1 } else { exit 0 }
