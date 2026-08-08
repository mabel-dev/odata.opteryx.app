"""Tests for source type annotations in EDMX metadata."""

from tests._opteryx_stubs import load_with_stubs

# Load against the stubs so the heavy engine/catalog imports are skipped; the
# stubs are torn down immediately so later tests still get the real opteryx.
metadata = load_with_stubs("metadata_mod", "app", "routes", "v4", "metadata.py")


class DummyState:
    pass


class DummyRequest:
    state = DummyState()
    # Real requests always carry headers; the endpoint reads If-None-Match.
    headers: dict = {}


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
    # Stand in for the batched schema read with a minimal column, so an
    # EntityType is created and the annotations below have somewhere to hang.
    monkeypatch.setattr(
        metadata,
        "_facts_for",
        lambda db, datasets: {
            dataset.resource: metadata._DatasetFacts(
                columns=[("id", "Edm.Int64", {})], last_modified_ms=None
            )
            for dataset in datasets
        },
    )

    # Prepare a fake request with claims allowing access to public workspace
    req = DummyRequest()
    req.state.claims = {
        "identity": "tester",
        "policies": [{"pattern": "public.*", "role": "reader"}],
    }

    res = metadata.get_edmx_metadata(req)

    # Response content should include our custom annotation term
    content = res.body.decode() if hasattr(res, "body") else res.content.decode()
    assert "Custom.SourceType" in content
    assert "Table" in content
