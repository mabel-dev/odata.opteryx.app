"""Tests for source type annotations in EDMX metadata."""

from fastapi import Response

from tests._opteryx_stubs import load_with_stubs

# Load against the stubs so the heavy engine/catalog imports are skipped; the
# stubs are torn down immediately so later tests still get the real opteryx.
metadata = load_with_stubs("metadata_mod", "app", "routes", "v4", "metadata.py")


class DummyState:
    pass


class DummyRequest:
    state = DummyState()


def test_edmx_metadata_includes_source_annotation(monkeypatch):
    # Create a fake DatasetInfo with explicit source
    ds = metadata.DatasetInfo(
        resource="public.ns.table",
        workspace="public",
        collection="ns",
        name="table",
        source="Table",
    )

    # No Firestore in tests: hand the endpoint a placeholder client so it gets
    # past the connectivity check -- the dataset lookups below never use it.
    monkeypatch.setattr(metadata, "_get_firestore_client", lambda **kwargs: object())
    # Patch dataset retrieval functions to return our dataset
    monkeypatch.setattr(metadata, "_get_personal_datasets", lambda db, identity: [ds])
    monkeypatch.setattr(metadata, "_get_workspace_datasets", lambda db, ids: [])
    # Ensure dataset schema lookup returns a minimal column so EntityType is created
    monkeypatch.setattr(metadata, "_get_dataset_schema", lambda dataset_info: [("id", "Edm.Int64")])

    # Prepare a fake request with claims allowing access to public workspace
    req = DummyRequest()
    req.state.claims = {
        "identity": "tester",
        "policies": [{"pattern": "public.*", "role": "reader"}],
    }

    resp = Response()
    res = metadata.get_edmx_metadata(req, resp)

    # Response content should include our custom annotation term
    content = res.body.decode() if hasattr(res, "body") else res.content.decode()
    assert "Custom.SourceType" in content
    assert "Table" in content
