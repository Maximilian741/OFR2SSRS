# SERVER-SIDE PREVIEW RENDER — let the customer's OWN SSRS render the RDL.
#
# WHY: every local render path needs something on the machine (a Python
# package for rasterising, the ReportViewer DLL folder). On a locked-down
# workstation even a project-folder download can be against policy. The one
# render engine EVERY SSRS customer already has is the report server itself
# — and it renders with the real fonts, real expression evaluation, and
# (when the shared data source path is right) REAL DATA. Zero local
# footprint: this script uses only PowerShell, which ships with Windows,
# and -UseDefaultCredentials, which is the logged-in domain identity.
#
# WHAT IT DOES
#   1. Ensures a scratch folder on the server (default /O2S_Preview).
#   2. Uploads the RDL there via the ReportService2010 SOAP endpoint.
#   3. Renders it to PDF via URL access (rs:Format=PDF).
#   4. Deletes the uploaded item again (unless -Keep).
#
# SAFETY
#   * Runs only when the caller passes a server URL — never by default.
#   * Uploads to the dedicated scratch folder, never over existing items.
#   * The item is deleted afterwards.
#
# Output: "RENDER OK bytes=<n>" on success (PDF written to -OutPdf).
# On failure: "SERVER FAIL: <the server's own message>" — SSRS messages
# (rsInvalidReportDefinition, rsItemNotFound, missing data source) name
# the real problem better than anything local can.
param(
    [Parameter(Mandatory = $true)][string]$RdlPath,
    [Parameter(Mandatory = $true)][string]$ServerUrl,
    [Parameter(Mandatory = $true)][string]$OutPdf,
    [string]$Folder = "/O2S_Preview",
    [switch]$Keep
)

$ErrorActionPreference = "Stop"
$ServerUrl = $ServerUrl.TrimEnd("/")
$soapUrl = "$ServerUrl/ReportService2010.asmx"
$name = "o2s_preview_" + ([System.IO.Path]::GetFileNameWithoutExtension($RdlPath) -replace "[^A-Za-z0-9_]", "_")
$itemPath = ($Folder.TrimEnd("/") + "/" + $name)

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

function Get-SoapFault($ex) {
    try {
        $stream = $ex.Exception.Response.GetResponseStream()
        $reader = New-Object System.IO.StreamReader($stream)
        $xmlText = $reader.ReadToEnd()
        $m = [regex]::Match($xmlText, "<faultstring[^>]*>(.*?)</faultstring>",
                            [System.Text.RegularExpressions.RegexOptions]::Singleline)
        if ($m.Success) { return $m.Groups[1].Value.Trim() }
        return $xmlText.Substring(0, [Math]::Min(400, $xmlText.Length))
    } catch { return $ex.Exception.Message }
}

try {
    # 1. scratch folder (AlreadyExists is fine)
    $parent = "/"
    $leaf = $Folder.Trim("/")
    try {
        [void](Invoke-Soap "CreateFolder" @"
<rs:CreateFolder><rs:Folder>$leaf</rs:Folder><rs:Parent>$parent</rs:Parent></rs:CreateFolder>
"@)
        Write-Output "STAGE folder-created $Folder"
    } catch {
        $msg = Get-SoapFault $_
        if ($msg -notmatch "AlreadyExists|already exists") {
            Write-Output "SERVER FAIL: creating $Folder : $msg"; exit 1
        }
        Write-Output "STAGE folder-exists $Folder"
    }

    # 2. upload (overwrite our OWN previous scratch copy only)
    $bytes = [System.IO.File]::ReadAllBytes((Resolve-Path $RdlPath))
    $b64 = [Convert]::ToBase64String($bytes)
    try {
        [void](Invoke-Soap "CreateCatalogItem" @"
<rs:CreateCatalogItem>
  <rs:ItemType>Report</rs:ItemType>
  <rs:Name>$name</rs:Name>
  <rs:Parent>$Folder</rs:Parent>
  <rs:Overwrite>true</rs:Overwrite>
  <rs:Definition>$b64</rs:Definition>
</rs:CreateCatalogItem>
"@)
        Write-Output "STAGE uploaded $itemPath"
    } catch {
        Write-Output ("SERVER FAIL: upload rejected : " + (Get-SoapFault $_)); exit 1
    }

    # 3. render via URL access — the server evaluates expressions and, when
    #    the shared data source resolves, runs the REAL queries.
    $renderUrl = "$ServerUrl`?$([uri]::EscapeDataString($itemPath))&rs:Command=Render&rs:Format=PDF"
    try {
        Invoke-WebRequest -Uri $renderUrl -UseDefaultCredentials `
            -OutFile $OutPdf -UseBasicParsing
        $len = (Get-Item $OutPdf).Length
        Write-Output "RENDER OK bytes=$len"
    } catch {
        Write-Output ("SERVER FAIL: render : " + (Get-SoapFault $_)); exit 1
    }
}
finally {
    # 4. clean up the scratch item
    if (-not $Keep) {
        try {
            [void](Invoke-Soap "DeleteItem" "<rs:DeleteItem><rs:ItemPath>$itemPath</rs:ItemPath></rs:DeleteItem>")
            Write-Output "STAGE cleaned $itemPath"
        } catch {}
    }
}
exit 0
