#!/usr/bin/env python3
"""
Phase 2: Test Logical Plan Node Creation

Tests that ODataLogicalPlanBuilder creates correct logical plan nodes
with proper structure, references, and attributes.
"""

import sys

from opteryx.expression import NodeType
from opteryx.planner.logical_planner import LogicalPlanStepType

from app.odata.odata_to_logical_plan import ODataLogicalPlanBuilder

sys.path.insert(0, "/Users/justin/Nextcloud/opteryx-cloud/odata.opteryx")


def test_scan_node():
    """Phase 2.1: Test Scan node creation"""
    print("\n" + "=" * 80)
    print("Phase 2.1: Testing Scan Node")
    print("=" * 80)

    builder = ODataLogicalPlanBuilder("workstream.collection.dataset")
    plan = builder.build()

    # Find the Scan node (should be first)
    scan_node = None
    for node_id in plan.nodes():
        node = plan[node_id]
        if node.node_type == LogicalPlanStepType.Scan:
            scan_node = node
            break

    if scan_node is None:
        print("✗ No Scan node found in plan")
        return False

    print(f"✓ Scan node found with ID: {node_id}")

    # Check relation (table name)
    if not hasattr(scan_node, "relation") or scan_node.relation != "workstream.collection.dataset":
        print(f"✗ Scan node relation incorrect: {getattr(scan_node, 'relation', 'MISSING')}")
        return False
    print(f"✓ Scan node has correct table name: {scan_node.relation}")

    # Check columns (should be empty list or wildcard for scan - not None to avoid optimizer issues)
    if hasattr(scan_node, "columns"):
        # Allow empty list (for optimizer compatibility) or wildcard
        if scan_node.columns is None or len(scan_node.columns) == 0:
            print(
                f"✓ Scan node columns = {scan_node.columns} (not projecting, optimizer-compatible)"
            )
        elif len(scan_node.columns) == 1 and scan_node.columns[0].node_type == NodeType.WILDCARD:
            print("✓ Scan node has wildcard column")
        else:
            print(f"✗ Scan node should not project specific columns: {scan_node.columns}")
            return False
    else:
        print("✗ Scan node missing columns attribute")
        return False

    print("✓✓ Phase 2.1 PASS: Scan node structure correct")
    return True


def test_filter_node():
    """Phase 2.2: Test Filter node creation"""
    print("\n" + "=" * 80)
    print("Phase 2.2: Testing Filter Node")
    print("=" * 80)

    builder = ODataLogicalPlanBuilder("workstream.collection.dataset")
    plan = builder.build(filter_expr="id eq 42")

    # Find the Filter node
    filter_node = None
    for node_id in plan.nodes():
        node = plan[node_id]
        if node.node_type == LogicalPlanStepType.Filter:
            filter_node = node
            break

    if filter_node is None:
        print("✗ No Filter node found in plan")
        return False

    print(f"✓ Filter node found with ID: {node_id}")

    # Check condition exists
    if not hasattr(filter_node, "condition") or filter_node.condition is None:
        print("✗ Filter node missing condition")
        return False
    print(f"✓ Filter node has condition: {filter_node.condition.node_type}")

    # Validate condition is a comparison
    if filter_node.condition.node_type != NodeType.COMPARISON_OPERATOR:
        print(
            f"✗ Filter condition should be COMPARISON_OPERATOR, got {filter_node.condition.node_type}"
        )
        return False
    print("✓ Filter condition is COMPARISON_OPERATOR")

    # Check columns (should pass through all columns)
    if hasattr(filter_node, "columns"):
        if filter_node.columns and len(filter_node.columns) == 1:
            if filter_node.columns[0].node_type == NodeType.WILDCARD:
                print("✓ Filter node has wildcard (passes through all columns)")
            else:
                print(f"✗ Filter columns unexpected: {filter_node.columns}")
                return False
        elif filter_node.columns is None:
            print("✓ Filter node columns = None")
        else:
            print(f"✗ Filter columns unexpected: {filter_node.columns}")
            return False

    print("✓✓ Phase 2.2 PASS: Filter node structure correct")
    return True


