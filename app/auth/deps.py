import os
from typing import Any
from typing import Dict
from typing import Optional

import requests
from fastapi import Header
from fastapi import HTTPException
from fastapi import status
from google.api_core import exceptions as gcp_exceptions
from google.auth.transport import requests as google_auth_requests
from google.oauth2 import id_token as google_id_token
from jose import JWTError
from jose import jwt as jose_jwt
from orso.logging import get_logger

from app.secret_store import load_public_key


def _fetch_jwks(auth_url: str, timeout: float = 2.0):
    """Fetch JWKS from the auth server using optional discovery.
    Falls back to `/.well-known/jwks.json`. Returns dict or None.
    """
    try:
        discovery = requests.get(
            f"{auth_url.rstrip('/')}/.well-known/openid-configuration", timeout=timeout
        )
        if discovery.status_code == 200:
            jwks_uri = discovery.json().get("jwks_uri")
            if jwks_uri:
                r = requests.get(jwks_uri, timeout=timeout)
                if r.status_code == 200:
                    return r.json()
    except requests.RequestException:
        pass
    try:
        r = requests.get(f"{auth_url.rstrip('/')}/.well-known/jwks.json", timeout=timeout)
        if r.status_code == 200:
            return r.json()
    except requests.RequestException:
        pass
    return None


logger = get_logger()


async def require_bearer_token(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    """Require and verify a bearer JWT.

    This function verifies the JWT's signature, ensures the token is within its
    validity window and checks the `iss` claim against a configured authority.
    The verification uses `app.secret_store.load_key(kid)` first (dev-friendly),
    and falls back to fetching JWKS from `AUTH_URL` when needed.
    Returns the decoded claims on success.
    """
    if not (authorization and authorization.lower().startswith("bearer ")):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    token = authorization.split(" ", 1)[1]

    # Resolve expected values from environment
    auth_url = os.environ.get("AUTH_URL", "https://authenticate.opteryx.app")
    issuer = os.environ.get("JWT_ISSUER", auth_url)
    audience = os.environ.get("DATA_AUDIENCE", "opteryx")

    try:
        headers = jose_jwt.get_unverified_header(token)
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token header"
        ) from exc
    alg = headers.get("alg")
    kid = headers.get("kid")

    # Check issuer/audience in the token claims (unverified) first
    try:
        claims_unverified = jose_jwt.get_unverified_claims(token)
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token claims"
        ) from exc
    iss = claims_unverified.get("iss")
    aud = claims_unverified.get("aud")
    # Accept either the configured issuer (users/auth) OR Google OIDC issuer
    google_issuers = ("https://accounts.google.com", "accounts.google.com")
    if iss in google_issuers:
        # defer audience validation to Google's verification below (TASKS_OIDC_AUDIENCE)
        pass
    else:
        if iss != issuer or aud != audience:
            logger.info(
                "token claims mismatch; iss=%s aud=%s expected_iss=%s expected_aud=%s",
                iss,
                aud,
                issuer,
                audience,
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token claims"
            )

    # Best-effort: try fetching the public key via the secret store first
    try:
        # First attempt strict date-based public load
        pub_key = load_public_key(kid)
    except (
        RuntimeError,
        gcp_exceptions.GoogleAPIError,
    ) as exc:  # pragma: no cover - best-effort fallback
        logger.debug("load_public_key raised, attempting find_key_by_kid: %s", exc)
        pub_key = None

    options = {"require_exp": True, "require_iss": True, "verify_nbf": True, "verify_iat": True}

    # If we got a public key from the secret store, use it directly
    if pub_key:
        try:
            claims = jose_jwt.decode(
                token,
                pub_key,
                algorithms=[alg] if alg else None,
                options=options,
                issuer=issuer,
                audience=audience,
            )
            return claims
        except JWTError as exc:
            logger.info("Token verification failed with secret store key: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
            ) from exc

    # Otherwise, attempt JWKS fallback
    jwks = _fetch_jwks(auth_url)
    if jwks:
        try:
            claims = jose_jwt.decode(
                token,
                jwks,
                algorithms=[alg] if alg else None,
                options=options,
                issuer=issuer,
                audience=audience,
            )
            return claims
        except JWTError as exc:
            logger.info("Token verification failed using remote JWKS: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
            ) from exc
    # If we reach here, JWT signature verification via local key or JWKS failed.
    # Fall through to try Google OIDC validation below if available.

    # Accept Google-signed OIDC tokens (used by Cloud Tasks OIDC) if library is available
    if not pub_key and iss in ("https://accounts.google.com", "accounts.google.com"):
        # Use google_auth library to verify token and audience and issuer
        expected_aud = os.environ.get("TASKS_OIDC_AUDIENCE")
        try:
            google_claims = google_id_token.verify_oauth2_token(
                token, google_auth_requests.Request(), audience=expected_aud
            )
        except Exception as exc:  # pragma: no cover - library dependent
            logger.info("Google token verification failed: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Google ID token"
            ) from exc

        # Optionally restrict to invoker SA
        invoker_sa = os.environ.get("TASKS_OIDC_SA")
        if invoker_sa and google_claims.get("email") != invoker_sa:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden caller")
        # Accept Google claims as valid
        return google_claims
