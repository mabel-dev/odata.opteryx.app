"""Health endpoints.

`/health` is not a ping: it reports whether this instance can actually serve, by
probing the dependencies `app.preflight` declares -- the same checks the process
runs at startup. It answers 503 when any of them fails, so an instance whose
dependencies have gone away stops claiming to be servable.

`/health/live` is the ping. It answers 200 as long as the process is running and
touches nothing external, so it can be used as a liveness probe without a
transient Firestore blip getting a busy instance killed mid-request.
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.preflight import preflight

router = APIRouter(tags=["service"])


@router.get("/health", include_in_schema=False)
def health() -> JSONResponse:
    """Report whether every dependency needed to serve is reachable.

    The probes are cached inside `readiness` -- this endpoint is
    unauthenticated, so it must not be a way to make the service issue
    unbounded RPCs.
    """
    healthy, payload = preflight.readiness()
    return JSONResponse(status_code=200 if healthy else 503, content=payload)


@router.get("/health/live", include_in_schema=False)
def liveness() -> dict:
    """The process is up. Nothing external is consulted."""
    return {"status": "ok"}
