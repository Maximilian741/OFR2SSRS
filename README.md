# Oracle Reports → SSRS Converter

Hackathon project. Drag-and-drop tool that takes an Oracle Reports artifact
(`.xml` / `.rdf`-exported XML) and outputs a valid **SSRS 2008+ RDL** plus a
live preview of how the report will render.

## Quick start

```bat
:: From this folder
run.bat              (Windows)
```

```bash
./run.sh             (Linux / macOS / WSL)
```

Then open <http://127.0.0.1:5057>. Drag `samples\oracle\MVWF_PERMIT.xml` onto
the drop zone, or click the sample chip in the sidebar.

Requires Python 3.9+. The launcher pip-installs Flask + lxml + python-docx on
first run.

## What it does

1. **Parse.** Reads the Oracle Reports XML (DTD 9.0.2.0.10) into a normalized
   in-memory model: parameters, queries, data items, formula columns, layout
   groups, triggers.
2. **Translate.** Converts Oracle SQL & PL/SQL to T-SQL. Handles `DECODE`,
   `NVL`, `TO_CHAR`, `TO_DATE`, `TRUNC`, `SYSDATE`, `INSTR`, `SUBSTR`, `CHR`,
   `||`, `(+)` outer joins, bind variables, lexical refs, and any
   `Pkg_*.F_*` package call (which gets stubbed as a `dbo.fn_*` T-SQL UDF).
3. **Generate.** Emits a well-formed RDL document with `DataSources`,
   `DataSets`, `ReportParameters`, a `Tablix` bound to the main query, page
   header/footer, and code helpers — opens directly in Report Builder.
4. **Preview.** Four side-by-side views: HTML mockup, RDL XML (Prism
   highlighted), Oracle ↔ SSRS diff, and **live data** running the translated
   query against a seeded SQLite sample DB with Python UDFs that mimic the
   Oracle package functions.

## Layout

```
backend/
  app.py                          Flask entry point (port 5057)
  converter/
    models.py                     Shared dataclasses (the contract between modules)
    parsers/oracle_xml.py         Agent 1 — Oracle Reports XML parser
    translators/
      plsql_to_tsql.py            Agent 2 — Oracle SQL/PLSQL → T-SQL translator
      udf_stubs.py                Agent 2 — Pkg_WUTM_Util.F_* UDF stubs
    generators/rdl.py             Agent 3 — SSRS RDL XML generator
    preview/
      html_mockup.py              Agent 4 — printed-form HTML preview
      live_data.py                Agent 5 — T-SQL→SQLite + UDF runtime
  db/
    seed_sample_db.py             Run once to (re)build sample.sqlite
    sample.sqlite                 Seeded DEQ schema with 5 sample MVWF permits
frontend/
  templates/index.html            Drag-drop UI shell
  static/
    css/style.css                 Polished styling
    js/app.js                     Tab logic, fetch, Prism highlighting
samples/
  oracle/MVWF_PERMIT.xml          Real Montana DEQ Oracle Report
  oracle/*.docx                   Backend/frontend screenshots, SQL queries
  expected_rdl/MVWF_PERMIT.rdl    Pre-generated RDL output (42 KB)
docs/DEMO_SCRIPT.md               Talking points for Monday
```

## Endpoints

| Method | Path                           | What it does                          |
| ------ | ------------------------------ | ------------------------------------- |
| GET    | `/`                            | Render the drag-drop UI               |
| POST   | `/api/convert`                 | Multipart upload → conversion JSON    |
| POST   | `/api/convert-sample/<name>`   | Convert a bundled sample file         |
| POST   | `/api/run-query`               | Run translated T-SQL against SQLite   |
| GET    | `/api/download/rdl`            | Download the most recent .rdl         |
| GET    | `/api/health`                  | Sanity check + sample list            |

## Adding more sample reports

Drop another `.xml` into `samples/oracle/` and restart the app — the sidebar
picks it up automatically.

## Re-seeding the sample database

```bash
python backend/db/seed_sample_db.py
```
