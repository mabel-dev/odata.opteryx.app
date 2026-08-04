"""Tests for the $top=0&$count=true path (the "schema_count_split" strategy).

That path reports the result's column names and its total count without
returning rows. When $select names the columns, they are known up front and no
query is needed to discover them; otherwise a one-row query establishes the
shape, concurrently with the count.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.routes.v4.interface import execute_odata_query

TABLE = "$planets"
PLANET_COUNT = 9
FULL_SCHEMA_WIDTH = 20


def test_explicit_select_determines_columns():
    """$select names the response columns, in the caller's order."""
    result = execute_odata_query(
        table_name=TABLE, top=0, count_requested=True, select_cols="name,id"
    )

    assert result["col_names"] == ["name", "id"]
    assert result["total_count"] == PLANET_COUNT
    assert result["value_bytes"] == b"[]"
    assert result["row_count"] == 0


def test_wildcard_select_reports_full_schema():
    result = execute_odata_query(table_name=TABLE, top=0, count_requested=True, select_cols="*")

    assert len(result["col_names"]) == FULL_SCHEMA_WIDTH
    assert result["total_count"] == PLANET_COUNT


def test_no_select_reports_full_schema():
    result = execute_odata_query(table_name=TABLE, top=0, count_requested=True)

    assert len(result["col_names"]) == FULL_SCHEMA_WIDTH
    assert result["total_count"] == PLANET_COUNT
    assert result["row_count"] == 0


def test_count_reflects_filter():
    result = execute_odata_query(
        table_name=TABLE, top=0, count_requested=True, filter_expr="id gt 6"
    )

    assert result["total_count"] == 3
    assert result["row_count"] == 0


def test_select_shortcut_agrees_with_the_queried_shape():
    """The no-query $select answer must match what the row query actually returns."""
    counted = execute_odata_query(
        table_name=TABLE, top=0, count_requested=True, select_cols="name,gravity"
    )
    fetched = execute_odata_query(table_name=TABLE, top=1, select_cols="name,gravity")

    assert counted["col_names"] == fetched["col_names"]


def test_apply_reports_aggregate_columns():
    """$apply output columns aren't in the catalog, so the shape query still runs."""
    result = execute_odata_query(
        table_name=TABLE,
        top=0,
        count_requested=True,
        apply_expr="groupby((number_of_moons),aggregate($count as cnt))",
    )

    assert result["col_names"] == ["number_of_moons", "cnt"]
