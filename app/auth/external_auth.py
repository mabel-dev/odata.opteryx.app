"""External authentication service client.

This module handles communication with the external authentication service
(authenticate.opteryx.app) to convert basic auth credentials into JWTs.
"""

import copy
import hashlib
import os
import secrets
import threading
import time
from collections import OrderedDict
from functools import lru_cache
from typing import Any
from typing import Dict
from typing import Optional
from typing import Tuple

import requests
from fastapi import HTTPException
from fastapi import status
from jose import JWTError
from jose import jwt as jose_jwt
from app.config_client import get_config
from app.logging import get_logger

logger = get_logger()

# Basic-auth clients (Excel, Power BI, ODBC drivers) resend credentials on every
# request, and every page of an OData result is a separate request -- so without
# a cache each one pays a full round trip to the auth service before the query
# even starts, which for a paged extract dominates the response time.
#
# Entries are keyed by a hash of the credentials salted with a per-process random
# value, never by the credentials themselves: the salt means the table is useless
# outside this process even if the heap is dumped. Entries expire at the earlier
# of the issued token's own `exp` and AUTH_CACHE_TTL, so a revoked or rotated
# credential stops being honoured promptly rather than for the process lifetime.
# Failures are never cached -- a user who fixes a bad password must not be locked
# out for the TTL, and a transient 503 must not be remembered as an outage.
_CACHE_MAX_ENTRIES = 256
_CACHE_SALT = secrets.token_bytes(16)
_cache_lock = threading.Lock()
_claims_cache: "OrderedDict[bytes, Tuple[float, Dict[str, Any]]]" = OrderedDict()


def _auth_url() -> str:
    return get_config("AUTH_URL", "https://authenticate.opteryx.app")


def _cache_ttl() -> float:
    """Seconds to cache a successful exchange. Set AUTH_CACHE_TTL=0 to disable."""
    try:
        return float(get_config("AUTH_CACHE_TTL", "300"))
    except ValueError:
        return 300.0


@lru_cache(maxsize=1)
def _http_session() -> requests.Session:
    """One pooled Session per process, so calls to the auth service reuse an
    established TLS connection instead of paying a fresh handshake each time.
    """
    session = requests.Session()
    adapter = requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=32)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def _cache_key(auth_url: str, username: str, password: str) -> bytes:
    digest = hashlib.blake2b(_CACHE_SALT, digest_size=32)
    for part in (auth_url, username, password):
        encoded = part.encode("utf-8")
        # Length-prefix each field so that ("ab", "c") and ("a", "bc") can't
        # collide onto the same key.
        digest.update(str(len(encoded)).encode("ascii"))
        digest.update(b":")
        digest.update(encoded)
    return digest.digest()


def _cache_get(key: bytes) -> Optional[Dict[str, Any]]:
    now = time.monotonic()
    with _cache_lock:
        entry = _claims_cache.get(key)
        if entry is None:
            return None
        expires_at, claims = entry
        if expires_at <= now:
            del _claims_cache[key]
            return None
        _claims_cache.move_to_end(key)
        # Hand back a copy: callers store these claims on request.state and
        # downstream code is free to mutate them.
        return copy.deepcopy(claims)


def _cache_put(key: bytes, claims: Dict[str, Any], ttl: float) -> None:
    if ttl <= 0:
        return
    with _cache_lock:
        _claims_cache[key] = (time.monotonic() + ttl, copy.deepcopy(claims))
        _claims_cache.move_to_end(key)
        while len(_claims_cache) > _CACHE_MAX_ENTRIES:
            _claims_cache.popitem(last=False)


def _cache_lifetime(claims: Dict[str, Any]) -> float:
    """How long these claims may be reused: the configured TTL, shortened when
    the token itself expires sooner so a cache hit can never outlive its token.
    """
    ttl = _cache_ttl()
    exp = claims.get("exp")
    if exp is not None:
        try:
            ttl = min(ttl, float(exp) - time.time() - 30.0)
        except (TypeError, ValueError):
            pass
    return ttl


def clear_credential_cache() -> None:
    """Drop every cached exchange. For tests and for credential-rotation hooks."""
    with _cache_lock:
        _claims_cache.clear()


