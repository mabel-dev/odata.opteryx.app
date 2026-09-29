"""Phase 3 Integration Tests: Complete Logical Plan Creation

Tests that OData queries build correct complete logical plans with all nodes
connected properly. Validates the full conversion pipeline from OData parameters
to Opteryx logical plans.
"""

import sys
from pathlib import Path

from opteryx.expression import NodeType
from opteryx.planner.logical_planner import LogicalPlanStepType

from app.odata.odata_to_logical_plan import build_logical_plan

# Add app directory to path
sys.path.insert(0, str(Path(__file__).parent / "app"))


def validate_plan_structure(plan, expected_node_types, test_name):
    """Helper to validate plan node types and structure."""
    print(f"\nTest: {test_name}")
    print("-" * 80)

    # Get all node IDs
    node_ids = list(plan.nodes())
    actual_count = len(node_ids)
    expected_count = len(expected_node_types)

    print(f"  Node count: {actual_count} (expected {expected_count})")

    if actual_count != expected_count:
        print(f"  ✗ FAIL: Expected {expected_count} nodes, got {actual_count}")
        return False

    print(f"  ✓ Node count correct: {actual_count}")

    # Validate node types
    print("\n  Node structure:")
    all_match = True
    for idx, node_id in enumerate(node_ids):
        node = plan[node_id]
        expected_type = expected_node_types[idx]
        actual_type = node.node_type

        match = "✓" if actual_type == expected_type else "✗"
        print(f"    {idx + 1}. {match} {actual_type.name} (expected {expected_type.name})")

        if actual_type != expected_type:
            all_match = False

    if all_match:
        print("\n  ✓✓ PASS: All nodes correct")
        return True
    else:
        print("\n  ✗✗ FAIL: Node types mismatch")
        return False


def test_3_1_simple_select():
    """Test 3.1: Simple SELECT * FROM planets (no query options)

    Expected plan: Scan → Exit (no Project without $select)
    """
    plan = build_logical_plan(
        table_name="planets",
        filter_expr=None,
        select_cols=None,
        orderby_expr=None,
        top=None,
        skip=None,
    )

    expected_types = [LogicalPlanStepType.Scan, LogicalPlanStepType.Exit]

    return validate_plan_structure(plan, expected_types, "Simple SELECT * FROM planets")


def test_3_2_select_with_filter():
    """Test 3.2: SELECT with $filter

    Expected plan: Scan → Filter → Exit
    """
    plan = build_logical_plan(
        table_name="planets",
        filter_expr="id eq 1",
        select_cols=None,
        orderby_expr=None,
        top=None,
        skip=None,
    )

    expected_types = [
        LogicalPlanStepType.Scan,
        LogicalPlanStepType.Filter,
        LogicalPlanStepType.Exit,
    ]

    result = validate_plan_structure(plan, expected_types, "SELECT with $filter=id eq 1")

    if result:
        # Additional validation: check filter condition
        node_ids = list(plan.nodes())
        filter_node = plan[node_ids[1]]  # Should be Filter node

        if filter_node.condition is not None:
            print("  ✓ Filter condition present")
            print(f"    Condition type: {filter_node.condition.node_type}")
            if filter_node.condition.node_type == NodeType.COMPARISON_OPERATOR:
                print(f"    Operator: {filter_node.condition.value}")
                print("  ✓ Filter structure correct")
            else:
                print("  ✗ Filter condition not a comparison")
                return False
        else:
            print("  ✗ Filter condition is None")
            return False

    return result


def test_3_3_select_with_orderby():
    """Test 3.3: SELECT with $orderby

    Expected plan: Scan → Order → Exit
    """
    plan = build_logical_plan(
        table_name="planets",
        filter_expr=None,
        select_cols=None,
        orderby_expr="id asc",
        top=None,
        skip=None,
    )

    expected_types = [LogicalPlanStepType.Scan, LogicalPlanStepType.Order, LogicalPlanStepType.Exit]

    result = validate_plan_structure(plan, expected_types, "SELECT with $orderby=id asc")

    if result:
        # Additional validation: check order columns
        node_ids = list(plan.nodes())
        order_node = plan[node_ids[1]]  # Should be Order node

        if order_node.order_by and len(order_node.order_by) > 0:
            print(f"  ✓ Order columns present: {len(order_node.order_by)}")
            col, direction, _nulls_first = order_node.order_by[0]
            print(f"    Column: {col.current_name}")
            print(f"    Direction: {'ASC' if direction else 'DESC'}")
            if direction is True:  # True = ASC
                print("  ✓ Order direction correct (ASC)")
            else:
                print("  ✗ Order direction incorrect")
                return False
        else:
            print("  ✗ Order columns empty")
            return False

    return result


def test_3_4_select_with_filter_and_orderby():
    """Test 3.4: SELECT with $filter and $orderby

    Expected plan: Scan → Filter → Order → Exit
    """
    plan = build_logical_plan(
        table_name="planets",
        filter_expr="id gt 1",
        select_cols=None,
        orderby_expr="name desc",
        top=None,
        skip=None,
    )

    expected_types = [
        LogicalPlanStepType.Scan,
        LogicalPlanStepType.Filter,
        LogicalPlanStepType.Order,
        LogicalPlanStepType.Exit,
    ]

    result = validate_plan_structure(
        plan, expected_types, "SELECT with $filter=id gt 1 and $orderby=name desc"
    )

    if result:
        node_ids = list(plan.nodes())

        # Validate filter
        filter_node = plan[node_ids[1]]
        if filter_node.condition and filter_node.condition.value == "Gt":
            print("  ✓ Filter condition correct (Gt)")
        else:
            print("  ✗ Filter condition incorrect")
            return False

        # Validate order
        order_node = plan[node_ids[2]]
        if order_node.order_by and len(order_node.order_by) > 0:
            col, direction, _nulls_first = order_node.order_by[0]
            if direction is False:  # False = DESC
                print("  ✓ Order direction correct (DESC)")
            else:
                print("  ✗ Order direction should be DESC")
                return False
        else:
            print("  ✗ Order columns missing")
            return False

    return result


