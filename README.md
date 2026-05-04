# Oracle Reports → SSRS Converter

Drag-and-drop tool that takes an Oracle Reports artifact (`.xml` or `.rdf`-exported XML) and outputs a deployable **SSRS 2008+ RDL** file plus a 4-pane preview, deployment checklist, T-SQL validation, and (optionally) **fully-automated AI translation** of the trickier PL/SQL.

## Quick start

```bat
run.bat              (Windows)
```
```bash
./run.sh             (Linux / macOS / WSL)
```

Then open <http://127.0.0.1:5057>. Drop an XML on the page, or click `MVWF_PERMIT.xml` in the sidebar.

Requires Python 3.9+. The launcher pip-installs Flask + lxml + python-docx + (optional) anthropic on first run.

## Auto-fix with AI (optional but recommended)

For tricky PL/SQL the deterministic translator can't handle, the app can call **Claude** directly to fill in the body, validate it, and patch the RDL — no copy-paste.

**Setup:**
1. Get an Anthropic API key at <https://console.anthropic.com/settings/keys>
2. Copy `.env.example` to `.env`
3. Paste your key:
   ```
   ANTHROPIC_API_KEY=sk-ant-...
   ```
4. Restart `run.bat`. The app loads the `.env` automatically.

**Use it:**
After a conversion, click the **Extras** tab → at the top you'll see a purple **"Fix all with AI"** button → click → Claude is called once per AI prompt, each result is validated (no DROP/EXEC etc.), and applied to the RDL. Bundle download then includes all the fixes.

**Without a key** the app still works fully — you just see the prompt templates in the Extras tab to paste into Claude/Copilot manually.

## What it does

1. **Parse.** Reads Oracle Reports XML (DTD 9.0.2.0.10) into a normalized model.
2. **Translate.** Oracle SQL/PL-SQL → T-SQL: DECODE/NVL/TO_CHAR/TO_DATE/TRUNC/SYSDATE/INSTR/SUBSTR/CHR/||/(+)/CONNECT BY/LISTAGG/ROWNUM, bind vars, lexical refs, package-function stubs.
3. **Generate.** Emits well-formed RDL with DataSources, DataSets, ReportParameters, master-detail Tablix, page numbering, image control for signatures.
4. **Validate.** T-SQL static checks + RDL structural checks (catches "won't open in Report Builder" before download).
5. **Audit.** Records every translation decision with before/after snippets.
6. **Bursting.** Detects distribution patterns (P_AS_PATH, CF_File_F) and emits a burst-query SQL stub + PowerShell DDS emulator script for SSRS Standard.
7. **Preview.** 7 tabs: HTML Mockup, RDL XML, Side-by-Side, Live Data (against seeded SQLite), T-SQL Validation, Deploy Checklist, Extras (audit + AI prompts + bursting + Auto-fix button).
8. **Compare.** Drop two reports, see structured diff + complexity delta.
9. **Bundle.** One-click `.zip` of RDL + validation + checklist + audit + prompts + burst SQL + DDS script + README.

## API endpoints

| Method | Path | What |
|---|---|---|
| GET | `/` | UI |
| POST | `/api/convert` | Single XML → full conversion JSON |
| POST | `/api/convert-bundle` | Multi-file/folder → conversion + ingest report |
| POST | `/api/convert-sample/<name>` | Bundled sample shortcut |
| POST | `/api/compare` | Two XMLs → structured diff |
| POST | `/api/run-query` | T-SQL → live SQLite results |
| GET  | `/api/download/rdl` | Most recent .rdl |
| GET  | `/api/download/bundle` | 8-file zip |
| GET  | `/api/mockup/<print\|compact>` | Print or compact mockup variant |
| GET  | `/api/ai/status` | Auto-AI configured? |
| POST | `/api/auto-fix` | Run Claude on every prompt, apply each |
| POST | `/api/apply-fix` | Apply one pasted UDF body |
| GET  | `/api/health` | Health + sample list |

## Layout

```
backend/
  app.py                          Flask routes
  cli.py                          Batch CLI conversion
  converter/
    models.py                     Shared dataclasses
    parsers/oracle_xml.py
    translators/
      plsql_to_tsql.py            Oracle SQL → T-SQL
      udf_stubs.py                Pkg_*.F_* → dbo.fn_*
      registry.py                 Plugin system for org-specific rules
    generators/rdl.py             RDL XML emitter
    validators/
      tsql_check.py               Static T-SQL checks
      rdl_check.py                RDL structural checks
    preview/
      html_mockup.py              B&W rendered preview
      mockup_variants.py          Print + compact variants
      live_data.py                T-SQL → SQLite + Python UDFs
    deployment.py                 9-step migration checklist
    audit.py                      Translation audit trail
    ai_assist.py                  Paste-into-LLM prompt templates
    ai_apply.py                   Apply one fix back into RDL
    ai_runner.py                  Auto-call Claude API
    bursting.py                   DDS detection + scripts
    bundle_export.py              Build the 8-file .zip
    ingest.py                     Multi-file/folder auto-classification
    cache.py                      SHA-256 memoize
    compare.py                    Two-report diff + complexity
  db/
    seed_sample_db.py             Seeds sample.sqlite
frontend/
  templates/index.html
  static/
    css/style.css
    js/
      app.js                      Main UI
      demo_mode.js                "Take a tour" walkthrough
      compare_mode.js             Compare two reports modal
      ai_apply.js                 Paste-back textareas
      ai_auto.js                  Auto-AI button
samples/oracle/                   MVWF_PERMIT + 3 synthetic test reports
tests/                            112 pytest tests
docs/
  ARCHITECTURE.md
  API.md
  COWORKER_DEMO.md
  DEMO_SCRIPT.md
  AGENT_RULES.md                  Mount-corruption guardrail for any future agent edits
.env.example                      Copy to .env, paste your ANTHROPIC_API_KEY
```

## Development

```bash
# install deps
pip install -r requirements.txt

# run tests
pytest

# batch conversion
python backend/cli.py samples/oracle --out ./out --strict
```

See `docs/ARCHITECTURE.md` for module-by-module deep-dive, `docs/AGENT_RULES.md` for the file-write protocol any contributor (human or AI) must follow.
