"""
OData v4 $apply (Data Aggregation) Parser

Parses $apply query parameter expressions into a structured AST.
Supports:
- groupby((properties), aggregate(expressions))
- aggregate(expressions)
- filter(expression)
- Transformation pipelines (chained with /)

Examples:
- groupby((regionkey), aggregate($count as total))
- aggregate(mass with sum as total_mass)
- filter(mass gt 10)/groupby((type))
- groupby((region, status), aggregate(revenue with sum as total_revenue, $count as count))
"""

from dataclasses import dataclass
from typing import List
from typing import Optional
from typing import Union

# ============================================================================
# AST Node Classes
# ============================================================================


@dataclass
class AggregateExpression:
    """
    Represents a single aggregate expression.

    Examples:
    - $count as total
    - mass with sum as total_mass
    - price with average as avg_price
    - quantity with min as min_qty
    - quantity with max as max_qty
    """

    property_path: Optional[str]  # None for $count, field name for aggregates
    method: str  # "count", "sum", "average", "min", "max"
    alias: str  # The 'as' name

    def __repr__(self):
        if self.property_path:
            return f"AggregateExpression({self.property_path} with {self.method} as {self.alias})"
        return f"AggregateExpression($count as {self.alias})"


@dataclass
class AggregateTransform:
    """
    Represents aggregate(...) transformation.

    Example: aggregate($count as total, mass with sum as total_mass)
    """

    expressions: List[AggregateExpression]

    def __repr__(self):
        return f"AggregateTransform({len(self.expressions)} expressions)"


@dataclass
class GroupByTransform:
    """
    Represents groupby((properties), aggregate(...)) transformation.

    Example: groupby((regionkey, status), aggregate($count as total))
    """

    properties: List[str]  # Grouping dimensions
    aggregate: Optional[AggregateTransform]  # Optional aggregation

    def __repr__(self):
        agg_info = f", {self.aggregate}" if self.aggregate else ""
        return f"GroupByTransform(properties={self.properties}{agg_info})"


@dataclass
class FilterTransform:
    """
    Represents filter(expression) transformation.

    Example: filter(mass gt 10)
    Note: The filter expression is parsed by the existing odata-query library
    """

    filter_expr: str  # Raw filter expression string (to be parsed by existing library)

    def __repr__(self):
        return f"FilterTransform({self.filter_expr})"


@dataclass
class ApplyPipeline:
    """
    Represents the full $apply transformation pipeline.

    Transformations are applied left-to-right.
    Example: filter(mass gt 10)/groupby((type), aggregate($count as total))
    """

    transformations: List[Union[GroupByTransform, AggregateTransform, FilterTransform]]

    def __repr__(self):
        return f"ApplyPipeline({len(self.transformations)} transformations)"


# ============================================================================
# Parser
# ============================================================================


class ApplyParseError(Exception):
    """Raised when $apply expression cannot be parsed."""

    pass


