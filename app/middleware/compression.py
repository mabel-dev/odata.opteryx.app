"""Response compression that runs off the event loop.

Replaces ``brotli_asgi.BrotliMiddleware``: that middleware calls
``Compressor.process()`` directly inside the ASGI ``send`` coroutine, i.e. on
the event loop. For the multi-thousand-row JSON payloads this service can
return (up to ``ODATA_MAX_TOP`` rows), that compression call can run long
enough to stall every other in-flight request on this worker for its
duration. Every response this service produces is built fully in memory
before being sent (see app/routes/v4/interface.py), so there is no streaming
case to support -- a single one-shot ``brotli.compress``/``gzip.compress``
call, offloaded to a worker thread via ``anyio.to_thread.run_sync``, is both
simpler than the incremental Compressor and keeps the loop free.
"""

import gzip

import anyio
import brotli
from starlette.datastructures import Headers
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp
from starlette.types import Message
from starlette.types import Receive
from starlette.types import Scope
from starlette.types import Send


class CompressionMiddleware:
    """Brotli (preferred) or gzip (fallback) compression, off the event loop."""

    def __init__(self, app: ASGIApp, quality: int = 4, minimum_size: int = 1024) -> None:
        self.app = app
        self.quality = quality
        self.minimum_size = minimum_size

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        accept_encoding = Headers(scope=scope).get("accept-encoding", "")
        if "br" in accept_encoding:
            encoding = "br"
        elif "gzip" in accept_encoding:
            encoding = "gzip"
        else:
            await self.app(scope, receive, send)
            return

        initial_message: Message = {}

        async def send_wrapper(message: Message) -> None:
            nonlocal initial_message
            if message["type"] == "http.response.start":
                initial_message = message
                return
            if message["type"] != "http.response.body":
                await send(message)
                return

            body = message.get("body", b"")
            if message.get("more_body", False):
                # This service never streams responses; pass a multi-part body
                # through uncompressed rather than mishandling it.
                await send(initial_message)
                await send(message)
                return

            resp_headers = Headers(raw=initial_message["headers"])
            if "content-encoding" in resp_headers or len(body) < self.minimum_size:
                await send(initial_message)
                await send(message)
                return

            if encoding == "br":
                compressed = await anyio.to_thread.run_sync(
                    brotli.compress, body, brotli.MODE_TEXT, self.quality
                )
            else:
                compressed = await anyio.to_thread.run_sync(gzip.compress, body, 6)

            mutable_headers = MutableHeaders(raw=initial_message["headers"])
            mutable_headers["content-encoding"] = encoding
            mutable_headers["content-length"] = str(len(compressed))
            mutable_headers.add_vary_header("Accept-Encoding")
            message["body"] = compressed
            await send(initial_message)
            await send(message)

        await self.app(scope, receive, send_wrapper)
