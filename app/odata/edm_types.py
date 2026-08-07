"""Unified OData EDM type mapper.

Consolidates type mapping logic for both service-wide $metadata and per-dataset
$metadata endpoints, ensuring consistent EDM type declarations that match the
payload the service actually emits.

The source vocabulary
---------------------
Column types reach us as strings. The catalog stores them and renders them via
``SimpleDataset.schema()`` -> ``SchemaColumn.type``, which mirrors
``str(opteryx.types.logical_type.ColumnType)``. That is the *Draken* vocabulary
(``INT64``, ``FLOAT64``, ``BOOL``, ``TIMESTAMP[us]``, ``VARIANT``, ...), not the
older orso ``OrsoTypes`` names -- opteryx no longer depends on orso. Names from
the orso era are still accepted, because schemas persisted back then still say
``INTEGER``/``DOUBLE``/``BLOB`` and re-reading them must not change their
meaning; opteryx keeps the same aliases in ``_SQL_NAME_ALIASES``.

The target vocabulary
---------------------
All of OData v4's primitive types are available and this module uses them.
Nothing here is narrowed for "client compatibility": a client that is told
``Edm.Int32`` gets a value that fits in an Int32, and one told ``Edm.Decimal``
with a Scale facet gets the exact scaled decimal.

Every mapping below describes the JSON the row payload actually carries. That
payload does NOT go through ``_sanitize_value_for_odata`` -- rows are rendered
natively by ``_execute_and_serialize_rows`` -> rugo ``write_jsonl`` ->
``rugo/src/_text_render.hpp``, whose per-type emitters are the authority:

    INT8..INT64      -> JSON number            SByte / Int16 / Int32 / Int64
    UINT8..UINT64    -> JSON number            Byte / Int32 / Int64 / Int64
    IPV4             -> "192.0.2.1"  (ej_ipv4) String
    BOOL             -> true / false           Boolean
    FLOAT32/FLOAT64  -> JSON number            Single / Double
    DECIMAL(p, s)    -> exact scaled number    Decimal + Precision/Scale
    DATE32           -> "YYYY-MM-DD"           Date
    TIMESTAMP64      -> RFC 3339 + "+00:00"    DateTimeOffset + Precision
    TIME32/TIME64    -> "HH:MM:SS[.ffffff]"    TimeOfDay + Precision
    VARCHAR/NVARCHAR/VARIANT/VARBINARY -> JSON string    String
    ARRAY            -> JSON array             Collection(element)
    VECTOR_FP16      -> JSON array of floats   Collection(Double)
    INTERVAL, NULL   -> null                   EDM_UNKNOWN

Anything not recognised maps to :data:`EDM_UNKNOWN`, never to ``Edm.String``.
Declaring an unrecognised column as a string is a lie clients act on (they
parse, sort and filter it as text); the abstract type says "primitive, but we
cannot narrow it", which is the truth.
"""

import re
from typing import Any, NamedTuple

#: EDM type used when the source type is unknown or has no wire representation.
#:
#: ``Edm.PrimitiveType`` is the built-in abstract primitive type (CSDL v4.0
#: §4.4) and is legal as a structural property type, so the metadata document
#: stays valid for 4.0 clients. OData v4 has no ``Edm.Null``; a column whose
#: every value is null is a nullable property of unknown type, which is exactly
#: what this says. ``Edm.Untyped`` would be more expressive but is 4.01-only,
#: and this service advertises ``OData-Version: 4.0``.
EDM_UNKNOWN = "Edm.PrimitiveType"

