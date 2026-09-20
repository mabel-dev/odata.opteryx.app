"""Tests for including views in the OData service document."""

from fastapi import Response

from app.routes.v4 import service_document as sd


class DummyState:
    pass


class DummyRequest:
    state = DummyState()
    # Real requests always carry headers; the endpoint reads If-None-Match.
    headers: dict = {}


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
    # get_service_document calls this directly now (not just inside the
    # mocked _get_workspace_datasets above), so it needs a stub too or it
    # hits the real Firestore batched read against the placeholder client.
    monkeypatch.setattr(sd, "_drop_unlistable_workspaces", lambda db, ids: ids)

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


def _service_document_names(monkeypatch, policies):
    monkeypatch.setattr(sd, "_get_firestore_client", lambda **kwargs: object())
    monkeypatch.setattr(sd, "_get_personal_datasets", lambda db, identity: [])
    monkeypatch.setattr(sd, "_get_workspace_datasets", lambda db, ids: [])
    monkeypatch.setattr(sd, "_get_tasks", lambda db, ids: [])
    monkeypatch.setattr(sd, "_drop_unlistable_workspaces", lambda db, ids: ids)

    req = DummyRequest()
    req.state.claims = {"identity": "tester", "policies": policies}
    result = sd.get_service_document(req, Response())
    return {entry["name"]: entry for entry in result["value"]}


def test_service_document_offers_a_reader_the_shape_tables_only(monkeypatch):
    """The virtual information_schema tables are offered at the tier the engine
    filters their rows at. A reader's `tasks`, `triggers` and `grants` would only
    ever come back empty, so they are not in the document."""
    names = _service_document_names(monkeypatch, [{"pattern": "ws.*", "role": "reader"}])

    assert "ws.information_schema.tables" in names
    assert names["ws.information_schema.tables"]["role"] == "reader"
    assert "ws.information_schema.tasks" not in names
    assert "ws.information_schema.triggers" not in names
    assert "ws.information_schema.grants" not in names
    # public is reader for everyone; the caller's personal workspace is theirs.
    assert "public.information_schema.tasks" not in names
    assert names["personal.information_schema.tasks"]["role"] == "owner"


def test_service_document_offers_an_owner_every_table(monkeypatch):
    names = _service_document_names(monkeypatch, [{"pattern": "ws.*", "role": "owner"}])

    for table in ("tables", "columns", "views", "schemata", "column_relationships", "triggers", "tasks", "grants"):
        assert f"ws.information_schema.{table}" in names, table
    assert names["ws.information_schema.tasks"]["role"] == "owner"
    assert names["ws.information_schema.tasks"]["policy"] == "virtual-information-schema"
