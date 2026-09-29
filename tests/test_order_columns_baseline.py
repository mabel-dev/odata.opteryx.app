"""Test what columns attribute should be for Order nodes in Opteryx baseline"""

import os
import sys

import opteryx
from opteryx.expression import NodeType
from opteryx.compiled.structures.expressions import LogicalColumn
from opteryx.planner.logical_planner import LogicalPlan
from opteryx.planner.plan_context import PlanContext
from opteryx.compiled.structures.plan_steps import OrderStep
from opteryx.compiled.structures.plan_steps import ProjectStep
from opteryx.compiled.structures.plan_steps import ScanStep

sys.path.insert(1, os.path.join(sys.path[0], "../opteryx-core"))


def test_order_with_columns_empty():
    """Test Order node with columns=[]"""
    print("Testing Order node with columns=[]")
    print("=" * 60)

    plan_context = PlanContext()
    plan = LogicalPlan(plan_context)

    # Scan node
    scan_node = ScanStep()
    scan_node.relation = "$planets"
    scan_node.columns = []
    scan_id = plan.add_node(scan_node)

    # Order node
    order_node = OrderStep()
    order_node.order_by = [(LogicalColumn(NodeType.IDENTIFIER, "id", arena=plan_context.expressions), False, False)]
    order_node.columns = []  # Empty list
    order_id = plan.add_node(order_node)
    plan.add_edge(scan_id, order_id)

    try:
        result = opteryx.execute_logical_plan(plan)
        print(f"✓ SUCCESS: {result.num_rows} rows, {len(result.column_names)} columns")
        return True
    except Exception as e:
        print(f"✗ FAILED: {e}")
        return False


def test_order_with_columns_none():
    """Test Order node with columns=None"""
    print("\nTesting Order node with columns=None")
    print("=" * 60)

    plan_context = PlanContext()
    plan = LogicalPlan(plan_context)

    # Scan node
    scan_node = ScanStep()
    scan_node.relation = "$planets"
    scan_node.columns = []
    scan_id = plan.add_node(scan_node)

    # Order node
    order_node = OrderStep()
    order_node.order_by = [(LogicalColumn(NodeType.IDENTIFIER, "id", arena=plan_context.expressions), False, False)]
    order_node.columns = None  # None
    order_id = plan.add_node(order_node)
    plan.add_edge(scan_id, order_id)

    try:
        result = opteryx.execute_logical_plan(plan)
        print(f"✓ SUCCESS: {result.num_rows} rows, {len(result.column_names)} columns")
        return True
    except Exception as e:
        print(f"✗ FAILED: {e}")
        return False


def test_order_with_project():
    """Test Order node with Project after it (like $select + $orderby)"""
    print("\nTesting Order + Project combination")
    print("=" * 60)

    plan_context = PlanContext()
    plan = LogicalPlan(plan_context)

    # Scan
    scan_node = ScanStep()
    scan_node.relation = "$planets"
    scan_node.columns = []
    scan_id = plan.add_node(scan_node)

    # Order
    order_node = OrderStep()
    order_node.order_by = [(LogicalColumn(NodeType.IDENTIFIER, "id", arena=plan_context.expressions), False, False)]
    order_node.columns = []  # Test with empty list
    order_id = plan.add_node(order_node)
    plan.add_edge(scan_id, order_id)

    # Project
    project_node = ProjectStep()
    project_node.columns = [
        LogicalColumn(NodeType.IDENTIFIER, "id", arena=plan_context.expressions),
        LogicalColumn(NodeType.IDENTIFIER, "name", arena=plan_context.expressions),
    ]
    project_id = plan.add_node(project_node)
    plan.add_edge(order_id, project_id)

    try:
        result = opteryx.execute_logical_plan(plan)
        print(f"✓ SUCCESS: {result.num_rows} rows, {len(result.column_names)} columns")
        print(f"  Columns: {result.column_names}")
        return True
    except Exception as e:
        print(f"✗ FAILED: {e}")
        import traceback

        traceback.print_exc()
        return False


if __name__ == "__main__":
    results = []
    results.append(("columns=[]", test_order_with_columns_empty()))
    results.append(("columns=None", test_order_with_columns_none()))
    results.append(("Order+Project", test_order_with_project()))

    print("\n" + "=" * 60)
    print("SUMMARY:")
    for name, passed in results:
        status = "✓" if passed else "✗"
        print(f"  {status} {name}")
