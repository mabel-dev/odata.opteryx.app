#!/usr/bin/env python3
"""Debug specific failing orderby+select test"""

import os
import sys
import traceback

from opteryx import set_default_connector
from opteryx.connectors import OpteryxConnector
from opteryx_catalog import OpteryxCatalog

from app.routes.v4.interface import execute_odata_query

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

try:
    print("Testing: $select=id,name&$orderby=id desc&$top=3")
    result = execute_odata_query("$planets", "$select=id,name&$orderby=id desc&$top=3")
    print(f"Success! Got {len(result)} rows")
    for r in result:
        print(f"  {r}")
except Exception as e:
    print(f"Error: {e}")
    traceback.print_exc()
