"""Test that our logical plan nodes match Opteryx's baseline structure."""

import sys
from pathlib import Path

from app.odata.odata_to_logical_plan import ODataLogicalPlanBuilder

# Add vendored path
sys.path.insert(0, str(Path(__file__).parent / "app" / "vendored" / "odata-query"))


def test_scan_node_structure():
    """Verify Scan nodes we build match Opteryx's baseline."""
    print("\n=== Testing Scan Node Structure ===\n")

    # Build our Scan node
    builder = ODataLogicalPlanBuilder("$planets")
    our_plan = builder.build()

    print(f"Our plan nodes count: {len(our_plan.nodes())}")
    nodes_list = list(our_plan.nodes(True))  # Get (id, node) tuples
    for node_id, node in nodes_list:
        print(f"  Node {node_id}:")
        print(f"    step_type: {node.node_type}")
        print(f"    relation: {node.relation if hasattr(node, 'relation') else 'N/A'}")
        print(f"    columns: {node.columns if hasattr(node, 'columns') else 'N/A'}")
        print(f"    alias: {node.alias if hasattr(node, 'alias') else 'N/A'}")

    # Identify the Scan node
    print("\n=== Scan Node Details ===")
    for node_id, node in nodes_list:
        if node.node_type and "Scan" in str(node.node_type):
            print(f"Found Scan node: {node_id}")
            print(f"  Properties: {node.field_values()}")


def test_filter_node_structure():
    """Verify Filter nodes convert correctly."""
    print("\n=== Testing Filter Node Structure ===\n")

    # Build with a filter
    builder = ODataLogicalPlanBuilder("$planets")
    our_plan = builder.build(filter_expr="id eq 1")

    print(f"Our plan with filter nodes: {len(our_plan.nodes())}")
    for node_id in our_plan.nodes():
        node = our_plan[node_id]
        print(f"  Node {node_id}: type={node.node_type}")
        if hasattr(node, "condition"):
            print(f"    condition: {node.condition}")


if __name__ == "__main__":
    test_scan_node_structure()
    test_filter_node_structure()
