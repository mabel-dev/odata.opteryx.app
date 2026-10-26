"""
Unit tests for OData $apply parser.

Tests the parsing of $apply expressions into AST nodes.
"""

import pytest
import sys

sys.path.insert(0, "app/vendored/odata-query")

from odata_query.apply import (
    parse_apply,
    ApplyPipeline,
    GroupByTransform,
    AggregateTransform,
    FilterTransform,
    ComputeTransform,
    ApplyParseError,
)


# ============================================================================
# Basic Aggregate Expression Tests
# ============================================================================


def test_parse_count_aggregate():
    """Test: aggregate($count as total)"""
    result = parse_apply("aggregate($count as total)")

    assert isinstance(result, ApplyPipeline)
    assert len(result.transformations) == 1

    agg = result.transformations[0]
    assert isinstance(agg, AggregateTransform)
    assert len(agg.expressions) == 1

    expr = agg.expressions[0]
    assert expr.property_path is None
    assert expr.method == "count"
    assert expr.alias == "total"


def test_parse_sum_aggregate():
    """Test: aggregate(mass with sum as total_mass)"""
    result = parse_apply("aggregate(mass with sum as total_mass)")

    agg = result.transformations[0]
    expr = agg.expressions[0]

    assert expr.property_path == "mass"
    assert expr.method == "sum"
    assert expr.alias == "total_mass"


def test_parse_average_aggregate():
    """Test: aggregate(price with average as avg_price)"""
    result = parse_apply("aggregate(price with average as avg_price)")

    expr = result.transformations[0].expressions[0]

    assert expr.property_path == "price"
    assert expr.method == "average"
    assert expr.alias == "avg_price"


def test_parse_min_max_aggregates():
    """Test: aggregate(qty with min as min_qty, qty with max as max_qty)"""
    result = parse_apply("aggregate(qty with min as min_qty, qty with max as max_qty)")

    agg = result.transformations[0]
    assert len(agg.expressions) == 2

    min_expr = agg.expressions[0]
    assert min_expr.property_path == "qty"
    assert min_expr.method == "min"
    assert min_expr.alias == "min_qty"

    max_expr = agg.expressions[1]
    assert max_expr.property_path == "qty"
    assert max_expr.method == "max"
    assert max_expr.alias == "max_qty"


def test_parse_multiple_aggregates():
    """Test: aggregate($count as total, mass with sum as total_mass, mass with average as avg_mass)"""
    result = parse_apply(
        "aggregate($count as total, mass with sum as total_mass, mass with average as avg_mass)"
    )

    agg = result.transformations[0]
    assert len(agg.expressions) == 3

    # Count
    assert agg.expressions[0].property_path is None
    assert agg.expressions[0].method == "count"
    assert agg.expressions[0].alias == "total"

    # Sum
    assert agg.expressions[1].property_path == "mass"
    assert agg.expressions[1].method == "sum"

    # Average
    assert agg.expressions[2].property_path == "mass"
    assert agg.expressions[2].method == "average"


# ============================================================================
# GroupBy Tests
# ============================================================================


def test_parse_simple_groupby():
    """Test: groupby((regionkey))"""
    result = parse_apply("groupby((regionkey))")

    groupby = result.transformations[0]
    assert isinstance(groupby, GroupByTransform)
    assert groupby.properties == ["regionkey"]
    assert groupby.aggregate is None


def test_parse_groupby_with_count():
    """Test: groupby((regionkey), aggregate($count as total))"""
    result = parse_apply("groupby((regionkey), aggregate($count as total))")

    groupby = result.transformations[0]
    assert groupby.properties == ["regionkey"]
    assert groupby.aggregate is not None
    assert len(groupby.aggregate.expressions) == 1
    assert groupby.aggregate.expressions[0].alias == "total"


def test_parse_groupby_multiple_properties():
    """Test: groupby((region, status))"""
    result = parse_apply("groupby((region, status))")

    groupby = result.transformations[0]
    assert groupby.properties == ["region", "status"]


