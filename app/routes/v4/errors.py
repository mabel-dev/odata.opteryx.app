"""OData v4 error handling."""

from fastapi import HTTPException
from fastapi import Request
from fastapi import status
from fastapi.responses import JSONResponse
from opteryx_shared_services.logging import get_logger

logger = get_logger()


class ODataError(Exception):
    """OData-specific error that should be returned in OData error format."""

    def __init__(
        self, code: str, message: str, status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    ):
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def _map_status_to_code(status_code: int) -> str:
    """Map HTTP status code to OData error code."""
    mapping = {
        status.HTTP_400_BAD_REQUEST: "BadRequest",
        status.HTTP_401_UNAUTHORIZED: "Unauthorized",
        status.HTTP_403_FORBIDDEN: "Forbidden",
        status.HTTP_404_NOT_FOUND: "NotFound",
        status.HTTP_406_NOT_ACCEPTABLE: "NotAcceptable",
        status.HTTP_501_NOT_IMPLEMENTED: "NotImplemented",
        status.HTTP_500_INTERNAL_SERVER_ERROR: "InternalServerError",
        status.HTTP_502_BAD_GATEWAY: "BadGateway",
        status.HTTP_503_SERVICE_UNAVAILABLE: "ServiceUnavailable",
        status.HTTP_504_GATEWAY_TIMEOUT: "GatewayTimeout",
    }
    return mapping.get(status_code, "Error")


