#!/usr/bin/env python3
"""
Phase 1.6: Test AST Function Call Conversions

Tests that AST Call nodes (OData functions like startswith, endswith, contains)
correctly convert to Opteryx FUNCTION nodes.
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


def test_function_call(filter_str, expected_func, expected_arg_count, test_desc):
    """Test that a function call converts to the correct Opteryx node."""
    print(f"\nTest: {filter_str}")
    print("-" * 80)

    try:
        # Parse OData filter
        # For function calls used in filters, they're typically the right side of a comparison
        # or used directly as a boolean expression
        ast = parse_filter(filter_str)

        # If it's a comparison like "contains(name, 'test') eq true", get the left side
        # If it's just "contains(name, 'test')", it's the whole thing
        if hasattr(ast, "left"):
            func_ast = ast.left
        else:
            func_ast = ast

        # Check AST type
        ast_type = type(func_ast).__name__
        if ast_type != "Call":
            print(f"  ✗ AST type incorrect: expected Call, got {ast_type}")
            return False
        print(f"  ✓ AST type correct: {ast_type}")

        # Check function name
        func_name = func_ast.func.name
        if func_name != expected_func:
            print(f"  ✗ Function name incorrect: expected '{expected_func}', got '{func_name}'")
            return False
        print(f"  ✓ Function name correct: '{func_name}'")

        # Check argument count
        if len(func_ast.args) != expected_arg_count:
            print(
                f"  ✗ Argument count incorrect: expected {expected_arg_count}, got {len(func_ast.args)}"
            )
            return False
        print(f"  ✓ Argument count correct: {expected_arg_count}")

        # Convert to Opteryx node
        node = func_ast.to_opteryx_node()

        # Check node type
        if node.node_type != NodeType.FUNCTION:
            print(f"  ✗ Node type incorrect: expected NodeType.FUNCTION, got {node.node_type}")
            return False
        print(f"  ✓ Node type correct: {node.node_type}")

        # Check function value (name)
        if node.value != expected_func:
            print(f"  ✗ Function value incorrect: expected '{expected_func}', got '{node.value}'")
            return False
        print(f"  ✓ Function value correct: '{node.value}'")

        # Check parameters exist
        if not hasattr(node, "parameters") or node.parameters is None:
            print("  ✗ Node missing parameters")
            return False

        if len(node.parameters) != expected_arg_count:
            print(
                f"  ✗ Parameters count incorrect: expected {expected_arg_count}, got {len(node.parameters)}"
            )
            return False
        print(f"  ✓ Parameters present: {len(node.parameters)} arguments")

        # Check all parameters are nodes
        for i, param in enumerate(node.parameters):
            if not hasattr(param, "node_type"):
                print(f"  ✗ Parameter {i} is not a node")
                return False
        print("  ✓ All parameters are valid nodes")

        print("  ✓✓ PASS")
        return True

    except Exception as e:
        print(f"  ✗ Conversion failed: {e}")
        import traceback

        traceback.print_exc()
        return False


def main():
    print("Phase 1.6: Testing AST Function Call Conversions")
    print("=" * 80)

    tests = [
        # String functions
        ("contains(name, 'test')", "contains", 2, "contains function"),
        ("startswith(name, 'Mr')", "startswith", 2, "startswith function"),
        ("endswith(name, 'son')", "endswith", 2, "endswith function"),
        ("concat(firstName, lastName)", "concat", 2, "concat function"),
        ("length(name)", "length", 1, "length function"),
        ("tolower(name)", "tolower", 1, "tolower function"),
        ("toupper(name)", "toupper", 1, "toupper function"),
        ("trim(name)", "trim", 1, "trim function"),
        # String functions with comparisons
        ("contains(name, 'test') eq true", "contains", 2, "contains in comparison"),
        ("length(name) gt 10", "length", 1, "length in comparison"),
        # Date functions
        ("year(birthDate)", "year", 1, "year function"),
        ("month(birthDate)", "month", 1, "month function"),
        ("day(birthDate)", "day", 1, "day function"),
    ]

    passed = 0
    total = len(tests)

    for filter_str, expected_func, expected_args, desc in tests:
        if test_function_call(filter_str, expected_func, expected_args, desc):
            passed += 1

    print("\n" + "=" * 80)
    print(f"Results: {passed}/{total} passed ({int(passed / total * 100)}%)")

    if passed == total:
        print("✓✓ Phase 1.6 COMPLETE: All function call conversions working correctly!")
    else:
        print(f"✗✗ Phase 1.6 INCOMPLETE: {total - passed} tests failing")


if __name__ == "__main__":
    main()