def test_order_node():
    """Phase 2.3: Test Order node creation"""
    print("\n" + "=" * 80)
    print("Phase 2.3: Testing Order Node")
    print("=" * 80)

    builder = ODataLogicalPlanBuilder("workstream.collection.dataset")
    plan = builder.build(orderby_expr="name asc, age desc")

    # Find the Order node
    order_node = None
    for node_id in plan.nodes():
        node = plan[node_id]
        if node.node_type == LogicalPlanStepType.Order:
            order_node = node
            break

    if order_node is None:
        print("✗ No Order node found in plan")
        return False

    print(f"✓ Order node found with ID: {node_id}")

    # Check order_by list
    if not hasattr(order_node, "order_by") or not order_node.order_by:
        print("✗ Order node missing order_by list")
        return False

    if len(order_node.order_by) != 2:
        print(f"✗ Expected 2 order clauses, got {len(order_node.order_by)}")
        return False
    print(f"✓ Order node has {len(order_node.order_by)} order clauses")

    # Check first order clause (name asc)
    first_order = order_node.order_by[0]
    # CRITICAL: Validate this is a LogicalColumn
    from opteryx.compiled.structures.expressions import LogicalColumn

    if not isinstance(first_order[0], LogicalColumn):
        print(f"✗ First order column should be LogicalColumn, got {type(first_order[0]).__name__}")
        return False
    if first_order[0].source_column != "name":
        print(f"✗ First order column should be 'name', got {first_order[0].source_column}")
        return False
    if first_order[1] != True:  # True = ascending
        print(f"✗ First order direction should be True (ascending), got {first_order[1]}")
        return False
    print("✓ First order clause: name ASC (True) - LogicalColumn")

    # Check second order clause (age desc)
    second_order = order_node.order_by[1]
    if second_order[0].source_column != "age":
        print(f"✗ Second order column should be 'age', got {second_order[0].source_column}")
        return False
    if second_order[1] != False:  # False = descending
        print(f"✗ Second order direction should be False (descending), got {second_order[1]}")
        return False
    print("✓ Second order clause: age DESC (False)")

    print("✓✓ Phase 2.3 PASS: Order node structure correct")
    return True


def test_limit_node():
    """Phase 2.4: Test Limit node creation"""
    print("\n" + "=" * 80)
    print("Phase 2.4: Testing Limit Node")
    print("=" * 80)

    builder = ODataLogicalPlanBuilder("workstream.collection.dataset")
    plan = builder.build(top=100, skip=50)

    # Find the Limit node
    limit_node = None
    for node_id in plan.nodes():
        node = plan[node_id]
        if node.node_type == LogicalPlanStepType.Limit:
            limit_node = node
            break

    if limit_node is None:
        print("✗ No Limit node found in plan")
        return False

    print(f"✓ Limit node found with ID: {node_id}")

    # Check limit value
    if not hasattr(limit_node, "limit") or limit_node.limit != 100:
        print(f"✗ Limit value should be 100, got {getattr(limit_node, 'limit', 'MISSING')}")
        return False
    print(f"✓ Limit value correct: {limit_node.limit}")

    # Check offset value
    if not hasattr(limit_node, "offset") or limit_node.offset != 50:
        print(f"✗ Offset value should be 50, got {getattr(limit_node, 'offset', 'MISSING')}")
        return False
    print(f"✓ Offset value correct: {limit_node.offset}")

    print("✓✓ Phase 2.4 PASS: Limit node structure correct")
    return True


