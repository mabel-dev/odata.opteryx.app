"""Custom FastAPI response classes.

Provides a fast JSON response backed by ``msgspec`` instead of ``orjson``.
``orjson`` (a Rust extension) publishes no free-threaded (``cp314t``) wheels,
so it cannot be used on a free-threaded Python 3.14 build; ``msgspec`` does
ship free-threaded wheels and is comparably fast.
"""

from __future__ import annotations

from typing import Any

import msgspec
from fastapi.responses import JSONResponse

__all__ = ["MsgspecJSONResponse"]


class MsgspecJSONResponse(JSONResponse):
    """``JSONResponse`` that serializes with ``msgspec.json`` for speed.

    ``msgspec`` encodes the common OData payload types natively (``dict``,
    ``list``, ``str``/``int``/``float``/``bool``/``None``, ``datetime``,
    ``date``, ``time``, ``uuid.UUID``, ``bytes`` as base64). Response values are
    additionally normalised to JSON-friendly primitives upstream (see
    ``interface._sanitize_value_for_odata``).
    """

    def render(self, content: Any) -> bytes:
        return msgspec.json.encode(content)
