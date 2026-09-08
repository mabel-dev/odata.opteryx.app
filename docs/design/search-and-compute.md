# Design: `$search` and `$compute`

**Status:** `$compute` is implemented (2026-09-08) as designed in §2 below,
including the v1 scope decision to reject it combined with `$apply`. See
[expression_parser.py](../../app/odata/expression_parser.py)'s `parse_compute`,
[odata_to_logical_plan.py](../../app/odata/odata_to_logical_plan.py)'s
`_add_compute_node`, and [tests/test_compute_execution.py](../../tests/test_compute_execution.py).
`$search` is still draft/not started — it still returns `501 Not Implemented`
(deliberately — see [tests/test_unsupported_options.py](../../tests/test_unsupported_options.py)
and [tests/test_minimum_conformance.py](../../tests/test_minimum_conformance.py),
item 7). §1 below is the design to replace that 501 with real support.

**Repos involved:** `odata.opteryx` (this repo) for both features; `opteryx-core`
only if we choose to build the Phase 2 native `$search` primitive described
below. `$compute` needs no `opteryx-core` changes at all — see §2.

---

## 1. `$search`

### 1.1 What it means

OData §11.2.5.6 defines `$search` as free-text search across whatever
properties the *service* decides are searchable — the spec deliberately does
not pin this to client-named columns (unlike `$filter`). The syntax it does
standardize supports quoted phrases, `OR`, implicit `AND`, `NOT`/`-` negation,
and parens, e.g. `$search="red bicycle" OR blue`.

### 1.2 Scope decision: simplified matching semantics (deliberate, documented)

We are **not** implementing the full `searchExpr` grammar (phrases/OR/NOT/parens)
in v1. OData explicitly leaves matching semantics up to the service, so a
documented subset is spec-legal — but it must be a *documented* choice, not an
accident: a naive `str.split()` would silently mis-tokenize
`"red bicycle" OR blue` into four ANDed garbage terms (`"red`, `bicycle"`,
`OR`, `blue`) instead of either honoring or rejecting the quoting/boolean
syntax.

**v1 semantics:** whitespace-tokenized terms, implicit `AND` only. No quoted
phrases, no `OR`/`NOT`, no parens. If the raw `$search` value contains a `"`,
reject with `400` naming the unsupported syntax, rather than silently
mis-parsing it. This is documented in the endpoint description and in the
OpenAPI `responses` block, same as the existing `$top`/`$skip` documented
constraints in [interface.py](../../app/routes/v4/interface.py).

### 1.3 Which columns get searched

`$search` needs to know which columns of a dataset are "string-like." That
mapping already exists — `column_type_to_edm(...)` in
[edm_types.py:288-393](../../app/odata/edm_types.py) maps opteryx's column
types to `Edm.String` (or `EDM_UNKNOWN` if it declines to guess), and is
already used by `$metadata` generation
([metadata.py:26,644](../../app/routes/v4/metadata.py)).

What's missing: `read_dataset`/`execute_odata_query`
([interface.py:781-997](../../app/routes/v4/interface.py)) never fetches a
dataset's schema today — it hands opteryx a bare table name and lets opteryx's
own binder resolve columns lazily during execution. Supporting `$search`
means adding a schema lookup to that path that isn't there now.

**Decision needed:** "every column typed as `Edm.String`" vs. an explicit
per-dataset allowlist of searchable columns.

**There is currently no config surface for the allowlist option.** Checked
opteryx-catalog's column schema
(`SchemaColumn` in `opteryx_catalog/catalog/dataset.py:270-288`) — it carries
only `name`, `type`, `element_type`, `precision`, `scale`, `nullable`, `id`.
No description field, no arbitrary flags, nothing an allowlist could hang off
today. "An explicit allowlist" is therefore not "add a check" — it's a new
`SchemaColumn.searchable: bool` field (or equivalent), a migration for
existing catalog entries, and some way for a workspace owner to actually set
it (there's no admin surface for per-column config at all right now; compare
to `Custom.Description`, which is dataset-level, sourced from
`ds.metadata.description`, not per-column — see
[interface.py:2716](../../app/routes/v4/interface.py)).

- *Every string column* (type-derived, zero new schema) has two costs:
  (a) performance — cost scales with `rows × string-columns`, and a wide
  table with many text columns pays for all of them on every `$search` query;
  (b) exposure — a text column nobody intended to be "searchable" (an
  internal notes field, a raw log line) becomes reachable via a keyword
  guess, a different access-control surface than `$filter`'s "you must
  already know the exact column and exact value."
