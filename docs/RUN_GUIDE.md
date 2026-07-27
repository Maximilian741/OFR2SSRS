# Oracle2SSRS — Deployment Run Guide

A step-by-step operator's guide for running the converter, reading its
verdicts, and deploying the output to a SQL Server Reporting Services
(SSRS) Report Server. Written for an agency/enterprise deployment where
the person converting reports may not be the person who wrote them.

---

## 1. Install

Requirements: **Python 3.10+** on Windows, macOS, or Linux. (The optional
render-verification harness is Windows-only; conversion itself runs
anywhere.)

```bash
git clone <your-fork-or-clone-url>
cd <repo>
pip install -r requirements.txt
```

Or use the bundled scripts: `setup.ps1` (Windows) / `setup.sh` (POSIX).

### Optional: local render verification (RenderLab)

`tools/renderlab` renders generated RDLs through **Microsoft's own
ReportViewer engine** and measures the PDFs. It needs Windows + the .NET
ReportViewer assemblies (fetched by the tool on first run). If your
environment blocks unsigned executables, the harness automatically falls
back to a signed-DLL render path; conversion quality is identical either
way — only local proof-rendering is affected.

---

## 2. Run the app

```bash
python backend/app.py
```

Then open `http://localhost:5000`. The single-page app serves from
`frontend/`. Everything runs locally — no report content leaves the
machine.

---

## 3. Convert a report

