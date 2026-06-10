# Oracle2SSRS — HTTP API Reference

All endpoints live under the Flask app in `backend/app.py` and listen on
`http://127.0.0.1:5057` by default (override with the `PORT` env var).

* All responses are JSON unless explicitly noted (the `.rdl` download
  returns `application/xml`, the bundle and burst-pack downloads return
  `application/zip`).
* The server keeps the **most recent successful conversion** in a
  process-local cache so `GET /api/download/rdl` and
  `GET /api/download/bundle` work without re-running the pipeline.
  Restarting the process clears the cache.

---

## Endpoint summary

| Method | Path                                       | Purpose                                                       |
|--------|--------------------------------------------|---------------------------------------------------------------|
| GET    | `/`                                        | Render the single-page UI                                     |
| POST   | `/api/convert`                             | Convert one uploaded `.xml` / `.rdf`                          |
| POST   | `/api/convert-bundle`                      | Convert a multi-file folder upload                            |
| POST   | `/api/convert-sample/<name>`               | Convert one of the bundled sample reports                     |
| POST   | `/api/compare`                             | Structured diff between two Oracle reports                    |
| POST   | `/api/run-query`                           | Execute translated T-SQL against the bundled sample SQLite DB |
| GET    | `/api/download/rdl`                        | Download the most recent generated RDL                        |
| GET    | `/api/download/bundle`                     | Download the full conversion bundle (zip)                     |
| GET    | `/api/mockup/<variant>`                    | Print or compact mockup variant                               |
| POST   | `/api/burst-preview`                       | Preview the per-recipient burst query                         |
| POST   | `/api/download/burst-pack`                 | Download the Burst Pack zip                                   |
| GET    | `/api/subreports`                          | List detected drill-through child reports                     |
| POST   | `/api/subreport/<child>/upload`            | Attach artifacts to a child report                            |
| POST   | `/api/subreport/<child>/clear`             | Clear attached artifacts on a child report                    |
| POST   | `/api/subreport/<child>/build`             | Build the RDL for a child report                              |
| GET    | `/api/ai/status`                           | Is the optional Claude assist configured?                     |
| GET    | `/api/ai/test`                             | Smoke-test the configured Anthropic API key                   |
| POST   | `/api/auto-fix`                            | Run Claude on every prompt, apply each                        |
| POST   | `/api/apply-fix`                           | Apply one pasted UDF body back into the RDL                   |
| GET    | `/api/recent/clear`                        | Clear the in-memory recent-conversion cache                   |
| GET    | `/api/health`                              | Health probe + list of bundled samples                        |

---

## Error format

Every endpoint that can fail returns the same shape:

```json
{
  "error": "human-readable message",
  "trace": "full Python traceback as a string (debug only)"
}
```

with HTTP status `400` for client errors (missing file, malformed payload)
and `500` for everything else. The `trace` field is only populated when
`FLASK_DEBUG=1`, but is always present on the exception path because the
backend runs in `debug=True` for development.

---

## `GET /`

Renders `frontend/templates/index.html`. The template receives a
`samples` list (file names from `samples/oracle/*.xml`) so the dropdown
can be populated server-side.

No JSON. Returns `text/html`.

---

## `POST /api/convert`

Convert a single uploaded Oracle Reports artifact.

**Request** (`multipart/form-data`)

| Field | Type | Required | Notes                                       |
|-------|------|----------|---------------------------------------------|
| file  | file | yes      | A `.xml` or `.rdf` Oracle Reports artifact. |

**Curl**

```bash
curl -X POST -F "file=@samples/oracle/SAMPLE_INSPECTION.xml" \
     http://127.0.0.1:5057/api/convert
```

**Response 200** — the standard conversion payload (see
"Conversion payload" below).

**Response 400** — `{"error": "no file uploaded"}` if the `file` field is
missing.

**Response 500** — `{"error": "...", "trace": "..."}` for any pipeline
exception.

---

