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

* parse the Oracle XML
* translate the embedded Oracle SQL / PL/SQL into T-SQL
* emit a structurally-valid `.rdl`
* render an HTML mock-up of the RDL so a reviewer can eyeball the result
  before opening Report Builder
* run the translated T-SQL against a bundled SQLite sample DB so a
  non-technical reviewer can see live data immediately

What is **left to humans** is intentionally surfaced (lexical references,
package UDF stubs, pixel-precise positioning) rather than silently fudged.

---

## 2. High-level pipeline

```
                +-------------------------+
  .xml / .rdf  |  parsers/oracle_xml.py  |
  (bytes)  --> |  parse_oracle_xml()     |  --> ParsedReport
                +-------------------------+
                              |
                              v
                +---------------------------------+
                | translators/plsql_to_tsql.py    |
                | translate_report(report)        |
                |   (mutates each q.tsql,         |
                |    each formula.tsql_body,      |
                |    appends translation notes)   |
                +---------------------------------+
                              |
                              v
                +-------------------------+        +-----------------------+
                | generators/rdl.py       | -----> | rdl_xml: str          |
                | generate_rdl(report)    |        | (downloadable .rdl)   |
                +-------------------------+        +-----------------------+
                              |
                              v
   +--------------------+   +-------------------+   +----------------------+
   | preview/           |   | validators/       |   | deployment.py        |
   |  html_mockup       |   |  tsql_check       |   |  build_checklist()   |
   |  side_by_side      |   |  rdl_structure    |   |                      |
   |  live_data         |   |                   |   |                      |
   +--------------------+   +-------------------+   +----------------------+
                              |
                              v
                +-------------------------+
                |  Flask app.py           |
                |  JSON payload to UI     |
                +-------------------------+
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
  `/api/download/rdl` endpoint can serve the file without re-running the
  pipeline.
* Endpoints (full reference in `docs/API.md`):
  * `GET /` — render the index page
  * `POST /api/convert` — single-file Oracle XML upload
  * `POST /api/convert-bundle` — multi-file folder upload
  * `POST /api/convert-sample/<name>` — run against a bundled sample
  * `GET /api/download/rdl` — download the most recent RDL
  * `POST /api/run-query` — run translated T-SQL against the sample SQLite DB
  * `GET /api/health` — health/sample-list probe

### 3.2 `backend/converter/__init__.py` — Pipeline glue

`convert(xml_bytes) -> dict` is the single public entry point used by the
Flask layer. It runs the parser, translator, generator, mockup renderer,
T-SQL validator, and deployment checklist builder and returns a JSON-ready
payload.

### 3.3 `backend/converter/models.py` — Shared model

Defines the dataclasses every module reads/writes:

| Type             | Role                                                        |
|------------------|-------------------------------------------------------------|
| `ReportParameter`| One Oracle `userParameter` (name, datatype, label, default) |
| `DataItem`       | One column emitted by an Oracle dataSource                  |
| `DataQuery`      | One Oracle dataSource (sql + tsql + items + notes)          |
| `FormulaColumn`  | One Oracle CF\_\*\_F PL/SQL formula                         |
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

### 3.5 `backend/converter/translators/plsql_to_tsql.py`

* Public API:
  * `translate_report(report: ParsedReport) -> None` (mutates in place)
  * `translate_sql(oracle_sql: str) -> tuple[str, list[str]]`
* Rule-based, regex-driven. Translation rule table is in section 5.
* Every non-trivial rewrite emits a warning into `q.notes`, which is
  surfaced to the UI so reviewers can audit risky rewrites.
* Companion file `translators/udf_stubs.py` ships the `dbo.fn_*` SQL Server
  scalar-function stubs that mirror the Oracle PL/SQL package functions
  most reports rely on (`Pkg_*`, `Utl_URL`, etc.).

### 3.6 `backend/converter/generators/rdl.py`

* Public API: `generate_rdl(report: ParsedReport) -> str`
* Emits a 2008+ RDL document under the
  `http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition`
  namespace + `rd:` designer namespace.
* Strategy described in section 6.

### 3.7 `backend/converter/preview/html_mockup.py`

* Public API: `render_mockup(report) -> str`
* Renders a black-and-white HTML mock-up of the layout — *not* an RDL preview,
  but a quick "does the structure look right" check.

### 3.8 `backend/converter/preview/live_data.py`

* Public API: `run_query(sql, parameters) -> (rows, columns, warnings)`
* Translates `@P_FOO` bind params to SQLite-style `:P_FOO`, lightly rewrites
  T-SQL constructs to SQLite syntax, registers Python UDFs to mirror the
  Agent-2 `dbo.fn_*` package stubs, and runs the query read-only.

### 3.9 `backend/converter/validators/tsql_check.py`

* Public API:
  * `validate_tsql(sql: str) -> list[dict]`
  * `validate_report(report: ParsedReport) -> list[dict]`
* Static, pure-Python lexical checks. No SQL Server connection required.
* Issue shape: `severity`, `line`, `col`, `message`, `rule`, `scope`, `excerpt`.

### 3.10 `backend/converter/deployment.py`

* Public API: `build_checklist(report, rdl_xml, validation_issues) -> list[dict]`
* Produces an ordered, status-tagged checklist (auto / todo / manual / caution)
  describing what the user must do to take the downloaded `.rdl` from local
  file to running on a real SSRS server.

### 3.11 `backend/converter/ingest.py`

* Public API:
  * `classify_files(files) -> dict`
  * `convert_bundle(files) -> dict`
* Lets the user drop a whole folder (Oracle XML + raw `.sql` files + `.docx`
  walkthroughs + `.rdf` binaries + screenshots). Auto-classifies each blob
  and runs whatever subset of the pipeline is feasible.

### 3.12 `backend/db/seed_sample_db.py` and `backend/db/sample.sqlite`

* Bundled SQLite sample DB pre-seeded with the schema referenced by the
  primary sample (SAMPLE\_INSPECTION.xml). Lets the live-data tab return real rows
  with zero database setup.

### 3.13 `frontend/templates/index.html` + `frontend/static/`

* Single-page, vanilla-JS frontend: drag-drop zone + tabbed multi-pane
  preview (Mockup, RDL XML, T-SQL, Side-by-side, Live Data, Validation,
  Checklist). No build step.

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
| `CONNECT BY`                          | (left in place + warning)              | YES       |
| `MINUS`                               | `EXCEPT`                               | no        |
| `DUAL`                                | (removed)                              | no        |

Warnings are written to `DataQuery.notes` and surfaced to the UI's
"Translation Notes" column.

---

## 6. RDL generation strategy

1. **Skeleton.** Emit `<Report>` with `<DataSources>`, `<DataSets>`, `<ReportParameters>`,
   `<ReportSections>` / `<Body>`, and the standard `<Page>` block.
2. **DataSource.** A single shared `<DataSource Name="SampleDB">` is emitted with a
   placeholder connection string. The deployment checklist tells the user how
   to point this at their real SQL Server.
3. **DataSets.** One `<DataSet>` per `DataQuery`. The `<CommandText>` is the
   translated T-SQL. `<Fields>` mirror `DataItem`s with the right
   `<DataType>` (`System.Int32`, `System.DateTime`, `System.String`).
4. **ReportParameters.** One `<ReportParameter>` per `ReportParameter`. Datatype
   mapping: character to String, number to Integer, date to DateTime. Internal
   parameters (`display=False`) get `<Hidden>true</Hidden>`.
5. **Body / Tablix.** Each top-level `LayoutGroup` becomes a `<Tablix>`. Repeating
   frames nest as `<TablixRowHierarchy>` groups when applicable. Static
   `LayoutField`s become `<Textbox>` elements at the recorded x/y/width/height,
   with the right `<FontWeight>` / `<FontSize>` style.
6. **Master/detail.** Nested `LayoutGroup.children` produce nested groups
   bound to the inner dataset, with a `<DataSetName>` swap.
7. **Page numbering.** A textbox in the page footer with
   `=Globals!PageNumber & " of " & Globals!TotalPages`.

The RDL is **structurally valid** — it opens in Report Builder without
parse errors. Pixel-precise placement is a stretch goal; what we guarantee
is that the parameters, datasets, fields, and tablix structure are right
so the user only has to nudge layout, not rebuild from scratch.

---

## 7. Live preview engine (SQLite + UDFs)

The "Live Data" tab is the biggest reviewer-confidence multiplier in the
product. Reviewers don't trust generated SQL until they see rows come back.

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
  functions and the Agent-2 `dbo.fn_*` package stubs:
  `fn_FORMAT_DATE_LONG`, `fn_F_Format_Address`, etc.
* `run_query()` returns `(rows, columns, warnings)` so the UI can flag
  rewrites the bridge had to apply.

### Sample DB (`backend/db/sample.sqlite`)

Seeded by `seed_sample_db.py`. Schema mirrors the tables referenced by
SAMPLE\_INSPECTION.xml (`Permit`, `Site`, `Org`, `Visit`, ...). Data is synthetic.

---

## 8. Validation layers

Two independent validators run after generation:

### 8.1 T-SQL static validator (`validators/tsql_check.py`)

Pure-Python lexical / structural checks. No SQL Server connection. Catches:

* Untranslated Oracle constructs (`(+)`, `CONNECT BY`, `:bind` left over).
* Unbalanced parens / quotes.
* References to objects that won't exist on a fresh SampleDB
  (e.g. `Pkg_X.fn_Y` — caller must port).
* Missing/unbound report parameters.

Each issue is `{severity, line, col, message, rule, scope, excerpt}`.

### 8.2 RDL structural validator (Agent 12, `validators/rdl_structure.py`)

XSD-style structural validation of the emitted RDL. Verifies:

* Required elements present (`Body`, `Page`, `DataSources`, ...).
* Field references inside expressions resolve to a declared `<Field>`.
* Parameter references inside expressions resolve to a declared
  `<ReportParameter>`.
* Tablix groupings reference a real dataset.

Issues use the same shape as the T-SQL validator, so the UI renders both
in a single table.

---

## 9. Bursting / DDS support (Agent 13)

Oracle Reports' "distribution.xml" + bursting features map to SSRS
**Data-Driven Subscriptions** (DDS). Module produces:

* A recipient query (the "burst by" clause) emitted as a separate
  `<DataSet>` in the RDL.
* A SQL stub the user pastes into the SSRS DDS wizard's "Get a list of
  recipients" textbox.
* Per-recipient parameter mapping (which dataset column maps to which
  report parameter).
* Caveats list — DDS requires SSRS Enterprise + a SQL Agent job, which the
  deployment checklist surfaces.

---

## 10. Audit trail (Agent 17)

Every transformation emits a structured audit entry: input snippet, output
snippet, rule name, severity, source location (line/col in the original
Oracle XML when known). The trail is shipped as `report.audit_trail` in
the JSON payload and rendered as a sortable, filterable table in the UI's
"Audit" tab. This is the artifact a compliance reviewer signs off on
before the migrated report goes live.

---

## 11. Extension points

* **Translator plugin registry (Agent 21).** `translators/registry.py`
  exposes `register(rule)` so vendors can drop in custom Oracle to T-SQL
  rewrites without forking the core. Each rule is `(name, pattern,
  rewrite_fn, severity)`.
* **AI-assist module (Agent 11).** `translators/ai_prompt.py` builds a
  prompt for an LLM that pairs the un-translated Oracle SQL with the rules
  table; the response is parsed and merged into the rule pipeline so the
  deterministic translator runs first and the LLM only fills gaps.
* **Custom UDF stubs.** `translators/udf_stubs.py` is a plain Python dict.
  Adding a new `dbo.fn_*` mapping is a one-line edit.
* **Custom generators.** Implement `generate_<format>(report) -> str`
  against the same `ParsedReport`. Wire it into `converter/__init__.py`.
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
