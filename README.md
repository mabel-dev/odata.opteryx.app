# Opteryx OData Service

An OData v4-compliant API for querying and accessing Opteryx datasets with enterprise-grade authentication and authorization.

> **📖 User documentation: [Querying via OData](https://docs.opteryx.app/docs/guides/querying-via-odata)**
>
> That guide is the place to start if you want to *use* the service — it has a
> live "Try it" panel you can run queries in without signing up. This README
> covers the service itself: its API surface, its deviations from the OData
> spec, and how to run and develop it.

## Overview

This service provides OData v4 endpoints that allow you to:
- **Discover datasets** you have access to via service document
- **Query data** using standard OData query parameters ($filter, $top, $skip, etc.)
- **Inspect schemas** through EDMX metadata documents
- **Connect from BI tools** like Excel, Power BI, and Tableau

Requests enforce row-level security based on the permissions in your token.

A small number of public collections are readable **without credentials** —
currently `public.security.*` and `public.geopolitics.*` (see
`ANONYMOUS_EXEMPT_PAIRS` in `app/auth/permissions.py`). This is what lets the
documentation's "Try it" panel run real queries against
`public.geopolitics.countries` anonymously. Every other dataset requires
authentication: a request that omits the `Authorization` header entirely is
treated as anonymous and rejected by the endpoint, while a request that sends
malformed credentials is rejected at authentication.

## Quick Start

### Authentication

The service supports two authentication methods:

#### 1. Bearer Token (Recommended)
```bash
curl -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  https://odata.opteryx.app/api/v4/
```

#### 2. Basic Authentication
```bash
curl -u username:password \
  https://odata.opteryx.app/api/v4/
```

Basic auth credentials are exchanged for a JWT token via the external authentication service (`authenticate.opteryx.app`).

### Discovering Datasets

Get a list of all datasets you have access to:

```bash
curl -u username:password \
  https://odata.opteryx.app/api/v4/
```

Response:
```json
{
  "@odata.context": "/api/v4/$metadata",
  "value": [
    {
      "name": "public.demo.users",
      "kind": "EntitySet",
      "url": "public/demo/users"
    },
    {
      "name": "personal.alice.projects",
      "kind": "EntitySet",
      "url": "personal/alice/projects"
    }
  ]
}
```

### Querying Data

Query a dataset with OData parameters:

```bash
# Get first 10 rows
curl -u username:password \
  "https://odata.opteryx.app/api/v4/public/demo/users?\$top=10"

# Filter and sort
curl -u username:password \
  "https://odata.opteryx.app/api/v4/workspace/collection/dataset?\$filter=age gt 21&\$orderby=name&\$top=100"
```

Response format:
```json
{
  "@odata.context": "/api/v4/$metadata#dataset",
  "value": [
    {"id": 1, "name": "Alice", "age": 25},
    {"id": 2, "name": "Bob", "age": 30}
  ]
}
```

### Snapshot / Version Access

The dataset segment of the URL may carry a `@{label}` version selector, to read a point in the dataset's history instead of its current data:

| Selector | Meaning |
|---|---|
| `dataset` (no `@`) | Current data — the default |
| `dataset@current` | Current data, explicitly |
| `dataset@previous` | The version of the data before this one. Maintenance operations that change no rows (compaction, statistics refresh) are skipped, so this always names a version with different data, not just a different snapshot id. |
| `dataset@{tag}` | A named, immutable snapshot — see `Custom.Tags` in `$metadata` for the tags a dataset holds |
| `dataset@{snapshot_id}` | A specific snapshot, by id |

```bash
# The version before this one
curl -u username:password \
  "https://odata.opteryx.app/api/v4/public/demo/users@previous?\$top=10"

# A tagged snapshot
curl -u username:password \
  "https://odata.opteryx.app/api/v4/public/demo/users@release_v2?\$top=10"

# A specific snapshot id
curl -u username:password \
  "https://odata.opteryx.app/api/v4/public/demo/users@1700000000000?\$top=10"

# $metadata for a specific version -- its Custom.Snapshot.* and
# Custom.ReadSources.* annotations describe that version rather than the
# current one (Custom.Tags, a dataset-level pin list, does not change)
curl -u username:password \
  https://odata.opteryx.app/api/v4/public/demo/users@release_v2/\$metadata
```

An unknown tag, snapshot id, or a `@previous` on a dataset with no earlier
version returns 404. A malformed selector (`dataset@`, or `@label` with no
dataset name) returns 400.

**Pagination note:** `@{tag}` and `@{snapshot_id}` are immutable, so paging
(`$skip`/`$top`) through them is fully consistent. `@current` and `@previous`
are resolved fresh on every page, so paging through them is not isolated
against writes that land while you're paging.

### Getting Schema Information

Retrieve EDMX metadata for all accessible datasets:

```bash
curl -u username:password \
  https://odata.opteryx.app/api/v4/\$metadata
```

This returns an XML document describing EntityTypes, properties, and their data types.

## API Endpoints

### OData v4 API (`/api/v4`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v4/` | GET | Service document - lists all accessible datasets |
| `/api/v4/$metadata` | GET | EDMX metadata document with schemas |
| `/api/v4/{workspace}/{collection}/{dataset}` | GET | Query data from a specific dataset |
| `/api/v4/{workspace}/{collection}/{dataset}/$metadata` | GET | Schema for a specific dataset |

`{dataset}` may carry a `@{label}` version selector (`dataset@current`,
`dataset@previous`, `dataset@{tag}`, `dataset@{snapshot_id}`) on either
endpoint above — see [Snapshot / Version Access](#snapshot--version-access).

## Query Parameters

### Supported OData v4 Query Parameters (Phase 1)

| Parameter | Description | Example |
|-----------|-------------|---------|
| `$filter` | Filter results with comparison/logical operators | `$filter=age gt 21 and status eq 'active'` |
| `$select` | Select specific columns | `$select=name,age,status` |
| `$orderby` | Sort by one or more columns (asc/desc) | `$orderby=name asc, age desc` |
| `$top` | Limit number of results (max 25,000). If omitted, default is `100`. | `$top=100` |
| `$skip` | Skip N results for pagination | `$skip=200` |
| `$count` | Include total count of results | `$count=true` |
| `$apply` | Aggregation and grouping operations | `$apply=groupby((Region), aggregate(Sales with sum as Total))` |
| `$search` | Full-text search | *(Not implemented)* — returns 501 Not Implemented |
| `$expand` | Expand navigation properties (JOINs) | *(Not implemented)* — returns 501 Not Implemented |

**Case-insensitive string matching:** Use `tolower()`/`toupper()` to perform case-insensitive comparisons. Example:

```
$filter=tolower(name) eq tolower('earth')&$top=10
```
### Filter Operators

**Comparison Operators:**
- `eq` - equals (case-sensitive for strings)
- `ne` - not equals
- `gt` - greater than
- `ge` - greater than or equal
- `lt` - less than
- `le` - less than or equal

**Logical Operators:**
- `and` - logical AND
- `or` - logical OR
- `not` - logical NOT

**String Functions:**
- `contains(field, 'substring')` - case-sensitive substring search
- `startswith(field, 'prefix')` - case-sensitive prefix match
- `endswith(field, 'suffix')` - case-sensitive suffix match

**Network Functions:**
- `in_subnet(field, 'cidr')` - IPv4 CIDR containment, e.g. `in_subnet(src_addr, '192.168.4.0/24')`. `field` must be an IPv4-typed column. This is an Opteryx extension, not part of the OData v4 spec; OData has no containment operator, and PostgreSQL's `<<=` is not valid OData syntax.

**Date and Time Functions:**
- `now()` - the current UTC instant, as `Edm.DateTimeOffset`. Takes no arguments. Evaluated once per query, so every row is compared against the same instant.
- `year(field)`, `month(field)`, `day(field)` - date components as integers. Work on `Edm.Date` and `Edm.DateTimeOffset` columns.
- `hour(field)`, `minute(field)`, `second(field)` - time components as integers. Meaningful only on `Edm.DateTimeOffset` columns.
- `date(field)` - narrows a timestamp to its date, e.g. `date(published_at) eq 2024-01-15` matches the whole day.

`time()`, `mindatetime()` and `maxdatetime()` are part of the OData v4 grammar
but are **not** supported, and return 400 naming what to write instead. There is
no cast from a timestamp to a time in Opteryx, and the extremes of
`Edm.DateTimeOffset` are outside the range Opteryx timestamps can hold.

**Date and time literals are unquoted:** `shipped_date gt 2024-01-15` or
`created_at ge 2024-01-15T00:00:00Z`. Quoting one (`shipped_date gt
'2024-01-15'`) compares it as text and returns a type-mismatch error. Both forms
work against either a `Edm.Date` or a `Edm.DateTimeOffset` column; the date-only
form means midnight.

**Relative time windows** combine `now()` with an ISO-8601 duration literal via
`add`/`sub`, which is how a query stays correct when it is re-run on another day:

```
$filter=published_at ge now() sub duration'P30D'      # the last 30 days
$filter=created_at ge now() sub duration'PT12H'       # the last 12 hours
$filter=due_date le now() add duration'P1Y'           # due within a year
```

`P1Y` and `P1M` are calendar-aware (a year, a month), not 365.25 and 30.44 days.

### Aggregation (`$apply`)

The `$apply` parameter supports grouping and aggregation for analytics queries using OData v4 standard syntax:

```
$apply=groupby((column1, column2), aggregate(amount with sum as Total, $count as Count, price with average as AvgPrice, value with min as Min, value with max as Max))
```

**Supported Aggregation Methods:**
- `$count as alias` - count all records
- `column with sum as alias` - sum numeric values
- `column with average as alias` - average numeric values
- `column with min as alias` - minimum value
- `column with max as alias` - maximum value

Those four `with` methods — `sum`, `average`, `min`, `max` — are the complete
set. `column with count as alias` is **not** supported and returns 400; use
`$count as alias` to count records. Function-call syntax such as
`aggregate(min(column) as alias)` or `aggregate(count(*) as alias)` is not part
of the OData `$apply` grammar and is also rejected.

### Query Examples

**Basic filter:**
```
GET /api/v4/workspace/collection/dataset?$filter=vendor eq 'Oracle'
```

**Complex filter with logical operators:**
```
GET /api/v4/workspace/collection/dataset?$filter=(age gt 21 or status eq 'premium') and region ne 'APAC'
```

**Pagination:**
```
GET /api/v4/workspace/collection/dataset?$top=50&$skip=100&$orderby=created_date desc
```

**Select specific columns:**
```
GET /api/v4/workspace/collection/dataset?$select=name,email,phone&$top=1000
```

**Aggregation query (analytics):**
```
GET /api/v4/workspace/collection/dataset?$apply=groupby((region,product_type), aggregate(revenue with sum as TotalRevenue, $count as TransactionCount, order_value with average as AvgOrderValue))
```

**Combined query:**
```
GET /api/v4/workspace/collection/dataset?$filter=year ge 2023&$apply=groupby((status), aggregate($count as Count))&$select=status,Count
```

**Query a tagged snapshot** (see [Snapshot / Version Access](#snapshot--version-access)):
```
GET /api/v4/workspace/collection/dataset@release_v2?$filter=vendor eq 'Oracle'
```

## Known Differences from OData Specification

This service implements several intentional design choices that diverge from the OData v4.01 specification to maximize compatibility with SQL semantics and client tooling.

### Null Handling in Filters

This service implements **SQL three-valued logic (3VL)** for null handling in filter expressions, which differs from the strict OData v4.01 specification.

**OData v4.01 specification behavior:**
- `x ne 'value'` returns **true** when `x` is null (null is not equal to any value)
- Filters like `status ne 'inactive'` would **include** null-status rows

**This service's behavior:**
- `x ne 'value'` returns **null** when `x` is null (unknown comparison)
- Rows where the filter expression evaluates to null are **excluded** from results
- Filters like `status ne 'inactive'` **exclude** null-status rows

**Implication:** If you need to include null values in your results, use `or x eq null`:

**Example:**
```
GET /api/v4/workspace/collection/dataset?$filter=status ne 'inactive'
```
Returns rows where status is NOT 'inactive', **excluding** null-status rows.

To include null-status rows:
```
GET /api/v4/workspace/collection/dataset?$filter=(status ne 'inactive') or (status eq null)
```

### Response Value Type Mappings

The OData v4 interface coerces database column types to JSON-compatible OData types. These mappings differ from strict CSDL in some cases to maximize client compatibility.

| Source Type | Mapped to | JSON Wire Format | Notes |
|---|---|---|---|
| DECIMAL, NUMERIC | Edm.Double | JSON number (float) | Precision loss on large numbers (>2^53). For precise decimals, use the SQL interface. |
| DATE | Edm.DateTimeOffset | ISO 8601 datetime string (midnight UTC) | Example: `"2024-01-15T00:00:00Z"` |
| TIME | Edm.DateTimeOffset | ISO 8601 datetime string (1970-01-01) | Example: `"1970-01-01T14:30:00Z"` |
| TIMESTAMP | Edm.DateTimeOffset | ISO 8601 datetime string with timezone | Example: `"2024-01-15T14:30:00+00:00"` |
| NaN, ±Infinity | null | JSON null | OData JSON does not represent these; converted to null. |
| STRUCT, JSON, JSONB | Edm.Binary | base64-encoded JSON string | Original type available via `Custom.OriginalType` annotation in metadata. |
| ARRAY\<T\> | Collection(Edm.T) | JSON array | Inner element type inferred from first non-null element. |
| BLOB, VARBINARY, BINARY | Edm.Binary | base64-encoded binary string | Binary data encoded as base64. |

**Dual-interface note:** SQL queries return the same underlying values but with different wire representations (e.g., DECIMAL as a Decimal, not a float). When migrating queries between SQL and OData, verify that precision/type handling is acceptable for your use case.

### Pagination and Result Limits

**Default `$top` value:** If `$top` is not specified, the service defaults to `$top=100`.

**Server-driven paging:** Results exceeding the `$top` limit are truncated, and an `@odata.nextLink` is included in the response. Follow the nextLink to fetch the next page:

```
GET /api/v4/workspace/collection/dataset?$filter=status eq 'active'&$top=50&$skip=0
```

Response includes:
```json
{
  "@odata.context": "/api/v4/$metadata#workspace_collection_dataset",
  "value": [ /* 50 rows */ ],
  "@odata.nextLink": "/api/v4/workspace/collection/dataset?$filter=status%20eq%20%27active%27&$top=50&$skip=50"
}
```

**Max `$top` value:** The maximum allowed `$top` is 25,000 rows per request
(configurable with `ODATA_MAX_TOP`). Exceeding it returns 400.

**`$top=0` behavior:** `$top=0` returns zero rows and is useful with `$count=true` to fetch the total filtered count without data:

```
GET /api/v4/workspace/collection/dataset?$filter=status eq 'active'&$top=0&$count=true
```

Response:
```json
{
  "@odata.context": "/api/v4/$metadata#workspace_collection_dataset",
  "value": [],
  "@odata.count": 12847
}
```

**SQL interface note:** The SQL interface has no implicit `$top` default and returns all matching rows (subject to other limits). When switching between SQL and OData or vice versa, be aware that row counts and result sizes may differ.

### Type Safety for LLM-Generated Queries

When LLMs or automated tools generate OData queries, be aware of these type-coercion behaviors and common pitfalls:

1. **String literals are case-sensitive:**
   ```
   $filter=vendor eq 'Oracle'  ≠  vendor eq 'oracle'
   ```
   Exact case matching is required.

2. **LIKE-pattern escaping:** Functions like `contains()`, `startswith()`, `endswith()` treat `%` and `_` as literal characters, not SQL wildcards:
   ```
   $filter=contains(sku, 'A_B')  # Matches the literal string 'A_B', not 'A' + any-char + 'B'
   ```

3. **Null filtering requires explicit `or x eq null`:** The `ne` operator does not include nulls:
   ```
   $filter=age ne 21              # Excludes null-age rows
   $filter=age ne 21 or age eq null  # Includes null-age rows
   ```

4. **Numeric types in filters:** Do not quote numeric literals:
   ```
   $filter=age gt 21      # Correct
   $filter=age gt '21'    # Incorrect (string comparison, may not work as expected)
   ```

5. **DECIMAL precision:** Large DECIMAL values are converted to float, losing precision beyond ~15 significant digits. If precision is critical, use the SQL interface directly.

6. **Date/datetime literals must be unquoted**, per the OData v4 ABNF:
   ```
   $filter=shipped_date gt 2024-01-01               # Correct
   $filter=shipped_date gt '2024-01-01'             # Incorrect — compared as VARCHAR, raises a type-mismatch error
   ```
   This applies to `Edm.Date` (`2024-01-01`) and `Edm.DateTimeOffset` (`2024-01-01T00:00:00Z`) values alike.

7. **Relative dates need `now()`, not a guessed literal:** A generator with no
   clock has to invent a date bound, which is silently wrong the moment the
   query is re-used. Express the window instead:
   ```
   $filter=created_at ge now() sub duration'P30D'   # Correct: still means "last 30 days" tomorrow
   $filter=created_at ge 2024-05-01                 # Only correct on one particular day
   ```

### Important Notes

- **Case-Sensitive Filtering:** String comparisons in `$filter` are case-sensitive. `eq 'Oracle'` will not match `'oracle'`.
- **Column Names:** Reference columns by their exact names in the dataset schema.
- **Type Coercion:** Numeric values in comparisons should not be quoted (e.g., `age gt 21` not `age gt '21'`).
- **String Values:** Always quote string literals (e.g., `vendor eq 'ABC'`).
- **Date/Datetime Values:** Never quote date or datetime literals (e.g., `shipped_date gt 2024-01-01`, not `shipped_date gt '2024-01-01'`). A quoted date literal is compared as a string and raises a type-mismatch error against a DATE/TIMESTAMP column.
- **Maximum Results & Defaults:** Default `$top` is `100` if not specified. Maximum allowed is 25,000 records per request. While `$top` is optional, we recommend specifying it in client-generated queries to avoid unintended large responses.
- **`$expand` and `Nav/$count` ordering:** `$expand` (navigation property expansion) and ordering by `Nav/$count` are currently **not implemented**. Queries using `$expand` or ordering by `Nav/$count` will produce an error. Use precomputed columns (for example `number_of_moons`) as a workaround for ordering by counts until `$expand` is implemented.
- **Modulo and numeric coercion:** Mixing double and integer operands with `mod` is supported — `round(n_nationkey) mod 5 eq 0` evaluates correctly even though `ROUND()` yields DOUBLE. (Earlier releases raised `IncorrectTypeError` here; numeric promotion for `%` has since landed.)
- **URL encoding:** Always URL-encode query strings (spaces, parentheses, `$` and other special characters). For example: `Satellites/$count%20desc` for `Satellites/$count desc`.

## Permission Model

Access control is enforced at query time:

- **Public datasets** (`public.*`) - Read access for all authenticated users
- **Personal datasets** (`personal.{username}.*`) - Full access for owner only
- **Workspace datasets** - Access based on assigned permissions

Permissions are extracted from your JWT token and passed to the Opteryx query engine for enforcement.

## Using with BI Tools

### Excel
1. Data → Get Data → From OData Feed
2. Enter URL: `https://odata.opteryx.app/api/v4`
3. Choose Basic authentication
4. Enter your credentials

### Power BI
1. Get Data → OData Feed
2. Enter URL: `https://odata.opteryx.app/api/v4`
3. Select Basic authentication
4. Enter your credentials

### Tableau
1. Connect → OData
2. Server: `odata.opteryx.app`
3. Path: `/api/v4`
4. Authentication: Username and Password

## Error Responses

All v4 endpoints return errors in OData format:

```json
{
  "error": {
    "code": "Forbidden",
    "message": "Access denied to dataset workspace.collection.dataset"
  }
}
```

Error codes:

| Status | `error.code` | Meaning |
|---|---|---|
| 400 | `BadRequest` | Malformed `$filter`, `$top` outside `0..25000`, duplicated query parameter, invalid `$apply`, or a malformed `@{label}` version selector |
| 401 | `Unauthorized` | Missing or invalid authentication |
| 403 | `Forbidden` | Valid auth but insufficient permissions |
| 404 | `NotFound` | Dataset does not exist or is not accessible, or an `@{label}` version selector names a tag, snapshot, or previous version that does not exist |
| 500 | `InternalServerError` | Server error |
| 501 | `Error` | `$search` or `$expand` requested — not implemented |
| 502 | `BadGateway` | Upstream dependency returned an error |
| 503 | `ServiceUnavailable` | Dependent service unavailable |
| 504 | `GatewayTimeout` | Dependent service (e.g. Firestore) unreachable |

Statuses without an explicit mapping — 501 among them — report `error.code` as
the generic `"Error"`; see `_map_status_to_code` in `app/routes/v4/errors.py`.

---

## Development

### Local Development

Run the service locally:

```bash
make run
```

Or with uvicorn directly:

```bash
uvicorn app.main:service --reload --host 0.0.0.0 --port 8890
```

### Testing

Run the test script:

```bash
# Set credentials via environment variables
export ODATA_USERNAME="your_username"
export ODATA_PASSWORD="your_password"

# Test v4 endpoints
python scripts/test_odata.py --api-version v4 --workstream public --collection demo --dataset users

# Test service document
python scripts/test_odata.py --api-version v4 --test-service-doc

# Test metadata
python scripts/test_odata.py --api-version v4 --test-metadata
```

Run the test suite:

```bash
make t
```

This runs everything under `tests/` and is exactly what CI runs. It needs no
credentials and no network: queries go against the small local TPCH fixtures in
`testdata/` (see [testdata/README.md](testdata/README.md)) and Opteryx's built-in
`$planets` dataset. Tests that genuinely need a live Firestore/GCS catalog are
marked `requires_catalog` and skip automatically when no credentials resolve —
so a green run with skips is the expected local and CI result.

`tests/manual/` holds exploratory developer scripts. They are gitignored, not
collected by pytest, and several reference APIs that no longer exist.

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `GCP_PROJECT_ID` | GCP project ID for Firestore/GCS | Required |
| `FIRESTORE_DATABASE` | Firestore database name | Required |
| `GCS_BUCKET` | GCS bucket for data storage | Required |
| `ODATA_DEFAULT_TOP` | Default $top value when not specified | `100` |
| `ODATA_MAX_TOP` | Maximum accepted $top value | `25000` |
| `CORS_ALLOW_ALL` | Allow all CORS origins. Development only — the allowlist in `app/middleware/cors.py` is used otherwise | `false` |

### Docker Build

Build the Docker image:

```bash
docker build -f Dockerfile -t gcr.io/$PROJECT_ID/opteryx-odata .
```

### Architecture

The service architecture:

1. **Authentication Layer** (`app/auth/`)
   - External auth service integration
   - JWT token validation
   - Claims normalization

2. **Permission Handling** (`app/auth/permissions.py`)
   - Convert JWT permissions to Opteryx access policies
   - Row-level security enforcement

3. **OData v4 Routes** (`app/routes/v4/`)
   - Service document with filtered datasets
   - EDMX metadata generation
   - Data query endpoints with OData compliance

4. **Query Engine Integration**
   - Opteryx connector for GCS/Firestore
   - Access policies passed to every query
   - Automatic permission enforcement

Authentication setup is documented in [AUTH_SETUP.md](AUTH_SETUP.md).

## Documentation

- **[Querying via OData](https://docs.opteryx.app/docs/guides/querying-via-odata)** — user guide, with a live "Try it" panel
- [Opteryx documentation](https://docs.opteryx.app) — the full docs site

## Licence

Licensed under the Apache Licence 2.0 — see [LICENSE.md](LICENSE.md).

This repository vendors [odata-query](https://github.com/gorilla-co/odata-query)
under `app/vendored/odata-query/`, which is MIT licensed; its licence and
copyright notice are retained in that directory.