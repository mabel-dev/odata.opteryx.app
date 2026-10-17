"""A failed storage read says which read failed, and never claims more than it knows.

Every DatasetReadError that wasn't a version-selector miss used to be answered
"its stored metadata could not be read". opteryx raises that class for data-file
reads too: ClickBench Q20 failed on a parquet range read on every attempt and
was reported as a metadata failure, which sent the diagnosis to the wrong place.

opteryx now raises DatasetMetadataReadError and DatasetDataReadError (both
DatasetReadError) where it knows which half failed; every other read failure
still raises the base class and gets a message that claims neither.
"""

import opteryx.exceptions
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routes.v4 import interface
from app.routes.v4.interface import require_authentication, router

_PATH = "/api/v4/benchmarks/clickbench/hits"
_DATA_URI = "gs://opteryx_data/benchmarks/clickbench/hits/data/part-00042.parquet"


@pytest.fixture
def client():
    from fastapi import Request

    def fake_require_authentication(request: Request):
        claims = {"identity": "test-user"}
        request.state.claims = claims
        return claims

    app = FastAPI()
    app.dependency_overrides[require_authentication] = fake_require_authentication
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


def _raising(monkeypatch, exc):
    def fake_execute_odata_query(*args, **kwargs):
        raise exc

    monkeypatch.setattr(interface, "execute_odata_query", fake_execute_odata_query)


def test_failed_data_read_is_reported_as_a_data_file(client, monkeypatch):
    # The message the parquet pipeline raises (pool_reader.pyx).
    _raising(
        monkeypatch,
        opteryx.exceptions.DatasetDataReadError(
            "Unable to read data from storage, the read did not complete. "
            "The cause has been logged."
        ),
    )

    response = client.get(_PATH)

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert "a data file this query needed could not be read from storage" in detail
    assert "metadata" not in detail
    assert "manifest" not in detail


def test_failed_data_read_does_not_echo_the_storage_uri(client, monkeypatch):
    _raising(monkeypatch, opteryx.exceptions.DatasetDataReadError(f"Unable to read `{_DATA_URI}`."))

    body = client.get(_PATH).text

    assert "gs://" not in body
    assert "opteryx_data" not in body
    assert "part-00042" not in body


def test_unattributed_read_failure_claims_neither_half(client, monkeypatch):
    """Filesystem and connector paths still raise the base class; the response
    must not guess which half failed."""
    _raising(monkeypatch, opteryx.exceptions.DatasetReadError(f"Unable to read `{_DATA_URI}`."))

    response = client.get(_PATH)

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail.startswith("dataset could not be read from storage.")
    assert "metadata" not in detail
    assert "data file" not in detail
    assert "gs://" not in response.text


@pytest.mark.parametrize(
    "exc",
    [
        opteryx.exceptions.DatasetMetadataReadError("Cannot read information for dataset 'x': boom"),
        opteryx.exceptions.DatasetDataReadError("Unable to read data from storage"),
        opteryx.exceptions.DatasetReadError("Unable to list 'gs://bucket/x' - boom"),
    ],
)
def test_every_read_failure_tells_the_caller_what_to_do(client, monkeypatch, exc):
    _raising(monkeypatch, exc)

    detail = client.get(_PATH).json()["detail"]

    assert "not caused by the query" in detail
    assert "retry" in detail


def test_version_selector_miss_is_still_a_404(client, monkeypatch):
    """The base-class branch still peels off version-selector misses first."""
    _raising(monkeypatch, opteryx.exceptions.DatasetReadError("No tag bogus on benchmarks.clickbench.hits."))

    response = client.get(_PATH)

    assert response.status_code == 404


def test_unknown_snapshot_id_is_a_404_not_a_storage_failure(client, monkeypatch):
    """`@<snapshot id>` naming a snapshot that doesn't exist is the caller's miss,
    as `$metadata` already reports it - not a 503 storage failure."""
    _raising(
        monkeypatch,
        opteryx.exceptions.DatasetReadError(
            "No snapshot 1755000000000 for dataset benchmarks.clickbench.hits - "
            "it may not exist, or may have expired."
        ),
    )

    response = client.get(_PATH)

    assert response.status_code == 404
    assert "No snapshot 1755000000000" in response.json()["detail"]
