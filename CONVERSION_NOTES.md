# Oracle Reports to SSRS — Conversion Notes

Working notes on what the converter does, what it cannot do, and the design
decisions behind each non-obvious behavior. Read this once before you change
anything in `backend/converter/generators/rdl.py` or
`backend/converter/translators/plsql_to_tsql.py`.

## Mission

**1:1 structural copy.** The user drops an Oracle Reports XML, gets an RDL,
uploads the RDL to SSRS. When run, the SSRS report must render the same
data, in the same grouping, with the same per-record / tabular shape, on
the same pagination as the original Oracle output. Anything that "looks
close but isn't" is a failure. This is a conversion tool, not a
re-imagining tool.

## SharedDataSourceReference — why the RDL ships without an embedded connection

The generator emits `<DataSources>` as a `<DataSourceReference>` pointing at
a placeholder name, **not** an embedded `<ConnectionProperties>` block.

```xml
<DataSources>
  <DataSource Name="SharedDataSource">
    <DataSourceReference>SharedDataSource</DataSourceReference>
    <rd:SecurityType>None</rd:SecurityType>
    <rd:DataSourceID>00000000-0000-0000-0000-000000000001</rd:DataSourceID>
  </DataSource>
</DataSources>
```

**Why this shape is load-bearing.** When SSRS opens an RDL with an embedded
`<ConnectionProperties>` block, it has no cached credentials to use against
that connection at design time, so it pops the **"Define Query Parameters"**
dialog every time the user clicks **Refresh Fields**. Even with valid
saved credentials on the server, this dialog blocks production use — the
report effectively cannot be edited or refreshed without a human-in-the-loop
on every parameter.

A `<DataSourceReference>` tells SSRS "use the cached credentials on the
shared data source with this name in the deployed folder." Refresh Fields
then runs silently. The user's manual deploy step is to repoint the
reference at their actual shared DS (e.g. `BETA`, `PROD_REPORTS`) once
post-upload — which they were already doing anyway.

**Do not revert this design** without coordinating with the project owner.
The placeholder name (`SharedDataSource`) and placeholder GUID
(`00000000-...-0001`) are intentional: they keep the RDL diff clean across
regenerations, and SSRS regenerates the GUID at deploy time.

## PL/SQL to T-SQL translator behavior

`backend/converter/translators/plsql_to_tsql.py` is rule-based, regex-driven,
deterministic. Every non-trivial rewrite emits an entry into
`DataQuery.notes` (and the audit trail) so reviewers can audit risky
rewrites. The translator is intentionally conservative — it leaves anything
ambiguous in place and flags it.

Rewrites currently in place (see the module for the full table):

| Oracle / PL-SQL                | T-SQL emitted                              | Audit? |
|--------------------------------|--------------------------------------------|--------|
| `NVL(x, y)`                    | `ISNULL(x, y)`                             | no     |
| `NVL2(x, y, z)`                | `CASE WHEN x IS NOT NULL THEN y ELSE z`    | no     |
| `DECODE(a, b, c, d, e, f)`     | `CASE a WHEN b THEN c WHEN d THEN e ELSE f`| no     |
| `SUBSTR(s, m, n)`              | `SUBSTRING(s, m, n)`                       | no     |
| `INSTR(s, sub)`                | `CHARINDEX(sub, s)`                        | no     |
| `TO_CHAR(d, 'MM/DD/YYYY')`     | `CONVERT(VARCHAR, d, 101)`                 | yes    |
| `TO_CHAR(d, 'YYYY-MM-DD')`     | `CONVERT(VARCHAR, d, 23)`                  | yes    |
| `TO_DATE(s, fmt)`              | `TRY_CONVERT(DATETIME, s)`                 | yes    |
| `SYSDATE`                      | `GETDATE()`                                | no     |
| `||` (string concat)           | `+`                                        | no     |
| `:P_FOO` bind                  | `@P_FOO`                                   | no     |
| `&LEX_FOO` lexical             | (left in place + warning)                  | YES    |
| `Pkg_X.fn_Y(...)`              | `dbo.fn_Y(...)` + UDF stub                 | yes    |
| `ROWNUM`                       | `TOP n` / `ROW_NUMBER() OVER (...)`        | yes    |
| `(+)` outer join               | `LEFT OUTER JOIN ... ON ...`               | yes    |
| `CONNECT BY`                   | (CTE skeleton + warning)                   | YES    |
| `MINUS`                        | `EXCEPT`                                   | no     |
| `LISTAGG`                      | `STRING_AGG`                               | yes    |
| `CHR(n)`                       | `CHAR(n)`                                  | no     |
| `TRUNC(d)`                     | `CAST(d AS DATE)`                          | yes    |
| `DUAL`                         | (removed)                                  | no     |

