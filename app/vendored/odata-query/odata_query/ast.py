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

        return Node(NodeType.LITERAL, type=OrsoTypes.DATE, value=self.py_val)


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

        return Node(NodeType.LITERAL, type=OrsoTypes.TIME, value=self.py_val)


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

        return Node(NodeType.LITERAL, type=OrsoTypes.TIMESTAMP, value=self.py_val)


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

        # Convert each element to an Opteryx node
        element_nodes = [item.to_opteryx_node() for item in self.val]
        # Return an array literal containing the elements
        return Node(NodeType.LITERAL, type=OrsoTypes.ARRAY, value=element_nodes)


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

        # Map comparator types to operator names
        comparator_map = {
            "Eq": "Eq",
            "NotEq": "NotEq",
            "Lt": "Lt",
            "LtE": "LtEq",
            "Gt": "Gt",
            "GtE": "GtEq",
            "In": "In",
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
        return Node(node_type=NodeType.FUNCTION, value=func_name, parameters=arg_nodes)


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
