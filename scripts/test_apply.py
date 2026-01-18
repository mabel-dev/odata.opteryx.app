#!/usr/bin/env python3
"""
Test ODataLogicalPlanBuilder $apply aggregation parsing
"""

import sys
sys.path.insert(0, "/Users/justin/Nextcloud/opteryx-cloud/odata.opteryx")
sys.path.insert(0, "/Users/justin/Nextcloud/opteryx-cloud/odata.opteryx/app/vendored/odata-query")

from app.odata.odata_to_logical_plan import ODataLogicalPlanBuilder

# Test cases for $apply
test_cases = [
    ("groupby((Vendor), aggregate(sum(Price) as TotalPrice))", "Simple groupby with sum"),
    ("groupby((Category), aggregate(count(*) as ItemCount))", "Count aggregate"),
    ("groupby((Region, Category), aggregate(avg(Price) as AvgPrice, sum(Quantity) as TotalQty))", "Multiple groupby with multiple aggregates"),
]

builder = ODataLogicalPlanBuilder("workstream.collection.products")

print("=" * 80)
print("TESTING ODATA LOGICAL PLAN BUILDER - $APPLY AGGREGATION PARSING")
print("=" * 80)
print()

for apply_expr, description in test_cases:
    print(f"Apply: {apply_expr}")
    print(f"Description: {description}")
    try:
        # Parse the aggregates
        aggregates = builder._parse_aggregates(apply_expr.split("aggregate(")[1].rstrip(")"))
        
        print(f"Parsed aggregates:")
        for agg in aggregates:
            print(f"  - {agg['function']}({agg['expression']}) as {agg['alias']}")
        
        print("✓ PASS")
    except Exception as e:
        print(f"✗ ERROR: {e}")
        import traceback
        traceback.print_exc()
    
    print("-" * 80)
    print()
