"""Unit tests for unsupported OData filter constructs error handling (WP-08)."""

import pytest
from app.odata.expression_parser import ODataExpressionParser


class TestGUIDLiteralError:
    """Test that GUID literals raise descriptive ValueError."""

    def test_guid_literal_raises_value_error(self):
        """GUID literal should raise ValueError with 'GUID' in message."""
        parser = ODataExpressionParser()
        with pytest.raises(ValueError) as exc_info:
            parser.parse_filter("id eq guid'12345678-1234-1234-1234-123456789012'")
        assert "GUID" in str(exc_info.value)
        assert "unsupported" in str(exc_info.value).lower()

    def test_guid_error_message_is_descriptive(self):
        """Error message should explain GUID is not supported."""
        parser = ODataExpressionParser()
        with pytest.raises(ValueError) as exc_info:
            parser.parse_filter("id eq guid'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx'")
        assert "GUID" in str(exc_info.value)


class TestLambdaExpressionError:
    """Test that lambda operators (any/all) raise descriptive ValueError."""

    def test_any_lambda_raises_value_error(self):
        """any() expression should raise ValueError with lambda info in message."""
        parser = ODataExpressionParser()
        with pytest.raises(ValueError) as exc_info:
            parser.parse_filter("Items/any(x:x/price gt 100)")
        msg = str(exc_info.value).lower()
        # Should mention lambda, any, or unsupported
        assert "lambda" in msg or "any" in msg or "unsupported" in msg

    def test_all_lambda_raises_value_error(self):
        """all() expression should raise ValueError with lambda info in message."""
        parser = ODataExpressionParser()
        with pytest.raises(ValueError) as exc_info:
            parser.parse_filter("Items/all(x:x/status eq 'active')")
        msg = str(exc_info.value).lower()
        assert "lambda" in msg or "all" in msg or "unsupported" in msg

    def test_lambda_error_message_is_descriptive(self):
        """Error message should explain lambda operators are not supported."""
        parser = ODataExpressionParser()
        with pytest.raises(ValueError) as exc_info:
            parser.parse_filter("Orders/any(o:o/amount gt 100)")
        assert "lambda" in str(exc_info.value).lower()


class TestGeographyLiteralError:
    """Test that Geography literals raise descriptive ValueError."""

    def test_geography_literal_raises_value_error(self):
        """Geography literal should raise ValueError with 'Geography' in message."""
        parser = ODataExpressionParser()
        with pytest.raises(ValueError) as exc_info:
            parser.parse_filter("location eq geography'SRID=4326;POINT(-122.419 37.775)'")
        msg = str(exc_info.value).lower()
        assert "geography" in msg or "unsupported" in msg


class TestValidFiltersStillWork:
    """Regression: verify that valid filters still parse successfully."""

    def test_simple_string_equality(self):
        """String equality should parse without error."""
        parser = ODataExpressionParser()
        result = parser.parse_filter("name eq 'Alice'")
        assert result is not None

    def test_numeric_comparison(self):
        """Numeric comparison should parse without error."""
        parser = ODataExpressionParser()
        result = parser.parse_filter("age gt 21")
        assert result is not None

    def test_boolean_and_logic(self):
        """AND logic should parse without error."""
        parser = ODataExpressionParser()
        result = parser.parse_filter("status eq 'active' and age gt 18")
        assert result is not None

    def test_boolean_or_logic(self):
        """OR logic should parse without error."""
        parser = ODataExpressionParser()
        result = parser.parse_filter("status eq 'active' or status eq 'pending'")
        assert result is not None

    def test_contains_function(self):
        """contains() function should parse without error."""
        parser = ODataExpressionParser()
        result = parser.parse_filter("contains(name, 'ice')")
        assert result is not None

    def test_startswith_function(self):
        """startswith() function should parse without error."""
        parser = ODataExpressionParser()
        result = parser.parse_filter("startswith(name, 'A')")
        assert result is not None

    def test_endswith_function(self):
        """endswith() function should parse without error."""
        parser = ODataExpressionParser()
        result = parser.parse_filter("endswith(name, 'ice')")
        assert result is not None

    def test_is_null_check(self):
        """IS NULL check should parse without error."""
        parser = ODataExpressionParser()
        result = parser.parse_filter("deleted eq null")
        assert result is not None

    def test_in_operator(self):
        """IN operator should parse without error."""
        parser = ODataExpressionParser()
        result = parser.parse_filter("status in ('active', 'pending')")
        assert result is not None

    def test_empty_filter_returns_none(self):
        """Empty filter should return None."""
        parser = ODataExpressionParser()
        result = parser.parse_filter("")
        assert result is None

    def test_none_filter_returns_none(self):
        """None filter should return None."""
        parser = ODataExpressionParser()
        result = parser.parse_filter(None)
        assert result is None

    def test_whitespace_filter_returns_none(self):
        """Whitespace-only filter should return None."""
        parser = ODataExpressionParser()
        result = parser.parse_filter("   ")
        assert result is None


class TestErrorMessageDescriptiveness:
    """Test that error messages are user-friendly and descriptive."""

    def test_guid_error_mentions_guid_not_supported(self):
        """GUID error should explicitly say GUID literals are not supported."""
        parser = ODataExpressionParser()
        with pytest.raises(ValueError) as exc_info:
            parser.parse_filter("id eq guid'12345678-1234-1234-1234-123456789012'")
        error_msg = str(exc_info.value)
        assert "GUID" in error_msg
        assert "not supported" in error_msg or "unsupported" in error_msg.lower()

    def test_lambda_error_mentions_lambda_operators(self):
        """Lambda error should mention lambda operators or any/all."""
        parser = ODataExpressionParser()
        with pytest.raises(ValueError) as exc_info:
            parser.parse_filter("Items/any(x:x/price gt 100)")
        error_msg = str(exc_info.value).lower()
        assert "lambda" in error_msg or "any" in error_msg or "all" in error_msg
        assert "not supported" in error_msg or "unsupported" in error_msg

    def test_geography_error_mentions_geography(self):
        """Geography error should mention geography literals."""
        parser = ODataExpressionParser()
        with pytest.raises(ValueError) as exc_info:
            parser.parse_filter("location eq geography'SRID=4326;POINT(0 0)'")
        error_msg = str(exc_info.value).lower()
        assert "geography" in error_msg
        assert "not supported" in error_msg or "unsupported" in error_msg
