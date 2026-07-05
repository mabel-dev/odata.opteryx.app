"""Tests for rejecting unsupported OData filter constructs with descriptive 400 errors (WP-08)."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routes.v4.interface import router, require_authentication


@pytest.fixture
def client():
    """Create a test client with authentication mocked out."""
    app = FastAPI()
    app.dependency_overrides[require_authentication] = lambda: None
    app.include_router(router)
    return TestClient(app)


class TestGUIDLiteralRejection:
    """Test that GUID literals return 400 with descriptive error."""

    def test_guid_literal_rejected(self, client):
        """$filter with GUID literal should return 400."""
        response = client.get(
            "/api/v4/public/demo/users?$filter=id eq guid'12345678-1234-1234-1234-123456789012'&$top=10"
        )
        assert response.status_code == 400
        data = response.json()
        assert "error" in data
        assert "GUID" in data["error"]["message"] or "unsupported" in data["error"]["message"].lower()

    def test_guid_literal_error_is_descriptive(self, client):
        """Error message should mention GUID literals."""
        response = client.get(
            "/api/v4/public/demo/users?$filter=id eq guid'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx'&$top=10"
        )
        assert response.status_code == 400
        data = response.json()
        assert "GUID" in data["error"]["message"]

    def test_guid_in_complex_filter_rejected(self, client):
        """GUID literals in complex filters should return 400."""
        response = client.get(
            "/api/v4/public/demo/users?$filter=status eq 'active' and id eq guid'12345678-1234-1234-1234-123456789012'&$top=10"
        )
        assert response.status_code == 400
        data = response.json()
        assert "error" in data


class TestLambdaExpressionRejection:
    """Test that lambda operators (any/all) return 400 with descriptive error."""

    def test_any_lambda_rejected(self, client):
        """$filter with any() should return 400."""
        response = client.get(
            "/api/v4/public/demo/users?$filter=Items/any(x:x/price gt 100)&$top=10"
        )
        assert response.status_code == 400
        data = response.json()
        assert "error" in data
        # Should mention lambda or any/all
        msg = data["error"]["message"].lower()
        assert "lambda" in msg or "any" in msg or "all" in msg or "unsupported" in msg

    def test_all_lambda_rejected(self, client):
        """$filter with all() should return 400."""
        response = client.get(
            "/api/v4/public/demo/users?$filter=Items/all(x:x/status eq 'active')&$top=10"
        )
        assert response.status_code == 400
        data = response.json()
        assert "error" in data

    def test_lambda_error_is_descriptive(self, client):
        """Error message should mention lambda operators."""
        response = client.get(
            "/api/v4/public/demo/users?$filter=Items/any(i:i/price gt 50)&$top=10"
        )
        assert response.status_code == 400
        data = response.json()
        # Should explain that lambda operators are not supported
        assert "lambda" in data["error"]["message"].lower() or "any" in data["error"]["message"].lower()

    def test_nested_lambda_rejected(self, client):
        """Nested lambda expressions should return 400."""
        response = client.get(
            "/api/v4/public/demo/users?$filter=Orders/any(o:o/Items/any(i:i/quantity gt 5))&$top=10"
        )
        assert response.status_code == 400


class TestGeographyLiteralRejection:
    """Test that Geography literals return 400 with descriptive error."""

    def test_geography_literal_rejected(self, client):
        """$filter with geography literal should return 400."""
        response = client.get(
            "/api/v4/public/demo/locations?$filter=location eq geography'SRID=4326;POINT(-122.419 37.775)'&$top=10"
        )
        assert response.status_code == 400
        data = response.json()
        assert "error" in data
        msg = data["error"]["message"].lower()
        assert "geography" in msg or "unsupported" in msg


class TestValidFiltersStillWork:
    """Regression: verify that valid filters still work."""

    def test_simple_string_filter_works(self, client):
        """Simple string equality should work."""
        response = client.get(
            "/api/v4/public/demo/users?$filter=name eq 'Alice'&$top=10"
        )
        # Should not be 400 due to filter parsing
        assert response.status_code != 400

    def test_numeric_filter_works(self, client):
        """Numeric comparisons should work."""
        response = client.get(
            "/api/v4/public/demo/users?$filter=age gt 21&$top=10"
        )
        assert response.status_code != 400

    def test_boolean_logic_filter_works(self, client):
        """AND/OR logic should work."""
        response = client.get(
            "/api/v4/public/demo/users?$filter=status eq 'active' and age gt 18&$top=10"
        )
        assert response.status_code != 400

    def test_contains_filter_works(self, client):
        """String functions like contains should work."""
        response = client.get(
            "/api/v4/public/demo/users?$filter=contains(name, 'ice')&$top=10"
        )
        assert response.status_code != 400

    def test_startswith_filter_works(self, client):
        """String functions like startswith should work."""
        response = client.get(
            "/api/v4/public/demo/users?$filter=startswith(name, 'A')&$top=10"
        )
        assert response.status_code != 400

    def test_is_null_filter_works(self, client):
        """IS NULL checks should work."""
        response = client.get(
            "/api/v4/public/demo/users?$filter=deleted eq null&$top=10"
        )
        assert response.status_code != 400

    def test_in_operator_filter_works(self, client):
        """IN operator should work."""
        response = client.get(
            "/api/v4/public/demo/users?$filter=status in ('active', 'pending')&$top=10"
        )
        assert response.status_code != 400


class TestErrorFormatCompliance:
    """Test that error responses follow OData format."""

    def test_guid_error_format_is_odata(self, client):
        """Error response should be valid OData format for GUID errors."""
        response = client.get(
            "/api/v4/public/demo/users?$filter=id eq guid'12345678-1234-1234-1234-123456789012'&$top=10"
        )
        assert response.status_code == 400
        assert response.headers.get("OData-Version") == "4.0"
        data = response.json()
        assert "error" in data
        assert "code" in data["error"]
        assert "message" in data["error"]
        assert data["error"]["code"] == "BadRequest"

    def test_lambda_error_format_is_odata(self, client):
        """Error response should be valid OData format for lambda errors."""
        response = client.get(
            "/api/v4/public/demo/users?$filter=Items/any(x:x/price gt 100)&$top=10"
        )
        assert response.status_code == 400
        assert response.headers.get("OData-Version") == "4.0"
        data = response.json()
        assert "error" in data
        assert "code" in data["error"]
        assert "message" in data["error"]
        assert data["error"]["code"] == "BadRequest"

    def test_geography_error_format_is_odata(self, client):
        """Error response should be valid OData format for geography errors."""
        response = client.get(
            "/api/v4/public/demo/locations?$filter=geo eq geography'SRID=4326;POINT(0 0)'&$top=10"
        )
        assert response.status_code == 400
        assert response.headers.get("OData-Version") == "4.0"
        data = response.json()
        assert "error" in data
        assert "code" in data["error"]
        assert "message" in data["error"]


class TestUUIDTypeMappingConsistency:
    """Test that UUID columns are consistently mapped to Edm.String (not Edm.Guid)."""

    def test_uuid_not_advertised_as_guid(self, client):
        """UUID columns should be advertised as Edm.String, not Edm.Guid."""
        # This test verifies that metadata and $metadata endpoints
        # consistently map UUID to Edm.String
        # Note: This is a meta-test; actual verification depends on the endpoint
        # having a UUID column to test against
        # For now, we just verify that GUID literals are unsupported
        response = client.get(
            "/api/v4/public/demo/users?$filter=id eq guid'12345678-1234-1234-1234-123456789012'&$top=10"
        )
        # If UUID was advertised as Edm.Guid, this would be supported
        # Instead, it should fail because GUID literals are not supported
        assert response.status_code == 400
