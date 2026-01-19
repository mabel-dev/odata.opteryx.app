#!/usr/bin/env python3
"""Use Opteryx's SQL parser to see what the correct logical plans look like"""

import os
import sys

from opteryx import set_default_connector
from opteryx.connectors import OpteryxConnector
from opteryx.planner.logical_planner import logical_planner_builders
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


def print_opteryx_plan(sql, title):
    """Parse SQL with Opteryx's parser and print the logical plan"""
    print(f"\n{'=' * 80}")
    print(f"{title}")
    print(f"SQL: {sql}")
    print(f"{'=' * 80}")

    try:
        # Use Opteryx's SQL parser
        from opteryx.planner.logical_planner.logical_planner import parse_statement

        statement = parse_statement(sql)
        builder = logical_planner_builders.get_builder(statement)
        plan = builder.build(statement)

        # Print the plan
        nodes_with_data = plan.nodes(True)
        for node_id, node in nodes_with_data:
            print(f"\n[{node_id[:8]}...]")
            print(f"  node_type: {node.node_type}")

            # Print relevant attributes
            for attr in ["columns", "order_by", "limit", "offset", "relation"]:
                if hasattr(node, attr):
                    val = getattr(node, attr)
                    if val is not None:
                        print(f"  {attr}: {val}")
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback

        traceback.print_exc()


# Test SQL patterns
print_opteryx_plan("SELECT id, name FROM $planets", "SELECT columns")

print_opteryx_plan("SELECT id, name FROM $planets ORDER BY id DESC", "SELECT with ORDER BY")

print_opteryx_plan("SELECT id, name FROM $planets LIMIT 3", "SELECT with LIMIT")

print_opteryx_plan(
    "SELECT id, name FROM $planets ORDER BY id DESC LIMIT 3", "SELECT with ORDER BY and LIMIT"
)
