# Test data

Local TPCH fixtures, read via `DiskConnector` under the `testdata` workspace
(registered in `tests/test_odata_battery.py`). Committed to the repo so the test
suite runs offline, with no catalog credentials.

## These are deliberately small samples

They are **not** full TPCH tables — they are the leading rows of each table,
sized to what the tests actually need. Please don't "fix" them by regenerating
TPCH at scale factor 1: that reintroduces ~106 MB into every clone, forever,
and buys no extra coverage.

| table    | rows  | files | why that size                                    |
|----------|-------|-------|--------------------------------------------------|
| nation   | 24    | 1     | full table; used by most `$filter`/`$orderby` cases |
| region   | 4     | 1     | full table                                       |
| supplier | 2,000 | 1     | paging reaches `$skip=80&$top=20`; 25 nationkeys  |
| part     | 5,000 | 1     | paging reaches `$skip=40&$top=50`; 50 distinct `p_size` |
| customer | 5,000 | 1     | paging reaches `$skip=50&$top=50`; 25 nationkeys  |
| partsupp | 5,000 | 2     | paging reaches `$skip=100&$top=25`                |
| orders   | 5,000 | 3     | paging reaches `$skip=200&$top=50`                |

`partsupp` and `orders` are kept as multiple parquet files on purpose, so
multi-fragment reads stay exercised. Compression per table is unchanged from the
originals (ZSTD for those two, SNAPPY elsewhere).

## What the battery actually asserts

Row and column *counts*, not aggregate values — e.g. the `$apply=groupby(...)`
cases assert "1 row, 2 columns", not "CustomerCount = 6161". That's what lets
the fixtures be sampled without rewriting expectations.

The two properties the samples must preserve, if you ever resize them:

- **Group cardinality.** `customer.c_nationkey` and `supplier.s_nationkey` must
  keep all 25 distinct values, `part.p_size` at least 5, `nation.n_regionkey`
  all 5 — several cases assert the resulting group count.
- **Enough rows per group.** One case filters `$apply=...aggregate($count as
  cnt))&$filter=cnt gt 10` and expects all 25 groups back, so every nationkey
  needs more than 10 rows. The current sample has at least 176.

Each table also needs at least `max($skip + $top)` rows for its paging cases;
the table above lists the binding one.
