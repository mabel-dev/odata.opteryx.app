"""Phase 1.1: Test AST Literal Node Conversions

Validates that each literal type converts to the correct Opteryx node structure.
"""

import datetime as dt
import sys
from pathlib import Path

from odata_query import ast as odata_ast
from odata_query.grammar import ODataLexer
from odata_query.grammar import ODataParser
from opteryx.managers.expression import NodeType
from orso.types import OrsoTypes

# Set up vendored path
_vendored_path = str(Path(__file__).parent / "app" / "vendored" / "odata-query")
sys.path.insert(0, _vendored_path)


print("Phase 1.1: Testing AST Literal Node Conversions")
print("=" * 80)

test_cases = [
    # (filter_expression, expected_literal_type, expected_value, expected_orso_type)
    ("id eq 42", odata_ast.Integer, 42, OrsoTypes.INTEGER),
    ("id eq 0", odata_ast.Integer, 0, OrsoTypes.INTEGER),
    ("id eq -5", odata_ast.Integer, -5, OrsoTypes.INTEGER),
    ("name eq 'Earth'", odata_ast.String, "Earth", OrsoTypes.VARCHAR),
    ("name eq ''", odata_ast.String, "", OrsoTypes.VARCHAR),
    ("active eq true", odata_ast.Boolean, True, OrsoTypes.BOOLEAN),
    ("active eq false", odata_ast.Boolean, False, OrsoTypes.BOOLEAN),
    ("temp eq 3.14", odata_ast.Float, 3.14, OrsoTypes.DOUBLE),
    ("temp eq 0.0", odata_ast.Float, 0.0, OrsoTypes.DOUBLE),
    ("field eq null", odata_ast.Null, None, OrsoTypes.NULL),
    # Date/Time literals
    ("created_date eq 2024-01-15", odata_ast.Date, dt.date(2024, 1, 15), OrsoTypes.DATE),
    ("event_time eq 14:30:00", odata_ast.Time, dt.time(14, 30, 0), OrsoTypes.TIME),
    (
        "timestamp eq 2024-01-15T14:30:00Z",
        odata_ast.DateTime,
        None,
        OrsoTypes.TIMESTAMP,
    ),  # Value checked separately due to timezone parsing
    (
        "duration eq duration'P1DT12H'",
        odata_ast.Duration,
        None,
        OrsoTypes.INTERVAL,
    ),  # Duration is complex, check type only
]

passed = 0
failed = 0

for filter_expr, expected_ast_type, expected_value, expected_orso_type in test_cases:
    print(f"\nTest: {filter_expr}")
    print("-" * 80)

    try:
        # Parse the filter expression
        lexer = ODataLexer()
        parser = ODataParser()
        ast = parser.parse(lexer.tokenize(filter_expr))

        # The AST should be a Compare node with a literal on the right side
        if not isinstance(ast, odata_ast.Compare):
            print(f"  ✗ Expected Compare node, got {type(ast).__name__}")
            failed += 1
            continue

        # Extract the literal (should be on the right side)
        literal_ast = ast.right

        if not isinstance(literal_ast, expected_ast_type):
            print(f"  ✗ Expected {expected_ast_type.__name__}, got {type(literal_ast).__name__}")
            failed += 1
            continue

        print(f"  ✓ AST literal type correct: {expected_ast_type.__name__}")

        # Convert to Opteryx node
        try:
            node = literal_ast.to_opteryx_node()
        except Exception as e:
            print(f"  ✗ Conversion failed: {e}")
            import traceback

            traceback.print_exc()
            failed += 1
            continue

        # Validate node type
        if node.node_type != NodeType.LITERAL:
            print(f"  ✗ Expected NodeType.LITERAL, got {node.node_type}")
            failed += 1
            continue

        print("  ✓ Node type correct: NodeType.LITERAL")

        # Validate node value (skip if expected_value is None, meaning we just check type)
        if expected_value is not None:
            if node.value != expected_value:
                print(f"  ✗ Expected value {expected_value!r}, got {node.value!r}")
                failed += 1
                continue

            print(f"  ✓ Node value correct: {expected_value!r}")
        else:
            print(f"  ✓ Node value present: {node.value!r} (not validating exact value)")

        # Validate Orso type
        if hasattr(node, "type") and node.type != expected_orso_type:
            print(f"  ✗ Expected type {expected_orso_type}, got {node.type}")
            failed += 1
            continue

        if hasattr(node, "type"):
            print(f"  ✓ Orso type correct: {expected_orso_type}")

        print("  ✓✓ PASS")
        passed += 1

    except Exception as e:
        print(f"  ✗ Test failed with exception: {e}")
        import traceback

        traceback.print_exc()
        failed += 1

print("\n" + "=" * 80)
print(
    f"Results: {passed}/{passed + failed} passed ({100 * passed / (passed + failed) if passed + failed > 0 else 0:.0f}%)"
)

if failed == 0:
    print("✓✓ Phase 1.1 COMPLETE: All literal conversions working correctly!")
else:
    print(f"✗✗ {failed} tests failed - needs fixes")
    sys.exit(1)
