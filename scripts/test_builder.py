#!/usr/bin/env python3
"""
Test ODataLogicalPlanBuilder expression parser
"""

import sys
sys.path.insert(0, "/Users/justin/Nextcloud/opteryx-cloud/odata.opteryx")
sys.path.insert(0, "/Users/justin/Nextcloud/opteryx-cloud/odata.opteryx/app/vendored/odata-query")

from app.odata.odata_to_logical_plan import ODataLogicalPlanBuilder

# Test cases
test_cases = [
    ("vendor eq 'Oracle'", "Simple equality"),
    ("Price gt 100", "Numeric comparison"),
    ("vendor eq 'Oracle' and Price gt 100", "AND operator"),
    ("Status eq 'Active' or Status eq 'Inactive'", "OR operator"),
    ("not (Status eq 'Deleted')", "NOT operator"),
    ("Name ne null", "NULL comparison"),
]

builder = ODataLogicalPlanBuilder("workstream.collection.products")

print("=" * 80)
print("TESTING ODATA LOGICAL PLAN BUILDER - EXPRESSION PARSER")
print("=" * 80)
print()

for filter_expr, description in test_cases:
    print(f"Filter: {filter_expr}")
    print(f"Description: {description}")
    try:
        # Parse the expression
        expr_node = builder._parse_filter_expression(filter_expr)
        
        print(f"Result: {expr_node}")
        print(f"Node Type: {expr_node.node_type}")
        print(f"Value: {expr_node.value}")
        if hasattr(expr_node, 'left'):
            print(f"Left: {expr_node.left}")
        if hasattr(expr_node, 'right'):
            print(f"Right: {expr_node.right}")
        
        print("✓ PASS")
    except Exception as e:
        print(f"✗ ERROR: {e}")
        import traceback
        traceback.print_exc()
    
    print("-" * 80)
    print()
