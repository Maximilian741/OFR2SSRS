# Oracle2SSRS — Architecture

## 1. Mission

Oracle2SSRS converts legacy **Oracle Reports 6i / 9i / 10g** report definitions
(`.xml` / `.rdf`) into **Microsoft SQL Server Reporting Services (SSRS) RDL**
documents that open natively in Report Builder and Visual Studio Report
Designer.

Manually migrating an Oracle Reports artifact to SSRS is a 40-200 hour job per
report: re-typing every parameter, re-translating every PL/SQL formula column,
re-implementing every repeating frame, and re-validating the layout against the
original. A typical state agency or utility carries hundreds of these reports.

Our pipeline turns the bulk of that work into a 30-second drag-and-drop:

* parse the Oracle XML (or a mixed folder of XML / SQL / DOCX / RDF / image
  artifacts) into a single in-memory `ParsedReport`
* translate the embedded Oracle SQL into T-SQL, and compile each PL/SQL
  formula column (`CF_*` / `CP_*`) into an SSRS VB.NET expression that
  computes inline
* emit an RDL that is validated against Microsoft's own RDL 2008 XSD and
  render-verified through the ReportViewer engine
* detect per-recipient (bursting) distribution and drill-through sub-reports
* render an HTML mock-up of the RDL so a reviewer can eyeball the result
  before opening Report Builder
* run the translated T-SQL against a bundled SQLite sample DB so a
  non-technical reviewer can see live data immediately

What is **left to humans** is intentionally surfaced (lexical references,
package UDF stubs, pixel-precise positioning) rather than silently fudged.

---

## 2. High-level pipeline

```
  .xml / .rdf / .sql      +-------------------------+
  .docx / .png / folder -> |  parsers/oracle_xml.py  |
  (bytes; ingest.py        |  parse_oracle_xml()     | --> ParsedReport
   classifies bundles)     +-------------------------+     (models.py)
                                        |
                                        v
        +-------------------------------------------------------+
        | translators/                                          |
        |  plsql_to_tsql.py  translate_report()                 |
        |     SQL  -> T-SQL (mutates q.tsql, appends notes)      |
        |  plsql_formula.py  translate_formula_to_vb()           |
        |     CF_/CP_ PL/SQL -> SSRS VB.NET expression           |
        |  udf_stubs.py       package-fn -> dbo.fn_* signatures  |
        +-------------------------------------------------------+
                                        |
                                        v
        +-------------------------+        +-----------------------+
        | generators/rdl.py       | -----> | rdl_xml: str          |
        | generate_rdl(report)    |        | (RDL 2008 document)   |
        | rdl_postprocess.py      |        +-----------------------+
        +-------------------------+
                                        |
                                        v
   +--------------------+   +-------------------+   +----------------------+
   | preview/           |   | validators/       |   | deployment.py        |
   |  html_mockup       |   |  tsql_check       |   |  build_checklist()   |
   |  mockup_variants   |   |  rdl_check        |   | bursting.py          |
   |  live_data         |   |  preflight        |   | subreports.py        |
   +--------------------+   +-------------------+   | fidelity.py          |
                                        |           +----------------------+
                                        v
                +-------------------------+
                |  Flask app.py           |
                |  JSON payload to UI     |
                +-------------------------+

  Offline verification (not in the request path):
    tools/renderlab/  renders the emitted RDL through Microsoft's
    ReportViewer engine to a real PDF and measures it.
```

The shared in-memory contract between every module is `ParsedReport`
(see `backend/converter/models.py`). Adding a new translator, validator, or
generator means reading or writing this single dataclass — no module imports
another module's internals.

---

## 3. Module-by-module

### 3.1 `backend/app.py` — Flask entry point

* Creates the Flask app, wires the templates dir to `frontend/templates/` and
  the static dir to `frontend/static/`.
* Caches the most recent conversion in a process-local `_LAST` dict so the
  `/api/download/rdl` and `/api/download/bundle` endpoints can serve files
  without re-running the pipeline.
