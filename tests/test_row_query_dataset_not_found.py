"""Regression test: unknown-dataset errors on the row-query endpoint must
return 404, matching the $metadata endpoint, instead of 400.

opteryx.exceptions.DatasetNotFoundError is (surprisingly) a subclass of
SqlError, so without a special case it gets caught by the generic
`except (SqlError, IncompatibleTypesError)` block in the row-query endpoint
and reported as "Invalid query" / 400 -- indistinguishable from a malformed
filter. See app/routes/v4/interface.py around the execute_odata_query() call.
"""

import opteryx.exceptions
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routes.v4 import interface
from app.routes.v4.interface import require_authentication, router


@pytest.fixture
def client():
    """Create a test client with an authenticated identity injected.

    Unlike the plain `lambda: None` override used elsewhere, this stamps
    request.state.claims (as the real require_authentication dependency
    does) so the endpoint's own identity check at interface.py:681 passes
    and execution reaches execute_odata_query() -- the thing under test.
    """
    from fastapi import Request

    def fake_require_authentication(request: Request):
        claims = {"identity": "test-user"}
        request.state.claims = claims
        return claims

    app = FastAPI()
    app.dependency_overrides[require_authentication] = fake_require_authentication
    app.include_router(router)
    return TestClient(app)


def test_unknown_dataset_returns_404(client, monkeypatch):
    """DatasetNotFoundError from query execution should surface as 404."""

    def fake_execute_odata_query(*args, **kwargs):
        raise opteryx.exceptions.DatasetNotFoundError("missing_dataset")

    monkeypatch.setattr(interface, "execute_odata_query", fake_execute_odata_query)

    response = client.get("/api/v4/public/demo/missing_dataset")

    assert response.status_code == 404
    data = response.json()
    assert "not found" in data["detail"].lower()


def test_other_sql_errors_still_return_400(client, monkeypatch):
    """Non-DatasetNotFoundError SqlErrors should still map to 400 (unchanged)."""

    def fake_execute_odata_query(*args, **kwargs):
        raise opteryx.exceptions.UnsupportedSyntaxError("bad filter syntax")

    monkeypatch.setattr(interface, "execute_odata_query", fake_execute_odata_query)

    response = client.get("/api/v4/public/demo/users?$filter=broken")

    assert response.status_code == 400
    data = response.json()
    assert "invalid query" in data["detail"].lower()
