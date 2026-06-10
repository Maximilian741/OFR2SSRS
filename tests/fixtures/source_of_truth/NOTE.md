# source_of_truth fixtures

Each subdirectory is one regression case: a synthetic Oracle Reports `source.xml`
plus the `expected.rdl` our converter produces from it. The tests in
`tests/test_source_of_truth.py` (and the other fixture-walking tests) discover
every subdirectory that contains both files and assert structural invariants on
the conversion — no case names, table names, or column names are hard-coded in
the tests.

**These reports are 100% synthetic.** They use a neutral, made-up domain
(customers/orders, a sample notice letter) purely to exercise converter code
paths. They contain **no customer, agency, or production data of any kind.**

Current cases:

| Directory       | Shape                          | Exercises                                   |
| --------------- | ------------------------------ | ------------------------------------------- |
| `master_detail` | Customer → Orders (nested)     | group tree, `<link>` master-detail, date binds → TO_DATE |
| `letter`        | Sample notice letter           | positional document layout, letter detection |

To add a case: drop a synthetic Oracle XML in as `source.xml`, run the converter
to produce `expected.rdl`, and the suite picks it up automatically. Never commit
real customer reports here.