- *Explicit allowlist* avoids both costs but is real, separate engineering:
  a catalog schema field, a migration, and an opt-in UI/API path that doesn't
  exist yet — a project of its own, not a detail of this one.

**Revised recommendation:** ship v1 as type-derived (every `Edm.String`
column, via the schema lookup added in this feature anyway), with a hard cap
on the number of columns searched per dataset (e.g. skip `$search` support —
`400`, not silently partial — on any dataset whose string-column count
exceeds a configured ceiling) as the cheap mitigation for both the
performance and exposure concerns until a real per-column opt-in surface
exists. Treat the allowlist as a separate, later piece of work gated on that
catalog schema addition, not a v1 dependency.

### 1.4 Phase 1 — OData-layer only, no `opteryx-core` change

The `$filter` pipeline already compiles native `LIKE`/`contains()` predicates
— WHERE execution in opteryx-core is 100% native (see
`opteryx/operators/filter/filter.pyx`'s module docstring in opteryx-core:
"WHERE execution is 100% native ... the native compiler never calls a
push/execute method on it"), and the actual string-matching kernel already
exists as a real (non-naive) vectorized operator in
`opteryx/expression/operations/string_matching.pyx`. So "OR of per-column
`LIKE`" is not a slow Python-level hack — each column comparison is already
compiled and executed natively; the cost is genuinely about *how many columns*
get scanned, not interpretation overhead.

Mechanically: build a `Node(NodeType.OR, ...)` tree of
`Node(NodeType.COMPARISON_OPERATOR, value="Like", left=<column>, right=<pattern>)`
nodes, one per allowlisted string column per search term (AND across terms,
OR across columns per term). This is the same tree shape `ast.py`'s existing
`Compare.to_opteryx_node()` already builds for `IN (a, b, c)`
(`app/vendored/odata-query/odata_query/ast.py:493-512`) and the same
comparison-node shape `contains()` itself already produces
(`ast.py:839-856`) — no new AST node types needed, just a new builder function
that takes `(terms, columns)` instead of parsing a `$filter` string.

AND this predicate into the same Filter node `$filter` already produces
(`_add_filter_node`, [odata_to_logical_plan.py:185-209](../../app/odata/odata_to_logical_plan.py))
— `$search` and `$filter` are both row-restricting and combine with AND per
spec, so no new pipeline stage is needed, just an additional predicate ANDed
into the existing one.

**This phase covers the common case (flat string columns) and needs zero
`opteryx-core` changes.** It's the right place to start.

### 1.5 Phase 2 (optional, later) — a native `opteryx-core` search primitive

Phase 1's limit: it can't reach into nested/structured data (STRUCT, JSON,
arrays) without first stringifying each such column, and it pays for N
separate column scans rather than one fused per-row pass.

opteryx-core already has exactly the per-row, per-column-type-dispatched
machinery this would need, built for a different purpose: **rugo's morsel →
JSONL writer** (`rugo/src/_text_render.hpp`, `rugo/src/jsonl/_jsonl_writer.pxi`
in opteryx-core). Its design:

- `resolve_col()` picks a per-column `EmitFn` function pointer *once* per
  column (float/int/string/array/etc.), so the row loop itself has no
  per-cell type switch (`_text_render.hpp:44-50` and the `Col`/`EmitFn`
  machinery around it).
- `jsonl_render_rows()` walks rows using those resolved emitters, and
  `jsonl_write()` partitions rows across a shared thread pool
  (`jsonl_render_pool()`, `_text_render.hpp:476-527`) for parallel rendering.
- Critically, this already handles every type opteryx has, including nested
  ARRAY/STRUCT columns (`_fill_array_levels`, referenced in
  `_jsonl_writer.pxi:60-64`) — the exact reach Phase 1's plain-column `LIKE`
  doesn't have.

**The proposed primitive:** a sibling function next to `jsonl_write`, e.g.
`row_contains(dvs, descs, terms, ncols, nrows) -> BitVector`, that reuses the
same `Col`/`EmitFn` per-column dispatch and the same threaded row
partitioning, but instead of appending each emitted cell into an output
string, renders each cell into a small reused scratch buffer, tests it for a
substring match, and **short-circuits the row as soon as one column matches**
— never building a full serialized row, unlike a "concatenate to JSON then
substring-scan" approach (which was considered and rejected — see below).
Output is a boolean/selection vector, not bytes.

**Integration into the query engine:** WHERE is compiled natively via
`opteryx/managers/execution/compiler.py`'s `FilterNode` branch into
`src/cpp/engine/engine.hpp`'s `ExprFilterOperator` (per `filter.pyx`'s module
docstring). A `$search`-as-native-primitive would need:
1. A new intrinsic/function name in the expression layer (alongside how
   `Like`/`contains` are already registered — see
   `opteryx/expression/operator_catalog.pyx` and
   `opteryx/expression/operations/string_matching.pyx`) so it can appear
   inside an ordinary filter expression tree and AND-compose with `$filter`
   at zero extra pass, exactly like Phase 1's approach — the only difference
   is this one intrinsic call fans out to every eligible column internally
   instead of the OData layer building an explicit OR-tree.
