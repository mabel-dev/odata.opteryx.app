"""Convert OData v4 query parameters to Opteryx logical plans.

This module provides comprehensive conversion from OData query parameters
($filter, $select, $orderby, $top, $skip, $expand, $apply) to Opteryx
logical plan nodes, enabling proper query optimization through Opteryx's
optimization pipeline.

The conversion flow is:
    OData parameters → ExpressionParser (AST) → LogicalPlanBuilder → LogicalPlan
"""

from typing import List
from typing import Optional

from opteryx.expression import NodeType
from opteryx.models import Node
from opteryx.models.logical_column import LogicalColumn
from opteryx.planner.logical_planner import LogicalPlan
from opteryx.planner.logical_planner import LogicalPlanNode
from opteryx.planner.logical_planner import LogicalPlanStepType
from opteryx.utils import random_string

from app.odata.expression_parser import ODataExpressionParser


class ODataLogicalPlanBuilder:
    """Builds Opteryx logical plans from OData query parameters.

    This builder constructs a logical plan that represents the complete
    query semantics without SQL string generation. The plan can then be
    passed to Opteryx for optimization and execution.

    The builder focuses on plan construction from structured inputs.
    Expression parsing is delegated to ODataExpressionParser.
    """

    def __init__(self, table_name: str):
        """Initialize the builder for a specific table.

        Args:
            table_name: Fully qualified table name (workstream.collection.dataset)
        """
        self.table_name = table_name
        self.plan = LogicalPlan()
        self.current_step_id: Optional[str] = None
        self.expression_parser = ODataExpressionParser()
        # Output columns of the most recent aggregate step, pending consumption by
        # Exit (or a subsequent Project). Opteryx's Exit binder unconditionally
        # drops the "$derived" schema (where aggregate outputs live) before
        # resolving its own wildcard, so a bare Aggregate -> Exit plan built with
        # a wildcard would lose the aggregate's columns. Referencing the aggregate
        # Node objects directly (set here) sidesteps that lookup entirely.
        self._pending_aggregate_columns: Optional[List[Node]] = None

    def build(
        self,
        filter_expr: Optional[str] = None,
        select_cols: Optional[str] = None,
        orderby_expr: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        expand_expr: Optional[str] = None,
        apply_expr: Optional[str] = None,
        count_only: bool = False,
    ) -> LogicalPlan:
        """Build a complete logical plan from OData parameters.

        If `count_only` is True, the builder will construct a plan that
        returns a single COUNT(*) aggregate (after any filters/applies).

        OData query evaluation order (per Data Aggregation extension §3):
        - Scan → Expand → Apply → Filter → Select → OrderBy → Limit → Exit

        Note: When $apply is present, $filter applies to the aggregated output.
        When $apply is absent, $filter applies to the input rows.

        Args:
            filter_expr: OData $filter expression
            select_cols: OData $select expression (comma-separated columns)
            orderby_expr: OData $orderby expression
            top: OData $top limit value
            skip: OData $skip offset value
            expand_expr: OData $expand expression (navigation properties)
            apply_expr: OData $apply expression (aggregations)
            count_only: If True, build a COUNT(*) aggregate plan

        Returns:
            A LogicalPlan ready for Opteryx optimization
        """
        # Start with a Scan node to read the table
        self.current_step_id = self._add_scan_node()

        # Apply JOINs from $expand (must come before aggregations)
        if expand_expr:
            self.current_step_id = self._add_expand_nodes(expand_expr)

        # Apply aggregations from $apply
        if apply_expr:
            self.current_step_id = self._add_apply_nodes(apply_expr)

        # Apply WHERE clause from $filter (after $apply if present, before if absent)
        if filter_expr:
            self.current_step_id = self._add_filter_node(filter_expr)

        # If caller asked for a COUNT-only plan, add the count node and exit.
        # _add_count_node() populates self._pending_aggregate_columns, which
        # _add_exit_node() consumes directly instead of using a wildcard.
        if count_only:
            self.current_step_id = self._add_count_node()
            self._add_exit_node()
            return self.plan

        # Apply column projection (must come before ORDER BY). A bare wildcard
        # $select ("*", or no $select at all) immediately after an aggregation
        # step is left unresolved here — self._pending_aggregate_columns carries
        # the aggregate's output columns through to _add_exit_node instead, for
        # the same "$derived" reasons as the count_only branch above.
        is_wildcard_select = select_cols is not None and select_cols.strip() in ("", "*")
        if select_cols and not (self._pending_aggregate_columns is not None and is_wildcard_select):
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

    def _add_count_node(self) -> str:
        """Add an Aggregate node that computes COUNT(*) for the current plan.

        Returns the new step_id for the count aggregation node.
        """
        # No group by -> global aggregation; use the ungrouped "Aggregate" step
        # type (rather than AggregateAndGroup), which doesn't require a projection.
        agg_node = LogicalPlanNode(node_type=LogicalPlanStepType.Aggregate)
        agg_node.groups = []
        wildcard = Node(node_type=NodeType.WILDCARD)
        count_node = Node(node_type=NodeType.AGGREGATOR, value="COUNT", parameters=[wildcard])
        count_node.alias = "count"
        agg_node.aggregates = [count_node]
        self._pending_aggregate_columns = [count_node]
        return self._add_node(agg_node)

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

        # Parse filter expression using the dedicated expression parser
        condition = self.expression_parser.parse_filter(filter_expr)
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
        # For now, pass through without modifications
        return self.current_step_id

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
        # Delegate parsing + plan construction to ApplyLogicalPlanBuilder, which
        # uses the vendored OData-standard $apply grammar
        # (e.g. ``groupby((country), aggregate(price with sum as total))``).
        # This is the single source of truth for $apply parsing.
        from app.odata.apply_builder import ApplyLogicalPlanBuilder

        try:
            apply_builder = ApplyLogicalPlanBuilder()
            return apply_builder.apply_pipeline_to_builder(self, apply_expr)
        except ValueError as e:
            # If parsing failed, raise with context
            raise ValueError(f"Invalid $apply expression: {apply_expr}") from e

    def _add_order_node(self, orderby_expr: str) -> str:
        """Add an Order node for ORDER BY clause.

        Args:
            orderby_expr: OData $orderby expression (e.g., "name desc, age asc")

        Returns:
            Step ID of the order node
        """
        order_node = LogicalPlanNode(node_type=LogicalPlanStepType.Order)

        # Parse orderby expression using the dedicated expression parser
        orderby_tuples = self.expression_parser.parse_orderby(orderby_expr)

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
            orderby_expr: OData $orderby expression to determine passthrough_columns

        Returns:
            Step ID of the project node
        """
        if not select_cols:
            return self.current_step_id

        # An explicit projection (this method actually running) properly resolves
        # and republishes the aggregate's columns, so Exit no longer needs to
        # reference them directly.
        self._pending_aggregate_columns = None

        # Parse select expression using the dedicated expression parser
        selected_cols_list = self.expression_parser.parse_select(select_cols)

        # If the previous node is an AggregateAndGroup, validate $select columns
        prev_node = None
        if self.current_step_id is not None:
            prev_node = self.plan._nodes.get(self.current_step_id)

        if prev_node is not None and getattr(prev_node, "node_type", None) in (
            LogicalPlanStepType.AggregateAndGroup,
            LogicalPlanStepType.Aggregate,
        ):
            # Build set of allowed columns: group source_column names + aggregate aliases
            allowed = set()
            if getattr(prev_node, "groups", None):
                for g in prev_node.groups:
                    if getattr(g, "source_column", None):
                        allowed.add(g.source_column)
            if getattr(prev_node, "aggregates", None):
                for a in prev_node.aggregates:
                    alias = getattr(a, "alias", None)
                    if alias:
                        allowed.add(alias)

            # If select isn't '*', ensure every requested column is allowed
            if selected_cols_list != ["*"]:
                for col in selected_cols_list:
                    if col not in allowed:
                        raise ValueError(f"$select references column '{col}' which is not available after aggregation. Allowed: {sorted(list(allowed))}")

        project_node = LogicalPlanNode(node_type=LogicalPlanStepType.Project)

        # Convert parsed columns to nodes
        columns = []
        selected_column_names = set(selected_cols_list) if selected_cols_list != ["*"] else set()

        # Check if this is a wildcard select
        if selected_cols_list == ["*"]:
            # Use WILDCARD node to represent all columns
            wildcard_node = Node(node_type=NodeType.WILDCARD)
            wildcard_node.except_columns = None  # No exclusions for basic wildcard
            columns.append(wildcard_node)
            # With wildcard, all columns are selected, so passthrough_columns will be empty
        else:
            # Parse individual columns
            for col_name in selected_cols_list:
                col_node = LogicalColumn(
                    node_type=NodeType.IDENTIFIER,
                    source_column=col_name,
                    alias=None,  # OData doesn't support field aliasing in $select
                )
                col_node.query_column = col_name
                columns.append(col_node)

        project_node.columns = columns

        # Compute passthrough_columns: columns in ORDER BY but not in SELECT
        # This allows ordering by columns that aren't in the final projection
        passthrough_columns = []
        if orderby_expr and selected_cols_list != ["*"]:
            # Parse the orderby expression to extract column names
            orderby_tuples = self.expression_parser.parse_orderby(orderby_expr)
            for col_name, _ in orderby_tuples:
                if col_name not in selected_column_names:
                    # This column is needed for ordering but not in projection
                    col_node = LogicalColumn(
                        node_type=NodeType.IDENTIFIER,
                        source_column=col_name,
                        alias=None,
                    )
                    col_node.query_column = col_name
                    passthrough_columns.append(col_node)

        project_node.passthrough_columns = passthrough_columns
        # Initialize except_columns as empty list (for EXCEPT modifier if needed)
        project_node.except_columns = []

        return self._add_node(project_node)

    def _add_exit_node(self) -> str:
        """Add an Exit node to mark the end of the plan."""
        exit_node = LogicalPlanNode(node_type=LogicalPlanStepType.Exit)
        if self._pending_aggregate_columns is not None:
            # Reference the aggregate's output nodes directly rather than a
            # wildcard — see the note on self._pending_aggregate_columns.
            exit_node.columns = list(self._pending_aggregate_columns)
        else:
            # Use WILDCARD node to represent "*" - all columns
            # This tells the optimizer we want all columns from the previous step
            exit_node.columns = [Node(node_type=NodeType.WILDCARD)]
        return self._add_node(exit_node)

    def get_plan(self) -> LogicalPlan:
        """Get the constructed logical plan.

        Returns:
            The LogicalPlan ready for Opteryx optimization
        """
        return self.plan

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
