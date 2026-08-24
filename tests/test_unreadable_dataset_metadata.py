"""A dataset whose stored metadata cannot be read is a 503, and the response
says nothing about where that metadata lives.

opteryx wraps any failure to read a dataset's metadata during binding in
DatasetReadError, embedding the storage URI it failed on. Untreated, that
reaches the generic exception handler and the URI -- bucket name and internal
metadata layout -- is echoed to the caller inside a 500. The dataset is
registered and the query is well-formed, so neither 404 nor 400 describes it.
"""

import opteryx.exceptions
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routes.v4 import interface
from app.routes.v4.interface import require_authentication, router

_URI = "gs://opteryx_data/public/security/nvd_vulnerabilities/metadata/manifest-1787609483816.parquet"
_ENGINE_MESSAGE = f"Cannot read information for dataset 'public.security.nvd_vulnerabilities': {_URI}"


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


def test_unreadable_metadata_returns_503(client, monkeypatch):
    def fake_execute_odata_query(*args, **kwargs):
        raise opteryx.exceptions.DatasetReadError(_ENGINE_MESSAGE)

    monkeypatch.setattr(interface, "execute_odata_query", fake_execute_odata_query)

    response = client.get("/api/v4/public/security/nvd_vulnerabilities")

    assert response.status_code == 503
    assert "unavailable" in response.json()["detail"].lower()


def test_the_storage_uri_does_not_reach_the_caller(client, monkeypatch):
    def fake_execute_odata_query(*args, **kwargs):
        raise opteryx.exceptions.DatasetReadError(_ENGINE_MESSAGE)

    monkeypatch.setattr(interface, "execute_odata_query", fake_execute_odata_query)

    response = client.get("/api/v4/public/security/nvd_vulnerabilities")

    body = response.text
    assert "gs://" not in body
    assert "opteryx_data" not in body
    assert "manifest-" not in body


def test_generic_handler_does_not_echo_exception_text():
    """The backstop: anything that reaches the generic handler is a message
    nobody wrote for external eyes, so none of it is returned."""
    import asyncio

    from starlette.requests import Request

    from app.routes.v4.errors import generic_exception_handler

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/v4/public/security/nvd_vulnerabilities",
        "headers": [],
        "query_string": b"",
    }
    response = asyncio.run(
        generic_exception_handler(Request(scope), RuntimeError(_ENGINE_MESSAGE))
    )

    body = response.body.decode()
    assert response.status_code == 500
    assert "gs://" not in body
    assert "nvd_vulnerabilities" not in body
    assert "InternalServerError" in body