* Listens on `127.0.0.1:5057` by default; override with `PORT=8080`.
* Endpoints (full reference in `docs/API.md`):
  * `GET /` — render the index page
  * `POST /api/convert` — single-file Oracle XML upload
  * `POST /api/convert-bundle` — multi-file folder upload
  * `POST /api/convert-sample/<name>` — run against a bundled sample
  * `POST /api/compare` — diff two Oracle reports
  * `POST /api/run-query` — run translated T-SQL against the sample SQLite DB
  * `GET /api/download/rdl` — download the most recent RDL
  * `GET /api/download/bundle` — download the full conversion bundle (zip)
  * `GET /api/mockup/<variant>` — print or compact mockup variant
  * `POST /api/burst-preview` — preview the burst recipient query
  * `POST /api/download/burst-pack` — download the Burst Pack zip
  * `GET /api/subreports` — list detected drill-through children
  * `POST /api/subreport/<child>/upload|clear|build` — manage a child report
  * `GET /api/ai/status`, `GET /api/ai/test`, `POST /api/auto-fix`,
    `POST /api/apply-fix` — optional Claude assist
  * `GET /api/recent/clear` — reset the in-memory recent-conversion cache
  * `GET /api/health` — health/sample-list probe

### 3.2 `backend/converter/__init__.py` — Pipeline glue

`convert(xml_bytes, target_db="oracle") -> dict` is the single public entry
point used by the Flask layer. It runs the parser, translator, generator,
mockup renderer, T-SQL validator, RDL structural validator, pre-flight
auditor, bursting detector, sub-report detector, and deployment checklist
builder, and returns a JSON-ready payload.

### 3.3 `backend/converter/models.py` — Shared model

Defines the dataclasses every module reads/writes:

| Type             | Role                                                        |
|------------------|-------------------------------------------------------------|
| `ReportParameter`| One Oracle `userParameter` (name, datatype, label, default) |
| `DataItem`       | One column emitted by an Oracle dataSource                  |
| `DataQuery`      | One Oracle dataSource (sql + tsql + items + notes)          |
| `FormulaColumn`  | One Oracle `CF_*_F` PL/SQL formula                          |
| `LayoutField`    | A printed/static field on the layout                        |
| `LayoutGroup`    | A repeating frame + nested children                         |
| `TriggerCode`    | A trigger body, exposed for the side-by-side view           |
| `ParsedReport`   | The top-level container, also `to_dict()` for JSON          |

### 3.4 `backend/converter/parsers/oracle_xml.py`

* Public API: `parse_oracle_xml(xml_bytes: bytes) -> ParsedReport`
* Defensive XML walk over the Oracle Reports DTD 9.0.x layout.
* Decodes raw bytes against utf-8 / windows-1252 / latin-1 with fallbacks.
* Records unparseable nodes as `report.warnings` rather than raising.
* Namespace-agnostic: works on artifacts saved with or without an
  `xmlns="http://xmlns.oracle.com/oracle/reports/..."` declaration.
* Pulls: `userParameters`, `dataSource` (queries + items), `formula` columns,
  `layoutSection` (groups + repeating frames + fields), `programUnits`
  (triggers).

`parsers/oracle_colors.py` is a companion helper that extracts the color
palette from the layout's `<bgColor>` / `<fgColor>` attributes for the
dynamic-palette feature in the mockup renderer.

### 3.5 `backend/converter/translators/` — two translators

There are two independent translators with distinct jobs.

**`plsql_to_tsql.py` — SQL dialect translator** (rule-based, regex-driven).

* Public API: `translate_report(report: ParsedReport) -> None` (mutates in
  place; called from `converter/__init__.py`).
* Rewrites each `DataQuery.sql` (Oracle SQL) into `DataQuery.tsql` (T-SQL).
  Translation rule highlights are in section 5.
* Every non-trivial rewrite emits a warning into `q.notes`, surfaced to the
  UI so reviewers can audit risky rewrites.

**`plsql_formula.py` — PL/SQL formula compiler** (the core feature; a real
tokenizer + precedence-climbing parser, not pattern substitution).

* Public API: `translate_formula_to_vb(plsql_body, ...)` and
  `translate_expr(...)` (consumed by `generators/rdl.py`).
