#!/usr/bin/env python3
"""
Phase 1.5: Test AST Unary Operator Conversions

Tests that AST UnaryOp nodes (Not) correctly convert to
Opteryx NOT nodes with proper operand.
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


def test_unary_operator(filter_str, test_desc):
    """Test that a unary NOT operator converts to the correct Opteryx node."""
    print(f"\nTest: {filter_str}")
    print("-" * 80)

    try:
        # Parse OData filter - should return a UnaryOp node
        ast = parse_filter(filter_str)

        # Check AST type
        ast_type = type(ast).__name__
        if ast_type != "UnaryOp":
            print(f"  ✗ AST type incorrect: expected UnaryOp, got {ast_type}")
            return False
        print(f"  ✓ AST type correct: {ast_type}")

        # Check operator type
        op_type = type(ast.op).__name__
        if op_type != "Not":
            print(f"  ✗ Operator type incorrect: expected Not, got {op_type}")
            return False
        print(f"  ✓ Operator type: {op_type}")

        # Convert to Opteryx node
        node = ast.to_opteryx_node()

        # Check node type
        if node.node_type != NodeType.NOT:
            print(f"  ✗ Node type incorrect: expected NodeType.NOT, got {node.node_type}")
            return False
        print(f"  ✓ Node type correct: {node.node_type}")

        # Check operator value
        if node.value != "Not":
            print(f"  ✗ Operator value incorrect: expected 'Not', got '{node.value}'")
            return False
        print(f"  ✓ Operator value correct: '{node.value}'")

        # Check operand node exists (stored in left for NOT)
        if node.left is None:
            print("  ✗ Operand node is None")
            return False
        print(f"  ✓ Operand node present: {type(node.left)}")

        print("  ✓✓ PASS")
        return True

    except Exception as e:
        print(f"  ✗ Conversion failed: {e}")
        import traceback

        traceback.print_exc()
        return False


def main():
    print("Phase 1.5: Testing AST Unary Operator Conversions")
    print("=" * 80)

    tests = [
        # NOT operators with various operands
        ("not (id eq 42)", "NOT with comparison"),
        ("not (active eq true)", "NOT with boolean comparison"),
        ("not (name eq 'test')", "NOT with string comparison"),
        ("not (age gt 21)", "NOT with greater than"),
        ("not (price lt 100.50)", "NOT with float comparison"),
        ("not (status eq 'deleted')", "NOT with status check"),
    ]

    passed = 0
    total = len(tests)

    for filter_str, desc in tests:
        if test_unary_operator(filter_str, desc):
            passed += 1

    print("\n" + "=" * 80)
    print(f"Results: {passed}/{total} passed ({int(passed / total * 100)}%)")

    if passed == total:
        print("✓✓ Phase 1.5 COMPLETE: All unary operator conversions working correctly!")
    else:
        print(f"✗✗ Phase 1.5 INCOMPLETE: {total - passed} tests failing")


if __name__ == "__main__":
    main()
