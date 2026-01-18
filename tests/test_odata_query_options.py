"""Test OData query options: $filter, $select, $orderby"""

import re


def _convert_odata_filter_to_sql(odata_filter: str) -> str:
    """Convert OData filter syntax to SQL WHERE clause.

    Supports basic OData operators:
    - Comparison: eq, ne, gt, ge, lt, le
    - Logical: and, or, not
    - String functions: contains, startswith, endswith
    - Other: null literal

    Examples:
        "name eq 'John'" -> "name = 'John'"
        "age gt 25" -> "age > 25"
        "contains(name, 'test')" -> "name LIKE '%test%'"
    """
    if not odata_filter:
        return ""

    # Replace OData operators with SQL equivalents
    replacements = [
        (" eq ", " = "),
        (" ne ", " != "),
        (" gt ", " > "),
        (" ge ", " >= "),
        (" lt ", " < "),
        (" le ", " <= "),
        (" and ", " AND "),
        (" or ", " OR "),
        (" not ", " NOT "),
    ]

    result = odata_filter
    for odata_op, sql_op in replacements:
        result = result.replace(odata_op, sql_op)

    # Handle string functions
    # contains(field, 'value') -> field LIKE '%value%'
    result = re.sub(
        r"contains\(([^,]+),\s*'([^']*)'\)", r"\1 LIKE '%\2%'", result, flags=re.IGNORECASE
    )

    # startswith(field, 'value') -> field LIKE 'value%'
    result = re.sub(
        r"startswith\(([^,]+),\s*'([^']*)'\)", r"\1 LIKE '\2%'", result, flags=re.IGNORECASE
    )

    # endswith(field, 'value') -> field LIKE '%value'
    result = re.sub(
        r"endswith\(([^,]+),\s*'([^']*)'\)", r"\1 LIKE '%\2'", result, flags=re.IGNORECASE
    )

    # Handle null literal
    result = result.replace(" null", " NULL")
    result = result.replace("(null", "(NULL")

    return result


def test_filter_equality():
    """Test eq operator"""
    result = _convert_odata_filter_to_sql("name eq 'John'")
    assert result == "name = 'John'"
    print("✓ Test eq operator passed")


def test_filter_comparison():
    """Test comparison operators"""
    tests = [
        ("age gt 25", "age > 25"),
        ("age ge 25", "age >= 25"),
        ("age lt 25", "age < 25"),
        ("age le 25", "age <= 25"),
        ("status ne 'active'", "status != 'active'"),
    ]
    for odata, expected in tests:
        result = _convert_odata_filter_to_sql(odata)
        assert result == expected, f"Expected '{expected}', got '{result}'"
    print("✓ Test comparison operators passed")


def test_filter_logical():
    """Test logical operators"""
    tests = [
        ("age gt 25 and status eq 'active'", "age > 25 AND status = 'active'"),
        ("age gt 25 or status eq 'active'", "age > 25 OR status = 'active'"),
        ("not active", "NOT active"),
    ]
    for odata, expected in tests:
        result = _convert_odata_filter_to_sql(odata)
        assert result == expected, f"Expected '{expected}', got '{result}'"
    print("✓ Test logical operators passed")


def test_filter_string_functions():
    """Test string functions"""
    tests = [
        ("contains(name, 'test')", "name LIKE '%test%'"),
        ("startswith(name, 'test')", "name LIKE 'test%'"),
        ("endswith(name, 'test')", "name LIKE '%test'"),
    ]
    for odata, expected in tests:
        result = _convert_odata_filter_to_sql(odata)
        assert result == expected, f"Expected '{expected}', got '{result}'"
    print("✓ Test string functions passed")


def test_filter_null():
    """Test null literal"""
    tests = [
        ("status eq null", "status = NULL"),
        ("contains(null, 'test')", "contains(NULL, 'test')"),
    ]
    for odata, expected in tests:
        result = _convert_odata_filter_to_sql(odata)
        assert result == expected, f"Expected '{expected}', got '{result}'"
    print("✓ Test null literal passed")


def test_filter_complex():
    """Test complex filter"""
    odata = "age gt 25 and status eq 'active' or contains(name, 'test')"
    expected = "age > 25 AND status = 'active' OR name LIKE '%test%'"
    result = _convert_odata_filter_to_sql(odata)
    assert result == expected, f"Expected '{expected}', got '{result}'"
    print("✓ Test complex filter passed")


if __name__ == "__main__":
    print("Testing OData filter conversion...")
    test_filter_equality()
    test_filter_comparison()
    test_filter_logical()
    test_filter_string_functions()
    test_filter_null()
    test_filter_complex()
    print("\n✅ All tests passed!")
