#!/usr/bin/env python3
"""Quick test to verify LogicalColumn fix works."""

import os
import sys

import opteryx
from opteryx.connectors import OpteryxConnector
from opteryx_catalog import OpteryxCatalog

from app.odata.odata_to_logical_plan import ODataLogicalPlanBuilder

os.environ["OPTERYX_DEBUG"] = "1"

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


def test_filter(filter_expr, expected_min_rows=1):
    """Test a filter expression."""
    print(f"\nTesting: {filter_expr}")
    builder = ODataLogicalPlanBuilder("public.github.events")
    plan = builder.build(filter_expr=filter_expr, top=10)
    result = opteryx.execute_logical_plan(plan)
    print(f"  Result: {result.num_rows} rows")

    if result.num_rows >= expected_min_rows:
        print("  ✓ PASS")
        return True
    else:
        print(f"  ✗ FAIL - Expected at least {expected_min_rows} rows")
        return False


if __name__ == "__main__":
    tests = [
        ("actor_login eq 'Copilot'", 1),
        ("actor_id gt 100000000", 1),
        ("type eq 'PushEvent'", 1),
        ("actor_login ne 'Copilot'", 1),
        ("actor_login eq 'Copilot' and type eq 'PushEvent'", 1),
        ("actor_login eq 'Copilot' or type eq 'PullRequestEvent'", 1),
        ("not (actor_login eq 'deleted')", 1),
    ]

    print("=" * 80)
    print("FILTER TESTS - LogicalColumn Fix Verification")
    print("=" * 80)

    passed = 0
    failed = 0
    for filter_expr, min_rows in tests:
        if test_filter(filter_expr, min_rows):
            passed += 1
        else:
            failed += 1

    print("\n" + "=" * 80)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 80)

    sys.exit(0 if failed == 0 else 1)
