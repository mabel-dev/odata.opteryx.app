"""Compare startswith function to LIKE operator in Opteryx"""

import os
import sys

from opteryx.planner.logical_planner import do_logical_planning_phase
from opteryx.third_party import sqloxide

from app.odata.odata_to_logical_plan import ODataLogicalPlanBuilder

sys.path.insert(1, os.path.join(sys.path[0], "../opteryx-core"))


# Test SQL with LIKE
sql_like = "SELECT * FROM $planets WHERE name LIKE 'M%'"
parsed = sqloxide.parse_sql(sql_like, _dialect="opteryx")
sql_plan, ast, ctes = do_logical_planning_phase(parsed[0])

# Test SQL with STARTS_WITH function
sql_func = "SELECT * FROM $planets WHERE STARTS_WITH(name, 'M')"
parsed2 = sqloxide.parse_sql(sql_func, _dialect="opteryx")
sql_plan2, ast2, ctes2 = do_logical_planning_phase(parsed2[0])

# Test OData startswith
odata_plan = ODataLogicalPlanBuilder("$planets").build(filter_expr="startswith(name,'M')")

print("SQL LIKE 'M%' Filter condition:")
print("=" * 80)
for nid in sql_plan.nodes():
    node = sql_plan[nid]
    if "Filter" in str(node.node_type):
        print(f"Filter condition: {node.condition}")
        print(f"  node_type: {node.condition.node_type}")
        print(f"  value: {getattr(node.condition, 'value', 'N/A')}")
        if hasattr(node.condition, "left"):
            print(f"  left: {node.condition.left}")
            print(f"  right: {node.condition.right}")

print("\nSQL STARTS_WITH(name, 'M') Filter condition:")
print("=" * 80)
for nid in sql_plan2.nodes():
    node = sql_plan2[nid]
    if "Filter" in str(node.node_type):
        print(f"Filter condition: {node.condition}")
        print(f"  node_type: {node.condition.node_type}")
        print(f"  value: {getattr(node.condition, 'value', 'N/A')}")
        if hasattr(node.condition, "parameters"):
            print(f"  parameters: {node.condition.parameters}")

print("\nOData startswith(name,'M') Filter condition:")
print("=" * 80)
for nid in odata_plan.nodes():
    node = odata_plan[nid]
    if "Filter" in str(node.node_type):
        print(f"Filter condition: {node.condition}")
        print(f"  node_type: {node.condition.node_type}")
        print(f"  value: {getattr(node.condition, 'value', 'N/A')}")
        if hasattr(node.condition, "parameters"):
            print(f"  parameters: {node.condition.parameters}")
