"""Convert parsed $apply AST into Opteryx logical plan nodes.

This module provides a small builder that maps the vendored $apply AST
(`parse_apply`) into Opteryx LogicalPlanNodes by mutating the
`ODataLogicalPlanBuilder` (adding nodes via its `_add_node` method).

Only a focused subset is implemented: FilterTransform, GroupByTransform,
AggregateTransform and the common aggregate methods (COUNT, SUM, AVG,
MIN, MAX). The builder is intentionally small and relies on the
`ODataLogicalPlanBuilder` to supply helper methods for filters and node
insertion.
"""

from typing import Any

from odata_query.apply import parse_apply, AggregateTransform, GroupByTransform, FilterTransform
from opteryx.models import Node
from opteryx.expression import NodeType
from opteryx.planner.logical_planner import LogicalPlanNode, LogicalPlanStepType


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
                # Build AggregateAndGroup node
                agg_node = LogicalPlanNode(node_type=LogicalPlanStepType.AggregateAndGroup)

                # Group columns
                group_cols = []
                for col in transform.properties:
                    group_cols.append(Node(node_type=NodeType.IDENTIFIER, source_column=col))
                agg_node.group_by = group_cols

                # Aggregates (may be None)
                aggs = []
                if transform.aggregate:
                    for expr in transform.aggregate.expressions:
                        func = expr.method.upper()
                        prop = expr.property_path  # None for $count
                        alias = expr.alias

                        if prop is None:
                            expr_node = None
                        else:
                            expr_node = Node(node_type=NodeType.IDENTIFIER, source_column=prop)

                        aggs.append({"function": func, "expression": expr_node, "alias": alias})

                agg_node.aggregations = aggs
                last_step = odata_builder._add_node(agg_node)
                odata_builder.current_step_id = last_step

            elif isinstance(transform, AggregateTransform):
                agg_node = LogicalPlanNode(node_type=LogicalPlanStepType.AggregateAndGroup)
                agg_node.group_by = []
                aggs = []
                for expr in transform.expressions:
                    func = expr.method.upper()
                    prop = expr.property_path
                    alias = expr.alias
                    if prop is None or prop == "$count":
                        expr_node = None
                    else:
                        expr_node = Node(node_type=NodeType.IDENTIFIER, source_column=prop)
                    aggs.append({"function": func, "expression": expr_node, "alias": alias})
                agg_node.aggregations = aggs
                last_step = odata_builder._add_node(agg_node)
                odata_builder.current_step_id = last_step

            else:
                # Unknown transform - skip (future work: raise)
                continue

        return last_step
