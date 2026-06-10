# Oracle Reports to SSRS Converter

Drag an Oracle Reports artifact in, get a deployable SSRS RDL out.

Oracle Reports is desupported. This converts your reports to SSRS in
minutes — and **proves** the output instead of promising it:

- **450+ automated tests**, including renders through **Microsoft's own
  ReportViewer engine** (the same RDL processing code SSRS runs) with the
  produced PDFs measured for page cadence, blank pages, and geometry.
- **100% of generated RDL validated** against Microsoft's official
  RDL 2008 XSD schema.
- **Never prompts for parameter values** — at upload, Refresh Fields, or
  run. Every parameter and query bind ships wired.
- **Batch a whole folder** and get a Migration Assessment: per-report
  effort tiers, fidelity scores, and engine-render verdicts.

![Oracle2SSRS — drag-and-drop Oracle Reports to SSRS conversion](docs/screenshots/hero-app-view.png)

## What it does

Oracle2SSRS is a local Flask app that parses Oracle Reports XML/RDF/SQL/DOCX
artifacts, translates Oracle SQL and PL/SQL into T-SQL, and emits a
structurally valid SSRS 2008+ `.rdl` you can upload to a real Report Server.
A four-pane preview shows the rendered mockup, the raw RDL, bursting
(per-recipient distribution) detection, and sub-report (drill-through) child
generation — everything you need to verify the conversion before you deploy.

## Quick start

```bash
git clone https://github.com/Maximilian741/OFR2SSRS.git
cd OFR2SSRS
pip install -r requirements.txt

# Windows
run.bat

# macOS / Linux / WSL
./run.sh
```

Then open <http://127.0.0.1:5057> and drop an Oracle Reports XML on the page.
Override the listen port with `PORT=8080 ./run.sh`. Requires Python 3.9+.

## Features

The UI surfaces **four main tabs** for every conversion:

- **HTML Mockup** — black-and-white render of the layout, generated from the
  parsed structure. Eyeball the result before you ever open Report Builder.
- **RDL XML** — syntax-highlighted, structurally valid SSRS RDL. Download it
  and upload straight to your Report Server.
- **Bursting** — automatic detection of Oracle distribution patterns
  (per-recipient PDF output, email blast keys, file-path templates) with a
  downloadable **Burst Pack** containing the recipient query, parameter
  mapping, and a PowerShell DDS-emulator script for SSRS Standard.
- **Sub-Reports** — when the parser detects drill-through child reports, this
  tab lets you upload the child Oracle XML/SQL/DOCX and generates an RDL for
  each child.

Advanced views (Side-by-Side diff, Live Data against a sample SQLite DB,
Validation, Deploy Checklist, Extras with audit + AI prompts) are one click
away behind an **Advanced views** toggle.

Key differentiators:

- **Drag-drop ingest** of mixed artifacts: Oracle XML, raw `.sql`, `.docx`
  walkthroughs, `.rdf` exports, PNG/JPG reference screenshots, even whole
  folders — the ingester classifies each blob and runs whatever subset of
  the pipeline is feasible.
- **PL/SQL to T-SQL translation** with a rule-based core (DECODE, NVL,
  TO_CHAR, TO_DATE, TRUNC, SYSDATE, INSTR, SUBSTR, CHR, `||`, `(+)`,
  LISTAGG, ROWNUM, bind variables, lexical references) and auto-generated
  `dbo.fn_*` UDF stubs for every Oracle package function call.
  `CONNECT BY` / `START WITH` is detected and annotated with a TODO
  guiding a manual rewrite to a recursive CTE (not auto-translated).
- **Data source binding** — type your report server's shared data source
  path once in the sidebar (e.g. `/Data Sources/MyOracle`) and every
  generated artifact (main RDL, sub-report RDLs, the burst pack) ships
  pre-bound to it: upload and run, no repointing. An embedded connection
  string remains available as an opt-in. Every parameter carries a
  `=Nothing` default and every query bind is wired, so the "Define Query
  Parameters" dialog NEVER pops — at upload, Refresh Fields, or run.
  This is load-bearing for production use.
