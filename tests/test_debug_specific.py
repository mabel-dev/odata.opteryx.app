#!/usr/bin/env python3
"""Debug specific failing filter tests"""

import os
import sys

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


def test_filter(description, query_string):
    """Test a specific OData filter"""
    print(f"\n{'=' * 80}")
    print(f"Filter: {description}")
    print(f"Query: {query_string}")
    print(f"{'=' * 80}")

    try:
        result = execute_odata_query("$planets", query_string)
        print(f"Rows returned: {len(result)}")
        for r in result:
            mass = r.get("mass", "None")
            diameter = r.get("diameter", "None")
            temp = r.get("mean_temperature", "None")
            print(f"  {r['name']:10} mass={mass!s:>6}  diameter={diameter!s:>6}  temp={temp!s:>4}")
    except Exception as e:
        print(f"ERROR: {e}")


# Test all planets to see what we have
print("=" * 80)
print("ALL PLANETS")
print("=" * 80)
result = execute_odata_query("$planets", "$top=10")
for r in result:
    mass = r.get("mass", "None")
    diameter = r.get("diameter", "None")
    temp = r.get("mean_temperature", "None")
    print(f"{r['name']:10} mass={mass!s:>6}  diameter={diameter!s:>6}  temp={temp!s:>4}")

# Test 1: (mass gt 50 or mass lt 1) and diameter gt 5000
test_filter(
    "(mass gt 50 or mass lt 1) and diameter gt 5000",
    "$filter=(mass gt 50 or mass lt 1) and diameter gt 5000&$top=10",
)

# Test 2: mean_temperature lt 200 or mean_temperature gt 400
test_filter(
    "mean_temperature lt 200 or mean_temperature gt 400",
    "$filter=mean_temperature lt 200 or mean_temperature gt 400&$top=10",
)
