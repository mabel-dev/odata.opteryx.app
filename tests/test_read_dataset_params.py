#!/usr/bin/env python3
"""Tests for read_dataset parameter validation (duplicate $top etc.)."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routes.v4.interface import router, require_authentication


def test_duplicate_top_parameters_rejected():
    app = FastAPI()
    # override auth dependency to allow calls
    app.dependency_overrides[require_authentication] = lambda: None
    app.include_router(router)

    client = TestClient(app)

    res = client.get("/api/v4/public/github/events?$top=0&$top=1")
    assert res.status_code == 400
    assert "Multiple parameters not allowed: $top" in res.json().get("detail", "")


def test_count_with_groupby_returns_group_count():
    # Uses execute_odata_query directly to avoid router auth; may skip if dataset not present
    from app.routes.v4.interface import execute_odata_query
    import opteryx as _opteryx
    import pytest as _pytest

    if not hasattr(_opteryx, "execute_logical_plan"):
        _pytest.skip("opteryx.execute_logical_plan not available in this environment")

    try:
        res = execute_odata_query(
            table_name="public.github.events",
            apply_expr="groupby((region), aggregate($count as total))",
            top=0,
            count_requested=True,
        )
    except Exception as exc:
        # Skip if dataset not found in environment
        try:
            if exc.__class__.__name__ == "DatasetNotFoundError" or isinstance(
                exc, getattr(_opteryx, "exceptions").DatasetNotFoundError
            ):
                _pytest.skip("dataset not available in this environment")
        except Exception:
            pass
        raise

    # If we have a result, total_count should be int
    assert isinstance(res["total_count"], int)
