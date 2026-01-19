#!/usr/bin/env python3
"""Debug specific orderby+select combination test"""

import os
import sys
import traceback
from urllib.parse import parse_qs

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


def parse_odata_query_string(query_string: str) -> dict:
    """Parse OData query string into individual parameters"""
    params = {
        "filter_expr": None,
        "orderby_expr": None,
        "select_cols": None,
        "top": None,
        "skip": None,
        "count_requested": False,
    }

    parsed = parse_qs(query_string, keep_blank_values=True)

    for key, values in parsed.items():
        value = values[0] if values else None

        if key == "$filter":
            params["filter_expr"] = value
        elif key == "$orderby":
            params["orderby_expr"] = value
        elif key == "$select":
            params["select_cols"] = value
        elif key == "$top":
            params["top"] = int(value) if value else None
        elif key == "$skip":
            params["skip"] = int(value) if value else None
        elif key == "$count":
            params["count_requested"] = value.lower() == "true" if value else False

    return params


try:
    print("Testing: $select=id,name&$orderby=id desc&$top=3")
    query = "$select=id,name&$orderby=id desc&$top=3"
    parsed = parse_odata_query_string(query)
    print(f"Parsed params: {parsed}")
    result = execute_odata_query("$planets", **parsed)
    print(f"Success! Got {len(result['rows'])} rows")
    for r in result["rows"]:
        print(f"  {r}")
except Exception as e:
    print(f"Error: {e}")
    print("\nFull traceback:")
    traceback.print_exc()