## UDF stubs for package functions

`translators/udf_stubs.py` is a plain Python dict mapping Oracle package
function names (`Pkg_Foo.F_Bar`, `Utl_URL.escape`, etc.) to scalar T-SQL UDF
**signatures**. The translator rewrites every call site from
`Pkg_Foo.F_Bar(args)` to `dbo.fn_Bar(args)` and includes the stub body in
the downloadable Burst Pack / bundle so the SQL Server DBA has a starting
point. The stub bodies still need to be ported by hand — we do not attempt
to translate arbitrary PL/SQL function bodies.

Adding a new mapping is a one-line edit to the dict.

## Per-record (letter/certificate) vs tabular layout detection

Oracle Reports artifacts come in two structural shapes that need different
RDL bodies:

- **Per-record layout** — letter, certificate, invoice, statement. One full
  page per source row, with a free-form arrangement of fields. The body
  is a single `<Rectangle>` containing absolutely-positioned `<Textbox>`
  elements, wrapped in a single-row `<Tablix>` so the rectangle repeats
  per detail row.
- **Tabular layout** — list, register, summary. Many rows per page in a
  grid, often with grouping. The body is a multi-row `<Tablix>` with
  `<TablixRowHierarchy>` groups for each Oracle repeating frame.

The generator detects which shape applies by inspecting the parsed
`LayoutSection` tree: if any descendant of the main section has a
repeating frame, it is tabular; otherwise it is treated as per-record.
See `_looks_tabular` and `_build_per_record_body` in
`backend/converter/generators/rdl.py`.

Both code paths share the same DataSource, DataSet, ReportParameter, and
PageHeader/PageFooter emission — only the `<Body>` differs.

## Bursting / per-recipient distribution detection

`backend/converter/bursting.py` inspects the parsed report for the markers
Oracle Reports uses to indicate distribution: `P_AS_PATH`-style parameters,
file-template formula columns (e.g. `CF_File_F`), and per-recipient
`distribution.xml` payloads. When it sees them, it derives:

- A **burst key field** — the dataset column that uniquely identifies one
  recipient (typically the column referenced in the file template). The
  derivation is **name-agnostic** — no hardcoded column lists. It walks
  the file template's source columns and picks the first non-recipient
  identifier. Falls back to the first data item on the burst dataset if
  nothing matches.
- A **filename pattern** — `<BurstKey>.pdf` by default, or the literal
  template from `CF_File_F` if present.
- A **recipient query** — a T-SQL stub that returns one row per recipient
  with the burst key plus an `Email` column the user fills in (the
  generator suggests the Oracle UDF that probably produced the email
  address in the original).
- A **PowerShell DDS-emulator script** that loops the recipient query,
  renders the report bound to each burst key, and emails the PDF. This is
  for SSRS Standard installations that lack Data-Driven Subscriptions.

The Burst Pack (recipient SQL + PowerShell driver + README) is downloadable
as a separate ZIP from the Bursting tab.

## Sub-report drill-through child generation

When the parser detects a drill-through link (a `srw.run_report` call in a
formula column, an `&<P_REPORT_NAME>` lexical reference, or a
`<reference>` in the layout pointing at another report), the parent RDL
gets an `<Action>` with the right parameter mapping, and the Sub-Reports
tab in the UI lights up.

For each detected child, the user can upload the child's Oracle XML/SQL/DOCX
artifacts. `subreports.py` then composes a child RDL using the same
pipeline and bundles it alongside the parent. See `compose_subreport_rdl`
in `backend/converter/subreports.py`.

