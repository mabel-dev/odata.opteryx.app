#!/usr/bin/env python3
"""Tests for $filter/$apply evaluation order (WP-02).

Validates that $filter is applied to the output of $apply (aggregated rows),
per OData Data Aggregation §3, instead of to the input rows.
"""

from app.odata.odata_to_logical_plan import ODataLogicalPlanBuilder


def _get_node_names(plan):
    """Extract node type names from a plan."""
    nodes = list((step for _, step in plan.nodes(True)))
    return [
        getattr(n, "node_type", None).name if getattr(n, "node_type", None) else None
        for n in nodes
    ]


class TestFilterApplyOrder:
    """Test correct evaluation order of $filter and $apply."""

    def test_filter_after_apply_when_both_present(self):
        """When both $filter and $apply present, filter should come after apply."""
        builder = ODataLogicalPlanBuilder("public.data.events")
        plan = builder.build(
            filter_expr="cnt gt 5",
            apply_expr="groupby((region), aggregate($count as cnt))",
        )

        node_names = _get_node_names(plan)

        # Verify node sequence
        assert "Scan" in node_names, "Missing Scan node"
        assert "AggregateAndGroup" in node_names, "Missing AggregateAndGroup node"
        assert "Filter" in node_names, "Missing Filter node"

        # Verify order: Apply must come before Filter
        apply_idx = node_names.index("AggregateAndGroup")
        filter_idx = node_names.index("Filter")
        assert apply_idx < filter_idx, "Filter must come after AggregateAndGroup when $apply is present"

    def test_filter_before_apply_when_apply_absent(self):
        """When only $filter present (no $apply), filter should execute before limit."""
        builder = ODataLogicalPlanBuilder("public.data.events")
        plan = builder.build(
            filter_expr="region eq 'US'",
        )

        node_names = _get_node_names(plan)

        assert "Scan" in node_names, "Missing Scan node"
        assert "Filter" in node_names, "Missing Filter node"

        # Verify order: Scan → Filter → ...
        scan_idx = node_names.index("Scan")
        filter_idx = node_names.index("Filter")
        assert scan_idx < filter_idx, "Filter must come after Scan"

    def test_apply_without_filter_unchanged(self):
        """When only $apply present (no $filter), no filter node should be added."""
        builder = ODataLogicalPlanBuilder("public.data.events")
        plan = builder.build(
            apply_expr="groupby((region), aggregate($count as cnt))",
        )

        node_names = _get_node_names(plan)

        assert "Scan" in node_names, "Missing Scan node"
        assert "AggregateAndGroup" in node_names, "Missing AggregateAndGroup node"
        # Count Filter nodes (should be none from top-level, though $apply might have internal ones)
        filter_count = node_names.count("Filter")
        assert filter_count == 0, f"Should have no filter node, got {filter_count}"

    def test_filter_apply_with_select(self):
        """$select validation should work with $filter after $apply."""
        builder = ODataLogicalPlanBuilder("public.data.events")
        plan = builder.build(
            filter_expr="cnt gt 5",
            apply_expr="groupby((region), aggregate($count as cnt))",
            select_cols="region, cnt",
        )

        node_names = _get_node_names(plan)

        # Verify node sequence: Scan → Apply → Filter → Project → Exit
        assert "Scan" in node_names
        assert "AggregateAndGroup" in node_names
        assert "Filter" in node_names
        assert "Project" in node_names

        apply_idx = node_names.index("AggregateAndGroup")
        filter_idx = node_names.index("Filter")
        project_idx = node_names.index("Project")

        assert apply_idx < filter_idx < project_idx, (
            f"Order should be Apply({apply_idx}) → Filter({filter_idx}) → Project({project_idx})"
        )

    def test_filter_apply_with_orderby(self):
        """$orderby validation should work with $filter after $apply."""
        builder = ODataLogicalPlanBuilder("public.data.events")
        plan = builder.build(
            filter_expr="cnt gt 5",
            apply_expr="groupby((region), aggregate($count as cnt))",
            orderby_expr="cnt desc",
        )

        node_names = _get_node_names(plan)

        # Verify node sequence includes Order
        assert "AggregateAndGroup" in node_names
        assert "Filter" in node_names
        assert "Order" in node_names

        apply_idx = node_names.index("AggregateAndGroup")
        filter_idx = node_names.index("Filter")
        order_idx = node_names.index("Order")

        assert apply_idx < filter_idx < order_idx, (
            f"Order should be Apply({apply_idx}) → Filter({filter_idx}) → Order({order_idx})"
        )

    def test_filter_apply_with_pagination(self):
        """$top/$skip should work with $filter after $apply."""
        builder = ODataLogicalPlanBuilder("public.data.events")
        plan = builder.build(
            filter_expr="cnt gt 5",
            apply_expr="groupby((region), aggregate($count as cnt))",
            top=10,
            skip=5,
        )

        node_names = _get_node_names(plan)

        assert "AggregateAndGroup" in node_names
        assert "Filter" in node_names
        assert "Limit" in node_names

        apply_idx = node_names.index("AggregateAndGroup")
        filter_idx = node_names.index("Filter")
        limit_idx = node_names.index("Limit")

        assert apply_idx < filter_idx < limit_idx, (
            f"Order should be Apply({apply_idx}) → Filter({filter_idx}) → Limit({limit_idx})"
        )

    def test_filter_on_aggregate_alias_syntax_valid(self):
        """Filtering on an aggregate alias (e.g., cnt) should be syntactically valid."""
        builder = ODataLogicalPlanBuilder("public.data.events")
        plan = builder.build(
            filter_expr="cnt gt 5",
            apply_expr="groupby((region), aggregate($count as cnt))",
        )

        nodes = list((step for _, step in plan.nodes(True)))
        filter_nodes = [n for n in nodes if getattr(n, "node_type", None) and n.node_type.name == "Filter"]

        assert len(filter_nodes) == 1, "Should have exactly one filter node"
        assert filter_nodes[0].condition is not None, "Filter should have a parsed condition"

    def test_filter_on_group_key_syntax_valid(self):
        """Filtering on a group-key column (e.g., region) should be syntactically valid."""
        builder = ODataLogicalPlanBuilder("public.data.events")
        plan = builder.build(
            filter_expr="region eq 'US'",
            apply_expr="groupby((region), aggregate($count as cnt))",
        )

        nodes = list((step for _, step in plan.nodes(True)))
        filter_nodes = [n for n in nodes if getattr(n, "node_type", None) and n.node_type.name == "Filter"]

        assert len(filter_nodes) == 1, "Should have exactly one filter node"
        assert filter_nodes[0].condition is not None, "Filter should have a parsed condition"

    def test_count_only_with_filter_and_apply(self):
        """COUNT-only plan should work with both $filter and $apply."""
        builder = ODataLogicalPlanBuilder("public.data.events")
        plan = builder.build(
            filter_expr="cnt gt 5",
            apply_expr="groupby((region), aggregate($count as cnt))",
            count_only=True,
        )

        node_names = _get_node_names(plan)

        # For count_only, we should have Apply → Filter → AggregateAndGroup
        assert "Scan" in node_names
        assert "AggregateAndGroup" in node_names
        assert "Filter" in node_names


