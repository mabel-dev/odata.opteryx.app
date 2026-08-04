"""Test $select + $orderby combination.

This is the case that produces a Project node with non-empty passthrough_columns
(the ORDER BY column is not in the projection), so it exercises the field that
opteryx-core renamed from `order_by_columns` in 0.9.44.
"""

import os
import sys

from app.odata.odata_to_logical_plan import ODataLogicalPlanBuilder
from tests.test_phase_4_execution import execute_plan

sys.path.insert(1, os.path.join(sys.path[0], "../opteryx-core"))


def test_select_orderby_combo():
    """Test case: $select=id,name&$orderby=id desc&$top=3"""

    builder = ODataLogicalPlanBuilder(table_name="$planets")
    plan = builder.build(select_cols="id,name", orderby_expr="id desc", top=3)

    col_names, rows = execute_plan(plan)

    assert len(rows) == 3, f"Expected 3 rows, got {len(rows)}"
    assert set(col_names) >= {"id", "name"}, f"Expected id and name in {col_names}"

    ids = [row["id"] for row in rows]
    assert ids == sorted(ids, reverse=True), f"Rows not in descending id order: {ids}"


def test_orderby_column_not_in_select():
    """$orderby on a column outside $select populates Project.passthrough_columns.

    This is the exact shape that regressed: the ordering column has to survive
    the projection so the Order node can use it.
    """
    builder = ODataLogicalPlanBuilder(table_name="$planets")
    plan = builder.build(select_cols="name", orderby_expr="gravity desc", top=4)

    project_node = next(
        plan[node_id]
        for node_id in plan.nodes()
        if str(plan[node_id].node_type).endswith("Project")
    )
    passthrough = project_node.passthrough_columns
    assert passthrough is not None, "passthrough_columns is None -- stale field name?"
    assert [c.source_column for c in passthrough] == ["gravity"]

    _, rows = execute_plan(plan)
    assert len(rows) == 4, f"Expected 4 rows, got {len(rows)}"

    # NOTE: reading 'gravity' back only works because _add_exit_node uses a
    # WILDCARD, so passthrough columns leak past the projection. Per OData,
    # $select=name should return only 'name'. Tracked as a separate follow-up;
    # tighten this assertion when the Exit node lists columns explicitly.
    gravities = [row["gravity"] for row in rows]
    assert gravities == sorted(gravities, reverse=True), f"Not ordered by gravity: {gravities}"
