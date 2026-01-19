# OData Unit Testing Framework

## Overview

Extracted the core OData query execution logic into a standalone `execute_odata_query()` function that can be tested without:
- Authentication/JWT tokens
- HTTP requests
- Request/Response objects
- Mocking Opteryx

This enables fast, comprehensive testing of the OData→logical plan→query pipeline.

## The Function: `execute_odata_query()`

**Location:** `app/routes/v4/interface.py`

**Signature:**
```python
def execute_odata_query(
    table_name: str,
    filter_expr: Optional[str] = None,
    orderby_expr: Optional[str] = None,
    select_cols: Optional[str] = None,
    top: Optional[int] = None,
    skip: Optional[int] = None,
    count_requested: bool = False,
) -> dict:
```

**Returns:**
```python
{
    "rows": list[dict],           # Result rows
    "col_names": list[str],       # Column names
    "total_count": Optional[int], # Total count if requested
    "took_ms": float,             # Execution time in milliseconds
}
```

## How It Works

1. **No HTTP layer** - Direct Python function call
2. **No auth needed** - No JWT validation or claims extraction
3. **Fast iteration** - Run thousands of test queries in seconds
4. **Exact OData semantics** - Tests the real conversion pipeline
5. **Easy to integrate with test runners** - pytest, unittest, etc.

## Usage Examples

### Basic Query
```python
from app.routes.v4.interface import execute_odata_query

result = execute_odata_query(
    table_name="public.github.events",
    top=10
)
print(f"Got {len(result['rows'])} rows in {result['took_ms']:.2f}ms")
```

### With Filtering and Ordering
```python
result = execute_odata_query(
    table_name="public.examples.vulnerabilities",
    filter_expr="actor_login eq 'Copilot'",
    orderby_expr="published_at desc",
    top=1,
    count_requested=True,
)
print(f"Total count: {result['total_count']}")
```

### In Unit Tests
```python
def test_orderby_desc():
    result = execute_odata_query(
        table_name="public.github.events",
        orderby_expr="published_at desc",
        top=5,
    )
    assert len(result["rows"]) <= 5
    assert result["took_ms"] >= 0
```

## How `read_dataset()` Uses It

The HTTP endpoint `read_dataset()` now simply:
1. Validates parameters ($top range, etc.)
2. Calls `execute_odata_query()` with the parsed parameters
3. Formats the response for OData compliance

This means all the actual logic is in the testable function.

## Testing Approach (Matching Opteryx Pattern)

### Phase 1: Quick Unit Tests (~15 seconds)
Create 2000 test cases covering OData combinations:
- Basic filters: `$filter=column eq 'value'`
- Orderby: `$orderby=column asc/desc`
- Pagination: `$top=10&$skip=5`
- Selection: `$select=col1,col2`
- Combinations of above

All should execute without errors, column schema should match expected.

### Phase 2: Content Tests (slower)
Create 500 test cases that verify result data:
- Exact row values match expected
- Sort order is correct
- Filters produce expected results
- Counts are accurate

## Example Test File

See `test_odata_query_unit.py` for a comprehensive test battery template.

Run it:
```bash
pytest test_odata_query_unit.py -v
pytest test_odata_query_unit.py -k "orderby" -v  # Run specific tests
```

## Benefits

1. **Fast feedback** - 2000 tests in ~15 seconds instead of deploying to production
2. **Debug-friendly** - Add breakpoints, inspect logical plans, see exact SQL generated
3. **No dependencies** - Doesn't require Firestore, GCS, auth service, etc.
4. **Regression prevention** - Catches OData parsing bugs immediately
5. **Better error messages** - Errors are from the actual code, not mocked layers

## Next Steps

1. Create test data mapping from existing Opteryx SQL tests
2. Generate 2000 OData query variants
3. Build CI/CD step that runs these tests on every commit
4. Add content verification tests
5. Track coverage metrics (which OData features are tested)