* Compiles an Oracle `CF_*` / `CP_*` formula body to an SSRS VB.NET
  expression that **computes inline** — `||` becomes `&`,
  `NVL`/`NVL2`/`COALESCE` become `IIf(IsNothing(...))`, `DECODE` and searched
  `CASE`/`IF` become nested `IIf`, and the common string/number/date built-ins
  (`SUBSTR`→`Mid`, `INSTR`→`InStr`, `TO_CHAR`→`Format`, `SYSDATE`→`Now()`,
  etc.) map to their VB.NET equivalents.
* **Honest fallback.** A formula is reported as fully translated only when the
  *whole* expression compiled with no unknown calls; an external package
  function leaves the formula `unresolved` so the generator keeps a safe
  placeholder rather than emitting a broken expression.

**`udf_stubs.py`** ships the `dbo.fn_*` SQL Server scalar-function stub
signatures that mirror the Oracle PL/SQL package functions reports rely on;
the SQL translator rewrites each call site to the matching `dbo.fn_*`.

### 3.6 `backend/converter/generators/rdl.py`

* Public API: `generate_rdl(report: ParsedReport, target_db: str = "oracle") -> str`
* Emits a 2008+ RDL document under the
  `http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition`
  namespace + `rd:` designer namespace.
* **Body shape detection.** The generator inspects the parsed layout and
  picks a body shape:
  * **Per-record (letter / certificate / single-record form)** — a
    free-form arrangement of absolutely-positioned fields, typically one
    page per source row. Implemented by `_build_per_record_body`.
  * **Tabular** — grid-style list/register, often with grouping and
    subtotals. The tabular paths are selected by `_grouped_tabular_spec`
    and `_is_flat_tabular_list_rdl` and built by
    `_build_grouped_tabular_subtotal_tablix` and `_build_grouped_card_tablix`.
    Emits a multi-row `<Tablix>` with `<TablixRowHierarchy>` groups per
    Oracle repeating frame.
* **Formula columns.** `CF_*` / `CP_*` formulas are compiled to inline
  SSRS VB.NET expressions via `translators/plsql_formula.py`; an
  unresolvable formula is left as a safe placeholder.
* **DataSource shape.** Emitted as `<DataSourceReference>SharedDataSource</...>`
  rather than an embedded `<ConnectionProperties>` block. See
  `CONVERSION_NOTES.md` for why — it is load-bearing for SSRS refresh
  behavior.
* **Compatibility post-pass.** `_make_ssrs_oracle_compatible` runs over the
  CommandText to add date-bind wrapping, alias unaliased SELECT items, and
  auto-declare undeclared binds; the generator also sets
  `AllowBlank`/`Nullable` and strips empty required-children containers so
  SSRS does not reject the upload.
* **Post-process.** `rdl_postprocess.py` exposes helpers
  (`set_datasource_reference`, `inject_connection_string`) the deployment
  step uses to point the shared-data-source reference at the user's real
  server path or, optionally, embed a connection string.

### 3.7 `backend/converter/preview/html_mockup.py`

* Public API: `render_mockup(report, mode="frontend") -> str`
* Renders an HTML mock-up of the layout that mirrors the RDL's body shape
  (per-record or tabular) — *not* an RDL preview, but a quick "does the
  structure look right" check.
* Locked by SHA contract for golden-image regression tests.

`preview/mockup_variants.py` provides `render_mockup_print` and
`render_mockup_compact` for the print-friendly and compact mockup variants
served by `GET /api/mockup/<variant>`.

### 3.8 `backend/converter/preview/live_data.py`

* Public API: `run_query(sql, parameters) -> (rows, columns, warnings)`
* Translates `@P_FOO` bind params to SQLite-style `:P_FOO`, lightly rewrites
  T-SQL constructs to SQLite syntax, registers Python UDFs to mirror the
  `dbo.fn_*` package stubs, and runs the query read-only against
  `backend/db/sample.sqlite`.

### 3.9 `backend/converter/validators/`

Three independent validators, all returning issues in the shape
`{severity, line, col, message, rule, scope, excerpt}`:

* `tsql_check.py` — pure-Python lexical/structural T-SQL checks. Catches
  untranslated Oracle constructs, unbalanced parens, missing/unbound
  report parameters.
