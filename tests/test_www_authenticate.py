"""Every 401 carries a WWW-Authenticate challenge.

RFC 9110 requires one on a 401. Without it a browser never prompts for
credentials, and Power Query (Power BI, Excel) has no scheme to suggest in its
credentials dialog - the clients the Basic-auth path exists for.
"""

import os

import pytest
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.testclient import TestClient

os.environ["GCP_PROJECT_ID"] = "test-project"
os.environ["FIRESTORE_DATABASE"] = "test-db"
os.environ["GCS_BUCKET"] = "test-bucket"

from app.main import service
from app.middleware.cors import configure_cors
from app.routes.v4.errors import WWW_AUTHENTICATE
from app.routes.v4.errors import ODataError
from app.routes.v4.errors import http_exception_handler
from app.routes.v4.errors import odata_error_handler

client = TestClient(service)


def _assert_challenge(response):
    assert response.status_code == 401
    challenge = response.headers.get("WWW-Authenticate")
    assert challenge == WWW_AUTHENTICATE
    assert challenge.startswith("Basic ")
    assert "Bearer" in challenge


def test_challenge_offers_basic_and_bearer():
    assert WWW_AUTHENTICATE.startswith('Basic realm="opteryx"')
    assert 'Bearer realm="opteryx"' in WWW_AUTHENTICATE


def test_rejected_basic_credentials_carry_the_challenge(monkeypatch):
    def _reject(username, password):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    monkeypatch.setattr("app.auth.deps.authenticate_with_external_service", _reject)

    response = client.get("/api/v4/", auth=("someone", "wrong"))
    _assert_challenge(response)
    assert response.json()["error"]["code"] == "Unauthorized"
    assert response.headers.get("OData-Version") == "4.0"


def test_malformed_basic_credentials_carry_the_challenge():
    response = client.get("/api/v4/", headers={"Authorization": "Basic not-base64!"})
    _assert_challenge(response)


def test_unknown_scheme_carries_the_challenge():
    response = client.get("/api/v4/", headers={"Authorization": "Digest abc"})
    _assert_challenge(response)


def test_anonymous_read_of_a_private_dataset_carries_the_challenge():
    response = client.get("/api/v4/acme/security/findings")
    _assert_challenge(response)


@pytest.fixture
def probe_client():
    """A minimal app with both error handlers, to reach the paths routes rarely hit."""
    app = FastAPI()
    app.add_exception_handler(ODataError, odata_error_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)

    @app.get("/api/v4/odata-401")
    def odata_401():
        raise ODataError("Unauthorized", "no", status_code=401)

    @app.get("/api/v4/odata-403")
    def odata_403():
        raise ODataError("Forbidden", "no", status_code=403)

    @app.get("/other-401")
    def other_401():
        raise HTTPException(status_code=401, detail="no")

    return TestClient(app)


def test_odata_error_401_carries_the_challenge(probe_client):
    _assert_challenge(probe_client.get("/api/v4/odata-401"))


def test_non_v4_401_carries_the_challenge(probe_client):
    _assert_challenge(probe_client.get("/other-401"))


def test_other_statuses_do_not_carry_the_challenge(probe_client):
    response = probe_client.get("/api/v4/odata-403")
    assert response.status_code == 403
    assert "WWW-Authenticate" not in response.headers


def test_challenge_is_exposed_to_browser_clients():
    app = FastAPI()
    configure_cors(app)

    @app.get("/probe")
    def probe():
        raise HTTPException(status_code=401, detail="no")

    app.add_exception_handler(HTTPException, http_exception_handler)
    response = TestClient(app).get("/probe", headers={"Origin": "https://docs.opteryx.app"})
    exposed = response.headers.get("access-control-expose-headers", "")
    assert "WWW-Authenticate" in exposed
