"""Entity reads billed to a suspended or capped account are refused with 402.

The rule is control.opteryx's, shared as `opteryx_shared_services.billing.block`;
the parametrised cases are the ones control pins.
"""

import datetime

import pytest
from fastapi import FastAPI
from fastapi import Request
from fastapi.testclient import TestClient

from opteryx_shared_services.billing import accounts as billing_accounts
from opteryx_shared_services.billing import block as billing_block
from app.routes.v4 import interface
from app.routes.v4.interface import require_authentication
from app.routes.v4.interface import router

NOV = datetime.datetime(2026, 11, 9, 12, tzinfo=datetime.timezone.utc)


@pytest.mark.parametrize(
    "account_id, account, expected",
    [
        (None, None, None),
        ("acct", None, None),
        ("acct", {"billing_status": "past_due"}, None),
        ("acct", {"billing_status": "suspended"}, "payment_failed"),
        ("acct", {"cap_reached_period": "202611"}, "spend_cap_reached"),
        ("acct", {"cap_reached_period": "202610"}, None),
        ("opteryx", {"billing_status": "suspended"}, None),
    ],
)
def test_rule_matches_control(account_id, account, expected):
    assert billing_block.billing_block_reason(account_id, account, now=NOV) == expected


class _Snap:
    def __init__(self, data):
        self._data, self.exists = data, data is not None

    def to_dict(self):
        return self._data


class _Db:
    def __init__(self, accounts):
        self.accounts = accounts

    def collection(self, name):
        assert name == "billing_accounts"
        return self

    def document(self, account_id):
        return _Doc(self.accounts.get(account_id))


class _Doc:
    def __init__(self, data):
        self._data = data

    def get(self):
        return _Snap(self._data)


@pytest.fixture
def accounts(monkeypatch):
    billing_accounts.clear_cache()
    data = {}
    ran = []

    def fake_execute(*args, **kwargs):
        ran.append(kwargs.get("table_name"))
        raise RuntimeError("stop here - only whether the query was reached matters")

    monkeypatch.setattr("app.core._get_firestore_client", lambda *a, **k: _Db(data))
    monkeypatch.setattr(interface, "execute_odata_query", fake_execute)
    data["_ran"] = ran
    yield data
    billing_accounts.clear_cache()


@pytest.fixture
def client():
    def fake_require_authentication(request: Request):
        claims = {"identity": "alice", "billing_account": "acct_1"}
        request.state.claims = claims
        return claims

    from starlette.exceptions import HTTPException as StarletteHTTPException

    from app.routes.v4.errors import http_exception_handler

    app = FastAPI()
    app.dependency_overrides[require_authentication] = fake_require_authentication
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


def test_a_suspended_account_is_refused(client, accounts):
    accounts["acct_1"] = {"billing_status": "suspended"}

    response = client.get("/api/v4/sales/orders/lines")

    assert response.status_code == 402
    assert response.json()["error"]["code"] == "PaymentRequired"
    assert "invoice could not be paid" in response.text
    assert accounts["_ran"] == []


def test_an_account_at_its_cap_is_refused(client, accounts):
    accounts["acct_1"] = {"cap_reached_period": billing_block.billing_period()}

    response = client.get("/api/v4/sales/orders/lines")

    assert response.status_code == 402
    assert "spending cap" in response.text
    assert accounts["_ran"] == []


def test_a_blocked_account_can_still_read_its_own_billing(client, accounts):
    accounts["acct_1"] = {"billing_status": "suspended"}

    client.get("/api/v4/platform/billing/charges_daily")

    assert accounts["_ran"] == ["platform.billing.charges_daily"]


def test_an_account_in_good_standing_reads(client, accounts):
    accounts["acct_1"] = {"billing_status": "active"}

    client.get("/api/v4/sales/orders/lines")

    assert accounts["_ran"] == ["sales.orders.lines"]
