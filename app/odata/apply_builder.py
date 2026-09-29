"""Convert parsed $apply AST into Opteryx logical plan nodes.

This module provides a small builder that maps the vendored $apply AST
(`parse_apply`) into Opteryx typed plan steps by mutating the
`ODataLogicalPlanBuilder` (adding nodes via its `_add_node` method).

Only a focused subset is implemented: FilterTransform, GroupByTransform,
AggregateTransform and the common aggregate methods (COUNT, SUM, AVG,
MIN, MAX). The builder is intentionally small and relies on the
`ODataLogicalPlanBuilder` to supply helper methods for filters and node
insertion.

OData's aggregate method names are NOT the engine's aggregator names, and
where they coincide it is a coincidence -- see `_METHOD_TO_AGGREGATOR`.
"""

from typing import Any
from typing import Optional

from odata_query.apply import parse_apply, AggregateTransform, GroupByTransform, FilterTransform
from opteryx.compiled.structures.expressions import Aggregator
from opteryx.compiled.structures.expressions import LogicalColumn
from opteryx.compiled.structures.expressions import Wildcard
from opteryx.compiled.structures.plan_steps import AggregateAndGroupStep
from opteryx.compiled.structures.plan_steps import AggregateStep
from opteryx.expression import NodeType


# OData $apply aggregate method -> Opteryx aggregator name (opteryx.operators
# .aggregate.helpers.AGGREGATORS). Spelled out in full rather than relying on
# `method.upper()`: four of the five happen to uppercase to the engine's own
# name, but OData's "average" is the engine's "AVG", and uppercasing alone
# produced an unregistered "AVERAGE" aggregator -- which surfaced as a 500,
# not the 400 an unsupported shape earns. Anything the parser can emit must
# have an entry here; an unmapped method is a ValueError (a 400), never a
# mistranslation. Keys are the parser's own lowercase spellings
# (odata_query.apply: "count", "sum", "average", "min", "max").
_METHOD_TO_AGGREGATOR: dict = {
    "count": "COUNT",
    "sum": "SUM",
    "average": "AVG",
    "min": "MIN",
    "max": "MAX",
}


def _aggregator_for(method: str) -> str:
    """Resolve an OData aggregate method to its Opteryx aggregator name."""
    try:
        return _METHOD_TO_AGGREGATOR[method.lower()]
    except KeyError:
        raise ValueError(
            f"Unsupported aggregate method '{method}'. "
            f"Expected: {', '.join(sorted(_METHOD_TO_AGGREGATOR))}"
        ) from None


def _build_aggregate_node(func: str, prop: Optional[str], alias: str, arena) -> Aggregator:
    """Build an Aggregator expression for one $apply aggregate expression.

    `prop` is None for COUNT(*) (OData's "$count"); every other function takes
    a single column-identifier parameter.
    """
    if prop is None:
        parameter = Wildcard(arena=arena)
    else:
        parameter = LogicalColumn(node_type=NodeType.IDENTIFIER, source_column=prop, source=None, alias=None, arena=arena)
        parameter.query_column = prop
    return Aggregator(value=func, parameters=[parameter], alias=alias, arena=arena)


class ApplyLogicalPlanBuilder:
    """Build logical plan nodes from a parsed $apply pipeline."""

    def __init__(self) -> None:
        pass

    def apply_pipeline_to_builder(self, odata_builder: Any, apply_expr: str) -> str:
        """Parse `apply_expr` and apply transformations to `odata_builder`.

        Args:
            odata_builder: an instance of `ODataLogicalPlanBuilder` with methods
                `_add_node`, `_add_filter_node`, and attribute `current_step_id`.
            apply_expr: raw $apply expression string

        Returns:
            step_id of the last node added
        """
        pipeline = parse_apply(apply_expr)

        last_step = odata_builder.current_step_id

        for transform in pipeline.transformations:
            if isinstance(transform, FilterTransform):
                # Use existing filter helper on builder
                odata_builder.current_step_id = odata_builder._add_filter_node(transform.filter_expr)
                last_step = odata_builder.current_step_id

            elif isinstance(transform, GroupByTransform):
                # Group columns
                group_cols = []
                for col in transform.properties:
                    group_col = LogicalColumn(node_type=NodeType.IDENTIFIER, source_column=col, source=None, alias=None, arena=odata_builder.arena)
                    group_col.query_column = col
                    group_cols.append(group_col)

                # Aggregates (may be None)
                aggs = []
                if transform.aggregate:
                    for expr in transform.aggregate.expressions:
                        func = _aggregator_for(expr.method)
                        prop = expr.property_path  # None for $count
                        aggs.append(_build_aggregate_node(func, prop, expr.alias, odata_builder.arena))

                agg_node = AggregateAndGroupStep()
                agg_node.groups = group_cols
                agg_node.aggregates = aggs
                # Required by the physical planner for AggregateAndGroup nodes:
                # the full output column list (groups + aggregates), in order.
                agg_node.projection = group_cols + aggs
                last_step = odata_builder._add_node(agg_node)
                odata_builder.current_step_id = last_step
                # See ODataLogicalPlanBuilder._pending_aggregate_columns: Exit
                # can't resolve a wildcard against this node's output directly.
                odata_builder._pending_aggregate_columns = group_cols + aggs

            elif isinstance(transform, AggregateTransform):
                aggs = []
                for expr in transform.expressions:
                    func = _aggregator_for(expr.method)
                    prop = expr.property_path
                    prop = None if prop == "$count" else prop
                    aggs.append(_build_aggregate_node(func, prop, expr.alias, odata_builder.arena))

                # No group by -> global aggregation; use the ungrouped "Aggregate"
                # step type (rather than AggregateAndGroup), which doesn't
                # require a projection.
                agg_node = AggregateStep()
                agg_node.groups = []
                agg_node.aggregates = aggs
                last_step = odata_builder._add_node(agg_node)
                odata_builder.current_step_id = last_step
                odata_builder._pending_aggregate_columns = aggs

            else:
                transform_type = type(transform).__name__
                raise ValueError(f"Unsupported $apply transformation: {transform_type}")

        return last_step
