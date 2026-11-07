"""OData v4 minimal-conformance header semantics: Accept (8.2.1) and
OData-MaxVersion (8.2.7).

Minimal conformance requires the service to either honour these headers or
fail the request -- silently ignoring them (returning 200 with a body the
client said it can't parse, or a version newer than the client can handle) is
itself a violation. See `require_acceptable` for the single entry point.
"""

from __future__ import annotations

import re
from typing import Optional

from fastapi import HTTPException
from fastapi import Request

MEDIA_JSON = "application/json"
MEDIA_XML = "application/xml"
# Not an OData format. Offered on the data route for spreadsheet tools that
# can only read CSV (Google Sheets IMPORTDATA, Excel From Web); see
# `wants_csv` and `rows_to_csv` in routes/v4/interface.py.
MEDIA_CSV = "text/csv"

# This service implements OData 4.0. A client that caps itself below that
# with OData-MaxVersion is asking for something this service cannot produce.
_SERVICE_VERSION = (4, 0)

_VERSION_RE = re.compile(r"^\s*(\d+)\.(\d+)")


def _parse_version(header_value: str) -> Optional[tuple[int, int]]:
    """`"4.0"` -> (4, 0); `"3.0;q=1"` -> (3, 0); unparsable -> None.

    None means "we can't tell", which is treated as permissive rather than
    rejected -- failing a request over a header we can't interpret would be
    worse than the gap it closes.
    """
    match = _VERSION_RE.match(header_value)
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)))


def _accepts(accept_header: str, acceptable: tuple[str, ...]) -> bool:
    """True if `accept_header` admits at least one type in `acceptable`.

    Matches on bare type/subtype, ignoring parameters (`;q=...`,
    `;odata.metadata=...`) and wildcards (`*/*`, `type/*`). An absent or
    empty header is handled by the caller, not here -- HTTP treats "no
    Accept header" as "accepts anything", which is a different case from an
    Accept header present but not matching.
    """
    for raw_range in accept_header.split(","):
        media_range = raw_range.split(";", 1)[0].strip().lower()
        if not media_range:
            continue
        if media_range == "*/*":
            return True
        media_type = media_range.split("/", 1)[0]
        for candidate in acceptable:
            if media_range == candidate:
                return True
            if media_range == f"{media_type}/*" and candidate.startswith(f"{media_type}/"):
                return True
    return False


def require_acceptable(*acceptable: str):
    """Dependency factory: enforce Accept and OData-MaxVersion for one route.

    `acceptable` is the set of media types this route can actually return
    (e.g. just `application/json` for a data route, `application/xml` for a
    $metadata route) -- negotiation is meaningless without knowing what the
    endpoint is capable of producing.
    """

    def _dependency(request: Request) -> None:
        accept = request.headers.get("accept")
        if accept and not _accepts(accept, acceptable):
            raise HTTPException(
                status_code=406,
                detail=(
                    f"Accept header {accept!r} does not match a format this endpoint "
                    f"returns ({', '.join(acceptable)})"
                ),
            )

        max_version = request.headers.get("odata-maxversion")
        if max_version:
            parsed = _parse_version(max_version)
            if parsed is not None and parsed < _SERVICE_VERSION:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"OData-MaxVersion {max_version!r} is below the version this "
                        f"service implements (4.0)"
                    ),
                )

    return _dependency


__all__ = ["MEDIA_CSV", "MEDIA_JSON", "MEDIA_XML", "require_acceptable"]
