"""Test that OData AST -> Opteryx Node conversion matches Opteryx's own filter parser."""

import sys
from pathlib import Path

from odata_query.grammar import ODataLexer
from odata_query.grammar import ODataParser
from opteryx.managers.expression import Filter

# Set up vendored path
_vendored_path = str(Path(__file__).parent / "app" / "vendored" / "odata-query")
sys.path.insert(0, _vendored_path)


# Test filter expressions
test_filters = [
    "id eq 1",
    "name eq 'Earth'",
    "diameter gt 5000",
    "distance_from_sun lt 500",
    "id eq 1 and name eq 'Earth'",
    "id eq 1 or id eq 2",
    "not (id eq 1)",
]

print("Testing OData AST -> Opteryx Node conversion\n")
print("=" * 80)

for filter_expr in test_filters:
    print(f"\nFilter: {filter_expr}")
    print("-" * 80)

    # Parse with OData parser -> AST
    lexer = ODataLexer()
    parser = ODataParser()
    try:
        ast = parser.parse(lexer.tokenize(filter_expr))
        print("✓ OData AST parsed successfully")
    except Exception as e:
        print(f"✗ OData AST parse failed: {e}")
        continue

    # Convert AST -> Opteryx Node
    try:
        odata_node = ast.to_opteryx_node()
        print("✓ AST -> Node conversion successful")
        print(f"  Node type: {odata_node.node_type}")
        print(f"  Node value: {getattr(odata_node, 'value', 'N/A')}")
        if hasattr(odata_node, "left"):
            print(f"  Has left child: {odata_node.left is not None}")
        if hasattr(odata_node, "right"):
            print(f"  Has right child: {odata_node.right is not None}")
    except Exception as e:
        print(f"✗ AST -> Node conversion failed: {e}")
        import traceback

        traceback.print_exc()
        continue

    # Parse with Opteryx filter parser
    try:
        opteryx_node = Filter.build_filter(filter_expr)
        print("✓ Opteryx filter parser successful")
        print(f"  Node type: {opteryx_node.node_type}")
        print(f"  Node value: {getattr(opteryx_node, 'value', 'N/A')}")
        if hasattr(opteryx_node, "left"):
            print(f"  Has left child: {opteryx_node.left is not None}")
        if hasattr(opteryx_node, "right"):
            print(f"  Has right child: {opteryx_node.right is not None}")
    except Exception as e:
        print(f"✗ Opteryx filter parse failed: {e}")
        import traceback

        traceback.print_exc()
        continue

    # Compare structures
    print("\nComparison:")
    if odata_node.node_type == opteryx_node.node_type:
        print(f"  ✓ Node types match: {odata_node.node_type}")
    else:
        print(
            f"  ✗ Node types differ: OData={odata_node.node_type}, Opteryx={opteryx_node.node_type}"
        )

    if getattr(odata_node, "value", None) == getattr(opteryx_node, "value", None):
        print(f"  ✓ Values match: {getattr(odata_node, 'value', 'N/A')}")
    else:
        print(
            f"  ✗ Values differ: OData={getattr(odata_node, 'value', None)}, Opteryx={getattr(opteryx_node, 'value', None)}"
        )

print("\n" + "=" * 80)
