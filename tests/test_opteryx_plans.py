#!/usr/bin/env python3
"""Analyze Opteryx logical plans from SQL to understand proper node structure"""

import os
import sys

from opteryx import set_default_connector
from opteryx.connectors import OpteryxConnector
from opteryx.planner.logical_planner import do_logical_planning_phase
from opteryx_catalog import OpteryxCatalog

sys.path.insert(1, os.path.join(sys.path[0], "../pyiceberg-firestore-gcs"))
sys.path.insert(1, os.path.join(sys.path[0], "../opteryx-core"))
sys.path.insert(0, os.path.join(sys.path[0], "app"))


FIRESTORE_DATABASE = os.environ.get("FIRESTORE_DATABASE")
BUCKET_NAME = os.environ.get("GCS_BUCKET")
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID")

set_default_connector(
    OpteryxConnector,
    catalog=OpteryxCatalog,
    firestore_project=GCP_PROJECT_ID,
    firestore_database=FIRESTORE_DATABASE,
    gcs_bucket=BUCKET_NAME,
)


def print_plan(sql, title):
    """Parse SQL and print the logical plan structure"""
    print(f"\n{'=' * 80}")
    print(f"{title}")
    print(f"SQL: {sql}")
    print(f"{'=' * 80}")

    try:
        plan = do_logical_planning_phase(sql)
        print_plan_nodes(plan)
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback

        traceback.print_exc()


def print_plan_nodes(plan, indent=0):
    """Recursively print plan nodes"""
    for step_id, node in plan.nodes.items():
        prefix = "  " * indent
        node_type = node.node_type if hasattr(node, "node_type") else "UNKNOWN"
        print(f"{prefix}[{step_id}] {node_type}")

        # Print relevant properties
        if hasattr(node, "columns") and node.columns is not None:
            print(f"{prefix}  columns: {node.columns}")
        else:
            print(f"{prefix}  columns: None")

        if hasattr(node, "order_by") and node.order_by is not None:
            print(f"{prefix}  order_by: {node.order_by}")

        if hasattr(node, "limit") and node.limit is not None:
            print(f"{prefix}  limit: {node.limit}")

        if hasattr(node, "offset") and node.offset is not None:
            print(f"{prefix}  offset: {node.offset}")

        if hasattr(node, "relation") and node.relation is not None:
            print(f"{prefix}  relation: {node.relation}")


# Test various SQL patterns to see what Opteryx generates
print_plan("SELECT * FROM $planets", "Simple SELECT all")

print_plan("SELECT id, name FROM $planets", "SELECT specific columns")

print_plan("SELECT * FROM $planets ORDER BY id DESC", "SELECT with ORDER BY")

print_plan("SELECT id, name FROM $planets ORDER BY id DESC", "SELECT columns with ORDER BY")

print_plan("SELECT * FROM $planets LIMIT 3", "SELECT with LIMIT")

print_plan("SELECT id, name FROM $planets LIMIT 3", "SELECT columns with LIMIT")

print_plan(
    "SELECT id, name FROM $planets ORDER BY id DESC LIMIT 3",
    "SELECT columns with ORDER BY and LIMIT",
)

print_plan("SELECT * FROM $planets WHERE mass > 50 ORDER BY id DESC LIMIT 3", "Complex query")
