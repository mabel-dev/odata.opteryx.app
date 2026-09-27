import datetime as dt
import re
from dataclasses import dataclass
from dataclasses import field
from typing import List as ListType
from typing import Optional
from typing import Tuple
from uuid import UUID

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

        # c_parse_iso requires bytes; OData literal text arrives as str.
        if isinstance(value, str):
            value = value.encode("utf-8")
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
        from opteryx.expression import NodeType
        from opteryx.compiled.structures.expressions import LogicalColumn

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
        from opteryx.compiled.structures.expressions import Literal
        from opteryx.types.logical_type import ARRAY, BOOLEAN, DATE, FLOAT64, INT64, INTERVAL, NULL, TIME, TIMESTAMP, VARCHAR

        return Literal(value=None, type=NULL)


@dataclass(frozen=True)
class Integer(_Literal):
    val: str

    @property
    def py_val(self) -> int:
        return int(self.val)

    def to_opteryx_node(self):
        from opteryx.compiled.structures.expressions import Literal
        from opteryx.types.logical_type import ARRAY, BOOLEAN, DATE, FLOAT64, INT64, INTERVAL, NULL, TIME, TIMESTAMP, VARCHAR

        return Literal(value=self.py_val, type=INT64)


@dataclass(frozen=True)
class Float(_Literal):
    val: str

    @property
    def py_val(self) -> float:
        return float(self.val)

    def to_opteryx_node(self):
        from opteryx.compiled.structures.expressions import Literal
        from opteryx.types.logical_type import ARRAY, BOOLEAN, DATE, FLOAT64, INT64, INTERVAL, NULL, TIME, TIMESTAMP, VARCHAR

        return Literal(value=self.py_val, type=FLOAT64)


@dataclass(frozen=True)
class Boolean(_Literal):
    val: str

    @property
    def py_val(self) -> bool:
        return self.val.lower() == "true"

    def to_opteryx_node(self):
        from opteryx.compiled.structures.expressions import Literal
        from opteryx.types.logical_type import ARRAY, BOOLEAN, DATE, FLOAT64, INT64, INTERVAL, NULL, TIME, TIMESTAMP, VARCHAR

        return Literal(value=self.py_val, type=BOOLEAN)


@dataclass(frozen=True)
class String(_Literal):
    val: str

    @property
    def py_val(self) -> str:
        return self.val

    def to_opteryx_node(self):
        from opteryx.compiled.structures.expressions import Literal
        from opteryx.types.logical_type import ARRAY, BOOLEAN, DATE, FLOAT64, INT64, INTERVAL, NULL, TIME, TIMESTAMP, VARCHAR

        return Literal(value=self.val, type=VARCHAR)


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
        from opteryx.compiled.structures.expressions import Literal
        from opteryx.types.logical_type import ARRAY, BOOLEAN, DATE, FLOAT64, INT64, INTERVAL, NULL, TIME, TIMESTAMP, VARCHAR
        from opteryx.types.timestamps._datetime_conversion import date_to_int64_days

        # Try to parse ISO string to datetime for proper filtering
        parsed = try_parse_iso(self.val)
        if parsed is not None:
            # DATE literals must be encoded as int32 days-since-epoch, matching
            # how Opteryx's own CAST(... AS DATE) folding stores them.
            return Literal(value=date_to_int64_days(parsed), type=DATE)
        # Fallback to string if parsing fails
        return Literal(value=self.val, type=VARCHAR)


@dataclass(frozen=True)
class Time(_Literal):
    val: str

    @property
    def py_val(self) -> dt.time:
        return dt.time.fromisoformat(self.val)

    def to_opteryx_node(self):
        from opteryx.compiled.structures.expressions import Literal
        from opteryx.types.logical_type import ARRAY, BOOLEAN, DATE, FLOAT64, INT64, INTERVAL, NULL, TIME, TIMESTAMP, VARCHAR

        # Try to parse ISO string to datetime for proper filtering
        parsed = try_parse_iso(self.val)
        if parsed is not None:
            return Literal(value=parsed, type=TIME())
        # Fallback to string if parsing fails
        return Literal(value=self.val, type=VARCHAR)


