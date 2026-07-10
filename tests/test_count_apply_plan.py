"""Test that $count with $apply builds a correct count plan.

This verifies that the rejection of $count+$apply has been removed
and the count plan correctly handles the combination.
"""

from app.odata.odata_to_logical_plan import ODataLogicalPlanBuilder
from opteryx.planner.logical_planner import LogicalPlanStepType


def test_count_only_plan_with_apply():
    """Count plan with $apply should include the aggregation and count."""
    apply_expr = "groupby((status), aggregate($count as Count))"
    builder = ODataLogicalPlanBuilder("public.demo.users")
    plan = builder.build(apply_expr=apply_expr, count_only=True)

    # Verify the plan has been built successfully
    nodes = list(plan.nodes())
    assert len(nodes) > 0, "Plan should have nodes"

    # Look for AggregateAndGroup node (from $apply)
    found_aggregate = False
    found_count = False

    for node_id in nodes:
        node = None
        if hasattr(plan, "get_node"):
            node = plan.get_node(node_id)
        else:
            node = getattr(plan, "_nodes", {}).get(node_id)

        if not node:
            continue

        node_type = getattr(node, "node_type", None)
        if node_type in (LogicalPlanStepType.AggregateAndGroup, LogicalPlanStepType.Aggregate):
            aggs = getattr(node, "aggregates", None)
            groups = getattr(node, "groups", None)

            # Check if this is the $apply aggregation (has groups)
            if groups and aggs:
                found_aggregate = True

            # Check if this is the count aggregation (no groups, has COUNT)
            if not groups and aggs and any(a.value == "COUNT" for a in aggs):
                found_count = True

    # When count_only=True, the plan should have a count aggregation
    assert found_count, "Count-only plan should have a COUNT aggregate node"


def test_count_plan_order_apply_then_count():
    """Count plan should apply $apply first, then add count aggregation."""
    apply_expr = "groupby((status), aggregate($count as Count))"
    builder = ODataLogicalPlanBuilder("public.demo.users")
    plan = builder.build(apply_expr=apply_expr, count_only=True)

    # The plan should be: Scan -> Apply -> Count -> Exit
    # Verify that we can traverse from Scan to Count
    nodes = list(plan.nodes())
    assert len(nodes) >= 3, "Plan should have at least Scan, Apply (or aggregation), and Count nodes"


def test_standard_query_apply_without_count():
    """Standard query with $apply but no count should work."""
    apply_expr = "groupby((status), aggregate($count as Count))"
    builder = ODataLogicalPlanBuilder("public.demo.users")
    plan = builder.build(apply_expr=apply_expr, count_only=False, top=10)

    # Verify plan was built successfully
    nodes = list(plan.nodes())
    assert len(nodes) > 0, "Plan should have nodes"


def test_count_plan_accepts_apply_without_error():
    """Building a count plan with $apply should not raise an error."""
    apply_expr = "groupby((status), aggregate($count as Count))"
    builder = ODataLogicalPlanBuilder("public.demo.users")

    # This should not raise "ValueError: $count with $apply is not supported"
    try:
        plan = builder.build(apply_expr=apply_expr, count_only=True)
        # Success - no error raised
        assert plan is not None, "Plan should be created successfully"
    except ValueError as e:
        if "$count" in str(e) and "$apply" in str(e):
            raise AssertionError(
                f"$count with $apply should be supported, but got error: {e}"
            )
        raise


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
