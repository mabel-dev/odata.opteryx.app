"""CORS (Cross-Origin Resource Sharing) middleware configuration.

This module provides secure CORS configuration for the OData service.
By default, CORS is restrictive and requires explicit origin configuration.
"""

from fastapi.middleware.cors import CORSMiddleware
from opteryx_shared_services.config import get_config
from opteryx_shared_services.logging import get_logger

logger = get_logger()

CORS_ORIGINS = [
    "https://opteryx.app",
    "https://demo.opteryx.app",
    "http://127.0.0.1:8000",
    "http://localhost:8000",
    # Docs site "Try it" panel calls this service from the browser
    "https://docs.opteryx.app",
    "http://127.0.0.1:3000",  # docs-site `npm run dev`
    "http://localhost:3000",
]


def get_cors_origins() -> list[str]:
    """Get allowed CORS origins from environment configuration.

    Returns:
        List of allowed origin URLs, or empty list if none configured.
        Returns ["*"] only if CORS_ALLOW_ALL=true (development mode).
    """
    # Check for development mode first
    if get_config("CORS_ALLOW_ALL", "").lower() == "true":
        logger.error("CORS_ALLOW_ALL is enabled - this should only be used in development!")
        return ["*"]

    if CORS_ORIGINS:
        logger.info(f"CORS enabled for origins: {CORS_ORIGINS}")

    return CORS_ORIGINS


def configure_cors(app) -> None:
    """Configure CORS middleware on the FastAPI app.

    Args:
        app: FastAPI application instance
    """
    allowed_origins = get_cors_origins()

    if allowed_origins == ["*"]:
        logger.warning("CORS enabled for ALL origins - not recommended for production")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins if allowed_origins else [],
        allow_credentials=True,
        allow_methods=["GET", "POST"],  # Only allow necessary methods
        # If-None-Match lets a browser client revalidate a cached $metadata
        # document instead of refetching it. It is not a CORS-safelisted request
        # header, so without it here the preflight rejects the conditional request.
        allow_headers=["Authorization", "Content-Type", "X-Admin-Token", "If-None-Match"],
        # A cross-origin caller can only read headers named here. ETag is the
        # other half of conditional requests -- unlisted, it reads as null and
        # the client can never revalidate. The X-Dataset-* headers are response
        # metadata browser clients are expected to act on.
        expose_headers=[
            "ETag",
            "OData-Version",
            "X-Dataset-Source",
            "X-Dataset-LastModified",
            "X-Dataset-Snapshot-Id",
        ],
        max_age=3600,  # Cache preflight requests for 1 hour
    )
