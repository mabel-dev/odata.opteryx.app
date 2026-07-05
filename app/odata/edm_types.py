"""Unified OData EDM type mapper.

Consolidates type mapping logic for both service-wide $metadata and per-dataset
$metadata endpoints, ensuring consistent EDM type declarations that match the
sanitizer's payload output.

Policy: "broad, client-compatible"
- All date/time/timestamp types → Edm.DateTimeOffset (matches sanitizer output)
- DECIMAL → Edm.Double (matches sanitizer's float output)
- ARRAY<T> → Collection(Edm.T) with simplified inner mapping
- STRUCT/JSON → Edm.Binary with Custom.OriginalType annotation
- Integrators and integers → sensible defaults
"""

from typing import Any


def _extract_base_type(type_str: str) -> str:
    """Extract base category from a canonical type string.

    Examples:
        "DECIMAL(15, 2)" -> "DECIMAL"
        "ARRAY<VARCHAR>" -> "ARRAY"
        "INTEGER" -> "INTEGER"
        "DOUBLE PRECISION" -> "DOUBLE"
    """
    if not isinstance(type_str, str):
        type_str = str(type_str)
    # Remove parameters/angles and take first word (handles "DOUBLE PRECISION" -> "DOUBLE")
    return (
        type_str.strip()
        .upper()
        .split("(", 1)[0]
        .split("<", 1)[0]
        .split()[0]  # Take first word to handle compound types like "DOUBLE PRECISION"
        .strip()
    )


def column_type_to_edm(column_type: Any) -> str:
    """Convert an Opteryx column type to its OData EDM type.

    Accepts either:
    - A canonical Opteryx type string (from the catalog, e.g. "INTEGER",
      "DECIMAL(38, 9)", "ARRAY<VARCHAR>")
    - A type-like object exposing a .name attribute
    - A Python type object (legacy path)

    Uses a simplified, highly compatible set of EDM types to maximize client
    compatibility and match sanitizer behavior. This policy prioritizes broader
    types over precise types because:
    1. The sanitizer coerces all temporals to ISO datetime strings
    2. DECIMAL is coerced to float
    3. Client compatibility is more important than precision

    Args:
        column_type: Canonical type string, type-like object, or Python type.

    Returns:
        OData EDM type string (e.g., "Edm.String", "Edm.DateTimeOffset").
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
        type_str = type_str.upper()
        base = _extract_base_type(type_str)

        # Integer types -> Int64 for maximum range and compatibility
        if base in ("INTEGER", "BIGINT", "TINYINT", "SMALLINT", "INT"):
            return "Edm.Int64"

        # All numeric types -> Double (avoids Decimal IEEE754Compatible issues)
        if base in ("DOUBLE", "FLOAT", "REAL", "DECIMAL", "NUMERIC"):
            return "Edm.Double"

        # Boolean
        if base in ("BOOLEAN", "BOOL"):
            return "Edm.Boolean"

        # String types
        if base in ("VARCHAR", "NVARCHAR", "VARIANT", "TEXT", "CHAR", "STRING"):
            return "Edm.String"

        # Date/Time -> Use only DateTimeOffset for all temporal types
        # (matches sanitizer behavior: dates are coerced to ISO datetime strings)
        if base in ("TIMESTAMP", "DATETIME", "DATE", "TIME"):
            return "Edm.DateTimeOffset"

        # Arrays -> Collection of an inner EDM type when inner type is known
        if base in ("ARRAY", "LIST"):
            inner = _extract_array_inner_type(type_str)
            # Extract base type from inner (e.g., "DECIMAL(15,2)" -> "DECIMAL")
            inner_base = _extract_base_type(inner) if inner else ""
            if inner_base in ("INTEGER", "INT", "BIGINT", "TINYINT", "SMALLINT"):
                return "Collection(Edm.Int64)"
            if inner_base in ("DOUBLE", "FLOAT", "REAL", "DECIMAL", "NUMERIC"):
                return "Collection(Edm.Double)"
            # Default inner -> string
            return "Collection(Edm.String)"

        # Binary types
        if base in ("BINARY", "BLOB", "VARBINARY"):
            return "Edm.Binary"

        # Structs / JSON-like types -> expose as binary (base64)
        if base in ("STRUCT", "JSON", "JSONB"):
            return "Edm.Binary"

        # Default to string for unknown types
        return "Edm.String"

    # Handle Python types (legacy path)
    if column_type is int:
        return "Edm.Int64"
    if column_type is float:
        return "Edm.Double"
    if column_type is bool:
        return "Edm.Boolean"
    if column_type is str:
        return "Edm.String"
    if _uuid and column_type is _uuid.UUID:
        return "Edm.String"  # Serialize UUIDs as strings
    if column_type is _dec.Decimal:
        return "Edm.Double"  # Avoid Decimal type issues
    if column_type in (_dt.datetime, _dt.date, _dt.time):
        return "Edm.DateTimeOffset"

    # Default fallback
    return "Edm.String"


def _extract_array_inner_type(array_type_str: str) -> str | None:
    """Extract inner type from ARRAY<T> or ARRAY(T) notation.

    Examples:
        "ARRAY<VARCHAR>" -> "VARCHAR"
        "ARRAY(INT)" -> "INT"
        "ARRAY" -> None
    """
    import re

    m = re.search(r"ARRAY\s*[<\(]\s*([^>\)]+)\s*[>\)]", array_type_str)
    if m:
        return m.group(1).strip()
    return None
