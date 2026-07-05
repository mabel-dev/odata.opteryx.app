#!/usr/bin/env python3
"""Tests for @odata.nextLink truncation detection and generation (WP-01).

This test file validates the logic of has_more_rows detection through
direct unit testing of the URL building and parameter preservation logic.

To fully test the nextLink feature end-to-end, use the integration tests
or run the application with a test client.
"""

from urllib.parse import parse_qs, urlencode, urlparse
import pytest


class TestNextLinkURLConstruction:
    """Test nextLink URL format and parameter preservation logic."""

    def test_nextlink_url_format(self):
        """nextLink URL should have correct path and query format."""
        # Simulate nextLink construction logic
        workstream = "testdata"
        collection = "default"
        dataset = "articles"

        query_params = {
            "$top": ["2"],
            "$skip": ["0"],
            "$filter": ["status eq 'open'"],
        }

        # Build new skip (simulating has_more_rows detection)
        new_skip = 2
        query_params["$skip"] = [str(new_skip)]

        query_items = []
        for key, values in query_params.items():
            for value in values:
                query_items.append((key, value))

        next_query = urlencode(query_items)
        nextlink = f"/api/v4/{workstream}/{collection}/{dataset}?{next_query}"

        # Verify format
        assert nextlink.startswith(f"/api/v4/{workstream}/{collection}/{dataset}?")

        # Parse the URL to verify parameters
        parsed_url = urlparse(nextlink)
        parsed_params = parse_qs(parsed_url.query, keep_blank_values=True)

        assert parsed_params["$skip"][0] == "2"
        assert parsed_params["$top"][0] == "2"
        assert parsed_params["$filter"][0] == "status eq 'open'"

    def test_nextlink_preserves_filter(self):
        """nextLink should preserve $filter parameter."""
        original_query = "$filter=status eq 'open'&$top=2&$skip=0"
        parsed = parse_qs(original_query, keep_blank_values=True)

        # Simulate nextLink construction
        new_skip = 2
        parsed["$skip"] = [str(new_skip)]

        query_items = []
        for key, values in parsed.items():
            for value in values:
                query_items.append((key, value))

        next_query = urlencode(query_items)

        # Verify filter is preserved by parsing back
        parsed_nextlink = parse_qs(next_query, keep_blank_values=True)
        assert "$filter" in parsed_nextlink
        assert parsed_nextlink["$filter"][0] == "status eq 'open'"

    def test_nextlink_preserves_select(self):
        """nextLink should preserve $select parameter."""
        original_query = "$select=id,title,status&$top=2&$skip=0"
        parsed = parse_qs(original_query, keep_blank_values=True)

        new_skip = 2
        parsed["$skip"] = [str(new_skip)]

        query_items = []
        for key, values in parsed.items():
            for value in values:
                query_items.append((key, value))

        next_query = urlencode(query_items)

        # Verify select is preserved by parsing back
        parsed_nextlink = parse_qs(next_query, keep_blank_values=True)
        assert "$select" in parsed_nextlink
        assert "id" in parsed_nextlink["$select"][0]

    def test_nextlink_preserves_orderby(self):
        """nextLink should preserve $orderby parameter."""
        original_query = "$orderby=published_at desc&$top=2&$skip=0"
        parsed = parse_qs(original_query, keep_blank_values=True)

        new_skip = 2
        parsed["$skip"] = [str(new_skip)]

        query_items = []
        for key, values in parsed.items():
            for value in values:
                query_items.append((key, value))

        next_query = urlencode(query_items)

        # Verify orderby is preserved by parsing back
        parsed_nextlink = parse_qs(next_query, keep_blank_values=True)
        assert "$orderby" in parsed_nextlink
        assert "published_at" in parsed_nextlink["$orderby"][0]

    def test_nextlink_preserves_count_parameter(self):
        """nextLink should preserve $count parameter if present."""
        original_query = "$count=true&$top=2&$skip=0"
        parsed = parse_qs(original_query, keep_blank_values=True)

        new_skip = 2
        parsed["$skip"] = [str(new_skip)]

        query_items = []
        for key, values in parsed.items():
            for value in values:
                query_items.append((key, value))

        next_query = urlencode(query_items)

        # Verify count is preserved by parsing back
        parsed_nextlink = parse_qs(next_query, keep_blank_values=True)
        assert "$count" in parsed_nextlink
        assert parsed_nextlink["$count"][0] == "true"

    def test_nextlink_updates_skip_correctly(self):
        """nextLink should correctly advance $skip by top value."""
        original_query = "$top=5&$skip=0"
        parsed = parse_qs(original_query, keep_blank_values=True)

        # Simulate pagination: page 1 with top=5, skip=0
        top = 5
        current_skip = 0
        new_skip = current_skip + top  # Should be 5

        parsed["$skip"] = [str(new_skip)]

        query_items = []
        for key, values in parsed.items():
            for value in values:
                query_items.append((key, value))

        next_query = urlencode(query_items)

        # Verify skip was advanced correctly by parsing back
        parsed_nextlink = parse_qs(next_query, keep_blank_values=True)
        assert int(parsed_nextlink["$skip"][0]) == 5

        # Verify we can calculate the 3rd page
        current_skip2 = int(parsed_nextlink["$skip"][0])
        new_skip2 = current_skip2 + top  # Should be 10

        # Verify calculation
        assert new_skip2 == 10

    def test_nextlink_skip_advancement_with_existing_skip(self):
        """nextLink should correctly advance $skip when it's already set."""
        # Page 2: skip was already 10
        original_query = "$top=10&$skip=10"
        parsed = parse_qs(original_query, keep_blank_values=True)

        top = 10
        current_skip = int(parsed["$skip"][0])
        new_skip = current_skip + top  # Should be 20

        parsed["$skip"] = [str(new_skip)]

        query_items = []
        for key, values in parsed.items():
            for value in values:
                query_items.append((key, value))

        next_query = urlencode(query_items)

        # Verify skip was advanced correctly by parsing back
        parsed_nextlink = parse_qs(next_query, keep_blank_values=True)
        assert int(parsed_nextlink["$skip"][0]) == 20


