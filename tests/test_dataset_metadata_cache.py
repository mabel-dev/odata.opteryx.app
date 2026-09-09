"""Tests for the per-dataset $metadata response cache, and for its ABSENT validator.

This document carries no ETag (decision 2026-09-09): it is opened a handful of
times a session from the Studio UI rather than polled, so revalidation bought
little, and a validator over its bytes would constrain what may go in it -- a
downstream consumer list changes when a DIFFERENT dataset commits, and would
churn a hash that is supposed to identify this version. The SERVICE-wide
document still carries one; see tests/test_service_metadata_catalog_traffic.py.

What survives is the opt-in whole-response cache, which skips the catalog round
trips that build the document for ODATA_METADATA_CACHE_TTL seconds.
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


def test_response_carries_no_etag(catalog_calls):
    res = _get()

    assert res.status_code == 200
    assert "etag" not in {name.lower() for name in res.headers}
    assert b"Edmx" in res.body


def test_if_none_match_is_ignored(catalog_calls):
    """No validator is issued, so nothing a client sends can be honoured -- and a
    conditional request must get the document, never an empty 304 it cannot fill
    from a cache it was never allowed to populate."""
    for header in ('"anything"', "*", 'W/"anything"'):
        res = _get({"if-none-match": header})

        assert res.status_code == 200, header
        assert b"Edmx" in res.body


def test_default_ttl_is_zero_so_every_request_hits_the_catalog(catalog_calls):
    """Blind time-based caching is wrong for an endpoint whose job is to answer
    "what does this dataset look like right now" -- a dropped or altered table
    must be visible on the very next request, not after some TTL elapses. So the
    response cache is opt-in, not on by default.
    """
    _get()
    _get()

    assert len(catalog_calls) == 2, "each request must re-check the catalog by default"


def test_repeat_request_is_served_without_touching_the_catalog(catalog_calls, monkeypatch):
    monkeypatch.setenv("ODATA_METADATA_CACHE_TTL", "60")
    _get()
    assert len(catalog_calls) == 1

    _get()
    assert len(catalog_calls) == 1, "second request should have been served from cache"


def test_cached_response_keeps_its_headers(catalog_calls):
    fresh = _get()
    cached = _get()

    assert cached.headers["x-dataset-source"] == fresh.headers["x-dataset-source"] == "Table"
    assert cached.headers["odata-version"] == "4.0"


def test_ttl_zero_disables_caching(catalog_calls, monkeypatch):
    monkeypatch.setenv("ODATA_METADATA_CACHE_TTL", "0")

    first = _get()
    second = _get()

    assert len(catalog_calls) == 2
    assert first.body == second.body


def test_cache_expires(catalog_calls, monkeypatch):
    monkeypatch.setenv("ODATA_METADATA_CACHE_TTL", "60")
    _get()
    assert len(catalog_calls) == 1

    clock = interface.time.monotonic() + 61
    monkeypatch.setattr(interface.time, "monotonic", lambda: clock)
    _get()

    assert len(catalog_calls) == 2


def test_changed_metadata_is_served_immediately(catalog_calls, monkeypatch):
    """With the cache off, a schema change shows up on the very next request --
    the property the missing validator must not cost us."""
    monkeypatch.setenv("ODATA_METADATA_CACHE_TTL", "0")
    before = _get().body

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

    assert _get().body != before
