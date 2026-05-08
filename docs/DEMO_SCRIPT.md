# Demo script — Oracle2SSRS Converter

## 60-second pitch

> "Imagine you have hundreds of legacy Oracle Reports running on a server that
> Oracle no longer supports. Migrating each one to SSRS by hand is weeks of
> work per report. **Oracle2SSRS** does the conversion in seconds, end users
> just drag the file in, and get a working RDL out the other side — with a
> live preview against sample data so they know it actually runs."

## Live demo (3 minutes)

1. **Show the empty page.** "This is the whole UI. Sidebar on the left, four
   preview tabs on the right. There's nothing to learn."

2. **Click `SAMPLE_INSPECTION.xml` in the sample list** (or drag it from your
   Documents folder onto the drop zone). The status pill flips to *Converting*
   then *Converted*. The sidebar fills with the report summary: 16 parameters,
   3 queries, 5 formula columns.

3. **Tab 1 — HTML Mockup.** "This is what the SSRS report will look like when
   it renders. Letterhead, parameter form, data table, signature block.
   100% generated from the Oracle XML — we never wrote any of this markup
   ourselves."

4. **Tab 2 — RDL XML.** Scroll the syntax-highlighted RDL. "42 KB of valid
   SSRS 2008+ RDL. You can open this file directly in Report Builder right
   now, point it at any SQL Server, and it will run."

5. **Tab 3 — Side-by-Side.** "Auditors love this. Left pane: the original
   Oracle Reports XML. Right pane: the converted RDL. Click *Sync scrolling*
   and you can see exactly which Oracle construct produced which RDL element."

6. **Tab 4 — Live Data.** "Here's the magic. We translated Oracle SQL —
   DECODE, NVL, TO_CHAR, package functions like `Pkg_Common.F_Format_Address` —
   into T-SQL. The translated query runs against a sample SQLite DB seeded with
   synthetic inspection records. **Click Run Query.**"
   - Output shows 5 inspection sites belonging to fictional organizations like
     Acme Holdings, Northwind Industries, and Globex Group, with formatted
     Permit Dates like *MARCH 15, 2025 TO MARCH 14, 2030*.
   - Warning chips show every translation step so the user can audit what was
     adapted.

7. **Click Download .rdl** in the sidebar. "And there's the file you upload to
   your SSRS server. Done."

## What to call out if asked

- **Architecture.** Five independent modules: parser, translator, generator,
  preview, and live-DB. Hot-swappable — the same UI works for any Oracle
  Reports XML.
- **Translation coverage.** DECODE → CASE, NVL → ISNULL, TO_CHAR → FORMAT,
  TO_DATE → TRY_CONVERT, (+) outer join → LEFT JOIN, `||` → `+`, lexical
  refs flagged, and any `Pkg_*.F_*` package call gets a corresponding
  `dbo.fn_*` UDF stub auto-generated alongside the RDL.
- **Honesty.** Every non-trivial translation appears as a warning chip. The
  user knows exactly which lines the converter is confident about and which
  to review.
- **Hackathon ambition.** 5 sub-agents + 10 sub-sub-agents wrote this in
  parallel in the same hackathon session. Total ~3,000 lines of Python and
  ~30 KB of frontend.

## Backup plan if something breaks

- `samples/expected_rdl/SAMPLE_INSPECTION.rdl` — pre-generated RDL output, 42 KB.
- `samples/oracle/` — all four user-provided artifacts in case you need to
  reference them.