def test_parse_groupby_with_sum():
    """Test: groupby((category), aggregate(price with sum as total_price))"""
    result = parse_apply("groupby((category), aggregate(price with sum as total_price))")

    groupby = result.transformations[0]
    assert groupby.properties == ["category"]
    expr = groupby.aggregate.expressions[0]
    assert expr.property_path == "price"
    assert expr.method == "sum"
    assert expr.alias == "total_price"


def test_parse_groupby_multiple_aggregates():
    """Test: groupby((status), aggregate($count as cnt, revenue with sum as total_rev))"""
    result = parse_apply(
        "groupby((status), aggregate($count as cnt, revenue with sum as total_rev))"
    )

    groupby = result.transformations[0]
    assert len(groupby.aggregate.expressions) == 2


# ============================================================================
# Filter Transform Tests
# ============================================================================


def test_parse_simple_filter():
    """Test: filter(active eq true)"""
    result = parse_apply("filter(active eq true)")

    filter_transform = result.transformations[0]
    assert isinstance(filter_transform, FilterTransform)
    assert filter_transform.filter_expr == "active eq true"


def test_parse_filter_with_nested_parens():
    """Test: filter((status eq 'active') and (count gt 5))"""
    result = parse_apply("filter((status eq 'active') and (count gt 5))")

    filter_transform = result.transformations[0]
    assert filter_transform.filter_expr == "(status eq 'active') and (count gt 5)"


def test_parse_filter_complex():
    """Test: filter(mass gt 10 and number_of_moons lt 50)"""
    result = parse_apply("filter(mass gt 10 and number_of_moons lt 50)")

    filter_transform = result.transformations[0]
    assert "mass gt 10" in filter_transform.filter_expr
    assert "number_of_moons lt 50" in filter_transform.filter_expr


def test_parse_compute():
    """Test: compute(year(published_at) as Year, length(name) as L)"""
    result = parse_apply("compute(year(published_at) as Year, length(name) as L)")

    compute_transform = result.transformations[0]
    assert isinstance(compute_transform, ComputeTransform)
    assert compute_transform.compute_expr == "year(published_at) as Year, length(name) as L"


def test_parse_compute_then_groupby():
    """Test: compute(year(ts) as Year)/groupby((Year), aggregate($count as Count))"""
    result = parse_apply("compute(year(ts) as Year)/groupby((Year), aggregate($count as Count))")

    assert [type(t) for t in result.transformations] == [ComputeTransform, GroupByTransform]
    assert result.transformations[1].properties == ["Year"]


def test_parse_compute_unmatched_parens():
    with pytest.raises(ApplyParseError, match="compute"):
        parse_apply("compute(year(ts) as Year")


# ============================================================================
# Pipeline Tests
# ============================================================================


def test_parse_filter_then_groupby():
    """Test: filter(active eq true)/groupby((status))"""
    result = parse_apply("filter(active eq true)/groupby((status))")

    assert len(result.transformations) == 2

    filter_t = result.transformations[0]
    assert isinstance(filter_t, FilterTransform)
    assert filter_t.filter_expr == "active eq true"

    groupby = result.transformations[1]
    assert isinstance(groupby, GroupByTransform)
    assert groupby.properties == ["status"]


def test_parse_filter_groupby_aggregate():
    """Test: filter(mass gt 10)/groupby((type), aggregate($count as total))"""
    result = parse_apply("filter(mass gt 10)/groupby((type), aggregate($count as total))")

    assert len(result.transformations) == 2

    filter_t = result.transformations[0]
    assert filter_t.filter_expr == "mass gt 10"

    groupby = result.transformations[1]
    assert groupby.properties == ["type"]
    assert groupby.aggregate.expressions[0].alias == "total"


def test_parse_complex_pipeline():
    """Test: filter(active eq true)/groupby((region, status), aggregate($count as cnt, revenue with sum as total))"""
    result = parse_apply(
        "filter(active eq true)/groupby((region, status), aggregate($count as cnt, revenue with sum as total))"
    )

    assert len(result.transformations) == 2

    groupby = result.transformations[1]
    assert len(groupby.properties) == 2
    assert len(groupby.aggregate.expressions) == 2


