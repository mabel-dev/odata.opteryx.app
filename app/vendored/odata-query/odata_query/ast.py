import datetime as dt
import re
from dataclasses import dataclass
from dataclasses import field
from typing import List as ListType
from typing import Optional
from typing import Tuple
from uuid import UUID

from dateutil.parser import isoparse

DURATION_PATTERN = re.compile(r"([+-])?P(\d+Y)?(\d+M)?(\d+D)?(?:T(\d+H)?(\d+M)?(\d+(?:\.\d+)?S)?)?")


def try_parse_iso(value):
    """Try to parse a string/bytes value as ISO datetime.

    Uses Opteryx's parse_iso function to convert ISO-parseable strings to datetime.
    Returns the parsed datetime if successful, otherwise returns None.
    """
    if not isinstance(value, (str, bytes)):
        return None

    try:
        from opteryx.compiled.functions.timestamp import parse_iso as c_parse_iso

        return c_parse_iso(value)
    except (ValueError, TypeError, ImportError):
        return None


@dataclass(frozen=True)
class _Node:
    """Base class for all AST nodes. Can override to_opteryx_node to customize conversion."""

    def to_opteryx_node(self):
        """Convert to Opteryx node. Override in subclasses for specific behavior."""
        raise NotImplementedError(f"to_opteryx_node not implemented for {type(self).__name__}")


@dataclass(frozen=True)
class Identifier(_Node):
    name: str
    namespace: Tuple[str, ...] = field(default_factory=tuple)

    def full_name(self):
        return ".".join(self.namespace + (self.name,))

    def to_opteryx_node(self):
        """Convert to Opteryx LogicalColumn."""
        from opteryx.managers.expression import NodeType
        from opteryx.models.logical_column import LogicalColumn

        # LogicalColumn constructor: (node_type, source_column, ...)
        return LogicalColumn(NodeType.IDENTIFIER, self.name)


@dataclass(frozen=True)
class Attribute(_Node):
    owner: _Node
    attr: str


###############################################################################
# Literals
###############################################################################
@dataclass(frozen=True)
class _Literal(_Node):
    pass

    @property
    def py_val(self):
        raise NotImplementedError()


@dataclass(frozen=True)
class Null(_Literal):
    @property
    def py_val(self) -> None:
        return None

    def to_opteryx_node(self):
        from opteryx.managers.expression import NodeType
        from opteryx.models import Node
        from orso.types import OrsoTypes

        return Node(NodeType.LITERAL, type=OrsoTypes.NULL, value=None)


@dataclass(frozen=True)
class Integer(_Literal):
    val: str

    @property
    def py_val(self) -> int:
        return int(self.val)

    def to_opteryx_node(self):
        from opteryx.managers.expression import NodeType
        from opteryx.models import Node
        from orso.types import OrsoTypes

        return Node(NodeType.LITERAL, type=OrsoTypes.INTEGER, value=self.py_val)


@dataclass(frozen=True)
class Float(_Literal):
    val: str

    @property
    def py_val(self) -> float:
        return float(self.val)

    def to_opteryx_node(self):
        from opteryx.managers.expression import NodeType
        from opteryx.models import Node
        from orso.types import OrsoTypes

        return Node(NodeType.LITERAL, type=OrsoTypes.DOUBLE, value=self.py_val)


@dataclass(frozen=True)
class Boolean(_Literal):
    val: str

    @property
    def py_val(self) -> bool:
        return self.val.lower() == "true"

    def to_opteryx_node(self):
        from opteryx.managers.expression import NodeType
        from opteryx.models import Node
        from orso.types import OrsoTypes

        return Node(NodeType.LITERAL, type=OrsoTypes.BOOLEAN, value=self.py_val)


@dataclass(frozen=True)
class String(_Literal):
    val: str

    @property
    def py_val(self) -> str:
        return self.val

    def to_opteryx_node(self):
        from opteryx.managers.expression import NodeType
        from opteryx.models import Node
        from orso.types import OrsoTypes

        return Node(NodeType.LITERAL, type=OrsoTypes.VARCHAR, value=self.val)


@dataclass(frozen=True)
class Geography(_Literal):
    val: str

    def wkt(self):
        return self.val