class TestFilterApplyEdgeCases:
    """Test edge cases."""

    def test_empty_filter_not_added(self):
        """Empty filter_expr should not add a Filter node."""
        builder = ODataLogicalPlanBuilder("public.data.events")
        plan = builder.build(
            filter_expr=None,
            apply_expr="groupby((region), aggregate($count as cnt))",
        )

        node_names = _get_node_names(plan)
        filter_count = node_names.count("Filter")
        assert filter_count == 0, "No Filter node should be added for empty filter_expr"

    def test_filter_without_apply_creates_single_filter(self):
        """Filter without apply should create exactly one Filter node."""
        builder = ODataLogicalPlanBuilder("public.data.events")
        plan = builder.build(
            filter_expr="status eq 'active'",
        )

        nodes = list((step for _, step in plan.nodes(True)))
        filter_nodes = [n for n in nodes if getattr(n, "node_type", None) and n.node_type.name == "Filter"]

        assert len(filter_nodes) == 1, "Should have exactly one Filter node"

    def test_select_validation_with_aggregate_alias(self):
        """$select should validate aggregate aliases when $apply is present."""
        builder = ODataLogicalPlanBuilder("public.data.events")
        # This should succeed - cnt is both in $apply and referenced in $select
        plan = builder.build(
            filter_expr="cnt gt 5",
            apply_expr="groupby((region), aggregate($count as cnt))",
            select_cols="region,cnt",
        )

        node_names = _get_node_names(plan)
        assert "Project" in node_names, "Project node should be present for $select"

    def test_select_validation_rejects_unavailable_column(self):
        """$select should reject columns not available after aggregation."""
        builder = ODataLogicalPlanBuilder("public.data.events")
        # This should fail - amount is not in group_by or aggregations
        with_exception = False
        try:
            plan = builder.build(
                apply_expr="groupby((region), aggregate($count as cnt))",
                select_cols="region,amount",  # amount not available after aggregation
            )
        except ValueError as e:
            with_exception = True
            assert "not available after aggregation" in str(e), f"Unexpected error: {e}"

        assert with_exception, "Should raise ValueError for unavailable column in $select"