## SSRS / Oracle compatibility guarantees (test-locked)

These invariants are enforced by `tests/test_ssrs_oracle_compat.py` and
`tests/test_source_of_truth.py`. Breaking any of them breaks a real
upload.

- Every `:DATE_PARAM` bind in CommandText is wrapped in
  `TO_DATE(:P, 'YYYY-MM-DD')` driven by the param's declared SSRS
  DataType.
- Every unaliased SELECT expression gets an explicit `AS NAME` alias so
  **Refresh Fields** in Report Builder produces column names matching the
  `<Field Name>` declarations — no orphan `Fields!X.Value` references.
- Every `:BIND` referenced in SQL but not declared as a `<userParameter>`
  is auto-declared as `String/AllowBlank` so SSRS can bind it.
- Every `String` ReportParameter has `<AllowBlank>true</AllowBlank>`;
  every non-String has `<Nullable>true</Nullable>` — user can leave any
  field blank at runtime.
- DataSource is emitted as `<DataSourceReference>SharedDataSource</...>`
  so Refresh Fields runs silently after repointing to a shared DS (see
  above).
- All required-children containers (`ReportItems`, `CellContents`,
  `DataSources`, `DataSets`, `ReportParameters`) are removed if empty
  before serialization — no "has incomplete content" upload errors.
- Trailing `;` stripped from CommandText — no ORA-00933 / equivalent
  T-SQL parse error.

## CanGrow policy

Every value textbox in the per-record body and tabular detail cells emits
`<CanGrow>true</CanGrow>`. This is mandatory for free-text fields
(comments, descriptions, addresses) that wrap onto multiple lines. SSRS
auto-grows the row height to fit; pagination stays correct because row
heights are computed from the rendered content, not declared up front.

## Adding a new report as a test case

1. Drop the source Oracle XML at
   `tests/fixtures/source_of_truth/case_NNN/source.xml`.
2. (Optional) Drop a known-good hand-tweaked RDL at
   `tests/fixtures/source_of_truth/case_NNN/expected.rdl`.
3. Run `pytest -q`. The parametrized tests in
   `tests/test_source_of_truth.py` and `tests/test_field_alignment.py`
   automatically discover the new `case_NNN` directory and assert every
   compatibility guarantee above.

Customer report names, table names, column names, and bind variables must
not appear anywhere in `tests/*.py`. Fixtures are name-agnostic.

## Known limitations

- **Lexical references (`&LEX_FOO`).** No general SSRS equivalent (closest
  is dynamic SQL via expressions). The translator leaves them in place,
  the validator flags them as errors, the deploy checklist documents how
  to convert them.
- **PL/SQL package function bodies.** Call sites are rewritten and stubs
  are shipped, but the body of each `dbo.fn_*` UDF has to be ported by
  hand. The audit trail flags every call so nothing is missed.
- **Pixel-precise layout.** Position, font weight, and font size translate;
  Oracle's anchor and per-frame spacing model do not have a clean SSRS
  analog. Expect to nudge a few text boxes in Report Builder.
- **`.rdf` binary format.** Only the embedded XML payload is parsed; the
  binary layout/font tables are ignored. For best results, export as XML
  from Reports Builder before feeding the artifact in.
- **No SSRS server round-trip.** The converter stops at the `.rdl` file
  boundary by design — no network access or credentials are required to
  run it.

## Files of interest

- `backend/converter/generators/rdl.py` — RDL emission. Per-record body,
  tabular body, DataSource reference shape, compatibility post-pass.
- `backend/converter/translators/plsql_to_tsql.py` — rule table for
  every Oracle to T-SQL rewrite.
- `backend/converter/translators/udf_stubs.py` — package function to
  T-SQL UDF mapping.
- `backend/converter/bursting.py` — distribution detection and Burst Pack.
- `backend/converter/subreports.py` — drill-through child generation.
- `backend/converter/validators/preflight.py` — pre-flight RDL audit.
- `backend/converter/cross_validate.py` — cross-check parsed report
  against supporting artifacts (SQL files, DOCX walkthroughs, screenshots).