@dataclass(frozen=True)
class Date(_Literal):
    val: str

    @property
    def py_val(self) -> dt.date:
        return dt.date.fromisoformat(self.val)

    def to_opteryx_node(self):
        from opteryx.managers.expression import NodeType
        from opteryx.models import Node
        from orso.types import OrsoTypes

        # Try to parse ISO string to datetime for proper filtering
        parsed = try_parse_iso(self.val)
        if parsed is not None:
            return Node(NodeType.LITERAL, type=OrsoTypes.DATE, value=parsed)
        # Fallback to string if parsing fails
        return Node(NodeType.LITERAL, type=OrsoTypes.VARCHAR, value=self.val)


@dataclass(frozen=True)
class Time(_Literal):
    val: str

    @property
    def py_val(self) -> dt.time:
        return dt.time.fromisoformat(self.val)

    def to_opteryx_node(self):
        from opteryx.managers.expression import NodeType
        from opteryx.models import Node
        from orso.types import OrsoTypes

        # Try to parse ISO string to datetime for proper filtering
        parsed = try_parse_iso(self.val)
        if parsed is not None:
            return Node(NodeType.LITERAL, type=OrsoTypes.TIME, value=parsed)
        # Fallback to string if parsing fails
        return Node(NodeType.LITERAL, type=OrsoTypes.VARCHAR, value=self.val)


@dataclass(frozen=True)
class DateTime(_Literal):
    val: str

    @property
    def py_val(self) -> dt.datetime:
        return isoparse(self.val)

    def to_opteryx_node(self):
        from opteryx.managers.expression import NodeType
        from opteryx.models import Node
        from orso.types import OrsoTypes

        # Try to parse ISO string to datetime for proper filtering
        parsed = try_parse_iso(self.val)
        if parsed is not None:
            return Node(NodeType.LITERAL, type=OrsoTypes.TIMESTAMP, value=parsed)
        # Fallback to string if parsing fails
        return Node(NodeType.LITERAL, type=OrsoTypes.VARCHAR, value=self.val)


@dataclass(frozen=True)
class Duration(_Literal):
    val: str

    @property
    def py_val(self) -> dt.timedelta:
        sign, years, months, days, hours, minutes, seconds = self.unpack()

        # Initialize days to 0 if None
        num_days = float(days or 0)

        # Approximate conversion, adjust as necessary for more precision
        num_days += float(years or 0) * 365.25  # Average including leap years
        num_days += float(months or 0) * 30.44  # Average month length

        delta = dt.timedelta(
            days=num_days,
            hours=float(hours or 0),
            minutes=float(minutes or 0),
            seconds=float(seconds or 0),
        )
        if sign and sign == "-":
            delta = -1 * delta
        return delta

    def to_opteryx_node(self):
        from opteryx.managers.expression import NodeType
        from opteryx.models import Node
        from orso.types import OrsoTypes

        return Node(NodeType.LITERAL, type=OrsoTypes.INTERVAL, value=self.py_val)

    def unpack(
        self,
    ) -> Tuple[
        Optional[str],
        Optional[str],
        Optional[str],
        Optional[str],
        Optional[str],
        Optional[str],
        Optional[str],
    ]:
        """
        Returns:
            ``(sign, years, months, days, hours, minutes, seconds)``
        """

        match = DURATION_PATTERN.fullmatch(self.val)
        if not match:
            raise ValueError(f"Could not unpack Duration with value {self.val}")

        sign, years, months, days, hours, minutes, seconds = match.groups()

        _years = years[:-1] if years else None
        _months = months[:-1] if months else None
        _days = days[:-1] if days else None
        _hours = hours[:-1] if hours else None
        _minutes = minutes[:-1] if minutes else None
        _seconds = seconds[:-1] if seconds else None

        return sign, _years, _months, _days, _hours, _minutes, _seconds


@dataclass(frozen=True)
class GUID(_Literal):
    val: str

    @property
    def py_val(self) -> UUID:
        return UUID(self.val)


