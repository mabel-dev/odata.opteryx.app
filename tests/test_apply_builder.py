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
            if node.groups and node.groups[0].source_column == "region":
                assert len(node.aggregates) == 1
                assert node.aggregates[0].value == "COUNT"
                assert node.aggregates[0].alias == "total"
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


@pytest.mark.parametrize(
    "apply_expr",
    [
        # The production failure: $countdistinct is not an aggregate method.
        "groupby((domain), aggregate($countdistinct(client) as DeviceCount))",
        # `count` as a `with` method -- rejected by the parser.
        "groupby((status), aggregate(status with count as StatusCount))",
        # SQL-style function-call aggregates.
        "groupby((vendor), aggregate(sum(amount) as Total))",
        "aggregate(count(*) as Total)",
    ],
)
def test_malformed_apply_raises_valueerror_not_parse_error(apply_expr):
    """ApplyParseError derives from Exception, not ValueError, so an unconverted
    one escapes the route's `except ValueError` and becomes a 500. These must
    surface as ValueError -- the route maps that to a 400 the caller can act on.
    """
    builder = ODataLogicalPlanBuilder("public.github.events")
    builder.current_step_id = builder._add_scan_node()

    with pytest.raises(ValueError) as excinfo:
        builder._add_apply_nodes(apply_expr)

    # The parser's own message names the offending construct; without it the 400
    # says only "invalid" and the caller cannot tell what to change.
    assert apply_expr in str(excinfo.value)
    assert str(excinfo.value) != f"Invalid $apply expression: {apply_expr}"


def test_distinct_count_pipeline_is_supported():
    """The documented replacement for a distinct count: deduplicate with an
    aggregate-less groupby, then count that result."""
    builder = ODataLogicalPlanBuilder("public.github.events")
    builder.current_step_id = builder._add_scan_node()

    apl_builder = ApplyLogicalPlanBuilder()
    apl_builder.apply_pipeline_to_builder(
        builder, "groupby((domain,client))/groupby((domain), aggregate($count as DeviceCount))"
    )

    grouped = [
        node
        for node in builder.plan._nodes.values()
        if getattr(getattr(node, "node_type", None), "name", None) == "AggregateAndGroup"
    ]
    assert len(grouped) == 2
    dedupe, counted = grouped[0], grouped[1]
    assert [c.source_column for c in dedupe.groups] == ["domain", "client"]
    assert dedupe.aggregates == []
    assert [c.source_column for c in counted.groups] == ["domain"]
    assert counted.aggregates[0].value == "COUNT"
    assert counted.aggregates[0].alias == "DeviceCount"