## `POST /api/convert-bundle`

Convert a heterogeneous folder upload. Pass any number of files under the
form field `files`; the backend uses `converter/ingest.py` to classify
each by content (`primary_xml`, `rdf_binary`, `sql_files`, `docs`,
`screenshots`, `unknown`) and runs whatever subset of the pipeline is
feasible.

**Request** (`multipart/form-data`)

| Field | Type            | Required | Notes                              |
|-------|-----------------|----------|------------------------------------|
| files | file (multiple) | yes      | Repeat the field per file uploaded |

**Curl**

```bash
curl -X POST \
  -F "files=@samples/oracle/SAMPLE_INSPECTION.xml" \
  -F "files=@notes/queries.docx" \
  -F "files=@notes/screenshots.docx" \
  http://127.0.0.1:5057/api/convert-bundle
```

**Response 200** — the standard conversion payload **plus** an
`ingest_report` key listing which files fell into which classification
bucket. When no Oracle XML is present, `report` is omitted and
`ingest_report` explains what was found so the UI can guide the user.

**Response 400** — `{"error": "no files uploaded"}`.

---

## `POST /api/convert-sample/<name>`

Run the converter against one of the bundled samples (anything in
`samples/oracle/*.xml`). The `<name>` segment is validated against the
samples dir to prevent path traversal.

**Curl**

```bash
curl -X POST http://127.0.0.1:5057/api/convert-sample/SAMPLE_INSPECTION.xml
```

**Response 200** — same conversion payload as `/api/convert`.

**Response 404** — sample not found.

---

## `POST /api/compare`

Structured diff between two uploaded Oracle Reports artifacts. Returns
parameter / dataset / formula / layout deltas plus a complexity-score
delta.

**Request** (`multipart/form-data`)

| Field | Type | Required | Notes                  |
|-------|------|----------|------------------------|
| a     | file | yes      | First report (Oracle). |
| b     | file | yes      | Second report.         |

**Response 200** — JSON with `diff` and `complexity` keys (see
`converter/compare.py` for the exact shape).

---

## `GET /api/download/rdl`

Streams the most recently generated RDL as a downloadable file.

* Filename: `<report.name>.rdl` (falls back to `report.rdl`).
* MIME: `application/xml`.
* Source: the in-memory `_LAST` cache, populated by the most recent
  successful `POST /api/convert*` call.

**Response 404** — no RDL has been generated yet in this process.

---

## `GET /api/download/bundle`

Streams a `.zip` containing the most recent RDL plus the full conversion
artifacts: validation report, deploy checklist, audit trail, AI prompts,
Burst Pack (if bursting was detected), and a README explaining what's
inside.

* MIME: `application/zip`.
* Filename: `<report.name>_bundle.zip` (falls back to `report_bundle.zip`).

---

## `GET /api/mockup/<variant>`

Returns a standalone HTML mockup variant. `<variant>` is one of:

* `print` — print-friendly black-and-white version with page breaks.
* `compact` — compact one-page summary view.

MIME: `text/html`.

---

## `POST /api/burst-preview`

Returns a preview of the per-recipient burst recipient query (the T-SQL
that would be embedded in an SSRS Data-Driven Subscription).

**Request** (`application/json`)

```json
{ "limit": 50 }
```

**Response 200**

```json
{
  "burst_key_field": "Burst_Key",
  "filename_pattern": "<Burst_Key>.pdf",
  "recipient_sql":    "SELECT ... AS Burst_Key, ... AS Email ...",
  "rows":             [["KEY-001", "alice@example.com"], ...],
  "columns":          ["Burst_Key", "Email"],
  "warnings":         ["..."]
}
```

---

## `POST /api/download/burst-pack`

Streams the Burst Pack `.zip` (recipient SQL + PowerShell DDS-emulator
script + README) for the current conversion.

MIME: `application/zip`.

---

## `GET /api/subreports`

Lists drill-through child reports detected from the parent report's
formulas and layout references.

