"""Debug $select + $orderby NoneType iteration error"""

import os
import sys

# Direct imports without FastAPI dependencies
from app.odata.odata_to_logical_plan import ODataLogicalPlanBuilder

sys.path.insert(0, os.path.dirname(__file__))


def test_select_orderby():
    """Test case: $select=id,name&$orderby=id desc&$top=3"""

    print("Testing: $select=id,name&$orderby=id desc&$top=3")
    print("=" * 60)

    builder = ODataLogicalPlanBuilder(table_name="$planets")

    try:
        plan = builder.build(select_cols="id,name", orderby_expr="id desc", top=3)

        print(f"✓ Plan built successfully with {len(list(plan.nodes()))} nodes")

        # Print node structure
        for node_id in plan.nodes():
            node = plan[node_id]
            print(f"  Node {node_id}: {node.node_type}")
            if hasattr(node, "order_by"):
                print(f"    order_by: {node.order_by}")
            if hasattr(node, "columns"):
                print(f"    columns: {node.columns}")

    except Exception:
        print("✗ Error building plan:")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    test_select_orderby()
