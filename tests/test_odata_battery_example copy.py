#!/usr/bin/env python3
"""OData Test Battery - Quick validation of OData query parsing and execution.

Test battery format: List of tuples
  (dataset, filter_expr, orderby_expr, select_cols, top, skip, count_requested, expected_rows, expected_columns, expected_error)

Each test case validates:
- The query parses without error (if expected_error is None)
- The result has the expected number of rows
- The result has the expected number of columns
- Any expected errors are raised

"""

import os
import sys
from typing import List
from typing import Optional
from typing import Tuple
from urllib.parse import parse_qs

os.environ["OPTERYX_DEBUG"] = "1"

sys.path.insert(1, os.path.join(sys.path[0], "../../pyiceberg-firestore-gcs"))
sys.path.insert(1, os.path.join(sys.path[0], "../../opteryx-core"))
sys.path.insert(0, os.path.join(sys.path[0], "../"))

from opteryx import set_default_connector
from opteryx.connectors import OpteryxConnector
from opteryx_catalog import OpteryxCatalog

from app.routes.v4.interface import execute_odata_query


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
    """Parse OData query string into individual parameters.

    Examples:
        "$top=1" -> {"top": 1}
        "$orderby=published_at desc&$top=1" -> {"orderby": "published_at desc", "top": 1}
        "$filter=status eq 'open'&$select=id,status&$top=5" ->
            {"filter": "status eq 'open'", "select": "id,status", "top": 5}
    """
    # Initialize with all None values
    params = {
        "filter_expr": None,
        "orderby_expr": None,
        "select_cols": None,
        "top": None,
        "skip": None,
        "count_requested": False,
    }

    # Parse query string
    parsed = parse_qs(query_string, keep_blank_values=True)

    for key, values in parsed.items():
        # parse_qs returns lists, get the first value
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


def run_test_case(
    dataset: str,
    query_string: str,
    expected_rows: Optional[int],
    expected_columns: Optional[int],
    expected_error: Optional[str],
) -> dict:
    """Run a single test case and return results.

    Args:
        dataset: Table name (e.g., "public.github.events")
        query_string: OData query string (e.g., "$filter=status eq 'open'&$top=5")
        expected_rows: Expected number of rows (None if not validated)
        expected_columns: Expected number of columns (None if not validated)
        expected_error: Expected error type (None if no error expected)

    Returns:
        {
            "passed": bool,
            "actual_rows": int,
            "actual_columns": int,
            "error": str or None,
            "reason": str or None,
        }
    """
    try:
        # Parse query string into OData parameters
        parsed_params = parse_odata_query_string(query_string)

        # Execute query directly with parsed parameters
        result = execute_odata_query(
            table_name=dataset,
            filter_expr=parsed_params.get("filter_expr"),
            orderby_expr=parsed_params.get("orderby_expr"),
            select_cols=parsed_params.get("select_cols"),
            top=parsed_params.get("top"),
            skip=parsed_params.get("skip"),
            count_requested=parsed_params.get("count_requested", False),
        )

        actual_rows = len(result["rows"])
        actual_columns = len(result["col_names"])

        # Check for unexpected error
        if expected_error is not None:
            return {
                "passed": False,
                "actual_rows": actual_rows,
                "actual_columns": actual_columns,
                "error": None,
                "reason": f"Expected error '{expected_error}' but query succeeded",
            }

        # Check row count (if expected_rows is specified)
        if expected_rows is not None and actual_rows != expected_rows:
            return {
                "passed": False,
                "actual_rows": actual_rows,
                "actual_columns": actual_columns,
                "error": None,
                "reason": f"Row count mismatch: got {actual_rows}, expected {expected_rows}",
            }

        # Check column count (if expected_columns is specified)
        if expected_columns is not None and actual_columns != expected_columns:
            return {
                "passed": False,
                "actual_rows": actual_rows,
                "actual_columns": actual_columns,
                "error": None,
                "reason": f"Column count mismatch: got {actual_columns}, expected {expected_columns}",
            }

        # All checks passed
        return {
            "passed": True,
            "actual_rows": actual_rows,
            "actual_columns": actual_columns,
            "error": None,
            "reason": None,
        }

    except Exception as e:
        error_type = type(e).__name__

        # Check if expected error matches
        if expected_error is not None:
            return {
                "passed": True,
                "actual_rows": 0,
                "actual_columns": 0,
                "error": error_type,
                "reason": None,
            }

        # Unexpected error
        return {
            "passed": False,
            "actual_rows": 0,
            "actual_columns": 0,
            "error": error_type,
            "reason": str(e)[:100],
        }


