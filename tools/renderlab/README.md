# RenderLab — real Microsoft rendering verification

Renders generated RDLs through **Microsoft's ReportViewer LocalReport
engine** — the same RDL processing/rendering code SSRS uses — entirely
locally, with synthetic data injected per dataset (no database, no report
server). Output is a real PDF, which `run_corpus.py` then measures
(page count, blank pages, border-rect positions, centering) so layout
claims are verified numerically instead of asserted.

## One-time setup

    python tools/renderlab/fetch_reportviewer.py

Downloads the official Microsoft ReportViewer 2015 runtime NuGet packages
(RDL-2008-schema capable) from nuget.org and unpacks the DLLs into
`tools/renderlab/lib/` (gitignored). Requires Windows + .NET Framework
(present on every Windows 10/11 machine).

## Render one RDL

    python tools/renderlab/render.py path\to\report.rdl out.pdf

Synthesizes type-correct sample rows for every dataset declared in the
RDL (same column name ⇒ same values across datasets, so `Lookup()`
joins resolve), then renders via PowerShell + LocalReport.

## Verify a corpus

    python tools/renderlab/run_corpus.py <oracle-xml-or-dir> [...more]

Converts each Oracle XML with the project pipeline, renders the RDL with
the MS engine, and prints a verdict table: rendered? page count? blank
pages? width overflow? Never embeds client data — inputs come from the
command line at run time.
