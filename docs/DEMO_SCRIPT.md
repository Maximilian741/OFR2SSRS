# Demo script — Oracle2SSRS Converter

## 60-second pitch

> "Imagine you have hundreds of legacy Oracle Reports running on a server that
> Oracle no longer supports. Migrating each one to SSRS by hand is weeks of
> work per report. **Oracle2SSRS** does the conversion in seconds: end users
> just drag the file in, and get a deployable RDL out the other side — with a
> live HTML mockup, per-recipient bursting detection, and a downloadable
> bundle so they can verify everything before they upload to SSRS."

## Live demo (3 minutes)

1. **Show the empty page.** "This is the whole UI. Sidebar on the left,
   four main tabs on the right — Mockup, RDL, Bursting, Sub-Reports.
   There's nothing to learn. Advanced views like Validation and Live Data
   are one click away under the Advanced toggle."

2. **Drop an Oracle Reports XML** onto the drop zone (or click any sample
   in the sidebar). The status pill flips to *Converting* then *Converted*.
   The sidebar fills with the report summary: parameter count, dataset
   count, formula count.

3. **Tab 1 — HTML Mockup.** "This is what the SSRS report will look like
   when it renders. Header, parameter form, data body, signature block.
   100% generated from the Oracle XML — we never wrote any of this markup
   ourselves. For a per-record report (letter, certificate, invoice) you
   see one card per row. For a tabular report you see a grouped grid."

4. **Tab 2 — RDL XML.** Scroll the syntax-highlighted RDL. "This is a
   real, structurally valid SSRS 2008+ RDL. You download this file,
   upload it to your SSRS folder, point the data source at your shared
   DS, refresh fields — and because of how we shape the DataSource
   reference, SSRS does not pop the Define Query Parameters dialog
   that normally blocks editing."

5. **Tab 3 — Bursting.** "If the source report had per-recipient
   distribution — one PDF per customer, per facility, per district
   — we detect it automatically. Here's the burst key field we derived,
   the filename pattern, the recipient query, and a PowerShell DDS
   emulator script for SSRS Standard installations. Everything zips up
   as a downloadable Burst Pack."

6. **Tab 4 — Sub-Reports.** "If the parent report drills through to
   child reports, this tab lights up. Upload the child's Oracle XML
   right here, click Build, and you get an RDL for the child generated
   by the same pipeline."

7. **Click Advanced views** to show the additional tabs:
   - **Side-by-Side** for auditors — original Oracle XML on the left,
     converted RDL on the right.
   - **Live Data** — translated T-SQL runs against a bundled SQLite
     sample DB so reviewers can see real rows come back.
   - **Validation** — T-SQL static validator + RDL structural validator,
     errors at the top.
   - **Deploy Checklist** — punch list to take the RDL from local file
     to running on a real SSRS server.
   - **Extras** — translation audit trail + AI prompts + optional Claude
     auto-fix.

8. **Click Download .rdl** in the sidebar. "There's the file you upload
   to your SSRS server. Or click Download Bundle to get the RDL plus the
   validation report, deploy checklist, audit trail, and burst pack in
   a single zip."

## What to call out if asked

- **Architecture.** Independent modules behind one `ParsedReport`
  dataclass: parser, two translators (SQL→T-SQL and a PL/SQL *formula*
  compiler), RDL generator + post-process, the HTML-mockup and live-DB
  preview, three validators, plus bursting, sub-reports, pre-flight audit,
  a fidelity self-check, and cross-validation against supporting artifacts
  (SQL files, DOCX walkthroughs, screenshots). The same UI works for any
  Oracle Reports XML.
- **SQL translation coverage.** DECODE to CASE, NVL to ISNULL, TO_CHAR to
  CONVERT, TO_DATE to TRY_CONVERT, `(+)` outer join to LEFT JOIN, `||` to
  `+`, lexical refs flagged, every `Pkg_*.F_*` package call gets a
  corresponding `dbo.fn_*` UDF stub auto-generated alongside the RDL.
- **Formula compiler.** PL/SQL formula columns (`CF_*` / `CP_*`) are
  compiled — by a real tokenizer + parser, not pattern substitution — into
  SSRS VB.NET expressions that compute inline (`||`→`&`, `NVL`/`DECODE`→
  `IIf`, `SUBSTR`→`Mid`, `TO_CHAR`→`Format`, and so on). Anything that
  references an external package function is left as a safe placeholder
  rather than emitting a broken expression.
- **Verified, not asserted.** Generated RDL is validated against
  Microsoft's RDL 2008 XSD and render-verified through Microsoft's
  ReportViewer engine (`tools/renderlab`), which renders the RDL to a real
  PDF and measures page count and blank pages.
- **SharedDataSourceReference design.** The RDL ships pointing at a named
  shared data source rather than an embedded connection string. This is
  what prevents the "Define Query Parameters" dialog at refresh time.
  Production users have flagged this as the difference between "works"
  and "unusable."
- **Honesty.** Every non-trivial translation appears as a warning chip
  with an audit-trail entry. The user knows exactly which lines the
  converter is confident about and which to review.
- **Offline by default.** No SaaS, no telemetry, no API keys required.
  Optional Claude assist is opt-in via `.env`.

## Backup plan if something breaks

- `samples/expected_rdl/SAMPLE_INSPECTION.rdl` — pre-generated RDL output
  for the bundled sample, in case the live conversion misbehaves.
- `samples/oracle/SAMPLE_INSPECTION.xml` — the bundled (synthetic) Oracle
  source artifact.
- `python -m pytest -q` — runs the full test suite (parametrized,
  name-agnostic fixtures): **620 passed, 19 skipped**.
