"""Compare Opteryx SQL parser baseline for ORDER BY"""

import os
import sys

from opteryx.planner.logical_planner import do_logical_planning_phase
from opteryx.third_party import sqloxide

sys.path.insert(1, os.path.join(sys.path[0], "../opteryx-core"))

# Parse SQL with ORDER BY
sql = "SELECT id, name FROM $planets ORDER BY id DESC LIMIT 3"
parsed = sqloxide.parse_sql(sql, _dialect="opteryx")
plan, ast, ctes = do_logical_planning_phase(parsed[0])

print("Opteryx SQL plan nodes:")
for nid in plan.nodes():
    node = plan[nid]
    print(f"  {node.node_type}")
    if "Order" in str(node.node_type):
        print(f"    order_by: {node.order_by}")
        if node.order_by:
            for i, (col, direction) in enumerate(node.order_by):
                print(f"    Order column {i}:")
                print(f"      Type: {type(col).__name__}")
                print(f"      node_type: {col.node_type}")
                print(f"      source_column: {getattr(col, 'source_column', 'MISSING')}")
                print(f"      query_column: {getattr(col, 'query_column', 'MISSING')}")
                print(f"      alias: {getattr(col, 'alias', 'MISSING')}")
                print(f"      direction: {direction}")
