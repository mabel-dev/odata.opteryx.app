#!/usr/bin/env python3
"""Test OData filter conversion."""

from app.routes.v4.interface import _convert_odata_filter_to_sql

# Test cases
test_cases = [
    ("actor_login eq 'Copilot'", "LOWER(actor_login) = LOWER('Copilot')"),
    ("actor_login eq 'copilot'", "LOWER(actor_login) = LOWER('copilot')"),
    ("age gt 25", "age > 25"),
    ("name contains 'test'", "name ILIKE '%test%'"),
    ("status eq 'active' and age gt 21", "LOWER(status) = LOWER('active') AND age > 21"),
]

print("Testing OData filter conversion:")
print("=" * 80)

for odata_filter, expected in test_cases:
    result = _convert_odata_filter_to_sql(odata_filter)
    status = "✓" if result == expected else "✗"
    print(f"{status} Input:    {odata_filter}")
    print(f"  Expected: {expected}")
    print(f"  Got:      {result}")
    if result != expected:
        print("  MISMATCH!")
    print()
