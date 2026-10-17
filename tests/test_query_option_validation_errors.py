"""A query option FastAPI cannot parse is an OData 400, not FastAPI's own 422.

`$top` and `$skip` are typed int, so `$top=abc` is refused during request
validation, before read_dataset runs. With no handler for RequestValidationError
FastAPI answered it itself: 422 and `{"detail": [...]}`, the one error on the v4
surface that was not in the `{"error": {"code", "message"}}` shape.
"""

import pytest
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

from app.routes.v4.errors import http_exception_handler
from app.routes.v4.errors import request_validation_exception_handler
from app.routes.v4.interface import require_authentication
from app.routes.v4.interface import router

_PATH = "/api/v4/public/geopolitics/countries"


@pytest.fixture
def client():
    def fake_require_authentication(request: Request):
        claims = {"identity": "test-user"}
        request.state.claims = claims
        return claims

    app = FastAPI()
    app.dependency_overrides[require_authentication] = fake_require_authentication
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, request_validation_exception_handler)
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("option", ["$top", "$skip"])
def test_a_non_integer_paging_option_is_an_odata_400(client, option):
    response = client.get(_PATH, params={option: "abc"})

    assert response.status_code == 400
    assert response.headers["OData-Version"] == "4.0"
    body = response.json()
    assert "detail" not in body
    assert body["error"]["code"] == "BadRequest"
    assert body["error"]["message"] == f"{option} must be an integer, not 'abc'"


def test_a_long_bad_value_is_not_echoed_in_full(client):
    response = client.get(_PATH, params={"$top": "x" * 500})

    assert response.status_code == 400
    assert len(response.json()["error"]["message"]) < 120


def test_the_service_registers_the_handler():
    from app.main import service

    assert service.exception_handlers[RequestValidationError] is request_validation_exception_handler
