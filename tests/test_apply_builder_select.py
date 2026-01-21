#!/usr/bin/env python3
"""Tests for $select semantics after aggregation."""

import pytest

from app.odata.odata_to_logical_plan import ODataLogicalPlanBuilder


def test_select_allowed_after_groupby():
    builder = ODataLogicalPlanBuilder("public.github.events")
    builder.current_step_id = builder._add_scan_node()
    # Create a groupby with aggregate using internal builder path
    builder._add_apply_nodes("groupby((region), aggregate($count as total))")

    # Selecting a valid column (group column and aggregate alias) should succeed
    builder.current_step_id = builder._add_project_node("region,total")
    # If no exception, pass
    assert True


def test_select_invalid_after_groupby_raises():
    builder = ODataLogicalPlanBuilder("public.github.events")
    builder.current_step_id = builder._add_scan_node()
    builder._add_apply_nodes("groupby((region), aggregate($count as total))")

    with pytest.raises(ValueError):
        builder._add_project_node("region,not_allowed")