# ============================================================================
# Production Pattern Tests
# ============================================================================


def test_parse_production_pattern():
    """Test production query: groupby((planetId), aggregate($count as SatelliteCount))"""
    result = parse_apply("groupby((planetId), aggregate($count as SatelliteCount))")

    groupby = result.transformations[0]
    assert groupby.properties == ["planetId"]
    assert groupby.aggregate.expressions[0].alias == "SatelliteCount"


def test_parse_production_multigroup():
    """Test: groupby((region, year), aggregate(sales with sum as total_sales))"""
    result = parse_apply("groupby((region, year), aggregate(sales with sum as total_sales))")

    groupby = result.transformations[0]
    assert groupby.properties == ["region", "year"]
    assert groupby.aggregate.expressions[0].property_path == "sales"


# ============================================================================
# Whitespace Handling Tests
# ============================================================================


def test_parse_with_extra_whitespace():
    """Test parser handles extra whitespace"""
    result = parse_apply("  groupby (  ( regionkey )  ,  aggregate ( $count as total ) )  ")

    groupby = result.transformations[0]
    assert groupby.properties == ["regionkey"]
    assert groupby.aggregate.expressions[0].alias == "total"


def test_parse_no_whitespace():
    """Test parser works without whitespace"""
    result = parse_apply("groupby((regionkey),aggregate($count as total))")

    groupby = result.transformations[0]
    assert groupby.properties == ["regionkey"]


# ============================================================================
# Error Handling Tests
# ============================================================================


def test_parse_empty_expression():
    """Test empty expression raises error"""
    with pytest.raises(ApplyParseError, match="Empty"):
        parse_apply("")


def test_parse_invalid_keyword():
    """Test invalid transformation keyword raises error"""
    with pytest.raises(ApplyParseError, match="Expected 'groupby', 'aggregate', 'filter', or 'compute'"):
        parse_apply("invalid((property))")


def test_parse_invalid_method():
    """Test invalid aggregate method raises error"""
    with pytest.raises(ApplyParseError, match="Invalid aggregate method"):
        parse_apply("aggregate(mass with invalid_method as result)")


def test_parse_missing_paren():
    """Test missing closing paren raises error"""
    with pytest.raises(ApplyParseError, match="Expected '\\)'"):
        parse_apply("groupby((regionkey)")


def test_parse_unmatched_parens_in_filter():
    """Test unmatched parens in filter expression"""
    with pytest.raises(ApplyParseError, match="Unmatched parentheses"):
        parse_apply("filter((status eq 'active')")


def test_parse_missing_alias():
    """Test missing 'as alias' raises error"""
    with pytest.raises(ApplyParseError, match="Expected 'as'"):
        parse_apply("aggregate($count)")


def test_parse_invalid_pipeline_separator():
    """Test invalid character between transformations"""
    with pytest.raises(ApplyParseError, match="Expected '/'"):
        parse_apply("groupby((status)) AND aggregate($count as total)")


# ============================================================================
# Edge Cases
# ============================================================================


def test_parse_single_property_groupby():
    """Test groupby with single property (double parens still required)"""
    result = parse_apply("groupby((status))")
    assert result.transformations[0].properties == ["status"]


def test_parse_case_insensitive_keywords():
    """Test keywords are case-insensitive"""
    result = parse_apply("GROUPBY((status), AGGREGATE($COUNT AS total))")

    groupby = result.transformations[0]
    assert groupby.properties == ["status"]
    assert groupby.aggregate.expressions[0].alias == "total"


def test_parse_underscore_identifiers():
    """Test identifiers with underscores"""
    result = parse_apply("groupby((planet_id), aggregate($count as satellite_count))")

    groupby = result.transformations[0]
    assert groupby.properties == ["planet_id"]
    assert groupby.aggregate.expressions[0].alias == "satellite_count"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
