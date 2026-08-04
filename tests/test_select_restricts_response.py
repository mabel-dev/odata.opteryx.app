"""$select must restrict the properties in the response, and honour their order.

OData v4.01 Part 1 §11.2.4.1: $select specifies the properties returned. Two
things went wrong before the Exit node listed the projected columns explicitly:

  1. An $orderby column outside $select leaked into the response. It reaches the
     Project node as a passthrough column -- which is correct, the sort needs it
     -- but a WILDCARD Exit then expanded everything the Project node carried,
     passthrough included, straight into the payload.
  2. The response followed schema order rather than the order the caller asked
     for, because wildcard expansion walks the schema. JSON member order is not
     semantically significant (RFC 8259 §4), but consumers and BI tools surface
     it as column order, so the requested order is the least surprising one.

These run through execute_odata_query -- the same entry point the HTTP layer
uses -- against the built-in $planets dataset, so they need no catalog or
credentials. Assertions are on the exact key list, not a subset: a subset check
is what let the leak go unnoticed.
"""

import json

from app.routes.v4.interface import execute_odata_query

TABLE = "$planets"

# $planets in schema order. Used to assert the *absence* of a restriction.
FULL_SCHEMA = [
    "id",
    "name",
    "mass",
    "diameter",
    "density",
    "gravity",
    "escape_velocity",
    "rotation_period",
    "length_of_day",
    "distance_from_sun",
    "perihelion",
    "aphelion",
    "orbital_period",
    "orbital_velocity",
    "orbital_inclination",
    "orbital_eccentricity",
    "obliquity_to_orbit",
    "mean_temperature",
    "surface_pressure",
    "number_of_moons",
]


def _rows(result):
    """Decode the pre-serialized JSON array execute_odata_query returns."""
    return json.loads(result["value_bytes"])


def _keys(result):
    """The exact per-row key list, asserted to be identical across rows."""
    rows = _rows(result)
    assert rows, "expected at least one row to inspect"
    first = list(rows[0])
    assert all(list(row) == first for row in rows), "rows disagree on their keys"
    return first


# ---------------------------------------------------------------------------
# 1. The passthrough leak
# ---------------------------------------------------------------------------


def test_orderby_column_outside_select_is_not_returned():
    """The reported case: $select=name&$orderby=gravity desc returned gravity too."""
    result = execute_odata_query(
        table_name=TABLE, select_cols="name", orderby_expr="gravity desc", top=1
    )

    assert result["col_names"] == ["name"], result["col_names"]
    assert _keys(result) == ["name"]


def test_multiple_orderby_columns_outside_select_are_not_returned():
    """Every passthrough column has to be dropped, not just the first."""
    result = execute_odata_query(
        table_name=TABLE,
        select_cols="name",
        orderby_expr="number_of_moons desc, gravity asc, id desc",
        top=3,
    )

    assert result["col_names"] == ["name"], result["col_names"]
    assert _keys(result) == ["name"]


def test_partially_overlapping_orderby_returns_only_selected():
    """One ordering column is selected, one is not -- only the selected one shows."""
    result = execute_odata_query(
        table_name=TABLE,
        select_cols="name,gravity",
        orderby_expr="gravity desc, mass asc",
        top=3,
    )

    assert result["col_names"] == ["name", "gravity"], result["col_names"]
    assert _keys(result) == ["name", "gravity"]


def test_filter_column_outside_select_is_not_returned():
    """$filter never widened the projection, but assert it stays that way."""
    result = execute_odata_query(
        table_name=TABLE, select_cols="name", filter_expr="gravity gt 5", top=2
    )

    assert result["col_names"] == ["name"], result["col_names"]
    assert _keys(result) == ["name"]


# ---------------------------------------------------------------------------
# 2. Requested column order
# ---------------------------------------------------------------------------


def test_select_order_is_honoured_when_it_matches_the_schema():
    result = execute_odata_query(table_name=TABLE, select_cols="name,gravity", top=1)

    assert result["col_names"] == ["name", "gravity"], result["col_names"]
    assert _keys(result) == ["name", "gravity"]


