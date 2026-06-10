// RenderLab.exe — render an RDL to PDF with Microsoft's LocalReport engine.
//
//   RenderLab.exe <report.rdl> <data.json> <out.pdf>
//
// Why an exe instead of PowerShell: the engine compiles report expressions
// into an "expression host" assembly inside a sandbox AppDomain; assembly
// resolution there follows the PROCESS appbase + app.config probing paths.
// RenderLab.exe.config probes lib\, so every ReportViewer assembly resolves
// in every AppDomain — no resolver hacks.
//
// data.json: {"datasets":[{"name":"Q","columns":[{"name":"C","type":"System.String"}],
//                          "rows":[["v1"],...]}]}
using System;
using System.Collections.Generic;
using System.Data;
using System.Globalization;
using System.IO;
using Microsoft.Reporting.WinForms;
using System.Web.Script.Serialization;

public static class RenderLab
{
    public static int Main(string[] args)
    {
        if (args.Length < 3)
        {
            Console.WriteLine("usage: RenderLab.exe <rdl> <data.json> <out.pdf>");
            return 2;
        }
        try
        {
            Console.WriteLine("STAGE create");
            var lr = new LocalReport();
            using (var fs = File.OpenRead(args[0]))
                lr.LoadReportDefinition(fs);
            Console.WriteLine("STAGE loaded");

            var ser = new JavaScriptSerializer { MaxJsonLength = int.MaxValue };
            var spec = ser.Deserialize<Dictionary<string, object>>(
                File.ReadAllText(args[1]));
            var datasets = (System.Collections.ArrayList)spec["datasets"];
            foreach (Dictionary<string, object> ds in datasets)
            {
                var name = (string)ds["name"];
                var dt = new DataTable(name);
                var cols = (System.Collections.ArrayList)ds["columns"];
                var types = new List<Type>();
                foreach (Dictionary<string, object> c in cols)
                {
                    var t = Type.GetType((string)c["type"]) ?? typeof(string);
                    if (t == typeof(byte[])) t = typeof(byte[]);
                    types.Add(t);
                    dt.Columns.Add((string)c["name"], t);
                }
                var rows = (System.Collections.ArrayList)ds["rows"];
                foreach (System.Collections.ArrayList row in rows)
                {
                    var dr = dt.NewRow();
                    for (int i = 0; i < types.Count && i < row.Count; i++)
                    {
                        var v = row[i];
                        if (v == null) { dr[i] = DBNull.Value; continue; }
                        var t = types[i];
                        if (t == typeof(DateTime))
                            dr[i] = DateTime.Parse(v.ToString(), CultureInfo.InvariantCulture);
                        else if (t == typeof(byte[]))
                            dr[i] = DBNull.Value;
                        else
                            dr[i] = Convert.ChangeType(v, t, CultureInfo.InvariantCulture);
                    }
                    dt.Rows.Add(dr);
                }
                lr.DataSources.Add(new ReportDataSource(name, dt));
                Console.WriteLine("STAGE datasource " + name + " rows=" + dt.Rows.Count);
            }

            Console.WriteLine("STAGE render-start");
            string mime, enc, ext;
            string[] ids;
            Warning[] warnings;
            byte[] bytes = lr.Render("PDF", null, out mime, out enc, out ext,
                                     out ids, out warnings);
            File.WriteAllBytes(args[2], bytes);
            if (warnings != null)
                foreach (var w in warnings)
                    Console.WriteLine("WARN " + w.Severity + " " + w.Code + " "
                                      + w.ObjectName + ": " + w.Message);
            Console.WriteLine("RENDER OK bytes=" + bytes.Length);
            return 0;
        }
        catch (Exception ex)
        {
            var parts = new List<string>();
            for (var cur = ex; cur != null; cur = cur.InnerException)
                parts.Add("[" + cur.GetType().Name + "] " + cur.Message);
            Console.WriteLine("RENDER FAIL: " + string.Join("\n  inner: ", parts));
            return 1;
        }
    }
}
