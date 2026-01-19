#!/usr/bin/env python3
"""Trace through how a working query builds its logical plan"""

import os
import sys

from app.odata.odata_to_logical_plan import ODataLogicalPlanBuilder

sys.path.insert(1, os.path.join(sys.path[0], "../pyiceberg-firestore-gcs"))
sys.path.insert(1, os.path.join(sys.path[0], "../opteryx-core"))
sys.path.insert(0, os.path.join(sys.path[0], "app"))


# Start with a simple query that we know works
builder = ODataLogicalPlanBuilder("$planets")
plan = builder.build(
    filter_expr=None,
    select_cols="id,name",
    orderby_expr=None,
    top=None,
    skip=None,
    apply_expr=None,
)

print("Logical Plan Structure:")
print(f"Type: {type(plan)}")
print()

# Get nodes with data (returns tuples of (node_id, node_object))
nodes_with_data = plan.nodes(True)
print(f"Number of nodes: {len(nodes_with_data)}")
print()

# Iterate through nodes
for node_id, node in nodes_with_data:
    print(f"\n[{node_id}]")
    print(f"  Type: {type(node)}")
    print(f"  node_type: {node.node_type}")

    # Print columns and other relevant attributes
    if hasattr(node, "columns"):
        print(f"  columns: {node.columns}")
    if hasattr(node, "order_by"):
        print(f"  order_by: {node.order_by}")
    if hasattr(node, "limit"):
        print(f"  limit: {node.limit}")
    if hasattr(node, "offset"):
        print(f"  offset: {node.offset}")
