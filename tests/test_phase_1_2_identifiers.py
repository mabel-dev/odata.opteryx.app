#!/usr/bin/env python3
"""
Phase 1.2: Test AST Identifier Node Conversions

Tests that AST Identifier nodes correctly convert to Opteryx IDENTIFIER nodes.
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


def test_identifier_conversion(filter_str, expected_name, test_desc):
    """Test that an identifier converts to the correct Opteryx node."""
    print(f"\nTest: {filter_str}")
    print("-" * 80)

    try:
        # Parse OData filter
        ast = parse_filter(filter_str)

        # For simple equality comparisons, the identifier will be the left side
        # ast is a Compare node, left is the identifier
        identifier_ast = ast.left

        # Check AST type
        ast_type = type(identifier_ast).__name__
        if ast_type != "Identifier":
            print(f"  ✗ AST type incorrect: expected Identifier, got {ast_type}")
            return False
        print(f"  ✓ AST literal type correct: {ast_type}")

        # Convert to Opteryx node
        node = identifier_ast.to_opteryx_node()

        # CRITICAL: Check this is a LogicalColumn, not a Node
        from opteryx.models.logical_column import LogicalColumn

        if not isinstance(node, LogicalColumn):
            print(f"  ✗ Node should be LogicalColumn, got {type(node).__name__}")
            return False
        print("  ✓ Node is LogicalColumn")

        # Check node type
        if node.node_type != NodeType.IDENTIFIER:
            print(f"  ✗ Node type incorrect: expected NodeType.IDENTIFIER, got {node.node_type}")
            return False
        print(f"  ✓ Node type correct: {node.node_type}")

        # Check source_column attribute (required for binding)
        if not hasattr(node, "source_column") or node.source_column != expected_name:
            actual = getattr(node, "source_column", "MISSING")
            print(f"  ✗ source_column incorrect: expected '{expected_name}', got '{actual}'")
            return False
        print(f"  ✓ source_column correct: '{node.source_column}'")

        print("  ✓✓ PASS")
        return True

    except Exception as e:
        print(f"  ✗ Conversion failed: {e}")
        import traceback

        traceback.print_exc()
        return False


def main():
    print("Phase 1.2: Testing AST Identifier Node Conversions")
    print("=" * 80)

    tests = [
        # Simple identifiers
        ("id eq 42", "id", "Simple identifier 'id'"),
        ("name eq 'Earth'", "name", "Simple identifier 'name'"),
        ("active eq true", "active", "Simple identifier 'active'"),
        ("temperature eq 3.14", "temperature", "Simple identifier 'temperature'"),
        ("created_date eq 2024-01-15", "created_date", "Identifier with underscore"),
        ("firstName eq 'John'", "firstName", "CamelCase identifier"),
        ("FirstName eq 'John'", "FirstName", "PascalCase identifier"),
        ("field123 eq 42", "field123", "Identifier with numbers"),
    ]

    passed = 0
    total = len(tests)

    for filter_str, expected_name, desc in tests:
        if test_identifier_conversion(filter_str, expected_name, desc):
            passed += 1

    print("\n" + "=" * 80)
    print(f"Results: {passed}/{total} passed ({int(passed / total * 100)}%)")

    if passed == total:
        print("✓✓ Phase 1.2 COMPLETE: All identifier conversions working correctly!")
    else:
        print(f"✗✗ Phase 1.2 INCOMPLETE: {total - passed} tests failing")


if __name__ == "__main__":
    main()