@dataclass(frozen=True)
class List(_Literal):
    val: ListType[_Literal]

    @property
    def py_val(self) -> list:
        return [v.py_val for v in self.val]

    def to_opteryx_node(self):
        from opteryx.managers.expression import NodeType
        from opteryx.models import Node
        from orso.types import OrsoTypes

        # Convert each element to a node, then extract Python values
        element_nodes = [item.to_opteryx_node() for item in self.val]
        value_list = [node.value for node in element_nodes]

        # Determine the element type (all elements should have the same type)
        element_types = {node.type for node in element_nodes}
        element_type = element_types.pop() if len(element_types) == 1 else OrsoTypes.VARCHAR

        return Node(
            node_type=NodeType.LITERAL,
            type=OrsoTypes.ARRAY,
            element_type=element_type,
            value=value_list,
        )


###############################################################################
# Arithmetic
###############################################################################
@dataclass(frozen=True)
class _BinOpToken(_Node):
    pass


@dataclass(frozen=True)
class Add(_BinOpToken):
    pass


@dataclass(frozen=True)
class Sub(_BinOpToken):
    pass


@dataclass(frozen=True)
class Mult(_BinOpToken):
    pass


@dataclass(frozen=True)
class Div(_BinOpToken):
    pass


@dataclass(frozen=True)
class Mod(_BinOpToken):
    pass


@dataclass(frozen=True)
class BinOp(_Node):
    op: _BinOpToken
    left: _Node
    right: _Node

    def to_opteryx_node(self):
        """Convert a binary arithmetic expression to an Opteryx BINARY_OPERATOR node."""
        from opteryx.managers.expression import NodeType
        from opteryx.models import Node

        left_node = self.left.to_opteryx_node()
        right_node = self.right.to_opteryx_node()

        op_map = {
            "Add": "Plus",
            "Sub": "Minus",
            "Mult": "Multiply",
            "Div": "Divide",
            "Mod": "Modulo",
        }

        op_name = op_map.get(type(self.op).__name__)
        if op_name is None:
            raise ValueError(f"Unknown binary operator: {type(self.op).__name__}")

        return Node(
            node_type=NodeType.BINARY_OPERATOR, value=op_name, left=left_node, right=right_node
        )


###############################################################################
# Comparison
###############################################################################
@dataclass(frozen=True)
class _Comparator(_Node):
    pass


@dataclass(frozen=True)
class Eq(_Comparator):
    pass


@dataclass(frozen=True)
class NotEq(_Comparator):
    pass


@dataclass(frozen=True)
class Lt(_Comparator):
    pass


@dataclass(frozen=True)
class LtE(_Comparator):
    pass


@dataclass(frozen=True)
class Gt(_Comparator):
    pass


@dataclass(frozen=True)
class GtE(_Comparator):
    pass


@dataclass(frozen=True)
class In(_Comparator):
    pass


@dataclass(frozen=True)
class Compare(_Node):
    comparator: _Comparator
    left: _Node
    right: _Node

    def to_opteryx_node(self):
        from opteryx.managers.expression import NodeType
        from opteryx.models import Node

        left_node = self.left.to_opteryx_node()
        right_node = self.right.to_opteryx_node()

        # Check if comparing to NULL - convert to IS NULL / IS NOT NULL unary operators
        if right_node.node_type == NodeType.LITERAL and right_node.type == "NULL":
            if isinstance(self.comparator, Eq):
                return Node(node_type=NodeType.UNARY_OPERATOR, value="IsNull", centre=left_node)
            elif isinstance(self.comparator, NotEq):
                return Node(node_type=NodeType.UNARY_OPERATOR, value="IsNotNull", centre=left_node)

        # Special-case IN: expand `col IN (a,b,c)` into `col = a OR col = b OR col = c`
        if isinstance(self.comparator, In) and isinstance(self.right, List):
            # Build equality comparisons for each element
            element_nodes = [elem.to_opteryx_node() for elem in self.right.val]
            eq_nodes = [
                Node(node_type=NodeType.COMPARISON_OPERATOR, value="Eq", left=left_node, right=en)
                for en in element_nodes
            ]

            # Fold into a chain of ORs
            if not eq_nodes:
                # Empty IN () should be false
                from orso.types import OrsoTypes

                return Node(
                    node_type=NodeType.COMPARISON_OPERATOR,
                    value="Eq",
                    left=left_node,
                    right=Node(node_type=NodeType.LITERAL, type=OrsoTypes.BOOLEAN, value=False),
                )

            result = eq_nodes[0]
            for en in eq_nodes[1:]:
                result = Node(node_type=NodeType.OR, value="Or", left=result, right=en)
            return result

        # Map comparator types to operator names
        comparator_map = {
            "Eq": "Eq",
            "NotEq": "NotEq",
            "Lt": "Lt",
            "LtE": "LtEq",
            "Gt": "Gt",
            "GtE": "GtEq",
            # "In" handled specially above
        }

        op_name = comparator_map.get(type(self.comparator).__name__)
        if op_name is None:
            raise ValueError(f"Unknown comparator: {type(self.comparator).__name__}")

        return Node(
            node_type=NodeType.COMPARISON_OPERATOR,
            value=op_name,
            left=left_node,
            right=right_node,
        )


