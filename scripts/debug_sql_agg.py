#!/usr/bin/env python3
from opteryx.planner.logical_planner import do_logical_planning_phase
from opteryx.third_party import sqloxide

sql = "SELECT COUNT(*) FROM $planets"
parsed = sqloxide.parse_sql(sql, _dialect="opteryx")
logical_plan, ast, ctes = do_logical_planning_phase(parsed[0])

# find aggregate node
agg_node = None
for nid in logical_plan.nodes():
    node = logical_plan[nid]
    if getattr(node, "node_type", None) and "Aggregate" in str(node.node_type):
        agg_node = node
        break

print("agg_node:", agg_node)
print("attrs:", [a for a in dir(agg_node) if not a.startswith("_")])
if hasattr(agg_node, "aggregates"):
    for i, item in enumerate(agg_node.aggregates):
        print("Item", i, "type", type(item))
        print("dir item:", [x for x in dir(item) if not x.startswith("_")])
        for attr in [
            "value",
            "parameters",
            "node_type",
            "function",
            "alias",
            "expression",
            "query_column",
            "name",
        ]:
            print(" ", attr, getattr(item, attr, None))
        try:
            print("  __dict__", item.__dict__)
        except Exception as e:
            print("  __dict__ error", e)
