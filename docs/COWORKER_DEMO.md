# Oracle2SSRS — Coworker Demo (Monday, 5 minutes)

## Why this matters (30 seconds)

We have **hundreds of Oracle Reports** still in production. Every one of them
has to move to SSRS before the Oracle support contract expires.

Doing it by hand:

* Re-type every parameter into Report Builder.
* Read every PL/SQL formula column and re-write it in T-SQL.
* Rebuild every repeating frame as a Tablix.
* Re-validate the layout against an old Oracle screenshot.

Realistic estimate from the team: **40 to 200 hours per report.** At our
volume that is 2-3 FTE-years of pure migration drudgery.

This tool turns the bulk of it into **30 seconds of drag-and-drop**, plus a
short, well-defined list of things a human still needs to clean up. The
human time per report drops to 2-8 hours.

---

## 5-minute live demo flow

You're going to walk through this in front of the laptop. Don't read off the
script — just hit each beat.

### Beat 1: open the app (15 seconds)

```bash
cd HackathonOracle2SSRS
./run.sh        # or run.bat on Windows
```

Browser opens at `http://127.0.0.1:5057`. Show the empty drag-drop zone.

> "This runs locally. No data leaves the machine. No accounts, no API keys."

### Beat 2: drop the Oracle XML (30 seconds)

Drag `samples/oracle/SAMPLE_INSPECTION.xml` onto the drop zone.

While the spinner runs (it won't run for long), say:

> "SAMPLE_INSPECTION.xml is a synthetic Oracle Reports XML modeled on the
> structures the converter targets. About 1,200 lines of XML. Eight
> parameters, seven datasets, four formula columns, a master-detail
> layout, and a footer with page numbers."

When the tabs light up, you have everything you need open at once.

### Beat 3: the Mockup tab (30 seconds)

This is the first tab — black and white, looks like the printed report.

> "This is what the report would look like. We render it from the parsed
> structure so you can eyeball it side by side with the original Oracle
> output before you ever open Report Builder."

### Beat 4: the RDL tab (45 seconds)

Click the **RDL XML** tab.

> "This is a real, structurally valid SSRS RDL document. It opens in
> Report Builder with no parse errors. Every Oracle parameter became an
> SSRS ReportParameter with the right datatype. Every Oracle dataSource
> became an SSRS DataSet. Every repeating frame became a Tablix."

Scroll through it briefly. Don't try to read it — it's not the point.

### Beat 5: the T-SQL tab (45 seconds)

Click **T-SQL**.

> "Each query went through a rule-based translator. NVL became ISNULL.
> DECODE became CASE. SUBSTR became SUBSTRING. SYSDATE became GETDATE.
> Bind parameters got the @ prefix. Anything risky — outer-join (+),
> CONNECT BY, lexical references — gets flagged in the Notes column
> rather than silently fudged."

Point at one query's Notes. Read one note out loud.

### Beat 6: the Live Data tab (45 seconds)

Click **Live Data**, pick a query, click **Run**.

> "This is the moment that sells it. We bundle a SQLite sample DB and run
> the translated T-SQL against it. Real rows come back. We can see the
> migration is functional, not just textually plausible, before we touch
> a real SQL Server."

If the query has parameters, change one and re-run to show binding works.

### Beat 7: the Validation tab (30 seconds)

Click **Validation**.

> "Two validators run automatically. The T-SQL static validator catches
> untranslated Oracle constructs and unbalanced parens. The RDL structural
> validator confirms every field reference resolves to a declared field
> and every parameter reference resolves to a declared parameter. Errors
> here mean Report Builder will reject the file."

Sort by severity. Show that errors are at the top.

### Beat 8: the Checklist tab (30 seconds)

Click **Deployment Checklist**.

> "Once you download the RDL, this is the punch list to take it from
> 'file on my disk' to 'running on the SSRS server'. Items marked
> 'auto' the converter already did. 'todo' is what you have to do and
> we tell you exactly how. 'caution' is a known footgun. 'manual' is UI
> work nothing can automate."

### Beat 9: download and open in Report Builder (45 seconds)

Click **Download .rdl**. Open it in Report Builder live.

> "Opens clean, no parse errors. Parameters are there with the right
> types. Datasets are wired. The Tablix is laid out. The remaining work
> is layout polish and porting the few PL/SQL package functions we
> stubbed."

That's the demo. Stop here.

---

## What we still need to do by hand (be honest)

We do **not** want to oversell this. The tool nails the structural 80% and
deliberately surfaces what is left.

### 1. Lexical references (`&LEX_FOO`)

Oracle's lexical-reference feature splices arbitrary text into the SQL at
runtime. There is no clean SSRS analog. The translator leaves them in
place; the validator flags them as errors; the checklist tells the human
how to convert them to RDL expressions. **Expect 30-60 minutes per
lexical reference**, depending on what it does.

### 2. PL/SQL package functions (`Pkg_Foo.fn_Bar`)

We rewrite the **call site** to `dbo.fn_Bar` and ship a stub, but the
**body** of the function has to be ported by hand into a real SQL Server
scalar UDF. The audit trail flags every package call so nothing gets
missed. **Plan on a one-time porting investment per package** — once you've
ported `Pkg_Common`, every report that uses it is free.

### 3. Pixel-precise layout

We translate position, font weight, and font size, but Oracle Reports'
layout model has anchors and per-frame spacing rules SSRS doesn't share.
Reviewers should expect to nudge a few text boxes in Report Builder.
**Plan 30-90 minutes of layout polish per report.**

### 4. Triggers

Most Oracle triggers (`BeforeReport`, `AfterParameterForm`, ...) have no
SSRS analog. We expose the trigger bodies in the side-by-side view; the
human decides whether the logic belongs in the dataset, in a parameter
default, or whether it can be dropped.

### 5. The `.rdf` binary format

We accept `.rdf` files and pull out the embedded XML payload, but the
binary layout/font tables are not parsed. **For best results, export the
report as XML from Reports Builder before feeding it in.**

---

## Roadmap toward fuller automation

Things the team has scoped but not built yet:

* **Translator plugin registry.** Drop-in custom rewrite rules per
  customer / database without forking the core. (Agent 21 stub already
  scaffolded.)
* **AI-assist module.** When the deterministic translator can't handle
  something, build a tight LLM prompt that includes the rules table plus
  the un-translated snippet, and merge the response back into the rule
  pipeline. Deterministic first, LLM only fills gaps. (Agent 11.)
* **CLI mode.** Batch convert an entire folder of Oracle reports without
  the UI, for nightly migration runs. (Agent 14.)
* **Conversion cache.** Hash-keyed result cache so re-running over the
  same artifact is instant. (Agent 20.)
* **Test suite.** pytest coverage of every translation rule and every
  RDL structural invariant, runnable in CI. (Agent 15 plus Agent 19's CI
  config.)
* **Bursting / DDS support.** Convert Oracle distribution.xml to an SSRS
  Data-Driven Subscription scaffold. (Agent 13.)
* **More samples.** Beyond SAMPLE\_INSPECTION, harvest 5-10 representative
  reports across the migration backlog and pin them as regression
  fixtures. (Agent 16.)

---

## If you have 30 more seconds

Mention these in passing:

* The whole pipeline is **decoupled** behind one `ParsedReport` dataclass.
  Adding a new generator (CSV, Power BI, Crystal) means writing one
  function. Adding a new translator (LLM-assisted, DB2-target) means
  writing one function.
* It is **offline by default**. No SaaS, no telemetry, no API keys.
* The frontend is **vanilla JS** with no build step. You can clone and
  run it in 60 seconds on a fresh laptop.

Then stop talking and let them play with it.
