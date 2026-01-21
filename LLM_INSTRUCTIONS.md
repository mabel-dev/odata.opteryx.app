# OData v4 LLM Directives — FOLLOW THESE EXACT RULES

Base URL: `GET https://odata.opteryx.app/api/v4/{workspace}/{collection}/{dataset}?{params}`

## Query Parameters

- `$filter` - Filter records: comparison (`eq`, `ne`, `gt`, `ge`, `lt`, `le`), logical (`and`, `or`, `not`), string functions (`contains()`, `startswith()`, `endswith()`)
- `$select` - Columns to return: `$select=col1,col2,col3`
- `$orderby` - Sort: `$orderby=col1 asc, col2 desc`
- `$top` - Optional (default `100`). Limit results (0-10000): `$top=50`. LLMs should include an explicit `$top` to avoid returning excessively large result sets.
- `$skip` - Offset for pagination: `$skip=100`
- `$count` - Include total count: `$count=true`
- `$apply` - Aggregation: `$apply=groupby((col), aggregate(sum(amount) as Total))`

## Key Rules

1. **MANDATORY — `$top` MUST be provided**. Use integer 1–10000. If the user does not supply `$top`, choose a conservative default (e.g., `$top=50`).
2. **String values are case-sensitive**: `vendor eq 'Oracle'` ≠ `vendor eq 'oracle'`
3. **Quote strings, not numbers**: `status eq 'active'` and `age gt 21`
4. **Use exact column names** from schema
5. **Use `$orderby` with pagination** for consistent results
6. **`$expand` MUST NOT be generated**.

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
$apply=groupby((department), aggregate(count(*) as Count))
```

**Multiple aggregations:**
```
$apply=groupby((region,product), aggregate(sum(sales) as Total, avg(price) as Avg, count(*) as Records))
```

**Top N by metric:**
```
$apply=groupby((product), aggregate(sum(revenue) as Revenue))&$orderby=Revenue desc&$top=5
```

OUTPUT FORMAT (REQUIRED)

When asked to GENERATE a query string, output EXACTLY one URL-encoded query string on a single line and nothing else. Example:
```
?$select=name%2Cnumber_of_moons&$orderby=number_of_moons%20desc&$top=1
```

Supported functions you may use directly:
- Comparison: `eq`, `ne`, `gt`, `ge`, `lt`, `le`
- Logical: `and`, `or`, `not`
- String funcs: `contains()`, `startswith()`, `endswith()`, `tolower()`, `toupper()`, `indexof()`, `substring()`, `length()`
- Aggregates: `sum()`, `count(*)`, `count(col)`, `avg()`, `min()`, `max()`

## Aggregation Functions

`sum()`, `count(*)`, `count(col)`, `avg()`, `min()`, `max()`
