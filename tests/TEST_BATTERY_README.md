# OData Test Battery

A simple, fast test battery for OData query parsing and execution. Validates query shape (rows, columns) without needing production data or manual verification.

## Format

Test cases are tuples: `(dataset, query, expected_rows, expected_columns, expected_error)`

```python
QUICK_TEST_BATTERY = [
    # (dataset, query, expected_rows, expected_columns, expected_error)
    ("public.github.events", "$top=10", 10, None, None),
    ("public.github.events", "$orderby=published_at desc&$top=1", 1, None, None),
    ("public.github.events", "$select=id,name&$top=5", 5, 2, None),
    ("public.github.events", "$orderby=invalid_col&$top=1", None, None, "Exception"),
]
```

**Fields:**
- `dataset` - Table name (e.g., `"public.github.events"`)
- `query` - OData query string with parameters (e.g., `"$orderby=published_at desc&$top=1"`)
- `expected_rows` - Expected number of rows returned (or `None` if variable/unknown)
- `expected_columns` - Expected number of columns (or `None` if not checked)
- `expected_error` - Expected error type (e.g., `"Exception"`, `"ValueError"`, or `None` if no error)

## Running the Battery

### Quick test (should complete in seconds)
```bash
python test_odata_battery_example.py
```

### With pytest
```bash
pytest test_odata_battery_example.py -v
```

### Get detailed results
```python
from test_odata_battery_example import run_battery, QUICK_TEST_BATTERY

summary = run_battery(QUICK_TEST_BATTERY)
print(f"Passed: {summary['passed']}/{summary['total']}")
```

## Building Your Own Battery

1. **Quick battery** (15 seconds, ~100-500 tests)
   - Basic queries: `$top`, `$orderby`, `$filter`, `$select`
   - Combinations of parameters
   - Invalid queries (should error)

2. **Extended battery** (1-2 minutes, ~1000+ tests)
   - More parameter combinations
   - Edge cases
   - Different datasets

3. **Content battery** (5-10 minutes, ~500 tests)
   - Verify exact row/column values
   - Sorting is correct
   - Filtering produces right results

## Example Test Case

```python
# Basic orderby test
("public.github.events", "$orderby=published_at desc&$top=1", 1, None, None)
# Expected: 1 row, any number of columns, no error

# Select specific columns
("public.github.events", "$select=id,name&$top=5", 5, 2, None)
# Expected: 5 rows, exactly 2 columns, no error

# Invalid column should error
("public.github.events", "$orderby=invalid_column&$top=1", None, None, "Exception")
# Expected: any error of type Exception
```

## Integration with CI/CD

Add to your test suite:

```yaml
# .github/workflows/test.yml
- name: Run OData Battery
  run: python test_odata_battery_example.py
```

Fails the build if any test fails, so regressions are caught immediately.

## Tips

- Use `expected_rows = None` when row count varies (e.g., filtering with unknown results)
- Use `expected_columns = None` when you don't care about column count validation
- Use `expected_error = "Exception"` for any exception type
- Use `expected_error = "ValueError"` for specific error type validation
- Query strings can include `$filter`, `$orderby`, `$select`, `$top`, `$skip`, `$count`

## Output

```
================================================================================
Running OData Quick Test Battery
================================================================================

Passed:  42/45
Failed:  3/45
Time:    2.34s

Failed tests:
  #15: Column count mismatch: got 5, expected 2
  #23: Row count mismatch: got 8, expected 10
  #44: Expected error 'ValueError' but got 'TypeError'
```
