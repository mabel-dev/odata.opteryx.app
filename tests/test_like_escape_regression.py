#!/usr/bin/env python3
"""Regression tests for LIKE metacharacter escaping.

These tests ensure that the escaping fix doesn't break existing functionality
and that special characters in search patterns are handled correctly.
"""

import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.odata.expression_parser import ODataExpressionParser


def test_plain_contains_still_works():
    """Verify that normal contains queries without special chars still work."""
    parser = ODataExpressionParser()
    node = parser.parse_filter("contains(name, 'John')")
    pattern = node.right.value.decode("utf-8")

    # Should have wrapped with % on both sides
    assert pattern == "%John%", f"Expected %John%, got {pattern}"
    print("✓ Plain contains() works: 'John' → '%John%'")


def test_multiple_contains_in_expression():
    """Verify that multiple contains in one expression work."""
    parser = ODataExpressionParser()
    # This will create a complex expression with AND
    node = parser.parse_filter("contains(name, 'John') and contains(city, 'New%')")

    # The node should be an AND operator with two LIKE comparisons
    from opteryx.expression import NodeType
    assert node.node_type == NodeType.AND, f"Expected AND, got {node.node_type}"
    print("✓ Multiple contains in one expression works")


def test_escape_in_middle_of_pattern():
    """Verify that special chars in the middle of patterns are escaped."""
    parser = ODataExpressionParser()
    node = parser.parse_filter("contains(code, 'AB%CD')")
    pattern = node.right.value.decode("utf-8")

    # The % should be escaped in the middle
    assert pattern == "%AB\\%CD%", f"Expected %AB\\%CD%, got {pattern}"
    print("✓ Special char in middle of pattern: 'AB%CD' → '%AB\\%CD%'")


def test_multiple_same_metacharacters():
    """Verify that multiple occurrences of the same metachar are escaped."""
    parser = ODataExpressionParser()
    node = parser.parse_filter("contains(sku, '%20%discount%')")
    pattern = node.right.value.decode("utf-8")

    # All % should be escaped
    assert pattern == "%\\%20\\%discount\\%%", f"Expected %\\%20\\%discount\\%%, got {pattern}"
    print("✓ Multiple same metacharacters: '%20%discount%' → '%\\%20\\%discount\\%%'")


def test_underscore_with_other_chars():
    """Verify underscore escaping works with other characters."""
    parser = ODataExpressionParser()
    node = parser.parse_filter("startswith(filename, 'file_2024')")
    pattern = node.right.value.decode("utf-8")

    # The _ should be escaped
    assert pattern == "file\\_2024%", f"Expected file\\_2024%, got {pattern}"
    print("✓ Underscore in pattern: 'file_2024' → 'file\\_2024%'")


def test_endswith_with_percent():
    """Verify endswith with metacharacters."""
    parser = ODataExpressionParser()
    node = parser.parse_filter("endswith(name, 'sale%off')")
    pattern = node.right.value.decode("utf-8")

    # Should have % prepended and the % in the pattern escaped
    assert pattern == "%sale\\%off", f"Expected %sale\\%off, got {pattern}"
    print("✓ Endswith with %: 'sale%off' → '%sale\\%off'")


if __name__ == "__main__":
    print("Running LIKE escaping regression tests...")
    print("=" * 60)

    try:
        test_plain_contains_still_works()
        test_multiple_contains_in_expression()
        test_escape_in_middle_of_pattern()
        test_multiple_same_metacharacters()
        test_underscore_with_other_chars()
        test_endswith_with_percent()

        print("=" * 60)
        print("✓ All regression tests passed!")
    except AssertionError as e:
        print(f"✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
