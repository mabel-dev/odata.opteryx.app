"""Tests for rejecting unsupported OData query options ($search, $expand)."""

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


class TestSearchRejection:
    """Test that $search queries are rejected with 501."""

    def test_search_with_value_rejected(self, client):
        """$search=text should return 501 Not Implemented."""
        response = client.get(
            "/api/v4/public/demo/users?$search=alice"
        )
        assert response.status_code == 501
        data = response.json()
        assert "error" in data
        assert "$search" in data["error"]["message"]

    def test_search_empty_allowed(self, client):
        """$search= with empty value should not trigger rejection."""
        # Empty search should be treated as not present
        response = client.get(
            "/api/v4/public/demo/users?$search=&$top=1"
        )
        # Should not be 501; could be 404 if dataset doesn't exist or 200 if it does
        assert response.status_code != 501

    def test_search_whitespace_allowed(self, client):
        """$search with whitespace-only value should be allowed (treated as empty)."""
        response = client.get(
            "/api/v4/public/demo/users?$search=   &$top=1"
        )
        # Whitespace-only is treated as empty after strip()
        assert response.status_code != 501

    def test_search_with_filter_rejected(self, client):
        """$search combined with $filter should return 501."""
        response = client.get(
            "/api/v4/public/demo/users?$search=alice&$filter=age gt 21&$top=10"
        )
        assert response.status_code == 501
        data = response.json()
        assert "$search" in data["error"]["message"]

    def test_search_error_format(self, client):
        """Error response should be valid OData format."""
        response = client.get(
            "/api/v4/public/demo/users?$search=test"
        )
        assert response.status_code == 501
        assert response.headers.get("OData-Version") == "4.0"
        data = response.json()
        assert "error" in data
        assert "code" in data["error"]
        assert "message" in data["error"]


class TestExpandRejection:
    """Test that $expand queries are rejected with 501."""

    def test_expand_with_value_rejected(self, client):
        """$expand=Orders should return 501 Not Implemented."""
        response = client.get(
            "/api/v4/public/demo/users?$expand=Orders"
        )
        assert response.status_code == 501
        data = response.json()
        assert "error" in data
        assert "$expand" in data["error"]["message"]

    def test_expand_empty_allowed(self, client):
        """$expand= with empty value should not trigger rejection."""
        response = client.get(
            "/api/v4/public/demo/users?$expand=&$top=1"
        )
        # Empty expand should be treated as not present
        assert response.status_code != 501

    def test_expand_with_filter_rejected(self, client):
        """$expand=Orders($filter=Amount gt 100) should return 501."""
        response = client.get(
            "/api/v4/public/demo/users?$expand=Orders($filter=Amount%20gt%20100)&$top=10"
        )
        assert response.status_code == 501
        data = response.json()
        assert "$expand" in data["error"]["message"]

    def test_expand_multiple_properties_rejected(self, client):
        """$expand=Orders,Products should return 501."""
        response = client.get(
            "/api/v4/public/demo/users?$expand=Orders,Products"
        )
        assert response.status_code == 501

    def test_expand_with_select_rejected(self, client):
        """$expand combined with $select should return 501."""
        response = client.get(
            "/api/v4/public/demo/users?$expand=Orders&$select=id,name&$top=10"
        )
        assert response.status_code == 501
        data = response.json()
        assert "$expand" in data["error"]["message"]

    def test_expand_error_format(self, client):
        """Error response should be valid OData format."""
        response = client.get(
            "/api/v4/public/demo/users?$expand=Orders"
        )
        assert response.status_code == 501
        assert response.headers.get("OData-Version") == "4.0"
        data = response.json()
        assert "error" in data
        assert "code" in data["error"]
        assert "message" in data["error"]


class TestSupportedOptionsStillWork:
    """Regression: verify that supported options still work without $search/$expand."""

    def test_filter_still_works(self, client):
        """$filter alone should not be rejected."""
        response = client.get(
            "/api/v4/public/demo/users?$filter=id eq 1&$top=10"
        )
        # Should not be 501; catalog loading might fail (404) but not from our check
        assert response.status_code != 501

    def test_select_still_works(self, client):
        """$select alone should not be rejected."""
        response = client.get(
            "/api/v4/public/demo/users?$select=id,name&$top=10"
        )
        assert response.status_code != 501

    def test_orderby_still_works(self, client):
        """$orderby alone should not be rejected."""
        response = client.get(
            "/api/v4/public/demo/users?$orderby=name asc&$top=10"
        )
        assert response.status_code != 501

    def test_top_skip_still_work(self, client):
        """$top and $skip alone should not be rejected."""
        response = client.get(
            "/api/v4/public/demo/users?$top=10&$skip=5"
        )
        assert response.status_code != 501

    def test_count_still_works(self, client):
        """$count alone should not be rejected."""
        response = client.get(
            "/api/v4/public/demo/users?$count=true&$top=10"
        )
        assert response.status_code != 501

    def test_apply_still_works(self, client):
        """$apply alone should not be rejected."""
        response = client.get(
            "/api/v4/public/demo/users?$apply=groupby((id), aggregate($count as Count))&$top=10"
        )
        assert response.status_code != 501

    def test_combined_supported_options(self, client):
        """Combination of supported options should work."""
        response = client.get(
            "/api/v4/public/demo/users?$filter=id gt 0&$select=id,name&$orderby=name asc&$top=10&$skip=0"
        )
        assert response.status_code != 501
