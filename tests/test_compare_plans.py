#!/usr/bin/env python3
"""Compare different logical plans"""

import os
import sys

from app.odata.odata_to_logical_plan import ODataLogicalPlanBuilder

sys.path.insert(1, os.path.join(sys.path[0], "../pyiceberg-firestore-gcs"))
sys.path.insert(1, os.path.join(sys.path[0], "../opteryx-core"))
sys.path.insert(0, os.path.join(sys.path[0], "app"))


def print_plan(description, **kwargs):
    """Build and print a logical plan"""
    print(f"\n{'=' * 80}")
    print(f"{description}")
    print(f"{'=' * 80}")

    builder = ODataLogicalPlanBuilder("$planets")
    plan = builder.build(filter_expr=None, apply_expr=None, **kwargs)

    # Get nodes with data
    nodes_with_data = plan.nodes(True)

    for node_id, node in nodes_with_data:
        print(f"\n[{node_id[:8]}...]")
        print(f"  node_type: {node.node_type}")
        if hasattr(node, "columns") and node.columns is not None:
            print(f"  columns: {node.columns}")
        if hasattr(node, "order_by") and node.order_by is not None:
            print(f"  order_by: {node.order_by}")
        if hasattr(node, "limit") and node.limit is not None:
            print(f"  limit: {node.limit}")


# Compare different query combinations
print_plan(
    "Query 1: SELECT id, name",
    select_cols="id,name",
    orderby_expr=None,
    top=None,
    skip=None,
)

print_plan(
    "Query 2: SELECT id, name ORDER BY id DESC",
    select_cols="id,name",
    orderby_expr="id desc",
    top=None,
    skip=None,
)

print_plan(
    "Query 3: SELECT id, name ORDER BY id DESC LIMIT 3",
    select_cols="id,name",
    orderby_expr="id desc",
    top=3,
    skip=None,
)
