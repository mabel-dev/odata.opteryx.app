"""Test $select + $orderby combination - currently failing"""

import os
import sys

import opteryx

from app.odata.odata_to_logical_plan import ODataLogicalPlanBuilder

sys.path.insert(1, os.path.join(sys.path[0], "../opteryx-core"))


def test_select_orderby_combo():
    """Test case: $select=id,name&$orderby=id desc&$top=3"""

    print("Testing: $select=id,name&$orderby=id desc&$top=3")
    print("=" * 60)

    builder = ODataLogicalPlanBuilder(table_name="$planets")
    plan = builder.build(select_cols="id,name", orderby_expr="id desc", top=3)

    print("✓ Plan built successfully")

    # Check node structure
    print(f"\nPlan has {len(list(plan.nodes()))} nodes:")
    for node_id in plan.nodes():
        node = plan[node_id]
        cols = getattr(node, "columns", "NO_ATTR")
        print(
            f"  {node.node_type}: columns={type(cols).__name__} ({len(cols) if hasattr(cols, '__len__') and cols else 'N/A'})"
        )

    print("\nAttempting execution...")
    try:
        arrow_table = opteryx.execute_logical_plan(plan)
        print(f"✓✓ SUCCESS: {arrow_table.num_rows} rows, {arrow_table.column_names}")
        return True
    except Exception as e:
        print(f"✗✗ FAILED: {e}")
        import traceback

        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = test_select_orderby_combo()
    sys.exit(0 if success else 1)
