# OData Service — Currency Migration & Implementation Review

_Date: 2026-07-05_

This document records (1) the dependency-currency work carried out in this pass,
(2) latent correctness bugs found and fixed along the way, and (3) prioritized
recommendations for the OData implementation.

---

## 1. Context — what "left to rot" actually meant

The literal ask ("replace pyarrow/orso with rugo/draken, bump opteryx-core /
opteryx-catalog") was the visible tip of a service that **could not import or
run** in its checked-out state:

| Symptom | Root cause |
|---|---|
| `import orso` fails | `orso` was removed from the environment; the fleet moved to `rugo`/`draken` (draken is the type/vector engine; `opteryx.*` wraps it). |
| `from opteryx.odata import …` resolves to nothing | The entire OData translation engine the service proxied to is **absent** from both published `opteryx-core` (0.8.13, verified via sdist) and the local `../opteryx-core` checkout (empty, untracked `opteryx/odata/`). |
| `$metadata` column types all reported as `Edm.String` | Two type mappers still compared against `orso.types.OrsoTypes` enums, but the catalog now returns **type strings**. |
| Fragile wiring | `sys.path.insert` hacks in 4+ files (two different relative paths) point at a moving dev checkout. |

Note also: the local `../opteryx-core/opteryx/logging.py` is **corrupted** — it
contains pasted AI chatter and a syntax error (a prior agent's artifact). This
service no longer imports `opteryx.logging`, so it is insulated, but **other
fleet services that use `orso.logging`/`opteryx.logging` are not** — flag to the
core repo owner.

No `pyarrow` references exist in `app/` (only transitively under opteryx-core).

---

## 2. Changes made in this pass

**Dependency currency**
- Removed the `orso` dependency from `pyproject.toml`.
- Bumped `opteryx-core` pin `0.6.*` → `0.8.*` (latest published line). `opteryx-catalog` stays `0.4.*` (already latest, 0.4.44).
- `rugo`/`draken` are **not** added as direct deps — the app does not import them directly; they arrive transitively through opteryx-core (the correct fleet pattern).

**orso → opteryx/draken migration**
- `orso.logging.{get_logger,set_log_name}` → new self-contained [`app/logging.py`](../app/logging.py) shim (avoids depending on the corrupted `opteryx.logging`; preserves the `set_log_name` API). Updated 10 importers.
- `orso.types.OrsoTypes` → `opteryx.types.logical_type` `ColumnType` constructors for expression literals, and canonical **type strings** for catalog-driven EDM mapping.

**OData engine restored into the repo** (per decision: self-contained service)
- Recovered the real implementation from this repo's git history (`34b570f`) into `app/odata/` — `odata_to_logical_plan.py`, `expression_parser.py`, `apply_builder.py`, `plan_optimizer.py`, and a real package `__init__.py` (previously all thin proxies to the missing `opteryx.odata`).
- Ported to current core APIs: `opteryx.managers.expression` → `opteryx.expression`; `orso.tools.random_string` → `opteryx.utils.random_string`.
- Ported the vendored `odata_query/ast.py` literal types to `ColumnType` (`INTEGER`→`INT64`, `DOUBLE`→`FLOAT64`, `TIME`/`TIMESTAMP` now parameterized calls, `ARRAY(element)` with the element embedded in the type, NULL detection via `str(type)=="NULL"`).

**Verification** (this environment has no live catalog/connector, so execution was not exercised end-to-end — see §4):
- `$filter`, `$select`, `$orderby`, `$top`/`$skip`, `$count`-only, and `$apply` all build valid logical plans.
- `IN (...)` expands to an OR-chain; `eq null` → `IsNull`; substring/indexof offset adjustment intact.
- Both EDM mappers verified against representative catalog type strings.
- `ruff` clean on all changed non-vendored files.

---

## 3. Bugs found and fixed

1. **`$apply` was 100% broken.** `ODataLogicalPlanBuilder._add_apply_nodes` validated the expression with `expression_parser.parse_apply` (a non-standard `sum(price) as total` regex grammar) *and then* built it with `ApplyLogicalPlanBuilder` (the OData-standard `price with sum as total` grammar). No input satisfies both, so every `$apply` raised. Removed the redundant, conflicting validation; `ApplyLogicalPlanBuilder` is now the single source of truth. **Fixed.**

2. **`interface.py` `$metadata` types always `Edm.String`.** `_orso_type_to_edm` compared the catalog's **string** `field.type` against `OrsoTypes` enum members — never equal. Rewrote as `_column_type_to_edm`, mapping the canonical type string (stripping `DECIMAL(…)`/`ARRAY<…>` parameters). **Fixed.**

3. **`metadata.py` discovery types always `Edm.String`.** `_map_py_type_to_edm` gated all type logic on `hasattr(py_type, "name")`; catalog strings have no `.name`, so every column fell through to the Python-type path and defaulted to `Edm.String`. Now accepts `str`, strips type parameters, and adds `NVARCHAR`/`VARIANT`. **Fixed.**

---

## 4. Recommendations (prioritized — not yet actioned)

### P0 — correctness / operability
- **Integration test against a live dataset.** This pass verified *plan construction* and *type mapping*, not full *execution* (binder → connector → draken). Stand up one Firestore/GCS-backed dataset and assert real rows for `$filter/$select/$orderby/$top/$apply/$count`. This is the only gap between "imports & builds plans" and "serves correct data."
- **`$expand` silently no-ops.** `_add_expand_nodes` returns the plan unchanged, so navigation-property requests return base rows with the expansion silently dropped. Either implement JOIN translation or return `501 Not Implemented` for `$expand` so clients aren't misled.

### P1 — dependency hygiene
- **Retire the `sys.path` hacks.** `opteryx-core` **0.9.1 is now published** (with a `cp314t` wheel) and the pin is `opteryx-core==0.9.*` — truthful and matching the APIs the engine was ported against. The `sys.path.insert` into `../opteryx-core` (and `../pyiceberg-firestore-gcs`) can now be removed so dev and container both resolve core from pip. Do this next; it removes the two-different-relative-paths fragility.
- **Report upstream:** empty `opteryx/odata/` and corrupted `opteryx/logging.py` in the core checkout affect the whole fleet.

### P2 — implementation quality
- **Consolidate the two EDM mappers.** `interface.py` (precise) and `metadata.py` (broad, client-compat) implement divergent policies and were independently broken the same way. Move both behind one module with a documented policy and shared base-category parsing to prevent drift.
- **Delete dead legacy code.** `expression_parser.parse_apply` / `_parse_aggregates` implement the abandoned non-standard `$apply` grammar and are now unused. Remove to prevent someone re-wiring the broken path.
- **Tighten broad `except Exception`.** e.g. schema fetch in `interface.py` sets `schema = None` on *any* error, masking real catalog failures behind a generic 404. Catch specific exceptions and log.
- **Grammar conflicts.** The vendored SLY grammar emits *36 reduce/reduce conflicts* at import — the parser is ambiguous and may parse edge-case filters surprisingly. Worth resolving.
- **Reconcile `REDESIGN.md`.** Several issues it lists are now resolved (auth *is* enforced on both routers via `Depends(require_authentication)`). Update or archive it so it stops describing a stale reality.

---

## 5. Python 3.14 migration status

Target: the Docker image runs on **standard (GIL-ful) CPython 3.14**.

Free-threaded (3.14t) was evaluated and **abandoned**: `grpcio` (pulled in by
`google-cloud-firestore`/`-secret-manager`) has never shipped a `cp314t` wheel
and is impractical to build from source, so the catalog backend can't run
free-threaded today. GIL-ful 3.14 has **no such blocker** — `grpcio`, `orjson`,
`google-crc32c`, `pydantic-core`, `cryptography`, `uvloop`, `httptools` and
`opteryx-core` all ship `cp314` (GIL) wheels.

**Done in this pass**
- `Dockerfile`: base `python:3.13-slim` → `python:3.14-slim` (stock GIL image).
- `requires-python` bumped to `>=3.14`.
- `opteryx-core` pinned to `0.9.*` (0.9.1 published; ships a `cp314` wheel and all modules the restored engine imports).
- **`orjson` retained.** The earlier `orjson`→`msgspec` swap was only needed for free-threaded; on GIL-ful 3.14 `orjson==3.11.7` has `cp314` wheels, so we reverted to it to stay consistent with the rest of the fleet (all services use `ORJSONResponse`). `app/responses.py` and `.python-version` were removed.

**Follow-up (not blocking, worth doing):**
- Audit the exact pins for `cp314` wheels — notably `cryptography==46.0.5` (looks stale/possibly nonexistent; verify or bump) and `fastapi`/`uvicorn` (ensure they resolve `pydantic-core`/`uvloop`/`httptools` versions with `cp314` wheels). None of these are 3.14 *blockers*, but stale pins can force slow source builds in the image.
- If free-threading is revisited later, the only true blocker is `grpcio` — track its `cp314t` support or evaluate a Google-client REST transport.