**Response 200**

```json
{
  "children": [
    {
      "name":           "CHILD_REPORT_A",
      "param_mapping":  {"P_KEY": "Fields!Key.Value"},
      "artifacts":      ["CHILD_REPORT_A.xml"],
      "ready":          true
    }
  ]
}
```

---

## `POST /api/subreport/<child>/upload`

Attach Oracle XML / SQL / DOCX artifacts to a child report. Multipart
form upload identical in shape to `/api/convert-bundle`.

## `POST /api/subreport/<child>/clear`

Clear all artifacts attached to a child report.

## `POST /api/subreport/<child>/build`

Run the converter pipeline against the artifacts attached to a child
report and return the conversion payload for that child.

---

## `POST /api/run-query`

Execute translated T-SQL against `backend/db/sample.sqlite`. The bridge
layer in `preview/live_data.py` rewrites a few T-SQL constructs for
SQLite and registers Python UDFs for the `dbo.fn_*` package stubs.

**Request** (`application/json`)

```json
{
  "sql":        "SELECT TOP 10 ITEM_NO FROM ITEM WHERE YEAR = @P_YEAR",
  "parameters": {"P_YEAR": 2024}
}
```

| Field      | Type   | Required | Notes                                            |
|------------|--------|----------|--------------------------------------------------|
| sql        | string | yes      | Translated T-SQL; `@P_FOO` binds are supported.  |
| parameters | object | no       | Map of bind name (without `@`) to value.         |

**Response 200**

```json
{
  "rows":     [["I-001", "Acme Inc."], ["I-002", "Beta LLC"]],
  "columns":  ["ITEM_NO", "ENTITY_NAME"],
  "warnings": ["GETDATE() rewritten to CURRENT_TIMESTAMP for SQLite"]
}
```

`rows` is row-major; values are JSON primitives (strings, numbers, null).

**Response 500** — `{"error": "...", "trace": "..."}` on any SQL error.

---

## `GET /api/ai/status`

Reports whether the optional Claude assist is configured.

**Response 200**

```json
{ "configured": true, "model": "claude-..." }
```

`configured` is `true` when `ANTHROPIC_API_KEY` is set in the process
environment (or loaded from `.env` at startup).

## `GET /api/ai/test`

Sends a trivial probe to the Anthropic API to confirm the key is valid.

## `POST /api/auto-fix`

Iterates every AI prompt in the current conversion, calls Claude once
per prompt, validates the response, and patches the RDL in place.

## `POST /api/apply-fix`

Applies one manually-pasted UDF body back into the RDL.

---

## `GET /api/recent/clear`

Clears the in-memory recent-conversion cache. Useful in development when
you want to reset state without restarting the process.

---

## `GET /api/health`

Cheap probe used by the UI on page load.

**Response 200**

```json
{
  "ok":      true,
  "samples": ["SAMPLE_INSPECTION.xml"]
}
```

---

## Conversion payload

The shape returned by every successful `/api/convert*` call:

```json
{
  "report":              { ParsedReport.to_dict() },
  "rdl_xml":             "<Report ...>...</Report>",
  "oracle_xml":          "<?xml version=\"1.0\"?>...",
  "mockup_html":         "<!doctype html>...",
  "validation_issues":   [ ValidationIssue, ... ],
  "deployment_checklist":[ ChecklistItem,    ... ],
  "bursting":            { ... },
  "subreports":          { ... },
  "audit_trail":         [ AuditEntry, ... ],
  "ai_prompts":          [ ... ]
}
```

### `validation_issues[]` — `ValidationIssue`

```json
{
  "severity": "error",
  "line":     12,
  "col":      8,
  "message":  "Outer-join (+) syntax not supported in T-SQL",
  "rule":     "OUTER_JOIN_PLUS",
  "scope":    "Q_MAIN",
  "excerpt":  "WHERE p.fac_id = f.id (+)"
}
```

