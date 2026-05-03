# Oracle2SSRS — HTTP API Reference

All endpoints live under the Flask app in `backend/app.py` and listen on
`http://127.0.0.1:5057` by default (override with the `PORT` env var).

* All responses are JSON unless explicitly noted (the `.rdl` download
  returns `application/xml`).
* The server keeps the **most recent successful conversion** in a
  process-local cache so `GET /api/download/rdl` works without re-running
  the pipeline. Restarting the process clears the cache.

---

## Endpoint summary

| Method | Path                            | Purpose                                                       |
|--------|---------------------------------|---------------------------------------------------------------|
| GET    | `/`                             | Render the single-page UI                                     |
| POST   | `/api/convert`                  | Convert one uploaded `.xml` / `.rdf`                          |
| POST   | `/api/convert-bundle`           | Convert a multi-file folder upload                            |
| POST   | `/api/convert-sample/<name>`    | Convert one of the bundled sample reports                     |
| GET    | `/api/download/rdl`             | Download the most recent generated RDL                        |
| POST   | `/api/run-query`                | Execute translated T-SQL against the bundled sample SQLite DB |
| GET    | `/api/health`                   | Health probe + list of bundled samples                        |

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
backend runs in `debug=True` for the hackathon demo.

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
curl -X POST -F "file=@samples/oracle/MVWF_PERMIT.xml" \
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
  -F "files=@samples/oracle/MVWF_PERMIT.xml" \
  -F "files=@samples/oracle/MVWF_PERMIT Sql queries.docx" \
  -F "files=@samples/oracle/MVWF_PERMITbackend screenshots.docx" \
  http://127.0.0.1:5057/api/convert-bundle
```

**Response 200** — the standard conversion payload **plus** an
`ingest_report` key:

```json
{
  "report":              { ... },
  "rdl_xml":             "...",
  "oracle_xml":          "...",
  "mockup_html":         "...",
  "validation_issues":   [ ... ],
  "deployment_checklist":[ ... ],
  "ingest_report": {
    "primary_xml":  ["MVWF_PERMIT.xml"],
    "rdf_binary":   [],
    "sql_files":    [],
    "docs":         ["MVWF_PERMIT Sql queries.docx"],
    "screenshots":  ["MVWF_PERMITbackend screenshots.docx"],
    "unknown":      []
  }
}
```

When no Oracle XML is present, `report` is omitted and `ingest_report`
explains what was found so the UI can guide the user.

**Response 400** — `{"error": "no files uploaded"}`.

---

## `POST /api/convert-sample/<name>`

Run the converter against one of the bundled samples (anything in
`samples/oracle/*.xml`). The `<name>` segment is validated against the
samples dir to prevent path traversal.

**Curl**

```bash
curl -X POST http://127.0.0.1:5057/api/convert-sample/MVWF_PERMIT.xml
```

**Response 200** — same conversion payload as `/api/convert`.

**Response 404** — sample not found.

---

## `GET /api/download/rdl`

Streams the most recently generated RDL as a downloadable file.

* Filename: `<report.name>.rdl` (falls back to `report.rdl`).
* MIME: `application/xml`.
* Source: the in-memory `_LAST` cache, populated by the most recent
  successful `POST /api/convert*` call.

**Curl**

```bash
curl -OJ http://127.0.0.1:5057/api/download/rdl
```

**Response 404** — no RDL has been generated yet in this process.

---

## `POST /api/run-query`

Execute translated T-SQL against `backend/db/sample.sqlite`. The bridge
layer in `preview/live_data.py` rewrites a few T-SQL constructs for
SQLite and registers Python UDFs for the Agent-2 `dbo.fn_*` stubs.

**Request** (`application/json`)

```json
{
  "sql":        "SELECT TOP 10 PERMIT_NO FROM PERMIT WHERE YEAR = @P_YEAR",
  "parameters": {"P_YEAR": 2024}
}
```

| Field      | Type   | Required | Notes                                            |
|------------|--------|----------|--------------------------------------------------|
| sql        | string | yes      | Translated T-SQL; `@P_FOO` binds are supported.  |
| parameters | object | no       | Map of bind name (without `@`) to value.         |

**Curl**

```bash
curl -X POST http://127.0.0.1:5057/api/run-query \
     -H "Content-Type: application/json" \
     -d '{"sql": "SELECT * FROM PERMIT", "parameters": {}}'
```

**Response 200**

```json
{
  "rows":     [["P-001", "Acme Inc."], ["P-002", "Beta LLC"]],
  "columns":  ["PERMIT_NO", "FACILITY_NAME"],
  "warnings": ["GETDATE() rewritten to CURRENT_TIMESTAMP for SQLite"]
}
```

`rows` is row-major; values are JSON primitives (strings, numbers, null).

**Response 500** — `{"error": "...", "trace": "..."}` on any SQL error.

---

## `GET /api/health`

Cheap probe used by the UI on page load.

**Response 200**

```json
{
  "ok":      true,
  "samples": ["MVWF_PERMIT.xml"]
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
  "deployment_checklist":[ ChecklistItem,    ... ]
}
```

### `validation_issues[]` — `ValidationIssue`

```json
{
  "severity": "error" | "warning" | "info",
  "line":     12,
  "col":      8,
  "message":  "Outer-join (+) syntax not supported in T-SQL",
  "rule":     "OUTER_JOIN_PLUS",
  "scope":    "Q_PERMIT",
  "excerpt":  "WHERE p.fac_id = f.id (+)"
}
```

`line`, `col`, and `excerpt` may be `null` when the issue is report-wide
rather than at a specific source location.

### `deployment_checklist[]` — `ChecklistItem`

```json
{
  "step":   "Point the DataSource at your DEQ SQL Server",
  "status": "todo",
  "detail": "Open the .rdl in Report Builder, edit DataSource 'DEQ', set the connection string."
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
  "name":        "MVWF_PERMIT",
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
  "name":          "P_RENEWAL_YEAR",
  "label":         "Renewal Year",
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
  "name":  "Q_PERMIT",
  "sql":   "SELECT permit_no, ... FROM permit WHERE year = :P_YEAR",
  "tsql":  "SELECT permit_no, ... FROM permit WHERE year = @P_YEAR",
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
  "name":       "Permit",
  "expression": "p.permit_no",
  "datatype":   "vchar2",
  "width":      30,
  "label":      "Permit #"
}
```

### `FormulaColumn`

```json
{
  "name":        "CF_File",
  "return_type": "VARCHAR2",
  "plsql_body":  "RETURN :permit_no || '-' || :year;",
  "tsql_body":   "RETURN @permit_no + '-' + CAST(@year AS VARCHAR);",
  "notes":       [ "Concatenation rewritten || -> +" ]
}
```

### `LayoutGroup`

```json
{
  "name":         "G_PERMIT",
  "source_query": "Q_PERMIT",
  "fields":       [ LayoutField, ... ],
  "children":     [ LayoutGroup,  ... ]
}
```

`children` recurses, mirroring the Oracle repeating-frame nesting.

### `LayoutField`

```json
{
  "name":      "F_Permit",
  "source":    "Permit",
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
