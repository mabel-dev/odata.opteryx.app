#!/usr/bin/env python3
"""Test that actually builds a logical plan and prints it"""

import os
import sys

from app.odata.odata_to_logical_plan import ODataLogicalPlanBuilder
from opteryx.planner.logical_planner import LogicalPlanStepType

sys.path.insert(1, os.path.join(sys.path[0], "../opteryx-catalog"))
sys.path.insert(1, os.path.join(sys.path[0], "../opteryx-core"))
sys.path.insert(0, os.path.join(sys.path[0], "app"))


def print_plan(description, select_cols=None, orderby_expr=None, top=None, skip=None):
    """Build and print a logical plan"""
    print(f"\n{'=' * 80}")
    print(f"{description}")
    print(f"  select_cols={select_cols}, orderby={orderby_expr}, top={top}, skip={skip}")
    print(f"{'=' * 80}")

    try:
        builder = ODataLogicalPlanBuilder("$planets")
        plan = builder.build(
            filter_expr=None,
            select_cols=select_cols,
            orderby_expr=orderby_expr,
            top=top,
            skip=skip,
            apply_expr=None,
        )

        # Print each node
        for step_id, node in plan.nodes.items():
            node_type = node.node_type if hasattr(node, "node_type") else "UNKNOWN"
            print(f"\n[{step_id}] {node_type}")

            # Print relevant properties
            for attr in ["columns", "order_by", "limit", "offset", "relation"]:
                if hasattr(node, attr):
                    val = getattr(node, attr)
                    if val is not None:
                        print(f"  {attr}: {val}")
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback

        traceback.print_exc()


# Test cases
print_plan("SELECT *")

print_plan("SELECT id, name", select_cols="id,name")

print_plan("SELECT * ORDER BY id DESC", orderby_expr="id desc")

print_plan("SELECT id, name ORDER BY id DESC", select_cols="id,name", orderby_expr="id desc")

print_plan("SELECT * LIMIT 3", top=3)

print_plan("SELECT id, name LIMIT 3", select_cols="id,name", top=3)

print_plan(
    "SELECT id, name ORDER BY id DESC LIMIT 3", select_cols="id,name", orderby_expr="id desc", top=3
)


def test_apply_aggregate_only_plan():
    builder = ODataLogicalPlanBuilder("public.github.events")
    plan = builder.build(
        # OData v4 $apply aggregate syntax is "<property> with <method> as <alias>";
        # min(...)/max(...) function-call form is not part of the spec grammar.
        apply_expr=(
            "aggregate(published_at with min as min_published_at, "
            "published_at with max as max_published_at)"
        )
    )

    found = False
    for node in plan._nodes.values():
        if getattr(node, "node_type", None) == LogicalPlanStepType.Aggregate:
            # No group by for aggregate-only
            assert node.groups == []
            assert len(node.aggregates) == 2
            func_names = {agg.value for agg in node.aggregates}
            assert "MIN" in func_names and "MAX" in func_names
            aliases = {agg.alias for agg in node.aggregates}
            assert "min_published_at" in aliases and "max_published_at" in aliases
            found = True

    assert found