#: source base type -> EDM type. Canonical Draken spelling first, then the
#: legacy (orso-era) aliases older persisted schemas may still hold.
_SCALAR_TYPES: dict[str, str] = {
    # --- signed integers -------------------------------------------------
    # Edm.SByte is the SIGNED 8-bit type; Edm.Byte is unsigned (0..255).
    "INT8": "Edm.SByte",
    "INT16": "Edm.Int16",
    "INT32": "Edm.Int32",
    "INT64": "Edm.Int64",
    "TINYINT": "Edm.SByte",  # legacy
    "SMALLINT": "Edm.Int16",  # legacy
    "INTEGER": "Edm.Int64",  # legacy -- opteryx aliases INTEGER/INT/BIGINT to INT64
    "INT": "Edm.Int64",  # legacy
    "BIGINT": "Edm.Int64",  # legacy
    # --- unsigned integers -----------------------------------------------
    # OData has one unsigned type (Edm.Byte), so the wider ones are promoted to
    # the smallest signed type that holds their whole range.
    "UINT8": "Edm.Byte",
    "UINT16": "Edm.Int32",
    "UINT32": "Edm.Int64",
    # KNOWN NARROWING: UINT64's range (0..2**64-1) exceeds Edm.Int64's. rugo
    # writes the true value -- `SELECT 18446744073709551615::UINT64` really does
    # emit 18446744073709551615 -- so a client parsing strictly per the declared
    # type will overflow above 2**63-1. Edm.Decimal(20, 0) would be exact;
    # Edm.Int64 is the deliberate choice, and Custom.OriginalType carries UINT64
    # so a caller that cares can tell.
    "UINT64": "Edm.Int64",
    # --- floating point ---------------------------------------------------
    # Edm.Single is single precision; rugo's ej_float renders FLOAT32 from the
    # un-promoted value, so the declared type matches what is written.
    "FLOAT32": "Edm.Single",
    "FLOAT64": "Edm.Double",
    "REAL": "Edm.Single",  # legacy -- single precision per the SQL standard
    "DOUBLE": "Edm.Double",  # legacy
    "FLOAT": "Edm.Double",  # legacy -- opteryx aliases bare FLOAT to FLOAT64
    # --- boolean ----------------------------------------------------------
    "BOOL": "Edm.Boolean",
    "BOOLEAN": "Edm.Boolean",  # legacy
    # --- temporal ---------------------------------------------------------
    # Distinct EDM types, matching rugo's three distinct renderings. The old
    # "every temporal is a DateTimeOffset" policy described the Python
    # sanitizer, which the row path no longer uses.
    "DATE": "Edm.Date",
    "DATE32": "Edm.Date",
    "TIMESTAMP": "Edm.DateTimeOffset",
    "TIMESTAMP64": "Edm.DateTimeOffset",
    "DATETIME": "Edm.DateTimeOffset",  # legacy
    "TIME": "Edm.TimeOfDay",
    "TIME32": "Edm.TimeOfDay",
    "TIME64": "Edm.TimeOfDay",
    # --- text -------------------------------------------------------------
    # VARIANT means "type not narrowed", but DrakenType.VARIANT is rendered by
    # ej_string, so it really is text on the wire.
    "VARCHAR": "Edm.String",
    "NVARCHAR": "Edm.String",
    "VARIANT": "Edm.String",
    "STRING": "Edm.String",  # legacy
    "TEXT": "Edm.String",  # legacy
    "CHAR": "Edm.String",  # legacy
    "UUID": "Edm.String",  # legacy
    # STRUCT and JSONB alias to NVARCHAR in opteryx today -- they arrive as JSON
    # text, not as the base64 blobs this service used to declare.
    "STRUCT": "Edm.String",  # legacy
    "JSON": "Edm.String",  # legacy
    "JSONB": "Edm.String",  # legacy
    "MAP": "Edm.String",  # legacy
    # --- binary -----------------------------------------------------------
    # NOT Edm.Binary. rugo routes VARBINARY through ej_string, the same emitter
    # VARCHAR uses, so the payload is a plain JSON string of the bytes -- not
    # base64. Declaring Edm.Binary would tell clients to base64-decode it and
    # hand them garbage. Custom.OriginalType carries VARBINARY.
    "VARBINARY": "Edm.String",
    "BINARY": "Edm.String",  # legacy
    "BLOB": "Edm.String",  # legacy
    "BYTES": "Edm.String",  # legacy
    # --- IPv4 -------------------------------------------------------------
    # Physically UINT32, but the logical descriptor makes rugo pick ej_ipv4,
    # which writes a quoted dotted-decimal address.
    "IPV4": "Edm.String",
}

_ARRAY_TYPES = frozenset({"ARRAY", "LIST"})
_VECTOR_TYPES = frozenset({"VECTOR", "VECTOR_FP16"})
_DECIMAL_TYPES = frozenset({"DECIMAL", "DECIMAL128", "NUMERIC"})

#: TIMESTAMP[unit] / TIME[unit] -> the CSDL Precision facet (fractional digits).
_UNIT_PRECISION = {"s": "0", "ms": "3", "us": "6", "ns": "9"}
#: opteryx's canonical temporal unit when the type string carries none.
_DEFAULT_UNIT_PRECISION = "6"


#: CSDL annotation term carrying the opteryx/Draken type a property came from.
#:
#: OData's own type system cannot express what this platform distinguishes: a
#: UINT32 and an INT64 are both Edm.Int64; an IPv4 address, a VARBINARY blob, a
#: JSONB document and a plain VARCHAR are all Edm.String; a DECIMAL and a
#: FLOAT64 are different types with different exactness. CSDL forbids adding
#: attributes to <Property> in the EDM namespace, so the sanctioned extension
#: point is an <Annotation> child with a custom Term -- this one. It is emitted
#: on EVERY property, by both $metadata endpoints, so a client never has to
#: guess whether its absence means "same as the EDM type" or "we forgot".
ORIGINAL_TYPE_TERM = "Custom.OriginalType"