* `rdl_check.py` — structural validation of the emitted RDL.
  Verifies required elements present, field references resolve to
  declared `<Field>`s, parameter references resolve to declared
  `<ReportParameter>`s, Tablix groupings reference a real dataset.
* `preflight.py` — `preflight_audit(rdl_xml)` runs a final RDL audit
  immediately before download, catching anything the per-stage validators
  missed (orphan field refs, empty required containers, stray bind syntax).

Beyond these runtime validators, the test suite validates generated RDL
against **Microsoft's own RDL 2008 XSD** (`ReportDefinition_2008.xsd`,
loaded with `lxml.etree.XMLSchema` in `tests/test_rdl_schema_xsd.py` and
several other test modules). This is true schema validation, not a
hand-rolled approximation — a generated RDL that fails the Microsoft schema
fails the test suite.

### 3.10 `backend/converter/bursting.py`

Detects per-recipient distribution patterns (`P_AS_PATH`-style parameters,
file-template formula columns, `distribution.xml` payloads) and produces:

* A `burst_key_field` — the dataset column that uniquely identifies one
  recipient. Derived **name-agnostically** by walking the file template's
  source columns; falls back to the first data item on the burst dataset.
* A `filename_pattern` — `<BurstKey>.pdf` by default, or the literal
  template from the source formula if present.
* A T-SQL recipient query stub returning one row per recipient, with a
  placeholder `Email` column.
* A PowerShell DDS-emulator script that loops the recipient query, renders
  the report bound to each burst key, and emails the rendered PDF. For
  SSRS Standard installations that lack native Data-Driven Subscriptions.

The downloadable Burst Pack zip bundles all of the above plus a README.

### 3.11 `backend/converter/subreports.py`

Detects drill-through child reports referenced from the parent (via
`srw.run_report`, `&P_REPORT_NAME` lexical refs, or layout `<reference>`
nodes) and composes a child RDL for each. The Sub-Reports tab in the UI
lets the user upload child Oracle XML/SQL/DOCX, then calls
`compose_subreport_rdl` to generate the child RDL using the same pipeline
as the parent.

### 3.12 `backend/converter/cross_validate.py`

`cross_validate(report, supporting, ...)` cross-checks the parsed Oracle
report against any supporting artifacts the user dropped in:

* `.sql` files — confirms bind names and column references match.
* `.docx` walkthroughs — extracts SQL blocks and compares.
* PDF reference outputs — sanity-checks expected row counts.
* PNG/JPG screenshots — logs as evidence in the audit trail.

### 3.13 `backend/converter/deployment.py`

`build_checklist(report, rdl_xml, validation_issues) -> list[dict]` produces
an ordered, status-tagged checklist (`auto` / `todo` / `manual` / `caution`)
describing what the user must do to take the downloaded `.rdl` from local
file to running on a real SSRS server.

### 3.14 `backend/converter/ingest.py`

* Public API:
  * `classify_files(files) -> dict`
  * `convert_bundle(files) -> dict`
* Lets the user drop a whole folder (Oracle XML + raw `.sql` files + `.docx`
  walkthroughs + `.rdf` binaries + PNG/JPG screenshots). Auto-classifies
  each blob and runs whatever subset of the pipeline is feasible.

### 3.15 `backend/converter/audit.py`, `bundle_export.py`, `cache.py`, `compare.py`, `artifact_enrich.py`

* `audit.py` — structured per-rewrite audit trail (input snippet, output
  snippet, rule name, severity, source location).
* `bundle_export.py` — builds the full conversion bundle zip (RDL,
  validation report, deploy checklist, audit trail, AI prompts, burst
  pack, README).
* `cache.py` — SHA-256 memoize layer so re-running on identical bytes is
  instant.
* `compare.py` — two-report diff + complexity delta for the Compare modal.
* `artifact_enrich.py` — enriches the parsed report with hints derived
  from supporting artifacts.

### 3.16 `backend/converter/ai_assist.py`, `ai_apply.py`, `ai_runner.py`

* `ai_assist.py` — builds paste-into-LLM prompt templates for tricky
  PL/SQL the deterministic translator can't handle.
