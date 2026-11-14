"""The platform maintenance switch gates every odata route but health and docs.

odata only reads, so `operational` and `read_only` serve and `reject` and
`queue` refuse with a 503. The mode is read on every request - never at import
or startup - so a change made between two requests is seen by the second.

The `platform_mode_store` fixture (conftest) backs the shared module with an
in-memory config document and no cache, so nothing here reaches Firestore.
Refused requests are refused before authentication, so the gated routes below
are exercised without credentials; allowed requests are aimed at a v4 path
that matches no route, which answers 404 without touching the catalog.
"""

import datetime
import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("GCP_PROJECT_ID", "test-project")
os.environ.setdefault("FIRESTORE_DATABASE", "test-db")
os.environ.setdefault("GCS_BUCKET", "test-bucket")

from opteryx_shared_services import platform_mode as pm

from app.main import service
from app.preflight import health_checks

client = TestClient(service)

# A v4 path no route matches: past the gate it is a plain 404.
UNROUTED_V4 = "/api/v4/no/such/route/here"

GATED = [
    "/api/v4/",
    "/api/v4/$metadata",
    "/api/v4/public/demo/users",
    "/api/v4/public/demo/users/$metadata",
    "/api/v4/public/demo/users?$top=1&$filter=id eq 1",
    UNROUTED_V4,
]

UNGATED = ["/health/live", "/docs", "/redoc", "/openapi.json"]


def _set_mode(store, mode, **fields):
    store.platform_mode = {"mode": mode, **fields}


@pytest.mark.parametrize("mode", [pm.OPERATIONAL, pm.READ_ONLY])
def test_operational_and_read_only_serve(platform_mode_store, mode):
    _set_mode(platform_mode_store, mode)
    response = client.get(UNROUTED_V4)
    assert response.status_code == 404
    assert "Retry-After" not in response.headers


def test_no_switch_set_serves(platform_mode_store):
    platform_mode_store.platform_mode = None
    assert client.get(UNROUTED_V4).status_code == 404


@pytest.mark.parametrize("mode", [pm.REJECT, pm.QUEUE])
@pytest.mark.parametrize("path", GATED)
def test_reject_and_queue_refuse_every_v4_route_in_odata_shape(platform_mode_store, mode, path):
    _set_mode(platform_mode_store, mode)
    response = client.get(path)
    assert response.status_code == 503
    assert response.headers["Retry-After"] == str(pm.DEFAULT_RETRY_AFTER_SECONDS)
    assert response.headers["OData-Version"] == "4.0"
    assert response.json() == {
        "error": {"code": "ServiceUnavailable", "message": pm.MESSAGES[mode]}
    }


@pytest.mark.parametrize("mode", [pm.REJECT, pm.QUEUE])
def test_internal_routes_are_refused_too(platform_mode_store, mode):
    _set_mode(platform_mode_store, mode)
    response = client.get("/internal/clickbench/run")
    assert response.status_code == 503
    assert response.headers["Retry-After"] == str(pm.DEFAULT_RETRY_AFTER_SECONDS)
    assert response.json() == {"detail": pm.MESSAGES[mode]}


def test_refusal_carries_reason_and_retry_after_from_until(platform_mode_store):
    until = datetime.datetime.now(tz=datetime.timezone.utc) + datetime.timedelta(minutes=30)
    _set_mode(platform_mode_store, pm.REJECT, reason="catalog migration", until=until)
    response = client.get("/api/v4/")
    assert response.status_code == 503
    assert 1700 < int(response.headers["Retry-After"]) <= 1800
    assert response.json()["error"]["message"] == (
        f"{pm.MESSAGES[pm.REJECT]} (catalog migration)"
    )


@pytest.mark.parametrize("mode", [pm.REJECT, pm.QUEUE])
@pytest.mark.parametrize("path", UNGATED)
def test_health_and_docs_stay_open(platform_mode_store, mode, path):
    _set_mode(platform_mode_store, mode)
    reads = platform_mode_store.reads
    response = client.get(path)
    assert response.status_code == 200
    assert platform_mode_store.reads == reads, "an ungated route consulted the mode"


@pytest.mark.parametrize("mode", [pm.REJECT, pm.QUEUE])
def test_readiness_stays_open(platform_mode_store, monkeypatch, mode):
    monkeypatch.setattr(health_checks, "readiness", lambda: (True, {"status": "ok"}))
    _set_mode(platform_mode_store, mode)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_the_mode_is_read_on_every_request(platform_mode_store):
    _set_mode(platform_mode_store, pm.OPERATIONAL)
    assert client.get(UNROUTED_V4).status_code == 404

    _set_mode(platform_mode_store, pm.REJECT)
    assert client.get(UNROUTED_V4).status_code == 503

    _set_mode(platform_mode_store, pm.READ_ONLY)
    assert client.get(UNROUTED_V4).status_code == 404
    assert platform_mode_store.reads == 3


def test_the_gate_asks_as_odata_reading(monkeypatch):
    calls = []

    def check(service_name, operation):
        calls.append((service_name, operation))
        return None

    monkeypatch.setattr(pm, "check", check)
    client.get(UNROUTED_V4)
    assert calls == [(pm.ODATA, pm.READ)]


def test_break_glass_environment_override(platform_mode_store, monkeypatch):
    platform_mode_store.platform_mode = None
    monkeypatch.setenv(pm.ENV_OVERRIDE, pm.REJECT)
    assert client.get(UNROUTED_V4).status_code == 503
    monkeypatch.delenv(pm.ENV_OVERRIDE)
    assert client.get(UNROUTED_V4).status_code == 404
