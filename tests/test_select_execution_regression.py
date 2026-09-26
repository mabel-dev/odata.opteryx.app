"""Regression tests for $select driving the real execution path.

These call execute_odata_query -- the same entry point the HTTP layer uses --
against the built-in $planets dataset, so they need no catalog or credentials
and can run anywhere.

Guards against the regression where the OData plan builder wrote the Project
node's passthrough columns under the pre-0.9.44 name (`order_by_columns`).
Because the (since deleted) attribute-bag LogicalPlanNode returned None for
unset attributes, the stale write was accepted silently, `passthrough_columns` stayed None, and the
binder raised TypeError -- surfacing as a blanket HTTP 500 on every $select.
"""

import json

import pytest

from app.routes.v4.interface import execute_odata_query

TABLE = "$planets"


def _rows(result):
    """Decode the pre-serialized JSON array execute_odata_query returns."""
    return json.loads(result["value_bytes"])


def test_select_alone():
    result = execute_odata_query(table_name=TABLE, select_cols="id,name", top=3)

    assert result["col_names"] == ["id", "name"]
    assert result["row_count"] == 3
    rows = _rows(result)
    assert len(rows) == 3
    assert all(set(row) == {"id", "name"} for row in rows)


def test_select_single_column():
    result = execute_odata_query(table_name=TABLE, select_cols="name", top=2)

    assert result["col_names"] == ["name"]
    assert [row["name"] for row in _rows(result)] == ["Mercury", "Venus"]


def test_select_with_filter():
    result = execute_odata_query(
        table_name=TABLE, select_cols="id,name", filter_expr="id gt 5", top=3
    )

    assert result["col_names"] == ["id", "name"]
    assert all(row["id"] > 5 for row in _rows(result))


def test_select_with_orderby_on_selected_column():
    result = execute_odata_query(
        table_name=TABLE, select_cols="id,name", orderby_expr="id desc", top=3
    )

    assert result["col_names"] == ["id", "name"]
    ids = [row["id"] for row in _rows(result)]
    assert ids == sorted(ids, reverse=True)


def test_select_with_orderby_on_unselected_column():
    """$orderby outside $select is what populates Project.passthrough_columns.

    This is the shape that broke; ordering must still be correct.
    """
    result = execute_odata_query(
        table_name=TABLE, select_cols="name", orderby_expr="gravity desc", top=4
    )

    rows = _rows(result)
    assert len(rows) == 4
    assert "name" in result["col_names"]
    # Jupiter has the highest gravity in $planets.
    assert rows[0]["name"] == "Jupiter"


def test_select_with_top_zero():
    result = execute_odata_query(
        table_name=TABLE, select_cols="id,name", top=0, count_requested=True
    )

    assert result["row_count"] == 0
    assert _rows(result) == []


def test_select_wildcard():
    result = execute_odata_query(table_name=TABLE, select_cols="*", top=2)

    assert result["row_count"] == 2
    col_names = result["col_names"]
    assert "id" in col_names and "name" in col_names
    assert len(col_names) > 2, "wildcard should return the full schema"


def test_select_with_count():
    result = execute_odata_query(
        table_name=TABLE, select_cols="id,name", top=3, count_requested=True
    )

    assert result["col_names"] == ["id", "name"]
    assert result["row_count"] == 3
    assert result["total_count"] == 9


def test_apply_with_select():
    """$apply builds an Aggregate node; $select then projects its output.

    Layering $select over an aggregate used to lose the aggregate's alias -- the
    same $apply without $select returned ['orbital_eccentricity', 'cnt'], but
    with $select it returned ['orbital_eccentricity', 'COUNT(*)']. Now that Exit
    names the $select columns instead of expanding a wildcard, the requested
    alias survives.
    """
    result = execute_odata_query(
        table_name=TABLE,
        apply_expr="groupby((orbital_eccentricity),aggregate($count as cnt))",
        select_cols="orbital_eccentricity,cnt",
    )

    assert result["col_names"] == ["orbital_eccentricity", "cnt"], result["col_names"]
    assert result["row_count"] > 0
    assert len(_rows(result)) == result["row_count"]


def test_select_unknown_column_is_not_a_500():
    """A bad column name must surface as a query error, not an internal error."""
    with pytest.raises(Exception) as exc_info:
        execute_odata_query(table_name=TABLE, select_cols="no_such_column", top=1)

    # TypeError is the signature of the passthrough_columns regression; a
    # genuinely unknown column should raise a column-resolution error instead.
    assert not isinstance(exc_info.value, TypeError)
