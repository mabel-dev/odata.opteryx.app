# OData Service

Purpose
- OData API endpoint (Opteryx) that validates incoming JWTs against the Auth service JWKS.

Local run
- `make run` starts this service on port `8890`.
- Or from repo root:
  - `uvicorn app.main:service --reload --host 0.0.0.0 --port 8890`

Build (Docker / Cloud Build)
- Built by Cloud Build using `odata/pyproject.toml` and `odata/Dockerfile`.
- To build locally from repo root:
  - `docker build -f odata.opteryx/Dockerfile -t gcr.io/$PROJECT_ID/opteryx-odata .`

Auth verification
- Uses `odata/app/auth/deps.py` to verify incoming JWTs. Verification first attempts to use the local `odata/app/secret_store` (`kid` key lookup), and falls back to fetching JWKS from `AUTH_URL` by `kid` header if a local key cannot be found.
- Checks signature, expiry (`exp` and `nbf`), and issuer (`iss`). The expected audience is `DATA_AUDIENCE` (defaults to `opteryx-api`).

Env
- `AUTH_URL` — URL of the auth service (default `http://localhost:8081` when running locally).
- `DATA_AUDIENCE` — expected `aud` claim for tokens (defaults to `opteryx-api`).

---

## API Overview

OData endpoint for querying and accessing Opteryx data through OData protocol.