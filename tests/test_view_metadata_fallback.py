"""Tests for view fallback in dataset metadata endpoint."""

from fastapi import Response

from app.routes.v4 import interface


class DummyState:
    claims = {"identity": "test-user"}


class DummyRequest:
    state = DummyState()


class DummyDoc:
    def __init__(self, data):
        self._data = data

    def to_dict(self):
        return self._data


class DummyQuery:
    def __init__(self, docs):
        self._docs = docs

    def where(self, *args, **kwargs):
        return self

    def limit(self, *_):
        return self

    def stream(self):
        for d in self._docs:
            yield d


class DummyDB:
    def __init__(self, docs):
        self._docs = docs

    def collection_group(self, name):
        if name == "views":
            return DummyQuery(self._docs)
        return DummyQuery([])


def test_dataset_metadata_view_fallback(monkeypatch):
    # Make OpteryxCatalog.load_dataset raise and provide load_view to return a view object with metadata timestamp
    class FakeView:
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

        @property
        def metadata(self):
            return type(
                "M",
                (),
                {
                    "timestamp_ms": 1700000000000,
                    "description": None,
                    "author": None,
                    "describer": None,
                },
            )()

    class FakeCatalog:
        def __init__(self, *args, **kwargs):
            pass

        def load_dataset(self, path):
            raise RuntimeError("not a dataset")

        def load_view(self, path):
            return FakeView()

    monkeypatch.setattr(interface, "OpteryxCatalog", FakeCatalog)

    # Prepare request/response
    req = DummyRequest()
    resp = Response()

    # Call metadata for view and expect success
    res = interface.dataset_metadata(
        req, resp, workstream="public", collection="examples", dataset="my_view"
    )

    content = res.content.decode() if hasattr(res, "content") else res.body.decode()
    assert "Custom.SourceType" in content
    assert "View" in content
    # Header should indicate source
    assert resp.headers.get("X-Dataset-Source") == "View"
    assert resp.headers.get("X-Dataset-LastModified") is not None
