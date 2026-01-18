# OData v4 Query Syntax for LLMs

Base URL: `GET /api/v4/{workspace}/{collection}/{dataset}?{params}`

## Query Parameters

- `$filter` - Filter records: comparison (`eq`, `ne`, `gt`, `ge`, `lt`, `le`), logical (`and`, `or`, `not`), string functions (`contains()`, `startswith()`, `endswith()`)
- `$select` - Columns to return: `$select=col1,col2,col3`
- `$orderby` - Sort: `$orderby=col1 asc, col2 desc`
- `$top` - **Required**. Limit results (0-10000): `$top=50`
- `$skip` - Offset for pagination: `$skip=100`
- `$count` - Include total count: `$count=true`
- `$apply` - Aggregation: `$apply=groupby((col), aggregate(sum(amount) as Total))`

## Key Rules

1. **`$top` is required**: Must specify value between 0-10000
2. **String values are case-sensitive**: `vendor eq 'Oracle'` ≠ `vendor eq 'oracle'`
3. **Quote strings, not numbers**: `status eq 'active'` and `age gt 21`
4. **Use exact column names** from schema
5. **Use `$orderby` with pagination** for consistent results

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

## Aggregation Functions

`sum()`, `count(*)`, `count(col)`, `avg()`, `min()`, `max()`

## Error Codes

- `400 BadRequest` - Invalid syntax or unknown column
- `401 Unauthorized` - Missing credentials
- `403 Forbidden` - No access to dataset
- `404 NotFound` - Dataset doesn't exist
