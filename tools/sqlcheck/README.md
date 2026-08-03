# sqlcheck — is the generated SQL actually SQL?

Every other check in this repo reasons about the **RDL**. None of them
ever asked whether the **SQL** we emit is SQL that Oracle would accept.

That gap is not theoretical. The render harness supplies synthetic rows
*by field name*, so a query the server rejects still produces a perfect
local PDF. Four classes of un-runnable SQL reached production that way:

| Oracle error | What we did wrong |
|---|---|
| `ORA-00923` | aliased a star expansion (`SELECT O.* AS O`) |
| `ORA-01789` | added a join key to only the **first** `UNION ALL` branch |
| `ORA-00979` | selected an injected key from a **grouped** query without grouping it |
| `ORA-00904` / `ORA-00937` | injected a column the branch cannot see, or a bare column into an aggregate |

## Run it

```bash
python tools/sqlcheck/check_corpus.py report.xml
```

```bash
python tools/sqlcheck/check_corpus.py --dir ./my_reports
```

Exit code `0` means no converter-introduced SQL defects.

## The differential rule

A generated query that fails to parse is reported **only when the
report's original query parsed cleanly**. Oracle has constructs no
third-party grammar fully models — legacy `(+)` outer joins in
non-equality comparisons, `KEEP (DENSE_RANK ...)`, package calls,
optimizer hints. Those appear in the source *and* the output, so they are
grammar gaps, not defects, and reporting them would bury real findings in
noise from valid customer SQL.

Requires `pip install sqlglot`. Without it, every check degrades to "no
opinion" — never to a false alarm.

## Strongest check: validate against the real database

`EXPLAIN PLAN` compiles a statement on the server — syntax, table and
column resolution, privileges — **without reading or modifying a single
row**. Point it at the schema the reports will actually run against:

```bash
pip install oracledb
set O2S_ORACLE_DSN=host:1521/XEPDB1
set O2S_ORACLE_USER=reporting_user
set O2S_ORACLE_PASSWORD=...
python tools/sqlcheck/check_corpus.py --live report.xml
```

This is the check to run before a go-live decision: the grammar backend
proves the SQL is well-formed, the live backend proves it resolves
against the real schema. A read-only account is sufficient and
recommended.
