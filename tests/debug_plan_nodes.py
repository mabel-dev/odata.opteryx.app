"""Debug script to see what nodes are actually in the plan"""

import sys
from pathlib import Path

from app.odata.odata_to_logical_plan import build_logical_plan

sys.path.insert(0, str(Path(__file__).parent / "app"))


# Test simple SELECT
plan = build_logical_plan(
    table_name="planets", filter_expr=None, select_cols=None, orderby_expr=None, top=None, skip=None
)

print("Simple SELECT * FROM planets:")
print(f"Node count: {len(list(plan.nodes()))}")
print("\nNodes:")
for idx, node_id in enumerate(plan.nodes()):
    node = plan[node_id]
    print(f"  {idx + 1}. {node.node_type.name} (id={node_id})")
