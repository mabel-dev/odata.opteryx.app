"""Phase 4 Execution Tests: End-to-End Query Execution

Tests that complete OData queries execute successfully through Opteryx's
optimizer and executor, returning correct results.
"""

import os
import sys
from pathlib import Path

import opteryx

from app.odata.odata_to_logical_plan import build_logical_plan

# Add opteryx-core to path (for execute_logical_plan support)
sys.path.insert(1, os.path.join(sys.path[0], "../opteryx-core"))

# Add app directory to path
sys.path.insert(0, str(Path(__file__).parent / "app"))


def execute_plan(plan):
    """Execute a logical plan through Opteryx optimizer and executor.

    The module-level ``opteryx.execute_logical_plan`` no longer exists; execution
    goes through ``Session.execute_logical_plan``, which returns a
    ``(results_generator, ResultType)`` tuple of Draken Morsels rather than a
    materialized table. Mirror what app.routes.v4.interface does so these tests
    exercise the same API production uses.

    Returns:
        Tuple of (column_names, rows) where rows is a list of dicts.

    Raises:
        Whatever the planner/binder/executor raises -- callers assert on success.
    """
    from opteryx.constants import ResultType

    session = opteryx.session()
    results_generator, result_type = session.execute_logical_plan(plan, connection=session)

    if result_type != ResultType.TABULAR:
        return [], []

    column_names = None
    rows = []
    for morsel in results_generator:
        morsel.materialize()
        names = [n.decode("utf-8") if isinstance(n, bytes) else n for n in morsel.column_names]
        if column_names is None:
            column_names = names
        columns = {name: morsel.column(name).to_pylist() for name in names}
        for i in range(morsel.num_rows):
            rows.append({name: columns[name][i] for name in names})

    return column_names or [], rows


def test_4_1_simple_query():
    """Test 4.1: Simple query execution

    Query: SELECT * FROM $planets
    Expected: Should execute successfully and return rows
    """
    print("\nTest 4.1: Simple SELECT * FROM $planets")
    print("-" * 80)

    plan = build_logical_plan(
        table_name="$planets",
        filter_expr=None,
        select_cols=None,
        orderby_expr=None,
        top=None,
        skip=None,
    )

    print("  Building plan...")
    print(f"    Nodes: {len(list(plan.nodes()))}")

    print("  Executing plan...")
    col_names, rows = execute_plan(plan)
    print(f"    Rows returned: {len(rows)}, columns: {col_names}")

    assert len(rows) > 0, "SELECT * FROM $planets returned no rows"


def test_4_2_query_with_filter():
    """Test 4.2: Query with filter

    Query: SELECT * FROM $planets WHERE id = 1
    Expected: Should return 1 row
    """
    print("\nTest 4.2: SELECT * FROM $planets WHERE id = 1")
    print("-" * 80)

    plan = build_logical_plan(
        table_name="$planets",
        filter_expr="id eq 1",
        select_cols=None,
        orderby_expr=None,
        top=None,
        skip=None,
    )

    print("  Executing plan...")
    _, rows = execute_plan(plan)
    print(f"    Rows returned: {len(rows)}")

    assert len(rows) == 1, f"Expected 1 row for 'id eq 1', got {len(rows)}"


def test_4_3_query_with_orderby():
    """Test 4.3: Query with ORDER BY

    Query: SELECT * FROM $planets ORDER BY id ASC
    Expected: Should return rows in ascending ID order
    """
    print("\nTest 4.3: SELECT * FROM $planets ORDER BY id ASC")
    print("-" * 80)

    plan = build_logical_plan(
        table_name="$planets",
        filter_expr=None,
        select_cols=None,
        orderby_expr="id asc",
        top=None,
        skip=None,
    )

    print("  Executing plan...")
    _, rows = execute_plan(plan)
    print(f"    Rows returned: {len(rows)}")

    assert len(rows) > 0, "ORDER BY query returned no rows"
    ids = [row["id"] for row in rows]
    assert ids == sorted(ids), f"Rows not in ascending id order: {ids}"


def test_4_4_query_with_limit():
    """Test 4.4: Query with LIMIT

    Query: SELECT * FROM $planets LIMIT 5
    Expected: Should return exactly 5 rows
    """
    print("\nTest 4.4: SELECT * FROM $planets TOP 5")
    print("-" * 80)

    plan = build_logical_plan(
        table_name="$planets",
        filter_expr=None,
        select_cols=None,
        orderby_expr=None,
        top=5,
        skip=None,
    )

    print("  Executing plan...")
    _, rows = execute_plan(plan)
    print(f"    Rows returned: {len(rows)}")

    assert len(rows) == 5, f"Expected 5 rows for top=5, got {len(rows)}"


def test_4_5_complex_query():
    """Test 4.5: Complex query

    Query: SELECT * FROM $planets WHERE id > 1 ORDER BY name DESC LIMIT 3
    Expected: Should execute and return 3 rows
    """
    print("\nTest 4.5: Complex query (filter + order + limit)")
    print("-" * 80)

    plan = build_logical_plan(
        table_name="$planets",
        filter_expr="id gt 1",
        select_cols=None,
        orderby_expr="name desc",
        top=3,
        skip=None,
    )

    print("  Executing plan...")
    _, rows = execute_plan(plan)
    print(f"    Rows returned: {len(rows)}")

    assert len(rows) == 3, f"Expected 3 rows for filter+order+top=3, got {len(rows)}"


def test_4_6_query_with_select():
    """Test 4.6: Query with column projection

    Query: SELECT id, name FROM $planets
    Expected: Should return rows with only 2 columns
    """
    print("\nTest 4.6: SELECT id, name FROM $planets")
    print("-" * 80)

    plan = build_logical_plan(
        table_name="$planets",
        filter_expr=None,
        select_cols="id,name",
        orderby_expr=None,
        top=None,
        skip=None,
    )

    print("  Executing plan...")
    col_names, rows = execute_plan(plan)
    print(f"    Rows returned: {len(rows)}, columns: {col_names}")

    assert len(rows) > 0, "$select query returned no rows"
    assert col_names == ["id", "name"], f"Expected columns ['id', 'name'], got {col_names}"


def main():
    """Run all Phase 4 execution tests."""
    print("=" * 80)
    print("Phase 4: Execution Tests - End-to-End Query Execution")
    print("=" * 80)

    tests = [
        ("4.1", test_4_1_simple_query),
        ("4.2", test_4_2_query_with_filter),
        ("4.3", test_4_3_query_with_orderby),
        ("4.4", test_4_4_query_with_limit),
        ("4.5", test_4_5_complex_query),
        ("4.6", test_4_6_query_with_select),
    ]

    # The test functions assert rather than return a status; a test "passes"
    # exactly when it returns without raising.
    results = []
    for test_num, test_func in tests:
        try:
            test_func()
            results.append((test_num, True))
        except Exception as e:
            print(f"\n  ✗✗ FAILED test {test_num}: {e}")
            import traceback

            traceback.print_exc()
            results.append((test_num, False))

    # Summary
    print("\n" + "=" * 80)
    passed = sum(1 for _, result in results if result)
    total = len(results)
    print(f"Results: {passed}/{total} passed ({100 * passed // total}%)")

    if passed == total:
        print("✓✓ Phase 4 COMPLETE: All execution tests passing!")
    else:
        print("Some tests failed:")
        for test_num, result in results:
            status = "✓" if result else "✗"
            print(f"  {status} Test {test_num}")

    print("=" * 80)


if __name__ == "__main__":
    main()
