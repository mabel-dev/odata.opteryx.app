#!/usr/bin/env python3
"""Tests for ordering by aggregate aliases and group-by columns."""

from app.odata.odata_to_logical_plan import ODataLogicalPlanBuilder
from opteryx.planner.logical_planner import LogicalPlanStepType


def test_orderby_on_aggregate_alias():
    builder = ODataLogicalPlanBuilder("public.github.events")
    builder.current_step_id = builder._add_scan_node()

    builder._add_apply_nodes("groupby((region), aggregate($count as total))")

    # Add ORDER BY using aggregate alias
    order_step = builder._add_order_node("total desc")

    # Find the Order node in the plan
    order_node = builder.plan[order_step]
    assert order_node is not None
    assert getattr(order_node, "node_type", None) == LogicalPlanStepType.Order

    # Check order_by entries
    order_by = getattr(order_node, "order_by", [])
    assert len(order_by) == 1
    col_node, is_asc, _nulls_first = order_by[0]
    assert col_node.query_column == "total"
    assert is_asc is False


def test_orderby_on_groupby_column():
    builder = ODataLogicalPlanBuilder("public.github.events")
    builder.current_step_id = builder._add_scan_node()

    builder._add_apply_nodes("groupby((region), aggregate($count as total))")

    order_step = builder._add_order_node("region asc")
    order_node = builder.plan[order_step]

    assert order_node is not None
    order_by = getattr(order_node, "order_by", [])
    assert len(order_by) == 1
    col_node, is_asc, _nulls_first = order_by[0]
    assert col_node.query_column == "region"
    assert is_asc is True