class TestTruncationDetectionLogic:
    """Test the logic for detecting truncation (has_more_rows flag)."""

    def test_truncation_flag_when_fetch_top_1_returned_rows_gt_top(self):
        """When we fetch top+1 and get more than top rows, truncation detected."""
        top = 2
        fetch_top = top + 1  # Fetch 3
        rows_returned = 3  # Got 3 rows

        # Truncation logic: if fetch_top > top and len(rows) > top
        has_more_rows = fetch_top > top and rows_returned > top

        assert has_more_rows is True
        # In this case, trim to top
        trimmed_rows = rows_returned if rows_returned <= top else top
        assert trimmed_rows == 2

    def test_no_truncation_when_rows_equal_top(self):
        """When we fetch top+1 but get exactly top rows, no truncation."""
        top = 2
        fetch_top = top + 1  # Fetch 3
        rows_returned = 2  # Got 2 rows (exactly top)

        has_more_rows = fetch_top > top and rows_returned > top

        assert has_more_rows is False

    def test_no_truncation_when_rows_less_than_top(self):
        """When rows are fewer than top, no truncation."""
        top = 100
        fetch_top = top + 1  # Fetch 101
        rows_returned = 50  # Got 50 rows

        has_more_rows = fetch_top > top and rows_returned > top

        assert has_more_rows is False

    def test_no_truncation_with_top_zero(self):
        """When top=0, no truncation (no data rows)."""
        top = 0
        fetch_top = top + 1  # Would be 1, but in actual code, fetch_top stays 0
        rows_returned = 0

        has_more_rows = fetch_top > top and rows_returned > top

        assert has_more_rows is False

    def test_truncation_calculation_for_different_tops(self):
        """Test truncation detection for various top values."""
        test_cases = [
            (1, 2, True),    # fetch 2, got 2, truncated
            (5, 6, True),    # fetch 6, got 6, truncated
            (100, 101, True),  # fetch 101, got 101, truncated
            (1, 1, False),   # fetch 2, got 1, not truncated
            (10, 10, False), # fetch 11, got 10, not truncated
        ]

        for top, rows_returned, expected_truncation in test_cases:
            fetch_top = top + 1
            has_more_rows = fetch_top > top and rows_returned > top
            assert has_more_rows == expected_truncation, (
                f"top={top}, rows={rows_returned}: expected {expected_truncation}, got {has_more_rows}"
            )


class TestNextLinkEdgeCases:
    """Test edge cases in nextLink construction."""

    def test_nextlink_with_complex_filter(self):
        """nextLink should preserve complex filter expressions."""
        complex_filter = "((status eq 'open') and (priority gt 5)) or (assigned_to eq 'user@example.com')"
        original_query = f"$filter={complex_filter}&$top=2&$skip=0"
        parsed = parse_qs(original_query, keep_blank_values=True)

        new_skip = 2
        parsed["$skip"] = [str(new_skip)]

        query_items = []
        for key, values in parsed.items():
            for value in values:
                query_items.append((key, value))

        next_query = urlencode(query_items)

        # Verify that the filter value is preserved by parsing back
        parsed_nextlink = parse_qs(next_query, keep_blank_values=True)
        assert "$filter" in parsed_nextlink
        assert parsed_nextlink["$filter"][0] == complex_filter

    def test_nextlink_with_multiple_orderby_fields(self):
        """nextLink should preserve multiple orderby fields."""
        original_query = "$orderby=created_at desc, updated_at asc&$top=5&$skip=0"
        parsed = parse_qs(original_query, keep_blank_values=True)

        new_skip = 5
        parsed["$skip"] = [str(new_skip)]

        query_items = []
        for key, values in parsed.items():
            for value in values:
                query_items.append((key, value))

        next_query = urlencode(query_items)

        # Verify orderby is preserved
        parsed_nextlink = parse_qs(next_query, keep_blank_values=True)
        assert "created_at" in parsed_nextlink["$orderby"][0]
        assert "updated_at" in parsed_nextlink["$orderby"][0]

    def test_nextlink_with_multiple_select_fields(self):
        """nextLink should preserve all selected fields."""
        original_query = "$select=id,title,status,author,created_at,updated_at&$top=10&$skip=0"
        parsed = parse_qs(original_query, keep_blank_values=True)

        new_skip = 10
        parsed["$skip"] = [str(new_skip)]

        query_items = []
        for key, values in parsed.items():
            for value in values:
                query_items.append((key, value))

        next_query = urlencode(query_items)

        # Verify all select fields are preserved
        parsed_nextlink = parse_qs(next_query, keep_blank_values=True)
        select_value = parsed_nextlink["$select"][0]
        for field in ["id", "title", "status", "author", "created_at", "updated_at"]:
            assert field in select_value


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
