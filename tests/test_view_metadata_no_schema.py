"""Test metadata generation for views that don't expose schema in the catalog."""

from fastapi import Response

from app.routes.v4 import interface


class DummyState:
    claims = {"identity": "test-user"}


class DummyRequest:
    state = DummyState()
    # Real requests always carry headers; the endpoint reads If-None-Match.
    headers: dict = {}


class FakeViewNoSchema:
    # No schema(), no describe(), but has snapshot
    def snapshot(self):
        return type("Snap", (), {"timestamp_ms": 1700000000000})()


class FakeCatalog:
    def __init__(self, *args, **kwargs):
        pass

    def load_dataset(self, path):
        raise RuntimeError("not a dataset")

    def load_view(self, path):
        return FakeViewNoSchema()


def test_view_metadata_without_schema(monkeypatch):
    # Use fake catalog that returns a view object without schema/describe
    monkeypatch.setattr(interface, "OpteryxCatalog", FakeCatalog)

    req = DummyRequest()
    resp = Response()

    res = interface.dataset_metadata(
        req, resp, workstream="public", collection="examples", dataset="v_no_schema"
    )
    content = res.content.decode() if hasattr(res, "content") else res.body.decode()

    # Ensure metadata is valid and contains the placeholder property 'id'
    assert "Custom.SourceType" in content
    assert "View" in content
    # The EDMX is serialised with namespace prefixes (<ns1:Property .../>), so
    # match on the tag name and attribute rather than anchoring on "<".
    assert 'Property Name="id"' in content
    # Header should indicate source; no last-modified since metadata timestamp is missing
    assert resp.headers.get("X-Dataset-Source") == "View"
    assert resp.headers.get("X-Dataset-LastModified") is None