###############################################################################
# Boolean ops
###############################################################################
@dataclass(frozen=True)
class _BoolOpToken(_Node):
    pass


@dataclass(frozen=True)
class And(_BoolOpToken):
    pass


@dataclass(frozen=True)
class Or(_BoolOpToken):
    pass


@dataclass(frozen=True)
class BoolOp(_Node):
    op: _BoolOpToken
    left: _Node
    right: _Node

    def to_opteryx_node(self):
        from opteryx.managers.expression import NodeType
        from opteryx.models import Node

        op_map = {
            "And": (NodeType.AND, "And"),
            "Or": (NodeType.OR, "Or"),
        }

        result = op_map.get(type(self.op).__name__)
        if result is None:
            raise ValueError(f"Unknown boolean operator: {type(self.op).__name__}")

        op_type, op_value = result
        left_node = self.left.to_opteryx_node()
        right_node = self.right.to_opteryx_node()

        return Node(op_type, value=op_value, left=left_node, right=right_node)


###############################################################################
# Unary ops
###############################################################################
@dataclass(frozen=True)
class _UnaryOpToken(_Node):
    pass


@dataclass(frozen=True)
class Not(_UnaryOpToken):
    pass


@dataclass(frozen=True)
class USub(_UnaryOpToken):
    pass


@dataclass(frozen=True)
class UnaryOp(_Node):
    op: _UnaryOpToken
    operand: _Node

    def to_opteryx_node(self):
        from opteryx.managers.expression import NodeType
        from opteryx.models import Node

        if isinstance(self.op, Not):
            operand_node = self.operand.to_opteryx_node()
            return Node(NodeType.NOT, centre=operand_node)
        else:
            raise ValueError(f"Unknown unary operator: {type(self.op).__name__}")


###############################################################################
# Function calls
###############################################################################
@dataclass(frozen=True)
class NamedParam(_Node):
    name: Identifier
    param: _Node


