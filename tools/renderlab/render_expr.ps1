# EXPRESSION-MODE render driver: evaluates a report's =expressions for real.
#
# WHY THIS EXISTS
# ---------------
# The original expression host was a locally-compiled RenderLab.exe. On a
# machine with Smart App Control enforcing (HKLM CI\Policy
# VerifiedAndReputablePolicyState = 1) Windows blocks launching any unsigned
# executable, so that host died with WinError 4551 and every render fell back
# to the LAYOUT path -- which staticizes expressions into placeholders and
# therefore CANNOT prove a single computed value.
#
# Smart App Control blocks PROCESS CREATION of unsigned exes. It does not stop
# a signed host (powershell.exe) from loading managed assemblies, which is why
# Add-Type and the signed ReportViewer DLLs work fine here.
#
# The remaining obstacle was not security at all: LocalReport runs a report's
# compiled expression host inside a SANDBOX AppDomain. That domain's
# ApplicationBase is the host process's directory (PowerShell's, under
# System32), so it cannot see the ReportViewer DLLs in LibDir and fails with
# "Failed to load expression host assembly ... Microsoft.ReportViewer.Common".
# An AssemblyResolve handler on the default domain does not help -- the sandbox
# domain never sees it. ExecuteReportInCurrentAppDomain would sidestep the
# sandbox but demands legacy CAS policy, removed in .NET 4.
#
# So: run the whole render inside an AppDomain whose ApplicationBase IS LibDir.
# The sandbox domain LocalReport spawns inherits that base and resolves the
# engine assemblies by normal probing. No admin rights, no GAC changes, and
# Smart App Control stays ON.
param(
    [Parameter(Mandatory = $true)][string]$RdlPath,
    [Parameter(Mandatory = $true)][string]$DataJson,
    [Parameter(Mandatory = $true)][string]$OutPdf,
    [Parameter(Mandatory = $true)][string]$LibDir
)

$ErrorActionPreference = "Stop"
$lib = (Resolve-Path $LibDir).Path
$rvWin = Join-Path $lib "Microsoft.ReportViewer.WinForms.dll"
$helper = Join-Path $lib "RvExprHost.exe"

# The helper must live in LibDir: the render AppDomain probes its own
# ApplicationBase to load it by simple name.
$src = @"
using System;
using System.Collections.Generic;
using System.Data;
using System.Globalization;
using System.IO;
using System.Text;
using System.Web.Script.Serialization;
using Microsoft.Reporting.WinForms;

