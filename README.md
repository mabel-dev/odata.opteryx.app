# Opteryx OData Service

An OData v4-compliant API for querying and accessing Opteryx datasets with enterprise-grade authentication and authorization.

## Overview

This service provides OData v4 endpoints that allow you to:
- **Discover datasets** you have access to via service document
- **Query data** using standard OData query parameters ($filter, $top, $skip, etc.)
- **Inspect schemas** through EDMX metadata documents
- **Connect from BI tools** like Excel, Power BI, and Tableau

All requests are authenticated and enforce row-level security based on your permissions.

## Quick Start

### Authentication

The service supports two authentication methods:

#### 1. Bearer Token (Recommended)
```bash
curl -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  https://odata.opteryx.app/api/v4/
```

#### 2. Basic Authentication
```bash
curl -u username:password \
  https://odata.opteryx.app/api/v4/
```

Basic auth credentials are exchanged for a JWT token via the external authentication service (`authenticate.opteryx.app`).

### Discovering Datasets

Get a list of all datasets you have access to:

```bash
curl -u username:password \
  https://odata.opteryx.app/api/v4/
```

Response:
```json
{
  "@odata.context": "/api/v4/$metadata",
  "value": [
    {
      "name": "public.demo.users",
      "kind": "EntitySet",
      "url": "public/demo/users"
    },
    {
      "name": "personal.alice.projects",
      "kind": "EntitySet",
      "url": "personal/alice/projects"
    }
  ]
}
```

### Querying Data

Query a dataset with OData parameters:

```bash
# Get first 10 rows
curl -u username:password \
  "https://odata.opteryx.app/api/v4/public/demo/users?\$top=10"

# Filter and sort
curl -u username:password \
  "https://odata.opteryx.app/api/v4/workspace/collection/dataset?\$filter=age gt 21&\$orderby=name&\$top=100"
```

Response format:
```json
{
  "@odata.context": "/api/v4/$metadata#dataset",
  "value": [
    {"id": 1, "name": "Alice", "age": 25},
    {"id": 2, "name": "Bob", "age": 30}
  ]
}
```

### Getting Schema Information

Retrieve EDMX metadata for all accessible datasets:

```bash
curl -u username:password \
  https://odata.opteryx.app/api/v4/\$metadata
```

This returns an XML document describing EntityTypes, properties, and their data types.

## API Endpoints

### OData v4 API (`/api/v4`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v4/` | GET | Service document - lists all accessible datasets |
| `/api/v4/$metadata` | GET | EDMX metadata document with schemas |
| `/api/v4/{workspace}/{collection}/{dataset}` | GET | Query data from a specific dataset |
| `/api/v4/{workspace}/{collection}/{dataset}/$metadata` | GET | Schema for a specific dataset |

### Legacy v1 API (`/api/v1`)

The v1 API is still available but deprecated. Use `/api/v4` for new integrations.

## Query Parameters

Standard OData v4 query parameters are supported:

| Parameter | Description | Example |
|-----------|-------------|---------|
| `$top` | Limit number of results (max 10000) | `$top=100` |
| `$skip` | Skip N results for pagination | `$skip=200` |
| `$filter` | Filter results (limited support) | `$filter=age gt 21` |
| `$orderby` | Sort results | `$orderby=name desc` |
| `$select` | Select specific columns | `$select=name,age` |
| `$count` | Include total count | `$count=true` |

## Permission Model

Access control is enforced at query time:

- **Public datasets** (`public.*`) - Read access for all authenticated users
- **Personal datasets** (`personal.{username}.*`) - Full access for owner only
- **Workspace datasets** - Access based on assigned permissions

Permissions are extracted from your JWT token and passed to the Opteryx query engine for enforcement.

## Using with BI Tools

### Excel
1. Data → Get Data → From OData Feed
2. Enter URL: `https://odata.opteryx.app/api/v4`
3. Choose Basic authentication
4. Enter your credentials

### Power BI
1. Get Data → OData Feed
2. Enter URL: `https://odata.opteryx.app/api/v4`
3. Select Basic authentication
4. Enter your credentials

### Tableau
1. Connect → OData
2. Server: `odata.opteryx.app`
3. Path: `/api/v4`
4. Authentication: Username and Password

## Error Responses

All v4 endpoints return errors in OData format:

```json
{
  "error": {
    "code": "Forbidden",
    "message": "Access denied to dataset workspace.collection.dataset"
  }
}
```

Common error codes:
- `Unauthorized` (401) - Missing or invalid authentication
- `Forbidden` (403) - Valid auth but insufficient permissions
- `NotFound` (404) - Dataset does not exist or is not accessible
- `InternalServerError` (500) - Server error
- `ServiceUnavailable` (503) - Dependent service unavailable

---

## Development

### Local Development

Run the service locally:

```bash
make run
```

Or with uvicorn directly:

```bash
uvicorn app.main:service --reload --host 0.0.0.0 --port 8890
```

### Testing

Run the test script:

```bash
# Set credentials via environment variables
export ODATA_USERNAME="your_username"
export ODATA_PASSWORD="your_password"

# Test v4 endpoints
python scripts/test_odata.py --api-version v4 --workstream public --collection demo --dataset users

# Test service document
python scripts/test_odata.py --api-version v4 --test-service-doc

# Test metadata
python scripts/test_odata.py --api-version v4 --test-metadata
```

Run integration tests:

```bash
pytest tests/test_auth_integration.py -v
```

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `GCP_PROJECT_ID` | GCP project ID for Firestore/GCS | Required |
| `FIRESTORE_DATABASE` | Firestore database name | Required |
| `GCS_BUCKET` | GCS bucket for data storage | Required |
| `ODATA_DEFAULT_TOP` | Default $top value when not specified | `100` |

### Docker Build

Build the Docker image:

```bash
docker build -f Dockerfile -t gcr.io/$PROJECT_ID/opteryx-odata .
```

### Architecture

The service architecture:

1. **Authentication Layer** (`app/auth/`)
   - External auth service integration
   - JWT token validation
   - Claims normalization

2. **Permission Handling** (`app/auth/permissions.py`)
   - Convert JWT permissions to Opteryx access policies
   - Row-level security enforcement

3. **OData v4 Routes** (`app/routes/v4/`)
   - Service document with filtered datasets
   - EDMX metadata generation
   - Data query endpoints with OData compliance

4. **Query Engine Integration**
   - Opteryx connector for GCS/Firestore
   - Access policies passed to every query
   - Automatic permission enforcement

For implementation details, see [REDESIGN.md](REDESIGN.md).