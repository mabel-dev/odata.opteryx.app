"""Tests for the basic-auth credential exchange cache.

Basic-auth clients resend credentials on every request, so a successful exchange
is cached briefly rather than costing a round trip to the auth service per page.
"""

import time

import pytest
from fastapi import HTTPException

from app.auth import external_auth


@pytest.fixture(autouse=True)
def _clean_cache(monkeypatch):
    monkeypatch.setenv("AUTH_CACHE_TTL", "300")
    external_auth.clear_credential_cache()
    yield
    external_auth.clear_credential_cache()


def _count_calls(monkeypatch, claims=None, exc=None):
    """Stub the network exchange, returning a mutable call counter."""
    calls = []

    def _fake(auth_url, username, password):
        calls.append((auth_url, username, password))
        if exc is not None:
            raise exc
        return dict(claims or {"sub": "alice", "identity": "alice"})

    monkeypatch.setattr(external_auth, "_request_token_claims", _fake)
    return calls


def test_repeat_credentials_hit_the_service_once(monkeypatch):
    calls = _count_calls(monkeypatch)

    first = external_auth.authenticate_with_external_service("alice", "pw")
    second = external_auth.authenticate_with_external_service("alice", "pw")

    assert len(calls) == 1
    assert first == second == {"sub": "alice", "identity": "alice"}


def test_different_credentials_are_cached_separately(monkeypatch):
    calls = _count_calls(monkeypatch)

    external_auth.authenticate_with_external_service("alice", "pw")
    external_auth.authenticate_with_external_service("bob", "pw")
    external_auth.authenticate_with_external_service("alice", "different")

    assert len(calls) == 3


def test_username_password_boundary_is_unambiguous(monkeypatch):
    """("ab", "c") and ("a", "bc") must not collide onto one cache entry."""
    calls = _count_calls(monkeypatch)

    external_auth.authenticate_with_external_service("ab", "c")
    external_auth.authenticate_with_external_service("a", "bc")

    assert len(calls) == 2


def test_failures_are_not_cached(monkeypatch):
    """A user who fixes a bad password must not stay locked out for the TTL."""
    calls = _count_calls(monkeypatch, exc=HTTPException(status_code=401, detail="nope"))

    for _ in range(3):
        with pytest.raises(HTTPException):
            external_auth.authenticate_with_external_service("alice", "wrong")

    assert len(calls) == 3


def test_cached_claims_are_isolated_from_caller_mutation(monkeypatch):
    """Callers put these on request.state; mutating them must not poison the cache."""
    _count_calls(monkeypatch, claims={"sub": "alice", "policies": [{"role": "reader"}]})

    first = external_auth.authenticate_with_external_service("alice", "pw")
    first["sub"] = "mallory"
    first["policies"][0]["role"] = "admin"

    second = external_auth.authenticate_with_external_service("alice", "pw")
    assert second["sub"] == "alice"
    assert second["policies"][0]["role"] == "reader"


def test_ttl_zero_disables_caching(monkeypatch):
    monkeypatch.setenv("AUTH_CACHE_TTL", "0")
    calls = _count_calls(monkeypatch)

    external_auth.authenticate_with_external_service("alice", "pw")
    external_auth.authenticate_with_external_service("alice", "pw")

    assert len(calls) == 2


def test_entry_expires_after_ttl(monkeypatch):
    monkeypatch.setenv("AUTH_CACHE_TTL", "60")
    calls = _count_calls(monkeypatch)

    external_auth.authenticate_with_external_service("alice", "pw")

    clock = time.monotonic() + 61
    monkeypatch.setattr(external_auth.time, "monotonic", lambda: clock)
    external_auth.authenticate_with_external_service("alice", "pw")

    assert len(calls) == 2


def test_token_expiry_shortens_the_ttl(monkeypatch):
    """A token expiring sooner than the TTL must not be served past its own exp."""
    monkeypatch.setenv("AUTH_CACHE_TTL", "300")
    # exp is 40s away; minus the 30s safety margin, this is cacheable for ~10s.
    claims = {"sub": "alice", "exp": time.time() + 40}
    calls = _count_calls(monkeypatch, claims=claims)

    external_auth.authenticate_with_external_service("alice", "pw")

    clock = time.monotonic() + 15
    monkeypatch.setattr(external_auth.time, "monotonic", lambda: clock)
    external_auth.authenticate_with_external_service("alice", "pw")

    assert len(calls) == 2


def test_already_expired_token_is_not_cached(monkeypatch):
    claims = {"sub": "alice", "exp": time.time() - 1}
    calls = _count_calls(monkeypatch, claims=claims)

    external_auth.authenticate_with_external_service("alice", "pw")
    external_auth.authenticate_with_external_service("alice", "pw")

    assert len(calls) == 2


def test_cache_is_bounded(monkeypatch):
    calls = _count_calls(monkeypatch)

    for i in range(external_auth._CACHE_MAX_ENTRIES + 50):
        external_auth.authenticate_with_external_service(f"user{i}", "pw")

    assert len(external_auth._claims_cache) == external_auth._CACHE_MAX_ENTRIES
    # The oldest entries were evicted, so the first user must re-exchange.
    before = len(calls)
    external_auth.authenticate_with_external_service("user0", "pw")
    assert len(calls) == before + 1
