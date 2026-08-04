"""Tests for $metadata ETag / If-None-Match handling and the EDMX response cache.

OData clients refetch $metadata constantly, so the endpoint emits an ETag (letting
a client that already holds the document skip the transfer and the XML parse) and
caches the rendered document briefly (letting the server skip the catalog round
trips that build it).
"""

import pytest
from fastapi import Response

from app.routes.v4 import interface


class DummyState:
    claims = {"identity": "test-user"}


class DummyRequest:
    def __init__(self, headers=None):
        self.state = DummyState()
        self.headers = headers or {}


class FakeDataset:
    def __init__(self):
        self._schema = type(
            "S",
            (),
            {"columns": [type("C", (), {"name": "id", "type": "INTEGER", "nullable": False})]},
        )

    def schema(self):
        return self._schema

    def describe(self):
        return {"id": {"min": 1, "max": 100}}

    def snapshot(self):
        return None


@pytest.fixture
def catalog_calls(monkeypatch):
    """Install a fake catalog and return the list of times it was constructed."""
    calls = []

    class FakeCatalog:
        def __init__(self, *args, **kwargs):
            calls.append(kwargs)

        def load_dataset(self, path):
            return FakeDataset()

        def load_view(self, path):
            raise RuntimeError("not a view")

    monkeypatch.setattr(interface, "OpteryxCatalog", FakeCatalog)
    return calls


def _get(headers=None):
    return interface.dataset_metadata(
        DummyRequest(headers),
        Response(),
        workstream="public",
        collection="examples",
        dataset="my_table",
    )


def test_response_carries_an_etag(catalog_calls):
    res = _get()

    assert res.status_code == 200
    assert res.headers["etag"].startswith('"')
    assert b"Edmx" in res.body


def test_etag_is_stable_across_requests(catalog_calls):
    assert _get().headers["etag"] == _get().headers["etag"]


def test_matching_if_none_match_returns_304(catalog_calls):
    etag = _get().headers["etag"]

    res = _get({"if-none-match": etag})

    assert res.status_code == 304
    assert res.body == b""
    # The ETag must be repeated on a 304 so the client can keep revalidating.
    assert res.headers["etag"] == etag


def test_non_matching_if_none_match_returns_the_document(catalog_calls):
    res = _get({"if-none-match": '"not-the-current-version"'})

    assert res.status_code == 200
    assert b"Edmx" in res.body


def test_wildcard_if_none_match_returns_304(catalog_calls):
    res = _get({"if-none-match": "*"})

    assert res.status_code == 304


def test_weak_validator_is_matched(catalog_calls):
    etag = _get().headers["etag"]

    res = _get({"if-none-match": f"W/{etag}"})

    assert res.status_code == 304


def test_etag_from_a_list_of_validators_is_matched(catalog_calls):
    etag = _get().headers["etag"]

    res = _get({"if-none-match": f'"other", {etag}, "another"'})

    assert res.status_code == 304


def test_default_ttl_is_zero_so_every_request_hits_the_catalog(catalog_calls):
    """Blind time-based caching is wrong for an endpoint whose job is to answer
    "what does this dataset look like right now" -- a dropped or altered table
    must be visible on the very next request, not after some TTL elapses. So the
    response cache is opt-in, not on by default.
    """
    _get()
    _get()

    assert len(catalog_calls) == 2, "each request must re-check the catalog by default"


def test_304_saves_the_transfer_even_with_caching_off(catalog_calls):
    """The catalog is still queried (content might have changed), but if the
    freshly-rebuilt document is unchanged, the client still gets a 304 -- the
    transfer and its own parse are saved even though the server did the work.
    """
    etag = _get().headers["etag"]
    assert len(catalog_calls) == 1

    res = _get({"if-none-match": etag})
    assert res.status_code == 304
    assert len(catalog_calls) == 2, "content is still freshly checked, just not re-sent"


def test_repeat_request_is_served_without_touching_the_catalog(catalog_calls, monkeypatch):
    monkeypatch.setenv("ODATA_METADATA_CACHE_TTL", "60")
    _get()
    assert len(catalog_calls) == 1

    _get()
    assert len(catalog_calls) == 1, "second request should have been served from cache"


def test_304_is_served_without_touching_the_catalog(catalog_calls, monkeypatch):
    monkeypatch.setenv("ODATA_METADATA_CACHE_TTL", "60")
    etag = _get().headers["etag"]
    assert len(catalog_calls) == 1

    assert _get({"if-none-match": etag}).status_code == 304
    assert len(catalog_calls) == 1


def test_cached_response_keeps_its_headers(catalog_calls):
    fresh = _get()
    cached = _get()

    assert cached.headers["x-dataset-source"] == fresh.headers["x-dataset-source"] == "Table"
    assert cached.headers["odata-version"] == "4.0"


def test_ttl_zero_disables_caching_but_keeps_etags(catalog_calls, monkeypatch):
    monkeypatch.setenv("ODATA_METADATA_CACHE_TTL", "0")

    first = _get()
    second = _get()

    assert len(catalog_calls) == 2
    # Same inputs still produce the same validator, so conditional requests work.
    assert first.headers["etag"] == second.headers["etag"]


def test_cache_expires(catalog_calls, monkeypatch):
    monkeypatch.setenv("ODATA_METADATA_CACHE_TTL", "60")
    _get()
    assert len(catalog_calls) == 1

    clock = interface.time.monotonic() + 61
    monkeypatch.setattr(interface.time, "monotonic", lambda: clock)
    _get()

    assert len(catalog_calls) == 2


def test_changed_metadata_produces_a_new_etag(catalog_calls, monkeypatch):
    """A dataset whose schema changed must not keep serving the old validator."""
    monkeypatch.setenv("ODATA_METADATA_CACHE_TTL", "0")
    before = _get().headers["etag"]

    class WiderDataset(FakeDataset):
        def __init__(self):
            super().__init__()
            self._schema = type(
                "S",
                (),
                {
                    "columns": [
                        type("C", (), {"name": "id", "type": "INTEGER", "nullable": False}),
                        type("C2", (), {"name": "added", "type": "VARCHAR", "nullable": True}),
                    ]
                },
            )

    monkeypatch.setattr(
        interface,
        "OpteryxCatalog",
        lambda *a, **k: type(
            "C",
            (),
            {"load_dataset": lambda self, path: WiderDataset(), "load_view": lambda self, p: None},
        )(),
    )

    assert _get().headers["etag"] != before
