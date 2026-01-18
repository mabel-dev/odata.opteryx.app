#!/usr/bin/env python3
"""Comprehensive filter syntax tests for OData queries."""

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


def create_mock_request():
    """Create a mock FastAPI request object."""
    request = Mock()
    request.state = Mock()
    request.state.claims = {"identity": "test-user", "jwt_sub": "test-user"}
    return request


class MockResponse:
    """Mock FastAPI response object."""

    def __init__(self):
        self.status_code = 200
        self.headers = {}


def test_basic_no_filter():
    """Test 1: Basic query without filter"""
    request = create_mock_request()
    response = MockResponse()

    try:
        result = read_dataset(
            request=request,
            response=response,
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
        if isinstance(result, dict) and "@odata.context" in result:
            return True
    except Exception as e:
        print(f"  Exception: {str(e)[:80]}")
    return False


def run_filter_test(name, filter_expr):
    """Run a single filter test and return success/failure"""
    try:
        request = create_mock_request()
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

        if isinstance(result, dict) and "@odata.context" in result:
            print(f"  ✓ {name}")
            return True
        else:
            print(f"  ✗ {name} - Invalid result structure")
            return False
    except Exception as e:
        error_msg = str(e)[:80]
        print(f"  ✗ {name}")
        print(f"    Filter: {filter_expr}")
        print(f"    Error: {error_msg}")
        return False


if __name__ == "__main__":
    print("=" * 80)
    print("COMPREHENSIVE OData FILTER SYNTAX TESTS")
    print("=" * 80)

    # Test 1: Basic query
    print("\n[Test 1] Basic query without filter")
    test1 = test_basic_no_filter()
    print(f"  Result: {'PASS' if test1 else 'FAIL'}")

    # Test 2+: Filter variations
    print("\n[Test 2] Equality Operators")
    eq_tests = [
        ("String equality", "actor_login eq 'Copilot'"),
        ("String inequality", "actor_login ne 'Copilot'"),
    ]
    eq_results = [run_filter_test(name, expr) for name, expr in eq_tests]
    print(f"  Result: {sum(eq_results)}/{len(eq_results)} passed")

    print("\n[Test 3] Comparison Operators")
    cmp_tests = [
        ("Greater than", "number gt 5"),
        ("Greater or equal", "number ge 5"),
        ("Less than", "number lt 10"),
        ("Less or equal", "number le 10"),
    ]
    cmp_results = [run_filter_test(name, expr) for name, expr in cmp_tests]
    print(f"  Result: {sum(cmp_results)}/{len(cmp_results)} passed")

    print("\n[Test 4] Logical Operators - AND")
    and_tests = [
        ("AND two conditions", "actor_login eq 'Copilot' and action eq 'opened'"),
        (
            "AND three conditions",
            "actor_login eq 'Copilot' and action eq 'opened' and repository_name eq 'mabel'",
        ),
    ]
    and_results = [run_filter_test(name, expr) for name, expr in and_tests]
    print(f"  Result: {sum(and_results)}/{len(and_results)} passed")

    print("\n[Test 5] Logical Operators - OR")
    or_tests = [
        ("OR two conditions", "action eq 'opened' or action eq 'closed'"),
        ("OR three conditions", "action eq 'opened' or action eq 'closed' or action eq 'edited'"),
    ]
    or_results = [run_filter_test(name, expr) for name, expr in or_tests]
    print(f"  Result: {sum(or_results)}/{len(or_results)} passed")

    print("\n[Test 6] Logical Operators - NOT")
    not_tests = [
        ("NOT simple", "not (action eq 'deleted')"),
        ("NOT with AND", "not (action eq 'deleted') and actor_login eq 'Copilot'"),
    ]
    not_results = [run_filter_test(name, expr) for name, expr in not_tests]
    print(f"  Result: {sum(not_results)}/{len(not_results)} passed")

    print("\n[Test 7] Complex Logical Combinations")
    complex_tests = [
        ("AND with OR", "(action eq 'opened' or action eq 'closed') and actor_login eq 'Copilot'"),
        (
            "Nested parentheses",
            "((action eq 'opened' and actor_login eq 'Copilot') or action eq 'closed')",
        ),
    ]
    complex_results = [run_filter_test(name, expr) for name, expr in complex_tests]
    print(f"  Result: {sum(complex_results)}/{len(complex_results)} passed")

    print("\n[Test 8] String Functions (if supported)")
    str_tests = [
        ("startswith", "startswith(actor_login, 'Co')"),
        ("endswith", "endswith(actor_login, 'lot')"),
        ("contains", "contains(actor_login, 'pil')"),
    ]
    str_results = [run_filter_test(name, expr) for name, expr in str_tests]
    print(f"  Result: {sum(str_results)}/{len(str_results)} passed")

    print("\n[Test 9] Case Functions")
    case_tests = [
        ("tolower", "tolower(actor_login) eq 'copilot'"),
        ("toupper", "toupper(actor_login) eq 'COPILOT'"),
    ]
    case_results = [run_filter_test(name, expr) for name, expr in case_tests]
    print(f"  Result: {sum(case_results)}/{len(case_results)} passed")

    print("\n[Test 10] Length Function")
    len_tests = [
        ("length equals", "length(actor_login) eq 7"),
        ("length greater than", "length(actor_login) gt 5"),
    ]
    len_results = [run_filter_test(name, expr) for name, expr in len_tests]
    print(f"  Result: {sum(len_results)}/{len(len_results)} passed")

    print("\n[Test 11] Substring Function")
    substr_tests = [
        ("substring equals", "substring(actor_login, 0, 2) eq 'Co'"),
        ("substring with length", "substring(actor_login, 0, 1) eq 'C'"),
    ]
    substr_results = [run_filter_test(name, expr) for name, expr in substr_tests]
    print(f"  Result: {sum(substr_results)}/{len(substr_results)} passed")

    # Summary
    all_results = (
        [test1]
        + eq_results
        + cmp_results
        + and_results
        + or_results
        + not_results
        + complex_results
        + str_results
        + case_results
        + len_results
        + substr_results
    )
    total_passed = sum(all_results)
    total_tests = len(all_results)

    print("\n" + "=" * 80)
    print(f"OVERALL RESULTS: {total_passed}/{total_tests} tests passed")
    print("=" * 80)
