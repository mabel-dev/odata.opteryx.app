"""Integration tests for authentication and authorization."""

import re

import pytest
from fastapi.testclient import TestClient

from app.auth.permissions import anonymous_may_read
from app.main import service

client = TestClient(service)


class TestAuthentication:
    """Test authentication mechanisms."""

    def test_v4_bearer_token_invalid(self):
        """Test that invalid bearer token returns 401."""
        headers = {"Authorization": "Bearer invalid_token"}
        response = client.get("/api/v4/", headers=headers)
        assert response.status_code in [401, 403]
        # Check OData error format
        data = response.json()
        assert "error" in data

    def test_v4_basic_auth_invalid(self, monkeypatch):
        """Test that invalid basic auth returns 401.

        The external auth service is stubbed out: unpatched, basic auth is
        forwarded to https://authenticate.opteryx.app, which is a live outbound
        call CI must not make. Stubbing it also makes the assertion exact --
        previously the test accepted 503 as a pass, so it went green whenever
        the auth service was simply unreachable.
        """
        from fastapi import HTTPException

        def _reject(username, password):
            raise HTTPException(status_code=401, detail="Invalid credentials")

        monkeypatch.setattr("app.auth.deps.authenticate_with_external_service", _reject)

        response = client.get("/api/v4/", auth=("invalid", "credentials"))
        assert response.status_code == 401
        # Check OData error format
        data = response.json()
        assert "error" in data


class TestPermissionFiltering:
    """Test permission-based dataset filtering."""

    # No Authorization header is admitted as *anonymous* (require_authentication),
    # so these list what opteryx-access lets anyone read, not a 401.
    @pytest.mark.requires_catalog
    def test_anonymous_service_document_lists_only_anonymous_readable(self):
        """An anonymous service document lists only relations an anonymous caller may read."""
        response = client.get("/api/v4/")
        assert response.status_code == 200
        names = [entry["name"] for entry in response.json()["value"]]
        assert names, "anonymous callers should see the demo datasets"
        assert [name for name in names if not anonymous_may_read(name)] == []

    @pytest.mark.requires_catalog
    def test_anonymous_metadata_describes_only_anonymous_readable(self):
        """Anonymous $metadata describes only relations an anonymous caller may read."""
        response = client.get("/api/v4/$metadata")
        assert response.status_code == 200
        names = re.findall(r'Term="Custom.Resource" String="([^"]+)"', response.text)
        assert names, "anonymous callers should see the demo datasets"
        assert [name for name in names if not anonymous_may_read(name)] == []

    def test_data_endpoint_requires_auth(self):
        """Test that data endpoints require authentication."""
        response = client.get("/api/v4/public/demo/test")
        assert response.status_code == 401


class TestODataErrorFormat:
    """Test OData v4 error response format."""

    def test_401_returns_odata_error(self):
        """Test that 401 errors return OData error format."""
        # An anonymous read of a relation anonymous callers may not read.
        response = client.get("/api/v4/public/demo/test")
        assert response.status_code == 401
        data = response.json()
        assert "error" in data
        assert "code" in data["error"]
        assert "message" in data["error"]
        assert data["error"]["code"] == "Unauthorized"
        # Check OData-Version header
        assert response.headers.get("OData-Version") == "4.0"

    def test_404_returns_odata_error(self):
        """Test that 404 errors return OData error format for v4."""
        # This will fail auth first, but if we had valid auth, 404 would be OData format
        response = client.get("/api/v4/nonexistent/path/here")
        # Returns 401 because auth happens before routing
        assert response.status_code == 401
        data = response.json()
        assert "error" in data


class TestODataHeaders:
    """Test OData v4 headers."""

    def test_v4_endpoints_have_odata_version_header(self):
        """Test that v4 endpoints include OData-Version header."""
        response = client.get("/api/v4/")
        # Will fail auth, but should still have header
        assert response.headers.get("OData-Version") == "4.0"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
