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

The runtime that is fetched is **ReportViewer 2015** (`v12.x`), which
processes the RDL `2008/01` schema — exactly the schema this project emits.
(The later `v15`/`150.x` runtime dropped the 2008 schema in favor of 2016.)

## Render one RDL

    python tools/renderlab/render.py path\to\report.rdl out.pdf

Synthesizes type-correct sample rows for every dataset declared in the
RDL (same column name ⇒ same values across datasets, so `Lookup()`
joins resolve), then renders through the real engine.

There are two render paths, and `render.py` picks automatically:

* **`RenderLab.exe`** — a `LocalReport` host that JIT-compiles the RDL's
  VB.NET expressions, so computed values are exercised end-to-end. This is
  the default.
* **`render_rdl.ps1`** — a fallback that drives the signed ReportViewer
  DLLs directly. It is used when `RenderLab.exe` cannot launch (for
  example, when an OS Application Control policy blocks the JIT host); it
  staticizes expressions first so the engine never has to compile one. The
  returned result carries a `mode` field (`expression` vs `layout`) so the
  caller knows which path ran.

## Verify a corpus

    python tools/renderlab/run_corpus.py <oracle-xml-or-dir> [...more] \
        [--out DIR] [--rows N]

Converts each Oracle XML with the project pipeline, renders each generated
RDL with the MS engine, and prints a verdict table per report:

* `rendered` — the engine produced a PDF (the ultimate "will it render"
  proof)
* `pages` — page count
* `blank` — interior pages with no content text (the blank-page bug class)
* `warns` — engine warnings (overlap / width overflow surface here)

Never embeds client data — inputs come from the command line at run time.

## Used by the test suite

`tests/test_renderlab_fixtures.py` renders the synthetic fixtures through
this harness as part of `python -m pytest`. When the engine host is not
available on the machine, those cases skip cleanly rather than failing.
