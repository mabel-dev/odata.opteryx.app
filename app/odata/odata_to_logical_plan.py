"""Convert OData v4 query parameters to Opteryx logical plans.

This module provides comprehensive conversion from OData query parameters
($filter, $select, $orderby, $top, $skip, $expand, $apply) to Opteryx
logical plan nodes, enabling proper query optimization through Opteryx's
optimization pipeline.

The conversion flow is:
    OData parameters → Parser → AST → Logical Plan Nodes → Opteryx Optimizer
"""

import re
import sys
from pathlib import Path
from typing import List
from typing import Optional
from typing import Tuple

# CRITICAL: Set up vendored path BEFORE importing odata_query
_vendored_path = str(Path(__file__).parent.parent / "vendored" / "odata-query")
sys.path.insert(0, _vendored_path)

from odata_query.grammar import ODataLexer
from odata_query.grammar import ODataParser
from opteryx.managers.expression import NodeType
from opteryx.models import Node
from opteryx.models.logical_column import LogicalColumn
from opteryx.planner.logical_planner import LogicalPlan
from opteryx.planner.logical_planner import LogicalPlanNode
from opteryx.planner.logical_planner import LogicalPlanStepType
from orso.tools import random_string


class ODataLogicalPlanBuilder:
    """Builds Opteryx logical plans from OData query parameters.

    This builder constructs a logical plan that represents the complete
    query semantics without SQL string generation. The plan can then be
    passed to Opteryx for optimization and execution.
    """

    def __init__(self, table_name: str):
        """Initialize the builder for a specific table.

        Args:
            table_name: Fully qualified table name (workstream.collection.dataset)
        """
        self.table_name = table_name
        self.plan = LogicalPlan()
        self.current_step_id: Optional[str] = None

    def build(
        self,
        filter_expr: Optional[str] = None,
        select_cols: Optional[str] = None,
        orderby_expr: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        expand_expr: Optional[str] = None,
        apply_expr: Optional[str] = None,
    ) -> LogicalPlan:
        """Build a complete logical plan from OData parameters.

        Args:
            filter_expr: OData $filter expression
            select_cols: OData $select expression (comma-separated columns)
            orderby_expr: OData $orderby expression
            top: OData $top limit value
            skip: OData $skip offset value
            expand_expr: OData $expand expression (navigation properties)
            apply_expr: OData $apply expression (aggregations)

        Returns:
            A LogicalPlan ready for Opteryx optimization
        """
        # Start with a Scan node to read the table
        self.current_step_id = self._add_scan_node()

        # Apply WHERE clause from $filter
        if filter_expr:
            self.current_step_id = self._add_filter_node(filter_expr)

        # Apply JOINs from $expand (must come before aggregations)
        if expand_expr:
            self.current_step_id = self._add_expand_nodes(expand_expr)

        # Apply aggregations from $apply
        if apply_expr:
            self.current_step_id = self._add_apply_nodes(apply_expr)

        # Apply column projection (must come before ORDER BY)
        if select_cols:
            self.current_step_id = self._add_project_node(select_cols, orderby_expr)

        # Apply ORDER BY (after projection so ordered columns are available)
        if orderby_expr:
            self.current_step_id = self._add_order_node(orderby_expr)

        # Apply LIMIT and OFFSET
        if top is not None or skip is not None:
            self.current_step_id = self._add_limit_node(top, skip)

        # Add exit node
        self._add_exit_node()

        return self.plan

    def _add_node(self, node: LogicalPlanNode) -> str:
        """Add a node to the plan and connect it to the previous node.

        Args:
            node: The LogicalPlanNode to add

        Returns:
            The step_id of the newly added node
        """
        step_id = random_string()
        self.plan.add_node(step_id, node)

        if self.current_step_id is not None:
            self.plan.add_edge(self.current_step_id, step_id)

        return step_id

    def _add_scan_node(self) -> str:
        """Add a Scan node to read the table."""
        scan_node = LogicalPlanNode(node_type=LogicalPlanStepType.Scan)
        scan_node.relation = self.table_name
        scan_node.alias = self.table_name.split(".")[-1]  # Use dataset name as alias
        scan_node.hints = []
        # Don't set columns - binder will populate it

        return self._add_node(scan_node)

    def _add_filter_node(self, filter_expr: str) -> str:
        """Add a Filter node for WHERE clause.

        Args:
            filter_expr: OData $filter expression string

        Returns:
            Step ID of the filter node
        """
        if not filter_expr:
            return self.current_step_id

        filter_node = LogicalPlanNode(node_type=LogicalPlanStepType.Filter)

        # Convert OData filter string to Opteryx expression
        # This will be implemented to parse the filter and build expression nodes
        condition = self._parse_filter_expression(filter_expr)
        if condition is None:
            # If parsing returned None, skip the filter node
            return self.current_step_id

        filter_node.condition = condition
        # Filter passes through all columns (doesn't change column set)
        filter_node.columns = [Node(node_type=NodeType.WILDCARD)]

        return self._add_node(filter_node)

    def _add_expand_nodes(self, expand_expr: str) -> str:
        """Add Join nodes for OData $expand (navigation properties).

        Args:
            expand_expr: OData $expand expression string

        Returns:
            Step ID of the last join node
        """
        # $expand syntax: expand=Orders($filter=Amount gt 100)&expand=Products
        # This maps to SQL JOINs in Opteryx
        # Implementation to follow
        current_id = self.current_step_id
        # TODO: Parse expand expression and create Join nodes
        return current_id

    def _add_apply_nodes(self, apply_expr: str) -> str:
        """Add AggregateAndGroup nodes for OData $apply (data aggregation).

        Args:
            apply_expr: OData $apply expression string

        Returns:
            Step ID of the aggregate node

        Syntax:
            groupby((column1, column2), aggregate(sum(price) as total, count(*) as cnt))

        Supported aggregates:
            - sum(expr) - SUM aggregate
            - count(*) or count(expr) - COUNT aggregate
            - avg(expr) - AVERAGE aggregate
            - min(expr) - MINIMUM aggregate
            - max(expr) - MAXIMUM aggregate
        """
        # Parse groupby expression: groupby((col1, col2), aggregate(...))
        # Extract groupby columns
        groupby_match = re.match(
            r"groupby\s*\(\s*\((.*?)\)\s*,\s*aggregate\s*\((.*)\)\s*\)", apply_expr, re.IGNORECASE
        )
        if not groupby_match:
            raise ValueError(f"Invalid $apply syntax: '{apply_expr}'")

        groupby_cols_str = groupby_match.group(1)
        aggregate_str = groupby_match.group(2)

        # Parse groupby columns: "col1, col2, col3"
        groupby_cols = [col.strip() for col in groupby_cols_str.split(",")]

        # Parse aggregates: "sum(price) as total, count(*) as cnt, avg(amount)"
        aggregates = self._parse_aggregates(aggregate_str)

        # Build AggregateAndGroup node
        agg_node = LogicalPlanNode(node_type=LogicalPlanStepType.AggregateAndGroup)

        # Set groupby columns (as Node objects - identifiers)
        agg_node.group_by = [
            Node(node_type=NodeType.IDENTIFIER, source_column=col) for col in groupby_cols
        ]

        # Set aggregates
        agg_node.aggregations = aggregates

        return self._add_node(agg_node)

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

    def _add_order_node(self, orderby_expr: str) -> str:
        """Add an Order node for ORDER BY clause.

        Args:
            orderby_expr: OData $orderby expression (e.g., "name desc, age asc")

        Returns:
            Step ID of the order node
        """
        order_node = LogicalPlanNode(node_type=LogicalPlanStepType.Order)

        # Parse orderby expression: "col1 desc, col2 asc"
        # _parse_orderby_expression returns List[Tuple[str, str]] of (column_name, direction)
        orderby_tuples = self._parse_orderby_expression(orderby_expr)

        # Convert to format Opteryx expects: List[Tuple[Node, bool]]
        # where bool is True for ascending, False for descending
        order_by_nodes = []
        for column_name, direction in orderby_tuples:
            # Create a LogicalColumn node for the column
            col_node = LogicalColumn(
                node_type=NodeType.IDENTIFIER,
                source_column=column_name,
                source=None,  # Will be bound during optimization
                alias=None,
            )
            col_node.query_column = column_name  # Required for binder
            # direction is "asc" or "desc", convert to boolean (True=asc, False=desc)
            is_ascending = direction.lower() == "asc"
            order_by_nodes.append((col_node, is_ascending))

        order_node.order_by = order_by_nodes
        # Don't set columns - binder will populate it

        return self._add_node(order_node)

    def _add_limit_node(self, top: Optional[int], skip: Optional[int]) -> str:
        """Add a Limit node for pagination.

        Args:
            top: OData $top value (limit)
            skip: OData $skip value (offset)

        Returns:
            Step ID of the limit node
        """
        limit_node = LogicalPlanNode(node_type=LogicalPlanStepType.Limit)

        if top is not None:
            limit_node.limit = top

        if skip is not None:
            limit_node.offset = skip

        # Don't set columns - binder will populate it

        return self._add_node(limit_node)

    def _add_project_node(self, select_cols: str, orderby_expr: Optional[str] = None) -> str:
        """Add a Project node for column selection.

        Args:
            select_cols: OData $select expression (comma-separated columns)
            orderby_expr: OData $orderby expression to determine order_by_columns

        Returns:
            Step ID of the project node
        """
        if not select_cols:
            return self.current_step_id

        project_node = LogicalPlanNode(node_type=LogicalPlanStepType.Project)

        # Parse select expression: "col1, col2, col3" or "*"
        # "*" means all columns (represented as a WILDCARD node)
        # Each column becomes a Node(NodeType.IDENTIFIER) or Node(NodeType.WILDCARD)
        columns = []
        selected_column_names = set()

        # Check if this is a wildcard select
        if select_cols.strip() == "*":
            # Use WILDCARD node to represent all columns
            wildcard_node = Node(node_type=NodeType.WILDCARD)
            wildcard_node.except_columns = None  # No exclusions for basic wildcard
            columns.append(wildcard_node)
            # With wildcard, all columns are selected, so order_by_columns will be empty
        else:
            # Parse individual columns
            for col in select_cols.split(","):
                col_name = col.strip()
                selected_column_names.add(col_name)
                col_node = LogicalColumn(
                    node_type=NodeType.IDENTIFIER,
                    source_column=col_name,
                    alias=None,  # OData doesn't support field aliasing in $select
                )
                col_node.query_column = col_name
                columns.append(col_node)

        project_node.columns = columns

        # Compute order_by_columns: columns in ORDER BY but not in SELECT
        # This allows ordering by columns that aren't in the final projection
        order_by_columns = []
        if orderby_expr and select_cols.strip() != "*":
            # Parse the orderby expression to extract column names
            orderby_tuples = self._parse_orderby_expression(orderby_expr)
            for col_name, _ in orderby_tuples:
                if col_name not in selected_column_names:
                    # This column is needed for ordering but not in projection
                    col_node = LogicalColumn(
                        node_type=NodeType.IDENTIFIER,
                        source_column=col_name,
                        alias=None,
                    )
                    col_node.query_column = col_name
                    order_by_columns.append(col_node)

        project_node.order_by_columns = order_by_columns
        # Initialize except_columns as empty list (for EXCEPT modifier if needed)
        project_node.except_columns = []

        return self._add_node(project_node)

    def _add_exit_node(self) -> str:
        """Add an Exit node to mark the end of the plan."""
        exit_node = LogicalPlanNode(node_type=LogicalPlanStepType.Exit)
        # Use WILDCARD node to represent "*" - all columns
        # This tells the optimizer we want all columns from the previous step
        exit_node.columns = [Node(node_type=NodeType.WILDCARD)]
        return self._add_node(exit_node)

    # Expression Parsing Methods

    def _parse_filter_expression(self, filter_expr: str) -> Node:
        """Convert an OData filter expression to an Opteryx expression node.

        Args:
            filter_expr: OData filter expression (e.g., "vendor eq 'microsoft'")

        Returns:
            An expression Node representing the filter

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

        # Parse OData filter string to AST using odata-query library
        lexer = ODataLexer()
        parser = ODataParser()
        ast = parser.parse(lexer.tokenize(filter_expr))

        # Convert AST to Opteryx expression node
        return self._odata_ast_to_opteryx_node(ast)

    def _odata_ast_to_opteryx_node(self, ast_node) -> Node:
        """Convert OData AST to Opteryx node using the AST node's built-in method."""
        if ast_node is None:
            return None

        # Use the AST node's own conversion method
        return ast_node.to_opteryx_node()

    def _parse_orderby_expression(self, orderby_expr: str) -> List[Tuple[str, str]]:
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

    def get_plan(self) -> LogicalPlan:
        """Get the constructed logical plan.

        Returns:
            The LogicalPlan ready for Opteryx optimization
        """
        return self.plan

    def _print_ast(self, node, indent=0):
        """Recursively print AST node structure for debugging."""
        prefix = "  " * indent
        if node is None:
            print(f"{prefix}None")
            return

        node_type = type(node).__name__
        print(f"{prefix}{node_type}")

        # Print relevant attributes based on node type
        if hasattr(node, "__dict__"):
            for key, value in node.__dict__.items():
                if key.startswith("_"):
                    continue
                if isinstance(value, (str, int, float, bool)):
                    print(f"{prefix}  {key}: {value}")
                elif isinstance(value, list):
                    if value:
                        print(f"{prefix}  {key}:")
                        for item in value:
                            self._print_ast(item, indent + 2)
                elif hasattr(value, "__dict__"):
                    print(f"{prefix}  {key}:")
                    self._print_ast(value, indent + 2)
                else:
                    print(f"{prefix}  {key}: {type(value).__name__}")

    def __repr__(self) -> str:
        return f"ODataLogicalPlanBuilder(table={self.table_name}, nodes={len(self.plan.nodes())})"


def build_logical_plan(
    table_name: str,
    filter_expr: Optional[str] = None,
    select_cols: Optional[str] = None,
    orderby_expr: Optional[str] = None,
    top: Optional[int] = None,
    skip: Optional[int] = None,
    expand_expr: Optional[str] = None,
    apply_expr: Optional[str] = None,
) -> LogicalPlan:
    """Convenience function to build a logical plan from OData parameters.

    Args:
        table_name: Fully qualified table name
        filter_expr: OData $filter expression
        select_cols: OData $select expression
        orderby_expr: OData $orderby expression
        top: OData $top value
        skip: OData $skip value
        expand_expr: OData $expand expression
        apply_expr: OData $apply expression

    Returns:
        A LogicalPlan ready for Opteryx
    """
    builder = ODataLogicalPlanBuilder(table_name)
    return builder.build(
        filter_expr=filter_expr,
        select_cols=select_cols,
        orderby_expr=orderby_expr,
        top=top,
        skip=skip,
        expand_expr=expand_expr,
        apply_expr=apply_expr,
    )
