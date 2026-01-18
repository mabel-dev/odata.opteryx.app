#!/usr/bin/env python3
"""
Test that the ODataLogicalPlanBuilder integrates properly with interface.py
This tests the validation flow without running the full HTTP server
"""

import sys
sys.path.insert(0, "/Users/justin/Nextcloud/opteryx-cloud/odata.opteryx")
sys.path.insert(0, "/Users/justin/Nextcloud/opteryx-cloud/odata.opteryx/app/vendored/odata-query")

from app.odata.odata_to_logical_plan import ODataLogicalPlanBuilder

# Simulate what interface.py does now
def test_odata_parameter_validation():
    """Test that OData parameters are validated through the builder"""
    
    test_cases = [
        {
            "name": "Valid simple filter",
            "table": "workstream.collection.products",
            "filter": "vendor eq 'Microsoft'",
            "select": "Name, Price",
            "orderby": None,
            "top": 10,
            "skip": 0,
            "should_pass": True,
        },
        {
            "name": "Invalid filter - should error",
            "table": "workstream.collection.products",
            "filter": "vendor (invalid syntax here",
            "select": None,
            "orderby": None,
            "top": 10,
            "skip": 0,
            "should_pass": False,
        },
        {
            "name": "Valid aggregation query",
            "table": "workstream.collection.orders",
            "filter": None,
            "select": None,
            "orderby": "TotalAmount desc",
            "top": 1,
            "skip": 0,
            "should_pass": True,
        },
    ]
    
    print("=" * 80)
    print("TESTING ODATA PARAMETER VALIDATION")
    print("=" * 80)
    print()
    
    passed = 0
    failed = 0
    
    for test_case in test_cases:
        print(f"Test: {test_case['name']}")
        try:
            builder = ODataLogicalPlanBuilder(test_case['table'])
            plan = builder.build(
                filter_expr=test_case['filter'],
                select_cols=test_case['select'],
                orderby_expr=test_case['orderby'],
                top=test_case['top'],
                skip=test_case['skip'],
            )
            
            if test_case['should_pass']:
                print("✓ PASS - Validation succeeded as expected")
                passed += 1
            else:
                print("✗ FAIL - Expected validation error but got success")
                failed += 1
                
        except Exception as e:
            if not test_case['should_pass']:
                print(f"✓ PASS - Validation failed as expected: {type(e).__name__}")
                passed += 1
            else:
                print(f"✗ FAIL - Unexpected error: {e}")
                failed += 1
        
        print("-" * 80)
        print()
    
    print("=" * 80)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 80)
    
    return failed == 0

if __name__ == "__main__":
    success = test_odata_parameter_validation()
    sys.exit(0 if success else 1)
