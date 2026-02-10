"""Parse OData query expressions into structured data and AST.

This module handles the "statement -> AST" step for OData query components,
separating the parsing concerns from logical plan building. It converts
OData query strings into normalized, parsed representations that the
ODataLogicalPlanBuilder can use.
"""

import re
from typing import List
from typing import Optional
from typing import Tuple

from odata_query.grammar import ODataLexer
from odata_query.grammar import ODataParser
from opteryx.managers.expression import NodeType
from opteryx.models import Node


class ODataExpressionParser:
    """Parse OData query expressions into AST and structured data.

    Handles all parsing of OData query string components:
    - $filter: WHERE clause expressions
    - $orderby: ORDER BY specifications
    - $select: Column projection specifications
    - $apply: Aggregation pipeline syntax

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
        if not filter_expr:
            return None

        try:
            # Parse OData filter string to AST using odata-query library
            ast = self.parser.parse(self.lexer.tokenize(filter_expr))
            # Convert AST to Opteryx expression node using the AST node's built-in method
            return ast.to_opteryx_node()
        except Exception as e:
            raise ValueError(f"Failed to parse $filter expression: {filter_expr}") from e

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

    def parse_apply(self, apply_expr: str) -> dict:
        """Parse OData $apply expression into aggregation specification.

        Supports two forms:
        1. Global aggregation: aggregate(sum(price) as total, count(*) as cnt)
        2. Group-by aggregation: groupby((col1, col2), aggregate(...))

        Args:
            apply_expr: OData $apply expression string

        Returns:
            dict with keys:
                - type: "aggregate" or "groupby"
                - groupby_cols: List of column names (for groupby only)
                - aggregations: List of dicts with 'function', 'expression', 'alias'

        Raises:
            ValueError: If $apply syntax is invalid
        """
        if not apply_expr:
            return None

        from odata_query.apply import parse_apply

        parse_apply(apply_expr)

        # Try global aggregate pattern: aggregate(sum(price) as total, count(*) as cnt)
        aggregate_only_match = re.match(
            r"^\s*aggregate\s*\(\s*(.*)\s*\)\s*$", apply_expr, re.IGNORECASE
        )
        if aggregate_only_match:
            aggregate_str = aggregate_only_match.group(1)
            aggregations = self._parse_aggregates(aggregate_str)
            return {
                "type": "aggregate",
                "groupby_cols": [],
                "aggregations": aggregations,
            }

        # Try groupby pattern: groupby((col1, col2), aggregate(...))
        groupby_match = re.match(
            r"groupby\s*\(\s*\((.*?)\)\s*,\s*aggregate\s*\((.*)\)\s*\)",
            apply_expr,
            re.IGNORECASE,
        )
        if groupby_match:
            groupby_cols_str = groupby_match.group(1)
            aggregate_str = groupby_match.group(2)
            groupby_cols = [col.strip() for col in groupby_cols_str.split(",")]
            aggregations = self._parse_aggregates(aggregate_str)
            return {
                "type": "groupby",
                "groupby_cols": groupby_cols,
                "aggregations": aggregations,
            }

        raise ValueError(f"Invalid $apply syntax: '{apply_expr}'")

    def _parse_aggregates(self, aggregate_str: str) -> List[dict]:
        """Parse OData aggregate expressions.

        Args:
            aggregate_str: String like "sum(price) as total, count(*) as cnt"

        Returns:
            List of dicts with 'function', 'expression', and 'alias' keys
        """
        aggregates = []

        # Split by comma (but be careful about nested parens)
        parts = []
        current = ""
        paren_depth = 0
        for char in aggregate_str:
            if char == "(":
                paren_depth += 1
            elif char == ")":
                paren_depth -= 1
            elif char == "," and paren_depth == 0:
                parts.append(current.strip())
                current = ""
                continue
            current += char
        if current.strip():
            parts.append(current.strip())

        # Parse each aggregate: "sum(price) as total" or "count(*)"
        for part in parts:
            agg_match = re.match(r"(\w+)\s*\(\s*(.*?)\s*\)(?:\s+as\s+(\w+))?", part, re.IGNORECASE)
            if not agg_match:
                raise ValueError(f"Invalid aggregate expression: '{part}'")

            func_name = agg_match.group(1).upper()
            expr_str = agg_match.group(2).strip()
            alias = agg_match.group(3) or func_name.lower()

            # Parse the expression inside the aggregate
            if expr_str == "*":
                # count(*) - pass None for expr
                expr_node = None
            else:
                # For simple identifiers, create a Node
                expr_node = Node(node_type=NodeType.IDENTIFIER, source_column=expr_str)

            aggregates.append(
                {
                    "function": func_name,
                    "expression": expr_node,
                    "alias": alias,
                }
            )

        return aggregates