def run_battery(test_cases: List[Tuple]) -> dict:
    """Run the test battery and return summary.

    Args:
        test_cases: List of tuples:
            (dataset, query_string, expected_rows, expected_columns, expected_error)

    Returns:
        {
            "total": int,
            "passed": int,
            "failed": int,
            "elapsed": float,
            "results": list,
        }
    """
    import time

    start_time = time.time()
    results = []
    passed = 0
    failed = 0

    for i, test_case in enumerate(test_cases, 1):
        dataset, query_string, exp_rows, exp_cols, exp_error = test_case

        test_result = run_test_case(
            dataset=dataset,
            query_string=query_string,
            expected_rows=exp_rows,
            expected_columns=exp_cols,
            expected_error=exp_error,
        )
        results.append(
            {
                "test_num": i,
                "dataset": dataset,
                "query_string": query_string,
                "expected": {"rows": exp_rows, "columns": exp_cols, "error": exp_error},
                **test_result,
            }
        )

        if test_result["passed"]:
            passed += 1
        else:
            failed += 1
            # Show failures
            if i <= 10 or failed <= 5:  # Show first 10 and up to 5 failures
                print(f"FAIL #{i}: {dataset}")
                print(f"  query: {query_string}")
                print(f"  Reason: {test_result['reason']}")

    elapsed = time.time() - start_time

    return {
        "total": len(test_cases),
        "passed": passed,
        "failed": failed,
        "elapsed": elapsed,
        "results": results,
    }


# ==============================================================================
# EXAMPLE TEST BATTERIES
# ==============================================================================

# Format: (dataset, query_string, expected_rows, expected_columns, expected_error)


EXTENDED_TEST_BATTERY = [
    (
        "public.examples.vulnerabilities",
        "$filter=vendor eq 'Microsoft'&$count=true&$top=0",
        0,
        7,
        None,
    ),
]


if __name__ == "__main__":
    # Run comprehensive planets test battery
    print("=" * 80)
    print("OData Planets Test Battery - 53 Tests")
    print("=" * 80)
    print()
    print("Known Issues:")
    print("  - $orderby parameter broken (AttributeError in binder)")
    print("  - $select with multiple columns broken (list concatenation issue)")
    print("  - Filter row counts may be incorrect")
    print("  - not() function not recognized")
    print()
    print("Running available tests...")
    print("=" * 80)

    summary = run_battery(EXTENDED_TEST_BATTERY)

    print()
    print(
        f"Passed:  {summary['passed']}/{summary['total']} ({100 * summary['passed'] // summary['total']}%)"
    )
    print(
        f"Failed:  {summary['failed']}/{summary['total']} ({100 * summary['failed'] // summary['total']}%)"
    )
    print(f"Time:    {summary['elapsed']:.2f}s")

    if summary["failed"] > 0:
        print()
        print("Failed Tests (by category):")

        # Categorize failures by issue type
        orderby_fails = []
        listconcat_fails = []
        function_fails = []
        count_fails = []
        other_fails = []

        for result in summary["results"]:
            if not result["passed"]:
                error_str = result.get("reason", "")
                if "node_type" in error_str:
                    orderby_fails.append(result)
                elif "concatenate list" in error_str:
                    listconcat_fails.append(result)
                elif "Unknown function" in error_str:
                    function_fails.append(result)
                elif "Count" in error_str:
                    count_fails.append(result)
                else:
                    other_fails.append(result)

        # Print ALL failures grouped by error message
        print(f"\n  All {summary['failed']} failures:")
        for result in summary["results"]:
            if not result["passed"]:
                table = result.get("dataset", "unknown")
                query_str = result.get("query_string", "unknown")
                print(f"    • Test #{result['test_num']}: {table}?{query_str}")
                print(f"      Error: {result.get('reason', result.get('error', 'Unknown'))}")

    sys.exit(0 if summary["failed"] == 0 else 1)
