"""Tests for the CORS allowlist.

CORS is configured on the *app* by `configure_cors()`, not on the router, so
none of the router-level tests exercise it. A missing origin here is invisible
until a browser on that origin gets no Access-Control-Allow-Origin header back
-- which is exactly how the docs site's "Try it" panel broke.

`allow_credentials=True` is set on the middleware, so the allowlist must stay an
explicit list of origins: browsers reject `*` in combination with credentials.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.middleware.cors import CORS_ORIGINS, configure_cors, get_cors_origins


@pytest.fixture
def client():
    """A minimal app carrying only the CORS middleware."""
    app = FastAPI()
    configure_cors(app)

    @app.get("/probe")
    def probe():
        return {"ok": True}

    return TestClient(app)


def preflight(client, origin, method="GET"):
    return client.options(
        "/probe",
        headers={"Origin": origin, "Access-Control-Request-Method": method},
    )


@pytest.mark.parametrize("origin", CORS_ORIGINS)
def test_every_allowlisted_origin_is_echoed_back(client, origin):
    """Each configured origin must get its own value back, not `*`."""
    response = preflight(client, origin)
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin


def test_docs_site_origin_is_allowed(client):
    """The docs site's Try-it panel calls this service from the browser."""
    response = preflight(client, "https://docs.opteryx.app")
    assert response.headers.get("access-control-allow-origin") == "https://docs.opteryx.app"


def test_docs_site_dev_server_origins_are_allowed(client):
    """`npm run dev` in docs-site/ serves on port 3000, not 8000."""
    for origin in ("http://localhost:3000", "http://127.0.0.1:3000"):
        response = preflight(client, origin)
        assert response.headers.get("access-control-allow-origin") == origin, origin


def test_unknown_origin_is_not_allowed(client):
    """An origin outside the allowlist must not receive an allow header."""
    response = preflight(client, "https://not-opteryx.example.com")
    assert response.headers.get("access-control-allow-origin") is None


def test_credentialed_responses_never_use_a_wildcard(client):
    """`*` plus credentials is rejected by browsers, so it must never be emitted."""
    response = preflight(client, "https://docs.opteryx.app")
    assert response.headers.get("access-control-allow-origin") != "*"
    assert response.headers.get("access-control-allow-credentials") == "true"


def test_allowlist_is_explicit_by_default():
    """Without CORS_ALLOW_ALL, the origin list must be explicit rather than `*`."""
    origins = get_cors_origins()
    assert origins != ["*"]
    assert all(o != "*" for o in origins)


def test_cors_allow_all_is_opt_in(monkeypatch):
    """The wildcard escape hatch stays available for local development only."""
    monkeypatch.setenv("CORS_ALLOW_ALL", "true")
    assert get_cors_origins() == ["*"]


def test_etag_is_exposed_to_browser_clients(client):
    """A cross-origin caller can only read headers named in expose-headers.

    Without ETag listed, `response.headers.get("ETag")` reads as null in the
    browser and a client can never revalidate a cached $metadata document -- the
    conditional-request path silently degrades to always refetching.
    """
    response = client.get("/probe", headers={"Origin": "https://opteryx.app"})
    exposed = {
        h.strip().lower()
        for h in response.headers.get("access-control-expose-headers", "").split(",")
    }
    assert "etag" in exposed


def test_dataset_headers_are_exposed_to_browser_clients(client):
    response = client.get("/probe", headers={"Origin": "https://opteryx.app"})
    exposed = {
        h.strip().lower()
        for h in response.headers.get("access-control-expose-headers", "").split(",")
    }
    assert {"x-dataset-source", "x-dataset-lastmodified", "odata-version"} <= exposed


def test_if_none_match_survives_preflight(client):
    """If-None-Match is not CORS-safelisted, so it must be explicitly allowed."""
    response = client.options(
        "/probe",
        headers={
            "Origin": "https://opteryx.app",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization,if-none-match",
        },
    )
    assert response.status_code == 200
    allowed = {
        h.strip().lower()
        for h in response.headers.get("access-control-allow-headers", "").split(",")
    }
    assert "if-none-match" in allowed
