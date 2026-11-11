"""CORS (Cross-Origin Resource Sharing) middleware configuration.

Two policies, chosen by the request's Origin:

- Opteryx's own sites (CORS_ORIGINS) get a credentialed policy: the origin is
  echoed back with Access-Control-Allow-Credentials, as before.
- Every other site gets a public policy: `Access-Control-Allow-Origin: *` and
  NO credentials. Anyone can build a page on the anonymous `demo` workspace, or
  call the API with a bearer token their own code holds.

Why the split matters: this service accepts HTTP Basic auth, and browsers
remember Basic credentials and attach them on their own. A credentialed `*`
would let any web page read a signed-in visitor's private datasets. With no
Allow-Credentials on the public policy, a browser never sends or exposes those
ambient credentials to a foreign page; only an Authorization header the page
set itself (so a token it already has) gets through.
"""

from fastapi.middleware.cors import CORSMiddleware
from opteryx_shared_services.config import get_config
from opteryx_shared_services.logging import get_logger
from starlette.datastructures import Headers, MutableHeaders

logger = get_logger()

CORS_ORIGINS = [
    "https://opteryx.app",
    "https://demo.opteryx.app",
    "http://127.0.0.1:8000",
    "http://localhost:8000",
    # Docs site "Try it" panel calls this service from the browser
    "https://docs.opteryx.app",
    # Notebooks (notebooks.opteryx.app) run their queries from the browser,
    # anonymously, against the demo workspace only.
    "https://notebooks.opteryx.app",
    "http://127.0.0.1:3000",  # docs-site `npm run dev`, notebooks serve.py
    "http://localhost:3000",
]

ALLOW_METHODS = ["GET", "POST"]  # Only allow necessary methods

# If-None-Match lets a browser client revalidate a cached $metadata document
# instead of refetching it. It is not a CORS-safelisted request header, so
# without it here the preflight rejects the conditional request.
PUBLIC_ALLOW_HEADERS = ["Authorization", "Content-Type", "If-None-Match"]
# Admin tooling only ever runs on Opteryx's own sites.
TRUSTED_ALLOW_HEADERS = PUBLIC_ALLOW_HEADERS + ["X-Admin-Token"]

# A cross-origin caller can only read headers named here. ETag is the other half
# of conditional requests -- unlisted, it reads as null and the client can never
# revalidate. The X-Dataset-* headers are response metadata browser clients are
# expected to act on. WWW-Authenticate tells a browser client which scheme a 401
# wants.
EXPOSE_HEADERS = [
    "ETag",
    "OData-Version",
    "WWW-Authenticate",
    "X-Dataset-Source",
    "X-Dataset-LastModified",
    "X-Dataset-Snapshot-Id",
]

MAX_AGE = 3600  # Cache preflight requests for 1 hour


def get_cors_origins() -> list[str]:
    """Get the origins that get the credentialed policy.

    Returns:
        List of trusted origin URLs, or ["*"] only if CORS_ALLOW_ALL=true
        (development mode: every origin credentialed).
    """
    if get_config("CORS_ALLOW_ALL", "").lower() == "true":
        logger.error("CORS_ALLOW_ALL is enabled - this should only be used in development!")
        return ["*"]

    logger.info(f"Credentialed CORS for origins: {CORS_ORIGINS}; all others public")
    return CORS_ORIGINS


class OriginSplitCORSMiddleware:
    """Route each request to the credentialed or the public CORS policy."""

    def __init__(self, app, trusted_origins: list[str]):
        self.trusted = set(trusted_origins)
        self.credentialed = CORSMiddleware(
            app,
            allow_origins=trusted_origins,
            allow_credentials=True,
            allow_methods=ALLOW_METHODS,
            allow_headers=TRUSTED_ALLOW_HEADERS,
            expose_headers=EXPOSE_HEADERS,
            max_age=MAX_AGE,
        )
        self.public = CORSMiddleware(
            app,
            allow_origins=["*"],
            allow_credentials=False,
            allow_methods=ALLOW_METHODS,
            allow_headers=PUBLIC_ALLOW_HEADERS,
            expose_headers=EXPOSE_HEADERS,
            max_age=MAX_AGE,
        )

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.credentialed(scope, receive, send)
            return

        origin = Headers(scope=scope).get("origin")
        if origin is None or origin in self.trusted:
            await self.credentialed(scope, receive, send)
            return

        # The same URL answers `*` here and an echoed origin for a trusted
        # site, so a cache must key on Origin or it could hand one site's
        # answer to the other. The trusted policy already adds this. Starlette
        # 1.10+ appends `Origin` here too, without checking whether the route
        # already varies on it, so rebuild the header with each value once.
        async def send_with_vary(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                values: dict[str, str] = {}
                for header in headers.getlist("vary"):
                    for value in header.split(","):
                        value = value.strip()
                        if value:
                            values.setdefault(value.lower(), value)
                values.setdefault("origin", "Origin")
                headers["vary"] = ", ".join(values.values())
            await send(message)

        await self.public(scope, receive, send_with_vary)


def configure_cors(app) -> None:
    """Configure CORS middleware on the FastAPI app.

    Args:
        app: FastAPI application instance
    """
    trusted = get_cors_origins()

    if trusted == ["*"]:
        logger.warning("Credentialed CORS enabled for ALL origins - not for production")
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=ALLOW_METHODS,
            allow_headers=TRUSTED_ALLOW_HEADERS,
            expose_headers=EXPOSE_HEADERS,
            max_age=MAX_AGE,
        )
        return

    app.add_middleware(OriginSplitCORSMiddleware, trusted_origins=trusted)
