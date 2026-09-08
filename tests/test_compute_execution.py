"""$compute (OData v4.01 §11.2.4.9): computed properties.

These run through execute_odata_query -- the same entry point the HTTP layer
uses -- against the built-in $planets dataset, so they need no catalog or
credentials (the pattern test_select_restricts_response.py already uses for
the same reason). See docs/design/search-and-compute.md for the design this
implements, and app/odata/odata_to_logical_plan.py's build() docstring for
the pipeline-order rationale (compute has to run before filter/select/orderby
so they can reference the new alias).
"""

import json

import pytest

from app.odata.expression_parser import ODataExpressionParser
from app.odata.odata_to_logical_plan import ODataLogicalPlanBuilder
from app.routes.v4.interface import execute_odata_query

TABLE = "$planets"


def _rows(result):
    return json.loads(result["value_bytes"])


class TestParseCompute:
    """Unit coverage of ODataExpressionParser.parse_compute -- the "new" half
    of $compute, since the expression grammar itself is $filter's."""

    def setup_method(self):
        self.parser = ODataExpressionParser()

    def test_empty_and_none_return_no_clauses(self):
        assert self.parser.parse_compute("") == []
        assert self.parser.parse_compute(None) == []

    def test_single_clause_sets_alias(self):
        nodes = self.parser.parse_compute("mass mul gravity as Foo")
        assert len(nodes) == 1
        assert nodes[0].alias == "Foo"

    def test_multiple_clauses_preserve_order(self):
        nodes = self.parser.parse_compute(
            "mass mul gravity as Foo, mass sub gravity as Bar"
        )
        assert [n.alias for n in nodes] == ["Foo", "Bar"]

    def test_comma_inside_function_call_is_not_a_clause_boundary(self):
        nodes = self.parser.parse_compute("contains(name, 'a') as HasA")
        assert [n.alias for n in nodes] == ["HasA"]

    def test_missing_as_raises(self):
        with pytest.raises(ValueError, match="expected"):
            self.parser.parse_compute("mass mul gravity")

    def test_invalid_alias_raises(self):
        with pytest.raises(ValueError, match="valid identifier"):
            self.parser.parse_compute("mass mul gravity as 1bad")

    def test_duplicate_alias_raises(self):
        with pytest.raises(ValueError, match="more than once"):
            self.parser.parse_compute("mass mul gravity as X, mass add 1 as X")

    def test_unparsable_expression_raises(self):
        with pytest.raises(ValueError):
            self.parser.parse_compute("!!! as Bad")


class TestComputePipelinePlacement:
    """The alias must be visible to $select, $orderby, and $filter -- the
    thing Phase 1 of the design doc identified as the real risk, since a
    naive "tack it onto the final projection" implementation would only
    satisfy $select."""

    def test_compute_alias_usable_in_select(self):
        result = execute_odata_query(
            table_name=TABLE, compute_expr="mass mul gravity as Foo",
            select_cols="name,Foo", top=3, orderby_expr="Foo desc",
        )
        assert result["col_names"] == ["name", "Foo"]
        rows = _rows(result)
        assert rows[0]["name"] == "Jupiter"
        assert rows[0]["Foo"] == pytest.approx(1898.0 * 23.1, rel=1e-6)

    def test_compute_alias_usable_in_orderby_without_select(self):
        # Compute a column, sort by it, but don't explicitly select it --
        # confirms Project (not just Exit) sees the alias.
        result = execute_odata_query(
            table_name=TABLE, compute_expr="mass mul gravity as Foo",
            orderby_expr="Foo asc", top=1,
        )
        rows = _rows(result)
        assert rows[0]["name"] == "Pluto"  # smallest mass*gravity

    def test_compute_alias_usable_in_filter(self):
        result = execute_odata_query(
            table_name=TABLE,
            compute_expr="mass mul gravity as Foo",
            filter_expr="Foo gt 1000",
            select_cols="name",
            orderby_expr="name asc",
            top=10,
        )
        names = {row["name"] for row in _rows(result)}
        assert names == {"Jupiter", "Neptune", "Saturn"}

    def test_compute_alias_usable_in_count(self):
        result = execute_odata_query(
            table_name=TABLE,
            compute_expr="mass mul gravity as Foo",
            filter_expr="Foo gt 1000",
            count_requested=True,
            top=0,
        )
        assert result["total_count"] == 3

    def test_multiple_compute_clauses(self):
        result = execute_odata_query(
            table_name=TABLE,
            compute_expr="mass mul gravity as Foo, mass sub gravity as Bar",
            select_cols="name,Foo,Bar",
            top=1,
        )
        row = _rows(result)[0]
        assert set(row) == {"name", "Foo", "Bar"}


class TestComputeAndApplyRejected:
    """v1 scope decision (design doc §2.3c): $compute referencing $apply's
    aggregate output hits the same "$derived" schema problem
    _pending_aggregate_columns works around elsewhere, and hasn't been
    designed on purpose -- reject the combination rather than get it wrong."""

    def test_build_raises_when_both_present(self):
        builder = ODataLogicalPlanBuilder(TABLE)
        with pytest.raises(ValueError, match=r"\$compute.*\$apply"):
            builder.build(
                compute_expr="mass mul gravity as Foo",
                apply_expr="groupby((id), aggregate($count as Count))",
            )

    def test_execute_odata_query_surfaces_the_same_error(self):
        with pytest.raises(ValueError, match=r"\$compute.*\$apply"):
            execute_odata_query(
                table_name=TABLE,
                compute_expr="mass mul gravity as Foo",
                apply_expr="groupby((id), aggregate($count as Count))",
            )
