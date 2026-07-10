"""Tests for OData query parameter validation fixes (WP-11).

Tests cover:
1. $top=0 allowed unconditionally
2. $count+$apply combination allowed
3. Duplicate $filter/$skip parameters rejected
4. $count parameter accepts only "true"/"false"
"""

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


class TestTopZeroAllowed:
    """Test that $top=0 is allowed unconditionally."""

    def test_top_zero_alone_allowed(self, client):
        """$top=0 without $count should return 200 with zero rows."""
        response = client.get("/api/v4/public/demo/users?$top=0")
        # Should not be 400; catalog loading might fail (404) but not from validation
        assert response.status_code != 400, f"$top=0 should be allowed, got {response.status_code}"

    def test_top_zero_with_count_allowed(self, client):
        """$top=0 with $count=true should return 200 with count."""
        response = client.get("/api/v4/public/demo/users?$top=0&$count=true")
        # Should not be 400; catalog loading might fail (404) but not from validation
        assert response.status_code != 400, f"$top=0&$count=true should be allowed, got {response.status_code}"

    def test_top_zero_with_filter_allowed(self, client):
        """$top=0 with $filter should return 200 with zero rows."""
        response = client.get("/api/v4/public/demo/users?$top=0&$filter=id eq 1")
        # Should not be 400
        assert response.status_code != 400

    def test_top_negative_rejected(self, client):
        """$top=-1 should be rejected with 400."""
        response = client.get("/api/v4/public/demo/users?$top=-1")
        assert response.status_code == 400
        data = response.json()
        assert "error" in data
        assert "$top" in data["error"]["message"] or "between" in data["error"]["message"]

    def test_top_exceeds_max_rejected(self, client):
        """$top=25001 should be rejected with 400."""
        response = client.get("/api/v4/public/demo/users?$top=25001")
        assert response.status_code == 400
        data = response.json()
        assert "error" in data
        assert "$top" in data["error"]["message"] or "between" in data["error"]["message"]

    def test_top_in_valid_range_allowed(self, client):
        """$top values 1-25000 should be allowed."""
        for top_val in [1, 100, 10000, 25000]:
            response = client.get(f"/api/v4/public/demo/users?$top={top_val}")
            # Should not be 400 from validation
            assert response.status_code != 400, f"$top={top_val} should be allowed"


class TestCountApplyAllowed:
    """Test that $count+$apply combination is allowed."""

    def test_count_with_apply_allowed(self, client):
        """$count=true with $apply should return 200 with group count."""
        response = client.get(
            "/api/v4/public/demo/users?$apply=groupby((status), aggregate($count as Count))&$count=true&$top=10"
        )
        # Should not be 400 from validation
        assert response.status_code != 400, f"$count with $apply should be allowed, got {response.status_code}"

    def test_count_true_with_apply(self, client):
        """$count=true with $apply should allow group counting."""
        response = client.get(
            "/api/v4/public/demo/users?$apply=groupby((id), aggregate($count as Count))&$count=true"
        )
        assert response.status_code != 400

    def test_count_false_with_apply(self, client):
        """$count=false with $apply should work (no total count requested)."""
        response = client.get(
            "/api/v4/public/demo/users?$apply=groupby((id), aggregate($count as Count))&$count=false"
        )
        assert response.status_code != 400

    def test_apply_alone_still_works(self, client):
        """$apply without $count should still work."""
        response = client.get(
            "/api/v4/public/demo/users?$apply=groupby((id), aggregate($count as Count))&$top=10"
        )
        assert response.status_code != 400


class TestDuplicateParamRejection:
    """Test that duplicate sensitive parameters are rejected."""

    def test_duplicate_top_rejected(self, client):
        """Duplicate $top parameters should return 400."""
        response = client.get("/api/v4/public/demo/users?$top=1&$top=2")
        assert response.status_code == 400
        data = response.json()
        assert "error" in data
        assert "Multiple parameters" in data["error"]["message"] or "$top" in data["error"]["message"]

    def test_duplicate_filter_rejected(self, client):
        """Duplicate $filter parameters should return 400."""
        response = client.get("/api/v4/public/demo/users?$filter=id eq 1&$filter=id eq 2&$top=10")
        assert response.status_code == 400
        data = response.json()
        assert "error" in data
        assert "Multiple parameters" in data["error"]["message"] or "$filter" in data["error"]["message"]

    def test_duplicate_skip_rejected(self, client):
        """Duplicate $skip parameters should return 400."""
        response = client.get("/api/v4/public/demo/users?$skip=1&$skip=2&$top=10")
        assert response.status_code == 400
        data = response.json()
        assert "error" in data
        assert "Multiple parameters" in data["error"]["message"] or "$skip" in data["error"]["message"]

    def test_duplicate_apply_rejected(self, client):
        """Duplicate $apply parameters should return 400."""
        response = client.get(
            "/api/v4/public/demo/users?$apply=groupby((id))&$apply=groupby((status))&$top=10"
        )
        assert response.status_code == 400
        data = response.json()
        assert "error" in data
        assert "Multiple parameters" in data["error"]["message"] or "$apply" in data["error"]["message"]

    def test_duplicate_count_rejected(self, client):
        """Duplicate $count parameters should return 400."""
        response = client.get("/api/v4/public/demo/users?$count=true&$count=false&$top=10")
        assert response.status_code == 400
        data = response.json()
        assert "error" in data
        assert "Multiple parameters" in data["error"]["message"] or "$count" in data["error"]["message"]

    def test_duplicate_select_rejected(self, client):
        """Duplicate $select parameters should return 400."""
        response = client.get("/api/v4/public/demo/users?$select=id&$select=name&$top=10")
        assert response.status_code == 400
        data = response.json()
        assert "error" in data
        assert "Multiple parameters" in data["error"]["message"] or "$select" in data["error"]["message"]

    def test_duplicate_orderby_rejected(self, client):
        """Duplicate $orderby parameters should return 400."""
        response = client.get("/api/v4/public/demo/users?$orderby=id asc&$orderby=name desc&$top=10")
        assert response.status_code == 400
        data = response.json()
        assert "error" in data
        assert "Multiple parameters" in data["error"]["message"] or "$orderby" in data["error"]["message"]

    def test_single_params_allowed(self, client):
        """Single instances of all parameters should be allowed."""
        response = client.get(
            "/api/v4/public/demo/users?$top=10&$skip=5&$filter=id gt 0&$orderby=name asc&$select=id,name&$count=true"
        )
        # Should not be 400 from duplicate validation
        assert response.status_code != 400


