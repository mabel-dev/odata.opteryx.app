# OData v4 LLM Directives — FOLLOW THESE EXACT RULES

Base URL: `GET https://odata.opteryx.app/api/v4/{workspace}/{collection}/{dataset}?{params}`

## Query Parameters

- `$filter` - Filter records: comparison (`eq`, `ne`, `gt`, `ge`, `lt`, `le`), logical (`and`, `or`, `not`), string functions (`contains()`, `startswith()`, `endswith()`)
- `$select` - Columns to return: `$select=col1,col2,col3`
- `$orderby` - Sort: `$orderby=col1 asc, col2 desc`
- `$top` - Optional (default `100`). Limit results (0-10000): `$top=50`. LLMs should include an explicit `$top` to avoid returning excessively large result sets.
- `$skip` - Offset for pagination: `$skip=100`
- `$count` - Include total count: `$count=true`
- `$apply` - Aggregation: `$apply=groupby((col), aggregate(amount with sum as Total))`

## Key Rules

1. **MANDATORY — `$top` MUST be provided**. Use integer 1–10000. If the user does not supply `$top`, choose a conservative default (e.g., `$top=50`).
2. **String values are case-sensitive**: `vendor eq 'Oracle'` ≠ `vendor eq 'oracle'`
3. **Quote strings, not numbers**: `status eq 'active'` and `age gt 21`
4. **Dates/datetimes are unquoted, not strings**: `shipped_date gt 2024-01-01` or `created_at ge 2024-01-01T00:00:00Z`. Quoting a date (`shipped_date gt '2024-01-01'`) compares it as text and raises a type-mismatch error against a DATE/TIMESTAMP column.
5. **Use exact column names** from schema
6. **Use `$orderby` with pagination** for consistent results
7. **`$expand` MUST NOT be generated**.
8. **Null filtering requires explicit `or x eq null`**: The `ne` operator excludes nulls (SQL 3VL semantics). Use `(age ne 21) or (age eq null)` to include nulls.
9. **LIKE patterns are literal**: Functions like `contains()`, `startswith()`, `endswith()` do NOT treat `%` or `_` as wildcards. Example: `contains(sku, 'A_B')` matches the literal string `'A_B'`, not a pattern.

## Common Patterns

**Filter & select:**
```
$filter=status eq 'active' and age gt 21&$select=name,email,age&$top=50
```

**Sort & limit:**
```
$orderby=salary desc&$top=10&$select=name,department,salary
```

**Aggregation (count by group):**
```
$apply=groupby((department), aggregate($count as Count))
```

**Multiple aggregations:**
```
$apply=groupby((region,product), aggregate(sales with sum as Total, price with average as Avg, $count as Records))
```

**Top N by metric:**
```
$apply=groupby((product), aggregate(revenue with sum as Revenue))&$orderby=Revenue desc&$top=5
```

OUTPUT FORMAT (REQUIRED)

When asked to GENERATE a query string, output EXACTLY one URL-encoded query string on a single line and nothing else. Example:
```
?$select=name%2Cnumber_of_moons&$orderby=number_of_moons%20desc&$top=1
```

Supported functions you may use directly (in `$filter` expressions):
- Comparison: `eq`, `ne`, `gt`, `ge`, `lt`, `le`
- Logical: `and`, `or`, `not`
- String funcs: `contains()`, `startswith()`, `endswith()`, `tolower()`, `toupper()`, `indexof()`, `substring()`, `length()`

(Aggregates are only used in `$apply` expressions, see Aggregation Functions below.)

## Aggregation Functions

In `$apply` expressions, aggregations use the format: `column with method as alias` or `$count as alias`

Supported methods:
- `sum` - sum numeric values: `amount with sum as Total`
- `average` - arithmetic mean: `price with average as AvgPrice`
- `min` - minimum value: `value with min as MinVal`
- `max` - maximum value: `value with max as MaxVal`
- `count` - count non-null values: `status with count as StatusCount`
- `$count` - count all records: `$count as TotalCount`

Example: `$apply=groupby((region), aggregate(revenue with sum as Total, $count as Count, price with average as AvgPrice))`
