# odata.opteryx Structure Setup - Complete

## Overview
Successfully initialized `odata.opteryx` as an OData endpoint service with the same structure as `jobs.opteryx`. The service includes authentication, deployment configuration, and middleware infrastructure. OData-specific functionality will be added next.

## Directory Structure

```
odata.opteryx/
├── .git/                          # Git repository
├── .gitignore                     # Git ignore rules
├── README.md                      # Service documentation
├── Dockerfile                     # Container build configuration
├── Makefile                       # Development tasks
├── pyproject.toml                 # Python project configuration
│
├── app/                           # Application code
│   ├── __init__.py
│   ├── main.py                    # FastAPI app initialization
│   ├── core.py                    # Core helpers (Firestore, service account)
│   ├── secret_store.py            # GCP Secret Manager integration
│   │
│   ├── auth/                      # Authentication module
│   │   ├── __init__.py
│   │   └── deps.py                # JWT verification (require_bearer_token)
│   │
│   ├── middleware/                # HTTP middleware
│   │   ├── __init__.py
│   │   ├── audit.py               # Request/response audit logging
│   │   └── cors.py                # CORS configuration
│   │
│   ├── models/                    # Pydantic data models (OData models go here)
│   │   └── __init__.py
│   │
│   └── routes/                    # API route handlers
│       ├── __init__.py
│       ├── health.py              # Health check endpoint
│       └── v1/                    # v1 API routes
│           ├── __init__.py
│           └── interface.py       # OData interface endpoints (ready for implementation)
│
├── cloudbuild/                    # Cloud Build configuration
│   ├── cloudbuild.yaml            # Cloud Build pipeline steps
│   └── build.sh                   # Build script
│
└── tests/                         # Test module
    └── __init__.py
```

## Key Components

### 1. **FastAPI Application** (`app/main.py`)
- Title: "Opteryx OData Service"
- Configured with Brotli compression middleware
- Audit logging middleware for all requests
- CORS middleware for cross-origin requests
- Routes registered from `app.routes`

### 2. **Authentication** (`app/auth/deps.py`)
- JWT bearer token validation
- Supports both local public key storage (Secret Manager) and JWKS fallback
- Verifies: signature, expiry (exp/nbf), issuer (iss), audience (aud)
- Supports Google OIDC tokens for Cloud Tasks
- Environment variables:
  - `AUTH_URL`: Auth service URL (default: https://authenticate.opteryx.app)
  - `JWT_ISSUER`: Expected token issuer
  - `DATA_AUDIENCE`: Expected token audience (default: opteryx)

### 3. **Secret Management** (`app/secret_store.py`)
- Abstraction for dev (filesystem) or GCP Secret Manager
- Functions:
  - `load_public_key(kid)`: Load public key by key ID
  - `load_key(kid)`: Load both private and public keys
  - `store_key(kid, priv_pem, pub_pem)`: Store key material
  - `list_known_kids()`: List all available key IDs
- Automatic GCP project detection from environment

### 4. **Middleware**
- **Audit** (`app/middleware/audit.py`): Logs all requests with status, duration, JWT subject
- **CORS** (`app/middleware/cors.py`): Restricts origins to specified list or allows all in development

### 5. **Routes**
- **Health** (`app/routes/health.py`): Simple health check endpoint
- **v1 API** (`app/routes/v1/interface.py`): OData endpoints placeholder (ready for implementation)

### 6. **Core Helpers** (`app/core.py`)
- `_get_firestore_client()`: Creates Firestore client for database operations
- `_runtime_service_account()`: Retrieves GCP service account email

### 7. **Deployment**
- **Docker** (`Dockerfile`): Multi-stage build with Python 3.13, non-root user 'norris'
- **Cloud Build** (`cloudbuild/cloudbuild.yaml`): Builds and pushes to Artifact Registry
- **Make** (`Makefile`): Local development tasks (install, lint, run)

## Configuration

### Environment Variables
- `AUTH_URL`: Authentication service URL
- `DATA_AUDIENCE`: JWT audience claim
- `JWT_ISSUER`: JWT issuer claim
- `GCP_PROJECT_ID`, `GCP_PROJECT`, `GOOGLE_CLOUD_PROJECT`: GCP project ID
- `CORS_ALLOW_ALL`: Set to "true" for development (allows all origins)

### Dependencies (from pyproject.toml)
- FastAPI 0.128.0
- Uvicorn 0.38.0
- Google Cloud Firestore, Secret Manager, Storage
- python-jose with cryptography
- orso (logging)
- opteryx-catalog, opteryx-core

## Local Development

```bash
# Install dependencies
make install

# Run development server (port 8890)
make run

# Run linting
make lint
```

## Next Steps

To add OData functionality:

1. **Define OData Data Models** in `app/models/`
   - Create Pydantic models for your OData entities
   
2. **Implement OData Routes** in `app/routes/v1/interface.py`
   - Query endpoints (GET /odata/...)
   - Create endpoints (POST /odata/...)
   - Update endpoints (PATCH/PUT /odata/...)
   - Delete endpoints (DELETE /odata/...)

3. **Add Query Processing Logic**
   - OData query parameter parsing and filtering
   - Sorting, pagination support
   - Relationships and expansions

4. **Integration with Data Sources**
   - Firestore queries
   - Catalog lookups
   - Query optimization

5. **Testing** in `tests/`
   - Unit tests for OData endpoints
   - Integration tests with Firestore
   - Authentication tests

## File Summary

| File | Purpose |
|------|---------|
| `app/main.py` | FastAPI app initialization |
| `app/core.py` | Shared helpers (Firestore, service account) |
| `app/secret_store.py` | Secret key management |
| `app/auth/deps.py` | JWT verification dependency |
| `app/middleware/audit.py` | Request audit logging |
| `app/middleware/cors.py` | CORS configuration |
| `app/routes/health.py` | Health check endpoint |
| `app/routes/v1/interface.py` | OData API endpoints |
| `Dockerfile` | Container image build |
| `Makefile` | Development tasks |
| `pyproject.toml` | Python dependencies |
| `cloudbuild/cloudbuild.yaml` | Cloud Build pipeline |
| `README.md` | Service documentation |
