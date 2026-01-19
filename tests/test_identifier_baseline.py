"""Test identifier conversion against Opteryx baseline"""

import os
import sys
from pathlib import Path

import opteryx
from odata_query.grammar import ODataLexer
from odata_query.grammar import ODataParser
from opteryx.models.logical_column import LogicalColumn

sys.path.insert(1, os.path.join(sys.path[0], "../opteryx-core"))


# Set up vendored path
_vendored_path = str(Path(__file__).parent / "app" / "vendored" / "odata-query")
sys.path.insert(0, _vendored_path)


def test_identifier_baseline():
    """Compare OData identifier conversion to Opteryx SQL parser baseline"""
    print("Testing identifier conversion against Opteryx baseline")
    print("=" * 80)

    # Get Opteryx baseline - parse SQL to see how it creates identifier nodes
    sql = "SELECT id FROM test WHERE id = 1"
    baseline_plan = opteryx.query_to_plan(sql)

    # Find a LogicalColumn in the baseline plan
    baseline_column = None
    for node_id in baseline_plan.nodes():
        node = baseline_plan[node_id]
        if hasattr(node, "columns") and node.columns:
            for col in node.columns:
                if isinstance(col, LogicalColumn):
                    baseline_column = col
                    break
        if baseline_column:
            break

    if not baseline_column:
        print("✗ Could not find baseline LogicalColumn in Opteryx plan")
        return False

    print("✓ Found Opteryx baseline LogicalColumn")
    print(f"  Type: {type(baseline_column).__name__}")
    print(f"  node_type: {baseline_column.node_type}")
    print(f"  source_column: {baseline_column.source_column}")
    print(f"  Has query_column: {hasattr(baseline_column, 'query_column')}")

    # Now create OData identifier and convert it
    print("\nConverting OData identifier 'id'...")
    lexer = ODataLexer()
    parser = ODataParser()
    odata_ast = parser.parse(lexer.tokenize("id eq 1"))

    # Get the identifier from left side of comparison
    odata_column = odata_ast.left.to_opteryx_node()

    print(f"  Type: {type(odata_column).__name__}")
    print(f"  node_type: {odata_column.node_type}")
    print(f"  source_column: {getattr(odata_column, 'source_column', 'MISSING')}")
    print(f"  Has query_column: {hasattr(odata_column, 'query_column')}")

    # Validate match
    errors = []

    if type(odata_column) != type(baseline_column):
        errors.append(
            f"Type mismatch: {type(odata_column).__name__} vs {type(baseline_column).__name__}"
        )

    if odata_column.node_type != baseline_column.node_type:
        errors.append(
            f"node_type mismatch: {odata_column.node_type} vs {baseline_column.node_type}"
        )

    if not hasattr(odata_column, "source_column"):
        errors.append("Missing source_column attribute")

    if not hasattr(odata_column, "query_column"):
        errors.append("Missing query_column attribute")

    if errors:
        print("\n✗ VALIDATION FAILED:")
        for err in errors:
            print(f"  - {err}")
        return False

    print("\n✓✓ SUCCESS: OData identifier matches Opteryx baseline structure")
    return True


if __name__ == "__main__":
    success = test_identifier_baseline()
    sys.exit(0 if success else 1)
