"""Optimize OData logical plans based on query parameters and constraints.

This module handles optimization of logical plans after they've been built
from OData expressions. It encapsulates strategic decisions about query
execution (e.g., when to fetch only schema + count vs. full data).
"""

from typing import Optional
from typing import Tuple

from opteryx.planner.logical_planner import LogicalPlan


class ODataLogicalPlanOptimizer:
    """Optimize OData logical plans based on constraints and parameters.
    
    Makes strategic decisions about query execution:
    - When to fetch schema + count separately from data
    - Other optimization heuristics specific to OData semantics
    
    This separates optimization logic from core plan building, making
    it easier to understand and test query strategies.
    """

    def optimize_with_constraints(
        self,
        logical_plan: LogicalPlan,
        top: Optional[int],
        count_requested: bool,
    ) -> Tuple[LogicalPlan, dict]:
        """Optimize a logical plan given query constraints.

        Args:
            logical_plan: The logical plan to optimize
            top: The $top limit value (or None for no limit)
            count_requested: Whether $count was requested

        Returns:
            Tuple of (optimized_plan, strategy_info) where strategy_info
            is a dict describing the optimization strategy applied:
                - strategy: name of the optimization (e.g., "schema_count_split")
                - description: human-readable description
                - original_plan: the original plan (for debugging)
        """
        # Check for the "top=0 with count" optimization opportunity
        if self._should_use_schema_count_split(top, count_requested):
            return logical_plan, {
                "strategy": "schema_count_split",
                "description": "top=0 with count_requested: fetch schema and count separately, no data rows",
                "original_plan": logical_plan,
            }

        # No special optimization applied
        return logical_plan, {
            "strategy": "standard",
            "description": "Standard query execution with all constraints applied",
            "original_plan": logical_plan,
        }

    def _should_use_schema_count_split(
        self, top: Optional[int], count_requested: bool
    ) -> bool:
        """Determine if we should use the schema + count split optimization.

        This optimization is beneficial when:
        - top=0 (user only wants count, no data rows)
        - count_requested is True (user actually asked for the count)

        In this case, we can:
        1. Get schema by running query with top=1
        2. Get count by running a count aggregation
        3. Return empty data with count, avoiding large data fetch

        Args:
            top: The $top limit value
            count_requested: Whether count was requested

        Returns:
            True if this optimization should be applied
        """
        return top == 0 and count_requested
