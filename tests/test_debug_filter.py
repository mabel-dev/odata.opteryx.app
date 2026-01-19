#!/usr/bin/env python3
"""Debug why string filter returns empty results."""

import os
import sys
from unittest.mock import Mock

import opteryx
from opteryx.connectors import OpteryxConnector
from opteryx_catalog import OpteryxCatalog

from app.routes.v4.interface import read_dataset

sys.path.insert(1, os.path.join(sys.path[0], "../pyiceberg-firestore-gcs"))
sys.path.insert(1, os.path.join(sys.path[0], "../opteryx-core"))
sys.path.insert(0, os.path.join(sys.path[0], "app"))


FIRESTORE_DATABASE = os.environ.get("FIRESTORE_DATABASE")
BUCKET_NAME = os.environ.get("GCS_BUCKET")
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID")

opteryx.set_default_connector(
    OpteryxConnector,
    catalog=OpteryxCatalog,
    firestore_project=GCP_PROJECT_ID,
    firestore_database=FIRESTORE_DATABASE,
    gcs_bucket=BUCKET_NAME,
)


class MockResponse:
    def __init__(self):
        self.status_code = 200
        self.headers = {}


def test_filter(filter_expr):
    """Test a filter and show what we get back."""
    request = Mock()
    request.state = Mock()
    request.state.claims = {"identity": "test-user", "jwt_sub": "test-user"}

    response = MockResponse()

    print(f"\n{'=' * 80}")
    print(f"Filter: {filter_expr}")
    print("=" * 80)

    result = read_dataset(
        request=request,
        response=response,
        workstream="public",
        collection="examples",
        dataset="planets",
        filter=filter_expr,
        top=100,
        skip=None,
        orderby=None,
        count=None,
        select=None,
        search=None,
    )

    if "value" in result:
        print(f"Rows returned: {len(result['value'])}")
        if result["value"]:
            print(f"First row: {result['value'][0]}")
            for row in result["value"]:
                print(f"  - {row.get('name')} (id={row.get('id')})")
        else:
            print("NO ROWS RETURNED")
    return result


if __name__ == "__main__":
    # Test 1: No filter - should get all planets
    test_filter(None)

    # Test 2: Filter for Earth - should get 1 row
    test_filter("name eq 'Earth'")

    # Test 3: Filter for Mercury - should get 1 row
    test_filter("name eq 'Mercury'")

    # Test 4: Filter with lowercase - does it matter?
    test_filter("name eq 'earth'")
