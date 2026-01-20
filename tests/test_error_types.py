"""Test to see what error types are raised for invalid columns"""

import os
import sys

from tests.test_odata_battery import run_odata_battery_test

sys.path.insert(1, os.path.join(sys.path[0], "../opteryx-core"))


# Test #95: invalid column in $orderby
print("Test #95: $orderby=invalid_column")
result = run_odata_battery_test(
    "$planets", None, "invalid_column", None, 1, None, False, None, None, "Exception"
)
print(f"  Error type: {result['error']}")
print(f"  Reason: {result['reason']}")
print()

# Test #96: invalid column in $filter
print("Test #96: $filter=invalid_column eq 1")
result = run_odata_battery_test(
    "$planets", "invalid_column eq 1", None, None, None, None, False, None, None, "Exception"
)
print(f"  Error type: {result['error']}")
print(f"  Reason: {result['reason']}")
print()

# Test #97: invalid column in $select
print("Test #97: $select=invalid_column")
result = run_odata_battery_test(
    "$planets", None, None, "invalid_column", 1, None, False, None, None, "Exception"
)
print(f"  Error type: {result['error']}")
print(f"  Reason: {result['reason']}")
print()

# Test #99: double && in $orderby
print("Test #99: $orderby=name&&$top=1")
result = run_odata_battery_test(
    "$planets", None, "name", None, 1, None, False, None, None, "Exception"
)
print(f"  Error type: {result['error']}")
print(f"  Reason: {result['reason']}")
