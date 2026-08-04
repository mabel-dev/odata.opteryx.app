#!/usr/bin/env python3
"""Unit tests for OData query execution without the auth/HTTP layer.

Exercises the OData -> logical plan -> execution pipeline directly, so there is
no authentication to mock and no HTTP request to make.

These run against `$planets`, an opteryx built-in dataset. That is deliberate:
CI must not reach GCS/Firestore, so nothing here may reference a catalog-backed
dataset such as `public.github.events`. Tests that need live data belong in
tests/manual/ (see its README).

`$planets` has 9 rows and 20 columns; `id` runs 1..9 in schema order, and `name`
is the planet name (Mercury, Venus, Earth, ...).

Usage:
    pytest tests/test_odata_query_unit.py -v
    pytest tests/test_odata_query_unit.py -k "orderby" -v
"""

import json

import pytest

from app.routes.v4.interface import execute_odata_query

TABLE = "$planets"
PLANET_COUNT = 9


def rows_of(result):
    """Decode the pre-serialized JSON array execute_odata_query returns.

    execute_odata_query returns `value_bytes` (raw JSON bytes), not a `rows`
    list -- an earlier version of this file asserted on a "rows" key that the
    function has never returned, so every assertion here was vacuous.
    """
    return json.loads(result["value_bytes"])


class TestODataOrderBy:
    """Test $orderby parameter parsing and execution."""

    def test_orderby_single_column_asc(self):
        """Test ordering by a single column ascending (explicit)."""
        result = execute_odata_query(table_name=TABLE, orderby_expr="id asc", top=3)

        assert result["took_ms"] >= 0
        assert [row["id"] for row in rows_of(result)] == [1, 2, 3]

    def test_orderby_single_column_desc(self):
        """Test ordering by a single column descending."""
        result = execute_odata_query(table_name=TABLE, orderby_expr="id desc", top=3)

        assert [row["id"] for row in rows_of(result)] == [9, 8, 7]

    def test_orderby_single_column_default(self):
        """Test ordering by a single column (default asc)."""
        result = execute_odata_query(table_name=TABLE, orderby_expr="id", top=3)

        assert [row["id"] for row in rows_of(result)] == [1, 2, 3]

    def test_orderby_multiple_columns(self):
        """Test ordering by multiple columns."""
        result = execute_odata_query(
            table_name=TABLE, orderby_expr="number_of_moons desc, name asc", top=5
        )

        rows = rows_of(result)
        moons = [row["number_of_moons"] for row in rows]
        assert moons == sorted(moons, reverse=True)
        # Ties on moon count must fall back to name ascending.
        for earlier, later in zip(rows, rows[1:]):
            if earlier["number_of_moons"] == later["number_of_moons"]:
                assert earlier["name"] <= later["name"]

    def test_orderby_invalid_direction(self):
        """Test that an invalid sort direction is rejected."""
        with pytest.raises(ValueError):
            execute_odata_query(table_name=TABLE, orderby_expr="id invalid", top=1)

    def test_orderby_invalid_column(self):
        """Test that ordering by a nonexistent column raises."""
        with pytest.raises(Exception):
            execute_odata_query(table_name=TABLE, orderby_expr="nonexistent_column desc", top=1)


class TestODataFilter:
    """Test $filter parameter parsing and execution."""

    def test_filter_simple_eq(self):
        """Test simple equality filter."""
        result = execute_odata_query(table_name=TABLE, filter_expr="name eq 'Earth'", top=10)

        rows = rows_of(result)
        assert len(rows) == 1
        assert rows[0]["name"] == "Earth"

    def test_filter_gt(self):
        """Test greater-than filter."""
        result = execute_odata_query(table_name=TABLE, filter_expr="id gt 6", top=10)

        assert [row["id"] for row in rows_of(result)] == [7, 8, 9]

    def test_filter_no_matches_returns_empty(self):
        """A filter matching nothing is a valid empty result, not an error."""
        result = execute_odata_query(table_name=TABLE, filter_expr="id gt 1000", top=10)

        assert rows_of(result) == []
        assert result["row_count"] == 0

    def test_filter_invalid_syntax(self):
        """Test that invalid filter syntax raises."""
        with pytest.raises(Exception):
            execute_odata_query(table_name=TABLE, filter_expr="invalid filter syntax !!!", top=1)