`line`, `col`, and `excerpt` may be `null` when the issue is report-wide
rather than at a specific source location.

### `deployment_checklist[]` — `ChecklistItem`

```json
{
  "step":   "Point the DataSource at your target SQL Server",
  "status": "todo",
  "detail": "Open the .rdl in Report Builder, edit the shared DataSource reference, point it at your shared DS."
}
```

`status` is one of `auto` (already done), `todo` (user must perform),
`manual` (UI work the converter cannot automate), `caution` (a known
footgun the user should read carefully).

---

## JSON contract for `ParsedReport`

This is the `report` key in the conversion payload. Top-level shape:

```json
{
  "name":        "SAMPLE_INSPECTION",
  "dtd_version": "9.0.4.0.33",
  "parameters":  [ ReportParameter, ... ],
  "queries":     [ DataQuery,       ... ],
  "formulas":    [ FormulaColumn,   ... ],
  "layout":      [ LayoutGroup,     ... ],
  "triggers":    [ TriggerCode,     ... ],
  "warnings":    [ "string", ...    ]
}
```

### `ReportParameter`

```json
{
  "name":          "P_YEAR",
  "label":         "Year",
  "datatype":      "number",
  "width":         4,
  "precision":     0,
  "initial_value": "2024",
  "input_mask":    null,
  "display":       true
}
```

* `datatype` is the original Oracle datatype (`character`, `number`, `date`).
* The SSRS datatype is derived in code via the `ssrs_datatype` property on
  the dataclass: character to `String`, number to `Integer`,
  date to `DateTime`.
* `display: false` indicates an internal-only parameter, which the RDL
  generator marks `<Hidden>true</Hidden>`.

### `DataQuery`

```json
{
  "name":  "Q_MAIN",
  "sql":   "SELECT id, name FROM item WHERE year = :P_YEAR",
  "tsql":  "SELECT id, name FROM item WHERE year = @P_YEAR",
  "items": [ DataItem, ... ],
  "notes": [ "Replaced :P_YEAR with @P_YEAR", ... ]
}
```

* `sql` is the original Oracle SQL as parsed.
* `tsql` is the translator's output. Empty before the translator runs.
* `notes` is the per-query translation audit log.

### `DataItem`

```json
{
  "name":       "Item",
  "expression": "i.item_no",
  "datatype":   "vchar2",
  "width":      30,
  "label":      "Item #"
}
```

### `FormulaColumn`

```json
{
  "name":        "CF_File",
  "return_type": "VARCHAR2",
  "plsql_body":  "RETURN :item_no || '-' || :year;",
  "tsql_body":   "RETURN @item_no + '-' + CAST(@year AS VARCHAR);",
  "notes":       [ "Concatenation rewritten || -> +" ]
}
```

### `LayoutGroup`

```json
{
  "name":         "G_MAIN",
  "source_query": "Q_MAIN",
  "fields":       [ LayoutField, ... ],
  "children":     [ LayoutGroup,  ... ]
}
```

`children` recurses, mirroring the Oracle repeating-frame nesting.

### `LayoutField`

```json
{
  "name":      "F_Item",
  "source":    "Item",
  "text":      "",
  "bold":      true,
  "font_size": 10,
  "x":         0.0,
  "y":         0.5,
  "width":     2.0,
  "height":    0.25
}
```

* `source` references either a `DataItem.name` or `FormulaColumn.name`.
* `text` is non-empty for boilerplate (static label) fields.
* `x` / `y` / `width` / `height` are inches.

### `TriggerCode`

```json
{
  "name": "BeforeReport",
  "body": "/* PL/SQL */"
}
```

Triggers are surfaced in the UI's side-by-side view and as audit-trail
entries; they are not auto-translated (most have no SSRS analog).

### `warnings[]`

Top-level parser warnings (`"unparseable <foo> at line 12"`, etc.). Per-query
translation warnings live on the individual `DataQuery.notes`.
