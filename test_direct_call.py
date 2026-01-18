#!/usr/bin/env python3
"""Direct test of read_dataset function."""

import os
import sys
from unittest.mock import Mock

import opteryx
from opteryx.connectors import OpteryxConnector
from opteryx_catalog import OpteryxCatalog

from app.routes.v4.interface import read_dataset

os.environ["OPTERYX_DEBUG"] = "1"


sys.path.insert(1, os.path.join(sys.path[0], "../pyiceberg-firestore-gcs"))
sys.path.insert(1, os.path.join(sys.path[0], "../opteryx-core"))
sys.path.insert(0, os.path.join(sys.path[0], "app"))


FIRESTORE_DATABASE = os.environ.get("FIRESTORE_DATABASE")
BUCKET_NAME = os.environ.get("GCS_BUCKET")
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID")

workspace = OpteryxCatalog(
    "public",
    firestore_project=GCP_PROJECT_ID,
    firestore_database=FIRESTORE_DATABASE,
    gcs_bucket=BUCKET_NAME,
)

opteryx.set_default_connector(
    OpteryxConnector,
    catalog=OpteryxCatalog,
    firestore_project=GCP_PROJECT_ID,
    firestore_database=FIRESTORE_DATABASE,
    gcs_bucket=BUCKET_NAME,
)

print(opteryx.query("SELECT * FROM public.github.events WHERE actor_login = 'Copilot' LIMIT 10"))


def test_read_dataset_direct():
    """Test calling read_dataset directly with mock objects."""

    # Create mock request and response
    request = Mock()
    request.state = Mock()
    request.state.claims = {"identity": "test-user", "jwt_sub": "test-user"}

    response = Mock()
    response.headers = {}

    # Call with a simple query
    try:
        result = read_dataset(
            request=request,
            response=response,
            workstream="public",
            collection="examples",
            dataset="planets",
            filter=None,
            top=10,
            skip=None,
            orderby=None,
            count=None,
            select=None,
            search=None,
        )
        print(f"Success! Result type: {type(result)}")
        print(f"Result keys: {result.keys() if isinstance(result, dict) else 'N/A'}")
        print(result)
        return True
    except Exception as e:
        print(f"Error: {e}")
        import traceback

        traceback.print_exc()
        return False


def test_read_dataset_with_filter():
    """Test calling read_dataset with a filter."""

    # Create mock request and response
    request = Mock()
    request.state = Mock()
    request.state.claims = {"identity": "test-user", "jwt_sub": "test-user"}

    response = Mock()
    response.headers = {}

    # Call with a filter
    try:
        result = read_dataset(
            request=request,
            response=response,
            workstream="public",
            collection="github",
            dataset="events",
            filter="actor_login eq 'Copilot'",
            top=10,
            skip=None,
            orderby=None,
            count=None,
            select=None,
            search=None,
        )
        print(f"Success! Result type: {type(result)}")
        print(f"Result keys: {result.keys() if isinstance(result, dict) else 'N/A'}")
        return True
    except Exception as e:
        print(f"Error: {e}")
        import traceback

        traceback.print_exc()
        return False


if __name__ == "__main__":
    print("=" * 80)
    print("Test 1: Basic query without filter")
    print("=" * 80)
    success1 = test_read_dataset_direct()

    print("\n" + "=" * 80)
    print("Test 2: Query with filter")
    print("=" * 80)
    success2 = test_read_dataset_with_filter()

    print("\n" + "=" * 80)
    print(f"Results: Test1={success1}, Test2={success2}")
    print("=" * 80)