def _request_token_claims(auth_url: str, username: str, password: str) -> Dict[str, Any]:
    """Exchange credentials with the auth service and return the JWT's claims.

    The uncached network call behind :func:`authenticate_with_external_service`;
    see that function for the contract.
    """
    token_endpoint = f"{auth_url.rstrip('/')}/token"
    timeout = float(os.environ.get("AUTH_SERVICE_TIMEOUT", "10"))

    # Prepare request data for OAuth2 client_credentials grant
    data = {
        "grant_type": "client_credentials",
        "client_id": username,
        "client_secret": password,
    }

    try:
        response = _http_session().post(
            token_endpoint,
            params={"set_cookie": "false"},  # Add query parameter
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=timeout,
        )

        # Handle authentication failures
        if response.status_code == 401:
            logger.info(f"Authentication failed for user '{username}': invalid credentials")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid username or password",
            )

        if response.status_code == 403:
            logger.info(f"Authentication forbidden for user '{username}'")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Access forbidden",
            )

        # Handle service errors
        if response.status_code >= 500:
            logger.error(
                f"Auth service returned server error: {response.status_code} - {response.text}"
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Authentication service unavailable",
            )

        # Handle other non-200 responses
        if response.status_code != 200:
            logger.warning(
                f"Auth service returned unexpected status {response.status_code}: {response.text}"
            )

            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Authentication service error",
            )

        # Parse response and extract access token
        response_data = response.json()
        access_token = response_data.get("access_token")

        if not access_token:
            logger.error(f"Auth service response missing access_token: {response_data}")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Invalid auth service response",
            )

        # Decode JWT to extract claims (no verification needed - already validated by auth service)
        try:
            claims = jose_jwt.get_unverified_claims(access_token)
        except JWTError as exc:
            logger.error(f"Failed to decode JWT from auth service: {exc}")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Invalid token from auth service",
            ) from exc

        # Extract identity from standard JWT claims
        identity = (
            claims.get("sub") or claims.get("email") or claims.get("preferred_username") or username
        )

        # Policies claim encodes what a user can access. Keep policies untouched
        # (do not convert into a separate "permissions" claim which is incorrect).
        policies = claims.get("policies", [])

        # Add identity to claims for easy access
        claims["identity"] = identity

        # This path does not go through _normalize_claims, so derive the billing
        # account here too — otherwise basic-auth callers bill to the house account.
        from app.auth.permissions import billing_account_from_claims

        claims["billing_account"] = billing_account_from_claims(claims)

        logger.info(
            f"Successfully authenticated user '{username}' (identity: {identity}) with {len(policies)} policies"
        )

        return claims

    except requests.Timeout as exc:
        logger.error(f"Auth service timeout after {timeout}s for user '{username}'")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Authentication service timeout after {timeout}s",
        ) from exc

    except requests.RequestException as exc:
        logger.error(f"Auth service connection error for user '{username}': {exc}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service unavailable",
        ) from exc


def authenticate_with_external_service(username: str, password: str) -> Dict[str, Any]:
    """Authenticate credentials with the external auth service and return JWT claims.

    Calls the authentication service at authenticate.opteryx.app using the OAuth2
    client_credentials grant. The service validates the credentials and returns a
    JWT access token containing user identity and permissions.

    Successful exchanges are cached briefly (see the module header) because
    basic-auth clients resend credentials on every request; failures are not.

    Args:
        username: The username (used as client_id)
        password: The password (used as client_secret)

    Returns:
        dict: JWT claims including:
            - identity (str): User identifier from sub/email/preferred_username
            - billing_account (str): Account the caller's usage is attributed to
            - Other standard JWT claims (exp, iss, aud, policies, etc.)

    Raises:
        HTTPException(401): Invalid credentials
        HTTPException(503): Auth service unavailable or timeout
    """
    auth_url = _auth_url()
    key = _cache_key(auth_url, username, password)

    cached = _cache_get(key)
    if cached is not None:
        return cached

    claims = _request_token_claims(auth_url, username, password)
    _cache_put(key, claims, _cache_lifetime(claims))
    return claims
