"""Parse OData query expressions into structured data and AST.

This module handles the "statement -> AST" step for OData query components,
separating the parsing concerns from logical plan building. It converts
OData query strings into normalized, parsed representations that the
ODataLogicalPlanBuilder can use.
"""

import sys
from pathlib import Path
from typing import List
from typing import Optional
from typing import Tuple

# CRITICAL: Set up vendored path BEFORE importing odata_query
# ruff: noqa: E402
_vendored_path = str(Path(__file__).parent.parent / "vendored" / "odata-query")
if _vendored_path not in sys.path:
    sys.path.insert(0, _vendored_path)

from odata_query.grammar import ODataLexer
from odata_query.grammar import ODataParser


class ODataExpressionParser:
    """Parse OData query expressions into AST and structured data.

    Handles parsing of OData query string components:
    - $filter: WHERE clause expressions
    - $orderby: ORDER BY specifications
    - $select: Column projection specifications

    Note: $apply (aggregation) expressions are handled by ApplyLogicalPlanBuilder,
    which uses the vendored odata-query parse_apply() function directly.

    Separates parsing concerns from plan building, allowing reuse
    and clearer separation of responsibilities.
    """

    def __init__(self):
        """Initialize the parser."""
        self.lexer = ODataLexer()
        self.parser = ODataParser()

    def parse_filter(self, filter_expr: str) -> Optional[Node]:
        """Parse an OData $filter expression into an Opteryx expression node.

        Args:
            filter_expr: OData filter expression (e.g., "vendor eq 'microsoft'")

        Returns:
            An expression Node representing the filter, or None if empty

        Example:
            "vendor eq 'microsoft' and price gt 100"
            →
            BinaryOp(
                left=BinaryOp(vendor eq microsoft),
                op=AND,
                right=BinaryOp(price gt 100)
            )
        """
        if not filter_expr or not filter_expr.strip():
            return None

        try:
            # Parse OData filter string to AST using odata-query library
            ast = self.parser.parse(self.lexer.tokenize(filter_expr))
            # Convert AST to Opteryx expression node using the AST node's built-in method
            return ast.to_opteryx_node()
        except NotImplementedError as e:
            # Catch unsupported OData constructs and provide a user-friendly error
            raise ValueError(
                f"Unsupported filter construct: {self._describe_unsupported(ast)}"
            ) from e
        except Exception as e:
            # Try to infer what unsupported construct caused the parse error
            error_msg = self._infer_unsupported_construct(filter_expr, str(e))
            if error_msg:
                raise ValueError(error_msg) from e
            raise ValueError(f"Failed to parse $filter expression: {filter_expr}") from e

    def _describe_unsupported(self, ast_node) -> str:
        """Map AST node types to user-friendly descriptions.

        Searches recursively through the AST to find unsupported constructs.
        """
        # First check the node itself
        node_type = type(ast_node).__name__
        descriptions = {
            "Lambda": "lambda operators (any/all) are not supported",
            "CollectionLambda": "lambda operators (any/all) are not supported",
            "GUID": "GUID literals are not supported",
            "Geography": "geography literals are not supported",
            "Call": "this function is not supported",
        }

        if node_type in descriptions:
            return descriptions[node_type]

        # Search recursively for unsupported constructs in child nodes
        for attr_name in ("left", "right", "operand", "centre", "expression", "owner"):
            if hasattr(ast_node, attr_name):
                child = getattr(ast_node, attr_name)
                if child is not None:
                    result = self._describe_unsupported(child)
                    # If we found something other than the generic message, return it
                    if result != f"{type(child).__name__} is not supported":
                        return result

        return f"{node_type} is not supported"

    def _infer_unsupported_construct(self, filter_expr: str, error_msg: str) -> Optional[str]:
        """Try to infer what unsupported construct caused a parse error.

        Returns a user-friendly error message if we can identify the construct,
        or None if we cannot.
        """
        # Look for known patterns that indicate unsupported constructs
        lower_expr = filter_expr.lower()

        # GUID literals: guid'...'
        if "guid'" in lower_expr:
            return "Unsupported filter construct: GUID literals are not supported"

        # Geography literals: geography'...'
        if "geography'" in lower_expr:
            return "Unsupported filter construct: geography literals are not supported"

        return None

    def parse_orderby(self, orderby_expr: str) -> List[Tuple[str, str]]:
        """Parse OData $orderby expression into column/direction tuples.

        Args:
            orderby_expr: OData orderby expression (e.g., "name desc, age asc")

        Returns:
            List of (column_name, direction) tuples where direction is 'asc' or 'desc'

        Example:
            "name desc, price asc" → [("name", "desc"), ("price", "asc")]
        """
        if not orderby_expr:
            return []

        # Parse comma-separated list of "column [asc|desc]"
        sort_clauses = []
        for clause in orderby_expr.split(","):
            clause = clause.strip()
            parts = clause.split()

            if len(parts) == 1:
                # No direction specified, default to asc
                sort_clauses.append((parts[0], "asc"))
            elif len(parts) == 2:
                column_name, direction = parts
                direction = direction.lower()
                if direction not in ("asc", "desc"):
                    raise ValueError(
                        f"Invalid sort direction '{direction}', must be 'asc' or 'desc'"
                    )
                sort_clauses.append((column_name, direction))
            else:
                raise ValueError(f"Invalid orderby clause: '{clause}'")

        return sort_clauses

    def parse_select(self, select_cols: str) -> List[str]:
        """Parse OData $select expression into column names.

        Args:
            select_cols: OData select expression (e.g., "col1, col2, col3" or "*")

        Returns:
            List of column names, or ["*"] for wildcard selection
        """
        if not select_cols:
            return []

        select_cols = select_cols.strip()
        
        if select_cols == "*":
            return ["*"]
        
        # Split by comma and strip whitespace
        return [col.strip() for col in select_cols.split(",")]