class EdmProperty(NamedTuple):
    """Everything both $metadata endpoints need to emit one <Property>."""

    name: str
    #: EDM type reference, e.g. "Edm.Int32" or "Collection(Edm.Double)".
    type: str
    #: CSDL facet attributes (Precision/Scale). Not optional -- see edm_type_facets.
    facets: dict[str, str]
    #: The opteryx type this came from, for the ORIGINAL_TYPE_TERM annotation.
    source_type: str = ""


def edm_property(name: str, column_type: Any, element_type: Any = None) -> EdmProperty:
    """Resolve one column into its complete CSDL property description."""
    return EdmProperty(
        name=name,
        type=column_type_to_edm(column_type, element_type),
        facets=edm_type_facets(column_type),
        source_type=_source_type_display(column_type),
    )


def _source_type_display(column_type: Any) -> str:
    """The platform type exactly as opteryx spells it, for the annotation.

    Deliberately NOT upper-cased: `str(ColumnType)` writes `TIMESTAMP[us]`, and
    that string round-trips through opteryx's own `parse_column_type` -- an
    upper-cased `TIMESTAMP[US]` does not.
    """
    if column_type is None:
        return ""
    if isinstance(column_type, str):
        return column_type.strip()
    name = getattr(column_type, "name", None)
    return str(name if name is not None else column_type).strip()


def extract_base_type(type_str: str) -> str:
    """Extract base category from a canonical type string.

    Examples:
        "DECIMAL(15, 2)"   -> "DECIMAL"
        "TIMESTAMP[us]"    -> "TIMESTAMP"
        "ARRAY<VARCHAR>"   -> "ARRAY"
        "INT64"            -> "INT64"
        "DOUBLE PRECISION" -> "DOUBLE"
    """
    if not isinstance(type_str, str):
        type_str = str(type_str)
    # Drop parameters -- "(p, s)", "[unit]" and "<element>" -- then take the
    # first word so compound spellings like "DOUBLE PRECISION" still resolve.
    head = type_str.strip().upper()
    for sep in ("(", "[", "<"):
        head = head.split(sep, 1)[0]
    head = head.strip()
    if not head:
        return ""
    return head.split()[0]


def _type_name(type_like: Any) -> str | None:
    """Best-effort canonical name for a type string or type-like object."""
    if type_like is None:
        return None
    if isinstance(type_like, str):
        return type_like.upper() or None
    name = getattr(type_like, "name", None)
    return str(name or type_like).upper() or None


def _map_scalar(type_str: str) -> str | None:
    """Map a non-collection type string to an EDM type, or None if unrecognised.

    Returning None (rather than a string fallback) is what lets callers tell
    "we know this is text" apart from "we have no idea what this is".
    """
    base = extract_base_type(type_str)
    if not base:
        return None

    # DECIMAL carries Precision/Scale facets (see edm_type_facets); without a
    # Scale facet CSDL defaults it to 0 and clients read the values as integers.
    if base in _DECIMAL_TYPES:
        return "Edm.Decimal"

    return _SCALAR_TYPES.get(base)


def edm_type_facets(column_type: Any) -> dict[str, str]:
    """CSDL facet attributes that must accompany this column's EDM type.

    Not decoration -- omitting them changes meaning. CSDL v4.0 §7.2 defaults
    ``Scale`` to 0, so an ``Edm.Decimal`` property with no Scale declares an
    integer; and it defaults temporal ``Precision`` to 0, so a DateTimeOffset
    with no Precision declares whole seconds while rugo writes microseconds.

    Returns an empty dict for types that need no facets.
    """
    name = _type_name(column_type) or ""
    base = extract_base_type(name)

    if base in _DECIMAL_TYPES:
        precision, scale = _extract_decimal_params(name)
        # opteryx's own default when the catalog stored no parameters.
        return {"Precision": str(precision or 38), "Scale": str(scale if scale is not None else 9)}

    if base in ("TIMESTAMP", "TIMESTAMP64", "TIME", "TIME32", "TIME64"):
        return {"Precision": _extract_unit_precision(name)}

    return {}