@dataclass(frozen=True)
class DateTime(_Literal):
    val: str

    @property
    def py_val(self) -> dt.datetime:
        parsed = try_parse_iso(self.val)
        if parsed is None:
            raise ValueError(f"Invalid ISO datetime literal: {self.val!r}")
        return parsed

    def to_opteryx_node(self):
        from opteryx.compiled.structures.expressions import Literal
        from opteryx.types.logical_type import ARRAY, BOOLEAN, DATE, FLOAT64, INT64, INTERVAL, NULL, TIME, TIMESTAMP, VARCHAR
        from opteryx.types.timestamps._datetime_conversion import timestamp_to_int64_us

        # Try to parse ISO string to datetime for proper filtering
        parsed = try_parse_iso(self.val)
        if parsed is not None:
            # TIMESTAMP literals must be encoded as int64 microseconds-since-epoch,
            # matching how Opteryx's own CAST(... AS TIMESTAMP) folding stores them.
            return Literal(value=timestamp_to_int64_us(parsed), type=TIMESTAMP())
        # Fallback to string if parsing fails
        return Literal(value=self.val, type=VARCHAR)


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
        from opteryx.compiled.structures.expressions import Literal
        from opteryx.types.logical_type import ARRAY, BOOLEAN, DATE, FLOAT64, INT64, INTERVAL, NULL, TIME, TIMESTAMP, VARCHAR

        # INTERVAL literals are a (months, microseconds) pair in Opteryx, not a
        # timedelta -- the same physical-encoding contract the DATE and TIMESTAMP
        # literals above follow. A timedelta here reached the expression compiler
        # and failed there with "interval: element must be a (months, us) tuple",
        # which made `now() sub duration'P30D'` -- the whole point of having a
        # clock -- unusable.
        #
        # Splitting years/months into the months half rather than approximating
        # them as 365.25/30.44 days is also what makes `P1Y` mean a calendar year:
        # month arithmetic is calendar-aware in the engine, day arithmetic is not.
        return Literal(value=self.interval_val, type=INTERVAL)

    @property
    def interval_val(self) -> Tuple[int, int]:
        """This duration as Opteryx's ``(months, microseconds)`` pair."""
        sign, years, months, days, hours, minutes, seconds = self.unpack()

        whole_months = int(years or 0) * 12 + int(months or 0)
        microseconds = round(
            (
                float(days or 0) * 86400
                + float(hours or 0) * 3600
                + float(minutes or 0) * 60
                + float(seconds or 0)
            )
            * 1_000_000
        )
        if sign == "-":
            return -whole_months, -microseconds
        return whole_months, microseconds

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
        from opteryx.compiled.structures.expressions import Literal
        from opteryx.types.logical_type import ARRAY, BOOLEAN, DATE, FLOAT64, INT64, INTERVAL, NULL, TIME, TIMESTAMP, VARCHAR

        # Convert each element to a node, then extract Python values
        element_nodes = [item.to_opteryx_node() for item in self.val]
        value_list = [node.value for node in element_nodes]

        # Determine the element type (all elements should have the same type)
        element_types = {node.type for node in element_nodes}
        element_type = element_types.pop() if len(element_types) == 1 else VARCHAR

        return Literal(value=value_list, type=ARRAY(element_type))


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
        from opteryx.compiled.structures.expressions import BinaryOperator

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

        return BinaryOperator(value=op_name, left=left_node, right=right_node)


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
        from opteryx.expression import NodeType
        from opteryx.compiled.structures.expressions import Comparison
        from opteryx.compiled.structures.expressions import Literal
        from opteryx.compiled.structures.expressions import Or
        from opteryx.compiled.structures.expressions import UnaryOperator

        left_node = self.left.to_opteryx_node()
        right_node = self.right.to_opteryx_node()

        # Check if comparing to NULL - convert to IS NULL / IS NOT NULL unary operators
        if right_node.node_type == NodeType.LITERAL and str(right_node.type) == "NULL":
            if isinstance(self.comparator, Eq):
                return UnaryOperator(value="IsNull", centre=left_node)
            elif isinstance(self.comparator, NotEq):
                return UnaryOperator(value="IsNotNull", centre=left_node)

        # Special-case IN: expand `col IN (a,b,c)` into `col = a OR col = b OR col = c`
        if isinstance(self.comparator, In) and isinstance(self.right, List):
            # Build equality comparisons for each element
            element_nodes = [elem.to_opteryx_node() for elem in self.right.val]
            eq_nodes = [
                Comparison(value="Eq", left=left_node, right=en)
                for en in element_nodes
            ]

            # Fold into a chain of ORs
            if not eq_nodes:
                # Empty IN () should return a constant-false literal, not col = false
                from opteryx.types.logical_type import ARRAY, BOOLEAN, DATE, FLOAT64, INT64, INTERVAL, NULL, TIME, TIMESTAMP, VARCHAR

                return Literal(value=False, type=BOOLEAN)

            result = eq_nodes[0]
            for en in eq_nodes[1:]:
                result = Or(value="Or", left=result, right=en)
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

        return Comparison(value=op_name, left=left_node, right=right_node)


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
        from opteryx.compiled.structures.expressions import And as AndExpression
        from opteryx.compiled.structures.expressions import Or as OrExpression

        op_map = {
            "And": (AndExpression, "And"),
            "Or": (OrExpression, "Or"),
        }

        result = op_map.get(type(self.op).__name__)
        if result is None:
            raise ValueError(f"Unknown boolean operator: {type(self.op).__name__}")

        op_class, op_value = result
        left_node = self.left.to_opteryx_node()
        right_node = self.right.to_opteryx_node()

        return op_class(value=op_value, left=left_node, right=right_node)


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
        from opteryx.compiled.structures.expressions import Not as NotExpression

        if isinstance(self.op, Not):
            operand_node = self.operand.to_opteryx_node()
            return NotExpression(centre=operand_node)
        else:
            raise ValueError(f"Unknown unary operator: {type(self.op).__name__}")


