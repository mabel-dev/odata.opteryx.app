"""Compare OData plan to equivalent SQL plan"""

import os
import sys

from opteryx.planner.logical_planner import do_logical_planning_phase
from opteryx.third_party import sqloxide

from app.odata.odata_to_logical_plan import ODataLogicalPlanBuilder

sys.path.insert(1, os.path.join(sys.path[0], "../opteryx-core"))


def get_sql_plan(sql: str):
    parsed = sqloxide.parse_sql(sql, _dialect="opteryx")
    logical_plan, ast, ctes = do_logical_planning_phase(parsed[0])
    return logical_plan


# Test case: $select=id,name&$orderby=id desc&$top=3
print("Test: $select=id,name&$orderby=id desc&$top=3")
print("=" * 80)

# SQL equivalent
sql = "SELECT id, name FROM $planets ORDER BY id DESC LIMIT 3"
sql_plan = get_sql_plan(sql)

# OData
odata_plan = ODataLogicalPlanBuilder("$planets").build(
    select_cols="id,name", orderby_expr="id desc", top=3
)

print("\nSQL Plan nodes:")
for nid in sql_plan.nodes():
    node = sql_plan[nid]
    print(f"  {node.node_type}")

print("\nOData Plan nodes:")
for nid in odata_plan.nodes():
    node = odata_plan[nid]
    print(f"  {node.node_type}")

# Compare Order nodes
print("\n" + "=" * 80)
print("Comparing Order nodes:")
print("=" * 80)

sql_order = None
odata_order = None

for nid in sql_plan.nodes():
    node = sql_plan[nid]
    if "Order" in str(node.node_type):
        sql_order = node
        break

for nid in odata_plan.nodes():
    node = odata_plan[nid]
    if "Order" in str(node.node_type):
        odata_order = node
        break

if sql_order and odata_order:
    print("\nSQL Order node:")
    sql_col, sql_dir = sql_order.order_by[0]
    print(f"  Column type: {type(sql_col).__name__}")
    print(f"  node_type: {sql_col.node_type}")
    print(f"  source_column: {sql_col.source_column}")
    print(f"  query_column: {sql_col.query_column}")
    print(f"  direction: {sql_dir}")

    print("\nOData Order node:")
    odata_col, odata_dir = odata_order.order_by[0]
    print(f"  Column type: {type(odata_col).__name__}")
    print(f"  node_type: {odata_col.node_type}")
    print(f"  source_column: {odata_col.source_column}")
    print(f"  query_column: {odata_col.query_column}")
    print(f"  direction: {odata_dir}")

    # Compare
    errors = []
    if type(sql_col).__name__ != type(odata_col).__name__:
        errors.append(
            f"Column type mismatch: {type(sql_col).__name__} vs {type(odata_col).__name__}"
        )
    if sql_col.source_column != odata_col.source_column:
        errors.append(
            f"source_column mismatch: {sql_col.source_column} vs {odata_col.source_column}"
        )
    if sql_col.query_column != odata_col.query_column:
        errors.append(f"query_column mismatch: {sql_col.query_column} vs {odata_col.query_column}")
    if sql_dir != odata_dir:
        errors.append(f"direction mismatch: {sql_dir} vs {odata_dir}")

    if errors:
        print("\n✗ Order node differences:")
        for err in errors:
            print(f"  - {err}")
    else:
        print("\n✓ Order nodes match!")

# Compare Project nodes
print("\n" + "=" * 80)
print("Comparing Project nodes:")
print("=" * 80)

sql_project = None
odata_project = None

for nid in sql_plan.nodes():
    node = sql_plan[nid]
    if "Project" in str(node.node_type):
        sql_project = node
        break

for nid in odata_plan.nodes():
    node = odata_plan[nid]
    if "Project" in str(node.node_type):
        odata_project = node
        break

if sql_project and odata_project:
    print(f"\nSQL Project: {len(sql_project.columns)} columns")
    for i, col in enumerate(sql_project.columns):
        print(f"  [{i}] {type(col).__name__}: {col.source_column} -> {col.query_column}")

    print(f"\nOData Project: {len(odata_project.columns)} columns")
    for i, col in enumerate(odata_project.columns):
        print(f"  [{i}] {type(col).__name__}: {col.source_column} -> {col.query_column}")

    if len(sql_project.columns) != len(odata_project.columns):
        print(
            f"\n✗ Column count mismatch: {len(sql_project.columns)} vs {len(odata_project.columns)}"
        )
    else:
        print("\n✓ Column counts match!")
