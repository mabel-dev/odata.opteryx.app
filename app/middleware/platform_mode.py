"""The platform maintenance switch, applied to every request this service serves.

The mode itself, and what each mode allows, live in
``opteryx_shared_services.platform_mode``. For odata the answer is simple: this
service only reads, so ``operational`` and ``read_only`` serve, and ``reject``
and ``queue`` refuse everything - odata answers synchronously and has nowhere to
hold a request until the platform resumes.

A middleware rather than a route dependency so the gate is deny-by-default: a
route added later, or a router included without the dependency, is gated
without anyone remembering to. What is NOT gated is the explicit list below -
health (the deploy canary and the load balancer must still reach it) and the
API docs, which read nothing.

The mode is looked up on every request, here, at the moment of serving it. The
shared module caches the document for ten seconds; nothing in this service may
hold it any longer than that, so it is never read at import or startup.
"""

from fastapi import status
from fastapi.responses import JSONResponse
from opteryx_shared_services import platform_mode
from starlette.concurrency import run_in_threadpool
from starlette.types import ASGIApp
from starlette.types import Receive
from starlette.types import Scope
from starlette.types import Send

# Exact paths that answer whatever the mode. Everything else is gated.
UNGATED_PATHS = frozenset(
    {
        "/health",
        "/health/live",
        "/docs",
        "/docs/oauth2-redirect",
        "/redoc",
        "/openapi.json",
    }
)


def _refusal_response(path: str, refusal: platform_mode.Refusal) -> JSONResponse:
    """A 503 in the shape every other error on this path uses (see app.routes.v4.errors)."""
    headers = {"Retry-After": str(refusal.retry_after_seconds)}
    if path.startswith("/api/v4"):
        headers["OData-Version"] = "4.0"
        content = {"error": {"code": "ServiceUnavailable", "message": refusal.message}}
    else:
        content = {"detail": refusal.message}
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content=content, headers=headers
    )


class PlatformModeMiddleware:
    """Refuse every request but health and docs while the platform mode forbids odata reads."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope["path"].rstrip("/") or "/"
        if path in UNGATED_PATHS:
            await self.app(scope, receive, send)
            return

        # Every odata route reads; none writes data or catalog. In a thread: a
        # cache miss is a Firestore read of up to READ_TIMEOUT_SECONDS, which must
        # not stall the event loop for every other request on this worker.
        refusal = await run_in_threadpool(
            platform_mode.check, platform_mode.ODATA, platform_mode.READ
        )
        if refusal is not None:
            await _refusal_response(scope["path"], refusal)(scope, receive, send)
            return

        await self.app(scope, receive, send)
