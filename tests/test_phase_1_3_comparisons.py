#!/usr/bin/env python3
"""
Phase 1.3: Test AST Comparison Operator Conversions

Tests that AST Compare nodes with different comparators correctly convert to
Opteryx COMPARISON_OPERATOR nodes with the right operator values.
"""

import sys
from pathlib import Path

from odata_query.grammar import ODataLexer
from odata_query.grammar import ODataParser
from opteryx.managers.expression import NodeType

# Set up vendored path
_vendored_path = str(Path(__file__).parent / "app" / "vendored" / "odata-query")
sys.path.insert(0, _vendored_path)


def parse_filter(filter_str):
    """Parse an OData filter string into an AST."""
    lexer = ODataLexer()
    parser = ODataParser()
    tokens = lexer.tokenize(filter_str)
    return parser.parse(tokens)


def test_comparison_operator(filter_str, expected_operator, test_desc):
    """Test that a comparison operator converts to the correct Opteryx node."""
    print(f"\nTest: {filter_str}")
    print("-" * 80)

    try:
        # Parse OData filter - should return a Compare node
        ast = parse_filter(filter_str)

        # Check AST type
        ast_type = type(ast).__name__
        if ast_type != "Compare":
            print(f"  ✗ AST type incorrect: expected Compare, got {ast_type}")
            return False
        print(f"  ✓ AST type correct: {ast_type}")

        # Check comparator type
        comparator_type = type(ast.comparator).__name__
        print(f"  ✓ Comparator type: {comparator_type}")

        # Convert to Opteryx node
        node = ast.to_opteryx_node()

        # Check node type
        if node.node_type != NodeType.COMPARISON_OPERATOR:
            print(
                f"  ✗ Node type incorrect: expected NodeType.COMPARISON_OPERATOR, got {node.node_type}"
            )
            return False
        print(f"  ✓ Node type correct: {node.node_type}")

        # Check operator value
        if node.value != expected_operator:
            print(
                f"  ✗ Operator value incorrect: expected '{expected_operator}', got '{node.value}'"
            )
            return False
        print(f"  ✓ Operator value correct: '{node.value}'")

        # Check left and right nodes exist
        if node.left is None:
            print("  ✗ Left node is None")
            return False
        print(f"  ✓ Left node present: {type(node.left)}")

        if node.right is None:
            print("  ✗ Right node is None")
            return False
        print(f"  ✓ Right node present: {type(node.right)}")

        print("  ✓✓ PASS")
        return True

    except Exception as e:
        print(f"  ✗ Conversion failed: {e}")
        import traceback

        traceback.print_exc()
        return False


def main():
    print("Phase 1.3: Testing AST Comparison Operator Conversions")
    print("=" * 80)

    tests = [
        # Equality operators
        ("id eq 42", "Eq", "Equality: eq"),
        ("name ne 'test'", "NotEq", "Not equal: ne"),
        # Comparison operators
        ("age gt 21", "Gt", "Greater than: gt"),
        ("age ge 21", "GtEq", "Greater than or equal: ge"),
        ("age lt 65", "Lt", "Less than: lt"),
        ("age le 65", "LtEq", "Less than or equal: le"),
        # In operator
        ("status in ('active', 'pending')", "In", "In operator: in"),
        # Mixed types
        ("price gt 99.99", "Gt", "Float comparison"),
        ("active eq true", "Eq", "Boolean comparison"),
        ("created_date lt 2024-01-15", "Lt", "Date comparison"),
    ]

    passed = 0
    total = len(tests)

    for filter_str, expected_op, desc in tests:
        if test_comparison_operator(filter_str, expected_op, desc):
            passed += 1

    print("\n" + "=" * 80)
    print(f"Results: {passed}/{total} passed ({int(passed / total * 100)}%)")

    if passed == total:
        print("✓✓ Phase 1.3 COMPLETE: All comparison operator conversions working correctly!")
    else:
        print(f"✗✗ Phase 1.3 INCOMPLETE: {total - passed} tests failing")


if __name__ == "__main__":
    main()
