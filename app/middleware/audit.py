import time

from jose import JWTError
from jose import jwt as jose_jwt
from starlette.requests import Request

from app.logging import get_logger

logger = get_logger()


class AuditMiddleware:
    """Pure-ASGI audit logging.

    This wraps `send` directly instead of using Starlette's BaseHTTPMiddleware.
    BaseHTTPMiddleware's call_next() returns a Response as soon as the status/
    headers are captured -- it has no visibility into whether the body actually
    finished streaming to the client. That meant a response which started as a
    clean 200 and then died mid-stream (compression middleware crash, worker
    OOM, dropped connection) was logged as a plain "200 okay" with the request
    otherwise indistinguishable from one that fully succeeded, because the log
    line was written before the body was ever sent. This version tracks bytes
    actually written to the ASGI `send` and whether the stream was cleanly
    terminated (the last body message had more_body=False), and logs a
    distinct status/detail when it wasn't -- that's the signal a "200 but the
    client got nothing" incident needs.

    It also no longer swallows exceptions from other middleware/the app into a
    synthetic empty Response: any exception that reaches here is logged then
    re-raised, so Starlette's own ServerErrorMiddleware (which wraps this
    entire stack) can turn it into a real error response instead of a blank
    one going out to the client.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        start = time.time()

        state = {"status": None, "bytes_sent": 0, "body_started": False, "completed": False}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                state["status"] = message["status"]
                state["body_started"] = True
            elif message["type"] == "http.response.body":
                state["bytes_sent"] += len(message.get("body", b""))
                if not message.get("more_body", False):
                    state["completed"] = True
            await send(message)

        transport_error = None
        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:
            transport_error = exc
        finally:
            duration_ms = int((time.time() - start) * 1000)
            xff = request.headers.get("x-forwarded-for", "-")

            if transport_error is not None:
                status = state["status"] or getattr(transport_error, "status_code", 500)
                if state["body_started"]:
                    # Headers (incl. a possible 2xx) already went out before the
                    # app/middleware chain died -- the client received a
                    # truncated or empty body despite the "successful" status.
                    detail = (
                        f"transport failure after {state['bytes_sent']} bytes sent: "
                        f"{type(transport_error).__name__}: {transport_error}"
                    )
                else:
                    detail = f"{type(transport_error).__name__}: {transport_error}"
            elif state["body_started"] and not state["completed"]:
                # No exception, but the stream was never cleanly terminated --
                # the app returned without sending a final more_body=False chunk.
                status = state["status"] or 0
                detail = f"incomplete response: only {state['bytes_sent']} bytes sent, stream not terminated"
            else:
                status = state["status"] or 0
                detail = "okay"

            payload = {
                "method": request.method,
                "path": request.url.path,
                "query": request.url.query or None,
                "status": status,
                "duration_ms": duration_ms,
                "bytes_sent": state["bytes_sent"],
                "from": xff,
                "timestamp": int(time.time()),
                "detail": detail,
            }

            # If a bearer token is present, extract the 'sub' (identity) claim
            # via unverified claims and include it in the audit payload. We do not
            # attempt to verify the token here (audit should not require key fetch).
            auth = request.headers.get("authorization")
            if auth and auth.lower().startswith("bearer "):
                try:
                    token = auth.split(" ", 1)[1]
                    claims = jose_jwt.get_unverified_claims(token)
                    sub = claims.get("sub") if isinstance(claims, dict) else None
                    if sub:
                        payload["jwt_sub"] = sub
                        payload["jwt_present"] = True
                    else:
                        payload["jwt_present"] = True
                except JWTError:
                    # best-effort: ignore any parsing errors
                    payload["jwt_present"] = True

            logger.audit(payload)

        if transport_error is not None:
            raise transport_error