###############################################################################
# Function calls
###############################################################################
@dataclass(frozen=True)
class NamedParam(_Node):
    name: Identifier
    param: _Node


# The OData v4 date component functions that Opteryx answers through EXTRACT.
# The OData spelling is also the EXTRACT part name, so the mapping is the identity
# and this stays a set rather than a dict.
_ODATA_DATE_PARTS = frozenset({"year", "month", "day", "hour", "minute", "second"})

# OData v4 datetime functions this service does not implement, mapped to the
# nearest thing a caller can write instead. See the refusal in Call.to_opteryx_node.
_UNSUPPORTED_DATETIME_FUNCTIONS = {
    "time": "compare the timestamp column directly, or use hour()/minute()/second()",
    "mindatetime": "write the lower bound as a literal, e.g. 1900-01-01T00:00:00Z",
    "maxdatetime": "write the upper bound as a literal, e.g. 2999-12-31T23:59:59Z",
}


# cast(expr, Edm.X) targets (OData v4 §5.1.1.10.1), mapped to the Opteryx type
# the CAST node produces. Each is the inverse of app/odata/edm_types.py for that
# Edm type, so a cast result is declared back to the caller as the type they
# asked for.
CAST_TARGETS = {
    "Edm.String": "VARCHAR",
    "Edm.Boolean": "BOOLEAN",
    "Edm.Byte": "UINT8",
    "Edm.SByte": "INT8",
    "Edm.Int16": "INT16",
    "Edm.Int32": "INT32",
    "Edm.Int64": "INT64",
    "Edm.Single": "FLOAT32",
    "Edm.Double": "FLOAT64",
    "Edm.Decimal": "DECIMAL",
    "Edm.Date": "DATE",
    "Edm.DateTimeOffset": "TIMESTAMP",
}

# Edm types that are valid cast targets in the standard but that this service
# cannot produce, mapped to what a caller can write instead.
_UNSUPPORTED_CAST_TARGETS = {
    # Opteryx cannot narrow a TIMESTAMP to a TIME ("CAST TIMESTAMP64 -> TIME is
    # not supported"), and a string cast would be the only other source.
    "Edm.TimeOfDay": "compare the timestamp column directly, or use hour()/minute()/second()",
    # Opteryx has no cast into INTERVAL from any type.
    "Edm.Duration": "write the interval as a literal, e.g. duration'P30D'",
    # Binary columns are served as plain strings (see app/odata/edm_types.py), so
    # an Edm.Binary result would be declared base64 and delivered as raw bytes.
    "Edm.Binary": "compare against the column as Edm.String",
    "Edm.Guid": "compare against the column as Edm.String",
}