class TestCountStrictValidation:
    """Test that $count parameter strictly accepts only "true" or "false"."""

    def test_count_true_accepted(self, client):
        """$count=true should be accepted."""
        response = client.get("/api/v4/public/demo/users?$count=true&$top=10")
        assert response.status_code != 400

    def test_count_false_accepted(self, client):
        """$count=false should be accepted."""
        response = client.get("/api/v4/public/demo/users?$count=false&$top=10")
        assert response.status_code != 400

    def test_count_case_insensitive_true(self, client):
        """$count=True, $count=TRUE should be accepted (case-insensitive)."""
        for val in ["True", "TRUE", "tRuE"]:
            response = client.get(f"/api/v4/public/demo/users?$count={val}&$top=10")
            assert response.status_code != 400, f"$count={val} should be accepted (case-insensitive)"

    def test_count_case_insensitive_false(self, client):
        """$count=False, $count=FALSE should be accepted (case-insensitive)."""
        for val in ["False", "FALSE", "fAlSe"]:
            response = client.get(f"/api/v4/public/demo/users?$count={val}&$top=10")
            assert response.status_code != 400, f"$count={val} should be accepted (case-insensitive)"

    def test_count_one_rejected(self, client):
        """$count=1 should be rejected with 400."""
        response = client.get("/api/v4/public/demo/users?$count=1&$top=10")
        assert response.status_code == 400
        data = response.json()
        assert "error" in data
        assert "$count" in data["error"]["message"] or "Invalid" in data["error"]["message"]

    def test_count_yes_rejected(self, client):
        """$count=yes should be rejected with 400."""
        response = client.get("/api/v4/public/demo/users?$count=yes&$top=10")
        assert response.status_code == 400
        data = response.json()
        assert "error" in data
        assert "$count" in data["error"]["message"] or "Invalid" in data["error"]["message"]

    def test_count_zero_rejected(self, client):
        """$count=0 should be rejected with 400."""
        response = client.get("/api/v4/public/demo/users?$count=0&$top=10")
        assert response.status_code == 400
        data = response.json()
        assert "error" in data
        assert "$count" in data["error"]["message"] or "Invalid" in data["error"]["message"]

    def test_count_maybe_rejected(self, client):
        """$count=maybe should be rejected with 400."""
        response = client.get("/api/v4/public/demo/users?$count=maybe&$top=10")
        assert response.status_code == 400
        data = response.json()
        assert "error" in data
        assert "$count" in data["error"]["message"] or "Invalid" in data["error"]["message"]

    def test_count_empty_rejected(self, client):
        """$count= (empty) should be rejected with 400."""
        response = client.get("/api/v4/public/demo/users?$count=&$top=10")
        assert response.status_code == 400
        data = response.json()
        assert "error" in data

    def test_count_unicode_rejected(self, client):
        """$count with non-ASCII value should be rejected."""
        response = client.get("/api/v4/public/demo/users?$count=真實&$top=10")
        assert response.status_code == 400
        data = response.json()
        assert "error" in data


class TestRegressions:
    """Regression tests to ensure valid queries still work."""

    def test_valid_simple_query(self, client):
        """Simple query with $top should work."""
        response = client.get("/api/v4/public/demo/users?$top=10")
        assert response.status_code != 400

    def test_valid_count_true_query(self, client):
        """Query with $count=true should work."""
        response = client.get("/api/v4/public/demo/users?$count=true&$top=10")
        assert response.status_code != 400

    def test_valid_count_false_query(self, client):
        """Query with $count=false should work."""
        response = client.get("/api/v4/public/demo/users?$count=false&$top=10")
        assert response.status_code != 400

    def test_valid_filter_query(self, client):
        """Query with $filter should work."""
        response = client.get("/api/v4/public/demo/users?$filter=id eq 1&$top=10")
        assert response.status_code != 400

    def test_valid_orderby_query(self, client):
        """Query with $orderby should work."""
        response = client.get("/api/v4/public/demo/users?$orderby=name asc&$top=10")
        assert response.status_code != 400

    def test_valid_combined_query(self, client):
        """Query with multiple parameters should work."""
        response = client.get(
            "/api/v4/public/demo/users?$filter=id gt 0&$select=id,name&$orderby=name asc&$top=10&$skip=5&$count=true"
        )
        assert response.status_code != 400