async def odata_error_handler(request: Request, exc: ODataError) -> JSONResponse:
    """Handle ODataError exceptions and return OData-compliant error response."""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
            }
        },
        headers={"OData-Version": "4.0"},
    )


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Handle HTTPException and convert to OData error format for v4 endpoints."""
    # Only convert to OData format for /api/v4 requests
    if request.url.path.startswith("/api/v4"):
        code = _map_status_to_code(exc.status_code)
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": code,
                    "message": exc.detail,
                }
            },
            headers={"OData-Version": "4.0"},
        )

    # For non-v4 endpoints, return standard FastAPI error
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )


# Opteryx ends every type-mismatch message with SQL remediation -- `CAST(column
# AS type)`. OData spells the same thing `cast(column, Edm.Type)`, so the SQL
# sentence is swapped for the OData one rather than passed through in a syntax
# the caller cannot write. These two patterns cover both spellings opteryx uses
# (IncompatibleTypesError's imperative form, IncorrectTypeError's "usually
# resolves it" form).
_CAST_ADVICE = (
    "Cast one side to match the other with `CAST(column AS type)`.",
    "Casting one side to match the other with `CAST(column AS type)` usually resolves it.",
)

_TEMPORAL_TYPES = ("DATE", "TIMESTAMP", "TIME")
_NUMERIC_TYPES = ("INT", "DOUBLE", "FLOAT", "DECIMAL", "NUMERIC")
_STRING_TYPES = ("VARCHAR", "NVARCHAR", "VARBINARY")


def _base_type(type_name) -> str:
    """`TIMESTAMP[us]` -> `TIMESTAMP`; None -> ``."""
    return str(type_name or "").split("[", 1)[0].strip().upper()


def _property_name(qualified) -> str:
    """`sales.orders.shipped_date` -> `shipped_date`.

    The exception qualifies each side with its relation, which is right for
    naming the offending column but wrong inside a worked $filter example: a
    filter is written against the bare property name.
    """
    return str(qualified or "").rsplit(".", 1)[-1]


def _literal_quoting_advice(exc):
    """The OData rule that a string-vs-non-string mismatch usually comes down to.

    The example is built from the caller's own column where the exception names
    one, because a worked example in their column reads as the fix while a worked
    example in someone else's reads as documentation.

    Returns None when the pair is some other mismatch (say TIMESTAMP vs INTEGER),
    where guessing at a cause would be worse than saying nothing.
    """
    left = _base_type(getattr(exc, "left_type", None))
    right = _base_type(getattr(exc, "right_type", None))
    if left not in _STRING_TYPES and right not in _STRING_TYPES:
        return None
    if left in _STRING_TYPES:
        other, column = right, getattr(exc, "right_column", None)
    else:
        other, column = left, getattr(exc, "left_column", None)
    if other in _TEMPORAL_TYPES:
        name = _property_name(column) or "shipped_date"
        return (
            "Date and time literals are unquoted in OData v4: write "
            f"`{name} ge 2024-01-01T00:00:00Z`, not `{name} ge '2024-01-01T00:00:00Z'` "
            "-- a quoted literal is a string."
        )
    if any(other.startswith(numeric) for numeric in _NUMERIC_TYPES):
        name = _property_name(column) or "price"
        return f"Numeric literals are unquoted: write `{name} gt 100`, not `{name} gt '100'`."
    return None


def _cast_advice(exc) -> str:
    """The OData form of the engine's `CAST(column AS type)` advice.

    Worked in the caller's own column and the other side's Edm type where the
    exception names both and cast() can produce that type; otherwise the
    general form. A temporal-vs-numeric pair gets the general form too: a cast
    between them converts through the epoch, which is almost never what the
    comparison meant, and a worked example would read as a recommendation.
    """
    # Order matters: expression_parser puts the vendored odata_query on sys.path.
    from app.odata import expression_parser  # noqa: F401, I001
    from app.odata.edm_types import column_type_to_edm
    from odata_query.ast import CAST_TARGETS

    left = _base_type(getattr(exc, "left_type", None))
    right = _base_type(getattr(exc, "right_type", None))
    temporal_vs_numeric = {left in _TEMPORAL_TYPES, right in _TEMPORAL_TYPES} == {True, False} and any(
        side.startswith(numeric) for side in (left, right) for numeric in _NUMERIC_TYPES
    )
    if not temporal_vs_numeric:
        for column, other_type in (
            (getattr(exc, "left_column", None), getattr(exc, "right_type", None)),
            (getattr(exc, "right_column", None), getattr(exc, "left_type", None)),
        ):
            if not column or not other_type:
                continue
            edm_type = column_type_to_edm(str(other_type))
            if edm_type in CAST_TARGETS:
                return (
                    "Cast one side to match the other with "
                    f"`cast({_property_name(column)}, {edm_type})`."
                )
    return "Cast one side to match the other with `cast(column, Edm.Type)`, e.g. `cast(price, Edm.Double)`."


def describe_type_mismatch(exc) -> str:
    """Restate an engine type-mismatch error in terms a $filter author can act on.

    The engine's own message names the column, both types and the offending
    literal, all of which are useful -- it is only the trailing CAST advice that
    is in the wrong syntax for OData, so that sentence is replaced rather than
    the whole message rewritten.

    A string-vs-temporal or string-vs-numeric mismatch is nearly always a quoted
    literal, so that case is told to drop the quotes instead of to cast: a cast
    would work, but it would paper over the actual mistake.
    """
    message = str(exc)
    stripped = False
    for advice in _CAST_ADVICE:
        if advice in message:
            message = message.replace(advice, "").strip()
            stripped = True
    advice = _literal_quoting_advice(exc)
    if advice is None:
        if not stripped:
            # Some other type error that never offered SQL advice in the first
            # place -- leave it exactly as the engine wrote it.
            return message
        advice = _cast_advice(exc)
    return f"{message} {advice}"


# What an unhandled exception tells the caller. Deliberately says nothing about
# the failure itself: every error whose text is safe and useful to a caller is
# already raised as an ODataError or an HTTPException with a written message,
# so anything arriving here is by definition a message nobody composed for
# external eyes. The one that prompted this carried a `gs://` URI -- the bucket
# name and the internal metadata layout -- straight into the response body.
_UNEXPECTED_ERROR_MESSAGE = (
    "An unexpected error occurred. If it persists, quote the time of the request."
)


async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle generic exceptions for v4 endpoints."""
    # The full exception, type included, is logged rather than returned: this is
    # the only copy of it, and the audit middleware's own record of the request
    # is what ties it back to the caller who saw the 500.
    logger.error(f"Unhandled exception in {request.url.path}: {type(exc).__name__}: {exc}")

    # Only convert to OData format for /api/v4 requests
    if request.url.path.startswith("/api/v4"):
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": {
                    "code": "InternalServerError",
                    "message": _UNEXPECTED_ERROR_MESSAGE,
                }
            },
            headers={"OData-Version": "4.0"},
        )

    # For non-v4 endpoints, return standard error
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": _UNEXPECTED_ERROR_MESSAGE},
    )


__all__ = [
    "ODataError",
    "describe_type_mismatch",
    "odata_error_handler",
    "http_exception_handler",
    "generic_exception_handler",
]
