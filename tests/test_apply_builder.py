#!/usr/bin/env python3
"""Tests for ApplyLogicalPlanBuilder integration with ODataLogicalPlanBuilder."""

import pytest
from unittest.mock import MagicMock

from app.odata.apply_builder import ApplyLogicalPlanBuilder
from app.odata.odata_to_logical_plan import ODataLogicalPlanBuilder


def test_groupby_with_count_creates_aggregate_node():
    builder = ODataLogicalPlanBuilder("public.github.events")
    # start plan with scan node
    builder.current_step_id = builder._add_scan_node()

    apply_expr = "groupby((region), aggregate($count as total))"

    apl_builder = ApplyLogicalPlanBuilder()
    last_step = apl_builder.apply_pipeline_to_builder(builder, apply_expr)

    # Inspect nodes in the plan to find AggregateAndGroup node
    found = False
    for node in builder.plan._nodes.values():
        if (
            getattr(node, "node_type", None) is not None
            and node.node_type.name == "AggregateAndGroup"
        ):
            # Check grouping and aggregate
            if node.group_by and node.group_by[0].source_column == "region":
                assert len(node.aggregations) == 1
                assert node.aggregations[0]["function"] == "COUNT"
                assert node.aggregations[0]["alias"] == "total"
                found = True

    assert found


def test_filter_in_pipeline_applies_before_groupby():
    builder = ODataLogicalPlanBuilder("public.github.events")
    builder.current_step_id = builder._add_scan_node()

    apply_expr = "filter(region eq 'X')/groupby((region), aggregate($count as total))"

    apl_builder = ApplyLogicalPlanBuilder()
    last_step = apl_builder.apply_pipeline_to_builder(builder, apply_expr)

    nodes = list(builder.plan._nodes.values())
    node_names = [
        getattr(n, "node_type", None).name if getattr(n, "node_type", None) else None for n in nodes
    ]

    assert "Filter" in node_names, "Filter node not present for pipeline filter"
    assert "AggregateAndGroup" in node_names, "Aggregate node not present for groupby"
    assert node_names.index("Filter") < node_names.index("AggregateAndGroup"), (
        "Filter should come before Aggregate"
    )

    # Check the filter node has a condition
    for n in nodes:
        if getattr(n, "node_type", None) and n.node_type.name == "Filter":
            assert getattr(n, "condition", None) is not None, "Filter node missing parsed condition"
            break


def test_unknown_transform_raises_error():
    """Test that unknown $apply transformations raise ValueError, not silently skip."""
    builder = ODataLogicalPlanBuilder("public.test_table")
    builder.current_step_id = builder._add_scan_node()

    # Mock an unknown transform class
    class UnknownTransform:
        pass

    # Create a mock pipeline with the unknown transform
    mock_pipeline = MagicMock()
    mock_pipeline.transformations = [UnknownTransform()]

    apl_builder = ApplyLogicalPlanBuilder()

    # Monkey-patch parse_apply to return our mock pipeline
    import app.odata.apply_builder as apply_builder_module
    original_parse = apply_builder_module.parse_apply

    try:
        apply_builder_module.parse_apply = lambda _: mock_pipeline

        # Verify that an unknown transform raises ValueError with the transform name
        with pytest.raises(ValueError, match="Unsupported \\$apply transformation: UnknownTransform"):
            apl_builder.apply_pipeline_to_builder(builder, "unknown()")
    finally:
        # Restore original parse_apply
        apply_builder_module.parse_apply = original_parse
