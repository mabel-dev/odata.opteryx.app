"""OData v4 error handling."""

from fastapi import HTTPException
from fastapi import Request
from fastapi import status
from fastapi.responses import JSONResponse
from app.logging import get_logger

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


async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle generic exceptions for v4 endpoints."""
    # Log the actual exception so we can debug it
    logger.error(f"Unhandled exception in {request.url.path}: {exc}")

    # Only convert to OData format for /api/v4 requests
    if request.url.path.startswith("/api/v4"):
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": {
                    "code": "InternalServerError",
                    "message": f"An unexpected error occurred: {str(exc)}",
                }
            },
            headers={"OData-Version": "4.0"},
        )

    # For non-v4 endpoints, return standard error
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": f"Internal server error: {str(exc)}"},
    )


__all__ = [
    "ODataError",
    "odata_error_handler",
    "http_exception_handler",
    "generic_exception_handler",
]
