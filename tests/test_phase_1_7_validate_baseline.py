#!/usr/bin/env python3
"""
Phase 1.7: Validate OData AST Node Structure

Validates that OData→Opteryx conversions produce well-formed node trees
with correct structure, types, and relationships.
"""

import sys
from pathlib import Path

from odata_query.grammar import ODataLexer
from odata_query.grammar import ODataParser
from opteryx.managers.expression import NodeType

# Set up vendored path
_vendored_path = str(Path(__file__).parent / "app" / "vendored" / "odata-query")
sys.path.insert(0, _vendored_path)


def parse_odata_filter(filter_str):
    """Parse an OData filter string into an AST and convert to Opteryx node."""
    lexer = ODataLexer()
    parser = ODataParser()
    tokens = lexer.tokenize(filter_str)
    ast = parser.parse(tokens)
    return ast.to_opteryx_node()


def validate_node_structure(node, path="root"):
    """Recursively validate node structure."""
    issues = []

    # Check node has node_type
    if not hasattr(node, "node_type"):
        issues.append(f"{path}: missing node_type attribute")
        return issues

    # Check node_type is valid
    valid_types = [
        NodeType.LITERAL,
        NodeType.IDENTIFIER,
        NodeType.COMPARISON_OPERATOR,
        NodeType.AND,
        NodeType.OR,
        NodeType.NOT,
        NodeType.FUNCTION,
    ]
    if node.node_type not in valid_types:
        issues.append(f"{path}: invalid node_type {node.node_type}")

    # Validate based on node type
    if node.node_type == NodeType.LITERAL:
        # Literals should have a value
        if not hasattr(node, "value"):
            issues.append(f"{path}: LITERAL missing value")
        # Literals should have a type annotation (except ARRAY)
        if hasattr(node, "type") and node.type is not None:
            pass  # Good - has type
        elif hasattr(node, "value") and isinstance(node.value, list):
            pass  # ARRAY literal - value is a list of nodes

    elif node.node_type == NodeType.IDENTIFIER:
        # Identifiers should have a value (the name)
        if not hasattr(node, "value") or not node.value:
            issues.append(f"{path}: IDENTIFIER missing value (name)")

    elif node.node_type == NodeType.COMPARISON_OPERATOR:
        # Comparisons should have left and right children
        if not (hasattr(node, "left") and node.left is not None):
            issues.append(f"{path}: COMPARISON_OPERATOR missing left child")
        if not (hasattr(node, "right") and node.right is not None):
            issues.append(f"{path}: COMPARISON_OPERATOR missing right child")
        # Should have an operator value
        if not hasattr(node, "value") or not node.value:
            issues.append(f"{path}: COMPARISON_OPERATOR missing operator value")

    elif node.node_type in (NodeType.AND, NodeType.OR):
        # Boolean ops should have left and right children
        if not (hasattr(node, "left") and node.left is not None):
            issues.append(f"{path}: {node.node_type} missing left child")
        if not (hasattr(node, "right") and node.right is not None):
            issues.append(f"{path}: {node.node_type} missing right child")

    elif node.node_type == NodeType.NOT:
        # NOT should have an operand (in left)
        if not (hasattr(node, "left") and node.left is not None):
            issues.append(f"{path}: NOT missing operand (left child)")

    # Recursively validate children
    if hasattr(node, "left") and node.left is not None:
        issues.extend(validate_node_structure(node.left, f"{path}.left"))

    if hasattr(node, "right") and node.right is not None:
        issues.extend(validate_node_structure(node.right, f"{path}.right"))

    # Validate array elements
    if hasattr(node, "value") and isinstance(node.value, list):
        for i, child in enumerate(node.value):
            if hasattr(child, "node_type"):  # It's a node
                issues.extend(validate_node_structure(child, f"{path}.value[{i}]"))

    return issues


def test_node_structure(odata_expr, description):
    """Test that OData conversion produces valid node structure."""
    print(f"\nTest: {description}")
    print(f"  Expression: {odata_expr}")
    print("-" * 80)

    try:
        # Parse and convert
        node = parse_odata_filter(odata_expr)

        print(f"  ✓ Converted to node type: {node.node_type}")

        # Validate structure
        issues = validate_node_structure(node)

        if not issues:
            print("  ✓ Node structure valid")
            print("  ✓✓ PASS")
            return True
        else:
            print("  ✗ Node structure issues:")
            for issue in issues:
                print(f"    - {issue}")
            return False

    except Exception as e:
        print(f"  ✗ Conversion failed: {e}")
        import traceback

        traceback.print_exc()
        return False


def main():
    print("Phase 1.7: Validating OData Node Structures")
    print("=" * 80)

    tests = [
        # Literals
        ("id eq 42", "Integer literal"),
        ("name eq 'test'", "String literal"),
        ("active eq true", "Boolean literal"),
        ("price eq 99.99", "Float literal"),
        ("field eq null", "Null literal"),
        # Identifiers
        ("user_id eq 42", "Identifier with underscore"),
        # Comparisons
        ("age gt 21", "Greater than"),
        ("age ge 21", "Greater or equal"),
        ("age lt 65", "Less than"),
        ("age le 65", "Less or equal"),
        ("status ne 'deleted'", "Not equal"),
        ("status in ('active', 'pending')", "In operator with array"),
        # Boolean operators
        ("id eq 42 and name eq 'test'", "AND operator"),
        ("status eq 'active' or status eq 'pending'", "OR operator"),
        ("price gt 100 and price lt 1000 and active eq true", "Multiple AND"),
        # Unary operators
        ("not (active eq false)", "NOT operator"),
        ("not (age lt 18)", "NOT with comparison"),
        # Complex expressions
        ("(age gt 21 and age lt 65) or status eq 'VIP'", "Complex nested expression"),
        (
            "not ((status eq 'deleted' or status eq 'archived') and age lt 18)",
            "Complex NOT with nested booleans",
        ),
        ("name eq 'Alice' and (age gt 25 or city eq 'NYC')", "Mixed AND/OR nesting"),
    ]

    passed = 0
    total = len(tests)

    for odata_expr, desc in tests:
        if test_node_structure(odata_expr, desc):
            passed += 1

    print("\n" + "=" * 80)
    print(f"Results: {passed}/{total} passed ({int(passed / total * 100)}%)")

    if passed == total:
        print("✓✓ Phase 1.7 COMPLETE: All node structures valid!")
    else:
        print(f"✗✗ Phase 1.7 INCOMPLETE: {total - passed} tests failing")


if __name__ == "__main__":
    main()