2. A compiler hook so `ExprFilterOperator` knows how to call the new native
   function.

This is real core-engine work (C++ + Cython + a new expression-layer
intrinsic + a compiler hook), not a small patch. **Recommendation: defer
Phase 2 until Phase 1's allowlisted-column approach is shipped and someone
actually needs `$search` over nested/struct data** — don't build it
speculatively.

*Rejected alternative:* concatenate each row into a JSON or CSV blob (via
`write_jsonl` as-is, or a hand-rolled concatenation) and substring-scan the
blob. Rejected because (a) it pays full per-row serialization cost with no
early exit, unlike the short-circuiting `row_contains` sketched above; (b)
naive CSV/blob concatenation risks a term matching across a field boundary
that isn't really "in" either field, unless every value is safely delimited;
(c) it doesn't reduce the column-discovery work either way — you still need
to know which columns to include in the blob.

---

## 2. `$compute`

### 2.1 What it means

OData §11.2.4.9 (v4.01) lets a client define a new, ad-hoc computed property
from an expression over existing columns, then use it in `$select`,
`$orderby`, or `$filter` as if it were a real column:

```
$compute=price mul quantity as TotalValue&$select=id,TotalValue&$orderby=TotalValue desc
```

### 2.2 The good news: `opteryx-core` needs no changes

`opteryx-core`'s `ProjectionNode` **already natively supports computed
expressions** — its module docstring says execution "re-derives the computed
expressions from `node.parameters` — hoisted_columns, projection,
passthrough_columns — via `_add_computed`"
(`opteryx/operators/projection/projection.pyx` in opteryx-core). `SELECT a,
b*c AS x FROM t` is already a first-class, fully native operation in the
engine. `$compute` is entirely an OData-layer (parsing + plan-shape) problem,
not an engine problem.

### 2.3 What's actually needed in `odata.opteryx`

**(a) Alias parsing.** `$filter`'s existing grammar
(`app/vendored/odata-query`) already parses arithmetic/function expressions
into plain *value* nodes when they're not used as a boolean predicate — e.g.
`BinOp.to_opteryx_node()`
(`app/vendored/odata-query/odata_query/ast.py:405-427`) already returns a
`NodeType.BINARY_OPERATOR` value node for `price mul quantity`, today, just
currently only ever invoked from inside a `$filter` boolean context. What's
missing is `AS <identifier>` — that syntax doesn't exist anywhere in
`grammar.py`. The only existing alias-parsing in this codebase is `$apply`'s
`with sum as Total` mini-grammar
(`app/vendored/odata-query/odata_query/apply.py`), which is a **separate,
hand-rolled recursive-descent parser**, not reusable directly — but it's a
good model for how much new parsing this actually needs (small).

Plan: add `ODataExpressionParser.parse_compute()`
([expression_parser.py](../../app/odata/expression_parser.py)) that:
1. Splits the raw `$compute` value on top-level commas (respecting
   parens/quotes/nested calls — `$compute` allows a comma-separated list of
   clauses).
2. Splits each clause on its trailing `AS <identifier>`.
3. Feeds the left-hand expression string through the *existing* SLY
   lexer/parser + `.to_opteryx_node()`, then sets `.alias` on the resulting
   node (same idiom `apply_builder.py:24-37` already uses for aggregate
   aliases: `agg_node.alias = alias`).

**(b) Pipeline placement.** The plan pipeline order is
`Scan → Expand → Apply → Filter → Select → OrderBy → Limit → Exit`
(documented at [odata_to_logical_plan.py:78-79](../../app/odata/odata_to_logical_plan.py)).
A computed property must be usable in `$filter`, `$select`, *and* `$orderby`
— so it cannot be tacked onto the final `Select` projection; it has to be
injected as an early `Project`-shaped step **right after `Scan`/`Apply`,
before `Filter`**, carrying the original wildcard columns plus the new
aliased expression(s). `_add_project_node`
([odata_to_logical_plan.py:346-371](../../app/odata/odata_to_logical_plan.py))
already has the ingredients for a wildcard-plus-explicit-columns projection
(`project_node.columns` / `project_node.except_columns`) — this is an
extension of that pattern, applied earlier in the pipeline, not a new
opteryx-core node type (opteryx has no dedicated `Extend`/`Compute` step;
`SELECT a, b*c AS x` is just an ordinary `Project` node whose column list
mixes a plain identifier and an aliased expression subtree, per §2.2).