1. **Export the report from Oracle** as XML:
   `rwconverter userid=... source=REPORT.rdf dest=REPORT.xml dtype=XMLFILE`
   (Reports Builder's *File → Generate to File → XML* works too.)
   The converter also accepts `.jsp` Reports Web Sources, raw `.sql`
   files, `.docx` walkthroughs, and mixed folders of artifacts.
2. **Drag the file(s) into the app** (or POST to `/api/convert` — see
   `docs/API.md`).
3. Review the four-pane preview: HTML mockup, generated RDL, bursting
   detection, and sub-report (drill-through) children.
4. **Read the verdict banner** (section 4) before downloading.
5. Download the `.rdl` and deploy (section 8).

---

## 4. The verdict banner

Every conversion is audited by a preflight validator plus a static layout
auditor. The banner is the worst finding:

| Verdict | Meaning | What to do |
|---|---|---|
| **READY** | No blocking or data-correctness findings. | Deploy. |
| **AMBER** | Cosmetic or informational findings (layout clip risk, an auxiliary dataset that never renders, a linked detail intentionally unused). | Deploy; review the notes. |
| **RED** | A data-correctness risk that will not error at run time — the worst kind. | Fix or consciously accept before go-live. |
| **BLOCKER** | The report will fail to publish or is not a convertible source at all. | Must fix. |

Rules you are most likely to see:

- `source.unsupported_kind` (**BLOCKER**) — the file isn't an Oracle
  Reports definition (e.g. a Reports Server bursting/distribution spec, a
  JasperReports design, an Oracle Forms module). The message says what it
  actually is.
- `rdl.hollow_body` (**BLOCKER**) — the body references zero dataset
  fields while real fields exist; the output would render blank pages.
- `rdl.placeholder_dataset.*` (**RED**) — column extraction failed for a
  query; anything bound to it renders empty.
- `sql.lexical_identifier.*` / `sql.lexical_tablesource.*` (**BLOCKER**)
  — a runtime lexical (`&P_X`) formed part of a table/column name; the
  emitted SQL cannot run until you substitute the real identifier.
- `sql.lexical_where_dropped.*` (**RED**) — the source filtered rows
  through a runtime lexical WHERE fragment; the converted query runs
  **unfiltered** until you reimplement the filter. Nothing errors — the
  report just returns every row. Take this seriously.
- `sql.lexical_nevermatch.*` (**RED**) — a lexical inside an
  IN/comparison operand was NULL-stubbed; that predicate can never match,
  so the dataset returns **zero rows** until reimplemented.
- `sql.lexical_operand_null.*` (**AMBER**) — a lexical filled an
  expression operand; the computed column degrades to NULL.
- `rdl.linked_detail_not_rendered.*` (**AMBER**) — a linked child query
  is never bound to any region. Legitimate for auxiliary queries (LOV /
  formula feeders); a data loss if that detail was supposed to print.

---

## 5. The fidelity card

Each conversion carries a fidelity report (`fidelity_report` in the API
payload, the *Extras* card in the UI):

- **score** — `1.0` means no silent loss of parameters, dataset columns,
  or layout-placed data columns. Anything lower names exactly what
  dropped.
- **categories.layout_fields.display_coverage** — the stricter secondary
  axis: the share of layout columns actually referenced by a display
  expression. A report can declare every column (score 1.0) yet display
  few of them — this number exposes that.
- **needs_attention** — a plain-English list: formulas wired as stubs,
  totals not rendered, orphaned datasets, non-SQL data sources, charts
  auto-built.

---

## 6. Labels, overrides, and things a source can't tell us

Some display text simply isn't in the export (a bundle built from bare
SQL has no title; operator-supplied captions live in people's heads).
The **label-override facility** closes that gap generically:

- Every conversion returns `overridable_labels` — an inventory of each
  literal label in the output (name, current text, region).
- POST `label_overrides` (JSON object) with your replacements, keyed by
  the inventory name — or the shorthand key `"title"` for the report
  title. Data expressions can never be overridden, only labels.

```bash
curl -F "file=@REPORT.xml" \
     -F 'label_overrides={"title":"Quarterly Contacts List"}' \
     http://localhost:5000/api/convert
```

---

## 7. Data source binding

- **Shared data source (recommended):** set the shared data source path
  in the sidebar once; every artifact produced in the session (downloads,
  batch output, sub-reports) binds to it.
- **Embedded:** supply a connection string per request.
- Undeclared query binds default to `Nothing` so SSRS **never prompts**
  at upload, Refresh Fields, or run — a deliberate, load-bearing design.
  Optional prompts stay optional: `col = :P` predicates are NULL-guarded,
  so an empty prompt means *all rows*, not zero.

---

## 8. Deploy to a Report Server

1. Upload the `.rdl` via the Report Server web portal (or
   `rs.exe` / PowerShell `Publish-RsRestItemContent`).
2. Point the report at your data source (shared reference recommended).
3. Open the report once in **Report Builder** and run **Refresh Fields**
   — the generated SQL aliases every column so the refreshed field list
   matches the RDL exactly; the report must not prompt.
4. Run with real parameters and compare against a known-good output of
   the original report.
5. For DATE parameters, values pass as `yyyy-MM-dd` strings and are
   `TO_DATE`-wrapped in the SQL — enter dates in that format when testing
   outside the portal's date picker.

---

## 9. Batch conversion & assessment

```bash
python -m backend.converter.batch <folder-of-xml> --out <outdir>
```

Produces every RDL plus a **Migration Assessment** (per-report verdicts,
fidelity scores, effort tiers). The community tier processes batches of
10; set `O2S_LICENSE` for larger tiers. The same runs via `/api/batch`.

---

## 10. Sub-reports & bursting

- **Sub-reports:** drop a child artifact onto a detected drill-through
  link; the child is built through the full pipeline (RDL + mockup) with
  the parent's forwarded parameters declared and hidden, and the drilled
  key NULL-guard-filtered so a standalone run still returns rows.
- **Bursting:** per-recipient distribution instructions are detected and
  summarized in the Bursting pane; pure distribution specs (a
  `<destinations>` file) are classified honestly rather than converted.

---

## 11. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| Upload rejected by Report Server | Check the verdict banner first — BLOCKER findings name the exact publish error class. |
| Report prompts for a parameter | Should never happen with this converter's output; re-download and confirm you didn't hand-edit defaults. |
| Every row / no rows returned | See the `sql.lexical_*` RED findings — a runtime lexical filter needs reimplementing (dynamic WHERE or parameter-guarded predicates). |
| Blank columns | The fidelity card's `needs_attention` names unbound columns and stubbed formulas; supply the SQL/UDF where noted. |
| Dates print like `6/8/2026 12:00:00 AM` | Fixed automatically for masked fields; if you see it, the source field carried no `formatMask` — set a Format in Report Builder. |
| A conditionally-printed frame always prints | Its PL/SQL format trigger was beyond the supported translation subset — the preflight notes list untranslated triggers. |
| Wide report spills across pages | Intentional: reports genuinely wider than the page paginate horizontally at legible column widths instead of crushing to fit. |

---

*Everything in this guide is exercised by the automated test suite
(698 tests at last count), including publish + render through Microsoft's
own ReportViewer engine.*
