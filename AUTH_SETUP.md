# Authentication Setup: Supporting Bearer Tokens and Basic Auth

This guide explains how to support both bearer token (JWT) and basic authentication on the same endpoints.

## Implementation Summary

The authentication system now supports two methods:

1. **Bearer Token (JWT)**: For token-based authentication
2. **Basic Authentication**: For username/password authentication

## Updated Dependencies

A new authentication function `require_authentication` has been added to [app/auth/deps.py](app/auth/deps.py) that supports both auth methods.

### Available Functions

- `require_bearer_token()` - Validates JWT bearer tokens only
- `require_authentication()` - **NEW**: Supports both bearer tokens and basic auth
- `_verify_basic_auth()` - Internal function for validating basic credentials

## Usage

### Option 1: Both Methods on Same Endpoint

Replace `require_bearer_token` with `require_authentication`:

```python
from fastapi import APIRouter, Depends
from app.auth.deps import require_authentication

router = APIRouter()

@router.get("/data")
async def get_data(claims: dict = Depends(require_authentication)):
    """This endpoint accepts both bearer tokens and basic auth"""
    username = claims.get("sub")  # Works for both auth methods
    auth_method = claims.get("auth_method", "bearer")
    return {"message": f"Hello {username} via {auth_method}"}
```

### Option 2: Bearer Token Only (Legacy)

Keep using the existing function:

```python
from app.auth.deps import require_bearer_token

@router.get("/data")
async def get_data(claims: dict = Depends(require_bearer_token)):
    """This endpoint accepts bearer tokens only"""
    return {"user": claims.get("sub")}
```

## Client Usage Examples

### Using Bearer Token

```bash
curl -H "Authorization: Bearer eyJhbGc..." https://your-api.com/data
```

### Using Basic Authentication

```bash
# Username: myuser, Password: mypass
curl -H "Authorization: Basic $(echo -n 'myuser:mypass' | base64)" https://your-api.com/data
```

Or with curl's built-in auth:

```bash
curl --user myuser:mypass https://your-api.com/data
```

## Implementing Credential Validation

The `_verify_basic_auth()` function currently accepts any non-empty credentials. Update it to implement real validation:

### Example 1: Database Lookup

```python
import bcrypt
from app.models import User  # Your user model

def _verify_basic_auth(username: str, password: str) -> Dict[str, Any]:
    """Verify credentials against database"""
    user = User.get_by_username(username)  # Your DB query
    
    if not user or not bcrypt.checkpw(password.encode(), user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password"
        )
    
    return {
        "sub": user.id,
        "preferred_username": user.username,
        "email": user.email,
        "auth_method": "basic",
    }
```

### Example 2: External Auth Service

```python
import httpx

def _verify_basic_auth(username: str, password: str) -> Dict[str, Any]:
    """Verify credentials via external auth service"""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://auth-service.example.com/verify",
            json={"username": username, "password": password}
        )
        
        if response.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials"
            )
        
        user_data = response.json()
        return {
            "sub": user_data["id"],
            "preferred_username": user_data["username"],
            "email": user_data.get("email"),
            "auth_method": "basic",
        }
```

### Example 3: Environment Variable with Hash

```python
import hashlib
import os

def _verify_basic_auth(username: str, password: str) -> Dict[str, Any]:
    """Verify against environment-stored credentials (development only)"""
    # Store as: USERNAME:PASSWORD_SHA256_HASH
    valid_creds = os.environ.get("API_CREDENTIALS", "")
    
    if not valid_creds or ":" not in valid_creds:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credentials not configured"
        )
    
    valid_user, valid_hash = valid_creds.split(":", 1)
    password_hash = hashlib.sha256(password.encode()).hexdigest()
    
    if username != valid_user or password_hash != valid_hash:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password"
        )
    
    return {
        "sub": username,
        "preferred_username": username,
        "auth_method": "basic",
    }
```

## Authentication Flow

The `require_authentication()` function:

1. Checks if `Authorization` header exists
2. If it starts with `"Bearer "`, validates as JWT
3. If it starts with `"Basic "`, parses and validates as basic auth
4. Returns claims dict on success
5. Raises HTTP 401 on failure

## Environment Variables

The bearer token validation uses these environment variables:

- `AUTH_URL` - Authority issuing tokens (default: `https://authenticate.opteryx.app`)
- `JWT_ISSUER` - Expected token issuer claim (default: value of `AUTH_URL`)
- `DATA_AUDIENCE` - Expected token audience (default: `opteryx`)
- `TASKS_OIDC_AUDIENCE` - For Google OIDC validation (optional)
- `TASKS_OIDC_SA` - Service account to restrict to (optional)

## Migrating Existing Endpoints

To add dual-auth support to existing endpoints:

### Before
```python
from app.auth.deps import require_bearer_token

@router.get("/metadata/{resource}")
async def get_metadata(
    resource: str,
    claims: dict = Depends(require_bearer_token),
):
    ...
```

### After
```python
from app.auth.deps import require_authentication

@router.get("/metadata/{resource}")
async def get_metadata(
    resource: str,
    claims: dict = Depends(require_authentication),
):
    # Check auth_method if needed
    auth_method = claims.get("auth_method", "bearer")
    ...
```

## Security Considerations

- **Basic Auth**: Always use HTTPS in production to avoid sending credentials in cleartext
- **Token Expiry**: Bearer tokens include expiry validation (`require_exp: True`)
- **Audience Validation**: JWT audience claims are validated against `DATA_AUDIENCE`
- **Issuer Validation**: JWT issuer claims are validated against `JWT_ISSUER`

## Testing

### Test Bearer Token
```bash
export TOKEN="eyJhbGc..."
curl -H "Authorization: Bearer $TOKEN" https://api.example.com/data
```

### Test Basic Auth
```bash
curl --user testuser:testpass https://api.example.com/data
```

### Test Invalid Credentials
```bash
curl --user wronguser:wrongpass https://api.example.com/data
# Returns: 401 Unauthorized
```