# The lookup is case-insensitive: a caller who wrote `edm.int32` meant Edm.Int32
# and nothing else, and messages still quote the canonical spelling.
_CAST_TARGET_NAMES = {name.lower(): name for name in (*CAST_TARGETS, *_UNSUPPORTED_CAST_TARGETS)}


def _cast_target(type_arg: "_Node") -> str:
    """The Opteryx type for cast()'s type argument, or raise naming what to write.

    The standard spells the type as a bare qualified name (`Edm.Int32`). A quoted
    name is accepted too: some clients still send the pre-v4 `'Edm.Int32'` form,
    and there is nothing else it could mean.
    """
    from .exceptions import CastTargetException

    if isinstance(type_arg, Identifier):
        type_name = type_arg.full_name()
    elif isinstance(type_arg, String):
        type_name = type_arg.val
    else:
        raise CastTargetException(
            "The second argument to cast() must be a type name, e.g. cast(price, Edm.Int32)."
        )

    canonical = _CAST_TARGET_NAMES.get(type_name.lower())
    if canonical in CAST_TARGETS:
        return CAST_TARGETS[canonical]
    if canonical in _UNSUPPORTED_CAST_TARGETS:
        raise CastTargetException(
            f"cast() to {canonical} is not supported by this service. "
            f"Instead, {_UNSUPPORTED_CAST_TARGETS[canonical]}."
        )
    raise CastTargetException(
        f"cast() cannot produce '{type_name}'. "
        f"Supported target types: {', '.join(CAST_TARGETS)}."
    )


