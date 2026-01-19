"""Validate Project node columns match Opteryx baseline"""

import os
import sys

import opteryx
from opteryx.managers.expression import NodeType
from opteryx.models.logical_column import LogicalColumn

from app.odata.odata_to_logical_plan import ODataLogicalPlanBuilder

sys.path.insert(1, os.path.join(sys.path[0], "../opteryx-core"))


def test_project_column_structure():
    """Test that Project node columns are LogicalColumn objects with proper attributes"""
    print("Testing Project node column structure against Opteryx baseline")
    print("=" * 80)

    builder = ODataLogicalPlanBuilder(table_name="$planets")
    plan = builder.build(select_cols="id,name")

    # Find Project node
    project_node = None
    for node_id in plan.nodes():
        node = plan[node_id]
        if str(node.node_type) == "LogicalPlanStepType.Project":
            project_node = node
            break

    if not project_node:
        print("✗ No Project node found")
        return False

    print("✓ Project node found")
    print(f"  columns: {len(project_node.columns)} items")

    # Validate each column
    for i, col in enumerate(project_node.columns):
        print(f"\n  Column {i}:")
        print(f"    type: {type(col).__name__}")

        # Should be LogicalColumn, not Node
        if not isinstance(col, LogicalColumn):
            print(f"    ✗ Should be LogicalColumn, got {type(col).__name__}")
            return False
        print("    ✓ Is LogicalColumn")

        # Check node_type
        if col.node_type != NodeType.IDENTIFIER:
            print(f"    ✗ node_type should be IDENTIFIER, got {col.node_type}")
            return False
        print("    ✓ node_type = IDENTIFIER")

        # Check source_column
        if not hasattr(col, "source_column") or not col.source_column:
            print("    ✗ Missing source_column")
            return False
        print(f"    ✓ source_column = {col.source_column}")

        # Check query_column (critical for binder)
        if not hasattr(col, "query_column") or not col.query_column:
            print("    ✗ Missing query_column")
            return False
        print(f"    ✓ query_column = {col.query_column}")

    print("\n✓✓ All Project columns are valid LogicalColumn objects")

    # Now test execution
    print("\nTesting execution...")
    try:
        arrow_table = opteryx.execute_logical_plan(plan)
        print(
            f"✓✓✓ SUCCESS: Execution works! {arrow_table.num_rows} rows, columns: {arrow_table.column_names}"
        )
        return True
    except Exception as e:
        print(f"✗✗✗ FAILED execution: {e}")
        return False


if __name__ == "__main__":
    success = test_project_column_structure()
    sys.exit(0 if success else 1)
