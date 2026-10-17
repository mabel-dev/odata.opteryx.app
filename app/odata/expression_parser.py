"""Parse OData query expressions into structured data and AST.

This module handles the "statement -> AST" step for OData query components,
separating the parsing concerns from logical plan building. It converts
OData query strings into normalized, parsed representations that the
ODataLogicalPlanBuilder can use.
"""

import re
import sys
import threading
from pathlib import Path
from typing import List
from typing import Optional
from typing import Tuple

# CRITICAL: Set up vendored path BEFORE importing odata_query
# ruff: noqa: E402
_vendored_path = str(Path(__file__).parent.parent / "vendored" / "odata-query")
if _vendored_path not in sys.path:
    sys.path.insert(0, _vendored_path)

from odata_query.exceptions import FunctionCallException
from odata_query.exceptions import LiteralValueException
from opteryx.compiled.structures.expressions import Expression
from odata_query.grammar import ODataLexer
from odata_query.grammar import ODataParser

# SLY's Parser.parse() stores parse state (state/symstack/statestack) as instance
# attributes, so a single lexer/parser instance isn't safe to share across concurrent
# requests. Building the SLY grammar/parse tables is also non-trivial work best done
# once. Cache one instance per thread and reuse it instead of rebuilding per request.
_thread_local = threading.local()


def _get_lexer_and_parser():
    try:
        return _thread_local.lexer, _thread_local.parser
    except AttributeError:
        lexer = ODataLexer()
        parser = ODataParser()
        _thread_local.lexer = lexer
        _thread_local.parser = parser
        return lexer, parser


# A CSDL-legal alias name (also matches a plain OData identifier): starts with
# a letter or underscore, then letters/digits/underscores.
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _split_top_level(text: str, sep: str) -> List[str]:
    """Split `text` on `sep`, ignoring any occurrence inside `()` or a quoted
    string literal ('...' or "...").

    Used for $compute's comma-separated clause list, where a clause's own
    expression can itself contain commas inside a function call, e.g.
    `concat(a, b) as ab, price mul qty as total`.
    """
    parts = []
    depth = 0
    quote = None
    start = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if quote is not None:
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0 and text.startswith(sep, i):
            parts.append(text[start:i])
            i += len(sep)
            start = i
            continue
        i += 1
    parts.append(text[start:])
    return parts


def _split_trailing_alias(clause: str) -> Tuple[str, Optional[str]]:
    """Split one $compute clause into `(expression, alias)` on its trailing,
    top-level, whole-word `as`.

    Returns `(clause, None)` if no such `as` is found -- the caller turns
    that into the "expected '<expr> as <name>'" error, since $compute has no
    other valid clause shape.
    """
    depth = 0
    quote = None
    n = len(clause)
    last_as = None
    i = 0
    while i < n:
        ch = clause[i]
        if quote is not None:
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            i += 1
            continue
        if ch == "(":
            depth += 1
            i += 1
            continue
        if ch == ")":
            depth -= 1
            i += 1
            continue
        if depth == 0 and clause[i : i + 2].lower() == "as":
            before_ok = i == 0 or clause[i - 1].isspace()
            after_ok = i + 2 == n or clause[i + 2].isspace()
            if before_ok and after_ok:
                last_as = i
        i += 1

    if last_as is None:
        return clause, None

    expr_text = clause[:last_as].strip()
    alias = clause[last_as + 2 :].strip()
    if not expr_text or not alias:
        return clause, None
    return expr_text, alias


