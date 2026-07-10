from app.odata.odata_to_logical_plan import ODataLogicalPlanBuilder
from opteryx.planner.logical_planner import LogicalPlanStepType


def test_count_only_plan_has_count_aggregate():
    builder = ODataLogicalPlanBuilder("public.nyc_taxi.2009_yellow_taxi_trips")
    plan = builder.build(filter_expr="vendor eq 'microsoft'", count_only=True)

    # Collect nodes and find an AggregateAndGroup node with COUNT aggregation
    found = False
    for node_id in list(plan.nodes()):
        # Try to retrieve node object via attributes available on LogicalPlan
        node = None
        # Preferred API
        if hasattr(plan, "get_node"):
            node = plan.get_node(node_id)
        else:
            # Fallback to internal storage naming
            node = getattr(plan, "_nodes", {}).get(node_id)

        if not node:
            continue

        if getattr(node, "node_type", None) == LogicalPlanStepType.Aggregate:
            aggs = getattr(node, "aggregates", None)
            assert aggs is not None
            assert any(a.value == "COUNT" for a in aggs)
            found = True
            break

    assert found, "COUNT aggregate node not present in count_only plan"
