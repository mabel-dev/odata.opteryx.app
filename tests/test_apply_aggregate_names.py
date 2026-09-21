#!/usr/bin/env python3
"""Every $apply aggregate method must map to an aggregator the engine registers.

The bug this guards against: the builder used to derive the engine's aggregator
name with `method.upper()`. Four of the five methods the parser can emit happen
to uppercase to the engine's own spelling, so COUNT/SUM/MIN/MAX worked and hid
the fact that the derivation was a coincidence rather than a mapping. OData's
"average" is the engine's "AVG", so `average` produced an unregistered
"AVERAGE" aggregator and a 500 -- the one outcome that is wrong either way,
since a shape that cannot be spelled correctly is supposed to be declined.

These tests are name-level, not execution-level, so they run everywhere: they
check the builder's output against opteryx's own AGGREGATORS registry.
"""

import pytest

from opteryx.operators.aggregate.helpers import AGGREGATORS

from app.odata.apply_builder import _METHOD_TO_AGGREGATOR
from app.odata.apply_builder import ApplyLogicalPlanBuilder
from app.odata.odata_to_logical_plan import ODataLogicalPlanBuilder
from odata_query.apply import ApplyParser


def _aggregates_for(apply_expr: str):
    """Build a plan for `apply_expr` and return the aggregate nodes it produced."""
    builder = ODataLogicalPlanBuilder("public.astronomy.planets")
    builder.current_step_id = builder._add_scan_node()
    ApplyLogicalPlanBuilder().apply_pipeline_to_builder(builder, apply_expr)

    aggregates = []
    for node in builder.plan._nodes.values():
        node_type = getattr(node, "node_type", None)
        if node_type is not None and node_type.name in ("Aggregate", "AggregateAndGroup"):
            aggregates.extend(node.aggregates or [])
    return aggregates


def test_every_mapped_aggregator_is_registered_by_the_engine():
    """The map must not name an aggregator opteryx doesn't have."""
    for method, aggregator in _METHOD_TO_AGGREGATOR.items():
        assert aggregator in AGGREGATORS, (
            f"$apply method '{method}' maps to '{aggregator}', "
            f"which is not in opteryx's AGGREGATORS registry"
        )


def test_every_parseable_method_is_mapped():
    """Anything the parser accepts must have an entry -- no method may fall through.

    Pinned to the parser's own list so that adding a method there without
    adding a mapping here fails, rather than reaching the engine unmapped.
    """
    parseable = set(ApplyParser.VALID_METHODS) | {"count"}
    assert parseable <= set(_METHOD_TO_AGGREGATOR), (
        f"unmapped $apply aggregate methods: {sorted(parseable - set(_METHOD_TO_AGGREGATOR))}"
    )


@pytest.mark.parametrize(
    "method, expected",
    [("sum", "SUM"), ("average", "AVG"), ("min", "MIN"), ("max", "MAX")],
)
def test_grouped_aggregate_uses_the_engine_name(method, expected):
    aggregates = _aggregates_for(
        f"groupby((id),aggregate(number_of_moons with {method} as v))"
    )
    assert len(aggregates) == 1
    assert aggregates[0].value == expected
    assert aggregates[0].value in AGGREGATORS
    assert aggregates[0].alias == "v"


@pytest.mark.parametrize(
    "method, expected",
    [("sum", "SUM"), ("average", "AVG"), ("min", "MIN"), ("max", "MAX")],
)
def test_ungrouped_aggregate_uses_the_engine_name(method, expected):
    """The AggregateTransform branch is a second call site -- cover it too."""
    aggregates = _aggregates_for(f"aggregate(number_of_moons with {method} as v)")
    assert len(aggregates) == 1
    assert aggregates[0].value == expected
    assert aggregates[0].value in AGGREGATORS


def test_count_still_maps_to_count():
    aggregates = _aggregates_for("groupby((id),aggregate($count as total))")
    assert len(aggregates) == 1
    assert aggregates[0].value == "COUNT"
    assert aggregates[0].value in AGGREGATORS


def test_unmapped_method_raises_valueerror_not_keyerror():
    """A method with no mapping must be declined (-> 400), never mistranslated.

    ValueError specifically: `_add_apply_nodes` catches ValueError to turn a bad
    $apply into a 400, and a KeyError would sail past it into a 500.
    """
    from app.odata.apply_builder import _aggregator_for

    with pytest.raises(ValueError, match="stddev"):
        _aggregator_for("stddev")