@dataclass(frozen=True)
class Call(_Node):
    func: Identifier
    args: ListType[_Node]

    def to_opteryx_node(self):
        from opteryx.expression import NodeType
        from opteryx.compiled.structures.expressions import BinaryOperator
        from opteryx.compiled.structures.expressions import Cast
        from opteryx.compiled.structures.expressions import Comparison
        from opteryx.compiled.structures.expressions import Function
        from opteryx.compiled.structures.expressions import Literal
        from opteryx.compiled.structures.expressions import Not as NotExpression

        # Get function name
        func_name = self.func.name

        # cast(expr, Edm.X) -> CAST(expr AS <opteryx type>). Handled before the
        # arguments are converted: the type name parses as an Identifier, and
        # converting it would turn `Edm.Int32` into a reference to a column `Int32`.
        #
        # The one-argument form casts the current instance ($it) to a derived
        # entity type. This service's entity types have no derived types, so it is
        # refused rather than treated as a no-op that silently matches everything.
        if func_name == "cast":
            from .exceptions import CastTargetException

            if len(self.args) == 1:
                raise CastTargetException(
                    "cast() with one argument casts the entity to a derived type, and "
                    "this service's entity types have none. Pass the property to cast "
                    "as well, e.g. cast(price, Edm.Int32)."
                )
            target = _cast_target(self.args[1])
            parameters = None
            if target == "DECIMAL":
                # Edm.Decimal carries no precision/scale here, and the runtime CAST
                # node needs both; this is the engine's own default for a bare
                # DECIMAL, stated rather than left implicit.
                from opteryx.types.logical_type import INT64

                parameters = [Literal(value=18, type=INT64), Literal(value=6, type=INT64)]
            return Cast(left=self.args[0].to_opteryx_node(), value=target, parameters=parameters)

        # Convert arguments to Opteryx nodes
        arg_nodes = [arg.to_opteryx_node() for arg in self.args]

        # Handle 'not' function as a NOT operator
        if func_name == "not" and len(arg_nodes) == 1:
            return NotExpression(centre=arg_nodes[0])

        # Handle indexof specially - needs parameter swap and adjustment
        # OData: indexof(string, substring) returns 0-based index, -1 if not found
        # SQL: POSITION(substring IN string) returns 1-based index, 0 if not found
        # Conversion: POSITION(substring, string) - 1
        if func_name == "indexof" and len(arg_nodes) == 2:
            # Swap parameters: indexof(string, substring) → POSITION(substring, string)
            string_node = arg_nodes[0]
            substring_node = arg_nodes[1]

            # Create POSITION(substring, string) call
            position_call = Function(value="POSITION", parameters=[substring_node, string_node])

            # Subtract 1 to convert from 1-based to 0-based (POSITION(...) - 1)
            from opteryx.types.logical_type import ARRAY, BOOLEAN, DATE, FLOAT64, INT64, INTERVAL, NULL, TIME, TIMESTAMP, VARCHAR

            return BinaryOperator(
                value="Minus",
                left=position_call,
                right=Literal(value=1, type=INT64),
            )

        # Handle substring specially - OData is 0-based, SQL is 1-based
        # OData: substring(string, start, length) where start is 0-based
        # SQL: SUBSTRING(string, start, length) where start is 1-based
        # Conversion: SUBSTRING(string, start+1, length)
        if func_name == "substring" and len(arg_nodes) >= 2:
            string_node = arg_nodes[0]
            start_node = arg_nodes[1]

            # Add 1 to start position to convert from 0-based to 1-based
            from opteryx.types.logical_type import ARRAY, BOOLEAN, DATE, FLOAT64, INT64, INTERVAL, NULL, TIME, TIMESTAMP, VARCHAR

            adjusted_start = BinaryOperator(
                value="Plus",
                left=start_node,
                right=Literal(value=1, type=INT64),
            )

            # Build parameter list with adjusted start
            params = [string_node, adjusted_start]
            if len(arg_nodes) > 2:  # If length is provided
                params.append(arg_nodes[2])

            return Function(value="SUBSTRING", parameters=params)

        # now() -> the query's wall clock, as Edm.DateTimeOffset (OData v4 §5.1.1.7).
        # Opteryx spells this NOW(); the planner folds it to a single timestamp per
        # plan (see opteryx.planner.expression_traits), so every row in a query sees
        # the same instant rather than the clock drifting mid-scan.
        #
        # Without a clock a caller has no way to express a relative window --
        # "the last 30 days" has to be written as a hardcoded literal range, which
        # is silently wrong the moment the query is reused on another day.
        if func_name == "now" and not arg_nodes:
            return Function(value="NOW", parameters=[])

        # year/month/day/hour/minute/second(temporal) -> EXTRACT(part, temporal).
        # Opteryx has no YEAR()/MONTH()/... function of its own: the part name is a
        # VARCHAR argument to a single EXTRACT function. These were previously mapped
        # name-for-name onto functions that do not exist, so every one of them reached
        # the binder and failed there with "FUNCTION 'YEAR' has no function_ref".
        if func_name in _ODATA_DATE_PARTS and len(arg_nodes) == 1:
            from opteryx.types.logical_type import VARCHAR

            return Function(
                value="EXTRACT",
                parameters=[Literal(value=func_name, type=VARCHAR), arg_nodes[0]],
            )

        # date(timestamp) -> the date part, as Edm.Date. Opteryx spells this as a
        # cast rather than a function -- DATE is a type name there, and DATE(x) is
        # refused outright ("DATE is a type name, not a function").
        if func_name == "date" and len(arg_nodes) == 1:
            return Cast(left=arg_nodes[0], value="DATE", parameters=None)

        # The remaining OData v4 datetime functions have no Opteryx equivalent, and
        # are refused HERE rather than being handed to the binder as a function name
        # it has never heard of -- that failure surfaces as
        # "FUNCTION 'TIME' has no function_ref - not bound", which tells a caller
        # nothing about what to write instead.
        #
        #   time(x)         Opteryx cannot narrow a TIMESTAMP to a TIME at all
        #                   ("CAST TIMESTAMP64 -> TIME is not supported").
        #   mindatetime()   the extreme values of Edm.DateTimeOffset, whose range is
        #   maxdatetime()   not the range of Opteryx's TIMESTAMP64, so a literal
        #                   substitute would quietly mean something else.
        if func_name in _UNSUPPORTED_DATETIME_FUNCTIONS:
            from .exceptions import UnsupportedFunctionException

            raise UnsupportedFunctionException(
                func_name, _UNSUPPORTED_DATETIME_FUNCTIONS[func_name]
            )

        # in_subnet(ip_column, 'cidr') -> IPv4 CIDR containment.
        # Opteryx spells this `addr <<= '10.0.0.0/8'`, whose canonical operator name
        # is IPContainedBy (see opteryx-core operator_catalog). OData has no
        # containment operator of its own, so this is an Opteryx extension -- without
        # it callers fall back on startswith() prefix matching, which is only exact
        # on byte boundaries (/8, /16, /24).
        #
        # The left operand must be an IPV4 (or integer) column: the binder keys
        # IPContainedBy on (INTEGER, VARCHAR), because an IPv4 address is held as its
        # 32-bit value. A VARCHAR address column is a bind-time type error here, not
        # something this layer silently casts.
        if func_name == "in_subnet" and len(arg_nodes) == 2:
            from opteryx.types.logical_type import VARCHAR

            ip_node, cidr_node = arg_nodes
            if cidr_node.node_type == NodeType.LITERAL and cidr_node.type != VARCHAR:
                from .exceptions import ArgumentTypeException

                raise ArgumentTypeException(
                    "in_subnet",
                    expected_type="a quoted CIDR string, e.g. '192.168.4.0/24'",
                )
            return Comparison(value="IPContainedBy", left=ip_node, right=cidr_node)

        # Helper function to escape LIKE metacharacters
        def escape_like_literal(s: str, escape_char: str = "\\") -> str:
            """Escape SQL LIKE metacharacters in a literal string.

            Escapes the escape character first to avoid double-escaping, then
            escapes % and _ so they match literally in LIKE patterns.
            """
            s = str(s)
            # Escape the escape character first
            s = s.replace(escape_char, escape_char + escape_char)
            # Escape wildcards
            s = s.replace("%", escape_char + "%")
            s = s.replace("_", escape_char + "_")
            return s

        # Convert startswith, endswith, contains to LIKE operators
        if func_name == "startswith" and len(arg_nodes) == 2:
            # startswith(field, 'value') → field LIKE 'value%'
            col_node = arg_nodes[0]
            pattern = arg_nodes[1]
            # Escape metacharacters and append '%' to the pattern literal
            if pattern.node_type == NodeType.LITERAL:
                escaped_value = escape_like_literal(str(pattern.value))
                new_pattern = Literal(value=escaped_value + "%", type=pattern.type)
                return Comparison(value="Like", left=col_node, right=new_pattern)

        elif func_name == "endswith" and len(arg_nodes) == 2:
            # endswith(field, 'value') → field LIKE '%value'
            col_node = arg_nodes[0]
            pattern = arg_nodes[1]
            # Escape metacharacters and prepend '%' to the pattern literal
            if pattern.node_type == NodeType.LITERAL:
                escaped_value = escape_like_literal(str(pattern.value))
                new_pattern = Literal(value="%" + escaped_value, type=pattern.type)
                return Comparison(value="Like", left=col_node, right=new_pattern)

        elif func_name == "contains" and len(arg_nodes) == 2:
            # contains(field, 'value') → field LIKE '%value%'
            col_node = arg_nodes[0]
            pattern = arg_nodes[1]
            # Escape metacharacters and wrap pattern with '%' on both sides
            if pattern.node_type == NodeType.LITERAL:
                escaped_value = escape_like_literal(str(pattern.value))
                new_pattern = Literal(value="%" + escaped_value + "%", type=pattern.type)
                return Comparison(value="Like", left=col_node, right=new_pattern)

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
            # year/month/day/hour/minute/second handled specially above (EXTRACT),
            # now() and date() likewise -- Opteryx has no function of those names.
            "round": "ROUND",
            "floor": "FLOOR",
            "ceiling": "CEIL",  # Note: SQL uses CEIL, not CEILING
        }

        sql_func_name = odata_to_sql_functions.get(func_name, sql_func_name)

        return Function(value=sql_func_name, parameters=arg_nodes)


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
