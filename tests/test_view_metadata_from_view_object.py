"""Test that metadata endpoint includes view metadata (definition, author, description, LLM flag)."""

from fastapi import Response

from app.routes.v4 import interface


class DummyState:
    claims = {"identity": "test-user"}


class DummyRequest:
    state = DummyState()
    # Real requests always carry headers; the endpoint reads If-None-Match.
    headers: dict = {}


class FakeMeta:
    def __init__(self, ts, desc, author, describer):
        self.timestamp_ms = ts
        self.description = desc
        self.author = author
        self.describer = describer


class FakeView:
    def __init__(self, sql, meta):
        self.sql = sql
        self.metadata = meta


class FakeCatalog:
    def __init__(self, *args, **kwargs):
        pass

    def load_dataset(self, path):
        raise RuntimeError("not a dataset")

    def load_view(self, path):
        return FakeView(
            "select * from foo where id > 0",
            FakeMeta(1700000000000, "desc", "authorX", "Gemini:desc"),
        )


def test_view_metadata_includes_annotations(monkeypatch):
    monkeypatch.setattr(interface, "OpteryxCatalog", FakeCatalog)

    req = DummyRequest()
    resp = Response()

    res = interface.dataset_metadata(
        req, resp, workstream="public", collection="examples", dataset="my_view"
    )
    content = res.content.decode() if hasattr(res, "content") else res.body.decode()

    assert "Custom.SourceType" in content
    assert "View" in content
    assert "Custom.Definition" in content
    assert "Custom.Author" in content
    assert "Custom.Description" in content
    assert "Custom.LLMDescribed" in content
    # header from metadata timestamp
    assert resp.headers.get("X-Dataset-LastModified") == str(1700000000000)
    # LastModified annotation present in metadata and in ISO format
    assert "Custom.LastModified" in content
    assert "+00:00" in content or "Z" in content