* `ai_apply.py` — applies one pasted UDF body back into the RDL.
* `ai_runner.py` — when `ANTHROPIC_API_KEY` is set, calls Claude directly
  for every prompt, validates each result (no `DROP`/`EXEC`/etc.), and
  patches the RDL in place.

### 3.17 `backend/converter/rdl_postprocess.py`

Post-processing helpers that run on the generated RDL string:

* `set_datasource_reference(rdl_xml, path)` — rewrites the
  `<DataSourceReference>` target to the caller's real shared-data-source
  path (e.g. `/Data Sources/Oracle_Prod`). When the path matches an
  existing shared data source, SSRS binds it at upload, so the user never
  has to repoint the data source by hand.
* `inject_connection_string(rdl_xml, conn_str)` — alternative path that
  switches the data source to an embedded connection string.

The shared-reference shape itself (and *why* it is load-bearing) is
documented in `CONVERSION_NOTES.md`.

### 3.18 `backend/converter/fidelity.py`

`fidelity.py` is the converter's self-check: it parses the **generated**
RDL back and compares it to the parsed Oracle source, scoring how faithful
the copy is (1.0 = no silently dropped column or parameter) and listing
exactly what was preserved and what still needs manual wiring. Where the
XSD/preflight gates answer "will it upload?", this answers "is it a 1:1
copy?". Generic and structural — no per-report logic. Surfaced in the UI's
Extras card.

### 3.19 `backend/converter/batch.py` and `licensing.py`

* `batch.py` — converts many Oracle reports in one pass and produces a
  **Migration Assessment**: a per-report verdict table (upload-readiness,
  fidelity score, effort tier, concrete reasons) plus an executive summary,
  and a zip of every generated RDL. Effort tiers (`automatic` /
  `light-touch` / `assisted` / `manual`) are derived deterministically from
  the converter's own preflight and fidelity signals. When RenderLab is
  available, each RDL is also rendered through Microsoft's engine and the
  page-count / blank-page verdict is stamped into the assessment.
* `licensing.py` — an edition seam (`O2S_LICENSE` → Community / Pro /
  Enterprise) that gates only volume features (batch size) and assessment
  branding. The converter core is fully functional in every edition and
  single-report conversion is always unlimited; there is no phone-home.

### 3.20 `backend/db/seed_sample_db.py` and `backend/db/sample.sqlite`

Bundled SQLite sample DB seeded with synthetic data so the Live Data tab
returns real rows with zero database setup.

### 3.21 `tools/renderlab/` — Microsoft-engine render verification

A standalone harness (not in the request path) that renders a generated
RDL through **Microsoft's ReportViewer engine** to a real PDF and measures
it (page count, blank pages, width overflow), so layout claims are verified
numerically rather than asserted. It has two render paths: `RenderLab.exe`
(a `LocalReport` host that JIT-compiles RDL expressions) and, when that host
is blocked by an OS Application Control policy, a signed-DLL PowerShell
fallback (`render_rdl.ps1`) that staticizes expressions and still drives the
real engine. See `tools/renderlab/README.md`.

### 3.22 `frontend/templates/index.html` + `frontend/static/`

Single-page, vanilla-JS frontend. The UI surfaces **four main tabs** by
default (HTML Mockup, RDL XML, Bursting, Sub-Reports) with the other
tabs (Side-by-Side, Live Data, Validation, Deploy Checklist, Extras)
hidden behind an **Advanced views** toggle. No build step.

---

## 4. Shared model — ParsedReport contract

The full JSON shape produced by `ParsedReport.to_dict()` is documented in
`docs/API.md` section "JSON contract for ParsedReport". Every module in the
pipeline treats this dataclass as the bus — when a translator wants to
emit a warning it appends to `q.notes`; when a generator wants the raw
Oracle XML it reads `report.raw_xml`. Nothing else is shared.

This means:
* You can swap the parser implementation as long as it returns a `ParsedReport`.
* You can add new generators (CSV, Power BI, Crystal) without touching the
  parser or the translator.
* You can add new translators (an LLM-assisted one, a DB2 target, etc.) by
  consuming the same `DataQuery.sql` and writing back into `q.tsql`.

---

## 5. Translation rules table

(Selected highlights — see `translators/plsql_to_tsql.py` for the full set.)

