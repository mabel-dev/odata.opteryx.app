"""Which virtual information_schema datasets a caller may fetch `$metadata` for.

The service document and the service-wide $metadata offer these tables at the
tier the engine filters their rows at (see app.auth.permissions
.virtual_information_schema_role). The per-dataset endpoint has to hold the same
line, or a table left out of the document is one URL away.
"""

import pytest
from fastapi import HTTPException, Response

from app.routes.v4 import interface


def _request(policies):
    request = type("R", (), {})()
    request.state = type("S", (), {})()
    request.state.claims = {"identity": "test-user", "policies": policies}
    request.headers = {}
    return request


@pytest.fixture
def quiet_catalog(monkeypatch):
    # No Firestore and no catalog: a virtual dataset's schema is fixed, and the
    # lookups that run for real datasets must simply find nothing.
    monkeypatch.setattr(interface, "_external_catalog_stub", lambda *a, **kw: None)
    monkeypatch.setattr(interface, "_declared_relationships", lambda *a, **kw: [])
    monkeypatch.setattr(interface, "_inbound_relationships", lambda *a, **kw: [])
    monkeypatch.setattr(interface, "clear_metadata_cache", lambda: None)
    interface.clear_metadata_cache()


def _render(policies, workstream, dataset):
    response = Response()
    result = interface.dataset_metadata(
        _request(policies), response, workstream=workstream, collection="information_schema", dataset=dataset
    )
    body = result.content if hasattr(result, "content") else result.body
    return body.decode()


READER = [{"pattern": "ws.*", "role": "reader"}]
OWNER = [{"pattern": "ws.*", "role": "owner"}]


def test_a_reader_may_fetch_the_shape_tables(quiet_catalog):
    body = _render(READER, "ws", "tables")

    assert 'Property Name="table_name"' in body
    assert 'Term="Custom.Role" String="reader"' in body


def test_a_reader_is_refused_the_automation_tables(quiet_catalog):
    for dataset in ("tasks", "triggers", "grants"):
        with pytest.raises(HTTPException) as raised:
            _render(READER, "ws", dataset)
        assert raised.value.status_code == 403, dataset


def test_an_owner_may_fetch_the_automation_tables(quiet_catalog):
    body = _render(OWNER, "ws", "tasks")

    assert 'Property Name="task_name"' in body
    assert 'Term="Custom.Role" String="owner"' in body


def test_public_offers_the_shape_tables_to_anyone_and_the_rest_to_nobody(quiet_catalog):
    assert 'Property Name="table_name"' in _render([], "public", "tables")
    with pytest.raises(HTTPException) as raised:
        _render([], "public", "grants")
    assert raised.value.status_code == 403


def test_the_callers_personal_workspace_offers_everything(quiet_catalog):
    body = _render([], "personal", "triggers")

    assert 'Property Name="trigger_name"' in body
    assert 'Term="Custom.Role" String="owner"' in body


def test_a_workspace_the_caller_cannot_reach_offers_nothing(quiet_catalog):
    with pytest.raises(HTTPException) as raised:
        _render(READER, "elsewhere", "tables")
    assert raised.value.status_code == 403