def column_type_to_edm(column_type: Any, element_type: Any = None) -> str:
    """Convert an Opteryx column type to its OData EDM type.

    Accepts either:
    - A canonical opteryx type string (from the catalog, e.g. "INT64",
      "DECIMAL(38, 9)", "TIMESTAMP[us]", "ARRAY<VARCHAR>")
    - A type-like object exposing a .name attribute
    - A Python type object (legacy path)

    Types we cannot recognise, and types with no wire representation, map to
    :data:`EDM_UNKNOWN` rather than ``Edm.String``.

    Callers emitting CSDL must also apply :func:`edm_type_facets` -- several of
    these types are wrong without their facets.

    Args:
        column_type: Canonical type string, type-like object, or Python type.
        element_type: Element type of an ARRAY/LIST column, for callers whose
            schema carries it beside the column type rather than inside it
            (``SchemaColumn.element_type``). The catalog normally renders
            ``ARRAY<element>`` into the type string itself, so this is a
            fallback.

    Returns:
        OData EDM type string (e.g. "Edm.Int32", "Edm.DateTimeOffset",
        "Collection(Edm.Double)", or "Edm.PrimitiveType" when unknown).
    """
    import datetime as _dt
    import decimal as _dec

    try:
        import uuid as _uuid
    except ImportError:
        _uuid = None

    # Handle canonical type strings (from the catalog) and type-like objects.
    if isinstance(column_type, str) or hasattr(column_type, "name"):
        # For type-like objects with .name, get the name attribute value
        type_str = (
            getattr(column_type, "name", str(column_type))
            if hasattr(column_type, "name")
            else str(column_type)
        )
        type_str = str(type_str).upper()
        base = extract_base_type(type_str)

        # Arrays -> Collection of an element EDM type when the element is known.
        # The element can live in the type string itself ("ARRAY<VARCHAR>"), in
        # the object's repr while .name is the bare "ARRAY", or alongside the
        # column as a separate element_type -- check all three.
        if base in _ARRAY_TYPES:
            inner = (
                _extract_array_inner_type(type_str)
                or _extract_array_inner_type(str(column_type).upper())
                or _type_name(element_type)
            )
            inner_edm = _map_scalar(inner) if inner else None
            return f"Collection({inner_edm or EDM_UNKNOWN})"

        # VECTOR_FP16 is rendered by ej_fp16 as a JSON array of fp16-widened
        # doubles. The dimension is not expressible in CSDL and is annotated.
        if base in _VECTOR_TYPES:
            return "Collection(Edm.Double)"

        # INTERVAL has NO case in rugo's resolve_col, so it falls to ej_null and
        # every value renders as JSON null. There is nothing to declare: the
        # column is unrepresentable on this wire, not a duration the client can
        # parse. Edm.Duration would promise an ISO-8601 string that never comes.
        if base == "INTERVAL":
            return EDM_UNKNOWN

        # An all-NULL column carries no type information -- and OData v4 has no
        # Edm.Null -- so it is a nullable property of unknown type.
        if base == "NULL":
            return EDM_UNKNOWN

        scalar = _map_scalar(type_str)
        if scalar is not None:
            return scalar

        return EDM_UNKNOWN

    # Handle Python types (legacy path)
    if column_type is bool:
        return "Edm.Boolean"
    if column_type is int:
        return "Edm.Int64"
    if column_type is float:
        return "Edm.Double"
    if column_type is str:
        return "Edm.String"
    if column_type in (bytes, bytearray):
        return "Edm.Binary"
    if _uuid and column_type is _uuid.UUID:
        return "Edm.Guid"
    if column_type is _dec.Decimal:
        return "Edm.Decimal"
    if column_type is _dt.datetime:
        return "Edm.DateTimeOffset"
    if column_type is _dt.date:
        return "Edm.Date"
    if column_type is _dt.time:
        return "Edm.TimeOfDay"

    # Unknown Python type -- say so rather than guessing "string"
    return EDM_UNKNOWN


def _extract_decimal_params(type_str: str) -> tuple[int | None, int | None]:
    """Pull (precision, scale) out of "DECIMAL(15, 2)". (None, None) if absent."""
    m = re.search(r"\(\s*(\d+)\s*(?:,\s*(\d+)\s*)?\)", type_str)
    if not m:
        return None, None
    precision = int(m.group(1))
    scale = int(m.group(2)) if m.group(2) is not None else None
    return precision, scale


def _extract_unit_precision(type_str: str) -> str:
    """Fractional-second digits for "TIMESTAMP[us]" / "TIME[ms]" and friends."""
    m = re.search(r"\[\s*([A-Za-z]+)\s*\]", type_str)
    if m:
        return _UNIT_PRECISION.get(m.group(1).lower(), _DEFAULT_UNIT_PRECISION)
    return _DEFAULT_UNIT_PRECISION


def _extract_array_inner_type(array_type_str: str) -> str | None:
    """Extract the element type from ARRAY<T> / ARRAY(T) notation.

    Takes the outermost bracket pair so parameterized elements survive intact.

    Examples:
        "ARRAY<VARCHAR>"        -> "VARCHAR"
        "ARRAY(INT64)"          -> "INT64"
        "ARRAY<DECIMAL(15, 2)>" -> "DECIMAL(15, 2)"
        "ARRAY"                 -> None
    """
    m = re.match(r"\s*(?:ARRAY|LIST)\s*<(.+)>\s*$", array_type_str)
    if m is None:
        m = re.match(r"\s*(?:ARRAY|LIST)\s*\((.+)\)\s*$", array_type_str)
    if m:
        return m.group(1).strip() or None
    return None