def test_3_5_select_with_limit():
    """Test 3.5: SELECT with $top and $skip

    Expected plan: Scan → Limit → Exit
    """
    # Test with just $top
    plan = build_logical_plan(
        table_name="planets",
        filter_expr=None,
        select_cols=None,
        orderby_expr=None,
        top=10,
        skip=None,
    )

    expected_types = [LogicalPlanStepType.Scan, LogicalPlanStepType.Limit, LogicalPlanStepType.Exit]

    result = validate_plan_structure(plan, expected_types, "SELECT with $top=10")

    if result:
        node_ids = list(plan.nodes())
        limit_node = plan[node_ids[1]]

        if limit_node.limit == 10:
            print("  ✓ Limit value correct: 10")
        else:
            print(f"  ✗ Limit value incorrect: {limit_node.limit}")
            return False

        # Offset can be None or 0 when no $skip provided
        if limit_node.offset in (0, None):
            print(f"  ✓ Offset value correct: {limit_node.offset}")
        else:
            print(f"  ✗ Offset value incorrect: {limit_node.offset}")
            return False

    # Test with $top and $skip
    print()
    plan2 = build_logical_plan(
        table_name="planets", filter_expr=None, select_cols=None, orderby_expr=None, top=10, skip=5
    )

    result2 = validate_plan_structure(plan2, expected_types, "SELECT with $top=10&$skip=5")

    if result2:
        node_ids = list(plan2.nodes())
        limit_node = plan2[node_ids[1]]

        if limit_node.limit == 10 and limit_node.offset == 5:
            print("  ✓ Limit and offset correct: limit=10, offset=5")
        else:
            print(
                f"  ✗ Limit/offset incorrect: limit={limit_node.limit}, offset={limit_node.offset}"
            )
            return False

    return result and result2


def test_3_6_complex_query():
    """Test 3.6: Complex query with $filter, $orderby, and $top

    Expected plan: Scan → Filter → Order → Limit → Exit
    """
    plan = build_logical_plan(
        table_name="planets",
        filter_expr="id gt 1",
        select_cols=None,
        orderby_expr="name asc",
        top=5,
        skip=None,
    )

    expected_types = [
        LogicalPlanStepType.Scan,
        LogicalPlanStepType.Filter,
        LogicalPlanStepType.Order,
        LogicalPlanStepType.Limit,
        LogicalPlanStepType.Exit,
    ]

    result = validate_plan_structure(
        plan, expected_types, "Complex: $filter=id gt 1, $orderby=name asc, $top=5"
    )

    if result:
        node_ids = list(plan.nodes())

        # Validate all nodes present and correct
        filter_node = plan[node_ids[1]]
        order_node = plan[node_ids[2]]
        limit_node = plan[node_ids[3]]

        checks = []

        # Check filter
        if filter_node.condition and filter_node.condition.value == "Gt":
            print("  ✓ Filter correct")
            checks.append(True)
        else:
            print("  ✗ Filter incorrect")
            checks.append(False)

        # Check order
        if order_node.order_by and order_node.order_by[0][1] is True:
            print("  ✓ Order correct (ASC)")
            checks.append(True)
        else:
            print("  ✗ Order incorrect")
            checks.append(False)

        # Check limit
        if limit_node.limit == 5:
            print("  ✓ Limit correct (5)")
            checks.append(True)
        else:
            print("  ✗ Limit incorrect")
            checks.append(False)

        result = all(checks)

    return result


def main():
    """Run all Phase 3 integration tests."""
    print("=" * 80)
    print("Phase 3: Integration Tests - Complete Logical Plan Creation")
    print("=" * 80)

    tests = [
        ("3.1", test_3_1_simple_select),
        ("3.2", test_3_2_select_with_filter),
        ("3.3", test_3_3_select_with_orderby),
        ("3.4", test_3_4_select_with_filter_and_orderby),
        ("3.5", test_3_5_select_with_limit),
        ("3.6", test_3_6_complex_query),
    ]

    results = []
    for test_num, test_func in tests:
        try:
            result = test_func()
            results.append((test_num, result))
        except Exception as e:
            print(f"\n  ✗✗ EXCEPTION in test {test_num}: {e}")
            import traceback

            traceback.print_exc()
            results.append((test_num, False))

    # Summary
    print("\n" + "=" * 80)
    passed = sum(1 for _, result in results if result)
    total = len(results)
    print(f"Results: {passed}/{total} passed ({100 * passed // total}%)")

    if passed == total:
        print("✓✓ Phase 3 COMPLETE: All integration tests passing!")
    else:
        print("Some tests failed:")
        for test_num, result in results:
            status = "✓" if result else "✗"
            print(f"  {status} Test {test_num}")

    print("=" * 80)


if __name__ == "__main__":
    main()