class ODataExpressionParser:
    """Parse OData query expressions into AST and structured data.

    Handles parsing of OData query string components:
    - $filter: WHERE clause expressions
    - $orderby: ORDER BY specifications
    - $select: Column projection specifications

    Note: $apply (aggregation) expressions are handled by ApplyLogicalPlanBuilder,
    which uses the vendored odata-query parse_apply() function directly.

    Separates parsing concerns from plan building, allowing reuse
    and clearer separation of responsibilities.
    """

    def __init__(self, arena=None):
        """Initialize the parser.

        Args:
            arena: The query's expression arena (`PlanContext.expressions`); every
                expression this parser mints belongs to it. A parser used on its
                own (no plan being built) gets an arena of its own.
        """
        self.lexer, self.parser = _get_lexer_and_parser()
        if arena is None:
            from opteryx.planner.plan_context import PlanContext

            # The arena does not keep its context alive, so the parser does.
            self._own_context = PlanContext()
            arena = self._own_context.expressions
        self.arena = arena

    def parse_filter(self, filter_expr: str) -> Optional[Expression]:
        """Parse an OData $filter expression into an Opteryx expression node.

        Args:
            filter_expr: OData filter expression (e.g., "vendor eq 'microsoft'")

        Returns:
            An expression Node representing the filter, or None if empty

        Example:
            "vendor eq 'microsoft' and price gt 100"
            →
            BinaryOp(
                left=BinaryOp(vendor eq microsoft),
                op=AND,
                right=BinaryOp(price gt 100)
            )
        """
        if not filter_expr or not filter_expr.strip():
            return None

        try:
            # Parse OData filter string to AST using odata-query library
            ast = self.parser.parse(self.lexer.tokenize(filter_expr))
            # Convert AST to Opteryx expression node using the AST node's built-in method
            return ast.to_opteryx_node(self.arena)
        except (FunctionCallException, LiteralValueException) as e:
            # Unknown function, wrong argument count, wrong argument type, a literal
            # the engine cannot represent -- these already carry a precise,
            # user-facing message. Surface it rather than flattening it into the
            # generic parse failure below.
            raise ValueError(str(e)) from e
        except NotImplementedError as e:
            # Catch unsupported OData constructs and provide a user-friendly error
            raise ValueError(
                f"Unsupported filter construct: {self._describe_unsupported(ast)}"
            ) from e
        except Exception as e:
            # Try to infer what unsupported construct caused the parse error
            error_msg = self._infer_unsupported_construct(filter_expr, str(e))
            if error_msg:
                raise ValueError(error_msg) from e
            raise ValueError(f"Failed to parse $filter expression: {filter_expr}") from e

    def _describe_unsupported(self, ast_node) -> str:
        """Map AST node types to user-friendly descriptions.

        Searches recursively through the AST to find unsupported constructs.
        """
        # First check the node itself
        node_type = type(ast_node).__name__
        descriptions = {
            "Lambda": "lambda operators (any/all) are not supported",
            "CollectionLambda": "lambda operators (any/all) are not supported",
            "GUID": "GUID literals are not supported",
            "Geography": "geography literals are not supported",
            "Call": "this function is not supported",
        }

        if node_type in descriptions:
            return descriptions[node_type]

        # Search recursively for unsupported constructs in child nodes
        for attr_name in ("left", "right", "operand", "centre", "expression", "owner"):
            if hasattr(ast_node, attr_name):
                child = getattr(ast_node, attr_name)
                if child is not None:
                    result = self._describe_unsupported(child)
                    # If we found something other than the generic message, return it
                    if result != f"{type(child).__name__} is not supported":
                        return result

        return f"{node_type} is not supported"

    def _infer_unsupported_construct(self, filter_expr: str, error_msg: str) -> Optional[str]:
        """Try to infer what unsupported construct caused a parse error.

        Returns a user-friendly error message if we can identify the construct,
        or None if we cannot.
        """
        # Look for known patterns that indicate unsupported constructs
        lower_expr = filter_expr.lower()

        # GUID literals: guid'...'
        if "guid'" in lower_expr:
            return "Unsupported filter construct: GUID literals are not supported"

        # Geography literals: geography'...'
        if "geography'" in lower_expr:
            return "Unsupported filter construct: geography literals are not supported"

        # IP containment operators borrowed from PostgreSQL/inet: <<=, >>=, <<, >>.
        # These are not part of the OData grammar, so the parser fails on the first
        # predicate and never reaches the rest of the expression -- which made the
        # generic message actively misleading. Point at the supported spelling.
        for ip_operator in ("<<=", ">>=", "<<", ">>"):
            if ip_operator in filter_expr:
                return (
                    f"Unsupported filter construct: the IP containment operator "
                    f"'{ip_operator}' is not supported. Use in_subnet(column, 'cidr') "
                    f"instead, e.g. in_subnet(src_addr, '192.168.4.0/24')"
                )

        return None

    def parse_compute(self, compute_expr: str) -> List[Expression]:
        """Parse an OData $compute expression into aliased Opteryx expression nodes.

        Args:
            compute_expr: comma-separated `<expression> as <alias>` clauses,
                e.g. "price mul quantity as TotalValue, price sub cost as Margin"

        Returns:
            A list of expression Nodes, each with `.alias` set to its
            requested name, in the order the caller wrote them. Empty list
            for an empty/blank input.

        Each clause's expression half uses the same grammar $filter does
        (`common_expr`, which already produces plain value nodes for
        arithmetic and function calls -- $compute is the first caller to use
        that outside a boolean context). Only the trailing `as <alias>` is
        new parsing.

        Raises:
            ValueError: a clause has no trailing `as <name>`, an alias is not
                a valid identifier, an alias is reused within the same
                $compute, or the expression half fails to parse.
        """
        if not compute_expr or not compute_expr.strip():
            return []

        computed_nodes: List[Expression] = []
        seen_aliases = set()
        for raw_clause in _split_top_level(compute_expr, ","):
            clause = raw_clause.strip()
            if not clause:
                continue

            expr_text, alias = _split_trailing_alias(clause)
            if alias is None:
                raise ValueError(
                    f"Invalid $compute clause '{clause}': expected '<expression> as <name>'"
                )
            if not _IDENTIFIER_RE.match(alias):
                raise ValueError(
                    f"Invalid $compute alias '{alias}': must be a valid identifier"
                )
            if alias in seen_aliases:
                raise ValueError(f"$compute defines '{alias}' more than once")
            seen_aliases.add(alias)

            try:
                ast = self.parser.parse(self.lexer.tokenize(expr_text))
                node = ast.to_opteryx_node(self.arena)
            except (FunctionCallException, LiteralValueException) as e:
                raise ValueError(str(e)) from e
            except Exception as e:
                raise ValueError(
                    f"Failed to parse $compute expression '{expr_text}': {e}"
                ) from e

            node.alias = alias
            computed_nodes.append(node)

        return computed_nodes

    def parse_orderby(self, orderby_expr: str) -> List[Tuple[str, str]]:
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

    def parse_select(self, select_cols: str) -> List[str]:
        """Parse OData $select expression into column names.

        Args:
            select_cols: OData select expression (e.g., "col1, col2, col3" or "*")

        Returns:
            List of column names, or ["*"] for wildcard selection
        """
        if not select_cols:
            return []

        select_cols = select_cols.strip()
        
        if select_cols == "*":
            return ["*"]
        
        # Split by comma and strip whitespace
        return [col.strip() for col in select_cols.split(",")]

