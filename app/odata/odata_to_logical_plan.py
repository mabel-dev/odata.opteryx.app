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

from opteryx.compiled.structures.expressions import Aggregator
from opteryx.compiled.structures.expressions import Expression
from opteryx.compiled.structures.expressions import LogicalColumn
from opteryx.compiled.structures.expressions import Wildcard
from opteryx.compiled.structures.plan_steps import AggregateStep
from opteryx.compiled.structures.plan_steps import ExitStep
from opteryx.compiled.structures.plan_steps import FilterStep
from opteryx.compiled.structures.plan_steps import LimitStep
from opteryx.compiled.structures.plan_steps import OrderStep
from opteryx.compiled.structures.plan_steps import PlanStep
from opteryx.compiled.structures.plan_steps import ProjectStep
from opteryx.compiled.structures.plan_steps import ScanStep
from opteryx.expression import NodeType
from opteryx.planner.logical_planner import LogicalPlan
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

    def __init__(
        self,
        table_name: str,
        version: Optional[int] = None,
        version_tag: Optional[str] = None,
    ):
        """Initialize the builder for a specific table.

        Args:
            table_name: Fully qualified table name (workstream.collection.dataset)
            version: Explicit snapshot id (0 is the engine's sentinel for
                VERSION AS OF PREVIOUS), or None to read the current snapshot.
            version_tag: Tag name (including "current"), or None.
        """
        self.table_name = table_name
        self.version = version
        self.version_tag = version_tag
        self.plan = LogicalPlan()
        self.current_step_id: Optional[str] = None
        self.expression_parser = ODataExpressionParser()
        # Output columns of the most recent aggregate step, pending consumption by
        # Exit (or a subsequent Project). Opteryx's Exit binder unconditionally
        # drops the "$derived" schema (where aggregate outputs live) before
        # resolving its own wildcard, so a bare Aggregate -> Exit plan built with
        # a wildcard would lose the aggregate's columns. Referencing the aggregate
        # expressions directly (set here) sidesteps that lookup entirely.
        self._pending_aggregate_columns: Optional[List[Expression]] = None
        # Columns an explicit (non-wildcard) $select asked for, in the order the
        # caller wrote them. Consumed by _add_exit_node. A wildcard Exit would
        # emit every column the Project node carried -- including the
        # passthrough columns that exist only to keep ORDER BY working -- so
        # Exit has to name the $select columns itself for $select to actually
        # restrict the response (OData v4.01 Part 1 §11.2.4.1).
        self._exit_columns: Optional[List[Expression]] = None

    def build(
        self,
        filter_expr: Optional[str] = None,
        select_cols: Optional[str] = None,
        orderby_expr: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        expand_expr: Optional[str] = None,
        apply_expr: Optional[str] = None,
        compute_expr: Optional[str] = None,
        count_only: bool = False,
    ) -> LogicalPlan:
        """Build a complete logical plan from OData parameters.

        If `count_only` is True, the builder will construct a plan that
        returns a single COUNT(*) aggregate (after any filters/applies).

        OData query evaluation order (per Data Aggregation extension §3):
        - Scan → Expand → Apply → Compute → Filter → Select → OrderBy → Limit → Exit

        Note: When $apply is present, $filter applies to the aggregated output.
        When $apply is absent, $filter applies to the input rows. $compute is
        placed before $filter (and $select/$orderby, further down) so a
        computed alias is usable by all three -- and, for the same "$derived"
        schema reasons _pending_aggregate_columns exists for, $compute is not
        supported combined with $apply in this version (raises ValueError).

        Args:
            filter_expr: OData $filter expression
            select_cols: OData $select expression (comma-separated columns)
            orderby_expr: OData $orderby expression
            top: OData $top limit value
            skip: OData $skip offset value
            expand_expr: OData $expand expression (navigation properties)
            apply_expr: OData $apply expression (aggregations)
            compute_expr: OData $compute expression (computed properties)
            count_only: If True, build a COUNT(*) aggregate plan

        Returns:
            A LogicalPlan ready for Opteryx optimization

        Raises:
            ValueError: `compute_expr` is set together with `apply_expr`, or
                either expression fails to parse.
        """
        # Start with a Scan node to read the table
        self.current_step_id = self._add_scan_node()

        # Apply JOINs from $expand (must come before aggregations)
        if expand_expr:
            self.current_step_id = self._add_expand_nodes(expand_expr)

        # Apply aggregations from $apply
        if apply_expr:
            self.current_step_id = self._add_apply_nodes(apply_expr)

        # Introduce $compute's aliased properties before anything that might
        # reference them ($filter, $select, $orderby). Not supported combined
        # with $apply yet: a compute referencing an aggregate alias would hit
        # the same "$derived" schema resolution problem
        # _pending_aggregate_columns works around for count_only/wildcard
        # $select, and that combination hasn't been designed on purpose yet.
        if compute_expr and compute_expr.strip():
            if apply_expr:
                raise ValueError(
                    "$compute cannot be combined with $apply in this version"
                )
            self.current_step_id = self._add_compute_node(compute_expr)

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
        count_node = Aggregator(value="COUNT", parameters=[Wildcard()], alias="count")
        agg_node = AggregateStep(groups=[], aggregates=[count_node])
        self._pending_aggregate_columns = [count_node]
        return self._add_node(agg_node)

    def _add_node(self, node: PlanStep) -> str:
        """Add a node to the plan and connect it to the previous node.

        Args:
            node: The typed plan step to add

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
        scan_node = ScanStep(
            relation=self.table_name,
            alias=self.table_name.split(".")[-1],  # Use dataset name as alias
            hints=[],
            version=self.version,
            version_tag=self.version_tag,
        )
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

        # Parse filter expression using the dedicated expression parser
        condition = self.expression_parser.parse_filter(filter_expr)
        if condition is None:
            # If parsing returned None, skip the filter node
            return self.current_step_id

        # Filter passes through all columns (doesn't change column set)
        filter_node = FilterStep(condition=condition, columns=[Wildcard()])

        return self._add_node(filter_node)

    def _add_compute_node(self, compute_expr: str) -> str:
        """Add a Project node introducing $compute's aliased expressions.

        Placed right after Scan/Expand/Apply and before Filter -- see the
        pipeline-order note on build() -- carrying every original column
        (wildcard) plus the new aliased expression(s), the same shape
        Opteryx's own SQL planner uses for `SELECT *, b*c AS x FROM t`.

        Args:
            compute_expr: OData $compute expression string

        Returns:
            Step ID of the new project node, or the current step if
            `compute_expr` parses to no clauses (blank/whitespace-only).
        """
        computed_nodes = self.expression_parser.parse_compute(compute_expr)
        if not computed_nodes:
            return self.current_step_id

        project_node = ProjectStep(
            columns=[Wildcard()] + computed_nodes,
            passthrough_columns=[],
            except_columns=[],
        )

        return self._add_node(project_node)

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
            groupby((column1, column2), aggregate($count as cnt, price with sum as total))

        Supported aggregates (see ApplyParser; the list is exhaustive):
            - $count as alias - COUNT(*) over the group
            - expr with sum as alias - SUM aggregate
            - expr with average as alias - AVERAGE aggregate
            - expr with min as alias - MINIMUM aggregate
            - expr with max as alias - MAXIMUM aggregate

        Raises:
            ValueError: if the expression cannot be parsed. ApplyParseError is
                translated deliberately -- it derives from Exception, not
                ValueError, so without this the caller's `except ValueError`
                would miss it and a malformed $apply would surface as a 500.
        """
        # Delegate parsing + plan construction to ApplyLogicalPlanBuilder, which
        # uses the vendored OData-standard $apply grammar
        # (e.g. ``groupby((country), aggregate(price with sum as total))``).
        # This is the single source of truth for $apply parsing.
        from odata_query.apply import ApplyParseError

        from app.odata.apply_builder import ApplyLogicalPlanBuilder

        try:
            apply_builder = ApplyLogicalPlanBuilder()
            return apply_builder.apply_pipeline_to_builder(self, apply_expr)
        except (ValueError, ApplyParseError) as e:
            # If parsing failed, raise with context. The parser's own message
            # names the offending construct (e.g. an invalid aggregate method),
            # which is what makes the resulting 400 actionable.
            raise ValueError(f"Invalid $apply expression: {apply_expr} ({e})") from e

    def _add_order_node(self, orderby_expr: str) -> str:
        """Add an Order node for ORDER BY clause.

        Args:
            orderby_expr: OData $orderby expression (e.g., "name desc, age asc")

        Returns:
            Step ID of the order node
        """
        # Parse orderby expression using the dedicated expression parser
        orderby_tuples = self.expression_parser.parse_orderby(orderby_expr)

        # Convert to format Opteryx expects: List[Tuple[Expression, bool]]
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

        # Don't set columns - binder will populate it
        order_node = OrderStep(order_by=order_by_nodes)

        return self._add_node(order_node)

    def _add_limit_node(self, top: Optional[int], skip: Optional[int]) -> str:
        """Add a Limit node for pagination.

        Args:
            top: OData $top value (limit)
            skip: OData $skip value (offset)

        Returns:
            Step ID of the limit node
        """
        # Don't set columns - binder will populate it
        limit_node = LimitStep(limit=top, offset=skip)

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
            prev_node = self.plan[self.current_step_id]

        if prev_node is not None and prev_node.node_type in (
            LogicalPlanStepType.AggregateAndGroup,
            LogicalPlanStepType.Aggregate,
        ):
            # Build set of allowed columns: group source_column names + aggregate
            # aliases. Both are built by this service (apply_builder /
            # _add_count_node): every group is a LogicalColumn and every aggregate
            # an aliased Aggregator, so both fields are always present.
            allowed = {g.source_column for g in prev_node.groups}
            allowed.update(a.alias for a in prev_node.aggregates)

            # If select isn't '*', ensure every requested column is allowed
            if selected_cols_list != ["*"]:
                for col in selected_cols_list:
                    if col not in allowed:
                        raise ValueError(f"$select references column '{col}' which is not available after aggregation. Allowed: {sorted(list(allowed))}")

        # Convert parsed columns to nodes
        columns = []
        selected_column_names = set(selected_cols_list) if selected_cols_list != ["*"] else set()

        # Check if this is a wildcard select
        if selected_cols_list == ["*"]:
            # Use a Wildcard (no exclusions) to represent all columns
            columns.append(Wildcard())
            # With wildcard, all columns are selected, so passthrough_columns will be empty
        else:
            # Parse individual columns
            exit_columns = []
            for col_name in selected_cols_list:
                col_node = LogicalColumn(
                    node_type=NodeType.IDENTIFIER,
                    source_column=col_name,
                    alias=None,  # OData doesn't support field aliasing in $select
                )
                col_node.query_column = col_name
                columns.append(col_node)

                # A distinct node for Exit: the binder mutates the nodes it
                # binds (visit_project and visit_exit each rewrite the entry
                # they are given), so Project and Exit must not share objects.
                exit_col_node = LogicalColumn(
                    node_type=NodeType.IDENTIFIER,
                    source_column=col_name,
                    alias=None,
                )
                exit_col_node.query_column = col_name
                exit_columns.append(exit_col_node)

            self._exit_columns = exit_columns

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

        # except_columns is empty: OData $select has no EXCEPT modifier
        project_node = ProjectStep(
            columns=columns,
            passthrough_columns=passthrough_columns,
            except_columns=[],
        )

        return self._add_node(project_node)

    def _add_exit_node(self) -> str:
        """Add an Exit node to mark the end of the plan."""
        exit_node = ExitStep()
        if self._pending_aggregate_columns is not None:
            # Reference the aggregate's output nodes directly rather than a
            # wildcard — see the note on self._pending_aggregate_columns.
            exit_node.columns = list(self._pending_aggregate_columns)
        elif self._exit_columns is not None:
            # An explicit $select: name exactly those columns, in the requested
            # order. This is what Opteryx's own SQL planner does (it hands Exit
            # the projection list), and it is what stops ORDER BY passthrough
            # columns reaching the response.
            exit_node.columns = list(self._exit_columns)
        else:
            # A Wildcard represents "*" - all columns
            # This tells the optimizer we want all columns from the previous step
            exit_node.columns = [Wildcard()]
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
    compute_expr: Optional[str] = None,
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
        compute_expr: OData $compute expression

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
        compute_expr=compute_expr,
    )