class ApplyParser:
    """
    Parser for OData v4 $apply expressions.

    Grammar (simplified):
    apply_expr := transformation ('/' transformation)*
    transformation := groupby_transform | aggregate_transform | filter_transform
    groupby_transform := 'groupby' '((' property_list ')' [',' aggregate_transform] ')'
    aggregate_transform := 'aggregate' '(' aggregate_expr (',' aggregate_expr)* ')'
    filter_transform := 'filter' '(' filter_expr ')'
    aggregate_expr := '$count' 'as' identifier | property 'with' method 'as' identifier
    method := 'sum' | 'average' | 'min' | 'max'
    """

    # The exhaustive set of `with <method>` spellings this parser accepts. A
    # class constant rather than a local so that consumers which must handle
    # every method (app.odata.apply_builder's aggregator map) can pin
    # themselves to it, and adding a method here without teaching them fails
    # loudly instead of reaching the engine unmapped. Excludes '$count', which
    # has its own syntax and yields method "count".
    VALID_METHODS = ("sum", "average", "min", "max")

    def __init__(self, expr: str):
        self.expr = expr.strip()
        self.pos = 0

    def parse(self) -> ApplyPipeline:
        """Parse the full $apply expression."""
        transformations = []

        while self.pos < len(self.expr):
            # Parse next transformation
            transform = self._parse_transformation()
            transformations.append(transform)

            # Check for pipeline separator
            if self.pos < len(self.expr):
                if self.expr[self.pos] == "/":
                    self.pos += 1  # Skip '/'
                    self._skip_whitespace()
                elif self.pos < len(self.expr):
                    raise ApplyParseError(
                        f"Expected '/' or end of expression at position {self.pos}, "
                        f"found: {self.expr[self.pos :]}"
                    )

        if not transformations:
            raise ApplyParseError("Empty $apply expression")

        return ApplyPipeline(transformations)

    def _parse_transformation(self) -> Union[GroupByTransform, AggregateTransform, FilterTransform]:
        """Parse a single transformation (groupby, aggregate, or filter)."""
        self._skip_whitespace()

        if self._peek_keyword("groupby"):
            return self._parse_groupby()
        elif self._peek_keyword("aggregate"):
            return self._parse_aggregate()
        elif self._peek_keyword("filter"):
            return self._parse_filter()
        else:
            raise ApplyParseError(
                f"Expected 'groupby', 'aggregate', or 'filter' at position {self.pos}, "
                f"found: {self.expr[self.pos :]}"
            )

    def _parse_groupby(self) -> GroupByTransform:
        """Parse groupby((props), aggregate(...))"""
        self._consume_keyword("groupby")
        self._skip_whitespace()
        self._consume_char("(")
        self._skip_whitespace()
        self._consume_char("(")
        self._skip_whitespace()

        # Parse property list
        properties = self._parse_property_list()

        self._skip_whitespace()
        self._consume_char(")")
        self._skip_whitespace()

        # Optional aggregate
        aggregate = None
        if self.pos < len(self.expr) and self.expr[self.pos] == ",":
            self.pos += 1  # Skip ','
            self._skip_whitespace()
            aggregate = self._parse_aggregate()

        self._skip_whitespace()
        self._consume_char(")")

        return GroupByTransform(properties, aggregate)

    def _parse_aggregate(self) -> AggregateTransform:
        """Parse aggregate(expr1, expr2, ...)"""
        self._consume_keyword("aggregate")
        self._skip_whitespace()
        self._consume_char("(")
        self._skip_whitespace()

        expressions = []
        while True:
            expr = self._parse_aggregate_expression()
            expressions.append(expr)

            self._skip_whitespace()
            if self.pos < len(self.expr) and self.expr[self.pos] == ",":
                self.pos += 1  # Skip ','
                self._skip_whitespace()
            else:
                break

        self._consume_char(")")

        return AggregateTransform(expressions)

    def _parse_aggregate_expression(self) -> AggregateExpression:
        """Parse $count as alias OR property with method as alias"""
        self._skip_whitespace()

        # Check for $count
        if self._peek_keyword("$count"):
            self._consume_keyword("$count")
            self._skip_whitespace()
            self._consume_keyword("as")
            self._skip_whitespace()
            alias = self._parse_identifier()
            return AggregateExpression(None, "count", alias)

        # Otherwise: property with method as alias
        property_path = self._parse_identifier()
        self._skip_whitespace()
        self._consume_keyword("with")
        self._skip_whitespace()
        method = self._parse_identifier().lower()

        # Validate method
        if method not in self.VALID_METHODS:
            raise ApplyParseError(
                f"Invalid aggregate method '{method}'. Expected: {', '.join(self.VALID_METHODS)}"
            )

        self._skip_whitespace()
        self._consume_keyword("as")
        self._skip_whitespace()
        alias = self._parse_identifier()

        return AggregateExpression(property_path, method, alias)

    def _parse_filter(self) -> FilterTransform:
        """Parse filter(expression) - extract expression for later parsing"""
        self._consume_keyword("filter")
        self._skip_whitespace()
        self._consume_char("(")

        # Find matching closing paren (handle nested parens)
        start = self.pos
        paren_count = 1
        while self.pos < len(self.expr) and paren_count > 0:
            if self.expr[self.pos] == "(":
                paren_count += 1
            elif self.expr[self.pos] == ")":
                paren_count -= 1
            self.pos += 1

        if paren_count != 0:
            raise ApplyParseError("Unmatched parentheses in filter expression")

        # Extract filter expression (excluding closing paren)
        filter_expr = self.expr[start : self.pos - 1].strip()

        return FilterTransform(filter_expr)

    def _parse_property_list(self) -> List[str]:
        """Parse comma-separated list of property names"""
        properties = []
        while True:
            prop = self._parse_identifier()
            properties.append(prop)

            self._skip_whitespace()
            if self.pos < len(self.expr) and self.expr[self.pos] == ",":
                self.pos += 1  # Skip ','
                self._skip_whitespace()
            else:
                break

        return properties

    def _parse_identifier(self) -> str:
        """Parse an identifier (property name, alias, etc.)"""
        self._skip_whitespace()
        start = self.pos

        # Identifier: starts with letter or underscore, contains alphanumeric/underscore
        if self.pos >= len(self.expr) or not (
            self.expr[self.pos].isalpha() or self.expr[self.pos] == "_"
        ):
            raise ApplyParseError(f"Expected identifier at position {self.pos}")

        while self.pos < len(self.expr) and (
            self.expr[self.pos].isalnum() or self.expr[self.pos] == "_"
        ):
            self.pos += 1

        return self.expr[start : self.pos]

    def _peek_keyword(self, keyword: str) -> bool:
        """Check if next token is the given keyword (case-insensitive)"""
        self._skip_whitespace()
        if self.pos + len(keyword) > len(self.expr):
            return False

        token = self.expr[self.pos : self.pos + len(keyword)]
        # Check word boundary
        if token.lower() == keyword.lower():
            next_pos = self.pos + len(keyword)
            if next_pos >= len(self.expr) or not self.expr[next_pos].isalnum():
                return True
        return False

    def _consume_keyword(self, keyword: str):
        """Consume expected keyword or raise error"""
        if not self._peek_keyword(keyword):
            raise ApplyParseError(
                f"Expected '{keyword}' at position {self.pos}, "
                f"found: {self.expr[self.pos : self.pos + 20]}"
            )
        self.pos += len(keyword)

    def _consume_char(self, char: str):
        """Consume expected character or raise error"""
        self._skip_whitespace()
        if self.pos >= len(self.expr) or self.expr[self.pos] != char:
            raise ApplyParseError(
                f"Expected '{char}' at position {self.pos}, "
                f"found: {self.expr[self.pos] if self.pos < len(self.expr) else 'EOF'}"
            )
        self.pos += 1

    def _skip_whitespace(self):
        """Skip whitespace characters"""
        while self.pos < len(self.expr) and self.expr[self.pos].isspace():
            self.pos += 1


# ============================================================================
# Public API
# ============================================================================


def parse_apply(expr: str) -> ApplyPipeline:
    """
    Parse an OData v4 $apply expression.

    Args:
        expr: The $apply expression string

    Returns:
        ApplyPipeline containing parsed transformations

    Raises:
        ApplyParseError: If the expression cannot be parsed

    Examples:
        >>> parse_apply("groupby((regionkey), aggregate($count as total))")
        >>> parse_apply("aggregate(mass with sum as total_mass)")
        >>> parse_apply("filter(active eq true)/groupby((status))")
    """
    parser = ApplyParser(expr)
    return parser.parse()