| Oracle / PL-SQL                       | T-SQL emitted                          | Warning?  |
|---------------------------------------|----------------------------------------|-----------|
| `NVL(x, y)`                           | `ISNULL(x, y)`                         | no        |
| `NVL2(x, y, z)`                       | `CASE WHEN x IS NOT NULL THEN y ELSE z END` | no   |
| `DECODE(a, b, c, d, e, f)`            | `CASE a WHEN b THEN c WHEN d THEN e ELSE f END` | no |
| `SUBSTR(s, m, n)`                     | `SUBSTRING(s, m, n)`                   | no        |
| `INSTR(s, sub)`                       | `CHARINDEX(sub, s)`                    | no        |
| `TO_CHAR(d, 'MM/DD/YYYY')`            | `CONVERT(VARCHAR, d, 101)`             | yes       |
| `TO_CHAR(d, 'YYYY-MM-DD')`            | `CONVERT(VARCHAR, d, 23)`              | yes       |
| `TO_DATE(s, fmt)`                     | `TRY_CONVERT(DATETIME, s)`             | yes       |
| `SYSDATE`                             | `GETDATE()`                            | no        |
| `||` (string concat)                  | `+`                                    | no        |
| `:P_FOO` bind                         | `@P_FOO`                               | no        |
| `&LEX_FOO` lexical                    | (left in place + warning)              | YES       |
| `Pkg_X.fn_Y(...)`                     | `dbo.fn_Y(...)`                        | yes       |
| `ROWNUM`                              | `TOP n` / `ROW_NUMBER() OVER (...)`    | yes       |
| `(+)` outer join                      | `LEFT OUTER JOIN ... ON ...`           | yes       |
| `CONNECT BY`                          | (CTE skeleton + warning)               | YES       |
| `MINUS`                               | `EXCEPT`                               | no        |
| `LISTAGG`                             | `STRING_AGG`                           | yes       |
| `DUAL`                                | (removed)                              | no        |

Warnings are written to `DataQuery.notes` and surfaced to the UI's
"Translation Notes" column.

---

## 6. RDL generation strategy

1. **Skeleton.** Emit `<Report>` with `<DataSources>`, `<DataSets>`,
   `<ReportParameters>`, `<ReportSections>` / `<Body>`, and the standard
   `<Page>` block.
2. **DataSource.** A single `<DataSource Name="SharedDataSource">` is
   emitted as a `<DataSourceReference>` — see `CONVERSION_NOTES.md` for
   the rationale.
3. **DataSets.** One `<DataSet>` per `DataQuery`. The `<CommandText>` is
   the translated T-SQL. `<Fields>` mirror `DataItem`s with the right
   `<DataType>` (`System.Int32`, `System.DateTime`, `System.String`).
4. **ReportParameters.** One `<ReportParameter>` per `ReportParameter`.
   Datatype mapping: character to String, number to Integer, date to
   DateTime. Internal parameters (`display=False`) get `<Hidden>true</Hidden>`.
   Every String gets `<AllowBlank>true</AllowBlank>`; every non-String gets
   `<Nullable>true</Nullable>`.
5. **Body — per-record vs tabular.** The generator picks one of two body
   shapes based on layout analysis (see 3.6):
   * Per-record: single-row Tablix wrapping a Rectangle of absolutely-
     positioned Textboxes.
   * Tabular: multi-row Tablix with TablixRowHierarchy groups for each
     Oracle repeating frame.
6. **Page numbering.** A textbox in the page footer with
   `=Globals!PageNumber & " of " & Globals!TotalPages`.
7. **Compatibility post-pass.** `_make_ssrs_oracle_compatible` adds
   `TO_DATE` wrapping for date binds, aliases unaliased SELECT items,
   auto-declares undeclared binds, and strips empty required-children
   containers so SSRS does not reject the upload.

The RDL is **structurally valid** — it opens in Report Builder without
parse errors. Pixel-precise placement is a stretch goal; what we guarantee
is that the parameters, datasets, fields, and tablix structure are right
so the user only has to nudge layout, not rebuild from scratch.

---

## 7. Live preview engine (SQLite + UDFs)

The "Live Data" tab is a reviewer-confidence multiplier — reviewers don't
trust generated SQL until they see rows come back.

