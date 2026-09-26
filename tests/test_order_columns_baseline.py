"""Test what columns attribute should be for Order nodes in Opteryx baseline"""

import os
import sys

import opteryx
from opteryx.expression import NodeType
from opteryx.compiled.structures.expressions import LogicalColumn
from opteryx.planner.logical_planner import LogicalPlan
from opteryx.compiled.structures.plan_steps import OrderStep
from opteryx.compiled.structures.plan_steps import ProjectStep
from opteryx.compiled.structures.plan_steps import ScanStep
from opteryx.utils import random_string

sys.path.insert(1, os.path.join(sys.path[0], "../opteryx-core"))


def test_order_with_columns_empty():
    """Test Order node with columns=[]"""
    print("Testing Order node with columns=[]")
    print("=" * 60)

    plan = LogicalPlan()

    # Scan node
    scan_id = random_string()
    scan_node = ScanStep()
    scan_node.relation = "$planets"
    scan_node.columns = []
    plan.add_node(scan_id, scan_node)

    # Order node
    order_id = random_string()
    order_node = OrderStep()
    order_node.order_by = [(LogicalColumn(NodeType.IDENTIFIER, "id"), False)]
    order_node.columns = []  # Empty list
    plan.add_node(order_id, order_node)
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

    plan = LogicalPlan()

    # Scan node
    scan_id = random_string()
    scan_node = ScanStep()
    scan_node.relation = "$planets"
    scan_node.columns = []
    plan.add_node(scan_id, scan_node)

    # Order node
    order_id = random_string()
    order_node = OrderStep()
    order_node.order_by = [(LogicalColumn(NodeType.IDENTIFIER, "id"), False)]
    order_node.columns = None  # None
    plan.add_node(order_id, order_node)
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

    plan = LogicalPlan()

    # Scan
    scan_id = random_string()
    scan_node = ScanStep()
    scan_node.relation = "$planets"
    scan_node.columns = []
    plan.add_node(scan_id, scan_node)

    # Order
    order_id = random_string()
    order_node = OrderStep()
    order_node.order_by = [(LogicalColumn(NodeType.IDENTIFIER, "id"), False)]
    order_node.columns = []  # Test with empty list
    plan.add_node(order_id, order_node)
    plan.add_edge(scan_id, order_id)

    # Project
    project_id = random_string()
    project_node = ProjectStep()
    project_node.columns = [
        LogicalColumn(NodeType.IDENTIFIER, "id"),
        LogicalColumn(NodeType.IDENTIFIER, "name"),
    ]
    plan.add_node(project_id, project_node)
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
