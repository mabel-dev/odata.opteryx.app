"""Tests for last-modified annotations in EDMX metadata."""

from fastapi import Response

from tests._opteryx_stubs import load_with_stubs


class FakeSnapshot:
    def __init__(self, ts_ms):
        self.timestamp_ms = ts_ms


class FakeDataset:
    def __init__(self, ts_ms=None):
        self._snapshot = FakeSnapshot(ts_ms) if ts_ms else None

    def snapshot(self):
        return self._snapshot


class FakeCatalog:
    def __init__(self, *args, **kwargs):
        pass

    def load_dataset(self, path):
        # Provide a dataset object with a snapshot timestamp
        return FakeDataset(1700000000000)


# Load against the stubs so the heavy engine/catalog imports are skipped; the
# stubs are torn down immediately so later tests still get the real opteryx.
metadata = load_with_stubs("metadata_mod", "app", "routes", "v4", "metadata.py")
metadata.OpteryxCatalog = FakeCatalog


def test_edmx_metadata_includes_last_modified(monkeypatch):
    # Create dataset with last_modified_ms in the Firestore doc (also catalog returns snapshot)
    ds = metadata.DatasetInfo(
        resource="public.ns.t",
        workspace="public",
        collection="ns",
        name="t",
        source="Table",
        last_modified_ms=1600000000000,
    )

    # No Firestore in tests: hand the endpoint a placeholder client so it gets
    # past the connectivity check -- the dataset lookups below never use it.
    monkeypatch.setattr(metadata, "_get_firestore_client", lambda **kwargs: object())
    monkeypatch.setattr(metadata, "_get_personal_datasets", lambda db, identity: [ds])
    monkeypatch.setattr(metadata, "_get_workspace_datasets", lambda db, ids: [])
    monkeypatch.setattr(metadata, "_get_dataset_schema", lambda dataset_info: [("id", "Edm.Int64")])

    req = type("R", (), {})()
    req.state = type("S", (), {})()
    req.state.claims = {
        "identity": "tester",
        "policies": [{"pattern": "public.*", "role": "reader"}],
    }

    resp = Response()
    res = metadata.get_edmx_metadata(req, resp)

    content = res.body.decode() if hasattr(res, "body") else res.content.decode()
    assert "Custom.LastModified" in content
    # Check ISO timestamp format (starts with 20... and contains +00:00 or Z)
    assert "20" in content