### Why SQLite and not a real SQL Server?

* Zero installation footprint — the demo runs on any laptop with Python.
* Read-only via `mode=ro` URI, so demos can't accidentally mutate data.
* The translation gap between T-SQL and SQLite is small enough to bridge
  with a few rewrites + UDFs.

### Bridge layer (`preview/live_data.py`)

* `@P_FOO` bind parameters are rewritten to SQLite's `:P_FOO`.
* A handful of T-SQL constructs (`ISNULL`, `GETDATE()`, `TOP n`, `+` for
  string concat where unambiguous) are mapped to SQLite equivalents.
* Python UDFs registered on the connection mirror SQL Server scalar
  functions and the `dbo.fn_*` package stubs.
* `run_query()` returns `(rows, columns, warnings)` so the UI can flag
  rewrites the bridge had to apply.

---

## 8. Validation layers

See module summaries in 3.9. All three validators emit issues in the same
shape, so the UI renders them in a single table sorted by severity.

---

## 9. Bursting and drill-through

See module summaries in 3.10 (bursting) and 3.11 (sub-reports). These two
features are surfaced as top-level tabs in the UI so they are visible at
first glance, not hidden behind an Advanced toggle — they are common
enough in legacy Oracle deployments that they deserve front-row treatment.

---

## 10. Audit trail

Every transformation emits a structured audit entry: input snippet, output
snippet, rule name, severity, source location (line/col in the original
Oracle XML when known). The trail is shipped as `report.audit_trail` in
the JSON payload and rendered as a sortable, filterable table in the UI's
Extras tab. This is the artifact a compliance reviewer signs off on
before the migrated report goes live.

---

## 11. Extension points

Because every module reads and writes the same `ParsedReport`, the pipeline
is extended by adding a function, not by editing existing modules.

* **AI-assist module.** `ai_assist.py` builds a paste-into-LLM prompt for
  PL/SQL the deterministic translator could not handle. `ai_runner.py` does
  the same automatically when `ANTHROPIC_API_KEY` is set: it calls Claude,
  validates each result (rejecting `DROP`/`EXEC`/etc.), and patches the RDL
  in place.
* **Custom UDF stubs.** `translators/udf_stubs.py` keeps its known mappings
  in the `_KNOWN_STUBS` dict. Adding a new `dbo.fn_*` mapping is a
  dict-entry edit.
* **Custom translation rules.** `translators/plsql_to_tsql.py` is a single
  rule-driven module; new Oracle→T-SQL rewrites are added alongside the
  existing ones (see the rule highlights in section 5).
* **Custom generators.** Implement `generate_<format>(report) -> str`
  against the same `ParsedReport` and wire it into `converter/__init__.py`.
* **Custom validators.** Implement `validate_<thing>(report) -> list[dict]`
  with the standard issue shape; the UI renders any number of validators
  side-by-side.

---

## 12. Known limitations

* **Lexical references (`&LEX_FOO`).** Oracle's lexical-reference feature
  splices arbitrary text into the SQL at runtime. There is no general SSRS
  equivalent (closest is dynamic SQL via expressions). The translator
  leaves them in place, the validator flags them as errors, and the
  deployment checklist documents how to convert them to RDL expressions
  manually.
* **Pixel-precise layout.** Oracle Reports stores layout in inches with
  per-frame anchors and complex spacing rules. We translate position,
  font weight, and font size, but final layout polish in Report Builder
  is expected.
* **PL/SQL package functions.** Calls like `Pkg_Foo.fn_Bar` are rewritten
  to `dbo.fn_Bar` and a stub is suggested, but the body of the function
  must be ported by hand. The audit trail flags every package call so
  nothing is missed.
* **CONNECT BY (recursive queries).** Translated to a CTE skeleton with
  a warning; the user fills in the recursive anchor.
* **`.rdf` binary format.** Only the XML serialization is fully supported.
  `.rdf` files are accepted (we look for an embedded XML payload), but the
  binary layout/font tables are not parsed.
* **No SSRS server round-trip.** We don't deploy the RDL — we generate it
  and hand the user a checklist. By design: we don't want to require
  network access or credentials in a free, offline tool.