**Column-name validation is not a blocker.** `read_dataset` doesn't currently
validate `$select`/`$orderby` names against a known schema list at all —
unknown columns are caught implicitly downstream when opteryx's own binder
fails to resolve them, surfacing as a `400` via the existing exception
handling ([interface.py:1280-1293](../../app/routes/v4/interface.py)). So a
`$compute` alias needs no special-casing there: once it's a real column in
the plan by the time `$filter`/`$select`/`$orderby` try to resolve it,
opteryx's normal binder accepts it like any other column.

**(c) `$compute` + `$apply` interaction — explicitly out of scope for v1.**
The Data Aggregation extension allows computing over `$apply`'s aggregate
results (e.g. `$apply=aggregate(amount with sum as Total)&$compute=Total mul 2 as Doubled`).
This collides with an existing wrinkle: aggregate output lives in a special
`"$derived"` schema that the Exit binder unconditionally drops before
resolving its own wildcard — which is why `_pending_aggregate_columns`
already exists as a workaround, referencing `Node` objects directly rather
than resolving by name
([odata_to_logical_plan.py:47-60](../../app/odata/odata_to_logical_plan.py)).
A post-`$apply` `$compute` would hit the same resolution problem and likely
need the same kind of workaround.

**v1 decision: reject `$compute` combined with `$apply`** with a clear `400`
("`$compute` cannot reference `$apply` aggregate results in this version"),
rather than silently getting it wrong. Revisit as a follow-up once the
`$derived`-schema handling is designed for this case on purpose.

### 2.4 What `$compute` does *not* need

No new opteryx-core operator, no new grammar family (it piggybacks on
`$filter`'s existing value-expression grammar), no new column-name allowlist
in `interface.py`. This is the cheaper of the two features to build.

---

## 3. Rollout / test impact

`$compute` — done, as it played out:

- `tests/test_unsupported_options.py`: the old `TestComputeRejection` class
  (asserting blanket `501`) is gone; `$compute` moved into
  `TestSupportedOptionsStillWork` alongside the other real options.
- `tests/test_minimum_conformance.py::TestItem7SystemQueryOptions`: `$compute`
  moved out of the `501`-parametrized list into the
  "recognized and not dropped silently" one.
- New `tests/test_compute_execution.py`: `parse_compute` unit coverage,
  pipeline-placement coverage (alias usable in `$select`/`$orderby`/`$filter`/
  `$count`), and the `$compute` + `$apply` → `ValueError`/400 rejection —
  all run through `execute_odata_query` against the built-in `$planets`
  dataset, no catalog needed.

`$search` — still to do:

- `tests/test_unsupported_options.py::TestSearchRejection` asserts
  `$search=...` → `501`. Becomes "→ 200 with correct results" once it ships,
  with a new class for the rejected/edge cases (`$search` containing `"`, a
  dataset whose string-column count exceeds the cap).
- `tests/test_minimum_conformance.py::TestItem7SystemQueryOptions` still
  lists `$search` as a "recognized and rejected" example for item 7 — moves
  out of that list once it ships; item 7's overall conformance claim
  (parse-or-501) is unaffected either way.

## 4. Summary of decisions

Resolved (as implemented):

4. `$compute` + `$apply`: rejected with `400` (`ValueError` from
   `ODataLogicalPlanBuilder.build`) for v1, not supported yet.

Still open, needed before `$search` implementation starts:

1. `$search` matching scope: whitespace/implicit-AND only for v1 — confirm.
2. `$search` column scope: every `Edm.String` column with a per-dataset
   column-count cap (recommended for v1 — no catalog schema change needed)
   vs. a per-dataset explicit allowlist (requires adding a `searchable` field
   to opteryx-catalog's `SchemaColumn` plus a way to set it — real, separate
   work; not a v1 dependency).
3. `$search` quoted/boolean syntax sent by a client: reject with `400`
   (recommended) vs. silently degrade.
5. Whether Phase 2 (native `opteryx-core` search primitive) is worth
   scoping now or genuinely deferred until Phase 1 proves insufficient.
