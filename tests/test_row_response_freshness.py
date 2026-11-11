"""Row responses say when the data they came from was committed.

The per-dataset $metadata has always sent X-Dataset-LastModified, but it takes
about a second (it builds column statistics), and the website's "data updated
3 h ago" beside a live result was paying for one on every query. The binder
already resolved the snapshot to run the query, and the engine records it on
the telemetry (`read_commits`, beside the `read_sources` receipt), so the row
response carries the same headers for free -- for the version actually read.
"""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.routes.v4 import interface
from app.routes.v4.interface import _dataset_freshness, require_authentication, router

_PATH = "/api/v4/demo/security/exploit_db"
_TABLE = "demo.security.exploit_db"


@pytest.fixture
def client():
    def fake_require_authentication(request: Request):
        claims = {"identity": "test-user"}
        request.state.claims = claims
        return claims

    app = FastAPI()
    app.dependency_overrides[require_authentication] = fake_require_authentication
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


def _answer(monkeypatch, committed_at, snapshot_id):
    def fake_execute_odata_query(*args, **kwargs):
        return {
            "value_bytes": b'[{"a":1}]',
            "row_count": 1,
            "col_names": ["a"],
            "total_count": None,
            "has_more_rows": False,
            "took_ms": 1.0,
            "committed_at": committed_at,
            "snapshot_id": snapshot_id,
        }

    monkeypatch.setattr(interface, "execute_odata_query", fake_execute_odata_query)


def test_json_rows_carry_the_commit_time_and_snapshot(client, monkeypatch):
    _answer(monkeypatch, 1794280999462, 1794280999462)

    response = client.get(_PATH)

    assert response.status_code == 200
    assert response.headers["X-Dataset-LastModified"] == "1794280999462"
    assert response.headers["X-Dataset-Snapshot-Id"] == "1794280999462"


def test_csv_rows_carry_them_too(client, monkeypatch):
    _answer(monkeypatch, 1794280999462, 7)

    response = client.get(_PATH, params={"$format": "csv"})

    assert response.status_code == 200
    assert response.headers["X-Dataset-LastModified"] == "1794280999462"
    assert response.headers["X-Dataset-Snapshot-Id"] == "7"


def test_nothing_known_means_no_header_not_a_zero(client, monkeypatch):
    _answer(monkeypatch, None, None)

    response = client.get(_PATH)

    assert response.status_code == 200
    assert "X-Dataset-LastModified" not in response.headers
    assert "X-Dataset-Snapshot-Id" not in response.headers


# --- reading it off the telemetry -------------------------------------------


def _telemetry(**readings):
    return {"query_id": "q", "telemetry": SimpleNamespace(**readings)}


def test_the_dataset_queried_is_the_one_reported():
    reading = _telemetry(
        read_commits={_TABLE: 1794280999462, "demo.security.cisa_kev": 1},
        read_sources=[
            {"dataset": "demo.security.cisa_kev", "snapshot-id": 1, "resolved-by": "current"},
            {"dataset": _TABLE, "snapshot-id": 99, "resolved-by": "current"},
        ],
    )

    assert _dataset_freshness(reading, _TABLE) == (1794280999462, 99)


def test_a_view_reports_nothing():
    """A view's rows are read under its base tables' names; $metadata's header
    for a view is the definition's time, so the row response doesn't guess."""
    reading = _telemetry(
        read_commits={"demo.security.base": 5},
        read_sources=[{"dataset": "demo.security.base", "snapshot-id": 5}],
    )

    assert _dataset_freshness(reading, "demo.security.a_view") == (None, None)


def test_an_engine_without_read_commits_reports_nothing():
    """`_reading` is a defaultdict(int): a reading the engine never set is 0."""
    reading = _telemetry(read_commits=0, read_sources=0)

    assert _dataset_freshness(reading, _TABLE) == (None, None)


def test_no_execution_reports_nothing():
    assert _dataset_freshness(None, _TABLE) == (None, None)
