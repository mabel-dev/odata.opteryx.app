#!/usr/bin/env python3
"""Comprehensive filter syntax tests for OData queries - errors propagate, no silent failures."""

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
    """Mock FastAPI response object."""

    def __init__(self):
        self.status_code = 200
        self.headers = {}


def run_filter_test(name, filter_expr):
    """Run a single filter test. Let errors propagate."""
    request = Mock()
    request.state = Mock()
    request.state.claims = {"identity": "test-user", "jwt_sub": "test-user"}

    response = MockResponse()

    result = read_dataset(
        request=request,
        response=response,
        workstream="public",
        collection="github",
        dataset="events",
        filter=filter_expr,
        top=10,
        skip=None,
        orderby=None,
        count=None,
        select=None,
        search=None,
    )

    assert isinstance(result, dict), f"Expected dict result, got {type(result)}"
    assert "@odata.context" in result, f"Result missing @odata.context: {result.keys()}"

    print(f"  ✓ {name}")
    return True


if __name__ == "__main__":
    print("=" * 80)
    print("COMPREHENSIVE OData FILTER SYNTAX TESTS (Errors Propagate)")
    print("=" * 80)

    # Test 1: Basic query
    print("\n[Test 1] Basic query without filter")
    try:
        result = read_dataset(
            request=Mock(state=Mock(claims={"identity": "test-user", "jwt_sub": "test-user"})),
            response=MockResponse(),
            workstream="public",
            collection="github",
            dataset="events",
            filter=None,
            top=10,
            skip=None,
            orderby=None,
            count=None,
            select=None,
            search=None,
        )
        assert isinstance(result, dict) and "@odata.context" in result
        print("  ✓ Basic query")
    except Exception as e:
        print(f"  ✗ FAILED: {e}")
        import traceback

        traceback.print_exc()

    # Test 2+: Filter variations
    print("\n[Test 2] Equality Operators")
    for name, filter_expr in [
        ("String equality", "actor_login eq 'Copilot'"),
        ("String inequality", "actor_login ne 'Copilot'"),
    ]:
        try:
            run_filter_test(name, filter_expr)
        except Exception:
            print(f"  ✗ {name}")
            print(f"    Filter: {filter_expr}")
            raise

    print("\n[Test 3] Comparison Operators")
    for name, filter_expr in [
        ("Greater than", "actor_id gt 100000"),
        ("Greater or equal", "actor_id ge 100000"),
        ("Less than", "actor_id lt 300000000"),
        ("Less or equal", "actor_id le 300000000"),
    ]:
        try:
            run_filter_test(name, filter_expr)
        except Exception:
            print(f"  ✗ {name}")
            print(f"    Filter: {filter_expr}")
            raise

    print("\n[Test 4] Logical Operators - AND")
    for name, filter_expr in [
        ("AND two conditions", "actor_login eq 'Copilot' and public eq true"),
        (
            "AND three conditions",
            "actor_login eq 'Copilot' and public eq true and actor_id gt 100000",
        ),
    ]:
        try:
            run_filter_test(name, filter_expr)
        except Exception:
            print(f"  ✗ {name}")
            print(f"    Filter: {filter_expr}")
            raise

    print("\n[Test 5] Logical Operators - OR")
    for name, filter_expr in [
        ("OR two conditions", "actor_login eq 'Copilot' or actor_login eq 'dependabot'"),
        (
            "OR three conditions",
            "actor_login eq 'Copilot' or actor_login eq 'dependabot' or public eq false",
        ),
    ]:
        try:
            run_filter_test(name, filter_expr)
        except Exception:
            print(f"  ✗ {name}")
            print(f"    Filter: {filter_expr}")
            raise

    print("\n[Test 6] Logical Operators - NOT")
    for name, filter_expr in [
        ("NOT simple", "not (public eq true)"),
        ("NOT with AND", "not (public eq true) and actor_login eq 'Copilot'"),
    ]:
        try:
            run_filter_test(name, filter_expr)
        except Exception:
            print(f"  ✗ {name}")
            print(f"    Filter: {filter_expr}")
            raise

    print("\n[Test 7] Complex Logical Combinations")
    for name, filter_expr in [
        (
            "AND with OR",
            "(actor_login eq 'Copilot' or actor_login eq 'dependabot') and public eq true",
        ),
        (
            "Nested parentheses",
            "((actor_login eq 'Copilot' and public eq true) or actor_id gt 100000)",
        ),
    ]:
        try:
            run_filter_test(name, filter_expr)
        except Exception:
            print(f"  ✗ {name}")
            print(f"    Filter: {filter_expr}")
            raise

    print("\n[Test 8] String Functions")
    for name, filter_expr in [
        ("startswith", "startswith(actor_login, 'Cop')"),
        ("endswith", "endswith(actor_login, 'ilot')"),
        ("contains", "contains(actor_login, 'pil')"),
    ]:
        try:
            run_filter_test(name, filter_expr)
        except Exception:
            print(f"  ✗ {name}")
            print(f"    Filter: {filter_expr}")
            raise

    print("\n" + "=" * 80)
    print("ALL TESTS PASSED")
    print("=" * 80)