class TestODataSelect:
    """Test $select parameter for column selection."""

    def test_select_specific_columns(self):
        """Only the selected columns are returned, in the projection."""
        result = execute_odata_query(table_name=TABLE, select_cols="id,name", top=1)

        assert result["col_names"] == ["id", "name"]
        assert set(rows_of(result)[0]) == {"id", "name"}

    def test_select_single_column(self):
        """Test selecting a single column."""
        result = execute_odata_query(table_name=TABLE, select_cols="name", top=1)

        assert result["col_names"] == ["name"]
        assert rows_of(result)[0] == {"name": "Mercury"}


class TestODataPaging:
    """Test $top and $skip parameters."""

    def test_top_parameter(self):
        """Test $top parameter limits results."""
        result = execute_odata_query(table_name=TABLE, top=5)

        assert result["row_count"] == 5
        assert len(rows_of(result)) == 5

    def test_top_zero_with_count(self):
        """Test $top=0 returns no rows but still reports the count."""
        result = execute_odata_query(table_name=TABLE, top=0, count_requested=True)

        assert rows_of(result) == []
        assert result["total_count"] == PLANET_COUNT

    def test_skip_parameter(self):
        """Test $skip parameter offsets results."""
        first = execute_odata_query(table_name=TABLE, orderby_expr="id asc", top=3)
        skipped = execute_odata_query(table_name=TABLE, orderby_expr="id asc", top=3, skip=3)

        assert [row["id"] for row in rows_of(first)] == [1, 2, 3]
        assert [row["id"] for row in rows_of(skipped)] == [4, 5, 6]

    def test_has_more_rows_flag(self):
        """Truncated results are flagged; complete ones are not."""
        truncated = execute_odata_query(table_name=TABLE, top=3)
        complete = execute_odata_query(table_name=TABLE, top=PLANET_COUNT)

        assert truncated["has_more_rows"] is True
        assert complete["has_more_rows"] is False


class TestODataCount:
    """Test $count parameter."""

    def test_count_requested(self):
        """Test that count is returned when requested."""
        result = execute_odata_query(table_name=TABLE, top=1, count_requested=True)

        assert result["total_count"] == PLANET_COUNT

    def test_count_not_requested(self):
        """Test that count is None when not requested."""
        result = execute_odata_query(table_name=TABLE, top=1, count_requested=False)

        assert result["total_count"] is None

    def test_count_reflects_filter(self):
        """The count is of matching rows, not of the whole table."""
        result = execute_odata_query(
            table_name=TABLE, filter_expr="id gt 6", top=1, count_requested=True
        )

        assert result["total_count"] == 3


class TestODataCombinations:
    """Test combinations of parameters."""

    def test_filter_orderby_top(self):
        """Test combining $filter, $orderby, and $top."""
        result = execute_odata_query(
            table_name=TABLE, filter_expr="id gt 3", orderby_expr="id desc", top=2
        )

        assert [row["id"] for row in rows_of(result)] == [9, 8]

    def test_filter_select_orderby_count(self):
        """Test combining multiple parameters."""
        result = execute_odata_query(
            table_name=TABLE,
            filter_expr="id gt 3",
            select_cols="id,name",
            orderby_expr="id desc",
            top=5,
            count_requested=True,
        )

        assert result["col_names"] == ["id", "name"]
        assert result["total_count"] == 6
        assert result["took_ms"] >= 0
        assert [row["id"] for row in rows_of(result)] == [9, 8, 7, 6, 5]


class TestODataApply:
    """Test $apply aggregations execution."""

    def test_aggregate_min_max_returns_single_row(self):
        """A bare aggregate collapses the table to one row.

        Note the OData spec form is `<column> with <fn> as <alias>` -- the
        function-call form (`min(id) as min_id`) is not valid $apply syntax.
        """
        result = execute_odata_query(
            table_name=TABLE,
            apply_expr="aggregate(id with min as min_id, id with max as max_id)",
        )

        rows = rows_of(result)
        assert len(rows) == 1
        assert set(result["col_names"]) == {"min_id", "max_id"}
        assert rows[0]["min_id"] == 1
        assert rows[0]["max_id"] == PLANET_COUNT

    def test_groupby_with_count(self):
        """groupby produces one row per distinct group, and keeps the alias."""
        result = execute_odata_query(
            table_name=TABLE,
            apply_expr="groupby((number_of_moons),aggregate($count as cnt))",
        )

        rows = rows_of(result)
        assert result["col_names"] == ["number_of_moons", "cnt"]
        assert len(rows) == len({row["number_of_moons"] for row in rows})
        assert sum(row["cnt"] for row in rows) == PLANET_COUNT


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
