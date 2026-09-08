"""Row queries honour a `@{label}` version selector on the dataset segment.

Unlike `dataset_metadata`, `read_dataset` never loads the catalog dataset
directly -- it delegates entirely to `execute_odata_query`, whose bind-time
path in opteryx-core already does permission-check-then-resolve in the right
order for `VERSION AS OF`. So this route just needs to split `dataset@label`
and pass `version`/`version_tag` straight through to `execute_odata_query`
(and from there, `ODataLogicalPlanBuilder`) -- see
app/odata/version_selector.py.
"""

import opteryx.exceptions
import pytest
from fastapi import FastAPI
from fastapi import Request
from fastapi.testclient import TestClient

from app.routes.v4 import interface
from app.routes.v4.interface import require_authentication
from app.routes.v4.interface import router


@pytest.fixture
def client():
    def fake_require_authentication(request: Request):
        claims = {"identity": "test-user"}
        request.state.claims = claims
        return claims

    app = FastAPI()
    app.dependency_overrides[require_authentication] = fake_require_authentication
    app.include_router(router)
    return TestClient(app)


def _fake_result():
    return {
        "value_bytes": b"[]",
        "row_count": 0,
        "col_names": [],
        "total_count": None,
        "has_more_rows": False,
        "took_ms": 0.0,
    }


def test_bare_dataset_passes_no_version_kwargs(client, monkeypatch):
    captured = {}

    def fake_execute_odata_query(*args, **kwargs):
        captured.update(kwargs)
        return _fake_result()

    monkeypatch.setattr(interface, "execute_odata_query", fake_execute_odata_query)

    response = client.get("/api/v4/public/demo/people")

    assert response.status_code == 200
    assert captured["table_name"] == "public.demo.people"
    assert captured.get("version") is None
    assert captured.get("version_tag") is None


def test_current_label_passes_version_tag_current(client, monkeypatch):
    captured = {}

    def fake_execute_odata_query(*args, **kwargs):
        captured.update(kwargs)
        return _fake_result()

    monkeypatch.setattr(interface, "execute_odata_query", fake_execute_odata_query)

    response = client.get("/api/v4/public/demo/people@current")

    assert response.status_code == 200
    assert captured["table_name"] == "public.demo.people"
    assert captured["version_tag"] == "current"
    assert captured.get("version") is None


def test_previous_label_passes_version_zero_sentinel(client, monkeypatch):
    captured = {}

    def fake_execute_odata_query(*args, **kwargs):
        captured.update(kwargs)
        return _fake_result()

    monkeypatch.setattr(interface, "execute_odata_query", fake_execute_odata_query)

    response = client.get("/api/v4/public/demo/people@previous")

    assert response.status_code == 200
    assert captured["version"] == 0
    assert captured.get("version_tag") is None


def test_numeric_label_passes_explicit_version(client, monkeypatch):
    captured = {}

    def fake_execute_odata_query(*args, **kwargs):
        captured.update(kwargs)
        return _fake_result()

    monkeypatch.setattr(interface, "execute_odata_query", fake_execute_odata_query)

    response = client.get("/api/v4/public/demo/people@1700000000000")

    assert response.status_code == 200
    assert captured["table_name"] == "public.demo.people"
    assert captured["version"] == 1700000000000
    assert captured.get("version_tag") is None


def test_tag_label_passes_version_tag(client, monkeypatch):
    captured = {}

    def fake_execute_odata_query(*args, **kwargs):
        captured.update(kwargs)
        return _fake_result()

    monkeypatch.setattr(interface, "execute_odata_query", fake_execute_odata_query)

    response = client.get("/api/v4/public/demo/people@release_v2")

    assert response.status_code == 200
    assert captured["table_name"] == "public.demo.people"
    assert captured["version_tag"] == "release_v2"


def test_malformed_label_400s_without_calling_execute_odata_query(client, monkeypatch):
    called = []

    def fake_execute_odata_query(*args, **kwargs):
        called.append(True)
        return _fake_result()

    monkeypatch.setattr(interface, "execute_odata_query", fake_execute_odata_query)

    response = client.get("/api/v4/public/demo/people@")

    assert response.status_code == 400
    assert called == []


def test_unknown_tag_maps_dataset_read_error_to_404(client, monkeypatch):
    def fake_execute_odata_query(*args, **kwargs):
        raise opteryx.exceptions.DatasetReadError("No tag bogus on public.demo.people.")

    monkeypatch.setattr(interface, "execute_odata_query", fake_execute_odata_query)

    response = client.get("/api/v4/public/demo/people@bogus")

    assert response.status_code == 404
    assert "no tag" in response.json()["detail"].lower()


def test_genuine_storage_failure_still_returns_503(client, monkeypatch):
    """Regression: a real manifest-read failure must keep its existing 503,
    not get swept into the new 404 mapping for version-not-found."""

    def fake_execute_odata_query(*args, **kwargs):
        raise opteryx.exceptions.DatasetReadError(
            "Failed to read manifest at gs://bucket/internal/path/manifest.parquet"
        )

    monkeypatch.setattr(interface, "execute_odata_query", fake_execute_odata_query)

    response = client.get("/api/v4/public/demo/people")

    assert response.status_code == 503
    assert "temporarily unavailable" in response.json()["detail"].lower()
