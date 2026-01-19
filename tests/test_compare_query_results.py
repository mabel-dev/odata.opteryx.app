"""Compare SQL and OData query results for failing tests"""

import os
import sys

import opteryx
from opteryx.planner.logical_planner import do_logical_planning_phase
from opteryx.third_party import sqloxide

from app.odata.odata_to_logical_plan import ODataLogicalPlanBuilder

sys.path.insert(1, os.path.join(sys.path[0], "../opteryx-core"))


def execute_sql(sql):
    """Execute SQL and return results"""
    parsed = sqloxide.parse_sql(sql, _dialect="opteryx")
    logical_plan, ast, ctes = do_logical_planning_phase(parsed[0])

    # Execute the plan
    result = opteryx.execute_logical_plan(logical_plan)
    return result


def execute_odata(dataset, filter_expr=None, select_cols=None, orderby=None, top=None):
    """Execute OData query and return results"""
    builder = ODataLogicalPlanBuilder(dataset)
    plan = builder.build(
        filter_expr=filter_expr, select_cols=select_cols, orderby_expr=orderby, top=top
    )

    result = opteryx.execute_logical_plan(plan)
    return result


def compare_results(test_name, sql, odata_params):
    """Compare SQL and OData results"""
    print(f"\n{'=' * 80}")
    print(f"{test_name}")
    print(f"{'=' * 80}")
    print(f"SQL: {sql}")
    print(f"OData: {odata_params}")
    print()

    try:
        sql_result = execute_sql(sql)
        sql_data = sql_result.to_pydict()
        sql_rows_count = len(sql_data[list(sql_data.keys())[0]])  # Get length from first column
        sql_cols = list(sql_data.keys())

        print(f"SQL Result: {sql_rows_count} rows, {len(sql_cols)} columns")
        # Print rows row-by-row
        for i in range(min(10, sql_rows_count)):
            row = [sql_data[col][i] for col in sql_cols]
            print(f"  {row}")
        if sql_rows_count > 10:
            print(f"  ... ({sql_rows_count - 10} more rows)")
    except Exception as e:
        print(f"SQL Error: {e}")
        sql_rows_count = 0
        sql_cols = []

    try:
        odata_result = execute_odata(**odata_params)
        odata_data = odata_result.to_pydict()
        odata_rows_count = len(
            odata_data[list(odata_data.keys())[0]]
        )  # Get length from first column
        odata_cols = list(odata_data.keys())

        print(f"\nOData Result: {odata_rows_count} rows, {len(odata_cols)} columns")
        # Print rows row-by-row
        for i in range(min(10, odata_rows_count)):
            row = [odata_data[col][i] for col in odata_cols]
            print(f"  {row}")
        if odata_rows_count > 10:
            print(f"  ... ({odata_rows_count - 10} more rows)")
    except Exception as e:
        print(f"OData Error: {e}")
        odata_rows_count = 0
        odata_cols = []

    # Compare
    if sql_rows_count == odata_rows_count:
        print(f"\n✓ Row counts match: {sql_rows_count}")
    else:
        print(f"\n✗ Row count mismatch: SQL={sql_rows_count}, OData={odata_rows_count}")

    if len(sql_cols) == len(odata_cols):
        print(f"✓ Column counts match: {len(sql_cols)}")
    else:
        print(f"✗ Column count mismatch: SQL={len(sql_cols)}, OData={len(odata_cols)}")


# Test #63: distance_from_sun < 300 with $select and $top=5
compare_results(
    "Test #63: distance_from_sun < 300 (with select, top 5)",
    "SELECT id, name, distance_from_sun FROM $planets WHERE distance_from_sun < 300 LIMIT 5",
    {
        "dataset": "$planets",
        "filter_expr": "distance_from_sun lt 300",
        "select_cols": "id,name,distance_from_sun",
        "top": 5,
    },
)

# Test #64: mean_temperature > 0 with $select and $top=5
compare_results(
    "Test #64: mean_temperature > 0 (with select, top 5)",
    "SELECT id, name, mean_temperature FROM $planets WHERE mean_temperature > 0 LIMIT 5",
    {
        "dataset": "$planets",
        "filter_expr": "mean_temperature gt 0",
        "select_cols": "id,name,mean_temperature",
        "top": 5,
    },
)

# Test #85: gravity > 10
compare_results(
    "Test #85: gravity > 10 (top 10)",
    "SELECT * FROM $planets WHERE gravity > 10 LIMIT 10",
    {"dataset": "$planets", "filter_expr": "gravity gt 10", "top": 10},
)

# Test #86: gravity < 5
compare_results(
    "Test #86: gravity < 5 (top 10)",
    "SELECT * FROM $planets WHERE gravity < 5 LIMIT 10",
    {"dataset": "$planets", "filter_expr": "gravity lt 5", "top": 10},
)
