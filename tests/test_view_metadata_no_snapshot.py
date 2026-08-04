"""Test that views without snapshot do not set X-Dataset-LastModified."""

from fastapi import Response

from app.routes.v4 import interface


class DummyState:
    claims = {"identity": "test-user"}


class DummyRequest:
    state = DummyState()


class FakeViewNoSnapshot:
    # Has schema to avoid placeholder but no snapshot method
    def schema(self):
        return type(
            "S",
            (),
            {"columns": [type("C", (), {"name": "id", "type": "INTEGER", "nullable": False})]},
        )

    def describe(self):
        return {"id": {"min": 1, "max": 10}}


class FakeCatalog:
    def __init__(self, *args, **kwargs):
        pass

    def load_dataset(self, path):
        raise RuntimeError("not a dataset")

    def load_view(self, path):
        return FakeViewNoSnapshot()


def test_view_without_snapshot_no_last_modified(monkeypatch):
    monkeypatch.setattr(interface, "OpteryxCatalog", FakeCatalog)

    req = DummyRequest()
    resp = Response()

    res = interface.dataset_metadata(
        req, resp, workstream="public", collection="examples", dataset="v_no_snapshot"
    )

    content = res.content.decode() if hasattr(res, "content") else res.body.decode()
    assert "Custom.SourceType" in content
    assert "View" in content
    # Ensure last-modified header is not set for views without snapshot
    assert resp.headers.get("X-Dataset-LastModified") is None