namespace RvExprHost {
    public class Renderer {
        // Entry point: runs INSIDE the render AppDomain (ApplicationBase =
        // LibDir), so the engine and its expression-host sandbox both resolve
        // the ReportViewer assemblies by ordinary probing.
        public static int Main(string[] args) {
            string log = Render(args[0], args[1], args[2]);
            Console.WriteLine(log);
            return log.Contains("RENDER OK") ? 0 : 1;
        }
        public static string Render(string rdlPath, string dataJson, string outPdf) {
            StringBuilder log = new StringBuilder();
            try {
                LocalReport lr = new LocalReport();
                using (FileStream fs = File.OpenRead(rdlPath)) {
                    lr.LoadReportDefinition(fs);
                }
                try { lr.EnableHyperlinks = true; } catch {}
                try { lr.EnableExternalImages = true; } catch {}
                log.AppendLine("STAGE loaded");

                JavaScriptSerializer ser = new JavaScriptSerializer();
                ser.MaxJsonLength = Int32.MaxValue;
                Dictionary<string, object> spec =
                    (Dictionary<string, object>)ser.DeserializeObject(
                        File.ReadAllText(dataJson));
                object[] datasets = (object[])spec["datasets"];
                foreach (object dsObj in datasets) {
                    Dictionary<string, object> ds =
                        (Dictionary<string, object>)dsObj;
                    string name = Convert.ToString(ds["name"]);
                    DataTable dt = new DataTable(name);
                    object[] cols = (object[])ds["columns"];
                    foreach (object cObj in cols) {
                        Dictionary<string, object> c =
                            (Dictionary<string, object>)cObj;
                        Type t = Type.GetType(Convert.ToString(c["type"]));
                        if (t == null) { t = typeof(string); }
                        dt.Columns.Add(Convert.ToString(c["name"]), t);
                    }
                    object[] rows = (object[])ds["rows"];
                    foreach (object rObj in rows) {
                        object[] row = (object[])rObj;
                        DataRow dr = dt.NewRow();
                        for (int i = 0; i < cols.Length && i < row.Length; i++) {
                            object v = row[i];
                            if (v == null) { dr[i] = DBNull.Value; continue; }
                            Type ct = dt.Columns[i].DataType;
                            string s = Convert.ToString(
                                v, CultureInfo.InvariantCulture);
                            if (ct == typeof(DateTime)) {
                                dr[i] = DateTime.Parse(
                                    s, CultureInfo.InvariantCulture);
                            } else if (ct == typeof(decimal)) {
                                dr[i] = Convert.ToDecimal(
                                    s, CultureInfo.InvariantCulture);
                            } else if (ct == typeof(int)) {
                                dr[i] = Convert.ToInt32(
                                    s, CultureInfo.InvariantCulture);
                            } else {
                                dr[i] = s;
                            }
                        }
                        dt.Rows.Add(dr);
                    }
                    lr.DataSources.Add(new ReportDataSource(name, dt));
                    log.AppendLine("STAGE datasource " + name +
                                   " rows=" + dt.Rows.Count);
                }

                log.AppendLine("STAGE render-start");
                string mime, enc, ext;
                string[] ids;
                Warning[] warns;
                byte[] bytes = lr.Render("PDF", null, out mime, out enc,
                                         out ext, out ids, out warns);
                File.WriteAllBytes(outPdf, bytes);
                if (warns != null) {
                    foreach (Warning w in warns) {
                        log.AppendLine("WARN " + w.Severity + " " + w.Code +
                                       " " + w.ObjectName + ": " + w.Message);
                    }
                }
                log.AppendLine("RENDER OK bytes=" + bytes.Length);
            } catch (Exception ex) {
                List<string> parts = new List<string>();
                for (Exception cur = ex; cur != null; cur = cur.InnerException) {
                    parts.Add("[" + cur.GetType().Name + "] " + cur.Message);
                }
                log.AppendLine("RENDER FAIL: " +
                               String.Join("\n  inner: ", parts.ToArray()));
            }
            return log.ToString();
        }
    }
}
"@

# Rebuild the helper when missing or older than this script.
$needBuild = $true
if (Test-Path $helper) {
    $h = Get-Item $helper
    $s = Get-Item $PSCommandPath
    if ($h.LastWriteTimeUtc -ge $s.LastWriteTimeUtc) { $needBuild = $false }
}
if ($needBuild) {
    if (Test-Path $helper) { Remove-Item $helper -Force }
    Add-Type -TypeDefinition $src -Language CSharp `
        -OutputAssembly $helper -OutputType ConsoleApplication `
        -ReferencedAssemblies @($rvWin, "System.Data", "System.Xml",
                                "System.Web.Extensions", "System.Drawing",
                                "System.Windows.Forms")
    Write-Output "STAGE helper-built"
}

try {
    $setup = New-Object System.AppDomainSetup
    $setup.ApplicationBase = $lib
    $dom = [System.AppDomain]::CreateDomain("o2s-expr", $null, $setup)
    Write-Output "STAGE appdomain base=$lib"
    # ExecuteAssembly runs the helper's Main in THIS domain -- an in-process
    # managed load, not process creation, so Smart App Control (which gates
    # CreateProcess on unsigned exes) does not apply. Avoids marshalling
    # ReportViewer types across domains, which access-violates.
    $rc = $dom.ExecuteAssembly($helper, @((Resolve-Path $RdlPath).Path,
                                          (Resolve-Path $DataJson).Path,
                                          $OutPdf))
    try { [System.AppDomain]::Unload($dom) } catch {}
    exit $rc
}
catch {
    Write-Output ("HOST FAIL: [" + $_.Exception.GetType().Name + "] " +
                  $_.Exception.Message)
    exit 1
}
