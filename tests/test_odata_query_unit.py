#!/usr/bin/env python3
"""Unit tests for OData query execution without auth/HTTP layer.

This test battery allows testing the OData→logical plan→query conversion
pipeline without needing to mock authentication or make HTTP requests.

Usage:
    pytest test_odata_query_unit.py -v
    pytest test_odata_query_unit.py -k "orderby" -v
"""

import pytest

from app.routes.v4.interface import execute_odata_query


class TestODataOrderBy:
    """Test $orderby parameter parsing and execution."""

    def test_orderby_single_column_asc(self):
        """Test ordering by a single column ascending (explicit)."""
        result = execute_odata_query(
            table_name="public.github.events",
            orderby_expr="published_at asc",
            top=1,
        )
        assert "rows" in result
        assert "col_names" in result
        assert result["took_ms"] >= 0

    def test_orderby_single_column_desc(self):
        """Test ordering by a single column descending."""
        result = execute_odata_query(
            table_name="public.github.events",
            orderby_expr="published_at desc",
            top=1,
        )
        assert "rows" in result
        assert "col_names" in result
        assert result["took_ms"] >= 0

    def test_orderby_single_column_default(self):
        """Test ordering by a single column (default asc)."""
        result = execute_odata_query(
            table_name="public.github.events",
            orderby_expr="published_at",
            top=1,
        )
        assert "rows" in result
        assert "col_names" in result

    def test_orderby_multiple_columns(self):
        """Test ordering by multiple columns."""
        result = execute_odata_query(
            table_name="public.github.events",
            orderby_expr="published_at desc, actor_login asc",
            top=1,
        )
        assert "rows" in result
        assert "col_names" in result

    def test_orderby_invalid_direction(self):
        """Test that invalid sort direction raises ValueError."""
        with pytest.raises(ValueError):
            execute_odata_query(
                table_name="public.github.events",
                orderby_expr="published_at invalid",
                top=1,
            )

    def test_orderby_invalid_column(self):
        """Test that ordering by nonexistent column raises exception."""
        with pytest.raises(Exception):
            execute_odata_query(
                table_name="public.github.events",
                orderby_expr="nonexistent_column desc",
                top=1,
            )


class TestODataFilter:
    """Test $filter parameter parsing and execution."""

    def test_filter_simple_eq(self):
        """Test simple equality filter."""
        result = execute_odata_query(
            table_name="public.github.events",
            filter_expr="actor_login eq 'Copilot'",
            top=10,
        )
        assert "rows" in result
        assert isinstance(result["rows"], list)

    def test_filter_gt(self):
        """Test greater-than filter."""
        result = execute_odata_query(
            table_name="public.github.events",
            filter_expr="some_numeric_field gt 100",
            top=10,
        )
        assert "rows" in result

    def test_filter_invalid_syntax(self):
        """Test that invalid filter syntax raises ValueError."""
        with pytest.raises(Exception):
            execute_odata_query(
                table_name="public.github.events",
                filter_expr="invalid filter syntax !!!",
                top=1,
            )


class TestODataSelect:
    """Test $select parameter for column selection."""

    def test_select_specific_columns(self):
        """Test selecting specific columns."""
        result = execute_odata_query(
            table_name="public.github.events",
            select_cols="actor_login,published_at",
            top=1,
        )
        assert "rows" in result
        assert "col_names" in result
        # Note: validation that only selected columns are returned
        # depends on implementation details

    def test_select_single_column(self):
        """Test selecting a single column."""
        result = execute_odata_query(
            table_name="public.github.events",
            select_cols="actor_login",
            top=1,
        )
        assert "rows" in result


class TestODataPaging:
    """Test $top and $skip parameters."""

    def test_top_parameter(self):
        """Test $top parameter limits results."""
        result = execute_odata_query(
            table_name="public.github.events",
            top=5,
        )
        assert len(result["rows"]) <= 5

    def test_top_zero_with_count(self):
        """Test $top=0 returns no rows but can include count."""
        result = execute_odata_query(
            table_name="public.github.events",
            top=0,
            count_requested=True,
        )
        assert len(result["rows"]) == 0
        # total_count should be available with count_requested=True

    def test_skip_parameter(self):
        """Test $skip parameter offsets results."""
        result1 = execute_odata_query(
            table_name="public.github.events",
            top=5,
        )
        result2 = execute_odata_query(
            table_name="public.github.events",
            top=5,
            skip=5,
        )
        # Results should be different (different rows)
        assert result1["rows"] != result2["rows"]


class TestODataCount:
    """Test $count parameter."""

    def test_count_requested(self):
        """Test that count is returned when requested."""
        result = execute_odata_query(
            table_name="public.github.events",
            top=1,
            count_requested=True,
        )
        assert result["total_count"] is not None
        assert isinstance(result["total_count"], int)

    def test_count_not_requested(self):
        """Test that count is None when not requested."""
        result = execute_odata_query(
            table_name="public.github.events",
            top=1,
            count_requested=False,
        )
        assert result["total_count"] is None


class TestODataCombinations:
    """Test combinations of parameters."""

    def test_filter_orderby_top(self):
        """Test combining $filter, $orderby, and $top."""
        result = execute_odata_query(
            table_name="public.github.events",
            filter_expr="actor_login eq 'Copilot'",
            orderby_expr="published_at desc",
            top=10,
        )
        assert "rows" in result
        assert len(result["rows"]) <= 10

    def test_filter_select_orderby_count(self):
        """Test combining multiple parameters."""
        result = execute_odata_query(
            table_name="public.github.events",
            filter_expr="actor_login eq 'Copilot'",
            select_cols="actor_login,published_at",
            orderby_expr="published_at desc",
            top=5,
            count_requested=True,
        )
        assert "rows" in result
        assert result["total_count"] is not None
        assert result["took_ms"] >= 0


class TestODataApply:
    """Test $apply aggregations execution."""

    def test_aggregate_min_max_returns_single_row(self):
        import opteryx as _opteryx
        import pytest as _pytest

        # Prefer direct SQL path if available (query_to_arrow), otherwise
        # fall back to logical plan path if execute_logical_plan is present.
        if not hasattr(_opteryx, "query_to_arrow") and not hasattr(
            _opteryx, "execute_logical_plan"
        ):
            _pytest.skip(
                "neither opteryx.query_to_arrow nor opteryx.execute_logical_plan available in this environment"
            )

        try:
            result = execute_odata_query(
                table_name="public.github.events",
                apply_expr="aggregate(min(published_at) as min_published_at, max(published_at) as max_published_at)",
            )
        except Exception as exc:
            # If dataset is not present in this environment (local dev), skip this test
            try:
                import opteryx as _opteryx

                if exc.__class__.__name__ == "DatasetNotFoundError" or isinstance(
                    exc, getattr(_opteryx, "exceptions").DatasetNotFoundError
                ):
                    _pytest.skip("dataset not available in this environment")
            except Exception:
                pass
            raise
        # Aggregation should return a single row
        assert isinstance(result["rows"], list)
        assert len(result["rows"]) == 1
        # Column names should include the aggregate aliases
        assert "min_published_at" in result["col_names"] or "min_published_at" in result["rows"][0]
        assert "max_published_at" in result["col_names"] or "max_published_at" in result["rows"][0]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
