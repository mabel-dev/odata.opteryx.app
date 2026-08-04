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
import pytest
from urllib.parse import parse_qs

from pathlib import Path

_tests_dir = Path(__file__).resolve().parent
_project_root = _tests_dir.parent
# The external dev packages live at the workspace root (one level up from repo root)
_workspace_root = _project_root.parent
sys.path.insert(1, str(_workspace_root / "pyiceberg-firestore-gcs"))
sys.path.insert(1, str(_workspace_root / "opteryx-core"))
sys.path.insert(0, str(_project_root / "app"))


from opteryx import set_default_connector
from opteryx import register_workspace
from opteryx.connectors import OpteryxConnector
from opteryx_catalog import OpteryxCatalog
from opteryx.connectors import DiskConnector

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

# Create a local filesystem for testdata
register_workspace("testdata", DiskConnector)


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
        "apply_expr": None,
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
        elif key == "$apply":
            params["apply_expr"] = value

    return params


def run_test_case(
    dataset: str,
    query_string: str,
    expected_rows: Optional[int],
    expected_columns: Optional[int],
    expected_error: Optional[str],
    expected_count: Optional[int] = None,
) -> dict:
    """Run a single test case and return results.

    Args:
        dataset: Table name (e.g., "public.github.events")
        query_string: OData query string (e.g., "$filter=status eq 'open'&$top=5")
        expected_rows: Expected number of rows (None if not validated)
        expected_columns: Expected number of columns (None if not validated)
        expected_error: Expected error type (None if no error expected)
        expected_count: Expected @odata.count value (None if not validated)

    Returns:
        {
            "passed": bool,
            "actual_rows": int,
            "actual_columns": int,
            "actual_count": int or None,
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
            apply_expr=parsed_params.get("apply_expr"),
            count_requested=parsed_params.get("count_requested", False),
        )

        # execute_odata_query() returns the rows already serialised as
        # `value_bytes` (a complete JSON array) plus their count, rather than a
        # materialised "rows" list -- so take the count directly.
        actual_rows = result["row_count"]
        actual_columns = len(result["col_names"])
        actual_count = result.get("total_count")

        # Check for unexpected error
        if expected_error is not None:
            return {
                "passed": False,
                "actual_rows": actual_rows,
                "actual_columns": actual_columns,
                "actual_count": actual_count,
                "error": None,
                "reason": f"Expected error '{expected_error}' but query succeeded",
            }

        # Check row count (if expected_rows is specified)
        if expected_rows is not None and actual_rows != expected_rows:
            return {
                "passed": False,
                "actual_rows": actual_rows,
                "actual_columns": actual_columns,
                "actual_count": actual_count,
                "error": None,
                "reason": f"Row count mismatch: got {actual_rows}, expected {expected_rows}",
            }

        # Check column count (if expected_columns is specified)
        if expected_columns is not None and actual_columns != expected_columns:
            return {
                "passed": False,
                "actual_rows": actual_rows,
                "actual_columns": actual_columns,
                "actual_count": actual_count,
                "error": None,
                "reason": f"Column count mismatch: got {actual_columns}, expected {expected_columns}",
            }

        # Check count value (if expected_count is specified)
        if expected_count is not None and actual_count != expected_count:
            return {
                "passed": False,
                "actual_rows": actual_rows,
                "actual_columns": actual_columns,
                "actual_count": actual_count,
                "error": None,
                "reason": f"Count mismatch: got {actual_count}, expected {expected_count}",
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
        # Support both 5- and 6-tuple test cases
        if len(test_case) == 5:
            dataset, query_string, exp_rows, exp_cols, exp_error = test_case
            exp_count = None
        elif len(test_case) == 6:
            dataset, query_string, exp_rows, exp_cols, exp_count, exp_error = test_case
        else:
            raise ValueError(f"Invalid test tuple length: {len(test_case)}")

        test_result = run_test_case(
            dataset=dataset,
            query_string=query_string,
            expected_rows=exp_rows,
            expected_columns=exp_cols,
            expected_error=exp_error,
            expected_count=exp_count,
        )
        results.append(
            {
                "test_num": i,
                "dataset": dataset,
                "query_string": query_string,
                "expected": {
                    "rows": exp_rows,
                    "columns": exp_cols,
                    "count": exp_count,
                    "error": exp_error,
                },
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

# Format: (dataset, query_string, expected_rows, expected_columns, expected_count, expected_error)

# fmt:off
PLANETS_TEST_BATTERY = [
    ('$planets', '$top=1', 1, 20, None, None),
    ('$planets', '$top=5', 5, 20, None, None),
    ('$planets', '$top=9', 9, 20, None, None),
    ('$planets', '$top=20', 9, 20, None, None),
    ('$planets', '$top=0', 0, 20, None, None),
    ('$planets', '$orderby=id&$top=1', 1, 20, None, None),
    ('$planets', '$orderby=id asc&$top=1', 1, 20, None, None),
    ('$planets', '$orderby=id desc&$top=1', 1, 20, None, None),
    ('$planets', '$orderby=name asc&$top=1', 1, 20, None, None),
    ('$planets', '$orderby=name desc&$top=1', 1, 20, None, None),
    ('$planets', '$orderby=mass desc&$top=1', 1, 20, None, None),
    ('$planets', '$orderby=distance_from_sun asc&$top=1', 1, 20, None, None),
    ('$planets', '$orderby=mean_temperature asc&$top=1', 1, 20, None, None),
    ('$planets', '$orderby=number_of_moons desc,name asc&$top=3', 3, 20, None, None),
    ('$planets', '$orderby=mass desc,id asc&$top=5', 5, 20, None, None),
    ('$planets', '$orderby=mean_temperature asc,distance_from_sun desc&$top=4', 4, 20, None, None),
    ('$planets', '$orderby=orbital_eccentricity desc,orbital_period asc&$top=2', 2, 20, None, None),
    ('$planets', '$select=name&$top=1', 1, 1, None, None),
    ('$planets', '$select=id&$top=5', 5, 1, None, None),
    ('$planets', '$select=mass&$top=9', 9, 1, None, None),
    ('$planets', '$select=distance_from_sun&$top=3', 3, 1, None, None),
    ('$planets', '$select=number_of_moons&$top=1', 1, 1, None, None),
    ('$planets', '$select=id,name&$top=1', 1, 2, None, None),
    ('$planets', '$select=name,mass,diameter&$top=5', 5, 3, None, None),
    ('$planets', '$select=id,name,distance_from_sun&$top=3', 3, 3, None, None),
    ('$planets', '$select=id,name,mass,diameter,number_of_moons&$top=2', 2, 5, None, None),
    ('$planets', '$select=*&$top=1', 1, 20, None, None),
    ('$planets', '$filter=id eq 1&$top=10', 1, 20, None, None),
    ('$planets', '$filter=id eq 5&$top=10', 1, 20, None, None),
    ('$planets', "$filter=name eq 'Earth'&$top=10", 1, 20, None, None),
    ('$planets', "$filter=name eq 'Jupiter'&$top=10", 1, 20, None, None),
    ('$planets', '$filter=number_of_moons eq 0&$top=10', 2, 20, None, None),
    ('$planets', '$filter=mass gt 100&$top=10', 3, 20, None, None),
    ('$planets', '$filter=mass lt 10&$top=10', 5, 20, None, None),
    ('$planets', '$filter=diameter gt 10000&$top=10', 6, 20, None, None),
    ('$planets', '$filter=distance_from_sun lt 200&$top=10', 3, 20, None, None),
    ('$planets', '$filter=mean_temperature ge 0&$top=10', 3, 20, None, None),
    ('$planets', '$filter=number_of_moons le 2&$top=10', 4, 20, None, None),
    ('$planets', '$filter=number_of_moons ne 0&$top=10', 7, 20, None, None),
    ('$planets', "$filter=name ne 'Earth'&$top=10", 8, 20, None, None),
    ('$planets', '$filter=id ne 1&$top=10', 8, 20, None, None),
    ('$planets', "$filter=startswith(name,'M')&$top=10", 2, 20, None, None),
    ('$planets', "$filter=startswith(name,'J')&$top=10", 1, 20, None, None),
    ('$planets', "$filter=endswith(name,'e')&$top=10", 1, 20, None, None),
    ('$planets', "$filter=startswith(name,'E')&$top=10", 1, 20, None, None),
    ('$planets', "$filter=startswith(name,'S')&$top=10", 1, 20, None, None),
    ('$planets', "$filter=startswith(name,'V')&$top=10", 1, 20, None, None),
    ('$planets', "$filter=startswith(name,'N')&$top=10", 1, 20, None, None),
    ('$planets', "$filter=startswith(name,'U')&$top=10", 1, 20, None, None),
    ('$planets', "$filter=startswith(name,'P')&$top=10", 1, 20, None, None),
    ('$planets', "$filter=startswith(name,'Ma')&$top=10", 1, 20, None, None),
    ('$planets', "$filter=startswith(name,'Ju')&$top=10", 1, 20, None, None),
    ('$planets', "$filter=startswith(name,'Mer')&$top=10", 1, 20, None, None),
    ('$planets', "$filter=startswith(name,'Z')&$top=10", 0, 20, None, None),
    ('$planets', "$filter=endswith(name,'s')&$top=10", 3, 20, None, None),
    ('$planets', "$filter=endswith(name,'h')&$top=10", 1, 20, None, None),
    ('$planets', "$filter=endswith(name,'n')&$top=10", 1, 20, None, None),
    ('$planets', "$filter=endswith(name,'r')&$top=10", 1, 20, None, None),
    ('$planets', "$filter=endswith(name,'g')&$top=10", 0, 20, None, None),
    ('$planets', "$filter=endswith(name,'o')&$top=10", 1, 20, None, None),
    ('$planets', "$filter=endswith(name,'rs')&$top=10", 1, 20, None, None),
    ('$planets', "$filter=endswith(name,'ter')&$top=10", 1, 20, None, None),
    ('$planets', "$filter=endswith(name,'ury')&$top=10", 1, 20, None, None),
    ('$planets', "$filter=endswith(name,'tune')&$top=10", 1, 20, None, None),
    ('$planets', "$filter=endswith(name,'xyz')&$top=10", 0, 20, None, None),
    ('$planets', "$filter=contains(name,'ar')&$top=10", 2, 20, None, None),
    ('$planets', "$filter=contains(name,'ur')&$top=10", 2, 20, None, None),
    ('$planets', "$filter=contains(name,'er')&$top=10", 2, 20, None, None),
    ('$planets', "$filter=contains(name,'pt')&$top=10", 1, 20, None, None),
    ('$planets', "$filter=contains(name,'ut')&$top=10", 1, 20, None, None),
    ('$planets', "$filter=contains(name,'en')&$top=10", 1, 20, None, None),
    ('$planets', "$filter=contains(name,'a')&$top=10", 4, 20, None, None),
    ('$planets', "$filter=contains(name,'u')&$top=10", 7, 20, None, None),
    ('$planets', "$filter=contains(name,'e')&$top=10", 4, 20, None, None),
    ('$planets', "$filter=contains(name,'xyz')&$top=10", 0, 20, None, None),
    ('$planets', "$filter=contains(name,'Mars')&$top=10", 1, 20, None, None),
    ('$planets', "$filter=contains(name,'Earth')&$top=10", 1, 20, None, None),
    ('$planets', "$filter=tolower(name) eq 'earth'&$top=10", 1, 20, None, None),
    ('$planets', "$filter=tolower(name) eq 'mars'&$top=10", 1, 20, None, None),
    ('$planets', "$filter=tolower(name) eq 'jupiter'&$top=10", 1, 20, None, None),
    ('$planets', "$filter=tolower(name) eq 'saturn'&$top=10", 1, 20, None, None),
    ('$planets', "$filter=tolower(name) eq 'venus'&$top=10", 1, 20, None, None),
    ('$planets', "$filter=tolower(name) eq 'mercury'&$top=10", 1, 20, None, None),
    ('$planets', "$filter=tolower(name) eq 'neptune'&$top=10", 1, 20, None, None),
    ('$planets', "$filter=tolower(name) eq 'pluto'&$top=10", 1, 20, None, None),
    ('$planets', "$filter=toupper(name) eq 'EARTH'&$top=10", 1, 20, None, None),
    ('$planets', "$filter=toupper(name) eq 'MARS'&$top=10", 1, 20, None, None),
    ('$planets', "$filter=toupper(name) eq 'JUPITER'&$top=10", 1, 20, None, None),
    ('$planets', "$filter=toupper(name) eq 'SATURN'&$top=10", 1, 20, None, None),
    ('$planets', "$filter=toupper(name) eq 'VENUS'&$top=10", 1, 20, None, None),
    ('$planets', "$filter=toupper(name) eq 'MERCURY'&$top=10", 1, 20, None, None),
    ('$planets', "$filter=toupper(name) eq 'URANUS'&$top=10", 1, 20, None, None),
    ('$planets', "$filter=toupper(name) eq 'NEPTUNE'&$top=10", 1, 20, None, None),
    ('$planets', "$filter=startswith(name,'M') and contains(name,'ar')&$top=10", 1, 20, None, None),
    ('$planets', "$filter=endswith(name,'s') and startswith(name,'M')&$top=10", 1, 20, None, None),
    ('$planets', "$filter=contains(name,'u') and endswith(name,'r')&$top=10", 1, 20, None, None),
    ('$planets', "$filter=startswith(tolower(name),'e')&$top=10", 1, 20, None, None),
    ('$planets', "$filter=endswith(toupper(name),'S')&$top=10", 3, 20, None, None),
    ('$planets', "$filter=contains(tolower(name),'ear')&$top=10", 1, 20, None, None),
    ('$planets', "$filter=tolower(name) eq 'mars' and mass lt 1&$top=10", 1, 20, None, None),
    ('$planets', "$filter=startswith(name,'J') or startswith(name,'S')&$top=10", 2, 20, None, None),
    ('$planets', "$filter=contains(name,'e') and number_of_moons gt 0&$top=10", 2, 20, None, None),
    ('$planets', "$filter=toupper(name) eq 'EARTH' and id eq 3&$top=10", 1, 20, None, None),
    ('$planets', '$filter=number_of_moons gt 0 and mass gt 50&$top=10', 4, 20, None, None),
    ('$planets', '$filter=number_of_moons gt 0 or mass gt 100&$top=10', 7, 20, None, None),
    ('$planets', '$filter=distance_from_sun lt 500 and number_of_moons eq 0&$top=10', 2, 20, None, None),
    ('$planets', '$filter=not(number_of_moons eq 0)&$top=10', 7, 20, None, None),
    ('$planets', '$filter=(mass gt 50 or mass lt 1) and diameter gt 5000&$top=10', 5, 20, None, None),
    ('$planets', '$filter=mean_temperature lt 200 or mean_temperature gt 400&$top=10', 9, 20, None, None),
    ('$planets', '$select=id,name&$orderby=id desc&$top=3', 3, 2, None, None),
    ('$planets', '$select=name,mass&$orderby=mass desc&$top=1', 1, 2, None, None),
    ('$planets', '$select=id,name,distance_from_sun&$orderby=distance_from_sun asc&$top=2', 2, 3, None, None),
    ('$planets', '$select=name,number_of_moons&$orderby=number_of_moons desc,name asc&$top=3', 3, 2, None, None),
    ('$planets', '$select=*&$orderby=mean_temperature asc&$top=4', 4, 20, None, None),
    ('$planets', '$filter=number_of_moons gt 0&$orderby=number_of_moons desc&$top=3', 3, 20, None, None),
    ('$planets', '$filter=mass lt 50&$orderby=mass desc&$top=2', 2, 20, None, None),
    ('$planets', '$filter=distance_from_sun lt 500&$orderby=id asc&$top=5', 4, 20, None, None),
    ('$planets', '$filter=mean_temperature gt 100&$orderby=mean_temperature desc&$top=2', 2, 20, None, None),
    ('$planets', '$filter=diameter gt 10000&$orderby=distance_from_sun asc&$top=3', 3, 20, None, None),
    ('$planets', '$filter=number_of_moons gt 0&$select=id,name,number_of_moons&$top=3', 3, 3, None, None),
    ('$planets', '$filter=mass lt 100&$select=name,mass&$top=4', 4, 2, None, None),
    ('$planets', '$filter=distance_from_sun lt 300&$select=id,name,distance_from_sun&$top=5', 4, 3, None, None),
    ('$planets', '$filter=mean_temperature gt 0&$select=id,name,mean_temperature&$top=5', 3, 3, None, None),
    ('$planets', '$filter=orbital_eccentricity lt 0.1&$select=name,orbital_eccentricity&$top=4', 4, 2, None, None),
    ('$planets', '$filter=number_of_moons gt 0&$select=id,name,number_of_moons&$orderby=number_of_moons desc&$top=2', 2, 3, None, None),
    ('$planets', '$filter=mass lt 50&$select=name,mass&$orderby=mass desc&$top=3', 3, 2, None, None),
    ('$planets', '$filter=distance_from_sun lt 500&$select=id,name,distance_from_sun&$orderby=distance_from_sun asc&$top=4', 4, 3, None, None),
    ('$planets', '$filter=mean_temperature gt 0&$select=id,name,mean_temperature&$orderby=mean_temperature asc&$top=3', 3, 3, None, None),
    ('$planets', '$filter=diameter gt 5000&$select=id,name,diameter&$orderby=diameter desc&$top=4', 4, 3, None, None),
    ('$planets', '$top=3&$skip=0', 3, 20, None, None),
    ('$planets', '$top=3&$skip=3', 3, 20, None, None),
    ('$planets', '$top=3&$skip=6', 3, 20, None, None),
    ('$planets', '$top=10&$skip=8', 1, 20, None, None),
    ('$planets', '$orderby=id asc&$top=3&$skip=0', 3, 20, None, None),
    ('$planets', '$orderby=id asc&$top=3&$skip=3', 3, 20, None, None),
    ('$planets', '$orderby=id desc&$top=2&$skip=2', 2, 20, None, None),
    ('$planets', '$orderby=name asc&$top=5&$skip=4', 5, 20, None, None),
    ('$planets', '$filter=number_of_moons gt 0&$top=2&$skip=0', 2, 20, None, None),
    ('$planets', '$filter=mass lt 50&$top=3&$skip=2', 3, 20, None, None),
    ('$planets', '$filter=distance_from_sun lt 500&$top=2&$skip=1', 2, 20, None, None),
    ('$planets', '$count=true&$top=0', 0, 20, 9, None),
    ('$planets', '$count=true&$top=5', 5, 20, 9, None),
    ('$planets', '$filter=number_of_moons gt 0&$count=true', None, 20, 7, None),
    ('$planets', '$count=true', None, 20, 9, None),
    ('$planets', '$count=true&$filter=id eq 1', None, 20, 1, None),
    ('$planets', "$count=true&$filter=name eq 'Earth'", None, 20, 1, None),
    ('$planets', '$count=true&$filter=startswith(name,\'M\')', None, 20, 2, None),
    ('$planets', '$count=true&$filter=startswith(name,\'J\')', None, 20, 1, None),
    ('$planets', '$count=true&$filter=contains(name,\'u\')', None, 20, 7, None),
    ('$planets', '$count=true&$filter=contains(name,\'e\')', None, 20, 4, None),
    ('$planets', '$count=true&$filter=mean_temperature ge 0', None, 20, 3, None),
    ('$planets', '$count=true&$filter=number_of_moons le 2', None, 20, 4, None),
    ('$planets', '$count=true&$filter=number_of_moons ne 0', None, 20, 7, None),
    ('$planets', '$count=true&$filter=diameter gt 10000', None, 20, 6, None),
    ('$planets', '$count=true&$filter=mass gt 100', None, 20, 3, None),
    ('$planets', '$count=true&$filter=mass lt 10', None, 20, 5, None),
    ('$planets', '$count=true&$filter=distance_from_sun lt 500', None, 20, 4, None),
    ('$planets', '$count=true&$filter=number_of_moons gt 0', None, 20, 7, None),
    ('$planets', '$count=true&$filter=mass gt 100&$top=0', 0, 20, 3, None),
    ('$planets', '$count=true&$filter=mass gt 100&$top=1', 1, 20, 3, None),
    ('$planets', '$count=true&$filter=id in (1,2,3,4,5,6,7,8,9)', None, 20, 9, None),
    ('$planets', '$count=true&$filter=id in (2,4,6,8) and number_of_moons gt 10', None, 20, 2, None),
    ('$planets', '$count=true&$filter=not(id gt 5)', None, 20, 5, None),
    ('$planets', '$count=true&$filter=distance_from_sun lt 200', None, 20, 3, None),
    ('$planets', '$count=true&$filter=mean_temperature gt 100', None, 20, 2, None),
    ('$planets', '$count=true&$filter=contains(name,\'Mars\')', None, 20, 1, None),
    ('$planets', '$count=true&$filter=endswith(name,\'ter\')', None, 20, 1, None),
    ('$planets', '$count=true&$filter=endswith(name,\'ury\')', None, 20, 1, None),
    ('$planets', '$count=true&$filter=contains(name,\'pt\')', None, 20, 1, None),
    ('$planets', '$count=true&$filter=id mod 2 eq 0', None, 20, 4, None),
    ('$planets', '$count=true&$filter=indexof(name,\'a\') gt 0', None, 20, 4, None),
    ('$planets', "$count=true&$filter=substring(name,0,3) eq 'Mar'", None, 20, 1, None),
    ('$planets', '$count=true&$filter=number_of_moons in (0,1,2)', None, 20, 4, None),
    ('$planets', '$count=true&$filter=id in (1,3,5)', None, 20, 3, None),
    ('$planets', '$count=true&$filter=id in (2,4,6)', None, 20, 3, None),
    ('$planets', '$count=true&$filter=startswith(name,\'Ma\')', None, 20, 1, None),
    ('$planets', '$count=true&$filter=startswith(name,\'Ju\')', None, 20, 1, None),
    ('$planets', '$count=true&$filter=endswith(name,\'s\') and startswith(name,\'M\')', None, 20, 1, None),
    ('$planets', "$count=true&$filter=tolower(name) eq 'mars'", None, 20, 1, None),
    ('$planets', "$count=true&$filter=toupper(name) eq 'EARTH'", None, 20, 1, None),
    ('$planets', '$count=true&$top=1', 1, 20, 9, None),
    ('$planets', "$count=true&$filter=name ne 'Earth'", None, 20, 8, None),
    ('$planets', "$count=true&$filter=substring(name,1,3) eq 'erc'", None, 20, 1, None),
    ('$planets', '$filter=gravity gt 10&$top=10', 2, 20, None, None),
    ('$planets', '$filter=gravity lt 5&$top=10', 3, 20, None, None),
    ('$planets', '$select=name,gravity&$orderby=gravity desc&$top=3', 3, 2, None, None),
    ('$planets', '$filter=surface_pressure eq null', 4, 20, None, None),
    ('$planets', '$filter=surface_pressure ne null', 5, 20, None, None),
    ('$planets', '$skip=100', 0, 20, None, None),
    ('$planets', '$skip=8&$top=10', 1, 20, None, None),
    ('$planets', '$orderby=id asc,name asc&$top=9', 9, 20, None, None),
    ('$planets', '$select=id&$orderby=id&$top=5', 5, 1, None, None),
    ('$planets', '$filter=id gt 0 and id lt 10&$top=10', 9, 20, None, None),
    ('$planets', '$select=name,gravity,mean_temperature&$top=3', 3, 3, None, None),
    ('$planets', '$select=name,gravity,mean_temperature&$filter=mass gt 10&$top=2', 2, 3, None, None),
    ('$planets', '$filter=(mass gt 50 and number_of_moons gt 0)&$top=10', 4, 20, None, None),
    ('$planets', '$filter=mass gt 100 or number_of_moons gt 20&$top=10', 4, 20, None, None),
    ('$planets', '$top=2&$skip=7', 2, 20, None, None),
    ('$planets', '$skip=9&$top=1', 0, 20, None, None),
    ('$planets', "$select=id,name&$filter=name eq 'Earth'&$top=1", 1, 2, None, None),
    ('$planets', '$select=nonexistent&$top=1', None, None, None, 'Exception'),
    ('$planets', "$filter=mass gt 'abc'&$top=1", None, None, None, 'Exception'),
    ('$planets', "$filter=startswith(name,'M') and mass gt 0&$top=5", 2, 20, None, None),
    ('$planets', '$select=*,name&$top=1', None, None, None, 'Exception'),
    ('$planets', '$filter=gravity gt 10&$select=name,gravity&$top=2', 2, 2, None, None),
    ('$planets', '$filter=mean_temperature gt 0&$select=name,mean_temperature&$top=5', 3, 2, None, None),
    ('$planets', '$filter=id ge 1 and id le 3&$top=10', 3, 20, None, None),
    ('$planets', '$orderby=id desc&$skip=1&$top=2', 2, 20, None, None),
    ('$planets', '$orderby=invalid_column&$top=1', None, None, None, 'Exception'),
    ('$planets', '$filter=invalid_column eq 1', None, None, None, 'Exception'),
    ('$planets', '$select=invalid_column&$top=1', None, None, None, 'Exception'),
    ('$planets', '$filter=id eq&$top=1', None, None, None, 'Exception'),
    ('testdata.tpch.nation', '', 24, 4, None, None),
    ('testdata.tpch.region', '', 4, 3, None, None),
    ('testdata.tpch.nation', '$top=5', 5, 4, None, None),
    ('testdata.tpch.nation', '$top=10', 10, 4, None, None),
    ('testdata.tpch.nation', '$skip=20', 4, 4, None, None),
    ('testdata.tpch.nation', '$top=5&$skip=10', 5, 4, None, None),
    ('testdata.tpch.nation', '$select=n_name', 24, 1, None, None),
    ('testdata.tpch.nation', '$select=n_nationkey,n_name', 24, 2, None, None),
    ('testdata.tpch.nation', '$select=n_name,n_regionkey,n_comment', 24, 3, None, None),
    ('testdata.tpch.nation', '$filter=n_regionkey eq 1', None, 4, None, None),
    ('testdata.tpch.nation', '$filter=n_nationkey lt 5', None, 4, None, None),
    ('testdata.tpch.nation', '$filter=n_nationkey gt 20', None, 4, None, None),
    ('testdata.tpch.nation', '$filter=n_regionkey eq 0&$select=n_name', None, 1, None, None),
    ('testdata.tpch.nation', '$filter=n_nationkey ge 10 and n_nationkey le 15', None, 4, None, None),
    ('testdata.tpch.nation', '$orderby=n_name&$top=3', 3, 4, None, None),
    ('testdata.tpch.nation', '$orderby=n_nationkey desc&$top=5', 5, 4, None, None),
    ('testdata.tpch.region', '$top=3', 3, 3, None, None),
    ('testdata.tpch.region', '$select=r_name', 4, 1, None, None),
    ('testdata.tpch.region', '$select=r_regionkey,r_name', 4, 2, None, None),
    ('testdata.tpch.region', '$filter=r_regionkey lt 3', None, 3, None, None),
    ('testdata.tpch.region', '$filter=r_regionkey eq 2', None, 3, None, None),
    ('testdata.tpch.region', '$orderby=r_name&$top=2', 2, 3, None, None),
    ('testdata.tpch.region', '$skip=2', 2, 3, None, None),
    ('testdata.tpch.supplier', '$top=10', 10, 7, None, None),
    ('testdata.tpch.supplier', '$top=5', 5, 7, None, None),
    ('testdata.tpch.supplier', '$select=s_name', None, 1, None, None),
    ('testdata.tpch.supplier', '$select=s_suppkey,s_name,s_nationkey', None, 3, None, None),
    ('testdata.tpch.supplier', '$filter=s_nationkey eq 1&$top=20', None, 7, None, None),
    ('testdata.tpch.supplier', '$filter=s_suppkey lt 10', None, 7, None, None),
    ('testdata.tpch.supplier', '$filter=s_nationkey eq 0&$select=s_name,s_phone', None, 2, None, None),
    ('testdata.tpch.supplier', '$orderby=s_name&$top=5', 5, 7, None, None),
    ('testdata.tpch.supplier', '$skip=5&$top=10', 10, 7, None, None),
    ('testdata.tpch.customer', '$top=10', 10, 8, None, None),
    ('testdata.tpch.customer', '$top=5', 5, 8, None, None),
    ('testdata.tpch.customer', '$select=c_name', None, 1, None, None),
    ('testdata.tpch.customer', '$select=c_custkey,c_name,c_nationkey', None, 3, None, None),
    ('testdata.tpch.customer', '$filter=c_nationkey eq 5&$top=20', None, 8, None, None),
    ('testdata.tpch.customer', '$filter=c_custkey lt 20', None, 8, None, None),
    ('testdata.tpch.customer', '$filter=c_nationkey ge 10&$top=50', None, 8, None, None),
    ('testdata.tpch.customer', '$orderby=c_name&$top=5', 5, 8, None, None),
    ('testdata.tpch.customer', '$skip=10&$top=15', 15, 8, None, None),
    ('testdata.tpch.part', '$top=10', 10, 9, None, None),
    ('testdata.tpch.part', '$top=20', 20, 9, None, None),
    ('testdata.tpch.part', '$select=p_name', None, 1, None, None),
    ('testdata.tpch.part', '$select=p_partkey,p_name,p_type', None, 3, None, None),
    ('testdata.tpch.part', '$filter=p_size eq 5&$top=30', None, 9, None, None),
    ('testdata.tpch.part', '$filter=p_partkey lt 50', None, 9, None, None),
    ('testdata.tpch.part', '$orderby=p_name&$top=8', 8, 9, None, None),
    ('testdata.tpch.part', '$skip=20&$top=10', 10, 9, None, None),
    ('testdata.tpch.orders', '$top=10', 10, 9, None, None),
    ('testdata.tpch.orders', '$top=25', 25, 9, None, None),
    ('testdata.tpch.orders', '$top=50', 50, 9, None, None),
    ('testdata.tpch.orders', '$skip=100&$top=20', 20, 9, None, None),
    ('testdata.tpch.orders', '$skip=200&$top=50', 50, 9, None, None),
    ('testdata.tpch.partsupp', '$top=10', 10, 5, None, None),
    ('testdata.tpch.partsupp', '$top=25', 25, 5, None, None),
    ('testdata.tpch.partsupp', '$top=50', 50, 5, None, None),
    ('testdata.tpch.partsupp', '$skip=50&$top=20', 20, 5, None, None),
    ('testdata.tpch.partsupp', '$skip=100&$top=25', 25, 5, None, None),
    ('testdata.tpch.nation', '$filter=n_regionkey ne 1', None, 4, None, None),
    ('testdata.tpch.nation', '$filter=n_regionkey eq 2', None, 4, None, None),
    ('testdata.tpch.nation', '$filter=n_regionkey eq 3', None, 4, None, None),
    ('testdata.tpch.nation', '$filter=n_nationkey ge 5 and n_nationkey lt 15&$select=n_name', None, 1, None, None),
    ('testdata.tpch.nation', '$filter=n_nationkey eq 10 or n_nationkey eq 20', None, 4, None, None),
    ('testdata.tpch.nation', '$orderby=n_regionkey,n_name&$top=10', 10, 4, None, None),
    ('testdata.tpch.nation', '$skip=5&$top=5&$select=n_name,n_regionkey', 5, 2, None, None),
    ('testdata.tpch.nation', '$filter=n_regionkey le 2&$top=20', None, 4, None, None),
    ('testdata.tpch.region', '$filter=r_regionkey ne 0', None, 3, None, None),
    ('testdata.tpch.region', '$filter=r_regionkey ge 1', None, 3, None, None),
    ('testdata.tpch.region', '$orderby=r_regionkey desc', 4, 3, None, None),
    ('testdata.tpch.region', '$skip=1&$top=2&$select=r_name', 2, 1, None, None),
    ('testdata.tpch.region', '$filter=r_regionkey eq 1 or r_regionkey eq 3', None, 3, None, None),
    ('testdata.tpch.supplier', '$filter=s_nationkey ge 5 and s_nationkey lt 10&$top=30', None, 7, None, None),
    ('testdata.tpch.supplier', '$filter=s_suppkey ge 20 and s_suppkey lt 40', None, 7, None, None),
    ('testdata.tpch.supplier', '$filter=s_nationkey eq 2 or s_nationkey eq 5', None, 7, None, None),
    ('testdata.tpch.supplier', '$orderby=s_nationkey,s_name&$top=12', 12, 7, None, None),
    ('testdata.tpch.supplier', '$skip=15&$top=8&$select=s_name,s_nationkey', 8, 2, None, None),
    ('testdata.tpch.supplier', '$filter=s_suppkey le 30&$top=35', None, 7, None, None),
    ('testdata.tpch.supplier', '$orderby=s_suppkey desc&$top=10', 10, 7, None, None),
    ('testdata.tpch.customer', '$filter=c_custkey ge 30 and c_custkey lt 60', None, 8, None, None),
    ('testdata.tpch.customer', '$filter=c_nationkey ne 5&$top=40', None, 8, None, None),
    ('testdata.tpch.customer', '$filter=c_nationkey eq 3 or c_nationkey eq 7', None, 8, None, None),
    ('testdata.tpch.customer', '$orderby=c_nationkey,c_name&$top=12', 12, 8, None, None),
    ('testdata.tpch.customer', '$skip=25&$top=10&$select=c_name,c_nationkey', 10, 2, None, None),
    ('testdata.tpch.customer', '$filter=c_custkey le 50&$top=55', None, 8, None, None),
    ('testdata.tpch.customer', '$orderby=c_custkey desc&$top=8', 8, 8, None, None),
    ('testdata.tpch.part', '$filter=p_size ge 10 and p_size lt 30&$top=40', None, 9, None, None),
    ('testdata.tpch.part', '$filter=p_partkey ge 50 and p_partkey lt 100', None, 9, None, None),
    ('testdata.tpch.part', '$filter=p_size eq 15 or p_size eq 25', None, 9, None, None),
    ('testdata.tpch.part', '$orderby=p_size,p_name&$top=15', 15, 9, None, None),
    ('testdata.tpch.part', '$skip=30&$top=12&$select=p_name,p_type,p_size', 12, 3, None, None),
    ('testdata.tpch.part', '$filter=p_partkey le 80&$top=90', None, 9, None, None),
    ('testdata.tpch.part', '$orderby=p_partkey desc&$top=20', 20, 9, None, None),
    ('testdata.tpch.nation', "$filter=startswith(n_name,'A')&$top=30", None, 4, None, None),
    ('testdata.tpch.nation', "$filter=startswith(n_name,'C')&$top=30", None, 4, None, None),
    ('testdata.tpch.nation', "$filter=startswith(n_name,'U')&$top=30", None, 4, None, None),
    ('testdata.tpch.nation', "$filter=endswith(n_name,'A')&$top=30", None, 4, None, None),
    ('testdata.tpch.nation', "$filter=endswith(n_name,'Y')&$top=30", None, 4, None, None),
    ('testdata.tpch.nation', "$filter=endswith(n_name,'IA')&$top=30", None, 4, None, None),
    ('testdata.tpch.nation', "$filter=contains(n_name,'AN')&$top=30", None, 4, None, None),
    ('testdata.tpch.nation', "$filter=contains(n_name,'IN')&$top=30", None, 4, None, None),
    ('testdata.tpch.nation', "$filter=contains(n_name,'IA')&$top=30", None, 4, None, None),
    ('testdata.tpch.nation', "$filter=startswith(n_name,'A') and n_regionkey eq 1", None, 4, None, None),
    ('testdata.tpch.nation', "$filter=endswith(n_name,'A') or endswith(n_name,'Y')", None, 4, None, None),
    ('testdata.tpch.nation', "$filter=contains(n_name,'A') and n_nationkey lt 10", None, 4, None, None),
    ('testdata.tpch.nation', '$filter=tolower(n_name) eq tolower(n_name)', 24, 4, None, None),
    ('testdata.tpch.nation', "$filter=startswith(n_name,'I') or startswith(n_name,'J')", None, 4, None, None),
    ('testdata.tpch.nation', "$filter=contains(n_name,'UNITED')&$top=10", None, 4, None, None),
    ('testdata.tpch.region', "$filter=startswith(r_name,'A')&$top=10", None, 3, None, None),
    ('testdata.tpch.region', "$filter=startswith(r_name,'E')&$top=10", None, 3, None, None),
    ('testdata.tpch.region', "$filter=startswith(r_name,'M')&$top=10", None, 3, None, None),
    ('testdata.tpch.region', "$filter=endswith(r_name,'A')&$top=10", None, 3, None, None),
    ('testdata.tpch.region', "$filter=endswith(r_name,'E')&$top=10", None, 3, None, None),
    ('testdata.tpch.region', "$filter=contains(r_name,'RI')&$top=10", None, 3, None, None),
    ('testdata.tpch.region', "$filter=contains(r_name,'AS')&$top=10", None, 3, None, None),
    ('testdata.tpch.region', '$filter=toupper(r_name) eq toupper(r_name)', 4, 3, None, None),
    ('testdata.tpch.region', "$filter=startswith(r_name,'A') or startswith(r_name,'E')", None, 3, None, None),
    ('testdata.tpch.region', "$filter=contains(r_name,'EAST') or contains(r_name,'WEST')", None, 3, None, None),
    ('testdata.tpch.supplier', "$filter=startswith(s_name,'Supplier')&$top=50", None, 7, None, None),
    ('testdata.tpch.supplier', "$filter=contains(s_name,'000')&$top=30", None, 7, None, None),
    ('testdata.tpch.supplier', "$filter=contains(s_name,'001')&$top=30", None, 7, None, None),
    ('testdata.tpch.supplier', "$filter=contains(s_name,'#')&$top=50", None, 7, None, None),
    ('testdata.tpch.supplier', "$filter=endswith(s_name,'1')&$top=40", None, 7, None, None),
    ('testdata.tpch.supplier', "$filter=endswith(s_name,'5')&$top=40", None, 7, None, None),
    ('testdata.tpch.supplier', "$filter=startswith(s_phone,'1')&$top=50", None, 7, None, None),
    ('testdata.tpch.supplier', "$filter=startswith(s_phone,'2')&$top=50", None, 7, None, None),
    ('testdata.tpch.supplier', "$filter=contains(s_phone,'-')&$top=100", None, 7, None, None),
    ('testdata.tpch.supplier', "$filter=startswith(s_name,'Supplier') and s_nationkey eq 5", None, 7, None, None),
    ('testdata.tpch.supplier', "$filter=contains(s_address,'St')&$top=50", None, 7, None, None),
    ('testdata.tpch.supplier', '$filter=toupper(s_name) eq toupper(s_name)&$top=20', 20, 7, None, None),
    ('testdata.tpch.customer', "$filter=startswith(c_name,'Customer')&$top=50", None, 8, None, None),
    ('testdata.tpch.customer', "$filter=contains(c_name,'000')&$top=30", None, 8, None, None),
    ('testdata.tpch.customer', "$filter=contains(c_name,'#')&$top=100", None, 8, None, None),
    ('testdata.tpch.customer', "$filter=endswith(c_name,'1')&$top=50", None, 8, None, None),
    ('testdata.tpch.customer', "$filter=endswith(c_name,'9')&$top=50", None, 8, None, None),
    ('testdata.tpch.customer', "$filter=startswith(c_phone,'1')&$top=60", None, 8, None, None),
    ('testdata.tpch.customer', "$filter=startswith(c_phone,'3')&$top=60", None, 8, None, None),
    ('testdata.tpch.customer', "$filter=contains(c_phone,'-')&$top=100", None, 8, None, None),
    ('testdata.tpch.customer', "$filter=startswith(c_name,'Customer') and c_nationkey eq 10", None, 8, None, None),
    ('testdata.tpch.customer', "$filter=contains(c_address,'Ave')&$top=60", None, 8, None, None),
    ('testdata.tpch.customer', '$filter=tolower(c_name) eq tolower(c_name)&$top=30', 30, 8, None, None),
    ('testdata.tpch.customer', "$filter=startswith(c_name,'Customer#0000')&$top=100", None, 8, None, None),
    ('testdata.tpch.part', "$filter=contains(p_name,'red')&$top=50", None, 9, None, None),
    ('testdata.tpch.part', "$filter=contains(p_name,'blue')&$top=50", None, 9, None, None),
    ('testdata.tpch.part', "$filter=contains(p_name,'green')&$top=50", None, 9, None, None),
    ('testdata.tpch.part', "$filter=contains(p_name,'steel')&$top=50", None, 9, None, None),
    ('testdata.tpch.part', "$filter=contains(p_name,'forest')&$top=50", None, 9, None, None),
    ('testdata.tpch.part', "$filter=startswith(p_type,'STANDARD')&$top=60", None, 9, None, None),
    ('testdata.tpch.part', "$filter=startswith(p_type,'PROMO')&$top=60", None, 9, None, None),
    ('testdata.tpch.part', "$filter=startswith(p_type,'SMALL')&$top=60", None, 9, None, None),
    ('testdata.tpch.part', "$filter=endswith(p_type,'STEEL')&$top=60", None, 9, None, None),
    ('testdata.tpch.part', "$filter=endswith(p_type,'BRASS')&$top=60", None, 9, None, None),
    ('testdata.tpch.part', "$filter=contains(p_type,'COPPER')&$top=60", None, 9, None, None),
    ('testdata.tpch.part', "$filter=contains(p_name,'red') and p_size lt 20", None, 9, None, None),
    ('testdata.tpch.part', "$filter=startswith(p_type,'MEDIUM') and p_size gt 10", None, 9, None, None),
    ('testdata.tpch.part', '$filter=tolower(p_brand) eq tolower(p_brand)&$top=25', 25, 9, None, None),
    ('testdata.tpch.part', "$filter=contains(p_container,'BOX')&$top=70", None, 9, None, None),
    ('testdata.tpch.nation', '$apply=groupby((n_regionkey),aggregate($count as NationCount))&$orderby=NationCount desc&$top=1', 1, 2, None, None),
    ('testdata.tpch.supplier', '$apply=groupby((s_nationkey),aggregate($count as SupplierCount))&$orderby=SupplierCount desc&$top=1', 1, 2, None, None),
    ('testdata.tpch.customer', '$apply=groupby((c_nationkey),aggregate($count as CustomerCount))&$orderby=CustomerCount desc&$top=1', 1, 2, None, None),
    ('testdata.tpch.nation', '$count=true&$top=0&$apply=groupby((n_regionkey),aggregate($count as NationCount))&$orderby=NationCount desc&$top=1', 0, 2, 5, None),
    ('testdata.tpch.supplier', '$apply=groupby((s_nationkey),aggregate(s_suppkey with count as SupplierCount))', None, 7, None, 'Exception'),
    ('testdata.tpch.nation', '$apply=groupby((n_regionkey),aggregate($count as total))&$orderby=total desc', 5, 2, None, None),
    ('testdata.tpch.part', '$apply=groupby((p_size),aggregate($count as PartCount))&$orderby=PartCount desc&$top=5', 5, 2, None, None),
    ('testdata.tpch.customer', '$apply=groupby((c_nationkey),aggregate($count as cnt))&$filter=cnt gt 10', 25, 2, None, None),
    ('$planets', "$filter=tolower(name) eq 'earth'", 1, 20, None, None),
    ('$planets', "$filter=toupper(name) eq 'MARS'", 1, 20, None, None),
    ('$planets', "$filter=startswith(tolower(name),'m')", 2, 20, None, None),
    ('$planets', "$filter=startswith(toupper(name),'J')", 1, 20, None, None),
    ('$planets', "$filter=endswith(tolower(name),'s')", 3, 20, None, None),
    ('$planets', "$filter=contains(tolower(name),'ar')", 2, 20, None, None),
    ('$planets', "$filter=contains(toupper(name),'TURN')", 1, 20, None, None),
    ('$planets', "$filter=startswith(tolower(name),'e') and mass gt 0.5", 1, 20, None, None),
    ('$planets', "$filter=contains(tolower(name),'u') or contains(tolower(name),'e')", 8, 20, None, None),
    ('$planets', "$filter=endswith(name,'s') and startswith(name,'M')", 1, 20, None, None),
    ('testdata.tpch.nation', "$filter=startswith(tolower(n_name),'a')&$top=20", None, 4, None, None),
    ('testdata.tpch.nation', "$filter=endswith(tolower(n_name),'ia')&$top=20", None, 4, None, None),
    ('testdata.tpch.nation', "$filter=contains(toupper(n_name),'UNITED')&$top=20", None, 4, None, None),
    ('testdata.tpch.region', "$filter=startswith(toupper(r_name),'M')&$top=10", None, 3, None, None),
    ('testdata.tpch.region', "$filter=contains(tolower(r_name),'east')&$top=10", None, 3, None, None),
    ('testdata.tpch.supplier', "$filter=startswith(tolower(s_name),'supplier')&$top=30", None, 7, None, None),
    ('testdata.tpch.customer', "$filter=startswith(tolower(c_name),'customer')&$top=30", None, 8, None, None),
    ('testdata.tpch.part', "$filter=contains(tolower(p_name),'steel')&$top=40", None, 9, None, None),
    ('testdata.tpch.part', "$filter=startswith(tolower(p_type),'medium')&$top=40", None, 9, None, None),
    ('testdata.tpch.nation', "$filter=startswith(tolower(n_name),'c') and n_regionkey eq 0", None, 4, None, None),
    ('testdata.tpch.nation', "$filter=contains(tolower(n_name),'in') or contains(tolower(n_name),'an')", None, 4, None, None),
    ('$planets', "$filter=tolower(name) eq tolower('EARTH')", 1, 20, None, None),
    ('$planets', "$filter=toupper(name) eq toupper('mars')", 1, 20, None, None),
    ('testdata.tpch.nation', "$filter=endswith(tolower(n_name),'a') and n_nationkey lt 15", None, 4, None, None),
    ('testdata.tpch.part', "$filter=contains(toupper(p_name),'RED') and p_size lt 30", None, 9, None, None),
    ('testdata.tpch.supplier', "$filter=contains(tolower(s_name),'000') and s_suppkey lt 100", None, 7, None, None),
    ('testdata.tpch.customer', "$filter=endswith(tolower(c_name),'5') or endswith(tolower(c_name),'0')", None, 8, None, None),
    ('testdata.tpch.region', "$filter=toupper(r_name) eq 'AFRICA' or toupper(r_name) eq 'ASIA'", None, 3, None, None),
    ('testdata.tpch.nation', "$filter=startswith(n_name,'A') or startswith(tolower(n_name),'u')", None, 4, None, None),
    ('testdata.tpch.part', "$filter=startswith(tolower(p_type),'promo') or startswith(tolower(p_type),'standard')", None, 9, None, None),
    ('$planets', '$filter=id add 1 eq 2', 1, 20, None, None),
    ('$planets', '$filter=id add 5 lt 10', 4, 20, None, None),
    ('$planets', '$filter=number_of_moons sub 1 eq 0', 1, 20, None, None),
    ('$planets', '$filter=number_of_moons sub 5 gt 10', 3, 20, None, None),
    ('$planets', '$filter=id mul 2 lt 10', 4, 20, None, None),
    ('$planets', '$filter=id mul 3 eq 9', 1, 20, None, None),
    ('$planets', '$filter=number_of_moons div 10 gt 5', 2, 20, None, None),
    ('$planets', '$filter=id mod 2 eq 0', 4, 20, None, None),
    ('$planets', '$filter=id mod 2 eq 1', 5, 20, None, None),
    ('$planets', '$filter=id mod 3 eq 0', 3, 20, None, None),
    ('testdata.tpch.nation', '$filter=n_nationkey add 10 lt 15&$top=20', None, 4, None, None),
    ('testdata.tpch.nation', '$filter=n_nationkey mul 2 lt 30&$top=20', None, 4, None, None),
    ('testdata.tpch.nation', '$filter=n_regionkey add 1 eq 2&$top=10', None, 4, None, None),
    ('testdata.tpch.supplier', '$filter=s_suppkey mod 10 eq 0&$top=30', None, 7, None, None),
    ('testdata.tpch.supplier', '$filter=s_suppkey div 100 lt 5&$top=50', None, 7, None, None),
    ('testdata.tpch.customer', '$filter=c_custkey mod 100 eq 0&$top=30', None, 8, None, None),
    ('testdata.tpch.part', '$filter=p_size mul 2 gt 50&$top=50', None, 9, None, None),
    ('testdata.tpch.part', '$filter=p_size add 10 lt 30&$top=60', None, 9, None, None),
    ('testdata.tpch.part', '$filter=p_partkey mod 1000 eq 0&$top=20', None, 9, None, None),
    ('$planets', '$filter=id add 1 gt 5 and id sub 1 lt 8', 4, 20, None, None),
    ('$planets', '$filter=ceiling(mass) gt 1000', 1, 20, None, None),
    ('$planets', '$filter=floor(mass) lt 1', 3, 20, None, None),
    ('$planets', '$filter=round(mass) eq 1', 1, 20, None, None),
    ('$planets', '$filter=ceiling(mass) eq 1', 3, 20, None, None),
    ('$planets', '$filter=floor(mass) eq 0', 3, 20, None, None),
    ('$planets', '$filter=round(mass) gt 100', 3, 20, None, None),
    ('$planets', '$filter=ceiling(number_of_moons) gt 50', 2, 20, None, None),
    ('$planets', '$filter=floor(number_of_moons) lt 5', 4, 20, None, None),
    ('$planets', '$filter=round(mass) eq round(mass)', 9, 20, None, None),
    ('testdata.tpch.nation', '$filter=ceiling(n_nationkey) gt 20&$top=20', None, 4, None, None),
    ('testdata.tpch.nation', '$filter=floor(n_nationkey) lt 5&$top=20', None, 4, None, None),
    ('testdata.tpch.supplier', '$filter=ceiling(s_suppkey) lt 100&$top=40', None, 7, None, None),
    ('testdata.tpch.customer', '$filter=floor(c_custkey) lt 100&$top=50', None, 8, None, None),
    ('testdata.tpch.part', '$filter=ceiling(p_size) gt 40&$top=40', None, 9, None, None),
    ('testdata.tpch.part', '$filter=floor(p_size) lt 10&$top=60', None, 9, None, None),
    ('testdata.tpch.part', '$filter=round(p_size) eq 25&$top=30', None, 9, None, None),
    ('$planets', '$filter=ceiling(mass) gt 1 and floor(number_of_moons) gt 0', 5, 20, None, None),
    ('testdata.tpch.nation', '$filter=round(n_nationkey) mod 5 eq 0&$top=20', 4, 4, None, None),
    ('$planets', '$filter=length(name) gt 5', 5, 20, None, None),
    ('$planets', '$filter=length(name) eq 4', 1, 20, None, None),
    ('$planets', '$filter=length(name) lt 6', 4, 20, None, None),
    ('$planets', '$filter=length(name) ge 7', 3, 20, None, None),
    ('$planets', '$filter=length(name) le 5', 4, 20, None, None),
    ('$planets', "$filter=indexof(name,'a') gt 0", 4, 20, None, None),
    ('$planets', "$filter=indexof(name,'e') ge 0", 4, 20, None, None),
    ('$planets', "$filter=indexof(name,'u') ge 0", 7, 20, None, None),
    ('$planets', "$filter=indexof(name,'z') eq -1", 9, 20, None, None),
    ('$planets', "$filter=indexof(name,'M') eq 0", 2, 20, None, None),
    ('$planets', "$filter=substring(name,0,3) eq 'Mar'", 1, 20, None, None),
    ('$planets', "$filter=substring(name,0,3) eq 'Ear'", 1, 20, None, None),
    ('$planets', "$filter=substring(name,0,1) eq 'M'", 2, 20, None, None),
    ('$planets', "$filter=substring(name,0,2) eq 'Ju'", 1, 20, None, None),
    ('$planets', "$filter=substring(name,1,3) eq 'erc'", 1, 20, None, None),
    ('testdata.tpch.nation', '$filter=length(n_name) gt 10&$top=20', None, 4, None, None),
    ('testdata.tpch.nation', '$filter=length(n_name) le 7&$top=20', None, 4, None, None),
    ('testdata.tpch.nation', "$filter=indexof(n_name,'A') ge 0&$top=20", None, 4, None, None),
    ('testdata.tpch.nation', "$filter=indexof(n_name,'UNITED') ge 0&$top=10", None, 4, None, None),
    ('testdata.tpch.nation', "$filter=substring(n_name,0,1) eq 'A'&$top=20", None, 4, None, None),
    ('testdata.tpch.region', '$filter=length(r_name) gt 6&$top=10', None, 3, None, None),
    ('testdata.tpch.region', "$filter=indexof(r_name,'EAST') ge 0&$top=10", None, 3, None, None),
    ('testdata.tpch.supplier', '$filter=length(s_name) eq 18&$top=40', None, 7, None, None),
    ('testdata.tpch.supplier', "$filter=indexof(s_name,'#') ge 0&$top=50", None, 7, None, None),
    ('testdata.tpch.supplier', "$filter=substring(s_name,0,8) eq 'Supplier'&$top=50", None, 7, None, None),
    ('testdata.tpch.customer', '$filter=length(c_name) eq 18&$top=40', None, 8, None, None),
    ('testdata.tpch.customer', "$filter=indexof(c_name,'#') ge 0&$top=60", None, 8, None, None),
    ('testdata.tpch.customer', "$filter=substring(c_name,0,8) eq 'Customer'&$top=60", None, 8, None, None),
    ('testdata.tpch.part', '$filter=length(p_name) gt 20&$top=50', None, 9, None, None),
    ('testdata.tpch.part', "$filter=indexof(p_name,'steel') ge 0&$top=40", None, 9, None, None),
    ('testdata.tpch.part', "$filter=substring(p_type,0,7) eq 'PROMO '&$top=40", None, 9, None, None),
    ('$planets', "$filter=length(name) gt 4 and indexof(name,'a') ge 0", 3, 20, None, None),
    ('testdata.tpch.nation', "$filter=length(n_name) gt 5 and indexof(n_name,'A') ge 0&$top=20", None, 4, None, None),
    ('testdata.tpch.part', "$filter=length(p_name) lt 30 and indexof(p_name,'red') ge 0&$top=40", None, 9, None, None),
    ('$planets', "$filter=substring(name,0,1) eq 'M' and length(name) gt 4", 1, 20, None, None),
    ('$planets', '$filter=id in (1,3,5)', 3, 20, None, None),
    ('$planets', '$filter=id in (2,4,6)', 3, 20, None, None),
    ('$planets', '$filter=id in (1)', 1, 20, None, None),
    ('$planets', '$filter=id in (9)', 1, 20, None, None),
    ('$planets', '$filter=number_of_moons in (0,1,2)', 4, 20, None, None),
    ('$planets', '$filter=number_of_moons in (79,82)', 2, 20, None, None),
    ('$planets', "$filter=name in ('Earth','Mars','Venus')", 3, 20, None, None),
    ('$planets', "$filter=name in ('Jupiter','Saturn','Uranus','Neptune')", 4, 20, None, None),
    ('$planets', "$filter=name in ('Mercury')", 1, 20, None, None),
    ('$planets', '$filter=not (id in (1,2,3))', 6, 20, None, None),
    ('$planets', '$filter=id in (1,2,3) and mass gt 0.5', 2, 20, None, None),
    ('$planets', '$filter=id in (1,2,3,4,5,6,7,8,9)', 9, 20, None, None),
    ('testdata.tpch.nation', '$filter=n_regionkey in (0,1)&$top=30', None, 4, None, None),
    ('testdata.tpch.nation', '$filter=n_nationkey in (0,5,10,15,20)&$top=30', None, 4, None, None),
    ('testdata.tpch.region', '$filter=r_regionkey in (1,3)&$top=10', None, 3, None, None),
    ('testdata.tpch.supplier', '$filter=s_suppkey in (1,2,3,4,5)&$top=30', None, 7, None, None),
    ('testdata.tpch.customer', '$filter=c_custkey in (1,10,100,1000)&$top=30', None, 8, None, None),
    ('testdata.tpch.part', '$filter=p_size in (1,5,10,15,20)&$top=50', None, 9, None, None),
    ('$planets', '$filter=id in (2,4,6,8) and number_of_moons gt 10', 2, 20, None, None),
    ('testdata.tpch.nation', '$filter=n_nationkey in (1,2,3) or n_regionkey in (0)&$top=30', None, 4, None, None),
    ('$planets', '$filter=(id eq 1 or id eq 2) and mass lt 1', 1, 20, None, None),
    ('$planets', '$filter=(id gt 5 and number_of_moons gt 20) or id eq 1', 3, 20, None, None),
    ('$planets', '$filter=((id eq 1 or id eq 2) and mass lt 1) or id eq 3', 2, 20, None, None),
    ('$planets', '$filter=(id eq 3 and mass gt 0) and (number_of_moons eq 1 or number_of_moons eq 0)', 1, 20, None, None),
    ('$planets', '$filter=not(id gt 5)', 5, 20, None, None),
    ('$planets', '$filter=not(not(id gt 5))', 4, 20, None, None),
    ('$planets', '$filter=not(id eq 1 or id eq 2)', 7, 20, None, None),
    ('$planets', '$filter=not(id gt 3 and mass lt 10)', 7, 20, None, None),
    ('$planets', '$filter=(id eq 1 and mass lt 1) or (id eq 5 and mass gt 10)', 2, 20, None, None),
    ('$planets', '$filter=((id lt 3) or (id gt 7)) and number_of_moons lt 10', 3, 20, None, None),
    ('testdata.tpch.nation', '$filter=(n_regionkey eq 0 or n_regionkey eq 1) and n_nationkey lt 10&$top=30', None, 4, None, None),
    ('testdata.tpch.nation', '$filter=not(n_regionkey eq 2)&$top=30', None, 4, None, None),
    ('testdata.tpch.nation', '$filter=(n_nationkey gt 10 and n_regionkey eq 2) or n_nationkey lt 3&$top=30', None, 4, None, None),
    ('testdata.tpch.supplier', '$filter=(s_nationkey lt 5 or s_nationkey gt 20) and s_suppkey lt 100&$top=50', None, 7, None, None),
    ('testdata.tpch.customer', '$filter=not(c_nationkey eq 10 or c_nationkey eq 15)&$top=50', None, 8, None, None),
    ('testdata.tpch.part', '$filter=(p_size lt 10 or p_size gt 40) and p_partkey lt 1000&$top=50', None, 9, None, None),
    ('$planets', '$filter=(id eq 1 or id eq 3 or id eq 5) and (mass lt 10 or number_of_moons gt 50)', 3, 20, None, None),
    ('$planets', '$filter=not((id lt 3) and (mass gt 0.5))', 8, 20, None, None),
    ('testdata.tpch.nation', '$filter=((n_regionkey eq 0 and n_nationkey lt 10) or (n_regionkey eq 1 and n_nationkey gt 10))&$top=30', None, 4, None, None),
    ('testdata.tpch.part', '$filter=((p_size gt 20 and p_size lt 30) or p_size eq 10) and p_partkey lt 500&$top=50', None, 9, None, None),
    ('$planets', '$filter=(id in (1,2,3)) and (number_of_moons lt 5 or mass gt 1000)', 3, 20, None, None),
    ('testdata.tpch.supplier', '$filter=not(s_nationkey in (0,1,2,3,4))&$top=50', None, 7, None, None),
    ('testdata.tpch.nation', '$filter=(n_nationkey mod 2 eq 0) and (n_regionkey eq 1 or n_regionkey eq 3)&$top=30', None, 4, None, None),
    ('$planets', '$filter=((id lt 5 and mass lt 1) or (id gt 5 and mass gt 100)) and number_of_moons ne 0', 3, 20, None, None),
    ('testdata.tpch.part', '$filter=(p_size ge 10 and p_size le 20) or (p_size ge 30 and p_size le 40)&$top=60', None, 9, None, None),
]
# fmt:on

EXTENDED_TEST_BATTERY = PLANETS_TEST_BATTERY


# All test tuples are explicit 6-tuples now.
@pytest.mark.parametrize(
    "test_case",
    PLANETS_TEST_BATTERY,
)
def test_planets_battery(test_case):
    """Parametrized pytest test which runs each battery entry as a separate test.

    Each test case MUST be a 6-tuple:
      (dataset, query_string, expected_rows, expected_columns, expected_count, expected_error)
    """
    if len(test_case) != 6:
        raise ValueError(f"Invalid test tuple length: {len(test_case)}")
    dataset, query_string, expected_rows, expected_columns, expected_count, expected_error = (
        test_case
    )

    res = run_test_case(
        dataset,
        query_string,
        expected_rows,
        expected_columns,
        expected_error,
        expected_count=expected_count,
    )
    assert res["passed"], (
        f"{dataset} {query_string}: {res.get('reason') or res.get('error') or res}"
    )


# Count tests are included directly in PLANETS_TEST_BATTERY so they run with the main battery
# (We validate rows/@odata.count via the common run_test_case interface.)

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

    summary = run_battery(PLANETS_TEST_BATTERY)

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
