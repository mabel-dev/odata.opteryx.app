"""Tests for including views in the OData service document."""

from fastapi import Response

from app.routes.v4 import service_document as sd


class DummyState:
    pass


class DummyRequest:
    state = DummyState()


def test_service_document_includes_views(monkeypatch):
    # Create dataset and view entries
    table = sd.DatasetInfo(
        resource="public.examples.table",
        workspace="public",
        collection="examples",
        name="table",
        source="Table",
    )
    view = sd.DatasetInfo(
        resource="public.examples.view",
        workspace="public",
        collection="examples",
        name="view",
        source="View",
    )

    # No Firestore in tests: hand the endpoint a placeholder client so it gets
    # past the connectivity check -- the dataset lookups below never use it.
    monkeypatch.setattr(sd, "_get_firestore_client", lambda **kwargs: object())
    # Patch retrieval functions
    monkeypatch.setattr(sd, "_get_personal_datasets", lambda db, identity: [])
    monkeypatch.setattr(sd, "_get_workspace_datasets", lambda db, ids: [table, view])

    # Prepare request and claims
    req = DummyRequest()
    req.state.claims = {
        "identity": "tester",
        "policies": [{"pattern": "public.*", "role": "reader"}],
    }

    resp = Response()
    result = sd.get_service_document(req, resp)

    assert "value" in result
    names = {entry["name"]: entry for entry in result["value"]}
    assert "public.examples.table" in names
    assert "public.examples.view" in names
    assert names["public.examples.table"]["source"] == "Table"
    assert names["public.examples.view"]["source"] == "View"