- **Batch migration + Migration Assessment** — point the CLI at a whole
  folder of Oracle XML exports:

  ```bash
  python tools/batch_convert.py path/to/reports -o out --render
  ```

  Every report is converted, preflighted, fidelity-scored, optionally
  render-verified through the MS engine, and classified into an effort
  tier (`automatic` / `light-touch` / `assisted` / `manual`). You get
  `out/rdl/*.rdl`, a printable `ASSESSMENT.html` executive summary, and
  `migration_pack.zip`. Also available in the app sidebar ("Batch
  migration") and over HTTP (`POST /api/batch`). Community Edition
  processes up to 10 reports per batch (single-report conversion is
  always unlimited); set `O2S_LICENSE=pro` to lift the cap.
- **Embedded images (seals / logos / watermarks)** — images stored in the
  Oracle export's `binaryData` (both inline and document-level styles,
  including the nibble-swapped hex encoding some exports use) are decoded
  and embedded into the RDL automatically; layout placeholders without
  bytes get an upload slot in the sidebar, previewed live in the mockup.
- **`.rdf` onboarding** — drop an `.rdf` binary and the app replies with
  the exact one-line `rwconverter` command (Oracle's own tool) to export
  it to the XML this converter consumes, including the wildcard form for
  whole folders.
- **Render-verified output (RenderLab)** — `tools/renderlab` drives
  Microsoft's ReportViewer LocalReport engine (the same RDL processing
  code SSRS runs) headlessly with synthetic data, renders real PDFs from
  generated RDLs, and measures them: page cadence, blank-page detection,
  geometry. Run `python tools/renderlab/fetch_reportviewer.py` once
  (fetches the official Microsoft runtime from nuget.org); the rendering
  checks then run as part of `pytest`. Publish-time semantic rules that
  XSD validation cannot catch (aggregate-in-Lookup, TablixMember
  RepeatOnNewPage consistency) are enforced this way.
- **Pre-flight audit + cross-validation** against supporting artifacts: the
  parser cross-checks bind variables, parameter names, and column references
  against any `.sql`/`.docx`/screenshot you dropped in.
- **Per-record (letter/certificate) layout vs tabular grid** detection: the
  generator picks the right `<Body>` shape for the source's frame structure
  instead of forcing every report into one mold.
- **Optional Claude auto-fix.** If `ANTHROPIC_API_KEY` is set in `.env`, the
  Extras tab can call Claude once per AI prompt, validate each result, and
  patch the RDL in place. Without a key the prompt templates are still
  rendered for paste-into-Copilot use.
- **Bundle download.** One click produces a `.zip` of the RDL, validation
  report, deploy checklist, audit trail, AI prompts, burst pack, and a
  README explaining what's inside.

## Manual deploy workflow

The converter intentionally stops at the file boundary — it does not push to
a Report Server. After downloading the `.rdl`:

1. Upload it to your SSRS folder (Report Manager or Report Builder).
2. Open the report's Data Source properties and point it at the shared data
   source already configured in that folder.
3. Open the dataset and **Refresh Fields** — no parameter prompt should
   appear (this is what `SharedDataSourceReference` buys you).
4. Save, view, export to PDF for end users.

## Architecture

Pipeline: parse Oracle XML to a single `ParsedReport` dataclass, translate
SQL/PL-SQL in place, generate RDL, render previews, validate, build the
deploy checklist and burst pack. Every module reads or writes the same
dataclass — no module imports another's internals.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the module-by-module
breakdown and [docs/API.md](docs/API.md) for the HTTP endpoint reference.

## Development

```bash
pip install -r requirements.txt
pytest                                          # run the test suite
python backend/cli.py samples/oracle --out ./out --strict   # batch convert
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, test, and pull request
guidelines.

## License

**Elastic License 2.0** (source-available). In plain terms:

- ✅ Free to use, modify, and run inside your organization — convert as
  many of your own reports as you like.
- ✅ Free to evaluate, fork, and contribute.
- ❌ You may not offer Oracle2SSRS itself to third parties as a hosted /
  managed service.
- ❌ You may not remove or circumvent the license-key functionality
  (`O2S_LICENSE` tiers).

Commercial licenses (Pro / Enterprise: unlimited batch, white-label
Migration Assessments, support) — open an issue or contact the author.
Full text in [LICENSE](LICENSE).
