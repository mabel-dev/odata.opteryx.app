"""Compare OData ORDER BY to Opteryx baseline"""

from app.odata.odata_to_logical_plan import ODataLogicalPlanBuilder

plan = ODataLogicalPlanBuilder("$planets").build(
    select_cols="id,name", orderby_expr="id desc", top=3
)

print("OData plan nodes:")
for nid in plan.nodes():
    node = plan[nid]
    print(f"  {node.node_type}")
    if "Order" in str(node.node_type):
        print(f"    order_by: {node.order_by}")
        if node.order_by:
            col, direction = node.order_by[0]
            print("    Order column 0:")
            print(f"      Type: {type(col).__name__}")
            print(f"      node_type: {col.node_type}")
            print(f"      source_column: {getattr(col, 'source_column', 'MISSING')}")
            print(f"      query_column: {getattr(col, 'query_column', 'MISSING')}")
            print(f"      alias: {getattr(col, 'alias', 'MISSING')}")
            print(f"      direction: {direction}")
