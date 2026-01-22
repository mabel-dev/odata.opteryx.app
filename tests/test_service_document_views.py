"""Tests for including views in the OData service document."""
import sys
import types
from fastapi import Response

# Provide lightweight fake modules required by importing app routes
sys.modules.setdefault('opteryx', types.ModuleType('opteryx'))
_connectors_mod = types.ModuleType('opteryx.connectors')
_connectors_mod.OpteryxConnector = type('OpteryxConnector', (), {})
sys.modules['opteryx.connectors'] = _connectors_mod
_opteryx_catalog_mod = types.ModuleType('opteryx_catalog')
_opteryx_catalog_mod.OpteryxCatalog = type('OpteryxCatalog', (), {'load_dataset': lambda self, path: None})
sys.modules['opteryx_catalog'] = _opteryx_catalog_mod

import app.routes.v4.service_document as sd


class DummyState:
    pass


class DummyRequest:
    state = DummyState()


def test_service_document_includes_views(monkeypatch):
    # Create dataset and view entries
    table = sd.DatasetInfo(resource="public.examples.table", workspace="public", collection="examples", name="table", source="Table")
    view = sd.DatasetInfo(resource="public.examples.view", workspace="public", collection="examples", name="view", source="View")

    # Patch retrieval functions
    monkeypatch.setattr(sd, "_get_personal_datasets", lambda db, identity: [])
    monkeypatch.setattr(sd, "_get_workspace_datasets", lambda db, ids: [table, view])

    # Prepare request and claims
    req = DummyRequest()
    req.state.claims = {"identity": "tester", "policies": [{"pattern": "public.*", "role": "reader"}]}

    resp = Response()
    result = sd.get_service_document(req, resp)

    assert "value" in result
    names = {entry["name"]: entry for entry in result["value"]}
    assert "public.examples.table" in names
    assert "public.examples.view" in names
    assert names["public.examples.table"]["source"] == "Table"
    assert names["public.examples.view"]["source"] == "View"
