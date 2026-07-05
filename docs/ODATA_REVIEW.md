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
- **Retire the `sys.path` hacks.** Runtime currently loads opteryx-core from `../opteryx-core` (0.9.0, unpublished) while the pin claims `0.8.*` — the pin is untruthful. Publish the core release that ships the APIs used, then depend on it via pip (editable for dev). Removes the two-different-relative-paths fragility and the `pyiceberg-firestore-gcs` path insert.
- **Report upstream:** empty `opteryx/odata/` and corrupted `opteryx/logging.py` in the core checkout affect the whole fleet.

### P2 — implementation quality
- **Consolidate the two EDM mappers.** `interface.py` (precise) and `metadata.py` (broad, client-compat) implement divergent policies and were independently broken the same way. Move both behind one module with a documented policy and shared base-category parsing to prevent drift.
- **Delete dead legacy code.** `expression_parser.parse_apply` / `_parse_aggregates` implement the abandoned non-standard `$apply` grammar and are now unused. Remove to prevent someone re-wiring the broken path.
- **Tighten broad `except Exception`.** e.g. schema fetch in `interface.py` sets `schema = None` on *any* error, masking real catalog failures behind a generic 404. Catch specific exceptions and log.
- **Grammar conflicts.** The vendored SLY grammar emits *36 reduce/reduce conflicts* at import — the parser is ambiguous and may parse edge-case filters surprisingly. Worth resolving.
- **Reconcile `REDESIGN.md`.** Several issues it lists are now resolved (auth *is* enforced on both routers via `Depends(require_authentication)`). Update or archive it so it stops describing a stale reality.

---

## 5. Free-threaded Python 3.14 (3.14t) migration status

Target: the Docker image runs on free-threaded CPython 3.14. Moving to 3.14t
requires **every compiled dependency** to ship a `cp314t` wheel (or build from
source in-image).

**Done in this pass**
- `orjson` → `msgspec`. orjson ships **no** `cp314t` wheels (verified across its
  entire release history); msgspec does and is comparably fast. New response
  class in [`app/responses.py`](../app/responses.py); wired up in `app/main.py`.
- `requires-python` bumped to `>=3.14`; added [`.python-version`](../.python-version) = `3.14t`.
- `Dockerfile`: base `python:3.13-slim` → `python:3.14-slim`; uv now provisions
  the `3.14t` interpreter and builds `.venv` against it. (The official `python`
  image has **no** free-threaded tag — uv is the practical way to get 3.14t.)

**Wheel status of the other native deps** (checked on PyPI):

| dependency | `cp314t` wheel | notes |
|---|:---:|---|
| pydantic-core, cryptography, uvloop, httptools, msgspec | ✅ | fastapi / uvicorn / responses |
| **grpcio** | ❌ | **hard blocker** — pulled in by `google-cloud-firestore` and `google-cloud-secret-manager`; never shipped a free-threaded wheel and is hard to build from source |
| **google-crc32c** | ❌ | pulled in by `google-cloud-storage`; has a pure-Python fallback, so it degrades rather than blocks |

**Remaining blockers before the 3.14t image can actually run** (not yet actioned):
1. **grpcio / Firestore.** The catalog backend can't get a free-threaded wheel. Options to evaluate: wait for a grpcio `cp314t` release; build grpcio from source in the image (heavy, brittle); or move the Google clients to a **REST transport** where supported to drop the grpcio dependency.
2. **Audit/bump the exact pins** so their transitive compiled deps resolve `cp314t` wheels — notably `cryptography==46.0.5` (looks stale/possibly nonexistent) and `fastapi==0.135.1` / `uvicorn==0.38.0` (must resolve pydantic-core ≥2.47, uvloop/httptools versions that ship `cp314t`). I did **not** bump these unilaterally: they are behaviour-affecting, and grpcio blocks the build regardless, so they should be sequenced after the grpcio decision.
3. **opteryx-core in-container.** The image installs `opteryx-core==0.8.*` from PyPI (the `sys.path` sibling doesn't exist in the container), but the engine was ported against local **0.9.0** APIs (`opteryx.expression`, etc.). Publishing 0.9.x and pinning to it is a prerequisite for a working image (see §4, P1).