@dataclass(frozen=True)
class Call(_Node):
    func: Identifier
    args: ListType[_Node]

    def to_opteryx_node(self):
        from opteryx.managers.expression import NodeType
        from opteryx.models import Node

        # Get function name
        func_name = self.func.name

        # Convert arguments to Opteryx nodes
        arg_nodes = [arg.to_opteryx_node() for arg in self.args]

        # Handle 'not' function as a NOT operator
        if func_name == "not" and len(arg_nodes) == 1:
            return Node(node_type=NodeType.NOT, centre=arg_nodes[0])

        # Handle indexof specially - needs parameter swap and adjustment
        # OData: indexof(string, substring) returns 0-based index, -1 if not found
        # SQL: POSITION(substring IN string) returns 1-based index, 0 if not found
        # Conversion: POSITION(substring, string) - 1
        if func_name == "indexof" and len(arg_nodes) == 2:
            # Swap parameters: indexof(string, substring) → POSITION(substring, string)
            string_node = arg_nodes[0]
            substring_node = arg_nodes[1]

            # Create POSITION(substring, string) call
            position_call = Node(
                node_type=NodeType.FUNCTION,
                value="POSITION",
                parameters=[substring_node, string_node],
            )

            # Subtract 1 to convert from 1-based to 0-based (POSITION(...) - 1)
            from orso.types import OrsoTypes

            return Node(
                node_type=NodeType.BINARY_OPERATOR,
                value="Minus",
                left=position_call,
                right=Node(node_type=NodeType.LITERAL, type=OrsoTypes.INTEGER, value=1),
            )

        # Handle substring specially - OData is 0-based, SQL is 1-based
        # OData: substring(string, start, length) where start is 0-based
        # SQL: SUBSTRING(string, start, length) where start is 1-based
        # Conversion: SUBSTRING(string, start+1, length)
        if func_name == "substring" and len(arg_nodes) >= 2:
            string_node = arg_nodes[0]
            start_node = arg_nodes[1]

            # Add 1 to start position to convert from 0-based to 1-based
            from orso.types import OrsoTypes

            adjusted_start = Node(
                node_type=NodeType.BINARY_OPERATOR,
                value="Plus",
                left=start_node,
                right=Node(node_type=NodeType.LITERAL, type=OrsoTypes.INTEGER, value=1),
            )

            # Build parameter list with adjusted start
            params = [string_node, adjusted_start]
            if len(arg_nodes) > 2:  # If length is provided
                params.append(arg_nodes[2])

            return Node(node_type=NodeType.FUNCTION, value="SUBSTRING", parameters=params)

        # Convert startswith, endswith, contains to LIKE operators
        if func_name == "startswith" and len(arg_nodes) == 2:
            # startswith(field, 'value') → field LIKE 'value%'
            col_node = arg_nodes[0]
            pattern = arg_nodes[1]
            # Append '%' to the pattern literal
            if pattern.node_type == NodeType.LITERAL:
                new_pattern = Node(
                    node_type=NodeType.LITERAL, type=pattern.type, value=str(pattern.value) + "%"
                )
                return Node(
                    node_type=NodeType.COMPARISON_OPERATOR,
                    value="Like",
                    left=col_node,
                    right=new_pattern,
                )

        elif func_name == "endswith" and len(arg_nodes) == 2:
            # endswith(field, 'value') → field LIKE '%value'
            col_node = arg_nodes[0]
            pattern = arg_nodes[1]
            # Prepend '%' to the pattern literal
            if pattern.node_type == NodeType.LITERAL:
                new_pattern = Node(
                    node_type=NodeType.LITERAL, type=pattern.type, value="%" + str(pattern.value)
                )
                return Node(
                    node_type=NodeType.COMPARISON_OPERATOR,
                    value="Like",
                    left=col_node,
                    right=new_pattern,
                )

        elif func_name == "contains" and len(arg_nodes) == 2:
            # contains(field, 'value') → field LIKE '%value%'
            col_node = arg_nodes[0]
            pattern = arg_nodes[1]
            # Wrap pattern with '%' on both sides
            if pattern.node_type == NodeType.LITERAL:
                new_pattern = Node(
                    node_type=NodeType.LITERAL,
                    type=pattern.type,
                    value="%" + str(pattern.value) + "%",
                )
                return Node(
                    node_type=NodeType.COMPARISON_OPERATOR,
                    value="Like",
                    left=col_node,
                    right=new_pattern,
                )

        # For all other functions, create a function call node
        # Map OData function names to SQL/Opteryx function names
        sql_func_name = func_name.upper()  # Default: uppercase the function name

        # Explicit mappings for OData functions → Opteryx functions
        odata_to_sql_functions = {
            "tolower": "LOWER",
            "toupper": "UPPER",
            "length": "LENGTH",
            # indexof handled specially above (parameter swap + 0-based adjustment)
            # substring handled specially above (0-based → 1-based start position)
            "concat": "CONCAT",
            "trim": "TRIM",
            "year": "YEAR",
            "month": "MONTH",
            "day": "DAY",
            "hour": "HOUR",
            "minute": "MINUTE",
            "second": "SECOND",
            "round": "ROUND",
            "floor": "FLOOR",
            "ceiling": "CEIL",  # Note: SQL uses CEIL, not CEILING
        }

        sql_func_name = odata_to_sql_functions.get(func_name, sql_func_name)

        return Node(node_type=NodeType.FUNCTION, value=sql_func_name, parameters=arg_nodes)


###############################################################################
# Collections
###############################################################################
@dataclass(frozen=True)
class _CollectionOperator(_Node):
    pass


@dataclass(frozen=True)
class Any(_CollectionOperator):
    pass


@dataclass(frozen=True)
class All(_CollectionOperator):
    pass


@dataclass(frozen=True)
class Lambda(_Node):
    identifier: Identifier
    expression: _Node


@dataclass(frozen=True)
class CollectionLambda(_Node):
    owner: _Node
    operator: _CollectionOperator
    lambda_: Optional[Lambda]
