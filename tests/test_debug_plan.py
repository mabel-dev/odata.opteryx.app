#!/usr/bin/env python3
"""Debug logical plan structure"""

import os
import sys

from opteryx import set_default_connector
from opteryx.connectors import OpteryxConnector
from opteryx_catalog import OpteryxCatalog

from app.odata.odata_to_logical_plan import ODataLogicalPlanBuilder

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

builder = ODataLogicalPlanBuilder("$planets")
plan = builder.build(
    filter_expr=None,
    select_cols="id,name",
    orderby_expr="id desc",
    top=3,
    skip=None,
    apply_expr=None,
)

print("Logical Plan Nodes:")
print(f"Plan type: {type(plan)}")
print(f"Plan dir: {dir(plan)}")

# Try to access nodes through the private attribute
if hasattr(plan, "_nodes"):
    for step_id, node in plan._nodes.items():
        print(f"\n  {step_id}: {node.node_type}")
        print(f"    columns: {getattr(node, 'columns', 'NO ATTR')}")
        if hasattr(node, "order_by"):
            print(f"    order_by: {node.order_by}")
