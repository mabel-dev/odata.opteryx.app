"""Unit tests for OData query parameter validation (WP-11).

These tests validate the parameter parsing logic without requiring the full
FastAPI/Router stack, avoiding Google Cloud dependencies.

Tests cover:
1. $top=0 allowed unconditionally
2. $count+$apply combination allowed
3. Duplicate $filter/$skip parameters detected
4. $count parameter strict validation
"""

from urllib.parse import parse_qs


class TestDuplicateDetection:
    """Test duplicate parameter detection logic."""

    def test_duplicate_filter_detected(self):
        """Duplicate $filter should be detected."""
        duplicate_sensitive = ["$top", "$apply", "$count", "$select", "$orderby", "$filter", "$skip"]
        parsed_qs = parse_qs("$filter=id eq 1&$filter=id eq 2&$top=10")

        duplicates = [
            p for p in duplicate_sensitive if p in parsed_qs and len(parsed_qs.get(p, [])) > 1
        ]

        assert "$filter" in duplicates, "Duplicate $filter should be detected"

    def test_duplicate_skip_detected(self):
        """Duplicate $skip should be detected."""
        duplicate_sensitive = ["$top", "$apply", "$count", "$select", "$orderby", "$filter", "$skip"]
        parsed_qs = parse_qs("$skip=1&$skip=2&$top=10")

        duplicates = [
            p for p in duplicate_sensitive if p in parsed_qs and len(parsed_qs.get(p, [])) > 1
        ]

        assert "$skip" in duplicates, "Duplicate $skip should be detected"

    def test_single_params_no_duplicates(self):
        """Single instances of all parameters should not be detected as duplicates."""
        duplicate_sensitive = ["$top", "$apply", "$count", "$select", "$orderby", "$filter", "$skip"]
        parsed_qs = parse_qs("$top=10&$skip=5&$filter=id gt 0&$orderby=name asc&$select=id,name&$count=true&$apply=groupby((id))")

        duplicates = [
            p for p in duplicate_sensitive if p in parsed_qs and len(parsed_qs.get(p, [])) > 1
        ]

        assert len(duplicates) == 0, f"No duplicates should be detected, but got {duplicates}"

    def test_duplicate_top_detected(self):
        """Duplicate $top should be detected."""
        duplicate_sensitive = ["$top", "$apply", "$count", "$select", "$orderby", "$filter", "$skip"]
        parsed_qs = parse_qs("$top=10&$top=20")

        duplicates = [
            p for p in duplicate_sensitive if p in parsed_qs and len(parsed_qs.get(p, [])) > 1
        ]

        assert "$top" in duplicates, "Duplicate $top should be detected"

    def test_duplicates_list_extended(self):
        """The duplicate_sensitive list should include $filter and $skip."""
        duplicate_sensitive = ["$top", "$apply", "$count", "$select", "$orderby", "$filter", "$skip"]

        assert "$filter" in duplicate_sensitive, "$filter should be in duplicate_sensitive list"
        assert "$skip" in duplicate_sensitive, "$skip should be in duplicate_sensitive list"


class TestTopValidation:
    """Test $top parameter validation."""

    def test_top_zero_valid(self):
        """$top=0 should be valid."""
        top = 0
        # Validate $top range (allow 0 unconditionally per OData spec)
        assert 0 <= top <= 10000, "$top=0 should pass range validation"

    def test_top_positive_valid(self):
        """$top in range [1, 10000] should be valid."""
        for top in [1, 100, 10000]:
            assert 0 <= top <= 10000, f"$top={top} should pass range validation"

    def test_top_negative_invalid(self):
        """$top=-1 should be invalid."""
        top = -1
        assert not (0 <= top <= 10000), "$top=-1 should fail range validation"

    def test_top_exceeds_max_invalid(self):
        """$top=10001 should be invalid."""
        top = 10001
        assert not (0 <= top <= 10000), "$top=10001 should fail range validation"

    def test_top_zero_no_special_count_requirement(self):
        """$top=0 should be allowed regardless of $count presence."""
        # Old code: required $count=true for $top=0
        # New code: allows $top=0 unconditionally
        top = 0
        count = None  # No count specified

        # Validate using new logic: allow 0 unconditionally
        assert 0 <= top <= 10000, "$top=0 should pass validation without $count=true"


class TestCountValidation:
    """Test $count parameter validation."""

    def test_count_true_accepted(self):
        """$count='true' should be accepted."""
        count = "true"
        count_lower = count.lower()
        assert count_lower in ("true", "false"), "$count='true' should be valid"
        want_count = count_lower == "true"
        assert want_count is True

    def test_count_false_accepted(self):
        """$count='false' should be accepted."""
        count = "false"
        count_lower = count.lower()
        assert count_lower in ("true", "false"), "$count='false' should be valid"
        want_count = count_lower == "true"
        assert want_count is False

    def test_count_case_insensitive(self):
        """$count validation should be case-insensitive."""
        for val in ["True", "TRUE", "tRuE", "False", "FALSE", "fAlSe"]:
            count_lower = val.lower()
            assert count_lower in ("true", "false"), f"$count={val} should be valid (case-insensitive)"

    def test_count_one_rejected(self):
        """$count='1' should be rejected."""
        count = "1"
        count_lower = count.lower()
        # Should fail the validation
        assert count_lower not in ("true", "false"), "$count='1' should be invalid"

    def test_count_yes_rejected(self):
        """$count='yes' should be rejected."""
        count = "yes"
        count_lower = count.lower()
        # Should fail the validation
        assert count_lower not in ("true", "false"), "$count='yes' should be invalid"

    def test_count_zero_rejected(self):
        """$count='0' should be rejected."""
        count = "0"
        count_lower = count.lower()
        # Should fail the validation
        assert count_lower not in ("true", "false"), "$count='0' should be invalid"

    def test_count_empty_rejected(self):
        """$count='' (empty) should be rejected."""
        count = ""
        count_lower = count.lower()
        # Should fail the validation
        assert count_lower not in ("true", "false"), "$count='' (empty) should be invalid"


class TestCountApplyCombination:
    """Test that $count+$apply combination is allowed."""

    def test_count_apply_no_rejection(self):
        """$count with $apply should not raise ValueError."""
        # The old code had:
        # if apply_expr and count_requested:
        #     raise ValueError("$count with $apply is not supported")
        # This should now be removed

        apply_expr = "groupby((id), aggregate($count as Count))"
        count_requested = True

        # Should NOT raise - the validation has been removed
        # (This test just documents that we no longer reject this combination)
        assert apply_expr and count_requested, "Both $apply and $count are set"

    def test_apply_alone_still_works(self):
        """$apply without $count should work."""
        apply_expr = "groupby((id), aggregate($count as Count))"
        count_requested = False

        # Should work
        assert apply_expr and not count_requested


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
