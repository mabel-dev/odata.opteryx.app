"""Validators and caches shared by both $metadata endpoints.

Three things live here: ETag validators, the opt-in whole-response cache, and
the column-statistics cache (documented at its own definition below, because it
makes a different trade from the other two).

OData clients refetch $metadata constantly -- Power BI does so on every refresh,
and the web UI does so every time a details panel is opened. Two savings apply,
and only one is on by default:

 - ETag / If-None-Match (always on): the document is rebuilt fresh on every
   request -- these endpoints' entire job is to report what exists *now*, and a
   dataset can be dropped or altered between requests, so there is no way to
   know the previous answer is still correct without asking the catalog again.
   But if the freshly-rebuilt document is byte-identical to what the client
   already has, a 304 still saves the transfer and the client's XML parse.
 - The response cache (opt-in via ODATA_METADATA_CACHE_TTL, default 0/off):
   skips rebuilding -- and therefore the catalog round trips -- for a fixed
   window, at the cost of serving a stale answer to "does this exist / what
   does its schema look like" for up to that long. Off by default because that
   trade is exactly backwards for an endpoint whose purpose is to answer that
   question; set it explicitly if a deployment can tolerate the staleness in
   exchange for fewer catalog round trips under heavy $metadata polling.

Both endpoints share this one cache. Their keys carry whatever the rendered
document depends on -- the caller's role and policy for the per-dataset
document, the caller's identity and policy set for the service-wide one -- and
are namespaced by a leading tag so the two key spaces cannot collide.

An ETag is only useful if identical inputs render identical bytes, on every
replica. Callers must therefore build their document from a deterministically
ordered dataset list; a Firestore stream order or a set iteration order is
neither stable across processes nor guaranteed stable within one.
"""

from __future__ import annotations

import copy
import hashlib
import threading
import time
from collections import OrderedDict

from fastapi import Response

from opteryx_shared_services.config import get_config

_METADATA_CACHE_MAX_ENTRIES = 128
_metadata_cache_lock = threading.Lock()
_metadata_cache: OrderedDict[tuple, tuple[float, str, str, dict]] = OrderedDict()


def metadata_cache_ttl() -> float:
    try:
        return float(get_config("ODATA_METADATA_CACHE_TTL", "0"))
    except ValueError:
        return 0.0


def metadata_cache_get(key) -> tuple[str, str, dict] | None:
    """Return (etag, xml, headers) for a live cache entry, else None."""
    now = time.monotonic()
    with _metadata_cache_lock:
        entry = _metadata_cache.get(key)
        if entry is None:
            return None
        expires_at, etag, xml, headers = entry
        if expires_at <= now:
            del _metadata_cache[key]
            return None
        _metadata_cache.move_to_end(key)
        return etag, xml, dict(headers)


def metadata_cache_put(key, etag: str, xml: str, headers: dict) -> None:
    ttl = metadata_cache_ttl()
    if ttl <= 0:
        return
    with _metadata_cache_lock:
        _metadata_cache[key] = (time.monotonic() + ttl, etag, xml, dict(headers))
        _metadata_cache.move_to_end(key)
        while len(_metadata_cache) > _METADATA_CACHE_MAX_ENTRIES:
            _metadata_cache.popitem(last=False)


def clear_metadata_cache() -> None:
    """Drop every cached metadata document. For tests and cache-invalidation hooks."""
    with _metadata_cache_lock:
        _metadata_cache.clear()


# Column statistics, keyed by the immutable artifacts they were computed from.
#
# `SimpleDataset.describe()` is the most expensive thing either endpoint does:
# it fetches the current snapshot's manifest from GCS and walks every entry in
# it to accumulate per-column min/max, null counts and cardinality. Its result
# is a pure function of that manifest and the schema used to index it, and both
# are addressed by ids that change whenever their content does -- a write
# produces a new snapshot id, a schema change a new schema id.
#
# So unlike the response cache above, this one needs no TTL and trades away no
# freshness: a dataset that has changed misses by construction, because the key
# it would be looked up under does not exist yet.
_DESCRIBE_CACHE_MAX_ENTRIES = 256
_describe_cache_lock = threading.Lock()
_describe_cache: OrderedDict[tuple, dict] = OrderedDict()


def describe_cache_get(key) -> dict | None:
    """A previously computed description, or None.

    Returns a deep copy: callers merge these stats into their own per-column
    dicts and the nested values would otherwise be shared with the cache.
    """
    with _describe_cache_lock:
        description = _describe_cache.get(key)
        if description is None:
            return None
        _describe_cache.move_to_end(key)
        return copy.deepcopy(description)


def describe_cache_put(key, description: dict) -> None:
    with _describe_cache_lock:
        _describe_cache[key] = copy.deepcopy(description)
        _describe_cache.move_to_end(key)
        while len(_describe_cache) > _DESCRIBE_CACHE_MAX_ENTRIES:
            _describe_cache.popitem(last=False)


def clear_describe_cache() -> None:
    """Drop every cached description. For tests."""
    with _describe_cache_lock:
        _describe_cache.clear()


def etag_for(xml: str) -> str:
    return '"' + hashlib.blake2b(xml.encode("utf-8"), digest_size=16).hexdigest() + '"'


def etag_matches(if_none_match: str | None, etag: str) -> bool:
    """Whether an If-None-Match header covers `etag` (RFC 9110 §13.1.2)."""
    if not if_none_match:
        return False
    if if_none_match.strip() == "*":
        return True
    for candidate in if_none_match.split(","):
        candidate = candidate.strip()
        candidate = candidate.removeprefix("W/")
        if candidate == etag:
            return True
    return False


def metadata_response(xml: str, etag: str, headers: dict, if_none_match: str | None) -> Response:
    """Serve an EDMX document, or a 304 when the client already holds this version."""
    headers = {**headers, "ETag": etag}
    if etag_matches(if_none_match, etag):
        # A 304 carries no body, so media_type would be misleading here.
        return Response(status_code=304, headers=headers)
    return Response(content=xml, media_type="application/xml", headers=headers)


__all__ = [
    "clear_describe_cache",
    "clear_metadata_cache",
    "describe_cache_get",
    "describe_cache_put",
    "etag_for",
    "etag_matches",
    "metadata_cache_get",
    "metadata_cache_put",
    "metadata_cache_ttl",
    "metadata_response",
]