def test_select_order_is_honoured_when_it_reverses_the_schema():
    """The mirror image of the test above -- these two used to be identical."""
    result = execute_odata_query(table_name=TABLE, select_cols="gravity,name", top=1)

    assert result["col_names"] == ["gravity", "name"], result["col_names"]
    assert _keys(result) == ["gravity", "name"]


def test_select_order_is_honoured_with_orderby():
    """Requested order survives the Order node and its passthrough column."""
    result = execute_odata_query(
        table_name=TABLE,
        select_cols="number_of_moons,name,id",
        orderby_expr="gravity desc",
        top=2,
    )

    assert result["col_names"] == ["number_of_moons", "name", "id"], result["col_names"]
    assert _keys(result) == ["number_of_moons", "name", "id"]


# ---------------------------------------------------------------------------
# 3. No-regression: the cases that were already correct
# ---------------------------------------------------------------------------


def test_no_select_returns_every_column():
    result = execute_odata_query(table_name=TABLE, top=1)

    assert result["col_names"] == FULL_SCHEMA, result["col_names"]
    assert _keys(result) == FULL_SCHEMA


def test_wildcard_select_returns_every_column():
    result = execute_odata_query(table_name=TABLE, select_cols="*", top=1)

    assert result["col_names"] == FULL_SCHEMA, result["col_names"]
    assert _keys(result) == FULL_SCHEMA


def test_orderby_without_select_returns_every_column_sorted():
    result = execute_odata_query(table_name=TABLE, orderby_expr="gravity desc")

    assert result["col_names"] == FULL_SCHEMA, result["col_names"]
    assert _keys(result) == FULL_SCHEMA

    gravities = [row["gravity"] for row in _rows(result)]
    assert gravities == sorted(gravities, reverse=True), gravities


def test_sorting_is_still_correct_when_ordering_by_an_unselected_column():
    """The point of passthrough_columns: drop the column from the output, not the sort.

    The expected order is taken from the same query without a $select, which can
    still read gravity -- so this asserts the projected query agrees with the
    unprojected one rather than re-deriving the order from $planets by hand.
    """
    unprojected = _rows(execute_odata_query(table_name=TABLE, orderby_expr="gravity desc"))
    expected = [row["name"] for row in unprojected]
    assert expected[0] == "Jupiter", expected  # sanity-check the reference order

    projected = execute_odata_query(
        table_name=TABLE, select_cols="name", orderby_expr="gravity desc"
    )

    assert projected["col_names"] == ["name"], projected["col_names"]
    assert [row["name"] for row in _rows(projected)] == expected


def test_sorting_is_still_correct_across_a_page_boundary():
    """$skip/$top on top of a projected sort still walks the same order."""
    unprojected = _rows(execute_odata_query(table_name=TABLE, orderby_expr="gravity desc"))
    expected = [row["name"] for row in unprojected][2:5]

    page = execute_odata_query(
        table_name=TABLE, select_cols="name", orderby_expr="gravity desc", skip=2, top=3
    )

    assert page["col_names"] == ["name"], page["col_names"]
    assert [row["name"] for row in _rows(page)] == expected


def test_apply_output_shape_is_unchanged_without_select():
    """$apply with no $select still returns the group keys and aggregate aliases."""
    result = execute_odata_query(
        table_name=TABLE, apply_expr="groupby((number_of_moons),aggregate($count as cnt))"
    )

    assert result["col_names"] == ["number_of_moons", "cnt"], result["col_names"]
    assert _keys(result) == ["number_of_moons", "cnt"]
    assert sum(row["cnt"] for row in _rows(result)) == 9  # every planet counted once


def test_apply_with_select_restricts_to_the_requested_properties():
    """$select over an aggregate restricts and orders like any other $select."""
    result = execute_odata_query(
        table_name=TABLE,
        apply_expr="groupby((number_of_moons),aggregate($count as cnt))",
        select_cols="cnt,number_of_moons",
    )

    assert result["col_names"] == ["cnt", "number_of_moons"], result["col_names"]
    assert _keys(result) == ["cnt", "number_of_moons"]


def test_count_only_shape_is_unchanged():
    """$count rides the _pending_aggregate_columns path, not the $select one."""
    result = execute_odata_query(
        table_name=TABLE, select_cols="name", top=0, count_requested=True
    )

    assert result["total_count"] == 9
    assert result["row_count"] == 0
