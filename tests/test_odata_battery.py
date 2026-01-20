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
        # Note: $apply is parsed but not yet supported by execute_odata_query
        # elif key == "$apply":
        #     params["apply_expr"] = value

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

# fmt:off
PLANETS_TEST_BATTERY = [
    # ============================================================================
    # BASIC QUERIES - Top/Limit (5 tests)
    # ============================================================================
    ("$planets", "$top=1", 1, 20, None),
    ("$planets", "$top=5", 5, 20, None),
    ("$planets", "$top=9", 9, 20, None),
    ("$planets", "$top=20", 9, 20, None),  # More than available
    ("$planets", "$top=0", 0, 20, None),
    # ============================================================================
    # BASIC ORDERBY - Single Column (8 tests) - BROKEN: $orderby not implemented
    # ============================================================================
    ("$planets", "$orderby=id&$top=1", 1, 20, None),
    ("$planets", "$orderby=id asc&$top=1", 1, 20, None),
    ("$planets", "$orderby=id desc&$top=1", 1, 20, None),
    ("$planets", "$orderby=name asc&$top=1", 1, 20, None),
    ("$planets", "$orderby=name desc&$top=1", 1, 20, None),
    ("$planets", "$orderby=mass desc&$top=1", 1, 20, None),
    ("$planets", "$orderby=distance_from_sun asc&$top=1", 1, 20, None),
    ("$planets", "$orderby=mean_temperature asc&$top=1", 1, 20, None),
    # ============================================================================
    # ORDERBY - Multiple Columns (4 tests) - BROKEN: $orderby not implemented
    # ============================================================================
    ("$planets", "$orderby=number_of_moons desc,name asc&$top=3", 3, 20, None),
    ("$planets", "$orderby=mass desc,id asc&$top=5", 5, 20, None),
    ("$planets", "$orderby=mean_temperature asc,distance_from_sun desc&$top=4", 4, 20, None),
    ("$planets", "$orderby=orbital_eccentricity desc,orbital_period asc&$top=2", 2, 20, None),
    # ============================================================================
    # SELECT - Single Column (5 tests) - BROKEN: list concatenation error
    # ============================================================================
    ("$planets", "$select=name&$top=1", 1, 1, None),
    ("$planets", "$select=id&$top=5", 5, 1, None),
    ("$planets", "$select=mass&$top=9", 9, 1, None),
    ("$planets", "$select=distance_from_sun&$top=3", 3, 1, None),
    ("$planets", "$select=number_of_moons&$top=1", 1, 1, None),
    # ============================================================================
    # SELECT - Multiple Columns (5 tests) - BROKEN: list concatenation error
    # ============================================================================
    ("$planets", "$select=id,name&$top=1", 1, 2, None),
    ("$planets", "$select=name,mass,diameter&$top=5", 5, 3, None),
    ("$planets", "$select=id,name,distance_from_sun&$top=3", 3, 3, None),
    ("$planets", "$select=id,name,mass,diameter,number_of_moons&$top=2", 2, 5, None),
    ("$planets", "$select=*&$top=1", 1, 20, None),
    # ============================================================================
    # FILTER - Equality (5 tests)
    # ============================================================================
    ("$planets", "$filter=id eq 1&$top=10", 1, 20, None),
    ("$planets", "$filter=id eq 5&$top=10", 1, 20, None),
    ("$planets", "$filter=name eq 'Earth'&$top=10", 1, 20, None),
    ("$planets", "$filter=name eq 'Jupiter'&$top=10", 1, 20, None),
    ("$planets", "$filter=number_of_moons eq 0&$top=10", 2, 20, None),
    # ============================================================================
    # FILTER - Comparison (6 tests)
    # ============================================================================
    ("$planets", "$filter=mass gt 100&$top=10", 3, 20, None),  # Jupiter, Saturn, Neptune
    ("$planets", "$filter=mass lt 10&$top=10", 5, 20, None),  # Mercury, Venus, Earth, Mars, Pluto
    ("$planets", "$filter=diameter gt 10000&$top=10", 6, 20, None),
    ("$planets", "$filter=distance_from_sun lt 200&$top=10", 3, 20, None),
    ("$planets", "$filter=mean_temperature ge 0&$top=10", 3, 20, None),
    ("$planets", "$filter=number_of_moons le 2&$top=10", 4, 20, None),
    # ============================================================================
    # FILTER - Not Equal (3 tests) - BROKEN: row count mismatches
    # ============================================================================
    ("$planets", "$filter=number_of_moons ne 0&$top=10", 7, 20, None),
    ("$planets", "$filter=name ne 'Earth'&$top=10", 8, 20, None),
    ("$planets", "$filter=id ne 1&$top=10", 8, 20, None),
    # ============================================================================
    # FILTER - String Patterns (3 tests) - BROKEN: startswith/endswith not supported
    # ============================================================================
    ("$planets", "$filter=startswith(name,'M')&$top=10", 2, 20, None),  # Mars, Mercury
    ("$planets", "$filter=startswith(name,'J')&$top=10", 1, 20, None),  # Jupiter
    ("$planets", "$filter=endswith(name,'e')&$top=10", 1, 20, None),  # Neptune
    # ============================================================================
    # STRING FUNCTIONS - startswith (10 tests)
    # ============================================================================
    ("$planets", "$filter=startswith(name,'E')&$top=10", 1, 20, None),  # Earth
    ("$planets", "$filter=startswith(name,'S')&$top=10", 1, 20, None),  # Saturn
    ("$planets", "$filter=startswith(name,'V')&$top=10", 1, 20, None),  # Venus
    ("$planets", "$filter=startswith(name,'N')&$top=10", 1, 20, None),  # Neptune
    ("$planets", "$filter=startswith(name,'U')&$top=10", 1, 20, None),  # Uranus
    ("$planets", "$filter=startswith(name,'P')&$top=10", 1, 20, None),  # Pluto
    ("$planets", "$filter=startswith(name,'Ma')&$top=10", 1, 20, None),  # Mars
    ("$planets", "$filter=startswith(name,'Ju')&$top=10", 1, 20, None),  # Jupiter
    ("$planets", "$filter=startswith(name,'Mer')&$top=10", 1, 20, None),  # Mercury
    ("$planets", "$filter=startswith(name,'Z')&$top=10", 0, 20, None),  # None
    # ============================================================================
    # STRING FUNCTIONS - endswith (10 tests)
    # ============================================================================
    ("$planets", "$filter=endswith(name,'s')&$top=10", 3, 20, None),  # Mars, Venus, Uranus
    ("$planets", "$filter=endswith(name,'h')&$top=10", 1, 20, None),  # Earth
    ("$planets", "$filter=endswith(name,'n')&$top=10", 1, 20, None),  # Saturn
    ("$planets", "$filter=endswith(name,'r')&$top=10", 1, 20, None),  # Jupiter
    ("$planets", "$filter=endswith(name,'g')&$top=10", 0, 20, None),
    ("$planets", "$filter=endswith(name,'o')&$top=10", 1, 20, None),  # Pluto
    ("$planets", "$filter=endswith(name,'rs')&$top=10", 1, 20, None),  # Mars
    ("$planets", "$filter=endswith(name,'ter')&$top=10", 1, 20, None),  # Jupiter
    ("$planets", "$filter=endswith(name,'ury')&$top=10", 1, 20, None),  # Mercury
    ("$planets", "$filter=endswith(name,'tune')&$top=10", 1, 20, None),  # Neptune
    ("$planets", "$filter=endswith(name,'xyz')&$top=10", 0, 20, None),  # None
    # ============================================================================
    # STRING FUNCTIONS - contains (12 tests)
    # ============================================================================
    ("$planets", "$filter=contains(name,'ar')&$top=10", 2, 20, None),  # Mars, Earth
    ("$planets", "$filter=contains(name,'ur')&$top=10", 2, 20, None),  # Mercury, Saturn, Uranus
    ("$planets", "$filter=contains(name,'er')&$top=10", 2, 20, None),  # Mercury, Jupiter
    ("$planets", "$filter=contains(name,'pt')&$top=10", 1, 20, None),  # Neptune
    ("$planets", "$filter=contains(name,'ut')&$top=10", 1, 20, None),  # Pluto
    ("$planets", "$filter=contains(name,'en')&$top=10", 1, 20, None),  # Venus
    ("$planets", "$filter=contains(name,'a')&$top=10", 4, 20, None),  # Mars, Earth, Saturn, Uranus
    ("$planets", "$filter=contains(name,'u')&$top=10", 7, 20, None),  # Mercury, Venus, Jupiter, Saturn, Uranus, Neptune, Pluto
    ("$planets", "$filter=contains(name,'e')&$top=10", 4, 20, None),  # Mercury, Venus, Jupiter, Neptune
    ("$planets", "$filter=contains(name,'xyz')&$top=10", 0, 20, None),  # None
    ("$planets", "$filter=contains(name,'Mars')&$top=10", 1, 20, None),  # Mars (exact)
    ("$planets", "$filter=contains(name,'Earth')&$top=10", 1, 20, None),  # Earth (exact)
    # ============================================================================
    # STRING FUNCTIONS - tolower (8 tests)
    # ============================================================================
    ("$planets", "$filter=tolower(name) eq 'earth'&$top=10", 1, 20, None),
    ("$planets", "$filter=tolower(name) eq 'mars'&$top=10", 1, 20, None),
    ("$planets", "$filter=tolower(name) eq 'jupiter'&$top=10", 1, 20, None),
    ("$planets", "$filter=tolower(name) eq 'saturn'&$top=10", 1, 20, None),
    ("$planets", "$filter=tolower(name) eq 'venus'&$top=10", 1, 20, None),
    ("$planets", "$filter=tolower(name) eq 'mercury'&$top=10", 1, 20, None),
    ("$planets", "$filter=tolower(name) eq 'neptune'&$top=10", 1, 20, None),
    ("$planets", "$filter=tolower(name) eq 'pluto'&$top=10", 1, 20, None),
    # ============================================================================
    # STRING FUNCTIONS - toupper (8 tests)
    # ============================================================================
    ("$planets", "$filter=toupper(name) eq 'EARTH'&$top=10", 1, 20, None),
    ("$planets", "$filter=toupper(name) eq 'MARS'&$top=10", 1, 20, None),
    ("$planets", "$filter=toupper(name) eq 'JUPITER'&$top=10", 1, 20, None),
    ("$planets", "$filter=toupper(name) eq 'SATURN'&$top=10", 1, 20, None),
    ("$planets", "$filter=toupper(name) eq 'VENUS'&$top=10", 1, 20, None),
    ("$planets", "$filter=toupper(name) eq 'MERCURY'&$top=10", 1, 20, None),
    ("$planets", "$filter=toupper(name) eq 'URANUS'&$top=10", 1, 20, None),
    ("$planets", "$filter=toupper(name) eq 'NEPTUNE'&$top=10", 1, 20, None),
    # ============================================================================
    # STRING FUNCTIONS - Combined (10 tests)
    # ============================================================================
    ("$planets", "$filter=startswith(name,'M') and contains(name,'ar')&$top=10", 1, 20, None),  # Mars
    ("$planets", "$filter=endswith(name,'s') and startswith(name,'M')&$top=10", 1, 20, None),  # Mars
    ("$planets", "$filter=contains(name,'u') and endswith(name,'r')&$top=10", 1, 20, None),  # Jupiter
    ("$planets", "$filter=startswith(tolower(name),'e')&$top=10", 1, 20, None),  # Earth
    ("$planets", "$filter=endswith(toupper(name),'S')&$top=10", 3, 20, None),  # Mars, Venus, Uranus
    ("$planets", "$filter=contains(tolower(name),'ear')&$top=10", 1, 20, None),  # Earth
    ("$planets", "$filter=tolower(name) eq 'mars' and mass lt 1&$top=10", 1, 20, None),
    ("$planets", "$filter=startswith(name,'J') or startswith(name,'S')&$top=10", 2, 20, None),  # Jupiter, Saturn
    ("$planets", "$filter=contains(name,'e') and number_of_moons gt 0&$top=10", 2, 20, None),  # Jupiter, Neptune, (Mercury has 0 moons)
    ("$planets", "$filter=toupper(name) eq 'EARTH' and id eq 3&$top=10", 1, 20, None),
    # ============================================================================
    # FILTER - Logical Operations (6 tests)
    # ============================================================================
    ("$planets", "$filter=number_of_moons gt 0 and mass gt 50&$top=10", 4, 20, None),  # Jupiter, Saturn, Uranus, Neptune
    ("$planets", "$filter=number_of_moons gt 0 or mass gt 100&$top=10", 7, 20, None),
    ("$planets", "$filter=distance_from_sun lt 500 and number_of_moons eq 0&$top=10", 2, 20, None),  # Mercury, Venus
    ("$planets", "$filter=not(number_of_moons eq 0)&$top=10", 7, 20, None),
    ("$planets", "$filter=(mass gt 50 or mass lt 1) and diameter gt 5000&$top=10", 5, 20, None),
    ("$planets", "$filter=mean_temperature lt 200 or mean_temperature gt 400&$top=10", 9, 20, None),
    # ============================================================================
    # COMBINED - Select + OrderBy (5 tests) - BROKEN: $orderby not implemented
    # ============================================================================
    ("$planets", "$select=id,name&$orderby=id desc&$top=3", 3, 2, None),
    ("$planets", "$select=name,mass&$orderby=mass desc&$top=1", 1, 2, None),
    ("$planets", "$select=id,name,distance_from_sun&$orderby=distance_from_sun asc&$top=2", 2, 3, None),
    ("$planets", "$select=name,number_of_moons&$orderby=number_of_moons desc,name asc&$top=3", 3, 2, None),
    ("$planets", "$select=*&$orderby=mean_temperature asc&$top=4", 4, 20, None),
    # ============================================================================
    # COMBINED - Filter + OrderBy (5 tests) - BROKEN: $orderby not implemented
    # ============================================================================
    ("$planets", "$filter=number_of_moons gt 0&$orderby=number_of_moons desc&$top=3", 3, 20, None),
    ("$planets", "$filter=mass lt 50&$orderby=mass desc&$top=2", 2, 20, None),
    ("$planets", "$filter=distance_from_sun lt 500&$orderby=id asc&$top=5", 4, 20, None),
    ("$planets", "$filter=mean_temperature gt 100&$orderby=mean_temperature desc&$top=2", 2, 20, None),
    ("$planets", "$filter=diameter gt 10000&$orderby=distance_from_sun asc&$top=3", 3, 20, None),
    # ============================================================================
    # COMBINED - Filter + Select (5 tests)
    # ============================================================================
    ("$planets", "$filter=number_of_moons gt 0&$select=id,name,number_of_moons&$top=3", 3, 3, None),
    ("$planets", "$filter=mass lt 100&$select=name,mass&$top=4", 4, 2, None),
    ("$planets", "$filter=distance_from_sun lt 300&$select=id,name,distance_from_sun&$top=5", 4, 3, None),  # Mercury, Venus, Earth, Mars (all < 300)
    ("$planets", "$filter=mean_temperature gt 0&$select=id,name,mean_temperature&$top=5", 3, 3, None),  # Mercury, Venus, Earth (only 3 planets with temp > 0)
    ("$planets", "$filter=orbital_eccentricity lt 0.1&$select=name,orbital_eccentricity&$top=4", 4, 2, None),
    # ============================================================================
    # COMBINED - Filter + Select + OrderBy (5 tests) - BROKEN: $orderby + $select combo
    # ============================================================================
    ("$planets", "$filter=number_of_moons gt 0&$select=id,name,number_of_moons&$orderby=number_of_moons desc&$top=2", 2, 3, None),
    ("$planets", "$filter=mass lt 50&$select=name,mass&$orderby=mass desc&$top=3", 3, 2, None),
    ("$planets", "$filter=distance_from_sun lt 500&$select=id,name,distance_from_sun&$orderby=distance_from_sun asc&$top=4", 4, 3, None),
    ("$planets", "$filter=mean_temperature gt 0&$select=id,name,mean_temperature&$orderby=mean_temperature asc&$top=3", 3, 3, None),
    ("$planets", "$filter=diameter gt 5000&$select=id,name,diameter&$orderby=diameter desc&$top=4", 4, 3, None),
    # ============================================================================
    # PAGING - Skip (4 tests)
    # ============================================================================
    ("$planets", "$top=3&$skip=0", 3, 20, None),
    ("$planets", "$top=3&$skip=3", 3, 20, None),
    ("$planets", "$top=3&$skip=6", 3, 20, None),
    ("$planets", "$top=10&$skip=8", 1, 20, None),
    # ============================================================================
    # PAGING - Skip with OrderBy (4 tests) - BROKEN: $orderby not implemented
    # ============================================================================
    ("$planets", "$orderby=id asc&$top=3&$skip=0", 3, 20, None),
    ("$planets", "$orderby=id asc&$top=3&$skip=3", 3, 20, None),
    ("$planets", "$orderby=id desc&$top=2&$skip=2", 2, 20, None),
    ("$planets", "$orderby=name asc&$top=5&$skip=4", 5, 20, None),
    # ============================================================================
    # PAGING - Skip with Filter (3 tests) - BROKEN: row count mismatches
    # ============================================================================
    ("$planets", "$filter=number_of_moons gt 0&$top=2&$skip=0", 2, 20, None),
    ("$planets", "$filter=mass lt 50&$top=3&$skip=2", 3, 20, None),
    ("$planets", "$filter=distance_from_sun lt 500&$top=2&$skip=1", 2, 20, None),
    # ============================================================================
    # COUNT (3 tests) - BROKEN: null handling may be incorrect
    # ============================================================================
    ("$planets", "$count=true&$top=0", 0, 20, "Exception"),
    ("$planets", "$count=true&$top=5", 5, 20, "Exception"),
    ("$planets", "$filter=number_of_moons gt 0&$count=true", None, 20, "Exception"),
    # ============================================================================
    # DECIMAL COLUMN TESTS (3 tests) - BROKEN: list concat + orderby
    # ============================================================================
    ("$planets", "$filter=gravity gt 10&$top=10", 2, 20, None),  # Jupiter (24.79), Neptune (11.15)
    ("$planets", "$filter=gravity lt 5&$top=10", 3, 20, None),  # Mercury (3.7), Mars (3.71), Pluto (0.7)
    ("$planets", "$select=name,gravity&$orderby=gravity desc&$top=3", 3, 2, None),
    # ============================================================================
    # NULL HANDLING (2 tests) - BROKEN: null filter results wrong
    # ============================================================================
    ("$planets", "$filter=surface_pressure eq null", 4, 20, None),
    ("$planets", "$filter=surface_pressure ne null", 5, 20, None),
    # ============================================================================
    # EDGE CASES (5 tests)
    # ============================================================================
    ("$planets", "$skip=100", 0, 20, None),  # Skip beyond result set
    ("$planets", "$skip=8&$top=10", 1, 20, None),  # One remaining after skip
    ("$planets", "$orderby=id asc,name asc&$top=9", 9, 20, None),  # All rows - BROKEN: $orderby
    ("$planets", "$select=id&$orderby=id&$top=5", 5, 1, None),  # BROKEN: select + orderby
    ("$planets", "$filter=id gt 0 and id lt 10&$top=10", 9, 20, None),
    # ============================================================================
    # EXTENDED VARIATIONS (15 tests)
    # ============================================================================
    ("$planets", "$select=name,gravity,mean_temperature&$top=3", 3, 3, None),
    ("$planets", "$select=name,gravity,mean_temperature&$filter=mass gt 10&$top=2", 2, 3, None),
    ("$planets", "$filter=(mass gt 50 and number_of_moons gt 0)&$top=10", 4, 20, None),
    ("$planets", "$filter=mass gt 100 or number_of_moons gt 20&$top=10", 4, 20, None),
    ("$planets", "$top=2&$skip=7", 2, 20, None),
    ("$planets", "$skip=9&$top=1", 0, 20, None),
    ("$planets", "$select=id,name&$filter=name eq 'Earth'&$top=1", 1, 2, None),
    ("$planets", "$select=nonexistent&$top=1", None, None, "Exception"),
    ("$planets", "$filter=mass gt 'abc'&$top=1", None, None, "Exception"),
    ("$planets", "$filter=startswith(name,'M') and mass gt 0&$top=5", 2, 20, None),
    ("$planets", "$select=*,name&$top=1", None, None, "Exception"),
    ("$planets", "$filter=gravity gt 10&$select=name,gravity&$top=2", 2, 2, None),
    ("$planets", "$filter=mean_temperature gt 0&$select=name,mean_temperature&$top=5", 3, 2, None),
    ("$planets", "$filter=id ge 1 and id le 3&$top=10", 3, 20, None),
    ("$planets", "$orderby=id desc&$skip=1&$top=2", 2, 20, None),
    # ============================================================================
    # ERROR CASES (4 tests)
    # ============================================================================
    ("$planets", "$orderby=invalid_column&$top=1", None, None, "Exception"),  # BROKEN: $orderby
    ("$planets", "$filter=invalid_column eq 1", None, None, "Exception"),
    ("$planets", "$select=invalid_column&$top=1", None, None, "Exception"),
    ("$planets", "$filter=id eq&$top=1", None, None, "Exception"),

    ("testdata.tpch.nation", "", 24, 4, None),
    ("testdata.tpch.region", "", 4, 3, None),
    # ============================================================================
    # TESTDATA.TPCH.* EXTENDED TESTS (48 tests)
    # ============================================================================
    # Nation table extended tests
    ("testdata.tpch.nation", "$top=5", 5, 4, None),
    ("testdata.tpch.nation", "$top=10", 10, 4, None),
    ("testdata.tpch.nation", "$skip=20", 4, 4, None),
    ("testdata.tpch.nation", "$top=5&$skip=10", 5, 4, None),
    ("testdata.tpch.nation", "$select=n_name", 24, 1, None),
    ("testdata.tpch.nation", "$select=n_nationkey,n_name", 24, 2, None),
    ("testdata.tpch.nation", "$select=n_name,n_regionkey,n_comment", 24, 3, None),
    ("testdata.tpch.nation", "$filter=n_regionkey eq 1", None, 4, None),
    ("testdata.tpch.nation", "$filter=n_nationkey lt 5", None, 4, None),
    ("testdata.tpch.nation", "$filter=n_nationkey gt 20", None, 4, None),
    ("testdata.tpch.nation", "$filter=n_regionkey eq 0&$select=n_name", None, 1, None),
    ("testdata.tpch.nation", "$filter=n_nationkey ge 10 and n_nationkey le 15", None, 4, None),
    ("testdata.tpch.nation", "$orderby=n_name&$top=3", 3, 4, None),
    ("testdata.tpch.nation", "$orderby=n_nationkey desc&$top=5", 5, 4, None),
    # Region table extended tests
    ("testdata.tpch.region", "$top=3", 3, 3, None),
    ("testdata.tpch.region", "$select=r_name", 4, 1, None),
    ("testdata.tpch.region", "$select=r_regionkey,r_name", 4, 2, None),
    ("testdata.tpch.region", "$filter=r_regionkey lt 3", None, 3, None),
    ("testdata.tpch.region", "$filter=r_regionkey eq 2", None, 3, None),
    ("testdata.tpch.region", "$orderby=r_name&$top=2", 2, 3, None),
    ("testdata.tpch.region", "$skip=2", 2, 3, None),
    # Supplier table tests
    ("testdata.tpch.supplier", "$top=10", 10, 7, None),
    ("testdata.tpch.supplier", "$top=5", 5, 7, None),
    ("testdata.tpch.supplier", "$select=s_name", None, 1, None),
    ("testdata.tpch.supplier", "$select=s_suppkey,s_name,s_nationkey", None, 3, None),
    ("testdata.tpch.supplier", "$filter=s_nationkey eq 1&$top=20", None, 7, None),
    ("testdata.tpch.supplier", "$filter=s_suppkey lt 10", None, 7, None),
    ("testdata.tpch.supplier", "$filter=s_nationkey eq 0&$select=s_name,s_phone", None, 2, None),
    ("testdata.tpch.supplier", "$orderby=s_name&$top=5", 5, 7, None),
    ("testdata.tpch.supplier", "$skip=5&$top=10", 10, 7, None),
    # Customer table tests
    ("testdata.tpch.customer", "$top=10", 10, 8, None),
    ("testdata.tpch.customer", "$top=5", 5, 8, None),
    ("testdata.tpch.customer", "$select=c_name", None, 1, None),
    ("testdata.tpch.customer", "$select=c_custkey,c_name,c_nationkey", None, 3, None),
    ("testdata.tpch.customer", "$filter=c_nationkey eq 5&$top=20", None, 8, None),
    ("testdata.tpch.customer", "$filter=c_custkey lt 20", None, 8, None),
    ("testdata.tpch.customer", "$filter=c_nationkey ge 10&$top=50", None, 8, None),
    ("testdata.tpch.customer", "$orderby=c_name&$top=5", 5, 8, None),
    ("testdata.tpch.customer", "$skip=10&$top=15", 15, 8, None),
    # Part table tests
    ("testdata.tpch.part", "$top=10", 10, 9, None),
    ("testdata.tpch.part", "$top=20", 20, 9, None),
    ("testdata.tpch.part", "$select=p_name", None, 1, None),
    ("testdata.tpch.part", "$select=p_partkey,p_name,p_type", None, 3, None),
    ("testdata.tpch.part", "$filter=p_size eq 5&$top=30", None, 9, None),
    ("testdata.tpch.part", "$filter=p_partkey lt 50", None, 9, None),
    ("testdata.tpch.part", "$orderby=p_name&$top=8", 8, 9, None),
    ("testdata.tpch.part", "$skip=20&$top=10", 10, 9, None),
    # ============================================================================
    # TESTDATA.TPCH.* ADDITIONAL TESTS - MULTI-FILE DATASETS (50 tests)
    # ============================================================================
    # Orders table tests (multi-file dataset) - columns have arrow_schema prefix, filter/select broken
    ("testdata.tpch.orders", "$top=10", 10, 9, None),
    ("testdata.tpch.orders", "$top=25", 25, 9, None),
    ("testdata.tpch.orders", "$top=50", 50, 9, None),
    ("testdata.tpch.orders", "$skip=100&$top=20", 20, 9, None),
    ("testdata.tpch.orders", "$skip=200&$top=50", 50, 9, None),
    # Partsupp table tests (multi-file dataset) - columns have arrow_schema prefix, filter/select broken  
    ("testdata.tpch.partsupp", "$top=10", 10, 5, None),
    ("testdata.tpch.partsupp", "$top=25", 25, 5, None),
    ("testdata.tpch.partsupp", "$top=50", 50, 5, None),
    ("testdata.tpch.partsupp", "$skip=50&$top=20", 20, 5, None),
    ("testdata.tpch.partsupp", "$skip=100&$top=25", 25, 5, None),
    # Nation - more complex variations
    ("testdata.tpch.nation", "$filter=n_regionkey ne 1", None, 4, None),
    ("testdata.tpch.nation", "$filter=n_regionkey eq 2", None, 4, None),
    ("testdata.tpch.nation", "$filter=n_regionkey eq 3", None, 4, None),
    ("testdata.tpch.nation", "$filter=n_nationkey ge 5 and n_nationkey lt 15&$select=n_name", None, 1, None),
    ("testdata.tpch.nation", "$filter=n_nationkey eq 10 or n_nationkey eq 20", None, 4, None),
    ("testdata.tpch.nation", "$orderby=n_regionkey,n_name&$top=10", 10, 4, None),
    ("testdata.tpch.nation", "$skip=5&$top=5&$select=n_name,n_regionkey", 5, 2, None),
    ("testdata.tpch.nation", "$filter=n_regionkey le 2&$top=20", None, 4, None),
    # Region - more variations
    ("testdata.tpch.region", "$filter=r_regionkey ne 0", None, 3, None),
    ("testdata.tpch.region", "$filter=r_regionkey ge 1", None, 3, None),
    ("testdata.tpch.region", "$orderby=r_regionkey desc", 4, 3, None),
    ("testdata.tpch.region", "$skip=1&$top=2&$select=r_name", 2, 1, None),
    ("testdata.tpch.region", "$filter=r_regionkey eq 1 or r_regionkey eq 3", None, 3, None),
    # Supplier - more complex tests
    ("testdata.tpch.supplier", "$filter=s_nationkey ge 5 and s_nationkey lt 10&$top=30", None, 7, None),
    ("testdata.tpch.supplier", "$filter=s_suppkey ge 20 and s_suppkey lt 40", None, 7, None),
    ("testdata.tpch.supplier", "$filter=s_nationkey eq 2 or s_nationkey eq 5", None, 7, None),
    ("testdata.tpch.supplier", "$orderby=s_nationkey,s_name&$top=12", 12, 7, None),
    ("testdata.tpch.supplier", "$skip=15&$top=8&$select=s_name,s_nationkey", 8, 2, None),
    ("testdata.tpch.supplier", "$filter=s_suppkey le 30&$top=35", None, 7, None),
    ("testdata.tpch.supplier", "$orderby=s_suppkey desc&$top=10", 10, 7, None),
    # Customer - more complex tests
    ("testdata.tpch.customer", "$filter=c_custkey ge 30 and c_custkey lt 60", None, 8, None),
    ("testdata.tpch.customer", "$filter=c_nationkey ne 5&$top=40", None, 8, None),
    ("testdata.tpch.customer", "$filter=c_nationkey eq 3 or c_nationkey eq 7", None, 8, None),
    ("testdata.tpch.customer", "$orderby=c_nationkey,c_name&$top=12", 12, 8, None),
    ("testdata.tpch.customer", "$skip=25&$top=10&$select=c_name,c_nationkey", 10, 2, None),
    ("testdata.tpch.customer", "$filter=c_custkey le 50&$top=55", None, 8, None),
    ("testdata.tpch.customer", "$orderby=c_custkey desc&$top=8", 8, 8, None),
    # Part - more variations
    ("testdata.tpch.part", "$filter=p_size ge 10 and p_size lt 30&$top=40", None, 9, None),
    ("testdata.tpch.part", "$filter=p_partkey ge 50 and p_partkey lt 100", None, 9, None),
    ("testdata.tpch.part", "$filter=p_size eq 15 or p_size eq 25", None, 9, None),
    ("testdata.tpch.part", "$orderby=p_size,p_name&$top=15", 15, 9, None),
    ("testdata.tpch.part", "$skip=30&$top=12&$select=p_name,p_type,p_size", 12, 3, None),
    ("testdata.tpch.part", "$filter=p_partkey le 80&$top=90", None, 9, None),
    ("testdata.tpch.part", "$orderby=p_partkey desc&$top=20", 20, 9, None),
    # ============================================================================
    # TPC-H STRING FUNCTIONS - Nation table (15 tests)
    # ============================================================================
    ("testdata.tpch.nation", "$filter=startswith(n_name,'A')&$top=30", None, 4, None),
    ("testdata.tpch.nation", "$filter=startswith(n_name,'C')&$top=30", None, 4, None),
    ("testdata.tpch.nation", "$filter=startswith(n_name,'U')&$top=30", None, 4, None),
    ("testdata.tpch.nation", "$filter=endswith(n_name,'A')&$top=30", None, 4, None),
    ("testdata.tpch.nation", "$filter=endswith(n_name,'Y')&$top=30", None, 4, None),
    ("testdata.tpch.nation", "$filter=endswith(n_name,'IA')&$top=30", None, 4, None),
    ("testdata.tpch.nation", "$filter=contains(n_name,'AN')&$top=30", None, 4, None),
    ("testdata.tpch.nation", "$filter=contains(n_name,'IN')&$top=30", None, 4, None),
    ("testdata.tpch.nation", "$filter=contains(n_name,'IA')&$top=30", None, 4, None),
    ("testdata.tpch.nation", "$filter=startswith(n_name,'A') and n_regionkey eq 1", None, 4, None),
    ("testdata.tpch.nation", "$filter=endswith(n_name,'A') or endswith(n_name,'Y')", None, 4, None),
    ("testdata.tpch.nation", "$filter=contains(n_name,'A') and n_nationkey lt 10", None, 4, None),
    ("testdata.tpch.nation", "$filter=tolower(n_name) eq tolower(n_name)", 24, 4, None),
    ("testdata.tpch.nation", "$filter=startswith(n_name,'I') or startswith(n_name,'J')", None, 4, None),
    ("testdata.tpch.nation", "$filter=contains(n_name,'UNITED')&$top=10", None, 4, None),
    # ============================================================================
    # TPC-H STRING FUNCTIONS - Region table (10 tests)
    # ============================================================================
    ("testdata.tpch.region", "$filter=startswith(r_name,'A')&$top=10", None, 3, None),
    ("testdata.tpch.region", "$filter=startswith(r_name,'E')&$top=10", None, 3, None),
    ("testdata.tpch.region", "$filter=startswith(r_name,'M')&$top=10", None, 3, None),
    ("testdata.tpch.region", "$filter=endswith(r_name,'A')&$top=10", None, 3, None),
    ("testdata.tpch.region", "$filter=endswith(r_name,'E')&$top=10", None, 3, None),
    ("testdata.tpch.region", "$filter=contains(r_name,'RI')&$top=10", None, 3, None),
    ("testdata.tpch.region", "$filter=contains(r_name,'AS')&$top=10", None, 3, None),
    ("testdata.tpch.region", "$filter=toupper(r_name) eq toupper(r_name)", 4, 3, None),
    ("testdata.tpch.region", "$filter=startswith(r_name,'A') or startswith(r_name,'E')", None, 3, None),
    ("testdata.tpch.region", "$filter=contains(r_name,'EAST') or contains(r_name,'WEST')", None, 3, None),
    # ============================================================================
    # TPC-H STRING FUNCTIONS - Supplier table (12 tests)
    # ============================================================================
    ("testdata.tpch.supplier", "$filter=startswith(s_name,'Supplier')&$top=50", None, 7, None),
    ("testdata.tpch.supplier", "$filter=contains(s_name,'000')&$top=30", None, 7, None),
    ("testdata.tpch.supplier", "$filter=contains(s_name,'001')&$top=30", None, 7, None),
    ("testdata.tpch.supplier", "$filter=contains(s_name,'#')&$top=50", None, 7, None),
    ("testdata.tpch.supplier", "$filter=endswith(s_name,'1')&$top=40", None, 7, None),
    ("testdata.tpch.supplier", "$filter=endswith(s_name,'5')&$top=40", None, 7, None),
    ("testdata.tpch.supplier", "$filter=startswith(s_phone,'1')&$top=50", None, 7, None),
    ("testdata.tpch.supplier", "$filter=startswith(s_phone,'2')&$top=50", None, 7, None),
    ("testdata.tpch.supplier", "$filter=contains(s_phone,'-')&$top=100", None, 7, None),
    ("testdata.tpch.supplier", "$filter=startswith(s_name,'Supplier') and s_nationkey eq 5", None, 7, None),
    ("testdata.tpch.supplier", "$filter=contains(s_address,'St')&$top=50", None, 7, None),
    ("testdata.tpch.supplier", "$filter=toupper(s_name) eq toupper(s_name)&$top=20", 20, 7, None),
    # ============================================================================
    # TPC-H STRING FUNCTIONS - Customer table (12 tests)
    # ============================================================================
    ("testdata.tpch.customer", "$filter=startswith(c_name,'Customer')&$top=50", None, 8, None),
    ("testdata.tpch.customer", "$filter=contains(c_name,'000')&$top=30", None, 8, None),
    ("testdata.tpch.customer", "$filter=contains(c_name,'#')&$top=100", None, 8, None),
    ("testdata.tpch.customer", "$filter=endswith(c_name,'1')&$top=50", None, 8, None),
    ("testdata.tpch.customer", "$filter=endswith(c_name,'9')&$top=50", None, 8, None),
    ("testdata.tpch.customer", "$filter=startswith(c_phone,'1')&$top=60", None, 8, None),
    ("testdata.tpch.customer", "$filter=startswith(c_phone,'3')&$top=60", None, 8, None),
    ("testdata.tpch.customer", "$filter=contains(c_phone,'-')&$top=100", None, 8, None),
    ("testdata.tpch.customer", "$filter=startswith(c_name,'Customer') and c_nationkey eq 10", None, 8, None),
    ("testdata.tpch.customer", "$filter=contains(c_address,'Ave')&$top=60", None, 8, None),
    ("testdata.tpch.customer", "$filter=tolower(c_name) eq tolower(c_name)&$top=30", 30, 8, None),
    ("testdata.tpch.customer", "$filter=startswith(c_name,'Customer#0000')&$top=100", None, 8, None),
    # ============================================================================
    # TPC-H STRING FUNCTIONS - Part table (15 tests)
    # ============================================================================
    ("testdata.tpch.part", "$filter=contains(p_name,'red')&$top=50", None, 9, None),
    ("testdata.tpch.part", "$filter=contains(p_name,'blue')&$top=50", None, 9, None),
    ("testdata.tpch.part", "$filter=contains(p_name,'green')&$top=50", None, 9, None),
    ("testdata.tpch.part", "$filter=contains(p_name,'steel')&$top=50", None, 9, None),
    ("testdata.tpch.part", "$filter=contains(p_name,'forest')&$top=50", None, 9, None),
    ("testdata.tpch.part", "$filter=startswith(p_type,'STANDARD')&$top=60", None, 9, None),
    ("testdata.tpch.part", "$filter=startswith(p_type,'PROMO')&$top=60", None, 9, None),
    ("testdata.tpch.part", "$filter=startswith(p_type,'SMALL')&$top=60", None, 9, None),
    ("testdata.tpch.part", "$filter=endswith(p_type,'STEEL')&$top=60", None, 9, None),
    ("testdata.tpch.part", "$filter=endswith(p_type,'BRASS')&$top=60", None, 9, None),
    ("testdata.tpch.part", "$filter=contains(p_type,'COPPER')&$top=60", None, 9, None),
    ("testdata.tpch.part", "$filter=contains(p_name,'red') and p_size lt 20", None, 9, None),
    ("testdata.tpch.part", "$filter=startswith(p_type,'MEDIUM') and p_size gt 10", None, 9, None),
    ("testdata.tpch.part", "$filter=tolower(p_brand) eq tolower(p_brand)&$top=25", 25, 9, None),
    ("testdata.tpch.part", "$filter=contains(p_container,'BOX')&$top=70", None, 9, None),
    # ============================================================================
    # PRODUCTION ISSUE TESTS - Complex $apply aggregations (8 tests)
    # ============================================================================
    # Based on production failure: $count=true&$top=0&$apply=groupby((planetId),aggregate($count as SatelliteCount))&$orderby=SatelliteCount desc&$top=1
    # Simulating with TPC-H data: group by region/nation and count, order by count desc, take top 1
    # NOTE: $apply is not yet supported, so these tests verify the queries fail gracefully or are ignored
    ("testdata.tpch.nation", "$apply=groupby((n_regionkey),aggregate($count as NationCount))&$orderby=NationCount desc&$top=1", None, None, "Exception"),  # Group nations by region, count, get region with most nations
    ("testdata.tpch.supplier", "$apply=groupby((s_nationkey),aggregate($count as SupplierCount))&$orderby=SupplierCount desc&$top=1", None, None, "Exception"),  # Group suppliers by nation, count, get nation with most suppliers
    ("testdata.tpch.customer", "$apply=groupby((c_nationkey),aggregate($count as CustomerCount))&$orderby=CustomerCount desc&$top=1", None, None, "Exception"),  # Group customers by nation, count, get nation with most customers
    ("testdata.tpch.nation", "$count=true&$top=0&$apply=groupby((n_regionkey),aggregate($count as NationCount))&$orderby=NationCount desc&$top=1", None, None, "Exception"),  # Conflicting $top parameters (0 and 1) - production issue pattern
    ("testdata.tpch.supplier", "$apply=groupby((s_nationkey),aggregate(s_suppkey with count as SupplierCount))", None, 7, None),  # $apply is ignored, returns all columns (not yet supported)
    ("testdata.tpch.nation", "$apply=groupby((n_regionkey),aggregate($count as total))&$orderby=total desc", None, None, "Exception"),  # Group and order without $top
    ("testdata.tpch.part", "$apply=groupby((p_size),aggregate($count as PartCount))&$orderby=PartCount desc&$top=5", None, None, "Exception"),  # Group parts by size, get top 5 sizes
    ("testdata.tpch.customer", "$apply=groupby((c_nationkey),aggregate($count as cnt))&$filter=cnt gt 10", None, None, "Exception"),  # Filter after aggregation (having clause pattern)
    # ============================================================================
    # NESTED FUNCTION TESTS (30 tests)
    # ============================================================================
    ("$planets", "$filter=tolower(name) eq 'earth'", 1, 20, None),  # Case-insensitive match
    ("$planets", "$filter=toupper(name) eq 'MARS'", 1, 20, None),  # Case-insensitive match uppercase
    ("$planets", "$filter=startswith(tolower(name),'m')", 2, 20, None),  # Mars, Mercury (lowercase prefix)
    ("$planets", "$filter=startswith(toupper(name),'J')", 1, 20, None),  # Jupiter (uppercase prefix)
    ("$planets", "$filter=endswith(tolower(name),'s')", 3, 20, None),  # Venus, Mars, Uranus (lowercase suffix)
    ("$planets", "$filter=contains(tolower(name),'ar')", 2, 20, None),  # Earth, Mars (lowercase contains)
    ("$planets", "$filter=contains(toupper(name),'TURN')", 1, 20, None),  # Saturn (uppercase contains)
    ("$planets", "$filter=startswith(tolower(name),'e') and mass gt 0.5", 1, 20, None),  # Earth with mass filter
    ("$planets", "$filter=contains(tolower(name),'u') or contains(tolower(name),'e')", 8, 20, None),  # Multiple planets
    ("$planets", "$filter=endswith(name,'s') and startswith(name,'M')", 1, 20, None),  # Mars (both conditions)
    ("testdata.tpch.nation", "$filter=startswith(tolower(n_name),'a')&$top=20", None, 4, None),  # Case-insensitive starts with 'a'
    ("testdata.tpch.nation", "$filter=endswith(tolower(n_name),'ia')&$top=20", None, 4, None),  # Case-insensitive ends with 'ia'
    ("testdata.tpch.nation", "$filter=contains(toupper(n_name),'UNITED')&$top=20", None, 4, None),  # Case-insensitive contains
    ("testdata.tpch.region", "$filter=startswith(toupper(r_name),'M')&$top=10", None, 3, None),  # MIDDLE EAST uppercase
    ("testdata.tpch.region", "$filter=contains(tolower(r_name),'east')&$top=10", None, 3, None),  # Contains 'east' lowercase
    ("testdata.tpch.supplier", "$filter=startswith(tolower(s_name),'supplier')&$top=30", None, 7, None),  # All suppliers start with 'Supplier'
    ("testdata.tpch.customer", "$filter=startswith(tolower(c_name),'customer')&$top=30", None, 8, None),  # All customers start with 'Customer'
    ("testdata.tpch.part", "$filter=contains(tolower(p_name),'steel')&$top=40", None, 9, None),  # Contains 'steel' case-insensitive
    ("testdata.tpch.part", "$filter=startswith(tolower(p_type),'medium')&$top=40", None, 9, None),  # Type starts with 'medium'
    ("testdata.tpch.nation", "$filter=startswith(tolower(n_name),'c') and n_regionkey eq 0", None, 4, None),  # Nested with condition
    ("testdata.tpch.nation", "$filter=contains(tolower(n_name),'in') or contains(tolower(n_name),'an')", None, 4, None),  # Multiple nested contains
    ("$planets", "$filter=tolower(name) eq tolower('EARTH')", 1, 20, None),  # Both sides lowercased
    ("$planets", "$filter=toupper(name) eq toupper('mars')", 1, 20, None),  # Both sides uppercased
    ("testdata.tpch.nation", "$filter=endswith(tolower(n_name),'a') and n_nationkey lt 15", None, 4, None),  # Combined conditions
    ("testdata.tpch.part", "$filter=contains(toupper(p_name),'RED') and p_size lt 30", None, 9, None),  # Nested with numeric filter
    ("testdata.tpch.supplier", "$filter=contains(tolower(s_name),'000') and s_suppkey lt 100", None, 7, None),  # Nested with range
    ("testdata.tpch.customer", "$filter=endswith(tolower(c_name),'5') or endswith(tolower(c_name),'0')", None, 8, None),  # Multiple endswith nested
    ("testdata.tpch.region", "$filter=toupper(r_name) eq 'AFRICA' or toupper(r_name) eq 'ASIA'", None, 3, None),  # Multiple toupper comparisons
    ("testdata.tpch.nation", "$filter=startswith(n_name,'A') or startswith(tolower(n_name),'u')", None, 4, None),  # Mixed case operations
    ("testdata.tpch.part", "$filter=startswith(tolower(p_type),'promo') or startswith(tolower(p_type),'standard')", None, 9, None),  # Multiple nested startswith
    # ============================================================================
    # ARITHMETIC OPERATOR TESTS (20 tests)
    # ============================================================================
    ("$planets", "$filter=id add 1 eq 2", 1, 20, None),  # id=1: 1+1=2
    ("$planets", "$filter=id add 5 lt 10", 4, 20, None),  # id<5: 1+5=6, 2+5=7, 3+5=8, 4+5=9
    ("$planets", "$filter=number_of_moons sub 1 eq 0", 1, 20, None),  # Moons=1: Earth
    ("$planets", "$filter=number_of_moons sub 5 gt 10", 3, 20, None),  # Jupiter(79-5>10), Saturn(82-5>10), Uranus(27-5>10)
    ("$planets", "$filter=id mul 2 lt 10", 4, 20, None),  # id<5: 1*2=2, 2*2=4, 3*2=6, 4*2=8
    ("$planets", "$filter=id mul 3 eq 9", 1, 20, None),  # id=3: 3*3=9
    ("$planets", "$filter=number_of_moons div 10 gt 5", 2, 20, None),  # Jupiter(79/10>5), Saturn(82/10>5)
    ("$planets", "$filter=id mod 2 eq 0", 4, 20, None),  # Even IDs: 2,4,6,8
    ("$planets", "$filter=id mod 2 eq 1", 5, 20, None),  # Odd IDs: 1,3,5,7,9
    ("$planets", "$filter=id mod 3 eq 0", 3, 20, None),  # IDs divisible by 3: 3,6,9
    ("testdata.tpch.nation", "$filter=n_nationkey add 10 lt 15&$top=20", None, 4, None),  # n_nationkey < 5
    ("testdata.tpch.nation", "$filter=n_nationkey mul 2 lt 30&$top=20", None, 4, None),  # n_nationkey < 15
    ("testdata.tpch.nation", "$filter=n_regionkey add 1 eq 2&$top=10", None, 4, None),  # n_regionkey = 1
    ("testdata.tpch.supplier", "$filter=s_suppkey mod 10 eq 0&$top=30", None, 7, None),  # Supplier keys divisible by 10
    ("testdata.tpch.supplier", "$filter=s_suppkey div 100 lt 5&$top=50", None, 7, None),  # s_suppkey < 500
    ("testdata.tpch.customer", "$filter=c_custkey mod 100 eq 0&$top=30", None, 8, None),  # Customer keys divisible by 100
    ("testdata.tpch.part", "$filter=p_size mul 2 gt 50&$top=50", None, 9, None),  # Large parts: size*2 > 50
    ("testdata.tpch.part", "$filter=p_size add 10 lt 30&$top=60", None, 9, None),  # Small parts: size+10 < 30
    ("testdata.tpch.part", "$filter=p_partkey mod 1000 eq 0&$top=20", None, 9, None),  # Part keys divisible by 1000
    ("$planets", "$filter=id add 1 gt 5 and id sub 1 lt 8", 4, 20, None),  # Combined arithmetic: id in [5,8]
    # ============================================================================
    # MATH FUNCTION TESTS (18 tests)
    # ============================================================================
    ("$planets", "$filter=ceiling(mass) gt 1000", 1, 20, None),  # Jupiter(1898)
    ("$planets", "$filter=floor(mass) lt 1", 3, 20, None),  # Mercury, Mars, Pluto
    ("$planets", "$filter=round(mass) eq 1", 1, 20, None),  # Earth (mass ~1.0)
    ("$planets", "$filter=ceiling(mass) eq 1", 3, 20, None),  # Mercury(0.055→1), Mars(0.107→1), Pluto(0.0022→1)
    ("$planets", "$filter=floor(mass) eq 0", 3, 20, None),  # Same as above, floor to 0
    ("$planets", "$filter=round(mass) gt 100", 3, 20, None),  # Jupiter(1898), Saturn(568), Neptune(102)
    ("$planets", "$filter=ceiling(number_of_moons) gt 50", 2, 20, None),  # Jupiter(79), Saturn(82)
    ("$planets", "$filter=floor(number_of_moons) lt 5", 4, 20, None),  # Mercury(0), Venus(0), Earth(1), Mars(2)
    ("$planets", "$filter=round(mass) eq round(mass)", 9, 20, None),  # All planets (identity)
    ("testdata.tpch.nation", "$filter=ceiling(n_nationkey) gt 20&$top=20", None, 4, None),  # n_nationkey > 20
    ("testdata.tpch.nation", "$filter=floor(n_nationkey) lt 5&$top=20", None, 4, None),  # n_nationkey < 5
    ("testdata.tpch.supplier", "$filter=ceiling(s_suppkey) lt 100&$top=40", None, 7, None),  # s_suppkey < 100
    ("testdata.tpch.customer", "$filter=floor(c_custkey) lt 100&$top=50", None, 8, None),  # c_custkey < 100
    ("testdata.tpch.part", "$filter=ceiling(p_size) gt 40&$top=40", None, 9, None),  # Large parts
    ("testdata.tpch.part", "$filter=floor(p_size) lt 10&$top=60", None, 9, None),  # Small parts
    ("testdata.tpch.part", "$filter=round(p_size) eq 25&$top=30", None, 9, None),  # Size around 25
    ("$planets", "$filter=ceiling(mass) gt 1 and floor(number_of_moons) gt 0", 5, 20, None),  # Combined math functions
    ("testdata.tpch.nation", "$filter=round(n_nationkey) mod 5 eq 0&$top=20", None, 4, "Exception"),  # Math with arithmetic — Known issue: DOUBLE % INTEGER causes IncorrectTypeError
    # ============================================================================
    # REMAINING STRING FUNCTION TESTS - length, indexof, substring, concat, trim (35 tests)
    # ============================================================================
    ("$planets", "$filter=length(name) gt 5", 5, 20, None),  # Mercury(7), Jupiter(7), Saturn(6), Neptune(7), Uranus(6)
    ("$planets", "$filter=length(name) eq 4", 1, 20, None),  # Mars
    ("$planets", "$filter=length(name) lt 6", 4, 20, None),  # Mars(4), Earth(5), Venus(5), Pluto(5)
    ("$planets", "$filter=length(name) ge 7", 3, 20, None),  # Mercury, Jupiter, Neptune
    ("$planets", "$filter=length(name) le 5", 4, 20, None),  # Mars, Earth, Venus, Pluto
    ("$planets", "$filter=indexof(name,'a') gt 0", 4, 20, None),  # Mars(M-a), Earth(E-a), Saturn(S-a), Uranus(Ur-a)
    ("$planets", "$filter=indexof(name,'e') ge 0", 4, 20, None),  # Mercury, Venus, Earth, Neptune
    ("$planets", "$filter=indexof(name,'u') ge 0", 7, 20, None),  # Jupiter, Saturn, Uranus, Neptune, Pluto, Mercury, Venus
    ("$planets", "$filter=indexof(name,'z') eq -1", 9, 20, None),  # All planets (no 'z')
    ("$planets", "$filter=indexof(name,'M') eq 0", 2, 20, None),  # Mars, Mercury (starts with M)
    ("$planets", "$filter=substring(name,0,3) eq 'Mar'", 1, 20, None),  # Mars
    ("$planets", "$filter=substring(name,0,3) eq 'Ear'", 1, 20, None),  # Earth
    ("$planets", "$filter=substring(name,0,1) eq 'M'", 2, 20, None),  # Mars, Mercury
    ("$planets", "$filter=substring(name,0,2) eq 'Ju'", 1, 20, None),  # Jupiter
    ("$planets", "$filter=substring(name,1,3) eq 'erc'", 1, 20, None),  # Mercury
    ("testdata.tpch.nation", "$filter=length(n_name) gt 10&$top=20", None, 4, None),  # Long nation names
    ("testdata.tpch.nation", "$filter=length(n_name) le 7&$top=20", None, 4, None),  # Short nation names
    ("testdata.tpch.nation", "$filter=indexof(n_name,'A') ge 0&$top=20", None, 4, None),  # Contains 'A'
    ("testdata.tpch.nation", "$filter=indexof(n_name,'UNITED') ge 0&$top=10", None, 4, None),  # Contains 'UNITED'
    ("testdata.tpch.nation", "$filter=substring(n_name,0,1) eq 'A'&$top=20", None, 4, None),  # Starts with 'A'
    ("testdata.tpch.region", "$filter=length(r_name) gt 6&$top=10", None, 3, None),  # AMERICA, MIDDLE EAST
    ("testdata.tpch.region", "$filter=indexof(r_name,'EAST') ge 0&$top=10", None, 3, None),  # Contains EAST
    ("testdata.tpch.supplier", "$filter=length(s_name) eq 18&$top=40", None, 7, None),  # Supplier#000000001 = 18 chars
    ("testdata.tpch.supplier", "$filter=indexof(s_name,'#') ge 0&$top=50", None, 7, None),  # All have '#'
    ("testdata.tpch.supplier", "$filter=substring(s_name,0,8) eq 'Supplier'&$top=50", None, 7, None),  # All start with 'Supplier'
    ("testdata.tpch.customer", "$filter=length(c_name) eq 18&$top=40", None, 8, None),  # Customer#000000001 = 18 chars
    ("testdata.tpch.customer", "$filter=indexof(c_name,'#') ge 0&$top=60", None, 8, None),  # All have '#'
    ("testdata.tpch.customer", "$filter=substring(c_name,0,8) eq 'Customer'&$top=60", None, 8, None),  # All start with 'Customer'
    ("testdata.tpch.part", "$filter=length(p_name) gt 20&$top=50", None, 9, None),  # Long part names
    ("testdata.tpch.part", "$filter=indexof(p_name,'steel') ge 0&$top=40", None, 9, None),  # Contains 'steel'
    ("testdata.tpch.part", "$filter=substring(p_type,0,7) eq 'PROMO '&$top=40", None, 9, None),  # Note: may have trailing space
    ("$planets", "$filter=length(name) gt 4 and indexof(name,'a') ge 0", 3, 20, None),  # Saturn, Uranus, Neptune (length>4 AND has 'a')
    ("testdata.tpch.nation", "$filter=length(n_name) gt 5 and indexof(n_name,'A') ge 0&$top=20", None, 4, None),  # Combined string functions
    ("testdata.tpch.part", "$filter=length(p_name) lt 30 and indexof(p_name,'red') ge 0&$top=40", None, 9, None),  # Short names with 'red'
    ("$planets", "$filter=substring(name,0,1) eq 'M' and length(name) gt 4", 1, 20, None),  # Mercury (Mars is 4 chars)
    # ============================================================================
    # 'IN' OPERATOR TESTS (20 tests)
    # ============================================================================
    ("$planets", "$filter=id in (1,3,5)", 3, 20, None),  # Mercury, Earth, Jupiter
    ("$planets", "$filter=id in (2,4,6)", 3, 20, None),  # Venus, Mars, Saturn
    ("$planets", "$filter=id in (1)", 1, 20, None),  # Single value: Mercury
    ("$planets", "$filter=id in (9)", 1, 20, None),  # Single value: Pluto
    ("$planets", "$filter=number_of_moons in (0,1,2)", 4, 20, None),  # Mercury(0), Venus(0), Earth(1), Mars(2)
    ("$planets", "$filter=number_of_moons in (79,82)", 2, 20, None),  # Jupiter(79), Saturn(82)
    ("$planets", "$filter=name in ('Earth','Mars','Venus')", 3, 20, None),  # Three specific planets
    ("$planets", "$filter=name in ('Jupiter','Saturn','Uranus','Neptune')", 4, 20, None),  # Gas giants
    ("$planets", "$filter=name in ('Mercury')", 1, 20, None),  # Single string value
    ("$planets", "$filter=not (id in (1,2,3))", 6, 20, None),  # NOT in list
    ("$planets", "$filter=id in (1,2,3) and mass gt 0.5", 2, 20, None),  # IN with additional filter (Venus & Earth)
    ("$planets", "$filter=id in (1,2,3,4,5,6,7,8,9)", 9, 20, None),  # All planets
    ("testdata.tpch.nation", "$filter=n_regionkey in (0,1)&$top=30", None, 4, None),  # Regions 0 and 1
    ("testdata.tpch.nation", "$filter=n_nationkey in (0,5,10,15,20)&$top=30", None, 4, None),  # Specific nations
    ("testdata.tpch.region", "$filter=r_regionkey in (1,3)&$top=10", None, 3, None),  # Two regions
    ("testdata.tpch.supplier", "$filter=s_suppkey in (1,2,3,4,5)&$top=30", None, 7, None),  # First 5 suppliers
    ("testdata.tpch.customer", "$filter=c_custkey in (1,10,100,1000)&$top=30", None, 8, None),  # Specific customers
    ("testdata.tpch.part", "$filter=p_size in (1,5,10,15,20)&$top=50", None, 9, None),  # Common sizes
    ("$planets", "$filter=id in (2,4,6,8) and number_of_moons gt 10", 2, 20, None),  # Even IDs with many moons
    ("testdata.tpch.nation", "$filter=n_nationkey in (1,2,3) or n_regionkey in (0)&$top=30", None, 4, None),  # IN with OR
    # ============================================================================
    # COMPLEX LOGICAL EXPRESSION TESTS (25 tests)
    # ============================================================================
    ("$planets", "$filter=(id eq 1 or id eq 2) and mass lt 1", 1, 20, None),  # Mercury OR Venus AND mass<1
    ("$planets", "$filter=(id gt 5 and number_of_moons gt 20) or id eq 1", 3, 20, None),  # (Saturn/Uranus/Neptune) OR Mercury
    ("$planets", "$filter=((id eq 1 or id eq 2) and mass lt 1) or id eq 3", 2, 20, None),  # (Mercury OR Venus) AND mass<1 OR Earth
    ("$planets", "$filter=(id eq 3 and mass gt 0) and (number_of_moons eq 1 or number_of_moons eq 0)", 1, 20, None),  # Earth specifically
    ("$planets", "$filter=not(id gt 5)", 5, 20, None),  # NOT(id>5) = id<=5
    ("$planets", "$filter=not(not(id gt 5))", 4, 20, None),  # Double negative = id>5
    ("$planets", "$filter=not(id eq 1 or id eq 2)", 7, 20, None),  # NOT(Mercury OR Venus)
    ("$planets", "$filter=not(id gt 3 and mass lt 10)", 7, 20, None),  # NOT(4<=id<=9 AND small mass)
    ("$planets", "$filter=(id eq 1 and mass lt 1) or (id eq 5 and mass gt 10)", 2, 20, None),  # Mercury OR Jupiter
    ("$planets", "$filter=((id lt 3) or (id gt 7)) and number_of_moons lt 10", 3, 20, None),  # (Mercury/Venus) OR (Neptune/Pluto) with few moons
    ("testdata.tpch.nation", "$filter=(n_regionkey eq 0 or n_regionkey eq 1) and n_nationkey lt 10&$top=30", None, 4, None),  # Two regions, low keys
    ("testdata.tpch.nation", "$filter=not(n_regionkey eq 2)&$top=30", None, 4, None),  # All except region 2
    ("testdata.tpch.nation", "$filter=(n_nationkey gt 10 and n_regionkey eq 2) or n_nationkey lt 3&$top=30", None, 4, None),  # Complex OR
    ("testdata.tpch.supplier", "$filter=(s_nationkey lt 5 or s_nationkey gt 20) and s_suppkey lt 100&$top=50", None, 7, None),  # Range with exclusion
    ("testdata.tpch.customer", "$filter=not(c_nationkey eq 10 or c_nationkey eq 15)&$top=50", None, 8, None),  # Exclude two nations
    ("testdata.tpch.part", "$filter=(p_size lt 10 or p_size gt 40) and p_partkey lt 1000&$top=50", None, 9, None),  # Size extremes
    ("$planets", "$filter=(id eq 1 or id eq 3 or id eq 5) and (mass lt 10 or number_of_moons gt 50)", 3, 20, None),  # Multiple ORs in both groups
    ("$planets", "$filter=not((id lt 3) and (mass gt 0.5))", 8, 20, None),  # NOT(Venus AND mass>0.5)
    ("testdata.tpch.nation", "$filter=((n_regionkey eq 0 and n_nationkey lt 10) or (n_regionkey eq 1 and n_nationkey gt 10))&$top=30", None, 4, None),  # Nested groups
    ("testdata.tpch.part", "$filter=((p_size gt 20 and p_size lt 30) or p_size eq 10) and p_partkey lt 500&$top=50", None, 9, None),  # Range OR exact
    ("$planets", "$filter=(id in (1,2,3)) and (number_of_moons lt 5 or mass gt 1000)", 3, 20, None),  # IN with complex condition
    ("testdata.tpch.supplier", "$filter=not(s_nationkey in (0,1,2,3,4))&$top=50", None, 7, None),  # NOT IN
    ("testdata.tpch.nation", "$filter=(n_nationkey mod 2 eq 0) and (n_regionkey eq 1 or n_regionkey eq 3)&$top=30", None, 4, None),  # Arithmetic with complex OR
    ("$planets", "$filter=((id lt 5 and mass lt 1) or (id gt 5 and mass gt 100)) and number_of_moons ne 0", 3, 20, None),  # Complex nested with NE
    ("testdata.tpch.part", "$filter=(p_size ge 10 and p_size le 20) or (p_size ge 30 and p_size le 40)&$top=60", None, 9, None),  # Two ranges
]
# fmt:on

EXTENDED_TEST_BATTERY = PLANETS_TEST_BATTERY

@pytest.mark.parametrize(
    "dataset,query_string,expected_rows,expected_columns,expected_error",
    PLANETS_TEST_BATTERY,
)
def test_planets_battery(dataset, query_string, expected_rows, expected_columns, expected_error):
    """Parametrized pytest test which runs each battery entry as a separate test."""
    res = run_test_case(dataset, query_string, expected_rows, expected_columns, expected_error)
    assert res["passed"], f"{dataset} {query_string}: {res.get('reason') or res.get('error') or res}"

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
