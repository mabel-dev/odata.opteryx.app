#!/usr/bin/env python3
"""
Phase 1.4: Test AST Boolean Operator Conversions

Tests that AST BoolOp nodes (And, Or) correctly convert to
Opteryx AND/OR nodes with proper children.
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


def test_boolean_operator(filter_str, expected_op_type, expected_value, test_desc):
    """Test that a boolean operator converts to the correct Opteryx node."""
    print(f"\nTest: {filter_str}")
    print("-" * 80)

    try:
        # Parse OData filter - should return a BoolOp node
        ast = parse_filter(filter_str)

        # Check AST type
        ast_type = type(ast).__name__
        if ast_type != "BoolOp":
            print(f"  ✗ AST type incorrect: expected BoolOp, got {ast_type}")
            return False
        print(f"  ✓ AST type correct: {ast_type}")

        # Check operator type
        op_type = type(ast.op).__name__
        print(f"  ✓ Operator type: {op_type}")

        # Convert to Opteryx node
        node = ast.to_opteryx_node()

        # Check node type
        if node.node_type != expected_op_type:
            print(f"  ✗ Node type incorrect: expected {expected_op_type}, got {node.node_type}")
            return False
        print(f"  ✓ Node type correct: {node.node_type}")

        # Check operator value
        if node.value != expected_value:
            print(f"  ✗ Operator value incorrect: expected '{expected_value}', got '{node.value}'")
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
    print("Phase 1.4: Testing AST Boolean Operator Conversions")
    print("=" * 80)

    tests = [
        # AND operators
        ("id eq 42 and name eq 'test'", NodeType.AND, "And", "AND with two comparisons"),
        ("active eq true and age gt 21", NodeType.AND, "And", "AND with boolean and comparison"),
        ("price gt 100 and price lt 1000", NodeType.AND, "And", "AND with range check"),
        # OR operators
        ("status eq 'active' or status eq 'pending'", NodeType.OR, "Or", "OR with two comparisons"),
        ("age lt 18 or age gt 65", NodeType.OR, "Or", "OR with two conditions"),
        ("name eq 'test' or id eq 999", NodeType.OR, "Or", "OR with mixed types"),
        # Complex types
        ("price gt 99.99 and active eq true", NodeType.AND, "And", "AND with float and boolean"),
        (
            "created_date lt 2024-01-15 or updated_date gt 2024-12-31",
            NodeType.OR,
            "Or",
            "OR with dates",
        ),
    ]

    passed = 0
    total = len(tests)

    for filter_str, expected_type, expected_value, desc in tests:
        if test_boolean_operator(filter_str, expected_type, expected_value, desc):
            passed += 1

    print("\n" + "=" * 80)
    print(f"Results: {passed}/{total} passed ({int(passed / total * 100)}%)")

    if passed == total:
        print("✓✓ Phase 1.4 COMPLETE: All boolean operator conversions working correctly!")
    else:
        print(f"✗✗ Phase 1.4 INCOMPLETE: {total - passed} tests failing")


if __name__ == "__main__":
    main()
