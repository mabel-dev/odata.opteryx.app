"""Test that tables include snapshot last-modified header and annotation."""

from fastapi import Response

from tests._opteryx_stubs import load_with_stubs

# Load interface.py against lightweight fakes so this test needs no engine or
# catalog. load_with_stubs tears the fakes back out of sys.modules as soon as
# the module is loaded -- leaving them installed poisons every later test that
# imports the real opteryx ("cannot import name ... (unknown location)").
interface = load_with_stubs("interface_mod", "app", "routes", "v4", "interface.py")


class DummyState:
    claims = {"identity": "test-user"}


class DummyRequest:
    state = DummyState()


class FakeDatasetWithSnapshot:
    def __init__(self):
        self._schema = type(
            "S",
            (),
            {"columns": [type("C", (), {"name": "id", "type": "INTEGER", "nullable": False})]},
        )

    def schema(self):
        return self._schema

    def describe(self):
        return {"id": {"min": 1, "max": 10}}

    def snapshot(self):
        return type(
            "Snap",
            (),
            {
                "timestamp_ms": 1600000000000,
                "snapshot_id": "snap-12345",
                "summary": {"total-records": 42, "total-data-size": 1024},
                "commit_message": "backfill Q3 records",
                "author": "ichnos",
            },
        )()

    @property
    def metadata(self):
        return type("M", (), {"description": "table desc", "describer": "Llama-xyz"})()


class FakeCatalog:
    def __init__(self, *args, **kwargs):
        pass

    def load_dataset(self, path):
        return FakeDatasetWithSnapshot()

    def load_view(self, path):
        raise RuntimeError("not a view")


def test_table_metadata_last_modified(monkeypatch):
    monkeypatch.setattr(interface, "OpteryxCatalog", FakeCatalog)

    req = DummyRequest()
    resp = Response()

    res = interface.dataset_metadata(
        req, resp, workstream="public", collection="examples", dataset="my_table"
    )
    content = res.content.decode() if hasattr(res, "content") else res.body.decode()

    assert "Custom.SourceType" in content
    assert "Table" in content
    assert resp.headers.get("X-Dataset-LastModified") == str(1600000000000)
    assert "Custom.LastModified" in content
    assert "+00:00" in content or "Z" in content
    # Snapshot annotations
    assert "Custom.Snapshot.Id" in content
    assert "Custom.Snapshot.TotalRecords" in content
    assert "Custom.Snapshot.TotalDataSize" in content
    assert "Custom.Snapshot.CommitMessage" in content
    assert "backfill Q3 records" in content
    assert "Custom.Snapshot.Author" in content
    assert "ichnos" in content
    # Description/LLM annotations
    assert "Custom.Description" in content
    assert "Custom.LLMDescribed" in content