def test_project_node():
    """Phase 2.5: Test Project node creation"""
    print("\n" + "=" * 80)
    print("Phase 2.5: Testing Project Node")
    print("=" * 80)

    builder = ODataLogicalPlanBuilder("workstream.collection.dataset")
    plan = builder.build(select_cols="id, name, age")

    # Find the Project node
    project_node = None
    for node_id in plan.nodes():
        node = plan[node_id]
        if node.node_type == LogicalPlanStepType.Project:
            project_node = node
            break

    if project_node is None:
        print("✗ No Project node found in plan")
        return False

    print(f"✓ Project node found with ID: {node_id}")

    # Check columns list
    if not hasattr(project_node, "columns") or not project_node.columns:
        print("✗ Project node missing columns list")
        return False

    if len(project_node.columns) != 3:
        print(f"✗ Expected 3 columns, got {len(project_node.columns)}")
        return False
    print(f"✓ Project node has {len(project_node.columns)} columns")

    # Check each column is an identifier
    expected_cols = ["id", "name", "age"]
    for i, col_node in enumerate(project_node.columns):
        if col_node.node_type != NodeType.IDENTIFIER:
            print(f"✗ Column {i} should be IDENTIFIER, got {col_node.node_type}")
            return False
        actual_name = getattr(col_node, "source_column", None) or getattr(
            col_node, "current_name", None
        )
        if actual_name != expected_cols[i]:
            print(f"✗ Column {i} should be '{expected_cols[i]}', got '{actual_name}'")
            return False
    print(f"✓ All columns are IDENTIFIER nodes: {', '.join(expected_cols)}")

    # Check passthrough_columns attribute (should be empty list, not None).
    # ProjectStep defaults every declared field to None, so an explicit
    # `is None` check is what catches the builder never setting it.
    assert (
        project_node.passthrough_columns is not None
    ), "Project node passthrough_columns is None (should be [])"
    assert (
        project_node.passthrough_columns == []
    ), f"Project node passthrough_columns should be [], got {project_node.passthrough_columns}"
    print("✓ Project node has passthrough_columns = []")

    print("✓✓ Phase 2.5 PASS: Project node structure correct")
    return True


def test_exit_node():
    """Phase 2.6: Test Exit node creation"""
    print("\n" + "=" * 80)
    print("Phase 2.6: Testing Exit Node")
    print("=" * 80)

    builder = ODataLogicalPlanBuilder("workstream.collection.dataset")
    plan = builder.build()

    # Find the Exit node (should be last)
    exit_node = None
    for node_id in plan.nodes():
        node = plan[node_id]
        if node.node_type == LogicalPlanStepType.Exit:
            exit_node = node
            break

    if exit_node is None:
        print("✗ No Exit node found in plan")
        return False

    print(f"✓ Exit node found with ID: {node_id}")

    # Check it has columns (should be wildcard)
    if not hasattr(exit_node, "columns") or not exit_node.columns:
        print("✗ Exit node missing columns")
        return False

    if len(exit_node.columns) != 1:
        print(f"✗ Exit node should have 1 column (wildcard), got {len(exit_node.columns)}")
        return False

    if exit_node.columns[0].node_type != NodeType.WILDCARD:
        print(f"✗ Exit node column should be WILDCARD, got {exit_node.columns[0].node_type}")
        return False
    print("✓ Exit node has WILDCARD column")

    # Check it's terminal (no dependencies on it)
    has_dependents = False
    for other_node in plan.nodes():
        if hasattr(other_node, "depends_on") and exit_node.node_id in other_node.depends_on:
            has_dependents = True
            break

    if has_dependents:
        print("✗ Exit node should be terminal (no dependents)")
        return False
    print("✓ Exit node is terminal in graph")

    print("✓✓ Phase 2.6 PASS: Exit node structure correct")
    return True


def main():
    print("=" * 80)
    print("PHASE 2: LOGICAL PLAN NODE CREATION TESTS")
    print("=" * 80)

    tests = [
        ("2.1 Scan Node", test_scan_node),
        ("2.2 Filter Node", test_filter_node),
        ("2.3 Order Node", test_order_node),
        ("2.4 Limit Node", test_limit_node),
        ("2.5 Project Node", test_project_node),
        ("2.6 Exit Node", test_exit_node),
    ]

    results = []
    for name, test_func in tests:
        try:
            result = test_func()
            results.append((name, result))
        except Exception as e:
            print(f"\n✗ Test {name} crashed: {e}")
            import traceback

            traceback.print_exc()
            results.append((name, False))

    print("\n" + "=" * 80)
    print("PHASE 2 SUMMARY")
    print("=" * 80)

    for name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"{status}: {name}")

    passed = sum(1 for _, r in results if r)
    total = len(results)

    print(f"\nResults: {passed}/{total} passed ({int(passed / total * 100)}%)")

    if passed == total:
        print("✓✓ PHASE 2 COMPLETE: All logical plan nodes validated!")
    else:
        print(f"✗✗ PHASE 2 INCOMPLETE: {total - passed} tests failing")


if __name__ == "__main__":
    main()
