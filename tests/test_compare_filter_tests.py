"""Compare failing filter tests to equivalent SQL"""

import os
import sys

from opteryx.planner.logical_planner import do_logical_planning_phase
from opteryx.third_party import sqloxide

from app.odata.odata_to_logical_plan import ODataLogicalPlanBuilder

sys.path.insert(1, os.path.join(sys.path[0], "../opteryx-core"))


def compare_filter(sql, odata_filter, test_name):
    """Compare SQL filter to OData filter"""
    print(f"\n{'=' * 80}")
    print(f"Test: {test_name}")
    print(f"{'=' * 80}")
    print(f"SQL: {sql}")
    print(f"OData: $filter={odata_filter}")
    print()

    # Parse SQL
    parsed = sqloxide.parse_sql(sql, _dialect="opteryx")
    sql_plan, ast, ctes = do_logical_planning_phase(parsed[0])

    # Parse OData
    odata_plan = ODataLogicalPlanBuilder("$planets").build(filter_expr=odata_filter)

    # Find Filter nodes
    sql_filter = None
    for nid in sql_plan.nodes():
        node = sql_plan[nid]
        if "Filter" in str(node.node_type):
            sql_filter = node
            break

    odata_filter_node = None
    for nid in odata_plan.nodes():
        node = odata_plan[nid]
        if "Filter" in str(node.node_type):
            odata_filter_node = node
            break

    if sql_filter and odata_filter_node:
        print("SQL Filter condition:")
        print(f"  {sql_filter.condition}")
        print(f"  node_type: {sql_filter.condition.node_type}")
        print(f"  value: {getattr(sql_filter.condition, 'value', 'N/A')}")
        if hasattr(sql_filter.condition, "left"):
            print(f"  left: {sql_filter.condition.left}")
        if hasattr(sql_filter.condition, "right"):
            print(f"  right: {sql_filter.condition.right}")
        if hasattr(sql_filter.condition, "centre"):
            print(f"  centre: {sql_filter.condition.centre}")

        print("\nOData Filter condition:")
        print(f"  {odata_filter_node.condition}")
        print(f"  node_type: {odata_filter_node.condition.node_type}")
        print(f"  value: {getattr(odata_filter_node.condition, 'value', 'N/A')}")
        if hasattr(odata_filter_node.condition, "left"):
            print(f"  left: {odata_filter_node.condition.left}")
        if hasattr(odata_filter_node.condition, "right"):
            print(f"  right: {odata_filter_node.condition.right}")
        if hasattr(odata_filter_node.condition, "centre"):
            print(f"  centre: {odata_filter_node.condition.centre}")


# Test #48: not(number_of_moons eq 0)
compare_filter(
    "SELECT * FROM $planets WHERE NOT (number_of_moons = 0)",
    "not(number_of_moons eq 0)",
    "Test #48: not function",
)

# Test #88: surface_pressure eq null
compare_filter(
    "SELECT * FROM $planets WHERE surface_pressure IS NULL",
    "surface_pressure eq null",
    "Test #88: null equality",
)

# Test #89: surface_pressure ne null
compare_filter(
    "SELECT * FROM $planets WHERE surface_pressure IS NOT NULL",
    "surface_pressure ne null",
    "Test #89: null inequality",
)

# Test #63: distance_from_sun lt 300 (with projection)
compare_filter(
    "SELECT id, name, distance_from_sun FROM $planets WHERE distance_from_sun < 300",
    "distance_from_sun lt 300",
    "Test #63: numeric comparison lt 300",
)

# Test #64: mean_temperature gt 0 (with projection)
compare_filter(
    "SELECT id, name, mean_temperature FROM $planets WHERE mean_temperature > 0",
    "mean_temperature gt 0",
    "Test #64: numeric comparison gt 0",
)

# Test #85: gravity gt 10
compare_filter(
    "SELECT * FROM $planets WHERE gravity > 10", "gravity gt 10", "Test #85: gravity > 10"
)

# Test #86: gravity lt 5
compare_filter("SELECT * FROM $planets WHERE gravity < 5", "gravity lt 5", "Test #86: gravity < 5")
