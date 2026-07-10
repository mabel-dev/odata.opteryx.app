#!/usr/bin/env python3
"""Test that counting groups via logical-plan count works as expected.

This verifies that building a count plan after an apply/groupby yields
an Aggregate COUNT node and is executed via the plan executor (when
available)."""

import pytest
from app.odata.odata_to_logical_plan import ODataLogicalPlanBuilder
import opteryx as _opteryx


def test_groupby_count_plan_construction():
    builder = ODataLogicalPlanBuilder("public.dataset.table")
    # Build a plan that groups by two columns and then asks for count_only
    count_plan = builder.build(
        apply_expr="groupby((region,category), aggregate($count as total))", count_only=True
    )

    # Walk nodes to ensure there is a COUNT aggregation (over the grouped rows,
    # via a plain Aggregate node stacked on top of the AggregateAndGroup)
    found = False
    for n in count_plan._nodes.values():
        if getattr(n, "node_type", None) and n.node_type.name in ("AggregateAndGroup", "Aggregate"):
            aggs = getattr(n, "aggregates", None)
            if aggs and any(a.value == "COUNT" for a in aggs):
                found = True
    assert found


@pytest.mark.skipif(
    not hasattr(_opteryx, "execute_logical_plan"), reason="Opteryx executor not available"
)
def test_groupby_count_plan_execution():
    # Attempt to execute the count plan (skips if dataset not present or executor missing)
    try:
        res = _opteryx.execute_logical_plan(
            ODataLogicalPlanBuilder("public.github.events").build(
                apply_expr="groupby((region), aggregate($count as total))",
                count_only=True,
            )
        )
    except Exception as exc:
        # Skip if dataset not present
        from pytest import skip

        try:
            if exc.__class__.__name__ == "DatasetNotFoundError" or isinstance(
                exc, getattr(_opteryx, "exceptions").DatasetNotFoundError
            ):
                skip("dataset not available in this environment")
        except Exception:
            pass
        raise

    assert res.num_rows == 1
